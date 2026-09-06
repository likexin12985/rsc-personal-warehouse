"""Non-sensitive historical acknowledgements for review commands.

The response is a read-only recovery fact.  It deliberately omits the review
comment, per-item comments, raw idempotency key, request hash, audit hashes and
other payloads that are not needed to decide whether a lost POST completed.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StocktakeReviewHistoricalCommandOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: UUID
    task_id: UUID
    round_id: UUID
    review_stage: Literal["region", "headquarters"]
    decision: Literal["approve", "recount", "reject"]
    resulting_task_status: Literal["hq_review", "recount_required", "approved"]
    expected_task_version: int = Field(strict=True, ge=0)
    resulting_task_version: int = Field(strict=True, ge=1)
    task_version: int = Field(strict=True, ge=1)
    item_count: int = Field(strict=True, ge=0)
    pending_verification_count: int = Field(strict=True, ge=0)
    ready_for_posting: bool = Field(strict=True)
    reviewed_at: datetime

    @model_validator(mode="after")
    def validate_coordinates(self):
        if self.review_id.int == 0 or self.task_id.int == 0 or self.round_id.int == 0:
            raise ValueError("review coordinates must be nonzero")
        if self.resulting_task_version != self.expected_task_version + 1:
            raise ValueError("review version coordinates are not contiguous")
        if self.task_version != self.resulting_task_version:
            raise ValueError("review task version is not the resulting version")
        expected_ready = (
            self.review_stage == "headquarters"
            and self.decision == "approve"
            and self.pending_verification_count == 0
        )
        if self.ready_for_posting is not expected_ready:
            raise ValueError("review readiness does not match the persisted decision")
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("review timestamp must be timezone aware")
        return self


class StocktakeReviewCommandStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    round_id: UUID
    review_stage: Literal["region", "headquarters"]
    actor_person_id: UUID
    actor_authorization_version: int = Field(strict=True, ge=1)
    trace_request_id: str = Field(
        strict=True,
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    lookup_status: Literal["confirmed", "not_observed"]
    command: StocktakeReviewHistoricalCommandOut | None

    @model_validator(mode="after")
    def validate_status_shape(self):
        for value in (self.task_id, self.round_id, self.actor_person_id):
            if value.int == 0:
                raise ValueError("recovery coordinates must be nonzero")
        if (self.lookup_status == "confirmed") != (self.command is not None):
            raise ValueError("historical evidence does not match lookup status")
        if self.command is not None:
            if self.command.task_id != self.task_id or self.command.round_id != self.round_id:
                raise ValueError("historical command coordinates do not match lookup")
            if self.command.review_stage != self.review_stage:
                raise ValueError("historical review stage does not match lookup")
        return self


__all__ = [
    "StocktakeReviewCommandStatusOut",
    "StocktakeReviewHistoricalCommandOut",
]
