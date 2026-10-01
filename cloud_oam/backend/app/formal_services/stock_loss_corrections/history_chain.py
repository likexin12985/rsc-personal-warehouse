"""Iterative complete account-to-account correction history proof.

Candidate only. Native later-generation constraints, typed binding admission,
request seals and HTTP activation are separate requirements. No proof result
is persisted or reused across transactions; callers must authorize access.
"""
from dataclasses import dataclass
from uuid import UUID

from app.inventory_models import InventoryTransaction
from app.stock_operation_models import StockLossDisposition
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections.correction_models import (
    StockLossDispositionReversal as Inverse,
    StockLossCorrectionExecution as Correction,
)
from app.formal_services.stock_loss_corrections.historical_original import _bound, verify_historical_original
from app.formal_services.stock_loss_corrections.historical_holds import read_hold_snapshot
from app.formal_services.stock_loss_corrections.history_events import load_event_checked_inventory_history
from app.formal_services.stock_loss_corrections.history_inventory import _candidate_rows
from .historical_inverse import verify_plan as inverse_plan
from .correction_facts import verify_plan as correction_plan


def _need(condition, code='loss_correction_chain_evidence_invalid'):
    if not condition:
        raise InvalidChain(code)


@dataclass(frozen=True)
class ChainProof:
    root_disposition_id: UUID
    observed_ledger_cursor: int
    proved_roots: frozenset[UUID]
    inverse_proofs: tuple
    correction_proofs: tuple
    proved_decision_ids: frozenset[UUID]


def verify_chain(db, *, root_disposition_id):
    """Discover persisted dependencies, then prove every plan in ledger order.

The stock graph verifier already rejects forks, disconnected edges and cycles.
The additional ordered proof prevents a later inverse from citing a merely
well-hashed correction whose full stock plan has never been verified.
"""
    _need(type(root_disposition_id) is UUID and root_disposition_id.int != 0)
    with db.no_autoflush:
        start = _bound(db)
        pending, roots, signatures, postings = [root_disposition_id], {}, {}, {}
        while pending:
            root_id = pending.pop()
            if root_id in roots:
                continue
            root = db.get(StockLossDisposition, root_id, populate_existing=True)
            _need(root is not None)
            original = verify_historical_original(db, root_disposition_id=root_id)
            loaded = load_event_checked_inventory_history(db, root_disposition_id=root_id)
            _need(loaded.observed_ledger_cursor == start[0])
            roots[root_id] = root
            groups = _candidate_rows(db, root_id)
            signatures[root_id] = tuple(tuple(row.id for row in rows) for rows in groups)
            inverse_rows, _, correction_rows = groups
            # Current shared holds may depend on a sibling root created after
            # this root's latest posting. Include both current and past bounds.
            cutoffs = {start[0]}
            for row in (root, *inverse_rows, *correction_rows):
                tx = db.get(InventoryTransaction, row.posting_transaction_id, populate_existing=True)
                _need(tx is not None and tx.ledger_cursor <= start[0])
                cutoffs.add(tx.ledger_cursor - 1)
                if row is not root:
                    _need(row.id not in postings)
                    postings[row.id] = (tx.ledger_cursor, type(row), row.id)
            pending.extend(identifier for identifier in original.verified_original_ids if identifier not in roots)
            for cutoff in sorted(cutoffs):
                holds = read_hold_snapshot(db, source_account_id=root.source_account_id, through_cursor=cutoff)
                _need(holds.observed_ledger_cursor == start[0])
                pending.extend(line.root_disposition_id for line in holds.lines
                    if line.root_disposition_id is not None and line.root_disposition_id not in roots)

        execution_ids, inverse_ids = set(roots), set()
        inverse_proofs, correction_proofs = [], []
        ordered = sorted(postings.values(), key=lambda value: value[0])
        _need(len({cursor for cursor, _, _ in ordered}) == len(ordered))
        for _, kind, identifier in ordered:
            if kind is Inverse:
                proof = inverse_plan(db, reversal_id=identifier, proved_execution_ids=frozenset(execution_ids))
                inverse_ids.add(identifier)
                inverse_proofs.append(proof)
            else:
                _need(kind is Correction)
                proof = correction_plan(db, correction_execution_id=identifier,
                    proved_inverse_ids=frozenset(inverse_ids))
                execution_ids.add(identifier)
                correction_proofs.append(proof)
            _need(proof.observed_ledger_cursor == start[0])
        for root_id, signature in signatures.items():
            _need(tuple(tuple(row.id for row in rows) for rows in _candidate_rows(db, root_id)) == signature,
                'business_history_changed_during_read')
        _need(_bound(db) == start, 'business_history_changed_during_read')
        return ChainProof(root_disposition_id, start[0], frozenset(roots),
            tuple(inverse_proofs), tuple(correction_proofs),
            frozenset(identifier for signature in signatures.values() for identifier in signature[1]))


def verify_inverse(db, *, reversal_id):
    with db.no_autoflush:
        row = db.get(Inverse, reversal_id, populate_existing=True)
        _need(row is not None)
        proof = verify_chain(db, root_disposition_id=row.root_disposition_id)
        result = next((value for value in proof.inverse_proofs if value.reversal_id == reversal_id), None)
        _need(result is not None)
        return result


def verify_correction(db, *, correction_execution_id):
    with db.no_autoflush:
        row = db.get(Correction, correction_execution_id, populate_existing=True)
        _need(row is not None)
        proof = verify_chain(db, root_disposition_id=row.root_disposition_id)
        result = next((value for value in proof.correction_proofs
            if value.correction_execution_id == correction_execution_id), None)
        _need(result is not None)
        return result
