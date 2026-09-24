"""0110 API-role seals on the parent's verified disposable PG16 database.

No provisioning or business-system requests. This covers sealing, receipt
binding and late command exclusion; complete inbound-posting proof is separate.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
import time
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.stock_operation_models import StockOperationReceipt, StockOperationReturnInboundSeal
from app.formal_services import inventory_posting as posting, stock_return_inbound_recovery as recovery
from app.formal_services import stock_return_inbound_commands as commands
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError


def seal_snapshot(engine):
    with engine.connect() as db:
        return tuple(db.execute(text('SELECT * FROM stock_operation_return_inbound_seals ORDER BY id')))


def _stock(engine):
    with engine.connect() as db:
        return tuple(tuple(db.execute(text(f'SELECT * FROM {table} ORDER BY {key}'))) for table,key in
            (('inventory_transactions','id'),('inventory_movements','id'),('stock_balances','stock_account_id'),('serial_current_positions','serial_id')))


def _args(db, receipt_id):
    receipt=db.get(StockOperationReceipt,receipt_id)
    actor=load_formal_principal(db,receipt.actor_user_id)
    request_id=uuid4().hex
    return dict(actor=actor,receipt_id=receipt.id,request_id=request_id,
        request_hash=commands._request_hash(receipt_id=receipt.id,request_id=request_id,plan_hash='a'*64))


def _raw(db,args,*,mismatched_shipment=None,audit=False):
    receipt=db.get(StockOperationReceipt,args['receipt_id']);now=datetime.now(timezone.utc)
    row=StockOperationReturnInboundSeal(id=uuid4(),receipt_id=receipt.id,
        shipment_id=mismatched_shipment or receipt.shipment_id,actor_user_id=args['actor'].user_id,
        operator_person_id=args['actor'].person_id,authorization_version=args['actor'].authorization_version,
        request_id=args['request_id'],request_hash=args['request_hash'],
        request_reference=posting._request_reference(args['request_id']),created_at=now)
    db.add(row)
    if audit:
        append_audit_event(db,stream_key='material_request',actor_user_id=row.actor_user_id,
            action='stock_return_inbound.command_sealed',aggregate_type='stock_operation_return_inbound_seal',
            aggregate_id=str(row.id),request_id=f'stock-return-inbound-seal:{row.id}',before_jsonb={},
            after_jsonb=recovery.seal_payload(row),occurred_at=now,created_at=now)
    db.flush()
    return row


def _rejected(engine, action, message, state='23514'):
    with Session(engine) as db:
        try:
            with pytest.raises(DBAPIError) as error:
                action(db); db.commit()
            assert error.value.orig.sqlstate==state
            assert message in str(error.value.orig)
        finally:db.rollback()


def _race(engine,args,late_operation):
    release=Event();held=Event();ready=Event();pids={}
    def worker(index):
        with Session(engine) as db:
            pids[index]=db.scalar(text('SELECT pg_backend_pid()'))
            if index==0:
                posting._lock_inventory_ledger_head_for_atomic_batch(db);held.set()
                assert release.wait(45),'inbound seal coordinator did not release ledger'
            else:ready.set()
            actor=load_formal_principal(db,args['actor'].user_id)
            try:
                if index==1 and late_operation=='post':
                    result=commands.execute_return_inbound(db,actor=actor,receipt_id=args['receipt_id'],
                        request_id=args['request_id'],idempotency_key=uuid4().hex,expected_plan_hash='a'*64)
                else:result=recovery.seal_return_inbound_request(db,**dict(args,actor=actor))
                db.commit();return result
            except InventoryReadError as error:
                db.rollback();return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(worker,0);assert held.wait(20)
        second=pool.submit(worker,1);assert ready.wait(20)
        try:
            deadline=time.monotonic()+35;blocked=False
            with engine.connect() as observer:
                while time.monotonic()<deadline:
                    if pids[0] in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),dict(pid=pids[1])):
                        blocked=True;break
                    if second.done():break
                    time.sleep(.05)
            assert blocked,'late inbound request did not wait for the ledger lock'
        finally:release.set()
        return first.result(timeout=180),second.result(timeout=180)


def assert_return_inbound_seal_gate(api_engine):
    with Session(api_engine) as db:
        assert db.scalar(text('SELECT current_user'))=='star_oam_api'
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        receipts=tuple(db.scalars(select(StockOperationReceipt).order_by(StockOperationReceipt.id)))
        assert len(receipts)>=2
        args=_args(db,receipts[0].id)
        other=next(row for row in receipts if row.shipment_id!=receipts[0].shipment_id)
        bad_shipment=other.shipment_id
    before=_stock(api_engine)
    _rejected(api_engine,lambda db:_raw(db,args),'0110 complete inbound seal audit required')
    _rejected(api_engine,lambda db:_raw(db,args,mismatched_shipment=bad_shipment,audit=True),'0110 inbound seal exact acceptance mismatch')
    with Session(api_engine) as db:
        receipt=db.get(StockOperationReceipt,args['receipt_id'])
        conflicting=dict(args,request_id=receipt.request_id)
    _rejected(api_engine,lambda db:_raw(db,conflicting,audit=True),'0110 return request namespace conflict')
    for operation in ('seal','post'):
        with Session(api_engine) as db:race_args=_args(db,args['receipt_id'])
        first,second=_race(api_engine,race_args,operation)
        assert first['lookup_status']=='sealed'
        assert second==(first if operation=='seal' else 'stock_return_request_sealed')
        with Session(api_engine) as db:
            assert recovery.lookup_return_inbound_request(db,**{key:value for key,value in race_args.items() if key!='request_hash'})==first
        print(f'PG16 inbound seal / {operation}: observed ledger contention, exact receipt and one durable seal PASS',flush=True)
    def late_audit(db):
        now=datetime.now(timezone.utc)
        append_audit_event(db,stream_key='inventory',actor_user_id=race_args['actor'].user_id,
            action='synthetic_late_inbound',aggregate_type='inventory_transaction',aggregate_id=str(uuid4()),
            request_id=posting._request_reference(race_args['request_id']),before_jsonb={},after_jsonb={},occurred_at=now,created_at=now)
        db.flush()
        db.execute(text('SET CONSTRAINTS trg_audit_events_request_0110 IMMEDIATE'))
    _rejected(api_engine,late_audit,'0110 sealed inbound request has execution evidence')
    for sql in ('UPDATE stock_operation_return_inbound_seals SET id=id','DELETE FROM stock_operation_return_inbound_seals','TRUNCATE stock_operation_return_inbound_seals'):
        _rejected(api_engine,lambda db,sql=sql:db.execute(text(sql)),'permission denied','42501')
    assert _stock(api_engine)==before
    print('PG16 inbound seals: API SELECT/INSERT only, receipt/audit/request proof, stock unchanged PASS',flush=True)
