"""0060 assertions for the caller's already-validated disposable PostgreSQL 16.

No target discovery, environment reads, engine construction or credentials are
permitted here. ``security_engine`` is the main gate's explicit bootstrap
connection: the isolated migrator must never be granted API-role membership.
Every mutation, including successful controls, is rolled back. Authorization
negatives use raw INSERTs, not the supply service's authorization checks.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any
import uuid

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

_GUARD_MESSAGE = "formal material request supply projection is invalid"
_WRITE_GUARD = "rsc_guard_material_request_supply_write_0060"
_SUPPLY_OPERATIONS = (
    "create_supply_task", "update_supply_task", "cancel_supply_task",
)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        default=str,
    ).encode()).hexdigest()


def _set_role(connection: Connection, role: str) -> None:
    # Names are a closed test-only allowlist, never caller-controlled SQL.
    statements = {
        "star_oam_migrator": "SET LOCAL ROLE star_oam_migrator",
        "star_oam_api": "SET LOCAL ROLE star_oam_api",
    }
    connection.execute(text(statements[role]))
    assert connection.scalar(text("SELECT current_user")) == role


def _facts(connection: Connection, request_id, task_id, command_id, admin_user_id):
    from app.demand_models import MaterialRequest, MaterialRequestCommand, SupplyTask
    from app.foundation_models import Person, RoleAssignment

    command = dict(connection.execute(select(MaterialRequestCommand.__table__).where(
        MaterialRequestCommand.id == command_id,
    )).mappings().one())
    task = dict(connection.execute(select(SupplyTask.__table__).where(
        SupplyTask.id == task_id,
    )).mappings().one())
    request = dict(connection.execute(select(MaterialRequest.__table__).where(
        MaterialRequest.id == request_id,
    )).mappings().one())
    assert command["operation"] == "create_supply_task"
    assert command["request_id"] == request_id
    assert command["actor_user_id"] == task["created_by_user_id"] == admin_user_id
    assert command["request_jsonb"]["supply_task_id"] == str(task_id)
    assert command["target_version"] == request["version"]
    assert task["version"] == 0 and task["status"] == "open"
    assert request["status"] in {"approved", "partially_approved"}
    person = connection.execute(select(
        Person.id, Person.organization_id,
    ).where(Person.id == command["actor_person_id"])).one()
    assignment = connection.execute(select(
        RoleAssignment.id, RoleAssignment.role_id, RoleAssignment.valid_from,
    ).where(RoleAssignment.id == command["actor_role_assignment_id"])).one()
    permission_id = connection.scalar(text(
        "SELECT id FROM public.permissions WHERE resource='supply_task' "
        "AND action='manage' AND field_code=''"
    ))
    assert permission_id is not None
    return {
        "request": request, "task": task, "command": command,
        "person_id": person.id, "organization_id": person.organization_id,
        "assignment_id": assignment.id, "role_id": assignment.role_id,
        "assignment_valid_from": assignment.valid_from,
        "permission_id": permission_id,
    }


def _snapshot(connection: Connection, facts) -> str:
    """Hash only non-secret security facts and business evidence for rollback QA."""
    from app.demand_models import MaterialRequest, MaterialRequestCommand, SupplyTask
    from app.foundation_models import (
        AuthIdentity, Organization, Person, Role, RoleAssignment, RolePermission,
    )
    from app.models import User

    actor_id = facts["command"]["actor_user_id"]
    queries = (
        select(User.id, User.person_id, User.account_status, User.is_active,
               User.authorization_version).where(User.id == actor_id),
        select(Person.id, Person.organization_id, Person.employment_status).where(
            Person.id == facts["person_id"]),
        select(Organization.id, Organization.org_type, Organization.status).where(
            Organization.id == facts["organization_id"]),
        select(AuthIdentity.id, AuthIdentity.status, AuthIdentity.verified_at,
               AuthIdentity.revoked_at).where(AuthIdentity.user_id == actor_id)
            .order_by(AuthIdentity.id),
        select(Role.id, Role.code, Role.status, Role.is_external).order_by(Role.id),
        select(RolePermission.id, RolePermission.role_id, RolePermission.effect)
            .where(RolePermission.permission_id == facts["permission_id"])
            .order_by(RolePermission.id),
        select(RoleAssignment.id, RoleAssignment.role_id, RoleAssignment.scope_type,
               RoleAssignment.scope_id, RoleAssignment.status,
               RoleAssignment.valid_from, RoleAssignment.valid_to,
               RoleAssignment.revoked_at, RoleAssignment.revoked_by)
            .where(RoleAssignment.user_id == actor_id).order_by(RoleAssignment.id),
        select(MaterialRequest.version, MaterialRequest.status).where(
            MaterialRequest.id == facts["request"]["id"]),
        select(SupplyTask.__table__).where(SupplyTask.id == facts["task"]["id"]),
        select(MaterialRequestCommand.id, MaterialRequestCommand.target_version,
               MaterialRequestCommand.request_hash, MaterialRequestCommand.result_hash)
            .where(MaterialRequestCommand.request_id == facts["request"]["id"])
            .order_by(MaterialRequestCommand.target_version),
    )
    return _hash([list(connection.execute(query).tuples()) for query in queries])


def _candidate(connection: Connection, facts, *, operation: str):
    """Build structurally ordinary raw rows without any service authorization."""
    original = facts["command"]
    task = deepcopy(facts["task"])
    command = deepcopy(original)
    now = connection.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime) and now > original["occurred_at"]
    new_task_id = uuid.uuid4() if operation == "create_supply_task" else task["id"]
    task_no = f"PG16-SEC-{uuid.uuid4().hex}" if operation == "create_supply_task" else task["task_no"]
    target_task_version = 0 if operation == "create_supply_task" else task["version"] + 1
    task_status = "cancelled" if operation == "cancel_supply_task" else task["status"]
    key_hash = _hash({"test_only": str(uuid.uuid4()), "operation": operation})
    command.update(
        id=uuid.uuid4(), operation=operation,
        target_version=facts["request"]["version"] + 1,
        idempotency_key_hash=key_hash, occurred_at=now, created_at=now,
        request_reference=f"/api/v1/material-requests/{original['request_id']}/supply-tasks"
        + ("" if operation == "create_supply_task" else f"/{new_task_id}"),
    )
    # The trigger-owned manifest column is deliberately omitted, matching the
    # normal ORM insert rather than asserting write authority over that column.
    command.pop("projection_manifest_sha256", None)
    result = command["result_jsonb"]
    result.update(
        action=operation, request_version=command["target_version"],
        supply_task_id=str(new_task_id), task_no=task_no,
        task_version=target_task_version, task_status=task_status,
    )
    # The payload has no external destination and never reaches a commit. Its
    # digest is self-consistent so a malformed hash is not our rejection oracle.
    command["request_hash"] = _hash({
        "test_only": key_hash, "operation": operation,
        "request_id": str(original["request_id"]), "supply_task_id": str(new_task_id),
    })
    command["result_hash"] = _hash(result)
    command["request_jsonb"].update(
        operation=operation, target_version=command["target_version"],
        supply_task_id=str(new_task_id), task_no=task_no,
        target_task_version=target_task_version,
        payload_sha256=command["request_hash"],
    )
    task.update(id=new_task_id, task_no=task_no, created_at=now, updated_at=now)
    return command, task


def _deny_scope(connection: Connection, facts, scope_type: str, scope_id: str) -> None:
    """Add a deny on another grant; selected HQ allow remains untouched."""
    from app.foundation_models import Role, RoleAssignment

    role_id = connection.scalar(select(Role.id).where(Role.code == "technician"))
    assert role_id is not None and role_id != facts["role_id"]
    connection.execute(text(
        "INSERT INTO public.role_permissions (id, role_id, permission_id, effect, created_at) "
        "VALUES (:id, :role, :permission, 'deny', clock_timestamp()) "
        "ON CONFLICT (role_id, permission_id) DO UPDATE SET effect='deny'"
    ), {"id": uuid.uuid4(), "role": role_id, "permission": facts["permission_id"]})
    actor_id = facts["command"]["actor_user_id"]
    current = connection.scalar(select(RoleAssignment.id).where(
        RoleAssignment.user_id == actor_id, RoleAssignment.role_id == role_id,
        RoleAssignment.scope_type == scope_type, RoleAssignment.scope_id == scope_id,
        RoleAssignment.status.in_(("scheduled", "active")),
    ))
    now = connection.scalar(select(func.clock_timestamp()))
    values = dict(
        valid_from=now - timedelta(days=1), valid_to=None, status="active",
        revoked_at=None, revoked_by=None, updated_at=now,
    )
    if current is not None:
        connection.execute(update(RoleAssignment.__table__).where(
            RoleAssignment.id == current,
        ).values(**values))
    else:
        connection.execute(insert(RoleAssignment.__table__).values(
            id=uuid.uuid4(), user_id=actor_id, role_id=role_id,
            scope_type=scope_type, scope_id=scope_id, assigned_by=actor_id,
            reason="disposable PG16 supply authorization counterexample",
            created_at=now, **values,
        ))


def _mutate_authorization(connection: Connection, facts, case: str) -> None:
    from app.foundation_models import (
        AuthIdentity, Organization, Person, Role, RoleAssignment, RolePermission,
    )
    from app.models import User

    actor_id = facts["command"]["actor_user_id"]
    now = connection.scalar(select(func.clock_timestamp()))
    if case == "selected_permission_deny":
        statement = update(RolePermission.__table__).where(
            RolePermission.role_id == facts["role_id"],
            RolePermission.permission_id == facts["permission_id"],
        ).values(effect="deny")
    elif case in {"national_deny", "requester_org_deny", "ancestor_org_deny", "uncovered_person_deny"}:
        scope_type, scope_id = {
            "national_deny": ("national", "*"),
            "requester_org_deny": ("organization", str(facts["request"]["requester_org_id"])),
            "ancestor_org_deny": ("organization", str(facts["ancestor_org_id"])),
            "uncovered_person_deny": ("person", str(facts["person_id"])),
        }[case]
        _deny_scope(connection, facts, scope_type, scope_id)
        return
    elif case in {"revoked_identity", "unverified_identity"}:
        statement = update(AuthIdentity.__table__).where(
            AuthIdentity.user_id == actor_id,
        ).values(**({"status": "revoked", "revoked_at": now} if case == "revoked_identity"
                    else {"status": "pending", "verified_at": None, "revoked_at": None}))
    elif case in {"disabled_account", "inactive_user", "stale_authorization_version"}:
        values = {
            "disabled_account": {"account_status": "disabled"},
            "inactive_user": {"is_active": False},
            "stale_authorization_version": {"authorization_version": User.authorization_version + 1},
        }[case]
        statement = update(User.__table__).where(User.id == actor_id).values(**values)
    elif case == "inactive_person":
        statement = update(Person.__table__).where(Person.id == facts["person_id"]).values(employment_status="left")
    elif case in {"non_headquarters_org", "inactive_org"}:
        statement = update(Organization.__table__).where(Organization.id == facts["organization_id"]).values(
            **({"org_type": "department"} if case == "non_headquarters_org" else {"status": "inactive"}))
    elif case in {"external_role", "inactive_role"}:
        statement = update(Role.__table__).where(Role.id == facts["role_id"]).values(
            **({"is_external": True} if case == "external_role" else {"status": "inactive"}))
    elif case in {"revoked_assignment", "expired_assignment", "scoped_admin_assignment"}:
        values = {
            "revoked_assignment": {"status": "revoked", "revoked_at": now, "revoked_by": actor_id},
            "expired_assignment": {"valid_to": now},
            "scoped_admin_assignment": {"scope_type": "organization", "scope_id": str(facts["request"]["requester_org_id"])},
        }[case]
        statement = update(RoleAssignment.__table__).where(RoleAssignment.id == facts["assignment_id"]).values(**values)
    else:
        raise AssertionError(f"unknown test case: {case}")
    assert connection.execute(statement).rowcount > 0, case


def _assert_write_guard(error: DBAPIError, *, case: str, target: str) -> None:
    original = error.orig
    assert getattr(original, "sqlstate", None) == "23514", (case, target)
    diagnostic = getattr(original, "diag", None)
    assert diagnostic is not None, (case, target)
    assert diagnostic.message_primary == _GUARD_MESSAGE, (case, target)
    # Existing capacity, uniqueness and causal validators are NOT proof of 0060
    # authorization rejection. Require its exact BEFORE INSERT trigger context.
    assert _WRITE_GUARD + "()" in (diagnostic.context or ""), (case, target)


def _assert_historical_actor_revocation(security_engine: Engine, facts, second_admin_user_id: str) -> None:
    from app.formal_access import load_formal_principal
    from app.formal_services.material_request_query import material_request_detail
    from app.formal_services.material_request_supply import SupplyTaskUpdateInput, update_supply_task

    with security_engine.connect() as connection:
        transaction = connection.begin()
        try:
            _set_role(connection, "star_oam_migrator")
            _mutate_authorization(connection, facts, "revoked_assignment")
            _mutate_authorization(connection, facts, "stale_authorization_version")
            _set_role(connection, "star_oam_api")
            # Only this positive control uses the service to produce the whole
            # legitimate graph. Database constraints, not a service assertion,
            # must then accept the history whose old actor has been revoked.
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                principal = load_formal_principal(db, second_admin_user_id)
                detail = material_request_detail(db, actor=principal, request_id=facts["request"]["id"])
                projected = next(row for row in detail.supply_tasks if row.id == facts["task"]["id"])
                assert projected.version == facts["task"]["version"]
                result = update_supply_task(
                    db, actor=principal, material_request_id=facts["request"]["id"],
                    supply_task_id=facts["task"]["id"],
                    expected_request_version=facts["request"]["version"],
                    expected_task_version=facts["task"]["version"],
                    update=SupplyTaskUpdateInput(
                        status=facts["task"]["status"],
                        reference_no=facts["task"]["reference_no"],
                        expected_date=facts["task"]["expected_date"],
                        comment="PG16 historical creator revocation positive control",
                    ),
                    idempotency_key=f"pg16-security-history-{uuid.uuid4()}",
                    idempotency_hmac_secret="disposable-pg16-security-test-secret-not-a-credential",
                    trace_request_id=f"pg16-security-history-{uuid.uuid4()}",
                )
                assert result.task_version == facts["task"]["version"] + 1
                db.flush()
                db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                # Release only the savepoint; the surrounding bootstrap
                # transaction is unconditionally rolled back below.
                db.commit()
        finally:
            transaction.rollback()


def assert_supply_security_gate(
    api_engine: Engine,
    security_engine: Engine,
    *,
    request_id: uuid.UUID,
    task_id: uuid.UUID,
    command_id: uuid.UUID,
    admin_user_id: str,
    second_admin_user_id: str | None = None,
) -> tuple[str, ...]:
    """Assert fresh-authority rejection without leaving any test mutations.

    Called immediately after the first genuine supply create commit. The API
    engine is also used after EACH rollback to independently verify restoration.
    This helper intentionally does not disable triggers to forge version history;
    a 0,1,1,3 upgrade counterexample needs its own pre-0060 disposable fixture.
    """
    # Match the main gate's existing lazy helper imports: application models have
    # already been loaded under its validated test configuration at this point.
    # Importing this helper alone must not initialize application configuration.
    from app.demand_models import MaterialRequestCommand, SupplyTask

    with api_engine.connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == "star_oam_api"
        assert 160000 <= int(connection.scalar(text("SHOW server_version_num"))) < 170000
        facts = _facts(connection, request_id, task_id, command_id, admin_user_id)
        original_snapshot = _snapshot(connection, facts)
        ancestor = connection.scalar(text(
            "SELECT parent_id FROM public.organizations WHERE id=:id"
        ), {"id": facts["request"]["requester_org_id"]})
        facts["ancestor_org_id"] = ancestor
        if second_admin_user_id is None:
            second_admin_user_id = connection.scalar(text(
                "SELECT assignment.user_id FROM public.role_assignments AS assignment "
                "JOIN public.roles AS role ON role.id=assignment.role_id "
                "JOIN public.users AS actor ON actor.id=assignment.user_id "
                "WHERE role.code='admin' AND role.status='active' AND NOT role.is_external "
                "AND assignment.scope_type='national' AND assignment.scope_id='*' "
                "AND assignment.status='active' AND assignment.revoked_at IS NULL "
                "AND assignment.valid_from<=clock_timestamp() "
                "AND (assignment.valid_to IS NULL OR assignment.valid_to>clock_timestamp()) "
                "AND actor.account_status='active' AND actor.is_active AND actor.id<>:actor "
                "ORDER BY assignment.user_id LIMIT 1"
            ), {"actor": admin_user_id})
    with security_engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=session_user"
        )) is True
        assert connection.scalar(text("SELECT current_database()")) == api_engine.url.database
        assert connection.scalar(text(
            "SELECT count(*) FROM pg_catalog.pg_auth_members AS membership "
            "JOIN pg_catalog.pg_roles AS role ON role.oid=membership.member "
            "OR role.oid=membership.roleid WHERE role.rolname='star_oam_migrator'"
        )) == 0

    def restored() -> None:
        with api_engine.connect() as connection:
            assert _snapshot(connection, facts) == original_snapshot

    # These controls test BEFORE INSERT acceptance only, not an incomplete
    # business graph's commit. Full successful commits live in pg16_supply_gate.
    controls = (None, "uncovered_person_deny")
    for control in controls:
        with security_engine.connect() as connection:
            transaction = connection.begin()
            try:
                _set_role(connection, "star_oam_migrator")
                if control:
                    _mutate_authorization(connection, facts, control)
                _set_role(connection, "star_oam_api")
                command, _task = _candidate(connection, facts, operation="update_supply_task")
                connection.execute(insert(MaterialRequestCommand.__table__).values(**command))
            finally:
                transaction.rollback()
        restored()

    cases = [
        "selected_permission_deny", "national_deny", "requester_org_deny",
        "revoked_identity", "unverified_identity", "disabled_account",
        "inactive_user", "stale_authorization_version", "inactive_person",
        "non_headquarters_org", "inactive_org", "external_role", "inactive_role",
        "revoked_assignment", "expired_assignment", "scoped_admin_assignment",
    ]
    if ancestor is not None:
        cases.append("ancestor_org_deny")
    covered: list[str] = []
    for case in cases:
        # All three command operations and the direct task INSERT must enter the
        # same new guard; a service-denied mutation cannot satisfy this proof.
        for target in (*_SUPPLY_OPERATIONS, "raw_task_insert"):
            with security_engine.connect() as connection:
                transaction = connection.begin()
                try:
                    _set_role(connection, "star_oam_migrator")
                    _mutate_authorization(connection, facts, case)
                    _set_role(connection, "star_oam_api")
                    command, task = _candidate(connection, facts, operation=(
                        "create_supply_task" if target == "raw_task_insert" else target))
                    table, values = ((SupplyTask.__table__, task) if target == "raw_task_insert"
                                     else (MaterialRequestCommand.__table__, command))
                    with pytest.raises(DBAPIError) as caught:
                        connection.execute(insert(table).values(**values))
                    _assert_write_guard(caught.value, case=case, target=target)
                finally:
                    transaction.rollback()
            restored()
            covered.append(f"{case}:{target}")

    assert second_admin_user_id and second_admin_user_id != admin_user_id, (
        "disposable supply fixture needs a second active HQ for historical revocation control"
    )
    _assert_historical_actor_revocation(security_engine, facts, second_admin_user_id)
    restored()
    covered.append("historical_actor_revocation:second_hq_read_and_causal_append")
    return tuple(covered)
