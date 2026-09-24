"""Actual acceptance/posting/HTTP evidence for receipt-scoped inbound state."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction
from app.stock_operation_models import StockOperationReceipt, StockOperationReturnInbound
from app.formal_services import stock_return_inbound_queries as queries
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError
from test_stock_return_inbound import (db,world,stock,recovered,destination,prepared,parcel,incoming,acceptance,
    inbound_accounts,accepted,command,commands,recovery,planning,posting,application,prefix,snapshot,submit,execute)


def state(db,accepted):
    return queries.read_return_inbound_state(db,actor=accepted.actor,receipt_id=accepted.receipt.receipt_id)


def test_http_state_proves_absence_then_actual_posting_without_request_marker(db,accepted):
    with TestClient(application(db,accepted.actor),raise_server_exceptions=False) as client:
        before=snapshot(db);response=client.get(prefix(accepted));assert response.status_code==200,response.text
        assert response.json()['status']=='not_posted' and response.json()['inbound'] is None
        assert snapshot(db)==before and 'no-store' in response.headers['cache-control']
        commands.execute_return_inbound(db,**command(db,accepted));db.commit();before=snapshot(db)
        response=client.get(prefix(accepted));assert response.status_code==200,response.text
        body=response.json();assert body['status']=='posted' and body['inbound']['posting_transaction_id']
        assert all(key not in response.text for key in ('request_hash','plan_hash','request_id','idempotency'))
        script="""const fs=require('node:fs'),c=require('./utils/stock-return-inbound-contract');
const data=JSON.parse(fs.readFileSync(0,'utf8'));c.validateState(data,{receiptId:data.receipt_id,shipmentId:data.shipment_id,personId:data.operator_person_id,authorizationVersion:data.authorization_version});"""
        node=subprocess.run(['node','-e',script],input=response.text,text=True,capture_output=True,
            cwd=Path(__file__).resolve().parents[2]/'miniprogram',timeout=30)
        assert node.returncode==0,node.stderr
        preview=client.post(prefix(accepted)+'/preview');assert preview.status_code==409,preview.text
        assert preview.json()['detail']['code']=='stock_return_inbound_receipt_already_posted'
        assert snapshot(db)==before


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_state_read_preserves_pending_outbox_and_uses_only_select(db,accepted):
    pending=OutboxEvent(event_type='synthetic_pending',aggregate_type='test',aggregate_id=uuid4().hex,payload_jsonb={},idempotency_key=uuid4().hex)
    db.add(pending);sql=[];connection=db.connection()
    def capture(_c,_cu,statement,*_):sql.append(statement.strip().split()[0].upper())
    event.listen(connection,'before_cursor_execute',capture)
    try:assert state(db,accepted)['status']=='not_posted'
    finally:event.remove(connection,'before_cursor_execute',capture)
    assert sql and set(sql)=={'SELECT'} and pending in db.new
    db.rollback()


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
@pytest.mark.parametrize('kind',['transaction','audit','outbox','state'])
def test_orphan_execution_evidence_cannot_be_reported_as_not_posted(db,accepted,kind):
    if kind=='transaction':
        plan=planning.plan_return_inbound(db,actor=accepted.actor,receipt_id=accepted.receipt.receipt_id)
        posting.post_inventory_transaction(db,actor=accepted.actor,command=plan['command'],idempotency_key=uuid4().hex,
            request_id=uuid4().hex,permission_resource='stock_operation',permission_action='receive_return')
    else:
        common=dict(aggregate_type='stock_operation_return_inbound',aggregate_id=str(uuid4()))
        body=dict(receipt_id=str(accepted.receipt.receipt_id));now=datetime.now(timezone.utc)
        if kind=='audit':append_audit_event(db,stream_key='material_request',actor_user_id=accepted.actor.user_id,
            action='stock_return_inbound_posted',**common,request_id=uuid4().hex,before_jsonb={},after_jsonb=body,occurred_at=now,created_at=now)
        elif kind=='outbox':db.add(OutboxEvent(**common,event_type='stock_return_inbound_posted',payload_jsonb=body,idempotency_key=uuid4().hex,available_at=now))
        else:db.add(StateTransitionEvent(**common,from_status=None,to_status='posted',actor_id=accepted.actor.user_id,
            reason='stock_return_inbound_posted',metadata_jsonb=body,idempotency_key=uuid4().hex,occurred_at=now))
    db.commit();before=snapshot(db)
    with TestClient(application(db,accepted.actor),raise_server_exceptions=False) as client:
        response=client.get(prefix(accepted));assert response.status_code==503,response.text
        assert 'no-store' in response.headers['cache-control']
    assert snapshot(db)==before


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_state_reproves_posted_fact_and_current_read_authority(db,stock,accepted):
    result=commands.execute_return_inbound(db,**command(db,accepted));db.commit()
    read_only=replace(accepted.actor,entitlements=tuple(row for row in accepted.actor.entitlements if row.action!='receive_return'))
    stock.world.current_principal=read_only
    assert queries.read_return_inbound_state(db,actor=read_only,receipt_id=accepted.receipt.receipt_id)['status']=='posted'
    out_of_scope=replace(read_only,entitlements=tuple(replace(row,scope_type='organization',scope_id=str(uuid4())) for row in read_only.entitlements))
    stock.world.current_principal=out_of_scope
    with pytest.raises((InventoryReadError,posting.InventoryPostingError)):
        queries.read_return_inbound_state(db,actor=out_of_scope,receipt_id=accepted.receipt.receipt_id)
    stock.world.current_principal=accepted.actor
    db.get(StockOperationReturnInbound,result['inbound_id']).request_hash='a'*64;db.commit()
    with TestClient(application(db,accepted.actor),raise_server_exceptions=False) as client:
        assert client.get(prefix(accepted)).status_code==503


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_state_is_per_acceptance_even_on_same_parcel_and_seals_stay_independent(db,acceptance,inbound_accounts):
    from types import SimpleNamespace
    contexts=[]
    for quantity in ('.375','.625'):
        request=acceptance.request.model_copy(update={'lines':(acceptance.request.lines[0].model_copy(update={'accepted_qty':Decimal(quantity)}),)})
        receipt=execute(db,acceptance,submit(db,acceptance,request));db.commit()
        contexts.append(SimpleNamespace(receipt=receipt,actor=acceptance.actor))
    first,second=contexts
    commands.execute_return_inbound(db,**command(db,first));db.commit()
    args=command(db,second)
    recovery.seal_return_inbound_request(db,actor=second.actor,receipt_id=second.receipt.receipt_id,request_id=args['request_id'],request_hash='a'*64);db.commit()
    assert state(db,first)['status']=='posted' and state(db,second)['status']=='not_posted'


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_state_cannot_mix_read_snapshots_when_audit_cursor_changes(db,accepted,monkeypatch):
    cursor=queries.material_audit_cursor(db);calls=iter((cursor,((cursor[0][0]+1,*cursor[0][1:]),)))
    monkeypatch.setattr(queries,'material_audit_cursor',lambda db:next(calls))
    with pytest.raises(InventoryReadError,match='核验期间变化'):state(db,accepted)
