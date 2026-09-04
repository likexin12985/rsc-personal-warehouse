"""Non-sensitive historical count acknowledgements, never write commands."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OpeningCountHistoricalCommandOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_id: UUID
    round_no: Annotated[int, Field(strict=True, ge=1)]
    completed_at: datetime
    scope_completed: Literal[True]
    caused_round_submission: bool = Field(strict=True)

    @field_validator("scope_completed", mode="before")
    @classmethod
    def strict_completed_fact(cls, value):
        # Literal[True] otherwise also accepts 1 / 1.0 by Python equality.
        if value is not True:
            raise ValueError("scope completion must be the boolean true")
        return value

    @field_validator("completion_id")
    @classmethod
    def nonzero_completion(cls, value):
        if value.int == 0:
            raise ValueError("completion coordinate must be nonzero")
        return value

    @field_validator("completed_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completion timestamp must be timezone aware")
        return value


class OpeningCountCommandStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    actor_person_id: UUID
    actor_authorization_version: Annotated[int, Field(strict=True, ge=1)]
    trace_request_id: Annotated[str, Field(strict=True, min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")]
    lookup_status: Literal["confirmed", "not_observed"]
    command: OpeningCountHistoricalCommandOut | None

    @field_validator("task_id", "round_id", "scope_id", "actor_person_id")
    @classmethod
    def nonzero_anchor(cls, value):
        if value.int == 0:
            raise ValueError("recovery coordinates must be nonzero")
        return value

    @model_validator(mode="after")
    def exact_status_shape(self):
        if (self.lookup_status == "confirmed") != (self.command is not None):
            raise ValueError("historical evidence does not match lookup status")
        return self
