"""Real API-role return acceptance with exact parcel and stock-neutral evidence."""
from contextlib import nullcontext
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from threading import Event
import time
from uuid import UUID, uuid4

from sqlalchemy import select, text, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import FileObject
from app.inventory_models import Shipment, InventorySerial, Receipt, InboundOrder
from app.models import User
from app.stock_operation_models import (StockOperationShipment, StockOperationReceipt, StockOperationReceiptLine,
    StockOperationReceiptSerial, StockOperationReceiptException)
from app.stock_return_receipt_schemas import StockReturnReceiptPreviewIn, StockReturnReceiptSubmitIn
from app.formal_services import stock_return_receipt_plan as plan, stock_return_receipt_commands as commands
from app.formal_services import stock_return_receipt_queries as queries, stock_return_receipt_recovery as recovery
from app.formal_services import formal_files
from app.formal_services.inventory_query import InventoryReadError
from pg16_work_order_material_gate import _checkpoint
from pg16_stock_return_shipment_gate import parcel_snapshot


def receipt_snapshot(engine):
    with engine.connect() as db:
        rows=tuple(tuple(db.execute(text(f'SELECT * FROM {table} ORDER BY id'))) for table in
            ('receipts','stock_operation_receipts','stock_operation_receipt_lines','stock_operation_receipt_serials','stock_operation_receipt_exceptions','files'))
    return parcel_snapshot(engine),rows


def receipt_candidates(engine, candidates, *, all_items=False):
    operations=tuple(UUID(str(row['operation_id'])) for row in candidates)
    with Session(engine) as db:
        rows=tuple(db.execute(select(Shipment.id,User.id).join(StockOperationShipment,StockOperationShipment.id==Shipment.id)
            .join(User,User.person_id==Shipment.target_person_id).where(StockOperationShipment.operation_id.in_(operations)).order_by(Shipment.id)))
        result={}
        for shipment_id,user_id in rows:
            actor=load_formal_principal(db,user_id);_,detail=plan.authorize(db,actor,shipment_id)
            if (len(detail.package.lines)==1 and Decimal(detail.package.lines[0].shipped_quantity)>0
                    and not db.scalar(select(StockOperationReceipt.id).where(StockOperationReceipt.shipment_id==shipment_id).limit(1))):
                result.setdefault('serial' if detail.package.lines[0].serials else 'quantity',[]).append(dict(shipment_id=shipment_id,user_id=user_id))
    assert set(result)=={'quantity','serial'},'exact unconsumed quantity and SN parcels required'
    return result if all_items else {kind:rows[0] for kind,rows in result.items()}


def _context(db, candidate):
    actor=load_formal_principal(db,candidate['user_id'])
    _,detail=plan.authorize(db,actor,candidate['shipment_id']);line=detail.package.lines[0]
    serials=tuple(db.get(InventorySerial,sn.serial_id) for sn in line.serials)
    request=StockReturnReceiptPreviewIn(operator_person_id=actor.person_id,received_at=datetime.now(timezone.utc),
        reason='Synthetic independent return acceptance, not inbound',lines=[dict(shipment_line_id=line.shipment_line_id,
            accepted_qty=line.shipped_quantity,accepted_serial_verifications=[dict(serial_id=sn.id,serial_no=sn.serial_no,
                sku_code=line.sku_code,qr_code=sn.qr_code) for sn in serials])])
    return SimpleNamespace(actor=actor,package=detail.package,request=request)


def _command(db, context, request=None):
    request=request or context.request
    checked,_=plan.preview_receipt(db,actor=context.actor,shipment_id=context.package.shipment_id,request=request)
    return StockReturnReceiptSubmitIn(**request.model_dump(),expected_plan_hash=checked.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex)


def _execute(db, context, value):
    result=commands.execute_receipt(db,actor=context.actor,shipment_id=context.package.shipment_id,request=value)
    _checkpoint(db)
    return result


def _evidence(db, context):
    from test_formal_files_service import FakeStorage
    from test_material_request_draft_service import SECRET
    storage=FakeStorage();key=uuid4().hex
    uploaded=formal_files.create_file_upload_intent(db,actor=context.actor,
        command=formal_files.FileUploadIntentInput(purpose='receipt_exception_evidence',original_filename='synthetic-return.png',
            size_bytes=10,mime_type='image/png',sha256='a'*64),idempotency_key=key,idempotency_hmac_secret=SECRET,
        trace_request_id='upload-'+key,storage=storage,upload_ttl_seconds=600)
    file=db.get(FileObject,uploaded.file_id);storage.materialize(file)
    formal_files.complete_file_upload(db,actor=context.actor,file_id=file.id,trace_request_id='complete-'+key,storage=storage)
    _checkpoint(db)
    return file,storage


def assert_receipt_normal_gate(api_engine, candidate, kind):
    before=receipt_snapshot(api_engine)
    with Session(api_engine) as db:
        try:
            context=_context(db,candidate);value=_command(db,context)
            result=_execute(db,context,value)
            assert _execute(db,context,value)==result
            assert recovery.lookup_receipt_request(db,actor=context.actor,shipment_id=result.shipment_id,request_id=value.request_id)==result
            assert recovery.seal_receipt_request(db,actor=context.actor,shipment_id=result.shipment_id,request_id=value.request_id,
                request_hash=result.request_hash)==result
            _checkpoint(db)
            history=queries.receipt_history(db,actor=context.actor,shipment_id=result.shipment_id)
            assert history.receipts==(result,) and history.lines[0].unconfirmed_qty=='0.000'
            assert 'qr_code' not in history.model_dump_json() and 'posting_transaction_id' not in history.model_dump_json()
        finally:db.rollback()
    assert receipt_snapshot(api_engine)==before
    print(f'PG16 {kind} receipt: exact API role, atomic guards, original GET/replay and stock neutrality PASS',flush=True)


def assert_receipt_exception_gate(api_engine,candidate,kind,exception_type):
    before=receipt_snapshot(api_engine)
    with Session(api_engine) as db:
        try:
            context=_context(db,candidate);file,storage=_evidence(db,context)
            value=context.request.model_dump();line=value['lines'][0];amount=line['accepted_qty']
            identifiers=tuple(proof['serial_id'] for proof in line['accepted_serial_verifications'])
            line['exceptions']=[dict(exception_type=exception_type,description='Synthetic exact return acceptance exception',evidence_file_id=file.id)]
            if exception_type=='damaged':line.update(damaged_qty=amount,damaged_serial_ids=identifiers)
            else:
                outcome='shortage' if exception_type=='shortage' else 'rejected'
                line.update(accepted_qty=Decimal(0),accepted_serial_verifications=(),**{outcome+'_qty':amount,outcome+'_serial_ids':identifiers})
            request=StockReturnReceiptPreviewIn.model_validate(value)
            result=_execute(db,context,_command(db,context,request))
            assert result.status=='exception' and result.lines[0].exceptions[0].evidence_file_id==file.id
            download=formal_files.create_file_download_intent(db,actor=context.actor,file_id=file.id,trace_request_id='download-'+uuid4().hex,
                storage=storage,download_ttl_seconds=60)
            assert download.file_id==file.id
            _checkpoint(db)
            if exception_type=='shortage':
                later=_execute(db,context,_command(db,context))
                assert later.lines[0].previously_accepted_qty==later.lines[0].previously_rejected_qty=='0.000'
                assert len(queries.receipt_history(db,actor=context.actor,shipment_id=result.shipment_id).receipts)==2
            else:
                import pytest
                with pytest.raises(InventoryReadError):_command(db,context)
        finally:db.rollback()
    assert receipt_snapshot(api_engine)==before
    print(f'PG16 {kind} receipt {exception_type}: original file, exact quantities/SNs, private download and unchanged stock PASS',flush=True)


def assert_receipt_partial_gate(api_engine,candidate):
    before=receipt_snapshot(api_engine)
    with Session(api_engine) as db:
        try:
            context=_context(db,candidate)
            total=context.request.lines[0].accepted_qty;first_qty=Decimal('.125')
            assert total>first_qty,'fractional acceptance needs an original parcel larger than 0.125'
            first=context.request.model_copy(update={'lines':(context.request.lines[0].model_copy(update={'accepted_qty':first_qty}),)})
            a=_execute(db,context,_command(db,context,first))
            second=first.model_copy(update={'lines':(first.lines[0].model_copy(update={'accepted_qty':total-first_qty}),)})
            b=_execute(db,context,_command(db,context,second))
            history=queries.receipt_history(db,actor=context.actor,shipment_id=context.package.shipment_id)
            assert history.receipts==(a,b) and Decimal(history.lines[0].accepted_qty)==total and history.lines[0].unconfirmed_qty=='0.000'
        finally:db.rollback()
    assert receipt_snapshot(api_engine)==before
    print('PG16 fractional return receipt: two exact parts confirm the original parcel quantity, no stock posting PASS',flush=True)


def assert_receipt_seal_gate(api_engine,candidate,kind):
    import pytest
    before=receipt_snapshot(api_engine)
    with Session(api_engine) as db:
        try:
            context=_context(db,candidate);value=_command(db,context)
            from app.formal_services.work_order_return_sources import _hash
            coords=dict(actor=context.actor,shipment_id=context.package.shipment_id,request_id=value.request_id)
            assert recovery.lookup_receipt_request(db,**coords) is None
            result=recovery.seal_receipt_request(db,**coords,request_hash=_hash(plan.intent(context.package.shipment_id,value)))
            _checkpoint(db)
            assert result.lookup_status=='sealed' and recovery.lookup_receipt_request(db,**coords)==result
            with pytest.raises(InventoryReadError):_execute(db,context,value)
            assert not db.scalar(select(StockOperationReceipt.id))
        finally:db.rollback()
    assert receipt_snapshot(api_engine)==before
    print(f'PG16 {kind} receipt absent-request seal: original GET and late-command exclusion PASS',flush=True)


def assert_return_receipt_rollback_gate(api_engine,candidates):
    selected=receipt_candidates(api_engine,candidates)
    for kind,candidate in selected.items():
        assert_receipt_normal_gate(api_engine,candidate,kind)
        assert_receipt_seal_gate(api_engine,candidate,kind)
        for exception_type in ('shortage','damaged','rejected','wrong_material','wrong_serial'):
            assert_receipt_exception_gate(api_engine,candidate,kind,exception_type)
    assert_receipt_partial_gate(api_engine,selected['quantity'])
    return selected


def _raw_receipt(db, context, *, change=None, omit_audit=False, key_override=None, actor_override=None):
    """Bypass application preview validation, but never disable SQL guards."""
    from app.formal_services import inventory_posting as posting
    from app.formal_services.work_order_return_sources import _hash
    _,basis=plan.preview_receipt(db,actor=context.actor,shipment_id=context.package.shipment_id,request=context.request)
    basis=deepcopy(basis)
    if change:change(basis)
    value=basis['intent'];now=datetime.now(timezone.utc);identifier=uuid4();key=key_override or posting._storage_hash(uuid4().hex)
    header=Receipt(id=identifier,receipt_no='RET-RCV-'+key[:24].upper(),shipment_id=context.package.shipment_id,
        status='exception' if any(line['exceptions'] for line in value['lines']) else 'accepted',
        received_at=context.request.received_at,receiver_person_id=context.actor.person_id,request_hash=_hash(value),
        idempotency_key_hash=key,created_at=now)
    fact=StockOperationReceipt(id=identifier,shipment_id=context.package.shipment_id,actor_user_id=actor_override or context.actor.user_id,
        operator_person_id=context.actor.person_id,authorization_version=context.actor.authorization_version,
        target_custody_assignment_id=context.package.custody_assignment_id,request_id=uuid4().hex,reason=value['reason'],
        plan_hash=_hash(basis),audit_version=basis['audit_cursor']+1,command_jsonb=value,plan_jsonb=basis,created_at=now)
    db.add(header);db.flush();db.add(fact);db.flush()
    for number,chosen in enumerate(value['lines'],1):
        line=StockOperationReceiptLine(id=uuid4(),receipt_id=identifier,shipment_line_id=UUID(chosen['shipment_line_id']),
            line_no=number,**{field:Decimal(chosen[field]) for field in ('accepted_qty','rejected_qty','damaged_qty','shortage_qty')},created_at=now)
        db.add(line);db.flush()
        for ids,outcome in (([proof['serial_id'] for proof in chosen['accepted_serial_verifications']],'accepted'),
                (chosen['rejected_serial_ids'],'rejected'),(chosen['shortage_serial_ids'],'shortage')):
            db.add_all(StockOperationReceiptSerial(id=uuid4(),line_id=line.id,shipment_line_id=line.shipment_line_id,
                serial_id=UUID(sn),result=outcome,damaged=sn in chosen['damaged_serial_ids'],sku_verified=outcome=='accepted',
                qr_verified=outcome=='accepted',created_at=now) for sn in ids)
        db.add_all(StockOperationReceiptException(id=uuid4(),line_id=line.id,receipt_id=identifier,
            exception_type=item['exception_type'],description=item['description'],evidence_file_id=UUID(item['evidence_file_id']),created_at=now)
            for item in chosen['exceptions'])
    db.flush()
    if not omit_audit:commands._record(db,context.actor,fact,header,context.package)
    return fact


def assert_receipt_sql_rejections(api_engine,candidate,kind):
    import pytest
    before=receipt_snapshot(api_engine)
    def overage(basis):
        basis['intent']['lines'][0]['accepted_qty']='2.000';basis['lines'][0]['accepted_qty']='2.000'
    def forged_package(basis):basis['package']['tracking_no']='forged-original-waybill'
    def forged_policy(basis):basis['policies']=[list(item) for item in basis['policies']];basis['policies'][0][3]=2
    def missing_exception(basis):
        amount=basis['intent']['lines'][0]['accepted_qty']
        basis['intent']['lines'][0]['damaged_qty']=amount;basis['lines'][0]['damaged_qty']=amount
        if kind=='serial':
            ids=[proof['serial_id'] for proof in basis['intent']['lines'][0]['accepted_serial_verifications']]
            basis['intent']['lines'][0]['damaged_serial_ids']=ids;basis['lines'][0]['damaged_serial_ids']=ids
    def wrong_scan(basis):basis['intent']['lines'][0]['accepted_serial_verifications'][0]['qr_code']='synthetic-wrong-physical-scan'
    attacks=[('overage',overage),('original_parcel',forged_package),('policy',forged_policy),('missing_exception',missing_exception),
        ('audit',None),('shared_shipment_key',None),('sender_as_receiver',None)]
    if kind=='serial':attacks.append(('physical_scan',wrong_scan))
    for name,change in attacks:
        with Session(api_engine) as db:
            try:
                context=_context(db,candidate)
                source=db.get(Shipment,context.package.shipment_id)
                with pytest.raises(DBAPIError) as rejected,db.begin_nested():
                    _raw_receipt(db,context,change=change,omit_audit=name=='audit',
                        key_override=source.idempotency_key_hash if name=='shared_shipment_key' else None,
                        actor_override=source.actor_user_id if name=='sender_as_receiver' else None)
                    _checkpoint(db)
                assert '0105' in str(rejected.value.orig),(name,str(rejected.value.orig))
                assert getattr(rejected.value.orig,'sqlstate',None)=='23514'
            finally:db.rollback()
        assert receipt_snapshot(api_engine)==before
        print(f'PG16 {kind} receipt direct SQL {name}: API-role insert rejected without altering original facts PASS',flush=True)
    with Session(api_engine) as db:
        try:
            context=_context(db,candidate);result=_execute(db,context,_command(db,context))
            for sql in ('DELETE FROM stock_operation_receipts WHERE id=:id',
                    'UPDATE stock_operation_receipts SET reason=reason WHERE id=:id'):
                with pytest.raises(DBAPIError),db.begin_nested():db.execute(text(sql),dict(id=result.receipt_id))
            with pytest.raises(DBAPIError),db.begin_nested():db.execute(text('TRUNCATE stock_operation_receipts'))
            with pytest.raises(DBAPIError) as rejected,db.begin_nested():
                db.add(InboundOrder(id=uuid4(),receipt_id=result.receipt_id,inbound_no='SYNTHETIC-INBOUND-'+uuid4().hex,status='pending',
                    target_location_id=context.package.target_location_id,target_person_id=context.actor.person_id))
                _checkpoint(db)
            # The existing 0087 inbound boundary may reject first; either path
            # must block the attempted coupling before any posting exists.
            assert getattr(rejected.value.orig,'sqlstate',None) in {'23514','P0001'}
        finally:db.rollback()
    assert receipt_snapshot(api_engine)==before
    print(f'PG16 {kind} receipt immutable facts and unsupported direct inbound rejection PASS',flush=True)


def _receipt_race(api_engine,candidate,value,operations,*,different_request=False):
    from app.formal_services import inventory_posting as posting
    from app.formal_services.work_order_return_sources import _hash
    release=Event();held=Event();second_ready=Event();pids={}
    digest=_hash(plan.intent(candidate['shipment_id'],value))
    def worker(index,operation):
        with Session(api_engine) as db:
            pids[index]=db.scalar(text('SELECT pg_backend_pid()'))
            if index==0:
                posting._lock_inventory_ledger_head_for_atomic_batch(db);held.set()
                assert release.wait(45),'coordinator did not release receipt lock'
            else:second_ready.set()
            try:
                actor=load_formal_principal(db,candidate['user_id'])
                selected=value.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex}) if index==1 and different_request else value
                if operation=='seal':
                    result=recovery.seal_receipt_request(db,actor=actor,shipment_id=candidate['shipment_id'],request_id=selected.request_id,request_hash=digest)
                else:result=commands.execute_receipt(db,actor=actor,shipment_id=candidate['shipment_id'],request=selected)
                db.commit();return result
            except InventoryReadError as exc:
                db.rollback();return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(worker,0,operations[0]);assert held.wait(20)
        second=pool.submit(worker,1,operations[1]);assert second_ready.wait(20)
        try:
            deadline=time.monotonic()+35;blocked=False
            with api_engine.connect() as observer:
                while time.monotonic()<deadline:
                    blockers=observer.execute(text('SELECT pg_blocking_pids(:pid)'),dict(pid=pids[1])).scalar_one()
                    if pids[0] in blockers:blocked=True;break
                    if second.done():break
                    time.sleep(.05)
            assert blocked,'second receipt connection did not contend on the held ledger'
        finally:release.set()
        return first.result(timeout=180),second.result(timeout=180)


def assert_receipt_commit_gate(api_engine,candidates):
    from app.inventory_models import InventoryTransaction, StockBalance, SerialCurrentPosition
    from app.stock_operation_models import StockOperationCommandSeal
    selected=receipt_candidates(api_engine,candidates,all_items=True)
    assert len(selected['quantity'])>=3 and len(selected['serial'])>=1,'three exact quantity parcels and one SN parcel required'
    def stock():
        with api_engine.connect() as db:
            return tuple(tuple(db.execute(text(f'SELECT * FROM {table} ORDER BY {column}'))) for table,column in
                (('inventory_transactions','id'),('inventory_movements','id'),('stock_balances','stock_account_id'),('serial_current_positions','serial_id')))
    before=stock()
    scenarios=[('quantity',selected['quantity'][0],('receive','receive'),False),
        ('serial',selected['serial'][0],('receive','receive'),True),
        ('quantity',selected['quantity'][1],('seal','receive'),False),
        ('quantity',selected['quantity'][1],('receive','seal'),False),
        ('quantity',selected['quantity'][2],('receive','receive'),True)]
    for kind,candidate,operations,different in scenarios:
        with Session(api_engine) as db:
            context=_context(db,candidate);value=_command(db,context)
        first,second=_receipt_race(api_engine,candidate,value,operations,different_request=different)
        if operations[0]=='seal':assert first.lookup_status=='sealed' and second=='stock_return_request_sealed'
        elif different:assert first.status=='accepted' and second=='stock_return_receipt_quantity_exceeded'
        else:assert first==second and first.status=='accepted'
        with Session(api_engine) as db:
            if operations[0]=='seal':
                assert not db.scalar(select(StockOperationReceipt.id).where(StockOperationReceipt.shipment_id==candidate['shipment_id']))
                assert len(tuple(db.scalars(select(StockOperationCommandSeal.id).where(StockOperationCommandSeal.actor_user_id==candidate['user_id'],
                    StockOperationCommandSeal.request_id==value.request_id))))==1
            else:
                assert len(tuple(db.scalars(select(StockOperationReceipt.id).where(StockOperationReceipt.shipment_id==candidate['shipment_id']))))==1
        assert stock()==before
        print(f'PG16 {kind} receipt two-connection race {operations}, distinct request={different}: observed ledger contention and one durable outcome PASS',flush=True)
    return selected


def assert_receipt_http_read_gate(api_engine):
    import json
    import os
    from pathlib import Path
    import shutil
    import subprocess
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import formal_stock_return_receipts as router
    from app.routers import formal_stock_return_receiving as receiving_router
    node=shutil.which('node');assert node,'Node is required for the receiver mini contract gate'
    script=Path(__file__).resolve().parents[2]/'miniprogram/tests/fixtures/stock-return-receiving-pg16.cjs'
    before=receipt_snapshot(api_engine)
    with Session(api_engine) as db:
        rows=tuple(db.execute(select(StockOperationReceipt.id,StockOperationReceipt.shipment_id,
            StockOperationReceipt.actor_user_id,StockOperationReceipt.request_id).order_by(StockOperationReceipt.audit_version)))
    assert rows,'durable return receipt facts are required before their READ ONLY HTTP probe'
    for receipt_id,shipment_id,user_id,request_id in rows:
        with Session(api_engine) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            app=FastAPI();app.include_router(router.router,prefix='/api')
            app.include_router(receiving_router.router,prefix='/api')
            app.dependency_overrides[get_db]=lambda:db
            for route in (*router.router.routes,*receiving_router.router.routes):
                for dependency in route.dependant.dependencies:
                    if dependency.name=='principal':app.dependency_overrides[dependency.call]=lambda:load_formal_principal(db,user_id)
            statements=[]
            def capture(_c,_cu,sql,_p,_ctx,_many):statements.append(sql.strip().split()[0].upper())
            c=db.connection();event.listen(c,'before_cursor_execute',capture)
            try:
                with TestClient(app) as client:
                    url=f'/api/v1/stock-returns/my-receiving/{shipment_id}/receipts'
                    original=client.get(url+'/by-request/'+request_id);history=client.get(url)
                    assert original.status_code==history.status_code==200,(original.text,history.text)
                    assert original.json()['receipt_id']==str(receipt_id) and original.json() in history.json()['receipts']
                    for response in (original,history):
                        assert 'no-store' in response.headers['cache-control']
                        assert 'qr_code' not in response.text and 'posting_transaction_id' not in response.text
                    assert all(Decimal(line['unconfirmed_qty'])==0 for line in history.json()['lines'])
                    directory=client.get('/api/v1/stock-returns/my-receiving?limit=10')
                    assert directory.status_code==200 and 'no-store' in directory.headers['cache-control']
                    checked=subprocess.run([node,str(script)],input=json.dumps(dict(history=history.json(),directory=directory.json())),
                        text=True,capture_output=True,timeout=30,env={'PATH':os.environ.get('PATH','')})
                    assert checked.returncode==0,checked.stderr
                    assert checked.stdout=='PG16 recipient mini contracts PASS\n'
            finally:event.remove(c,'before_cursor_execute',capture)
            assert statements and set(statements)=={'SELECT'}
            db.rollback()
    assert receipt_snapshot(api_engine)==before
    print('PG16 committed return receipt HTTP and mini contracts: exact original request/history, forced READ ONLY, no QR or posting claims PASS',flush=True)
