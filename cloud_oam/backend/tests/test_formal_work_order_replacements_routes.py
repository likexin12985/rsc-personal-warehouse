"""HTTP uncertainty, permission and request-recovery contracts."""
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.routers import formal_work_order_material as api
from app.work_order_material_schemas import WorkOrderReplacementOut, WorkOrderReplacementRecoveredOut
from test_formal_work_order_material_routes import http


def payload(http):
    sku,account=uuid4(),uuid4()
    return {"operator_person_id":str(http.actor.person_id),
        "consume_lines":[{"material_id":str(sku),"stock_account_id":str(account),"quantity":"1"}],
        "recover_lines":[{"material_id":str(sku),"basis_stock_account_id":str(account),"quantity":"1","condition_before":"used"}],
        "idempotency_key":"replacement-http-key","request_id":"replacement-http-request"}


def path(http):
    return f"/api/v1/work-orders/{http.order_id}/material-replacements"


def output(http):
    return WorkOrderReplacementOut(replacement_id=uuid4(),replacement_no="WOR-HTTP",work_order_id=http.order_id,
        consume_operation_id=uuid4(),recover_operation_id=uuid4(),consume_transaction_id=uuid4(),recover_transaction_id=uuid4())


@pytest.mark.parametrize("error",["operator","header","new_recovery","extra"])
def test_replacement_transport_rejects_wrong_identity_or_ambiguous_intent(http,monkeypatch,error):
    execute=Mock();monkeypatch.setattr(api.replacements,"execute_replacement",execute)
    body=payload(http);headers={}
    if error=="operator": body["operator_person_id"]=str(uuid4())
    elif error=="header": headers["X-Request-ID"]="different-request"
    elif error=="new_recovery": body["recover_lines"][0]["condition_before"]="new"
    else: body["consume_transaction_id"]=str(uuid4())
    response=http.client.post(path(http),json=body,headers=headers)
    assert response.status_code=={"operator":403,"header":400,"new_recovery":422,"extra":422}[error]
    execute.assert_not_called();http.db.commit.assert_not_called()


def test_replacement_success_preserves_intent_and_returns_two_transactions_after_commit(http,monkeypatch):
    fact=object();result=output(http);execute=Mock(return_value=fact)
    monkeypatch.setattr(api.replacements,"execute_replacement",execute)
    monkeypatch.setattr(api,"replacement_result",Mock(return_value=result))
    response=http.client.post(path(http),json=payload(http))
    assert response.status_code==200 and response.json()==result.model_dump(mode="json")
    args=execute.call_args.kwargs
    assert args["recover_lines"][0].basis_stock_account_id==args["consume_lines"][0].stock_account_id
    assert args["recover_lines"][0].target_stock_account_id is None
    http.db.commit.assert_called_once()


def test_uncertain_commit_returns_no_success_and_requires_original_request_read(http,monkeypatch):
    monkeypatch.setattr(api.replacements,"execute_replacement",Mock(return_value=object()))
    monkeypatch.setattr(api,"replacement_result",Mock(return_value=output(http)))
    http.db.commit.side_effect=SQLAlchemyError("connection lost")
    response=http.client.post(path(http),json=payload(http))
    assert response.status_code==503
    assert response.json()["detail"]["code"]=="work_order_storage_unavailable"
    assert "replacement_id" not in response.json()
    http.db.rollback.assert_called_once()


def test_read_by_request_only_reports_verified_original_result_without_writes(http,monkeypatch):
    result=WorkOrderReplacementRecoveredOut(**output(http).model_dump(), operator_person_id=http.actor.person_id,
        request_id="replacement-http-request", request_hash="a"*64)
    verify=Mock(return_value=result);monkeypatch.setattr(api,"lookup_replacement",verify)
    response=http.client.get(path(http)+"/by-request/replacement-http-request")
    assert response.status_code==200 and response.json()==result.model_dump(mode="json")
    assert response.headers["cache-control"]=="private, no-store"
    verify.assert_called_once_with(http.db,actor=http.actor,work_order_id=http.order_id,request_id="replacement-http-request")
    http.db.commit.assert_not_called();http.db.add.assert_not_called()
    verify.return_value=None
    absent=http.client.get(path(http)+"/by-request/replacement-absent")
    assert absent.status_code==404
    http.db.commit.assert_not_called()
