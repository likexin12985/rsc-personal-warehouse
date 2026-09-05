from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.formal_services import stocktake_options as service
from app.foundation_models import AuthIdentity, Organization, Permission, Person, Role, RoleAssignment, RolePermission
from app.inventory_models import CustodyAssignment, StockLocation
from app.models import User
from app.routers import formal_stocktake_options
from app.stocktake_option_schemas import StocktakeRegionOptionPageOut
from test_stocktake_task_service import NOW, world  # noqa: F401


@pytest.fixture
def db():
    # TestClient runs synchronous dependencies in a worker thread; share only
    # this isolated in-memory database, never the application database.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_region_options_are_limited_to_exact_manager_scope(world):
    admin = service.list_region_options(
        world.db,
        actor=world.principals["admin"],
        limit=20,
        now=NOW,
    )
    assert {row.region_org_id for row in admin.items} == {
        world.region_x.id,
        world.region_y.id,
    }

    manager = service.list_region_options(
        world.db,
        actor=world.principals["manager_x"],
        limit=20,
        now=NOW,
    )
    assert [row.region_org_id for row in manager.items] == [world.region_x.id]
    assert manager.authorization_version == world.manager_x.user.authorization_version

    with pytest.raises(service.StocktakeOptionError) as denied:
        service.list_region_options(
            world.db,
            actor=world.principals["technician"],
            limit=20,
            now=NOW,
        )
    assert denied.value.code == "stocktake_options_manager_required"
    assert denied.value.http_status_code == 403


def test_location_options_are_scoped_and_custody_labelled(world):
    output = service.list_location_options(
        world.db,
        actor=world.principals["manager_x"],
        region_org_id=world.region_x.id,
        limit=20,
        now=NOW,
    )

    assert {row.location_id for row in output.items} == {
        world.region_location.id,
        world.personal_location.id,
    }
    by_id = {row.location_id: row for row in output.items}
    assert by_id[world.region_location.id].custodian_person_id == world.manager_x.person.id
    assert by_id[world.personal_location.id].custodian_person_id == world.technician.person.id
    assert by_id[world.personal_location.id].owner_org_id == world.region_x.id

    with pytest.raises(service.StocktakeOptionError) as denied:
        service.list_location_options(
            world.db,
            actor=world.principals["manager_y"],
            region_org_id=world.region_x.id,
            limit=20,
            now=NOW,
        )
    assert denied.value.code == "stocktake_region_forbidden"


def test_assignee_options_match_the_command_services_exact_count_scope(world):
    region = service.list_assignee_options(
        world.db,
        actor=world.principals["admin"],
        region_org_id=world.region_x.id,
        location_id=world.region_location.id,
        limit=20,
        now=NOW,
    )
    assert {row.person_id for row in region.items} == {
        world.manager_x.person.id,
        world.principals["admin"].person_id,
    }

    personal = service.list_assignee_options(
        world.db,
        actor=world.principals["manager_x"],
        region_org_id=world.region_x.id,
        location_id=world.personal_location.id,
        limit=20,
        now=NOW,
    )
    assert {row.person_id for row in personal.items} == {
        world.manager_x.person.id,
        world.principals["admin"].person_id,
        world.technician.person.id,
    }
    technician = next(
        row for row in personal.items if row.person_id == world.technician.person.id
    )
    assert technician.assignee_user_id == world.technician.user.id
    assert technician.role_codes == ("technician",)


def test_ambiguous_current_custody_fails_closed(world):
    world.db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=world.personal_location.id,
            custodian_person_id=world.technician.person.id,
            valid_from=NOW - timedelta(days=1),
            valid_to=NOW + timedelta(days=1),
            handover_case_id=None,
        )
    )
    world.db.flush()

    with pytest.raises(service.StocktakeOptionError) as captured:
        service.list_location_options(
            world.db,
            actor=world.principals["admin"],
            region_org_id=world.region_x.id,
            limit=20,
            now=NOW,
        )
    assert captured.value.code == "stocktake_location_custody_ambiguous"
    assert captured.value.http_status_code == 503


def test_stale_manager_context_is_rejected(world):
    principal = world.principals["manager_x"]
    world.manager_x.user.authorization_version += 1
    world.db.flush()

    with pytest.raises(service.StocktakeOptionError) as captured:
        service.list_region_options(
            world.db,
            actor=principal,
            limit=20,
            now=NOW,
        )
    assert captured.value.code == "stocktake_options_actor_stale"


def test_stocktake_option_api_is_read_only_and_no_store(monkeypatch):
    database = Mock()
    principal = Mock()
    principal.allows.return_value = True
    output = StocktakeRegionOptionPageOut(
        items=(),
        next_after_id=None,
        authorization_version=7,
    )
    call = Mock(return_value=output)
    monkeypatch.setattr(service, "list_region_options", call)
    api = FastAPI()
    api.include_router(formal_stocktake_options.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: principal

    with TestClient(api) as client:
        response = client.get("/api/v1/stocktake-options/regions?limit=10")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "items": [],
        "next_after_id": None,
        "authorization_version": 7,
    }
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    call.assert_called_once_with(
        database,
        actor=principal,
        limit=10,
        after_id=None,
    )
    database.commit.assert_not_called()
    database.rollback.assert_not_called()


def _dual_manager(world, *, manager_region=None):
    role = world.db.scalar(select(Role).where(Role.code == "provincial_manager"))
    assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=world.admin.user.id,
        role_id=role.id,
        scope_type="organization",
        scope_id=str((manager_region or world.region_x).id),
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        status="active",
        assigned_by=world.admin.user.id,
        revoked_at=None,
        revoked_by=None,
        reason="exercise independent effective grants",
    )
    world.db.add(assignment)
    world.db.commit()
    return assignment


def _role_permission(world, role_code, action="manage"):
    return world.db.scalar(
        select(RolePermission)
        .join(Role, Role.id == RolePermission.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            Role.code == role_code,
            Permission.resource == "stocktake",
            Permission.action == action,
            Permission.field_code == "",
        )
    )


def _options_call(world, kind, actor):
    kwargs = dict(actor=actor, limit=100, now=NOW)
    if kind != "regions":
        kwargs["region_org_id"] = world.region_x.id
    if kind == "assignees":
        kwargs["location_id"] = world.region_location.id
    return getattr(service, f"list_{kind[:-1] if kind != 'assignees' else 'assignee'}_options")(
        world.db, **kwargs
    )


def _option_api(world, monkeypatch, kind, actor, *, extra_params=None):
    name = f"list_{kind[:-1] if kind != 'assignees' else 'assignee'}_options"
    implementation = getattr(service, name)
    called = Mock(side_effect=lambda *args, **kwargs: implementation(*args, **kwargs, now=NOW))
    monkeypatch.setattr(service, name, called)
    api = FastAPI()
    api.include_router(formal_stocktake_options.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: world.db
    # Keep the actual require_permission dependency. This override represents
    # the immutable principal snapshot loaded earlier in the same request.
    api.dependency_overrides[get_formal_principal] = lambda: actor
    params = {"limit": 100}
    if kind != "regions":
        params["region_org_id"] = str(world.region_x.id)
    if kind == "assignees":
        params["location_id"] = str(world.region_location.id)
    params.update(extra_params or {})
    with TestClient(api) as client:
        response = client.get(f"/api/v1/stocktake-options/{kind}", params=params)
    return response, called


@pytest.mark.parametrize("kind", ("regions", "locations", "assignees"))
def test_service_preserves_global_deny_from_another_effective_grant(world, kind):
    _dual_manager(world)
    _role_permission(world, "admin").effect = "deny"
    world.db.commit()
    actor = load_formal_principal(world.db, world.admin.user.id, now=NOW)
    assert not actor.allows(
        world.db, "stocktake", "manage",
        target_scope_type="organization", target_scope_id=str(world.region_x.id),
    )
    if kind == "regions":
        assert _options_call(world, kind, actor).items == ()
    else:
        with pytest.raises(service.StocktakeOptionError) as captured:
            _options_call(world, kind, actor)
        assert captured.value.code == "stocktake_region_forbidden"


@pytest.mark.parametrize("kind", ("regions", "locations", "assignees"))
def test_api_existing_global_deny_is_already_blocked_by_top_level_permission(
    world, monkeypatch, kind,
):
    _dual_manager(world)
    _role_permission(world, "admin").effect = "deny"
    world.db.commit()
    actor = load_formal_principal(world.db, world.admin.user.id, now=NOW)
    response, called = _option_api(world, monkeypatch, kind, actor)
    assert response.status_code == 403
    assert response.json() == {"detail": "没有此操作权限"}
    called.assert_not_called()


@pytest.mark.parametrize("kind", ("regions", "locations", "assignees"))
def test_api_reloaded_global_deny_after_dependency_snapshot_never_leaks_options(
    world, monkeypatch, kind,
):
    _dual_manager(world)
    actor = load_formal_principal(world.db, world.admin.user.id, now=NOW)
    assert actor.allows(world.db, "stocktake", "manage")
    # Catalog permission changes do not require an account-version mutation.
    # The service must preserve the deny from its own current principal load.
    _role_permission(world, "admin").effect = "deny"
    world.db.commit()
    assert world.admin.user.authorization_version == actor.authorization_version
    current = load_formal_principal(world.db, world.admin.user.id, now=NOW)
    assert not current.allows(world.db, "stocktake", "manage")
    response, called = _option_api(world, monkeypatch, kind, actor)
    called.assert_called_once()
    if kind == "regions":
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["next_after_id"] is None
    else:
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "stocktake_region_forbidden"
    assert not world.db.new and not world.db.dirty and not world.db.deleted


_CROSS_GRANT_CASES = (
    ("both_allow", True, True),
    ("only_exact_manager_allow", True, True),
    ("only_ancestor_manager_allow", False, True),
    ("regional_deny_matches", False, False),
    ("regional_deny_unrelated", True, False),
    ("different_action_deny", True, True),
    ("different_field_deny", True, True),
    ("expired_deny_assignment", True, True),
    ("revoked_deny_assignment", True, True),
)


def _cross_grant_actor(world, case):
    assignment = _dual_manager(
        world,
        manager_region=(
            world.region_y
            if case in {"only_ancestor_manager_allow", "regional_deny_unrelated"}
            else world.region_x
        ),
    )
    if case.startswith("only_"):
        world.db.delete(_role_permission(world, "admin"))
    if case == "only_ancestor_manager_allow":
        # The principal can manage this descendant through the ancestor grant,
        # but no allowed national or exact-region manager grant was selected.
        world.region_x.parent_id = world.region_y.id
    if case in {
        "regional_deny_matches", "regional_deny_unrelated",
        "expired_deny_assignment", "revoked_deny_assignment",
    }:
        _role_permission(world, "provincial_manager").effect = "deny"
    if case == "different_action_deny":
        _role_permission(world, "admin", "count").effect = "deny"
    if case == "different_field_deny":
        permission = Permission(
            id=uuid.uuid4(), resource="stocktake", action="manage",
            field_code="unrelated_field", description="unrelated field deny",
        )
        world.db.add(permission)
        world.db.flush()
        world.db.add(RolePermission(
            role_id=_role_permission(world, "admin").role_id,
            permission_id=permission.id, effect="deny",
        ))
    if case == "expired_deny_assignment":
        assignment.valid_to = NOW
    if case == "revoked_deny_assignment":
        revoked_by = world.admin.user.id
        assignment.status = "revoked"
        assignment.revoked_at = NOW - timedelta(seconds=1)
        assignment.revoked_by = revoked_by
    world.db.commit()
    return load_formal_principal(world.db, world.admin.user.id, now=NOW)


@pytest.mark.parametrize("case, allowed, coarse_allowed", _CROSS_GRANT_CASES)
@pytest.mark.parametrize("kind", ("regions", "locations", "assignees"))
def test_service_cross_grant_scope_and_deny_matrix(world, kind, case, allowed, coarse_allowed):
    actor = _cross_grant_actor(world, case)
    assert actor.allows(world.db, "stocktake", "manage") is coarse_allowed
    target_allowed = actor.allows(
        world.db, "stocktake", "manage",
        target_scope_type="organization", target_scope_id=str(world.region_x.id),
    )
    assert target_allowed is (allowed or case == "only_ancestor_manager_allow")
    if kind == "regions":
        result = _options_call(world, kind, actor)
        assert (world.region_x.id in {row.region_org_id for row in result.items}) is allowed
    elif allowed:
        assert _options_call(world, kind, actor).items
    else:
        with pytest.raises(service.StocktakeOptionError) as captured:
            _options_call(world, kind, actor)
        assert captured.value.code == "stocktake_region_forbidden"


@pytest.mark.parametrize("case, allowed, coarse_allowed", _CROSS_GRANT_CASES)
@pytest.mark.parametrize("kind", ("regions", "locations", "assignees"))
def test_api_cross_grant_scope_and_deny_matrix(
    world, monkeypatch, kind, case, allowed, coarse_allowed,
):
    actor = _cross_grant_actor(world, case)
    response, called = _option_api(world, monkeypatch, kind, actor)
    if not coarse_allowed:
        # The existing top-level permission check is deliberately conservative:
        # even an unrelated region deny blocks this unscoped menu permission.
        assert response.status_code == 403
        called.assert_not_called()
    elif kind == "regions":
        assert response.status_code == 200
        ids = {item["region_org_id"] for item in response.json()["items"]}
        assert (str(world.region_x.id) in ids) is allowed
        called.assert_called_once()
    elif allowed:
        assert response.status_code == 200
        assert response.json()["items"]
        called.assert_called_once()
    else:
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "stocktake_region_forbidden"
        called.assert_called_once()


def _ordered_directory_people(world, eligible):
    """Low positive IDs precede every version-4 fixture UUID deterministically."""
    people = [
        Person(
            id=uuid.UUID(int=index + 1), organization_id=world.region_x.id,
            employee_no=f"DIRECTORY-{index}", name=f"Directory {index}",
            employment_status="active", source_updated_at=NOW,
        )
        for index in range(len(eligible))
    ]
    world.db.add_all(people)
    world.db.flush()
    users = [
        User(
            id=f"d{index:035d}", person_id=person.id, account_status="active",
            authorization_version=1, mobile=f"199{index:08d}", name=person.name,
            password_hash="formal-password-disabled", role="provincial_manager",
            is_active=True, require_password_change=False,
        )
        for index, person in enumerate(people)
    ]
    world.db.add_all(users)
    world.db.flush()
    role = world.db.scalar(select(Role).where(Role.code == "provincial_manager"))
    for index, (user, person) in enumerate(zip(users, people, strict=True)):
        world.db.add_all([
            AuthIdentity(
                id=uuid.uuid4(), user_id=user.id, identity_type="mobile",
                provider_key="test", identifier_hash=f"{index + 1:064x}",
                hash_version=1, verified_at=NOW - timedelta(days=1),
                status="active", revoked_at=None,
            ),
            RoleAssignment(
                id=uuid.uuid4(), user_id=user.id, role_id=role.id,
                scope_type="organization",
                scope_id=str(world.region_x.id if eligible[index] else world.region_y.id),
                valid_from=NOW - timedelta(days=1), valid_to=None, status="active",
                assigned_by=world.admin.user.id, revoked_at=None, revoked_by=None,
                reason="controlled directory fixture",
            ),
        ])
    world.db.commit()
    return users, people


def _assignee_page(world, *, limit, after=None):
    return service.list_assignee_options(
        world.db, actor=world.principals["manager_x"],
        region_org_id=world.region_x.id, location_id=world.region_location.id,
        limit=limit, after_person_id=after, now=NOW,
    )


def test_assignee_cursor_is_last_returned_authorized_person_not_raw_lookahead(world):
    _users, people = _ordered_directory_people(world, [True, False, True])
    page = _assignee_page(world, limit=1)
    assert [item.person_id for item in page.items] == [people[0].id]
    assert page.next_after_person_id == people[0].id
    assert str(people[1].id) not in page.model_dump_json()
    assert str(people[2].id) not in page.model_dump_json()


def test_assignee_scan_fills_authorized_page_past_filtered_prefix(world):
    _users, people = _ordered_directory_people(world, [False, False, True, True, True])
    page = _assignee_page(world, limit=2)
    assert [item.person_id for item in page.items] == [people[2].id, people[3].id]
    assert page.next_after_person_id == people[3].id


@pytest.mark.parametrize("limit", (1, 2, 3, 100))
def test_assignee_authorized_pagination_has_no_duplicates_or_omissions(world, limit):
    _ordered_directory_people(world, [True, False, True, False, True])
    expected = [item.person_id for item in _assignee_page(world, limit=100).items]
    observed = []
    after = None
    for _ in range(len(expected) + 1):
        page = _assignee_page(world, limit=limit, after=after)
        observed.extend(item.person_id for item in page.items)
        if page.next_after_person_id is None:
            break
        assert len(page.items) == limit
        assert page.next_after_person_id == page.items[-1].person_id
        assert after is None or page.next_after_person_id > after
        after = page.next_after_person_id
    else:
        pytest.fail("authorized pagination did not terminate")
    assert observed == expected
    assert len(observed) == len(set(observed))


def test_assignee_scan_crosses_internal_batch_boundary(world):
    _users, people = _ordered_directory_people(world, [False] * 105 + [True] * 3)
    page = _assignee_page(world, limit=2)
    assert [row.person_id for row in page.items] == [people[105].id, people[106].id]
    assert page.next_after_person_id == people[106].id


def test_assignee_scan_exact_boundary_returns_complete_terminal_page(world, monkeypatch):
    monkeypatch.setattr(service, "_MAX_RAW_IDENTITIES_SCANNED", 4)
    page = _assignee_page(world, limit=100)
    assert len(page.items) == 2
    assert page.next_after_person_id is None


def test_assignee_scan_limit_is_1000_and_never_returns_partial_page(world):
    assert service._MAX_RAW_IDENTITIES_SCANNED == 1000
    _ordered_directory_people(world, [True] + [False] * 1000)
    with pytest.raises(service.StocktakeOptionError) as captured:
        _assignee_page(world, limit=1)
    assert captured.value.code == "stocktake_assignee_scan_limit_exceeded"
    assert captured.value.http_status_code == 503


def test_assignee_scan_limit_api_is_sanitized_without_items(world, monkeypatch):
    _ordered_directory_people(world, [True, False, False, False])
    monkeypatch.setattr(service, "_MAX_RAW_IDENTITIES_SCANNED", 3)
    response, called = _option_api(
        world, monkeypatch, "assignees", world.principals["manager_x"],
        extra_params={"limit": 1},
    )
    called.assert_called_once()
    assert response.status_code == 503
    assert response.json() == {"detail": {
        "code": "stocktake_assignee_scan_limit_exceeded",
        "category": "service_unavailable",
        "message": "盘点候选人员目录超过受控扫描上限",
    }}


def test_assignee_authorized_cursor_api_compatibility(world, monkeypatch):
    _users, people = _ordered_directory_people(world, [True, False, True])
    response, _called = _option_api(
        world, monkeypatch, "assignees", world.principals["manager_x"],
        extra_params={"limit": 1},
    )
    assert response.status_code == 200
    assert response.json()["next_after_person_id"] == response.json()["items"][-1]["person_id"]
    assert response.json()["next_after_person_id"] == str(people[0].id)
    assert str(people[1].id) not in response.text
    assert str(people[2].id) not in response.text
    assert response.headers["cache-control"] == "private, no-store, max-age=0"


@pytest.mark.parametrize("mode", (
    "authorization_version", "account_status", "person_name", "person_organization",
    "count_permission", "verified_identity", "lookahead_name", "person_binding",
))
def test_assignee_page_revalidates_returned_and_lookahead_identity(world, monkeypatch, mode):
    users, people = _ordered_directory_people(world, [True, True])
    target_user = users[1].id if mode == "lookahead_name" else users[0].id
    target_person = people[1].id if mode == "lookahead_name" else people[0].id
    region_y_id = world.region_y.id
    replacement_person_id = uuid.uuid4()
    if mode == "person_binding":
        world.db.add(Person(
            id=replacement_person_id, organization_id=world.region_x.id,
            employee_no="REPLACEMENT", name="Replacement identity",
            employment_status="active", source_updated_at=NOW,
        ))
        world.db.commit()
    count_permission = _role_permission(world, "provincial_manager", "count")
    count_pk = (count_permission.role_id, count_permission.permission_id)
    original = service._revalidate_assignee_snapshots

    def changed(db, **kwargs):
        if mode == "authorization_version":
            db.execute(update(User).where(User.id == target_user).values(authorization_version=2))
        elif mode == "account_status":
            db.execute(update(User).where(User.id == target_user).values(account_status="disabled"))
        elif mode in {"person_name", "lookahead_name"}:
            db.execute(update(Person).where(Person.id == target_person).values(name="Changed identity"))
        elif mode == "person_organization":
            db.execute(update(Person).where(Person.id == target_person).values(organization_id=region_y_id))
        elif mode == "person_binding":
            db.execute(update(User).where(User.id == target_user).values(person_id=replacement_person_id))
        elif mode == "count_permission":
            db.execute(update(RolePermission).where(
                RolePermission.role_id == count_pk[0], RolePermission.permission_id == count_pk[1],
            ).values(effect="deny"))
        elif mode == "verified_identity":
            db.execute(update(AuthIdentity).where(AuthIdentity.user_id == target_user).values(
                status="revoked", revoked_at=NOW,
            ))
        return original(db, **kwargs)

    monkeypatch.setattr(service, "_revalidate_assignee_snapshots", changed)
    with pytest.raises(service.StocktakeOptionError) as captured:
        _assignee_page(world, limit=1)
    assert captured.value.code == "stocktake_assignee_read_conflict"
    assert captured.value.http_status_code == 503


@pytest.mark.parametrize("mode", ("authorization_version", "global_deny", "employment_status"))
@pytest.mark.parametrize("phase", ("scan", "candidate_revalidation"))
def test_assignee_page_rechecks_actor_after_scan(world, monkeypatch, mode, phase):
    actor = world.manager_x.user.id
    person = world.manager_x.person.id
    permission = _role_permission(world, "provincial_manager")
    pk = permission.role_id, permission.permission_id
    method = "_scan_assignee_snapshots" if phase == "scan" else "_revalidate_assignee_snapshots"
    original = getattr(service, method)

    def changed(db, **kwargs):
        result = original(db, **kwargs)
        if mode == "authorization_version":
            db.execute(update(User).where(User.id == actor).values(authorization_version=2))
        elif mode == "employment_status":
            db.execute(update(Person).where(Person.id == person).values(employment_status="inactive"))
        else:
            db.execute(update(RolePermission).where(
                RolePermission.role_id == pk[0], RolePermission.permission_id == pk[1],
            ).values(effect="deny"))
        return result

    monkeypatch.setattr(service, method, changed)
    with pytest.raises(service.StocktakeOptionError) as captured:
        _assignee_page(world, limit=1)
    assert captured.value.http_status_code == 403


def test_assignee_page_read_has_no_orm_or_transaction_writes(world, monkeypatch):
    kwargs = dict(
        actor=world.principals["manager_x"], region_org_id=world.region_x.id,
        location_id=world.region_location.id, limit=1, now=NOW,
    )
    for method in ("flush", "commit", "rollback", "add", "delete"):
        monkeypatch.setattr(world.db, method, Mock(side_effect=AssertionError("read wrote")))
    page = service.list_assignee_options(world.db, **kwargs)
    assert len(page.items) == 1
    assert page.next_after_person_id == page.items[-1].person_id
    for method in ("flush", "commit", "rollback", "add", "delete"):
        getattr(world.db, method).assert_not_called()


def test_assignee_page_rechecks_location_binding_after_scan(world, monkeypatch):
    location_id = world.region_location.id
    original = service._scan_assignee_snapshots

    def changed(db, **kwargs):
        result = original(db, **kwargs)
        db.execute(update(StockLocation).where(StockLocation.id == location_id).values(
            custodian_person_id=None,
        ))
        return result

    monkeypatch.setattr(service, "_scan_assignee_snapshots", changed)
    with pytest.raises(service.StocktakeOptionError) as captured:
        _assignee_page(world, limit=1)
    assert captured.value.code == "stocktake_assignee_read_conflict"


@pytest.mark.parametrize("mode", ("owner_inactive", "owner_moved", "parent_inactive"))
def test_assignee_target_fresh_reads_do_not_reuse_cached_graph(world, monkeypatch, mode):
    owner = Organization(
        id=uuid.uuid4(), code="DIRECTORY-CHILD", name="Child region",
        parent_id=world.region_x.id, org_type="region_company", status="active",
    )
    world.db.add(owner)
    world.db.flush()
    parent = StockLocation(
        id=uuid.uuid4(), code="DIRECTORY-PARENT", name="Parent location",
        location_type="region", owner_org_id=owner.id, parent_id=None,
        custodian_person_id=None, status="active",
    )
    world.db.add(parent)
    world.db.flush()
    world.region_location.owner_org_id = owner.id
    world.region_location.parent_id = parent.id
    world.db.commit()
    owner_id, parent_id, outside_id = owner.id, parent.id, world.region_y.id
    original = service._scan_assignee_snapshots

    def changed(db, **kwargs):
        result = original(db, **kwargs)
        if mode == "parent_inactive":
            db.execute(update(StockLocation).where(StockLocation.id == parent_id).values(
                status="inactive",
            ).execution_options(synchronize_session=False))
            assert parent.status == "active"
        else:
            values = {"status": "inactive"} if mode == "owner_inactive" else {"parent_id": outside_id}
            db.execute(update(Organization).where(Organization.id == owner_id).values(
                **values,
            ).execution_options(synchronize_session=False))
            assert owner.status == "active" and owner.parent_id != outside_id
        return result

    monkeypatch.setattr(service, "_scan_assignee_snapshots", changed)
    with pytest.raises(service.StocktakeOptionError) as captured:
        _assignee_page(world, limit=1)
    assert captured.value.code == (
        "stocktake_location_tree_invalid" if mode == "parent_inactive" else "stocktake_location_forbidden"
    )
