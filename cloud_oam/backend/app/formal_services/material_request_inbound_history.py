"""Read immutable inbound evidence after the caller authorizes request access.

This is an internal fact verifier, not a public endpoint or a closure decision.
A caller must still prove final approval/cancellation and absence of unfinished
fulfillment before appending a business-closure fact. No stock balance or
request status is changed here; the historical actor is used only for hashes.
"""
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from app.demand_models import MaterialRequestCommand, MaterialRequestLine
from app.foundation_models import AuditEvent
from app.inventory_models import (InboundPosting, InventoryTransaction, InventoryMovement,
    InventoryMovementSerial, Receipt, ReceiptLine, Shipment, ShipmentLine, OutboundPosting)
from app.formal_services import inventory_posting as inventory
from app.formal_services import material_request_inbound as inbound
from app.formal_services import material_request_outbound as outbound_history
from app.formal_services.audit_chain import AuditChainError, verify_audit_event_in_read_snapshot
from app.formal_services.material_request_fulfillment_command import verify_fulfillment_command
from app.formal_services.material_request_query import MaterialRequestReadError


@dataclass(frozen=True)
class PostedReceiptLine:
    request_line_id: UUID
    receipt_line_id: UUID
    inventory_transaction_id: UUID
    accepted_qty: Decimal


def _invalid(message="入账历史与不可变库存流水不一致"):
    raise MaterialRequestReadError("closure_inbound_history_invalid", "service_unavailable", message)


def verified_posted_receipt_lines(db, *, request, order):
    """Return exact posted coverage; never infer completion from projections."""
    with db.no_autoflush:
        try:
            return _verify(db, request=request, order=order)
        except AuditChainError:
            _invalid("入账审计链不完整，不能用于关闭需求")


def _verify(db, *, request, order):
    bindings = tuple(db.scalars(select(InboundPosting).where(InboundPosting.inbound_order_id == order.id)).all())
    if len(bindings) != 1:
        _invalid("入账单必须有唯一的库存过账事实")
    receipt = db.get(Receipt, order.receipt_id, populate_existing=True)
    shipment = db.get(Shipment, receipt.shipment_id, populate_existing=True) if receipt else None
    if receipt is None or shipment is None or receipt.status not in {"accepted", "exception"}:
        _invalid("入账来源缺少已确认收货和发运事实")
    if order.status != "pending" or order.posting_transaction_id is not None:
        _invalid("入账单原始记录被改写，不能代替库存事实")
    inbound._validate_inbound_target(shipment, order.target_location_id, order.target_person_id)
    transaction = db.get(InventoryTransaction, bindings[0].inventory_transaction_id, populate_existing=True)
    if transaction is None or transaction.status != "posted" or transaction.posted_at is None \
            or not isinstance(transaction.ledger_cursor, int) or transaction.ledger_cursor <= 0 \
            or transaction.reversed_transaction_id is not None:
        _invalid("入账缺少已完成的原始库存交易")
    if db.scalar(select(InventoryTransaction.id).where(
            InventoryTransaction.reversed_transaction_id == transaction.id).limit(1)) is not None:
        raise MaterialRequestReadError("closure_inbound_reversed", "precondition_failed",
                                       "原入账已有冲销事实，需要核验补偿链后关闭")
    commands = tuple(db.scalars(select(MaterialRequestCommand).where(
        MaterialRequestCommand.idempotency_key_hash == transaction.idempotency_key_hash)).all())
    if len(commands) != 1:
        _invalid("入账缺少唯一的需求版本命令")
    command = commands[0]
    if command.target_version > request.version or command.target_version <= 0 \
            or command.authorization_version <= 0 or transaction.actor_user_id != command.actor_user_id \
            or command.request_reference not in {
                f"/api/v1/material-requests/{request.id}/inbound-orders/{order.id}/post",
                f"/api/v1/material-requests/{request.id}/my-inbounds"}:
        _invalid("原入账命令的需求、版本或提交者绑定无效")
    historical_actor = SimpleNamespace(user_id=command.actor_user_id, person_id=command.actor_person_id,
                                       authorization_version=command.authorization_version)
    verify_fulfillment_command(db, request=request, actor=historical_actor, operation="personal_inbound",
        fact=transaction, request_reference=command.request_reference, expected_version=command.target_version)
    prior_version = 0
    for parent, operation, references in (
        (shipment, "shipment", {f"/api/v1/material-requests/{request.id}/shipments"}),
        (receipt, "receipt", {f"/api/v1/material-requests/{request.id}/receipts", f"/api/v1/material-requests/{request.id}/my-receipts"}),
    ):
        parents = tuple(db.scalars(select(MaterialRequestCommand).where(
            MaterialRequestCommand.idempotency_key_hash == parent.idempotency_key_hash)).all())
        if len(parents) != 1:
            _invalid("入账来源缺少发运或收货版本命令")
        original = parents[0]
        if original.request_reference not in references or not prior_version < original.target_version < command.target_version:
            _invalid("发运、收货和入账命令的因果顺序无效")
        verify_fulfillment_command(db, request=request,
            actor=SimpleNamespace(user_id=original.actor_user_id, person_id=original.actor_person_id),
            operation=operation, fact=parent, request_reference=original.request_reference,
            expected_version=original.target_version)
        prior_version = original.target_version
    expected = inventory._validate_posting_command(inbound._order_posting_command(db, order, create_missing=False))
    movements = tuple(db.scalars(select(InventoryMovement).where(
        InventoryMovement.transaction_id == transaction.id).order_by(InventoryMovement.line_no)).all())
    if tuple(row.line_no for row in movements) != tuple(range(1, len(expected.movements)+1)):
        _invalid("入账库存流水明细缺失或多余")
    serial_rows = tuple(db.scalars(select(InventoryMovementSerial).where(
        InventoryMovementSerial.transaction_id == transaction.id)).all())
    if any(row.movement_id not in {movement.id for movement in movements} for row in serial_rows):
        _invalid("入账 SN 不属于原库存移动")
    actual = inventory.InventoryPostingCommand(transaction.transaction_no, transaction.movement_type,
        transaction.source_document_type, transaction.source_document_id, transaction.posting_key,
        inbound._aware_time(transaction.effective_at), tuple(inventory.InventoryMovementCommand(
            row.from_account_id, row.to_account_id, row.quantity,
            tuple(sorted((serial.serial_id for serial in serial_rows if serial.movement_id == row.id), key=str)),
            row.external_boundary_code) for row in movements))
    if inventory._posting_request_hash(historical_actor, expected) != transaction.request_hash \
            or inventory._posting_document(actual) != inventory._posting_document(expected):
        _invalid()
    audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.stream_key == "material_request",
        AuditEvent.action == "personal_inbound_posted", AuditEvent.aggregate_type == "inbound_order",
        AuditEvent.aggregate_id == str(order.id))).all())
    if len(audits) != 1 or audits[0].actor_user_id != command.actor_user_id \
            or audits[0].before_jsonb != {"status": "pending"} \
            or audits[0].after_jsonb != {"status": "posted", "request_id": str(request.id),
                                        "inventory_transaction_id": str(transaction.id)}:
        _invalid("入账过账审计与库存交易不一致")
    verify_audit_event_in_read_snapshot(db, stream_key="material_request", event_id=audits[0].id)
    receipt_lines = tuple(db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id)
                                    .order_by(ReceiptLine.id)).all())
    result = []
    verified_outbounds = set()
    for line in receipt_lines:
        shipped = db.get(ShipmentLine, line.shipment_line_id, populate_existing=True)
        outbound = db.get(OutboundPosting, shipped.outbound_posting_id, populate_existing=True) if shipped else None
        approved = db.get(MaterialRequestLine, outbound.request_line_id, populate_existing=True) if outbound else None
        if shipped is None or shipped.shipment_id != shipment.id or outbound is None or approved is None \
                or outbound.request_id != request.id or approved.request_id != request.id \
                or approved.revision_no != request.revision_no or outbound.revision_id != approved.revision_id:
            _invalid("入账明细不属于需求当前版本")
        if outbound.id not in verified_outbounds:
            outbound_history.verified_outbound_history(db, fact=outbound, request=request, lock_audit=False)
            verified_outbounds.add(outbound.id)
        if line.accepted_qty > 0:
            result.append(PostedReceiptLine(approved.id, line.id, transaction.id, Decimal(line.accepted_qty)))
    if not result:
        _invalid("入账缺少合格验收数量")
    return tuple(result)
