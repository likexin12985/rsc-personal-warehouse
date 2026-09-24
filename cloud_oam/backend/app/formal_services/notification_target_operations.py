"""Inspect retained people and explicitly restore one previously unbound target.

Recovery verifies a current provider-scoped AuthIdentity, records one recipient
and its provenance, then reopens expansion. It neither expands nor sends. An
existing recipient is never bypassed, including failed/unknown deliveries.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal, lock_formal_principal_graph
from ..foundation_models import AuditEvent, NotificationEvent, NotificationPersonTarget, NotificationRecipient, NotificationTargetBinding, Person
from ..models import User
from .audit_chain import append_audit_event
from .notification_identities import IdentityPolicy, identity_policy, policy_available, resolve_verified_channels
from .inventory_notification_failures import ensure_outer_transaction
from .notification_delivery_operations import NotificationDeliveryOperationsError as OperationsError
from .notification_events import target_manifest_hash


ACTION = "notification_target.recovered"
AGGREGATE = "notification_person_target"
STREAM = "material_request"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HEADER = re.compile(r"[A-Za-z0-9._:-]+\Z", re.ASCII)


@dataclass(frozen=True)
class TargetRecord:
    target_id: UUID
    event_id: UUID
    event_type: str
    business_type: str
    business_id: str
    person_id: UUID
    person_name: str
    created_at: datetime
    event_status: str
    state: str
    bound_count: int
    available_channels: tuple[str, ...]
    latest_recovery_audit_id: UUID | None
    snapshot_sha256: str


@dataclass(frozen=True)
class TargetPage:
    items: tuple[TargetRecord, ...]
    next_after_id: UUID | None


@dataclass(frozen=True)
class LegacyEventRecord:
    event_id: UUID
    event_type: str
    business_type: str
    business_id: str
    created_at: datetime
    recipient_count: int


@dataclass(frozen=True)
class LegacyEventPage:
    items: tuple[LegacyEventRecord, ...]
    next_after_id: UUID | None


@dataclass(frozen=True)
class RecoveryResult:
    target_id: UUID
    event_id: UUID
    audit_id: UUID
    outcome: str
    code: str | None
    channel: str
    recipient_id: UUID | None
    checked_at: datetime
    replayed: bool


def _error(code, category, message):
    return OperationsError(f"notification_target_{code}", category, message)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _now(db):
    return _utc(db.scalar(text("SELECT clock_timestamp()"))) if db.get_bind().dialect.name == "postgresql" else datetime.now(timezone.utc)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _require_operator(db, actor, action):
    if (not isinstance(actor, FormalPrincipal) or not actor.user_id or actor.access_mode != "active"
            or not any(row.role_code == "admin" and row.scope_type == "national" and row.scope_id == "*" for row in actor.assignments)
            or not actor.allows(db, "notification_delivery", action, target_scope_type="national", target_scope_id="*")):
        raise _error("operator_forbidden", "forbidden", "仅有效的总部通知运维管理员可操作通知目标")


def _mapped_user(db, person_id):
    users = tuple(db.scalars(select(User).where(User.person_id == person_id).limit(2)
        .execution_options(populate_existing=True)))
    if len(users) > 1:
        raise _error("mapping_ambiguous", "service_unavailable", "人员账号关系不唯一，需先核查身份")
    return users[0] if users else None


def _audit_scope():
    return (AuditEvent.stream_key == STREAM, AuditEvent.aggregate_type == AGGREGATE, AuditEvent.action == ACTION)


def _snapshot(db, target, policy, now):
    event = db.get(NotificationEvent, target.event_id, populate_existing=True)
    person = db.get(Person, target.person_id, populate_existing=True)
    if event is None or person is None:
        raise _error("evidence_missing", "service_unavailable", "原通知目标证据不完整")
    people = tuple(db.scalars(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id == event.id)))
    bindings = tuple(db.execute(select(NotificationTargetBinding.recipient_id, NotificationRecipient.user_id)
        .join(NotificationRecipient, NotificationRecipient.id == NotificationTargetBinding.recipient_id)
        .where(NotificationTargetBinding.target_id == target.id).order_by(NotificationTargetBinding.recipient_id)))
    user = _mapped_user(db, person.id)
    # An unlinked recipient for the same account/event also blocks a second
    # route. Historical/partial provenance must be reviewed, not inferred.
    unlinked = tuple(db.scalars(select(NotificationRecipient.id).where(NotificationRecipient.event_id == event.id,
        NotificationRecipient.user_id == user.id).order_by(NotificationRecipient.id))) if user else ()
    latest = db.scalar(select(AuditEvent.id).where(*_audit_scope(), AuditEvent.aggregate_id == target.id.hex)
        .order_by(AuditEvent.stream_version.desc()).limit(1))
    candidates = ()
    if event.target_manifest_sha256 is None or target_manifest_hash(people) != event.target_manifest_sha256:
        state = "evidence_invalid"
    elif event.status == "cancelled":
        state = "cancelled"
    elif bindings or unlinked:
        state = "bound"
    elif person.employment_status != "active":
        state = "account_inactive"
    elif user is None:
        state = "needs_account"
    elif user.account_status != "active" or not user.is_active:
        state = "account_inactive"
    elif not policy_available(policy):
        state = "configuration_unavailable"
    else:
        candidates = resolve_verified_channels(db, person_id=person.id, policy=policy, now=now)
        state = "ready" if candidates else "needs_verified_channel"
    facts = {"schema":"notification-target-observation.v1", "target_id":str(target.id),
        "event_id":str(event.id), "event_type":event.event_type, "business_type":event.business_type,
        "business_id":event.business_id, "event_status":event.status, "payload_sha256":_hash(event.payload_jsonb),
        "target_manifest_sha256":event.target_manifest_sha256, "person_id":str(person.id),
        "organization_id":str(person.organization_id), "employment_status":person.employment_status,
        "user_id":user.id if user else None, "authorization_version":user.authorization_version if user else None,
        "account_status":user.account_status if user else None, "account_active":user.is_active if user else None,
        "bound_recipients":[str(row.recipient_id) for row in bindings], "account_recipients":[str(key) for key in unlinked],
        "latest_recovery_audit_id":str(latest) if latest else None, "state":state,
        "candidates":[{"channel":row.channel,"identity_id":str(row.identity_id),"provider_key":row.provider_key,
            "hash_version":row.hash_version,"verified_at":row.verified_at.isoformat(),
            "identifier_hash":row.identifier_hash} for row in candidates]}
    return TargetRecord(target.id,event.id,event.event_type,event.business_type,event.business_id,
        person.id,person.name,_utc(target.created_at),event.status,state,len(set(unlinked) | {row.recipient_id for row in bindings}),
        tuple(row.channel for row in candidates),latest,_hash(facts)), candidates


def _page_args(limit, after_id):
    if type(limit) is not int or not 1 <= limit <= 100 or (after_id is not None and not isinstance(after_id,UUID)):
        raise _error("query_invalid", "invalid_request", "通知目标分页参数无效")


def list_notification_targets(db: Session, *, actor: FormalPrincipal, policy: IdentityPolicy,
    limit: int = 50, after_id: UUID | None = None, unbound_only: bool = True) -> TargetPage:
    _require_operator(db, actor, "read"); _page_args(limit,after_id)
    if type(unbound_only) is not bool:
        raise _error("query_invalid", "invalid_request", "通知目标筛选参数无效")
    with db.no_autoflush:
        statement = select(NotificationPersonTarget)
        if after_id is not None:
            cursor = db.get(NotificationPersonTarget, after_id)
            if cursor is None:
                raise _error("cursor_invalid", "not_found", "通知目标分页位置不存在")
            statement = statement.where(or_(NotificationPersonTarget.created_at < cursor.created_at,
                and_(NotificationPersonTarget.created_at == cursor.created_at, NotificationPersonTarget.id < cursor.id)))
        if unbound_only:
            bound = select(NotificationTargetBinding.target_id).where(NotificationTargetBinding.target_id == NotificationPersonTarget.id).exists()
            statement = statement.where(~bound)
        rows = tuple(db.scalars(statement.order_by(NotificationPersonTarget.created_at.desc(),NotificationPersonTarget.id.desc()).limit(limit+1)))
        now = _now(db)
        return TargetPage(tuple(_snapshot(db,target,policy,now)[0] for target in rows[:limit]),
            rows[limit-1].id if len(rows)>limit else None)


def list_legacy_notification_events(db: Session, *, actor: FormalPrincipal, limit: int = 50,
    after_id: UUID | None = None) -> LegacyEventPage:
    _require_operator(db,actor,"read"); _page_args(limit,after_id)
    with db.no_autoflush:
        statement=select(NotificationEvent).where(NotificationEvent.target_manifest_sha256.is_(None))
        if after_id is not None:
            cursor=db.scalar(statement.where(NotificationEvent.id==after_id))
            if cursor is None:
                raise _error("cursor_invalid","not_found","历史通知分页位置不存在")
            statement=statement.where(or_(NotificationEvent.created_at<cursor.created_at,
                and_(NotificationEvent.created_at==cursor.created_at,NotificationEvent.id<cursor.id)))
        rows=tuple(db.scalars(statement.order_by(NotificationEvent.created_at.desc(),NotificationEvent.id.desc()).limit(limit+1)))
        counts=dict(db.execute(select(NotificationRecipient.event_id,func.count()).where(
            NotificationRecipient.event_id.in_([row.id for row in rows[:limit]])).group_by(NotificationRecipient.event_id)).all())
        return LegacyEventPage(tuple(LegacyEventRecord(row.id,row.event_type,row.business_type,row.business_id,
            _utc(row.created_at),counts.get(row.id,0)) for row in rows[:limit]), rows[limit-1].id if len(rows)>limit else None)


def _try_target_lock(db,target_id):
    dialect=db.get_bind().dialect.name
    if dialect=="sqlite": return True
    if dialect!="postgresql": raise _error("database_invalid","service_unavailable","通知目标数据库不受支持")
    key=int.from_bytes(hashlib.sha256(f"notification-target:{target_id}".encode()).digest()[:8],"big",signed=True)
    return bool(db.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),{"key":key}))


def _result(audit, target, replayed):
    def require(condition):
        if not condition:
            raise ValueError("invalid recovery audit")
    try:
        data=audit.after_jsonb
        require(data["schema"]=="notification-target-recovery.v1")
        require(data["target_id"]==str(target.id) and data["event_id"]==str(target.event_id))
        require(data["outcome"] in ("bound","blocked") and data["channel"] in ("sms","wechat"))
        if data["outcome"]=="bound":
            require(data["code"] is None)
            recipient_id=UUID(data["recipient_id"])
            UUID(data["identity_id"])
        else:
            require(data["recipient_id"] is None and data["code"] in (
                "evidence_invalid","cancelled","bound","account_inactive","needs_account",
                "configuration_unavailable","needs_verified_channel","channel_unavailable"))
            recipient_id=None
        return RecoveryResult(target.id,target.event_id,audit.id,data["outcome"],data["code"],data["channel"],recipient_id,_utc(audit.occurred_at),replayed)
    except (KeyError,TypeError,ValueError,AttributeError) as exc:
        raise _error("audit_invalid","service_unavailable","通知恢复审计不完整") from exc


def recover_notification_target(db: Session, *, actor: FormalPrincipal, policy: IdentityPolicy,
    target_id: UUID, expected_snapshot_sha256: str, channel: str, reason: str,
    idempotency_key: str, request_id: str) -> RecoveryResult:
    _require_operator(db,actor,"retry")
    if (not isinstance(target_id,UUID) or not isinstance(expected_snapshot_sha256,str) or not _SHA256.fullmatch(expected_snapshot_sha256)
        or channel not in ("sms","wechat") or not isinstance(reason,str) or not 1<=len(reason.strip())<=500):
        raise _error("command_invalid","invalid_request","通知恢复需要原目标状态、渠道及原因")
    for value,minimum,maximum in ((idempotency_key,16,128),(request_id,8,160)):
        if not isinstance(value,str) or not minimum<=len(value)<=maximum or not _HEADER.fullmatch(value):
            raise _error("headers_invalid","invalid_request","恢复幂等键或请求标识无效")
    reason=reason.strip()
    command_hash=_hash({"actor_user_id":actor.user_id,"target_id":str(target_id),"snapshot_sha256":expected_snapshot_sha256,
        "channel":channel,"reason":reason})
    key_hash=hashlib.sha256(idempotency_key.encode()).hexdigest()
    ensure_outer_transaction(db)
    target=db.get(NotificationPersonTarget,target_id,populate_existing=True)
    if target is None: raise _error("not_found","not_found","原通知目标不存在")
    observed_user=_mapped_user(db,target.person_id)
    observed_user_id=observed_user.id if observed_user else None
    lock_formal_principal_graph(db,tuple(key for key in (actor.user_id,observed_user_id) if key))
    if not _try_target_lock(db,target_id): raise _error("busy","conflict","该通知目标正在处理中，请刷新后再操作")
    try:
        db.scalars(select(NotificationEvent).where(NotificationEvent.id==target.event_id).with_for_update(nowait=True)
            .execution_options(populate_existing=True)).one()
    except DBAPIError as exc:
        if getattr(exc.orig,"sqlstate",None)=="55P03":
            raise _error("busy","conflict","该通知正在扩展或处理中，请刷新后再操作") from None
        raise
    now=_now(db)
    try:
        current_actor=load_formal_principal(db,actor.user_id,now=now)
    except FormalAccessError as exc:
        raise _error("operator_revoked","forbidden","当前正式运维身份已失效") from exc
    operator_user=db.get(User,current_actor.user_id,populate_existing=True)
    if operator_user is None or not operator_user.is_active:
        raise _error("operator_revoked","forbidden","当前正式运维身份已失效")
    _require_operator(db,current_actor,"retry")
    prior=tuple(db.scalars(select(AuditEvent).where(*_audit_scope(),AuditEvent.aggregate_id==target_id.hex,
        AuditEvent.actor_user_id==actor.user_id,or_(AuditEvent.after_jsonb["idempotency_sha256"].as_string()==key_hash,AuditEvent.request_id==request_id))))
    if prior:
        if len(prior)!=1 or prior[0].after_jsonb.get("command_sha256")!=command_hash:
            raise _error("conflict","conflict","该目标的幂等键或请求标识已用于其他恢复内容")
        return _result(prior[0],target,True)
    current_user=_mapped_user(db,target.person_id)
    if (current_user.id if current_user else None)!=observed_user_id:
        raise _error("mapping_changed","precondition_failed","人员账号关系已变化，请重新查询")
    record,candidates=_snapshot(db,target,policy,now)
    if record.snapshot_sha256!=expected_snapshot_sha256:
        raise _error("stale","precondition_failed","通知目标或身份已变化，请重新查询")
    selected=next((row for row in candidates if row.channel==channel),None)
    code=record.state if record.state!="ready" else (None if selected else "channel_unavailable")
    recipient=None
    if code is None:
        recipient=NotificationRecipient(id=uuid4(),event_id=target.event_id,user_id=selected.user_id,
            channel=channel,recipient_key=selected.recipient_key,status="active",created_at=now)
        db.add(recipient)
        db.add(NotificationTargetBinding(target_id=target.id,recipient_id=recipient.id,created_at=now))
        # Persist the new binding before the guarded event status transition.
        db.flush()
        db.get(NotificationEvent,target.event_id).status="pending"
        db.flush()
    audit=append_audit_event(db,stream_key=STREAM,actor_user_id=current_actor.user_id,action=ACTION,
        aggregate_type=AGGREGATE,aggregate_id=target.id.hex,
        before_jsonb={"snapshot_sha256":record.snapshot_sha256,"event_status":record.event_status,
            "latest_recovery_audit_id":str(record.latest_recovery_audit_id) if record.latest_recovery_audit_id else None},
        after_jsonb={"schema":"notification-target-recovery.v1","target_id":str(target.id),"event_id":str(target.event_id),
            "person_id":str(target.person_id),"outcome":"bound" if recipient else "blocked","code":code,"channel":channel,
            "recipient_id":str(recipient.id) if recipient else None,"identity_id":str(selected.identity_id) if recipient else None,
            "identity_provider":selected.provider_key if recipient else None,"identity_hash_version":selected.hash_version if recipient else None,
            "identity_verified_at":selected.verified_at.isoformat() if recipient else None,
            "reason":reason,"idempotency_sha256":key_hash,"command_sha256":command_hash},
        request_id=request_id,occurred_at=now,created_at=now)
    return _result(audit,target,False)
