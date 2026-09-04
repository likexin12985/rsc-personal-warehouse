from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import opening_control_reconciliation as service
from app.routers import formal_reconciliation


TASK_ID = uuid.UUID("97000000-0000-4000-8000-000000000001")
RUN_ID = uuid.UUID("97000000-0000-4000-8000-000000000002")
ITEM_ID = uuid.UUID("97000000-0000-4000-8000-000000000003")
FILE_ID = uuid.UUID("97000000-0000-4000-8000-000000000004")
NOW = datetime(2026, 8, 31, 11, 30, tzinfo=timezone.utc)


class _Db:
    def __init__(self) -> None:
        self.commit = Mock()
        self.rollback = Mock()


class _Principal:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def allows(self, _db, resource: str, action: str, **_kwargs) -> bool:
        self.calls.append((resource, action))
        return resource == "reconciliation"


@pytest.fixture()
def api_client():
    db = _Db()
    principal = _Principal()
    api = FastAPI()
    api.include_router(formal_reconciliation.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    with TestClient(api) as client:
        yield client, db, principal


def _headers() -> dict[str, str]:
    return {
        "Idempotency-Key": "opening-reconciliation-api-0001",
        "X-Request-ID": "opening-reconciliation-request-0001",
    }


def _start_body() -> dict[str, object]:
    return {"expected_task_version": 7}


def _explain_body() -> dict[str, object]:
    return {
        "expected_version": 1,
        "items": [
            {
                "reconciliation_item_id": str(ITEM_ID),
                "expected_version": 1,
                "explanation": "区域负责人核实为历史在途差额",
                "evidence_reference": "REGION-EVIDENCE-20260831-0001",
                "evidence_file_id": str(FILE_ID),
            }
        ],
    }


def _approve_body() -> dict[str, object]:
    return {
        "expected_version": 2,
        "comment": "总部复核证据完整，同意关闭门禁",
    }


def _start_result():
    return service.OpeningControlReconciliationStartResult(
        reconciliation_run_id=RUN_ID,
        task_id=TASK_ID,
        status="differences",
        version=0,
        item_count=2,
        created_at=NOW,
    )


def _explain_result():
    return service.OpeningControlReconciliationExplainResult(
        reconciliation_run_id=RUN_ID,
        task_id=TASK_ID,
        status="differences",
        version=2,
        explained_item_count=1,
        explained_at=NOW,
        replayed=False,
    )


def _approve_result():
    return service.OpeningControlReconciliationApproveResult(
        reconciliation_run_id=RUN_ID,
        task_id=TASK_ID,
        status="approved",
        version=3,
        resolved_item_count=1,
        approved_at=NOW,
    )


_RECONCILIATION_ACTIONS = [
    (
        "start",
        f"/api/v1/reconciliations/opening/tasks/{TASK_ID}",
        _start_body(),
        "start_opening_control_reconciliation",
        _start_result,
    ),
    (
        "explain",
        f"/api/v1/reconciliations/opening/{RUN_ID}/explanations",
        _explain_body(),
        "explain_opening_control_reconciliation",
        _explain_result,
    ),
    (
        "approve",
        f"/api/v1/reconciliations/opening/{RUN_ID}/approve",
        _approve_body(),
        "approve_opening_control_reconciliation",
        _approve_result,
    ),
]


_RECONCILIATION_412_CASES = [
    (*_RECONCILIATION_ACTIONS[0][:4], "opening_reconciliation_task_state_invalid"),
    (*_RECONCILIATION_ACTIONS[0][:4], "opening_reconciliation_not_required"),
    (*_RECONCILIATION_ACTIONS[1][:4], "opening_reconciliation_explain_state_invalid"),
    (*_RECONCILIATION_ACTIONS[1][:4], "opening_reconciliation_item_set_mismatch"),
    (*_RECONCILIATION_ACTIONS[2][:4], "opening_reconciliation_approve_state_invalid"),
    (*_RECONCILIATION_ACTIONS[2][:4], "opening_reconciliation_explanation_incomplete"),
]


def test_start_maps_server_owned_task_and_commits_once(
    api_client, monkeypatch
) -> None:
    client, db, principal = api_client
    captured: dict[str, object] = {}

    def fake_start(_db, **kwargs):
        captured.update(kwargs)
        return service.OpeningControlReconciliationStartResult(
            reconciliation_run_id=RUN_ID,
            task_id=TASK_ID,
            status="differences",
            version=0,
            item_count=2,
            created_at=NOW,
        )

    monkeypatch.setattr(service, "start_opening_control_reconciliation", fake_start)
    response = client.post(
        f"/api/v1/reconciliations/opening/tasks/{TASK_ID}",
        json={"expected_task_version": 7},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json() == {
        "schema_version": "1.0",
        "reconciliation_run_id": str(RUN_ID),
        "task_id": str(TASK_ID),
        "status": "differences",
        "version": 0,
        "item_count": 2,
        "created_at": "2026-08-31T11:30:00Z",
        "replayed": False,
    }
    assert captured["command"] == service.StartOpeningControlReconciliationCommand(
        task_id=TASK_ID,
        expected_task_version=7,
    )
    assert captured["idempotency_key"] == _headers()["Idempotency-Key"]
    assert captured["request_id"] == _headers()["X-Request-ID"]
    assert principal.calls == [("reconciliation", "create_opening")]
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_explain_maps_complete_item_evidence_and_replay_header(
    api_client, monkeypatch
) -> None:
    client, db, principal = api_client
    captured: dict[str, object] = {}

    def fake_explain(_db, **kwargs):
        captured.update(kwargs)
        return service.OpeningControlReconciliationExplainResult(
            reconciliation_run_id=RUN_ID,
            task_id=TASK_ID,
            status="differences",
            version=2,
            explained_item_count=1,
            explained_at=NOW,
            replayed=True,
        )

    monkeypatch.setattr(
        service,
        "explain_opening_control_reconciliation",
        fake_explain,
    )
    response = client.post(
        f"/api/v1/reconciliations/opening/{RUN_ID}/explanations",
        json={
            "expected_version": 1,
            "items": [
                {
                    "reconciliation_item_id": str(ITEM_ID),
                    "expected_version": 1,
                    "explanation": "区域负责人核实为历史在途差额",
                    "evidence_reference": "REGION-EVIDENCE-20260831-0001",
                    "evidence_file_id": str(FILE_ID),
                }
            ],
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "true"
    command = captured["command"]
    assert command == service.ExplainOpeningControlReconciliationCommand(
        reconciliation_run_id=RUN_ID,
        expected_version=1,
        items=(
            service.OpeningControlExplanationInput(
                reconciliation_item_id=ITEM_ID,
                expected_version=1,
                explanation="区域负责人核实为历史在途差额",
                evidence_reference="REGION-EVIDENCE-20260831-0001",
                evidence_file_id=FILE_ID,
            ),
        ),
    )
    assert principal.calls == [("reconciliation", "explain_opening")]
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_approve_maps_only_comment_and_expected_version(
    api_client, monkeypatch
) -> None:
    client, db, principal = api_client
    captured: dict[str, object] = {}

    def fake_approve(_db, **kwargs):
        captured.update(kwargs)
        return service.OpeningControlReconciliationApproveResult(
            reconciliation_run_id=RUN_ID,
            task_id=TASK_ID,
            status="approved",
            version=3,
            resolved_item_count=1,
            approved_at=NOW,
        )

    monkeypatch.setattr(
        service,
        "approve_opening_control_reconciliation",
        fake_approve,
    )
    response = client.post(
        f"/api/v1/reconciliations/opening/{RUN_ID}/approve",
        json={"expected_version": 2, "comment": "总部复核证据完整，同意关闭门禁"},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert captured["command"] == service.ApproveOpeningControlReconciliationCommand(
        reconciliation_run_id=RUN_ID,
        expected_version=2,
        comment="总部复核证据完整，同意关闭门禁",
    )
    assert principal.calls == [("reconciliation", "approve_opening")]
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("missing_header", ["Idempotency-Key", "X-Request-ID"])
def test_write_headers_are_mandatory_before_service_call(
    api_client, monkeypatch, missing_header: str
) -> None:
    client, db, _principal = api_client
    called = Mock()
    monkeypatch.setattr(service, "start_opening_control_reconciliation", called)
    headers = _headers()
    headers.pop(missing_header)

    response = client.post(
        f"/api/v1/reconciliations/opening/tasks/{TASK_ID}",
        json={"expected_task_version": 0},
        headers=headers,
    )

    assert response.status_code == 400
    called.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_service_error_rolls_back_and_hides_database_detail(
    api_client, monkeypatch
) -> None:
    client, db, _principal = api_client

    def reject(_db, **_kwargs):
        raise service.OpeningControlReconciliationError(
            "opening_reconciliation_version_conflict",
            "conflict",
            "对账运行版本已变化，请重新读取",
        )

    monkeypatch.setattr(service, "approve_opening_control_reconciliation", reject)
    response = client.post(
        f"/api/v1/reconciliations/opening/{RUN_ID}/approve",
        json={"expected_version": 2, "comment": "总部复核证据完整，同意关闭门禁"},
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "opening_reconciliation_version_conflict",
            "category": "conflict",
            "message": "对账运行版本已变化，请重新读取",
        }
    }
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()


def test_extra_write_fields_are_rejected_without_mutation(
    api_client, monkeypatch
) -> None:
    client, db, _principal = api_client
    called = Mock()
    monkeypatch.setattr(service, "approve_opening_control_reconciliation", called)

    response = client.post(
        f"/api/v1/reconciliations/opening/{RUN_ID}/approve",
        json={
            "expected_version": 2,
            "comment": "总部复核证据完整，同意关闭门禁",
            "status": "approved",
            "approved_by_user_id": "spoofed-user",
        },
        headers=_headers(),
    )

    assert response.status_code == 422
    called.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("invalid_version", [True, 1.0, "1"])
def test_expected_versions_are_strict_integers(
    api_client, monkeypatch, invalid_version: object
) -> None:
    client, db, _principal = api_client
    called = Mock()
    monkeypatch.setattr(service, "start_opening_control_reconciliation", called)

    response = client.post(
        f"/api/v1/reconciliations/opening/tasks/{TASK_ID}",
        json={"expected_task_version": invalid_version},
        headers=_headers(),
    )

    assert response.status_code == 422
    called.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("_action", "path", "body", "function_name", "_result_factory"),
    _RECONCILIATION_ACTIONS,
)
@pytest.mark.parametrize(
    ("headers", "expected_code"),
    [
        (
            {"X-Request-ID": "opening-reconciliation-request-0001"},
            "idempotency_key_invalid",
        ),
        (
            {
                "Idempotency-Key": "opening-reconciliation-api-0001",
                "X-Request-ID": "bad request",
            },
            "x_request_id_invalid",
        ),
    ],
)
def test_reconciliation_header_rejection_is_exact_and_never_enters_service(
    api_client,
    monkeypatch,
    _action,
    path,
    body,
    function_name,
    _result_factory,
    headers,
    expected_code,
) -> None:
    client, db, _principal = api_client
    called = Mock()
    monkeypatch.setattr(service, function_name, called)

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
    _RECONCILIATION_412_CASES,
)
def test_reconciliation_exact_precondition_rejection_rolls_back_before_response(
    api_client,
    monkeypatch,
    action,
    path,
    body,
    function_name,
    error_code,
) -> None:
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
        raise service.OpeningControlReconciliationError(
            error_code,
            "precondition_failed",
            "当前状态不允许执行该期初控制账对账操作",
        )

    db.rollback.side_effect = rollback
    monkeypatch.setattr(service, function_name, reject)

    response = client.post(path, json=body, headers=_headers())

    assert response.status_code == 412
    assert response.json()["detail"] == {
        "code": error_code,
        "category": "precondition_failed",
        "message": "当前状态不允许执行该期初控制账对账操作",
    }
    assert events == ["service", "rollback"]
    assert staged == []
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("_action", "path", "body", "function_name", "result_factory"),
    _RECONCILIATION_ACTIONS,
)
def test_reconciliation_commit_exception_is_not_translated_to_domain_4xx(
    api_client,
    monkeypatch,
    _action,
    path,
    body,
    function_name,
    result_factory,
) -> None:
    client, db, _principal = api_client
    monkeypatch.setattr(
        service,
        function_name,
        lambda *_args, **_kwargs: result_factory(),
    )
    db.commit.side_effect = RuntimeError("commit outcome unknown")

    with pytest.raises(RuntimeError, match="commit outcome unknown"):
        client.post(path, json=body, headers=_headers())

    db.commit.assert_called_once_with()
    db.rollback.assert_called_once_with()
