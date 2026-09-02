from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import main as main_module
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.formal_services import material_request_options as service
from app.foundation_models import (
    ExternalObject,
    ExternalObjectVersion,
    Permission,
    RolePermission,
)
from app.material_request_option_schemas import (
    MaterialRequestWorkOrderOptionDetailOut,
    MaterialRequestWorkOrderOptionOut,
    MaterialRequestWorkOrderOptionPageOut,
)
from app.routers import formal_material_request_options
from test_material_request_draft_service import (  # noqa: F401
    NOW,
    _oam_work_order,
    _organization,
    _person,
    db,
    make_world,
)


WORK_ORDER_1 = uuid.UUID("b1000000-0000-4000-8000-000000000001")
WORK_ORDER_2 = uuid.UUID("b1000000-0000-4000-8000-000000000002")
WORK_ORDER_3 = uuid.UUID("b1000000-0000-4000-8000-000000000003")


def test_work_order_options_are_self_scoped_filtered_and_keyset_paginated(db):
    world = make_world(db)
    first = _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_1,
        work_order_no="WO-RSC-ALPHA-001",
        status="pending",
        observed_at=NOW,
    )
    second = _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_2,
        work_order_no="WO-RSC-BETA-002",
        status="active",
        observed_at=NOW,
    )
    _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_3,
        work_order_no="WO-RSC-CLOSED-003",
        status="closed",
        observed_at=NOW,
    )
    other_person = _person(db, world.department, "同区域其他工程师")
    _oam_work_order(
        db,
        world,
        work_order_no="WO-RSC-OTHER-004",
        engineer=other_person,
        observed_at=NOW,
    )
    other_region = _organization(
        db,
        "OTHER-REGION",
        "region_company",
        parent=world.headquarters,
    )
    other_department = _organization(
        db,
        "OTHER-DEPARTMENT",
        "department",
        parent=other_region,
    )
    _oam_work_order(
        db,
        world,
        work_order_no="WO-RSC-CROSS-005",
        organization=other_department,
        observed_at=NOW,
    )

    page_one = service.list_work_order_options(
        db,
        actor=world.actor,
        limit=1,
        now=NOW,
    )
    assert [item.work_order_id for item in page_one.items] == [first.id]
    assert page_one.next_after_id == second.id
    assert page_one.person_id == world.actor_person.id
    assert page_one.authorization_version == world.actor.authorization_version
    assert page_one.items[0].source_system_code == "starcharge_oam"
    assert page_one.items[0].source_external_id.startswith("WO-")
    assert page_one.items[0].source_version == f"v-{first.id.hex}"
    assert page_one.items[0].synced_at == NOW
    assert page_one.items[0].freshness_status == "fresh"

    page_two = service.list_work_order_options(
        db,
        actor=world.actor,
        limit=1,
        after_id=page_one.next_after_id,
        now=NOW,
    )
    assert [item.work_order_id for item in page_two.items] == [second.id]
    assert page_two.next_after_id is None

    queried = service.list_work_order_options(
        db,
        actor=world.actor,
        limit=20,
        query="beta_002%",
        now=NOW,
    )
    # SQL wildcard characters are escaped and therefore cannot broaden a
    # requester-scoped search.
    assert queried.items == ()
    queried = service.list_work_order_options(
        db,
        actor=world.actor,
        limit=20,
        query="beta",
        now=NOW,
    )
    assert [item.work_order_id for item in queried.items] == [second.id]


def test_work_order_option_detail_reuses_exact_command_guard(db):
    world = make_world(db)
    active = _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_1,
        work_order_no="WO-RSC-DETAIL-001",
        observed_at=NOW,
    )
    inactive = _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_2,
        work_order_no="WO-RSC-DETAIL-002",
        status="completed",
        observed_at=NOW,
    )
    other_person = _person(db, world.department, "不可见工程师")
    foreign = _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_3,
        work_order_no="WO-RSC-DETAIL-003",
        engineer=other_person,
        observed_at=NOW,
    )

    output = service.work_order_option_detail(
        db,
        actor=world.actor,
        work_order_id=active.id,
        now=NOW,
    )
    assert output.item.work_order_id == active.id
    assert output.item.work_order_no == active.work_order_no
    assert output.item.status == "active"
    assert output.item.source_updated_at == NOW

    for identifier in (inactive.id, foreign.id, uuid.uuid4()):
        with pytest.raises(service.MaterialRequestOptionError) as captured:
            service.work_order_option_detail(
                db,
                actor=world.actor,
                work_order_id=identifier,
                now=NOW,
            )
        assert captured.value.code == "material_request_work_order_option_not_found"
        assert captured.value.http_status_code == 404


def test_work_order_options_reject_invalid_inputs_and_stale_principal(db):
    world = make_world(db)

    for kwargs, expected_code in (
        ({"limit": 0}, "material_request_work_order_limit_invalid"),
        ({"limit": True}, "material_request_work_order_limit_invalid"),
        (
            {"limit": 20, "after_id": "not-a-uuid"},
            "material_request_work_order_cursor_invalid",
        ),
        (
            {"limit": 20, "after_id": uuid.UUID(int=0)},
            "material_request_work_order_cursor_invalid",
        ),
        (
            {"limit": 20, "query": " padded "},
            "material_request_work_order_query_invalid",
        ),
    ):
        with pytest.raises(service.MaterialRequestOptionError) as captured:
            service.list_work_order_options(
                db,
                actor=world.actor,
                now=NOW,
                **kwargs,
            )
        assert captured.value.code == expected_code
        assert captured.value.http_status_code == 422

    world.actor_user.authorization_version += 1
    db.flush()
    with pytest.raises(service.MaterialRequestOptionError) as stale:
        service.list_work_order_options(
            db,
            actor=world.actor,
            limit=20,
            now=NOW,
        )
    assert stale.value.code == "material_request_actor_principal_stale"
    assert stale.value.http_status_code == 412


@pytest.mark.parametrize(
    ("kept_action", "allowed"),
    (("create", True), ("update_draft", True), (None, False)),
)
def test_work_order_options_require_create_or_update_draft_self_scope(
    db,
    kept_action,
    allowed,
):
    world = make_world(db)
    bindings = tuple(
        db.scalars(
            select(RolePermission)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                RolePermission.role_id == world.roles["technician"].id,
                Permission.resource == "material_request",
                Permission.action.in_(("create", "update_draft")),
            )
        ).all()
    )
    for binding in bindings:
        permission = db.get(Permission, binding.permission_id)
        assert permission is not None
        if permission.action != kept_action:
            db.delete(binding)
    db.flush()
    world.actor = load_formal_principal(db, world.actor_user.id, now=NOW)

    if allowed:
        result = service.list_work_order_options(
            db,
            actor=world.actor,
            limit=20,
            now=NOW,
        )
        assert result.items == ()
    else:
        with pytest.raises(service.MaterialRequestOptionError) as captured:
            service.list_work_order_options(
                db,
                actor=world.actor,
                limit=20,
                now=NOW,
            )
        assert captured.value.code == "material_request_self_scope_forbidden"
        assert captured.value.http_status_code == 403


def test_work_order_options_fail_closed_on_projection_or_database_failure(db):
    world = make_world(db)
    _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_1,
        work_order_no="X" * 101,
        observed_at=NOW,
    )
    with pytest.raises(service.MaterialRequestOptionError) as malformed:
        service.list_work_order_options(
            db,
            actor=world.actor,
            limit=20,
            now=NOW,
        )
    assert malformed.value.code == "material_request_work_order_projection_invalid"
    assert malformed.value.http_status_code == 503

    with patch.object(
        db,
        "scalars",
        side_effect=OperationalError("SELECT", {}, RuntimeError("database down")),
    ):
        with pytest.raises(service.MaterialRequestOptionError) as unavailable:
            service.list_work_order_options(
                db,
                actor=world.actor,
                limit=20,
                now=NOW,
            )
    assert unavailable.value.code == (
        "material_request_work_order_options_database_unavailable"
    )
    assert unavailable.value.http_status_code == 503


def test_work_order_options_fail_closed_on_provenance_but_detail_is_replaceable(
    db,
):
    world = make_world(db)
    row = _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_1,
        work_order_no="WO-RSC-PROVENANCE-001",
        observed_at=NOW,
    )
    version = db.scalar(
        select(ExternalObjectVersion).where(
            ExternalObjectVersion.external_object_id == row.external_object_id,
            ExternalObjectVersion.is_current.is_(True),
        )
    )
    assert version is not None
    version.payload_sha256 = "0" * 64
    db.flush()

    with patch.object(
        service.draft_service,
        "material_request_database_now",
        return_value=NOW,
    ) as database_clock, pytest.raises(
        service.MaterialRequestOptionError
    ) as listed:
        service.list_work_order_options(db, actor=world.actor, limit=20)
    database_clock.assert_called_once_with(db)
    assert listed.value.code == "material_request_work_order_projection_invalid"
    assert listed.value.http_status_code == 503

    with pytest.raises(service.MaterialRequestOptionError) as detailed:
        service.work_order_option_detail(
            db,
            actor=world.actor,
            work_order_id=row.id,
            now=NOW,
        )
    assert detailed.value.code == "material_request_work_order_option_not_found"
    assert detailed.value.http_status_code == 404


def test_work_order_options_enforce_database_clock_freshness_and_source_time(db):
    world = make_world(db)
    stale = _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_1,
        work_order_no="WO-RSC-STALE-001",
        observed_at=NOW - timedelta(minutes=46),
    )
    with patch.object(
        service.draft_service,
        "material_request_database_now",
        return_value=NOW,
    ) as database_clock, pytest.raises(
        service.MaterialRequestOptionError
    ) as listed:
        service.list_work_order_options(db, actor=world.actor, limit=20)
    database_clock.assert_called_once_with(db)
    assert listed.value.code == "material_request_work_order_projection_stale"
    assert listed.value.http_status_code == 503
    with patch.object(
        service.draft_service,
        "material_request_database_now",
        return_value=NOW,
    ), pytest.raises(service.MaterialRequestOptionError) as detailed:
        service.work_order_option_detail(
            db,
            actor=world.actor,
            work_order_id=stale.id,
        )
    assert detailed.value.code == "material_request_work_order_option_not_found"
    assert detailed.value.http_status_code == 404

    # Rebuild on a fresh isolated world is not possible inside one unique-role
    # fixture, so repair the same evidence then prove source time cannot be
    # newer than the projection sync time.
    external = db.get(ExternalObject, stale.external_object_id)
    assert external is not None
    version = db.get(ExternalObjectVersion, external.current_version_id)
    assert version is not None
    stale.updated_at = NOW
    stale.source_updated_at = NOW + timedelta(seconds=1)
    version.created_at = NOW
    version.valid_from = NOW - timedelta(minutes=1)
    version.source_updated_at = stale.source_updated_at
    payload = service.draft_service.material_request_work_order_projection_payload(
        stale
    )
    version.payload_jsonb = payload
    version.payload_sha256 = service.draft_service._canonical_hash(payload)
    db.flush()
    with patch.object(
        service.draft_service,
        "material_request_database_now",
        return_value=NOW,
    ), pytest.raises(service.MaterialRequestOptionError) as future_source:
        service.list_work_order_options(db, actor=world.actor, limit=20)
    assert future_source.value.code == "material_request_work_order_projection_invalid"
    assert future_source.value.http_status_code == 503


def test_work_order_option_reads_never_invoke_command_row_lock(db):
    world = make_world(db)
    row = _oam_work_order(
        db,
        world,
        identifier=WORK_ORDER_1,
        observed_at=NOW,
    )
    with patch.object(
        service.draft_service,
        "lock_material_request_work_order",
        side_effect=AssertionError("read selector attempted command row lock"),
    ):
        page = service.list_work_order_options(
            db,
            actor=world.actor,
            limit=20,
            now=NOW,
        )
        detail = service.work_order_option_detail(
            db,
            actor=world.actor,
            work_order_id=row.id,
            now=NOW,
        )
    assert page.items[0].work_order_id == row.id
    assert detail.item.work_order_id == row.id


def test_work_order_option_output_requires_aware_source_time():
    with pytest.raises(ValidationError):
        MaterialRequestWorkOrderOptionOut(
            work_order_id=WORK_ORDER_1,
            work_order_no="WO-RSC-NAIVE",
            status="active",
            source_system_code="starcharge_oam",
            source_external_id="external-work-order-1",
            source_version="v1",
            source_updated_at=datetime(2026, 9, 2, 12, 0),
            synced_at=datetime(2026, 9, 2, 12, 1, tzinfo=timezone.utc),
            freshness_status="fresh",
        )


def test_work_order_option_api_is_permissioned_strict_and_no_store(monkeypatch):
    database = Mock()
    principal = Mock()
    principal.allows.side_effect = (
        lambda _db, resource, action, **_kwargs: (
            resource == "material_request" and action == "create"
        )
    )
    item = MaterialRequestWorkOrderOptionOut(
        work_order_id=WORK_ORDER_1,
        work_order_no="WO-RSC-API-001",
        status="active",
        source_system_code="starcharge_oam",
        source_external_id="external-work-order-api-001",
        source_version="v-api-001",
        source_updated_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
        synced_at=datetime(2026, 9, 2, 12, 1, tzinfo=timezone.utc),
        freshness_status="fresh",
    )
    page = MaterialRequestWorkOrderOptionPageOut(
        person_id=uuid.UUID("b2000000-0000-4000-8000-000000000001"),
        authorization_version=7,
        items=(item,),
        next_after_id=None,
    )
    detail = MaterialRequestWorkOrderOptionDetailOut(
        person_id=page.person_id,
        authorization_version=7,
        item=item,
    )
    list_call = Mock(return_value=page)
    detail_call = Mock(return_value=detail)
    monkeypatch.setattr(service, "list_work_order_options", list_call)
    monkeypatch.setattr(service, "work_order_option_detail", detail_call)
    api = FastAPI()
    api.include_router(formal_material_request_options.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: principal

    with TestClient(api) as client:
        list_response = client.get(
            "/api/v1/material-request-options/work-orders",
            params={"limit": 10, "query": "API"},
        )
        detail_response = client.get(
            f"/api/v1/material-request-options/work-orders/{WORK_ORDER_1}"
        )

    assert list_response.status_code == 200
    assert list_response.json() == {
        "schema_version": "1.0",
        "person_id": str(page.person_id),
        "authorization_version": 7,
        "items": [
            {
                "work_order_id": str(WORK_ORDER_1),
                "work_order_no": "WO-RSC-API-001",
                "status": "active",
                "source_system_code": "starcharge_oam",
                "source_external_id": "external-work-order-api-001",
                "source_version": "v-api-001",
                "source_updated_at": "2026-09-02T12:00:00Z",
                "synced_at": "2026-09-02T12:01:00Z",
                "freshness_status": "fresh",
            }
        ],
        "next_after_id": None,
    }
    assert detail_response.status_code == 200
    assert detail_response.json()["item"] == list_response.json()["items"][0]
    for response in (list_response, detail_response):
        assert response.headers["cache-control"] == "private, no-store, max-age=0"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
    list_call.assert_called_once_with(
        database,
        actor=principal,
        limit=10,
        after_id=None,
        query="API",
    )
    detail_call.assert_called_once_with(
        database,
        actor=principal,
        work_order_id=WORK_ORDER_1,
    )
    assert principal.allows.call_args_list[0].args[1:3] == (
        "material_request",
        "create",
    )
    database.commit.assert_not_called()
    database.rollback.assert_not_called()

    assert any(
        route.path == "/api/v1/material-request-options/work-orders"
        for route in main_module.app.routes
    )


def test_work_order_option_api_allows_update_draft_without_create(monkeypatch):
    database = Mock()
    principal = Mock()
    principal.allows.side_effect = (
        lambda _db, resource, action, **_kwargs: (
            resource == "material_request" and action == "update_draft"
        )
    )
    item = MaterialRequestWorkOrderOptionOut(
        work_order_id=WORK_ORDER_1,
        work_order_no="WO-RSC-UPDATE-ONLY",
        status="active",
        source_system_code="starcharge_oam",
        source_external_id="external-update-only",
        source_version="v-update-only",
        source_updated_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
        synced_at=datetime(2026, 9, 2, 12, 1, tzinfo=timezone.utc),
        freshness_status="fresh",
    )
    page = MaterialRequestWorkOrderOptionPageOut(
        person_id=uuid.UUID("b2000000-0000-4000-8000-000000000002"),
        authorization_version=8,
        items=(item,),
        next_after_id=None,
    )
    detail = MaterialRequestWorkOrderOptionDetailOut(
        person_id=page.person_id,
        authorization_version=page.authorization_version,
        item=item,
    )
    list_call = Mock(return_value=page)
    detail_call = Mock(return_value=detail)
    monkeypatch.setattr(service, "list_work_order_options", list_call)
    monkeypatch.setattr(service, "work_order_option_detail", detail_call)
    api = FastAPI()
    api.include_router(formal_material_request_options.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: principal

    with TestClient(api) as client:
        list_response = client.get("/api/v1/material-request-options/work-orders")
        detail_response = client.get(
            f"/api/v1/material-request-options/work-orders/{WORK_ORDER_1}"
        )

    assert [list_response.status_code, detail_response.status_code] == [200, 200]
    assert [call.args[2] for call in principal.allows.call_args_list] == [
        "create",
        "update_draft",
        "create",
        "update_draft",
    ]
    list_call.assert_called_once()
    detail_call.assert_called_once()


def test_work_order_option_api_denies_without_create_or_update_permission(
    monkeypatch,
):
    database = Mock()
    principal = Mock()
    principal.allows.return_value = False
    call = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(service, "list_work_order_options", call)
    api = FastAPI()
    api.include_router(formal_material_request_options.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: principal

    with TestClient(api) as client:
        responses = (
            client.get("/api/v1/material-request-options/work-orders"),
            client.get(
                f"/api/v1/material-request-options/work-orders/{WORK_ORDER_1}"
            ),
        )

    assert [response.status_code for response in responses] == [403, 403]
    call.assert_not_called()


def test_work_order_option_api_maps_service_failure_without_leaking_rows(
    monkeypatch,
):
    database = Mock()
    principal = Mock()
    principal.allows.return_value = True
    call = Mock(
        side_effect=service.MaterialRequestOptionError(
            "material_request_work_order_options_database_unavailable",
            "service_unavailable",
            "OAM 工单选项暂时不可用",
        )
    )
    monkeypatch.setattr(service, "list_work_order_options", call)
    api = FastAPI()
    api.include_router(formal_material_request_options.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: principal

    with TestClient(api) as client:
        response = client.get("/api/v1/material-request-options/work-orders")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "material_request_work_order_options_database_unavailable",
            "category": "service_unavailable",
            "message": "OAM 工单选项暂时不可用",
        }
    }
    assert "work_order_no" not in response.text
    database.commit.assert_not_called()
    database.rollback.assert_not_called()


def test_main_middleware_applies_no_store_to_option_errors(monkeypatch):
    database = Mock()
    principal = Mock()
    principal.allows.return_value = True
    unavailable = Mock(
        side_effect=service.MaterialRequestOptionError(
            "material_request_work_order_options_database_unavailable",
            "service_unavailable",
            "OAM 工单选项暂时不可用",
        )
    )
    monkeypatch.setattr(service, "list_work_order_options", unavailable)
    main_module.app.dependency_overrides[get_db] = lambda: database
    main_module.app.dependency_overrides[get_formal_principal] = lambda: principal
    try:
        with TestClient(main_module.app) as client:
            responses = [
                client.get("/api/v1/material-request-options/work-orders"),
                client.get(
                    "/api/v1/material-request-options/work-orders",
                    params={"limit": 0},
                ),
                client.get("/api/v1/material-request-options/unknown"),
            ]
            principal.allows.return_value = False
            responses.append(
                client.get("/api/v1/material-request-options/work-orders")
            )
            main_module.app.dependency_overrides.pop(get_formal_principal, None)
            responses.append(
                client.get("/api/v1/material-request-options/work-orders")
            )
    finally:
        main_module.app.dependency_overrides.pop(get_db, None)
        main_module.app.dependency_overrides.pop(get_formal_principal, None)

    assert [response.status_code for response in responses] == [
        503,
        422,
        404,
        403,
        401,
    ]
    for response in responses:
        assert response.headers["cache-control"] == "private, no-store, max-age=0"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
