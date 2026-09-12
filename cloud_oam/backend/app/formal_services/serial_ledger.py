"""Derive serial position and lifecycle from immutable posted movements."""
from dataclasses import dataclass
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..inventory_models import (
    InventoryMovement,
    InventoryMovementSerial,
    InventoryTransaction,
)


class SerialLedgerError(ValueError):
    pass


@dataclass(frozen=True)
class SerialLedgerState:
    stock_account_id: UUID | None = None
    last_movement_id: UUID | None = None
    ledger_cursor: int = 0
    lifecycle_status: str = "active"


def rebuild_serial_states(
    db: Session,
    serial_ids: Iterable[UUID],
    *,
    through_cursor: int | None = None,
) -> dict[UUID, SerialLedgerState]:
    """Read only; callers hold the ledger lock or verify their read snapshot.

    A terminal SN cannot reenter stock by merely changing its mutable status.
    Controlled recovery/reversal must extend this transition rule together with
    the database proof, never delete or overwrite the consumption movement.
    """
    identifiers = tuple(sorted(set(serial_ids), key=str))
    if not identifiers:
        return {}
    states = {identifier: SerialLedgerState() for identifier in identifiers}
    statement = (
        select(
            InventoryMovementSerial.serial_id,
            InventoryMovement.id.label("movement_id"),
            InventoryMovement.from_account_id,
            InventoryMovement.to_account_id,
            InventoryMovement.external_boundary_code,
            InventoryTransaction.movement_type,
            InventoryTransaction.source_document_type,
            InventoryTransaction.ledger_cursor,
        )
        .join(InventoryMovement, InventoryMovement.id == InventoryMovementSerial.movement_id)
        .join(InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id)
        .where(
            InventoryMovementSerial.serial_id.in_(identifiers),
            InventoryTransaction.status == "posted",
        )
    )
    if through_cursor is not None:
        statement = statement.where(InventoryTransaction.ledger_cursor <= through_cursor)
    rows = db.execute(statement.order_by(
        InventoryTransaction.ledger_cursor,
        InventoryMovement.line_no,
        InventoryMovementSerial.serial_id,
    )).mappings()
    for row in rows:
        previous = states[row["serial_id"]]
        if (
            row["ledger_cursor"] <= previous.ledger_cursor
            or row["from_account_id"] != previous.stock_account_id
            or previous.lifecycle_status != "active"
        ):
            raise SerialLedgerError("SN 流水不连续，或已终结的 SN 缺少受控回收/冲销事实")
        lifecycle = "active"
        if row["movement_type"] == "consume":
            if (
                row["source_document_type"] != "work_order_material"
                or row["external_boundary_code"] != "work_order_material_consume"
                or row["from_account_id"] is None
                or row["to_account_id"] is not None
            ):
                raise SerialLedgerError("SN 消耗缺少原工单库存事实")
            lifecycle = "consumed"
        elif row["movement_type"] == "scrap":
            raise SerialLedgerError("SN 报废缺少受控生命周期事实")
        states[row["serial_id"]] = SerialLedgerState(
            stock_account_id=row["to_account_id"],
            last_movement_id=row["movement_id"],
            ledger_cursor=row["ledger_cursor"],
            lifecycle_status=lifecycle,
        )
    return states
