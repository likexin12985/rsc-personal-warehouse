"""Scoped, verified recovery references; none authorize stock changes or replay."""
from datetime import datetime
from typing import Annotated, Literal
from pydantic import Field
from .stock_loss_correction_http_schemas import Output, Quantity
from .formal_services.stock_loss_corrections.request_contracts import FactId, Digest
from .stock_scrap_recovery_schemas import ScrapRecoverySource
from .stock_scrap_http_schemas import RecoveryApplyFact, RecoveryRegionalFact, RecoveryHeadquartersFact, RecoveryPostingFact

Stage = Literal['apply', 'regional', 'headquarters', 'execute']
State = Literal['awaiting_application', 'awaiting_regional', 'awaiting_headquarters', 'needs_evidence', 'approved_pending_execution', 'recovered']

class ApplyReference(Output):
    stage: Literal['apply']
    source: ScrapRecoverySource

class ReviewReference(Output):
    source: ScrapRecoverySource
    recovery_request_id: FactId
    expected_request_hash: Digest

class RegionalReference(ReviewReference):
    stage: Literal['regional']

class HeadquartersReference(ReviewReference):
    stage: Literal['headquarters']
    regional_review_id: FactId
    expected_regional_hash: Digest

class ExecuteReference(ReviewReference):
    stage: Literal['execute']
    headquarters_review_id: FactId
    expected_headquarters_hash: Digest

NextReference = Annotated[ApplyReference | RegionalReference | HeadquartersReference | ExecuteReference, Field(discriminator='stage')]

class ApplicationHistory(Output):
    application: RecoveryApplyFact
    evidence_file_ids: tuple[FactId, ...]
    regional_reviews: tuple[RecoveryRegionalFact, ...]
    headquarters_reviews: tuple[RecoveryHeadquartersFact, ...]
    status: State

class RecoverySerialReference(Output):
    serial_id: FactId
    serial_no: str = Field(min_length=1, max_length=200)
    qr_code: str = Field(min_length=1, max_length=250)


class RecoverySources(Output):
    schema_version: Literal['1.0'] = '1.0'
    availability: Literal['verified'] = 'verified'
    result_scope: Literal['verified_scrap_recovery_references'] = 'verified_scrap_recovery_references'
    write_authorization_provided: Literal[False] = False
    stock_effect: Literal['none'] = 'none'
    person_id: FactId
    authorization_version: int = Field(ge=1)
    queried_at: datetime
    observed_ledger_cursor: int = Field(ge=1)
    requested_stage: Stage
    scrap_reference: ScrapRecoverySource
    root_disposition_id: FactId
    operation_id: FactId
    operation_no: str
    line_id: FactId
    owner_org_id: FactId
    requester_person_id: FactId
    requester_name: str
    material_id: FactId
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: Literal['new', 'used', 'damaged']
    quantity: Quantity
    serial_ids: tuple[FactId, ...]
    serials: tuple[RecoverySerialReference, ...]
    is_current_scrap: bool
    state: State
    next_reference: NextReference | None
    applications: tuple[ApplicationHistory, ...]
    recovery_posting: RecoveryPostingFact | None

class BlockedSource(Output):
    availability: Literal['blocked'] = 'blocked'
    scrap_line_id: FactId

class RecoveryQueue(Output):
    schema_version: Literal['1.0'] = '1.0'
    person_id: FactId
    authorization_version: int = Field(ge=1)
    requested_stage: Stage
    queried_at: datetime
    items: tuple[RecoverySources | BlockedSource, ...]
    next_after_id: FactId | None
