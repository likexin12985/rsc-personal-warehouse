"""Daily comparison reads; numerical matching is independent of review."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from .review_core import ItemState, Explanation

class Output(BaseModel):
    model_config=ConfigDict(extra='forbid')

class DailySummary(Output):
    cutoff_id: UUID
    business_date: date
    source_system_id: UUID
    region_org_id: UUID
    source_publication_id: UUID
    mapping_decision_id: UUID
    local_ledger_cursor: int = Field(ge=0)
    source_captured_at: datetime
    local_captured_at: datetime
    created_at: datetime
    cutoff_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    comparison_status: Literal['matched','differences']
    comparison_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    item_count: int = Field(ge=0)
    excluded_quantity_count: int = Field(ge=0)
    review_status: Literal['not_recorded','awaiting_explanations','pending_review','changes_requested','approved']
    review_version: int = Field(ge=0)
    review_updated_at: datetime | None

class ActionContext(Output):
    person_id: UUID
    authorization_version: int = Field(strict=True,ge=1)

class DailyDetail(DailySummary):
    covered_warehouses: list[str]
    included_buckets: list[str]
    approval_comment: str = ''
    approved_by_person_id: UUID | None = None
    # Current read hints only; the command worker revalidates all authority,
    # versions, evidence and independent-review rules before writing.
    allowed_actions: list[Literal['open','explain','approve','request_changes']]
    action_context: ActionContext

class DailyPage(Output):
    items: list[DailySummary]
    next_after_id: UUID | None

class ComparisonItem(Output):
    ordinal: int = Field(ge=1)
    warehouse_code: str
    material_id: UUID
    condition: str
    external_qty: str = Field(pattern=r'^\d+\.\d{3}$')
    local_qty: str = Field(pattern=r'^\d+\.\d{3}$')
    difference: str = Field(pattern=r'^-?\d+\.\d{3}$')
    status: Literal['matched','difference']
    review: ItemState

class ExcludedQuantity(Output):
    ordinal: int = Field(ge=1)
    warehouse_code: str
    material_id: UUID
    condition: str
    bucket: str
    quantity: str = Field(pattern=r'^\d+\.\d{3}$')

class ComparisonPage(Output):
    review_version: int = Field(ge=0)
    cutoff_id: UUID
    comparison_sha256: str
    items: list[ComparisonItem]
    next_after_ordinal: int | None

class ExcludedPage(Output):
    cutoff_id: UUID
    comparison_sha256: str
    items: list[ExcludedQuantity]
    next_after_ordinal: int | None


class ReviewHistoryItem(Output):
    version: int = Field(ge=1)
    event_id: UUID
    event_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    actor_person_id: UUID
    occurred_at: datetime
    operation: Literal['open','explain','approve','request_changes']
    explanations: tuple[Explanation,...] = ()
    comment: str = ''
    returned_ordinals: tuple[int,...] = ()

class ReviewHistoryPage(Output):
    cutoff_id: UUID
    review_version: int = Field(ge=0)
    items: list[ReviewHistoryItem]
    next_after_version: int | None
