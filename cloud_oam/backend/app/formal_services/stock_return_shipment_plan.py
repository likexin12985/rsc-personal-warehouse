"""Plan carrier parcels from exact departures without posting inventory."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from ..inventory_models import (StockAccount, StockBalance, FormalMaterial, InventoryLot,
    InventorySerial, SerialCurrentPosition)
from ..stock_operation_models import (StockOperationLine, StockOperationOutbound,
    StockOperationOutboundLine, StockOperationShipment, StockOperationShipmentLine)
from ..stock_return_shipment_schemas import (StockReturnShipmentPreviewIn,
    StockReturnShipmentPreviewOut, StockReturnShipmentLineOut)
from . import inventory_posting as posting, inventory_query as inventory
from . import stock_return_facts as returns, stock_return_outbound_facts as departures
from .stock_return_outbound_plan import original
from .stock_return_plan import authorize, destination
from .work_order_query import _aware
from .work_order_return_sources import _hash, _fail, _policies
from .work_order_evidence_snapshot import material_audit_cursor


def intent(operation_id, request):
    value = StockReturnShipmentPreviewIn.model_validate(request.model_dump(include=set(StockReturnShipmentPreviewIn.model_fields)))
    data = value.model_dump(mode="json")
    data["shipped_at"] = value.shipped_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    data["lines"] = [{"outbound_line_id": str(line.outbound_line_id), "quantity": format(line.quantity, ".3f"),
        "serial_ids": sorted(str(identifier) for identifier in line.serial_ids)}
        for line in sorted(value.lines, key=lambda row: str(row.outbound_line_id))]
    return {"operation_type": "ship_return", "operation_id": str(operation_id), **data}


def _committed(db, actor, accounts):
    from .stock_return_shipment_facts import shipment_result, serial_ids
    quantities = defaultdict(Decimal); serials = defaultdict(set); assigned = defaultdict(Decimal)
    rows = tuple(db.execute(select(StockOperationShipmentLine, StockOperationOutboundLine.transit_stock_account_id)
        .join(StockOperationOutboundLine, StockOperationOutboundLine.id == StockOperationShipmentLine.outbound_line_id)
        .where(StockOperationOutboundLine.transit_stock_account_id.in_(accounts)).limit(10001)
        .execution_options(populate_existing=True)))
    if len(rows) > 10000: _fail("stock_return_shipment_history_limit", "运单历史超过完整核验范围，请核验原单", 503)
    verified = set()
    for line, account_id in rows:
        if line.shipment_id not in verified:
            shipment_result(db, actor=actor, fact=db.get(StockOperationShipment, line.shipment_id, populate_existing=True))
            verified.add(line.shipment_id)
        ids = set(serial_ids(db, line))
        if serials[line.outbound_line_id] & ids: returns.invalid()
        quantities[line.outbound_line_id] += line.quantity
        serials[line.outbound_line_id].update(ids)
        assigned[account_id] += line.quantity
    return quantities, serials, assigned


def shipment_context(db, actor, order, rows):
    snapshot = inventory._projection_snapshot(db)
    at = datetime.now(timezone.utc)
    route = destination(db, person_id=actor.person_id, source_location_id=order.source_location_id,
        target_location_id=order.target_location_id, transit_location_id=order.transit_location_id, at=at)
    accounts = {row.transit_stock_account_id: db.get(StockAccount, row.transit_stock_account_id, populate_existing=True) for row in rows.values()}
    posting._authorize_account_ids(db, actor, tuple(sorted(accounts, key=str)), resource="inventory", action="read", lock_rows=False)
    posting._require_active_account_masters(db, accounts)
    inventory._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(accounts))
    # An undeparted return has no transit accounts. Passing an empty scope to
    # the posting freeze guard would query unrelated freezes globally.
    if accounts:
        posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
    quantities, prior_serials, assigned = _committed(db, actor, accounts)
    policies, fingerprint = _policies(db, {account.material_id for account in accounts.values()}, at)
    return snapshot, at, route, accounts, quantities, prior_serials, assigned, policies, fingerprint


def _basis(db, actor, order, request):
    rows = {}; parents = {}
    for chosen in request.lines:
        row = db.get(StockOperationOutboundLine, chosen.outbound_line_id, populate_existing=True)
        if row is None or row.id in rows:
            _fail("stock_return_shipment_line_invalid", "必须选择准确原发出行且不能重复")
        parent = db.get(StockOperationOutbound, row.outbound_id, populate_existing=True)
        if parent is None or parent.operation_id != order.id:
            _fail("stock_return_shipment_line_invalid", "发出行不属于该退回单")
        if parent.id not in parents:
            departures.outbound_result(db, actor=actor, fact=parent)
            parents[parent.id] = parent
        rows[row.id] = row
    snapshot, at, route, accounts, quantities, prior_serials, assigned, policies, fingerprint = shipment_context(db, actor, order, rows)
    if request.shipped_at > at or request.shipped_at < route.custody_effective_from:
        _fail("stock_return_shipment_time_invalid", "实际交运时间不能晚于当前时间或早于接收责任生效时间")
    for parent in parents.values():
        if request.shipped_at < _aware(parent.outbound_at):
            _fail("stock_return_shipment_time_invalid", "实际交运时间不能早于所选物料的实物发出时间")
    selected_serials = set(); totals = defaultdict(Decimal); views = []; moves = []
    for chosen in sorted(request.lines, key=lambda item: str(item.outbound_line_id)):
        row = rows[chosen.outbound_line_id]; account = accounts[row.transit_stock_account_id]
        balance = db.get(StockBalance, account.id, populate_existing=True)
        held = balance.quantity if balance else Decimal(0)
        remaining = row.quantity - quantities[row.id]; unassigned = held - assigned[account.id]
        if remaining < 0 or unassigned < 0: returns.invalid()
        if chosen.quantity > remaining:
            _fail("stock_return_shipment_quantity_exceeded", "本次分包超过原发出行尚未交运的数量")
        totals[account.id] += chosen.quantity
        if totals[account.id] > unassigned:
            _fail("stock_return_shipment_stock_insufficient", "整批分包超过该在途账户尚未绑定运单的实物数量")
        ids = chosen.serial_ids
        if len(set(ids)) != len(ids) or selected_serials.intersection(ids):
            _fail("stock_return_shipment_serial_duplicate", "整批运单中同一 SN 只能选择一次")
        selected_serials.update(ids)
        if not set(ids) <= set(departures.serial_ids(db, row)) - prior_serials[row.id]:
            _fail("stock_return_shipment_serial_invalid", "SN 必须来自该原发出行且尚未绑定运单")
        serial_views = []
        for identifier in sorted(ids, key=str):
            serial = db.get(InventorySerial, identifier, populate_existing=True)
            position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
            if (serial is None or position is None or serial.lifecycle_status != "active"
                    or serial.material_id != account.material_id or serial.lot_id != account.lot_id
                    or position.stock_account_id != account.id):
                _fail("stock_return_shipment_serial_position", "所选 SN 已不在原发出对应的在途账户")
            serial_views.append(dict(serial_id=identifier, serial_no=serial.serial_no))
        sku = db.get(FormalMaterial, account.material_id, populate_existing=True)
        lot = db.get(InventoryLot, account.lot_id, populate_existing=True) if account.lot_id else None
        original_line = db.get(StockOperationLine, row.operation_line_id, populate_existing=True)
        views.append(StockReturnShipmentLineOut(outbound_id=row.outbound_id, outbound_no=parents[row.outbound_id].outbound_no,
            outbound_line_id=row.id, operation_line_id=row.operation_line_id, source_recovery_line_id=original_line.source_recovery_line_id,
            transit_stock_account_id=account.id, material_id=account.material_id, sku_code=sku.sku_code, material_name=sku.name,
            base_unit=sku.base_unit, condition_code=account.condition_code, lot_id=account.lot_id, lot_no=lot.lot_no if lot else None,
            outbound_quantity=format(row.quantity, ".3f"), shipped_quantity=format(quantities[row.id], ".3f"),
            unshipped_quantity=format(remaining, ".3f"), in_transit_quantity=format(held, ".3f"),
            unassigned_quantity=format(unassigned, ".3f"), selected_quantity=format(chosen.quantity, ".3f"), selected_serials=tuple(serial_views)))
        moves.append(posting.InventoryMovementCommand(from_account_id=account.id, to_account_id=None, quantity=chosen.quantity, serial_ids=ids))
    # Reuse quantity/lot/SN policy checks only. Parcel registration never posts this command.
    command = posting.InventoryPostingCommand(transaction_no="return-parcel-preview", movement_type="transfer",
        source_document_type="stock_operation_return_shipment", source_document_id=str(order.id), posting_key="preview",
        effective_at=request.shipped_at, movements=tuple(moves))
    posting._validate_tracking_rules(command, accounts, policies)
    inventory._ensure_projection_snapshot_current(db, snapshot)
    return snapshot, route, tuple(views), fingerprint


def preview_shipment(db, *, actor, work_order_id, operation_id, request):
    request = StockReturnShipmentPreviewIn.model_validate(request.model_dump(include=set(StockReturnShipmentPreviewIn.model_fields)))
    current = authorize(db, actor, "ship_return")
    if request.operator_person_id != current.person_id: _fail("operator_mismatch", "操作人必须是当前登录人员", 403)
    with db.no_autoflush:
        audit = material_audit_cursor(db)
        if len(audit) != 1: returns.invalid()
        order, original_result = original(db, current, operation_id, work_order_id)
        first = _basis(db, current, order, request)
        latest = _basis(db, current, order, request)
        if first != latest or material_audit_cursor(db) != audit or returns.order_result(db, actor=current, order=order) != original_result:
            _fail("stock_return_shipment_plan_changed", "原发出、分包、库存或接收责任在预检期间变化，请重新核验")
        snapshot, route, views, policies = latest
        inventory._ensure_projection_snapshot_current(db, snapshot)
        authorize(db, current, "ship_return")
        value = intent(operation_id, request)
        plan = {"intent": value, "authorization_version": current.authorization_version, "ledger_cursor": snapshot.ledger_cursor,
            "audit_cursor": audit[0].version, "original_request_hash": original_result.request_hash,
            "destination": route.model_dump(mode="json"), "policies": policies, "lines": [row.model_dump(mode="json") for row in views]}
        return StockReturnShipmentPreviewOut(operation_id=order.id, operation_no=order.operation_no, work_order_id=work_order_id,
            operator_person_id=current.person_id, authorization_version=current.authorization_version, shipped_at=request.shipped_at,
            carrier=request.carrier, tracking_no=request.tracking_no, reason=request.reason, ledger_cursor=snapshot.ledger_cursor,
            checked_at=datetime.now(timezone.utc), destination=route, request_hash=_hash(value), plan_hash=_hash(plan), lines=views), plan
