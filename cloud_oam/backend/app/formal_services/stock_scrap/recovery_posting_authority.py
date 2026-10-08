"""Transaction-bound permit for the exact independently approved scrap inverse."""
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_loss_corrections.history_inventory import inverse_command
from . import recovery_plan, recovery_authority as authority

KEY = 'stock_scrap.live_recovery_permit'


@dataclass(frozen=True)
class RecoveryPermit:
    transaction: object
    actor: object
    request: object
    preparation: object
    inverse_id: UUID
    execution_id: UUID
    key_hash: str
    reversal: object
    command: object


def invalid():
    posting._fail('stock_scrap_recovery_posting_authority_invalid', 'precondition_failed',
        '报废恢复必须由专用服务在本次事务内核验独立批准和准确原交易')


def build(db, actor, request, checked, *, inverse_id, execution_id):
    if request.expected_plan_hash != checked.plan_hash:
        authority.fail('plan_changed', '找回恢复方案已变化，请重新核验')
    doc = checked.document
    key = posting._storage_hash('stock-scrap-recovery:' + request.idempotency_key)
    reversal = inverse_command(SimpleNamespace(id=inverse_id, original_transaction_id=UUID(doc['original_transaction_id']),
        idempotency_key_hash=key, created_at=checked.checked_at))
    command = posting.InventoryPostingCommand(transaction_no=reversal.transaction_no, movement_type='reversal',
        source_document_type=reversal.source_document_type, source_document_id=reversal.source_document_id,
        posting_key=reversal.posting_key, effective_at=reversal.effective_at,
        movements=(posting.InventoryMovementCommand(None, UUID(doc['target_account_id']), Decimal(doc['quantity']),
            tuple(UUID(s) for s in doc['serial_ids']), 'stock_operation_scrap'),))
    return RecoveryPermit(db.get_nested_transaction() or db.get_transaction(), actor, request, checked,
        inverse_id, execution_id, key, reversal, command)


def prepare(db, *, actor, request):
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    checked = recovery_plan.prepare(db, actor=actor, request=request)
    ids = dict(inverse_id=uuid4(), execution_id=uuid4())
    first = build(db, actor, request, checked, **ids)
    posting._plan_and_lock_terminal_opening_graphs(db, command=first.command, current_actor_user_id=actor.user_id)
    checked = recovery_plan.prepare(db, actor=actor, request=request)
    permit = build(db, actor, request, checked, **ids)
    db.info[KEY] = permit
    return permit


def require(db, *, actor, command, permit, permission_resource, permission_action, reversed_transaction_id,
        opening_task_id, current_cursor, idempotency_key_hash, request_hash, request_reference, occurred_at,
        event_suffix, receipt_authority, scrap_authority):
    if (type(permit) is not RecoveryPermit or db.info.get(KEY) is not permit or permit.transaction is None
            or (db.get_nested_transaction() or db.get_transaction()) is not permit.transaction
            or not permit.transaction.is_active or permit.actor != actor or permit.command != command
            or current_cursor != permit.preparation.document['ledger_cursor']
            or permission_resource != 'stock_operation' or permission_action != authority.ACTIONS['execute']
            or reversed_transaction_id != permit.reversal.original_transaction_id
            or opening_task_id is not None or receipt_authority is not None or scrap_authority is not None
            or event_suffix != 'reversed' or occurred_at != permit.preparation.checked_at
            or idempotency_key_hash != permit.key_hash
            or request_hash != posting._reversal_request_hash(actor, permit.reversal)
            or request_reference != posting._request_reference(permit.request.request_id)):
        invalid()
    source = authority.load_source(db, permit.request.source)
    authority.authorize(db, actor=actor, source=source, stage='execute')
    _, serials, prior = recovery_plan.serial_basis(db, source, cursor=current_cursor)
    if serials != permit.preparation.document['serials']:
        invalid()
    return prior


def discard(db, permit):
    if db.info.get(KEY) is permit:
        db.info.pop(KEY, None)
