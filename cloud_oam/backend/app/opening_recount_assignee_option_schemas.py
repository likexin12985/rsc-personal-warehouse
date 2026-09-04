"""Strict read contracts for task-bound opening recount assignee options."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpeningRecountAssigneeOptionOut(_StrictOutputModel):
    user_id: StrictStr = Field(min_length=1, max_length=36)
    person_id: UUID
    display_name: StrictStr = Field(min_length=1, max_length=120)
    employee_no: StrictStr = Field(min_length=1, max_length=100)
    role_code: Literal["admin", "provincial_manager", "technician"]

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        if value != value.strip() or any(
            not 33 <= ord(character) <= 126 for character in value
        ):
            raise ValueError("user_id must be printable ASCII without spaces")
        return value


class OpeningRecountAssigneeOptionPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    source_round_id: UUID
    scope_id: UUID
    location_id: UUID
    region_org_id: UUID
    task_version: int = Field(ge=0, strict=True)
    actor_person_id: UUID
    actor_authorization_version: int = Field(ge=1, strict=True)
    items: tuple[OpeningRecountAssigneeOptionOut, ...]
    next_after_person_id: UUID | None = None


__all__ = [
    "OpeningRecountAssigneeOptionOut",
    "OpeningRecountAssigneeOptionPageOut",
]
