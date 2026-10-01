"""Original immutable facts remain valid after an evidenced later inverse.

Inverse/correction rows are synthetic owner fixtures, not new posting APIs.
"""
from uuid import UUID
import pytest
from sqlalchemy import select, text
from app.foundation_models import NotificationEvent, OutboxEvent, AuditEvent
from app.stock_operation_models import StockLossDisposition
from app.formal_services import stock_loss_disposition_facts as original, stock_loss_sources as sources
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.audit_chain import AuditChainError
from test_stock_loss_correction_history_inventory import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution
from test_stock_loss_correction_posting_events import graph
from test_stock_loss_correction_business_events import snapshot
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections.historical_original import verify_historical_original

def test_original_four_flows_keep_current_and_historical_proof_before_reversal(db, execution):
    result = execution.commit()
    root = db.get(StockLossDisposition, UUID(result['disposition_id']))
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert original.verified(db, row=root)['disposition_id'] == str(root.id)
    proof = verify_historical_original(db, root_disposition_id=root.id)
    assert proof.verified_original_ids == frozenset((root.id,))
    assert proof.original_posting_transaction_id == root.posting_transaction_id
    assert snapshot(db) == before and (not db.new) and (not db.dirty)

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_later_inverse_preserves_original_proof_but_current_recovery_remains_closed(db, graph):
    (root, inverse, decision, correction) = graph
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError):
        original.verified(db, row=root)
    proof = verify_historical_original(db, root_disposition_id=root.id)
    assert proof.root_disposition_id == root.id
    assert proof.original_posting_transaction_id == root.posting_transaction_id
    assert proof.original_posting_cursor < proof.observed_ledger_cursor
    assert snapshot(db) == before and (not db.new) and (not db.dirty)

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_original_domain_and_plan_proof_cannot_be_skipped_after_an_inverse(db, graph):
    (root, inverse, decision, correction) = graph
    for variant in ('missing_original_outbox', 'original_notification_payload', 'rehashed_original_plan', 'original_audit_action', 'missing_inverse_event'):
        before = snapshot(db)
        savepoint = db.begin_nested()
        try:
            if variant == 'missing_original_outbox':
                db.delete(db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == 'stock_loss_disposition', OutboxEvent.aggregate_id == str(root.id))))
            elif variant == 'original_notification_payload':
                event = db.scalar(select(NotificationEvent).where(NotificationEvent.business_type == 'stock_loss_disposition', NotificationEvent.business_id == str(root.id)))
                event.payload_jsonb = dict(event.payload_jsonb, quantity='999.000')
            elif variant == 'rehashed_original_plan':
                root.plan_jsonb = dict(root.plan_jsonb, source_balance_quantity='999.000')
                root.plan_hash = sources._hash(root.plan_jsonb)
            elif variant == 'original_audit_action':
                event = db.scalar(select(AuditEvent).where(AuditEvent.aggregate_type == 'stock_loss_disposition', AuditEvent.aggregate_id == str(root.id)))
                event.action = 'invented'
            else:
                db.delete(db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == 'stock_loss_disposition_reversal', OutboxEvent.aggregate_id == str(inverse.id))))
            db.flush()
            with pytest.raises((InvalidChain, InventoryReadError, AuditChainError)):
                verify_historical_original(db, root_disposition_id=root.id)
        finally:
            savepoint.rollback()
            db.expire_all()
        assert snapshot(db) == before
