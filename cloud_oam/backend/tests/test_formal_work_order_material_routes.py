"""HTTP validation/dispatch contracts; ledger behavior has separate DB tests."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers import formal_work_order_material as api


@pytest.fixture
def http():
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    actor = SimpleNamespace(person_id=uuid4(), user_id=str(uuid4()))
    db = Mock(spec=Session)
    app.dependency_overrides[get_db] = lambda: db
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal":
                app.dependency_overrides[dependency.call] = lambda: actor
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, actor=actor, db=db, order_id=uuid4())


def payload_for(http, operation):
    line = {"material_id": str(uuid4()), "quantity": "1", "condition_before": "used"}
    if operation != "recover":
        line["stock_account_id"] = str(uuid4())
    if operation != "consume":
        line["target_stock_account_id"] = str(uuid4())
    return {"operator_person_id": str(http.actor.person_id), "lines": [line],
            "idempotency_key": "http-command-key", "request_id": "http-command-trace"}


def path_for(http, operation):
    return f"/api/v1/work-orders/{http.order_id}/material-operations/{operation}"


@pytest.mark.parametrize("operation", ["consume", "release", "occupy", "recover"])
def test_operator_mismatch_is_rejected_before_command_dispatch(http, monkeypatch, operation):
    command = Mock(side_effect=AssertionError("must not dispatch another operator's payload"))
    monkeypatch.setattr(api.service, f"execute_{operation}_operation", command)
    payload = payload_for(http, operation)
    payload["operator_person_id"] = str(uuid4())
    response = http.client.post(path_for(http, operation), json=payload)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "operator_mismatch"
    command.assert_not_called()
    http.db.commit.assert_not_called()


@pytest.mark.parametrize("operation", ["consume", "release", "occupy", "recover"])
def test_http_dispatch_preserves_target_and_returns_operation(http, monkeypatch, operation):
    fact = SimpleNamespace(id=uuid4(), operation_no="WO-TEST", oam_work_order_id=http.order_id,
                           posting_transaction_id=uuid4(), operation_type=operation, status="posted")
    command = Mock(return_value=(fact, SimpleNamespace(transaction_id=fact.posting_transaction_id)))
    monkeypatch.setattr(api.service, f"execute_{operation}_operation", command)
    payload = payload_for(http, operation)
    response = http.client.post(path_for(http, operation), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["operation_id"] == str(fact.id)
    assert response.json()["operation_type"] == operation
    kwargs = command.call_args.kwargs
    assert kwargs["actor"] is http.actor
    assert kwargs["work_order_id"] == http.order_id
    if operation == "recover":
        target = payload["lines"][0]["target_stock_account_id"]
        assert str(kwargs["lines"][0].stock_account_id) == target
        assert str(kwargs["lines"][0].target_stock_account_id) == target
    http.db.commit.assert_called_once_with()


@pytest.mark.parametrize("change", ["new", "scrapped", "missing_condition", "source_account", "missing_target"])
def test_recover_rejects_non_return_conditions_and_ambiguous_accounts(http, monkeypatch, change):
    command = Mock(side_effect=AssertionError("invalid recover input reached service"))
    monkeypatch.setattr(api.service, "execute_recover_operation", command)
    payload = payload_for(http, "recover")
    line = payload["lines"][0]
    if change in {"new", "scrapped"}:
        line["condition_before"] = change
    elif change == "missing_condition":
        line.pop("condition_before")
    elif change == "source_account":
        line["stock_account_id"] = str(uuid4())
    else:
        line.pop("target_stock_account_id")
    response = http.client.post(path_for(http, "recover"), json=payload)
    assert response.status_code == 422
    command.assert_not_called()
    http.db.commit.assert_not_called()


def test_openapi_binds_release_and_recover_to_their_own_models(http):
    spec = http.app.openapi()
    for operation, model in (("release", "WorkOrderMaterialReleaseIn"), ("recover", "WorkOrderMaterialRecoverIn")):
        path = f"/api/v1/work-orders/{{work_order_id}}/material-operations/{operation}"
        schema = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert schema["$ref"] == f"#/components/schemas/{model}"


def test_openapi_constrains_operation_response_enums(http):
    output = http.app.openapi()["components"]["schemas"]["WorkOrderMaterialOperationOut"]
    assert output["properties"]["operation_type"]["enum"] == ["occupy", "release", "consume", "recover", "reverse"]
    status = output["properties"]["status"]
    assert status.get("enum", [status.get("const")]) == ["posted"]


def test_recover_database_failure_rolls_back_without_success_response(http, monkeypatch):
    monkeypatch.setattr(api.service, "execute_recover_operation", Mock(side_effect=SQLAlchemyError("private detail")))
    response = http.client.post(path_for(http, "recover"), json=payload_for(http, "recover"))
    assert response.status_code == 503
    assert "private detail" not in response.text
    http.db.rollback.assert_called_once_with()
    http.db.commit.assert_not_called()
