"""Real API-role inbound proof on the parent's protected disposable PG16 DB.

The predecessor fixtures establish receiving accounts through real opening.
Mocks below only suppress Python proof or inject malformed facts to test SQL;
positive commits, ledger movement, permissions and recovery use real services.
"""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from threading import Event
import time
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.database import get_db
from app.formal_access import load_formal_principal
from app.foundation_models import OutboxEvent
from app.inventory_models import StockBalance, SerialCurrentPosition
from app.stock_operation_models import (StockOperationShipment, StockOperationReceipt, StockOperationReceiptLine,
    StockOperationReceiptSerial, StockOperationReturnInbound, StockOperationReturnInboundLine,
    StockOperationReturnInboundSerial, StockOperationReturnInboundPosting)
from app.formal_services import stock_return_inbound_commands as commands, stock_return_inbound_recovery as recovery
from app.formal_services.stock_return_inbound_facts import document
from app.formal_services.stock_return_inbound_plan import plan_return_inbound
from app.formal_services import inventory_posting as posting
from app.routers import formal_stock_return_inbounds as router
from pg16_work_order_material_gate import _checkpoint, _snapshot


def inbound_snapshot(engine):
    with engine.connect() as db:
        facts=tuple(tuple(db.execute(text(f'SELECT * FROM {name} ORDER BY id'))) for name in
            ('stock_operation_return_inbounds','stock_operation_return_inbound_lines',
             'stock_operation_return_inbound_serials','stock_operation_return_inbound_postings'))
    return _snapshot(engine),facts


def _args(db,identifier):
    receipt=db.get(StockOperationReceipt,identifier)
    actor=load_formal_principal(db,receipt.actor_user_id)
    plan=plan_return_inbound(db,actor=actor,receipt_id=identifier)
    return dict(actor=actor,receipt_id=identifier,request_id=uuid4().hex,idempotency_key=uuid4().hex,
        expected_plan_hash=plan['plan_hash']),plan


def _assert_receipt_state_read_only(engine,identifier,user_id,posted=None):
    """Real HTTP response under the API role's read-only PG transaction."""
    before=inbound_snapshot(engine)
    with Session(engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        app=FastAPI();app.include_router(router.router,prefix='/api')
        app.dependency_overrides[get_db]=lambda:db
        for route in router.router.routes:
            for dependency in route.dependant.dependencies:
                if dependency.name=='principal':
                    app.dependency_overrides[dependency.call]=lambda:load_formal_principal(db,user_id)
        sql=[];connection=db.connection()
        def capture(_c,_cu,statement,*_):sql.append(statement.strip().split()[0].upper())
        event.listen(connection,'before_cursor_execute',capture)
        try:
            with TestClient(app,raise_server_exceptions=False) as client:
                response=client.get(f'/api/v1/stock-returns/my-receiving/{identifier}/inbound')
                assert response.status_code==200,response.text
                assert 'private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control']
                body=response.json();assert body['receipt_id']==str(identifier)
                assert body['status']==('posted' if posted else 'not_posted')
                if posted:
                    assert body['inbound']['inbound_id']==str(posted['inbound_id'])
                    assert body['inbound']['posting_transaction_id']==str(posted['posting_transaction_id'])
                else:assert body['inbound'] is None
                assert not any(key in response.text for key in ('request_hash','plan_hash','request_id','idempotency'))
            assert sql and set(sql)=={'SELECT'},sql
        finally:
            event.remove(connection,'before_cursor_execute',capture);db.rollback()
    assert inbound_snapshot(engine)==before


def _corrupt(db,change):
    def before_flush(session,*_):
        for row in tuple(session.new):
            if isinstance(row,StockOperationReturnInbound):
                if change=='hash':row.request_hash='a'*64
                elif change=='command':row.command_jsonb={**row.command_jsonb,'unexpected':True}
                elif change=='plan':
                    value=deepcopy(row.plan_jsonb);value['ledger_cursor']=0;row.plan_jsonb=value
            elif isinstance(row,StockOperationReturnInboundLine) and change=='quantity':row.accepted_qty+=Decimal('.001')
            elif isinstance(row,StockOperationReturnInboundPosting) and change=='posting_link':session.expunge(row)
            elif isinstance(row,StockOperationReturnInboundSerial) and change=='serial':session.expunge(row)
            elif isinstance(row,OutboxEvent) and row.aggregate_type=='stock_operation_return_inbound' and change=='outbox':session.expunge(row)
    event.listen(db,'before_flush',before_flush)
    return before_flush


def _commit_competing_same_request(engine,args):
    held=Event();started=Event();release=Event();pids={}
    def worker(number):
        with Session(engine) as db:
            pids[number]=db.scalar(text('SELECT pg_backend_pid()'))
            if number==0:
                posting._lock_inventory_ledger_head_for_atomic_batch(db);held.set()
                assert release.wait(45),'inbound coordinator did not release the ledger'
            else:started.set()
            actor=load_formal_principal(db,args['actor'].user_id)
            result=commands.execute_return_inbound(db,**dict(args,actor=actor));_checkpoint(db);db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(worker,0);assert held.wait(20)
        second=pool.submit(worker,1);assert started.wait(20)
        try:
            deadline=time.monotonic()+35;blocked=False
            with engine.connect() as observer:
                while time.monotonic()<deadline:
                    if pids[0] in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),dict(pid=pids[1])):
                        blocked=True;break
                    if second.done():break
                    time.sleep(.05)
            assert blocked,'second inbound request did not wait for the original ledger transaction'
        finally:release.set()
        original=first.result(timeout=180);replay=second.result(timeout=180)
    assert replay==dict(original,replayed=True)
    return original


def assert_return_inbound_gate(api_engine,origins):
    operations=tuple(UUID(str(row['operation_id'])) for row in origins)
    with Session(api_engine) as db:
        assert db.scalar(text('SELECT current_user'))=='star_oam_api'
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        candidates={}
        for receipt in db.scalars(select(StockOperationReceipt).join(StockOperationShipment,
                StockOperationShipment.id==StockOperationReceipt.shipment_id).where(StockOperationShipment.operation_id.in_(operations))
                .order_by(StockOperationReceipt.id)):
            accepted=tuple(db.scalars(select(StockOperationReceiptLine.id).where(
                StockOperationReceiptLine.receipt_id==receipt.id,StockOperationReceiptLine.accepted_qty>0)))
            if not accepted:continue
            serial=db.scalar(select(StockOperationReceiptSerial.id).where(StockOperationReceiptSerial.line_id.in_(accepted),
                StockOperationReceiptSerial.result=='accepted').limit(1))
            candidates.setdefault('serial' if serial else 'quantity',receipt.id)
    assert set(candidates)=={'quantity','serial'},'exact accepted quantity/SN returns required'
    for kind,identifier in candidates.items():
        before=inbound_snapshot(api_engine)
        with Session(api_engine) as db:
            args,plan=_args(db,identifier);token=uuid4()
            orphan=replace(plan['command'],source_document_id=str(token),
                transaction_no='INV-RETURN-IN-'+token.hex[:16].upper(),effective_at=datetime.now(timezone.utc))
            try:
                with pytest.raises(DBAPIError) as failure:
                    posting.post_inventory_transaction(db,actor=args['actor'],command=orphan,
                        idempotency_key=uuid4().hex,request_id=uuid4().hex,
                        permission_resource='stock_operation',permission_action='receive_return');_checkpoint(db)
                assert failure.value.orig.sqlstate=='23514' and '0106 detached return inbound' in str(failure.value.orig)
            finally:db.rollback()
        assert inbound_snapshot(api_engine)==before
        for change in ('hash','command','plan','quantity','posting_link','outbox','audit',*(('serial',) if kind=='serial' else ())):
            before=inbound_snapshot(api_engine)
            with Session(api_engine) as db:
                args,_=_args(db,identifier);listener=_corrupt(db,change)
                try:
                    # Only the final Python projection is bypassed. SQL row,
                    # FK, audit, privilege and deferred guards remain active.
                    with patch.object(commands,'inbound_result',lambda _db,*,actor,fact,**kw:document(fact)), \
                            (patch.object(commands,'append_audit_event',return_value=None) if change=='audit' else nullcontext()):
                        with pytest.raises(DBAPIError) as failure:
                            commands.execute_return_inbound(db,**args);_checkpoint(db)
                        assert failure.value.orig.sqlstate=='23514',str(failure.value.orig)
                        assert '0106' in str(failure.value.orig) or '0111' in str(failure.value.orig),str(failure.value.orig)
                finally:
                    event.remove(db,'before_flush',listener);db.rollback()
            assert inbound_snapshot(api_engine)==before
        with Session(api_engine) as db:
            args,plan=_args(db,identifier)
            old={}
            for line in plan['lines']:
                account=UUID(line['target_account_id']);balance=db.get(StockBalance,account)
                old[account]=balance.quantity if balance is not None else Decimal(0)
        _assert_receipt_state_read_only(api_engine,identifier,args['actor'].user_id)
        result=_commit_competing_same_request(api_engine,args)
        _assert_receipt_state_read_only(api_engine,identifier,args['actor'].user_id,result)
        with Session(api_engine) as db:
            args['actor']=load_formal_principal(db,args['actor'].user_id)
            lookup={key:args[key] for key in ('actor','receipt_id','request_id')}
            local=SimpleNamespace(connect=lambda:nullcontext(db.connection()));before=inbound_snapshot(local)
            assert recovery.lookup_return_inbound_request(db,**lookup)==result
            assert commands.execute_return_inbound(db,**args)==dict(result,replayed=True)
            assert recovery.seal_return_inbound_request(db,**lookup,request_hash=result['request_hash'])==result
            _checkpoint(db);assert inbound_snapshot(local)==before
            amounts={}
            for line in plan['lines']:
                account=UUID(line['target_account_id']);amounts[account]=amounts.get(account,Decimal(0))+Decimal(line['accepted_qty'])
                for serial in line['serial_ids']:assert db.get(SerialCurrentPosition,UUID(serial)).stock_account_id==account
            for account,amount in amounts.items():assert db.get(StockBalance,account).quantity==old[account]+amount
            db.commit()
        before=inbound_snapshot(api_engine)
        with Session(api_engine) as db:
            db.add(OutboxEvent(event_type='synthetic_extra',aggregate_type='stock_operation_return_inbound',
                aggregate_id=str(result['inbound_id']),payload_jsonb={},idempotency_key=uuid4().hex,available_at=datetime.now(timezone.utc)))
            with pytest.raises(DBAPIError) as failure:_checkpoint(db)
            assert failure.value.orig.sqlstate=='23514' and '0111' in str(failure.value.orig)
            db.rollback()
        assert inbound_snapshot(api_engine)==before
        print(f'PG16 {kind} independent inbound: malformed SQL graph rollback, two-session exact commit, receipt state read-only HTTP, ledger/SN and replay PASS',flush=True)
