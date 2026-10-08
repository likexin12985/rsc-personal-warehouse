from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from .material_request_completion_schemas import CompletionLineOut, MaterialRequestCompletionOut


class MaterialRequestCloseIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_request_version: StrictInt = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value):
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('close reason must be nonempty, trimmed text without control characters')
        return value


class MaterialRequestClosureOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    closure_id: UUID
    request_id: UUID
    revision_id: UUID
    request_version: StrictInt = Field(gt=0)
    business_status: Literal['closed'] = 'closed'
    closed_at: AwareDatetime
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    lines: tuple[CompletionLineOut, ...] = Field(min_length=1, max_length=200)
    replayed: StrictBool = False

    @model_validator(mode='after')
    def complete_evidence(self):
        if self.closure_id.int == 0:
            raise ValueError('closure requires a nonempty fact identity')
        MaterialRequestCompletionOut(request_id=self.request_id, revision_id=self.revision_id,
            request_version=self.request_version, quantity_coverage_complete=True,
            pending_inbound_orders=0, lines=self.lines)
        return self


class MaterialRequestClosureStateOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    request_version: StrictInt = Field(ge=0)
    business_status: Literal['open', 'closed']
    close_permitted: StrictBool
    closure: MaterialRequestClosureOut | None

    @model_validator(mode='after')
    def consistent_state(self):
        if (self.business_status == 'closed') != (self.closure is not None):
            raise ValueError('closure state requires a matching immutable fact')
        if self.closure is not None and (self.close_permitted
                or self.closure.request_id != self.request_id
                or self.closure.request_version != self.request_version):
            raise ValueError('closed request identity or permission inconsistent')
        return self


class MaterialRequestClosureCommandStatusOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lookup_status: Literal['confirmed', 'not_observed']
    command: MaterialRequestClosureOut | None

    @model_validator(mode='after')
    def consistent_result(self):
        if (self.lookup_status == 'confirmed') != (self.command is not None):
            raise ValueError('closure recovery needs an exact command')
        if self.command is not None and not self.command.replayed:
            raise ValueError('closure recovery must be read-only replay evidence')
        return self
