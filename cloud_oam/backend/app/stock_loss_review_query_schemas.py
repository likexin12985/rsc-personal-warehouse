"""Scoped approval inbox projections, without submission keys or stock totals."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from .stock_loss_schemas import StockLossEvidenceOut, StockLossHeadquartersDecisionIn
from .work_order_query_schemas import WorkOrderSerialOptionOut


class LossReviewLineOut(BaseModel):
    line_id: UUID
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: Literal['new', 'used', 'damaged']
    lot_id: UUID | None
    lot_no: str | None
    quantity: str
    serials: tuple[WorkOrderSerialOptionOut, ...]


class LossReviewFactOut(BaseModel):
    review_id: UUID
    reviewer_person_id: UUID
    request_hash: str
    comment: str
    reviewed_at: datetime


class LossReviewHeadquartersFactOut(LossReviewFactOut):
    regional_review_id: UUID
    regional_review_hash: str
    decisions: tuple[StockLossHeadquartersDecisionIn, ...]


class LossReviewDetailOut(BaseModel):
    availability: Literal['available'] = 'available'
    operation_id: UUID
    operation_no: str
    owner_org_id: UUID
    owner_org_name: str
    requester_person_id: UUID
    requester_name: str
    source_location_id: UUID
    source_location_name: str
    submitted_at: datetime
    reason: str
    submission_plan_hash: str
    approval_stage: Literal['awaiting_regional', 'awaiting_headquarters', 'approved']
    approval_stock_effect: Literal['none'] = 'none'
    # This is an approval view. Never infer disposal completion from approval.
    lines: tuple[LossReviewLineOut, ...]
    evidence: tuple[StockLossEvidenceOut, ...]
    regional_review: LossReviewFactOut | None
    headquarters_review: LossReviewHeadquartersFactOut | None


class LossReviewBlockedOut(BaseModel):
    availability: Literal['blocked'] = 'blocked'
    operation_id: UUID
    reason_code: Literal['stock_loss_review_evidence_unavailable'] = 'stock_loss_review_evidence_unavailable'


class LossReviewQueueOut(BaseModel):
    schema_version: Literal['1.0'] = '1.0'
    person_id: UUID
    authorization_version: int
    stage: Literal['regional', 'headquarters']
    view: Literal['pending', 'all']
    queried_at: datetime
    items: tuple[LossReviewDetailOut | LossReviewBlockedOut, ...]
    next_after_id: UUID | None


class LossReviewQueryOut(BaseModel):
    schema_version: Literal['1.0'] = '1.0'
    person_id: UUID
    authorization_version: int
    stage: Literal['regional', 'headquarters']
    queried_at: datetime
    report: LossReviewDetailOut
