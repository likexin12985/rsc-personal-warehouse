from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt

class InboundOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_request_version: StrictInt = Field(ge=1)
    receipt_id: UUID
    target_location_id: UUID
    target_person_id: UUID

class InboundOrderOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    inbound_order_id: UUID
    inbound_no: str
    receipt_id: UUID
    target_location_id: UUID
    target_person_id: UUID
    status: str
