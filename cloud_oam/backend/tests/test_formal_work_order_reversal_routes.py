"""Public reversal dispatch preserves coordinates and uncertain commit results."""
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.routers import formal_work_order_material as api
from app.work_order_reversal_schemas import WorkOrderReversalOut, WorkOrderReversalResultItemOut, WorkOrderReversalSealOut, WorkOrderReversalSealedOut
from test_formal_work_order_material_routes import http


def path(http):
    return f"/api/v1/work-orders/{http.order_id}/material-reversals"


def body(http):
    return dict(operator_person_id=str(http.actor.person_id), original_operation_id=str(uuid4()), reason="录入错误，整批冲销",
        expected_plan_hash="a"*64, request_id="original-reversal", idempotency_key="original-reversal-key")


def posted(http):
    return WorkOrderReversalOut(reversal_id=uuid4(), reversal_no="WOV-EXAMPLE", work_order_id=http.order_id,
        operator_person_id=http.actor.person_id, request_id="original-reversal", request_hash="b"*64, plan_hash="a"*64,
        original_operation_id=uuid4(), original_replacement_id=None, reason="录入错误，整批冲销", posted_at=datetime.now(timezone.utc),
        items=(WorkOrderReversalResultItemOut(original_operation_id=uuid4(), original_transaction_id=uuid4(),
            inverse_operation_id=uuid4(), inverse_transaction_id=uuid4()),))


def sealed(http):
    return WorkOrderReversalSealedOut(seal=WorkOrderReversalSealOut(seal_id=uuid4(), work_order_id=http.order_id,
        operator_person_id=http.actor.person_id, operation_type="reverse", request_id="original-reversal",
        request_hash="b"*64, sealed_at=datetime.now(timezone.utc)))


def test_submit_then_read_returns_exact_whole_result(http, monkeypatch):
    result = posted(http); command = Mock(return_value=result); monkeypatch.setattr(api,"execute_reversal",command)
    response = http.client.post(path(http), json=body(http), headers={"X-Request-ID":"original-reversal"})
    assert response.status_code == 200 and response.json() == result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store"
    http.db.commit.assert_called_once()
    assert command.call_args.kwargs["request"].expected_plan_hash == "a"*64
    http.db.reset_mock(); monkeypatch.setattr(api,"lookup_reversal",Mock(return_value=result))
    response = http.client.get(path(http)+"/by-request/original-reversal")
    assert response.status_code == 200 and response.json() == result.model_dump(mode="json")
    http.db.commit.assert_not_called(); http.db.rollback.assert_not_called()


@pytest.mark.parametrize("kind",["posted","sealed"])
def test_seal_and_lookup_resolve_only_one_original_result(http, monkeypatch, kind):
    result = posted(http) if kind == "posted" else sealed(http)
    command = Mock(return_value=result); monkeypatch.setattr(api,"seal_reversal",command)
    response = http.client.post(path(http)+"/by-request/original-reversal/seal",
        json={"operator_person_id":str(http.actor.person_id),"request_hash":"b"*64})
    assert response.status_code == 200 and response.json() == result.model_dump(mode="json")
    http.db.commit.assert_called_once()
    monkeypatch.setattr(api,"lookup_reversal",Mock(return_value=result))
    assert http.client.get(path(http)+"/by-request/original-reversal").json() == response.json()


@pytest.mark.parametrize("change",["operator","trace","key","plan","both","reason","extra"])
def test_bad_submit_never_dispatches(http,monkeypatch,change):
    command=Mock();monkeypatch.setattr(api,"execute_reversal",command)
    payload,headers=body(http),{}
    if change=="operator": payload["operator_person_id"]=str(uuid4())
    elif change=="trace": headers["X-Request-ID"]="another-request"
    elif change=="key": headers["Idempotency-Key"]="another-key"
    elif change=="plan": payload["expected_plan_hash"]="bad"
    elif change=="both": payload["original_replacement_id"]=str(uuid4())
    elif change=="reason": payload["reason"]="  "
    else: payload["quantity"]=999
    response=http.client.post(path(http),json=payload,headers=headers)
    assert response.status_code in {400,403,422}
    command.assert_not_called();http.db.commit.assert_not_called()


@pytest.mark.parametrize("kind",["submit","seal"])
def test_uncertain_commit_returns_no_completion_and_keeps_original_coordinates(http,monkeypatch,kind):
    monkeypatch.setattr(api,"execute_reversal",Mock(return_value=posted(http)))
    monkeypatch.setattr(api,"seal_reversal",Mock(return_value=sealed(http)))
    http.db.commit.side_effect=SQLAlchemyError("unknown commit; must not expose database text")
    payload=body(http) if kind=="submit" else {"operator_person_id":str(http.actor.person_id),"request_hash":"b"*64}
    url=path(http) if kind=="submit" else path(http)+"/by-request/original-reversal/seal"
    response=http.client.post(url,json=payload)
    assert response.status_code==503 and "unknown commit" not in response.text
    assert "reversal_id" not in response.json() and "seal" not in response.json()
    http.db.rollback.assert_called_once()


def test_missing_lookup_is_provisional_without_replaying_or_committing(http,monkeypatch):
    monkeypatch.setattr(api,"lookup_reversal",Mock(return_value=None))
    command=Mock();monkeypatch.setattr(api,"execute_reversal",command)
    response=http.client.get(path(http)+"/by-request/original-reversal")
    assert response.status_code==404 and response.json()["detail"]["code"]=="work_order_reversal_not_observed"
    assert response.headers["cache-control"] == "private, no-store"
    command.assert_not_called();http.db.commit.assert_not_called()


@pytest.mark.parametrize("headers",[{"X-Request-ID":"another-request"},{"Idempotency-Key":"another-key"}])
def test_seal_rejects_alternative_coordinates(http,monkeypatch,headers):
    command=Mock();monkeypatch.setattr(api,"seal_reversal",command)
    response=http.client.post(path(http)+"/by-request/original-reversal/seal",headers=headers,
        json={"operator_person_id":str(http.actor.person_id),"request_hash":"b"*64})
    assert response.status_code==400;command.assert_not_called();http.db.commit.assert_not_called()
