"""A personal posting confirms immutable accepted stock, never caller quantities."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt

from .material_request_my_receipt_schemas import StrictModel


class MyInboundIn(StrictModel):
    expected_request_version: StrictInt = Field(ge=1)
    receipt_id: UUID
    receipt_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MyInboundOut(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_version: int
    current_request_version: int
    person_id: UUID
    receipt_id: UUID
    receipt_request_hash: str
    shipment_id: UUID
    inbound_order_id: UUID
    inbound_no: str
    inventory_transaction_id: UUID
    posted_at: datetime
    request_hash: str
    idempotency_replayed: bool


class MyInboundCommandStatusOut(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    lookup_status: Literal["confirmed", "not_observed"]
    command: MyInboundOut | None = None
