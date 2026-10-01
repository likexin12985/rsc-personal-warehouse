"""All original kinds retain historical proof, never current replay rights.

Synthetic inverse rows do not establish real return compensation or posting.
"""
from uuid import UUID
import pytest
from sqlalchemy import text
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.formal_services import stock_loss_disposition_facts as original
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_correction_history_inventory import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, append_inverse
from test_stock_loss_correction_posting_events import seed_posting_bundle
from test_stock_loss_correction_business_events import snapshot
from app.formal_services.stock_loss_corrections.historical_original import verify_historical_original
from app.formal_services.stock_loss_corrections import business_events

def test_each_original_kind_keeps_historical_identity_after_exact_inverse(db, execution):
    result = execution.commit()
    root = db.get(StockLossDisposition, UUID(result['disposition_id']))
    (inverse, decision, serials) = append_inverse(db, root)
    order = db.get(StockOperationOrder, root.operation_id)
    seed_posting_bundle(db, inverse)
    for row in (inverse, decision):
        business_events.record(db, row=row, root=root, order=order)
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError) as caught:
        original.verified(db, row=root)
    assert caught.value.code == ('stock_loss_return_evidence_invalid' if root.disposition == 'return_to_region' else 'stock_loss_disposition_evidence_invalid')
    proof = verify_historical_original(db, root_disposition_id=root.id)
    assert proof.original_posting_transaction_id == root.posting_transaction_id
    assert proof.verified_original_ids == frozenset((root.id,))
    assert snapshot(db) == before and (not db.new) and (not db.dirty)
