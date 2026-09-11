from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OamReceiptEvidenceOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    evidence_id: UUID
    external_object_id: UUID
    shipment_id: UUID
    status: Literal["synced", "exception"]
    source_time: datetime
    source_version: str = Field(min_length=1, max_length=160)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
