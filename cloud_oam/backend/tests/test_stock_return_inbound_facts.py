"""Recover a posted return only from its complete immutable business graph."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select, text

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryMovement, InventoryMovementSerial, InventoryTransaction, StockBalance, SerialCurrentPosition
from app.stock_operation_models import (StockOperationReturnInbound, StockOperationReturnInboundLine,
    StockOperationReturnInboundSerial, StockOperationReturnInboundPosting)
from app.formal_services import stock_return_inbound_facts as facts
from app.formal_services import inventory_posting as posting
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.work_order_return_sources import _hash
from test_stock_return_inbound import (db,world,stock,recovered,destination,prepared,parcel,incoming,acceptance,
    inbound_accounts,accepted,command,commands,recovery,application,prefix,snapshot)


@pytest.fixture
def posted(db,accepted):
    args=command(db,accepted);result=commands.execute_return_inbound(db,**args);db.commit()
    return args,result


def _request(args):return {key:args[key] for key in ('actor','receipt_id','request_id')}


def _rehash(row):
    row.plan_hash=_hash(row.plan_jsonb)
    row.request_hash=commands._request_hash(receipt_id=row.receipt_id,request_id=row.request_id,plan_hash=row.plan_hash)


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
@pytest.mark.parametrize('change',['receipt_line_missing','posting_link_missing','quantity_rehashed','target_rehashed',
    'source_rehashed','plan_cursor_rehashed','authorization_rehashed','request_hash','audit_version','command',
    'movement_quantity','movement_target','transaction_source','domain_outbox_missing','domain_outbox_payload',
    'domain_state_payload','inventory_outbox_payload','extra_inventory_state','extra_inventory_outbox','domain_audit_missing'])
def test_corrupt_graph_never_looks_posted_or_replayed_or_completed_sealed(db,accepted,posted,recovered,change):
    args,result=posted;row=db.get(StockOperationReturnInbound,result['inbound_id'])
    line=db.scalar(select(StockOperationReturnInboundLine).where(StockOperationReturnInboundLine.inbound_id==row.id))
    tx=db.get(InventoryTransaction,row.posting_transaction_id)
    movement=db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id==tx.id))
    if change=='receipt_line_missing':db.delete(line)
    elif change=='posting_link_missing':db.execute(delete(StockOperationReturnInboundPosting).where(StockOperationReturnInboundPosting.inbound_id==row.id))
    elif change in ('quantity_rehashed','target_rehashed','source_rehashed'):
        plan=deepcopy(row.plan_jsonb)
        if change=='quantity_rehashed':line.accepted_qty=Decimal('.500');plan['lines'][0]['accepted_qty']='0.500'
        elif change=='target_rehashed':line.target_account_id=recovered.account.id;plan['lines'][0]['target_account_id']=str(recovered.account.id)
        else:line.source_account_id=recovered.account.id;plan['lines'][0]['source_account_id']=str(recovered.account.id)
        row.plan_jsonb=plan;_rehash(row)
    elif change=='plan_cursor_rehashed':row.plan_jsonb={**row.plan_jsonb,'ledger_cursor':0};_rehash(row)
    elif change=='authorization_rehashed':row.authorization_version+=1;row.plan_jsonb={**row.plan_jsonb,'authorization_version':row.authorization_version};_rehash(row)
    elif change=='request_hash':row.request_hash='a'*64
    elif change=='audit_version':row.audit_version+=1
    elif change=='command':row.command_jsonb={**row.command_jsonb,'effective_at':datetime.now(timezone.utc).isoformat()}
    elif change=='movement_quantity':movement.quantity=Decimal('.500')
    elif change=='movement_target':movement.to_account_id=recovered.account.id
    elif change=='transaction_source':tx.source_document_type='synthetic_unbound_inbound'
    elif change in ('domain_outbox_missing','domain_outbox_payload','inventory_outbox_payload'):
        box=db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type==('inventory_transaction' if change.startswith('inventory') else 'stock_operation_return_inbound'),
            OutboxEvent.aggregate_id==str(tx.id if change.startswith('inventory') else row.id)))
        if change=='domain_outbox_missing':db.delete(box)
        else:box.payload_jsonb={**box.payload_jsonb,'synthetic_extra':True}
    elif change=='domain_state_payload':
        state=db.scalar(select(StateTransitionEvent).where(StateTransitionEvent.aggregate_type=='stock_operation_return_inbound',StateTransitionEvent.aggregate_id==str(row.id)))
        state.metadata_jsonb={**state.metadata_jsonb,'status':'accepted'}
    elif change=='extra_inventory_state':
        db.add(StateTransitionEvent(aggregate_type='inventory_transaction',aggregate_id=str(tx.id),from_status=None,
            to_status='posted',actor_id=accepted.actor.user_id,reason='synthetic_duplicate',idempotency_key=uuid4().hex,
            occurred_at=datetime.now(timezone.utc),metadata_jsonb={}))
    elif change=='extra_inventory_outbox':
        db.add(OutboxEvent(event_type='synthetic_duplicate',aggregate_type='inventory_transaction',aggregate_id=str(tx.id),payload_jsonb={},idempotency_key=uuid4().hex,available_at=datetime.now(timezone.utc)))
    else:
        audit=db.scalar(select(AuditEvent).where(AuditEvent.aggregate_type=='stock_operation_return_inbound',AuditEvent.aggregate_id==str(row.id)))
        audit.aggregate_id=str(uuid4())
    db.commit()
    for action in (lambda:recovery.lookup_return_inbound_request(db,**_request(args)),
            lambda:commands.execute_return_inbound(db,**args),
            lambda:recovery.seal_return_inbound_request(db,**_request(args),request_hash=result['request_hash'])):
        with pytest.raises((InventoryReadError,posting.InventoryPostingError)) as error:action()
        assert getattr(error.value,'status_code',getattr(error.value,'http_status_code',None)) in (409,503)
    with TestClient(application(db,accepted.actor),raise_server_exceptions=False) as client:
        response=client.get(prefix(accepted)+'/by-request/'+args['request_id'])
        assert response.status_code==503,response.text
        assert 'no-store' in response.headers['cache-control']


@pytest.mark.parametrize('stock',['serial'],indirect=True)
@pytest.mark.parametrize('change',['inbound_serial_missing','different_receipt_serial','movement_serial_missing'])
def test_serial_evidence_is_exact_across_acceptance_inbound_and_ledger(db,stock,accepted,posted,change):
    args,result=posted;row=db.get(StockOperationReturnInbound,result['inbound_id'])
    if change=='movement_serial_missing':db.execute(delete(InventoryMovementSerial).where(InventoryMovementSerial.transaction_id==row.posting_transaction_id))
    else:
        serial=db.scalar(select(StockOperationReturnInboundSerial).where(StockOperationReturnInboundSerial.inbound_id==row.id))
        if change=='inbound_serial_missing':db.delete(serial)
        else:serial.serial_id=stock.serials[1].id
    db.commit()
    with pytest.raises((InventoryReadError,posting.InventoryPostingError)):
        recovery.lookup_return_inbound_request(db,**_request(args))


def test_historical_posting_survives_later_real_transfer_and_notification_processing(db,stock,recovered,accepted,posted):
    args,original=posted
    command=posting.InventoryPostingCommand(transaction_no='FOLLOWUP-'+uuid4().hex,movement_type='transfer',
        source_document_type='synthetic_receipt_history_followup',source_document_id=uuid4().hex,posting_key=uuid4().hex,
        effective_at=datetime.now(timezone.utc),movements=(posting.InventoryMovementCommand(from_account_id=accepted.target_id,
            to_account_id=recovered.account.id,quantity=Decimal(1),serial_ids=(stock.serials[0].id,) if stock.tracked else ()),))
    posting.post_inventory_transaction(db,actor=accepted.actor,command=command,idempotency_key=uuid4().hex,request_id=uuid4().hex,
        permission_resource='stock_operation',permission_action='receive_return');db.commit()
    assert db.get(StockBalance,accepted.target_id).quantity==0
    if stock.tracked:assert db.get(SerialCurrentPosition,stock.serials[0].id).stock_account_id==recovered.account.id
    box=db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type=='stock_operation_return_inbound',OutboxEvent.aggregate_id==str(original['inbound_id'])))
    box.status='published';box.attempts=3;db.commit()
    assert recovery.lookup_return_inbound_request(db,**_request(args))==original
    assert commands.execute_return_inbound(db,**args)==dict(original,replayed=True)


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_complete_fact_read_is_select_only_and_preserves_pending_outbox(db,accepted,posted):
    args,original=posted;pending=OutboxEvent(event_type='synthetic_pending',aggregate_type='test',aggregate_id=uuid4().hex,payload_jsonb={},idempotency_key=uuid4().hex)
    db.add(pending);statements=[]
    def capture(_c,_cu,sql,*_):statements.append(sql.strip().split()[0].upper())
    connection=db.connection();event.listen(connection,'before_cursor_execute',capture)
    try:assert recovery.lookup_return_inbound_request(db,**_request(args))==original
    finally:event.remove(connection,'before_cursor_execute',capture)
    assert pending in db.new and statements and set(statements)=={'SELECT'}
    db.rollback()
