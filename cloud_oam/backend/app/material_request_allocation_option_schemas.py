"""Strict read contracts for source-inventory allocation candidates.

The option surface is a read-only inventory directory.  It carries enough
ledger coordinates for a later allocation command to revalidate the candidate
without turning this response into an allocation or reservation fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaterialRequestAllocationOptionOut(_StrictOutputModel):
    stock_account_id: UUID
    owner_org_id: UUID
    owner_org_code: StrictStr = Field(min_length=1, max_length=100)
    owner_org_name: StrictStr = Field(min_length=1, max_length=200)
    location_owner_org_id: UUID
    location_owner_org_code: StrictStr = Field(min_length=1, max_length=100)
    location_owner_org_name: StrictStr = Field(min_length=1, max_length=200)
    location_id: UUID
    location_code: StrictStr = Field(min_length=1, max_length=100)
    location_name: StrictStr = Field(min_length=1, max_length=200)
    location_type: StrictStr = Field(min_length=1, max_length=24)
    location_parent_id: UUID | None
    custodian_person_id: UUID | None
    custodian_person_name: StrictStr | None = Field(default=None, max_length=200)
    material_id: UUID
    sku_code: StrictStr = Field(min_length=1, max_length=80)
    material_name: StrictStr = Field(min_length=1, max_length=200)
    base_unit: StrictStr = Field(min_length=1, max_length=32)
    condition_code: Literal["new", "used", "damaged", "scrapped"]
    availability_bucket: Literal["available"]
    lot_id: UUID | None
    lot_no: StrictStr | None = Field(default=None, max_length=160)
    quantity: StrictStr = Field(min_length=1, max_length=32)
    quantity_scale: int = Field(ge=0, le=3)
    balance_version: int = Field(ge=0)
    ledger_cursor: int = Field(ge=0)


class MaterialRequestAllocationOptionPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_line_id: UUID
    request_version: int = Field(ge=0)
    current_revision_id: UUID
    current_revision_no: int = Field(ge=1)
    material_id: UUID
    final_approved_qty: StrictStr = Field(min_length=1, max_length=32)
    cancelled_qty: StrictStr = Field(min_length=1, max_length=32)
    allocatable_qty: StrictStr = Field(min_length=1, max_length=32)
    projection_status: Literal["ready"]
    opening_balance_status: Literal["established"]
    projected_at: AwareDatetime | None
    ledger_cursor: int = Field(ge=0)
    items: tuple[MaterialRequestAllocationOptionOut, ...]


__all__ = [
    "MaterialRequestAllocationOptionOut",
    "MaterialRequestAllocationOptionPageOut",
]
