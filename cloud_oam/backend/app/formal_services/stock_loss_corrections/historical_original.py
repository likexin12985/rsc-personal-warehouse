"""Historical original disposition proof after separately proved successors.

Candidate internal read component. The historical payload describes the
original action, never a current stock result or permission to replay it.
No public endpoint is wired to this module.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.foundation_models import AuditChainHead
from app.inventory_models import InventoryTransaction
from app.stock_operation_models import StockLossDisposition
from . import original_posting_facts as original
from .chain_projection import InvalidChain
from .history_events import load_event_checked_inventory_history
from .historical_holds import read_hold_snapshot


@dataclass(frozen=True)
class HistoricalOriginal:
    root_disposition_id: UUID
    original_posting_transaction_id: UUID
    original_posting_cursor: int
    verified_original_ids: frozenset[UUID]
    observed_ledger_cursor: int


def _bound(db):
    heads = tuple(db.execute(select(AuditChainHead.stream_key, AuditChainHead.version,
        AuditChainHead.last_event_id, AuditChainHead.last_hash).where(
            AuditChainHead.stream_key.in_(('inventory', 'material_request'))).order_by(AuditChainHead.stream_key)))
    return db.scalar(select(func.max(InventoryTransaction.ledger_cursor))), heads


def verify_historical_original(db, *, root_disposition_id):
    with db.no_autoflush:
        start = _bound(db)
        pending = [root_disposition_id]; seen = set(); root_tx = None
        while pending:
            identifier = pending.pop()
            if identifier in seen: continue
            loaded = load_event_checked_inventory_history(db, root_disposition_id=identifier)
            if loaded.observed_ledger_cursor != start[0]:
                raise InvalidChain('business_history_changed_during_read')
            row = db.get(StockLossDisposition, identifier, populate_existing=True)
            # This retains the complete existing original command, plan,
            # policy, custody, audit, notification and return-child checks.
            original._verify_at_posting(db, row)
            tx = db.get(InventoryTransaction, row.posting_transaction_id, populate_existing=True)
            if identifier == root_disposition_id: root_tx = tx
            holds = read_hold_snapshot(db, source_account_id=row.source_account_id,
                through_cursor=tx.ledger_cursor - 1)
            actual_basis = holds.plan_basis() if row.disposition == 'scrap' else holds.legacy_basis()
            recorded_basis = row.plan_jsonb.get('frozen_holds_before') if row.disposition == 'scrap' else row.plan_jsonb['holds']
            if holds.observed_ledger_cursor != start[0] or recorded_basis != actual_basis:
                raise InvalidChain('original_historical_hold_mismatch')
            seen.add(identifier)
            # Re-prove the full earlier original graph iteratively, including
            # its current successors. Never reuse a proof across transactions.
            pending.extend(line.root_disposition_id for line in holds.lines
                if line.root_disposition_id is not None and line.root_disposition_id not in seen)
        if _bound(db) != start:
            raise InvalidChain('business_history_changed_during_read')
        return HistoricalOriginal(root_disposition_id, root_tx.id, root_tx.ledger_cursor,
            frozenset(seen), start[0])
