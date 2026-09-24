"""National operator inspection and exact-source, audited rechecks.

The original quarantine audit remains immutable, even after recovery. Recheck
only projects a notification; the expander and provider delivery remain separate.
Idempotency is scoped to the actor and source, serialized by its advisory lock.
The caller owns commit/rollback, including the result audit and new notification.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal, lock_formal_principal_graph
from ..foundation_models import AuditEvent
from . import inventory_notification_failures as failures
from .audit_chain import append_audit_event
from .inventory_notifications import InventoryNotificationError, project_inventory_notification
from .notification_events import NotificationEventError
from .notification_delivery_operations import NotificationDeliveryOperationsError as OperationsError


RECHECK_ACTION = "inventory_notification.source_rechecked"
SOURCE_CHANGED = "inventory_notification_source_changed"
_ACTIONS = (failures.BLOCKED_ACTION, RECHECK_ACTION)
_CODES = (failures.SOURCE_INVALID, failures.EVENT_CONFLICT, SOURCE_CHANGED)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HEADER = re.compile(r"[A-Za-z0-9._:-]+\Z", re.ASCII)


@dataclass(frozen=True)
class SourceRecord:
    outbox_id: UUID
    failure_audit_id: UUID
    latest_audit_id: UUID
    source_sha256: str
    code: str | None
    status: str
    isolated_at: datetime
    last_checked_at: datetime | None
    event_id: UUID | None
    recipient_count: int | None


@dataclass(frozen=True)
class SourcePage:
    items: tuple[SourceRecord, ...]
    next_after_id: UUID | None


@dataclass(frozen=True)
class SourceRecheckResult:
    item: SourceRecord
    replayed: bool


def _error(code: str, category: str, message: str):
    return OperationsError(f"inventory_notification_{code}", category, message)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        return _utc(db.scalar(text("SELECT clock_timestamp()")))
    return datetime.now(timezone.utc)


def _require_operator(db: Session, actor: FormalPrincipal, action: str) -> None:
    if not isinstance(actor, FormalPrincipal) or not actor.user_id or actor.access_mode != "active":
        raise _error("operator_required", "forbidden", "需要有效的正式通知运维身份")
    if not any(row.role_code == "admin" and row.scope_type == "national" and row.scope_id == "*"
               for row in actor.assignments) or not actor.allows(
                   db, "notification_delivery", action, target_scope_type="national", target_scope_id="*"):
        raise _error("operator_forbidden", "forbidden", "仅总部通知运维管理员可操作来源异常")


def _scope():
    return (AuditEvent.stream_key == failures.STREAM, AuditEvent.aggregate_type == failures.AGGREGATE)


def _record(failure: AuditEvent, latest: AuditEvent) -> SourceRecord:
    def require(condition):
        if not condition:
            raise ValueError("invalid source audit")

    try:
        data = failure.after_jsonb
        outbox_id = UUID(data["outbox_id"])
        require(failure.aggregate_id == outbox_id.hex)
        require(data["schema"] == "inventory-notification-source-failure.v1")
        require(_SHA256.fullmatch(data["source_sha256"]))
        require(data["code"] in (failures.SOURCE_INVALID, failures.EVENT_CONFLICT))
        require(latest.aggregate_id == failure.aggregate_id and latest.stream_version >= failure.stream_version)
        result = SourceRecord(outbox_id, failure.id, latest.id, data["source_sha256"], data["code"],
                              "blocked", _utc(failure.occurred_at), None, None, None)
        if latest.action == RECHECK_ACTION:
            after = latest.after_jsonb
            require(after["schema"] == "inventory-notification-source-recheck.v1")
            require(after["failure_audit_id"] == str(failure.id))
            require(after["source_sha256"] == result.source_sha256)
            require(_SHA256.fullmatch(after["observed_source_sha256"]))
            require(after["status"] in ("blocked", "projected"))
            require((after["observed_source_sha256"] != result.source_sha256) == (after["code"] == SOURCE_CHANGED))
            if after["status"] == "projected":
                require(after["code"] is None)
                event_id = UUID(after["event_id"])
                require(type(after["recipient_count"]) is int and after["recipient_count"] >= 0)
            else:
                require(after["code"] in _CODES and after["event_id"] is None and after["recipient_count"] is None)
                event_id = None
            result = replace(result, status=after["status"], code=after["code"], event_id=event_id,
                             recipient_count=after["recipient_count"], last_checked_at=_utc(latest.occurred_at))
        else:
            require(latest.id == failure.id)
        return result
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _error("audit_invalid", "service_unavailable", "来源隔离审计不完整，无法安全复核") from exc


def list_inventory_notification_sources(
    db: Session, *, actor: FormalPrincipal, limit: int = 50, after_id: UUID | None = None,
) -> SourcePage:
    _require_operator(db, actor, "read")
    if type(limit) is not int or not 1 <= limit <= 100 or (after_id is not None and not isinstance(after_id, UUID)):
        raise _error("query_invalid", "invalid_request", "来源异常分页参数无效")
    statement = select(AuditEvent).where(*_scope(), AuditEvent.action == failures.BLOCKED_ACTION)
    if after_id is not None:
        cursor = db.scalar(statement.where(AuditEvent.id == after_id))
        if cursor is None:
            raise _error("cursor_invalid", "not_found", "来源异常分页位置不存在")
        statement = statement.where(AuditEvent.stream_version < cursor.stream_version)
    rows = tuple(db.scalars(statement.order_by(AuditEvent.stream_version.desc()).limit(limit + 1)))
    page = rows[:limit]
    if not page:
        return SourcePage((), None)
    # Fetch only the latest audit per object on this page, with no Python-side
    # full-history scan. PG EXPLAIN/large-history cost remains a separate gate.
    versions = select(func.max(AuditEvent.stream_version)).where(
        *_scope(), AuditEvent.action.in_(_ACTIONS),
        AuditEvent.aggregate_id.in_([row.aggregate_id for row in page]),
    ).group_by(AuditEvent.aggregate_id)
    latest = {row.aggregate_id: row for row in db.scalars(select(AuditEvent).where(
        *_scope(), AuditEvent.stream_version.in_(versions)))}
    return SourcePage(tuple(_record(row, latest[row.aggregate_id]) for row in page),
                      page[-1].id if len(rows) > limit else None)


def recheck_inventory_notification_source(
    db: Session, *, actor: FormalPrincipal, outbox_id: UUID, expected_audit_id: UUID,
    expected_source_sha256: str, reason: str, idempotency_key: str, request_id: str,
) -> SourceRecheckResult:
    _require_operator(db, actor, "retry")
    if (not isinstance(outbox_id, UUID) or not isinstance(expected_audit_id, UUID)
            or not isinstance(expected_source_sha256, str) or not _SHA256.fullmatch(expected_source_sha256)
            or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500):
        raise _error("recheck_invalid", "invalid_request", "来源复核需要原对象版本、摘要及原因")
    for value, minimum, maximum in ((idempotency_key, 16, 128), (request_id, 8, 160)):
        if not isinstance(value, str) or not minimum <= len(value) <= maximum or not _HEADER.fullmatch(value):
            raise _error("recheck_headers_invalid", "invalid_request", "来源复核的幂等键或请求标识无效")
    reason = reason.strip()
    command = {"actor_user_id": actor.user_id, "outbox_id": str(outbox_id),
               "expected_audit_id": str(expected_audit_id), "source_sha256": expected_source_sha256, "reason": reason}
    command_hash = hashlib.sha256(json.dumps(command, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    failures.ensure_outer_transaction(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    if not failures.try_source_lock(db, outbox_id):
        raise _error("source_busy", "conflict", "该来源正在处理中，请刷新后再复核")
    # Re-evaluate current authority after taking locks; never trust an old
    # request principal when an account or assignment was revoked meanwhile.
    now = _database_now(db)
    try:
        current_actor = load_formal_principal(db, actor.user_id, now=now)
    except FormalAccessError as exc:
        raise _error("operator_revoked", "forbidden", "当前正式运维身份已失效") from exc
    _require_operator(db, current_actor, "retry")
    failure = failures.source_failure(db, outbox_id)
    if failure is None:
        raise _error("source_not_found", "not_found", "该来源没有可复核的隔离记录")
    prior = tuple(db.scalars(select(AuditEvent).where(
        *_scope(), AuditEvent.aggregate_id == outbox_id.hex, AuditEvent.action == RECHECK_ACTION,
        AuditEvent.actor_user_id == actor.user_id,
        or_(AuditEvent.after_jsonb["idempotency_sha256"].as_string() == key_hash, AuditEvent.request_id == request_id),
    )))
    if prior:
        if len(prior) != 1 or prior[0].after_jsonb.get("command_sha256") != command_hash:
            raise _error("recheck_conflict", "conflict", "该对象的幂等键或请求标识已用于其他复核内容")
        return SourceRecheckResult(_record(failure, prior[0]), True)
    latest = db.scalars(select(AuditEvent).where(
        *_scope(), AuditEvent.aggregate_id == outbox_id.hex, AuditEvent.action.in_(_ACTIONS),
    ).order_by(AuditEvent.stream_version.desc()).limit(1)).one()
    previous = _record(failure, latest)
    if latest.id != expected_audit_id or previous.source_sha256 != expected_source_sha256:
        raise _error("source_stale", "precondition_failed", "来源复核记录已变化，请重新查询")
    if previous.status == "projected":
        raise _error("source_projected", "conflict", "该来源已生成通知，请查看独立投递记录")
    observed_source_sha256 = failures.source_fingerprint(db, outbox_id)
    code = SOURCE_CHANGED if observed_source_sha256 != previous.source_sha256 else None
    projected = None
    if code is None:
        try:
            with db.begin_nested():
                projected = project_inventory_notification(db, outbox_id=outbox_id, now=now)
        except (InventoryNotificationError, NotificationEventError) as exc:
            code = failures.EVENT_CONFLICT if isinstance(exc, NotificationEventError) else failures.SOURCE_INVALID
        if projected is None and code is None:
            raise _error("source_busy", "conflict", "该库存交易正在处理中，请刷新后再复核")
    audit = append_audit_event(
        db, stream_key=failures.STREAM, actor_user_id=current_actor.user_id, action=RECHECK_ACTION,
        aggregate_type=failures.AGGREGATE, aggregate_id=outbox_id.hex,
        before_jsonb={"latest_audit_id": str(latest.id)},
        after_jsonb={"schema": "inventory-notification-source-recheck.v1",
            "failure_audit_id": str(failure.id), "source_sha256": previous.source_sha256,
            "observed_source_sha256": observed_source_sha256,
            "status": "projected" if projected else "blocked", "code": code,
            "event_id": str(projected.event_id) if projected else None,
            "recipient_count": projected.recipient_count if projected else None,
            "reason": reason, "idempotency_sha256": key_hash, "command_sha256": command_hash},
        request_id=request_id, occurred_at=now, created_at=now,
    )
    return SourceRecheckResult(_record(failure, audit), False)
