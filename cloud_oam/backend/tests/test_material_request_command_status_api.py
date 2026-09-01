from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main as main_module
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import material_request_command_status as status_service
from app.routers import formal_material_requests

from test_formal_material_request_api import (
    INSTANCE_ID,
    PERSON_ID,
    REQUEST_ID,
    REVISION_ID,
    _Db,
    _Principal,
)


TRACE_ID = "pc-command-status-trace-00000001"


def _states(request_status: str) -> dict[str, str]:
    return {
        "request_status": request_status,
        "allocation_status": "not_allocated",
        "reservation_status": "not_reserved",
        "outbound_status": "not_started",
        "shipment_status": "not_started",
        "logistics_signature_status": "not_signed",
        "oam_receipt_status": "not_occurred",
        "personal_inbound_status": "not_started",
        "notification_status": "not_started",
        "reconciliation_status": "not_started",
    }


@pytest.fixture()
def status_client():
    db = _Db()
    principal = _Principal()
    api = FastAPI()
    api.include_router(formal_material_requests.command_status_router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    with TestClient(api) as client:
        yield client, db, principal


@pytest.mark.parametrize("lookup_status", ("confirmed", "not_observed"))
def test_command_status_route_is_exact_read_only_and_no_store(
    status_client,
    monkeypatch: pytest.MonkeyPatch,
    lookup_status: str,
) -> None:
    client, db, principal = status_client
    captured = {}
    command = (
        status_service.MaterialRequestLifecycleCommandStatusCommand(
            action="withdraw",
            request_id=REQUEST_ID,
            request_version=7,
            revision_id=REVISION_ID,
            revision_no=1,
            approval_instance_id=INSTANCE_ID,
            approval_attempt_no=1,
            current_step_id=None,
            states=_states("withdrawn"),
            occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        )
        if lookup_status == "confirmed"
        else None
    )

    def fake_lookup(db_arg, *, actor, trace_request_id):
        captured.update(
            db=db_arg,
            actor=actor,
            trace_request_id=trace_request_id,
        )
        return status_service.MaterialRequestLifecycleCommandStatusResult(
            lookup_status=lookup_status,
            command=command,
        )

    monkeypatch.setattr(
        status_service,
        "material_request_lifecycle_command_status",
        fake_lookup,
    )
    response = client.get(
        "/api/v1/material-request-lifecycle-command-status",
        headers={"X-Request-ID": TRACE_ID},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.json()["schema_version"] == "1.0"
    assert response.json()["lookup_status"] == lookup_status
    if lookup_status == "confirmed":
        assert set(response.json()["command"]) == {
            "action",
            "request_id",
            "request_version",
            "revision_id",
            "revision_no",
            "approval_instance_id",
            "approval_attempt_no",
            "current_step_id",
            "states",
            "occurred_at",
        }
        assert response.json()["command"]["current_step_id"] is None
        assert "result_jsonb" not in response.text
        assert PERSON_ID.hex not in response.text
    else:
        assert response.json()["command"] is None
    assert captured == {
        "db": db,
        "actor": principal,
        "trace_request_id": TRACE_ID,
    }
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_command_status_framework_failure_is_also_private_no_store() -> None:
    with TestClient(main_module.app) as client:
        response = client.get(
            "/api/v1/material-request-lifecycle-command-status",
            headers={"X-Request-ID": TRACE_ID},
        )
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
