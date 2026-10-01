"""Candidate event composition, not a correction posting/authority gate."""
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent, NotificationPersonTarget
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.audit_chain import AuditChainError
from test_stock_loss_correction_history_inventory import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, append_inverse, append_correction, state
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections import business_events as events
pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)

@pytest.fixture
def graph(db, execution):
    result = execution.commit()
    root = db.get(StockLossDisposition, UUID(result['disposition_id']))
    (inverse, decision, serials) = append_inverse(db, root)
    correction = append_correction(db, root, inverse, decision, serials)
    return (root, db.get(StockOperationOrder, root.operation_id), (inverse, decision, correction))

def snapshot(db):
    return (state(db), tuple(db.execute(text('SELECT * FROM audit_chain_heads ORDER BY stream_key'))), tuple(db.execute(text('SELECT * FROM notification_person_targets ORDER BY id'))))

def record_all(db, graph):
    (root, order, rows) = graph
    for row in rows:
        events.record(db, row=row, root=root, order=order)
    db.commit()

def test_three_independent_events_keep_original_history_and_notification_intent(db, graph):
    (root, order, rows) = graph
    before = state(db)
    record_all(db, graph)
    after = dict(state(db))
    initial = dict(before)
    for name in ('stock_loss_dispositions', 'inventory_transactions', 'inventory_movements', 'inventory_movement_serials', 'stock_balances', 'serial_current_positions'):
        assert after[name] == initial[name]
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    bodies = [events.verify(db, row=row, root=root, order=order) for row in rows]
    assert bodies[0]['stock_effect'] == 'restores_original_frozen_share'
    assert bodies[1]['stock_effect'] == 'none' and bodies[1]['approval_stage'] == 'approved'
    assert bodies[2]['stock_effect'] == 'frozen_to_available'
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    aggregate = events.KINDS[type(rows[2])][0]
    event = db.scalar(select(NotificationEvent).where(NotificationEvent.business_type == aggregate, NotificationEvent.business_id == str(rows[2].id)))
    event.status = 'expanded'
    db.commit()
    assert events.verify(db, row=rows[2], root=root, order=order) == bodies[2]

def test_missing_conflicting_and_substituted_event_evidence_cannot_hide(db, graph):
    (root, order, rows) = graph
    record_all(db, graph)
    row = rows[2]
    aggregate = events.KINDS[type(row)][0]
    for variant in ('outbox_payload', 'outbox_missing', 'outbox_duplicate', 'state_result', 'state_missing', 'audit_action', 'notification_payload', 'notification_missing', 'wrong_target', 'target_hash', 'notification_time'):
        before = snapshot(db)
        savepoint = db.begin_nested()
        try:
            outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == aggregate, OutboxEvent.aggregate_id == str(row.id)))
            transition = db.scalar(select(StateTransitionEvent).where(StateTransitionEvent.aggregate_type == aggregate, StateTransitionEvent.aggregate_id == str(row.id)))
            notification = db.scalar(select(NotificationEvent).where(NotificationEvent.business_type == aggregate, NotificationEvent.business_id == str(row.id)))
            if variant == 'outbox_payload':
                outbox.payload_jsonb = dict(outbox.payload_jsonb, quantity='999.000')
            elif variant == 'outbox_missing':
                db.delete(outbox)
            elif variant == 'outbox_duplicate':
                db.add(OutboxEvent(event_type=outbox.event_type, aggregate_type=aggregate, aggregate_id=str(row.id), payload_jsonb=outbox.payload_jsonb, idempotency_key=uuid4().hex, available_at=outbox.available_at))
            elif variant == 'state_result':
                transition.to_status = 'approved'
            elif variant == 'state_missing':
                db.delete(transition)
            elif variant == 'audit_action':
                audit = db.scalar(select(AuditEvent).where(AuditEvent.aggregate_type == aggregate, AuditEvent.aggregate_id == str(row.id)))
                audit.action = 'changed'
            elif variant == 'notification_payload':
                notification.payload_jsonb = dict(notification.payload_jsonb, stock_effect='none')
            elif variant == 'notification_missing':
                notification.business_id = str(uuid4())
            elif variant == 'wrong_target':
                target = db.scalar(select(NotificationPersonTarget).where(NotificationPersonTarget.event_id == notification.id))
                target.person_id = root.executor_person_id
            elif variant == 'target_hash':
                notification.target_manifest_sha256 = 'f' * 64
            else:
                notification.occurred_at = root.created_at
            db.flush()
            with pytest.raises((InvalidChain, InventoryReadError, AuditChainError)):
                events.verify(db, row=row, root=root, order=order)
        finally:
            savepoint.rollback()
            db.expire_all()
        assert snapshot(db) == before

def test_failure_after_audit_rolls_back_entire_event_bundle_and_chain_head(db, graph, monkeypatch):
    (root, order, rows) = graph
    before = snapshot(db)
    savepoint = db.begin_nested()

    def fail(*args, **kwargs):
        raise RuntimeError('synthetic notification-intent failure')
    monkeypatch.setattr(events, 'record_business_notification', fail)
    try:
        with pytest.raises(RuntimeError, match='synthetic notification-intent failure'):
            events.record(db, row=rows[0], root=root, order=order)
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before
