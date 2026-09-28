"""Actual loss-return departures on owned native and disposable hosted PG16."""
from datetime import datetime, timedelta, timezone
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



def snapshot(owner):
    result = stock_snapshot(owner)
    with owner.connect() as db:
        for table in ('stock_operation_outbounds', 'stock_operation_outbound_lines', 'stock_operation_outbound_serials'):
            result[table] = tuple(sorted(repr(dict(row)) for row in db.execute(text('SELECT * FROM '+table)).mappings()))
    return result


def run_sources(engines, *, tracking):
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
        captured.update(transit_id=transit_id)
        return fixture
    with patch.object(source_gate, 'prepare_stocktake_inventory', prepare):
        return source_gate.run(engines, tracking=tracking, after_preview=lambda context: exercise({**context, **captured}))


def exercise(context):
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
    amount = Decimal('1') if context['tracking']=='serial' else Decimal('.100')
    rejected = []
    damage_modes = ('missing_notification', 'missing_target', 'wrong_target', 'payload', 'manifest',
        'origin_plan', 'line_source', 'line_quantity', 'line_snapshot_keys', 'material_snapshot', 'route_snapshot', 'actor')
    if context['tracking'] == 'serial': damage_modes += ('missing_serial',)
    for damage in damage_modes:
        before = snapshot(owner)
        with Session(api) as db:
            actor, request = request_for(db, amount)
            recorder = commands.record_stock_return_notification
            def damaged_notification(session, **kwargs):
                if damage == 'missing_notification': return None
                if damage == 'missing_target': kwargs['recipient_person_id'] = None
                if damage == 'wrong_target': kwargs['recipient_person_id'] = actor.person_id
                if damage == 'payload': kwargs['payload'] = {**kwargs['payload'], 'origin_kind':'work_order_recovery'}
                return recorder(session, **kwargs)
            damaged = set()
            def before_insert(session, _flush, _instances):
                for fact in tuple(session.new):
                    # Balance and serial-position projections have their own keys.
                    # Only the four fact models below belong to this corruption fixture.
                    if not isinstance(fact, (StockOperationOutbound, StockOperationOutboundLine,
                                             StockOperationOutboundSerial, NotificationEvent)):
                        continue
                    if fact.id in damaged: continue
                    if isinstance(fact, StockOperationOutbound):
                        if damage == 'origin_plan':
                            fact.plan_jsonb = {**fact.plan_jsonb, 'origin':{**fact.plan_jsonb['origin'], 'loss_line_id':str(uuid4())}}
                        elif damage in ('line_snapshot_keys', 'material_snapshot'):
                            changes = {'extra_source':'forged'} if damage == 'line_snapshot_keys' else {'material_name':'FORGED'}
                            fact.plan_jsonb = {**fact.plan_jsonb, 'lines':[{**fact.plan_jsonb['lines'][0], **changes}]}
                        elif damage == 'route_snapshot':
                            fact.plan_jsonb = {**fact.plan_jsonb, 'destination':{**fact.plan_jsonb['destination'], 'region_org_id':str(uuid4())}}
                        elif damage == 'actor': fact.actor_user_id = context['admin_id']
                        fact.plan_hash = _hash(fact.plan_jsonb)
                        damaged.add(fact.id)
                    elif isinstance(fact, StockOperationOutboundLine):
                        if damage == 'line_source': fact.operation_line_id = UUID(derived['line_id'])
                        elif damage == 'line_quantity': fact.quantity += Decimal(1)
                        damaged.add(fact.id)
                    elif isinstance(fact, StockOperationOutboundSerial) and damage == 'missing_serial':
                        session.expunge(fact)
                    elif isinstance(fact, NotificationEvent) and fact.event_type == 'stock_return_outbound' and damage == 'manifest':
                        fact.target_manifest_sha256 = '0' * 64
                        damaged.add(fact.id)
            event.listen(db, 'before_flush', before_insert)
            try:
                with patch.object(commands, 'record_stock_return_notification', damaged_notification), \
                     patch.object(facts, 'outbound_result', return_value=None):
                    commands.execute_outbound(db, actor=actor, work_order_id=None, operation_id=child_id, request=request)
                    with pytest.raises(DBAPIError) as error: db.commit()
                    assert error.value.orig.sqlstate == '23514', (damage, error.value.orig.sqlstate, str(error.value.orig))
            finally:
                event.remove(db, 'before_flush', before_insert)
            db.rollback()
        assert snapshot(owner) == before
        rejected.append(damage)
        print("PG16 expanded loss outbound " + context["tracking"] + ": rollback " + damage + " PASS", flush=True)
    # A genuine command returns while permission is valid, but COMMIT must
    # reject if the real clock passes the assignment's expiry afterwards.
    with Session(owner) as db:
        assignment = db.scalars(select(RoleAssignment).where(RoleAssignment.user_id==context['engineer_id'])).one()
        assignment_id, original_end = assignment.id, assignment.valid_to
        expiry = db.scalar(text('SELECT clock_timestamp()')) + timedelta(seconds=60)
        assignment.valid_to = expiry; db.commit()
    before = snapshot(owner)
    try:
        with Session(api) as db:
            actor, request = request_for(db, amount)
            commands.execute_outbound(db, actor=actor, work_order_id=None, operation_id=child_id, request=request)
            now = db.scalar(text('SELECT clock_timestamp()')); assert now < expiry
            db.execute(text('SELECT pg_sleep(:delay)'), {'delay':(expiry-now).total_seconds()+0.1})
            with pytest.raises(DBAPIError) as error: db.commit()
            assert error.value.orig.sqlstate == '23514'
            db.rollback()
        assert snapshot(owner) == before
    finally:
        with Session(owner) as db:
            db.get(RoleAssignment, assignment_id).valid_to = original_end; db.commit()
    results = []
    for quantity in ([amount] if context['tracking']=='serial' else [amount, Decimal('.150')]):
        with Session(api) as db:
            actor, request = request_for(db, quantity)
            result = commands.execute_outbound(db, actor=actor, work_order_id=None, operation_id=child_id, request=request)
            db.commit(); results.append(result)
            before = snapshot(owner)
            replay = commands.execute_outbound(db, actor=actor, work_order_id=None, operation_id=child_id, request=request)
            db.commit(); assert replay == result and snapshot(owner) == before
    with Session(api) as db:
        assert db.get(StockBalance, pending_id).quantity == 0
        for result in results:
            assert result.origin.origin_kind == 'loss_report' and result.lines[0].condition_code == 'new'
            assert result.operator_person_id == context['person_id']
            if proofs:
                from app.inventory_models import StockAccount
                account = db.get(StockAccount, db.get(SerialCurrentPosition, proofs[0].serial_id).stock_account_id)
                assert account.location_id == context['transit_id'] and account.custodian_person_id == context['person_id']
    # A later permission change must not invalidate immutable historical proof.
    with Session(owner) as db:
        grant = db.scalars(select(RolePermission).join(Role, Role.id==RolePermission.role_id)
            .join(Permission, Permission.id==RolePermission.permission_id)
            .where(Role.code=='technician', Permission.resource=='stock_operation', Permission.action=='outbound_return')).one()
        grant_id, effect = grant.id, grant.effect; grant.effect='deny'; db.commit()
    try:
        with Session(owner) as db:
            for result in results:
                db.execute(text('SELECT public.rsc_check_loss_outbound_0153(:id,false)'), {'id':result.outbound_id})
            db.commit()
        with Session(api) as db:
            actor = load_formal_principal(db, context['engineer_id'])
            for result in results:
                assert facts.outbound_result(db, actor=actor, fact=db.get(StockOperationOutbound,result.outbound_id)) == result
    finally:
        with Session(owner) as db:
            db.get(RolePermission,grant_id).effect=effect; db.commit()
    return dict(passed=True, actualOutboundSubmitted=True, actualTransitOpening=True, malformedCommitRollbacks=rejected,
        departedCount=len(results), exactReplay=True, newConditionPreserved=True, engineerDistinctFromHQ=True,
        historicalProofAfterPermissionRevocation=True, actualCommandExpiryAtCommitRejected=True,
        fullMigrationImplemented=True)


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
    before = catalog(owner)
    migrate('empty-downgrade', 'downgrade', '20261201_0152')
    migrate('empty-reupgrade', 'upgrade', 'head')
    assert catalog(owner) == before
    security()
    result = run_sources(engines, tracking=tracking)
    result['returnOutbound'] = result.pop('submission')
    assert result['passed'] and result['returnOutbound']['passed']
    before_facts, before = snapshot(owner), catalog(owner)
    migrate('retained-loss-outbound-downgrade', 'downgrade', '20261201_0152',
        '0153 loss outbound history requires retention')
    assert snapshot(owner) == before_facts and catalog(owner) == before
    security()
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD_REVISION
    result.update(runtimeSecurityBeforeAndAfter=True, emptyMigrationRoundtripCatalogAndAclExact=True,
        lossOutboundHistoryPreventsDowngrade=True, migrationHead=HEAD_REVISION, productionAcceptance=False)
    return result
