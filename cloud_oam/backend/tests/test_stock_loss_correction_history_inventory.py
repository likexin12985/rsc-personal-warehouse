"""Actual original approval/posting services plus owner-seeded candidate edges.

The candidate inverse/correction rows below are deliberately NOT produced by
a posting service. This tests history loading, not atomic new stock effects,
authorization, balance/SN projections or correction audit/notification proof.
"""
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from sqlalchemy import func, select, text
from app.inventory_models import InventoryTransaction, InventoryMovement, InventoryMovementSerial
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.work_order_query import _aware
from test_stock_loss_disposition_recovery import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution
from app.formal_services.stock_loss_corrections.correction_models import StockLossDispositionReversal as Inverse
from app.formal_services.stock_loss_corrections.correction_models import StockLossCorrectionDecision as Decision, StockLossCorrectionExecution as Correction
from app.formal_services.stock_loss_corrections.history_inventory import load_inventory_history, inverse_command, correction_command
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain, project
from app.formal_services.stock_loss_corrections import request_contracts as contracts

def state(db):
    names = ('stock_loss_dispositions', 'stock_loss_disposition_reversals', 'stock_loss_correction_decisions', 'stock_loss_correction_executions', 'inventory_transactions', 'inventory_movements', 'inventory_movement_serials', 'stock_balances', 'serial_current_positions', 'audit_events', 'outbox_events', 'notification_events')
    return tuple(((name, tuple(db.execute(text('SELECT * FROM ' + name + ' ORDER BY 1')))) for name in names))

def context(root, request):
    binding = contracts.original_request(actor=SimpleNamespace(user_id=root.actor_user_id, person_id=root.executor_person_id), request=request)
    return dict(id=uuid4(), root_disposition_id=root.id, actor_user_id=root.actor_user_id, actor_person_id=root.executor_person_id, authorization_version=root.authorization_version, request_id=request.request_id, idempotency_key_hash=binding.key_hash, reason=request.reason, command_jsonb=binding.document, request_hash=binding.request_hash)

def request_basis(db, root):
    return dict(root_disposition_id=root.id, expected_root_request_hash=root.request_hash, expected_submission_plan_hash=db.get(StockOperationOrder, root.operation_id).plan_hash, reason='Synthetic candidate history', request_id=uuid4().hex, idempotency_key=uuid4().hex)

def persist_candidate_ledger(db, row, command, serials, reverse_id=None):
    actor = SimpleNamespace(user_id=row.actor_user_id, person_id=row.actor_person_id, authorization_version=row.authorization_version)
    request_hash = (posting._reversal_request_hash if reverse_id else posting._posting_request_hash)(actor, command)
    cursor = db.scalar(select(func.max(InventoryTransaction.ledger_cursor))) + 1
    db.add(InventoryTransaction(id=row.posting_transaction_id, transaction_no=command.transaction_no, movement_type='reversal' if reverse_id else command.movement_type, source_document_type=command.source_document_type, source_document_id=command.source_document_id, posting_key=command.posting_key, idempotency_key_hash=row.idempotency_key_hash, request_hash=request_hash, status='posted', effective_at=row.created_at, posted_at=row.created_at, created_at=row.created_at, ledger_cursor=cursor, reversed_transaction_id=reverse_id, actor_user_id=row.actor_user_id))
    db.flush()
    db.add(InventoryMovement(id=row.posting_movement_id, transaction_id=row.posting_transaction_id, line_no=1, from_account_id=row.source_account_id, to_account_id=row.target_account_id, quantity=row.quantity, external_boundary_code=None, created_at=row.created_at))
    db.flush()
    for serial in serials:
        db.add(InventoryMovementSerial(movement_id=row.posting_movement_id, transaction_id=row.posting_transaction_id, serial_id=serial, created_at=row.created_at))
    db.flush()

def append_inverse(db, root):
    serials = tuple((UUID(s) for s in root.plan_jsonb['serial_ids']))
    plan = {'serial_ids': [str(s) for s in serials]}
    request = contracts.ReversalExecute(**request_basis(db, root), reversed_correction_id=None, expected_execution_request_hash=root.request_hash, expected_plan_hash=sources._hash(plan))
    row = Inverse(**context(root, request), reversed_correction_id=None, original_transaction_id=root.posting_transaction_id, original_movement_id=root.posting_movement_id, posting_transaction_id=uuid4(), posting_movement_id=uuid4(), source_account_id=root.target_account_id, target_account_id=root.source_account_id, custody_assignment_id=root.custody_assignment_id, quantity=root.quantity, plan_jsonb=plan, plan_hash=sources._hash(plan), created_at=_aware(root.created_at) + timedelta(seconds=1))
    persist_candidate_ledger(db, row, inverse_command(row), serials, root.posting_transaction_id)
    db.add(row)
    db.commit()
    request = contracts.CorrectionApprove(**request_basis(db, root), reversal_id=row.id, expected_reversal_hash=row.request_hash, disposition='restore_available')
    decision = Decision(**context(root, request), reversal_id=row.id, expected_reversal_hash=row.request_hash, disposition='restore_available', created_at=_aware(row.created_at) + timedelta(seconds=1))
    db.add(decision)
    db.commit()
    return (row, decision, serials)

def append_correction(db, root, inverse, decision, serials):
    plan = {'serial_ids': [str(s) for s in serials]}
    request = contracts.CorrectionExecute(**request_basis(db, root), reversal_id=inverse.id, expected_reversal_hash=inverse.request_hash, correction_decision_id=decision.id, expected_correction_decision_hash=decision.request_hash, expected_plan_hash=sources._hash(plan))
    row = Correction(**context(root, request), correction_decision_id=decision.id, reversal_id=inverse.id, disposition='restore_available', return_operation_id=None, source_account_id=root.source_account_id, target_account_id=root.target_account_id, custody_assignment_id=root.custody_assignment_id, quantity=root.quantity, posting_transaction_id=uuid4(), posting_movement_id=uuid4(), plan_jsonb=plan, plan_hash=sources._hash(plan), created_at=_aware(decision.created_at) + timedelta(seconds=1))
    persist_candidate_ledger(db, row, correction_command(row, serials), serials)
    db.add(row)
    db.commit()
    return row

def test_existing_four_dispositions_load_from_actual_business_facts_query_only(db, execution):
    result = execution.commit()
    root_id = UUID(result['disposition_id'])
    before = state(db)
    db.execute(text('PRAGMA query_only=ON'))
    loaded = load_inventory_history(db, root_disposition_id=root_id)
    h = loaded.history
    p = project(h.basis, h.executions, h.reversals, h.decisions)
    assert p.active_execution_id == root_id and p.frozen_quantity == 0
    assert len(h.executions) == 1 and (not h.reversals) and (not h.decisions)
    assert state(db) == before and (not db.new) and (not db.dirty)

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_persisted_inverse_decision_and_successor_preserve_original_and_restore_share(db, execution):
    result = execution.commit()
    root = db.get(StockLossDisposition, UUID(result['disposition_id']))
    (inverse, decision, serials) = append_inverse(db, root)
    before = state(db)
    loaded = load_inventory_history(db, root_disposition_id=root.id)
    h = loaded.history
    restored = project(h.basis, h.executions, h.reversals, h.decisions)
    assert restored.frozen_quantity == root.quantity and restored.pending_reversal_id == inverse.id
    assert restored.active_execution_id is None and state(db) == before
    correction = append_correction(db, root, inverse, decision, serials)
    before = state(db)
    loaded = load_inventory_history(db, root_disposition_id=root.id)
    h = loaded.history
    corrected = project(h.basis, h.executions, h.reversals, h.decisions)
    assert corrected.active_execution_id == correction.id and corrected.frozen_quantity == 0
    assert corrected.execution_history == (root.id, correction.id) and state(db) == before
    inverse_cursor = db.get(InventoryTransaction, inverse.posting_transaction_id).ledger_cursor
    assert project(h.basis, h.executions, h.reversals, h.decisions, through_cursor=inverse_cursor) == restored

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_persisted_lineage_request_plan_and_approval_substitution_are_rejected(db, execution):
    result = execution.commit()
    root = db.get(StockLossDisposition, UUID(result['disposition_id']))
    (inverse, decision, serials) = append_inverse(db, root)
    correction = append_correction(db, root, inverse, decision, serials)
    for (row, field, value, code) in ((inverse, 'original_movement_id', correction.posting_movement_id, 'inverse_original_binding_mismatch'), (inverse, 'request_hash', 'f' * 64, 'persisted_request_hash_mismatch'), (correction, 'plan_hash', 'f' * 64, 'persisted_plan_hash_mismatch'), (decision, 'expected_reversal_hash', 'f' * 64, 'correction_approval_inverse_hash_mismatch')):
        before = state(db)
        savepoint = db.begin_nested()
        try:
            setattr(row, field, value)
            db.flush()
            with pytest.raises(InvalidChain, match=code):
                load_inventory_history(db, root_disposition_id=root.id)
        finally:
            savepoint.rollback()
            db.expire_all()
        assert state(db) == before
    for (row, changed_intent) in ((correction, {'expected_root_request_hash': 'f' * 64}), (decision, {'disposition': 'scrap'})):
        before = state(db)
        savepoint = db.begin_nested()
        try:
            row.command_jsonb = dict(row.command_jsonb, intent=dict(row.command_jsonb['intent'], **changed_intent))
            row.request_hash = sources._hash(row.command_jsonb)
            db.flush()
            with pytest.raises(InvalidChain, match='correction_request_fact_mismatch'):
                load_inventory_history(db, root_disposition_id=root.id)
        finally:
            savepoint.rollback()
            db.expire_all()
        assert state(db) == before
