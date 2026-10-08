"""Counts of independent request projections, never inferred stock quantities."""
from typing import Literal, get_args
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.demand_schemas import MaterialRequestStateAxesOut
from app.material_request_read_schemas import MaterialRequestApprovalStepOut

STATE_VALUES = {
    name: get_args(field.annotation)
    for name, field in MaterialRequestStateAxesOut.model_fields.items()
}
APPROVAL_VALUES = ('not_started', *get_args(MaterialRequestApprovalStepOut.model_fields['status'].annotation))
DIMENSIONS = {**STATE_VALUES, **{f'approval_level_{level}': APPROVAL_VALUES for level in (1, 2, 3)}}


class MaterialRequestOverviewOut(BaseModel):
    model_config = ConfigDict(extra='forbid')

    metric: Literal['current_request_counts'] = 'current_request_counts'
    observed_at: AwareDatetime
    created_from: AwareDatetime | None
    created_before: AwareDatetime | None
    organization_id: UUID | None
    matched_requests: StrictInt = Field(ge=0)
    counts: dict[str, dict[str, StrictInt]]

    @model_validator(mode='after')
    def complete_independent_dimensions(self):
        if self.created_from and self.created_before and self.created_from >= self.created_before:
            raise ValueError('invalid creation time interval')
        if set(self.counts) != set(DIMENSIONS):
            raise ValueError('missing or unrecognized dimension')
        for name, values in DIMENSIONS.items():
            actual = self.counts[name]
            if set(actual) != set(values) or any(value < 0 for value in actual.values()):
                raise ValueError('unrecognized state or negative count')
            if sum(actual.values()) != self.matched_requests:
                raise ValueError('each dimension must cover the same request cohort exactly once')
        return self
