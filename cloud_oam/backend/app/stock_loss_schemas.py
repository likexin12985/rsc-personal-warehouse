"""Loss-source selection contracts; no submitted or frozen fact is implied."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .inventory_schemas import InventoryAccountOut
from .work_order_material_schemas import SerialVerificationIn, StrictInput
from .work_order_query_schemas import WorkOrderSerialOptionOut


class StockLossSelectionLineIn(StrictInput):
    stock_account_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    serial_verifications: tuple[SerialVerificationIn, ...] = Field(default=(), max_length=1000)

    @field_validator('quantity', mode='before')
    @classmethod
    def decimal_quantity(cls, value):
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError('报损数量必须使用十进制字符串或整数')
        return value


class StockLossSelectionIn(StrictInput):
    operator_person_id: UUID
    lines: tuple[StockLossSelectionLineIn, ...] = Field(min_length=1, max_length=100)


class StockLossSourcesOut(BaseModel):
    schema_version: Literal['1.0'] = '1.0'
    person_id: UUID
    authorization_version: int
    location_id: UUID
    custody_effective_from: datetime
    ledger_cursor: int
    projected_at: datetime | None
    queried_at: datetime
    items: tuple[InventoryAccountOut, ...]


class StockLossSelectionLineOut(BaseModel):
    source: InventoryAccountOut
    selected_quantity: str
    selected_serials: tuple[WorkOrderSerialOptionOut, ...]


class StockLossSelectionOut(BaseModel):
    schema_version: Literal['1.0'] = '1.0'
    planning_status: Literal['source_selection_only'] = 'source_selection_only'
    operator_person_id: UUID
    authorization_version: int
    location_id: UUID
    ledger_cursor: int
    checked_at: datetime
    selection_hash: str
    basis_hash: str
    lines: tuple[StockLossSelectionLineOut, ...]


class StockLossPreviewIn(StockLossSelectionIn):
    reason: str = Field(min_length=1, max_length=500)
    evidence_file_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)

    @field_validator('reason')
    @classmethod
    def explicit_reason(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 and char not in '\n\t' for char in value):
            raise ValueError('请填写明确的报损原因')
        value.encode('utf-8')
        return value

    @field_validator('evidence_file_ids')
    @classmethod
    def unique_evidence(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('同一报损证据只能选择一次')
        return value


class StockLossEvidenceOut(BaseModel):
    file_id: UUID
    original_filename: str
    sha256: str
    size_bytes: int
    mime_type: str


class StockLossPreviewOut(BaseModel):
    schema_version: Literal['1.0'] = '1.0'
    planning_status: Literal['preview_only'] = 'preview_only'
    operator_person_id: UUID
    authorization_version: int
    location_id: UUID
    ledger_cursor: int
    checked_at: datetime
    reason: str
    request_hash: str
    plan_hash: str
    lines: tuple[StockLossSelectionLineOut, ...]
    evidence: tuple[StockLossEvidenceOut, ...]


class StockLossSubmitIn(StockLossPreviewIn):
    expected_plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r'^[A-Za-z0-9._:-]+$')
    request_id: str = Field(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$')


class StockLossSubmittedOut(BaseModel):
    schema_version: Literal['1.0'] = '1.0'
    operation_id: UUID
    operation_no: str
    requester_id: UUID
    source_location_id: UUID
    status: Literal['submitted'] = 'submitted'
    reason: str
    request_id: str
    request_hash: str
    plan_hash: str
    posting_transaction_id: UUID
    submitted_at: datetime
    lines: tuple[StockLossSelectionLineOut, ...]
    evidence: tuple[StockLossEvidenceOut, ...]


class StockLossRequestLookupIn(StrictInput):
    operator_person_id: UUID
    request_id: str = Field(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$')
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r'^[A-Za-z0-9._:-]+$')
    request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class StockLossRequestFoundOut(BaseModel):
    lookup_status: Literal['found'] = 'found'
    retry_permitted: Literal[False] = False
    submission: StockLossSubmittedOut


class StockLossRequestMissingOut(BaseModel):
    lookup_status: Literal['not_found'] = 'not_found'
    retry_permitted: Literal[False] = False


class StockLossSealIn(StockLossRequestLookupIn):
    source_location_id: UUID


class StockLossSealOut(BaseModel):
    seal_id: UUID
    operator_person_id: UUID
    source_location_id: UUID
    request_id: str
    request_hash: str
    plan_hash: str
    sealed_at: datetime


class StockLossRequestSealedOut(BaseModel):
    lookup_status: Literal['sealed'] = 'sealed'
    retry_permitted: Literal[False] = False
    seal: StockLossSealOut


StockLossRequestLookupOut = StockLossRequestFoundOut | StockLossRequestMissingOut | StockLossRequestSealedOut


class StockLossRegionalReviewIn(StrictInput):
    operation_id: UUID
    expected_submission_plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    comment: str = Field(min_length=1, max_length=1000)
    request_id: str = Field(pattern=r'^[A-Za-z0-9._:-]{8,160}$')
    idempotency_key: str = Field(min_length=8, max_length=200)

    @field_validator('comment')
    @classmethod
    def reviewed_comment(cls, value):
        if not value.strip() or value != value.strip() or '\x00' in value:
            raise ValueError('核实意见不能为空或带首尾空白')
        return value


class StockLossRegionalReviewOut(BaseModel):
    review_id: UUID
    operation_id: UUID
    owner_org_id: UUID
    reviewer_person_id: UUID
    approval_stage: Literal['awaiting_headquarters'] = 'awaiting_headquarters'
    stock_effect: Literal['none'] = 'none'
    decision: Literal['verified'] = 'verified'
    comment: str
    request_id: str
    request_hash: str
    submission_plan_hash: str
    reviewed_at: datetime


class StockLossHeadquartersDecisionIn(StrictInput):
    line_id: UUID
    disposition: Literal['restore_available', 'convert_used', 'convert_damaged', 'return_to_region', 'scrap']
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def explicit_decision_reason(cls, value):
        if not value.strip() or value != value.strip() or any(ord(c) < 32 and c not in '\n\t' for c in value):
            raise ValueError('请填写明确的逐行处置理由')
        return value


class StockLossHeadquartersReviewIn(StockLossRegionalReviewIn):
    regional_review_id: UUID
    expected_regional_review_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    decisions: tuple[StockLossHeadquartersDecisionIn, ...] = Field(min_length=1, max_length=100)

    @field_validator('decisions')
    @classmethod
    def unique_lines(cls, value):
        if len({row.line_id for row in value}) != len(value):
            raise ValueError('每条原报损明细只能有一个处置决定')
        return tuple(sorted(value, key=lambda row: str(row.line_id)))


class StockLossHeadquartersReviewOut(BaseModel):
    review_id: UUID
    operation_id: UUID
    owner_org_id: UUID
    reviewer_person_id: UUID
    regional_review_id: UUID
    regional_review_hash: str
    approval_stage: Literal['approved'] = 'approved'
    stock_effect: Literal['none'] = 'none'
    disposition_stage: Literal['pending'] = 'pending'
    decision: Literal['approved'] = 'approved'
    comment: str
    request_id: str
    request_hash: str
    submission_plan_hash: str
    decisions: tuple[StockLossHeadquartersDecisionIn, ...]
    reviewed_at: datetime


class StockLossDispositionPreviewIn(StrictInput):
    """Reference an exact approved original line; all stock values are server-owned."""
    headquarters_decision_id: UUID
    expected_headquarters_review_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_submission_plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class StockLossDispositionExecuteIn(StockLossDispositionPreviewIn):
    expected_plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r'^[A-Za-z0-9._:-]+$')
    request_id: str = Field(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$')
