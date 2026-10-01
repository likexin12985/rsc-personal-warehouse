"""Public historical execution results; never proof of current inventory."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RecoveryOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ExecutionFact(RecoveryOutput):
    disposition_id: UUID
    operation_id: UUID
    line_id: UUID
    headquarters_decision_id: UUID
    executor_person_id: UUID
    authorization_version: int = Field(ge=1)
    posting_transaction_id: UUID
    posting_movement_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    source_account_id: UUID
    target_account_id: UUID
    request_id: str
    request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    status: Literal['posted']


class AccountDispositionFact(ExecutionFact):
    disposition: Literal['restore_available', 'convert_used', 'convert_damaged']


class DerivedReturnFact(ExecutionFact):
    disposition: Literal['return_to_region']
    return_operation_id: UUID
    origin_kind: Literal['loss_report']
    stock_effect: Literal['frozen_to_return_pending']
    return_fulfillment_required: Literal[True]


class ExecutionSealFact(RecoveryOutput):
    seal_id: UUID
    flow: Literal['disposition', 'return']
    operation_id: UUID
    line_id: UUID
    headquarters_decision_id: UUID
    executor_person_id: UUID
    request_id: str
    request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    sealed_at: datetime
    stock_effect: Literal['none']


class OriginalExecutionRecovery(RecoveryOutput):
    retry_permitted: Literal[False] = False
    result_scope: Literal['original_command'] = 'original_command'


class ExecutionMissing(OriginalExecutionRecovery):
    lookup_status: Literal['not_found']


class ExecutionSealed(OriginalExecutionRecovery):
    lookup_status: Literal['sealed']
    seal: ExecutionSealFact


class DispositionFound(OriginalExecutionRecovery):
    lookup_status: Literal['found']
    disposition: AccountDispositionFact


class ReturnFound(OriginalExecutionRecovery):
    lookup_status: Literal['found']
    disposition: DerivedReturnFact


DispositionRecoveryOut = Annotated[
    DispositionFound | ExecutionMissing | ExecutionSealed, Field(discriminator='lookup_status')]
ReturnRecoveryOut = Annotated[
    ReturnFound | ExecutionMissing | ExecutionSealed, Field(discriminator='lookup_status')]
