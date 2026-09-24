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
from test_stocktake_task_service import NOW, world  # noqa: F401


@pytest.fixture
def db():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    engine = create_engine('sqlite+pysqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _): connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine) as session: yield session
    engine.dispose()


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


def test_unknown_source_time_is_explicit_null_through_database_http_and_mini(catalog_world, monkeypatch):
    import json
    import shutil
    import subprocess
    from pathlib import Path
    from app.inventory_models import FormalMaterial

    database = catalog_world.db
    catalog_world.material_a.source_updated_at = None
    database.commit()
    known = catalog_world.material_b.source_updated_at
    before = tuple(database.execute(select(FormalMaterial.id, FormalMaterial.source_updated_at)).all())
    principal = catalog_world.catalog_principals['technician']
    original = service.list_active_materials
    monkeypatch.setattr(service, 'list_active_materials', lambda db, **kw: original(db, now=NOW, **kw))
    api = FastAPI()
    api.include_router(formal_material_catalog.router, prefix='/api')
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: principal
    with TestClient(api) as client:
        response = client.get('/api/v1/materials?limit=100')
    assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
    body = response.json()
    assert body['schema_version'] == '2.0'
    rows = {item['material_id']: item for item in body['items']}
    assert rows[str(catalog_world.material_a.id)]['source_updated_at'] is None
    assert rows[str(catalog_world.material_b.id)]['source_updated_at'] == service._aware(known).isoformat().replace('+00:00', 'Z')
    assert tuple(database.execute(select(FormalMaterial.id, FormalMaterial.source_updated_at)).all()) == before
    node = shutil.which('node')
    if node is None:
        pytest.fail('Node is required to verify the actual HTTP-to-mini source-time contract')
    module = Path(__file__).parents[2] / 'miniprogram/utils/material-catalog-contract.js'
    result = subprocess.run([node, '-e', "const fs=require('node:fs');const c=require(process.argv[1]);process.stdout.write(JSON.stringify(c.validatePage(JSON.parse(fs.readFileSync(0,'utf8')))))", str(module)],
        input=json.dumps(body), text=True, capture_output=True, timeout=20, check=True)
    assert json.loads(result.stdout) == body


def test_material_output_requires_explicit_unknown_and_rejects_invalid_time(catalog_world):
    from pydantic import ValidationError
    from app.material_catalog_schemas import MaterialCatalogItemOut

    value = service.list_active_materials(catalog_world.db, actor=catalog_world.catalog_principals['admin'], limit=1, now=NOW).items[0].model_dump()
    for bad in ('', 'unknown', '2026-09-01T00:00:00', False):
        with pytest.raises(ValidationError): MaterialCatalogItemOut.model_validate(dict(value, source_updated_at=bad))
    value.pop('source_updated_at')
    with pytest.raises(ValidationError): MaterialCatalogItemOut.model_validate(value)


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
        "schema_version": "2.0",
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
