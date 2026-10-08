"""Disjoint fulfillment quantities for compensation planning, never write permission."""
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Quantity = Annotated[str, Field(pattern=r'^(?:0|[1-9]\d{0,14})\.\d{3}$')]
BUCKETS = ('unreserved_qty', 'reserved_unpicked_qty', 'picked_unoutbound_qty',
           'outbound_unshipped_qty', 'shipped_unreceived_qty', 'accepted_unposted_qty',
           'rejected_unsettled_qty')


class RemainderLineOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_line_id: UUID
    approved_qty: Quantity
    cancelled_qty: Quantity
    posted_qty: Quantity
    unreserved_qty: Quantity
    reserved_unpicked_qty: Quantity
    picked_unoutbound_qty: Quantity
    outbound_unshipped_qty: Quantity
    shipped_unreceived_qty: Quantity
    accepted_unposted_qty: Quantity
    rejected_unsettled_qty: Quantity

    @model_validator(mode='after')
    def conserve(self):
        if self.request_line_id.int == 0 or Decimal(self.approved_qty) != sum(
                (Decimal(getattr(self, k)) for k in ('cancelled_qty', 'posted_qty', *BUCKETS)), Decimal(0)):
            raise ValueError('remaining fulfillment quantities must conserve each approved line')
        return self


class RemainderAssessmentOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    assessment: Literal['remaining_fulfillment_quantities'] = 'remaining_fulfillment_quantities'
    request_id: UUID
    revision_id: UUID
    request_version: StrictInt = Field(ge=0)
    open_supply_tasks: StrictInt = Field(ge=0)
    pending_substitutions: StrictInt = Field(ge=0)
    lines: tuple[RemainderLineOut, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def identities(self):
        if self.request_id.int == 0 or self.revision_id.int == 0 or len({v.request_line_id for v in self.lines}) != len(self.lines):
            raise ValueError('remainder assessment requires unique current line identities')
        return self
