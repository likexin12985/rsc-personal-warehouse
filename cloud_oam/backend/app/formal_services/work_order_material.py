"""Fail-closed preflight for formal work-order material operations.

This module deliberately stops before creating an operation fact or posting
inventory.  It gives the later occupy/consume commands one canonical batch
validation boundary and never consults the quarantined v0.9 work-order table.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..demand_models import (
    OamWorkOrder, WorkOrderMaterialLine, WorkOrderMaterialOperation,
    WorkOrderMaterialSerial, WorkOrderReplacementPair,
)
from ..inventory_models import (
    FormalMaterial, InventorySerial, InventoryTransaction, SerialCurrentPosition,
    StockAccount,
)


class WorkOrderMaterialPreflightError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_serial_quantity(quantity: Decimal, serial_ids: tuple[UUID, ...]) -> None:
    """Serial-tracked lines must carry one SN for each physical unit."""
    if serial_ids and quantity != Decimal(len(serial_ids)):
        raise WorkOrderMaterialPreflightError(
            "serial_quantity_mismatch", "SN 数量必须与物料数量一致"
        )


@dataclass(frozen=True, slots=True)
class WorkOrderMaterialLineInput:
    material_id: UUID
    stock_account_id: UUID
    quantity: Decimal
    serial_ids: tuple[UUID, ...] = ()
    condition_before: str = "new"


@dataclass(frozen=True, slots=True)
class WorkOrderMaterialPreflight:
    work_order_id: UUID
    operator_person_id: UUID
    lines: tuple[WorkOrderMaterialLineInput, ...]


@dataclass(frozen=True, slots=True)
class WorkOrderReplacementPairInput:
    installed_serial_id: UUID
    removed_serial_id: UUID


def operation_request_hash(*, operation_type: str, work_order_id: UUID,
                           operator_person_id: UUID,
                           lines: tuple[WorkOrderMaterialLineInput, ...],
                           replacement_pairs: tuple[WorkOrderReplacementPairInput, ...] = ()) -> str:
    """Return a stable request fingerprint for the append-only operation fact."""
    if operation_type not in {"occupy", "release", "consume", "recover", "reverse"}:
        raise WorkOrderMaterialPreflightError("operation_type_invalid", "工单物料操作类型不合法")
    validate_batch(lines)
    seen_installed: set[UUID] = set()
    seen_removed: set[UUID] = set()
    for pair in replacement_pairs:
        if pair.installed_serial_id == pair.removed_serial_id:
            raise WorkOrderMaterialPreflightError("replacement_pair_invalid", "新旧 SN 不能相同")
        if pair.installed_serial_id in seen_installed or pair.removed_serial_id in seen_removed:
            raise WorkOrderMaterialPreflightError("replacement_pair_duplicate", "新旧 SN 不得重复配对")
        seen_installed.add(pair.installed_serial_id)
        seen_removed.add(pair.removed_serial_id)
    payload = {
        "operation_type": operation_type,
        "work_order_id": str(work_order_id),
        "operator_person_id": str(operator_person_id),
        "lines": [
            {"material_id": str(row.material_id), "stock_account_id": str(row.stock_account_id),
             "quantity": str(row.quantity), "condition_before": row.condition_before,
             "serial_ids": [str(value) for value in row.serial_ids]}
            for row in lines
        ],
        "replacement_pairs": [
            {"installed_serial_id": str(row.installed_serial_id),
             "removed_serial_id": str(row.removed_serial_id)}
            for row in replacement_pairs
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_batch(lines: tuple[WorkOrderMaterialLineInput, ...]) -> None:
    if not lines:
        raise WorkOrderMaterialPreflightError("empty_batch", "物料操作至少需要一条明细")
    seen_materials: set[UUID] = set()
    seen_serials: set[UUID] = set()
    for line in lines:
        if line.quantity <= 0:
            raise WorkOrderMaterialPreflightError("quantity_invalid", "物料数量必须大于零")
        if line.condition_before not in {"new", "used", "damaged", "scrapped"}:
            raise WorkOrderMaterialPreflightError("condition_invalid", "物料状态不合法")
        validate_serial_quantity(line.quantity, line.serial_ids)
        if line.material_id in seen_materials:
            raise WorkOrderMaterialPreflightError("duplicate_material", "同一批次不得重复提交物料")
        seen_materials.add(line.material_id)
        for serial_id in line.serial_ids:
            if serial_id in seen_serials:
                raise WorkOrderMaterialPreflightError("duplicate_serial", "同一序列号不得重复提交")
            seen_serials.add(serial_id)


def preflight_work_order_material_batch(
    db: Session,
    *,
    work_order_id: UUID,
    operator_person_id: UUID,
    lines: tuple[WorkOrderMaterialLineInput, ...],
) -> WorkOrderMaterialPreflight:
    """Verify exact work-order/operator/account coordinates without writes."""
    validate_batch(lines)
    order = db.scalar(select(OamWorkOrder).where(OamWorkOrder.id == work_order_id))
    if order is None:
        raise WorkOrderMaterialPreflightError("work_order_not_found", "工单不存在")
    if order.status != "active":
        raise WorkOrderMaterialPreflightError("work_order_inactive", "工单当前不可执行物料操作")
    if order.engineer_person_id != operator_person_id:
        raise WorkOrderMaterialPreflightError("operator_mismatch", "操作人不是工单工程师")
    for line in lines:
        material = db.get(FormalMaterial, line.material_id)
        if material is None or material.status != "active":
            raise WorkOrderMaterialPreflightError("material_invalid", "物料不存在或已停用")
        account = db.get(StockAccount, line.stock_account_id)
        if account is None or account.material_id != line.material_id:
            raise WorkOrderMaterialPreflightError("stock_account_material_mismatch", "库存账户与物料不匹配")
        if account.custodian_person_id != operator_person_id:
            raise WorkOrderMaterialPreflightError("stock_account_custodian_mismatch", "库存账户不属于当前操作人")
        if account.availability_bucket != "available":
            raise WorkOrderMaterialPreflightError("stock_account_unavailable", "库存账户当前不可用于工单操作")
        if account.condition_code != line.condition_before:
            raise WorkOrderMaterialPreflightError("condition_mismatch", "物料状态与库存账户不一致")
        for serial_id in line.serial_ids:
            serial = db.get(InventorySerial, serial_id)
            if serial is None or serial.material_id != line.material_id:
                raise WorkOrderMaterialPreflightError("serial_material_mismatch", "SN 与物料不匹配")
            if serial.lifecycle_status != "active":
                raise WorkOrderMaterialPreflightError("serial_not_active", "SN 当前不可操作")
            position = db.get(SerialCurrentPosition, serial_id)
            if position is None or position.stock_account_id != line.stock_account_id:
                raise WorkOrderMaterialPreflightError("serial_account_mismatch", "SN 不在指定库存账户")
    return WorkOrderMaterialPreflight(work_order_id, operator_person_id, lines)


def record_posted_operation(
    db: Session,
    *,
    operation_type: str,
    work_order_id: UUID,
    operator_person_id: UUID,
    lines: tuple[WorkOrderMaterialLineInput, ...],
    posting_transaction_id: UUID,
    idempotency_key: str,
    replacement_pairs: tuple[WorkOrderReplacementPairInput, ...] = (),
) -> WorkOrderMaterialOperation:
    """Append a work-order fact only after the inventory transaction exists.

    The caller must invoke the unified inventory posting service first and pass
    its committed transaction coordinate.  This function never changes stock.
    """
    preflight_work_order_material_batch(
        db, work_order_id=work_order_id, operator_person_id=operator_person_id,
        lines=lines,
    )
    if not idempotency_key.strip():
        raise WorkOrderMaterialPreflightError("idempotency_key_missing", "缺少幂等键")
    transaction = db.get(InventoryTransaction, posting_transaction_id)
    if transaction is None or transaction.status != "posted":
        raise WorkOrderMaterialPreflightError("posting_transaction_missing", "库存事务尚未成功过账")
    request_hash = operation_request_hash(
        operation_type=operation_type, work_order_id=work_order_id,
        operator_person_id=operator_person_id, lines=lines,
        replacement_pairs=replacement_pairs,
    )
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    existing = db.scalar(select(WorkOrderMaterialOperation).where(
        WorkOrderMaterialOperation.idempotency_key_hash == key_hash
    ))
    if existing is not None:
        if existing.request_hash != request_hash or existing.posting_transaction_id != posting_transaction_id:
            raise WorkOrderMaterialPreflightError("idempotency_conflict", "幂等键已绑定其他工单操作")
        return existing
    operation = WorkOrderMaterialOperation(
        operation_no=f"WOM-{UUID(int=work_order_id.int).hex[:12].upper()}-{key_hash[:12].upper()}",
        oam_work_order_id=work_order_id, operator_person_id=operator_person_id,
        operation_type=operation_type, status="posted",
        posting_transaction_id=posting_transaction_id,
        idempotency_key_hash=key_hash, request_hash=request_hash,
    )
    db.add(operation)
    db.flush()
    for index, value in enumerate(lines, 1):
        line = WorkOrderMaterialLine(
            operation_id=operation.id, line_no=index, material_id=value.material_id,
            stock_account_id=value.stock_account_id, quantity=value.quantity,
            condition_before=value.condition_before, condition_after=None,
        )
        db.add(line)
        db.flush()
        for serial_id in value.serial_ids:
            db.add(WorkOrderMaterialSerial(
                operation_line_id=line.id, serial_id=serial_id,
                sku_verified=True, qr_verified=True,
            ))
    for pair in replacement_pairs:
        db.add(WorkOrderReplacementPair(
            operation_id=operation.id,
            installed_serial_id=pair.installed_serial_id,
            removed_serial_id=pair.removed_serial_id,
        ))
    return operation
