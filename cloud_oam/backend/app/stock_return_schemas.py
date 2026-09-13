"""Formal return submission/cancellation; neither is an accepted receipt."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .work_order_material_schemas import StrictInput
from .work_order_return_schemas import WorkOrderReturnSelectionIn, WorkOrderReturnSelectionLineOut


class ReturnReason(StrictInput):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def explicit_reason(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("请填写明确的退回或取消原因")
        value.encode("utf-8")
        return value


class StockReturnPreviewIn(WorkOrderReturnSelectionIn, ReturnReason):
    target_location_id: UUID
    transit_location_id: UUID


class StockReturnDestinationOut(BaseModel):
    source_location_id: UUID
    target_location_id: UUID
    target_location_code: str
    target_location_name: str
    transit_location_id: UUID
    transit_location_code: str
    transit_location_name: str
    region_org_id: UUID
    custody_assignment_id: UUID
    custodian_person_id: UUID
    custody_effective_from: datetime


class StockReturnPreviewOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    planning_status: Literal["preview_only"] = "preview_only"
    operator_person_id: UUID
    authorization_version: int
    work_order_id: UUID
    reason: str
    ledger_cursor: int
    checked_at: datetime
    destination: StockReturnDestinationOut
    request_hash: str
    plan_hash: str
    lines: tuple[WorkOrderReturnSelectionLineOut, ...]


class StockReturnSubmitIn(StockReturnPreviewIn):
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class StockReturnCancelIn(ReturnReason):
    operator_person_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class StockReturnOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: UUID
    operation_no: str
    work_order_id: UUID
    requester_id: UUID
    status: Literal["submitted"] = "submitted"
    reason: str
    request_id: str
    request_hash: str
    plan_hash: str
    posting_transaction_id: UUID
    submitted_at: datetime
    destination: StockReturnDestinationOut
    lines: tuple[WorkOrderReturnSelectionLineOut, ...]


class StockReturnCancellationOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    cancellation_id: UUID
    operation_id: UUID
    operator_person_id: UUID
    reason: str
    request_id: str
    request_hash: str
    status: Literal["cancelled"] = "cancelled"
    posting_transaction_id: UUID
    cancelled_at: datetime


class StockReturnSealIn(StrictInput):
    operator_person_id: UUID
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class StockReturnSealOut(BaseModel):
    seal_id: UUID
    operator_person_id: UUID
    work_order_id: UUID
    operation_id: UUID | None
    operation_type: Literal["submit_return", "cancel_return"]
    request_id: str
    request_hash: str
    sealed_at: datetime


class StockReturnSealedOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    lookup_status: Literal["sealed"] = "sealed"
    seal: StockReturnSealOut
