"""Quantity evidence is a prerequisite, not a business-close verdict."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class CompletionLineOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_line_id: UUID
    approved_qty: str = Field(pattern=r'^(?:0|[1-9]\d{0,14})\.\d{3}$')
    cancelled_qty: str = Field(pattern=r'^(?:0|[1-9]\d{0,14})\.\d{3}$')
    posted_qty: str = Field(pattern=r'^(?:0|[1-9]\d{0,14})\.\d{3}$')
    remaining_qty: str = Field(pattern=r'^(?:0|[1-9]\d{0,14})\.\d{3}$')

    @model_validator(mode='after')
    def conserve(self):
        if self.request_line_id.int == 0 or Decimal(self.approved_qty) != sum(
                (Decimal(getattr(self, key)) for key in ('cancelled_qty', 'posted_qty', 'remaining_qty')), Decimal(0)):
            raise ValueError('completion coverage must conserve each approved line')
        return self


class MaterialRequestCompletionOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    request_id: UUID
    request_version: StrictInt = Field(ge=0)
    revision_id: UUID
    assessment: Literal['final_approved_quantity_coverage'] = 'final_approved_quantity_coverage'
    quantity_coverage_complete: StrictBool
    pending_inbound_orders: StrictInt = Field(ge=0)
    lines: tuple[CompletionLineOut, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def consistent(self):
        if (self.request_id.int == 0 or self.revision_id.int == 0
                or len({r.request_line_id for r in self.lines}) != len(self.lines)
                or not any(Decimal(r.approved_qty) > 0 for r in self.lines)
                or self.quantity_coverage_complete != all(Decimal(r.remaining_qty) == 0 for r in self.lines)):
            raise ValueError('completion coverage summary disagrees with line facts')
        return self
