"""Exact, read-only supply planning quantities before reservation."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
Quantity = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,14})\.[0-9]{3}$")]

class SupplyCapacityLineOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_line_id: UUID
    approved_qty: Quantity
    cancelled_qty: Quantity
    allocated_qty: Quantity
    active_planned_qty: Quantity
    unallocated_qty: Quantity
    existing_overlap_qty: Quantity
    new_plan_qty: Quantity

class SupplyPlanningCapacityOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_version: Annotated[int, Field(strict=True, gt=0)]
    lines: Annotated[list[SupplyCapacityLineOut], Field(min_length=1, max_length=1000)]
