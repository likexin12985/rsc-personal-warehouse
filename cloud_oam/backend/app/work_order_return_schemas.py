"""Source selection for formal returns; these responses are not return orders."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .work_order_material_schemas import SerialVerificationIn, StrictInput
from .work_order_query_schemas import MyWorkOrderOut, WorkOrderSerialOptionOut


ReturnSourceBlocker = Literal["opening_not_established", "source_stale", "source_disabled",
    "history_scope_unresolved", "reversal_review_required"]


class WorkOrderReturnSerialOut(WorkOrderSerialOptionOut):
    selectable: bool


class WorkOrderReturnSourceOut(BaseModel):
    source_recovery_line_id: UUID
    recovery_operation_id: UUID
    recovery_operation_no: str
    stock_account_id: UUID
    owner_org_id: UUID
    custodian_person_id: UUID
    location_id: UUID
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: Literal["used", "damaged"]
    lot_id: UUID | None
    lot_no: str | None
    owed_quantity: str
    # Multiple recovery lines can share this balance. These quantities must
    # never be summed to obtain the batch's available stock.
    available_quantity: str
    selectable_quantity: str
    serials: tuple[WorkOrderReturnSerialOut, ...]


class WorkOrderReturnSourcesOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    person_id: UUID
    authorization_version: int
    work_order: MyWorkOrderOut
    location_id: UUID | None
    custody_effective_from: datetime | None
    ledger_cursor: int
    projected_at: datetime | None
    queried_at: datetime
    blockers: tuple[ReturnSourceBlocker, ...]
    items: tuple[WorkOrderReturnSourceOut, ...]


class WorkOrderReturnSelectionLineIn(StrictInput):
    source_recovery_line_id: UUID
    stock_account_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    serial_verifications: tuple[SerialVerificationIn, ...] = Field(default=(), max_length=1000)


class WorkOrderReturnSelectionIn(StrictInput):
    operator_person_id: UUID
    lines: tuple[WorkOrderReturnSelectionLineIn, ...] = Field(min_length=1, max_length=100)


class WorkOrderReturnSelectionLineOut(BaseModel):
    source: WorkOrderReturnSourceOut
    selected_quantity: str
    selected_serials: tuple[WorkOrderSerialOptionOut, ...]


class WorkOrderReturnSelectionOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    planning_status: Literal["source_selection_only"] = "source_selection_only"
    operator_person_id: UUID
    authorization_version: int
    work_order: MyWorkOrderOut
    location_id: UUID
    ledger_cursor: int
    checked_at: datetime
    selection_hash: str
    basis_hash: str
    lines: tuple[WorkOrderReturnSelectionLineOut, ...]
