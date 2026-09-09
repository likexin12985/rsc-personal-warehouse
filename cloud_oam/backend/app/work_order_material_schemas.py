from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SerialVerificationIn(StrictInput):
    serial_id: UUID
    sku_code: str = Field(min_length=1, max_length=80)
    serial_no: str = Field(min_length=1, max_length=200)
    qr_code: str = Field(min_length=1, max_length=250)


class WorkOrderMaterialLineIn(StrictInput):
    material_id: UUID
    stock_account_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    condition_before: Literal["new", "used", "damaged", "scrapped"] = "new"
    serial_verifications: tuple[SerialVerificationIn, ...] = Field(default=(), max_length=1000)


class WorkOrderMaterialPreflightIn(StrictInput):
    operator_person_id: UUID
    lines: tuple[WorkOrderMaterialLineIn, ...] = Field(min_length=1, max_length=100)


class WorkOrderMaterialPreflightOut(BaseModel):
    schema_version: str = "1.0"
    work_order_id: UUID
    operator_person_id: UUID
    line_count: int
    status: Literal["coordinates_validated"] = "coordinates_validated"


WorkOrderMaterialOperationType = Literal["occupy", "release", "consume", "recover", "reverse"]


class WorkOrderMaterialOperationIn(WorkOrderMaterialPreflightIn):
    operation_type: WorkOrderMaterialOperationType
    posting_transaction_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)
    replacement_pairs: tuple["WorkOrderReplacementPairIn", ...] = ()


class WorkOrderReplacementPairIn(StrictInput):
    installed_serial_id: UUID
    removed_serial_id: UUID


class WorkOrderMaterialOperationOut(BaseModel):
    schema_version: str = "1.0"
    operation_id: UUID
    operation_no: str
    work_order_id: UUID
    posting_transaction_id: UUID
    operation_type: WorkOrderMaterialOperationType
    status: Literal["posted"]


class WorkOrderMaterialConsumeIn(WorkOrderMaterialPreflightIn):
    idempotency_key: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class WorkOrderMaterialReleaseLineIn(WorkOrderMaterialLineIn):
    target_stock_account_id: UUID


class WorkOrderMaterialReleaseIn(StrictInput):
    operator_person_id: UUID
    lines: tuple[WorkOrderMaterialReleaseLineIn, ...] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class WorkOrderMaterialOccupyIn(WorkOrderMaterialReleaseIn):
    pass


class WorkOrderMaterialRecoverLineIn(StrictInput):
    """Removed parts have one destination and an explicit used/damaged condition."""
    material_id: UUID
    target_stock_account_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    condition_before: Literal["used", "damaged"]
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    serial_verifications: tuple[SerialVerificationIn, ...] = Field(default=(), max_length=1000)


class WorkOrderMaterialRecoverIn(StrictInput):
    operator_person_id: UUID
    lines: tuple[WorkOrderMaterialRecoverLineIn, ...] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class WorkOrderMaterialOperationHistoryOut(BaseModel):
    schema_version: str = "1.0"
    items: tuple[WorkOrderMaterialOperationOut, ...]
