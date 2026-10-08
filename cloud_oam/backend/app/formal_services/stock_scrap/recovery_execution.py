"""Private atomic found-stock inverse through the single inventory writer.

Caller owns commit/rollback. Native business guards, migration, seals and
public activation are independent release requirements, not installed here.
"""
from uuid import UUID
from sqlalchemy import select
from app.inventory_models import InventoryMovement
from app.stock_loss_correction_models import StockLossDispositionReversal
from app.stock_scrap_recovery_schemas import ScrapRecoveryExecute, validated_recovery_request
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_loss_corrections import business_events
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.serial_ledger import rebuild_serial_states
from . import recovery_posting_authority as permits, recovery_authority as authority, recovery_facts as facts
from .recovery_approval import _unused
from .execution import _verify_records
from .tables import tables


def records(permit, source, transaction_id, movement_id):
    request, plan = permit.request, permit.preparation
    command = facts.intent(request)
    common = dict(created_at=plan.checked_at, actor_user_id=permit.actor.user_id,
        actor_person_id=permit.actor.person_id, authorization_version=permit.actor.authorization_version,
        request_id=request.request_id, idempotency_key_hash=permit.key_hash, request_hash=sources._hash(command),
        reason=request.reason, command_jsonb=command, plan_hash=plan.plan_hash, plan_jsonb=plan.document)
    child, old = source['line'], source['fact']
    inverse = dict(common, id=permit.inverse_id, root_disposition_id=source['root'].id,
        reversed_correction_id=child['correction_execution_id'], original_transaction_id=old.posting_transaction_id,
        original_movement_id=old.posting_movement_id, posting_transaction_id=transaction_id, posting_movement_id=movement_id,
        source_account_id=None, target_account_id=child['frozen_account_id'], quantity=child['quantity'],
        custody_assignment_id=UUID(plan.document['custody_assignment_id']), scrap_recovery_execution_id=permit.execution_id,
        scrap_line_id=child['id'], scrap_source_kind=child['source_kind'])
    recovery = dict(common, id=permit.execution_id, headquarters_review_id=request.headquarters_review_id,
        recovery_request_id=request.recovery_request_id, scrap_line_id=child['id'], headquarters_decision='approve',
        reversal_id=permit.inverse_id, expected_headquarters_hash=request.expected_headquarters_hash)
    return dict(stock_loss_disposition_reversals=[inverse], stock_scrap_recovery_executions=[recovery])


def execute(db, *, actor, request):
    request = validated_recovery_request(request)
    if type(request) is not ScrapRecoveryExecute:
        raise ValueError('an exact scrap recovery execution is required')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    _unused(db, actor, request, posting._storage_hash('stock-scrap-recovery:' + request.idempotency_key))
    permit = permits.prepare(db, actor=actor, request=request)
    try:
        source = authority.load_source(db, request.source)
        posting._require_unused_business_keys(db, permit.command)
        commit = posting._post_new_transaction(db, actor=actor, command=permit.command,
            idempotency_key_hash=permit.key_hash, request_hash=posting._reversal_request_hash(actor, permit.reversal),
            request_reference=posting._request_reference(request.request_id), permission_resource='stock_operation',
            permission_action=authority.ACTIONS['execute'], reversed_transaction_id=permit.reversal.original_transaction_id,
            event_suffix='reversed', occurred_at=permit.preparation.checked_at, scrap_recovery_authority=permit)
        move = db.scalars(select(InventoryMovement.id).where(InventoryMovement.transaction_id == commit.result.transaction_id)).one()
        rows = records(permit, source, commit.result.transaction_id, move)
        for name, entries in rows.items():
            db.execute(tables()[name].insert(), entries)
        db.flush()
        inverse = db.get(StockLossDispositionReversal, permit.inverse_id, populate_existing=True)
        business_events.record(db, row=inverse, root=source['root'], order=source['order'])
        from .recovery_history import record_child
        record_child(db, inverse, source)
        _verify_records(db, rows)
        from .recovery_history import verify_execution
        verify_execution(db, inverse=inverse, source=source)
        verify_chain(db, root_disposition_id=source['root'].id)
        states = rebuild_serial_states(db, permit.command.movements[0].serial_ids)
        facts.need(all(s.lifecycle_status == 'active' and s.stock_account_id == source['account'].id
            and s.last_movement_id == move for s in states.values()))
        authority.authorize(db, actor=actor, source=source, stage='execute')
        return business_events.payload(inverse, root=source['root'], order=source['order'])
    finally:
        permits.discard(db, permit)
