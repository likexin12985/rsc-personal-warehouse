"""Derive a return-inbound posting plan from verified acceptance facts.

The plan is read-only.  It deliberately stops before creating an inbound fact
or an inventory transaction; 0106 will add those append-only write boundaries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..inventory_models import CustodyAssignment, Shipment, StockAccount, StockLocation
from ..stock_operation_models import (
    StockOperationOutboundLine,
    StockOperationReceipt,
    StockOperationReceiptLine,
    StockOperationReceiptSerial,
    StockOperationShipmentLine,
)
from . import inventory_posting as posting
from . import inventory_query as inventory
from . import stock_return_receipt_facts as receipt_facts
from .stock_return_inbound_contract import ReturnInboundLine, build_return_inbound_command
from .work_order_return_sources import _fail, _hash


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _target_account(db: Session, *, source: StockAccount, location: StockLocation, person_id: uuid.UUID) -> StockAccount:
    if source.owner_org_id != location.owner_org_id:
        _fail("stock_return_inbound_owner_mismatch", "conflict", "退回在途账户与接收仓组织不一致")
    rows = tuple(db.scalars(select(StockAccount).where(
        StockAccount.owner_org_id == location.owner_org_id,
        StockAccount.custodian_person_id == person_id,
        StockAccount.location_id == location.id,
        StockAccount.material_id == source.material_id,
        StockAccount.condition_code == source.condition_code,
        StockAccount.availability_bucket == "available",
        StockAccount.lot_id == source.lot_id,
    ).limit(2).execution_options(populate_existing=True)))
    if len(rows) != 1:
        _fail("stock_return_inbound_target_missing", "precondition_failed", "接收仓目标库存账户不存在或不唯一")
    return rows[0]


def plan_return_inbound(db: Session, *, actor, receipt_id: uuid.UUID) -> dict:
    """Return a verified, non-mutating plan for the accepted return quantity."""

    current = posting._require_current_actor(db, actor)
    fact = db.get(StockOperationReceipt, receipt_id, populate_existing=True)
    if fact is None:
        _fail("stock_return_inbound_receipt_not_found", "not_found", "退回验收事实不存在")
    if fact.operator_person_id != current.person_id:
        _fail("stock_return_inbound_forbidden", "forbidden", "只有当前接收责任人可以发起退回入账")
    header = db.get(Shipment, fact.shipment_id, populate_existing=True)
    location = db.get(StockLocation, header.target_location_id, populate_existing=True) if header else None
    if (header is None or location is None or location.location_type != "region" or location.status != "active"
            or location.custodian_person_id != current.person_id):
        _fail("stock_return_inbound_target_invalid", "conflict", "退回接收仓或当前保管责任已变化")
    assignments = tuple(db.scalars(select(CustodyAssignment).where(
        CustodyAssignment.id == fact.target_custody_assignment_id,
        CustodyAssignment.location_id == location.id,
        CustodyAssignment.custodian_person_id == current.person_id,
        CustodyAssignment.valid_from <= _aware(fact.created_at),
    ).limit(2).execution_options(populate_existing=True)))
    assignment = assignments[0] if len(assignments) == 1 else None
    if assignment is None or (assignment.valid_to is not None and _aware(assignment.valid_to) <= _aware(fact.created_at)):
        _fail("stock_return_inbound_custody_invalid", "conflict", "退回验收责任已失效，不能入账")

    result = receipt_facts.receipt_result(db, actor=current, fact=fact)
    receipt_lines = tuple(db.scalars(select(StockOperationReceiptLine).where(
        StockOperationReceiptLine.receipt_id == fact.id).order_by(StockOperationReceiptLine.line_no)))
    by_shipment_line = {row.shipment_line_id: row for row in receipt_lines}
    if len(by_shipment_line) != len(receipt_lines):
        receipt_facts.invalid()
    movements: list[ReturnInboundLine] = []
    projected_lines = []
    for view in result.lines:
        accepted = Decimal(view.accepted_qty)
        if accepted <= 0:
            continue
        receipt_line = by_shipment_line.get(view.shipment_line_id)
        shipment_line = db.get(StockOperationShipmentLine, view.shipment_line_id, populate_existing=True)
        outbound_line = db.get(StockOperationOutboundLine, shipment_line.outbound_line_id, populate_existing=True) if shipment_line else None
        source = db.get(StockAccount, outbound_line.transit_stock_account_id, populate_existing=True) if outbound_line else None
        if receipt_line is None or shipment_line is None or outbound_line is None or source is None:
            receipt_facts.invalid()
        target = _target_account(db, source=source, location=location, person_id=current.person_id)
        serial_ids = tuple(db.scalars(select(StockOperationReceiptSerial.serial_id).where(
            StockOperationReceiptSerial.line_id == receipt_line.id,
            StockOperationReceiptSerial.result == "accepted",
        ).order_by(StockOperationReceiptSerial.serial_id)))
        movements.append(ReturnInboundLine(
            receipt_line_id=receipt_line.id,
            source_account_id=source.id,
            target_account_id=target.id,
            material_id=source.material_id,
            condition_code=source.condition_code,
            lot_id=source.lot_id,
            accepted_quantity=accepted,
            serial_ids=serial_ids,
        ))
        projected_lines.append({
            "receipt_line_id": str(receipt_line.id),
            "shipment_line_id": str(view.shipment_line_id),
            "source_account_id": str(source.id),
            "target_account_id": str(target.id),
            "material_id": str(source.material_id),
            "condition_code": source.condition_code,
            "lot_id": str(source.lot_id) if source.lot_id else None,
            "accepted_qty": format(accepted, ".3f"),
            "serial_ids": [str(identifier) for identifier in serial_ids],
        })
    command = build_return_inbound_command(
        receipt_id=fact.id,
        effective_at=_aware(fact.created_at),
        lines=tuple(movements),
    )
    snapshot = inventory._projection_snapshot(db)
    result = {
        "schema_version": "1.0",
        "planning_status": "inbound_preview_only",
        "receipt_id": fact.id,
        "shipment_id": fact.shipment_id,
        "operator_person_id": current.person_id,
        "authorization_version": current.authorization_version,
        "target_location_id": location.id,
        "target_custody_assignment_id": assignment.id,
        "receipt_plan_hash": fact.plan_hash,
        "reason": fact.reason,
        "ledger_cursor": snapshot.ledger_cursor,
        "checked_at": datetime.now(timezone.utc),
        "command": command,
        "lines": tuple(projected_lines),
    }
    result["plan_hash"] = _hash({
        "schema_version": result["schema_version"],
        "receipt_id": str(result["receipt_id"]),
        "shipment_id": str(result["shipment_id"]),
        "operator_person_id": str(result["operator_person_id"]),
        "authorization_version": result["authorization_version"],
        "target_location_id": str(result["target_location_id"]),
        "target_custody_assignment_id": str(result["target_custody_assignment_id"]),
        "receipt_plan_hash": result["receipt_plan_hash"],
        "ledger_cursor": result["ledger_cursor"],
        "lines": result["lines"],
    })
    return result


__all__ = ["plan_return_inbound"]
