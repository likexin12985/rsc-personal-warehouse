from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import opening_stocktake_finalize as terminal_service
from app.main import app
from app.routers import formal_opening_stocktake


TASK_ID = uuid.UUID("81000000-0000-4000-8000-000000000001")
ROUND_ID = uuid.UUID("81000000-0000-4000-8000-000000000002")
POSTING_ID = uuid.UUID("81000000-0000-4000-8000-000000000003")
TRANSACTION_ID = uuid.UUID("81000000-0000-4000-8000-000000000004")
NOW = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


class _Principal:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    def allows(self, _db, resource: str, action: str, **_kwargs) -> bool:
        return self.allowed and (resource, action) == (
            "stocktake",
            "post_opening",
        )


@pytest.fixture()
def api_client():
    db = SimpleNamespace(commit=Mock(), rollback=Mock())
    principal = _Principal()
    api = FastAPI()
    api.include_router(formal_opening_stocktake.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    with TestClient(api) as client:
        yield client, db, principal


def _headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Idempotency-Key": "opening-post-0001",
        "X-Request-ID": "request-0001",
    }
    headers.update(overrides)
    return headers


def _post_result(*, replayed: bool = False, quantity: Decimal = Decimal("12.500")):
    return SimpleNamespace(
        task_id=TASK_ID,
        round_id=ROUND_ID,
        posting_id=POSTING_ID,
        inventory_transaction_id=TRANSACTION_ID,
        resulting_task_status="posted",
        task_version=6,
        total_quantity=quantity,
        established_scope_count=2,
        pending_control_difference_count=1,
        ledger_cursor=23,
        replayed=replayed,
    )


def _close_result(*, replayed: bool = False):
    return SimpleNamespace(
        task_id=TASK_ID,
        posting_id=POSTING_ID,
        inventory_transaction_id=TRANSACTION_ID,
        resulting_task_status="closed",
        task_version=7,
        closed_at=NOW,
        replayed=replayed,
    )


_TERMINAL_REQUESTS = [
    (
        "post",
        f"/api/v1/stocktakes/opening/{TASK_ID}/post",
        {"expected_version": 5},
        "post_approved_opening_stocktake",
        _post_result,
    ),
    (
        "close",
        f"/api/v1/stocktakes/opening/{TASK_ID}/close",
        {"expected_version": 6},
        "close_posted_opening_stocktake",
        _close_result,
    ),
]


_TERMINAL_412_CASES = [
    (*_TERMINAL_REQUESTS[0][:4], "opening_finalize_state_invalid"),
    (*_TERMINAL_REQUESTS[1][:4], "opening_finalize_state_invalid"),
    (*_TERMINAL_REQUESTS[1][:4], "opening_close_reconciliation_pending"),
]


def test_post_route_commits_once_and_exposes_only_terminal_result(
    api_client, monkeypatch
):
    client, db, _principal = api_client
    captured: dict[str, object] = {}

    def fake_post(_db, **kwargs):
        captured.update(kwargs)
        return _post_result()

    monkeypatch.setattr(
        terminal_service,
        "post_approved_opening_stocktake",
        fake_post,
    )

    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/post",
        json={"expected_version": 5},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json() == {
        "schema_version": "1.0",
        "task_id": str(TASK_ID),
        "round_id": str(ROUND_ID),
        "posting_id": str(POSTING_ID),
        "inventory_transaction_id": str(TRANSACTION_ID),
        "resulting_task_status": "posted",
        "task_version": 6,
        "total_quantity": "12.500",
        "established_scope_count": 2,
        "pending_control_difference_count": 1,
        "ledger_cursor": 23,
        "replayed": False,
    }
    command = captured["command"]
    assert isinstance(command, terminal_service.PostOpeningStocktakeCommand)
    assert command.task_id == TASK_ID
    assert command.expected_version == 5
    assert captured["idempotency_key"] == "opening-post-0001"
    assert captured["request_id"] == "request-0001"
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_close_route_is_a_separate_transition_and_marks_replay(
    api_client, monkeypatch
):
    client, db, _principal = api_client
    captured: dict[str, object] = {}

    def fake_close(_db, **kwargs):
        captured.update(kwargs)
        return _close_result(replayed=True)

    monkeypatch.setattr(
        terminal_service,
        "close_posted_opening_stocktake",
        fake_close,
    )

    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/close",
        json={"expected_version": 6},
        headers=_headers(**{"Idempotency-Key": "opening-close-0001"}),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "true"
    assert response.json()["resulting_task_status"] == "closed"
    assert response.json()["closed_at"] == "2026-08-31T08:00:00Z"
    command = captured["command"]
    assert isinstance(command, terminal_service.CloseOpeningStocktakeCommand)
    assert command.task_id == TASK_ID
    assert command.expected_version == 6
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("headers", "expected_code"),
    [
        ({"X-Request-ID": "request-0001"}, "idempotency_key_invalid"),
        (
            {
                "Idempotency-Key": "opening-post-0001",
                "X-Request-ID": "bad request",
            },
            "x_request_id_invalid",
        ),
    ],
)
def test_write_headers_are_mandatory_safe_values(
    api_client, headers, expected_code
):
    client, db, _principal = api_client

    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/post",
        json={"expected_version": 5},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == expected_code
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_permission_gate_blocks_before_domain_service(
    api_client, monkeypatch
):
    client, db, principal = api_client
    principal.allowed = False
    called = Mock()
    monkeypatch.setattr(
        terminal_service,
        "post_approved_opening_stocktake",
        called,
    )

    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/post",
        json={"expected_version": 5},
        headers=_headers(),
    )

    assert response.status_code == 403
    called.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_domain_failure_rolls_back_and_preserves_stable_error(
    api_client, monkeypatch
):
    client, db, _principal = api_client

    def fail(*_args, **_kwargs):
        raise terminal_service.OpeningStocktakeFinalizeError(
            "opening_stocktake_version_conflict",
            "conflict",
            "盘点任务版本已变化，请重新读取后再操作",
        )

    monkeypatch.setattr(
        terminal_service,
        "post_approved_opening_stocktake",
        fail,
    )

    response = client.post(
        f"/api/v1/stocktakes/opening/{TASK_ID}/post",
        json={"expected_version": 5},
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "opening_stocktake_version_conflict",
        "category": "conflict",
        "message": "盘点任务版本已变化，请重新读取后再操作",
    }
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_invalid_domain_output_rolls_back_before_commit(
    api_client, monkeypatch
):
    client, db, _principal = api_client
    monkeypatch.setattr(
        terminal_service,
        "post_approved_opening_stocktake",
        lambda *_args, **_kwargs: _post_result(quantity=Decimal("1.0001")),
    )

    with pytest.raises(RuntimeError):
        client.post(
            f"/api/v1/stocktakes/opening/{TASK_ID}/post",
            json={"expected_version": 5},
            headers=_headers(),
        )

    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("_action", "path", "body", "function_name", "_result_factory"),
    _TERMINAL_REQUESTS,
)
@pytest.mark.parametrize(
    ("headers", "expected_code"),
    [
        ({"X-Request-ID": "request-0001"}, "idempotency_key_invalid"),
        (
            {
                "Idempotency-Key": "opening-terminal-0001",
                "X-Request-ID": "bad request",
            },
            "x_request_id_invalid",
        ),
    ],
)
def test_terminal_header_rejection_is_exact_and_never_enters_service(
    api_client,
    monkeypatch,
    _action,
    path,
    body,
    function_name,
    _result_factory,
    headers,
    expected_code,
):
    client, db, _principal = api_client
    called = Mock()
    monkeypatch.setattr(terminal_service, function_name, called)

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


@pytest.mark.parametrize(
    ("action", "path", "body", "function_name", "error_code"),
    _TERMINAL_412_CASES,
)
def test_terminal_exact_state_rejection_rolls_back_staged_work_before_response(
    api_client,
    monkeypatch,
    action,
    path,
    body,
    function_name,
    error_code,
):
    client, db, _principal = api_client
    events: list[str] = []
    staged: list[str] = []

    def rollback() -> None:
        events.append("rollback")
        staged.clear()

    def reject(_db, **_kwargs):
        assert _db is db
        events.append("service")
        staged.append(f"staged:{action}")
        raise terminal_service.OpeningStocktakeFinalizeError(
            error_code,
            "precondition_failed",
            "期初任务当前状态不允许执行终态操作",
        )

    db.rollback.side_effect = rollback
    monkeypatch.setattr(terminal_service, function_name, reject)

    response = client.post(path, json=body, headers=_headers())

    assert response.status_code == 412
    assert response.json()["detail"] == {
        "code": error_code,
        "category": "precondition_failed",
        "message": "期初任务当前状态不允许执行终态操作",
    }
    assert events == ["service", "rollback"]
    assert staged == []
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("_action", "path", "body", "function_name", "result_factory"),
    _TERMINAL_REQUESTS,
)
def test_terminal_commit_exception_is_not_translated_to_domain_4xx(
    api_client,
    monkeypatch,
    _action,
    path,
    body,
    function_name,
    result_factory,
):
    client, db, _principal = api_client
    monkeypatch.setattr(
        terminal_service,
        function_name,
        lambda *_args, **_kwargs: result_factory(),
    )
    db.commit.side_effect = RuntimeError("commit outcome unknown")

    with pytest.raises(RuntimeError, match="commit outcome unknown"):
        client.post(path, json=body, headers=_headers())

    db.commit.assert_called_once_with()
    db.rollback.assert_called_once_with()


def test_terminal_routes_are_mounted_in_the_formal_v1_namespace():
    routes: dict[str, set[str]] = {}
    for route in app.routes:
        if route.path.startswith("/api/v1/stocktakes/opening"):
            routes.setdefault(route.path, set()).update(route.methods or ())
    expected = {
        "/api/v1/stocktakes/opening/{task_id}/post": {"POST"},
        "/api/v1/stocktakes/opening/{task_id}/close": {"POST"},
    }
    assert all(expected_methods <= routes.get(path, set()) for path, expected_methods in expected.items())
