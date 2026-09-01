"""Formal V1.0 principal and data-scope authorization evaluation.

This module never falls back to ``users.role`` or ``users.province``.  A
principal exists only when the user has an explicit person binding, a verified
identity, and at least one currently effective role assignment.  Invalid scope
data fails the whole evaluation closed instead of being interpreted as
nationwide access.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from .foundation_models import (
    AuthIdentity,
    Organization,
    Permission,
    Person,
    Role,
    RoleAssignment,
    RolePermission,
)
from .models import User


EFFECTIVE_ASSIGNMENT_STATUSES = ("scheduled", "active")
RESTRICTED_RESOURCES = frozenset(
    {"account", "access_context", "auth_session", "handover"}
)
_PG_LOCK_PRINCIPAL_GRAPH_FUNCTION = (
    "public.rsc_lock_formal_principal_graph_0026"
)


class FormalAccessError(RuntimeError):
    """The stored identity or authorization graph is not safe to use."""


@dataclass(frozen=True)
class ScopeGrant:
    assignment_id: uuid.UUID
    role_code: str
    scope_type: str
    scope_id: str
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True)
class Entitlement:
    assignment_id: uuid.UUID
    role_code: str
    scope_type: str
    scope_id: str
    resource: str
    action: str
    field_code: str
    effect: str


@dataclass(frozen=True)
class FormalPrincipal:
    user_id: str
    person_id: uuid.UUID
    account_status: str
    employment_status: str
    authorization_version: int
    access_mode: str
    assignments: tuple[ScopeGrant, ...]
    entitlements: tuple[Entitlement, ...]

    @property
    def role_codes(self) -> tuple[str, ...]:
        return tuple(sorted({assignment.role_code for assignment in self.assignments}))

    def permission_keys(self) -> tuple[tuple[str, str, str], ...]:
        keys = {
            (row.resource, row.action, row.field_code) for row in self.entitlements
        }
        return tuple(
            sorted(
                key
                for key in keys
                if any(
                    row.effect == "allow"
                    and (row.resource, row.action, row.field_code) == key
                    for row in self.entitlements
                )
                and not any(
                    row.effect == "deny"
                    and (row.resource, row.action, row.field_code) == key
                    for row in self.entitlements
                )
            )
        )

    def allows(
        self,
        db: Session,
        resource: str,
        action: str,
        *,
        field_code: str = "",
        target_scope_type: str | None = None,
        target_scope_id: str | None = None,
    ) -> bool:
        if self.access_mode == "restricted_handover" and resource not in (
            RESTRICTED_RESOURCES
        ):
            return False

        matching = [
            row
            for row in self.entitlements
            if row.resource == resource
            and row.action == action
            and row.field_code in {"", field_code}
            and _scope_covers(
                db,
                row.scope_type,
                row.scope_id,
                target_scope_type,
                target_scope_id,
            )
        ]
        if any(row.effect == "deny" for row in matching):
            return False
        return any(row.effect == "allow" for row in matching)


def lock_formal_principal_graph(
    db: Session,
    user_ids: Sequence[str],
) -> None:
    """Lock the mutable authorization graph for a stable business decision.

    The order is deterministic and starts with ``users`` because supported
    role-assignment writers increment that row's authorization version.  Role
    and permission rows are included so a catalog/global-deny change cannot
    split one authorization decision across two database states.
    """

    checked_user_ids = tuple(
        sorted(
            {
                value
                for value in user_ids
                if isinstance(value, str) and value.strip()
            }
        )
    )
    if not checked_user_ids:
        return

    is_postgresql = db.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        # Identity/catalog rows deliberately remain SELECT-only for the API
        # role.  The migration-owned function takes the exact deterministic
        # locks without broadening those table ACLs.
        db.execute(
            text(
                f"SELECT {_PG_LOCK_PRINCIPAL_GRAPH_FUNCTION}"
                "(CAST(:user_ids AS text[]))"
            ),
            {"user_ids": list(checked_user_ids)},
        )

    user_statement = (
        select(User).where(User.id.in_(checked_user_ids)).order_by(User.id)
    )
    if not is_postgresql:
        user_statement = user_statement.with_for_update()
    users = tuple(
        db.scalars(
            user_statement.execution_options(populate_existing=True)
        ).all()
    )
    person_ids = tuple(
        sorted(
            {row.person_id for row in users if row.person_id is not None},
            key=str,
        )
    )
    if person_ids:
        person_statement = (
            select(Person).where(Person.id.in_(person_ids)).order_by(Person.id)
        )
        if not is_postgresql:
            person_statement = person_statement.with_for_update()
        tuple(
            db.scalars(
                person_statement.execution_options(populate_existing=True)
            ).all()
        )
    identity_statement = (
        select(AuthIdentity)
        .where(AuthIdentity.user_id.in_(checked_user_ids))
        .order_by(AuthIdentity.id)
    )
    if not is_postgresql:
        identity_statement = identity_statement.with_for_update()
    tuple(
        db.scalars(
            identity_statement.execution_options(populate_existing=True)
        ).all()
    )
    assignment_statement = (
        select(RoleAssignment)
        .where(RoleAssignment.user_id.in_(checked_user_ids))
        .order_by(RoleAssignment.id)
    )
    if not is_postgresql:
        assignment_statement = assignment_statement.with_for_update()
    assignments = tuple(
        db.scalars(
            assignment_statement.execution_options(populate_existing=True)
        ).all()
    )
    role_ids = tuple(sorted({row.role_id for row in assignments}, key=str))
    if not role_ids:
        return
    role_statement = select(Role).where(Role.id.in_(role_ids)).order_by(Role.id)
    if not is_postgresql:
        role_statement = role_statement.with_for_update()
    tuple(
        db.scalars(
            role_statement.execution_options(populate_existing=True)
        ).all()
    )
    role_permission_statement = (
        select(RolePermission)
        .where(RolePermission.role_id.in_(role_ids))
        .order_by(RolePermission.id)
    )
    if not is_postgresql:
        role_permission_statement = role_permission_statement.with_for_update()
    role_permissions = tuple(
        db.scalars(
            role_permission_statement.execution_options(populate_existing=True)
        ).all()
    )
    permission_ids = tuple(
        sorted({row.permission_id for row in role_permissions}, key=str)
    )
    if permission_ids:
        permission_statement = (
            select(Permission)
            .where(Permission.id.in_(permission_ids))
            .order_by(Permission.id)
        )
        if not is_postgresql:
            permission_statement = permission_statement.with_for_update()
        tuple(
            db.scalars(
                permission_statement.execution_options(populate_existing=True)
            ).all()
        )


def load_formal_principal(
    db: Session,
    user_id: str,
    *,
    now: datetime | None = None,
) -> FormalPrincipal:
    effective_at = _as_utc(now or datetime.now(timezone.utc))
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise FormalAccessError("账号不可用")
    if user.person_id is None:
        raise FormalAccessError("账号尚未完成唯一人员绑定")
    if user.account_status in {"pending_identity", "suspended", "disabled"}:
        raise FormalAccessError("账号状态不允许建立正式权限上下文")

    person = db.scalar(
        select(Person)
        .where(Person.id == user.person_id)
        .execution_options(populate_existing=True)
    )
    if person is None:
        raise FormalAccessError("账号绑定的人员不存在")

    identity = db.scalar(
        select(AuthIdentity.id)
        .where(
            AuthIdentity.user_id == user.id,
            AuthIdentity.status == "active",
            AuthIdentity.verified_at.is_not(None),
            AuthIdentity.revoked_at.is_(None),
        )
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if identity is None:
        raise FormalAccessError("账号没有已验证的正式登录身份")

    assignment_rows = db.execute(
        select(RoleAssignment, Role)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(
            RoleAssignment.user_id == user.id,
            RoleAssignment.status.in_(EFFECTIVE_ASSIGNMENT_STATUSES),
            RoleAssignment.revoked_at.is_(None),
            RoleAssignment.valid_from <= effective_at,
            or_(
                RoleAssignment.valid_to.is_(None),
                RoleAssignment.valid_to > effective_at,
            ),
            Role.status == "active",
        )
        .execution_options(populate_existing=True)
    ).all()
    if not assignment_rows:
        raise FormalAccessError("账号没有当前有效的角色授权")

    assignments: list[ScopeGrant] = []
    role_by_id: dict[uuid.UUID, str] = {}
    for assignment, role in assignment_rows:
        _validate_assignment(db, assignment, role, person)
        role_by_id[role.id] = role.code
        assignments.append(
            ScopeGrant(
                assignment_id=assignment.id,
                role_code=role.code,
                scope_type=assignment.scope_type,
                scope_id=assignment.scope_id,
                valid_from=_as_utc(assignment.valid_from),
                valid_to=(
                    _as_utc(assignment.valid_to)
                    if assignment.valid_to is not None
                    else None
                ),
            )
        )

    permission_rows = db.execute(
        select(RolePermission, Permission).join(
            Permission,
            Permission.id == RolePermission.permission_id,
        )
        .where(RolePermission.role_id.in_(tuple(role_by_id)))
        .execution_options(populate_existing=True)
    ).all()
    permissions_by_role: dict[uuid.UUID, list[tuple[RolePermission, Permission]]] = {}
    for role_permission, permission in permission_rows:
        permissions_by_role.setdefault(role_permission.role_id, []).append(
            (role_permission, permission)
        )

    entitlements: list[Entitlement] = []
    for assignment, role in assignment_rows:
        for role_permission, permission in permissions_by_role.get(role.id, []):
            entitlements.append(
                Entitlement(
                    assignment_id=assignment.id,
                    role_code=role.code,
                    scope_type=assignment.scope_type,
                    scope_id=assignment.scope_id,
                    resource=permission.resource,
                    action=permission.action,
                    field_code=permission.field_code,
                    effect=role_permission.effect,
                )
            )

    access_mode = (
        "active"
        if user.account_status == "active" and person.employment_status == "active"
        else "restricted_handover"
    )
    return FormalPrincipal(
        user_id=user.id,
        person_id=person.id,
        account_status=user.account_status,
        employment_status=person.employment_status,
        authorization_version=user.authorization_version,
        access_mode=access_mode,
        assignments=tuple(assignments),
        entitlements=tuple(entitlements),
    )


def _validate_assignment(
    db: Session,
    assignment: RoleAssignment,
    role: Role,
    person: Person,
) -> None:
    person_organization = db.scalar(
        select(Organization)
        .where(Organization.id == person.organization_id)
        .execution_options(populate_existing=True)
    )
    person_is_internal = bool(
        person_organization
        and person_organization.status == "active"
        and person_organization.org_type
        in {"headquarters", "region_company", "department"}
    )
    if role.code == "admin":
        valid = (
            assignment.scope_type == "national"
            and assignment.scope_id == "*"
            and person_organization is not None
            and person_organization.status == "active"
            and person_organization.org_type == "headquarters"
        )
    elif role.code == "provincial_manager":
        target_organization = _active_organization(db, assignment.scope_id)
        valid = (
            person_is_internal
            and assignment.scope_type == "organization"
            and target_organization is not None
            and target_organization.org_type == "region_company"
        )
    elif role.code == "technician":
        valid = (
            person_is_internal
            and assignment.scope_type == "person"
            and _same_uuid(assignment.scope_id, person.id)
        )
    elif role.code == "star_headquarters_approver":
        valid = (
            assignment.scope_type == "document"
            and bool(assignment.scope_id.strip())
            and person_organization is not None
            and person_organization.status == "active"
            and person_organization.org_type == "external_approval_org"
        )
    else:
        valid = False
    if not valid:
        raise FormalAccessError(f"角色 {role.code} 的数据范围绑定无效")


def _scope_covers(
    db: Session,
    grant_type: str,
    grant_id: str,
    target_type: str | None,
    target_id: str | None,
) -> bool:
    if target_type is None and target_id is None:
        return True
    if not target_type or not target_id:
        return False
    if grant_type == "national" and grant_id == "*":
        return True
    if grant_type == target_type:
        if grant_type in {"organization", "person"}:
            if _same_uuid(grant_id, target_id):
                return True
        elif grant_id == target_id:
            return True
    if grant_type != "organization":
        return False
    if target_type == "person":
        try:
            target_person = db.scalar(
                select(Person)
                .where(Person.id == uuid.UUID(target_id))
                .execution_options(populate_existing=True)
            )
        except (TypeError, ValueError):
            return False
        if target_person is None:
            return False
        target_organization_id = target_person.organization_id
    elif target_type == "organization":
        try:
            target_organization_id = uuid.UUID(target_id)
        except (TypeError, ValueError):
            return False
    else:
        return False
    return _organization_descends_from(db, target_organization_id, grant_id)


def _organization_descends_from(
    db: Session,
    organization_id: uuid.UUID,
    ancestor_id: str,
) -> bool:
    try:
        ancestor_uuid = uuid.UUID(ancestor_id)
    except (TypeError, ValueError):
        return False
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            raise FormalAccessError("组织树存在循环引用")
        seen.add(current_id)
        if current_id == ancestor_uuid:
            return True
        current = db.scalar(
            select(Organization)
            .where(Organization.id == current_id)
            .execution_options(populate_existing=True)
        )
        if current is None or current.status != "active":
            return False
        current_id = current.parent_id
    return False


def _active_organization(db: Session, value: str) -> Organization | None:
    try:
        organization = db.scalar(
            select(Organization)
            .where(Organization.id == uuid.UUID(value))
            .execution_options(populate_existing=True)
        )
    except (TypeError, ValueError):
        return None
    if organization is None or organization.status != "active":
        return None
    return organization


def _same_uuid(left: str | uuid.UUID, right: str | uuid.UUID) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
