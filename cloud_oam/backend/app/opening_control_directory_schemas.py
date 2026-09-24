"""Published batch summaries; explicitly not a start-admission result."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from .opening_start_option_schemas import _StrictOutput


class OpeningControlBatchOut(_StrictOutput):
    publication_id: UUID
    source_system_id: UUID
    source_name: StrictStr = Field(min_length=1,max_length=200)
    captured_at: datetime
    published_at: datetime
    valid_until: datetime
    record_count: StrictInt = Field(ge=0)
    is_latest: StrictBool

    @field_validator('source_name')
    @classmethod
    def label(cls,value):
        if value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('invalid source label')
        return value

    @model_validator(mode='after')
    def time_order(self):
        if any(value.utcoffset() is None for value in (self.captured_at,self.published_at,self.valid_until)) \
                or not self.captured_at<=self.published_at<self.valid_until:
            raise ValueError('invalid publication times')
        return self


class OpeningControlBatchPageOut(_StrictOutput):
    schema_version: Literal['rsc.opening_control_batches.v1'] = 'rsc.opening_control_batches.v1'
    actor_person_id: UUID
    authorization_version: StrictInt = Field(ge=1)
    region_org_id: UUID
    start_ready: Literal[False] = False
    admission_status: Literal['not_evaluated'] = 'not_evaluated'
    items: tuple[OpeningControlBatchOut,...]
    next_after_id: UUID | None = None
