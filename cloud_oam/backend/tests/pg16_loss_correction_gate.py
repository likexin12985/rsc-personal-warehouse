"""Real 0159 API transactions with permanent migration grants and typed recovery.

Owned fixture identities only. No temporary correction grants or candidate
installers: the actual Alembic revision and startup catalog are authoritative.
"""
from datetime import datetime,timezone
from uuid import UUID,uuid4
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.foundation_models import Organization,Permission,Role,RolePermission,StateTransitionEvent
from app.inventory_models import StockLocation,StockBalance
from app.stock_operation_models import StockLossDisposition,StockOperationOrder,StockOperationLine,StockLossHeadquartersDecision
from app.stock_loss_schemas import StockLossSubmitIn,StockLossRegionalReviewIn,StockLossHeadquartersReviewIn,StockLossDispositionExecuteIn
from app.formal_services import stock_loss_plan,stock_loss_commands,stock_loss_regional_reviews as regional
from app.formal_services import stock_loss_headquarters_reviews as headquarters
from app.formal_services import stock_loss_disposition_plan as original_plan,stock_loss_disposition_commands as original_commands
from app.formal_services.stock_loss_corrections import bound_commands,reversal_stock,correction_stock
from app.formal_services.stock_loss_corrections.request_contracts import ReversalPreview,ReversalExecute,CorrectionApprove,CorrectionPreview,CorrectionExecute
from app.models import User
from app.database_security import validate_production_database_security
from test_formal_access import make_user,assign
import pg16_loss_correction_bindings as binding_boundaries
import pg16_loss_correction_recovery as recovery_checks

def stock_snapshot(owner):
    with owner.connect() as db:
        return {table:db.execute(sa.text('SELECT to_jsonb(t) FROM public.'+table+' t ORDER BY '+key)).scalars().all()
            for table,key in (('inventory_transactions','id'),('inventory_movements','id'),('stock_balances','stock_account_id'),
                ('serial_current_positions','serial_id'),('audit_chain_heads','stream_key'),
                ('stock_loss_disposition_reversals','id'),('stock_loss_correction_decisions','id'),('stock_loss_correction_executions','id'))}

def exercise(context,*,correction_kind='restore_available'):
    owner, api = (context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        location = db.get(StockLocation, context['location_id'])
        manager,_ = make_user(db,db.get(Organization,location.owner_org_id),name='Synthetic inverse regional reviewer')
        roles = {row.code:row for row in db.scalars(sa.select(Role))}
        assign(db,manager,roles['provincial_manager'],scope_type='organization',scope_id=str(location.owner_org_id))
        for action,role in ((regional.ACTION,'provincial_manager'),(headquarters.ACTION,'admin'),
            ('dispose_loss','admin'),('read','admin'),('reverse_loss','admin'),('approve_loss_correction','admin'),('correct_loss','admin')):
            permission = db.scalar(sa.select(Permission).where(Permission.resource=='stock_operation',Permission.action==action,Permission.field_code==''))
            if permission is None:
                permission=Permission(resource='stock_operation',action=action,field_code='',description='Synthetic native transaction gate only')
                db.add(permission);db.flush()
            if db.scalar(sa.select(RolePermission).where(RolePermission.role_id==roles[role].id,RolePermission.permission_id==permission.id)) is None:
                db.add(RolePermission(role_id=roles[role].id,permission_id=permission.id,effect='allow'))
        db.commit();manager_id=manager.id
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        preview,_=stock_loss_plan.preview_loss(db,actor=actor,request=context['request'])
        submitted=stock_loss_commands.submit_loss(db,actor=actor,request=StockLossSubmitIn(**context['request'].model_dump(),
            expected_plan_hash=preview.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex))
        db.commit()
    with Session(api) as db:
        reviewed=regional.verify_regional_loss(db,actor=load_formal_principal(db,manager_id),request=StockLossRegionalReviewIn(
            operation_id=submitted.operation_id,expected_submission_plan_hash=preview.plan_hash,
            comment='Synthetic independent regional verification',request_id=uuid4().hex,idempotency_key=uuid4().hex))
        db.commit()
    with Session(api) as db:
        line=db.scalars(sa.select(StockOperationLine).where(StockOperationLine.operation_id==submitted.operation_id)).one()
        actor=load_formal_principal(db,context['admin_id'])
        approved=headquarters.approve_headquarters_loss(db,actor=actor,request=StockLossHeadquartersReviewIn(
            operation_id=submitted.operation_id,expected_submission_plan_hash=preview.plan_hash,regional_review_id=reviewed.review_id,
            expected_regional_review_hash=reviewed.request_hash,
            decisions=(dict(line_id=line.id,disposition='restore_available',reason='Synthetic reviewed normal disposition'),),
            comment='Synthetic independent HQ approval',request_id=uuid4().hex,idempotency_key=uuid4().hex))
        db.commit()
        decision=db.scalars(sa.select(StockLossHeadquartersDecision).where(StockLossHeadquartersDecision.review_id==approved.review_id)).one()
        command=StockLossDispositionExecuteIn(headquarters_decision_id=decision.id,expected_headquarters_review_hash=approved.request_hash,
            expected_submission_plan_hash=preview.plan_hash,expected_plan_hash='0'*64,request_id=uuid4().hex,idempotency_key=uuid4().hex)
        command=command.model_copy(update={'expected_plan_hash':original_plan.preview_disposition(db,actor=actor,request=command)['plan_hash']})
        original_command=command
        original_result=original_commands.execute_disposition(db,actor=actor,request=original_command);db.commit()
        root=db.scalars(sa.select(StockLossDisposition)).one();root_id=root.id
        binding=dict(root_disposition_id=root.id,expected_root_request_hash=root.request_hash,
            expected_submission_plan_hash=db.get(StockOperationOrder,root.operation_id).plan_hash,
            reason='Synthetic native correction of reviewed loss')
        inverse_preview=ReversalPreview(**binding,reversed_correction_id=None,expected_execution_request_hash=root.request_hash)
        prepared=reversal_stock.prepare(db,actor=load_formal_principal(db,context['admin_id']),request=inverse_preview)
        inverse_command=ReversalExecute(**inverse_preview.model_dump(),expected_plan_hash=prepared.plan_hash,
            request_id=uuid4().hex,idempotency_key=uuid4().hex)
        source_id,target_id,amount=root.source_account_id,root.target_account_id,root.quantity
    print('actual original API posting and inverse preparation PASS',flush=True)
    binding_cases=binding_boundaries.before_inverse(owner,api,context,inverse_command,stock_snapshot)
    before=stock_snapshot(owner)
    # Change authority only after the service returned. Deferred native proof
    # must reject and roll back the complete stock/event transaction.
    with Session(owner) as db:
        bound_commands.inverse(db,actor=load_formal_principal(db,context['admin_id']),request=inverse_command)
        db.get(User,context['admin_id']).authorization_version+=1
        try:db.commit()
        except DBAPIError as error:assert error.orig.sqlstate=='23514';db.rollback()
        else:raise AssertionError('late authority change committed an inverse')
    assert stock_snapshot(owner)==before and binding_boundaries.count(owner)==0
    with Session(api) as db:
        source_before=db.get(StockBalance,source_id).quantity;target_before=db.get(StockBalance,target_id).quantity
        result=bound_commands.inverse(db,actor=load_formal_principal(db,context['admin_id']),request=inverse_command);db.commit()
        assert db.get(StockBalance,source_id).quantity==source_before+amount
        assert db.get(StockBalance,target_id).quantity==target_before-amount
        recovered=recovery_checks.readonly(api,context,inverse_command)
        assert recovered['request_state']=='found' and recovered['result']==result
        assert recovery_checks.readonly_original(api,context,original_command)==dict(lookup_status='found',retry_permitted=False,disposition=original_result)
        inverse_id=UUID(result['reversal_id']);inverse_hash=result['request_hash']
    print('native inverse API COMMIT, current-authority rollback and exact recovery PASS',flush=True)
    recovery_cases=recovery_checks.corrupted(owner,context,inverse_command,inverse_id)
    assert recovery_checks.readonly(api,context,inverse_command)['request_state']=='found'
    binding_cases.append(binding_boundaries.reused_key_approval(owner,api,context,binding,inverse_id,inverse_hash,inverse_command,stock_snapshot))
    with Session(api) as db:
        approval_command=CorrectionApprove(**binding,reversal_id=inverse_id,expected_reversal_hash=inverse_hash,
            disposition=correction_kind,request_id=uuid4().hex,idempotency_key=uuid4().hex)
        result=bound_commands.approve(db,actor=load_formal_principal(db,context['admin_id']),request=approval_command);db.commit()
        approval_recovered=recovery_checks.readonly(api,context,approval_command)
        assert approval_recovered['request_state']=='found' and approval_recovered['result']==result
        assert recovery_checks.readonly_original(api,context,original_command)==dict(lookup_status='found',retry_permitted=False,disposition=original_result)
        selection=CorrectionPreview(**binding,reversal_id=inverse_id,expected_reversal_hash=inverse_hash,
            correction_decision_id=UUID(result['correction_decision_id']),expected_correction_decision_hash=result['request_hash'])
        prepared=correction_stock.prepare(db,actor=load_formal_principal(db,context['admin_id']),request=selection)
        assert prepared.document['target_requires_creation'] is (correction_kind!='restore_available')
        new_target_id=UUID(prepared.document['target_account_id'])
        if correction_kind!='restore_available':assert db.get(StockBalance,new_target_id) is None
        command=CorrectionExecute(**selection.model_dump(),expected_plan_hash=prepared.plan_hash,
            request_id=uuid4().hex,idempotency_key=uuid4().hex)
        result=bound_commands.correct(db,actor=load_formal_principal(db,context['admin_id']),request=command);db.commit()
        assert db.get(StockBalance,source_id).quantity==source_before
        assert db.get(StockBalance,target_id).quantity==target_before-(amount if correction_kind!='restore_available' else 0)
        if correction_kind!='restore_available':assert db.get(StockBalance,new_target_id).quantity==amount
        recovered=recovery_checks.readonly(api,context,command)
        assert recovered['request_state']=='found' and recovered['result']==result
        assert recovery_checks.readonly_original(api,context,original_command)==dict(lookup_status='found',retry_permitted=False,disposition=original_result)
    print('native independent approval and correction API COMMIT/recovery PASS',flush=True)
    binding_cases.extend(binding_boundaries.seal_provenance(owner,api,context,inverse_command,stock_snapshot))
    stable=stock_snapshot(owner)
    with Session(api) as db:
        db.add(StateTransitionEvent(aggregate_type='unrelated',aggregate_id=str(uuid4()),from_status=None,to_status='posted',
            actor_id=context['admin_id'],reason='unknown state at bound request',idempotency_key=uuid4().hex,
            metadata_jsonb=dict(request_id=command.request_id),occurred_at=datetime.now(timezone.utc)))
        db.flush()  # Valid fixture reaches the deferred COMMIT fence.
        try:db.commit()
        except DBAPIError as error:
            assert error.orig.sqlstate=='23514' and 'detached evidence' in str(error.orig), str(error.orig)
            db.rollback()
        else:raise AssertionError('late detached state contaminated committed recovery')
    assert stock_snapshot(owner)==stable
    from pg16_loss_execution_auth_isolation import verify as verify_auth
    authentication=verify_auth(context['engines'],user_id=context['admin_id'])
    with owner.begin() as db:
        graph=db.scalar(sa.text('SELECT public.rsc_check_loss_history_graph_0159(:id)'),dict(id=root_id))
        assert len(graph['verified_inverse_ids'])==1 and len(graph['verified_correction_ids'])==1
    evidence=dict(passed=True,tracking=context['tracking'],originalApiCommits=1,inverseApiCommits=1,
        independentApprovalApiCommits=1,correctionApiCommits=1,lateAuthorityAtomicRollback=True,
        exactRequestRecovery=True,originalDispositionRecoveryAfterSuccessors=True,
        lateStateEvidenceRejected=True,authenticationIsolation=authentication,
        existingDestinationOnly=correction_kind=='restore_available',newAccountAdmission=correction_kind!='restore_available',
        correctionDisposition=correction_kind,concurrentInverseRaces=False,
        laterInverse=False,returnCompensation=False,scrap=False,formalApplied=True,productionAcceptance=False)
    evidence.update(databaseOwnedKeyBindings=True,bindingCases=binding_cases,bindingCount=4,
        readOnlyTypedBindingRecovery=True,bindingRecoveryCases=recovery_cases)
    return evidence


def run(engines,*,tracking,correction_kind='restore_available'):
    if tracking not in ('quantity','serial') or correction_kind not in ('restore_available','convert_used','convert_damaged'):
        raise ValueError('explicit correction tracking and disposition required')
    with engines['star_oam_migrator'].connect() as db:
        assert tuple(db.scalars(sa.text('SELECT version_num FROM alembic_version')))==('20261212_0163',)
    validate_production_database_security(engines['star_oam_api'],expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    from pg16_stock_loss_sources_gate import run as actual_sources
    result=actual_sources(engines,tracking=tracking,after_preview=lambda context:exercise(context,correction_kind=correction_kind))
    assert result['passed'] and result['submission']['passed']
    validate_production_database_security(engines['star_oam_api'],expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    result.update(migrationHead='20261212_0163',formalMigrationBusinessComposition=True,
        correctionDisposition=correction_kind,permanentMigrationDbPrivileges=True,temporaryCorrectionDbGrants=False,
        businessPermissionsFixtureOnly=True,publicHttpAcceptance=False,productionAcceptance=False)
    return result


def release(engines,*,tracking,migrate,provision,correction_kind='restore_available'):
    if tracking not in ('quantity','serial') or correction_kind not in ('restore_available','convert_used','convert_damaged'):
        raise ValueError('explicit correction tracking and disposition required')
    migrate('initial-correction-upgrade','upgrade','head')
    migrate('empty-correction-downgrade','downgrade','20261207_0158')
    migrate('empty-correction-reupgrade','upgrade','head')
    provision()
    result=run(engines,tracking=tracking,correction_kind=correction_kind)
    migrate('retained-correction-downgrade','downgrade','20261207_0158',
        '0161 immutable correction request history requires retention')
    with engines['star_oam_migrator'].connect() as db:
        assert tuple(db.scalars(sa.text('SELECT version_num FROM alembic_version')))==('20261212_0163',)
    validate_production_database_security(engines['star_oam_api'],expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    result.update(emptyCorrectionRoundtrip=True,retainedCorrectionBlocksDowngrade=True)
    return result
