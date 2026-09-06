"""Contract tests for the non-opening review status reader.

The API route and PostgreSQL fixtures are added in the next integration slice.
These tests keep the service/schema contract executable in the meantime and
make it difficult to accidentally turn the recovery reader into a replay or a
write path.
"""

import ast
import inspect
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import stocktake_review_command_status as service
from app.routers import formal_stocktakes
from app.stocktake_review_command_status_schemas import (
    StocktakeReviewCommandStatusOut,
    StocktakeReviewHistoricalCommandOut,
)


def test_status_reader_is_explicitly_read_only_and_fail_closed():
    source = inspect.getsource(service.stocktake_review_command_status)
    tree = ast.parse(source)
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    ]
    assert "commit" not in calls
    assert "flush" not in calls
    assert "rollback" not in calls
    assert "add" not in calls
    assert "append_audit_event" not in source
    assert "lock_nonopening_stocktake_review_graph" in source
    assert "_lock_audit_chain_head_with_proof" in source
    assert "_reread_actor_or_error" in source


def test_status_reader_never_accepts_an_idempotency_key_or_replays():
    source = inspect.getsource(service.stocktake_review_command_status)
    assert "idempotency_key" not in source
    assert "_submit_review" not in source
    assert "submit_stocktake_region_review" not in source
    assert "submit_stocktake_headquarters_review" not in source


def test_schema_requires_coordinates_and_distinguishes_not_observed():
    actor_person_id = uuid.uuid4()
    task_id = uuid.uuid4()
    round_id = uuid.uuid4()
    not_observed = StocktakeReviewCommandStatusOut(
        task_id=task_id,
        round_id=round_id,
        review_stage="region",
        actor_person_id=actor_person_id,
        actor_authorization_version=1,
        trace_request_id="trace-review-status-1",
        lookup_status="not_observed",
        command=None,
    )
    assert not_observed.lookup_status == "not_observed"
    assert not_observed.command is None

    with pytest.raises(ValueError):
        StocktakeReviewCommandStatusOut(
            task_id=task_id,
            round_id=round_id,
            review_stage="region",
            actor_person_id=actor_person_id,
            actor_authorization_version=1,
            trace_request_id="trace-review-status-2",
            lookup_status="confirmed",
            command=None,
        )


def test_historical_schema_enforces_contiguous_versions_and_readiness():
    values = {
        "review_id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "round_id": uuid.uuid4(),
        "review_stage": "headquarters",
        "decision": "approve",
        "resulting_task_status": "approved",
        "expected_task_version": 7,
        "resulting_task_version": 8,
        "task_version": 8,
        "item_count": 1,
        "pending_verification_count": 0,
        "ready_for_posting": True,
        "reviewed_at": "2026-09-06T00:00:00+00:00",
    }
    historical = StocktakeReviewHistoricalCommandOut(**values)
    assert historical.resulting_task_version == historical.expected_task_version + 1

    with pytest.raises(ValueError):
        StocktakeReviewHistoricalCommandOut(
            **{**values, "resulting_task_version": 9, "task_version": 9}
        )


@pytest.fixture
def review_status_api(monkeypatch):
    task_id = uuid.uuid4()
    round_id = uuid.uuid4()
    actor_person_id = uuid.uuid4()
    result = StocktakeReviewCommandStatusOut(
        task_id=task_id,
        round_id=round_id,
        review_stage="region",
        actor_person_id=actor_person_id,
        actor_authorization_version=4,
        trace_request_id="trace-review-route-1",
        lookup_status="not_observed",
        command=None,
    )
    principal = SimpleNamespace(
        user_id="review-user-1",
        person_id=actor_person_id,
        authorization_version=4,
        allows=lambda *_args, **_kwargs: True,
    )
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None, flush=lambda: None)
    called = []

    def stub(*args, **kwargs):
        called.append((args, kwargs))
        return result

    monkeypatch.setattr(service, "stocktake_review_command_status", stub)
    api = FastAPI()
    api.include_router(formal_stocktakes.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    with TestClient(api) as client:
        yield SimpleNamespace(
            client=client,
            db=db,
            called=called,
            result=result,
            path=f"/api/v1/stocktakes/{task_id}/rounds/{round_id}/reviews/region/command-status",
            params={
                "actor_person_id": str(actor_person_id),
                "actor_authorization_version": "4",
                "trace_request_id": "trace-review-route-1",
            },
        )


def test_review_status_route_is_private_and_read_only(review_status_api):
    state = review_status_api
    response = state.client.get(state.path, params=state.params)
    assert response.status_code == 200
    assert response.json() == state.result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert len(state.called) == 1
    assert state.called[0][1]["review_stage"] == "region"


@pytest.mark.parametrize(
    "kind",
    ["body", "idempotency_key", "duplicate", "unknown"],
)
def test_review_status_route_rejects_replay_material(review_status_api, kind):
    state = review_status_api
    kwargs = {"params": state.params}
    if kind == "body":
        kwargs["content"] = b'{"decision":"approve"}'
    elif kind == "idempotency_key":
        kwargs["headers"] = {"Idempotency-Key": "must-not-be-sent"}
    elif kind == "duplicate":
        kwargs["params"] = [*state.params.items(), ("trace_request_id", "another-trace")]
    else:
        kwargs["params"] = {**state.params, "request_jsonb": "forbidden"}
    response = state.client.request("GET", state.path, **kwargs)
    assert response.status_code == 400
    assert state.called == []
