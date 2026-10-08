"""Synthetic original loss return built through actual API transactions."""
from datetime import datetime,timezone
from uuid import UUID,uuid4
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.formal_access import load_formal_principal
from pg16_stock_operation_permission_policy import require_formal_grant
from app.inventory_models import CustodyAssignment,StockLocation,StockBalance
from app.stock_operation_models import StockOperationOrder,StockLossDisposition,StockLossHeadquartersDecision,StockLossHeadquartersReview
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.formal_services import stock_loss_return_plan,stock_loss_return_commands,stock_loss_disposition_recovery
from app.formal_services.stock_loss_corrections import reversal_stock,inverse_posting,inverse_recovery,return_stop,bound_commands,sealed_inverse
from app.formal_services.stock_loss_corrections.request_contracts import ReversalPreview,ReversalExecute
from pg16_stock_loss_sources_gate import run as source_fixture
from pg16_stock_loss_return_preview_gate import run as return_fixture


def prepare(context):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    assert return_fixture(context)['passed']
    with Session(owner) as db:
        source=db.get(StockLocation,context['location_id']); receiver=db.get(StockLocation,source.parent_id)
        db.add(CustodyAssignment(location_id=receiver.id,custodian_person_id=receiver.custodian_person_id,
            valid_from=datetime.now(timezone.utc)))
        for action in ('reverse_loss','read'):
            require_formal_grant(db,role_code='admin',action=action)
        db.commit()
    with Session(api) as db:
        decision=db.scalars(select(StockLossHeadquartersDecision)).one();review=db.get(StockLossHeadquartersReview,decision.review_id)
        order=db.get(StockOperationOrder,review.operation_id)
        transit=db.get(StockLocation,context['transit_id']) if 'transit_id' in context else db.scalars(select(StockLocation).where(StockLocation.code.like('LOSS-RETURN-TRANSIT-%'))).one()
        actor=load_formal_principal(db,context['admin_id'])
        original=StockLossReturnExecuteIn(headquarters_decision_id=decision.id,expected_headquarters_review_hash=review.request_hash,
            expected_submission_plan_hash=order.plan_hash,target_location_id=transit.parent_id,transit_location_id=transit.id,
            expected_plan_hash='0'*64,request_id=uuid4().hex,idempotency_key=uuid4().hex)
        preview=stock_loss_return_plan.preview_loss_return(db,actor=actor,request=original)
        original=original.model_copy(update={'expected_plan_hash':preview['plan_hash']})
        original_result=stock_loss_return_commands.execute_loss_return(db,actor=actor,request=original);db.commit()
        root=db.scalars(select(StockLossDisposition)).one()
        selected=ReversalPreview(root_disposition_id=root.id,expected_root_request_hash=root.request_hash,
            expected_submission_plan_hash=order.plan_hash,reason='Synthetic exact unshipped return correction',
            reversed_correction_id=None,expected_execution_request_hash=root.request_hash)
        prepared=reversal_stock.prepare(db,actor=load_formal_principal(db,context['admin_id']),request=selected)
        command=ReversalExecute(**selected.model_dump(),expected_plan_hash=prepared.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex)
        root_id=root.id; source_id=root.target_account_id;target_id=root.source_account_id;amount=root.quantity
    with owner.begin() as db:
        boundary=db.scalar(text('SELECT public.rsc_loss_return_stop_boundary_0163(:id)'),dict(id=root_id))
        assert boundary==prepared.document['return_boundary']
    print('PG16 portable return boundary matches service exactly',flush=True)
    return command,original,original_result,root_id,source_id,target_id,amount
