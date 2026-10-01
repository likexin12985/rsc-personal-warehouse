"""Reject coherent forged later inverse plans after three real rounds."""
import json
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from app.formal_services import stock_loss_sources as sources
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections.reversal_stock import StockPreparation
from test_stock_loss_correction_multigeneration import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, active, Correction, InventoryTransaction,
    ReversalPreview, ReversalExecute, reversal_stock, inverse_posting, history, snapshot,
    test_three_rounds_reverse_exact_predecessor_and_recover_all_original_requests as _build_three_rounds,
)
pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)


def test_later_inverse_rejects_forged_plan_and_stale_execution(db, active, execution, monkeypatch):
    _build_three_rounds(db, active, execution)
    db.execute(text('PRAGMA query_only=OFF'))
    w = active
    rows = tuple(db.scalars(select(Correction).join(InventoryTransaction,
        InventoryTransaction.id == Correction.posting_transaction_id)
        .order_by(InventoryTransaction.ledger_cursor)))
    assert len(rows) == 3
    def preview(selected):
        return ReversalPreview(root_disposition_id=w.root.id,
            expected_root_request_hash=w.root.request_hash,
            expected_submission_plan_hash=w.order.plan_hash,
            reversed_correction_id=selected.id,
            expected_execution_request_hash=selected.request_hash,
            reason='独立验证多次纠正后的方案防伪')
    before = snapshot(db)
    with pytest.raises((InvalidChain, InventoryReadError)):
        reversal_stock.prepare(db, actor=w.actor, request=preview(rows[0]))
    assert snapshot(db) == before
    selection = preview(rows[-1])
    base = reversal_stock.prepare(db, actor=w.actor, request=selection)
    original_prepare = inverse_posting.prepare
    for variant in ('source_balance', 'target_version', 'holds'):
        plan = base.document
        if variant == 'source_balance':
            plan['source_balance_quantity'] = '900.000'
        elif variant == 'target_version':
            plan['target_balance_version'] += 1
        else:
            plan['frozen_holds_before']['lines'] = []
        forged = StockPreparation(json.dumps(plan, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')), sources._hash(plan), base.checked_at)
        command = ReversalExecute(**selection.model_dump(), expected_plan_hash=forged.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        savepoint = db.begin_nested()
        try:
            monkeypatch.setattr(inverse_posting, 'prepare', lambda *args, **kwargs: forged)
            with pytest.raises(InvalidChain, match='loss_inverse_historical_plan_invalid'):
                inverse_posting.execute_account_inverse(db, actor=w.actor, request=command)
        finally:
            savepoint.rollback()
            db.expire_all()
            monkeypatch.setattr(inverse_posting, 'prepare', original_prepare)
        assert snapshot(db) == before
    # A moving ledger boundary cannot be accepted as one stable history proof.
    bound = history._bound
    calls = []
    def moving(session):
        value = bound(session); calls.append(True)
        return (value[0] + 1, value[1]) if len(calls) > 1 else value
    monkeypatch.setattr(history, '_bound', moving)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InvalidChain, match='business_history_changed_during_read'):
        history.verify_chain(db, root_disposition_id=w.root.id)
    assert len(calls) == 2 and snapshot(db) == before
