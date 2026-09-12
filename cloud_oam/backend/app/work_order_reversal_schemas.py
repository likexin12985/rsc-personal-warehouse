"""Explicit original-object selection and a read-only whole reversal proposal."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .work_order_query_schemas import MyWorkOrderOut
from .work_order_material_schemas import WorkOrderMaterialSealOut, WorkOrderMaterialSealedLookupOut


class WorkOrderReversalPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator_person_id: UUID
    original_operation_id: UUID | None = None
    original_replacement_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def reason_is_explicit(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("请填写明确的冲销原因")
        value.encode("utf-8")
        return value

    @model_validator(mode="after")
    def exactly_one_original(self):
        if (self.original_operation_id is None) == (self.original_replacement_id is None):
            raise ValueError("必须准确选择一笔原操作或一个原成对更换")
        return self


class WorkOrderReversalSerialOut(BaseModel):
    serial_id: UUID
    serial_no: str
    lifecycle_before: Literal["active", "consumed"]
    lifecycle_after: Literal["active", "consumed"]
    previous_movement_id: UUID | None
    previous_ledger_cursor: int
    registration_id: UUID | None


class WorkOrderReversalMovementOut(BaseModel):
    original_movement_id: UUID
    line_no: int
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: str
    lot_id: UUID | None
    lot_no: str | None
    from_account_id: UUID | None
    to_account_id: UUID | None
    from_bucket: str | None
    to_bucket: str | None
    quantity: str
    reservation_delta: str
    serials: tuple[WorkOrderReversalSerialOut, ...]


class WorkOrderReversalChildOut(BaseModel):
    original_operation_id: UUID
    original_operation_no: str
    original_operation_type: Literal["occupy", "release", "consume", "recover"]
    original_transaction_id: UUID
    original_ledger_cursor: int
    movements: tuple[WorkOrderReversalMovementOut, ...]


class WorkOrderReversalPairOut(BaseModel):
    installed_serial_id: UUID
    removed_serial_id: UUID


class WorkOrderReversalPreviewOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    planning_status: Literal["preview_only"] = "preview_only"
    operator_person_id: UUID
    authorization_version: int
    work_order: MyWorkOrderOut
    ledger_cursor: int
    checked_at: datetime
    original_operation_id: UUID | None
    original_replacement_id: UUID | None
    reason: str
    request_hash: str
    plan_hash: str
    children: tuple[WorkOrderReversalChildOut, ...]
    replacement_pairs: tuple[WorkOrderReversalPairOut, ...]


class WorkOrderReversalIn(WorkOrderReversalPreviewIn):
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class WorkOrderReversalResultItemOut(BaseModel):
    original_operation_id: UUID
    original_transaction_id: UUID
    inverse_operation_id: UUID
    inverse_transaction_id: UUID


class WorkOrderReversalOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["posted"] = "posted"
    reversal_id: UUID
    reversal_no: str
    work_order_id: UUID
    operator_person_id: UUID
    request_id: str
    request_hash: str
    plan_hash: str
    original_operation_id: UUID | None
    original_replacement_id: UUID | None
    reason: str
    posted_at: datetime
    items: tuple[WorkOrderReversalResultItemOut, ...]


class WorkOrderReversalSealOut(WorkOrderMaterialSealOut):
    operation_type: Literal["reverse"]


class WorkOrderReversalSealedOut(WorkOrderMaterialSealedLookupOut):
    seal: WorkOrderReversalSealOut
