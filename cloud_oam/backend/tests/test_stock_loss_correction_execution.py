from app.formal_services.stock_loss_corrections import history_chain
"""Actual original -> inverse -> independent approval -> stock correction."""
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission
from app.inventory_models import InventoryTransaction, InventoryMovement, StockAccount, StockBalance, SerialCurrentPosition
from app.formal_services import stock_loss_sources as sources
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections.correction_models import StockLossCorrectionDecision as Decision, StockLossCorrectionExecution as Execution
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections.historical_holds import read_hold_snapshot
from app.formal_services.stock_loss_corrections.history_chain import verify_inverse as verify_original_inverse
from app.formal_services.stock_loss_corrections.request_contracts import CorrectionPreview, CorrectionExecute
from app.formal_services.stock_loss_corrections.reversal_stock import StockPreparation
from test_stock_loss_correction_approval import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, recoverable, inverse_ready, request as approval_request
from test_stock_loss_correction_sealed_inverse import snapshot as previous_snapshot
from app.formal_services.stock_loss_corrections import correction_approval
from app.formal_services.stock_loss_corrections import correction_stock
from app.formal_services.stock_loss_corrections import correction_execution
from app.formal_services.stock_loss_corrections import correction_facts
pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)

def snapshot(db):
    return (previous_snapshot(db), tuple(db.execute(text('SELECT * FROM stock_accounts ORDER BY id'))))

@pytest.fixture(params=['restore_available', 'convert_used', 'convert_damaged'])
def ready(db, inverse_ready, request):
    w = inverse_ready
    permission = Permission(resource='stock_operation', action='correct_loss', field_code='', description='Synthetic dedicated correction execution')
    db.add(permission)
    db.flush()
    db.add(RolePermission(role_id=w.admin_role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    approved = correction_approval.approve(db, actor=w.actor, request=approval_request(w, request.param))
    db.commit()
    decision = db.get(Decision, UUID(approved['correction_decision_id']))
    preview = CorrectionPreview(root_disposition_id=w.root.id, expected_root_request_hash=w.root.request_hash, expected_submission_plan_hash=w.order.plan_hash, reversal_id=w.inverse.id, expected_reversal_hash=w.inverse.request_hash, correction_decision_id=decision.id, expected_correction_decision_hash=decision.request_hash, reason='按独立总部批准执行纠正')
    return SimpleNamespace(w=w, preview=preview, decision=decision, permission=permission)

def command(db, r):
    plan = correction_stock.prepare(db, actor=r.w.actor, request=r.preview)
    return CorrectionExecute(**r.preview.model_dump(), expected_plan_hash=plan.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex)

def test_three_outcomes_post_exact_stock_and_preserve_all_original_facts(db, ready):
    r = ready
    w = r.w
    before = snapshot(db)
    original_rows = tuple(db.execute(text('SELECT * FROM stock_loss_dispositions ORDER BY id')))
    inverse_rows = tuple(db.execute(text('SELECT * FROM stock_loss_disposition_reversals ORDER BY id')))
    db.execute(text('PRAGMA query_only=ON'))
    plan = correction_stock.prepare(db, actor=w.actor, request=r.preview)
    assert correction_stock.prepare(db, actor=w.actor, request=r.preview).plan_hash == plan.plan_hash
    assert snapshot(db) == before and (not db.new) and (not db.dirty) and (not db.deleted)
    db.execute(text('PRAGMA query_only=OFF'))
    source_before = db.get(StockBalance, w.root.source_account_id).quantity
    target_id = UUID(plan.document['target_account_id'])
    existing_target = db.get(StockBalance, target_id)
    target_before = existing_target.quantity if existing_target else Decimal(0)
    cmd = command(db, r)
    result = correction_execution.execute(db, actor=w.actor, request=cmd)
    db.commit()
    row = db.get(Execution, UUID(result['correction_execution_id']))
    tx = db.get(InventoryTransaction, row.posting_transaction_id)
    move = db.get(InventoryMovement, row.posting_movement_id)
    assert row.correction_decision_id == r.decision.id and row.reversal_id == w.inverse.id
    assert tx.movement_type == plan.document['movement_type'] and tx.reversed_transaction_id is None
    assert (move.from_account_id, move.to_account_id, move.quantity) == (w.root.source_account_id, target_id, w.root.quantity)
    assert db.get(StockBalance, w.root.source_account_id).quantity == source_before - w.root.quantity
    assert db.get(StockBalance, target_id).quantity == target_before + w.root.quantity
    assert tuple(db.execute(text('SELECT * FROM stock_loss_dispositions ORDER BY id'))) == original_rows
    assert tuple(db.execute(text('SELECT * FROM stock_loss_disposition_reversals ORDER BY id'))) == inverse_rows
    for identifier in plan.document['serial_ids']:
        pos = db.get(SerialCurrentPosition, UUID(identifier))
        assert pos.stock_account_id == target_id and pos.last_movement_id == move.id
    shares = read_hold_snapshot(db, source_account_id=w.root.source_account_id)
    own = next((line for line in shares.lines if line.line_id == w.root.line_id))
    assert own.frozen_quantity == 0 and own.active_execution_id == row.id and (own.pending_reversal_id is None)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert history_chain.verify_correction(db, correction_execution_id=row.id).plan_hash == cmd.expected_plan_hash
    verify_original_inverse(db, reversal_id=w.inverse.id)
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    with pytest.raises(InventoryReadError):
        correction_execution.execute(db, actor=w.actor, request=cmd)
    db.rollback()
    assert snapshot(db) == before
    other = cmd.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex})
    with pytest.raises(InventoryReadError) as caught:
        correction_execution.execute(db, actor=w.actor, request=other)
    assert caught.value.code == 'loss_correction_inverse_not_pending'
    db.rollback()
    assert snapshot(db) == before

@pytest.mark.parametrize('ready', ['convert_used'], indirect=True)
def test_failure_after_posting_rolls_back_new_target_and_entire_correction(db, ready, monkeypatch):
    r = ready
    cmd = command(db, r)
    plan = correction_stock.prepare(db, actor=r.w.actor, request=r.preview)
    target_id = UUID(plan.document['target_account_id'])
    assert plan.document['target_requires_creation'] and db.get(StockAccount, target_id) is None
    before = snapshot(db)
    savepoint = db.begin_nested()

    def fail(*args, **kwargs):
        raise RuntimeError('synthetic correction domain event failure')
    monkeypatch.setattr(correction_execution.business_events, 'record', fail)
    try:
        with pytest.raises(RuntimeError, match='synthetic correction'):
            correction_execution.execute(db, actor=r.w.actor, request=cmd)
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before and db.get(StockAccount, target_id) is None
    assert not tuple(db.scalars(select(Execution.id)))

@pytest.mark.parametrize('ready', ['convert_used'], indirect=True)
def test_stale_plan_wrong_approval_and_missing_execution_permission_cannot_create_target(db, ready):
    r = ready
    cmd = command(db, r)
    before = snapshot(db)
    for field in ('expected_plan_hash', 'expected_correction_decision_hash'):
        with pytest.raises(InventoryReadError):
            correction_execution.execute(db, actor=r.w.actor, request=cmd.model_copy(update={field: 'f' * 64}))
        db.rollback()
        assert snapshot(db) == before
    savepoint = db.begin_nested()
    try:
        grant = db.scalar(select(RolePermission).where(RolePermission.role_id == r.w.admin_role.id, RolePermission.permission_id == r.permission.id))
        grant.effect = 'deny'
        db.flush()
        with pytest.raises(InventoryReadError) as caught:
            correction_execution.execute(db, actor=r.w.actor, request=cmd)
        assert caught.value.code == 'stock_loss_correction_forbidden'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before

@pytest.mark.parametrize('ready', ['convert_used'], indirect=True)
def test_consistently_rehashed_invented_hold_or_balance_is_rejected_and_rolled_back(db, ready, monkeypatch):
    r = ready
    actual = correction_execution.prepare
    for variant in ('holds', 'balance'):
        plan = actual(db, actor=r.w.actor, request=r.preview)
        document = plan.document
        if variant == 'holds':
            document['frozen_holds_before']['lines'] = []
        else:
            document['source_balance_quantity'] = '900.000'
        forged = StockPreparation(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')), sources._hash(document), plan.checked_at)
        cmd = command(db, r).model_copy(update={'expected_plan_hash': forged.plan_hash})
        monkeypatch.setattr(correction_execution, 'prepare', lambda *args, **kwargs: forged)
        before = snapshot(db)
        savepoint = db.begin_nested()
        try:
            with pytest.raises(InvalidChain, match='loss_correction_historical_plan_invalid'):
                correction_execution.execute(db, actor=r.w.actor, request=cmd)
        finally:
            savepoint.rollback()
            db.expire_all()
            monkeypatch.setattr(correction_execution, 'prepare', actual)
        assert snapshot(db) == before

@pytest.mark.parametrize('ready', ['restore_available'], indirect=True)
def test_historical_correction_does_not_trust_current_balance_or_serial_cache(db, ready):
    r = ready
    result = correction_execution.execute(db, actor=r.w.actor, request=command(db, r))
    db.commit()
    identifier = UUID(result['correction_execution_id'])
    row = db.get(Execution, identifier)
    proof = history_chain.verify_correction(db, correction_execution_id=identifier)
    before = snapshot(db)
    savepoint = db.begin_nested()
    try:
        db.get(StockBalance, row.target_account_id).quantity += Decimal('100')
        for pos in db.scalars(select(SerialCurrentPosition)):
            if pos.stock_account_id == row.target_account_id:
                pos.stock_account_id = row.source_account_id
        db.flush()
        altered = snapshot(db)
        db.execute(text('PRAGMA query_only=ON'))
        assert history_chain.verify_correction(db, correction_execution_id=identifier) == proof
        assert snapshot(db) == altered
        db.execute(text('PRAGMA query_only=OFF'))
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before
