"""Atomic approved-loss return; caller owns the transaction.

Requires the 0152 provenance migration, deferred proofs and pending-account
admission. Public activation also requires recovery, corrections and fulfillment. Caller owns commit/rollback. No logistics or receiving fact is made.
"""
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import or_, select

from app.foundation_models import OutboxEvent, StateTransitionEvent
from app.inventory_models import CustodyAssignment, InventoryMovement, StockAccount, StockLocation
from app.stock_operation_models import StockLossDisposition as Disposition
from app.stock_operation_models import StockOperationOrder as Order, StockOperationLine as Line, StockOperationSerial as Serial
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services import stock_loss_disposition_plan as approved, stock_loss_disposition_facts as dispositions
from app.formal_services import stock_loss_disposition_commands as disposition_commands
from app.formal_services import stock_loss_return_plan as plan, stock_loss_return_facts as facts
from app.formal_services.stock_return_commands import _fresh_request
from app.formal_services.audit_chain import append_audit_event


def _record_child(db, *, row, child):
    body = facts.child_payload(row, child)
    append_audit_event(db, stream_key='material_request', actor_user_id=row.actor_user_id,
        action=facts.CHILD_KIND, aggregate_type='stock_operation_order', aggregate_id=str(child.id),
        before_jsonb={}, after_jsonb=body, request_id=row.request_id,
        occurred_at=row.created_at, created_at=row.created_at)
    db.add(OutboxEvent(event_type=facts.CHILD_KIND, aggregate_type='stock_operation_order',
        aggregate_id=str(child.id), payload_jsonb=body, idempotency_key=facts.CHILD_KIND+':'+str(child.id),
        available_at=row.created_at, created_at=row.created_at, updated_at=row.created_at))
    db.add(StateTransitionEvent(aggregate_type='stock_operation_order', aggregate_id=str(child.id),
        from_status=None, to_status='submitted', actor_id=row.actor_user_id, reason=facts.CHILD_KIND,
        idempotency_key=facts.CHILD_KIND+':'+str(child.id), metadata_jsonb=body,
        occurred_at=row.created_at, created_at=row.created_at))
    db.flush()


def execute_loss_return(db, *, actor, request):
    request = StockLossReturnExecuteIn.model_validate(request.model_dump())
    posting._require_request_id(request.request_id)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    current, order, origin, decision = approved.approved_line(db, actor=actor, request=request)
    from .stock_loss_disposition_seals import require_unsealed
    require_unsealed(db, actor=current, request=request, flow='return')
    if decision.disposition != 'return_to_region':
        sources._fail('stock_loss_return_decision_required', '必须使用原总部批准的退回决定', 412)
    posting_key = 'stock-loss-return:'+posting._require_idempotency_key(request.idempotency_key)
    key = posting._storage_hash(posting_key)
    document = dict(intent=plan.intent(request), request_id=request.request_id, expected_plan_hash=request.expected_plan_hash)
    existing = tuple(db.scalars(select(Disposition).where(or_(Disposition.line_id == origin.id,
        Disposition.idempotency_key_hash == key,
        (Disposition.actor_user_id == current.user_id) & (Disposition.request_id == request.request_id)))
        .limit(3).execution_options(populate_existing=True)))
    if existing:
        row = existing[0]
        if (len(existing) != 1 or row.line_id != origin.id or row.actor_user_id != current.user_id
                or row.executor_person_id != current.person_id or row.idempotency_key_hash != key
                or row.command_jsonb != document or row.request_id != request.request_id
                or row.plan_hash != request.expected_plan_hash or row.disposition != 'return_to_region'):
            sources._fail('stock_loss_return_request_conflict', '原批准或请求已派生退回，请回查准确原请求')
        return dispositions.verified(db, row=row)
    _fresh_request(db, actor=current, key=key, request_id=request.request_id)
    prepared = plan.preview_loss_return(db, actor=current, request=request)
    graph = posting.InventoryPostingCommand(transaction_no='loss-return-graph', movement_type='reserve',
        source_document_type='stock_operation_return', source_document_id=prepared['derived_return_operation_id'],
        posting_key='loss-return-graph', effective_at=prepared['checked_at'],
        movements=(posting.InventoryMovementCommand(from_account_id=origin.reserved_account_id, to_account_id=None,
            quantity=origin.quantity, serial_ids=tuple(UUID(s) for s in prepared['serial_ids'])),))
    posting._plan_and_lock_terminal_opening_graphs(db, command=graph, current_actor_user_id=current.user_id)
    # API masters remain SELECT-only. Existing posting graph locks the source;
    # the private deferred return proof locks all route parents and custody at
    # COMMIT and then rechecks uniqueness. Parent UPDATE locks block competing
    # custody FK inserts without granting API master UPDATE permission.
    locations = {order.source_location_id, request.target_location_id, request.transit_location_id}
    tuple(db.scalars(posting._select_only_reference_statement(db,
        select(StockLocation).where(StockLocation.id.in_(locations)).order_by(StockLocation.id))))
    tuple(db.scalars(posting._select_only_reference_statement(db,
        select(CustodyAssignment).where(CustodyAssignment.location_id.in_(locations)).order_by(CustodyAssignment.id))))
    prepared = plan.preview_loss_return(db, actor=current, request=request)
    if prepared['plan_hash'] != request.expected_plan_hash:
        sources._fail('stock_loss_return_plan_changed', '报损退回方案已变化，请重新预检')
    current = approved.authorize(db, current, order)
    source = db.get(StockAccount, origin.reserved_account_id, populate_existing=True)
    target = plan.pending_account(db, source)
    if str(target.id) != prepared['pending_account_id']:
        sources._fail('stock_loss_return_plan_changed', '待退回账户已变化，请重新预检')
    at = prepared['checked_at']
    if target not in db:
        target.created_at = at
        target.updated_at = at
        db.add(target)
        db.flush()
    plan_document = {k:v for k,v in prepared.items() if k not in ('checked_at','plan_hash','planning_status','stock_effect')}
    child = Order(id=UUID(prepared['derived_return_operation_id']), operation_no='LOSS-RET-'+key[:24].upper(),
        operation_type='return', status='submitted', oam_work_order_id=None, loss_headquarters_decision_id=decision.id,
        source_location_id=source.location_id, target_location_id=request.target_location_id,
        transit_location_id=request.transit_location_id,
        target_custody_assignment_id=UUID(prepared['destination']['custody_assignment_id']),
        requester_id=order.requester_id, actor_user_id=current.user_id, authorization_version=current.authorization_version,
        reason=decision.reason, request_id=request.request_id, idempotency_key_hash=key,
        request_hash=sources._hash(document), plan_hash=prepared['plan_hash'], command_jsonb=document,
        plan_jsonb=plan_document, created_at=at)
    row = Disposition(id=uuid4(), operation_id=order.id, line_id=origin.id, headquarters_decision_id=decision.id,
        return_operation_id=child.id, actor_user_id=current.user_id, executor_person_id=current.person_id,
        authorization_version=current.authorization_version, disposition='return_to_region',
        source_account_id=source.id, target_account_id=target.id,
        custody_assignment_id=UUID(prepared['source_custody_assignment_id']), quantity=origin.quantity,
        request_id=request.request_id, idempotency_key_hash=key, request_hash=child.request_hash,
        plan_hash=child.plan_hash, command_jsonb=document, plan_jsonb=plan_document, created_at=at)
    posted = posting.post_inventory_transaction(db, actor=current, command=facts.posting_command(row),
        idempotency_key=posting_key, request_id=request.request_id,
        permission_resource='stock_operation', permission_action=approved.ACTION)
    child.posting_transaction_id = row.posting_transaction_id = posted.transaction_id
    row.posting_movement_id = db.scalar(select(InventoryMovement.id).where(InventoryMovement.transaction_id == posted.transaction_id))
    db.add(child)
    db.flush()
    child_line = Line(id=facts.child_line_id(decision.id, origin.id), operation_id=child.id,
        operation_type='return', line_no=1, source_recovery_line_id=None, source_loss_line_id=origin.id,
        stock_account_id=source.id, reserved_account_id=target.id, material_id=source.material_id,
        quantity=origin.quantity, target_condition=source.condition_code, reason=decision.reason, created_at=at)
    db.add(child_line)
    db.flush()
    db.add_all(Serial(line_id=child_line.id, serial_id=UUID(sn), sku_verified=True, qr_verified=True, created_at=at)
        for sn in prepared['serial_ids'])
    db.add(row)
    db.flush()
    _record_child(db, row=row, child=child)
    disposition_commands._record(db, row=row, order=order)
    approved.authorize(db, current, order)
    return dispositions.verified(db, row=row)
