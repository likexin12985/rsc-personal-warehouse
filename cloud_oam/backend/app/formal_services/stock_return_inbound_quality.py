"""Exact accepted-receipt condition partitions; no stock or identity inference.

Version two of inbound planning separates damaged acceptance from the unchanged
condition. It is independent of receipt confirmation and never counts damage a
second time. The caller supplies a proved receipt and its material policy.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from .stock_return_inbound_contract import (
    ReturnInboundContractError, ReturnInboundLine, build_return_inbound_command,
)


@dataclass(frozen=True, slots=True)
class AcceptedPart:
    condition_code: str
    quantity: Decimal
    serial_ids: tuple[UUID, ...]


def _quantity(value, *, positive):
    if (type(value) is not Decimal or not value.is_finite()
            or value < 0 or (positive and value == 0)
            or value.as_tuple().exponent < -3 or value >= Decimal('1000000000000000')):
        raise ReturnInboundContractError('验收数量必须为有限、非负的三位小数，接受量必须大于零')
    return value.quantize(Decimal('.001'))


def _serials(values):
    if (type(values) is not tuple or any(type(value) is not UUID or value.int == 0 for value in values)
            or len(values) != len(set(values))):
        raise ReturnInboundContractError('验收SN必须使用不重复的准确标识')
    return frozenset(values)


def accepted_parts(*, source_condition, accepted_quantity, damaged_quantity,
                   accepted_serial_ids=(), damaged_serial_ids=(), tracked):
    if source_condition not in ('new', 'used', 'damaged') or type(tracked) is not bool:
        raise ReturnInboundContractError('原成色或追踪规则无效')
    accepted = _quantity(accepted_quantity, positive=True)
    damaged = _quantity(damaged_quantity, positive=False)
    all_ids, damaged_ids = _serials(accepted_serial_ids), _serials(damaged_serial_ids)
    if damaged > accepted or not damaged_ids <= all_ids:
        raise ReturnInboundContractError('破损必须属于本次接受份额')
    if tracked:
        if len(all_ids) != accepted or len(damaged_ids) != damaged:
            raise ReturnInboundContractError('接受与破损数量必须分别等于其准确SN数量')
    elif all_ids or damaged_ids:
        raise ReturnInboundContractError('非SN物料不能携带SN')
    if source_condition == 'damaged':
        return (AcceptedPart('damaged', accepted, tuple(sorted(all_ids, key=str))),)
    pieces = ((source_condition, accepted-damaged, all_ids-damaged_ids),
              ('damaged', damaged, damaged_ids))
    return tuple(AcceptedPart(condition, quantity, tuple(sorted(ids, key=str)))
        for condition, quantity, ids in pieces if quantity > 0)


def build_quality_inbound_command(*, receipt_id, effective_at, lines: tuple[ReturnInboundLine, ...],
                                  inbound_id=None, loss_origin=None):
    """Keep original receipt-line IDs while allowing two distinct conditions.

    Atomic stock and exact receipt quantity proof remain mandatory in the
    service and at COMMIT. This builder does not authorize invented parts.
    """
    if type(lines) is not tuple or not 1 <= len(lines) <= 200:
        raise ReturnInboundContractError('没有可入账的已接受退回份额')
    parts, serials, origins, targets, commands = set(), set(), {}, {}, []
    for line in lines:
        if not isinstance(line, ReturnInboundLine):
            raise ReturnInboundContractError('入账份额类型无效')
        _quantity(line.accepted_quantity, positive=True)
        # Existing validation handles canonical UUIDs, positive quantities,
        # times, source type, and condition restrictions for each movement.
        command = build_return_inbound_command(receipt_id=receipt_id, effective_at=effective_at,
            lines=(line,), inbound_id=inbound_id, loss_origin=loss_origin)
        movement = command.movements[0]
        identifier = UUID(str(line.receipt_line_id))
        key = identifier, line.condition_code
        if key in parts:
            raise ReturnInboundContractError('同一验收行的同成色份额只能入账一次')
        parts.add(key)
        origin = movement.from_account_id, UUID(str(line.material_id)), UUID(str(line.lot_id)) if line.lot_id else None
        if identifier in origins and origins[identifier] != origin:
            raise ReturnInboundContractError('拆分份额必须保持准确原验收来源')
        origins[identifier] = origin
        dimension = origin[1:], line.condition_code
        if movement.to_account_id in targets and targets[movement.to_account_id] != dimension:
            raise ReturnInboundContractError('同一目标账户不能代表不同成色或物料批次')
        targets[movement.to_account_id] = dimension
        if serials.intersection(movement.serial_ids):
            raise ReturnInboundContractError('SN不能跨份额重复入账')
        serials.update(movement.serial_ids)
        commands.append(command)
    return replace(commands[0], movements=tuple(command.movements[0] for command in commands))
