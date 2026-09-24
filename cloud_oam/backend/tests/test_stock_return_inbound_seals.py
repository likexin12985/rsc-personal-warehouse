"""Receipt-bound seals protect uncertain commands without moving inventory."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app.foundation_models import AuditEvent, OutboxEvent
from app.inventory_models import StockBalance
from app.stock_operation_models import StockOperationReturnInboundSeal, StockOperationCommandSeal
from app.formal_services import stock_return_receipt_recovery as receipt_recovery
from app.formal_services.stock_return_commands import _fresh_request
from app.formal_services.inventory_query import InventoryReadError
from test_stock_return_inbound import (db, world, stock, recovered, destination, prepared, parcel,
    incoming, acceptance, inbound_accounts, accepted, command, commands, recovery,
    application, prefix, snapshot, submit, execute)


def seal_arguments(db, accepted):
    value = command(db, accepted)
    return value, dict(actor=value['actor'], receipt_id=value['receipt_id'], request_id=value['request_id'],
        request_hash=commands._request_hash(receipt_id=value['receipt_id'], request_id=value['request_id'], plan_hash=value['expected_plan_hash']))


def test_fresh_seal_http_roundtrip_blocks_late_execution_and_validates_actual_mini_contract(db, accepted):
    value, args = seal_arguments(db, accepted)
    inventory = tuple(tuple(db.execute(text(f'SELECT * FROM {table} ORDER BY {key}')))
        for table,key in (('inventory_transactions','id'),('inventory_movements','id'),
            ('stock_balances','stock_account_id'),('serial_current_positions','serial_id')))
    with TestClient(application(db, accepted.actor), raise_server_exceptions=False) as client:
        url = prefix(accepted) + '/by-request/' + args['request_id']
        assert client.get(url).status_code == 404
        first = client.post(url + '/seal', json={'request_hash': args['request_hash']})
        assert first.status_code == 200, first.text
        result = first.json()
        assert result['seal']['receipt_id'] == str(args['receipt_id'])
        assert result['seal']['shipment_id'] == str(accepted.receipt.shipment_id)
        assert 'no-store' in first.headers['cache-control']
        marker = dict(receipt_id=str(args['receipt_id']), shipment_id=str(accepted.receipt.shipment_id),
            trace_request_id=args['request_id'], request_hash=args['request_hash'])
        script = "const fs=require('node:fs'); const {result,marker}=JSON.parse(fs.readFileSync(0,'utf8')); require('./utils/stock-return-inbound-contract').validateLookup(result,marker)"
        checked = subprocess.run(['node', '-e', script], input=json.dumps(dict(result=result,marker=marker)),
            cwd=Path(__file__).resolve().parents[2]/'miniprogram', text=True, capture_output=True, timeout=30)
        assert checked.returncode == 0, checked.stderr
        after = snapshot(db)
        assert client.get(url).json() == result
        assert client.post(url + '/seal', json={'request_hash': args['request_hash']}).json() == result
        assert client.post(url + '/seal', json={'request_hash': '0'*64}).status_code == 409
        assert snapshot(db) == after
    assert tuple(tuple(db.execute(text(f'SELECT * FROM {table} ORDER BY {key}')))
        for table,key in (('inventory_transactions','id'),('inventory_movements','id'),
            ('stock_balances','stock_account_id'),('serial_current_positions','serial_id'))) == inventory
    assert db.scalar(select(StockOperationCommandSeal.id)) is None
    for key in (value['idempotency_key'], uuid4().hex):
        with pytest.raises(InventoryReadError) as error:
            commands.execute_return_inbound(db, **dict(value, idempotency_key=key))
        assert error.value.code == 'stock_return_request_sealed'
    with pytest.raises(InventoryReadError):
        _fresh_request(db, actor=accepted.actor, request_id=value['request_id'], key='a'*64)
    with pytest.raises(InventoryReadError):
        receipt_recovery.seal_receipt_request(db, actor=accepted.actor,
            shipment_id=accepted.receipt.shipment_id, request_id=args['request_id'], request_hash=args['request_hash'])
    assert snapshot(db) == after


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_two_acceptances_of_one_parcel_have_distinct_seals_and_can_use_new_requests(db, acceptance, inbound_accounts):
    contexts=[]
    for quantity in ('.375','.625'):
        request=acceptance.request.model_copy(update={'lines':(acceptance.request.lines[0].model_copy(update={'accepted_qty':Decimal(quantity)}),)})
        receipt=execute(db, acceptance, submit(db, acceptance, request)); db.commit()
        contexts.append(SimpleNamespace(actor=acceptance.actor,receipt=receipt))
    _,args=seal_arguments(db, contexts[0])
    first=recovery.seal_return_inbound_request(db, **args); db.commit()
    wrong=dict(args,receipt_id=contexts[1].receipt.receipt_id)
    with pytest.raises(InventoryReadError) as error: recovery.seal_return_inbound_request(db, **wrong)
    assert error.value.code=='stock_return_inbound_request_conflict'
    with pytest.raises(InventoryReadError): recovery.lookup_return_inbound_request(db, **{key:value for key,value in wrong.items() if key!='request_hash'})
    _,other=seal_arguments(db,contexts[1])
    second=recovery.seal_return_inbound_request(db,**other); db.commit()
    assert first['seal']['seal_id']!=second['seal']['seal_id']
    for context in contexts:
        commands.execute_return_inbound(db,**command(db,context)); db.commit()
    assert db.get(StockBalance,inbound_accounts[0]).quantity==0
    assert db.get(StockBalance,inbound_accounts[1]).quantity==1
    assert recovery.lookup_return_inbound_request(db,**{key:value for key,value in args.items() if key!='request_hash'})==first


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_legacy_acceptance_seal_is_never_reinterpreted_as_inbound(db,accepted):
    _,args=seal_arguments(db,accepted)
    receipt_recovery.seal_receipt_request(db,actor=accepted.actor,shipment_id=accepted.receipt.shipment_id,
        request_id=args['request_id'],request_hash=args['request_hash']); db.commit(); before=snapshot(db)
    with pytest.raises(InventoryReadError) as error: recovery.seal_return_inbound_request(db,**args)
    assert error.value.code=='stock_return_inbound_request_conflict'
    assert snapshot(db)==before
    assert db.scalar(select(StockOperationReturnInboundSeal.id)) is None


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
@pytest.mark.parametrize('tamper',['hash','audit_missing','audit_payload','inventory_evidence'])
def test_corrupt_seal_evidence_cannot_be_confirmed(db,accepted,tamper):
    _,args=seal_arguments(db,accepted)
    sealed=recovery.seal_return_inbound_request(db,**args); db.commit()
    row=db.get(StockOperationReturnInboundSeal,sealed['seal']['seal_id'])
    audit=db.scalar(select(AuditEvent).where(AuditEvent.aggregate_type=='stock_operation_return_inbound_seal'))
    # Metadata-only fixture intentionally permits corruption for read proofs.
    if tamper=='hash': row.request_hash='b'*64
    elif tamper=='audit_missing': audit.aggregate_id=str(uuid4())
    elif tamper=='audit_payload': audit.after_jsonb={**audit.after_jsonb,'receipt_id':str(uuid4())}
    else:
        from datetime import datetime,timezone
        from app.formal_services.audit_chain import append_audit_event
        now=datetime.now(timezone.utc)
        append_audit_event(db,stream_key='inventory',actor_user_id=row.actor_user_id,
            action='synthetic_late_inbound',aggregate_type='inventory_transaction',aggregate_id=str(uuid4()),
            request_id=row.request_reference,before_jsonb={},after_jsonb={},occurred_at=now,created_at=now)
    db.commit()
    from app.formal_services.inventory_posting import InventoryPostingError
    with pytest.raises((InventoryReadError,InventoryPostingError)) as error:
        recovery.lookup_return_inbound_request(db,**{key:value for key,value in args.items() if key!='request_hash'})
    assert getattr(error.value,'status_code',getattr(error.value,'http_status_code',None))==503


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_lookup_is_read_only_and_checks_current_authority(db,stock,accepted):
    _,args=seal_arguments(db,accepted)
    original=recovery.seal_return_inbound_request(db,**args); db.commit()
    readonly=replace(accepted.actor,entitlements=tuple(row for row in accepted.actor.entitlements if row.action!='receive_return'))
    stock.world.current_principal=readonly
    lookup={key:value for key,value in args.items() if key!='request_hash'};lookup['actor']=readonly
    pending=OutboxEvent(event_type='synthetic_pending',aggregate_type='test',aggregate_id=str(uuid4()),payload_jsonb={},idempotency_key=uuid4().hex)
    db.add(pending)
    sql=[]
    def capture(_c,_cu,statement,*_): sql.append(statement.strip().split()[0].upper())
    connection=db.connection();event.listen(connection,'before_cursor_execute',capture)
    try: assert recovery.lookup_return_inbound_request(db,**lookup)==original
    finally:event.remove(connection,'before_cursor_execute',capture)
    assert sql and set(sql)=={'SELECT'} and pending in db.new
    db.rollback()
    with pytest.raises(InventoryReadError): recovery.seal_return_inbound_request(db,**dict(args,actor=readonly))


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_late_seal_audit_failure_rolls_back_without_false_confirmation(db,accepted,monkeypatch):
    _,args=seal_arguments(db,accepted); before=snapshot(db)
    def broken(*_args,**_kwargs): raise RuntimeError('synthetic seal audit failure')
    monkeypatch.setattr(recovery,'append_audit_event',broken)
    with pytest.raises(RuntimeError,match='synthetic seal audit failure'): recovery.seal_return_inbound_request(db,**args)
    db.rollback(); assert snapshot(db)==before
    assert recovery.lookup_return_inbound_request(db,**{key:value for key,value in args.items() if key!='request_hash'}) is None


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_unattached_legacy_seal_audit_cannot_be_repurposed_as_inbound(db,accepted):
    from datetime import datetime,timezone
    from app.formal_services.audit_chain import append_audit_event
    _,args=seal_arguments(db,accepted);now=datetime.now(timezone.utc);identifier=uuid4()
    append_audit_event(db,stream_key='material_request',actor_user_id=accepted.actor.user_id,
        action='stock_return.command_sealed',aggregate_type='stock_operation_command_seal',aggregate_id=str(identifier),
        request_id=f'stock-return-seal:{identifier}',before_jsonb={},after_jsonb={'request_id':args['request_id']},
        occurred_at=now,created_at=now)
    db.commit();before=snapshot(db)
    with pytest.raises(InventoryReadError) as error: recovery.seal_return_inbound_request(db,**args)
    assert error.value.status_code==503 and snapshot(db)==before
    assert db.scalar(select(StockOperationReturnInboundSeal.id)) is None
