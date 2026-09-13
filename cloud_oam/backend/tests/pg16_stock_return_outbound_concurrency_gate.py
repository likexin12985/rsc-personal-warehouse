"""Real competing API connections retain one physical departure or one seal."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from threading import Barrier,Event
import time
from types import SimpleNamespace
from uuid import UUID,uuid4
from sqlalchemy import select,text,func
from sqlalchemy.orm import Session
from app.demand_models import WorkOrderReplacement,WorkOrderMaterialLine,WorkOrderMaterialSerial
from app.foundation_models import SourceSystem
from app.inventory_models import StockAccount,InventorySerial,FormalMaterial,InventoryTransaction
from app.formal_access import load_formal_principal
from app.stock_operation_models import StockOperationLine,StockOperationOutbound,StockOperationCancellation,StockOperationCommandSeal
from app.stock_return_schemas import StockReturnPreviewIn,StockReturnSubmitIn,StockReturnCancelIn
from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn,StockReturnOutboundSubmitIn
from app.formal_services import stock_return_commands as returns,stock_return_outbound_commands as departures,stock_return_recovery as recovery,inventory_posting as posting
from app.formal_services.stock_return_plan import preview_return
from app.formal_services.stock_return_outbound_plan import preview_outbound
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.oam_work_order_projection import SOURCE_SYSTEM_CODE
from pg16_work_order_reversal_submit_gate import _original
from pg16_work_order_material_gate import _checkpoint
from work_order_fixtures import add_order


def _prepare_return(api_engine,fixture_engine,prepared):
    account_id,user_id,_orders,line=prepared['world']
    with Session(fixture_engine) as db:
        account=db.get(StockAccount,account_id)
        source=db.scalars(select(SourceSystem).where(SourceSystem.code==SOURCE_SYSTEM_CODE)).one()
        context=SimpleNamespace(person=SimpleNamespace(id=account.custodian_person_id),organization=SimpleNamespace(id=account.owner_org_id))
        work_order_id=add_order(db,context,source).id
        db.commit()
    world=(account_id,user_id,(work_order_id,),line)
    with Session(api_engine,expire_on_commit=False) as db:
        selected=_original(db,world,'replace')
        parent=db.get(WorkOrderReplacement,UUID(selected['original_replacement_id']))
        origin=db.scalars(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id==parent.recover_operation_id)).one()
        account=db.get(StockAccount,origin.stock_account_id);sku=db.get(FormalMaterial,account.material_id)
        serials=tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(select(WorkOrderMaterialSerial.serial_id).where(WorkOrderMaterialSerial.operation_line_id==origin.id)))))
        proofs=[dict(serial_id=sn.id,sku_code=sku.sku_code,serial_no=sn.serial_no,qr_code=sn.qr_code) for sn in serials]
        actor=load_formal_principal(db,user_id)
        request=StockReturnPreviewIn(operator_person_id=actor.person_id,target_location_id=prepared['target_id'],transit_location_id=prepared['transit_id'],
            reason='Synthetic concurrency original return',lines=[dict(source_recovery_line_id=origin.id,stock_account_id=account.id,quantity='1',serial_verifications=proofs)])
        checked,_=preview_return(db,actor=actor,work_order_id=work_order_id,request=request)
        submitted=returns.submit_return(db,actor=actor,work_order_id=work_order_id,request=StockReturnSubmitIn(**request.model_dump(),
            expected_plan_hash=checked.plan_hash,idempotency_key=uuid4().hex,request_id=uuid4().hex));_checkpoint(db)
        returned_line=db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id==submitted.operation_id)).one()
        request=StockReturnOutboundPreviewIn(operator_person_id=actor.person_id,outbound_at=datetime.now(timezone.utc),reason='Synthetic concurrent physical departure',
            lines=[dict(operation_line_id=returned_line.id,quantity='1',serial_verifications=proofs)])
        checked,_=preview_outbound(db,actor=actor,work_order_id=work_order_id,operation_id=submitted.operation_id,request=request)
        value=StockReturnOutboundSubmitIn(**request.model_dump(),expected_plan_hash=checked.plan_hash,idempotency_key=uuid4().hex,request_id=uuid4().hex)
        cancellation=StockReturnCancelIn(operator_person_id=actor.person_id,reason='Synthetic cancellation competing with first departure',request_id=uuid4().hex,idempotency_key=uuid4().hex)
        db.commit()
    return dict(user_id=user_id,work_order_id=work_order_id,operation_id=submitted.operation_id,value=value,cancellation=cancellation,request_hash=checked.request_hash)


def _race(api_engine,prepared,operations,*,first=None):
    """Observe the second connection blocked by the first before releasing it."""
    barrier=Barrier(2);release_first=Event();first_locked=Event();second_ready=Event();pids={}
    def worker(index,kind):
        with Session(api_engine) as db:
            pids[index]=db.scalar(text('SELECT pg_backend_pid()'))
            if first is None:barrier.wait(timeout=15)
            elif index==0:
                posting._lock_inventory_ledger_head_for_atomic_batch(db)
                first_locked.set()
                assert release_first.wait(30),'coordinator did not release first command'
            else:second_ready.set()
            try:
                actor=load_formal_principal(db,prepared['user_id'])
                coordinate=dict(actor=actor,work_order_id=prepared['work_order_id'],operation_id=prepared['operation_id'])
                if kind=='outbound':result=departures.execute_outbound(db,**coordinate,request=prepared['value'])
                elif kind=='seal':result=recovery.seal_return_request(db,**coordinate,operation_type='outbound_return',
                    request_id=prepared['value'].request_id,request_hash=prepared['request_hash'])
                else:result=returns.cancel_return(db,**coordinate,request=prepared['cancellation'])
                db.commit();return result
            except InventoryReadError as exc:
                db.rollback();return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        leading=pool.submit(worker,0,operations[0])
        if first is not None:assert first_locked.wait(15),'leading connection did not acquire its ledger lock'
        following=pool.submit(worker,1,operations[1])
        if first is not None:
            try:
                assert second_ready.wait(15),'second connection did not start'
                deadline=time.monotonic()+15;blocked=False
                while time.monotonic()<deadline:
                    with api_engine.connect() as observer:
                        blockers=observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pids[1]})
                    if pids[0] in blockers:blocked=True;break
                    if following.done():raise AssertionError(f'second connection did not wait: {following.result()}')
                    time.sleep(.02)
                assert blocked,'real lock contention was not observed'
            finally:release_first.set()
        results=(leading.result(timeout=240),following.result(timeout=240))
    assert len(set(pids.values()))==2
    return results


def assert_departure_commit_gate(api_engine,fixture_engine,worlds):
    from app.stock_return_schemas import StockReturnSealedOut
    from app.stock_return_outbound_schemas import StockReturnOutboundOut
    cases=[('quantity',('outbound','outbound'),None),('serial',('outbound','outbound'),None),
        ('quantity',('seal','outbound'),'seal'),('quantity',('outbound','seal'),'outbound'),
        ('quantity',('cancel','outbound'),'cancel'),('quantity',('outbound','cancel'),'outbound')]
    for kind,operations,first in cases:
        pending=_prepare_return(api_engine,fixture_engine,worlds[kind])
        results=_race(api_engine,pending,operations,first=first)
        with Session(api_engine) as db:
            actor=load_formal_principal(db,pending['user_id'])
            result=recovery.lookup_return_request(db,actor=actor,work_order_id=pending['work_order_id'],operation_id=pending['operation_id'],
                operation_type='outbound_return',request_id=pending['value'].request_id)
            departures_count=db.scalar(select(func.count()).select_from(StockOperationOutbound).where(StockOperationOutbound.operation_id==pending['operation_id']))
            cancellations_count=db.scalar(select(func.count()).select_from(StockOperationCancellation).where(StockOperationCancellation.operation_id==pending['operation_id']))
            seals_count=db.scalar(select(func.count()).select_from(StockOperationCommandSeal).where(StockOperationCommandSeal.actor_user_id==actor.user_id,StockOperationCommandSeal.request_id==pending['value'].request_id))
            if first=='seal':
                assert isinstance(result,StockReturnSealedOut) and results==(result,'stock_return_request_sealed')
                assert (departures_count,cancellations_count,seals_count)==(0,0,1)
            elif first=='cancel':
                assert result is None and results[1]=='stock_return_already_cancelled'
                assert (departures_count,cancellations_count,seals_count)==(0,1,0)
            else:
                assert isinstance(result,StockReturnOutboundOut)
                assert (departures_count,cancellations_count,seals_count)==(1,0,0)
                assert results==(result,'stock_return_already_outbound') if operations[1]=='cancel' else results==(result,result)
                assert db.scalar(select(func.count()).select_from(InventoryTransaction).where(InventoryTransaction.idempotency_key_hash==posting._storage_hash(pending['value'].idempotency_key)))==1
        print(f'PG16 {kind} {operations}: two API connections, '+('observed ordered lock contention' if first else 'same-key duplicate race')+', one durable outcome and exact request recovery PASS',flush=True)
