"""Atomic first correction through the unified inventory posting service.

Private candidate: native COMMIT constraints, return and scrap execution and
public recovery/sealing of correction commands remain release requirements.
"""
from . import correction_seal_facts

from uuid import UUID, uuid4
from sqlalchemy import select

from app.inventory_models import InventoryMovement, StockAccount
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_loss_disposition_plan import target_account
from .correction_models import StockLossCorrectionExecution as Execution
from .request_contracts import CorrectionExecute, original_request, validate, require_original_row
from .history_inventory import correction_command
from .correction_stock import prepare
from . import business_events
from .history_chain import verify_correction
from . import inverse_recovery
from . import correction_recovery
from . import request_authority
from . import sealed_inverse


def execute(db, *, actor, request):
    request = validate(request)
    if type(request) is not CorrectionExecute:
        raise ValueError('an exact correction execute command is required')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    refs = request_authority.references(db, actor=actor, request=request)
    current, root, order, inverse, decision = refs.actor, refs.root, refs.order, refs.reversal, refs.decision
    binding = original_request(actor=current, request=request); keys = sealed_inverse._keys(request)
    sealed_inverse.require_unsealed(db, actor=current, request=request)
    correction_seal_facts.require_unsealed(db, actor=current, request=request)
    existing = correction_recovery._coordinates(db, actor=current, request=request, keys=keys)
    if existing is not None:
        require_original_row(row=existing, actor=current, request=request)
        sources._fail('loss_correction_request_requires_recovery', '原请求已有业务事实，请只读回查完整原请求', 409)
    inverse_recovery._evidence(db, actor=current, request=request, keys=keys, row=None)
    checked = prepare(db, actor=current, request=request)
    if checked.plan_hash != request.expected_plan_hash:
        sources._fail('loss_correction_plan_changed', '独立纠正方案已变化，请重新预检', 409)
    # Establish opening/reference locks before introducing a new target row.
    graph = posting.InventoryPostingCommand(transaction_no='loss-correction-graph',
        movement_type=checked.document['movement_type'], source_document_type='stock_loss_correction_execution',
        source_document_id=str(root.id), posting_key='loss-correction-graph', effective_at=checked.checked_at,
        movements=(posting.InventoryMovementCommand(root.source_account_id, None, root.quantity,
            tuple(UUID(s) for s in checked.document['serial_ids'])),))
    posting._plan_and_lock_terminal_opening_graphs(db, command=graph, current_actor_user_id=current.user_id)
    checked = prepare(db, actor=current, request=request)
    if checked.plan_hash != request.expected_plan_hash:
        sources._fail('loss_correction_plan_changed', '加锁期间纠正方案已变化，请重新预检', 409)
    current = request_authority.references(db, actor=current, request=request).actor
    source = db.get(StockAccount, root.source_account_id, populate_existing=True)
    target = target_account(db, source, decision.disposition)
    if (str(target.id) != checked.document['target_account_id']
            or (target not in db) != checked.document['target_requires_creation']):
        sources._fail('loss_correction_plan_changed', '纠正目标账户已变化', 409)
    if target not in db:
        target.created_at = checked.checked_at; target.updated_at = checked.checked_at
        db.add(target); db.flush()
    row = Execution(id=uuid4(), root_disposition_id=root.id, actor_user_id=current.user_id,
        actor_person_id=current.person_id, authorization_version=current.authorization_version,
        request_id=request.request_id, idempotency_key_hash=binding.key_hash, request_hash=binding.request_hash,
        reason=request.reason, command_jsonb=binding.document, correction_decision_id=decision.id,
        reversal_id=inverse.id, disposition=decision.disposition, return_operation_id=None,
        source_account_id=source.id, target_account_id=target.id,
        custody_assignment_id=UUID(checked.document['custody_assignment_id']), quantity=root.quantity,
        plan_hash=checked.plan_hash, plan_jsonb=checked.document, created_at=checked.checked_at)
    command = correction_command(row, tuple(UUID(s) for s in checked.document['serial_ids']))
    posting._require_unused_business_keys(db, command)
    commit = posting._post_new_transaction(db, actor=current, command=command,
        idempotency_key_hash=binding.key_hash, request_hash=posting._posting_request_hash(current, command),
        request_reference=posting._request_reference(request.request_id), permission_resource='stock_operation',
        permission_action='correct_loss', reversed_transaction_id=None, event_suffix='posted', occurred_at=checked.checked_at)
    row.posting_transaction_id = commit.result.transaction_id
    row.posting_movement_id = db.scalars(select(InventoryMovement.id).where(
        InventoryMovement.transaction_id == row.posting_transaction_id)).one()
    db.add(row); db.flush()
    business_events.record(db, row=row, root=root, order=order)
    request_authority.authorize(db, actor=current, order=order, action='correct_loss')
    verify_correction(db, correction_execution_id=row.id)
    return business_events.payload(row, root=root, order=order)
