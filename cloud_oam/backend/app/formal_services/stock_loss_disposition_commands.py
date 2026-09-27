"""Atomic internal loss disposition; caller owns commit and rollback.

No route or production role seed is exposed until database proof, request
recovery, dedicated reversals and clients have passed their acceptance gates.
"""
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from ..foundation_models import OutboxEvent, StateTransitionEvent
from ..inventory_models import InventoryMovement, StockAccount
from ..stock_operation_models import StockLossDisposition as Disposition
from ..stock_loss_schemas import StockLossDispositionExecuteIn
from . import inventory_posting as posting, stock_loss_sources as sources
from . import stock_loss_disposition_plan as plan, stock_loss_disposition_facts as facts
from .audit_chain import append_audit_event
from .notification_events import record_business_notification


def _record(db, *, row, order):
    body = facts.payload(row); at = row.created_at
    append_audit_event(db,stream_key='inventory',actor_user_id=row.actor_user_id,action=facts.KIND,
        aggregate_type=facts.AGGREGATE,aggregate_id=str(row.id),before_jsonb={},after_jsonb=body,
        request_id=row.request_id,occurred_at=at,created_at=at)
    db.add(OutboxEvent(event_type=facts.KIND,aggregate_type=facts.AGGREGATE,aggregate_id=str(row.id),payload_jsonb=body,
        idempotency_key=facts.KIND+':'+str(row.id),available_at=at,created_at=at,updated_at=at))
    db.add(StateTransitionEvent(aggregate_type=facts.AGGREGATE,aggregate_id=str(row.id),from_status='pending',to_status='posted',
        actor_id=row.actor_user_id,reason=facts.KIND,idempotency_key=facts.KIND+':'+str(row.id),
        occurred_at=at,metadata_jsonb=body,created_at=at))
    record_business_notification(db,event_type=facts.KIND,business_type=facts.AGGREGATE,business_id=row.id,
        dedup_key=facts.KIND+':'+str(row.id),payload=body,recipient_person_id=order.requester_id,occurred_at=at,now=at)
    db.flush()


def execute_disposition(db, *, actor, request):
    request = StockLossDispositionExecuteIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    current, order, line, decision = plan.approved_line(db, actor=actor, request=request)
    posting_key = 'stock-loss-disposition:'+posting._require_idempotency_key(request.idempotency_key)
    key = posting._storage_hash(posting_key)
    document = dict(intent=plan.intent(request), request_id=request.request_id, expected_plan_hash=request.expected_plan_hash)
    rows = tuple(db.scalars(select(Disposition).where(or_(Disposition.line_id==line.id,
        Disposition.idempotency_key_hash==key,
        (Disposition.actor_user_id==current.user_id)&(Disposition.request_id==request.request_id)))
        .limit(3).execution_options(populate_existing=True)))
    if rows:
        row = rows[0]
        if (len(rows)!=1 or row.line_id!=line.id or row.actor_user_id!=current.user_id
                or row.executor_person_id!=current.person_id or row.idempotency_key_hash!=key
                or row.command_jsonb!=document or row.request_id!=request.request_id or row.plan_hash!=request.expected_plan_hash):
            sources._fail('stock_loss_disposition_conflict', '原行已处置或请求已绑定其他内容，请回查准确原请求')
        return facts.verified(db,row=row)
    prepared = plan.preview_disposition(db,actor=current,request=request)
    # Missing destination accounts must not be flushed merely to discover the
    # existing opening/principal lock graph. The real command below is two-ended.
    graph = posting.InventoryPostingCommand(transaction_no='loss-disposition-graph',movement_type=prepared['movement_type'],
        source_document_type=facts.AGGREGATE,source_document_id=str(line.id),posting_key='loss-disposition-graph',
        effective_at=prepared['checked_at'],movements=(posting.InventoryMovementCommand(from_account_id=line.reserved_account_id,
            to_account_id=None,quantity=line.quantity,serial_ids=tuple(UUID(s) for s in prepared['serial_ids'])),))
    posting._plan_and_lock_terminal_opening_graphs(db,command=graph,current_actor_user_id=current.user_id)
    prepared = plan.preview_disposition(db,actor=current,request=request)
    if prepared['plan_hash']!=request.expected_plan_hash:
        sources._fail('stock_loss_disposition_plan_changed', '报损处置方案已变化，请重新预览并确认')
    current = plan.authorize(db,current,order)
    source = db.get(StockAccount,line.reserved_account_id,populate_existing=True)
    target = plan.target_account(db,source,decision.disposition)
    if str(target.id)!=prepared['target_account_id']:
        sources._fail('stock_loss_disposition_plan_changed', '目标账户已变化，请重新预览并确认')
    at = prepared['checked_at']
    if target not in db:
        target.created_at=at; target.updated_at=at; db.add(target); db.flush()
    row = Disposition(id=uuid4(),operation_id=order.id,line_id=line.id,headquarters_decision_id=decision.id,
        actor_user_id=current.user_id,executor_person_id=current.person_id,authorization_version=current.authorization_version,
        disposition=decision.disposition,source_account_id=source.id,target_account_id=target.id,
        custody_assignment_id=UUID(prepared['custody_assignment_id']),quantity=line.quantity,request_id=request.request_id,
        idempotency_key_hash=key,request_hash=sources._hash(document),plan_hash=prepared['plan_hash'],
        command_jsonb=document,plan_jsonb=plan.plan_document(prepared),created_at=at)
    posted = posting.post_inventory_transaction(db,actor=current,command=facts.posting_command(row),
        idempotency_key=posting_key,request_id=request.request_id,permission_resource='stock_operation',permission_action=plan.ACTION)
    row.posting_transaction_id=posted.transaction_id
    row.posting_movement_id=db.scalar(select(InventoryMovement.id).where(InventoryMovement.transaction_id==posted.transaction_id))
    db.add(row); db.flush()
    _record(db,row=row,order=order)
    plan.authorize(db,current,order)
    return facts.verified(db,row=row)
