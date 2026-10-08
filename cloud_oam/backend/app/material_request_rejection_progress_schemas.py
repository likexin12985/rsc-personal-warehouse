"""Independent return cancellation, physical departure and carrier handover."""
from datetime import timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from .material_request_closure_schemas import MaterialRequestCloseIn
from .material_request_my_receipt_schemas import StrictModel


class RejectionProgressIn(MaterialRequestCloseIn):
    model_config = ConfigDict(extra='forbid', revalidate_instances='always')
    action: Literal['cancel_registration', 'depart', 'handover']
    registration_request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    previous_event_id: UUID | None = None
    previous_request_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    physical_at: AwareDatetime | None = None
    carrier: str | None = Field(default=None, min_length=1, max_length=100)
    tracking_no: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator('physical_at', mode='before')
    @classmethod
    def explicit_timestamp(cls, value):
        if isinstance(value, (int, float, bool)):
            raise ValueError('physical time must be an explicit timezone-aware timestamp')
        return value

    @field_validator('physical_at')
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc) if value is not None else None

    @field_validator('carrier', 'tracking_no')
    @classmethod
    def clean_text(cls, value):
        if value is not None and (value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError('carrier and tracking number must be trimmed text without control characters')
        return value

    @model_validator(mode='after')
    def exact_action(self):
        if (self.previous_event_id is None) != (self.previous_request_hash is None):
            raise ValueError('previous event identity and hash must be supplied together')
        if self.previous_event_id is not None and not self.previous_event_id.int:
            raise ValueError('previous event identity must be nonzero')
        if self.action == 'handover':
            if self.previous_event_id is None or self.carrier is None or self.tracking_no is None:
                raise ValueError('handover requires exact departure and carrier tracking evidence')
        elif self.previous_event_id is not None or self.carrier is not None or self.tracking_no is not None:
            raise ValueError('registration cancellation and departure do not accept handover fields')
        if (self.action == 'cancel_registration') != (self.physical_at is None):
            raise ValueError('only departure and handover require a physical event time')
        return self


class RejectionProgressOut(StrictModel):
    event_id: UUID
    return_id: UUID
    request_id: UUID
    request_version: int
    action: Literal['cancel_registration', 'depart', 'handover']
    registration_request_hash: str
    previous_event_id: UUID | None
    previous_request_hash: str | None
    physical_at: AwareDatetime | None
    recorded_at: AwareDatetime
    carrier: str | None
    tracking_no: str | None
    reason: str
    request_hash: str
    replayed: bool


class RejectionProgressStateOut(StrictModel):
    return_id: UUID
    request_id: UUID
    registration_request_hash: str
    status: Literal['registered', 'cancelled', 'departed', 'handed_over']
    events: tuple[RejectionProgressOut, ...] = Field(max_length=2)
