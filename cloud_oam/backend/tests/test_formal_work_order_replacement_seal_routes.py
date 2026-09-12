"""Parent seal HTTP commits only proof and retains the old posted GET shape."""
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.routers import formal_work_order_material as api
from app.work_order_material_schemas import WorkOrderReplacementSealOut, WorkOrderReplacementSealedLookupOut
from test_formal_work_order_material_routes import http


def path(http):
    return f"/api/v1/work-orders/{http.order_id}/material-replacements/by-request/original-parent/seal"


def body(http):
    return {"operator_person_id":str(http.actor.person_id),"request_hash":"a"*64}


def response(http):
    return WorkOrderReplacementSealedLookupOut(seal=WorkOrderReplacementSealOut(seal_id=uuid4(),
        work_order_id=http.order_id,operator_person_id=http.actor.person_id,operation_type="replace",
        request_id="original-parent",request_hash="a"*64,sealed_at=datetime.now(timezone.utc)))


def test_parent_seal_and_get_share_exact_proof_without_child_operation_dispatch(http,monkeypatch):
    value=response(http);command=Mock(return_value=value)
    monkeypatch.setattr(api,"seal_replacement",command)
    result=http.client.post(path(http),json=body(http),headers={"X-Request-ID":"original-parent"})
    assert result.status_code==200 and result.json()==value.model_dump(mode="json")
    assert result.headers["cache-control"]=="private, no-store"
    http.db.commit.assert_called_once()
    assert command.call_args.kwargs["request_id"]=="original-parent"
    monkeypatch.setattr(api,"lookup_replacement",Mock(return_value=value))
    assert http.client.get(path(http).removesuffix("/seal")).json()==result.json()


@pytest.mark.parametrize("change",["operator","digest","trace","key","extra"])
def test_bad_parent_seal_coordinates_do_not_dispatch(http,monkeypatch,change):
    command=Mock(side_effect=AssertionError("must not dispatch"));monkeypatch.setattr(api,"seal_replacement",command)
    payload,headers=body(http),{}
    if change=="operator":payload["operator_person_id"]=str(uuid4())
    elif change=="digest":payload["request_hash"]="bad"
    elif change=="trace":headers["X-Request-ID"]="another-request"
    elif change=="key":headers["Idempotency-Key"]="another-key"
    else:payload["idempotency_key"]="unexpected"
    result=http.client.post(path(http),json=payload,headers=headers)
    assert result.status_code in {400,403,422};command.assert_not_called();http.db.commit.assert_not_called()


def test_uncertain_parent_seal_commit_does_not_return_completion(http,monkeypatch):
    monkeypatch.setattr(api,"seal_replacement",Mock(return_value=response(http)))
    http.db.commit.side_effect=SQLAlchemyError("unknown commit")
    result=http.client.post(path(http),json=body(http))
    assert result.status_code==503 and result.json()["detail"]["code"]=="work_order_seal_unavailable"
    assert "seal" not in result.json();http.db.rollback.assert_called_once()
