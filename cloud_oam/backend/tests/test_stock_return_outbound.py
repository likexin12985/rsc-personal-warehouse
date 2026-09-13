"""Real posting service proves partial return departure and retained custody."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from app.stock_operation_models import StockOperationOrder, StockOperationLine, StockOperationOutbound
from app.inventory_models import StockAccount, StockBalance, SerialCurrentPosition
from app.stock_return_schemas import StockReturnCancelIn
from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn, StockReturnOutboundSubmitIn
from app.formal_services import stock_return_outbound_commands as commands, stock_return_outbound_plan as plan
from app.formal_services.stock_return_outbound_facts import outbound_result
from app.formal_services.stock_return_commands import submit_return, cancel_return
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import inventory_posting as posting
from test_stock_return_commands import db, world, stock, recovered, destination, submission, change_status
from test_inventory_posting import establish_account_for_posting, NOW
from test_work_order_removed_registration import counts, inventory


def snapshot(db):
    return counts(db), inventory(db), tuple(tuple(db.execute(text(f"SELECT * FROM {table} ORDER BY id")))
        for table in ("stock_operation_orders", "stock_operation_lines", "stock_operation_cancellations",
            "stock_operation_outbounds", "stock_operation_outbound_lines", "stock_operation_outbound_serials"))


@pytest.fixture
def prepared(db, stock, recovered, destination):
    target, transit = destination
    transit.created_at = transit.updated_at = NOW - timedelta(days=2)
    seed = StockAccount(id=uuid4(), owner_org_id=target.owner_org_id, location_id=transit.id, custodian_person_id=None,
        material_id=stock.world.material.id, condition_code="new", availability_bucket="available", lot_id=None,
        created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(days=1))
    db.add(seed); db.flush(); establish_account_for_posting(db, stock.world, seed); db.commit()
    stock.actor = replace(stock.actor, entitlements=stock.actor.entitlements + (replace(stock.actor.entitlements[0], resource="stock_operation", action="outbound_return"),))
    stock.world.current_principal = stock.actor
    value = submission(db, stock, recovered, destination)
    returned = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
    line = db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id == returned.operation_id))
    request = StockReturnOutboundPreviewIn(operator_person_id=stock.actor.person_id, outbound_at=datetime.now(timezone.utc),
        reason="核验实物后发出，待区域仓接收", lines=[dict(operation_line_id=line.id, quantity="1", serial_verifications=value.lines[0].serial_verifications)])
    return SimpleNamespace(order=returned, line=line, request=request, transit=transit, seed=seed)


def preview(db, stock, prepared, request=None):
    return plan.preview_outbound(db, actor=stock.actor, work_order_id=stock.orders[0].id, operation_id=prepared.order.operation_id,
        request=request or prepared.request)[0]


def submit(db, stock, prepared, request=None):
    request = request or prepared.request
    checked = preview(db, stock, prepared, request)
    value = StockReturnOutboundSubmitIn(**request.model_dump(), expected_plan_hash=checked.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex)
    result = commands.execute_outbound(db, actor=stock.actor, work_order_id=stock.orders[0].id, operation_id=prepared.order.operation_id, request=value)
    db.commit(); return result, value


def test_departure_moves_exact_stock_and_retains_custodian_until_acceptance(db, stock, prepared):
    result, value = submit(db, stock, prepared)
    assert result.status == "outbound" and result.lines[0].selected_quantity == "1.000"
    assert db.get(StockBalance, prepared.line.reserved_account_id).quantity == 0
    target = db.scalar(select(StockAccount).where(StockAccount.location_id == prepared.transit.id, StockAccount.availability_bucket == "in_transit"))
    assert target is not None and target.custodian_person_id == stock.actor.person_id
    assert target.owner_org_id == stock.account.owner_org_id and target.condition_code == "used"
    assert db.get(StockBalance, target.id).quantity == 1
    if stock.tracked: assert db.get(SerialCurrentPosition, stock.serials[0].id).stock_account_id == target.id
    assert commands.execute_outbound(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        operation_id=prepared.order.operation_id, request=value) == result
    db.commit(); before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        cancel_return(db, actor=stock.actor, operation_id=prepared.order.operation_id,
            request=StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="已发出禁止取消", request_id=uuid4().hex, idempotency_key=uuid4().hex))
    assert error.value.code == "stock_return_already_outbound"
    with pytest.raises(posting.InventoryPostingError) as error:
        posting._require_generic_reversal_origin(db, SimpleNamespace(original_transaction_id=result.posting_transaction_id, source_document_type="renamed"))
    assert error.value.code == "stock_return_reversal_requires_command"
    db.execute(text("PRAGMA query_only=ON"))
    assert outbound_result(db, actor=stock.actor, fact=db.get(StockOperationOutbound, result.outbound_id)) == result
    assert snapshot(db) == before and "qr_code" not in result.model_dump_json()


def test_preview_is_read_only_and_closed_work_order_keeps_own_return_eligibility(db, stock, prepared):
    change_status(db, stock.orders[0], "closed")
    before = snapshot(db); db.execute(text("PRAGMA query_only=ON"))
    result = preview(db, stock, prepared)
    assert result.planning_status == "preview_only" and result.destination.transit_location_id == prepared.transit.id
    assert result.lines[0].remaining_quantity == "1.000" and snapshot(db) == before
    assert "qr_code" not in result.model_dump_json()


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_partial_departures_preserve_remaining_exact_line_budget(db, stock, prepared):
    first = prepared.request.model_copy(update={"lines": (prepared.request.lines[0].model_copy(update={"quantity": Decimal("0.375")}),)})
    posted, _ = submit(db, stock, prepared, first)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error: preview(db, stock, prepared)
    assert error.value.code == "stock_return_outbound_quantity_exceeded" and snapshot(db) == before
    rest = first.model_copy(update={"lines": (first.lines[0].model_copy(update={"quantity": Decimal("0.625")}),)})
    checked = preview(db, stock, prepared, rest)
    assert checked.lines[0].departed_quantity == "0.375" and checked.lines[0].remaining_quantity == "0.625"
    second, _ = submit(db, stock, prepared, rest)
    assert second.posting_transaction_id != posted.posting_transaction_id
    assert db.get(StockBalance, prepared.line.reserved_account_id).quantity == 0


@pytest.mark.parametrize("damage", ["foreign_line", "excess", "duplicate", "future", "permission", "cancelled"])
def test_invalid_departures_do_not_move_any_stock(db, stock, prepared, damage):
    request = prepared.request
    if damage == "foreign_line": request = request.model_copy(update={"lines": (request.lines[0].model_copy(update={"operation_line_id": uuid4()}),)})
    elif damage == "excess": request = request.model_copy(update={"lines": (request.lines[0].model_copy(update={"quantity": Decimal(2)}),)})
    elif damage == "duplicate": request = request.model_copy(update={"lines": request.lines * 2})
    elif damage == "future": request = request.model_copy(update={"outbound_at": datetime.now(timezone.utc) + timedelta(days=1)})
    elif damage == "permission": stock.world.current_principal = replace(stock.actor, entitlements=tuple(row for row in stock.actor.entitlements if row.action != "outbound_return"))
    else:
        cancel_return(db, actor=stock.actor, operation_id=prepared.order.operation_id,
            request=StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="未发出先取消", request_id=uuid4().hex, idempotency_key=uuid4().hex)); db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError): preview(db, stock, prepared, request)
    assert snapshot(db) == before


def test_domain_audit_failure_rolls_back_departure_and_stock_together(db, stock, prepared, monkeypatch):
    checked = preview(db, stock, prepared)
    value = StockReturnOutboundSubmitIn(**prepared.request.model_dump(), expected_plan_hash=checked.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex)
    before = snapshot(db)
    def fail(*_): raise RuntimeError("synthetic departure domain failure")
    monkeypatch.setattr(commands, "_record", fail)
    with pytest.raises(RuntimeError, match="synthetic departure domain failure"), db.begin_nested():
        commands.execute_outbound(db, actor=stock.actor, work_order_id=stock.orders[0].id, operation_id=prepared.order.operation_id, request=value)
    db.expire_all(); assert snapshot(db) == before
