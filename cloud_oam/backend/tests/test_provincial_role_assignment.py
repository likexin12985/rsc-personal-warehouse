from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import itertools
import json
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.formal_access import FormalPrincipal, load_formal_principal
from app.formal_services.provincial_role_assignment import (
    AUTHORIZATION_AUDIT_STREAM_KEY,
    PROVINCIAL_MANAGER_ROLE_CODE,
    PROVINCIAL_SCOPE_TYPE,
    TECHNICIAN_ROLE_CODE,
    ProvincialManagerAssignmentView,
    ProvincialManagerCandidate,
    ProvincialRegionOption,
    ProvincialRoleAssignmentError,
    grant_provincial_manager,
    idempotency_storage_key,
    list_provincial_manager_assignments,
    list_provincial_manager_candidates,
    list_provincial_manager_regions,
    revoke_provincial_manager,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    Organization,
    Permission,
    Person,
    Role,
    RoleAssignment,
    RolePermission,
    StateTransitionEvent,
)
from app.models import User


NOW = datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc)
VALID_TO = NOW + timedelta(days=90)
_MOBILE_COUNTER = itertools.count(13700000000)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@dataclass
class World:
    headquarters: Organization
    region: Organization
    department: Organization
    sibling_region: Organization
    roles: dict[str, Role]
    actor_user: User
    actor: FormalPrincipal
    target_user: User
    target_person: Person
    technician_assignment: RoleAssignment


def make_organization(
    db: Session,
    *,
    name: str,
    org_type: str,
    parent: Organization | None = None,
    status: str = "active",
) -> Organization:
    organization = Organization(
        code=f"ORG-{uuid.uuid4().hex[:12]}",
        name=name,
        parent_id=parent.id if parent else None,
        org_type=org_type,
        province_code=None,
        status=status,
    )
    db.add(organization)
    db.flush()
    return organization


def make_person(
    db: Session,
    organization: Organization,
    *,
    name: str,
    employment_status: str = "active",
) -> Person:
    person = Person(
        organization_id=organization.id,
        employee_no=f"E-{uuid.uuid4().hex[:10]}",
        name=name,
        employment_status=employment_status,
    )
    db.add(person)
    db.flush()
    return person


def make_user(
    db: Session,
    person: Person,
    *,
    account_status: str = "active",
    authorization_version: int = 1,
    with_identity: bool = True,
) -> User:
    user = User(
        person_id=person.id,
        account_status=account_status,
        authorization_version=authorization_version,
        mobile=str(next(_MOBILE_COUNTER)),
        name=person.name,
        password_hash="formal-password-login-disabled",
        role="technician",
        province="legacy-province-must-not-authorize",
        is_active=True,
        require_password_change=False,
    )
    db.add(user)
    db.flush()
    if with_identity:
        add_identity(db, user)
    return user


def add_identity(db: Session, user: User, *, status: str = "active") -> AuthIdentity:
    revoked_at = NOW if status == "revoked" else None
    verified_at = NOW if status in {"active", "revoked"} else None
    identity = AuthIdentity(
        user_id=user.id,
        identity_type="mobile",
        provider_key="test",
        identifier_hash=hashlib.sha256(f"identity:{user.id}".encode()).hexdigest(),
        hash_version=1,
        verified_at=verified_at,
        status=status,
        revoked_at=revoked_at,
    )
    db.add(identity)
    db.flush()
    return identity


def add_assignment(
    db: Session,
    *,
    user: User,
    role: Role,
    actor_user: User,
    scope_type: str,
    scope_id: str,
    valid_from: datetime = NOW - timedelta(days=1),
    valid_to: datetime | None = None,
    status: str = "active",
    reason: str = "test setup",
) -> RoleAssignment:
    assignment = RoleAssignment(
        user_id=user.id,
        role_id=role.id,
        scope_type=scope_type,
        scope_id=scope_id,
        valid_from=valid_from,
        valid_to=valid_to,
        status=status,
        assigned_by=actor_user.id,
        revoked_at=None,
        revoked_by=None,
        reason=reason,
    )
    db.add(assignment)
    db.flush()
    return assignment


def make_target(
    db: Session,
    world: World,
    *,
    name: str,
    organization: Organization | None = None,
    account_status: str = "active",
    employment_status: str = "active",
    with_identity: bool = True,
    technician_scope: str = "person",
) -> tuple[User, Person, RoleAssignment]:
    person = make_person(
        db,
        organization or world.department,
        name=name,
        employment_status=employment_status,
    )
    user = make_user(
        db,
        person,
        account_status=account_status,
        with_identity=with_identity,
    )
    assignment = add_assignment(
        db,
        user=user,
        role=world.roles[TECHNICIAN_ROLE_CODE],
        actor_user=world.actor_user,
        scope_type=technician_scope,
        scope_id=(
            str(person.id)
            if technician_scope == "person"
            else str(person.organization_id)
        ),
    )
    return user, person, assignment


@pytest.fixture
def world(db: Session) -> World:
    headquarters = make_organization(db, name="蔚来总部", org_type="headquarters")
    region = make_organization(
        db,
        name="江苏区域公司",
        org_type="region_company",
        parent=headquarters,
    )
    department = make_organization(
        db,
        name="苏南服务部",
        org_type="department",
        parent=region,
    )
    sibling_region = make_organization(
        db,
        name="浙江区域公司",
        org_type="region_company",
        parent=headquarters,
    )
    roles = {
        code: Role(
            code=code,
            name=code,
            is_external=code == "star_headquarters_approver",
            status="active",
        )
        for code in (
            "admin",
            PROVINCIAL_MANAGER_ROLE_CODE,
            TECHNICIAN_ROLE_CODE,
            "star_headquarters_approver",
        )
    }
    db.add_all(roles.values())
    db.flush()
    permissions = {
        (resource, action): Permission(
            resource=resource,
            action=action,
            field_code="",
            description=f"{resource}/{action}",
        )
        for resource, action in (
            ("people", "read_minimal"),
            ("role_assignment", "manage_provincial"),
        )
    }
    db.add_all(permissions.values())
    db.flush()
    db.add_all(
        RolePermission(
            role_id=roles["admin"].id,
            permission_id=permission.id,
            effect="allow",
        )
        for permission in permissions.values()
    )
    db.flush()

    actor_person = make_person(db, headquarters, name="李珂鑫")
    actor_user = make_user(db, actor_person, authorization_version=4)
    add_assignment(
        db,
        user=actor_user,
        role=roles["admin"],
        actor_user=actor_user,
        scope_type="national",
        scope_id="*",
    )
    actor = load_formal_principal(db, actor_user.id, now=NOW)

    # Build the object incrementally because make_target accepts World for the
    # shared role catalog and actor.
    placeholder = World(
        headquarters=headquarters,
        region=region,
        department=department,
        sibling_region=sibling_region,
        roles=roles,
        actor_user=actor_user,
        actor=actor,
        target_user=actor_user,
        target_person=actor_person,
        technician_assignment=db.scalar(select(RoleAssignment).limit(1)),
    )
    target_user, target_person, technician_assignment = make_target(
        db,
        placeholder,
        name="区域工程师",
        organization=department,
    )
    placeholder.target_user = target_user
    placeholder.target_person = target_person
    placeholder.technician_assignment = technician_assignment
    db.add(AuditChainHead(stream_key=AUTHORIZATION_AUDIT_STREAM_KEY, version=0))
    db.flush()
    return placeholder


def grant(
    db: Session,
    world: World,
    *,
    key: str = "grant-key-001",
    reason: str = "任命江苏省背包负责人",
    request_id: str = "request-grant-001",
    expected_version: int | None = None,
    valid_to: datetime | None = VALID_TO,
):
    return grant_provincial_manager(
        db,
        actor=world.actor,
        target_person_id=world.target_person.id,
        organization_id=world.region.id,
        expected_authorization_version=(
            world.target_user.authorization_version
            if expected_version is None
            else expected_version
        ),
        valid_to=valid_to,
        reason=reason,
        idempotency_key=key,
        request_id=request_id,
        now=NOW,
    )


def assert_error(
    caught: pytest.ExceptionInfo[ProvincialRoleAssignmentError],
    *,
    code: str,
    category: str,
    status: int,
) -> None:
    error = caught.value
    assert error.code == code
    assert error.category == category
    assert error.http_status_code == status
    assert error.as_detail() == {
        "code": code,
        "category": category,
        "message": error.message,
    }


def test_candidates_are_minimal_safe_and_limited_to_active_region_subtree(
    db: Session,
    world: World,
):
    make_target(db, world, name="外省工程师", organization=world.sibling_region)
    make_target(db, world, name="离职工程师", employment_status="left")
    make_target(db, world, name="停用账号", account_status="disabled")
    make_target(db, world, name="无身份账号", with_identity=False)
    make_target(db, world, name="错误工程师范围", technician_scope="organization")

    candidates = list_provincial_manager_candidates(
        db,
        actor=world.actor,
        organization_id=world.region.id,
        now=NOW,
    )

    assert candidates == (
        ProvincialManagerCandidate(
            user_id=world.target_user.id,
            person_id=world.target_person.id,
            person_name=world.target_person.name,
            employee_no=world.target_person.employee_no,
            organization_id=world.department.id,
            organization_code=world.department.code,
            organization_name=world.department.name,
            authorization_version=world.target_user.authorization_version,
        ),
    )
    assert set(candidates[0].__dict__) == {
        "user_id",
        "person_id",
        "person_name",
        "employee_no",
        "organization_id",
        "organization_code",
        "organization_name",
        "authorization_version",
    }
    assert not hasattr(candidates[0], "mobile")
    assert not hasattr(candidates[0], "identifier_hash")
    assert world.target_user.mobile not in repr(candidates)


def test_region_options_include_only_active_region_companies_with_minimum_fields(
    db: Session,
    world: World,
):
    world.region.province_code = "320000"
    world.sibling_region.province_code = "330000"
    make_organization(
        db,
        name="停用区域公司",
        org_type="region_company",
        parent=world.headquarters,
        status="inactive",
    )
    db.flush()

    rows = list_provincial_manager_regions(db, actor=world.actor, now=NOW)

    assert rows == tuple(
        sorted(
            (
                ProvincialRegionOption(
                    organization_id=world.region.id,
                    organization_code=world.region.code,
                    organization_name=world.region.name,
                    province_code="320000",
                ),
                ProvincialRegionOption(
                    organization_id=world.sibling_region.id,
                    organization_code=world.sibling_region.code,
                    organization_name=world.sibling_region.name,
                    province_code="330000",
                ),
            ),
            key=lambda row: (
                row.organization_name,
                row.organization_code,
                row.organization_id,
            ),
        )
    )
    assert set(rows[0].__dict__) == {
        "organization_id",
        "organization_code",
        "organization_name",
        "province_code",
    }


def test_candidates_exclude_person_with_current_assignment_for_target_region(
    db: Session,
    world: World,
):
    grant(db, world)

    candidates = list_provincial_manager_candidates(
        db,
        actor=world.actor,
        organization_id=world.region.id,
        now=NOW,
    )

    assert candidates == ()


def test_assignment_list_is_minimal_exact_and_keeps_deactivated_target_visible(
    db: Session,
    world: World,
):
    granted = grant(db, world)
    world.target_user.account_status = "disabled"
    world.target_person.employment_status = "left"

    scheduled_user, scheduled_person, _ = make_target(
        db,
        world,
        name="候任区域负责人",
    )
    scheduled = add_assignment(
        db,
        user=scheduled_user,
        role=world.roles[PROVINCIAL_MANAGER_ROLE_CODE],
        actor_user=world.actor_user,
        scope_type=PROVINCIAL_SCOPE_TYPE,
        scope_id=str(world.region.id),
        valid_from=NOW + timedelta(days=1),
        valid_to=None,
        status="scheduled",
        reason="下月生效",
    )

    revoked_user, _revoked_person, _ = make_target(
        db,
        world,
        name="前任区域负责人",
    )
    db.add(
        RoleAssignment(
            user_id=revoked_user.id,
            role_id=world.roles[PROVINCIAL_MANAGER_ROLE_CODE].id,
            scope_type=PROVINCIAL_SCOPE_TYPE,
            scope_id=str(world.region.id),
            valid_from=NOW - timedelta(days=30),
            valid_to=None,
            status="revoked",
            assigned_by=world.actor_user.id,
            revoked_at=NOW - timedelta(days=1),
            revoked_by=world.actor_user.id,
            reason="已撤销测试数据",
        )
    )
    db.flush()

    rows = list_provincial_manager_assignments(
        db,
        actor=world.actor,
        organization_id=world.region.id,
        now=NOW,
    )

    assert [row.assignment_id for row in rows] == [granted.assignment_id, scheduled.id]
    first = rows[0]
    assert first == ProvincialManagerAssignmentView(
        assignment_id=granted.assignment_id,
        person_id=world.target_person.id,
        person_name=world.target_person.name,
        employee_no=world.target_person.employee_no,
        organization_id=world.department.id,
        organization_code=world.department.code,
        organization_name=world.department.name,
        valid_from=NOW,
        valid_to=VALID_TO,
        status="active",
        authorization_version=granted.authorization_version,
    )
    assert rows[1].person_id == scheduled_person.id
    assert rows[1].valid_to is None
    assert rows[1].status == "scheduled"
    assert set(first.__dict__) == {
        "assignment_id",
        "person_id",
        "person_name",
        "employee_no",
        "organization_id",
        "organization_code",
        "organization_name",
        "valid_from",
        "valid_to",
        "status",
        "authorization_version",
    }
    serialized = repr(rows)
    assert world.target_user.mobile not in serialized
    assert not hasattr(first, "mobile")
    assert not hasattr(first, "identifier_hash")


@pytest.mark.parametrize(
    ("resource", "action", "operation"),
    [
        ("people", "read_minimal", "list"),
        ("role_assignment", "manage_provincial", "grant"),
    ],
)
def test_admin_role_cannot_bypass_current_permission_deny(
    db: Session,
    world: World,
    resource: str,
    action: str,
    operation: str,
):
    role_permission = db.scalar(
        select(RolePermission)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            RolePermission.role_id == world.roles["admin"].id,
            Permission.resource == resource,
            Permission.action == action,
        )
    )
    assert role_permission is not None
    role_permission.effect = "deny"
    db.flush()

    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        if operation == "list":
            list_provincial_manager_candidates(
                db,
                actor=world.actor,
                organization_id=world.region.id,
                now=NOW,
            )
        else:
            grant(db, world)

    assert_error(
        caught,
        code="actor_permission_required",
        category="forbidden",
        status=403,
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_grant_is_fixed_scope_audited_versioned_and_never_commits(
    db: Session,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
):
    raw_key = "raw-grant-idempotency-secret"
    previous_version = world.target_user.authorization_version

    def forbidden_commit():
        raise AssertionError("domain service must not commit")

    monkeypatch.setattr(db, "commit", forbidden_commit)
    result = grant(db, world, key=raw_key)

    assignment = db.get(RoleAssignment, result.assignment_id)
    assert assignment is not None
    assert assignment.role_id == world.roles[PROVINCIAL_MANAGER_ROLE_CODE].id
    assert assignment.scope_type == PROVINCIAL_SCOPE_TYPE
    assert assignment.scope_id == str(world.region.id)
    assert assignment.status == "active"
    assert assignment.valid_to.replace(tzinfo=timezone.utc) == VALID_TO
    assert world.target_user.authorization_version == previous_version + 1
    assert result.authorization_version == previous_version + 1
    assert result.replayed is False

    transition = db.get(StateTransitionEvent, result.state_transition_event_id)
    audit = db.get(AuditEvent, result.audit_event_id)
    head = db.scalar(
        select(AuditChainHead).where(
            AuditChainHead.stream_key == AUTHORIZATION_AUDIT_STREAM_KEY
        )
    )
    assert transition is not None and audit is not None and head is not None
    assert transition.aggregate_id == str(assignment.id)
    assert transition.from_status is None
    assert transition.to_status == "active"
    assert transition.idempotency_key == idempotency_storage_key(
        world.actor.user_id,
        raw_key,
    )
    assert len(transition.metadata_jsonb["request_hash"]) == 64
    assert transition.metadata_jsonb["result"]["authorization_version"] == (
        previous_version + 1
    )
    assert audit.action == "provincial_manager.grant"
    assert audit.before_jsonb is None
    assert audit.after_jsonb["scope_type"] == PROVINCIAL_SCOPE_TYPE
    assert audit.after_jsonb["scope_id"] == str(world.region.id)
    assert head.last_event_id == audit.id
    assert head.version == 1

    persisted = json.dumps(
        {
            "idempotency_key": transition.idempotency_key,
            "metadata": transition.metadata_jsonb,
            "audit_before": audit.before_jsonb,
            "audit_after": audit.after_jsonb,
        },
        ensure_ascii=False,
    )
    assert raw_key not in persisted
    assert world.target_user.mobile not in persisted
    identity_hash = db.scalar(
        select(AuthIdentity.identifier_hash).where(
            AuthIdentity.user_id == world.target_user.id
        )
    )
    assert identity_hash not in persisted


def test_grant_resolves_exactly_one_account_from_person_id(
    db: Session,
    world: World,
):
    result = grant(db, world)

    assert result.target_person_id == world.target_person.id
    assert result.target_user_id == world.target_user.id
    assert list(
        db.scalars(select(User.id).where(User.person_id == world.target_person.id))
    ) == [world.target_user.id]


def test_grant_blocks_person_without_bound_account(
    db: Session,
    world: World,
):
    unbound_person = make_person(db, world.department, name="尚未绑定账号人员")

    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        grant_provincial_manager(
            db,
            actor=world.actor,
            target_person_id=unbound_person.id,
            organization_id=world.region.id,
            expected_authorization_version=1,
            valid_to=VALID_TO,
            reason="不得猜测账号",
            idempotency_key="unbound-person-key",
            request_id="unbound-person-request",
            now=NOW,
        )

    assert_error(
        caught,
        code="target_account_not_bound",
        category="conflict",
        status=409,
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_same_key_same_payload_replays_without_new_writes_or_version_bump(
    db: Session,
    world: World,
):
    expected_version = world.target_user.authorization_version
    first = grant(db, world, expected_version=expected_version)
    second = grant(
        db,
        world,
        expected_version=expected_version,
        request_id="transport-retry-has-a-new-request-id",
    )

    assert second.replayed is True
    assert second.assignment_id == first.assignment_id
    assert second.audit_event_id == first.audit_event_id
    assert second.state_transition_event_id == first.state_transition_event_id
    assert world.target_user.authorization_version == expected_version + 1
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_same_key_different_payload_is_idempotency_conflict(
    db: Session,
    world: World,
):
    old_version = world.target_user.authorization_version
    grant(db, world, expected_version=old_version)

    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        grant(
            db,
            world,
            expected_version=old_version,
            reason="不同请求载荷",
        )

    assert_error(
        caught,
        code="idempotency_key_conflict",
        category="conflict",
        status=409,
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_different_key_cannot_duplicate_current_assignment(
    db: Session,
    world: World,
):
    grant(db, world)
    current_version = world.target_user.authorization_version

    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        grant(
            db,
            world,
            key="another-key",
            request_id="another-request",
            expected_version=current_version,
        )

    assert_error(
        caught,
        code="assignment_already_active",
        category="conflict",
        status=409,
    )
    assert world.target_user.authorization_version == current_version
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_expected_authorization_version_is_checked_after_user_lock(
    db: Session,
    world: World,
):
    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        grant(db, world, expected_version=world.target_user.authorization_version + 1)

    assert_error(
        caught,
        code="authorization_version_mismatch",
        category="precondition_failed",
        status=412,
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0


@pytest.mark.parametrize(
    ("target_state", "expected_code"),
    [
        ("disabled_account", "target_account_not_active"),
        ("left_person", "target_person_not_active"),
        ("revoked_identity", "target_identity_not_verified"),
        ("wrong_technician_scope", "target_technician_assignment_invalid"),
        ("outside_subtree", "target_outside_region_scope"),
    ],
)
def test_grant_rejects_ineligible_target_state(
    db: Session,
    world: World,
    target_state: str,
    expected_code: str,
):
    if target_state == "disabled_account":
        world.target_user.account_status = "disabled"
    elif target_state == "left_person":
        world.target_person.employment_status = "left"
    elif target_state == "revoked_identity":
        identity = db.scalar(
            select(AuthIdentity).where(AuthIdentity.user_id == world.target_user.id)
        )
        identity.status = "revoked"
        identity.revoked_at = NOW
    elif target_state == "wrong_technician_scope":
        world.technician_assignment.scope_type = "organization"
        world.technician_assignment.scope_id = str(world.department.id)
    elif target_state == "outside_subtree":
        world.target_person.organization_id = world.sibling_region.id
    db.flush()
    previous_version = world.target_user.authorization_version

    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        grant(db, world)

    assert caught.value.code == expected_code
    assert caught.value.category == "conflict"
    assert world.target_user.authorization_version == previous_version
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_wrong_or_inactive_target_organization_is_never_accepted(
    db: Session,
    world: World,
):
    common = dict(
        db=db,
        actor=world.actor,
        target_person_id=world.target_person.id,
        expected_authorization_version=world.target_user.authorization_version,
        valid_to=VALID_TO,
        reason="范围注入测试",
        idempotency_key="scope-injection-key",
        request_id="scope-injection-request",
        now=NOW,
    )
    with pytest.raises(ProvincialRoleAssignmentError) as department_error:
        grant_provincial_manager(
            **common,
            organization_id=world.department.id,
        )
    assert department_error.value.code == "organization_not_active_region_company"

    world.region.status = "inactive"
    db.flush()
    with pytest.raises(ProvincialRoleAssignmentError) as inactive_error:
        grant_provincial_manager(
            **common,
            organization_id=world.region.id,
        )
    assert inactive_error.value.code == "organization_not_active_region_company"


def test_public_mutations_do_not_accept_role_or_scope_injection(
    db: Session,
    world: World,
):
    grant_parameters = inspect.signature(grant_provincial_manager).parameters
    revoke_parameters = inspect.signature(revoke_provincial_manager).parameters
    assert "role_code" not in grant_parameters
    assert "scope_type" not in grant_parameters
    assert "target_person_id" in grant_parameters
    assert "target_user_id" not in grant_parameters
    assert "role_code" not in revoke_parameters
    assert "scope_type" not in revoke_parameters

    with pytest.raises(TypeError):
        grant_provincial_manager(
            db,
            actor=world.actor,
            target_person_id=world.target_person.id,
            organization_id=world.region.id,
            expected_authorization_version=world.target_user.authorization_version,
            valid_to=VALID_TO,
            reason="不得注入角色",
            idempotency_key="injection-key",
            request_id="injection-request",
            now=NOW,
            role_code="admin",
        )


def test_non_admin_and_stale_admin_principals_are_rejected(
    db: Session,
    world: World,
):
    technician_principal = load_formal_principal(db, world.target_user.id, now=NOW)
    with pytest.raises(ProvincialRoleAssignmentError) as forbidden:
        list_provincial_manager_candidates(
            db,
            actor=technician_principal,
            organization_id=world.region.id,
            now=NOW,
        )
    assert_error(
        forbidden,
        code="actor_admin_required",
        category="forbidden",
        status=403,
    )

    world.actor_user.authorization_version += 1
    db.flush()
    with pytest.raises(ProvincialRoleAssignmentError) as stale:
        grant(db, world)
    assert_error(
        stale,
        code="actor_principal_stale",
        category="precondition_failed",
        status=412,
    )


def test_audit_chain_failure_rolls_back_assignment_and_version_atomically(
    db: Session,
    world: World,
):
    head = db.scalar(
        select(AuditChainHead).where(
            AuditChainHead.stream_key == AUTHORIZATION_AUDIT_STREAM_KEY
        )
    )
    db.delete(head)
    db.flush()
    previous_version = world.target_user.authorization_version

    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        grant(db, world)

    assert_error(
        caught,
        code="audit_chain_unavailable",
        category="service_unavailable",
        status=503,
    )
    db.refresh(world.target_user)
    assert world.target_user.authorization_version == previous_version
    assert db.scalar(
        select(func.count())
        .select_from(RoleAssignment)
        .where(RoleAssignment.role_id == world.roles[PROVINCIAL_MANAGER_ROLE_CODE].id)
    ) == 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0


def test_revoke_is_audited_allows_deactivated_target_and_preserves_technician(
    db: Session,
    world: World,
):
    granted = grant(db, world)
    world.target_user.account_status = "disabled"
    world.target_person.employment_status = "left"
    world.region.status = "inactive"
    db.flush()

    revoked = revoke_provincial_manager(
        db,
        actor=world.actor,
        assignment_id=granted.assignment_id,
        expected_authorization_version=granted.authorization_version,
        reason="人员停用，撤销省负责人",
        idempotency_key="revoke-key-001",
        request_id="request-revoke-001",
        now=NOW + timedelta(minutes=1),
    )

    assignment = db.get(RoleAssignment, granted.assignment_id)
    technician = db.get(RoleAssignment, world.technician_assignment.id)
    assert assignment.status == "revoked"
    assert assignment.revoked_by == world.actor.user_id
    assert assignment.revoked_at.replace(tzinfo=timezone.utc) == NOW + timedelta(
        minutes=1
    )
    assert assignment.reason == "任命江苏省背包负责人"
    assert technician.status == "active"
    assert technician.revoked_at is None
    assert revoked.status == "revoked"
    assert revoked.authorization_version == granted.authorization_version + 1

    transitions = list(
        db.scalars(select(StateTransitionEvent).order_by(StateTransitionEvent.occurred_at))
    )
    audits = list(db.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)))
    assert len(transitions) == 2
    assert transitions[-1].from_status == "active"
    assert transitions[-1].to_status == "revoked"
    assert len(audits) == 2
    assert audits[-1].action == "provincial_manager.revoke"
    assert audits[-1].before_jsonb["status"] == "active"
    assert audits[-1].after_jsonb["status"] == "revoked"


def test_revoke_replays_and_new_key_cannot_repeat_terminal_transition(
    db: Session,
    world: World,
):
    granted = grant(db, world)
    kwargs = dict(
        db=db,
        actor=world.actor,
        assignment_id=granted.assignment_id,
        expected_authorization_version=granted.authorization_version,
        reason="撤销错误任命",
        idempotency_key="revoke-replay-key",
        request_id="revoke-replay-request",
        now=NOW + timedelta(minutes=1),
    )
    first = revoke_provincial_manager(**kwargs)
    second = revoke_provincial_manager(**kwargs)
    assert second.replayed is True
    assert second.assignment_id == first.assignment_id
    assert second.authorization_version == first.authorization_version
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 2

    with pytest.raises(ProvincialRoleAssignmentError) as repeated:
        revoke_provincial_manager(
            db,
            actor=world.actor,
            assignment_id=granted.assignment_id,
            expected_authorization_version=first.authorization_version,
            reason="另一次撤销",
            idempotency_key="different-revoke-key",
            request_id="different-revoke-request",
            now=NOW + timedelta(minutes=2),
        )
    assert repeated.value.code == "assignment_not_revocable"


def test_indefinite_grant_can_be_revoked_and_preserves_null_expiry(
    db: Session,
    world: World,
):
    expected_version = world.target_user.authorization_version
    granted = grant(db, world, valid_to=None, expected_version=expected_version)
    assert granted.valid_to is None
    assert db.get(RoleAssignment, granted.assignment_id).valid_to is None
    replayed = grant(
        db,
        world,
        valid_to=None,
        expected_version=expected_version,
        request_id="indefinite-grant-transport-retry",
    )
    assert replayed.replayed is True
    assert replayed.valid_to is None

    revoked = revoke_provincial_manager(
        db,
        actor=world.actor,
        assignment_id=granted.assignment_id,
        expected_authorization_version=granted.authorization_version,
        reason="撤销长期省负责人授权",
        idempotency_key="indefinite-revoke-key",
        request_id="indefinite-revoke-request",
        now=NOW + timedelta(minutes=1),
    )
    assert revoked.valid_to is None
    assert db.get(RoleAssignment, granted.assignment_id).valid_to is None
    assert db.get(RoleAssignment, granted.assignment_id).status == "revoked"


def test_revoke_refuses_technician_assignment(
    db: Session,
    world: World,
):
    version = world.target_user.authorization_version
    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        revoke_provincial_manager(
            db,
            actor=world.actor,
            assignment_id=world.technician_assignment.id,
            expected_authorization_version=version,
            reason="不得撤销工程师角色",
            idempotency_key="technician-revoke-key",
            request_id="technician-revoke-request",
            now=NOW,
        )
    assert_error(
        caught,
        code="assignment_not_provincial_manager",
        category="invalid_request",
        status=422,
    )
    assert world.technician_assignment.status == "active"
    assert world.target_user.authorization_version == version


@pytest.mark.parametrize(
    ("override", "expected_code"),
    [
        ({"valid_to": NOW}, "valid_to_not_future"),
        ({"valid_to": datetime(2026, 9, 1)}, "valid_to_timezone_required"),
        ({"reason": "   "}, "reason_required"),
        ({"key": "   "}, "idempotency_key_required"),
        ({"request_id": "   "}, "request_id_required"),
        ({"expected_version": 0}, "expected_authorization_version_invalid"),
    ],
)
def test_grant_requires_explicit_safe_write_preconditions(
    db: Session,
    world: World,
    override: dict[str, object],
    expected_code: str,
):
    with pytest.raises(ProvincialRoleAssignmentError) as caught:
        grant(db, world, **override)
    assert caught.value.code == expected_code
    assert caught.value.category == "invalid_request"
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0
