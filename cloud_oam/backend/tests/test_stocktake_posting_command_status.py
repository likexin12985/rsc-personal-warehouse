from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.routers import formal_stocktakes

from app.formal_services import stocktake_posting_command_status as service
from app.stocktake_posting_command_status_schemas import StocktakePostingCommandStatusOut
from test_stocktake_difference_service import world
from test_stocktake_review_recount_service import _fixed_clocks, review_world
from test_stocktake_safe_posting_service import _approve, _post, posting_world
from test_stocktake_task_service import db


def _lookup(world, task, trace_request_id: str):
    actor = world.principals["admin"]
    return service.stocktake_posting_command_status(
        world.db,
        actor=actor,
        task_id=task.id,
        actor_person_id=actor.person_id,
        actor_authorization_version=actor.authorization_version,
        trace_request_id=trace_request_id,
    )


def test_unseen_trace_is_not_observed_and_does_not_claim_nonexecution(posting_world, monkeypatch):
    task, _ = _approve(
        posting_world,
        monkeypatch,
        key="posting-status-unseen",
        counted_qty=Decimal("4.000"),
        decision="no_adjustment",
    )
    result = _lookup(posting_world, task, "trace-posting-never-observed")
    assert result.lookup_status == "not_observed"
    assert result.command is None
    assert "not_executed" not in result.model_dump_json()


def test_exact_trace_reconstructs_only_persisted_posting_fact(posting_world, monkeypatch):
    task, _ = _approve(
        posting_world,
        monkeypatch,
        key="posting-status-confirmed",
        counted_qty=Decimal("4.000"),
        decision="no_adjustment",
    )
    _post(posting_world, task, key="posting-status-confirmed-post")
    result = _lookup(posting_world, task, "trace-posting-status-confirmed-post")
    assert isinstance(result, StocktakePostingCommandStatusOut)
    assert result.lookup_status == "confirmed"
    assert result.command is not None
    assert result.command.resulting_task_status == "posted"
    assert result.command.task_id == task.id
    assert result.command.transaction_count == 0
    assert result.command.total_quantity == "0.000"
    assert "idempotency_key" not in result.model_dump_json()


def test_actor_version_drift_fails_closed(posting_world, monkeypatch):
    task, _ = _approve(
        posting_world,
        monkeypatch,
        key="posting-status-identity-drift",
        counted_qty=Decimal("4.000"),
        decision="no_adjustment",
    )
    actor = posting_world.principals["admin"]
    drifted = replace(actor, authorization_version=actor.authorization_version + 1)
    with pytest.raises(service.StocktakePostingCommandStatusError) as caught:
        service.stocktake_posting_command_status(
            posting_world.db,
            actor=actor,
            task_id=task.id,
            actor_person_id=actor.person_id,
            actor_authorization_version=drifted.authorization_version,
            trace_request_id="trace-posting-identity-drift",
        )
    assert caught.value.http_status_code == 412


def test_get_does_not_mutate_caller_transaction(posting_world, monkeypatch):
    task, _ = _approve(
        posting_world,
        monkeypatch,
        key="posting-status-read-only",
        counted_qty=Decimal("4.000"),
        decision="no_adjustment",
    )
    _post(posting_world, task, key="posting-status-read-only-post")
    posting_world.db.commit()
    task_id = task.id
    for method in ("commit", "rollback", "flush"):
        monkeypatch.setattr(posting_world.db, method, Mock(side_effect=AssertionError(method)))
    result = service.stocktake_posting_command_status(
        posting_world.db,
        actor=posting_world.principals["admin"],
        task_id=task_id,
        actor_person_id=posting_world.principals["admin"].person_id,
        actor_authorization_version=posting_world.principals["admin"].authorization_version,
        trace_request_id="trace-posting-status-read-only-post",
    )
    assert result.lookup_status == "confirmed"


@pytest.fixture
def posting_status_api(posting_world, monkeypatch):
    task, _ = _approve(
        posting_world,
        monkeypatch,
        key="posting-status-api",
        counted_qty=Decimal("4.000"),
        decision="no_adjustment",
    )
    _post(posting_world, task, key="posting-status-api-post")
    posting_world.db.commit()
    actor = posting_world.principals["admin"]
    result = _lookup(posting_world, task, "trace-posting-status-api-post")
    stub = Mock(return_value=result)
    monkeypatch.setattr(service, "stocktake_posting_command_status", stub)
    api = FastAPI()
    api.include_router(formal_stocktakes.router, prefix="/api")
    fake_db = SimpleNamespace(commit=Mock(), rollback=Mock(), flush=Mock())
    api.dependency_overrides[get_db] = lambda: fake_db
    api.dependency_overrides[get_formal_principal] = lambda: actor
    params = {
        "actor_person_id": str(actor.person_id),
        "actor_authorization_version": str(actor.authorization_version),
        "trace_request_id": "trace-posting-status-api-post",
    }
    path = f"/api/v1/stocktakes/{task.id}/post-differences-command-status"
    with TestClient(api) as client:
        yield SimpleNamespace(client=client, path=path, params=params, result=result, stub=stub, db=fake_db)


def test_api_posting_status_is_private_and_exactly_read_only(posting_status_api):
    state = posting_status_api
    response = state.client.get(state.path, params=state.params)
    assert response.status_code == 200
    assert response.json() == state.result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    for call in (state.db.commit, state.db.rollback, state.db.flush):
        call.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    ["body", "idempotency_key", "duplicate", "request_jsonb"],
)
def test_api_posting_status_rejects_replay_material(posting_status_api, kind):
    state = posting_status_api
    kwargs = {"params": state.params}
    if kind == "body":
        kwargs["content"] = b'{"expected_task_version":1}'
    elif kind == "idempotency_key":
        kwargs["headers"] = {"Idempotency-Key": "must-not-be-sent"}
    elif kind == "duplicate":
        kwargs["params"] = [*state.params.items(), ("trace_request_id", "another-valid-trace")]
    else:
        kwargs["params"] = {**state.params, "request_jsonb": "forbidden"}
    response = state.client.request("GET", state.path, **kwargs)
    assert response.status_code == 400
    state.stub.assert_not_called()
