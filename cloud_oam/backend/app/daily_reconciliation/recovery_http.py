"""Safe recovery coordinates; never send or persist original business contents."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,AwareDatetime,model_validator
from .review_core import Receipt

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)

class Reference(Strict):
    cutoff_id:UUID
    actor_person_id:UUID
    original_authorization_version:Annotated[int,Field(strict=True,ge=1)]
    original_review_version:Annotated[int,Field(strict=True,ge=0,le=9223372036854775807)]
    operation:Literal['open','explain','approve','request_changes']
    trace_request_id:Annotated[str,Field(pattern=r'^[A-Za-z0-9._:-]{8,160}$')]

    @model_validator(mode='after')
    def check(self):
        if not self.cutoff_id.int or not self.actor_person_id.int:raise ValueError('nonzero coordinates required')
        return self

class LookupInput(Strict):
    expected_authorization_version:Annotated[int,Field(strict=True,ge=1)]
    reference:Reference

class SealInput(LookupInput):
    confirmation:Literal['permanently_prevent_original_daily_review_request']

class Seal(Strict):
    seal_id:UUID
    sealed_at:AwareDatetime
    reference:Reference
    permanent_nonexecution:Literal[True]=True

class RecoveryOutput(Strict):
    schema_version:Literal['rsc.daily_review_recovery.v1']='rsc.daily_review_recovery.v1'
    reference:Reference
    current_authorization_version:Annotated[int,Field(strict=True,ge=1)]
    outcome:Literal['found','sealed','not_observed']
    receipt:Receipt|None=None
    seal:Seal|None=None
    automatic_retry_allowed:Literal[False]=False

    @model_validator(mode='after')
    def check(self):
        if self.current_authorization_version<self.reference.original_authorization_version:raise ValueError('authorization regressed')
        if ((self.receipt is not None)!=(self.outcome=='found') or
            (self.seal is not None)!=(self.outcome=='sealed')):raise ValueError('contradictory recovery evidence')
        if self.seal is not None and self.seal.reference!=self.reference:raise ValueError('seal coordinates changed')
        if self.receipt is not None and self.receipt.version!=self.reference.original_review_version+1:raise ValueError('original result changed')
        return self
