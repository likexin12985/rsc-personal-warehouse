"""Read-only loss-return plans over genuine API-role freeze and approval facts.

Call only with the fresh owned source fixture. No return submission, external
provider or production permission is enabled by this gate.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session
from pg16_stock_operation_permission_policy import uses_migrated_loss_policy

from app.formal_access import load_formal_principal
from app.foundation_models import Organization, Permission, Role, RolePermission
from app.inventory_models import CustodyAssignment, StockAccount, StockLocation
from app.stock_operation_models import StockLossHeadquartersDecision, StockOperationLine
from app.stock_loss_schemas import StockLossSubmitIn, StockLossRegionalReviewIn, StockLossHeadquartersReviewIn
from app.stock_loss_return_schemas import StockLossReturnPreviewIn
from app.formal_services import stock_loss_commands, stock_loss_plan
from app.formal_services import stock_loss_regional_reviews as regional
from app.formal_services import stock_loss_headquarters_reviews as headquarters
from app.formal_services import stock_loss_return_plan as plan
from app.formal_services.inventory_query import InventoryReadError
from pg16_stock_loss_disposition_gate import snapshot
from test_formal_access import make_user, assign


def release(engines, *, tracking, migrate, provision):
    """Same real acceptance body for owned native clusters and disposable CI."""
    if tracking not in ('quantity', 'serial'):
        raise ValueError('tracking must be quantity or serial')
    from app.database_security import validate_production_database_security
    from pg16_stock_loss_sources_gate import run as sources
    from test_postgresql16_release_gate import HEAD_REVISION

    migrate('initial-upgrade', 'upgrade', 'head')
    provision()
    with engines['star_oam_migrator'].connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD_REVISION

    def security():
        validate_production_database_security(engines['star_oam_api'],
            expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
    security()
    result = sources(engines, tracking=tracking, after_preview=run)
    result['returnPreview'] = result.pop('submission')
    assert result['passed'] and result['returnPreview']['passed']
    assert result['returnPreview']['returnSubmissionImplemented'] is False
    security()
    result['runtimeSecurityBeforeAndAfter'] = True
    return result


def run(context):
    owner, api = (context['engines'][key] for key in ('star_oam_migrator', 'star_oam_api'))
    with Session(owner) as db:
        source = db.get(StockLocation, context['location_id'])
        target = db.get(StockLocation, source.parent_id)
        manager, _ = make_user(db, db.get(Organization, source.owner_org_id), name='Synthetic loss-return reviewer')
        roles = {row.code: row for row in db.scalars(select(Role))}
        assign(db, manager, roles['provincial_manager'], scope_type='organization', scope_id=str(source.owner_org_id))
        grant_id = None
        for action, role in ((regional.ACTION, 'provincial_manager'), (headquarters.ACTION, 'admin'), ('dispose_loss', 'admin')):
            permission = db.scalar(select(Permission).where(
                Permission.resource == 'stock_operation', Permission.action == action,
                Permission.field_code == ''))
            if permission is None:
                assert not uses_migrated_loss_policy(db), 'formal loss permission missing'
                permission = Permission(resource='stock_operation', action=action, field_code='',
                    description='Synthetic loss-return preview gate')
                db.add(permission)
                db.flush()
            # Head migrations seed these grants. Reuse their exact definition;
            # never turn a migrated deny into an allow to make the gate pass.
            grant = db.scalar(select(RolePermission).where(
                RolePermission.role_id == roles[role].id,
                RolePermission.permission_id == permission.id))
            if grant is None:
                assert not uses_migrated_loss_policy(db), 'formal loss grant missing'
                grant = RolePermission(role_id=roles[role].id, permission_id=permission.id, effect='allow')
                db.add(grant)
                db.flush()
            assert grant.effect == 'allow', (action, role)
            if action == 'dispose_loss':
                grant_id = grant.id
        # The source opening fixture already establishes the regional custodian.
        assert target.custodian_person_id is not None
        assignment = db.scalar(select(CustodyAssignment).where(CustodyAssignment.location_id == target.id))
        assert assignment is not None and assignment.custodian_person_id == target.custodian_person_id
        transit = StockLocation(code='LOSS-RETURN-TRANSIT-' + uuid4().hex, name='Synthetic loss return transit',
            location_type='transit', parent_id=target.id, owner_org_id=target.owner_org_id, status='active')
        db.add(transit)
        db.commit()
        manager_id, target_id, transit_id = manager.id, target.id, transit.id
        receiver_assignment_id = assignment.id

    with Session(api) as db:
        actor = load_formal_principal(db, context['engineer_id'])
        prepared, _ = stock_loss_plan.preview_loss(db, actor=actor, request=context['request'])
        submission = stock_loss_commands.submit_loss(db, actor=actor, request=StockLossSubmitIn(
            **context['request'].model_dump(), expected_plan_hash=prepared.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex))
        db.commit()
    with Session(api) as db:
        review = regional.verify_regional_loss(db, actor=load_formal_principal(db, manager_id),
            request=StockLossRegionalReviewIn(operation_id=submission.operation_id,
                expected_submission_plan_hash=prepared.plan_hash, comment='Synthetic independent review',
                request_id=uuid4().hex, idempotency_key=uuid4().hex))
        db.commit()
    with Session(api) as db:
        line = db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id == submission.operation_id))
        approval = headquarters.approve_headquarters_loss(db, actor=load_formal_principal(db, context['admin_id']),
            request=StockLossHeadquartersReviewIn(operation_id=submission.operation_id,
                expected_submission_plan_hash=prepared.plan_hash, regional_review_id=review.review_id,
                expected_regional_review_hash=review.request_hash,
                decisions=(dict(line_id=line.id, disposition='return_to_region', reason='Synthetic original-condition return'),),
                comment='Synthetic headquarters approval', request_id=uuid4().hex, idempotency_key=uuid4().hex))
        db.commit()
        decision = db.scalar(select(StockLossHeadquartersDecision).where(StockLossHeadquartersDecision.review_id == approval.review_id))
        request = StockLossReturnPreviewIn(headquarters_decision_id=decision.id,
            expected_headquarters_review_hash=approval.request_hash, expected_submission_plan_hash=prepared.plan_hash,
            target_location_id=target_id, transit_location_id=transit_id)

    statements = []
    def require_select(conn, cursor, statement, parameters, execution, executemany):
        statements.append(statement.split()[0].upper())
        assert statement.lstrip().upper().startswith('SELECT '), 'return preview changed business data'
    before = snapshot(owner)
    event.listen(api, 'before_cursor_execute', require_select)
    try:
        with Session(api) as db:
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            actor = load_formal_principal(db, context['admin_id'])
            first = plan.preview_loss_return(db, actor=actor, request=request)
            again = plan.preview_loss_return(db, actor=actor, request=request)
            assert first['plan_hash'] == again['plan_hash']
            assert first['stock_effect'] == 'none' and first['condition_code'] == 'new'
            assert first['requester_id'] == first['custodian_person_id'] == str(context['person_id'])
            assert first['executor_person_id'] != first['requester_id']
            assert first['quantity'] == ('1.000' if context['tracking'] == 'serial' else '0.250')
            assert len(first['serial_ids']) == int(context['tracking'] == 'serial')
            assert 'work_order_id' not in first and 'source_recovery_line_id' not in first
            assert db.get(StockAccount, UUID(first['pending_account_id'])) is None
            db.commit()
    finally:
        event.remove(api, 'before_cursor_execute', require_select)
    assert statements and set(statements) == {'SELECT'} and snapshot(owner) == before

    with Session(owner) as db:
        now = datetime.now(timezone.utc)
        receiver = db.get(StockLocation, target_id).custodian_person_id
        assert receiver != context['person_id']
        overlap = CustodyAssignment(location_id=context['location_id'], custodian_person_id=receiver,
            valid_from=now, valid_to=now + timedelta(hours=1))
        db.add(overlap)
        db.commit()
        overlap_id = overlap.id
    with Session(api) as db:
        with pytest.raises(InventoryReadError) as error:
            plan.preview_loss_return(db, actor=load_formal_principal(db, context['admin_id']), request=request)
        assert error.value.code == 'stock_loss_return_custody_changed'
    assert snapshot(owner) == before
    with Session(owner) as db:
        db.get(CustodyAssignment, overlap_id).valid_to = datetime.now(timezone.utc)
        db.commit()
    with Session(api) as db:
        # An expired conflicting interval does not permanently poison stock.
        assert plan.preview_loss_return(db, actor=load_formal_principal(db, context['admin_id']),
            request=request)['plan_hash'] == first['plan_hash']
    assert snapshot(owner) == before

    # Re-read live grants instead of trusting the historical approval identity.
    with Session(owner) as db:
        db.get(RolePermission, grant_id).effect = 'deny'
        db.commit()
    with Session(api) as db:
        with pytest.raises(InventoryReadError) as error:
            plan.preview_loss_return(db, actor=load_formal_principal(db, context['admin_id']), request=request)
        assert error.value.code == 'stock_loss_disposition_forbidden'
    assert snapshot(owner) == before
    with Session(owner) as db:
        db.get(RolePermission, grant_id).effect = 'allow'
        assignment = db.get(CustodyAssignment, receiver_assignment_id)
        assert assignment.valid_from < datetime.now(timezone.utc) - timedelta(seconds=1)
        assignment.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    with Session(api) as db:
        with pytest.raises(InventoryReadError) as error:
            plan.preview_loss_return(db, actor=load_formal_principal(db, context['admin_id']), request=request)
        assert error.value.code == 'stock_return_receiver_unresolved'
    assert snapshot(owner) == before
    print('PG16 loss-return ' + context['tracking'] + ': exact approval, SELECT-only plan, live authority and custody PASS', flush=True)
    return dict(passed=True, tracking=context['tracking'], originalConditionPreserved=True,
        exactApproval=True, queryOnlyBusinessFactsUnchanged=True, noPendingAccountCreated=True,
        currentAuthorityRevocationRefused=True, expiredReceiverRefused=True,
        overlappingSourceCustodyRefused=True, expiredOverlapAllowsOriginalPlan=True,
        existingOpeningProofRowLocksRequired=True, returnSubmissionImplemented=False, productionAcceptance=False)
