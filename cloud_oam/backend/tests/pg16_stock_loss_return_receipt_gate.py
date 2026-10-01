"""Real API-role loss acceptance, inbound and recovery on disposable PG16 only."""
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select, text, event
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.models import User
from app.inventory_models import StockLocation, StockAccount, InventorySerial
from app.stock_operation_models import StockOperationOutboundLine, StockOperationReceipt, StockOperationReceiptLine, StockOperationReceiptSerial
from app.foundation_models import NotificationEvent, NotificationPersonTarget, RoleAssignment
from app.formal_services.work_order_return_sources import _hash
from decimal import Decimal
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from app.stock_return_receipt_schemas import StockReturnReceiptPreviewIn, StockReturnReceiptSubmitIn
from app.formal_services import stock_return_shipment_plan as ship_plan, stock_return_shipment_commands as ship_commands
from app.formal_services import stock_return_receipt_plan as plan, stock_return_receipt_commands as commands, stock_return_receipt_recovery as recovery
from app.formal_services import stock_return_receipt_facts as facts, stock_return_inbound_commands as inbound, stock_return_inbound_recovery as inbound_recovery
from app.formal_services.inventory_query import InventoryReadError
from pg16_stock_loss_return_shipment_gate import prepare_departures, inventory_snapshot
import pg16_stock_loss_return_shipment_gate as shipment_gate
import pg16_stock_loss_sources_gate as source_gate
from test_postgresql16_release_gate import _establish_multiround_stocktake_location


def exercise(context):
    world=prepare_departures(context)
    owner,api=world['owner'],world['api']
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        departure=world['first']
        line=db.scalars(select(StockOperationOutboundLine).where(StockOperationOutboundLine.outbound_id==departure.outbound_id)).one()
        request=StockReturnShipmentPreviewIn(operator_person_id=actor.person_id,carrier='Synthetic carrier',tracking_no='SYNTHETIC-'+uuid4().hex,
            shipped_at=datetime.now(timezone.utc),reason='Synthetic loss receipt gate parcel',lines=(dict(outbound_line_id=line.id,
                quantity=departure.lines[0].selected_quantity,serial_ids=tuple(s.serial_id for s in departure.lines[0].selected_serials)),))
        checked,_=ship_plan.preview_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
        shipped=ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=StockReturnShipmentSubmitIn(
            **request.model_dump(),expected_plan_hash=checked.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex))
        db.commit()
    with Session(owner) as db:
        target=db.get(StockLocation,shipped.destination.target_location_id)
        receiver_id=db.scalars(select(User.id).where(User.person_id==target.custodian_person_id)).one()
    def request_for(db):
        actor=load_formal_principal(db,receiver_id)
        _,detail=plan.authorize(db,actor,shipped.shipment_id)
        original=detail.package.lines[0]
        serials=[db.get(InventorySerial,s.serial_id) for s in original.serials]
        value=StockReturnReceiptPreviewIn(operator_person_id=actor.person_id,received_at=datetime.now(timezone.utc),
            reason='Synthetic exact loss parcel physical acceptance',lines=(dict(shipment_line_id=original.shipment_line_id,
                accepted_qty=original.shipped_quantity,accepted_serial_verifications=tuple(dict(serial_id=s.id,serial_no=s.serial_no,
                    sku_code=original.sku_code,qr_code=s.qr_code) for s in serials)),))
        preview,_=plan.preview_receipt(db,actor=actor,shipment_id=shipped.shipment_id,request=value)
        return actor,StockReturnReceiptSubmitIn(**value.model_dump(),expected_plan_hash=preview.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex),preview
    # The receipt COMMIT proof must hold current identity and custody locks.
    locked=[]
    with Session(api) as holder:
        actor,request,_=request_for(holder)
        accepted=commands.execute_receipt(holder,actor=actor,shipment_id=shipped.shipment_id,request=request)
        holder.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        for label,sql,identifier in (
            ('receiver','UPDATE users SET account_status=account_status WHERE id=:id',actor.user_id),
            ('person','UPDATE people SET employment_status=employment_status WHERE id=:id',actor.person_id),
            ('target','UPDATE stock_locations SET status=status WHERE id=:id',accepted.target_location_id),
            ('custody','UPDATE custody_assignments SET valid_to=valid_to WHERE id=:id',accepted.target_custody_assignment_id),
        ):
            with owner.connect() as contender:
                contender.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError) as error:contender.execute(text(sql),{'id':identifier})
                assert error.value.orig.sqlstate=='55P03';contender.rollback();locked.append(label)
        holder.rollback()
    print('PG16 loss receipt '+context['tracking']+': checked receiver/custody locks PASS',flush=True)
    with Session(owner) as db:
        assignment=db.scalars(select(RoleAssignment).where(RoleAssignment.user_id==receiver_id)).one()
        assignment_id,old_end=assignment.id,assignment.valid_to
        expiry=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=30)
        assignment.valid_to=expiry;db.commit()
    try:
        with Session(api) as db:
            actor,request,_=request_for(db)
            commands.execute_receipt(db,actor=actor,shipment_id=shipped.shipment_id,request=request)
            now=db.scalar(text('SELECT clock_timestamp()'));assert now<expiry
            db.execute(text('SELECT pg_sleep(:delay)'),{'delay':(expiry-now).total_seconds()+.1})
            with pytest.raises(DBAPIError) as error:db.commit()
            assert error.value.orig.sqlstate=='23514';db.rollback()
    finally:
        with Session(owner) as db:db.get(RoleAssignment,assignment_id).valid_to=old_end;db.commit()
    print('PG16 loss receipt '+context['tracking']+': actual receiver grant expiry at COMMIT PASS',flush=True)
    # Bypass only the final app readback; malformed writes must fail at COMMIT.
    rejected=[]
    for damage in ('origin','line_quantity','manifest','missing_target','ledger_cursor') + (('missing_serial',) if context['tracking']=='serial' else ()):
        before=inventory_snapshot(owner)
        with Session(api) as db:
            actor,request,_=request_for(db)
            def mutate(session,*_):
                for row in tuple(session.new):
                    if isinstance(row,StockOperationReceipt):
                        if damage=='origin':row.plan_jsonb={**row.plan_jsonb,'package':{**row.plan_jsonb['package'],'origin':{**row.plan_jsonb['package']['origin'],'loss_line_id':str(uuid4())}}}
                        elif damage=='ledger_cursor':row.plan_jsonb={**row.plan_jsonb,'ledger_cursor':row.plan_jsonb['ledger_cursor']+1}
                        row.plan_hash=_hash(row.plan_jsonb)
                    elif isinstance(row,StockOperationReceiptLine) and damage=='line_quantity':row.accepted_qty+=Decimal(1)
                    elif isinstance(row,StockOperationReceiptSerial) and damage=='missing_serial':session.expunge(row)
                    elif isinstance(row,NotificationEvent) and row.event_type=='stock_return_received' and damage=='manifest':row.target_manifest_sha256='0'*64
                    elif isinstance(row,NotificationPersonTarget) and damage=='missing_target':session.expunge(row)
            event.listen(db,'before_flush',mutate)
            try:
                with patch.object(facts,'receipt_result',return_value=None):
                    commands.execute_receipt(db,actor=actor,shipment_id=shipped.shipment_id,request=request)
                    with pytest.raises(DBAPIError) as error:db.commit()
                    assert error.value.orig.sqlstate=='23514',(damage,error.value.orig.sqlstate)
            finally:event.remove(db,'before_flush',mutate);db.rollback()
        assert inventory_snapshot(owner)==before
        with owner.connect() as db:assert db.scalar(text('SELECT count(*) FROM stock_operation_receipts'))==0
        rejected.append(damage)
        print('PG16 loss receipt '+context['tracking']+': '+damage+' COMMIT rollback PASS',flush=True)
    before=inventory_snapshot(owner)
    with Session(api) as db:
        actor,request,preview=request_for(db)
        sealed=recovery.seal_receipt_request(db,actor=actor,shipment_id=shipped.shipment_id,request_id=request.request_id,request_hash=preview.request_hash)
        db.commit()
        assert sealed.seal.origin==preview.origin
        assert recovery.lookup_receipt_request(db,actor=actor,shipment_id=shipped.shipment_id,request_id=request.request_id)==sealed
        with pytest.raises(InventoryReadError):commands.execute_receipt(db,actor=actor,shipment_id=shipped.shipment_id,request=request)
        db.rollback()
    requests=[]
    for _ in range(2):
        with Session(api) as db:requests.append(request_for(db)[1])
    barrier=Barrier(2)
    def receive(index):
        with Session(api) as db:
            actor=load_formal_principal(db,receiver_id)
            barrier.wait(timeout=30)
            try:
                result=commands.execute_receipt(db,actor=actor,shipment_id=shipped.shipment_id,request=requests[index])
                db.commit();return (index,result,None)
            except InventoryReadError as error:
                db.rollback();return (index,None,error.code)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(receive,range(2)))
    winners=[value for value in results if value[1] is not None]
    assert len(winners)==1 and len([value for value in results if value[2]])==1
    index,accepted,_=winners[0];request=requests[index]
    with Session(api) as db:
        actor=load_formal_principal(db,receiver_id)
        assert commands.execute_receipt(db,actor=actor,shipment_id=shipped.shipment_id,request=request)==accepted
        assert recovery.lookup_receipt_request(db,actor=actor,shipment_id=shipped.shipment_id,request_id=request.request_id)==accepted
    # Both retries return the same committed fact, without an additional receipt.
    requests=[request,request];barrier=Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:replayed=list(pool.map(receive,range(2)))
    assert all(value[1]==accepted and value[2] is None for value in replayed)
    print('PG16 loss receipt '+context['tracking']+': concurrent full acceptance single winner and exact replay PASS',flush=True)
    assert inventory_snapshot(owner)==before
    print('PG16 loss receipt '+context['tracking']+': acceptance and recovery stock-neutral PASS',flush=True)
    inbound_rejected=[]
    for damage in ('manifest','missing_target'):
        before=inventory_snapshot(owner)
        with Session(api) as db:
            actor=load_formal_principal(db,receiver_id)
            planned=inbound.preview_return_inbound(db,actor=actor,receipt_id=accepted.receipt_id)
            assert all(db.get(StockAccount,UUID(line['target_account_id'])) is None for line in planned['lines'])
            def mutate_inbound(session,*_):
                for row in tuple(session.new):
                    if isinstance(row,NotificationEvent) and row.event_type=='stock_return_inbound_posted' and damage=='manifest':
                        row.target_manifest_sha256='0'*64
                    elif isinstance(row,NotificationPersonTarget) and damage=='missing_target':session.expunge(row)
            event.listen(db,'before_flush',mutate_inbound)
            try:
                with patch.object(inbound,'inbound_result',return_value=None):
                    inbound.execute_return_inbound(db,actor=actor,receipt_id=accepted.receipt_id,
                        expected_plan_hash=planned['plan_hash'],request_id=uuid4().hex,idempotency_key=uuid4().hex)
                    with pytest.raises(DBAPIError) as error:db.commit()
                    assert error.value.orig.sqlstate=='23514',(damage,error.value.orig.sqlstate)
            finally:event.remove(db,'before_flush',mutate_inbound);db.rollback()
        assert inventory_snapshot(owner)==before
        with owner.connect() as db:assert db.scalar(text('SELECT count(*) FROM stock_operation_return_inbounds'))==0
        inbound_rejected.append(damage)
        print('PG16 loss inbound '+context['tracking']+': '+damage+' COMMIT rollback PASS',flush=True)
    with Session(api) as db:
        actor=load_formal_principal(db,receiver_id)
        preview=inbound.preview_return_inbound(db,actor=actor,receipt_id=accepted.receipt_id)
        abandoned=uuid4().hex
        digest=inbound._request_hash(receipt_id=accepted.receipt_id,request_id=abandoned,plan_hash=preview['plan_hash'])
        sealed=inbound_recovery.seal_return_inbound_request(db,actor=actor,receipt_id=accepted.receipt_id,request_id=abandoned,request_hash=digest)
        db.commit()
        assert inbound_recovery.lookup_return_inbound_request(db,actor=actor,receipt_id=accepted.receipt_id,request_id=abandoned)==sealed
        with pytest.raises(InventoryReadError):inbound.execute_return_inbound(db,actor=actor,receipt_id=accepted.receipt_id,
            expected_plan_hash=preview['plan_hash'],request_id=abandoned,idempotency_key=uuid4().hex)
        db.rollback()
        assert all(db.get(StockAccount,UUID(line['target_account_id'])) is None for line in preview['lines'])
        command=dict(actor=actor,receipt_id=accepted.receipt_id,expected_plan_hash=preview['plan_hash'],request_id=uuid4().hex,idempotency_key=uuid4().hex)
        posted=inbound.execute_return_inbound(db,**command)
        db.commit()
        assert all(db.get(StockAccount,UUID(line['target_account_id'])) is not None for line in preview['lines'])
        assert inbound_recovery.lookup_return_inbound_request(db,actor=actor,receipt_id=accepted.receipt_id,request_id=command['request_id'])['inbound_id']==posted['inbound_id']
        replay=inbound.execute_return_inbound(db,**command)
        assert replay['replayed'] and replay['inbound_id']==posted['inbound_id']
        # The current serial position changed; original acceptance remains provable.
        assert facts.verified_receipt_history(db,fact=db.get(StockOperationReceipt,accepted.receipt_id))==accepted
    with Session(owner) as db:db.execute(text('SELECT public.rsc_check_loss_receipt_0155(:id,false)'),{'id':accepted.receipt_id})
    return dict(passed=True,tracking=context['tracking'],stockNeutralReceipt=True,independentInbound=True,
        firstNewConditionAccountCreated=True,malformedInboundRollbacks=inbound_rejected,concurrentAcceptanceSingleWinner=True,concurrentExactReplay=True,currentReceiverExpiryAtCommit=True,checkedLocks=locked,inboundRequestRecovery=True,inboundSealPreventsLateWrite=True,malformedRollbacks=rejected,exactReplay=True,receiptRequestRecovery=True,receiptSealPreventsLateWrite=True,historyAfterInbound=True)


def run_sources(engines,tracking):
    original=source_gate.prepare_stocktake_inventory
    def prepare(owner,edge,**kwargs):
        fixture=original(owner,edge,**kwargs)
        with Session(owner) as db:
            count=len(tuple(db.scalars(select(StockAccount.id).where(StockAccount.location_id==fixture['location_id']))))
        _establish_multiround_stocktake_location(engines['star_oam_api'],fixture={**fixture,'recount_location_id':fixture['location_id']},
            actor_user_id=kwargs['actor_user_id'],assignee_user_id=kwargs['assignee_user_id'],expected_snapshot_line_count=count)
        return fixture
    with patch.object(source_gate,'prepare_stocktake_inventory',prepare),patch.object(shipment_gate,'exercise',exercise):
        return shipment_gate.run_sources(engines,tracking=tracking)


def release(engines,*,tracking,migrate,provision):
    from app.database_security import validate_production_database_security
    from pg16_stock_loss_derived_return_gate import catalog
    owner,api=engines['star_oam_migrator'],engines['star_oam_api']
    def security():validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    migrate('initial-upgrade','upgrade','head');provision();security()
    before=catalog(owner)
    migrate('empty-downgrade','downgrade','20261203_0154')
    migrate('empty-reupgrade','upgrade','head')
    assert catalog(owner)==before
    security()
    for sql in ('SELECT public.rsc_check_loss_receipt_0155(:id,false)', 'SELECT public.rsc_guard_loss_receipt_insert_0155()',
                'SELECT public.rsc_guard_loss_receipt_notification_0155()'):
        with api.connect() as db:
            with pytest.raises(DBAPIError) as error:db.execute(text(sql),{'id':uuid4()})
            assert error.value.orig.sqlstate=='42501';db.rollback()
    result=run_sources(engines,tracking)
    result['returnReceipt']=result.pop('submission')
    before=catalog(owner)
    migrate('retained-receipt-downgrade','downgrade','20261203_0154','0162 immutable condition-aware inbound history requires retention')
    assert catalog(owner)==before
    security()
    result.update(runtimeSecurity=True,emptyMigrationRoundtripCatalogAndAclExact=True,retainedHistoryBlocksDowngrade=True,
        privateProofExecutionDenied=True,migrationHead='20261212_0163',productionAcceptance=False)
    return result
