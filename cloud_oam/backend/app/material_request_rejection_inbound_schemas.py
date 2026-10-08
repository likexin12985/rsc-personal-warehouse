"""Clients confirm a server-computed exact plan, never select stock accounts."""
from uuid import UUID
from pydantic import Field
from .material_request_closure_schemas import MaterialRequestCloseIn
from .material_request_my_receipt_schemas import StrictModel


class RejectionInboundIn(MaterialRequestCloseIn):
    receipt_request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class RejectionInboundOut(StrictModel):
    inbound_id: UUID
    receipt_id: UUID
    return_id: UUID
    request_id: UUID
    inventory_transaction_id: UUID
    target_location_id: UUID
    request_hash: str
    plan_hash: str
    replayed: bool
