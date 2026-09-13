"""Contract for the independent inbound fact after return-parcel acceptance.

Acceptance is deliberately a separate fact.  This module only derives the
immutable inventory command that a future 0106 inbound fact may post; it does
not create a generic ``InboundOrder`` and it never treats OAM receipt evidence
as local inventory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import uuid

from .inventory_posting import InventoryMovementCommand, InventoryPostingCommand


class ReturnInboundContractError(ValueError):
    """The accepted return lines cannot form one safe inbound command."""


@dataclass(frozen=True, slots=True)
class ReturnInboundLine:
    receipt_line_id: uuid.UUID
    source_account_id: uuid.UUID
    target_account_id: uuid.UUID
    material_id: uuid.UUID
    condition_code: str
    lot_id: uuid.UUID | None
    accepted_quantity: Decimal
    serial_ids: tuple[uuid.UUID, ...] = ()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ReturnInboundContractError("入账时刻必须带时区")
    return value.astimezone(timezone.utc)


def _id(value: object, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReturnInboundContractError(f"{label}标识无效") from exc


def _quantity(value: Decimal) -> Decimal:
    try:
        value = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReturnInboundContractError("退回入账数量无效") from exc
    if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -3:
        raise ReturnInboundContractError("退回入账数量必须为正的三位小数")
    return value.quantize(Decimal(".001"))


def build_return_inbound_command(
    *,
    receipt_id: uuid.UUID,
    effective_at: datetime,
    lines: tuple[ReturnInboundLine, ...],
    inbound_id: uuid.UUID | None = None,
) -> InventoryPostingCommand:
    """Build a transit-to-region transfer from accepted receipt lines.

    The caller must have independently proved the receipt, custody assignment,
    account dimensions, material policy, and current inventory projection.  A
    line carries those resolved dimensions so this pure boundary cannot infer
    a target from a city, person name, or OAM state.
    """

    try:
        receipt_id = uuid.UUID(str(receipt_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReturnInboundContractError("退回验收事实标识无效") from exc
    if not lines:
        raise ReturnInboundContractError("没有可入账的已接受退回明细")
    seen_lines: set[uuid.UUID] = set()
    seen_serials: set[uuid.UUID] = set()
    movements: list[InventoryMovementCommand] = []
    for line in lines:
        receipt_line_id = _id(line.receipt_line_id, "退回验收明细")
        source_account_id = _id(line.source_account_id, "退回在途账户")
        target_account_id = _id(line.target_account_id, "退回区域仓账户")
        _id(line.material_id, "物料")
        if line.lot_id is not None:
            _id(line.lot_id, "批次")
        if receipt_line_id in seen_lines:
            raise ReturnInboundContractError("同一退回验收明细只能入账一次")
        seen_lines.add(receipt_line_id)
        if source_account_id == target_account_id:
            raise ReturnInboundContractError("退回入账必须从在途账户转入区域仓账户")
        if line.condition_code not in {"used", "damaged"}:
            raise ReturnInboundContractError("退回入账成色无效")
        quantity = _quantity(line.accepted_quantity)
        try:
            serial_ids = tuple(sorted((uuid.UUID(str(identifier)) for identifier in line.serial_ids), key=str))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ReturnInboundContractError("退回入账 SN 标识无效") from exc
        if len(serial_ids) != len(set(serial_ids)) or seen_serials.intersection(serial_ids):
            raise ReturnInboundContractError("退回入账 SN 重复或跨明细重复")
        seen_serials.update(serial_ids)
        movements.append(InventoryMovementCommand(
            from_account_id=source_account_id,
            to_account_id=target_account_id,
            quantity=quantity,
            serial_ids=serial_ids,
        ))
    source_id = receipt_id if inbound_id is None else _id(inbound_id, "退回入账")
    return InventoryPostingCommand(
        transaction_no=f"INV-RETURN-IN-{source_id.hex[:16].upper()}",
        movement_type="transfer",
        source_document_type="stock_return_receipt_inbound",
        source_document_id=str(source_id),
        posting_key=f"stock-return-receipt-inbound:{receipt_id}",
        effective_at=_utc(effective_at),
        movements=tuple(movements),
    )


__all__ = ["ReturnInboundContractError", "ReturnInboundLine", "build_return_inbound_command"]
