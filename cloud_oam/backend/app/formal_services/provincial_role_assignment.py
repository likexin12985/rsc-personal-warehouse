"""Provincial-manager assignment service for the formal V1.0 role graph.

The public functions in this module intentionally expose no ``role_code`` or
``scope_type`` argument.  Every grant is therefore fixed to the active
``provincial_manager`` role and an active ``region_company`` organization
scope.  The caller owns the surrounding transaction; this service only uses
savepoints and ``flush()``, never ``commit()``.

Candidate responses contain only the minimum identifiers and display names
needed by the administrator UI.  Legacy mobile values and formal identity
hashes are neither selected into the response nor included in audit payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from ..foundation_models import (
    AuthIdentity,
    Organization,
    Person,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..models import User
from .audit_chain import AuditChainError, append_audit_event


PROVINCIAL_MANAGER_ROLE_CODE = "provincial_manager"
TECHNICIAN_ROLE_CODE = "technician"
PROVINCIAL_SCOPE_TYPE = "organization"
AUTHORIZATION_AUDIT_STREAM_KEY = "authorization"
ROLE_ASSIGNMENT_AGGREGATE_TYPE = "role_assignment"
EFFECTIVE_ASSIGNMENT_STATUSES = ("scheduled", "active")
INTERNAL_ORG_TYPES = frozenset({"headquarters", "region_company", "department"})

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class ProvincialRoleAssignmentError(RuntimeError):
    """A stable, HTTP-friendly domain failure.

    Routers may map ``category`` or ``http_status_code`` without exposing
    database exception text.  ``code`` is a stable machine-readable reason.
    """

    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported error category: {category}")
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


@dataclass(frozen=True)
class ProvincialManagerCandidate:
    """Minimal, non-sensitive administrator selection row."""

    user_id: str
    person_id: uuid.UUID
    person_name: str
    employee_no: str
    organization_id: uuid.UUID
    organization_code: str
    organization_name: str
    authorization_version: int


@dataclass(frozen=True)
class ProvincialRegionOption:
    """Minimal active regional-company option for administrator selection."""

    organization_id: uuid.UUID
    organization_code: str
    organization_name: str
    province_code: str | None


@dataclass(frozen=True)
class ProvincialRoleAssignmentResult:
    assignment_id: uuid.UUID
    target_user_id: str
    target_person_id: uuid.UUID | None
    organization_id: uuid.UUID
    role_code: str
    scope_type: str
    status: str
    valid_from: datetime
    valid_to: datetime | None
    authorization_version: int
    audit_event_id: uuid.UUID
    state_transition_event_id: uuid.UUID
    replayed: bool = False


@dataclass(frozen=True)
class ProvincialManagerAssignmentView:
    """Minimal current assignment row for administrator review."""

    assignment_id: uuid.UUID
    person_id: uuid.UUID
    person_name: str
    employee_no: str
    organization_id: uuid.UUID
    organization_code: str
    organization_name: str
    valid_from: datetime
    valid_to: datetime | None
    status: str
    authorization_version: int


def list_provincial_manager_regions(
    db: Session,
    *,
    actor: FormalPrincipal,
    now: datetime | None = None,
) -> tuple[ProvincialRegionOption, ...]:
    """List active region-company scopes without exposing source payloads."""

    effective_at = _effective_now(now)
    _require_current_national_admin(
        db,
        actor,
        effective_at,
        required_permission=("people", "read_minimal"),
    )
    organizations = db.scalars(
        select(Organization)
        .where(
            Organization.org_type == "region_company",
            Organization.status == "active",
        )
        .order_by(Organization.name, Organization.code, Organization.id)
    ).all()
    return tuple(
        ProvincialRegionOption(
            organization_id=organization.id,
            organization_code=organization.code,
            organization_name=organization.name,
            province_code=organization.province_code,
        )
        for organization in organizations
    )


def list_provincial_manager_candidates(
    db: Session,
    *,
    actor: FormalPrincipal,
    organization_id: uuid.UUID | str,
    now: datetime | None = None,
) -> tuple[ProvincialManagerCandidate, ...]:
    """Return eligible people inside one active regional organization tree."""

    effective_at = _effective_now(now)
    _require_current_national_admin(
        db,
        actor,
        effective_at,
        required_permission=("people", "read_minimal"),
    )
    target_organization = _require_active_region_company(db, organization_id)
    technician_role = _require_fixed_role(db, TECHNICIAN_ROLE_CODE, active=True)
    provincial_role = _require_fixed_role(
        db,
        PROVINCIAL_MANAGER_ROLE_CODE,
        active=True,
    )

    candidates: list[ProvincialManagerCandidate] = []
    people = db.scalars(
        select(Person)
        .where(Person.employment_status == "active")
        .order_by(Person.name, Person.id)
    ).all()
    for person in people:
        organization = db.get(Organization, person.organization_id)
        if organization is None or organization.status != "active":
            continue
        if organization.org_type not in INTERNAL_ORG_TYPES:
            continue
        if not _is_in_active_organization_subtree(
            db,
            organization.id,
            target_organization.id,
        ):
            continue

        users = list(db.scalars(select(User).where(User.person_id == person.id)).all())
        if len(users) != 1:
            continue
        user = users[0]
        if user.account_status != "active":
            continue
        if not _has_verified_active_identity(db, user.id):
            continue
        if not _has_exact_current_technician_assignment(
            db,
            user=user,
            person=person,
            technician_role=technician_role,
            now=effective_at,
        ):
            continue
        if _has_current_provincial_assignment(
            db,
            user=user,
            role=provincial_role,
            organization=target_organization,
            now=effective_at,
        ):
            continue

        candidates.append(
            ProvincialManagerCandidate(
                user_id=user.id,
                person_id=person.id,
                person_name=person.name,
                employee_no=person.employee_no,
                organization_id=organization.id,
                organization_code=organization.code,
                organization_name=organization.name,
                authorization_version=user.authorization_version,
            )
        )

    return tuple(candidates)


def list_provincial_manager_assignments(
    db: Session,
    *,
    actor: FormalPrincipal,
    organization_id: uuid.UUID | str,
    now: datetime | None = None,
) -> tuple[ProvincialManagerAssignmentView, ...]:
    """List non-terminal provincial-manager assignments for one region.

    Scheduled rows are included even before their ``valid_from`` and stale
    active rows remain visible until an explicit expiry/revocation transition
    closes them.  This mirrors the database's current-assignment uniqueness
    guard and ensures administrators can see every row that blocks a grant.
    """

    effective_at = _effective_now(now)
    _require_current_national_admin(
        db,
        actor,
        effective_at,
        required_permission=("people", "read_minimal"),
    )
    target_organization = _require_active_region_company(db, organization_id)
    role = _require_fixed_role(db, PROVINCIAL_MANAGER_ROLE_CODE, active=False)
    assignments = db.scalars(
        select(RoleAssignment)
        .where(
            RoleAssignment.role_id == role.id,
            RoleAssignment.scope_type == PROVINCIAL_SCOPE_TYPE,
            RoleAssignment.scope_id == str(target_organization.id),
            RoleAssignment.status.in_(EFFECTIVE_ASSIGNMENT_STATUSES),
            RoleAssignment.revoked_at.is_(None),
        )
        .order_by(RoleAssignment.valid_from, RoleAssignment.id)
    ).all()

    rows: list[ProvincialManagerAssignmentView] = []
    for assignment in assignments:
        user = db.get(User, assignment.user_id)
        if user is None or user.person_id is None:
            _fail(
                "assignment_principal_invalid",
                "service_unavailable",
                "省负责人授权没有有效的人员账号绑定，已失败关闭",
            )
        person = db.get(Person, user.person_id)
        if person is None:
            _fail(
                "assignment_principal_invalid",
                "service_unavailable",
                "省负责人授权绑定的人员不存在，已失败关闭",
            )
        person_organization = db.get(Organization, person.organization_id)
        if person_organization is None:
            _fail(
                "assignment_principal_invalid",
                "service_unavailable",
                "省负责人授权绑定人员的组织不存在，已失败关闭",
            )
        rows.append(
            ProvincialManagerAssignmentView(
                assignment_id=assignment.id,
                person_id=person.id,
                person_name=person.name,
                employee_no=person.employee_no,
                organization_id=person_organization.id,
                organization_code=person_organization.code,
                organization_name=person_organization.name,
                valid_from=_as_utc(assignment.valid_from),
                valid_to=(
                    _as_utc(assignment.valid_to)
                    if assignment.valid_to is not None
                    else None
                ),
                status=assignment.status,
                authorization_version=user.authorization_version,
            )
        )
    return tuple(rows)


def grant_provincial_manager(
    db: Session,
    *,
    actor: FormalPrincipal,
    target_person_id: uuid.UUID | str,
    organization_id: uuid.UUID | str,
    expected_authorization_version: int,
    valid_to: datetime | None,
    reason: str,
    idempotency_key: str,
    request_id: str,
    now: datetime | None = None,
) -> ProvincialRoleAssignmentResult:
    """Grant a provincial-manager assignment with an optional expiry.

    A non-null ``valid_to`` must be in the future; ``None`` represents a
    reviewed long-lived assignment as allowed by the formal schema.  Replays
    with the same actor/key and the same semantic request return the recorded
    result even if the transport-level ``request_id`` changes or the target
    authorization version has advanced.
    """

    effective_at = _effective_now(now)
    current_actor = _require_current_national_admin(
        db,
        actor,
        effective_at,
        required_permission=("role_assignment", "manage_provincial"),
    )
    checked_person_id = _require_uuid("target_person_id", target_person_id)
    checked_organization_id = _require_uuid("organization_id", organization_id)
    checked_version = _require_authorization_version(expected_authorization_version)
    checked_valid_to = (
        _require_aware_datetime("valid_to", valid_to)
        if valid_to is not None
        else None
    )
    if checked_valid_to is not None and checked_valid_to <= effective_at:
        _fail(
            "valid_to_not_future",
            "invalid_request",
            "省负责人授权必须提供晚于当前时间的有限有效期",
        )
    checked_reason = _require_text("reason", reason, 2000)
    checked_key = _require_text("idempotency_key", idempotency_key, 200)
    checked_request_id = _require_text("request_id", request_id, 160)

    storage_key = idempotency_storage_key(current_actor.user_id, checked_key)
    request_hash = _request_hash(
        {
            "operation": "grant",
            "actor_user_id": current_actor.user_id,
            "target_person_id": str(checked_person_id),
            "organization_id": str(checked_organization_id),
            "expected_authorization_version": checked_version,
            "valid_to": (
                _canonical_timestamp(checked_valid_to)
                if checked_valid_to is not None
                else None
            ),
            "reason": checked_reason,
        }
    )
    replay = _load_idempotent_result(db, storage_key, request_hash, "grant")
    if replay is not None:
        return replace(replay, replayed=True)

    target_organization = _require_active_region_company(db, checked_organization_id)
    role = _require_fixed_role(db, PROVINCIAL_MANAGER_ROLE_CODE, active=True)
    technician_role = _require_fixed_role(db, TECHNICIAN_ROLE_CODE, active=True)
    target_user = _resolve_and_lock_unique_user(db, checked_person_id)
    _require_expected_version(target_user, checked_version)
    target_person = _require_eligible_target(
        db,
        user=target_user,
        target_organization=target_organization,
        technician_role=technician_role,
        now=effective_at,
    )
    _require_no_nonterminal_duplicate(
        db,
        user=target_user,
        role=role,
        organization=target_organization,
        now=effective_at,
    )

    try:
        with db.begin_nested():
            assignment = RoleAssignment(
                user_id=target_user.id,
                role_id=role.id,
                scope_type=PROVINCIAL_SCOPE_TYPE,
                scope_id=str(target_organization.id),
                valid_from=effective_at,
                valid_to=checked_valid_to,
                status="active",
                assigned_by=current_actor.user_id,
                revoked_at=None,
                revoked_by=None,
                reason=checked_reason,
            )
            db.add(assignment)
            target_user.authorization_version += 1
            db.flush()

            after = _assignment_snapshot(
                assignment,
                role_code=PROVINCIAL_MANAGER_ROLE_CODE,
                authorization_version=target_user.authorization_version,
            )
            audit_event = append_audit_event(
                db,
                stream_key=AUTHORIZATION_AUDIT_STREAM_KEY,
                actor_user_id=current_actor.user_id,
                action="provincial_manager.grant",
                aggregate_type=ROLE_ASSIGNMENT_AGGREGATE_TYPE,
                aggregate_id=str(assignment.id),
                before_jsonb=None,
                after_jsonb=after,
                request_id=checked_request_id,
                occurred_at=effective_at,
            )
            transition_id = uuid.uuid4()
            result = ProvincialRoleAssignmentResult(
                assignment_id=assignment.id,
                target_user_id=target_user.id,
                target_person_id=target_person.id,
                organization_id=target_organization.id,
                role_code=PROVINCIAL_MANAGER_ROLE_CODE,
                scope_type=PROVINCIAL_SCOPE_TYPE,
                status="active",
                valid_from=effective_at,
                valid_to=checked_valid_to,
                authorization_version=target_user.authorization_version,
                audit_event_id=audit_event.id,
                state_transition_event_id=transition_id,
            )
            db.add(
                _transition_event(
                    event_id=transition_id,
                    assignment_id=assignment.id,
                    from_status=None,
                    to_status="active",
                    reason=checked_reason,
                    actor_user_id=current_actor.user_id,
                    storage_key=storage_key,
                    request_hash=request_hash,
                    request_id=checked_request_id,
                    operation="grant",
                    result=result,
                    occurred_at=effective_at,
                )
            )
            db.flush()
        return result
    except AuditChainError as exc:
        _fail(
            "audit_chain_unavailable",
            "service_unavailable",
            "授权审计链不可用，省负责人授权未写入",
            cause=exc,
        )
    except IntegrityError as exc:
        replay = _load_idempotent_result(db, storage_key, request_hash, "grant")
        if replay is not None:
            return replace(replay, replayed=True)
        _fail(
            "concurrent_assignment_conflict",
            "conflict",
            "授权期间发生并发变更，请重新读取人员授权后再操作",
            cause=exc,
        )


def revoke_provincial_manager(
    db: Session,
    *,
    actor: FormalPrincipal,
    assignment_id: uuid.UUID | str,
    expected_authorization_version: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    now: datetime | None = None,
) -> ProvincialRoleAssignmentResult:
    """Revoke exactly one provincial-manager assignment.

    The original optional expiry and grant reason remain intact;
    ``revoked_at``, the state event and immutable audit event record the actual
    revocation.  ``expected_authorization_version`` is the optimistic
    precondition, so clients do not need to echo a nullable expiry value.

    Target account/person/organization deactivation does not prevent cleanup.
    Actor authorization and the target authorization version remain mandatory.
    """

    effective_at = _effective_now(now)
    current_actor = _require_current_national_admin(
        db,
        actor,
        effective_at,
        required_permission=("role_assignment", "manage_provincial"),
    )
    checked_assignment_id = _require_uuid("assignment_id", assignment_id)
    checked_version = _require_authorization_version(expected_authorization_version)
    checked_reason = _require_text("reason", reason, 2000)
    checked_key = _require_text("idempotency_key", idempotency_key, 200)
    checked_request_id = _require_text("request_id", request_id, 160)

    storage_key = idempotency_storage_key(current_actor.user_id, checked_key)
    request_hash = _request_hash(
        {
            "operation": "revoke",
            "actor_user_id": current_actor.user_id,
            "assignment_id": str(checked_assignment_id),
            "expected_authorization_version": checked_version,
            "reason": checked_reason,
        }
    )
    replay = _load_idempotent_result(db, storage_key, request_hash, "revoke")
    if replay is not None:
        return replace(replay, replayed=True)

    initial_assignment = db.get(RoleAssignment, checked_assignment_id)
    if initial_assignment is None:
        _fail("assignment_not_found", "not_found", "省负责人授权不存在")
    target_user = _lock_user(db, initial_assignment.user_id)
    _require_expected_version(target_user, checked_version)
    assignment = db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.id == checked_assignment_id)
        .with_for_update()
    )
    if assignment is None:
        _fail("assignment_not_found", "not_found", "省负责人授权不存在")
    role = db.get(Role, assignment.role_id)
    if role is None or role.code != PROVINCIAL_MANAGER_ROLE_CODE:
        _fail(
            "assignment_not_provincial_manager",
            "invalid_request",
            "只能撤销省背包负责人角色授权",
        )
    if assignment.scope_type != PROVINCIAL_SCOPE_TYPE:
        _fail(
            "assignment_scope_invalid",
            "conflict",
            "省负责人授权的数据范围不是组织范围，已失败关闭",
        )
    try:
        organization_id_value = uuid.UUID(assignment.scope_id)
    except (TypeError, ValueError) as exc:
        _fail(
            "assignment_scope_invalid",
            "conflict",
            "省负责人授权的组织范围标识无效，已失败关闭",
            cause=exc,
        )
    assigned_organization = db.get(Organization, organization_id_value)
    if assigned_organization is None or assigned_organization.org_type != "region_company":
        _fail(
            "assignment_scope_invalid",
            "conflict",
            "省负责人授权未绑定区域公司，已失败关闭",
        )
    if assignment.status not in EFFECTIVE_ASSIGNMENT_STATUSES or assignment.revoked_at:
        _fail(
            "assignment_not_revocable",
            "conflict",
            "省负责人授权已终结，不能用新的幂等键重复撤销",
        )
    before = _assignment_snapshot(
        assignment,
        role_code=PROVINCIAL_MANAGER_ROLE_CODE,
        authorization_version=target_user.authorization_version,
    )
    try:
        with db.begin_nested():
            previous_status = assignment.status
            assignment.status = "revoked"
            assignment.revoked_at = effective_at
            assignment.revoked_by = current_actor.user_id
            target_user.authorization_version += 1
            db.flush()

            after = _assignment_snapshot(
                assignment,
                role_code=PROVINCIAL_MANAGER_ROLE_CODE,
                authorization_version=target_user.authorization_version,
            )
            audit_event = append_audit_event(
                db,
                stream_key=AUTHORIZATION_AUDIT_STREAM_KEY,
                actor_user_id=current_actor.user_id,
                action="provincial_manager.revoke",
                aggregate_type=ROLE_ASSIGNMENT_AGGREGATE_TYPE,
                aggregate_id=str(assignment.id),
                before_jsonb=before,
                after_jsonb=after,
                request_id=checked_request_id,
                occurred_at=effective_at,
            )
            transition_id = uuid.uuid4()
            result = ProvincialRoleAssignmentResult(
                assignment_id=assignment.id,
                target_user_id=target_user.id,
                target_person_id=target_user.person_id,
                organization_id=organization_id_value,
                role_code=PROVINCIAL_MANAGER_ROLE_CODE,
                scope_type=PROVINCIAL_SCOPE_TYPE,
                status="revoked",
                valid_from=_as_utc(assignment.valid_from),
                valid_to=(
                    _as_utc(assignment.valid_to)
                    if assignment.valid_to is not None
                    else None
                ),
                authorization_version=target_user.authorization_version,
                audit_event_id=audit_event.id,
                state_transition_event_id=transition_id,
            )
            db.add(
                _transition_event(
                    event_id=transition_id,
                    assignment_id=assignment.id,
                    from_status=previous_status,
                    to_status="revoked",
                    reason=checked_reason,
                    actor_user_id=current_actor.user_id,
                    storage_key=storage_key,
                    request_hash=request_hash,
                    request_id=checked_request_id,
                    operation="revoke",
                    result=result,
                    occurred_at=effective_at,
                )
            )
            db.flush()
        return result
    except AuditChainError as exc:
        _fail(
            "audit_chain_unavailable",
            "service_unavailable",
            "授权审计链不可用，省负责人撤销未写入",
            cause=exc,
        )
    except IntegrityError as exc:
        replay = _load_idempotent_result(db, storage_key, request_hash, "revoke")
        if replay is not None:
            return replace(replay, replayed=True)
        _fail(
            "concurrent_assignment_conflict",
            "conflict",
            "撤销期间发生并发变更，请重新读取人员授权后再操作",
            cause=exc,
        )


def idempotency_storage_key(actor_user_id: str, raw_key: str) -> str:
    """Return the only representation of an idempotency key stored in DB."""

    return hashlib.sha256(f"{actor_user_id}{raw_key}".encode("utf-8")).hexdigest()


def _require_current_national_admin(
    db: Session,
    supplied: FormalPrincipal,
    now: datetime,
    *,
    required_permission: tuple[str, str],
) -> FormalPrincipal:
    if not isinstance(supplied, FormalPrincipal):
        _fail("actor_principal_required", "forbidden", "必须使用正式权限主体操作")
    if (
        supplied.access_mode != "active"
        or supplied.account_status != "active"
        or supplied.employment_status != "active"
        or not _has_national_admin_assignment(supplied)
    ):
        _fail(
            "actor_admin_required",
            "forbidden",
            "仅当前有效的蔚来总部全国管理员可配置省负责人",
        )
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "actor_not_current",
            "forbidden",
            "管理员身份或授权已失效，请重新读取权限上下文",
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "actor_principal_stale",
            "precondition_failed",
            "管理员权限版本已变化，请重新读取权限上下文",
        )
    if (
        current.access_mode != "active"
        or current.account_status != "active"
        or current.employment_status != "active"
        or not _has_national_admin_assignment(current)
    ):
        _fail(
            "actor_admin_required",
            "forbidden",
            "仅当前有效的蔚来总部全国管理员可配置省负责人",
        )
    resource, action = required_permission
    if not current.allows(db, resource, action):
        _fail(
            "actor_permission_required",
            "forbidden",
            f"当前管理员缺少权限 {resource}/{action}",
        )
    return current


def _has_national_admin_assignment(principal: FormalPrincipal) -> bool:
    return any(
        assignment.role_code == "admin"
        and assignment.scope_type == "national"
        and assignment.scope_id == "*"
        for assignment in principal.assignments
    )


def _require_active_region_company(
    db: Session,
    value: uuid.UUID | str,
) -> Organization:
    organization_id = _require_uuid("organization_id", value)
    organization = db.get(Organization, organization_id)
    if organization is None:
        _fail("organization_not_found", "not_found", "区域组织不存在")
    if organization.status != "active" or organization.org_type != "region_company":
        _fail(
            "organization_not_active_region_company",
            "invalid_request",
            "目标组织必须是当前有效的区域公司",
        )
    return organization


def _require_fixed_role(db: Session, code: str, *, active: bool) -> Role:
    roles = list(db.scalars(select(Role).where(Role.code == code)).all())
    if len(roles) != 1 or (active and roles[0].status != "active"):
        _fail(
            "role_catalog_unavailable",
            "service_unavailable",
            f"固定角色 {code} 未唯一启用",
        )
    return roles[0]


def _lock_user(db: Session, user_id: str) -> User:
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        _fail("target_user_not_found", "not_found", "目标账号不存在")
    return user


def _resolve_and_lock_unique_user(db: Session, person_id: uuid.UUID) -> User:
    if db.get(Person, person_id) is None:
        _fail("target_person_not_found", "not_found", "目标人员不存在")
    user_ids = list(db.scalars(select(User.id).where(User.person_id == person_id)).all())
    if not user_ids:
        _fail(
            "target_account_not_bound",
            "conflict",
            "目标人员尚未唯一绑定正式账号",
        )
    if len(user_ids) != 1:
        _fail(
            "target_account_binding_ambiguous",
            "service_unavailable",
            "目标人员绑定了多个账号，已失败关闭",
        )
    user = _lock_user(db, user_ids[0])
    if user.person_id != person_id:
        _fail(
            "target_account_binding_changed",
            "conflict",
            "目标人员账号绑定已变化，请重新读取候选人",
        )
    return user


def _require_expected_version(user: User, expected: int) -> None:
    if user.authorization_version != expected:
        _fail(
            "authorization_version_mismatch",
            "precondition_failed",
            "目标账号授权版本已变化，请重新读取后再操作",
        )


def _require_eligible_target(
    db: Session,
    *,
    user: User,
    target_organization: Organization,
    technician_role: Role,
    now: datetime,
) -> Person:
    if user.account_status != "active":
        _fail(
            "target_account_not_active",
            "conflict",
            "只有正式启用账号可以新增省负责人授权",
        )
    if user.person_id is None:
        _fail(
            "target_person_not_bound",
            "conflict",
            "目标账号尚未唯一绑定人员",
        )
    person = db.get(Person, user.person_id)
    if person is None or person.employment_status != "active":
        _fail(
            "target_person_not_active",
            "conflict",
            "只有当前在职人员可以新增省负责人授权",
        )
    person_organization = db.get(Organization, person.organization_id)
    if (
        person_organization is None
        or person_organization.status != "active"
        or person_organization.org_type not in INTERNAL_ORG_TYPES
        or not _is_in_active_organization_subtree(
            db,
            person_organization.id,
            target_organization.id,
        )
    ):
        _fail(
            "target_outside_region_scope",
            "conflict",
            "目标人员不在所选区域公司的当前有效组织子树内",
        )
    if not _has_verified_active_identity(db, user.id):
        _fail(
            "target_identity_not_verified",
            "conflict",
            "目标账号没有已验证的当前登录身份",
        )
    if not _has_exact_current_technician_assignment(
        db,
        user=user,
        person=person,
        technician_role=technician_role,
        now=now,
    ):
        _fail(
            "target_technician_assignment_invalid",
            "conflict",
            "目标账号没有唯一且范围正确的当前工程师授权",
        )
    return person


def _has_verified_active_identity(db: Session, user_id: str) -> bool:
    return (
        db.scalar(
            select(AuthIdentity.id)
            .where(
                AuthIdentity.user_id == user_id,
                AuthIdentity.status == "active",
                AuthIdentity.verified_at.is_not(None),
                AuthIdentity.revoked_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def _has_exact_current_technician_assignment(
    db: Session,
    *,
    user: User,
    person: Person,
    technician_role: Role,
    now: datetime,
) -> bool:
    assignments = list(
        db.scalars(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role_id == technician_role.id,
                RoleAssignment.status.in_(EFFECTIVE_ASSIGNMENT_STATUSES),
                RoleAssignment.revoked_at.is_(None),
                RoleAssignment.valid_from <= now,
                or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
            )
        ).all()
    )
    return len(assignments) == 1 and (
        assignments[0].scope_type == "person"
        and _same_uuid(assignments[0].scope_id, person.id)
    )


def _require_no_nonterminal_duplicate(
    db: Session,
    *,
    user: User,
    role: Role,
    organization: Organization,
    now: datetime,
) -> None:
    assignments = list(
        db.scalars(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role_id == role.id,
                RoleAssignment.scope_type == PROVINCIAL_SCOPE_TYPE,
                RoleAssignment.scope_id == str(organization.id),
                RoleAssignment.status.in_(EFFECTIVE_ASSIGNMENT_STATUSES),
                RoleAssignment.revoked_at.is_(None),
            )
        ).all()
    )
    if not assignments:
        return
    if len(assignments) > 1:
        _fail(
            "duplicate_assignment_state",
            "conflict",
            "目标账号存在多个未终结的同区域省负责人授权",
        )
    assignment = assignments[0]
    if _as_utc(assignment.valid_from) > now:
        code = "assignment_already_scheduled"
        message = "目标账号已有同区域的未来省负责人授权"
    elif assignment.valid_to is None or _as_utc(assignment.valid_to) > now:
        code = "assignment_already_active"
        message = "目标账号已有同区域的当前省负责人授权"
    else:
        code = "assignment_state_conflict"
        message = "同区域省负责人授权已过有效期但状态未终结"
    _fail(code, "conflict", message)


def _has_current_provincial_assignment(
    db: Session,
    *,
    user: User,
    role: Role,
    organization: Organization,
    now: datetime,
) -> bool:
    return (
        db.scalar(
            select(RoleAssignment.id)
            .where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role_id == role.id,
                RoleAssignment.scope_type == PROVINCIAL_SCOPE_TYPE,
                RoleAssignment.scope_id == str(organization.id),
                RoleAssignment.status.in_(EFFECTIVE_ASSIGNMENT_STATUSES),
                RoleAssignment.revoked_at.is_(None),
                RoleAssignment.valid_from <= now,
                or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
            )
            .limit(1)
        )
        is not None
    )


def _is_in_active_organization_subtree(
    db: Session,
    organization_id: uuid.UUID,
    ancestor_id: uuid.UUID,
) -> bool:
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            _fail(
                "organization_tree_cycle",
                "service_unavailable",
                "组织树存在循环引用，无法安全判断数据范围",
            )
        seen.add(current_id)
        organization = db.get(Organization, current_id)
        if organization is None or organization.status != "active":
            return False
        if organization.id == ancestor_id:
            return True
        current_id = organization.parent_id
    return False


def _transition_event(
    *,
    event_id: uuid.UUID,
    assignment_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    reason: str,
    actor_user_id: str,
    storage_key: str,
    request_hash: str,
    request_id: str,
    operation: str,
    result: ProvincialRoleAssignmentResult,
    occurred_at: datetime,
) -> StateTransitionEvent:
    return StateTransitionEvent(
        id=event_id,
        aggregate_type=ROLE_ASSIGNMENT_AGGREGATE_TYPE,
        aggregate_id=str(assignment_id),
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        actor_id=actor_user_id,
        idempotency_key=storage_key,
        occurred_at=occurred_at,
        metadata_jsonb={
            "operation": operation,
            "request_hash": request_hash,
            "request_id": request_id,
            "result": _result_to_json(result),
        },
    )


def _load_idempotent_result(
    db: Session,
    storage_key: str,
    request_hash: str,
    operation: str,
) -> ProvincialRoleAssignmentResult | None:
    event = db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.idempotency_key == storage_key
        )
    )
    if event is None:
        return None
    metadata = event.metadata_jsonb
    if (
        event.aggregate_type != ROLE_ASSIGNMENT_AGGREGATE_TYPE
        or not isinstance(metadata, dict)
        or metadata.get("operation") != operation
        or metadata.get("request_hash") != request_hash
    ):
        _fail(
            "idempotency_key_conflict",
            "conflict",
            "该幂等键已用于不同的授权请求",
        )
    try:
        result = _result_from_json(metadata["result"])
    except (KeyError, TypeError, ValueError) as exc:
        _fail(
            "idempotency_record_invalid",
            "service_unavailable",
            "幂等记录不完整，已失败关闭",
            cause=exc,
        )
    if str(result.assignment_id) != event.aggregate_id:
        _fail(
            "idempotency_record_invalid",
            "service_unavailable",
            "幂等记录与授权对象不一致，已失败关闭",
        )
    return result


def _assignment_snapshot(
    assignment: RoleAssignment,
    *,
    role_code: str,
    authorization_version: int,
) -> dict[str, object]:
    return {
        "assignment_id": str(assignment.id),
        "user_id": assignment.user_id,
        "role_code": role_code,
        "scope_type": assignment.scope_type,
        "scope_id": assignment.scope_id,
        "valid_from": _canonical_timestamp(_as_utc(assignment.valid_from)),
        "valid_to": (
            _canonical_timestamp(_as_utc(assignment.valid_to))
            if assignment.valid_to is not None
            else None
        ),
        "status": assignment.status,
        "assigned_by": assignment.assigned_by,
        "revoked_at": (
            _canonical_timestamp(_as_utc(assignment.revoked_at))
            if assignment.revoked_at is not None
            else None
        ),
        "revoked_by": assignment.revoked_by,
        "reason": assignment.reason,
        "authorization_version": authorization_version,
    }


def _result_to_json(result: ProvincialRoleAssignmentResult) -> dict[str, object]:
    return {
        "assignment_id": str(result.assignment_id),
        "target_user_id": result.target_user_id,
        "target_person_id": (
            str(result.target_person_id) if result.target_person_id is not None else None
        ),
        "organization_id": str(result.organization_id),
        "role_code": result.role_code,
        "scope_type": result.scope_type,
        "status": result.status,
        "valid_from": _canonical_timestamp(result.valid_from),
        "valid_to": (
            _canonical_timestamp(result.valid_to)
            if result.valid_to is not None
            else None
        ),
        "authorization_version": result.authorization_version,
        "audit_event_id": str(result.audit_event_id),
        "state_transition_event_id": str(result.state_transition_event_id),
    }


def _result_from_json(value: object) -> ProvincialRoleAssignmentResult:
    if not isinstance(value, dict):
        raise TypeError("result is not a JSON object")
    person_value = value["target_person_id"]
    valid_to_value = value["valid_to"]
    if valid_to_value is not None and not isinstance(valid_to_value, str):
        raise TypeError("valid_to is not a string or null")
    return ProvincialRoleAssignmentResult(
        assignment_id=uuid.UUID(_json_string(value, "assignment_id")),
        target_user_id=_json_string(value, "target_user_id"),
        target_person_id=(uuid.UUID(person_value) if isinstance(person_value, str) else None),
        organization_id=uuid.UUID(_json_string(value, "organization_id")),
        role_code=_json_string(value, "role_code"),
        scope_type=_json_string(value, "scope_type"),
        status=_json_string(value, "status"),
        valid_from=_parse_canonical_timestamp(_json_string(value, "valid_from")),
        valid_to=(
            _parse_canonical_timestamp(valid_to_value)
            if isinstance(valid_to_value, str)
            else None
        ),
        authorization_version=_json_int(value, "authorization_version"),
        audit_event_id=uuid.UUID(_json_string(value, "audit_event_id")),
        state_transition_event_id=uuid.UUID(
            _json_string(value, "state_transition_event_id")
        ),
    )


def _json_string(value: dict[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise TypeError(f"{key} is not a string")
    return item


def _json_int(value: dict[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool):
        raise TypeError(f"{key} is not an integer")
    return item


def _request_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_text(field: str, value: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field}_required", "invalid_request", f"{field} 必须为非空字符串")
    normalized = value.strip()
    if len(normalized) > max_length:
        _fail(
            f"{field}_too_long",
            "invalid_request",
            f"{field} 超过允许长度 {max_length}",
        )
    return normalized


def _require_uuid(field: str, value: uuid.UUID | str) -> uuid.UUID:
    if not isinstance(value, (uuid.UUID, str)):
        _fail(f"{field}_invalid", "invalid_request", f"{field} 必须为 UUID")
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        _fail(
            f"{field}_invalid",
            "invalid_request",
            f"{field} 必须为 UUID",
            cause=exc,
        )


def _require_authorization_version(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail(
            "expected_authorization_version_invalid",
            "invalid_request",
            "expected_authorization_version 必须为正整数",
        )
    return value


def _effective_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return _require_aware_datetime("now", value)


def _require_aware_datetime(field: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(
            f"{field}_timezone_required",
            "invalid_request",
            f"{field} 必须包含时区",
        )
    return value.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_canonical_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _same_uuid(left: str, right: uuid.UUID) -> bool:
    try:
        return uuid.UUID(left) == right
    except (TypeError, ValueError):
        return False


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: BaseException | None = None,
) -> None:
    error = ProvincialRoleAssignmentError(code, category, message)
    if cause is None:
        raise error
    raise error from cause
