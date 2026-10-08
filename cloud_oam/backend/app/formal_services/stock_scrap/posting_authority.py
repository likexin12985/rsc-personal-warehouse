"""Single-transaction permission for the dedicated scrap writer.

An execution bundle alone is not authority. Issue only after locking the
ledger and opening/reference/principal graphs and repeating stock preparation.
The permit is never serialized, returned by an API, or valid in another txn.
Database business proofs and public activation remain separate release gates.
"""
from dataclasses import dataclass

from app.formal_services import inventory_posting as posting, stock_scrap_plan
from .execution_bundle import build

_KEY = 'stock_scrap.live_posting_permit'


@dataclass(frozen=True)
class PostingPermit:
    transaction: object
    actor: object
    bundle: object
    preparation: object
    action: str


def invalid():
    posting._fail('stock_scrap_posting_authority_invalid', 'precondition_failed',
        '报废必须由专用服务在本次事务内完成批准、冻结份额和库存复核')


def prepare(db, *, actor, request):
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    checked = stock_scrap_plan.prepare(db, actor=actor, request=request)
    preliminary = build(actor=actor, request=request, preparation=checked)
    posting._plan_and_lock_terminal_opening_graphs(db, command=preliminary.posting_command,
        current_actor_user_id=actor.user_id)
    checked = stock_scrap_plan.prepare(db, actor=actor, request=request)
    bundle = build(actor=actor, request=request, preparation=checked)
    permit = PostingPermit(db.get_nested_transaction() or db.get_transaction(), actor, bundle, checked,
        'correct_loss' if request.source.kind == 'correction' else 'dispose_loss')
    db.info[_KEY] = permit
    return permit


def require(db, *, actor, command, permit, permission_resource, permission_action,
            reversed_transaction_id, opening_task_id, current_cursor,
            idempotency_key_hash, request_hash, request_reference, occurred_at, event_suffix, receipt_authority):
    if (type(permit) is not PostingPermit or db.info.get(_KEY) is not permit
            or permit.transaction is None
            or (db.get_nested_transaction() or db.get_transaction()) is not permit.transaction
            or not permit.transaction.is_active or permit.actor != actor
            or command != permit.bundle.posting_command
            or current_cursor != permit.preparation.document['ledger_cursor']
            or permission_resource != 'stock_operation' or permission_action != permit.action
            or reversed_transaction_id is not None or opening_task_id is not None
            or receipt_authority is not None or event_suffix != 'posted'
            or occurred_at != permit.bundle.checked_at
            or idempotency_key_hash != posting._storage_hash(permit.bundle.posting_key)
            or request_hash != posting._posting_request_hash(actor, command)
            or request_reference != posting._request_reference(
                permit.bundle.document['records']['stock_operation_orders'][0]['request_id'])):
        invalid()
    return permit.preparation.document


def discard(db, permit):
    if db.info.get(_KEY) is permit:
        db.info.pop(_KEY, None)
