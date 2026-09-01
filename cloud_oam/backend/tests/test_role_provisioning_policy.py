from __future__ import annotations

from datetime import datetime, timedelta, timezone
import itertools
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    Organization,
    Person,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from app.models import User
from app.formal_services.role_provisioning import (
    ADMIN_ROLE_CODE,
    HEADQUARTERS_ADMIN_NAMES,
    MANUAL_ONLY_ROLE_CODES,
    PROVINCIAL_MANAGER_ROLE_CODE,
    TECHNICIAN_ROLE_CODE,
    RoleProvisioningError,
    apply_confirmed_role_policy,
)


NOW = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)
_MOBILE_COUNTER = itertools.count(13800000000)


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


@pytest.fixture
def catalog(db: Session) -> dict[str, Role]:
    roles = {
        code: Role(
            code=code,
            name=code,
            is_external=code == "star_headquarters_approver",
            status="active",
        )
        for code in (
            ADMIN_ROLE_CODE,
            PROVINCIAL_MANAGER_ROLE_CODE,
            TECHNICIAN_ROLE_CODE,
            "star_headquarters_approver",
        )
    }
    db.add_all(roles.values())
    db.flush()
    return roles


def make_organization(
    db: Session,
    *,
    name: str,
    org_type: str,
    status: str = "active",
) -> Organization:
    organization = Organization(
        code=f"ORG-{uuid.uuid4().hex[:12]}",
        name=name,
        org_type=org_type,
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


def bind_user(
    db: Session,
    person: Person | None,
    *,
    account_status: str = "active",
    legacy_role: str = "technician",
    authorization_version: int = 1,
) -> User:
    user = User(
        person_id=person.id if person is not None else None,
        account_status=account_status,
        authorization_version=authorization_version,
        mobile=str(next(_MOBILE_COUNTER)),
        name=person.name if person is not None else "授权执行人",
        password_hash="formal-password-login-disabled",
        role=legacy_role,
        province="legacy-value-must-not-be-used",
        is_active=True,
        require_password_change=False,
    )
    db.add(user)
    db.flush()
    return user


def make_actor(db: Session, catalog: dict[str, Role]) -> User:
    headquarters = make_organization(
        db,
        name="已复核的初始管理员组织",
        org_type="headquarters",
    )
    person = make_person(db, headquarters, name="已复核的初始管理员")
    actor = bind_user(db, person, account_status="active")
    db.add(
        AuthIdentity(
            user_id=actor.id,
            identity_type="mobile",
            provider_key="role-policy-test",
            identifier_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            hash_version=1,
            verified_at=NOW,
            status="active",
            revoked_at=None,
        )
    )
    add_assignment(
        db,
        user=actor,
        role=catalog[ADMIN_ROLE_CODE],
        actor=actor,
        scope_type="national",
        scope_id="*",
        valid_from=NOW - timedelta(days=1),
    )
    db.add(AuditChainHead(stream_key="authorization", version=0))
    db.flush()
    return actor


_POLICY_RUN_COUNTER = itertools.count(1)


def apply_policy(
    db: Session,
    actor: User,
    *,
    now: datetime = NOW,
    idempotency_key: str | None = None,
    request_id: str | None = None,
):
    sequence = next(_POLICY_RUN_COUNTER)
    return apply_confirmed_role_policy(
        db,
        actor_user_id=actor.id,
        idempotency_key=(
            idempotency_key or f"confirmed-policy-{sequence:08d}"
        ),
        request_id=request_id or f"role-policy-request-{sequence:08d}",
        now=now,
    )


def seed_named_admins(
    db: Session,
    headquarters: Organization,
) -> dict[str, tuple[Person, User]]:
    result: dict[str, tuple[Person, User]] = {}
    for name in HEADQUARTERS_ADMIN_NAMES:
        person = make_person(db, headquarters, name=name)
        result[name] = (person, bind_user(db, person))
    return result


def add_assignment(
    db: Session,
    *,
    user: User,
    role: Role,
    actor: User,
    scope_type: str,
    scope_id: str,
    valid_from: datetime,
    valid_to: datetime | None = None,
    status: str = "active",
) -> RoleAssignment:
    assignment = RoleAssignment(
        user_id=user.id,
        role_id=role.id,
        scope_type=scope_type,
        scope_id=scope_id,
        valid_from=valid_from,
        valid_to=valid_to,
        status=status,
        assigned_by=actor.id,
        reason="pre-existing test assignment",
    )
    db.add(assignment)
    db.flush()
    return assignment


def assignments_for(db: Session, role: Role) -> list[RoleAssignment]:
    return list(
        db.scalars(
            select(RoleAssignment)
            .where(RoleAssignment.role_id == role.id)
            .order_by(RoleAssignment.user_id, RoleAssignment.valid_from)
        ).all()
    )


def test_applies_default_technician_and_exact_named_admin_policy_without_identity_mutation(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    headquarters = make_organization(
        db, name="蔚来总部", org_type="headquarters"
    )
    admins = seed_named_admins(db, headquarters)
    region = make_organization(db, name="江苏区域", org_type="region_company")
    engineer_person = make_person(db, region, name="普通工程师")
    engineer = bind_user(
        db,
        engineer_person,
        account_status="pending_identity",
        legacy_role="admin",
    )
    unbound_person = make_person(db, region, name="尚未绑定人员")

    report = apply_policy(db, actor)

    technician_assignments = assignments_for(db, catalog[TECHNICIAN_ROLE_CODE])
    admin_assignments = assignments_for(db, catalog[ADMIN_ROLE_CODE])
    assert len(technician_assignments) == 6
    assert len(admin_assignments) == 5
    assert assignments_for(db, catalog[PROVINCIAL_MANAGER_ROLE_CODE]) == []
    assert report.admin_batch_status == "applied"
    assert report.created_count == 10
    assert report.manual_only_role_codes == (PROVINCIAL_MANAGER_ROLE_CODE,)
    assert MANUAL_ONLY_ROLE_CODES == {PROVINCIAL_MANAGER_ROLE_CODE}

    assert all(
        row.scope_type == "person" and row.assigned_by == actor.id
        for row in technician_assignments
    )
    assert all(
        row.scope_type == "national"
        and row.scope_id == "*"
        and row.assigned_by == actor.id
        for row in admin_assignments
    )
    assert {row.user_id for row in admin_assignments if row.user_id != actor.id} == {
        user.id for _, user in admins.values()
    }
    assert all(user.authorization_version == 3 for _, user in admins.values())
    assert engineer.authorization_version == 2
    assert engineer.account_status == "pending_identity"
    assert engineer.role == "admin"
    assert db.scalar(select(func.count()).select_from(AuthIdentity)) == 1
    assert db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.person_id == unbound_person.id)
    ) == 0
    assert any(
        outcome.person_id == unbound_person.id
        and outcome.status == "unbound_person"
        for outcome in report.technician_outcomes
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 10
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 11
    audit_head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authorization")
    )
    assert audit_head is not None
    assert audit_head.version == 10


def test_second_application_is_fully_idempotent(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    headquarters = make_organization(
        db, name="蔚来总部", org_type="headquarters"
    )
    admins = seed_named_admins(db, headquarters)

    first = apply_policy(db, actor)
    versions_after_first = {
        user.id: user.authorization_version for _, user in admins.values()
    }
    second = apply_policy(db, actor, now=NOW + timedelta(minutes=1))

    assert first.created_count == 9
    assert second.created_count == 0
    assert second.admin_batch_status == "idempotent"
    assert {row.status for row in second.technician_outcomes} == {"existing"}
    assert {row.status for row in second.administrator_outcomes} == {"existing"}
    assert len(assignments_for(db, catalog[TECHNICIAN_ROLE_CODE])) == 5
    assert len(assignments_for(db, catalog[ADMIN_ROLE_CODE])) == 5
    assert {
        user.id: user.authorization_version for _, user in admins.values()
    } == versions_after_first


@pytest.mark.parametrize(
    "failure_mode,expected_status",
    [
        ("missing", "admin_name_resolution_failed"),
        ("duplicate", "admin_name_resolution_failed"),
        ("unbound", "admin_user_resolution_failed"),
    ],
)
def test_any_admin_identity_resolution_failure_blocks_all_admin_writes_but_not_technicians(
    db: Session,
    catalog: dict[str, Role],
    failure_mode: str,
    expected_status: str,
):
    actor = make_actor(db, catalog)
    headquarters = make_organization(
        db, name="蔚来总部", org_type="headquarters"
    )
    users: list[User] = []
    failed_name = HEADQUARTERS_ADMIN_NAMES[-1]
    for name in HEADQUARTERS_ADMIN_NAMES:
        if name == failed_name and failure_mode == "missing":
            continue
        person = make_person(db, headquarters, name=name)
        if name != failed_name or failure_mode != "unbound":
            users.append(bind_user(db, person))
        if name == failed_name and failure_mode == "duplicate":
            duplicate = make_person(db, headquarters, name=name)
            users.append(bind_user(db, duplicate))

    report = apply_policy(db, actor)

    assert report.admin_batch_status == "blocked"
    assert len(assignments_for(db, catalog[ADMIN_ROLE_CODE])) == 1
    assert len(assignments_for(db, catalog[TECHNICIAN_ROLE_CODE])) == len(users) + 1
    assert any(
        row.person_name == failed_name and row.status == expected_status
        for row in report.administrator_outcomes
    )


def test_admin_exact_name_requires_active_person_in_active_headquarters(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    headquarters = make_organization(
        db, name="蔚来总部", org_type="headquarters"
    )
    inactive_headquarters = make_organization(
        db,
        name="已停用总部",
        org_type="headquarters",
        status="inactive",
    )
    region = make_organization(db, name="江苏区域", org_type="region_company")
    for index, name in enumerate(HEADQUARTERS_ADMIN_NAMES):
        if index == 0:
            person = make_person(db, region, name=name)
        elif index == 1:
            person = make_person(
                db, headquarters, name=name, employment_status="inactive"
            )
        elif index == 2:
            person = make_person(db, inactive_headquarters, name=name)
        else:
            person = make_person(db, headquarters, name=f"{name} ")
        bind_user(db, person)

    report = apply_policy(db, actor)

    assert report.admin_batch_status == "blocked"
    assert len(assignments_for(db, catalog[ADMIN_ROLE_CODE])) == 1
    assert {
        row.status for row in report.administrator_outcomes
    } == {"admin_name_resolution_failed"}
    assert len(assignments_for(db, catalog[TECHNICIAN_ROLE_CODE])) == 4
    assert any(
        row.status == "inactive_organization"
        for row in report.technician_outcomes
    )


def test_illegal_technician_scope_blocks_only_that_person(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    region = make_organization(db, name="江苏区域", org_type="region_company")
    conflicted_person = make_person(db, region, name="范围冲突人员")
    conflicted_user = bind_user(db, conflicted_person)
    safe_person = make_person(db, region, name="正常人员")
    safe_user = bind_user(db, safe_person)
    add_assignment(
        db,
        user=conflicted_user,
        role=catalog[TECHNICIAN_ROLE_CODE],
        actor=actor,
        scope_type="national",
        scope_id="*",
        valid_from=NOW - timedelta(days=1),
    )

    report = apply_policy(db, actor)

    outcomes = {row.person_name: row for row in report.technician_outcomes}
    assert outcomes[conflicted_person.name].status == "assignment_scope_conflict"
    assert outcomes[safe_person.name].status == "created"
    assert conflicted_user.authorization_version == 1
    assert safe_user.authorization_version == 2
    assert len(assignments_for(db, catalog[TECHNICIAN_ROLE_CODE])) == 3


def test_future_assignment_is_not_overwritten_and_expired_assignment_does_not_block(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    region = make_organization(db, name="江苏区域", org_type="region_company")
    expired_person = make_person(db, region, name="历史授权人员")
    expired_user = bind_user(db, expired_person)
    add_assignment(
        db,
        user=expired_user,
        role=catalog[TECHNICIAN_ROLE_CODE],
        actor=actor,
        scope_type="person",
        scope_id=str(expired_person.id),
        valid_from=NOW - timedelta(days=2),
        valid_to=NOW - timedelta(days=1),
        status="expired",
    )
    future_person = make_person(db, region, name="未来授权人员")
    future_user = bind_user(db, future_person)
    add_assignment(
        db,
        user=future_user,
        role=catalog[TECHNICIAN_ROLE_CODE],
        actor=actor,
        scope_type="person",
        scope_id=str(future_person.id),
        valid_from=NOW + timedelta(days=1),
        status="scheduled",
    )

    report = apply_policy(db, actor)

    outcomes = {row.person_name: row for row in report.technician_outcomes}
    assert outcomes[expired_person.name].status == "created"
    assert outcomes[future_person.name].status == "future_assignment_conflict"
    assert expired_user.authorization_version == 2
    assert future_user.authorization_version == 1
    assert len(assignments_for(db, catalog[TECHNICIAN_ROLE_CODE])) == 4


def test_technician_policy_excludes_external_and_inactive_organizations_but_keeps_handover_role(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    external = make_organization(
        db, name="星星总部", org_type="external_approval_org"
    )
    inactive_region = make_organization(
        db,
        name="停用区域",
        org_type="region_company",
        status="inactive",
    )
    active_region = make_organization(
        db, name="启用区域", org_type="region_company"
    )
    external_person = make_person(db, external, name="外部审批人员")
    external_user = bind_user(db, external_person)
    inactive_org_person = make_person(db, inactive_region, name="停用组织人员")
    inactive_org_user = bind_user(db, inactive_org_person)
    left_person = make_person(
        db,
        active_region,
        name="离职交接人员",
        employment_status="left",
    )
    left_user = bind_user(
        db,
        left_person,
        account_status="restricted_handover",
    )

    report = apply_policy(db, actor)

    outcomes = {row.person_name: row for row in report.technician_outcomes}
    assert outcomes[external_person.name].status == "excluded_external_identity"
    assert outcomes[inactive_org_person.name].status == "inactive_organization"
    assert outcomes[left_person.name].status == "created"
    assert {row.user_id for row in assignments_for(
        db, catalog[TECHNICIAN_ROLE_CODE]
    )} == {left_user.id, actor.id}
    assert external_user.authorization_version == 1
    assert inactive_org_user.authorization_version == 1
    assert left_user.authorization_version == 2
    assert left_user.account_status == "restricted_handover"


def test_admin_scope_conflict_blocks_entire_admin_batch_atomically(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    headquarters = make_organization(
        db, name="蔚来总部", org_type="headquarters"
    )
    admins = seed_named_admins(db, headquarters)
    first_person, first_user = admins[HEADQUARTERS_ADMIN_NAMES[0]]
    conflict_person, conflict_user = admins[HEADQUARTERS_ADMIN_NAMES[-1]]
    add_assignment(
        db,
        user=first_user,
        role=catalog[ADMIN_ROLE_CODE],
        actor=actor,
        scope_type="national",
        scope_id="*",
        valid_from=NOW - timedelta(days=1),
    )
    add_assignment(
        db,
        user=conflict_user,
        role=catalog[ADMIN_ROLE_CODE],
        actor=actor,
        scope_type="person",
        scope_id=str(conflict_person.id),
        valid_from=NOW - timedelta(days=1),
    )

    report = apply_policy(db, actor)

    assert report.admin_batch_status == "blocked"
    assert len(assignments_for(db, catalog[ADMIN_ROLE_CODE])) == 3
    admin_statuses = {
        row.person_name: row.status for row in report.administrator_outcomes
    }
    assert admin_statuses[first_person.name] == "existing"
    assert admin_statuses[conflict_person.name] == "assignment_scope_conflict"
    assert set(admin_statuses.values()) == {
        "existing",
        "assignment_scope_conflict",
        "admin_batch_blocked",
    }
    assert all(user.authorization_version == 2 for _, user in admins.values())


@pytest.mark.parametrize("actor_user_id", ["", "   ", "missing-user"])
def test_explicit_existing_actor_is_required_before_any_write(
    db: Session,
    catalog: dict[str, Role],
    actor_user_id: str,
):
    region = make_organization(db, name="江苏区域", org_type="region_company")
    bind_user(db, make_person(db, region, name="普通工程师"))

    with pytest.raises(RoleProvisioningError):
        apply_confirmed_role_policy(
            db,
            actor_user_id=actor_user_id,
            idempotency_key="invalid-actor-run-0001",
            request_id="invalid-actor-request-0001",
            now=NOW,
        )

    assert db.scalar(select(func.count()).select_from(RoleAssignment)) == 0


def test_inactive_fixed_role_fails_before_any_policy_write(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    region = make_organization(db, name="江苏区域", org_type="region_company")
    bind_user(db, make_person(db, region, name="普通工程师"))
    catalog[ADMIN_ROLE_CODE].status = "inactive"
    db.flush()

    assignment_count_before = db.scalar(
        select(func.count()).select_from(RoleAssignment)
    )
    with pytest.raises(RoleProvisioningError):
        apply_policy(db, actor)

    assert db.scalar(
        select(func.count()).select_from(RoleAssignment)
    ) == assignment_count_before


def test_same_idempotency_key_replays_original_report_without_new_writes(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    headquarters = make_organization(
        db, name="蔚来总部", org_type="headquarters"
    )
    seed_named_admins(db, headquarters)
    raw_key = "confirmed-policy-replay-0001"

    first = apply_policy(
        db,
        actor,
        idempotency_key=raw_key,
        request_id="role-policy-request-first",
    )
    assignment_count = db.scalar(select(func.count()).select_from(RoleAssignment))
    audit_count = db.scalar(select(func.count()).select_from(AuditEvent))
    transition_count = db.scalar(
        select(func.count()).select_from(StateTransitionEvent)
    )
    second = apply_policy(
        db,
        actor,
        now=NOW + timedelta(hours=1),
        idempotency_key=raw_key,
        request_id="role-policy-request-retry",
    )

    assert first.replayed is False
    assert second.replayed is True
    assert second.evaluated_at == first.evaluated_at
    assert second.created_count == first.created_count
    assert db.scalar(select(func.count()).select_from(RoleAssignment)) == assignment_count
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == audit_count
    assert db.scalar(
        select(func.count()).select_from(StateTransitionEvent)
    ) == transition_count
    stored_keys = set(db.scalars(select(StateTransitionEvent.idempotency_key)))
    assert raw_key not in stored_keys
    assert all(len(value) == 64 for value in stored_keys)


def test_non_admin_actor_cannot_bootstrap_default_or_named_roles(
    db: Session,
    catalog: dict[str, Role],
):
    headquarters = make_organization(
        db, name="蔚来总部", org_type="headquarters"
    )
    person = make_person(db, headquarters, name="非管理员")
    actor = bind_user(db, person, account_status="active")
    db.add(
        AuthIdentity(
            user_id=actor.id,
            identity_type="mobile",
            provider_key="role-policy-test",
            identifier_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            hash_version=1,
            verified_at=NOW,
            status="active",
            revoked_at=None,
        )
    )
    add_assignment(
        db,
        user=actor,
        role=catalog[TECHNICIAN_ROLE_CODE],
        actor=actor,
        scope_type="person",
        scope_id=str(person.id),
        valid_from=NOW - timedelta(days=1),
    )
    db.add(AuditChainHead(stream_key="authorization", version=0))
    db.flush()
    assignment_count = db.scalar(select(func.count()).select_from(RoleAssignment))

    with pytest.raises(RoleProvisioningError, match="全国管理员"):
        apply_policy(db, actor)

    assert db.scalar(select(func.count()).select_from(RoleAssignment)) == assignment_count
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0


def test_missing_authorization_audit_head_rolls_back_assignments_and_versions(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    region = make_organization(db, name="江苏区域", org_type="region_company")
    target = bind_user(db, make_person(db, region, name="待开通工程师"))
    head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authorization")
    )
    assert head is not None
    db.delete(head)
    db.flush()
    assignment_count = db.scalar(select(func.count()).select_from(RoleAssignment))

    with pytest.raises(RoleProvisioningError, match="审计链"):
        apply_policy(db, actor)

    assert db.scalar(select(func.count()).select_from(RoleAssignment)) == assignment_count
    assert actor.authorization_version == 1
    assert target.authorization_version == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0


def test_caller_can_roll_back_role_assignments_versions_and_all_evidence(
    db: Session,
    catalog: dict[str, Role],
):
    actor = make_actor(db, catalog)
    headquarters = make_organization(
        db, name="蔚来总部", org_type="headquarters"
    )
    seed_named_admins(db, headquarters)
    db.commit()

    report = apply_policy(db, actor)
    assert report.created_count > 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) > 0

    db.rollback()

    refreshed_actor = db.get(User, actor.id)
    assert refreshed_actor is not None
    assert refreshed_actor.authorization_version == 1
    assert db.scalar(select(func.count()).select_from(RoleAssignment)) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0
    head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authorization")
    )
    assert head is not None
    assert head.version == 0
    assert head.last_event_id is None
    assert head.last_hash is None
