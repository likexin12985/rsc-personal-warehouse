"""Compensation preparation must prove both ends of every fulfillment edge."""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app import stock_operation_models as models
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_dependencies, return_history
from app.formal_services.stock_loss_corrections.request_contracts import ReversalPreview
from app.formal_services.stock_loss_corrections.reversal_stock import prepare
from test_stock_loss_correction_reversal_stock import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, execution, prepared, snapshot, grant,
)
from test_stock_loss_return_outbound import derived, ready, submit as depart


@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_foreign_outbound_header_cannot_hide_a_linked_return_line(db, prepared):
    """Owner-seeded inconsistent history, not an authorized outbound write.

    A header-only dependency scan sees no outbound for the derived return.
    Its line still points at that return, so compensation must reject the graph
    even though balances and the original posting remain untouched.
    """
    root = prepared.root
    child_line = db.scalars(select(models.StockOperationLine).where(
        models.StockOperationLine.operation_id == root.return_operation_id)).one()
    foreign = models.StockOperationOutbound(
        id=uuid4(), outbound_no='foreign-' + uuid4().hex,
        operation_id=root.operation_id, status='outbound',
        actor_user_id=root.actor_user_id, operator_person_id=root.executor_person_id,
        target_custody_assignment_id=root.custody_assignment_id,
        authorization_version=root.authorization_version,
        reason='Synthetic misbound fulfillment evidence',
        outbound_at=root.created_at, created_at=root.created_at,
        request_id=uuid4().hex, idempotency_key_hash='a' * 64,
        request_hash='b' * 64, plan_hash='c' * 64,
        command_jsonb={}, plan_jsonb={},
        posting_transaction_id=root.posting_transaction_id,
    )
    db.add(foreign); db.flush()
    db.add(models.StockOperationOutboundLine(
        id=uuid4(), outbound_id=foreign.id, line_no=1,
        operation_line_id=child_line.id,
        source_stock_account_id=root.target_account_id,
        transit_stock_account_id=root.source_account_id,
        quantity=root.quantity, created_at=root.created_at,
    ))
    db.flush()
    assert not return_dependencies.read(db, disposition_id=root.id).has_downstream_facts
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        with pytest.raises(InventoryReadError) as caught:
            prepare(db, actor=prepared.actor, request=prepared.request)
        assert caught.value.code == 'loss_return_history_invalid'
        assert snapshot(db) == before
        assert not db.new and not db.dirty and not db.deleted
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


@pytest.mark.parametrize('stock,quantity', [
    ('quantity', Decimal(1)), ('quantity', Decimal('.375')), ('serial', Decimal(1)),
], indirect=['stock'])
def test_real_full_or_partial_departure_requires_physical_compensation(
        db, stock, approved, ready, monkeypatch, quantity):
    request = ready.request.model_copy(update={'lines': (
        ready.request.lines[0].model_copy(update={'quantity': quantity}),)})
    depart(db, ready, request)
    root = db.get(models.StockLossDisposition, UUID(ready.derived.result['disposition_id']))
    order = db.get(models.StockOperationOrder, root.operation_id)
    actor = grant(db, approved, monkeypatch)
    selection = ReversalPreview(root_disposition_id=root.id,
        expected_root_request_hash=root.request_hash,
        expected_submission_plan_hash=order.plan_hash,
        reason='Synthetic stopped return preparation', reversed_correction_id=None,
        expected_execution_request_hash=root.request_hash)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        with pytest.raises(InventoryReadError) as caught:
            prepare(db, actor=actor, request=selection)
        assert caught.value.code == 'loss_reversal_requires_return_compensation'
        assert snapshot(db) == before
        assert not db.new and not db.dirty and not db.deleted
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_child_changes_without_ledger_change_cannot_issue_a_plan(db, prepared, monkeypatch):
    original = return_history._capture
    calls = []
    savepoint = db.begin_nested()
    before = snapshot(db)

    def change_child(db, root):
        calls.append(True)
        if len(calls) == 2:
            line = db.scalars(select(models.StockOperationLine).where(
                models.StockOperationLine.operation_id == root.return_operation_id)).one()
            line.reason = 'Synthetic change between graph reads'
            db.flush()
        return original(db, root)

    monkeypatch.setattr(return_history, '_capture', change_child)
    try:
        with pytest.raises(InventoryReadError) as caught:
            prepare(db, actor=prepared.actor, request=prepared.request)
        assert caught.value.code == 'loss_reversal_read_changed'
        assert len(calls) == 2
    finally:
        savepoint.rollback(); db.expire_all()
    assert snapshot(db) == before
