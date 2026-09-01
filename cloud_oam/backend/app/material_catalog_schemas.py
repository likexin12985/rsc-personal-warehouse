"""Permission-minimal active material picker contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaterialCatalogItemOut(_StrictOutputModel):
    material_id: UUID
    sku_code: StrictStr = Field(min_length=1, max_length=80)
    name: StrictStr = Field(min_length=1, max_length=200)
    specification: StrictStr = Field(max_length=300)
    base_unit: StrictStr = Field(min_length=1, max_length=32)
    tracking_mode: Literal["none", "lot", "serial", "lot_and_serial"]
    quantity_scale: int = Field(ge=0, le=3)
    allow_fraction: bool
    source_updated_at: AwareDatetime


class MaterialCatalogPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[MaterialCatalogItemOut, ...]
    next_after_id: UUID | None


__all__ = ["MaterialCatalogItemOut", "MaterialCatalogPageOut"]
