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

from ..demand_models import OamWorkOrder
from ..inventory_models import FormalMaterial, StockAccount


class WorkOrderMaterialPreflightError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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


def operation_request_hash(*, operation_type: str, work_order_id: UUID,
                           operator_person_id: UUID,
                           lines: tuple[WorkOrderMaterialLineInput, ...]) -> str:
    """Return a stable request fingerprint for the append-only operation fact."""
    if operation_type not in {"occupy", "release", "consume", "recover", "reverse"}:
        raise WorkOrderMaterialPreflightError("operation_type_invalid", "工单物料操作类型不合法")
    validate_batch(lines)
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
    return WorkOrderMaterialPreflight(work_order_id, operator_person_id, lines)
