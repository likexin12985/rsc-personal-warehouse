"""Formal-head sender closure proofs on caller-owned disposable PostgreSQL 16.

Both physical operations require positive controls, bidirectional exclusion,
actual concurrent execution/sealing and immutable source/audit proof.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import RoleAssignment, RolePermission, Permission, Role
from app.stock_operation_models import StockOperationCommandSeal, StockOperationOutboundLine
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from app.formal_services import loss_return_sender_recovery as recovery, loss_return_sender_seals as seals
from app.formal_services import stock_return_shipment_commands as shipments, stock_return_shipment_plan as plans
from app.formal_services import stock_return_recovery as shared, stock_return_shipment_facts as shipment_facts
from app.formal_services import inventory_posting as posting
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.work_order_return_sources import _hash
from pg16_stock_loss_derived_return_gate import catalog
import pg16_stock_loss_return_shipment_gate as fixture
from app.formal_services import stock_return_outbound_commands as outbounds, stock_return_outbound_plan as departure_plans, stock_return_outbound_facts as outbound_facts


def snapshot(owner):
    result = fixture.snapshot(owner)
    with owner.connect() as db:
        for table in ('stock_operation_outbounds','stock_operation_outbound_lines','stock_operation_outbound_serials',
                      'stock_operation_command_seals', 'stock_operation_receipts',
                      'stock_operation_return_inbounds', 'receipts', 'inbound_orders'):
            result[table] = tuple(sorted(repr(dict(r)) for r in db.execute(text('SELECT * FROM '+table)).mappings()))
    return result


def seal_args(context, world, kind, request, db):
    return dict(actor=load_formal_principal(db, context['engineer_id']), operation_type=kind,
        operation_id=world['order_id'], request=request)


def raw_seal(db, args, *, audit=True):
    request, body, _ = recovery._coordinate(args['operation_type'], args['operation_id'], args['request'])
    from app.formal_services.stock_return_origins import authorize_return_fulfillment
    actor, _, origin = authorize_return_fulfillment(db, actor=args['actor'],
        operation_id=args['operation_id'], action=args['operation_type'])
    now = datetime.now(timezone.utc)
    row = StockOperationCommandSeal(id=uuid4(), actor_user_id=actor.user_id,
        operator_person_id=actor.person_id, oam_work_order_id=None, shipment_id=None,
        operation_id=args['operation_id'], source_loss_disposition_id=origin.disposition_id,
        operation_type=args['operation_type'], authorization_version=actor.authorization_version,
        request_id=request.request_id, request_reference=posting._request_reference(request.request_id),
        request_hash=_hash(body), created_at=now)
    db.add(row)
    if audit:
        append_audit_event(db, stream_key='material_request', actor_user_id=actor.user_id,
            action='stock_return.command_sealed', aggregate_type='stock_operation_command_seal',
            aggregate_id=str(row.id), before_jsonb={}, after_jsonb=seals.payload(row),
            request_id='stock-return-seal:'+str(row.id), occurred_at=now, created_at=now)
    db.flush()



def departure_boundary(context, db, *, actor, work_order_id, operation_id, request):
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    assert work_order_id is None
    world={'order_id':operation_id}
    before=snapshot(owner)
    # The complete unsealed departure must satisfy every deferred proof.
    positive=request.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    outbounds.execute_outbound(db,actor=actor,work_order_id=None,operation_id=operation_id,request=positive)
    db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'));db.rollback()
    assert snapshot(owner)==before
    closed=request.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    sealed=seals.seal_sender_request(db,**seal_args(context,world,'outbound_return',closed,db))
    assert sealed['lookup_status']=='sealed'
    db.commit();before=snapshot(owner)
    plan,_=departure_plans.preview_outbound(db,actor=load_formal_principal(db,context['engineer_id']),
        work_order_id=None,operation_id=operation_id,request=closed)
    late=closed.model_copy(update={'expected_plan_hash':plan.plan_hash});db.rollback()
    for targeted in (False,True):
        # Bypass the app's tombstone refusal and final Python readback only;
        # real inventory posting, audit, notification and SQL constraints run.
        with patch.object(shared,'require_unsealed',return_value=None),patch.object(outbound_facts,'outbound_result',return_value=None):
            outbounds.execute_outbound(db,actor=load_formal_principal(db,context['engineer_id']),
                work_order_id=None,operation_id=operation_id,request=late)
        with pytest.raises(DBAPIError,match='sealed return request cannot execute' if targeted else None) as error:
            if targeted:db.execute(text('SET CONSTRAINTS trg_stock_operation_outbounds_seal_0103 IMMEDIATE'))
            else:db.commit()
        assert error.value.orig.sqlstate=='23514'
        db.rollback()
        assert snapshot(owner)==before
    # Real outbound execution and sealing contend for one original request.
    plan,_=departure_plans.preview_outbound(db,actor=load_formal_principal(db,context['engineer_id']),
        work_order_id=None,operation_id=operation_id,request=request)
    racing=request.model_copy(update={'expected_plan_hash':plan.plan_hash,'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    db.rollback()
    barrier=Barrier(2)
    def compete(action):
        with Session(api) as participant:
            args=seal_args(context,world,'outbound_return',racing,participant)
            barrier.wait(timeout=30)
            if action=='seal':
                answer=seals.seal_sender_request(participant,**args)
                participant.commit()
                return answer['lookup_status'],None
            try:
                answer=outbounds.execute_outbound(participant,actor=args['actor'],work_order_id=None,
                    operation_id=operation_id,request=racing)
                participant.commit()
                return 'executed',answer
            except InventoryReadError as error:
                participant.rollback()
                assert error.code=='stock_return_request_sealed',error.code
                return 'sealed_rejected',None
    with ThreadPoolExecutor(max_workers=2) as pool:
        sealed_outcome,executed_outcome=tuple(pool.map(compete,('seal','execute')))
    assert (sealed_outcome[0],executed_outcome[0]) in (('sealed','sealed_rejected'),('found','executed'))
    # snapshot() additionally covers outbounds and posting for this boundary.
    after=snapshot(owner)
    seal_delta=len(after['stock_operation_command_seals'])-len(before['stock_operation_command_seals'])
    tx_delta=len(after['inventory_transactions'])-len(before['inventory_transactions'])
    assert (seal_delta,tx_delta)==((1,0) if sealed_outcome[0]=='sealed' else (0,1))
    observed=recovery.lookup_sender_request(db,**seal_args(context,world,'outbound_return',racing,db))
    assert observed['lookup_status']==sealed_outcome[0]
    db.rollback()
    if executed_outcome[0]=='executed':
        actual,result=racing,executed_outcome[1]
    else:
        # The sealed race consumed no quantity; execute a distinct fixture
        # command solely to establish the later shipment scenarios.
        plan,_=departure_plans.preview_outbound(db,actor=load_formal_principal(db,context['engineer_id']),
            work_order_id=None,operation_id=operation_id,request=request)
        actual=request.model_copy(update={'expected_plan_hash':plan.plan_hash})
        result=outbounds.execute_outbound(db,actor=load_formal_principal(db,context['engineer_id']),
            work_order_id=None,operation_id=operation_id,request=actual)
        db.commit()
    return actual,result,dict(unsealedAllConstraintsPositiveControl=True,
        sealedOutboundCannotExecuteAtCommit=True,targetedOutboundSealTriggerRejected=True,
        concurrentExecuteSealSingleWinner=True)


def exercise(context):
    captured = []
    departure_proofs = {}
    def actual_departure(db, **kwargs):
        command, result, proofs = departure_boundary(context, db, **kwargs)
        captured.append(command)
        departure_proofs.update(proofs)
        return result
    world = fixture.prepare_departures(context, departure=actual_departure)
    assert len(captured) == 1
    owner, api = world['owner'], world['api']
    with Session(api) as db:
        actor = load_formal_principal(db, context['engineer_id'])
        line = db.scalars(select(StockOperationOutboundLine).where(
            StockOperationOutboundLine.outbound_id == world['first'].outbound_id)).one()
        selected = StockReturnShipmentPreviewIn(operator_person_id=actor.person_id,
            carrier='Synthetic carrier', tracking_no='SYNTHETIC-'+uuid4().hex,
            shipped_at=datetime.now(timezone.utc), reason='Synthetic sender seal boundary',
            lines=(dict(outbound_line_id=line.id, quantity=world['first'].lines[0].selected_quantity,
                serial_ids=tuple(s.serial_id for s in world['first'].lines[0].selected_serials)),))
        preview, _ = plans.preview_shipment(db, actor=actor, work_order_id=None,
            operation_id=world['order_id'], request=selected)
        shipment = StockReturnShipmentSubmitIn(**selected.model_dump(), expected_plan_hash=preview.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
    commands = {'outbound_return':captured[0], 'ship_return':shipment}
    results = {}
    for kind, command in commands.items():
        # This new request has not executed; closure is independent of current
        # availability. It must never move stock or create receipt/inbound facts.
        missing = command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
        before = snapshot(owner)
        with Session(api) as db:
            args = seal_args(context, world, kind, missing, db)
            assert recovery.lookup_sender_request(db, **args) == {'lookup_status':'not_observed','retry_allowed':False}
            result = seals.seal_sender_request(db, **args)
            db.commit()
        after = snapshot(owner)
        changed = {table for table in before if before[table] != after[table]}
        assert changed == {'stock_operation_command_seals','audit_events','audit_chain_heads'}, changed
        assert len(after['stock_operation_command_seals']) == len(before['stock_operation_command_seals'])+1
        assert len(after['audit_events']) == len(before['audit_events'])+1
        assert result['lookup_status'] == 'sealed' and result['retry_allowed'] is False
        with Session(api) as db:
            args = seal_args(context, world, kind, missing, db)
            assert recovery.lookup_sender_request(db, **args) == result
            assert seals.seal_sender_request(db, **args) == result
            db.commit()
        assert snapshot(owner) == after
        service = outbounds.execute_outbound if kind == 'outbound_return' else shipments.execute_shipment
        with Session(api) as db:
            with pytest.raises(InventoryReadError) as error:
                service(db, actor=load_formal_principal(db,context['engineer_id']), work_order_id=None,
                    operation_id=world['order_id'], request=missing)
            assert error.value.code == 'stock_return_request_sealed'
            db.rollback()
        assert snapshot(owner) == after
        # Missing audit must be rejected by the actual deferred database proof.
        malformed = missing.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
        with Session(api) as db:
            raw_seal(db, seal_args(context, world, kind, malformed, db), audit=False)
            with pytest.raises(DBAPIError, match='complete seal audit required') as error:
                db.commit()
            assert error.value.orig.sqlstate == '23514'
            db.rollback()
        assert snapshot(owner) == after
        # Concurrent exact closure serializes on the actual ledger lock.
        concurrent = missing.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
        barrier = Barrier(2)
        def close(_):
            with Session(api) as db:
                args = seal_args(context, world, kind, concurrent, db)
                barrier.wait(timeout=30)
                answer = seals.seal_sender_request(db, **args)
                db.commit()
                return answer
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = tuple(pool.map(close, range(2)))
        assert first == second and first['lookup_status'] == 'sealed'
        concurrent_after = snapshot(owner)
        assert len(concurrent_after['stock_operation_command_seals']) == len(after['stock_operation_command_seals'])+1
        assert len(concurrent_after['audit_events']) == len(after['audit_events'])+1
        # A real grant that expires after the service call must still fail at
        # COMMIT, using PostgreSQL's live clock rather than a patched timer.
        with Session(owner) as db:
            assignment = db.scalars(select(RoleAssignment).where(RoleAssignment.user_id==context['engineer_id'])).one()
            assignment_id, saved_end = assignment.id, assignment.valid_to
            expiry = db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=60)
            assignment.valid_to = expiry
            db.commit()
        try:
            expiring = missing.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
            with Session(api) as db:
                seals.seal_sender_request(db, **seal_args(context, world, kind, expiring, db))
                assert db.scalar(text('SELECT clock_timestamp()')) < expiry
                db.execute(text('SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:expiry-clock_timestamp())))+0.1)'),{'expiry':expiry})
                with pytest.raises(DBAPIError) as error:
                    db.commit()
                assert error.value.orig.sqlstate == '23514'
                assert 'authority' in str(error.value.orig).lower(), str(error.value.orig)
                db.rollback()
        finally:
            with Session(owner) as db:
                db.get(RoleAssignment,assignment_id).valid_to=saved_end
                db.commit()
        assert snapshot(owner) == concurrent_after
        results[kind] = dict(actualApiCommit=True, stockNeutral=True, exactReplay=True,
            malformedAuditRollback=True, concurrentSameSealSingleFact=True, authorityExpiryAtCommit=True)
        print('PG16 formal sender '+context['tracking']+' '+kind+': audited seal, exact recovery, concurrency and expiry PASS',flush=True)
    # Existing actual outbound cannot also acquire a tombstone.
    before = snapshot(owner)
    with Session(api) as db:
        args = seal_args(context,world,'outbound_return',commands['outbound_return'],db)
        assert recovery.lookup_sender_request(db,**args)['lookup_status']=='found'
        assert seals.seal_sender_request(db,**args)['lookup_status']=='found'
        db.commit()
    assert snapshot(owner)==before
    with Session(api) as db:
        raw_seal(db,seal_args(context,world,'outbound_return',commands['outbound_return'],db))
        with pytest.raises(DBAPIError,match='(executed return request cannot be sealed|0110 return request namespace conflict)') as error:
            db.commit()
        assert error.value.orig.sqlstate=='23514'
        db.rollback()
    assert snapshot(owner)==before
    # Independently fire the new proof, without relying on alphabetical trigger
    # order at COMMIT (the existing 0110 namespace also rejects this collision).
    with Session(api) as db:
        raw_seal(db,seal_args(context,world,'outbound_return',commands['outbound_return'],db))
        with pytest.raises(DBAPIError,match='executed return request cannot be sealed') as error:
            db.execute(text('SET CONSTRAINTS trg_stock_operation_seals_proof_0101 IMMEDIATE'))
        assert error.value.orig.sqlstate=='23514'
        db.rollback()
    assert snapshot(owner)==before
    # Real shipment/closure collision at COMMIT: bypass only the app's sealed
    # request check, retain real plan/posting/notifications/database proofs.
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        preview,_=plans.preview_shipment(db,actor=actor,work_order_id=None,
            operation_id=world['order_id'],request=shipment)
        shipment=shipment.model_copy(update={'expected_plan_hash':preview.plan_hash})
        sealed=seals.seal_sender_request(db,**seal_args(context,world,'ship_return',shipment,db))
        db.commit()
    before=snapshot(owner)
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        preview,_=plans.preview_shipment(db,actor=actor,work_order_id=None,
            operation_id=world['order_id'],request=shipment)
        late=shipment.model_copy(update={'expected_plan_hash':preview.plan_hash})
    # Positive control: the same real parcel content without the tombstone must
    # pass every deferred constraint. Roll back the synthetic control so the
    # negative case retains precisely the original unshipped quantities.
    with Session(api) as db:
        positive=late.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
        shipments.execute_shipment(db,actor=load_formal_principal(db,context['engineer_id']),
            work_order_id=None,operation_id=world['order_id'],request=positive)
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        db.rollback()
    assert snapshot(owner)==before
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        assert db.scalar(select(StockOperationCommandSeal.id).where(
            StockOperationCommandSeal.actor_user_id==actor.user_id,
            StockOperationCommandSeal.request_id==late.request_id)) is not None
        with patch.object(shared,'require_unsealed',return_value=None), patch.object(shipment_facts,'shipment_result',return_value=None):
            shipments.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=late)
        with pytest.raises(DBAPIError,match='(sealed return request cannot execute|0110 return request namespace conflict|0104 parcel identity, original return or request mismatch)') as error:
            db.commit()
        assert error.value.orig.sqlstate=='23514'
        db.rollback()
    assert snapshot(owner)==before
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        with patch.object(shared,'require_unsealed',return_value=None), patch.object(shipment_facts,'shipment_result',return_value=None):
            shipments.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=late)
        with pytest.raises(DBAPIError,match='sealed return request cannot execute') as error:
            db.execute(text('SET CONSTRAINTS trg_stock_operation_shipments_seal_0104 IMMEDIATE'))
        assert error.value.orig.sqlstate=='23514'
        db.rollback()
    assert snapshot(owner)==before
    # Actual role grants, rather than a substituted principal, distinguish
    # retained read permission from physical-write permission.
    with Session(owner) as db:
        grants = tuple(db.scalars(select(RolePermission).join(Permission,
            Permission.id==RolePermission.permission_id).join(Role,Role.id==RolePermission.role_id)
            .where(Role.code=='technician',Permission.resource=='stock_operation',
                Permission.action.in_(('read','outbound_return','ship_return')))))
        actions={row.id:db.get(Permission,row.permission_id).action for row in grants}
        assert set(actions.values())=={'read','outbound_return','ship_return'}
        saved={row.id:row.effect for row in grants}
        for row in grants:
            if actions[row.id]!='read':row.effect='deny'
        db.commit()
    try:
        with Session(api) as db:
            args=seal_args(context,world,'ship_return',shipment,db)
            assert recovery.lookup_sender_request(db,**args)==sealed
            with pytest.raises(InventoryReadError) as error:
                seals.seal_sender_request(db,**args)
            assert error.value.status_code==403
            db.rollback()
        with Session(owner) as db:
            for key in saved:
                if actions[key]=='read':db.get(RolePermission,key).effect='deny'
            db.commit()
        with Session(api) as db:
            with pytest.raises(InventoryReadError) as error:
                recovery.lookup_sender_request(db,**seal_args(context,world,'ship_return',shipment,db))
            assert error.value.status_code==403
            db.rollback()
    finally:
        with Session(owner) as db:
            for key,value in saved.items():db.get(RolePermission,key).effect=value
            db.commit()
    assert snapshot(owner)==before
    # Actual competing execution and sealing share one actor/request. Whichever
    # holds the ledger lock first wins; the other must observe that exact fact.
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        planned,_=plans.preview_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=shipment)
        racing=shipment.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex,'expected_plan_hash':planned.plan_hash})
    barrier=Barrier(2)
    def compete(action):
        with Session(api) as db:
            args=seal_args(context,world,'ship_return',racing,db)
            barrier.wait(timeout=30)
            if action=='seal':
                answer=seals.seal_sender_request(db,**args)
                db.commit()
                return answer['lookup_status']
            try:
                shipments.execute_shipment(db,actor=args['actor'],work_order_id=None,operation_id=world['order_id'],request=racing)
                db.commit()
                return 'executed'
            except InventoryReadError as error:
                db.rollback()
                assert error.code=='stock_return_request_sealed',error.code
                return 'sealed_rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        sealing,executing=tuple(pool.map(compete,('seal','execute')))
    assert (sealing,executing) in (('sealed','sealed_rejected'),('found','executed'))
    raced=snapshot(owner)
    new_seals=len(raced['stock_operation_command_seals'])-len(before['stock_operation_command_seals'])
    new_parcels=len(raced['stock_operation_shipments'])-len(before['stock_operation_shipments'])
    assert (new_seals,new_parcels)==((1,0) if sealing=='sealed' else (0,1))
    with Session(api) as db:
        observed=recovery.lookup_sender_request(db,**seal_args(context,world,'ship_return',racing,db))
        assert observed['lookup_status']==sealing
    # Whichever race outcome occurred, establish an actual parcel command for
    # the reverse exclusion proof. Never infer execution from a seal response.
    if executing == 'executed':
        executed_shipment = racing
    else:
        with Session(api) as db:
            actor=load_formal_principal(db,context['engineer_id'])
            plan,_=plans.preview_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=shipment)
            executed_shipment=shipment.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex,'expected_plan_hash':plan.plan_hash})
            shipments.execute_shipment(db,actor=actor,work_order_id=None,operation_id=world['order_id'],request=executed_shipment)
            db.commit()
    before=snapshot(owner)
    with Session(api) as db:
        args=seal_args(context,world,'ship_return',executed_shipment,db)
        assert recovery.lookup_sender_request(db,**args)['lookup_status']=='found'
        assert seals.seal_sender_request(db,**args)['lookup_status']=='found'
        db.commit()
    assert snapshot(owner)==before
    for targeted in (False,True):
        with Session(api) as db:
            raw_seal(db,seal_args(context,world,'ship_return',executed_shipment,db))
            with pytest.raises(DBAPIError,match='executed return request cannot be sealed' if targeted else '(executed return request cannot be sealed|0110 return request namespace conflict)') as error:
                if targeted:db.execute(text('SET CONSTRAINTS trg_stock_operation_seals_proof_0101 IMMEDIATE'))
                else:db.commit()
            assert error.value.orig.sqlstate=='23514'
            db.rollback()
        assert snapshot(owner)==before
    return dict(passed=True, tracking=context['tracking'], actualFormalMigration=True,
        outboundBoundary=departure_proofs, executedShipmentCannotSealAtCommit=True,
        senderSeals=results, executedOutboundCannotSealAtCommit=True,
        sealedShipmentCannotExecuteAtCommit=True, targetedSenderProofsReject=True, unsealedParcelAllConstraintsPositiveControl=True, concurrentExecuteSealSingleWinner=True, actualReadWriteGrantSeparation=True,
        formalMigrationRegistered=True, productionAcceptance=False)


def release(engines, *, tracking, migrate, provision):
    if tracking not in ('quantity','serial'):
        raise ValueError('tracking must be quantity or serial')
    from app.database_security import validate_production_database_security
    migrate('initial-upgrade','upgrade','head')
    migrate('empty-seal-downgrade','downgrade','20261205_0156')
    migrate('empty-seal-reupgrade','upgrade','head')
    provision()
    def security():
        validate_production_database_security(engines['star_oam_api'],
            expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    security()
    result=fixture.run_sources(engines,tracking=tracking,after_preview=exercise)
    assert result['passed'] and result['submission']['passed']
    owner=engines['star_oam_migrator'];before=snapshot(owner);before_catalog=catalog(owner)
    migrate('retained-sender-seal-downgrade','downgrade','20261205_0156','0159 immutable business history requires retention')
    assert snapshot(owner)==before and catalog(owner)==before_catalog
    security()
    result.update(emptyRoundtrip=True,retainedSenderSealsBlockDowngrade=True,
        runtimeSecurityBeforeAndAfter=True,formalMigrationRegistered=True,productionAcceptance=False)
    return result
