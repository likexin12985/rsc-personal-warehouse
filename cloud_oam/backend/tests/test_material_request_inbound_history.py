"""Closure history unit checks; synthetic ledger commit is not native PG16 proof."""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, event

from app.demand_models import MaterialRequestCommand
from app.foundation_models import AuditEvent
from app.inventory_models import InboundOrder, InventoryTransaction, InventoryMovement, StockAccount
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_my_inbound import world, receipt_world, receiving_world, outbound_world, post, facts

pytest_plugins = ("test_material_request_picking",)

from app.formal_services import material_request_inbound_history as service


def test_reads_exact_posted_quantity_without_any_write_or_writer_lock(world, monkeypatch, outbound_world):
    db, actor, request, payload = world
    result = post(world)
    order = db.get(InboundOrder, result.inbound_order_id)
    before = facts(db)
    statements = []
    def observe(conn, cursor, statement, *args):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"), statement
        assert "FOR UPDATE" not in statement.upper()
    from app.formal_services import audit_chain
    def refuse(*args, **kwargs):
        raise AssertionError("read verification must not lock the audit writer")
    monkeypatch.setattr(audit_chain, "lock_audit_chain_head", refuse)
    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        rows = service.verified_posted_receipt_lines(db, request=request, order=order)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert sum((row.accepted_qty for row in rows), Decimal(0)) == (Decimal("1.000") if outbound_world[4] else Decimal("0.125"))
    assert all(row.inventory_transaction_id == result.inventory_transaction_id for row in rows)
    assert statements and facts(db) == before


@pytest.mark.parametrize("change", ["quantity", "target", "source_type", "request_hash", "projection", "version"])
def test_does_not_trust_posted_projection_or_mismatched_ledger(world, change):
    db, actor, request, payload = world
    result = post(world)
    order = db.get(InboundOrder, result.inbound_order_id)
    tx = db.get(InventoryTransaction, result.inventory_transaction_id)
    movement = db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == tx.id))
    command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.idempotency_key_hash == tx.idempotency_key_hash))
    if change == "quantity": movement.quantity = Decimal("0.500")
    elif change == "target": movement.to_account_id = db.scalar(select(StockAccount.id).where(StockAccount.id.not_in((movement.from_account_id, movement.to_account_id))).limit(1))
    elif change == "source_type": tx.source_document_type = "unrelated"
    elif change == "request_hash": tx.request_hash = "a"*64
    elif change == "projection": order.status = "posted"
    else: command.target_version = request.version + 1
    db.flush()
    with pytest.raises(MaterialRequestReadError):
        service.verified_posted_receipt_lines(db, request=request, order=order)


@pytest.mark.parametrize("operation", ["shipment", "receipt"])
def test_upstream_command_cannot_belong_to_another_request(world, operation):
    db, _, request, _ = world
    result = post(world)
    command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == operation))
    command.request_reference = command.request_reference.replace(str(request.id), str(uuid4()))
    db.flush()
    with pytest.raises(MaterialRequestReadError, match="因果顺序"):
        service.verified_posted_receipt_lines(db, request=request, order=db.get(InboundOrder, result.inbound_order_id))


def test_reversed_inbound_cannot_cover_approved_demand(world):
    db, _, request, _ = world
    result = post(world)
    tx = db.get(InventoryTransaction, result.inventory_transaction_id)
    db.add(InventoryTransaction(id=uuid4(), transaction_no="closure-reversal", movement_type="reversal",
        source_document_type="test_reversal", source_document_id=str(tx.id), posting_key="closure-reversal",
        idempotency_key_hash="f"*64, request_hash="e"*64, status="posted", effective_at=tx.effective_at,
        posted_at=tx.posted_at, created_at=tx.created_at, actor_user_id=tx.actor_user_id,
        ledger_cursor=10001, reversed_transaction_id=tx.id))
    db.flush()
    with pytest.raises(MaterialRequestReadError, match="冲销事实"):
        service.verified_posted_receipt_lines(db, request=request, order=db.get(InboundOrder, result.inbound_order_id))


def test_matching_audit_fields_do_not_bypass_audit_chain(world):
    db, _, request, _ = world
    result = post(world)
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "personal_inbound_posted"))
    audit.event_hash = "0"*64
    db.flush()
    with pytest.raises(MaterialRequestReadError, match="审计链"):
        service.verified_posted_receipt_lines(db, request=request, order=db.get(InboundOrder, result.inbound_order_id))
