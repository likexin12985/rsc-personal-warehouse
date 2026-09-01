from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import stocktake_options as service
from app.inventory_models import CustodyAssignment
from app.routers import formal_stocktake_options
from app.stocktake_option_schemas import StocktakeRegionOptionPageOut
from test_stocktake_task_service import NOW, db, world  # noqa: F401


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
