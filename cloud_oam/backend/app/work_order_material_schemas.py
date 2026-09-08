from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class WorkOrderMaterialLineIn(BaseModel):
    material_id: UUID
    stock_account_id: UUID
    quantity: Decimal = Field(gt=0)
    serial_ids: tuple[UUID, ...] = ()
    condition_before: str = "new"


class WorkOrderMaterialPreflightIn(BaseModel):
    operator_person_id: UUID
    lines: tuple[WorkOrderMaterialLineIn, ...] = Field(min_length=1)


class WorkOrderMaterialPreflightOut(BaseModel):
    schema_version: str = "1.0"
    work_order_id: UUID
    operator_person_id: UUID
    line_count: int
    status: str = "ready_for_posting"


class WorkOrderMaterialOperationIn(WorkOrderMaterialPreflightIn):
    operation_type: str
    posting_transaction_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)


class WorkOrderMaterialOperationOut(BaseModel):
    schema_version: str = "1.0"
    operation_id: UUID
    operation_no: str
    work_order_id: UUID
    posting_transaction_id: UUID
    operation_type: str
    status: str
