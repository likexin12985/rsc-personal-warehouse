"""Real original posting events; synthetic inverse/correction evidence.

Candidate rows/events are owner-seeded. These tests do not claim a new atomic
posting service, commit-time authority or PostgreSQL acceptance.
"""
from datetime import timedelta
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.foundation_models import AuditChainHead, AuditEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction, InventoryMovement, InventoryMovementSerial
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.formal_services import inventory_posting as posting
from app.formal_services.audit_chain import AuditChainError, append_audit_event
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.work_order_query import _aware
from test_stock_loss_correction_history_inventory import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, append_inverse, append_correction
from test_stock_loss_correction_business_events import snapshot
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections.correction_models import StockLossDispositionReversal
from app.formal_services.stock_loss_corrections.history_events import load_event_checked_inventory_history
from app.formal_services.stock_loss_corrections import business_events
from app.formal_services.stock_loss_corrections import posting_events

def seed_posting_bundle(db, fact):
    """Test-only owner fixture matching the actual atomic writer's schema."""
    tx = db.get(InventoryTransaction, fact.posting_transaction_id)
    at = _aware(tx.posted_at)
    suffix = 'reversed' if type(fact) is StockLossDispositionReversal else 'posted'
    kind = 'inventory.transaction.' + suffix
    reverse_id = str(tx.reversed_transaction_id) if tx.reversed_transaction_id else None
    identity = dict(aggregate_type='inventory_transaction', aggregate_id=str(tx.id))
    append_audit_event(db, stream_key='inventory', actor_user_id=fact.actor_user_id, action=kind, **identity, before_jsonb=None, after_jsonb=dict(ledger_cursor=tx.ledger_cursor, movement_count=1, movement_type=tx.movement_type, posting_key=tx.posting_key, reversed_transaction_id=reverse_id, status='posted'), request_id=posting._request_reference(fact.request_id), occurred_at=at, created_at=at)
    db.add(StateTransitionEvent(**identity, from_status=None, to_status='posted', reason='inventory_transaction_' + suffix, actor_id=fact.actor_user_id, idempotency_key=posting._derived_evidence_key('state', tx.id, suffix), occurred_at=at, created_at=at, metadata_jsonb=dict(ledger_cursor=tx.ledger_cursor, movement_type=tx.movement_type, request_reference=posting._request_reference(fact.request_id))))
    db.add(OutboxEvent(**identity, event_type=kind, payload_jsonb=dict(transaction_id=str(tx.id), transaction_no=tx.transaction_no, movement_type=tx.movement_type, ledger_cursor=tx.ledger_cursor, reversed_transaction_id=reverse_id), idempotency_key=posting._derived_evidence_key('outbox', tx.id, suffix), available_at=at, created_at=at, updated_at=at))
    db.flush()

@pytest.fixture
def graph(db, execution):
    result = execution.commit()
    root = db.get(StockLossDisposition, UUID(result['disposition_id']))
    (inverse, decision, serials) = append_inverse(db, root)
    correction = append_correction(db, root, inverse, decision, serials)
    order = db.get(StockOperationOrder, root.operation_id)
    for fact in (inverse, correction):
        seed_posting_bundle(db, fact)
    for fact in (inverse, decision, correction):
        business_events.record(db, row=fact, root=root, order=order)
    db.commit()
    return (root, inverse, decision, correction)

def test_actual_original_four_postings_are_verified_without_writes(db, execution):
    result = execution.commit()
    root = db.get(StockLossDisposition, UUID(result['disposition_id']))
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    loaded = load_event_checked_inventory_history(db, root_disposition_id=root.id)
    assert len(loaded.history.executions) == 1
    assert not db.new and (not db.dirty) and (not db.deleted) and (snapshot(db) == before)

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_inverse_and_correction_bundle_readback_is_query_only_and_delivery_independent(db, graph):
    (root, inverse, decision, correction) = graph
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    loaded = load_event_checked_inventory_history(db, root_disposition_id=root.id)
    assert len(loaded.history.executions) == 2 and len(loaded.history.reversals) == 1
    assert snapshot(db) == before and (not db.new) and (not db.dirty) and (not db.deleted)
    db.execute(text('PRAGMA query_only=OFF'))
    for fact in (inverse, correction):
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == 'inventory_transaction', OutboxEvent.aggregate_id == str(fact.posting_transaction_id)))
        outbox.status = 'published'
        outbox.attempts = 3
        outbox.available_at = _aware(outbox.available_at) + timedelta(minutes=2)
        outbox.published_at = outbox.available_at
    db.commit()
    assert load_event_checked_inventory_history(db, root_disposition_id=root.id) == loaded

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_missing_duplicate_wrong_action_request_time_or_inverse_evidence_is_rejected(db, graph):
    (root, inverse, decision, correction) = graph
    variants = ('missing_outbox', 'duplicate_outbox', 'wrong_outbox_reverse', 'wrong_outbox_kind', 'wrong_state_suffix', 'wrong_state_request', 'wrong_state_key', 'wrong_state_time', 'wrong_outbox_time', 'wrong_audit_actor', 'wrong_audit_hash', 'audit_duplicate_other_stream', 'missing_audit', 'missing_state', 'duplicate_state', 'wrong_transaction_time', 'wrong_movement_time', 'wrong_serial_time', 'missing_domain_outbox')
    for fact in (root, inverse, correction):
        for variant in variants:
            if variant == 'wrong_serial_time' and (not fact.plan_jsonb['serial_ids']):
                continue
            if variant == 'missing_domain_outbox' and fact is root:
                continue
            before = snapshot(db)
            savepoint = db.begin_nested()
            try:
                identity = dict(aggregate_type='inventory_transaction', aggregate_id=str(fact.posting_transaction_id))
                tx = db.get(InventoryTransaction, fact.posting_transaction_id)
                outbox = db.scalar(select(OutboxEvent).filter_by(**identity))
                state = db.scalar(select(StateTransitionEvent).filter_by(**identity))
                audit = db.scalar(select(AuditEvent).filter_by(**identity))
                if variant == 'missing_outbox':
                    db.delete(outbox)
                elif variant == 'duplicate_outbox':
                    db.add(OutboxEvent(**identity, event_type=outbox.event_type, payload_jsonb=outbox.payload_jsonb, idempotency_key=uuid4().hex, available_at=outbox.available_at))
                elif variant == 'wrong_outbox_reverse':
                    outbox.payload_jsonb = dict(outbox.payload_jsonb, reversed_transaction_id=str(uuid4()))
                elif variant == 'wrong_outbox_kind':
                    outbox.event_type = 'inventory.transaction.invented'
                elif variant == 'wrong_state_suffix':
                    state.reason = 'inventory_transaction_approved'
                elif variant == 'wrong_state_request':
                    state.metadata_jsonb = dict(state.metadata_jsonb, request_reference=uuid4().hex)
                elif variant == 'wrong_state_key':
                    state.idempotency_key = uuid4().hex
                elif variant == 'wrong_state_time':
                    state.occurred_at = _aware(state.occurred_at) + timedelta(seconds=1)
                elif variant == 'wrong_outbox_time':
                    outbox.created_at = _aware(outbox.created_at) + timedelta(seconds=1)
                elif variant == 'wrong_audit_actor':
                    audit.actor_user_id = None
                elif variant == 'wrong_audit_hash':
                    audit.event_hash = 'f' * 64
                elif variant == 'audit_duplicate_other_stream':
                    if db.scalar(select(AuditChainHead.id).where(AuditChainHead.stream_key == 'authentication')) is None:
                        db.add(AuditChainHead(stream_key='authentication', version=0))
                        db.flush()
                    append_audit_event(db, stream_key='authentication', actor_user_id=fact.actor_user_id, action='unrelated', **identity, before_jsonb=None, after_jsonb={}, request_id=uuid4().hex, occurred_at=_aware(tx.posted_at), created_at=_aware(tx.posted_at))
                elif variant == 'missing_audit':
                    audit.aggregate_id = str(uuid4())
                elif variant == 'missing_state':
                    db.delete(state)
                elif variant == 'duplicate_state':
                    db.add(StateTransitionEvent(**identity, from_status=None, to_status='posted', reason='conflict', actor_id=fact.actor_user_id, idempotency_key=uuid4().hex, occurred_at=_aware(tx.posted_at)))
                elif variant == 'wrong_transaction_time':
                    tx.created_at = _aware(tx.created_at) + timedelta(seconds=1)
                elif variant == 'wrong_movement_time':
                    move = db.get(InventoryMovement, fact.posting_movement_id)
                    move.created_at = _aware(move.created_at) + timedelta(seconds=1)
                elif variant == 'wrong_serial_time':
                    sn = db.scalar(select(InventoryMovementSerial).where(InventoryMovementSerial.movement_id == fact.posting_movement_id))
                    sn.created_at = _aware(sn.created_at) + timedelta(seconds=1)
                else:
                    domain = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(fact.id)))
                    db.delete(domain)
                db.flush()
                with pytest.raises((InvalidChain, InventoryReadError, AuditChainError)):
                    load_event_checked_inventory_history(db, root_disposition_id=root.id)
            finally:
                savepoint.rollback()
                db.expire_all()
            assert snapshot(db) == before

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_audit_append_during_composed_read_refuses_mixed_snapshot(db, graph, monkeypatch):
    (root, inverse, decision, correction) = graph
    original_verify = business_events.verify
    seen = []

    def concurrent_append(db, **kwargs):
        body = original_verify(db, **kwargs)
        if not seen:
            seen.append(True)
            append_audit_event(db, stream_key='inventory', actor_user_id=root.actor_user_id, action='test.observed.race', aggregate_type='test', aggregate_id=str(uuid4()), before_jsonb=None, after_jsonb={}, request_id=uuid4().hex, occurred_at=_aware(correction.created_at), created_at=_aware(correction.created_at))
        return body
    monkeypatch.setattr(business_events, 'verify', concurrent_append)
    before = snapshot(db)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(InvalidChain, match='business_history_changed_during_read'):
            load_event_checked_inventory_history(db, root_disposition_id=root.id)
    finally:
        savepoint.rollback()
        db.expire_all()
    assert seen and snapshot(db) == before
