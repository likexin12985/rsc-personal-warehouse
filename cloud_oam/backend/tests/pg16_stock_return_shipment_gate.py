"""Actual API-role parcel facts, SQL rejection and durable request races."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4
import time

from fastapi.testclient import TestClient
from sqlalchemy import select, text, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.foundation_models import StateTransitionEvent
from app.inventory_models import InventoryTransaction, InventoryMovement, StockAccount, LogisticsEvent, Shipment
from app.stock_operation_models import (StockOperationOrder, StockOperationOutbound, StockOperationOutboundLine,
    StockOperationOutboundSerial, StockOperationShipment, StockOperationShipmentLine, StockOperationLine)
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from app.formal_services import stock_return_shipment_commands as commands, stock_return_recovery as recovery, inventory_posting as posting
from app.formal_services.stock_return_shipment_plan import preview_shipment
from app.formal_services.stock_return_shipment_facts import shipment_result
from app.formal_services.inventory_query import InventoryReadError
from pg16_work_order_material_gate import _checkpoint
from pg16_stock_return_transport_gate import _app
from pg16_stock_return_outbound_gate import departure_snapshot


def parcel_snapshot(engine):
    with engine.connect() as db:
        rows=tuple(tuple(db.execute(text(f'SELECT * FROM {table} ORDER BY id'))) for table in
            ('shipments','stock_operation_shipments','stock_operation_shipment_lines','stock_operation_shipment_serials'))
    return departure_snapshot(engine),rows


def _coordinates(db,candidate):
    return dict(actor=load_formal_principal(db,candidate['actor_user_id']),
        work_order_id=UUID(str(candidate['oam_work_order_id'])),operation_id=UUID(str(candidate['operation_id'])))


def _command(db,candidate,quantity):
    coordinates=_coordinates(db,candidate)
    ids=tuple(db.scalars(select(StockOperationOutboundSerial.serial_id).where(
        StockOperationOutboundSerial.line_id==UUID(str(candidate['outbound_line_id']))).order_by(StockOperationOutboundSerial.serial_id)))
    request=StockReturnShipmentPreviewIn(operator_person_id=coordinates['actor'].person_id,carrier='Synthetic carrier',
        tracking_no='PG16-PARCEL-'+uuid4().hex,shipped_at=datetime.now(timezone.utc),reason='Synthetic independent carrier handover',
        lines=[dict(outbound_line_id=candidate['outbound_line_id'],quantity=quantity,serial_ids=ids)])
    checked,_=preview_shipment(db,**coordinates,request=request)
    return StockReturnShipmentSubmitIn(**request.model_dump(),expected_plan_hash=checked.plan_hash,
        request_id=uuid4().hex,idempotency_key=uuid4().hex),checked.request_hash


def assert_parcel_directory_read(db, candidate):
    """Use the real HTTP DTOs and API grants; all executed statements are SELECT."""
    local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
    before = parcel_snapshot(local)
    url = f"/api/v1/work-orders/{candidate['oam_work_order_id']}/returns/{candidate['operation_id']}/shipments"
    statements = []
    def capture(_connection, _cursor, statement, _params, _context, _executemany):
        statements.append(statement.strip().split()[0].upper())
    connection = db.connection(); event.listen(connection, 'before_cursor_execute', capture)
    try:
        with TestClient(_app(db, candidate['actor_user_id'])) as client:
            history = client.get(url)
            options = client.get(url + '/options')
    finally:
        event.remove(connection, 'before_cursor_execute', capture)
    assert all(response.status_code == 200 for response in (history, options)), (history.text, options.text)
    assert all('no-store' in response.headers['cache-control'] and 'qr_code' not in response.text for response in (history, options))
    assert statements and set(statements) == {'SELECT'}
    source = dict(db.execute(select(StockOperationLine.id, StockOperationLine.quantity)
        .where(StockOperationLine.operation_id == UUID(str(candidate['operation_id'])))).all())
    totals = {str(identifier): Decimal(0) for identifier in source}
    by_departure = {}; selected_serials = set()
    for item in history.json()['items']:
        for row in item['lines']:
            totals[row['operation_line_id']] += Decimal(row['selected_quantity'])
            identifier = row['outbound_line_id']
            by_departure[identifier] = by_departure.get(identifier, Decimal(0)) + Decimal(row['selected_quantity'])
            ids = {proof['serial_id'] for proof in row['selected_serials']}
            assert not (ids & selected_serials); selected_serials.update(ids)
    complete = all(totals[str(identifier)] == quantity for identifier, quantity in source.items())
    status = 'shipped' if complete else 'partially_shipped' if history.json()['items'] else 'not_shipped'
    assert history.json()['shipment_status'] == status
    lines = tuple(db.scalars(select(StockOperationOutboundLine).join(StockOperationOutbound,
        StockOperationOutbound.id == StockOperationOutboundLine.outbound_id)
        .where(StockOperationOutbound.operation_id == UUID(str(candidate['operation_id'])))))
    assert {row['outbound_line_id'] for row in options.json()['lines']} == {str(line.id) for line in lines}
    for row in options.json()['lines']:
        assert Decimal(row['shipped_quantity']) == by_departure.get(row['outbound_line_id'], Decimal(0))
        assert Decimal(row['unshipped_quantity']) + Decimal(row['shipped_quantity']) == Decimal(row['outbound_quantity'])
        assert Decimal(row['selectable_quantity']) <= min(Decimal(row['unshipped_quantity']), Decimal(row['unassigned_quantity']))
        assert not ({proof['serial_id'] for proof in row['serials']} & selected_serials)
    assert parcel_snapshot(local) == before
    return status


def assert_parcel_readonly_history(api_engine, candidates):
    before = parcel_snapshot(api_engine)
    for candidate in candidates:
        with Session(api_engine) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            assert_parcel_directory_read(db, candidate)
            db.rollback()
    assert parcel_snapshot(api_engine) == before
    print('PG16 parcel directory/history: actual HTTP, forced READ ONLY, exact original totals and stock unchanged PASS', flush=True)


def assert_parcel_rollback_gate(api_engine,candidate,kind):
    baseline=parcel_snapshot(api_engine)
    with Session(api_engine) as db:
        local=SimpleNamespace(connect=lambda:nullcontext(db.connection()))
        value,digest=_command(db,candidate,'1' if kind=='serial' else '.375')
        coordinates=_coordinates(db,candidate)
        url=f"/api/v1/work-orders/{candidate['oam_work_order_id']}/returns/{candidate['operation_id']}/shipments"
        stock_before=tuple(db.execute(select(InventoryTransaction.id,InventoryTransaction.ledger_cursor).order_by(InventoryTransaction.ledger_cursor)))
        with TestClient(_app(db,candidate['actor_user_id'])) as client,patch.object(db,'commit',side_effect=lambda:_checkpoint(db)):
            response=client.post(url,json=value.model_dump(mode='json'))
            assert response.status_code==200,response.text
            assert response.json()['status']=='shipped' and 'posting_transaction_id' not in response.json()
            assert 'no-store' in response.headers['cache-control']
            before=parcel_snapshot(local);statements=[]
            def capture(_connection,_cursor,statement,_params,_context,_executemany):statements.append(statement.strip().split()[0].upper())
            event.listen(db.connection(),'before_cursor_execute',capture)
            try:read=client.get(url+'/by-request/'+value.request_id)
            finally:event.remove(db.connection(),'before_cursor_execute',capture)
            assert read.status_code==200 and read.json()==response.json()
            assert statements and set(statements)=={'SELECT'} and parcel_snapshot(local)==before
            assert 'qr_code' not in read.text and 'no-store' in read.headers['cache-control']
            assert commands.execute_shipment(db,**coordinates,request=value).model_dump(mode='json')==response.json()
            assert tuple(db.execute(select(InventoryTransaction.id,InventoryTransaction.ledger_cursor).order_by(InventoryTransaction.ledger_cursor)))==stock_before
            assert_parcel_directory_read(db,candidate)
        with db.begin_nested():
            try:
                with db.begin_nested():
                    db.add(LogisticsEvent(id=uuid4(),shipment_id=UUID(response.json()['shipment_id']),event_type='signed',
                        event_at=datetime.now(timezone.utc),source='manual',idempotency_key_hash='b'*64,actor_user_id=coordinates['actor'].user_id))
                    _checkpoint(db)
            except DBAPIError as exc:
                assert getattr(exc.orig,'sqlstate',None)=='23514' and '0104' in str(exc.orig)
            else:raise AssertionError('generic logistics accepted a return parcel')
        assert parcel_snapshot(local)==before
        db.rollback()
    assert parcel_snapshot(api_engine)==baseline
    # Bypass only the application result reader, proving the SQL boundary itself.
    for damage in ('missing_audit','excess_quantity'):
        with Session(api_engine) as db:
            value,_=_command(db,candidate,'1' if kind=='serial' else '.375')
            coordinates=_coordinates(db,candidate)
            real_line=StockOperationShipmentLine
            def damaged_line(**fields):return real_line(**{**fields,'quantity':Decimal('999')})
            try:
                with patch.object(commands.facts,'shipment_result',return_value=None):
                    target=patch.object(commands,'_record',return_value=None) if damage=='missing_audit' else patch.object(commands,'StockOperationShipmentLine',side_effect=damaged_line)
                    with target:
                        commands.execute_shipment(db,**coordinates,request=value);_checkpoint(db)
            except DBAPIError as exc:
                assert getattr(exc.orig,'sqlstate',None)=='23514' and '0104' in str(exc.orig),(damage,str(exc.orig))
            else:raise AssertionError('SQL accepted invalid parcel '+damage)
            finally:db.rollback()
        assert parcel_snapshot(api_engine)==baseline
    print('PG16 parcel '+kind+': HTTP, SELECT-only recovery, stock neutrality, SQL rejection and rollback PASS',flush=True)


def _race(api_engine,candidate,value,digest,operations):
    release=Event();held=Event();second_ready=Event();pids={}
    def worker(index,kind):
        with Session(api_engine) as db:
            pids[index]=db.scalar(text('SELECT pg_backend_pid()'))
            if index==0:
                posting._lock_inventory_ledger_head_for_atomic_batch(db);held.set()
                assert release.wait(45),'coordinator did not release parcel lock'
            else:second_ready.set()
            try:
                coordinates=_coordinates(db,candidate)
                if kind=='seal':result=recovery.seal_return_request(db,**coordinates,operation_type='ship_return',request_id=value.request_id,request_hash=digest)
                else:result=commands.execute_shipment(db,**coordinates,request=value)
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
                    blockers=observer.execute(text('SELECT pg_blocking_pids(:pid)'),{'pid':pids[1]}).scalar_one()
                    if pids[0] in blockers:blocked=True;break
                    if second.done():break
                    time.sleep(.05)
            assert blocked,'second parcel connection did not contend on the held ledger'
        finally:release.set()
        return first.result(timeout=180),second.result(timeout=180)


def assert_parcel_commit_gate(api_engine,candidates):
    with api_engine.connect() as db:
        stock_before=tuple(db.execute(select(InventoryTransaction.id,InventoryTransaction.ledger_cursor).order_by(InventoryTransaction.ledger_cursor)))
    for kind in ('quantity','serial'):
        candidate=candidates[kind]
        with Session(api_engine) as db:value,digest=_command(db,candidate,'1' if kind=='serial' else '.375')
        first,second=_race(api_engine,candidate,value,digest,('ship','ship'))
        assert first==second and first.status=='shipped'
        with Session(api_engine) as db:
            assert len(tuple(db.scalars(select(StockOperationShipment).where(StockOperationShipment.actor_user_id==candidate['actor_user_id'],StockOperationShipment.request_id==value.request_id))))==1
        print('PG16 parcel '+kind+': duplicate concurrent command retains one durable parcel PASS',flush=True)
    candidate=candidates['quantity']
    for operations in [('seal','ship'),('ship','seal')]:
        with Session(api_engine) as db:value,digest=_command(db,candidate,'.375')
        first,second=_race(api_engine,candidate,value,digest,operations)
        if operations[0]=='seal':assert first.seal.operation_type=='ship_return' and second=='stock_return_request_sealed'
        else:assert first==second and first.status=='shipped'
        print('PG16 parcel: '+operations[0]+' then '+operations[1]+' lock ordering PASS',flush=True)
    with Session(api_engine) as db:
        value,_=_command(db,candidate,'.250')
        result=commands.execute_shipment(db,**_coordinates(db,candidate),request=value);db.commit()
        assert result.lines[0].shipped_quantity=='0.750' and result.lines[0].unshipped_quantity=='0.250'
    with api_engine.connect() as db:
        assert tuple(db.execute(select(InventoryTransaction.id,InventoryTransaction.ledger_cursor).order_by(InventoryTransaction.ledger_cursor)))==stock_before
    print('PG16 parcels: 0.375 + 0.375 + 0.250 complete one exact departure without inventory posting PASS',flush=True)


def parcel_candidates(api_engine,worlds):
    result={}
    with Session(api_engine) as db:
        for kind,prepared in worlds.items():
            _account,user_id,_orders,input_line=prepared['world']
            row=db.execute(select(StockOperationOutboundLine,StockOperationOrder)
                .join(StockOperationOutbound,StockOperationOutbound.id==StockOperationOutboundLine.outbound_id)
                .join(StockOperationOrder,StockOperationOrder.id==StockOperationOutbound.operation_id)
                .join(StockAccount,StockAccount.id==StockOperationOutboundLine.transit_stock_account_id)
                .where(StockOperationOrder.actor_user_id==user_id,StockOperationOrder.transit_location_id==prepared['transit_id'],
                    StockAccount.material_id==input_line.material_id,StockOperationOutboundLine.quantity==1)
                .order_by(StockOperationOutbound.created_at,StockOperationOutboundLine.id).limit(1)).one()
            line,parent=row
            result[kind]=dict(actor_user_id=user_id,oam_work_order_id=parent.oam_work_order_id,operation_id=parent.id,outbound_line_id=line.id)
    return result


def assert_stock_return_shipment_gate(api_engine,worlds):
    candidates=parcel_candidates(api_engine,worlds)
    for kind,candidate in candidates.items():assert_parcel_rollback_gate(api_engine,candidate,kind)
    assert_parcel_commit_gate(api_engine,candidates)
    assert_parcel_readonly_history(api_engine,tuple(candidates.values()))
