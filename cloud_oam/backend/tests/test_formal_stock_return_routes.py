"""HTTP coordinates and uncertain commit recovery for formal returns."""
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_db
from app.routers import formal_stock_returns as router
from test_stock_return_recovery import db, world, stock, recovered, destination, command
from test_stock_return_commands import _snapshot


@pytest.fixture
def client(db, stock):
    app = FastAPI(); app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal": app.dependency_overrides[dependency.call] = lambda: stock.world.current_principal
    with TestClient(app) as value: yield value


def path(coordinates):
    value = f"/api/v1/work-orders/{coordinates['work_order_id']}/returns"
    if coordinates["operation_type"] == "cancel_return": value += f"/{coordinates['operation_id']}/cancellations"
    if coordinates["operation_type"] == "outbound_return": value += f"/{coordinates['operation_id']}/outbounds"
    return value


@pytest.mark.parametrize("command", ["submit_return"], indirect=True)
def test_http_preview_keeps_exact_plan_without_posting(db, command, client):
    from app.stock_return_schemas import StockReturnPreviewIn
    value, coordinates, digest, _execute = command
    body = value.model_dump(mode="json", include=set(StockReturnPreviewIn.model_fields))
    before = _snapshot(db)
    response = client.post(path(coordinates) + "/preview", json=body)
    assert response.status_code == 200 and "no-store" in response.headers["cache-control"]
    assert response.json()["planning_status"] == "preview_only"
    assert response.json()["plan_hash"] == value.expected_plan_hash and response.json()["request_hash"] == digest
    assert "qr_code" not in response.text and _snapshot(db) == before


def test_committed_response_lost_recovers_by_original_get_without_replaying(db, stock, command, client, monkeypatch):
    value, coordinates, _digest, _execute = command
    url = path(coordinates)
    actual_commit = db.commit
    def lost_response():
        actual_commit()
        raise SQLAlchemyError("synthetic transport failure after commit")
    with monkeypatch.context() as context:
        context.setattr(db, "commit", lost_response)
        response = client.post(url, json=value.model_dump(mode="json"))
    assert response.status_code == 503 and "no-store" in response.headers["cache-control"]
    before = _snapshot(db)
    original = client.get(url + "/by-request/" + value.request_id)
    assert original.status_code == 200 and "no-store" in original.headers["cache-control"]
    assert original.json()["request_id"] == value.request_id
    assert "qr_code" not in original.text and "OPTIONS-QR" not in original.text
    assert _snapshot(db) == before


def test_absent_http_request_seal_blocks_late_post_and_changed_header(db, stock, command, client):
    value, coordinates, digest, _execute = command
    url = path(coordinates); lookup = url + "/by-request/" + value.request_id
    missing = client.get(lookup)
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "stock_return_not_observed"
    assert "no-store" in missing.headers["cache-control"]
    body = {"operator_person_id": str(stock.actor.person_id), "request_hash": digest}
    wrong = client.post(lookup + "/seal", json=body, headers={"Idempotency-Key": uuid4().hex})
    assert wrong.status_code == 400 and "no-store" in wrong.headers["cache-control"]
    sealed = client.post(lookup + "/seal", json=body, headers={"X-Request-ID": value.request_id})
    assert sealed.status_code == 200 and sealed.json()["lookup_status"] == "sealed"
    assert client.get(lookup).json() == sealed.json()
    before = _snapshot(db)
    late = client.post(url, json=value.model_copy(update={"idempotency_key": uuid4().hex}).model_dump(mode="json"))
    assert late.status_code == 409 and late.json()["detail"]["code"] == "stock_return_request_sealed"
    assert _snapshot(db) == before


def test_http_command_rejects_changed_original_coordinates_before_writing(db, command, client):
    value, coordinates, _digest, _execute = command
    before = _snapshot(db)
    for header in ("X-Request-ID", "Idempotency-Key"):
        denied = client.post(path(coordinates), json=value.model_dump(mode="json"), headers={header: uuid4().hex})
        assert denied.status_code == 400 and "no-store" in denied.headers["cache-control"]
        assert _snapshot(db) == before


@pytest.mark.parametrize("command", ["cancel_return"], indirect=True)
def test_cancel_path_must_match_original_work_order(db, stock, command, client):
    value, coordinates, _digest, _execute = command
    wrong = path({**coordinates, "work_order_id": stock.orders[1].id})
    before = _snapshot(db)
    denied = client.post(wrong, json=value.model_dump(mode="json"))
    assert denied.status_code == 404 and denied.json()["detail"]["code"] == "stock_return_not_found"
    assert _snapshot(db) == before


@pytest.mark.parametrize("failure", ["auth", "body", "path", "method", "route"])
def test_production_route_framework_failures_are_private_and_do_not_echo_scans(monkeypatch, failure):
    from app.main import app
    from fastapi import HTTPException
    from types import SimpleNamespace
    overrides = {}
    def principal():
        if failure == "auth": raise HTTPException(status_code=401, detail="需要登录")
        return SimpleNamespace(person_id=uuid4())
    overrides[get_db] = lambda: None
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal": overrides[dependency.call] = principal
    monkeypatch.setattr(app, "dependency_overrides", overrides)
    path = f"/api/v1/work-orders/{uuid4()}/returns"
    if failure == "path": path = "/api/v1/work-orders/invalid-order/returns"
    if failure == "route": path += "/unknown/route"
    # Exercise the real routes/middleware without running database startup.
    http = TestClient(app, raise_server_exceptions=False)
    try:
        response = http.request("DELETE" if failure == "method" else "POST", path,
            json={"operator_person_id": "INVALID", "lines": [{"qr_code": "PRIVATE-PHYSICAL-SCAN"}]})
    finally:
        http.close()
    assert response.status_code == {"auth": 401, "body": 422, "path": 422, "method": 405, "route": 404}[failure]
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "PRIVATE-PHYSICAL-SCAN" not in response.text and "qr_code" not in response.text
