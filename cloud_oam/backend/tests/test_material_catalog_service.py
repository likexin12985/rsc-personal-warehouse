from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.formal_services import material_catalog as service
from app.foundation_models import Permission, Role, RolePermission
from app.inventory_models import MaterialInventoryPolicy
from app.material_catalog_schemas import MaterialCatalogPageOut
from app.routers import formal_material_catalog
from test_stocktake_task_service import NOW, db, world  # noqa: F401


@pytest.fixture()
def catalog_world(world):
    permission = Permission(
        id=uuid.uuid4(),
        resource="inventory",
        action="read",
        field_code="",
        description="read formal inventory catalog",
    )
    world.db.add(permission)
    world.db.flush()
    roles = tuple(world.db.scalars(select(Role)).all())
    world.db.add_all(
        [
            RolePermission(
                id=uuid.uuid4(),
                role_id=role.id,
                permission_id=permission.id,
                effect="allow",
            )
            for role in roles
        ]
    )
    world.db.commit()
    world.catalog_principals = {
        name: load_formal_principal(world.db, principal.user_id, now=NOW)
        for name, principal in world.principals.items()
    }
    return world


def test_active_catalog_is_paginated_searchable_and_policy_complete(catalog_world):
    page_1 = service.list_active_materials(
        catalog_world.db,
        actor=catalog_world.catalog_principals["technician"],
        limit=1,
        now=NOW,
    )
    assert isinstance(page_1, MaterialCatalogPageOut)
    assert len(page_1.items) == 1
    assert page_1.next_after_id is not None
    first = page_1.items[0]
    assert first.sku_code in {"SKU-A", "SKU-B"}
    assert first.base_unit == "件"

    page_2 = service.list_active_materials(
        catalog_world.db,
        actor=catalog_world.catalog_principals["technician"],
        limit=1,
        after_id=page_1.next_after_id,
        now=NOW,
    )
    assert len(page_2.items) == 1
    assert page_2.items[0].material_id != first.material_id
    assert page_2.next_after_id is None

    searched = service.list_active_materials(
        catalog_world.db,
        actor=catalog_world.catalog_principals["technician"],
        limit=20,
        query="SKU-B",
        now=NOW,
    )
    assert [row.sku_code for row in searched.items] == ["SKU-B"]
    assert searched.items[0].tracking_mode == "serial"
    assert searched.items[0].allow_fraction is False


def test_missing_current_policy_fails_closed_instead_of_showing_unusable_material(
    catalog_world,
):
    policy = catalog_world.db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == catalog_world.material_a.id
        )
    )
    assert policy is not None
    policy.effective_from = NOW + timedelta(days=1)
    catalog_world.db.flush()

    with pytest.raises(service.MaterialCatalogError) as exc_info:
        service.list_active_materials(
            catalog_world.db,
            actor=catalog_world.catalog_principals["technician"],
            limit=20,
            now=NOW,
        )

    assert exc_info.value.code == "material_catalog_policy_missing"
    assert exc_info.value.http_status_code == 503


def test_stale_or_unscoped_principal_is_denied(catalog_world):
    principal = catalog_world.catalog_principals["technician"]
    catalog_world.technician.user.authorization_version += 1
    catalog_world.db.flush()

    with pytest.raises(service.MaterialCatalogError) as stale:
        service.list_active_materials(
            catalog_world.db,
            actor=principal,
            limit=20,
            now=NOW,
        )
    assert stale.value.code == "material_catalog_actor_stale"

    # A currently loaded principal without the inventory permission also fails
    # in the service, even if an HTTP adapter were accidentally misconfigured.
    manager = catalog_world.catalog_principals["manager_x"]
    for row in catalog_world.db.scalars(
        select(RolePermission).join(
            Role, Role.id == RolePermission.role_id
        ).where(Role.code == "provincial_manager")
    ).all():
        catalog_world.db.delete(row)
    catalog_world.db.flush()
    unscoped = load_formal_principal(
        catalog_world.db,
        catalog_world.manager_x.user.id,
        now=NOW,
    )
    with pytest.raises(service.MaterialCatalogError) as forbidden:
        service.list_active_materials(
            catalog_world.db,
            actor=unscoped,
            limit=20,
            now=NOW,
        )
    assert forbidden.value.code == "material_catalog_forbidden"


def test_catalog_query_contract_rejects_ambiguous_text(catalog_world):
    principal = catalog_world.catalog_principals["admin"]
    for bad in ("", " SKU-A", "SKU-A ", "x\n"):
        with pytest.raises(service.MaterialCatalogError) as exc_info:
            service.list_active_materials(
                catalog_world.db,
                actor=principal,
                limit=20,
                query=bad,
                now=NOW,
            )
        assert exc_info.value.code == "material_catalog_query_invalid"


def test_formal_catalog_api_is_read_only_and_no_store(monkeypatch):
    database = Mock()
    principal = Mock()
    output = MaterialCatalogPageOut(items=(), next_after_id=None)
    call = Mock(return_value=output)
    monkeypatch.setattr(service, "list_active_materials", call)
    api = FastAPI()
    api.include_router(formal_material_catalog.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: principal
    principal.allows.return_value = True

    with TestClient(api) as client:
        response = client.get("/api/v1/materials?limit=10&query=SKU")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "items": [],
        "next_after_id": None,
    }
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    call.assert_called_once_with(
        database,
        actor=principal,
        limit=10,
        after_id=None,
        query="SKU",
    )
    database.commit.assert_not_called()
    database.rollback.assert_not_called()
