"""Derive a work order's reserved stock from the immutable inventory ledger.

A personal reserved account can contain stock for several work orders. Its
balance is never evidence that a particular order can consume or release it.
Write callers own the ledger lock. Read callers bound the history cursor and
recheck the inventory snapshot after reading, without taking a write lock.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select

from ..inventory_models import InventoryMovement, InventoryMovementSerial, InventoryTransaction
from .inventory_posting import InventoryPostingError


@dataclass(frozen=True)
class WorkOrderReservation:
    quantity: Decimal
    serial_ids: frozenset[UUID]


def read_work_order_reservations(db, *, work_order_id, account_ids, before_cursor=None):
    account_ids = frozenset(account_ids)
    if not account_ids:
        return {}
    statement = (
        select(InventoryTransaction, InventoryMovement)
        .join(InventoryMovement, InventoryMovement.transaction_id == InventoryTransaction.id)
        .where(
            InventoryTransaction.source_document_type == "work_order_material",
            InventoryTransaction.source_document_id == str(work_order_id),
            InventoryTransaction.status == "posted",
            InventoryTransaction.movement_type.in_(("reserve", "release", "consume")),
            or_(InventoryMovement.from_account_id.in_(account_ids),
                InventoryMovement.to_account_id.in_(account_ids)),
        )
        .order_by(InventoryTransaction.ledger_cursor, InventoryMovement.line_no)
    )
    if before_cursor is not None:
        statement = statement.where(InventoryTransaction.ledger_cursor < before_cursor)
    history = tuple(db.execute(statement))
    serials = defaultdict(set)
    if history:
        for movement_id, serial_id in db.execute(select(
                InventoryMovementSerial.movement_id, InventoryMovementSerial.serial_id
        ).where(InventoryMovementSerial.movement_id.in_([m.id for _, m in history]))):
            serials[movement_id].add(serial_id)
    quantities = defaultdict(Decimal)
    reserved_serials = defaultdict(set)
    for transaction, movement in history:
        reserve = transaction.movement_type == "reserve"
        account_id = movement.to_account_id if reserve else movement.from_account_id
        if account_id not in account_ids:
            continue
        movement_serials = serials[movement.id]
        quantities[account_id] += movement.quantity if reserve else -movement.quantity
        if (quantities[account_id] < 0
                or (reserve and reserved_serials[account_id] & movement_serials)
                or (not reserve and not movement_serials <= reserved_serials[account_id])):
            raise InventoryPostingError("work_order_reservation_history_invalid", "conflict",
                                        "该工单原占用流水不连续，需先核验")
        if reserve:
            reserved_serials[account_id].update(movement_serials)
        else:
            reserved_serials[account_id].difference_update(movement_serials)
    return {identifier: WorkOrderReservation(quantities[identifier], frozenset(reserved_serials[identifier]))
            for identifier in account_ids}


def require_work_order_reservations(db, *, work_order_id, lines, before_cursor=None):
    lines = tuple(lines)
    remaining = read_work_order_reservations(db, work_order_id=work_order_id,
        account_ids={line.stock_account_id for line in lines}, before_cursor=before_cursor)
    quantities = {identifier: row.quantity for identifier, row in remaining.items()}
    reserved_serials = {identifier: set(row.serial_ids) for identifier, row in remaining.items()}
    for line in lines:
        if quantities[line.stock_account_id] < line.quantity:
            raise InventoryPostingError("work_order_reservation_insufficient", "conflict",
                                        "本工单剩余占用不足，不能使用其他工单的预留物料")
        if not set(line.serial_ids) <= reserved_serials[line.stock_account_id]:
            raise InventoryPostingError("work_order_serial_not_reserved", "conflict",
                                        "所选 SN 不在本工单剩余占用中")
        quantities[line.stock_account_id] -= line.quantity
        reserved_serials[line.stock_account_id].difference_update(line.serial_ids)
