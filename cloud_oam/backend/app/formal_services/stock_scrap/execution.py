"""Private atomic scrap writer, not registered as a public business endpoint.

Caller owns commit/rollback. Runtime activation still requires the forward
PG business/lifecycle guards, complete history, lookup/seals and recovery.
This service never substitutes a second balance writer for unified posting.
"""
from uuid import UUID
from sqlalchemy import or_, select
from app.inventory_models import InventoryMovement
from app.stock_operation_models import StockOperationOrder
from app.stock_scrap_schemas import ScrapExecute, validated_scrap_request
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_loss_disposition_plan import authorize as original_authorize
from app.formal_services.stock_loss_corrections.request_authority import authorize as correction_authorize
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.work_order_query import _aware
from . import events, posting_authority
from .tables import tables


def _verify_records(db, records):
    for name, entries in records.items():
        table = tables()[name]
        for expected in entries:
            row = db.execute(select(table).where(*(c == expected[c.name] for c in table.primary_key))).mappings().one()
            for key, value in expected.items():
                actual = row[key]
                if key == 'created_at':
                    actual, value = _aware(actual), _aware(value)
                if actual != value:
                    raise ValueError('persisted scrap record differs from the locked command: ' + name + '.' + key)


def execute(db, *, actor, request):
    request = validated_scrap_request(request)
    if type(request) is not ScrapExecute:
        raise ValueError('an exact scrap execution is required')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    key_hash = posting._storage_hash('stock-scrap:' + request.idempotency_key)
    prior = db.scalar(select(StockOperationOrder.id).where(or_(
        StockOperationOrder.idempotency_key_hash == key_hash,
        (StockOperationOrder.actor_user_id == actor.user_id) & (StockOperationOrder.request_id == request.request_id))))
    if prior is not None:
        sources._fail('stock_scrap_request_requires_recovery', '原请求已有事实，请只读回查，禁止重复执行', 409)
    permit = posting_authority.prepare(db, actor=actor, request=request)
    try:
        bundle = permit.bundle
        command = posting._validate_posting_command(bundle.posting_command)
        posting._require_unused_business_keys(db, command)
        commit = posting._post_new_transaction(db, actor=actor, command=command,
            idempotency_key_hash=key_hash, request_hash=posting._posting_request_hash(actor, command),
            request_reference=posting._request_reference(request.request_id), permission_resource='stock_operation',
            permission_action=permit.action, reversed_transaction_id=None, event_suffix='posted',
            occurred_at=bundle.checked_at, scrap_authority=permit)
        movement_id = db.scalars(select(InventoryMovement.id).where(
            InventoryMovement.transaction_id == commit.result.transaction_id)).one()
        records = bundle.rows(transaction_id=commit.result.transaction_id, movement_id=movement_id)
        # Cyclic source/child stock bindings are checked at COMMIT. Immediate
        # evidence/SN FKs still require the scrap line before its dependents.
        names = ['stock_operation_orders',
            'stock_loss_correction_executions' if request.source.kind == 'correction' else 'stock_loss_dispositions',
            'stock_scrap_lines', 'stock_scrap_serials', 'stock_scrap_files']
        for name in names:
            if records[name]:
                db.execute(tables()[name].insert(), records[name])
        db.flush()
        events.record(db, records=records)
        _verify_records(db, records)
        states = rebuild_serial_states(db, command.movements[0].serial_ids)
        if any(s.lifecycle_status != 'scrapped' or s.stock_account_id is not None or s.last_movement_id != movement_id
               for s in states.values()):
            raise ValueError('scrap SN cannot be reconstructed from the exact recorded transaction')
        order = db.get(StockOperationOrder, UUID(permit.preparation.document['operation_id']), populate_existing=True)
        if request.source.kind == 'correction':
            correction_authorize(db, actor=actor, order=order, action=permit.action)
        else:
            original_authorize(db, actor, order)
        from app.formal_services.stock_loss_corrections.history_chain import verify_chain
        verify_chain(db, root_disposition_id=records['stock_scrap_lines'][0]['root_disposition_id'])
        return events.verify(db, records=records)
    finally:
        posting_authority.discard(db, permit)
