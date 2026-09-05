"""Minimal read contracts for the opening-stocktake preparation directories.

These are eligibility hints, never inventory evidence or permission to start a
task.  The future control-evidence preparation has deliberately not run here.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*")
    @classmethod
    def _nonzero_identifier(cls, value):
        if isinstance(value, UUID) and value.int == 0:
            raise ValueError("option identifiers must not be nil UUIDs")
        return value


class _PreparationPage(_StrictOutput):
    schema_version: Literal["1.0"] = "1.0"
    actor_person_id: UUID
    authorization_version: StrictInt = Field(ge=1)
    start_ready: Literal[False] = False
    control_evidence_status: Literal["control_evidence_not_evaluated"] = (
        "control_evidence_not_evaluated"
    )


class OpeningStartRegionOptionOut(_StrictOutput):
    region_org_id: UUID
    code: StrictStr = Field(min_length=1, max_length=80)
    name: StrictStr = Field(min_length=1, max_length=200)
    province_code: StrictStr | None = Field(default=None, min_length=1, max_length=12)


class OpeningStartRegionOptionPageOut(_PreparationPage):
    items: tuple[OpeningStartRegionOptionOut, ...]
    next_after_id: UUID | None = None


class OpeningStartAssetOwnerOptionOut(_StrictOutput):
    owner_org_id: UUID
    code: StrictStr = Field(min_length=1, max_length=80)
    name: StrictStr = Field(min_length=1, max_length=200)


class OpeningStartAssetOwnerOptionPageOut(_PreparationPage):
    region_org_id: UUID
    items: tuple[OpeningStartAssetOwnerOptionOut, ...]
    next_after_id: UUID | None = None


class OpeningStartLocationOptionOut(_StrictOutput):
    location_id: UUID
    code: StrictStr = Field(min_length=1, max_length=100)
    name: StrictStr = Field(min_length=1, max_length=200)
    location_type: Literal["region", "personal"]
    physical_owner_org_id: UUID
    physical_owner_name: StrictStr = Field(min_length=1, max_length=200)
    custodian_person_id: UUID | None = None
    custodian_name: StrictStr | None = Field(default=None, min_length=1, max_length=120)


class OpeningStartLocationOptionPageOut(_PreparationPage):
    region_org_id: UUID
    owner_org_id: UUID
    items: tuple[OpeningStartLocationOptionOut, ...]
    next_after_id: UUID | None = None


class OpeningStartAssigneeOptionOut(_StrictOutput):
    assignee_user_id: StrictStr = Field(
        min_length=1, max_length=36, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$",
    )
    person_id: UUID
    name: StrictStr = Field(min_length=1, max_length=120)

    @field_validator("assignee_user_id")
    @classmethod
    def _assignee_user_id_is_accepted_by_start(cls, value: str) -> str:
        if not value.isascii() or any(ord(character) < 33 or ord(character) > 126 for character in value):
            raise ValueError("assignee_user_id must be printable ASCII without spaces")
        return value


class OpeningStartAssigneeOptionPageOut(_PreparationPage):
    region_org_id: UUID
    owner_org_id: UUID
    location_id: UUID
    items: tuple[OpeningStartAssigneeOptionOut, ...]
    next_after_person_id: UUID | None = None


__all__ = [
    "OpeningStartRegionOptionOut", "OpeningStartRegionOptionPageOut",
    "OpeningStartAssetOwnerOptionOut", "OpeningStartAssetOwnerOptionPageOut",
    "OpeningStartLocationOptionOut", "OpeningStartLocationOptionPageOut",
    "OpeningStartAssigneeOptionOut", "OpeningStartAssigneeOptionPageOut",
]
