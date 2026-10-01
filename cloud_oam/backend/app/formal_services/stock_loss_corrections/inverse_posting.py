"""Candidate atomic inverse of an original account-to-account loss disposition.

Not registered in a route or runtime migration. PostgreSQL deferred loss
guards, sealed recovery, corrected executions, return-child compensation and
scrap lifecycle remain required before production activation. The caller owns
rollback/commit; a missing result never grants permission to repeat a command.
"""
from . import correction_seal_facts

from uuid import UUID, uuid4

from sqlalchemy import or_, select

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryMovement, InventoryTransaction
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .correction_models import StockLossDispositionReversal as Inverse
from .correction_models import StockLossCorrectionDecision, StockLossCorrectionExecution
from .request_contracts import ReversalExecute, original_request, validate
from .history_inventory import inverse_command
from .historical_original import verify_historical_original
from .history_events import load_event_checked_inventory_history
from .history_chain import verify_inverse as verify_original_inverse
from .reversal_stock import prepare
from . import business_events
from . import request_authority
from .request_evidence_scope import authentication_state


def _fresh(db, *, actor, request, binding):
    correction_seal_facts.require_unsealed(db, actor=actor, request=request)
    for model in (Inverse, StockLossCorrectionDecision, StockLossCorrectionExecution):
        if db.scalar(select(model.id).where(or_(model.idempotency_key_hash == binding.key_hash,
                (model.actor_user_id == actor.user_id) & (model.request_id == request.request_id))).limit(1)):
            sources._fail('loss_inverse_request_requires_recovery', '原请求已有业务坐标，请只读核验原请求，禁止盲目重发', 409)
    if db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.idempotency_key_hash == binding.key_hash).limit(1)):
        sources._fail('loss_inverse_request_outcome_unknown', '原请求已有库存证据，请保留请求核验', 503)
    reference = posting._request_reference(request.request_id)
    if db.scalar(select(AuditEvent.id).where(AuditEvent.actor_user_id == actor.user_id,
            AuditEvent.stream_key.in_(('inventory', 'material_request')),
            AuditEvent.request_id.in_((request.request_id, reference))).limit(1)):
        sources._fail('loss_inverse_request_outcome_unknown', '原请求已有审计证据，请保留请求核验', 503)
    if db.scalar(select(OutboxEvent.id).where(OutboxEvent.payload_jsonb['actor_user_id'].as_string() == actor.user_id,
            OutboxEvent.payload_jsonb['request_id'].as_string() == request.request_id).limit(1)):
        sources._fail('loss_inverse_request_outcome_unknown', '原请求已有事件证据，请保留请求核验', 503)
    if db.scalar(select(StateTransitionEvent.id).where(StateTransitionEvent.actor_id == actor.user_id,
            ~authentication_state(),
            or_(StateTransitionEvent.metadata_jsonb['request_id'].as_string() == request.request_id,
                StateTransitionEvent.metadata_jsonb['request_reference'].as_string() == reference)).limit(1)):
        sources._fail('loss_inverse_request_outcome_unknown', '原请求已有状态证据，请保留请求核验', 503)


def execute_account_inverse(db, *, actor, request):
    """Existing public composition: account conversions only."""
    return _execute(db, actor=actor, request=request, stop_unshipped_return=False)


def execute_unshipped_return_inverse(db, *, actor, request):
    """Dedicated candidate path, intentionally absent from routes/bindings.

    Native stop facts, deferred fences and migration must pass before wiring.
    """
    return _execute(db, actor=actor, request=request, stop_unshipped_return=True)


def _execute(db, *, actor, request, stop_unshipped_return):
    request = validate(request)
    if type(request) is not ReversalExecute:
        raise ValueError('an exact reversal execute request is required')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    refs = request_authority.references(db, actor=actor, request=request)
    current, root, order, execution = refs.actor, refs.root, refs.order, refs.execution
    if stop_unshipped_return:
        if execution is not root or execution.disposition != 'return_to_region':
            sources._fail('loss_return_stop_requires_original_return',
                '本入口仅处理尚未出库的准确原报损退回', 412)
        from .sealed_inverse import require_unsealed
        require_unsealed(db, actor=current, request=request)
    elif execution.disposition not in {
            'restore_available', 'convert_used', 'convert_damaged'}:
        sources._fail('loss_inverse_dedicated_compensation_required',
            '退回与报废须完成各自完整补偿证明后方可过账', 412)
    binding = original_request(actor=current, request=request)
    _fresh(db, actor=current, request=request, binding=binding)
    checked = prepare(db, actor=current, request=request)
    if checked.plan_hash != request.expected_plan_hash:
        sources._fail('loss_inverse_plan_changed', '原冲销方案已变化，请重新预检', 409)
    serials = tuple(UUID(value) for value in checked.document['serial_ids'])
    movement = posting.InventoryMovementCommand(execution.target_account_id, root.source_account_id, root.quantity, serials)
    graph = posting.InventoryPostingCommand(transaction_no='loss-inverse-graph', movement_type='reversal',
        source_document_type='stock_loss_disposition_reversal', source_document_id=str(root.id),
        posting_key='loss-inverse-graph', effective_at=checked.checked_at, movements=(movement,))
    posting._plan_and_lock_terminal_opening_graphs(db, command=graph, current_actor_user_id=current.user_id)
    checked = prepare(db, actor=current, request=request)
    if checked.plan_hash != request.expected_plan_hash:
        sources._fail('loss_inverse_plan_changed', '加锁期间原冲销方案已变化，请重新核验', 409)
    current = request_authority.references(db, actor=current, request=request).actor
    # New immutable identifiers are server owned. No input may substitute a
    # quantity/account/SN or revise the original approval/disposition records.
    row = Inverse(id=uuid4(), root_disposition_id=root.id, reversed_correction_id=request.reversed_correction_id,
        actor_user_id=current.user_id, actor_person_id=current.person_id,
        authorization_version=current.authorization_version, request_id=request.request_id,
        idempotency_key_hash=binding.key_hash, request_hash=binding.request_hash,
        reason=request.reason, command_jsonb=binding.document,
        original_transaction_id=execution.posting_transaction_id, original_movement_id=execution.posting_movement_id,
        source_account_id=execution.target_account_id, target_account_id=root.source_account_id,
        custody_assignment_id=UUID(checked.document['custody_assignment_id']), quantity=root.quantity,
        plan_jsonb=checked.document, plan_hash=checked.plan_hash, created_at=checked.checked_at)
    inverse = inverse_command(row)
    command = posting.InventoryPostingCommand(transaction_no=inverse.transaction_no, movement_type='reversal',
        source_document_type=inverse.source_document_type, source_document_id=inverse.source_document_id,
        posting_key=inverse.posting_key, effective_at=inverse.effective_at, movements=(movement,))
    posting._require_unused_business_keys(db, command)
    commit = posting._post_new_transaction(db, actor=current, command=command,
        idempotency_key_hash=binding.key_hash, request_hash=posting._reversal_request_hash(current, inverse),
        request_reference=posting._request_reference(request.request_id), permission_resource='stock_operation',
        permission_action='reverse_loss', reversed_transaction_id=execution.posting_transaction_id, event_suffix='reversed',
        occurred_at=checked.checked_at)
    row.posting_transaction_id = commit.result.transaction_id
    row.posting_movement_id = db.scalars(select(InventoryMovement.id).where(
        InventoryMovement.transaction_id == row.posting_transaction_id)).one()
    db.add(row); db.flush()
    business_events.record(db, row=row, root=root, order=order)
    if stop_unshipped_return:
        from . import return_stop
        return_stop.record(db, root=root, inverse=row)
    request_authority.authorize(db, actor=current, order=order, action='reverse_loss')
    verify_historical_original(db, root_disposition_id=root.id)
    loaded = load_event_checked_inventory_history(db, root_disposition_id=root.id)
    if not any(fact.id == row.id for fact in loaded.history.reversals):
        sources._fail('loss_inverse_evidence_invalid', '反向事实未能完整回读，事务必须回滚', 503)
    verify_original_inverse(db, reversal_id=row.id)
    return business_events.payload(row, root=root, order=order)
