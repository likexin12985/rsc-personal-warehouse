"""Actual loss-return departures on owned native and disposable hosted PG16."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from decimal import Decimal
from uuid import UUID, uuid4
from unittest.mock import patch

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import NotificationEvent, OutboxEvent, Permission, Role, RoleAssignment, RolePermission
from app.inventory_models import StockLocation, CustodyAssignment, StockBalance, SerialCurrentPosition
from app.stock_operation_models import StockOperationOrder, StockOperationLine, StockOperationOutbound, StockOperationOutboundLine, StockOperationOutboundSerial, StockLossHeadquartersDecision, StockLossHeadquartersReview
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn, StockReturnOutboundSubmitIn
from app.formal_services import stock_loss_return_plan as derive_plan, stock_loss_return_commands as derive_commands
from app.formal_services import stock_return_outbound_plan as plan, stock_return_outbound_commands as commands, stock_return_outbound_facts as facts
from app.formal_services.work_order_return_sources import _hash
from pg16_stock_loss_return_preview_gate import run as preview
from pg16_stock_loss_submit_gate import snapshot as stock_snapshot
from test_postgresql16_release_gate import _establish_multiround_stocktake_location
import pg16_stock_loss_sources_gate as source_gate
from pg16_loss_return_sender_read_gate import run as sender_read_gate




LOSS_SHIPMENT_PROOF = "rsc_check_loss_shipment_0154"
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from app.formal_services import stock_return_shipment_plan as parcels, stock_return_shipment_commands as ship_commands, stock_return_shipment_facts as ship_facts
from app.inventory_models import Shipment
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import stocktake_task as stocktake, inventory_posting as posting
from app.stocktake_task_schemas import StocktakeTaskCreateIn, StocktakeScopeSelectionIn, StocktakeTaskStartIn
from app.stocktake_models import InventoryFreeze
from app.stock_operation_models import StockOperationShipment, StockOperationShipmentLine, StockOperationShipmentSerial

def run_sources(engines, *, tracking, after_preview=None):
    captured = {}
    original = source_gate.prepare_stocktake_inventory
    def prepare(owner, edge, **kwargs):
        fixture = original(owner, edge, **kwargs)
        with Session(owner) as db:
            transit = StockLocation(code='LOSS-OUTBOUND-TRANSIT-'+uuid4().hex, name='Synthetic established return transit',
                location_type='transit', parent_id=fixture['location_id'], owner_org_id=fixture['region_org_id'], status='active')
            db.add(transit); db.commit(); transit_id = transit.id
        _establish_multiround_stocktake_location(engines['star_oam_api'],
            fixture={**fixture, 'recount_location_id':transit_id}, actor_user_id=kwargs['actor_user_id'],
            assignee_user_id=kwargs['assignee_user_id'], expected_snapshot_line_count=0)
        captured.update(transit_id=transit_id,manager_id=kwargs['assignee_user_id'])
        return fixture
    with patch.object(source_gate, 'prepare_stocktake_inventory', prepare):
        return source_gate.run(engines, tracking=tracking, after_preview=lambda context: (after_preview or exercise)({**context, **captured}))


def prepare_departures(context, *, departure=None):
    assert preview(context)['passed']
    owner, api = (context['engines'][key] for key in ('star_oam_migrator', 'star_oam_api'))
    with Session(owner) as db:
        source = db.get(StockLocation, context['location_id']); target = db.get(StockLocation, source.parent_id)
        prior = db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id == target.id)).one()
        assert prior.valid_to is not None and prior.valid_to <= datetime.now(timezone.utc)
        db.add(CustodyAssignment(location_id=target.id, custodian_person_id=target.custodian_person_id,
            valid_from=datetime.now(timezone.utc))); db.commit()
    with Session(api) as db:
        decision = db.scalars(select(StockLossHeadquartersDecision)).one()
        review = db.get(StockLossHeadquartersReview, decision.review_id)
        parent = db.get(StockOperationOrder, review.operation_id)
        transit = db.get(StockLocation, context['transit_id'])
        actor = load_formal_principal(db, context['admin_id'])
        request = StockLossReturnExecuteIn(headquarters_decision_id=decision.id, expected_headquarters_review_hash=review.request_hash,
            expected_submission_plan_hash=parent.plan_hash, target_location_id=transit.parent_id, transit_location_id=transit.id,
            expected_plan_hash='0'*64, request_id=uuid4().hex, idempotency_key=uuid4().hex)
        planned = derive_plan.preview_loss_return(db, actor=actor, request=request)
        derived = derive_commands.execute_loss_return(db, actor=actor,
            request=request.model_copy(update={'expected_plan_hash':planned['plan_hash']})); db.commit()
        child_id = UUID(derived['return_operation_id'])
        line = db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id == child_id)).one()
        line_id, pending_id = line.id, line.reserved_account_id
    proofs = context['request'].lines[0].serial_verifications
    def request_for(db, quantity):
        actor = load_formal_principal(db, context['engineer_id'])
        request = StockReturnOutboundPreviewIn(operator_person_id=actor.person_id, outbound_at=datetime.now(timezone.utc),
            reason='Synthetic actual loss return departure', lines=(dict(operation_line_id=line_id,
                quantity=quantity, serial_verifications=proofs),))
        value, _ = plan.preview_outbound(db, actor=actor, work_order_id=None, operation_id=child_id, request=request)
        return actor, StockReturnOutboundSubmitIn(**request.model_dump(), expected_plan_hash=value.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
    def depart(quantity):
        with Session(api) as db:
            actor, request = request_for(db, quantity)
            result = (departure or commands.execute_outbound)(db, actor=actor, work_order_id=None, operation_id=child_id, request=request)
            db.commit()
            return result
    world = dict(context=context, owner=owner, api=api, order_id=child_id, depart=depart)
    world['senderReads'] = [sender_read_gate(world, phase='pending_departure',
        departed='0', shipped='0', snapshot=snapshot)]
    first_quantity = Decimal('1') if context['tracking']=='serial' else Decimal('.100')
    world['first'] = depart(first_quantity)
    world['senderReads'].append(sender_read_gate(world, phase='departed_not_shipped',
        departed=first_quantity, shipped='0', snapshot=snapshot))
    return world


def snapshot(owner):
    result = stock_snapshot(owner)
    with owner.connect() as db:
        for table in ('shipments','stock_operation_shipments','stock_operation_shipment_lines','stock_operation_shipment_serials',
            'stocktake_tasks','stocktake_scopes','stocktake_snapshot_lines','stocktake_rounds','stocktake_start_completions','inventory_freezes'):
            result[table] = tuple(sorted(repr(dict(row)) for row in db.execute(text('SELECT * FROM '+table)).mappings()))
    return result


def inventory_snapshot(owner):
    with owner.connect() as db:
        return {table:tuple(sorted(repr(dict(row)) for row in db.execute(text('SELECT * FROM '+table)).mappings()))
            for table in ('stock_accounts','stock_balances','inventory_transactions','inventory_movements',
                'inventory_movement_serials','serial_current_positions','inventory_ledger_heads')}


def complete_transit_stocktake(context, started, departures):
    """Commit each real API lifecycle fact; count the known synthetic parcels."""
    from app.formal_services import stocktake_count as count, stocktake_difference as difference
    from app.formal_services import stocktake_review as review, stocktake_posting as posting, stocktake_close as close
    from app.stocktake_models import StocktakeSnapshotLine, FormalStocktakeTask
    api=context['engines']['star_oam_api']; owner=context['engines']['star_oam_migrator']
    task_id=started.task_id; token=uuid4().hex
    expected=Decimal('1') if context['tracking']=='serial' else Decimal('.250')
    serial_ids=tuple(s.serial_id for s in departures.lines[0].selected_serials)
    before=inventory_snapshot(owner)
    with Session(api) as db:
        rows=tuple(db.scalars(select(StocktakeSnapshotLine).where(StocktakeSnapshotLine.task_id==task_id)))
        assert len(rows)==1 and rows[0].book_qty==expected
        account_id,scope_id=rows[0].stock_account_id,rows[0].scope_id
    def write(service,user_id,step,command):
        with Session(api) as db:
            result=service(db,actor=load_formal_principal(db,user_id),command=command,
                idempotency_key='transit-'+token+'-'+step,idempotency_hmac_secret=b'synthetic-transit-stocktake-secret-only',
                trace_request_id='transit-'+token+'-'+step)
            db.commit()
        print('PG16 transit '+context['tracking']+': '+step+' committed',flush=True)
        return result
    counted=write(count.submit_stocktake_initial_scope_count,context['manager_id'],'count',
        count.SubmitStocktakeInitialScopeCountCommand(task_id=task_id,round_id=started.initial_round_id,scope_id=scope_id,
            count_mode='blind',account_counts=(count.StocktakeSnapshotCountInput(stock_account_id=account_id,
                counted_qty=expected,serial_ids=serial_ids),)))
    assert counted.task_status=='submitted' and counted.round_submitted
    differences=write(difference.generate_stocktake_initial_differences,context['admin_id'],'difference',
        difference.GenerateStocktakeDifferenceCommand(task_id=task_id,round_id=started.initial_round_id,expected_task_version=counted.task_version))
    assert differences.difference_count==0
    regional=write(review.submit_stocktake_region_review,context['manager_id'],'region-review',
        review.SubmitStocktakeReviewCommand(task_id=task_id,round_id=started.initial_round_id,expected_task_version=differences.task_version,
            decision='approve',items=(),comment='Synthetic verified transit physical count'))
    assert regional.resulting_task_status=='hq_review'
    approved=write(review.submit_stocktake_headquarters_review,context['admin_id'],'hq-review',
        review.SubmitStocktakeReviewCommand(task_id=task_id,round_id=started.initial_round_id,expected_task_version=regional.task_version,
            decision='approve',items=(),comment='Synthetic verified transit physical count'))
    assert approved.ready_for_posting
    posted=write(posting.post_approved_stocktake_differences,context['admin_id'],'post',
        posting.PostApprovedStocktakeDifferencesCommand(task_id=task_id,expected_task_version=approved.task_version))
    assert posted.resulting_task_status=='posted' and posted.difference_count==posted.accepted_difference_count==0
    reconciled=write(close.reconcile_posted_stocktake_for_close,context['admin_id'],'reconcile',
        close.ReconcileStocktakeForCloseCommand(task_id=task_id,expected_task_version=posted.task_version))
    assert reconciled.book_total_qty==reconciled.physical_total_qty==expected
    assert reconciled.serial_count==len(serial_ids)
    closed=write(close.close_reconciled_stocktake,context['admin_id'],'close',
        close.CloseReconciledStocktakeCommand(task_id=task_id,expected_task_version=reconciled.task_version))
    assert closed.resulting_task_status=='closed'
    assert inventory_snapshot(owner)==before
    with Session(api) as db:
        assert db.get(FormalStocktakeTask,task_id).status=='closed'
        freeze=db.scalars(select(InventoryFreeze).where(InventoryFreeze.task_id==task_id)).one()
        assert freeze.status=='released' and freeze.valid_to is not None
    return dict(passed=True,taskId=str(task_id),closed=True,freezeReleased=True,inventoryUnchanged=True,
        countedQuantity=str(expected),serialCount=len(serial_ids),independentCommits=7)


def exercise(context):
    world = prepare_departures(context)
    owner, api = world['owner'], world['api']
    first = world['first']
    with Session(api) as db:
        line = db.scalars(select(StockOperationOutboundLine).where(StockOperationOutboundLine.outbound_id==first.outbound_id)).one()
        line_id = line.id

    def request_for(db, departure=first, selected_line=line_id):
        actor = load_formal_principal(db,context['engineer_id'])
        request = StockReturnShipmentPreviewIn(operator_person_id=actor.person_id,carrier='Synthetic carrier',
            tracking_no='SYNTHETIC-'+uuid4().hex,shipped_at=datetime.now(timezone.utc),reason='Synthetic physical loss return handover',
            lines=(dict(outbound_line_id=selected_line,quantity=departure.lines[0].selected_quantity,
                serial_ids=tuple(s.serial_id for s in departure.lines[0].selected_serials)),))
        checked,_ = parcels.preview_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
        return actor,StockReturnShipmentSubmitIn(**request.model_dump(),expected_plan_hash=checked.plan_hash,
            request_id=uuid4().hex,idempotency_key=uuid4().hex)

    def draft_freeze(condition=None):
        with Session(api) as db:
            actor=load_formal_principal(db,context['admin_id'])
            manager=load_formal_principal(db,context['manager_id'])
            location=db.get(StockLocation,context['transit_id'])
            token=uuid4().hex
            draft=StocktakeTaskCreateIn(task_type='sample',region_org_id=location.owner_org_id,
                scopes=(StocktakeScopeSelectionIn(owner_org_id=location.owner_org_id,location_id=location.id,
                    assignee_person_id=manager.person_id,scope_mode='filtered' if condition else 'location_all',
                    condition_code=condition,freeze_mode='hard'),),note='Synthetic shipment freeze boundary')
            made=stocktake.create_stocktake_task_draft(db,actor=actor,idempotency_hmac_secret=b'synthetic-transit-stocktake-secret-only',draft=draft,
                idempotency_key='transit-create-'+token,trace_request_id='transit-create-'+token)
            db.commit();return made.task_id

    def freeze(db,task_id):
        token=uuid4().hex
        result=stocktake.start_stocktake_task(db,actor=load_formal_principal(db,context['admin_id']),
            idempotency_hmac_secret=b'synthetic-transit-stocktake-secret-only',task_id=task_id,
            command=StocktakeTaskStartIn(expected_version=0),idempotency_key='transit-start-'+token,trace_request_id='transit-start-'+token)
        assert result.status=='counting'
        row=db.scalars(select(InventoryFreeze).where(InventoryFreeze.task_id==result.task_id)).one()
        assert row.status=='active' and row.freeze_mode=='hard'
        return result

    # Both the scope and freeze come from the real role-checked draft/start
    # service. No hand-written freeze row or disabled database guard is used.
    for condition in ('used',None):
        task_id=draft_freeze(condition)
        before=snapshot(owner)
        with Session(api) as db:
            started=freeze(db,task_id)
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            db.execute(text('SET CONSTRAINTS ALL DEFERRED'))
            if condition is None:
                with pytest.raises(posting.InventoryPostingError) as denied:request_for(db)
                assert denied.value.code=='inventory_scope_hard_frozen'
                # Bypass only the app freeze check/final read to reach the
                # deferred shipment invariant with otherwise valid facts.
                with patch.object(posting,'_require_no_active_hard_freezes',return_value=None),patch.object(ship_facts,'shipment_result',return_value=None):
                    actor,request=request_for(db)
                    ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
                with pytest.raises(DBAPIError,match='loss shipment transit scope is hard frozen') as denied:db.commit()
                assert denied.value.orig.sqlstate=='23514';db.rollback()
            else:
                actor,request=request_for(db)
                accepted=ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                assert accepted.shipment_id and started.snapshot_line_count==0
                db.rollback()
        assert snapshot(owner)==before
    print('PG16 loss shipment '+context['tracking']+': real transit stocktake hard freeze blocks app and deferred COMMIT; disjoint scope remains valid; full rollback PASS',flush=True)

    # Validate the real command's deferred proof before competing mutations.
    # These are owned synthetic roles, and all probed rows roll back intact.
    before=snapshot(owner)
    locked=[]
    with Session(api) as holder:
        actor,request=request_for(holder)
        pending=ship_commands.execute_shipment(holder,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
        holder.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        order=holder.get(StockOperationOrder,world['order_id'])
        coordinates=dict(source=order.source_location_id,target=order.target_location_id,transit=order.transit_location_id,
            custody=pending.destination.custody_assignment_id,person=context['person_id'],user=context['engineer_id'])
        for label,sql,key in (
            ('source_location','UPDATE public.stock_locations SET status=status WHERE id=:id','source'),
            ('target_location','UPDATE public.stock_locations SET status=status WHERE id=:id','target'),
            ('transit_location','UPDATE public.stock_locations SET status=status WHERE id=:id','transit'),
            ('target_custody','UPDATE public.custody_assignments SET valid_to=valid_to WHERE id=:id','custody'),
            ('actor','UPDATE public.users SET account_status=account_status WHERE id=:id','user'),
            ('person','UPDATE public.people SET employment_status=employment_status WHERE id=:id','person'),
        ):
            with owner.connect() as contender:
                contender.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError) as error:contender.execute(text(sql),{'id':coordinates[key]})
                assert error.value.orig.sqlstate=='55P03',(label,error.value.orig.sqlstate)
                contender.rollback();locked.append(label)
        holder.rollback()
    assert snapshot(owner)==before
    print('PG16 loss shipment '+context['tracking']+': checked command retains route/custody/identity locks until transaction end PASS',flush=True)

    modes = ['missing_notification','missing_target','wrong_target','payload','manifest','origin','line_source','line_quantity',
        'route_snapshot','material_snapshot','actor','ledger_cursor']
    if context['tracking']=='serial':modes.append('missing_serial')
    rejected = []
    for damage in modes:
        before = snapshot(owner)
        with Session(api) as db:
            actor,request = request_for(db)
            recorder = ship_commands.record_stock_return_notification
            def notification(session,**kwargs):
                if damage=='missing_notification':return None
                if damage=='missing_target':kwargs['recipient_person_id']=None
                if damage=='wrong_target':kwargs['recipient_person_id']=actor.person_id
                if damage=='payload':kwargs['payload']={**kwargs['payload'],'origin_kind':'work_order_recovery'}
                return recorder(session,**kwargs)
            changed = set()
            def mutate(session,*_):
                for fact in tuple(session.new):
                    if not isinstance(fact,(Shipment,StockOperationShipment,StockOperationShipmentLine,StockOperationShipmentSerial,NotificationEvent)):
                        continue
                    if (type(fact),fact.id) in changed:continue
                    if isinstance(fact,StockOperationShipment):
                        if damage=='origin':fact.plan_jsonb={**fact.plan_jsonb,'origin':{**fact.plan_jsonb['origin'],'loss_line_id':str(uuid4())}}
                        elif damage=='route_snapshot':fact.plan_jsonb={**fact.plan_jsonb,'destination':{**fact.plan_jsonb['destination'],'target_location_name':'FORGED'}}
                        elif damage=='material_snapshot':fact.plan_jsonb={**fact.plan_jsonb,'lines':[{**fact.plan_jsonb['lines'][0],'material_name':'FORGED'}]}
                        elif damage=='ledger_cursor':fact.plan_jsonb={**fact.plan_jsonb,'ledger_cursor':fact.plan_jsonb['ledger_cursor']+1}
                        fact.plan_hash=_hash(fact.plan_jsonb)
                    elif isinstance(fact,StockOperationShipmentLine):
                        if damage=='line_quantity':fact.quantity+=Decimal(1)
                        elif damage=='line_source':fact.outbound_line_id=uuid4()
                    elif isinstance(fact,StockOperationShipmentSerial) and damage=='missing_serial':session.expunge(fact)
                    elif isinstance(fact,Shipment) and damage=='actor':fact.actor_user_id=context['admin_id']
                    elif isinstance(fact,NotificationEvent) and fact.event_type=='stock_return_shipped' and damage=='manifest':fact.target_manifest_sha256='0'*64
                    changed.add((type(fact),fact.id))
            event.listen(db,'before_flush',mutate)
            try:
                with patch.object(ship_commands,'record_stock_return_notification',notification),patch.object(ship_facts,'shipment_result',return_value=None):
                    # Missing foreign rows can fail at INSERT under an immediate
                    # FK; all other cases must reach the deferred COMMIT proof.
                    if damage=='line_source':
                        with pytest.raises(DBAPIError) as error:
                            ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
                            db.commit()
                        assert error.value.orig.sqlstate in ('23503','23514')
                    else:
                        ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
                        with pytest.raises(DBAPIError) as error:db.commit()
                        assert error.value.orig.sqlstate=='23514', (damage,error.value.orig.sqlstate)
            finally:
                event.remove(db,'before_flush',mutate)
                db.rollback()
        assert snapshot(owner)==before
        rejected.append(damage)
        print('PG16 loss shipment '+context['tracking']+': '+damage+' full rollback PASS',flush=True)

    # Real time and unchanged app clock; only the owned synthetic grant expires.
    with Session(owner) as db:
        assignment=db.scalars(select(RoleAssignment).where(RoleAssignment.user_id==context['engineer_id'])).one()
        assignment_id,old_end=assignment.id,assignment.valid_to
        expiry=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=90)
        assignment.valid_to=expiry;db.commit()
    before=snapshot(owner)
    try:
        with Session(api) as db:
            actor,request=request_for(db)
            ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
            now=db.scalar(text('SELECT clock_timestamp()'));assert now<expiry
            db.execute(text('SELECT pg_sleep(:delay)'),{'delay':(expiry-now).total_seconds()+.1})
            with pytest.raises(DBAPIError) as error:db.commit()
            assert error.value.orig.sqlstate=='23514'
            db.rollback()
        assert snapshot(owner)==before
    finally:
        with Session(owner) as db:db.get(RoleAssignment,assignment_id).valid_to=old_end;db.commit()
    results=[]
    before_stock=inventory_snapshot(owner)
    requests=[]
    for _ in range(2):
        with Session(api) as db:
            _,request=request_for(db);requests.append(request)
    barrier=Barrier(2)
    def race(index):
        with Session(api) as db:
            actor=load_formal_principal(db,context['engineer_id'])
            barrier.wait(timeout=30)
            try:
                value=ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=requests[index])
                db.commit();return dict(index=index,result=value)
            except InventoryReadError as error:
                db.rollback();return dict(index=index,rejected=error.code)
    with ThreadPoolExecutor(max_workers=2) as pool:raced=list(pool.map(race,range(2)))
    winners=[v for v in raced if 'result' in v];losers=[v for v in raced if 'rejected' in v]
    assert len(winners)==len(losers)==1,raced
    assert losers[0]['rejected']=='stock_return_shipment_quantity_exceeded',losers
    result=winners[0]['result'];request=requests[winners[0]['index']];results.append(result)
    after=snapshot(owner);barrier=Barrier(2)
    def replay(_):
        with Session(api) as db:
            actor=load_formal_principal(db,context['engineer_id']);barrier.wait(timeout=30)
            value=ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
            db.commit();return value
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(replay,range(2)))==[result,result]
    assert snapshot(owner)==after
    with Session(api) as db:
        rows=tuple(db.scalars(select(StockOperationShipmentLine).where(StockOperationShipmentLine.outbound_line_id==line_id)))
        assert len(rows)==1 and rows[0].quantity==Decimal(first.lines[0].selected_quantity)
    print('PG16 loss shipment '+context['tracking']+': concurrent overbooking has one winner; concurrent exact replay adds no facts PASS',flush=True)
    assert inventory_snapshot(owner)==before_stock
    if context['tracking']=='quantity':
        second=world['depart'](Decimal('.150'))
        with Session(api) as db:
            second_line=db.scalars(select(StockOperationOutboundLine).where(StockOperationOutboundLine.outbound_id==second.outbound_id)).one()
            actor,request=request_for(db,second,second_line.id)
            before_stock=inventory_snapshot(owner)
            result=ship_commands.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=request)
            db.commit();results.append(result)
        assert inventory_snapshot(owner)==before_stock
    later_task_id=draft_freeze()
    before_stock=inventory_snapshot(owner)
    with Session(api) as db:
        later_freeze=freeze(db,later_task_id)
        db.commit()
        for result in results:
            assert ship_facts.verified_shipment_history(db,fact=db.get(StockOperationShipment,result.shipment_id))==result
    assert inventory_snapshot(owner)==before_stock
    with Session(owner) as db:
        assert db.scalars(select(InventoryFreeze).where(InventoryFreeze.task_id==later_freeze.task_id)).one().status=='active'
        for result in results:db.execute(text(f'SELECT public.{LOSS_SHIPMENT_PROOF}(:id,false)'),{'id':result.shipment_id})
    print('PG16 loss shipment '+context['tracking']+': later committed hard freeze retains earlier immutable parcel history and inventory PASS',flush=True)
    with Session(owner) as db:
        grant=db.scalars(select(RolePermission).join(Role,Role.id==RolePermission.role_id).join(Permission,Permission.id==RolePermission.permission_id)
            .where(Role.code=='technician',Permission.resource=='stock_operation',Permission.action=='ship_return')).one()
        grant_id,effect=grant.id,grant.effect;grant.effect='deny';db.commit()
    try:
        with Session(owner) as db:
            for result in results:db.execute(text(f'SELECT public.{LOSS_SHIPMENT_PROOF}(:id,false)'),{'id':result.shipment_id})
        with Session(api) as db:
            for result in results:
                assert ship_facts.verified_shipment_history(db,fact=db.get(StockOperationShipment,result.shipment_id))==result
                assert result.origin.origin_kind=='loss_report' and result.lines[0].condition_code=='new'
            assert db.scalar(text('SELECT count(*) FROM receipts'))==0
    finally:
        with Session(owner) as db:db.get(RolePermission,grant_id).effect=effect;db.commit()
    closed=complete_transit_stocktake(context,later_freeze,first)
    with Session(api) as db:
        for result in results:
            assert ship_facts.verified_shipment_history(db,fact=db.get(StockOperationShipment,result.shipment_id))==result
    total=context['request'].lines[0].quantity
    world['senderReads'].append(sender_read_gate(world, phase='shipped_history',
        departed=total, shipped=total, snapshot=snapshot, permissions=True))
    return dict(senderReads=world['senderReads'],transitLifecycle=closed,passed=True,tracking=context['tracking'],malformedRollbacks=rejected,actualShipmentCount=len(results),
        stockNeutral=True,exactReplay=True,historyAfterLaterStockPosting=context['tracking']=='quantity',
        historyAfterPermissionRevocation=True,currentAuthorityExpiryAtCommit=True,receiptNotCreated=True,
        newConditionPreserved=True,formalMigrationImplemented=True,productionAcceptance=False,
        checkedCommandLocks=locked,concurrentOverbookingSingleWinner=True,concurrentExactReplay=True,
        hardFreezeNativeProof=True,hardFreezeCreatedByRealTask=True,disjointFreezeChecked=True,historyAfterLaterCommittedFreeze=True)

def release(engines, *, tracking, migrate, provision):
    if tracking not in ('quantity', 'serial'):
        raise ValueError('tracking must be quantity or serial')
    from app.database_security import validate_production_database_security
    from pg16_stock_loss_derived_return_gate import catalog
    from test_postgresql16_release_gate import HEAD_REVISION
    owner, api = (engines[key] for key in ('star_oam_migrator', 'star_oam_api'))

    def security():
        validate_production_database_security(api, expected_runtime_role='star_oam_api',
            expected_migration_role='star_oam_migrator')

    migrate('initial-upgrade', 'upgrade', 'head')
    provision()
    security()
    for sql in (
        "SELECT public.rsc_assert_loss_shipment_authority_0154('synthetic',0,:id,:id)",
        "SELECT public.rsc_check_loss_shipment_0154(:id,false)",
        "SELECT public.rsc_guard_loss_shipment_insert_0154()",
        "SELECT public.rsc_guard_loss_shipment_notification_0154()",
    ):
        with api.connect() as db:
            with pytest.raises(DBAPIError) as denied:
                db.execute(text(sql), {'id': uuid4()})
            assert denied.value.orig.sqlstate == '42501'
            db.rollback()
    before = catalog(owner)
    migrate('empty-downgrade', 'downgrade', '20261202_0153')
    migrate('empty-reupgrade', 'upgrade', 'head')
    assert catalog(owner) == before
    security()
    result = run_sources(engines, tracking=tracking)
    result['returnShipment'] = result.pop('submission')
    assert result['passed'] and result['returnShipment']['passed']
    before_facts, before = snapshot(owner), catalog(owner)
    migrate('retained-loss-shipment-downgrade', 'downgrade', '20261202_0153',
        '0159 immutable business history requires retention')
    assert snapshot(owner) == before_facts and catalog(owner) == before
    security()
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD_REVISION
    result.update(runtimeSecurityBeforeAndAfter=True, emptyMigrationRoundtripCatalogAndAclExact=True,
        privateProofExecutionDenied=True,
        lossShipmentHistoryPreventsDowngrade=True, migrationHead=HEAD_REVISION, productionAcceptance=False)
    return result
