"""Strict read contracts for manager stocktake creation pickers.

The option APIs return only server-validated identifiers.  They never accept
or project book quantities, workflow state, inventory write facts, or an
external-system identity.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StocktakeRegionOptionOut(_StrictOutputModel):
    region_org_id: UUID
    code: StrictStr = Field(min_length=1, max_length=80)
    name: StrictStr = Field(min_length=1, max_length=200)
    province_code: StrictStr | None = Field(default=None, min_length=1, max_length=12)


class StocktakeRegionOptionPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[StocktakeRegionOptionOut, ...]
    next_after_id: UUID | None = None
    authorization_version: int = Field(ge=1)


class StocktakeLocationOptionOut(_StrictOutputModel):
    location_id: UUID
    code: StrictStr = Field(min_length=1, max_length=100)
    name: StrictStr = Field(min_length=1, max_length=200)
    location_type: Literal["region", "personal"]
    owner_org_id: UUID
    owner_org_name: StrictStr = Field(min_length=1, max_length=200)
    custodian_person_id: UUID | None = None
    custodian_name: StrictStr | None = Field(default=None, min_length=1, max_length=120)


class StocktakeLocationOptionPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    region_org_id: UUID
    items: tuple[StocktakeLocationOptionOut, ...]
    next_after_id: UUID | None = None
    authorization_version: int = Field(ge=1)


class StocktakeAssigneeOptionOut(_StrictOutputModel):
    assignee_user_id: StrictStr = Field(min_length=1, max_length=160)
    person_id: UUID
    name: StrictStr = Field(min_length=1, max_length=120)
    employee_no: StrictStr = Field(min_length=1, max_length=100)
    role_codes: tuple[
        Literal["admin", "provincial_manager", "technician"], ...
    ] = Field(min_length=1, max_length=3)


class StocktakeAssigneeOptionPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    region_org_id: UUID
    location_id: UUID
    items: tuple[StocktakeAssigneeOptionOut, ...]
    next_after_person_id: UUID | None = None
    authorization_version: int = Field(ge=1)


__all__ = [
    "StocktakeAssigneeOptionOut",
    "StocktakeAssigneeOptionPageOut",
    "StocktakeLocationOptionOut",
    "StocktakeLocationOptionPageOut",
    "StocktakeRegionOptionOut",
    "StocktakeRegionOptionPageOut",
]
