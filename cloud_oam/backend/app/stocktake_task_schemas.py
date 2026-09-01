"""Strict V1.0 contracts for non-opening stocktake task creation.

Opening establishment keeps its separately reviewed OAM-control boundary.
These models cover full, sample, ad-hoc, personal and termination stocktakes
without accepting actor identity, workflow state, book quantity, ledger
cursor, audit evidence or posting facts from a client.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)


_ZERO_UUID = UUID(int=0)


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_uuid(value: UUID, field_name: str) -> UUID:
    if value == _ZERO_UUID:
        raise ValueError(f"{field_name} cannot be the zero UUID")
    return value


class StocktakeScopeSelectionIn(_StrictRequestModel):
    owner_org_id: UUID
    location_id: UUID
    assignee_person_id: UUID
    scope_mode: Literal["location_all", "filtered"] = "location_all"
    material_id: UUID | None = None
    condition_code: Literal["new", "used", "damaged", "scrapped"] | None = None
    availability_bucket: Literal[
        "available",
        "reserved",
        "picking",
        "outbound",
        "in_transit",
        "arrived_pending",
        "frozen",
        "return_pending",
        "scrap_pending",
    ] | None = None
    freeze_mode: Literal["hard", "cutoff_replay"] = "hard"

    @field_validator(
        "owner_org_id",
        "location_id",
        "assignee_person_id",
        "material_id",
    )
    @classmethod
    def validate_ids(cls, value: UUID | None, info) -> UUID | None:
        return None if value is None else _required_uuid(value, info.field_name)

    @model_validator(mode="after")
    def validate_scope_shape(self):
        filters = (
            self.material_id,
            self.condition_code,
            self.availability_bucket,
        )
        if self.scope_mode == "location_all" and any(value is not None for value in filters):
            raise ValueError("location_all scope cannot include filters")
        if self.scope_mode == "filtered" and all(value is None for value in filters):
            raise ValueError("filtered scope requires at least one filter")
        return self


class StocktakeTaskCreateIn(_StrictRequestModel):
    """Manager-created task; server still revalidates every selected scope."""

    task_type: Literal["full", "sample", "ad_hoc", "termination"]
    region_org_id: UUID
    blind_count: StrictBool = True
    scopes: tuple[StocktakeScopeSelectionIn, ...] = Field(
        min_length=1,
        max_length=500,
    )
    deadline: AwareDatetime | None = None
    note: StrictStr = Field(default="", max_length=10000)

    @field_validator("region_org_id")
    @classmethod
    def validate_region(cls, value: UUID) -> UUID:
        return _required_uuid(value, "region_org_id")

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("note cannot have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_task_scope_set(self):
        dimensions = tuple(
            (
                row.location_id,
                row.material_id,
                row.condition_code,
                row.availability_bucket,
            )
            for row in self.scopes
        )
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("stocktake scopes contain duplicate dimensions")
        if self.task_type == "full" and any(
            row.scope_mode != "location_all" for row in self.scopes
        ):
            raise ValueError("full stocktake requires location_all scopes")
        return self


class PersonalStocktakeCreateIn(_StrictRequestModel):
    """Self-stocktake derives person, region and personal location server-side."""

    blind_count: StrictBool = True
    freeze_mode: Literal["hard", "cutoff_replay"] = "cutoff_replay"
    note: StrictStr = Field(default="", max_length=10000)

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("note cannot have surrounding whitespace")
        return value


class StocktakeTaskStartIn(_StrictRequestModel):
    expected_version: int = Field(ge=0)


class StocktakeTaskCreateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    task_no: StrictStr = Field(min_length=1, max_length=100)
    task_type: Literal["full", "sample", "ad_hoc", "personal", "termination"]
    status: Literal["draft"] = "draft"
    task_version: Literal[0] = 0
    scope_count: int = Field(ge=1, le=500)
    idempotency_replayed: bool


class StocktakeTaskStartOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    task_type: Literal["full", "sample", "ad_hoc", "personal", "termination"]
    status: Literal["counting"] = "counting"
    task_version: int = Field(ge=1)
    cutoff_ledger_cursor: int = Field(ge=0)
    initial_round_id: UUID
    scope_count: int = Field(ge=1, le=500)
    snapshot_line_count: int = Field(ge=0)
    active_freeze_count: int = Field(ge=1, le=500)
    idempotency_replayed: bool

    @model_validator(mode="after")
    def validate_freeze_coverage(self):
        if self.active_freeze_count != self.scope_count:
            raise ValueError("every started stocktake scope requires one active freeze")
        return self


__all__ = [
    "PersonalStocktakeCreateIn",
    "StocktakeScopeSelectionIn",
    "StocktakeTaskCreateIn",
    "StocktakeTaskCreateOut",
    "StocktakeTaskStartIn",
    "StocktakeTaskStartOut",
]
