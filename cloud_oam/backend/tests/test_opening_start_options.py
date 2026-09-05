from __future__ import annotations

from datetime import timedelta
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.formal_services import opening_start_options as service
from app.formal_services import opening_stocktake as opening
from app.foundation_models import AuthIdentity, Organization, Permission, Person, RoleAssignment, RolePermission
from app.inventory_models import CustodyAssignment, StockLocation
from app.models import User
from app.routers import formal_opening_start_options as router
from test_opening_stocktake_service import NOW, world, _organization, _user_with_role  # noqa: F401


STAGES = ("regions", "asset_owners", "locations", "assignees")
QUERIES = {
    "regions": service.list_region_options,
    "asset_owners": service.list_asset_owner_options,
    "locations": service.list_location_options,
    "assignees": service.list_assignee_options,
}
PREFIX = "/api/v1/stocktakes/opening/start-options"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _coordinates(world, stage):
    result = {}
    if stage != "regions":
        result["region_org_id"] = world.region_x.id
    if stage in {"locations", "assignees"}:
        result["owner_org_id"] = world.region_x.id
    if stage == "assignees":
        result["location_id"] = world.personal_location.id
    return result


def _call(world, stage, *, actor="admin", **overrides):
    args = {"limit": 50, "now": NOW, **_coordinates(world, stage), **overrides}
    principal = world.principals[actor] if isinstance(actor, str) else actor
    return QUERIES[stage](
        world.db, actor=principal, **args,
    )


def _assert_not_ready(page, world):
    payload = page.model_dump(mode="json")
    assert payload["schema_version"] == "1.0"
    assert payload["actor_person_id"] == str(world.admin.person.id)
    assert payload["authorization_version"] == 1
    assert payload["start_ready"] is False
    assert payload["control_evidence_status"] == "control_evidence_not_evaluated"
    forbidden = {"control_qty", "book_qty", "quantity", "sync_run_id", "control_sync_run_id",
                 "scope_id", "task_id", "scope_sha256", "idempotency_key", "token", "mobile"}

    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for nested in value.values():
                check(nested)
        elif isinstance(value, list):
            for nested in value:
                check(nested)
    check(payload)


@pytest.mark.parametrize("stage", STAGES)
def test_four_layers_are_explicitly_not_control_evidence_or_a_start_permit(world, stage):
    page = _call(world, stage)
    assert page.items
    _assert_not_ready(page, world)
    for field, value in _coordinates(world, stage).items():
        assert getattr(page, field) == value


def test_regions_require_exact_manager_grant_not_ancestor_role(world):
    page = _call(world, "regions", actor="manager_x")
    assert [row.region_org_id for row in page.items] == [world.region_x.id]
    child = _organization(world.db, "CHILD-REGION", "子区域", "region_company", parent=world.region_x)
    page = _call(world, "regions", actor="manager_x")
    assert child.id not in {row.region_org_id for row in page.items}


def test_same_region_distinct_asset_and_physical_owners_and_regional_child_are_supported(world):
    child = _organization(world.db, "ASSET-CHILD", "资产子区域", "region_company", parent=world.region_x)
    owners = _call(world, "asset_owners")
    assert {row.owner_org_id for row in owners.items} == {world.region_x.id, child.id}
    locations = _call(world, "locations", owner_org_id=child.id)
    assert {row.location_id for row in locations.items} == {world.region_location.id, world.personal_location.id}
    assert all(row.physical_owner_org_id == world.region_x.id for row in locations.items)
    assert locations.owner_org_id == child.id


@pytest.mark.parametrize("stage", ("locations", "assignees"))
def test_even_national_admin_cannot_choose_asset_owner_from_another_region(world, stage):
    with pytest.raises(service.OpeningStartOptionError) as error:
        _call(world, stage, owner_org_id=world.region_y.id)
    assert error.value.http_status_code == 403


def test_personal_executor_is_exact_custodian_and_returns_real_user_id(world):
    page = _call(world, "assignees")
    assert [(row.person_id, row.assignee_user_id) for row in page.items] == [
        (world.technician.person.id, world.technician.user.id),
    ]
    assert page.items[0].assignee_user_id != str(page.items[0].person_id)
    assert set(page.items[0].model_dump()) == {"person_id", "assignee_user_id", "name"}


def test_regional_executors_follow_existing_manager_count_grants(world):
    page = _call(world, "assignees", location_id=world.region_location.id)
    assert {row.assignee_user_id for row in page.items} == {world.admin.user.id, world.manager_x.user.id}


def test_missing_verified_identity_does_not_guess_or_create_personal_executor(world):
    world.db.execute(update(AuthIdentity).where(AuthIdentity.user_id == world.technician.user.id).values(status="revoked", revoked_at=NOW))
    world.db.flush()
    assert _call(world, "assignees").items == ()


def test_no_existing_count_grant_means_empty_not_automatic_manager_setup(world):
    count_id = world.db.scalar(select(Permission.id).where(Permission.resource == "stocktake", Permission.action == "count"))
    world.db.execute(delete(RolePermission).where(RolePermission.permission_id == count_id))
    world.db.flush()
    assert _call(world, "assignees", location_id=world.region_location.id).items == ()
    assert not world.db.new


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("limit", (True, False, 0, 101, "1", 1.5))
def test_service_rejects_non_strict_or_out_of_range_limits(world, stage, limit):
    with pytest.raises(service.OpeningStartOptionError) as error:
        _call(world, stage, limit=limit)
    assert error.value.http_status_code == 422


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("cursor", (True, "not-a-uuid", uuid.UUID(int=0)))
def test_service_rejects_invalid_cursors(world, stage, cursor):
    cursor_field = "after_person_id" if stage == "assignees" else "after_id"
    with pytest.raises(service.OpeningStartOptionError) as error:
        _call(world, stage, **{cursor_field: cursor})
    assert error.value.http_status_code == 422


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("mutation", ("technician", "is_active", "account", "employment", "version", "revoked", "expired"))
def test_each_layer_rejects_inactive_or_stale_initiator(world, stage, mutation):
    actor = "admin"
    if mutation == "technician":
        actor = "technician"
    elif mutation == "is_active":
        world.admin.user.is_active = False
    elif mutation == "account":
        world.admin.user.account_status = "disabled"
    elif mutation == "employment":
        world.admin.person.employment_status = "inactive"
    elif mutation == "version":
        world.admin.user.authorization_version += 1
    elif mutation == "revoked":
        world.admin.assignment.status = "revoked"
        world.admin.assignment.revoked_at = NOW
        world.admin.assignment.revoked_by = world.admin.user.id
    elif mutation == "expired":
        world.admin.assignment.valid_to = NOW
    world.db.flush()
    with pytest.raises(service.OpeningStartOptionError) as error:
        _call(world, stage, actor=actor)
    assert error.value.http_status_code == 403


@pytest.mark.parametrize("stage", STAGES)
def test_global_deny_is_not_lost_by_selecting_a_second_allow_grant(world, stage):
    world.db.add(RoleAssignment(
        id=uuid.uuid4(), user_id=world.admin.user.id, role_id=world.roles["provincial_manager"].id,
        scope_type="organization", scope_id=str(world.region_x.id),
        valid_from=NOW - timedelta(days=1), status="active", assigned_by=world.admin.user.id,
        reason="isolated deny regression",
    ))
    manage_id = world.db.scalar(select(Permission.id).where(Permission.resource == "stocktake", Permission.action == "manage"))
    world.db.execute(update(RolePermission).where(
        RolePermission.role_id == world.roles["admin"].id,
        RolePermission.permission_id == manage_id,
    ).values(effect="deny"))
    world.db.flush()
    if stage == "regions":
        assert _call(world, stage).items == ()
    else:
        with pytest.raises(service.OpeningStartOptionError) as error:
            _call(world, stage)
        assert error.value.http_status_code == 403


@pytest.mark.parametrize("stage", STAGES)
def test_all_queries_are_select_only_no_autoflush_no_scope_uuid_or_row_lock(world, stage, monkeypatch):
    # Freeze argument coordinates before adding pending state: dereferencing
    # an expired fixture ORM object is caller-side I/O, not a service read.
    coordinates = _coordinates(world, stage)
    pending = Organization(id=uuid.uuid4(), code="UNFLUSHED", name="未提交", org_type="region_company", status="active")
    world.db.add(pending)
    queries, locks = [], []

    def sql(_connection, _cursor, statement, _params, _context, _many):
        queries.append(statement)
    def orm(state):
        locks.append(getattr(state.statement, "_for_update_arg", None))
    def no_uuid():
        pytest.fail("read-only directory generated a business coordinate")

    event.listen(world.db.bind, "before_cursor_execute", sql)
    event.listen(world.db, "do_orm_execute", orm)
    monkeypatch.setattr(opening.uuid, "uuid4", no_uuid)
    try:
        assert QUERIES[stage](world.db, actor=world.principals["admin"],
                              now=NOW, limit=50, **coordinates).items
    finally:
        event.remove(world.db.bind, "before_cursor_execute", sql)
        event.remove(world.db, "do_orm_execute", orm)
    assert queries and all(row.lstrip().upper().startswith("SELECT") for row in queries)
    assert all(lock is None for lock in locks)
    assert pending in world.db.new


@pytest.mark.parametrize("stage", STAGES)
def test_safe_cursor_uses_last_returned_option_and_pages_without_loss(world, stage):
    overrides = {"location_id": world.region_location.id} if stage == "assignees" else {}
    if stage == "asset_owners":
        _organization(world.db, "SECOND-ASSET", "第二资产区域", "region_company", parent=world.region_x)
    expected = _call(world, stage, **overrides).items
    assert len(expected) >= 2
    cursor_name = "after_person_id" if stage == "assignees" else "after_id"
    next_name = "next_after_person_id" if stage == "assignees" else "next_after_id"
    id_name = {"regions": "region_org_id", "asset_owners": "owner_org_id", "locations": "location_id", "assignees": "person_id"}[stage]
    actual, cursor = [], None
    for _ in range(10):
        page = _call(world, stage, limit=1, **overrides, **{cursor_name: cursor})
        actual.extend(page.items)
        cursor = getattr(page, next_name)
        if cursor is None:
            break
        assert cursor == getattr(page.items[-1], id_name)
    else:
        pytest.fail("directory pagination did not terminate")
    assert actual == list(expected)
    assert len({getattr(row, id_name) for row in actual}) == len(actual)


@pytest.mark.parametrize("stage", STAGES)
def test_bound_exceeded_fails_the_entire_page_not_a_partial_result(world, stage, monkeypatch):
    monkeypatch.setattr(service, "_MAX_RAW_OPTIONS_SCANNED", 1)
    with pytest.raises(service.OpeningStartOptionError) as error:
        _call(world, stage)
    assert error.value.http_status_code == 503
    assert error.value.code == "opening_start_options_scan_limit_exceeded"


@pytest.mark.parametrize("total", (1000, 1001))
def test_actual_thousand_candidate_boundary_is_checked_without_public_raw_cursor(world, total):
    # There are two active region companies in the reusable fixture. All new
    # rows are outside manager X's exact scope, forcing a complete raw scan.
    world.db.add_all([
        Organization(id=uuid.uuid4(), code=f"RAW-{index}", name=f"隔离区域 {index}",
                     org_type="region_company", parent_id=world.hq.id, status="active")
        for index in range(total - 2)
    ])
    world.db.flush()
    if total == 1000:
        page = _call(world, "regions", actor="manager_x")
        assert [row.region_org_id for row in page.items] == [world.region_x.id]
        assert page.next_after_id is None
    else:
        with pytest.raises(service.OpeningStartOptionError) as error:
            _call(world, "regions", actor="manager_x")
        assert error.value.code == "opening_start_options_scan_limit_exceeded"


def test_assignee_filter_crosses_a_hundred_raw_identities_without_leaking_their_ids(world):
    # Small deterministic UUIDs place these valid but ineligible personal
    # engineers before the existing manager/admin identities in the scan.
    people, users, identities, grants = [], [], [], []
    for index in range(1, 102):
        person_id, user_id = uuid.UUID(int=index), str(uuid.UUID(int=2000 + index))
        people.append(Person(
            id=person_id, organization_id=world.region_x.id,
            employee_no=f"SYNTHETIC-{index}", name=f"隔离工程师 {index}",
            employment_status="active", source_updated_at=NOW,
        ))
        users.append(User(
            id=user_id, person_id=person_id, account_status="active", authorization_version=1,
            mobile=f"fixture-mobile-{index}", name=f"隔离工程师 {index}",
            password_hash="formal-password-disabled", role="technician", is_active=True,
            require_password_change=False,
        ))
        identities.append(AuthIdentity(
            id=uuid.uuid4(), user_id=user_id, identity_type="mobile", provider_key="test",
            identifier_hash=f"{index:064x}", hash_version=1,
            verified_at=NOW - timedelta(days=1), status="active",
        ))
        grants.append(RoleAssignment(
            id=uuid.uuid4(), user_id=user_id, role_id=world.roles["technician"].id,
            scope_type="person", scope_id=str(person_id),
            valid_from=NOW - timedelta(days=1), status="active", assigned_by=world.admin.user.id,
            reason="isolated pagination fixture",
        ))
    world.db.add_all(people)
    world.db.flush()
    world.db.add_all(users)
    world.db.flush()
    world.db.add_all([*identities, *grants])
    world.db.flush()
    first = _call(world, "assignees", location_id=world.region_location.id, limit=1)
    assert len(first.items) == 1
    assert first.next_after_person_id == first.items[-1].person_id
    assert first.next_after_person_id.int > 101
    second = _call(world, "assignees", location_id=world.region_location.id, limit=1,
                   after_person_id=first.next_after_person_id)
    assert second.next_after_person_id is None
    assert {row.assignee_user_id for row in (*first.items, *second.items)} == {
        world.admin.user.id, world.manager_x.user.id,
    }


@pytest.mark.parametrize("mutation", ("child", "parent_owner", "parent_type", "parent_inactive", "cycle", "no_custody", "ambiguous_custody", "custody_mismatch"))
def test_invalid_personal_reference_graph_fails_closed_not_a_partial_location_page(world, mutation):
    if mutation == "child":
        world.db.add(StockLocation(
            id=uuid.uuid4(), code="UNEXPECTED-CHILD", name="停用子位置", status="inactive",
            location_type="region", owner_org_id=world.region_x.id, parent_id=world.personal_location.id,
        ))
    elif mutation == "parent_owner":
        world.region_location.owner_org_id = world.region_y.id
    elif mutation == "parent_type":
        world.region_location.location_type = "quarantine"
    elif mutation == "parent_inactive":
        world.region_location.status = "inactive"
    elif mutation == "cycle":
        world.region_location.parent_id = world.personal_location.id
    elif mutation == "no_custody":
        world.db.delete(world.custody)
    elif mutation == "ambiguous_custody":
        world.db.add(CustodyAssignment(
            id=uuid.uuid4(), location_id=world.personal_location.id,
            custodian_person_id=world.technician.person.id,
            valid_from=NOW - timedelta(hours=1),
            valid_to=NOW + timedelta(hours=1),
        ))
    elif mutation == "custody_mismatch":
        world.custody.custodian_person_id = world.manager_x.person.id
    world.db.flush()
    with pytest.raises(service.OpeningStartOptionError) as error:
        _call(world, "locations")
    assert error.value.http_status_code == 503


@pytest.mark.parametrize("stage", STAGES)
def test_database_errors_are_sanitized_and_never_return_partial_options(world, stage, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError("SECRET-SQL", {"private": "SECRET-PARAMETER"}, RuntimeError("SECRET-DRIVER"))
    monkeypatch.setattr(service, "_raw_batch", fail)
    with pytest.raises(service.OpeningStartOptionError) as error:
        _call(world, stage)
    assert error.value.http_status_code == 503
    assert "SECRET" not in str(error.value.as_detail())


def _client(world, actor="admin"):
    from app.main import block_legacy_prototype_writes
    api = FastAPI()
    api.middleware("http")(block_legacy_prototype_writes)
    api.include_router(router.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: world.db
    api.dependency_overrides[get_formal_principal] = lambda: world.principals[actor]
    return TestClient(api)


def _http_params(world, stage):
    return {key: str(value) for key, value in _coordinates(world, stage).items()}


@pytest.mark.parametrize("stage", STAGES)
def test_http_routes_preserve_real_permission_dependency_and_safe_contract(world, stage):
    path = f"{PREFIX}/{stage.replace('_', '-')}"
    response = _client(world).get(path, params=_http_params(world, stage))
    assert response.status_code == 200, response.text
    assert response.json()["start_ready"] is False
    assert response.json()["items"]
    denied = _client(world, actor="technician").get(path, params=_http_params(world, stage))
    assert denied.status_code == 403
    for result in (response, denied):
        assert result.headers["cache-control"] == "private, no-store, max-age=0"
        assert result.headers["pragma"] == "no-cache"
        assert result.headers["referrer-policy"] == "no-referrer"
        assert result.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("mutation", ("extra", "duplicate", "limit", "uuid", "post"))
def test_http_rejects_ambiguous_queries_and_caches_no_failure(world, stage, mutation):
    path = f"{PREFIX}/{stage.replace('_', '-')}"
    params = list(_http_params(world, stage).items())
    method = "get"
    if mutation == "extra":
        params.append(("control_sync_run_id", str(uuid.uuid4())))
    elif mutation == "duplicate":
        params.extend([("limit", "1"), ("limit", "2")])
    elif mutation == "limit":
        params.append(("limit", "true"))
    elif mutation == "uuid":
        params.append(("after_person_id" if stage == "assignees" else "after_id", "invalid"))
    elif mutation == "post":
        method = "post"
    response = getattr(_client(world), method)(path, params=params)
    assert response.status_code == (405 if method == "post" else 422)
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.parametrize("stage", ("asset_owners", "locations", "assignees"))
def test_http_missing_selection_cannot_infer_scope_from_other_coordinates(world, stage):
    params = _http_params(world, stage)
    del params["region_org_id"]
    response = _client(world).get(f"{PREFIX}/{stage.replace('_', '-')}", params=params)
    assert response.status_code == 422
    assert "no-store" in response.headers["cache-control"]


def test_unknown_directory_route_is_not_cached(world):
    response = _client(world).get(f"{PREFIX}/unknown")
    assert response.status_code == 404
    assert "no-store" in response.headers["cache-control"]
