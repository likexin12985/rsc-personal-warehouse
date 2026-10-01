"""Real inverse writer plus immutable pre-inverse plan reconstruction."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
import pytest
from sqlalchemy import select, text
from app.inventory_models import CustodyAssignment, StockBalance, SerialCurrentPosition, InventoryTransaction
from app.formal_services import stock_loss_sources as sources
from app.formal_services.stock_loss_corrections.correction_models import StockLossDispositionReversal as Inverse
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections.reversal_stock import StockPreparation
from test_stock_loss_correction_inverse_posting import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, command, snapshot
from app.formal_services.stock_loss_corrections.history_chain import verify_inverse as verify_original_inverse
from app.formal_services.stock_loss_corrections import historical_inverse
from app.formal_services.stock_loss_corrections import inverse_posting

@pytest.mark.parametrize('execution', ['restore_available', 'convert_used', 'convert_damaged'], indirect=True)
def test_real_inverse_full_historical_plan_is_read_only(db, prepared):
    w = prepared
    request = command(db, w)
    result = inverse_posting.execute_account_inverse(db, actor=w.actor, request=request)
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    proof = verify_original_inverse(db, reversal_id=UUID(result['reversal_id']))
    assert proof.root_disposition_id == w.root.id and proof.plan_hash == request.expected_plan_hash
    assert str(proof.posting_transaction_id) == result['posting_transaction_id']
    assert proof.posting_cursor == db.get(InventoryTransaction, proof.posting_transaction_id).ledger_cursor
    assert snapshot(db) == before and (not db.new) and (not db.dirty) and (not db.deleted)

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_rehashed_forged_preparation_cannot_post_even_with_consistent_request_and_events(db, prepared, monkeypatch):
    w = prepared
    original_prepare = inverse_posting.prepare
    base = original_prepare(db, actor=w.actor, request=w.request)
    for variant in ('source_balance', 'target_version', 'holds', 'cursor', 'extra', 'boolean_version'):
        plan = base.document
        if variant == 'source_balance':
            plan['source_balance_quantity'] = '900.000'
        elif variant == 'target_version':
            plan['target_balance_version'] += 1
        elif variant == 'holds':
            plan['frozen_holds_before']['lines'] = []
        elif variant == 'cursor':
            plan['ledger_cursor'] -= 1
        elif variant == 'extra':
            plan['invented_authority'] = True
        else:
            plan['source_balance_version'] = True
        forged = StockPreparation(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':')), sources._hash(plan), base.checked_at)
        request = command(db, w).model_copy(update={'expected_plan_hash': forged.plan_hash})
        monkeypatch.setattr(inverse_posting, 'prepare', lambda *args, **kwargs: forged)
        before = snapshot(db)
        savepoint = db.begin_nested()
        try:
            with pytest.raises(InvalidChain, match='loss_inverse_historical_plan_invalid'):
                inverse_posting.execute_account_inverse(db, actor=w.actor, request=request)
        finally:
            savepoint.rollback()
            db.expire_all()
            monkeypatch.setattr(inverse_posting, 'prepare', original_prepare)
        assert snapshot(db) == before and (not tuple(db.scalars(select(Inverse.id))))

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_current_cache_and_later_custody_end_do_not_rewrite_historical_plan(db, prepared):
    w = prepared
    result = inverse_posting.execute_account_inverse(db, actor=w.actor, request=command(db, w))
    db.commit()
    identifier = UUID(result['reversal_id'])
    proof = verify_original_inverse(db, reversal_id=identifier)
    savepoint = db.begin_nested()
    try:
        row = db.get(Inverse, identifier)
        db.get(StockBalance, row.source_account_id).quantity += Decimal('100')
        db.get(StockBalance, row.target_account_id).version += 7
        for position in db.scalars(select(SerialCurrentPosition)):
            if position.stock_account_id == row.target_account_id:
                position.stock_account_id = row.source_account_id
        db.get(CustodyAssignment, row.custody_assignment_id).valid_to = datetime.now(timezone.utc) + timedelta(days=1)
        db.flush()
        before = snapshot(db)
        db.execute(text('PRAGMA query_only=ON'))
        assert verify_original_inverse(db, reversal_id=identifier) == proof
        assert snapshot(db) == before
        db.execute(text('PRAGMA query_only=OFF'))
    finally:
        savepoint.rollback()
        db.expire_all()

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_audit_or_ledger_bound_change_during_history_read_is_rejected(db, prepared, monkeypatch):
    w = prepared
    result = inverse_posting.execute_account_inverse(db, actor=w.actor, request=command(db, w))
    db.commit()
    before = snapshot(db)
    original = historical_inverse._bound
    calls = []

    def changed(session):
        result = original(session)
        calls.append(True)
        return (result[0] + 1, result[1]) if len(calls) == 2 else result
    monkeypatch.setattr(historical_inverse, '_bound', changed)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InvalidChain, match='loss_inverse_historical_plan_invalid'):
        verify_original_inverse(db, reversal_id=UUID(result['reversal_id']))
    assert len(calls) == 2 and snapshot(db) == before
