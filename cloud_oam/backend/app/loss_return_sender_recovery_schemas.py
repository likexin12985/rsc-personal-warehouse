"""Explicit loss sender recovery outcomes without claims about unknown writes."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from app.loss_return_outbound_schemas import LossReturnOutboundOut
from app.loss_return_shipment_schemas import LossReturnShipmentOut


class LossSenderMissingOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lookup_status: Literal['not_observed']
    retry_allowed: Literal[False]


class LossSenderSealOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seal_id: UUID
    seal_scope: Literal['actor_request_id']
    operation_type: Literal['outbound_return', 'ship_return']
    operation_id: UUID
    operator_person_id: UUID
    source_loss_disposition_id: UUID
    request_id: str
    request_hash: str
    sealed_at: datetime


class LossSenderSealedOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lookup_status: Literal['sealed']
    retry_allowed: Literal[False]
    seal: LossSenderSealOut


class LossSenderOutboundFoundOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lookup_status: Literal['found']
    retry_allowed: Literal[False]
    operation_type: Literal['outbound_return']
    result: LossReturnOutboundOut


class LossSenderShipmentFoundOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lookup_status: Literal['found']
    retry_allowed: Literal[False]
    operation_type: Literal['ship_return']
    result: LossReturnShipmentOut


LossSenderOutboundLookupOut = Annotated[
    LossSenderMissingOut | LossSenderSealedOut | LossSenderOutboundFoundOut, Field(discriminator='lookup_status')]
LossSenderShipmentLookupOut = Annotated[
    LossSenderMissingOut | LossSenderSealedOut | LossSenderShipmentFoundOut, Field(discriminator='lookup_status')]
