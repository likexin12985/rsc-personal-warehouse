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
    with Session(api) as db:
        inverse_result=bound_commands.inverse(db,actor=load_formal_principal(db,context['admin_id']),request=inverse_command)
        db.commit()
    approval=CorrectionApprove(**binding,reversal_id=UUID(inverse_result['reversal_id']),
        expected_reversal_hash=inverse_result['request_hash'],disposition=correction_kind,
        request_id=uuid4().hex,idempotency_key=uuid4().hex)
    approvals=exercise_seal(owner,api,context,approval)
    new_approval=approval.model_copy(update=dict(request_id=uuid4().hex,idempotency_key=uuid4().hex))
    with Session(api) as db:
        decision=bound_commands.approve(db,actor=load_formal_principal(db,context['admin_id']),request=new_approval)
        db.commit()
    selection=CorrectionPreview(**binding,reversal_id=approval.reversal_id,
        expected_reversal_hash=approval.expected_reversal_hash,
        correction_decision_id=UUID(decision['correction_decision_id']),expected_correction_decision_hash=decision['request_hash'])
    with Session(api) as db:
        plan=correction_stock.prepare(db,actor=load_formal_principal(db,context['admin_id']),request=selection)
    execution=CorrectionExecute(**selection.model_dump(),expected_plan_hash=plan.plan_hash,
        request_id=uuid4().hex,idempotency_key=uuid4().hex)
    executions=exercise_seal(owner,api,context,execution)
    new_execution=execution.model_copy(update=dict(request_id=uuid4().hex,idempotency_key=uuid4().hex))
    with Session(api) as db:
        result=bound_commands.correct(db,actor=load_formal_principal(db,context['admin_id']),request=new_execution)
        db.commit()
    assert recovery_checks.readonly(api,context,new_execution)['result']==result
    assert recovery_checks.readonly(api,context,approval)['request_state']=='sealed'
    assert recovery_checks.readonly(api,context,execution)['request_state']=='sealed'
    return dict(passed=True,tracking=context['tracking'],approval=approvals,execution=executions,
        explicitNewApprovalAndExecutionCommitted=True,oldRequestsRemainClosedAfterExecution=True,
        actualApiRole=True,readOnlyRecovery=True,formalMigration=False,productionAcceptance=False)


def complete_snapshot(owner):
    names=('inventory_transactions','inventory_movements','stock_balances','serial_current_positions',
        'audit_events','audit_chain_heads','inventory_ledger_heads','outbox_events','notification_events',
        'state_transition_events','stock_loss_request_key_bindings','stock_loss_correction_approval_seals',
        'stock_loss_correction_execution_seals','stock_loss_correction_decisions','stock_loss_correction_executions')
    with owner.connect() as db:
        return {name:db.execute(sa.text('SELECT to_jsonb(t)::text FROM public.'+name+' t ORDER BY to_jsonb(t)::text')).scalars().all()
                for name in names}


def exercise_seal(owner,api,context,request):
    from app.formal_services.stock_loss_corrections import sealed_corrections
    from app.formal_services.inventory_query import InventoryReadError
    from app.stock_loss_correction_models import StockLossCorrectionApprovalSeal,StockLossCorrectionExecutionSeal
    kind='approval_seal' if type(request) is CorrectionApprove else 'correction_seal'
    model=StockLossCorrectionApprovalSeal if type(request) is CorrectionApprove else StockLossCorrectionExecutionSeal
    from pg16_loss_correction_seal_races import run as races
    concurrency=races(owner,api,context,request)
    baseline=complete_snapshot(owner)
    cases=[]
    for mode in ('missing_binding','wrong_raw_key','late_authority'):
        with Session(owner if mode=='late_authority' else api) as db:
            actor=load_formal_principal(db,context['admin_id'])
            try:
                if mode=='late_authority':
                    bound_commands.seal(db,actor=actor,request=request)
                    db.get(User,context['admin_id']).authorization_version+=1
                else:
                    answer=sealed_corrections.seal(db,actor=actor,request=request)
                    if mode=='wrong_raw_key':
                        bound_commands.register(db,kind=kind,identifier=answer['seal']['seal_id'],client_key=uuid4().hex)
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514',str(error.orig)
                expected={'missing_binding':'database-owned request binding required',
                    'wrong_raw_key':'client key does not prove stored action hashes',
                    'late_authority':'current exact privileged actor required'}[mode]
                if mode!='late_authority':assert expected in str(error.orig),str(error.orig)
                db.rollback()
            else:raise AssertionError(mode+' unexpectedly committed')
        assert complete_snapshot(owner)==baseline
        cases.append(mode+'_entire_transaction_rolled_back')
        print(kind+' '+mode+' PASS',flush=True)
    with Session(api) as db:
        answer=bound_commands.seal(db,actor=load_formal_principal(db,context['admin_id']),request=request)
        assert answer['request_state']=='sealed';db.commit()
    assert recovery_checks.readonly(api,context,request)==answer
    stable=complete_snapshot(owner)
    for name in baseline:
        if name not in ('audit_events','audit_chain_heads','stock_loss_request_key_bindings',model.__tablename__):
            assert stable[name]==baseline[name],name
    with Session(api) as db:
        assert bound_commands.seal(db,actor=load_formal_principal(db,context['admin_id']),request=request)==answer
        db.commit()
    assert complete_snapshot(owner)==stable
    action=bound_commands.approve if type(request) is CorrectionApprove else bound_commands.correct
    with Session(api) as db:
        try:action(db,actor=load_formal_principal(db,context['admin_id']),request=request)
        except InventoryReadError as error:assert error.status_code==409
        else:raise AssertionError('sealed request executed')
        db.rollback()
    for mode in ('unrelated_outbox','unrelated_state','same_aggregate_outbox'):
        with Session(api) as db:
            from app.foundation_models import OutboxEvent
            if mode=='unrelated_state':
                db.add(StateTransitionEvent(aggregate_type='unrelated',aggregate_id=str(uuid4()),
                    from_status=None,to_status='posted',actor_id=context['admin_id'],reason='synthetic late alias',
                    idempotency_key=uuid4().hex,metadata_jsonb=dict(request_id=request.request_id),occurred_at=datetime.now(timezone.utc)))
            else:
                aggregate=('stock_loss_correction_approval_seal' if type(request) is CorrectionApprove else 'stock_loss_correction_execution_seal')
                db.add(OutboxEvent(aggregate_type=aggregate if mode=='same_aggregate_outbox' else 'unrelated',
                    aggregate_id=answer['seal']['seal_id'] if mode=='same_aggregate_outbox' else str(uuid4()),
                    event_type='synthetic.late',payload_jsonb=dict(actor_user_id=context['admin_id'],request_id=request.request_id),
                    idempotency_key=uuid4().hex,available_at=datetime.now(timezone.utc)))
            db.flush()
            try:db.commit()
            except DBAPIError as error:assert error.orig.sqlstate=='23514';db.rollback()
            else:raise AssertionError(mode+' contaminated closed request')
        assert complete_snapshot(owner)==stable
        cases.append(mode+'_commit_rejected')
    # API grants deny all rewriting; owner still meets always-on immutable fences.
    for statement in ('UPDATE public.'+model.__tablename__+" SET reason='overwrite' WHERE id=:id",
                      'DELETE FROM public.'+model.__tablename__+' WHERE id=:id',
                      'TRUNCATE public.'+model.__tablename__+', public.stock_loss_request_key_bindings'):
        with owner.begin() as db:
            savepoint=db.begin_nested()
            try:db.execute(sa.text(statement),dict(id=UUID(answer['seal']['seal_id'])))
            except DBAPIError as error:
                assert error.orig.sqlstate=='55000',str(error.orig)
                assert '0090 work order facts are append-only' in str(error.orig),str(error.orig)
                savepoint.rollback()
            else:raise AssertionError('immutable fact mutation accepted')
    assert complete_snapshot(owner)==stable
    cases.extend(('actual_api_commit_and_stock_neutrality','exact_replay_no_second_fact',
        'read_only_original_request_recovery','sealed_late_service_execution_rejected','immutable_update_delete_truncate'))
    print(kind+' COMMIT, replay, readonly recovery, late evidence and immutability PASS',flush=True)
    return dict(cases=cases,concurrency=concurrency)
