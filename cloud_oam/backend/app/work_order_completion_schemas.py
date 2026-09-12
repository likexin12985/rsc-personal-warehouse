"""Observed material obligations; never an OAM close command or a stock posting."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from .work_order_query_schemas import MyWorkOrderOut, WorkOrderSerialOptionOut

CompletionIssueKind = Literal["unreleased_reservation", "pending_recovery", "pending_return", "unpaired_serial_consumption"]
CompletionBlocker = Literal["opening_not_established", "source_stale", "source_disabled", "history_scope_unresolved",
    "reversal_review_required", "unreleased_reservation", "pending_recovery", "pending_return", "unpaired_serial_consumption"]


class WorkOrderCompletionIssueOut(BaseModel):
    kind: CompletionIssueKind
    reference_id: UUID
    operation_id: UUID | None = None
    operation_no: str | None = None
    stock_account_id: UUID
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: Literal["new", "used", "damaged", "scrapped"]
    lot_id: UUID | None
    lot_no: str | None
    quantity: str
    serials: tuple[WorkOrderSerialOptionOut, ...]


class WorkOrderCompletionCheckOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    person_id: UUID
    authorization_version: int
    work_order: MyWorkOrderOut
    checked_at: datetime
    ledger_cursor: int
    material_check_status: Literal["clear", "blocked"]
    blockers: tuple[CompletionBlocker, ...]
    issue_count: int
    issues: tuple[WorkOrderCompletionIssueOut, ...]
