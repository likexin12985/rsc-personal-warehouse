"""Return parcels partition physical departures while retaining inventory/custody."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from app.inventory_models import Shipment, StockBalance, SerialCurrentPosition
from app.stock_operation_models import StockOperationOutboundLine, StockOperationShipment
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from app.formal_services import stock_return_shipment_plan as plan, stock_return_shipment_commands as commands
from app.formal_services.stock_return_shipment_facts import shipment_result
from app.formal_services.stock_return_recovery import lookup_return_request, seal_return_request
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import inventory_posting as posting
from test_stock_return_outbound import db, world, stock, recovered, destination, prepared, submit as depart
from test_work_order_removed_registration import inventory, counts


@pytest.fixture
def parcel(db, stock, prepared):
    result, _ = depart(db, stock, prepared)
    line = db.scalar(select(StockOperationOutboundLine).where(StockOperationOutboundLine.outbound_id == result.outbound_id))
    stock.actor = replace(stock.actor, entitlements=stock.actor.entitlements + (replace(stock.actor.entitlements[0], resource="stock_operation", action="ship_return"),))
    stock.world.current_principal = stock.actor
    request = StockReturnShipmentPreviewIn(operator_person_id=stock.actor.person_id, carrier="人工登记承运商", tracking_no="SYNTHETIC-RETURN-1",
        shipped_at=datetime.now(timezone.utc), reason="已交承运，待接收仓验收", lines=[dict(outbound_line_id=line.id,
            quantity="1.000", serial_ids=tuple(proof.serial_id for proof in result.lines[0].selected_serials))])
    return SimpleNamespace(line=line, request=request, operation_id=result.operation_id, outbound=result)


def preview(db, stock, parcel, request=None):
    return plan.preview_shipment(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        operation_id=parcel.operation_id, request=request or parcel.request)[0]


def submission(db, stock, parcel, request=None):
    request = request or parcel.request
    checked = preview(db, stock, parcel, request)
    return StockReturnShipmentSubmitIn(**request.model_dump(), expected_plan_hash=checked.plan_hash,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)


def execute(db, stock, parcel, value):
    return commands.execute_shipment(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        operation_id=parcel.operation_id, request=value)


def snapshot(db):
    return counts(db), inventory(db), tuple(tuple(db.execute(text(f"SELECT * FROM {table} ORDER BY id")))
        for table in ("shipments", "stock_operation_shipments", "stock_operation_shipment_lines", "stock_operation_shipment_serials"))


def test_parcel_and_exact_replay_do_not_move_stock_or_release_custody(db, stock, parcel):
    before = inventory(db)
    value = submission(db, stock, parcel)
    result = execute(db, stock, parcel, value); db.commit()
    assert result.status == "shipped" and result.lines[0].selected_quantity == "1.000"
    assert "posting_transaction_id" not in result.model_dump() and "qr_code" not in result.model_dump_json()
    assert inventory(db) == before
    assert db.get(StockBalance, parcel.line.transit_stock_account_id).quantity == 1
    if stock.tracked: assert db.get(SerialCurrentPosition, stock.serials[0].id).stock_account_id == parcel.line.transit_stock_account_id
    assert execute(db, stock, parcel, value) == result
    db.commit(); after = snapshot(db)
    db.execute(text("PRAGMA query_only=ON"))
    assert shipment_result(db, actor=stock.actor, fact=db.get(StockOperationShipment, result.shipment_id)) == result
    assert snapshot(db) == after


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_partial_parcels_share_a_stock_cursor_but_keep_ordered_exact_budgets(db, stock, parcel):
    first = parcel.request.model_copy(update={"lines": (parcel.request.lines[0].model_copy(update={"quantity": Decimal("0.375")}),)})
    one = execute(db, stock, parcel, submission(db, stock, parcel, first)); db.commit()
    with pytest.raises(InventoryReadError) as exc: preview(db, stock, parcel)
    assert exc.value.code == "stock_return_shipment_quantity_exceeded"
    rest = first.model_copy(update={"tracking_no": "SYNTHETIC-RETURN-2", "lines": (first.lines[0].model_copy(update={"quantity": Decimal("0.625")}),)})
    checked = preview(db, stock, parcel, rest)
    assert checked.lines[0].shipped_quantity == "0.375" and checked.lines[0].unshipped_quantity == "0.625"
    two = execute(db, stock, parcel, submission(db, stock, parcel, rest)); db.commit()
    a, b = (db.get(StockOperationShipment, result.shipment_id) for result in (one, two))
    assert a.plan_jsonb["ledger_cursor"] == b.plan_jsonb["ledger_cursor"] and a.audit_version < b.audit_version
    assert shipment_result(db, actor=stock.actor, fact=a) == one
    assert shipment_result(db, actor=stock.actor, fact=b) == two
    assert db.get(StockBalance, parcel.line.transit_stock_account_id).quantity == 1


@pytest.mark.parametrize("damage", ["foreign_line", "duplicate_line", "excess", "future", "before_departure", "operator", "permission", "serial"])
def test_invalid_parcel_preview_changes_nothing(db, stock, parcel, damage):
    value = parcel.request
    if damage == "foreign_line": value = value.model_copy(update={"lines": (value.lines[0].model_copy(update={"outbound_line_id": uuid4()}),)})
    elif damage == "duplicate_line": value = value.model_copy(update={"lines": value.lines * 2})
    elif damage == "excess": value = value.model_copy(update={"lines": (value.lines[0].model_copy(update={"quantity": Decimal(2)}),)})
    elif damage == "future": value = value.model_copy(update={"shipped_at": datetime.now(timezone.utc) + timedelta(days=1)})
    elif damage == "before_departure": value = value.model_copy(update={"shipped_at": parcel.outbound.outbound_at - timedelta(microseconds=1)})
    elif damage == "operator": value = value.model_copy(update={"operator_person_id": uuid4()})
    elif damage == "permission": stock.world.current_principal = replace(stock.actor, entitlements=tuple(row for row in stock.actor.entitlements if row.action != "ship_return"))
    else: value = value.model_copy(update={"lines": (value.lines[0].model_copy(update={"serial_ids": (uuid4(),)}),)})
    before = snapshot(db)
    with pytest.raises(InventoryReadError): preview(db, stock, parcel, value)
    assert snapshot(db) == before


def test_changed_plan_and_audit_failure_roll_back_whole_parcel(db, stock, parcel, monkeypatch):
    value = submission(db, stock, parcel); before = snapshot(db)
    with pytest.raises(InventoryReadError) as exc:
        execute(db, stock, parcel, value.model_copy(update={"expected_plan_hash": "0" * 64}))
    assert exc.value.code == "stock_return_shipment_plan_changed" and snapshot(db) == before
    def fail(*_): raise RuntimeError("synthetic parcel audit failure")
    monkeypatch.setattr(commands, "_record", fail)
    with pytest.raises(RuntimeError, match="synthetic parcel audit failure"), db.begin_nested():
        execute(db, stock, parcel, value)
    db.expire_all(); assert snapshot(db) == before


def test_lost_response_reads_original_and_absent_request_seals_without_replay(db, stock, parcel):
    value = submission(db, stock, parcel)
    coordinates = dict(actor=stock.actor, work_order_id=stock.orders[0].id, operation_id=parcel.operation_id,
        operation_type="ship_return", request_id=value.request_id)
    result = execute(db, stock, parcel, value); db.commit(); before = snapshot(db)
    assert lookup_return_request(db, **coordinates) == result
    assert seal_return_request(db, **coordinates, request_hash=result.request_hash) == result
    assert snapshot(db) == before
    absent = value.model_copy(update={"request_id": uuid4().hex, "idempotency_key": uuid4().hex})
    missing = {**coordinates, "request_id": absent.request_id}
    assert lookup_return_request(db, **missing) is None
    sealed = seal_return_request(db, **missing, request_hash=result.request_hash); db.commit()
    assert sealed.seal.operation_type == "ship_return"
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as exc: execute(db, stock, parcel, absent)
    assert exc.value.code == "stock_return_request_sealed" and snapshot(db) == before


def test_parcel_intent_preserves_microseconds_and_normalizes_quantity_order_and_timezone():
    from uuid import UUID
    operation=uuid4();one,two=UUID(int=1),UUID(int=2)
    value=StockReturnShipmentPreviewIn(operator_person_id=uuid4(),carrier=' Synthetic carrier ',tracking_no=' TEST-1 ',
        shipped_at='2026-09-13T13:00:00.123456+08:00',reason='Synthetic canonical parcel',
        lines=[dict(outbound_line_id=two,quantity='1.000',serial_ids=[two,one]),dict(outbound_line_id=one,quantity='0.375')])
    equivalent=value.model_copy(update={'shipped_at':datetime.fromisoformat('2026-09-13T05:00:00.123456+00:00'),
        'lines':(value.lines[1],value.lines[0].model_copy(update={'quantity':Decimal(1),'serial_ids':(one,two)}))})
    assert plan.intent(operation,value)==plan.intent(operation,equivalent)
    assert plan.intent(operation,value)['shipped_at']=='2026-09-13T05:00:00.123456Z'
    for field in ('carrier','tracking_no','reason'):
        assert plan.intent(operation,value)!=plan.intent(operation,value.model_copy(update={field:'Changed '+field}))
    assert plan.intent(operation,value)!=plan.intent(operation,value.model_copy(update={'shipped_at':value.shipped_at+timedelta(microseconds=1)}))


@pytest.mark.parametrize('damage',['naive_time','empty_carrier','control_carrier','control_tracking','extra_field','infinite_quantity','excess_precision'])
def test_parcel_input_rejects_ambiguous_or_unrepresentable_values(damage):
    from pydantic import ValidationError
    value=dict(operator_person_id=uuid4(),carrier='Synthetic carrier',tracking_no='SYNTHETIC-INPUT-1',
        shipped_at='2026-09-13T05:00:00Z',reason='Synthetic input validation',lines=[dict(outbound_line_id=uuid4(),quantity='1')])
    if damage=='naive_time':value['shipped_at']='2026-09-13T05:00:00'
    elif damage=='empty_carrier':value['carrier']='   '
    elif damage=='control_carrier':value['carrier']='Synthetic\tcarrier'
    elif damage=='control_tracking':value['tracking_no']='SYNTHETIC\x7fINPUT'
    elif damage=='extra_field':value['posting_transaction_id']=str(uuid4())
    else:value['lines'][0]['quantity']='Infinity' if damage=='infinite_quantity' else '0.0001'
    with pytest.raises(ValidationError):StockReturnShipmentPreviewIn.model_validate(value)
