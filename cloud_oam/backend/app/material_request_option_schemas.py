"""Strict read contracts for formal material-request picker options.

The work-order picker exposes only the minimum current OAM projection needed
to replace a manually typed UUID. It contains no work-order payload, address,
inventory, approval, or fulfilment facts.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
)


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaterialRequestWorkOrderOptionOut(_StrictOutputModel):
    work_order_id: UUID
    work_order_no: StrictStr = Field(min_length=1, max_length=100)
    status: Literal["pending", "active"]
    source_system_code: Literal["starcharge_oam"]
    source_external_id: StrictStr = Field(min_length=1, max_length=250)
    source_version: StrictStr = Field(min_length=1, max_length=160)
    source_updated_at: AwareDatetime
    synced_at: AwareDatetime
    freshness_status: Literal["fresh"]

    @field_validator("work_order_id")
    @classmethod
    def validate_ids(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("work-order option identifiers cannot be zero")
        return value

    @field_validator("work_order_no", "source_external_id", "source_version")
    @classmethod
    def validate_printable_source_text(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("work-order source text must be trimmed printable text")
        return value


class MaterialRequestWorkOrderOptionPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    person_id: UUID
    authorization_version: int = Field(ge=1)
    items: tuple[MaterialRequestWorkOrderOptionOut, ...]
    next_after_id: UUID | None = None


class MaterialRequestWorkOrderOptionDetailOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    person_id: UUID
    authorization_version: int = Field(ge=1)
    item: MaterialRequestWorkOrderOptionOut


__all__ = [
    "MaterialRequestWorkOrderOptionDetailOut",
    "MaterialRequestWorkOrderOptionOut",
    "MaterialRequestWorkOrderOptionPageOut",
]
