from uuid import UUID
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr

class LogisticsEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: StrictStr = Field(pattern="^(pickup|transit|signed|exception)$")
    event_at: StrictStr = Field(min_length=1, max_length=64)
    source: StrictStr = Field(min_length=1, max_length=32)
    evidence_file_id: UUID | None = None
    external_ref: StrictStr | None = Field(default=None, max_length=200)

class LogisticsEventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    event_id: UUID
    shipment_id: UUID
    event_type: str
    event_at: str
    source: str
    evidence_file_id: UUID | None = None
    external_ref: str | None = None
    idempotency_replayed: bool = False

class LogisticsEventCommandStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    lookup_status: Literal["confirmed", "not_observed"]
    command: LogisticsEventOut | None
