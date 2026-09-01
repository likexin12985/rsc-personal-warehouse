"""Reviewed, local-only role provisioning policy.

This service applies the role policy explicitly confirmed for the V1.0
identity cutover.  It deliberately does not create users or identities, does
not change account status, and never consults the v0.9 ``role``, ``province``
or ``mobile`` columns.  The caller owns the surrounding transaction.  Every
actual grant is tied to a current formal nationwide administrator, an
idempotency record, a state transition and the append-only authorization audit
chain; the service flushes but never commits.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, load_formal_principal
from ..foundation_models import (
    Organization,
    Person,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..models import User
from .audit_chain import AuditChainError, append_audit_event


TECHNICIAN_ROLE_CODE = "technician"
ADMIN_ROLE_CODE = "admin"
PROVINCIAL_MANAGER_ROLE_CODE = "provincial_manager"

# Exact names explicitly confirmed by the user.  Name comparison is performed
# by the database with no trimming, fuzzy matching, aliases, mobile matching or
# fallback to the legacy user record.
HEADQUARTERS_ADMIN_NAMES: tuple[str, ...] = (
    "李珂鑫",
    "张鑫",
    "张洋洋",
    "张福利",
)

AUTO_PROVISIONED_ROLE_CODES = frozenset(
    {TECHNICIAN_ROLE_CODE, ADMIN_ROLE_CODE}
)
MANUAL_ONLY_ROLE_CODES = frozenset({PROVINCIAL_MANAGER_ROLE_CODE})
TECHNICIAN_INTERNAL_ORG_TYPES = frozenset(
    {"headquarters", "region_company", "department"}
)

ASSIGNMENT_REASON_TECHNICIAN = (
    "confirmed policy: uniquely bound person receives technician role"
)
ASSIGNMENT_REASON_ADMIN = (
    "confirmed policy: named active headquarters person receives admin role"
)

EFFECTIVE_ASSIGNMENT_STATUSES = ("scheduled", "active")
AUTHORIZATION_AUDIT_STREAM_KEY = "authorization"
ROLE_ASSIGNMENT_AGGREGATE_TYPE = "role_assignment"
POLICY_RUN_AGGREGATE_TYPE = "role_provisioning_run"
POLICY_RUN_AGGREGATE_ID = "confirmed-default-role-policy-v1"
POLICY_VERSION = "2026-08-30-v1"
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]+$")


class RoleProvisioningError(RuntimeError):
    """The policy cannot be safely evaluated or applied."""


@dataclass(frozen=True)
class ProvisioningOutcome:
    """One deterministic policy decision for one person/role combination."""

    role_code: str
    person_name: str
    status: str
    detail: str
    person_id: uuid.UUID | None = None
    user_id: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None
    assignment_id: uuid.UUID | None = None


@dataclass(frozen=True)
class RoleProvisioningReport:
    """Complete result of one policy application attempt."""

    actor_user_id: str
    evaluated_at: datetime
    admin_batch_status: str
    technician_outcomes: tuple[ProvisioningOutcome, ...]
    administrator_outcomes: tuple[ProvisioningOutcome, ...]
    manual_only_role_codes: tuple[str, ...] = (
        PROVINCIAL_MANAGER_ROLE_CODE,
    )
    replayed: bool = False

    @property
    def created_count(self) -> int:
        return sum(
            outcome.status == "created"
            for outcome in self.technician_outcomes
            + self.administrator_outcomes
        )


@dataclass(frozen=True)
class _GrantPreflight:
    outcome: ProvisioningOutcome
    can_create: bool


@dataclass(frozen=True)
class _ProvisioningWriteContext:
    root_storage_key: str
    request_hash: str
    request_id: str


def apply_confirmed_role_policy(
    db: Session,
    *,
    actor_user_id: str,
    idempotency_key: str,
    request_id: str,
    now: datetime | None = None,
) -> RoleProvisioningReport:
    """Apply the confirmed default and named-administrator role policy.

    The function flushes successful changes but never commits.  It does not
    bootstrap the first administrator: the actor must already have a current
    formal ``admin/national:*`` grant.  That deliberate boundary leaves initial
    administrator activation to a separately authorized, stable-person-id
    migration rather than an implicit name-based elevation.

    Technician grants are evaluated independently for every person.
    Administrator grants are written only after every configured name resolves
    to exactly one active headquarters person, exactly one bound user, and a
    safe assignment state.  A repeated actor/idempotency key with the same
    fixed policy returns the recorded report without reevaluating newly synced
    people; a new policy run requires a new key.
    """

    evaluated_at = _as_utc(now or datetime.now(timezone.utc))
    actor = _require_current_national_admin(db, actor_user_id, evaluated_at)
    checked_key = _require_safe_coordinate(
        "idempotency_key", idempotency_key, minimum=16, maximum=128
    )
    checked_request_id = _require_safe_coordinate(
        "request_id", request_id, minimum=8, maximum=160
    )
    root_storage_key = _idempotency_storage_key(actor.id, checked_key)
    request_hash = _policy_request_hash(actor.id)
    replay = _load_policy_replay(db, root_storage_key, request_hash)
    if replay is not None:
        return replace(replay, replayed=True)
    write_context = _ProvisioningWriteContext(
        root_storage_key=root_storage_key,
        request_hash=request_hash,
        request_id=checked_request_id,
    )
    _ensure_sqlite_outer_transaction_for_savepoints(db)

    try:
        with db.begin_nested():
            technician_role = _require_active_role(db, TECHNICIAN_ROLE_CODE)
            admin_role = _require_active_role(db, ADMIN_ROLE_CODE)
            technician_outcomes = _apply_technician_policy(
                db,
                role=technician_role,
                actor=actor,
                write_context=write_context,
                now=evaluated_at,
            )
            admin_batch_status, administrator_outcomes = _apply_admin_policy(
                db,
                role=admin_role,
                actor=actor,
                write_context=write_context,
                now=evaluated_at,
            )
            report = RoleProvisioningReport(
                actor_user_id=actor.id,
                evaluated_at=evaluated_at,
                admin_batch_status=admin_batch_status,
                technician_outcomes=tuple(technician_outcomes),
                administrator_outcomes=tuple(administrator_outcomes),
            )
            _record_policy_run(
                db,
                report=report,
                write_context=write_context,
            )
            db.flush()
        return report
    except AuditChainError as exc:
        raise RoleProvisioningError(
            "授权审计链不可用，默认角色开通未写入"
        ) from exc
    except IntegrityError as exc:
        replay = _load_policy_replay(db, root_storage_key, request_hash)
        if replay is not None:
            return replace(replay, replayed=True)
        raise RoleProvisioningError(
            "默认角色开通发生并发冲突，请重新读取后使用新幂等键"
        ) from exc


def _apply_technician_policy(
    db: Session,
    *,
    role: Role,
    actor: User,
    write_context: _ProvisioningWriteContext,
    now: datetime,
) -> list[ProvisioningOutcome]:
    people = list(
        db.scalars(select(Person).order_by(Person.name, Person.id)).all()
    )
    organizations = {
        organization.id: organization
        for organization in db.scalars(select(Organization)).all()
    }
    users_by_person = _users_by_person(db)
    outcomes: list[ProvisioningOutcome] = []

    for person in people:
        organization = organizations.get(person.organization_id)
        if organization is None:
            outcomes.append(
                ProvisioningOutcome(
                    role_code=role.code,
                    person_name=person.name,
                    person_id=person.id,
                    status="organization_unavailable",
                    detail="人员所属组织不存在；已失败关闭且未授予工程师角色",
                )
            )
            continue
        if organization.org_type == "external_approval_org":
            outcomes.append(
                ProvisioningOutcome(
                    role_code=role.code,
                    person_name=person.name,
                    person_id=person.id,
                    status="excluded_external_identity",
                    detail="星星外部审批人员不得获得工程师或库存权限",
                )
            )
            continue
        if organization.status != "active":
            outcomes.append(
                ProvisioningOutcome(
                    role_code=role.code,
                    person_name=person.name,
                    person_id=person.id,
                    status="inactive_organization",
                    detail="人员所属内部组织未启用；已失败关闭且未授予工程师角色",
                )
            )
            continue
        if organization.org_type not in TECHNICIAN_INTERNAL_ORG_TYPES:
            outcomes.append(
                ProvisioningOutcome(
                    role_code=role.code,
                    person_name=person.name,
                    person_id=person.id,
                    status="ineligible_organization",
                    detail="人员所属组织类型不在工程师默认授权范围内",
                )
            )
            continue

        users = users_by_person.get(person.id, [])
        if len(users) != 1:
            status = "unbound_person" if not users else "ambiguous_person_binding"
            outcomes.append(
                ProvisioningOutcome(
                    role_code=role.code,
                    person_name=person.name,
                    person_id=person.id,
                    status=status,
                    detail=(
                        "人员没有唯一绑定账号；未创建账号或角色授权"
                        if not users
                        else "人员绑定了多个账号；已阻断角色授权"
                    ),
                )
            )
            continue

        user = users[0]
        scope_id = str(person.id)
        preflight = _preflight_grant(
            db,
            user=user,
            person_name=person.name,
            person_id=person.id,
            role=role,
            scope_type="person",
            scope_id=scope_id,
            now=now,
        )
        if not preflight.can_create:
            outcomes.append(preflight.outcome)
            continue

        outcomes.append(
            _create_assignment(
                db,
                preflight=preflight,
                user=user,
                role=role,
                actor=actor,
                write_context=write_context,
                scope_type="person",
                scope_id=scope_id,
                reason=ASSIGNMENT_REASON_TECHNICIAN,
                now=now,
            )
        )

    return outcomes


def _apply_admin_policy(
    db: Session,
    *,
    role: Role,
    actor: User,
    write_context: _ProvisioningWriteContext,
    now: datetime,
) -> tuple[str, list[ProvisioningOutcome]]:
    preflights: list[tuple[_GrantPreflight, User | None, Person | None]] = []

    for name in HEADQUARTERS_ADMIN_NAMES:
        people = list(
            db.scalars(
                select(Person)
                .join(Organization, Organization.id == Person.organization_id)
                .where(
                    Person.name == name,
                    Person.employment_status == "active",
                    Organization.org_type == "headquarters",
                    Organization.status == "active",
                )
            ).all()
        )
        if len(people) != 1:
            preflights.append(
                (
                    _GrantPreflight(
                        outcome=ProvisioningOutcome(
                            role_code=role.code,
                            person_name=name,
                            status="admin_name_resolution_failed",
                            detail=(
                                "有效总部组织内的在职人员必须按精确姓名恰好匹配一人；"
                                f"当前匹配 {len(people)} 人"
                            ),
                            scope_type="national",
                            scope_id="*",
                        ),
                        can_create=False,
                    ),
                    None,
                    None,
                )
            )
            continue

        person = people[0]
        users = list(
            db.scalars(select(User).where(User.person_id == person.id)).all()
        )
        if len(users) != 1:
            preflights.append(
                (
                    _GrantPreflight(
                        outcome=ProvisioningOutcome(
                            role_code=role.code,
                            person_name=name,
                            person_id=person.id,
                            status="admin_user_resolution_failed",
                            detail=(
                                "总部管理员人员必须恰好绑定一个账号；"
                                f"当前绑定 {len(users)} 个"
                            ),
                            scope_type="national",
                            scope_id="*",
                        ),
                        can_create=False,
                    ),
                    None,
                    person,
                )
            )
            continue

        user = users[0]
        preflights.append(
            (
                _preflight_grant(
                    db,
                    user=user,
                    person_name=person.name,
                    person_id=person.id,
                    role=role,
                    scope_type="national",
                    scope_id="*",
                    now=now,
                ),
                user,
                person,
            )
        )

    if any(
        item.outcome.status
        not in {"ready", "existing"}
        for item, _, _ in preflights
    ):
        return "blocked", [
            _admin_batch_blocked_outcome(item.outcome)
            if item.outcome.status == "ready"
            else item.outcome
            for item, _, _ in preflights
        ]

    created: list[ProvisioningOutcome] = []
    try:
        with db.begin_nested():
            for preflight, user, person in preflights:
                if not preflight.can_create:
                    created.append(preflight.outcome)
                    continue
                assert user is not None and person is not None
                created.append(
                    _create_assignment_without_savepoint(
                        db,
                        preflight=preflight,
                        user=user,
                        role=role,
                        actor=actor,
                        write_context=write_context,
                        scope_type="national",
                        scope_id="*",
                        reason=ASSIGNMENT_REASON_ADMIN,
                        now=now,
                    )
                )
            db.flush()
    except IntegrityError:
        # All administrator writes are rolled back to the savepoint.  Do not
        # claim any individual creation when a concurrent change wins.
        return "blocked", [
            ProvisioningOutcome(
                role_code=item.outcome.role_code,
                person_name=item.outcome.person_name,
                person_id=item.outcome.person_id,
                user_id=item.outcome.user_id,
                scope_type="national",
                scope_id="*",
                status="admin_batch_concurrent_conflict",
                detail="管理员批次写入出现并发冲突；整个管理员批次未写入",
            )
            for item, _, _ in preflights
        ]

    return (
        "applied" if any(row.status == "created" for row in created) else "idempotent",
        created,
    )


def _preflight_grant(
    db: Session,
    *,
    user: User,
    person_name: str,
    person_id: uuid.UUID,
    role: Role,
    scope_type: str,
    scope_id: str,
    now: datetime,
) -> _GrantPreflight:
    current = list(
        db.scalars(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role_id == role.id,
                RoleAssignment.status.in_(EFFECTIVE_ASSIGNMENT_STATUSES),
                RoleAssignment.revoked_at.is_(None),
                RoleAssignment.valid_from <= now,
                or_(
                    RoleAssignment.valid_to.is_(None),
                    RoleAssignment.valid_to > now,
                ),
            )
        ).all()
    )
    exact = [
        assignment
        for assignment in current
        if assignment.scope_type == scope_type and assignment.scope_id == scope_id
    ]
    if len(current) == 1 and len(exact) == 1:
        assignment = exact[0]
        return _GrantPreflight(
            outcome=ProvisioningOutcome(
                role_code=role.code,
                person_name=person_name,
                person_id=person_id,
                user_id=user.id,
                scope_type=scope_type,
                scope_id=scope_id,
                assignment_id=assignment.id,
                status="existing",
                detail="当前已有完全一致的角色范围授权；未重复写入",
            ),
            can_create=False,
        )
    if current:
        return _GrantPreflight(
            outcome=ProvisioningOutcome(
                role_code=role.code,
                person_name=person_name,
                person_id=person_id,
                user_id=user.id,
                scope_type=scope_type,
                scope_id=scope_id,
                status="assignment_scope_conflict",
                detail=(
                    "当前角色授权存在重复或非法数据范围；未新增或覆盖授权"
                ),
            ),
            can_create=False,
        )

    future = list(
        db.scalars(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role_id == role.id,
                RoleAssignment.status.in_(EFFECTIVE_ASSIGNMENT_STATUSES),
                RoleAssignment.revoked_at.is_(None),
                RoleAssignment.valid_from > now,
                or_(
                    RoleAssignment.valid_to.is_(None),
                    RoleAssignment.valid_to > now,
                ),
            )
        ).all()
    )
    if future:
        return _GrantPreflight(
            outcome=ProvisioningOutcome(
                role_code=role.code,
                person_name=person_name,
                person_id=person_id,
                user_id=user.id,
                scope_type=scope_type,
                scope_id=scope_id,
                status="future_assignment_conflict",
                detail="角色已有未来授权；为避免范围重叠，未新增当前授权",
            ),
            can_create=False,
        )

    stale_nonterminal = list(
        db.scalars(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role_id == role.id,
                RoleAssignment.status.in_(EFFECTIVE_ASSIGNMENT_STATUSES),
                RoleAssignment.revoked_at.is_(None),
                RoleAssignment.valid_from <= now,
                RoleAssignment.valid_to.is_not(None),
                RoleAssignment.valid_to <= now,
            )
        ).all()
    )
    if stale_nonterminal:
        return _GrantPreflight(
            outcome=ProvisioningOutcome(
                role_code=role.code,
                person_name=person_name,
                person_id=person_id,
                user_id=user.id,
                scope_type=scope_type,
                scope_id=scope_id,
                status="assignment_state_conflict",
                detail="角色授权有效期已结束但状态未终结；未新增或覆盖授权",
            ),
            can_create=False,
        )

    return _GrantPreflight(
        outcome=ProvisioningOutcome(
            role_code=role.code,
            person_name=person_name,
            person_id=person_id,
            user_id=user.id,
            scope_type=scope_type,
            scope_id=scope_id,
            status="ready",
            detail="角色授权已通过写入前校验",
        ),
        can_create=True,
    )


def _create_assignment(
    db: Session,
    *,
    preflight: _GrantPreflight,
    user: User,
    role: Role,
    actor: User,
    write_context: _ProvisioningWriteContext,
    scope_type: str,
    scope_id: str,
    reason: str,
    now: datetime,
) -> ProvisioningOutcome:
    try:
        with db.begin_nested():
            result = _create_assignment_without_savepoint(
                db,
                preflight=preflight,
                user=user,
                role=role,
                actor=actor,
                write_context=write_context,
                scope_type=scope_type,
                scope_id=scope_id,
                reason=reason,
                now=now,
            )
            db.flush()
            return result
    except IntegrityError:
        # A target failure must not suppress technician provisioning for other
        # uniquely bound people.  The savepoint also restores the version bump.
        return ProvisioningOutcome(
            role_code=role.code,
            person_name=preflight.outcome.person_name,
            person_id=preflight.outcome.person_id,
            user_id=user.id,
            scope_type=scope_type,
            scope_id=scope_id,
            status="concurrent_assignment_conflict",
            detail="角色授权写入出现并发冲突；该人员未写入",
        )


def _create_assignment_without_savepoint(
    db: Session,
    *,
    preflight: _GrantPreflight,
    user: User,
    role: Role,
    actor: User,
    write_context: _ProvisioningWriteContext,
    scope_type: str,
    scope_id: str,
    reason: str,
    now: datetime,
) -> ProvisioningOutcome:
    assignment = RoleAssignment(
        user_id=user.id,
        role_id=role.id,
        scope_type=scope_type,
        scope_id=scope_id,
        valid_from=now,
        valid_to=None,
        status="active",
        assigned_by=actor.id,
        revoked_at=None,
        revoked_by=None,
        reason=reason,
    )
    db.add(assignment)
    user.authorization_version += 1
    db.flush()
    outcome = ProvisioningOutcome(
        role_code=role.code,
        person_name=preflight.outcome.person_name,
        person_id=preflight.outcome.person_id,
        user_id=user.id,
        scope_type=scope_type,
        scope_id=scope_id,
        assignment_id=assignment.id,
        status="created",
        detail="已创建正式角色范围授权并递增账号授权版本",
    )
    after_snapshot = {
        "assignment_id": str(assignment.id),
        "user_id": assignment.user_id,
        "person_id": (
            str(preflight.outcome.person_id)
            if preflight.outcome.person_id is not None
            else None
        ),
        "role_code": role.code,
        "scope_type": assignment.scope_type,
        "scope_id": assignment.scope_id,
        "valid_from": _canonical_timestamp(assignment.valid_from),
        "valid_to": None,
        "status": assignment.status,
        "assigned_by": assignment.assigned_by,
        "reason": assignment.reason,
        "authorization_version": user.authorization_version,
    }
    audit_event = append_audit_event(
        db,
        stream_key=AUTHORIZATION_AUDIT_STREAM_KEY,
        actor_user_id=actor.id,
        action="role_assignment.created",
        aggregate_type=ROLE_ASSIGNMENT_AGGREGATE_TYPE,
        aggregate_id=str(assignment.id),
        before_jsonb=None,
        after_jsonb=after_snapshot,
        request_id=write_context.request_id,
        occurred_at=now,
    )
    db.add(
        StateTransitionEvent(
            aggregate_type=ROLE_ASSIGNMENT_AGGREGATE_TYPE,
            aggregate_id=str(assignment.id),
            from_status=None,
            to_status="active",
            reason=reason,
            actor_id=actor.id,
            idempotency_key=_derived_assignment_event_key(
                write_context.root_storage_key,
                assignment.id,
            ),
            occurred_at=now,
            metadata_jsonb={
                "operation": "confirmed_role_policy_assignment_create",
                "policy_version": POLICY_VERSION,
                "request_hash": write_context.request_hash,
                "request_id": write_context.request_id,
                "role_code": role.code,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "audit_event_id": str(audit_event.id),
                "authorization_version": user.authorization_version,
            },
        )
    )
    db.flush()
    return outcome


def _admin_batch_blocked_outcome(
    outcome: ProvisioningOutcome,
) -> ProvisioningOutcome:
    return ProvisioningOutcome(
        role_code=outcome.role_code,
        person_name=outcome.person_name,
        person_id=outcome.person_id,
        user_id=outcome.user_id,
        scope_type=outcome.scope_type,
        scope_id=outcome.scope_id,
        assignment_id=outcome.assignment_id,
        status="admin_batch_blocked",
        detail="另一名配置管理员未通过预检；整个管理员批次未写入",
    )


def _users_by_person(db: Session) -> dict[uuid.UUID, list[User]]:
    result: dict[uuid.UUID, list[User]] = {}
    for user in db.scalars(
        select(User).where(User.person_id.is_not(None)).order_by(User.id)
    ):
        assert user.person_id is not None
        result.setdefault(user.person_id, []).append(user)
    return result


def _record_policy_run(
    db: Session,
    *,
    report: RoleProvisioningReport,
    write_context: _ProvisioningWriteContext,
) -> None:
    db.add(
        StateTransitionEvent(
            aggregate_type=POLICY_RUN_AGGREGATE_TYPE,
            aggregate_id=POLICY_RUN_AGGREGATE_ID,
            from_status=None,
            to_status="completed",
            reason="confirmed default technician and named administrator policy",
            actor_id=report.actor_user_id,
            idempotency_key=write_context.root_storage_key,
            occurred_at=report.evaluated_at,
            metadata_jsonb={
                "operation": "apply_confirmed_role_policy",
                "policy_version": POLICY_VERSION,
                "request_hash": write_context.request_hash,
                "request_id": write_context.request_id,
                "report": _report_to_json(report),
            },
        )
    )


def _load_policy_replay(
    db: Session,
    storage_key: str,
    request_hash: str,
) -> RoleProvisioningReport | None:
    event = db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.idempotency_key == storage_key
        )
    )
    if event is None:
        return None
    metadata = event.metadata_jsonb
    if (
        event.aggregate_type != POLICY_RUN_AGGREGATE_TYPE
        or event.aggregate_id != POLICY_RUN_AGGREGATE_ID
        or not isinstance(metadata, dict)
        or metadata.get("operation") != "apply_confirmed_role_policy"
        or metadata.get("policy_version") != POLICY_VERSION
        or metadata.get("request_hash") != request_hash
    ):
        raise RoleProvisioningError("该幂等键已用于不同的授权请求")
    try:
        report = _report_from_json(metadata["report"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RoleProvisioningError("角色开通幂等记录不完整，已失败关闭") from exc
    if report.actor_user_id != event.actor_id:
        raise RoleProvisioningError("角色开通幂等记录的操作人不一致")
    return report


def _report_to_json(report: RoleProvisioningReport) -> dict[str, object]:
    return {
        "actor_user_id": report.actor_user_id,
        "evaluated_at": _canonical_timestamp(report.evaluated_at),
        "admin_batch_status": report.admin_batch_status,
        "technician_outcomes": [
            _outcome_to_json(row) for row in report.technician_outcomes
        ],
        "administrator_outcomes": [
            _outcome_to_json(row) for row in report.administrator_outcomes
        ],
        "manual_only_role_codes": list(report.manual_only_role_codes),
    }


def _report_from_json(value: object) -> RoleProvisioningReport:
    if not isinstance(value, dict):
        raise TypeError("report is not a JSON object")
    actor_user_id = value["actor_user_id"]
    evaluated_at = value["evaluated_at"]
    admin_batch_status = value["admin_batch_status"]
    technician_rows = value["technician_outcomes"]
    administrator_rows = value["administrator_outcomes"]
    manual_roles = value["manual_only_role_codes"]
    if not isinstance(actor_user_id, str) or not actor_user_id:
        raise TypeError("actor_user_id is invalid")
    if not isinstance(evaluated_at, str):
        raise TypeError("evaluated_at is invalid")
    if not isinstance(admin_batch_status, str) or not admin_batch_status:
        raise TypeError("admin_batch_status is invalid")
    if not isinstance(technician_rows, list) or not isinstance(
        administrator_rows, list
    ):
        raise TypeError("outcomes are invalid")
    if not isinstance(manual_roles, list) or not all(
        isinstance(item, str) for item in manual_roles
    ):
        raise TypeError("manual_only_role_codes are invalid")
    return RoleProvisioningReport(
        actor_user_id=actor_user_id,
        evaluated_at=_parse_canonical_timestamp(evaluated_at),
        admin_batch_status=admin_batch_status,
        technician_outcomes=tuple(
            _outcome_from_json(row) for row in technician_rows
        ),
        administrator_outcomes=tuple(
            _outcome_from_json(row) for row in administrator_rows
        ),
        manual_only_role_codes=tuple(manual_roles),
    )


def _outcome_to_json(outcome: ProvisioningOutcome) -> dict[str, object]:
    return {
        "role_code": outcome.role_code,
        "person_name": outcome.person_name,
        "status": outcome.status,
        "detail": outcome.detail,
        "person_id": str(outcome.person_id) if outcome.person_id else None,
        "user_id": outcome.user_id,
        "scope_type": outcome.scope_type,
        "scope_id": outcome.scope_id,
        "assignment_id": (
            str(outcome.assignment_id) if outcome.assignment_id else None
        ),
    }


def _outcome_from_json(value: object) -> ProvisioningOutcome:
    if not isinstance(value, dict):
        raise TypeError("outcome is not a JSON object")
    required = ("role_code", "person_name", "status", "detail")
    if any(not isinstance(value.get(field), str) for field in required):
        raise TypeError("outcome required field is invalid")
    person_id = value.get("person_id")
    assignment_id = value.get("assignment_id")
    optional_strings = ("user_id", "scope_type", "scope_id")
    if any(
        value.get(field) is not None and not isinstance(value.get(field), str)
        for field in optional_strings
    ):
        raise TypeError("outcome optional field is invalid")
    return ProvisioningOutcome(
        role_code=value["role_code"],
        person_name=value["person_name"],
        status=value["status"],
        detail=value["detail"],
        person_id=uuid.UUID(person_id) if isinstance(person_id, str) else None,
        user_id=value.get("user_id"),
        scope_type=value.get("scope_type"),
        scope_id=value.get("scope_id"),
        assignment_id=(
            uuid.UUID(assignment_id) if isinstance(assignment_id, str) else None
        ),
    )


def _idempotency_storage_key(actor_user_id: str, raw_key: str) -> str:
    return hashlib.sha256(f"{actor_user_id}{raw_key}".encode("utf-8")).hexdigest()


def _derived_assignment_event_key(
    root_storage_key: str,
    assignment_id: uuid.UUID,
) -> str:
    return hashlib.sha256(
        f"{root_storage_key}:assignment:{assignment_id}:active".encode("utf-8")
    ).hexdigest()


def _policy_request_hash(actor_user_id: str) -> str:
    payload = {
        "operation": "apply_confirmed_role_policy",
        "policy_version": POLICY_VERSION,
        "actor_user_id": actor_user_id,
        "technician_role_code": TECHNICIAN_ROLE_CODE,
        "administrator_names": list(HEADQUARTERS_ADMIN_NAMES),
        "administrator_role_code": ADMIN_ROLE_CODE,
        "manual_only_role_codes": sorted(MANUAL_ONLY_ROLE_CODES),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_safe_coordinate(
    field: str,
    value: str,
    *,
    minimum: int,
    maximum: int,
) -> str:
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= maximum
        or _SAFE_KEY.fullmatch(value) is None
    ):
        raise RoleProvisioningError(
            f"{field} 必须是 {minimum}-{maximum} 位安全字符"
        )
    return value


def _ensure_sqlite_outer_transaction_for_savepoints(db: Session) -> None:
    """Keep test/dev SQLite savepoints inside a real outer transaction.

    Python's sqlite3 driver may defer the physical ``BEGIN`` across reads.  If
    the first write then happens inside ``SAVEPOINT``, releasing that savepoint
    can commit it immediately and defeat the caller-owned rollback contract.
    PostgreSQL is unaffected; for SQLite only, explicitly begin when the raw
    driver confirms no physical transaction exists yet.
    """

    connection = db.connection()
    if connection.dialect.name != "sqlite":
        return
    driver_connection = connection.connection.driver_connection
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _require_current_national_admin(
    db: Session,
    actor_user_id: str,
    now: datetime,
) -> User:
    if not isinstance(actor_user_id, str) or not actor_user_id.strip():
        raise RoleProvisioningError("必须显式提供 actor_user_id")
    actor = db.get(User, actor_user_id)
    if actor is None:
        raise RoleProvisioningError("actor_user_id 对应账号不存在")
    try:
        principal = load_formal_principal(db, actor.id, now=now)
    except FormalAccessError as exc:
        raise RoleProvisioningError(
            "角色开通执行人没有当前有效的正式权限上下文"
        ) from exc
    is_national_admin = any(
        assignment.role_code == ADMIN_ROLE_CODE
        and assignment.scope_type == "national"
        and assignment.scope_id == "*"
        for assignment in principal.assignments
    )
    if (
        principal.access_mode != "active"
        or principal.account_status != "active"
        or principal.employment_status != "active"
        or not is_national_admin
    ):
        raise RoleProvisioningError(
            "仅当前有效的蔚来总部全国管理员可执行角色开通策略"
        )
    return actor


def _require_active_role(db: Session, role_code: str) -> Role:
    roles = list(db.scalars(select(Role).where(Role.code == role_code)).all())
    if len(roles) != 1 or roles[0].status != "active":
        raise RoleProvisioningError(
            f"固定角色 {role_code} 必须恰好存在一个有效定义"
        )
    return roles[0]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_canonical_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
