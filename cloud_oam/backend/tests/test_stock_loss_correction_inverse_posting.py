"""Actual unified stock writer for candidate inverse facts, SQLite only.

No SQL deferred correction migration or public recovery/sealing is claimed.
"""
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.inventory_models import InventoryTransaction, InventoryMovement, StockBalance, SerialCurrentPosition
from app.stock_operation_models import StockLossDisposition
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import stock_loss_disposition_facts as original
from app.formal_services.stock_loss_corrections.correction_models import StockLossDispositionReversal as Inverse
from app.formal_services.stock_loss_corrections.historical_holds import read_hold_snapshot
from app.formal_services.stock_loss_corrections.historical_original import verify_historical_original
from app.formal_services.stock_loss_corrections.request_contracts import ReversalExecute
from test_stock_loss_correction_reversal_stock import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared
from test_stock_loss_correction_business_events import snapshot as event_snapshot
from app.formal_services.stock_loss_corrections.reversal_stock import prepare
from app.formal_services.stock_loss_corrections import inverse_posting

def snapshot(db):
    return (event_snapshot(db), tuple(db.execute(text('SELECT * FROM state_transition_events ORDER BY id'))))

def command(db, w):
    preview = prepare(db, actor=w.actor, request=w.request)
    return ReversalExecute(**w.request.model_dump(), expected_plan_hash=preview.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex)

@pytest.mark.parametrize('execution', ['restore_available', 'convert_used', 'convert_damaged'], indirect=True)
def test_real_inverse_posts_exact_stock_and_events_without_overwriting_original(db, prepared):
    w = prepared
    root = w.root
    original_row = tuple(db.execute(text('SELECT * FROM stock_loss_dispositions ORDER BY id')))
    before_source = db.get(StockBalance, root.target_account_id).quantity
    before_target = db.get(StockBalance, root.source_account_id).quantity
    request = command(db, w)
    result = inverse_posting.execute_account_inverse(db, actor=w.actor, request=request)
    db.commit()
    row = db.get(Inverse, UUID(result['reversal_id']))
    tx = db.get(InventoryTransaction, row.posting_transaction_id)
    movement = db.get(InventoryMovement, row.posting_movement_id)
    assert tx.reversed_transaction_id == root.posting_transaction_id and tx.movement_type == 'reversal'
    assert (movement.from_account_id, movement.to_account_id, movement.quantity) == (root.target_account_id, root.source_account_id, root.quantity)
    assert db.get(StockBalance, root.target_account_id).quantity == before_source - root.quantity
    assert db.get(StockBalance, root.source_account_id).quantity == before_target + root.quantity
    assert tuple(db.execute(text('SELECT * FROM stock_loss_dispositions ORDER BY id'))) == original_row
    holds = read_hold_snapshot(db, source_account_id=root.source_account_id)
    selected = next((line for line in holds.lines if line.line_id == root.line_id))
    assert selected.frozen_quantity == root.quantity and selected.pending_reversal_id == row.id
    for sn in root.plan_jsonb['serial_ids']:
        position = db.get(SerialCurrentPosition, UUID(sn))
        assert position.stock_account_id == root.source_account_id and position.last_movement_id == movement.id
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert verify_historical_original(db, root_disposition_id=root.id).original_posting_transaction_id == root.posting_transaction_id
    with pytest.raises(InventoryReadError):
        original.verified(db, row=root)
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    with pytest.raises(InventoryReadError) as caught:
        inverse_posting.execute_account_inverse(db, actor=w.actor, request=request)
    assert caught.value.code == 'loss_inverse_request_requires_recovery'
    db.rollback()
    assert snapshot(db) == before

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_failure_after_stock_posting_rolls_back_inverse_balance_serials_events_and_chain_heads(db, prepared, monkeypatch):
    w = prepared
    request = command(db, w)
    before = snapshot(db)

    def fail(*args, **kwargs):
        raise RuntimeError('synthetic failure after unified posting')
    monkeypatch.setattr(inverse_posting.business_events, 'record', fail)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(RuntimeError, match='synthetic failure'):
            inverse_posting.execute_account_inverse(db, actor=w.actor, request=request)
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before
    assert not tuple(db.scalars(select(Inverse.id)))

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_stale_plan_and_orphan_request_evidence_never_post_stock(db, prepared):
    from app.foundation_models import OutboxEvent
    from datetime import datetime, timezone
    w = prepared
    valid = command(db, w)
    before = snapshot(db)
    wrong = valid.model_copy(update={'expected_plan_hash': 'f' * 64})
    with pytest.raises(InventoryReadError) as caught:
        inverse_posting.execute_account_inverse(db, actor=w.actor, request=wrong)
    assert caught.value.code == 'loss_inverse_plan_changed' and snapshot(db) == before
    now = datetime.now(timezone.utc)
    db.add(OutboxEvent(event_type='stock_loss.disposition_reversed', aggregate_type='stock_loss_disposition_reversal', aggregate_id=str(uuid4()), payload_jsonb={'actor_user_id': w.actor.user_id, 'request_id': valid.request_id}, idempotency_key=uuid4().hex, available_at=now))
    db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        inverse_posting.execute_account_inverse(db, actor=w.actor, request=valid)
    assert caught.value.code == 'loss_inverse_request_outcome_unknown' and snapshot(db) == before
