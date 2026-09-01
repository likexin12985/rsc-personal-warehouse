"""Public, read-only contracts for formal opening-stocktake evidence.

Raw authorization rows, audit hashes and external/OAM payloads are deliberately
absent.  Quantity fields are nullable so a blind round can stay quantity-blind
until its immutable submission and difference-set seals have both been proved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


OpeningEvidenceStatus = Literal["not_started", "counting_hidden", "sealed"]
OpeningControlReconciliationStatus = Literal[
    "not_required", "pending", "approved"
]
OpeningTaskStatus = Literal[
    "draft",
    "issued",
    "frozen",
    "counting",
    "submitted",
    "region_review",
    "hq_review",
    "approved",
    "recount_required",
    "posted",
    "closed",
    "cancelled",
]
OpeningAllowedAction = Literal[
    "count",
    "review_region",
    "review_headquarters",
    "open_recount",
    "post",
    "close",
]
OpeningObservationDisposition = Literal[
    "resolved_existing_master",
    "pending_verification",
    "requires_recount",
]


class OpeningStocktakeRoundOut(BaseModel):
    round_id: UUID
    round_no: int = Field(ge=1)
    round_type: Literal["initial", "recount"]
    status: Literal["counting", "submitted", "superseded"]
    started_at: datetime
    submitted_at: datetime | None


class OpeningStocktakeReviewSummaryOut(BaseModel):
    stage: Literal["region", "headquarters"]
    decision: Literal["approve", "recount", "reject"]
    reviewed_at: datetime


class OpeningStocktakeTaskSummaryOut(BaseModel):
    task_id: UUID
    task_no: str
    region_org_id: UUID
    status: OpeningTaskStatus
    blind_count: bool
    current_round_no: int = Field(ge=0)
    current_round_status: Literal["counting", "submitted", "superseded"] | None
    visible_scope_count: int = Field(ge=0)
    completed_scope_count: int = Field(ge=0)
    evidence_status: OpeningEvidenceStatus
    difference_count: int | None = Field(default=None, ge=0)
    reconciliation_status: OpeningControlReconciliationStatus
    reconciliation_run_id: UUID | None
    pending_control_difference_count: int = Field(ge=0)
    task_version: int = Field(ge=0)
    deadline: datetime | None
    allowed_actions: list[OpeningAllowedAction]


class OpeningStocktakeTaskPageOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[OpeningStocktakeTaskSummaryOut]
    next_after_id: UUID | None


class OpeningStocktakeScopeOut(BaseModel):
    scope_id: UUID
    scope_no: int = Field(ge=1)
    location_id: UUID
    owner_org_id: UUID
    assigned_to_me: bool
    completion_status: Literal["pending", "completed"]
    zero_confirmed: bool | None
    count_line_count: int | None = Field(default=None, ge=0)
    observation_line_count: int | None = Field(default=None, ge=0)
    serial_count: int | None = Field(default=None, ge=0)
    total_counted_qty: str | None
    completed_at: datetime | None


class OpeningStocktakeDifferenceOut(BaseModel):
    difference_id: UUID
    difference_no: int = Field(ge=1)
    scope_id: UUID | None
    difference_type: Literal[
        "missing",
        "excess",
        "wrong_location",
        "wrong_condition",
        "wrong_lot",
        "wrong_serial",
        "control_unassigned",
    ]
    material_id: UUID | None
    book_qty: str
    counted_qty: str
    difference_qty: str
    affected_qty: str
    reason_code: str | None
    evidence_required: bool


class OpeningObservationDispositionSummaryOut(BaseModel):
    disposition_id: UUID
    disposition: OpeningObservationDisposition
    resolved_material_id: UUID | None
    resolved_lot_id: UUID | None
    resolved_serial_id: UUID | None
    reason_code: str
    decided_at: datetime


class OpeningStocktakeObservationOut(BaseModel):
    observation_id: UUID
    difference_id: UUID
    observation_no: int = Field(ge=1)
    scope_id: UUID
    material_identifier_type: Literal[
        "sku_code", "qr_code", "external_code", "unknown"
    ]
    material_identifier_raw: str
    condition_code: Literal["new", "used", "damaged", "scrapped"]
    availability_bucket: Literal[
        "available",
        "reserved",
        "picking",
        "outbound",
        "in_transit",
        "arrived_pending",
        "frozen",
        "return_pending",
        "scrap_pending",
    ]
    counted_qty: str
    verification_status: Literal["verified", "pending_verification"]
    material_id: UUID | None
    lot_id: UUID | None
    lot_no_raw: str | None
    serial_id: UUID | None
    serial_no_raw: str | None
    serial_identifier_type: Literal["serial_no", "qr_code", "unknown"] | None
    disposition: OpeningObservationDispositionSummaryOut | None
    allowed_dispositions: list[OpeningObservationDisposition]


class OpeningStocktakeTaskDetailOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    task_no: str
    region_org_id: UUID
    status: OpeningTaskStatus
    blind_count: bool
    task_version: int = Field(ge=0)
    deadline: datetime | None
    cutoff_at: datetime | None
    current_round: OpeningStocktakeRoundOut | None
    evidence_status: OpeningEvidenceStatus
    reconciliation_status: OpeningControlReconciliationStatus
    reconciliation_run_id: UUID | None
    pending_control_difference_count: int = Field(ge=0)
    scopes: list[OpeningStocktakeScopeOut]
    observations: list[OpeningStocktakeObservationOut]
    differences: list[OpeningStocktakeDifferenceOut]
    reviews: list[OpeningStocktakeReviewSummaryOut]
    allowed_actions: list[OpeningAllowedAction]


__all__ = [
    "OpeningAllowedAction",
    "OpeningControlReconciliationStatus",
    "OpeningEvidenceStatus",
    "OpeningObservationDisposition",
    "OpeningObservationDispositionSummaryOut",
    "OpeningStocktakeDifferenceOut",
    "OpeningStocktakeObservationOut",
    "OpeningStocktakeRoundOut",
    "OpeningStocktakeReviewSummaryOut",
    "OpeningStocktakeScopeOut",
    "OpeningStocktakeTaskDetailOut",
    "OpeningStocktakeTaskPageOut",
    "OpeningStocktakeTaskSummaryOut",
    "OpeningTaskStatus",
]
