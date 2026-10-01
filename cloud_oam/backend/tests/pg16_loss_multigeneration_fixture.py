"""Unchanged real opening/report/review/original-posting prefix; candidate fixture only."""
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
    return dict(root_id=root_id, original_command=original_command, original_result=original_result, binding=binding)
