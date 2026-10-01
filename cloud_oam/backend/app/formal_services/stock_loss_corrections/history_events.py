"""Compose persisted inventory history with posting and new domain evidence.

Internal candidate only: original disposition plan/hold/domain proofs and
downstream compensation are still required for a full business-history API.
No public recovery or write path consumes this partial proof.
"""
from sqlalchemy import func, select

from app.foundation_models import AuditChainHead
from app.inventory_models import InventoryTransaction
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from .chain_projection import InvalidChain
from .history_inventory import load_inventory_history, _candidate_rows
from . import business_events
from . import posting_events


def _head(db):
    return db.execute(select(AuditChainHead.version, AuditChainHead.last_event_id,
        AuditChainHead.last_hash).where(AuditChainHead.stream_key == 'inventory')).one()


def load_event_checked_inventory_history(db, *, root_disposition_id):
    """Refuse partial or mixed observations; return no write authority."""
    with db.no_autoflush:
        head = _head(db)
        loaded = load_inventory_history(db, root_disposition_id=root_disposition_id)
        root = db.get(StockLossDisposition, root_disposition_id, populate_existing=True)
        order = db.get(StockOperationOrder, root.operation_id, populate_existing=True)
        inverses, decisions, corrections = _candidate_rows(db, root.id)
        expected = (set(r.id for r in loaded.history.reversals), set(r.id for r in loaded.history.decisions),
            set(r.id for r in loaded.history.executions if r.id != root.id))
        def unchanged(rows):
            return tuple(set(r.id for r in group) for group in rows) == expected
        if not unchanged((inverses, decisions, corrections)):
            raise InvalidChain('business_history_changed_during_read')
        for row in (root, *inverses, *corrections):
            posting_events.verify(db, fact=row)
        for row in (*inverses, *decisions, *corrections):
            business_events.verify(db, row=row, root=root, order=order)
        if (_head(db) != head or not unchanged(_candidate_rows(db, root.id))
                or db.scalar(select(func.max(InventoryTransaction.ledger_cursor))) != loaded.observed_ledger_cursor):
            raise InvalidChain('business_history_changed_during_read')
        return loaded
