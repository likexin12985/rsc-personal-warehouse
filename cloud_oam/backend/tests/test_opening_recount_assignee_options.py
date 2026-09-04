from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import opening_recount_assignee_options as service
from app.formal_services import opening_stocktake_recount as recount_service
from app.formal_services.opening_stocktake import OpeningStocktakeScopeInput
from app.formal_services.opening_stocktake_review import submit_opening_region_review
from app.foundation_models import AuthIdentity, Person, RoleAssignment, RolePermission
from app.inventory_models import (
    CustodyAssignment,
    StockAccount,
    StockBalance,
    StockLocation,
)
from app.models import User
from app.opening_recount_assignee_option_schemas import (
    OpeningRecountAssigneeOptionPageOut,
)
from app.routers import formal_opening_stocktake_read
from app.stocktake_models import FormalStocktakeTask, InventoryFreeze, StocktakeRound
from test_opening_stocktake_review_service import (  # noqa: F401
    NOW,
    _fixed_database_times,
    _prepare_submitted,
    _review_command,
    _user_with_role,
    db,
    world,
)


READ_AT = NOW + timedelta(hours=3)


def _prepare_recount_required(world, *, command=None, count_actor=None):
    prepared = _prepare_submitted(
        world,
        command=command,
        count_actor=count_actor,
    )
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="区域明确要求复盘",
        ),
        idempotency_key=f"opening-assignee-review-{uuid.uuid4().hex}",
        request_id="opening-assignee-review-request",
    )
    world.db.commit()
    world.db.expire_all()
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    source_round = world.db.get(StocktakeRound, prepared.round.id)
    assert task is not None and task.status == "recount_required"
    assert source_round is not None and source_round.status == "submitted"
    return task, source_round, prepared.scope


def _list(world, task, source_round, scope, **overrides):
    arguments = {
        "actor": world.principals["manager_x"],
        "task_id": task.id,
        "source_round_id": source_round.id,
        "scope_id": scope.id,
        "expected_task_version": task.version,
        "limit": 100,
        "now": READ_AT,
    }
    arguments.update(overrides)
    return service.list_opening_recount_assignee_options(world.db, **arguments)


def _add_fixed_outsider(world, *, person_id: uuid.UUID, name: str):
    person = Person(
        id=person_id,
        organization_id=world.region_y.id,
        employee_no=f"OUT-{person_id.hex[-8:]}",
        name=name,
        mobile_encrypted=None,
        mobile_hash=None,
        employment_status="active",
        source_updated_at=NOW,
    )
    user = User(
        id=str(uuid.uuid4()),
        person_id=person.id,
        account_status="active",
        authorization_version=1,
        mobile=f"1{uuid.uuid4().int % 10**10:010d}",
        name=name,
        password_hash="formal-password-disabled",
        role="provincial_manager",
        province=None,
        is_active=True,
        require_password_change=False,
    )
    world.db.add(person)
    world.db.flush()
    world.db.add(user)
    world.db.flush()
    assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=user.id,
        role_id=world.roles["provincial_manager"].id,
        scope_type="organization",
        scope_id=str(world.region_y.id),
        valid_from=NOW - timedelta(days=30),
        valid_to=None,
        status="active",
        assigned_by=user.id,
        revoked_at=None,
        revoked_by=None,
        reason="out-of-scope pagination fixture",
    )
    identity = AuthIdentity(
        id=uuid.uuid4(),
        user_id=user.id,
        identity_type="mobile",
        provider_key="test",
        identifier_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        hash_version=1,
        verified_at=NOW - timedelta(days=1),
        status="active",
        revoked_at=None,
    )
    world.db.add_all([assignment, identity])
    world.db.flush()
    return user, person


def test_region_scope_returns_only_write_side_eligible_manager_and_admin(world):
    task, source_round, scope = _prepare_recount_required(world)

    output = _list(world, task, source_round, scope)

    assert output.task_id == task.id
    assert output.source_round_id == source_round.id
    assert output.scope_id == scope.id
    assert output.location_id == world.location.id
    assert output.region_org_id == world.region_x.id
    assert output.task_version == task.version
    assert output.actor_person_id == world.manager_x.person.id
    assert output.actor_authorization_version == world.manager_x.user.authorization_version
    assert {(row.user_id, row.role_code) for row in output.items} == {
        (world.admin.user.id, "admin"),
        (world.manager_x.user.id, "provincial_manager"),
    }
    assert world.manager_y.user.id not in {row.user_id for row in output.items}
    assert world.technician.user.id not in {row.user_id for row in output.items}


def test_explicit_count_deny_filters_candidate_without_expanding_another_role(world):
    task, source_round, scope = _prepare_recount_required(world)
    admin_count = world.db.scalar(
        select(RolePermission).where(
            RolePermission.role_id == world.roles["admin"].id,
            RolePermission.permission_id == world.permissions["count"].id,
        )
    )
    assert admin_count is not None
    admin_count.effect = "deny"
    admin_user = world.db.get(type(world.admin.user), world.admin.user.id)
    assert admin_user is not None
    admin_user.authorization_version += 1
    world.db.flush()

    output = _list(world, task, source_round, scope)

    assert {(row.user_id, row.role_code) for row in output.items} == {
        (world.manager_x.user.id, "provincial_manager"),
    }


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"task_id": uuid.uuid4()}, "opening_recount_assignee_task_not_found"),
        ({"scope_id": uuid.uuid4()}, "opening_recount_assignee_scope_not_found"),
        (
            {"source_round_id": uuid.uuid4()},
            "opening_recount_assignee_round_not_found",
        ),
    ],
)
def test_exact_task_round_scope_binding_rejects_wrong_coordinates(
    world,
    overrides,
    expected_code,
):
    task, source_round, scope = _prepare_recount_required(world)

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope, **overrides)

    assert captured.value.code == expected_code


def test_stale_task_version_and_changed_source_round_fail_closed(world):
    task, source_round, scope = _prepare_recount_required(world)
    with pytest.raises(service.OpeningRecountAssigneeOptionError) as stale:
        _list(
            world,
            task,
            source_round,
            scope,
            expected_task_version=task.version - 1,
        )
    assert stale.value.code == "opening_recount_assignee_task_version_conflict"
    assert stale.value.http_status_code == 409

    source_round.status = "superseded"
    world.db.flush()
    with pytest.raises(service.OpeningRecountAssigneeOptionError) as changed:
        _list(world, task, source_round, scope)
    assert changed.value.code == "opening_recount_source_graph_invalid"
    assert changed.value.http_status_code == 412


def test_cross_region_actor_is_denied_before_task_version_or_graph_details(world):
    task, source_round, scope = _prepare_recount_required(world)

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(
            world,
            task,
            source_round,
            scope,
            actor=world.principals["manager_y"],
            expected_task_version=task.version + 99,
        )

    assert captured.value.code == "opening_recount_opener_forbidden"
    assert captured.value.http_status_code == 403


def test_revoked_or_stale_actor_authorization_is_rejected(world):
    task, source_round, scope = _prepare_recount_required(world)
    admin_user_id = world.admin.user.id
    manager_user = world.db.get(type(world.manager_x.user), world.manager_x.user.id)
    assignment = world.db.get(
        type(world.manager_x.assignment),
        world.manager_x.assignment.id,
    )
    assert manager_user is not None and assignment is not None
    assignment.revoked_at = READ_AT - timedelta(minutes=1)
    assignment.status = "revoked"
    assignment.revoked_by = admin_user_id
    manager_user.authorization_version += 1
    world.db.flush()

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope)

    assert captured.value.code in {
        "opening_recount_actor_not_current",
        "opening_recount_actor_principal_stale",
    }
    assert captured.value.http_status_code in {403, 412}


def test_tampered_sealed_scope_manifest_rejects_before_candidate_scan(world, monkeypatch):
    task, source_round, scope = _prepare_recount_required(world)
    task.scope_manifest_sha256 = "f" * 64
    world.db.flush()
    candidates = Mock(side_effect=AssertionError("must not expose candidate identities"))
    monkeypatch.setattr(recount_service, "_authorize_scope_assignee", candidates)

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope)

    assert captured.value.code == "opening_recount_assignee_scope_manifest_invalid"
    candidates.assert_not_called()


def test_asset_owner_outside_actor_dimensions_rejects_even_with_matching_manifest(world, monkeypatch):
    task, source_round, scope = _prepare_recount_required(world)
    # Model a cross-owner task that only a national grant could manage. The
    # directory must not expand a regional manager to that other asset owner.
    scope.owner_org_id = world.region_y.id
    freezes = tuple(world.db.scalars(select(InventoryFreeze).where(InventoryFreeze.task_id == task.id)))
    task.scope_manifest_sha256 = recount_service.canonical_opening_recount_scope_manifest_sha256(
        task.region_org_id, (scope,), freezes
    )
    world.db.flush()
    candidates = Mock(side_effect=AssertionError("must not expose candidate identities"))
    monkeypatch.setattr(recount_service, "_authorize_scope_assignee", candidates)

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope)

    assert captured.value.code == "opening_scope_dimension_forbidden"
    assert captured.value.http_status_code == 403
    candidates.assert_not_called()


def test_physical_location_owner_outside_actor_dimensions_rejects(world, monkeypatch):
    task, source_round, scope = _prepare_recount_required(world)
    world.db.get(StockLocation, scope.location_id).owner_org_id = world.region_y.id
    world.db.flush()
    candidates = Mock(side_effect=AssertionError("must not expose candidate identities"))
    monkeypatch.setattr(recount_service, "_authorize_scope_assignee", candidates)

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope)

    assert captured.value.code == "opening_scope_dimension_forbidden"
    assert captured.value.http_status_code == 403
    candidates.assert_not_called()


def test_malformed_candidate_authorization_graph_fails_the_whole_page(world):
    task, source_round, scope = _prepare_recount_required(world)
    assignment = world.db.get(
        type(world.manager_y.assignment),
        world.manager_y.assignment.id,
    )
    assert assignment is not None
    assignment.scope_id = str(uuid.uuid4())
    world.db.flush()

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope)

    assert captured.value.code == (
        "opening_recount_assignee_authorization_graph_invalid"
    )
    assert captured.value.http_status_code == 503


def test_malformed_scope_authorization_graph_is_unavailable_not_forbidden(
    world,
    monkeypatch: pytest.MonkeyPatch,
):
    task, source_round, scope = _prepare_recount_required(world)

    def malformed_scope_graph(*_args, **_kwargs):
        raise service.opening_service.OpeningStocktakeError(
            "opening_scope_authorization_invalid",
            "forbidden",
            "期初盘点授权范围图无效",
        )

    monkeypatch.setattr(
        service.opening_service,
        "_authorize_scope_dimensions",
        malformed_scope_graph,
    )

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope)

    assert captured.value.code == "opening_scope_authorization_invalid"
    assert captured.value.http_status_code == 503


def test_personal_scope_allows_exact_technician_but_not_another_technician(world):
    personal_location = StockLocation(
        id=uuid.uuid4(),
        code="PERSONAL-ASSIGNEE-OPTIONS",
        name="期初复盘个人仓",
        location_type="personal",
        owner_org_id=world.region_x.id,
        parent_id=world.location.id,
        custodian_person_id=world.technician.person.id,
        status="active",
    )
    world.db.add(personal_location)
    world.db.flush()
    world.db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=personal_location.id,
            custodian_person_id=world.technician.person.id,
            valid_from=NOW - timedelta(days=30),
            valid_to=None,
            handover_case_id=None,
        )
    )
    personal_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.region_x.id,
        custodian_person_id=world.technician.person.id,
        location_id=personal_location.id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    world.db.add(personal_account)
    world.db.flush()
    world.db.add(
        StockBalance(
            stock_account_id=personal_account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=0,
            version=1,
        )
    )
    other_technician = _user_with_role(
        world.db,
        world.region_x,
        world.roles["technician"],
        "person",
        None,
        "Other-Technician-X",
    )
    world.db.commit()
    command = replace(
        world.command,
        task_no="OPEN-ASSIGNEE-PERSONAL-001",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    task, source_round, scope = _prepare_recount_required(
        world,
        command=command,
        count_actor=world.principals["technician"],
    )

    output = _list(world, task, source_round, scope)

    by_user = {row.user_id: row for row in output.items}
    assert by_user[world.technician.user.id].role_code == "technician"
    assert by_user[world.admin.user.id].role_code == "admin"
    assert by_user[world.manager_x.user.id].role_code == "provincial_manager"
    assert other_technician.user.id not in by_user


def test_paging_cursor_is_only_the_last_returned_authorized_person(world):
    task, source_round, scope = _prepare_recount_required(world)
    eligible_person_ids = sorted(
        (world.admin.person.id, world.manager_x.person.id), key=str
    )
    forbidden_person_ids = {
        world.manager_y.person.id,
        world.technician.person.id,
    }
    cursor = None
    seen_items: set[str] = set()
    consumed_cursors: list[uuid.UUID] = []
    for _ in range(len(eligible_person_ids) + 1):
        output = _list(
            world,
            task,
            source_round,
            scope,
            limit=1,
            after_person_id=cursor,
        )
        seen_items.update(row.user_id for row in output.items)
        if output.next_after_person_id is None:
            break
        assert len(output.items) == 1
        assert output.next_after_person_id == output.items[-1].person_id
        consumed_cursors.append(output.next_after_person_id)
        cursor = output.next_after_person_id
    else:  # pragma: no cover - defensive pagination bound
        pytest.fail("candidate cursor did not terminate")

    assert consumed_cursors == eligible_person_ids[:-1]
    assert consumed_cursors == sorted(set(consumed_cursors), key=str)
    assert forbidden_person_ids.isdisjoint(consumed_cursors)
    assert seen_items == {world.admin.user.id, world.manager_x.user.id}


def test_internal_multi_batch_scan_filters_many_outsiders_without_leaking_cursor(
    world,
    monkeypatch: pytest.MonkeyPatch,
):
    task, source_round, scope = _prepare_recount_required(world)
    outsiders = tuple(
        _user_with_role(
            world.db,
            world.region_y,
            world.roles["provincial_manager"],
            "organization",
            str(world.region_y.id),
            f"Filtered-Outsider-{index}",
        )
        for index in range(18)
    )
    world.db.flush()
    monkeypatch.setattr(service, "_RAW_SCAN_BATCH_SIZE", 3)

    output = _list(world, task, source_round, scope, limit=100)

    assert {row.user_id for row in output.items} == {
        world.admin.user.id,
        world.manager_x.user.id,
    }
    assert output.next_after_person_id is None
    assert {row.person.id for row in outsiders}.isdisjoint(
        {row.person_id for row in output.items}
    )


def test_filtered_tail_returns_empty_page_without_any_raw_identity_cursor(world):
    task, source_round, scope = _prepare_recount_required(world)
    _user, outsider = _add_fixed_outsider(
        world,
        person_id=uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        name="Filtered-Tail-Outsider",
    )
    last_eligible = max(
        (world.admin.person.id, world.manager_x.person.id), key=str
    )
    assert outsider.id > last_eligible

    output = _list(
        world,
        task,
        source_round,
        scope,
        limit=1,
        after_person_id=last_eligible,
    )

    assert output.items == ()
    assert output.next_after_person_id is None


def test_scan_limit_fails_closed_instead_of_returning_filtered_coordinate(
    world,
    monkeypatch: pytest.MonkeyPatch,
):
    task, source_round, scope = _prepare_recount_required(world)
    _add_fixed_outsider(
        world,
        person_id=uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffffd"),
        name="Filtered-Overflow-One",
    )
    _add_fixed_outsider(
        world,
        person_id=uuid.UUID("ffffffff-ffff-ffff-ffff-fffffffffffe"),
        name="Filtered-Overflow-Two",
    )
    monkeypatch.setattr(service, "_RAW_SCAN_BATCH_SIZE", 1)
    monkeypatch.setattr(service, "_MAX_RAW_IDENTITIES_SCANNED", 1)

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(
            world,
            task,
            source_round,
            scope,
            limit=1,
            after_person_id=uuid.UUID(
                "ffffffff-ffff-ffff-ffff-fffffffffffc"
            ),
        )

    assert captured.value.code == "opening_recount_assignee_scan_limit_exceeded"
    assert captured.value.http_status_code == 503


def test_returned_candidate_is_revalidated_after_scan(world, monkeypatch):
    task, source_round, scope = _prepare_recount_required(world)
    original = service._candidate_snapshot
    changed = False

    def mutate_admin_name_after_snapshot(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if kwargs["user"].id == world.admin.user.id and not changed:
            changed = True
            person = world.db.get(Person, world.admin.person.id)
            assert person is not None
            person.name = "Changed-While-Scanning"
            world.db.flush()
        return result

    monkeypatch.setattr(
        service,
        "_candidate_snapshot",
        mutate_admin_name_after_snapshot,
    )

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope)

    assert changed is True
    assert captured.value.code == "opening_recount_assignee_read_conflict"
    assert captured.value.http_status_code == 409


def test_source_round_changed_during_candidate_scan_is_not_returned(
    world,
    monkeypatch: pytest.MonkeyPatch,
):
    task, source_round, scope = _prepare_recount_required(world)
    original = recount_service._authorize_scope_assignee
    changed = False

    def mutate_after_authorization(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            row = world.db.get(StocktakeRound, source_round.id)
            assert row is not None
            row.status = "superseded"
            world.db.flush()
        return result

    monkeypatch.setattr(
        recount_service,
        "_authorize_scope_assignee",
        mutate_after_authorization,
    )

    with pytest.raises(service.OpeningRecountAssigneeOptionError) as captured:
        _list(world, task, source_round, scope)

    assert changed is True
    assert captured.value.code == "opening_recount_source_graph_invalid"
    assert captured.value.http_status_code == 412


def test_candidate_read_uses_plain_selects_no_flush_and_lock_rows_false(
    world,
    monkeypatch: pytest.MonkeyPatch,
):
    task, source_round, scope = _prepare_recount_required(world)
    task_id = task.id
    source_round_id = source_round.id
    scope_id = scope.id
    task_version = task.version
    statements: list[str] = []
    engine = world.db.get_bind()

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    flush = Mock(side_effect=AssertionError("read endpoint must not flush"))
    commit = Mock(side_effect=AssertionError("read endpoint must not commit"))
    rollback = Mock(side_effect=AssertionError("read endpoint must not rollback"))
    monkeypatch.setattr(world.db, "flush", flush)
    monkeypatch.setattr(world.db, "commit", commit)
    monkeypatch.setattr(world.db, "rollback", rollback)
    original_assignee = recount_service._authorize_scope_assignee
    original_opener = recount_service._authorize_opener
    assignee_lock_flags: list[bool] = []
    opener_lock_flags: list[bool] = []

    def spy_authorize(*args, **kwargs):
        assignee_lock_flags.append(kwargs["lock_rows"])
        return original_assignee(*args, **kwargs)

    def spy_opener(*args, **kwargs):
        opener_lock_flags.append(kwargs["lock_rows"])
        return original_opener(*args, **kwargs)

    monkeypatch.setattr(recount_service, "_authorize_scope_assignee", spy_authorize)
    monkeypatch.setattr(recount_service, "_authorize_opener", spy_opener)
    try:
        output = service.list_opening_recount_assignee_options(
            world.db,
            actor=world.principals["manager_x"],
            task_id=task_id,
            source_round_id=source_round_id,
            scope_id=scope_id,
            expected_task_version=task_version,
            limit=100,
            now=READ_AT,
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert output.items
    assert assignee_lock_flags and set(assignee_lock_flags) == {False}
    assert opener_lock_flags == [False, False]
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert all("FOR UPDATE" not in statement.upper() for statement in statements)
    flush.assert_not_called()
    commit.assert_not_called()
    rollback.assert_not_called()


def test_route_projects_strict_page_and_sets_private_no_store(monkeypatch):
    task_id = uuid.uuid4()
    round_id = uuid.uuid4()
    scope_id = uuid.uuid4()
    location_id = uuid.uuid4()
    region_id = uuid.uuid4()
    person_id = uuid.uuid4()
    database = Mock()
    principal = Mock()
    principal.allows.return_value = True
    output = OpeningRecountAssigneeOptionPageOut(
        task_id=task_id,
        source_round_id=round_id,
        scope_id=scope_id,
        location_id=location_id,
        region_org_id=region_id,
        task_version=9,
        actor_person_id=person_id,
        actor_authorization_version=3,
        items=(),
        next_after_person_id=None,
    )
    call = Mock(return_value=output)
    monkeypatch.setattr(service, "list_opening_recount_assignee_options", call)
    api = FastAPI()
    api.include_router(formal_opening_stocktake_read.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: principal

    with TestClient(api) as client:
        response = client.get(
            f"/api/v1/stocktakes/opening/{task_id}/rounds/{round_id}"
            f"/scopes/{scope_id}/assignees",
            params={"expected_task_version": 9, "limit": 25},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.json() == {
        "schema_version": "1.0",
        "task_id": str(task_id),
        "source_round_id": str(round_id),
        "scope_id": str(scope_id),
        "location_id": str(location_id),
        "region_org_id": str(region_id),
        "task_version": 9,
        "actor_person_id": str(person_id),
        "actor_authorization_version": 3,
        "items": [],
        "next_after_person_id": None,
    }
    call.assert_called_once_with(
        database,
        actor=principal,
        task_id=task_id,
        source_round_id=round_id,
        scope_id=scope_id,
        expected_task_version=9,
        limit=25,
        after_person_id=None,
    )
    database.commit.assert_not_called()
    database.rollback.assert_not_called()

    call.reset_mock()
    call.side_effect = service.OpeningRecountAssigneeOptionError(
        "opening_recount_opener_forbidden", "forbidden", "当前区域未授权"
    )
    with TestClient(api) as client:
        denied = client.get(
            f"/api/v1/stocktakes/opening/{task_id}/rounds/{round_id}"
            f"/scopes/{scope_id}/assignees",
            params={"expected_task_version": 9},
        )
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "private, no-store, max-age=0"
    assert denied.headers["x-content-type-options"] == "nosniff"
    assert denied.json()["detail"]["code"] == "opening_recount_opener_forbidden"
    database.commit.assert_not_called()
    database.rollback.assert_not_called()
