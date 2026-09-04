from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import opening_observation_disposition as disposition_service
from app.formal_services import opening_stocktake as start_service
from app.formal_services import opening_stocktake_count as count_service
from app.formal_services import opening_stocktake_recount as recount_service
from app.formal_services import opening_stocktake_review as review_service
from app.main import app
from app.opening_stocktake_schemas import (
    OpeningControlLineIn,
    OpeningPhysicalObservationIn,
)
from app.routers import formal_opening_stocktake


TASK_ID = uuid.UUID("82000000-0000-4000-8000-000000000001")
ROUND_ID = uuid.UUID("82000000-0000-4000-8000-000000000002")
SCOPE_ID = uuid.UUID("82000000-0000-4000-8000-000000000003")
OWNER_ID = uuid.UUID("82000000-0000-4000-8000-000000000004")
LOCATION_ID = uuid.UUID("82000000-0000-4000-8000-000000000005")
REGION_ID = uuid.UUID("82000000-0000-4000-8000-000000000006")
SOURCE_ID = uuid.UUID("82000000-0000-4000-8000-000000000007")
RUN_ID = uuid.UUID("82000000-0000-4000-8000-000000000008")
EVENT_ID = uuid.UUID("82000000-0000-4000-8000-000000000009")
VERSION_ID = uuid.UUID("82000000-0000-4000-8000-00000000000a")
OBJECT_ID = uuid.UUID("82000000-0000-4000-8000-00000000000b")
MATERIAL_ID = uuid.UUID("82000000-0000-4000-8000-00000000000c")
DIFFERENCE_ID = uuid.UUID("82000000-0000-4000-8000-00000000000d")
REVIEW_ID = uuid.UUID("82000000-0000-4000-8000-00000000000e")
RECOUNT_CASE_ID = uuid.UUID("82000000-0000-4000-8000-00000000000f")
NEXT_ROUND_ID = uuid.UUID("82000000-0000-4000-8000-000000000010")
OBSERVATION_ID = uuid.UUID("82000000-0000-4000-8000-000000000011")
DISPOSITION_ID = uuid.UUID("82000000-0000-4000-8000-000000000012")
NOW = datetime(2026, 8, 31, 9, 15, tzinfo=timezone.utc)
PAYLOAD_HASH = "a" * 64


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _Db:
    def __init__(self) -> None:
        self.commit = Mock()
        self.rollback = Mock()
        self.execute = Mock()


class _Principal:
    def __init__(self) -> None:
        self.allowed_actions = {
            ("stocktake", "manage"),
            ("stocktake", "count"),
            ("stocktake", "review_region"),
            ("stocktake", "review_headquarters"),
            ("stocktake", "post_opening"),
        }
        self.calls: list[tuple[str, str]] = []

    def allows(self, _db, resource: str, action: str, **_kwargs) -> bool:
        self.calls.append((resource, action))
        return (resource, action) in self.allowed_actions


@pytest.fixture()
def write_api_client():
    db = _Db()
    principal = _Principal()
    api = FastAPI()
    api.include_router(formal_opening_stocktake.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    with TestClient(api) as client:
        yield client, db, principal


def _headers() -> dict[str, str]:
    return {
        "Idempotency-Key": "opening-write-0001",
        "X-Request-ID": "request-write-0001",
    }


def _start_body() -> dict[str, object]:
    return {
        "task_no": "OPENING-2026-0001",
        "region_org_id": str(REGION_ID),
        "control_source_system_id": str(SOURCE_ID),
        "control_sync_run_id": str(RUN_ID),
        "control_sync_scope_key": "region:JS",
        "scopes": [
            {
                "owner_org_id": str(OWNER_ID),
                "location_id": str(LOCATION_ID),
                "assignee_user_id": "engineer-001",
                "freeze_mode": "hard",
            }
        ],
        "control_lines": [
            {
                "sync_inbox_event_id": str(EVENT_ID),
                "external_object_version_id": str(VERSION_ID),
                "external_business_key": "OAM-JS-MAT-001",
                "material_id": str(MATERIAL_ID),
                "condition_code": "new",
                "control_qty": "12.500",
                "mapping_status": "resolved",
                "source_updated_at": "2026-08-31T09:15:00Z",
                "mapping_note": "",
            }
        ],
        "blind_count": True,
        "deadline": "2026-09-01T09:15:00+08:00",
        "note": "正式期初盘点",
    }


def _count_body() -> dict[str, object]:
    return {
        "physical_observations": [
            {
                "material_identifier_raw": "SKU-001",
                "material_identifier_type": "sku_code",
                "condition_code": "new",
                "availability_bucket": "available",
                "counted_qty": "1.250",
                "material_id": str(MATERIAL_ID),
                "count_method": "scan",
                "reason_code": "initial_count",
                "remark": "现场扫码",
            }
        ],
        "zero_confirmed": False,
    }


def _review_body() -> dict[str, object]:
    return {
        "decision": "approve",
        "items": [
            {
                "difference_id": str(DIFFERENCE_ID),
                "decision": "accept_for_posting",
                "comment": "证据一致",
            }
        ],
        "comment": "",
    }


def _recount_body() -> dict[str, object]:
    return {
        "assignments": [
            {
                "scope_id": str(SCOPE_ID),
                "assignee_user_id": "engineer-002",
            }
        ],
        "reason": "区域复核要求复盘",
    }


def _disposition_body() -> dict[str, object]:
    return {
        "disposition": "resolved_existing_master",
        "reason_code": "master_matched",
        "comment": "主数据已核实",
        "resolved_material_id": str(MATERIAL_ID),
        "resolved_lot_id": None,
        "resolved_serial_id": None,
    }


def _configure_evidence(
    db: _Db,
    *,
    missing: bool = False,
    ambiguous: bool = False,
    mismatch: bool = False,
) -> None:
    if missing:
        db.execute.side_effect = [_Rows([]), _Rows([])]
        return
    payload = {"external_business_key": "OAM-JS-MAT-001"}
    event = SimpleNamespace(
        id=EVENT_ID,
        batch_id=uuid.uuid4(),
        source_system_id=SOURCE_ID,
        entity_type="oam_opening_stock_control",
        external_id="OAM-JS-MAT-001",
        source_version="v1",
        source_updated_at=NOW,
        payload_jsonb=payload,
        payload_sha256=PAYLOAD_HASH,
    )
    batch = SimpleNamespace(run_id=RUN_ID)
    external_object = SimpleNamespace(
        id=OBJECT_ID,
        source_system_id=SOURCE_ID,
        entity_type="oam_opening_stock_control",
        external_id="OAM-JS-MAT-001",
    )
    version = SimpleNamespace(
        id=VERSION_ID,
        external_object_id=OBJECT_ID,
        source_version="v1",
        source_updated_at=NOW,
        payload_jsonb=payload,
        payload_sha256="b" * 64 if mismatch else PAYLOAD_HASH,
    )
    event_rows = [(event, batch), (event, batch)] if ambiguous else [(event, batch)]
    db.execute.side_effect = [
        _Rows(event_rows),
        _Rows([(version, external_object)]),
    ]


def _start_result(*, replayed: bool = False):
    return SimpleNamespace(
        task_id=TASK_ID,
        task_no="OPENING-2026-0001",
        status="counting",
        cutoff_ledger_cursor=18,
        initial_round_id=ROUND_ID,
        scope_count=1,
        snapshot_line_count=2,
        control_line_count=1,
        replayed=replayed,
    )


def test_start_maps_strict_body_and_derives_persisted_hash(
    write_api_client, monkeypatch
):
    client, db, principal = write_api_client
    captured: dict[str, object] = {}
    _configure_evidence(db)

    def fake_start(_db, **kwargs):
        captured.update(kwargs)
        return _start_result()

    monkeypatch.setattr(start_service, "start_opening_stocktake", fake_start)

    response = client.post(
        "/api/v1/stocktakes/opening",
        json=_start_body(),
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json() == {
        "schema_version": "1.0",
        "task_id": str(TASK_ID),
        "task_no": "OPENING-2026-0001",
        "status": "counting",
        "cutoff_ledger_cursor": 18,
        "initial_round_id": str(ROUND_ID),
        "scope_count": 1,
        "snapshot_line_count": 2,
        "control_line_count": 1,
        "replayed": False,
    }
    command = captured["command"]
    assert isinstance(command, start_service.StartOpeningStocktakeCommand)
    assert command.region_org_id == REGION_ID
    assert command.scopes == (
        start_service.OpeningStocktakeScopeInput(
            owner_org_id=OWNER_ID,
            location_id=LOCATION_ID,
            assignee_user_id="engineer-001",
            freeze_mode="hard",
        ),
    )
    assert command.control_lines[0].control_qty == Decimal("12.500")
    assert command.control_lines[0].payload_sha256 == PAYLOAD_HASH
    assert captured["idempotency_key"] == "opening-write-0001"
    assert captured["request_id"] == "request-write-0001"
    assert ("stocktake", "manage") in principal.calls
    assert db.execute.call_count == 2
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_count_maps_path_and_decimal_to_existing_domain_command(
    write_api_client, monkeypatch
):
    client, db, principal = write_api_client
    captured: dict[str, object] = {}

    def fake_count(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            task_id=TASK_ID,
            round_id=ROUND_ID,
            scope_id=SCOPE_ID,
            task_status="submitted",
            round_status="submitted",
            scope_completed=True,
            round_sealed=True,
            has_pending_verification=False,
            replayed=True,
        )

    monkeypatch.setattr(
        count_service,
        "submit_opening_stocktake_scope_count",
        fake_count,
    )
    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/count",
        json=_count_body(),
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "true"
    assert response.json()["task_status"] == "submitted"
    command = captured["command"]
    assert isinstance(
        command,
        count_service.SubmitOpeningStocktakeScopeCountCommand,
    )
    assert (command.task_id, command.round_id, command.scope_id) == (
        TASK_ID,
        ROUND_ID,
        SCOPE_ID,
    )
    assert command.physical_observations[0].counted_qty == Decimal("1.250")
    assert ("stocktake", "count") in principal.calls
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("stage", "permission", "function_name", "resulting_status"),
    [
        ("region", "review_region", "submit_opening_region_review", "hq_review"),
        (
            "headquarters",
            "review_headquarters",
            "submit_opening_headquarters_review",
            "approved",
        ),
    ],
)
def test_review_routes_are_separate_permissions_and_state_transitions(
    write_api_client,
    monkeypatch,
    stage,
    permission,
    function_name,
    resulting_status,
):
    client, db, principal = write_api_client
    captured: dict[str, object] = {}

    def fake_review(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            review_id=REVIEW_ID,
            task_id=TASK_ID,
            round_id=ROUND_ID,
            review_stage=stage,
            decision="approve",
            resulting_task_status=resulting_status,
            item_count=1,
            pending_control_count=0,
            replayed=False,
        )

    monkeypatch.setattr(review_service, function_name, fake_review)
    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/reviews/{stage}",
        json=_review_body(),
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["review_stage"] == stage
    assert response.json()["resulting_task_status"] == resulting_status
    command = captured["command"]
    assert isinstance(
        command,
        review_service.SubmitOpeningStocktakeReviewCommand,
    )
    assert command.items == (
        review_service.OpeningStocktakeReviewItemInput(
            difference_id=DIFFERENCE_ID,
            decision="accept_for_posting",
            comment="证据一致",
        ),
    )
    assert ("stocktake", permission) in principal.calls
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_recount_and_disposition_map_path_owned_anchors(
    write_api_client, monkeypatch
):
    client, db, _principal = write_api_client
    recount_captured: dict[str, object] = {}
    disposition_captured: dict[str, object] = {}

    def fake_recount(_db, **kwargs):
        recount_captured.update(kwargs)
        return SimpleNamespace(
            recount_case_id=RECOUNT_CASE_ID,
            task_id=TASK_ID,
            source_round_id=ROUND_ID,
            next_round_id=NEXT_ROUND_ID,
            next_round_no=2,
            scope_count=1,
            resulting_task_status="counting",
            replayed=False,
        )

    def fake_disposition(_db, **kwargs):
        disposition_captured.update(kwargs)
        return SimpleNamespace(
            disposition_id=DISPOSITION_ID,
            task_id=TASK_ID,
            round_id=ROUND_ID,
            scope_id=SCOPE_ID,
            observation_id=OBSERVATION_ID,
            disposition="resolved_existing_master",
            resolved_material_id=MATERIAL_ID,
            resolved_lot_id=None,
            resolved_serial_id=None,
            disposition_manifest_sha256="c" * 64,
            replayed=False,
        )

    monkeypatch.setattr(
        recount_service,
        "open_opening_stocktake_recount",
        fake_recount,
    )
    monkeypatch.setattr(
        disposition_service,
        "record_opening_observation_disposition",
        fake_disposition,
    )
    recount_response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/recount",
        json=_recount_body(),
        headers=_headers(),
    )
    disposition_response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/observations/{OBSERVATION_ID}/disposition",
        json=_disposition_body(),
        headers={
            **_headers(),
            "Idempotency-Key": "opening-write-0002",
        },
    )

    assert recount_response.status_code == 200
    assert disposition_response.status_code == 200
    recount_command = recount_captured["command"]
    assert isinstance(
        recount_command,
        recount_service.OpenOpeningStocktakeRecountCommand,
    )
    assert recount_command.source_round_id == ROUND_ID
    assert recount_command.assignments[0].scope_id == SCOPE_ID
    disposition_command = disposition_captured["command"]
    assert isinstance(
        disposition_command,
        disposition_service.RecordOpeningObservationDispositionCommand,
    )
    assert disposition_command.observation_id == OBSERVATION_ID
    assert disposition_response.json()["disposition_manifest_sha256"] == "c" * 64
    assert db.commit.call_count == 2
    db.rollback.assert_not_called()


def test_known_observation_coordinates_cannot_bypass_disposition_scope_denial(
    write_api_client,
    monkeypatch,
):
    client, db, _principal = write_api_client
    captured: dict[str, object] = {}

    def reject_cross_scope(_db, **kwargs):
        captured.update(kwargs)
        raise disposition_service.OpeningObservationDispositionError(
            "opening_observation_disposition_forbidden",
            "forbidden",
            "当前人员无权以同一角色分配覆盖任务区域、资产所有组织与库位物理组织",
        )

    monkeypatch.setattr(
        disposition_service,
        "record_opening_observation_disposition",
        reject_cross_scope,
    )
    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/observations/{OBSERVATION_ID}/disposition",
        json={
            "disposition": "pending_verification",
            "reason_code": "site_identifier_pending",
            "comment": "继续人工核验",
        },
        headers=_headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == (
        "opening_observation_disposition_forbidden"
    )
    command = captured["command"]
    assert command.task_id == TASK_ID
    assert command.round_id == ROUND_ID
    assert command.observation_id == OBSERVATION_ID
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


_WRITE_REQUESTS = [
    ("/api/v1/stocktakes/opening", _start_body(), ("stocktake", "manage")),
    (
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/count",
        _count_body(),
        ("stocktake", "count"),
    ),
    (
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/reviews/region",
        _review_body(),
        ("stocktake", "review_region"),
    ),
    (
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/reviews/headquarters",
        _review_body(),
        ("stocktake", "review_headquarters"),
    ),
    (
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/recount",
        _recount_body(),
        ("stocktake", "manage"),
    ),
    (
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/observations/{OBSERVATION_ID}/disposition",
        _disposition_body(),
        ("stocktake", "manage"),
    ),
]


_WEB_OPENING_REJECTION_REQUESTS = [
    (
        "count",
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/count",
        _count_body(),
        count_service,
        "submit_opening_stocktake_scope_count",
        count_service.OpeningStocktakeCountError,
        "opening_count_state_invalid",
    ),
    (
        "review_region",
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/reviews/region",
        _review_body(),
        review_service,
        "submit_opening_region_review",
        review_service.OpeningStocktakeReviewError,
        "opening_review_stage_state_invalid",
    ),
    (
        "review_headquarters",
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/reviews/headquarters",
        _review_body(),
        review_service,
        "submit_opening_headquarters_review",
        review_service.OpeningStocktakeReviewError,
        "opening_review_stage_state_invalid",
    ),
    (
        "recount",
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/recount",
        _recount_body(),
        recount_service,
        "open_opening_stocktake_recount",
        recount_service.OpeningStocktakeRecountError,
        "opening_recount_state_invalid",
    ),
    (
        "disposition",
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/observations/{OBSERVATION_ID}/disposition",
        _disposition_body(),
        disposition_service,
        "record_opening_observation_disposition",
        disposition_service.OpeningObservationDispositionError,
        "opening_observation_disposition_state_invalid",
    ),
]


@pytest.mark.parametrize(
    (
        "_action",
        "path",
        "body",
        "service_module",
        "function_name",
        "_error_type",
        "_error_code",
    ),
    _WEB_OPENING_REJECTION_REQUESTS,
)
@pytest.mark.parametrize(
    ("headers", "expected_code"),
    [
        ({"X-Request-ID": "request-write-0001"}, "idempotency_key_invalid"),
        (
            {
                "Idempotency-Key": "opening-write-0001",
                "X-Request-ID": "bad request",
            },
            "x_request_id_invalid",
        ),
    ],
)
def test_web_opening_header_rejection_is_exact_and_never_enters_service(
    write_api_client,
    monkeypatch,
    _action,
    path,
    body,
    service_module,
    function_name,
    _error_type,
    _error_code,
    headers,
    expected_code,
):
    client, db, _principal = write_api_client
    called = Mock()
    monkeypatch.setattr(service_module, function_name, called)

    response = client.post(path, json=body, headers=headers)

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": expected_code,
        "category": "invalid_request",
        "message": (
            "Idempotency-Key 必须是 16-128 位安全字符"
            if expected_code == "idempotency_key_invalid"
            else "X-Request-ID 必须是 8-160 位安全字符"
        ),
    }
    called.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.parametrize(
    (
        "action",
        "path",
        "body",
        "service_module",
        "function_name",
        "error_type",
        "error_code",
    ),
    _WEB_OPENING_REJECTION_REQUESTS,
)
def test_web_opening_exact_state_rejection_rolls_back_staged_work_before_response(
    write_api_client,
    monkeypatch,
    action,
    path,
    body,
    service_module,
    function_name,
    error_type,
    error_code,
):
    client, db, _principal = write_api_client
    events: list[str] = []
    staged: list[str] = []

    def rollback() -> None:
        events.append("rollback")
        staged.clear()

    def reject(_db, **_kwargs):
        assert _db is db
        events.append("service")
        staged.append(f"staged:{action}")
        raise error_type(
            error_code,
            "precondition_failed",
            "当前状态不允许执行该期初盘点操作",
        )

    db.rollback.side_effect = rollback
    monkeypatch.setattr(service_module, function_name, reject)

    response = client.post(path, json=body, headers=_headers())

    assert response.status_code == 412
    assert response.json()["detail"] == {
        "code": error_code,
        "category": "precondition_failed",
        "message": "当前状态不允许执行该期初盘点操作",
    }
    assert events == ["service", "rollback"]
    assert staged == []
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_web_opening_commit_exception_is_not_translated_to_domain_4xx(
    write_api_client,
    monkeypatch,
):
    client, db, _principal = write_api_client
    monkeypatch.setattr(
        count_service,
        "submit_opening_stocktake_scope_count",
        lambda *_args, **_kwargs: SimpleNamespace(
            task_id=TASK_ID,
            round_id=ROUND_ID,
            scope_id=SCOPE_ID,
            task_status="submitted",
            round_status="submitted",
            scope_completed=True,
            round_sealed=True,
            has_pending_verification=False,
            replayed=False,
        ),
    )
    db.commit.side_effect = RuntimeError("commit outcome unknown")

    with pytest.raises(RuntimeError, match="commit outcome unknown"):
        client.post(
            f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/count",
            json=_count_body(),
            headers=_headers(),
        )

    db.commit.assert_called_once_with()
    db.rollback.assert_called_once_with()


@pytest.mark.parametrize(("path", "body", "_permission"), _WRITE_REQUESTS)
def test_every_write_requires_both_safe_headers(
    write_api_client, path, body, _permission
):
    client, db, _principal = write_api_client

    response = client.post(
        path,
        json=body,
        headers={"X-Request-ID": "request-write-0001"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "idempotency_key_invalid"
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.parametrize(("path", "body", "permission"), _WRITE_REQUESTS)
def test_each_write_has_its_exact_permission_dependency(
    write_api_client, path, body, permission
):
    client, db, principal = write_api_client
    principal.allowed_actions = set()

    response = client.post(path, json=body, headers=_headers())

    assert response.status_code == 403
    assert principal.calls == [permission]
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update({"actor_user_id": "attacker"}),
        lambda body: body.update({"status": "approved"}),
        lambda body: body["control_lines"][0].update(
            {"payload_sha256": "f" * 64}
        ),
        lambda body: body["control_lines"][0].update({"control_qty": 12.5}),
        lambda body: body.update({"deadline": "2026-09-01T09:15:00"}),
    ],
)
def test_start_rejects_actor_state_hash_lossy_decimal_and_naive_time(
    write_api_client, mutate
):
    client, db, _principal = write_api_client
    body = _start_body()
    mutate(body)

    response = client.post(
        "/api/v1/stocktakes/opening",
        json=body,
        headers=_headers(),
    )

    assert response.status_code == 422
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.execute.assert_not_called()


def test_http_quantities_are_strictly_below_numeric_18_3_limit(
    write_api_client,
):
    client, db, _principal = write_api_client
    largest = "999999999999999.999"
    boundary = "1000000000000000"

    control = _start_body()["control_lines"][0]
    control["control_qty"] = largest
    assert OpeningControlLineIn.model_validate(control).control_qty == Decimal(
        largest
    )
    control["control_qty"] = boundary
    with pytest.raises(ValidationError):
        OpeningControlLineIn.model_validate(control)

    observation = _count_body()["physical_observations"][0]
    observation["counted_qty"] = largest
    assert OpeningPhysicalObservationIn.model_validate(
        observation
    ).counted_qty == Decimal(largest)
    observation["counted_qty"] = boundary
    with pytest.raises(ValidationError):
        OpeningPhysicalObservationIn.model_validate(observation)

    start_body = _start_body()
    start_body["control_lines"][0]["control_qty"] = boundary
    count_body = _count_body()
    count_body["physical_observations"][0]["counted_qty"] = boundary
    for path, body in (
        ("/api/v1/stocktakes/opening", start_body),
        (
            f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/count",
            count_body,
        ),
    ):
        response = client.post(path, json=body, headers=_headers())
        assert response.status_code == 422

    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.execute.assert_not_called()


def test_count_api_preserves_stable_aggregate_overflow_error(
    write_api_client,
    monkeypatch,
):
    client, db, _principal = write_api_client
    body = _count_body()
    first = body["physical_observations"][0]
    first["counted_qty"] = "600000000000000.000"
    second = dict(first)
    second["condition_code"] = "used"
    body["physical_observations"] = [first, second]

    def reject_aggregate(_db, **kwargs):
        quantities = tuple(
            row.counted_qty
            for row in kwargs["command"].physical_observations
        )
        assert quantities == (
            Decimal("600000000000000.000"),
            Decimal("600000000000000.000"),
        )
        raise count_service.OpeningStocktakeCountError(
            "opening_count_aggregate_quantity_invalid",
            "invalid_request",
            "盘点范围实盘合计超出 Numeric(18,3) 安全范围",
        )

    monkeypatch.setattr(
        count_service,
        "submit_opening_stocktake_scope_count",
        reject_aggregate,
    )
    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/count",
        json=body,
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == (
        "opening_count_aggregate_quantity_invalid"
    )
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("missing", "ambiguous", "mismatch", "status_code", "error_code"),
    [
        (True, False, False, 412, "opening_control_evidence_not_found"),
        (False, True, False, 503, "opening_control_evidence_ambiguous"),
        (False, False, True, 412, "opening_control_evidence_mismatch"),
    ],
)
def test_start_evidence_adapter_fails_closed_with_stable_errors(
    write_api_client,
    monkeypatch,
    missing,
    ambiguous,
    mismatch,
    status_code,
    error_code,
):
    client, db, _principal = write_api_client
    called = Mock()
    monkeypatch.setattr(start_service, "start_opening_stocktake", called)
    _configure_evidence(
        db,
        missing=missing,
        ambiguous=ambiguous,
        mismatch=mismatch,
    )

    response = client.post(
        "/api/v1/stocktakes/opening",
        json=_start_body(),
        headers=_headers(),
    )

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == error_code
    called.assert_not_called()
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_evidence_race_is_revalidated_by_domain_and_rolls_back(
    write_api_client, monkeypatch
):
    client, db, _principal = write_api_client
    _configure_evidence(db)

    def fail_after_adapter(*_args, **_kwargs):
        raise start_service.OpeningStocktakeError(
            "control_line_evidence_mismatch",
            "precondition_failed",
            "控制行与锁内证据不一致",
        )

    monkeypatch.setattr(
        start_service,
        "start_opening_stocktake",
        fail_after_adapter,
    )
    response = client.post(
        "/api/v1/stocktakes/opening",
        json=_start_body(),
        headers=_headers(),
    )

    assert response.status_code == 412
    assert response.json()["detail"] == {
        "code": "control_line_evidence_mismatch",
        "category": "precondition_failed",
        "message": "控制行与锁内证据不一致",
    }
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("path", "body", "service", "function_name", "error_type"),
    [
        (
            "/api/v1/stocktakes/opening",
            _start_body(),
            start_service,
            "start_opening_stocktake",
            start_service.OpeningStocktakeError,
        ),
        (
            f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/count",
            _count_body(),
            count_service,
            "submit_opening_stocktake_scope_count",
            count_service.OpeningStocktakeCountError,
        ),
        (
            f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/reviews/region",
            _review_body(),
            review_service,
            "submit_opening_region_review",
            review_service.OpeningStocktakeReviewError,
        ),
        (
            f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/reviews/headquarters",
            _review_body(),
            review_service,
            "submit_opening_headquarters_review",
            review_service.OpeningStocktakeReviewError,
        ),
        (
            f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/recount",
            _recount_body(),
            recount_service,
            "open_opening_stocktake_recount",
            recount_service.OpeningStocktakeRecountError,
        ),
        (
            f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/observations/{OBSERVATION_ID}/disposition",
            _disposition_body(),
            disposition_service,
            "record_opening_observation_disposition",
            disposition_service.OpeningObservationDispositionError,
        ),
    ],
)
def test_domain_conflicts_have_stable_detail_and_rollback(
    write_api_client,
    monkeypatch,
    path,
    body,
    service,
    function_name,
    error_type,
):
    client, db, _principal = write_api_client
    if service is start_service:
        _configure_evidence(db)

    def fail(*_args, **_kwargs):
        raise error_type(
            "opening_write_concurrent_conflict",
            "conflict",
            "盘点事实已变化，请重新读取",
        )

    monkeypatch.setattr(service, function_name, fail)
    response = client.post(path, json=body, headers=_headers())

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "opening_write_concurrent_conflict",
        "category": "conflict",
        "message": "盘点事实已变化，请重新读取",
    }
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_unknown_service_failure_rolls_back_and_is_not_translated(
    write_api_client, monkeypatch
):
    client, db, _principal = write_api_client
    monkeypatch.setattr(
        count_service,
        "submit_opening_stocktake_scope_count",
        Mock(side_effect=RuntimeError("unexpected")),
    )

    with pytest.raises(RuntimeError, match="unexpected"):
        client.post(
            f"/api/v1/stocktakes/opening/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/count",
            json=_count_body(),
            headers=_headers(),
        )

    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_openapi_contains_each_write_operation_exactly_once():
    expected_paths = {
        "/api/v1/stocktakes/opening",
        "/api/v1/stocktakes/opening/{task_id}/rounds/{round_id}/scopes/{scope_id}/count",
        "/api/v1/stocktakes/opening/{task_id}/rounds/{round_id}/reviews/region",
        "/api/v1/stocktakes/opening/{task_id}/rounds/{round_id}/reviews/headquarters",
        "/api/v1/stocktakes/opening/{task_id}/rounds/{round_id}/recount",
        "/api/v1/stocktakes/opening/{task_id}/rounds/{round_id}/observations/{observation_id}/disposition",
    }
    route_pairs = [
        (route.path, method)
        for route in app.routes
        for method in route.methods or ()
        if route.path in expected_paths and method == "POST"
    ]

    assert sorted(path for path, _method in route_pairs) == sorted(expected_paths)
    schema = app.openapi()
    assert all("post" in schema["paths"][path] for path in expected_paths)
