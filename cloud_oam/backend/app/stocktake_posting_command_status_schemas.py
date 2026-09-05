"""Read-only recovery contract for a non-opening difference-post command."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StocktakePostingHistoricalCommandOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_id: UUID
    task_id: UUID
    terminal_round_id: UUID
    resulting_task_status: Literal["posted"]
    task_version: int = Field(strict=True, ge=1)
    scope_count: int = Field(strict=True, ge=1)
    difference_count: int = Field(strict=True, ge=0)
    accepted_difference_count: int = Field(strict=True, ge=0)
    no_adjustment_count: int = Field(strict=True, ge=0)
    transaction_count: int = Field(strict=True, ge=0)
    movement_count: int = Field(strict=True, ge=0)
    total_quantity: str
    first_ledger_cursor: int | None = Field(default=None, strict=True, ge=1)
    last_ledger_cursor: int | None = Field(default=None, strict=True, ge=1)
    posted_at: datetime

    @model_validator(mode="after")
    def validate_totals(self):
        if self.accepted_difference_count + self.no_adjustment_count != self.difference_count:
            raise ValueError("posting decisions must cover every difference")
        if self.movement_count != self.accepted_difference_count:
            raise ValueError("every accepted difference must have one movement")
        if self.transaction_count == 0:
            if self.first_ledger_cursor is not None or self.last_ledger_cursor is not None or self.movement_count != 0 or self.total_quantity != "0.000":
                raise ValueError("zero-transaction posting has invalid ledger totals")
        elif self.first_ledger_cursor is None or self.last_ledger_cursor is None or self.last_ledger_cursor - self.first_ledger_cursor + 1 != self.transaction_count:
            raise ValueError("posting ledger cursor range is invalid")
        return self


class StocktakePostingCommandStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    actor_person_id: UUID
    actor_authorization_version: int = Field(strict=True, ge=1)
    trace_request_id: str = Field(strict=True, min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    operation: Literal["post_differences"]
    lookup_status: Literal["confirmed", "not_observed"]
    command: StocktakePostingHistoricalCommandOut | None

    @model_validator(mode="after")
    def exact_status_shape(self):
        if (self.lookup_status == "confirmed") != (self.command is not None):
            raise ValueError("historical evidence does not match lookup status")
        return self

