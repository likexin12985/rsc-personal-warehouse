"""Derive serial position and lifecycle from immutable posted movements."""
from dataclasses import dataclass, replace
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from ..inventory_models import (
    InventoryMovement,
    InventoryMovementSerial,
    InventoryTransaction,
    StockAccount,
)
from .work_order_replacement_proof import serial_recovery_coordinates


class SerialLedgerError(ValueError):
    pass


@dataclass(frozen=True)
class SerialLedgerState:
    stock_account_id: UUID | None = None
    last_movement_id: UUID | None = None
    ledger_cursor: int = 0
    lifecycle_status: str = "active"
    owner_org_id: UUID | None = None
    admission_movement_id: UUID | None = None


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
    recoveries = serial_recovery_coordinates(db, identifiers)
    from .work_order_reversal_proof import serial_reversal_coordinates
    reversals = serial_reversal_coordinates(db, identifiers)
    before_movement = {}
    observed_movements = {}
    source = aliased(StockAccount)
    target = aliased(StockAccount)
    statement = (
        select(
            InventoryMovementSerial.serial_id,
            InventoryMovement.id.label("movement_id"),
            InventoryMovement.transaction_id,
            InventoryMovement.line_no,
            InventoryMovement.quantity,
            InventoryMovement.from_account_id,
            InventoryMovement.to_account_id,
            InventoryMovement.external_boundary_code,
            InventoryTransaction.movement_type,
            InventoryTransaction.source_document_type,
            InventoryTransaction.source_document_id,
            InventoryTransaction.reversed_transaction_id,
            InventoryTransaction.ledger_cursor,
            source.owner_org_id.label("source_owner_org_id"),
            target.owner_org_id.label("target_owner_org_id"),
        )
        .select_from(InventoryMovementSerial)
        .join(InventoryMovement, InventoryMovement.id == InventoryMovementSerial.movement_id)
        .join(InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id)
        .outerjoin(source, source.id == InventoryMovement.from_account_id)
        .outerjoin(target, target.id == InventoryMovement.to_account_id)
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
        before_movement[row["movement_id"], row["serial_id"]] = previous
        observed_movements[row["movement_id"], row["serial_id"]] = row
        original_tx = reversals.get((row["transaction_id"], row["serial_id"]))
        if original_tx is not None:
            original = observed_movements.get((previous.last_movement_id, row["serial_id"]))
            prior = before_movement.get((previous.last_movement_id, row["serial_id"]))
            if (original is None or prior is None or row["movement_type"] != "reversal"
                    or original["transaction_id"] != original_tx or row["reversed_transaction_id"] != original_tx
                    or row["ledger_cursor"] <= previous.ledger_cursor
                    or row["source_document_type"] != "work_order_material"
                    or original["source_document_type"] != "work_order_material"
                    or row["source_document_id"] != original["source_document_id"]
                    or original["movement_type"] not in {"reserve", "release", "consume", "inbound"}
                    or row["from_account_id"] != previous.stock_account_id
                    or row["quantity"] != original["quantity"] or row["line_no"] != original["line_no"]
                    or row["from_account_id"] != original["to_account_id"]
                    or row["to_account_id"] != original["from_account_id"]
                    or row["external_boundary_code"] != original["external_boundary_code"]):
                raise SerialLedgerError("SN 冲销没有连续的原操作及准确反向流水")
            states[row["serial_id"]] = replace(prior, stock_account_id=row["to_account_id"],
                last_movement_id=row["movement_id"], ledger_cursor=row["ledger_cursor"])
            continue
        if row["movement_type"] == "reversal" and row["source_document_type"] == "work_order_material":
            raise SerialLedgerError("SN 工单冲销缺少完整父命令与原操作关联")
        controlled_recovery = (
            previous.lifecycle_status == "consumed"
            and row["movement_type"] == "inbound"
            and row["source_document_type"] == "work_order_material"
            and row["external_boundary_code"] == "work_order_material_recover"
            and row["from_account_id"] is None and row["to_account_id"] is not None
            and row["target_owner_org_id"] == previous.owner_org_id
            and (row["transaction_id"], row["serial_id"]) in recoveries
        )
        if (
            row["ledger_cursor"] <= previous.ledger_cursor
            or row["from_account_id"] != previous.stock_account_id
            or (previous.lifecycle_status != "active" and not controlled_recovery)
            or (row["from_account_id"] is None and row["to_account_id"] is None)
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
            owner_org_id=row["target_owner_org_id"] if row["to_account_id"] is not None else row["source_owner_org_id"],
            admission_movement_id=previous.admission_movement_id or (row["movement_id"] if row["to_account_id"] is not None else None),
        )
    return states
