"""Permission-minimal read contracts for formal non-opening stocktakes.

The contracts intentionally keep count, difference evaluation, regional
review, headquarters review, recount and inventory posting as separate facts.
Authorization hashes, permission catalogs, raw mobile data and audit-chain
internals are not part of this public surface.  Blind-count snapshot fields
are represented by absence, never by a fabricated zero.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictStr,
    model_validator,
)


QuantityOut = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=3),
    PlainSerializer(lambda value: f"{value:.3f}", return_type=str, when_used="json"),
]
SignedQuantityOut = Annotated[
    Decimal,
    Field(max_digits=18, decimal_places=3),
    PlainSerializer(lambda value: f"{value:.3f}", return_type=str, when_used="json"),
]

StocktakeTaskType = Literal["full", "sample", "ad_hoc", "personal", "termination"]
StocktakeTaskStatus = Literal[
    "draft",
    "issued",
    "frozen",
    "counting",
    "submitted",
    "region_review",
    "hq_review",
    "approved",
    "recount_required",
    "posted",
    "closed",
    "cancelled",
]
StocktakeAllowedAction = Literal[
    "start",
    "submit_initial_count",
    "generate_initial_differences",
    "review_region",
    "review_headquarters",
    "open_recount",
    "submit_recount_count",
    "generate_recount_differences",
    "post",
    "reconcile",
    "close",
]


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StocktakeFreezeFactOut(_StrictOutputModel):
    freeze_id: UUID
    freeze_mode: Literal["hard", "cutoff_replay"]
    status: Literal["active", "released", "cancelled"]
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    version: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_status_time(self):
        if (self.status == "active") != (self.valid_to is None):
            raise ValueError("freeze status and validity disagree")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("freeze end must follow freeze start")
        return self


class StocktakeSnapshotAccountOut(_StrictOutputModel):
    stock_account_id: UUID
    material_id: UUID
    condition_code: Literal["new", "used", "damaged", "scrapped"]
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
    ]
    lot_id: UUID | None
    book_qty: QuantityOut
    expected_serial_ids: tuple[UUID, ...]


class StocktakeScopeOut(_StrictOutputModel):
    scope_id: UUID
    scope_no: int = Field(ge=1)
    scope_mode: Literal["location_all", "filtered"]
    owner_org_id: UUID
    location_id: UUID
    custodian_person_id_snapshot: UUID | None
    material_id: UUID | None
    condition_code: Literal["new", "used", "damaged", "scrapped"] | None
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
    ] | None
    assigned_to_me: bool
    freeze: StocktakeFreezeFactOut | None
    snapshot_visibility: Literal["not_started", "hidden", "visible"]
    snapshot_accounts: tuple[StocktakeSnapshotAccountOut, ...]
    allowed_actions: tuple[
        Literal["submit_initial_count", "submit_recount_count"], ...
    ]

    @model_validator(mode="after")
    def validate_snapshot_visibility(self):
        if self.snapshot_visibility != "visible" and self.snapshot_accounts:
            raise ValueError("hidden snapshot cannot contain account facts")
        return self


class StocktakeScopeCountCompletionOut(_StrictOutputModel):
    completion_id: UUID
    scope_id: UUID
    # NULL is retained only for read compatibility with pre-0033 rows.  It is
    # never sufficient evidence for a downstream difference action.
    count_ledger_cursor: int | None = Field(default=None, ge=0)
    count_line_count: int = Field(ge=0)
    observation_line_count: int = Field(ge=0)
    serial_count: int = Field(ge=0)
    total_counted_qty: QuantityOut
    zero_confirmed: bool
    completed_by_person_id: UUID
    completed_at: AwareDatetime


class StocktakeCountLineOut(_StrictOutputModel):
    count_line_id: UUID
    scope_id: UUID
    stock_account_id: UUID
    material_id: UUID
    counted_qty: QuantityOut
    count_method: Literal["scan", "manual", "import"]
    reason_code: StrictStr | None
    remark: StrictStr
    counted_by_me: bool
    counted_at: AwareDatetime
    counted_serial_ids: tuple[UUID, ...]
    book_qty: QuantityOut | None
    expected_serial_ids: tuple[UUID, ...] | None

    @model_validator(mode="after")
    def validate_book_evidence_pair(self):
        if (self.book_qty is None) != (self.expected_serial_ids is None):
            raise ValueError("book quantity and expected serials must hide together")
        return self


class StocktakeObservationDispositionOut(_StrictOutputModel):
    disposition_id: UUID
    disposition: Literal[
        "resolved_existing_master",
        "pending_verification",
        "requires_recount",
    ]
    resolved_material_id: UUID | None
    resolved_lot_id: UUID | None
    resolved_serial_id: UUID | None
    reason_code: StrictStr
    comment: StrictStr
    decided_at: AwareDatetime


class StocktakeObservationOut(_StrictOutputModel):
    observation_id: UUID
    scope_id: UUID
    observation_no: int = Field(ge=1)
    owner_org_id: UUID
    location_id: UUID
    custodian_person_id_snapshot: UUID | None
    material_id: UUID | None
    material_identifier_raw: StrictStr
    material_identifier_type: Literal[
        "sku_code", "qr_code", "external_code", "unknown"
    ]
    condition_code: Literal["new", "used", "damaged", "scrapped"]
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
    ]
    lot_id: UUID | None
    lot_no_raw: StrictStr | None
    serial_id: UUID | None
    serial_no_raw: StrictStr | None
    serial_identifier_type: Literal["serial_no", "qr_code", "unknown"] | None
    counted_qty: QuantityOut
    verification_status: Literal["verified", "pending_verification"]
    requires_verification: bool
    count_method: Literal["scan", "manual", "import"]
    reason_code: StrictStr | None
    remark: StrictStr
    counted_by_me: bool
    counted_at: AwareDatetime
    disposition: StocktakeObservationDispositionOut | None

    @model_validator(mode="after")
    def validate_pending_flag(self):
        if self.requires_verification != (
            self.verification_status == "pending_verification"
        ):
            raise ValueError("observation verification flag disagrees with status")
        return self


class StocktakeDifferenceOut(_StrictOutputModel):
    difference_id: UUID
    scope_id: UUID
    difference_no: int = Field(ge=1)
    difference_type: Literal[
        "missing",
        "excess",
        "wrong_location",
        "wrong_condition",
        "wrong_lot",
        "wrong_serial",
    ]
    material_id: UUID | None
    expected_account_id: UUID | None
    observed_account_id: UUID | None
    observed_line_id: UUID | None
    serial_id: UUID | None
    book_qty: QuantityOut
    counted_qty: QuantityOut
    difference_qty: SignedQuantityOut
    affected_qty: QuantityOut
    reason_code: StrictStr | None
    reason_text: StrictStr
    evidence_required: bool
    posting_blocked_by_pending_verification: bool


class StocktakeDifferenceCompletionOut(_StrictOutputModel):
    completion_id: UUID
    completed_at: AwareDatetime
    visible_difference_count: int = Field(ge=0)
    visible_pending_verification_count: int = Field(ge=0)
    visible_total_affected_qty: QuantityOut
    covers_all_task_scopes: bool

    @model_validator(mode="after")
    def validate_pending_total(self):
        if self.visible_pending_verification_count > self.visible_difference_count:
            raise ValueError("pending differences exceed visible differences")
        return self


class StocktakeRoundSubmissionOut(_StrictOutputModel):
    submission_id: UUID
    submitted_at: AwareDatetime
    visible_scope_count: int = Field(ge=0)
    visible_zero_scope_count: int = Field(ge=0)
    visible_count_line_count: int = Field(ge=0)
    visible_observation_line_count: int = Field(ge=0)
    visible_serial_count: int = Field(ge=0)
    visible_total_counted_qty: QuantityOut
    covers_all_task_scopes: bool

    @model_validator(mode="after")
    def validate_scope_totals(self):
        if self.visible_zero_scope_count > self.visible_scope_count:
            raise ValueError("zero-count scopes exceed visible scopes")
        return self


class StocktakeReviewItemOut(_StrictOutputModel):
    difference_id: UUID
    decision: Literal[
        "accept_for_posting",
        "pending_verification",
        "no_adjustment",
        "recount",
        "reject",
    ]
    comment: StrictStr


class StocktakeReviewFactOut(_StrictOutputModel):
    review_id: UUID
    review_stage: Literal["region", "headquarters"]
    decision: Literal["approve", "recount", "reject"]
    expected_task_version: int = Field(ge=0)
    resulting_task_version: int = Field(ge=1)
    comment: StrictStr | None
    comment_visible: bool
    reviewer_person_id: UUID
    reviewed_at: AwareDatetime
    visible_items: tuple[StocktakeReviewItemOut, ...]
    covers_all_task_scopes: bool

    @model_validator(mode="after")
    def validate_comment_visibility(self):
        if self.comment_visible != (self.comment is not None):
            raise ValueError("review comment visibility disagrees with value")
        return self


class StocktakeRecountAssignmentOut(_StrictOutputModel):
    assignment_id: UUID
    scope_id: UUID
    assignee_person_id: UUID
    assigned_to_me: bool
    assigned_at: AwareDatetime


class StocktakeRecountCauseOut(_StrictOutputModel):
    recount_case_id: UUID
    source_round_id: UUID
    source_difference_completion_id: UUID
    trigger_review_id: UUID
    next_round_no: int = Field(ge=2)
    visible_scope_count: int = Field(ge=0)
    covers_all_task_scopes: bool
    reason: StrictStr | None
    reason_visible: bool
    opened_by_person_id: UUID
    opened_at: AwareDatetime
    assignments: tuple[StocktakeRecountAssignmentOut, ...]

    @model_validator(mode="after")
    def validate_reason_visibility(self):
        if self.reason_visible != (self.reason is not None):
            raise ValueError("recount reason visibility disagrees with value")
        return self


class StocktakePostingFactOut(_StrictOutputModel):
    """Aggregate immutable posting facts without claiming workflow completion."""

    status: Literal["not_posted", "recorded"]
    posting_ids: tuple[UUID, ...]
    posting_fact_count: int = Field(ge=0)
    visible_total_quantity: QuantityOut
    covers_all_task_scopes: bool
    inventory_transaction_count: int = Field(ge=0)
    first_posted_at: AwareDatetime | None
    last_posted_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_posting_fact(self):
        missing = self.status == "not_posted"
        if (self.first_posted_at is None) != (self.last_posted_at is None):
            raise ValueError("posting timestamps must hide together")
        if self.posting_fact_count != len(self.posting_ids):
            raise ValueError("posting fact count disagrees with identifiers")
        if missing != (self.posting_fact_count == 0):
            raise ValueError("posting status and immutable facts disagree")
        if missing != (self.first_posted_at is None and self.last_posted_at is None):
            raise ValueError("posting status and time boundary disagree")
        if missing and (
            self.visible_total_quantity != 0 or self.inventory_transaction_count != 0
        ):
            raise ValueError("missing posting cannot expose movement facts")
        if self.inventory_transaction_count > self.posting_fact_count:
            raise ValueError("inventory transactions exceed posting facts")
        if (
            self.first_posted_at is not None
            and self.last_posted_at is not None
            and self.last_posted_at < self.first_posted_at
        ):
            raise ValueError("posting time boundary is reversed")
        return self


class StocktakeRoundOut(_StrictOutputModel):
    round_id: UUID
    round_no: int = Field(ge=1)
    round_type: Literal["initial", "recount"]
    status: Literal["counting", "submitted", "superseded"]
    started_at: AwareDatetime
    submitted_at: AwareDatetime | None
    submission: StocktakeRoundSubmissionOut | None
    visible_scope_completions: tuple[StocktakeScopeCountCompletionOut, ...]
    visible_count_lines: tuple[StocktakeCountLineOut, ...]
    visible_observations: tuple[StocktakeObservationOut, ...]
    differences_visible: bool
    difference_completion: StocktakeDifferenceCompletionOut | None
    visible_differences: tuple[StocktakeDifferenceOut, ...]
    region_review: StocktakeReviewFactOut | None
    headquarters_review: StocktakeReviewFactOut | None
    recount_cause: StocktakeRecountCauseOut | None
    posting: StocktakePostingFactOut
    allowed_actions: tuple[
        Literal[
            "generate_initial_differences",
            "review_region",
            "review_headquarters",
            "open_recount",
            "generate_recount_differences",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def validate_visibility_and_submission(self):
        submitted = self.status in {"submitted", "superseded"}
        if submitted != (self.submitted_at is not None):
            raise ValueError("round status and submitted time disagree")
        if not self.differences_visible and any(
            value is not None
            for value in (
                self.difference_completion,
                self.region_review,
                self.headquarters_review,
            )
        ):
            raise ValueError("blind-hidden round cannot expose difference review facts")
        if not self.differences_visible and self.visible_differences:
            raise ValueError("blind-hidden round cannot expose differences")
        return self


class StocktakeStateAxesOut(_StrictOutputModel):
    count_status: Literal["not_started", "counting", "submitted"]
    difference_status: Literal[
        "not_ready",
        "not_evaluated",
        "evaluated",
        "hidden_for_blind_counter",
    ]
    region_review_status: Literal[
        "not_ready", "pending", "approve", "recount", "reject"
    ]
    headquarters_review_status: Literal[
        "not_ready", "pending", "approve", "recount", "reject"
    ]
    recount_status: Literal["not_required", "required", "counting", "submitted"]
    posting_status: Literal["not_posted", "recorded"]
    reconciliation_status: Literal["not_reconciled", "recorded", "stale"]
    closure_status: Literal["open", "closed"]


class StocktakeLatestCloseReconciliationOut(_StrictOutputModel):
    completion_id: UUID
    reconciliation_no: int = Field(ge=1)
    reconciliation_ledger_cursor: int = Field(ge=0)
    reconciled_task_version: int = Field(ge=1)
    reconciled_at: AwareDatetime


class StocktakeCloseCompletionOut(_StrictOutputModel):
    completion_id: UUID
    reconciliation_completion_id: UUID
    closed_task_version: int = Field(ge=1)
    closed_at: AwareDatetime


class StocktakeCloseControlOut(_StrictOutputModel):
    latest_reconciliation: StocktakeLatestCloseReconciliationOut | None
    close_completion: StocktakeCloseCompletionOut | None

    @model_validator(mode="after")
    def validate_close_binding(self):
        if self.close_completion is not None and (
            self.latest_reconciliation is None
            or self.close_completion.reconciliation_completion_id
            != self.latest_reconciliation.completion_id
            or self.close_completion.closed_task_version
            != self.latest_reconciliation.reconciled_task_version + 1
        ):
            raise ValueError("close completion must bind the latest reconciliation")
        return self


class StocktakeTaskSummaryOut(_StrictOutputModel):
    task_id: UUID
    task_no: StrictStr = Field(min_length=1, max_length=100)
    task_type: StocktakeTaskType
    region_org_id: UUID
    status: StocktakeTaskStatus
    version: int = Field(ge=0)
    blind_count: bool
    current_round_no: int = Field(ge=0)
    current_round_status: Literal["counting", "submitted", "superseded"] | None
    cutoff_ledger_cursor: int | None = Field(default=None, ge=0)
    cutoff_at: AwareDatetime | None
    visible_scope_count: int = Field(ge=1)
    current_round_visible_completed_scope_count: int = Field(ge=0)
    freeze_status: Literal["not_started", "active", "released", "cancelled", "mixed"]
    state_axes: StocktakeStateAxesOut
    deadline: AwareDatetime | None
    allowed_actions: tuple[StocktakeAllowedAction, ...]

    @model_validator(mode="after")
    def validate_cutoff_pair_and_progress(self):
        if (self.cutoff_ledger_cursor is None) != (self.cutoff_at is None):
            raise ValueError("task cutoff cursor and time must be paired")
        if self.current_round_visible_completed_scope_count > self.visible_scope_count:
            raise ValueError("completed visible scopes exceed visible scopes")
        if self.status == "closed":
            if (
                self.state_axes.posting_status != "recorded"
                or
                self.state_axes.reconciliation_status != "recorded"
                or self.state_axes.closure_status != "closed"
                or self.allowed_actions
            ):
                raise ValueError("closed task summary must expose a terminal reconciliation axis")
        elif self.status == "posted":
            if (
                self.state_axes.posting_status != "recorded"
                or self.state_axes.closure_status != "open"
                or any(
                    action not in {"reconcile", "close"}
                    for action in self.allowed_actions
                )
            ):
                raise ValueError("posted task summary cannot be closed")
        elif (
            self.state_axes.posting_status != "not_posted"
            or self.state_axes.reconciliation_status != "not_reconciled"
            or self.state_axes.closure_status != "open"
            or any(action in {"reconcile", "close"} for action in self.allowed_actions)
        ):
            raise ValueError("non-terminal task summary cannot expose close facts")
        if "close" in self.allowed_actions and (
            self.status != "posted"
            or self.state_axes.reconciliation_status != "recorded"
        ):
            raise ValueError("close action requires a current reconciliation")
        if "reconcile" in self.allowed_actions and self.status != "posted":
            raise ValueError("reconcile action requires a posted task")
        return self


class StocktakeTaskPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[StocktakeTaskSummaryOut, ...]
    next_after_id: UUID | None


class StocktakeTaskDetailOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    task_no: StrictStr = Field(min_length=1, max_length=100)
    task_type: StocktakeTaskType
    region_org_id: UUID
    status: StocktakeTaskStatus
    version: int = Field(ge=0)
    blind_count: bool
    current_round_no: int = Field(ge=0)
    cutoff_ledger_cursor: int | None = Field(default=None, ge=0)
    cutoff_at: AwareDatetime | None
    issued_at: AwareDatetime | None
    frozen_at: AwareDatetime | None
    submitted_at: AwareDatetime | None
    posted_at: AwareDatetime | None
    closed_at: AwareDatetime | None
    cancelled_at: AwareDatetime | None
    deadline: AwareDatetime | None
    note: StrictStr
    state_axes: StocktakeStateAxesOut
    close_control: StocktakeCloseControlOut
    scopes: tuple[StocktakeScopeOut, ...]
    rounds: tuple[StocktakeRoundOut, ...]
    allowed_actions: tuple[StocktakeAllowedAction, ...]

    @model_validator(mode="after")
    def validate_task_projection(self):
        if (self.cutoff_ledger_cursor is None) != (self.cutoff_at is None):
            raise ValueError("task cutoff cursor and time must be paired")
        if not self.scopes:
            raise ValueError("a visible task requires at least one visible scope")
        if self.current_round_no == 0 and self.rounds:
            raise ValueError("draft task cannot expose rounds")
        latest = self.close_control.latest_reconciliation
        close = self.close_control.close_completion
        reconciliation_status = self.state_axes.reconciliation_status
        closure_status = self.state_axes.closure_status
        if (self.status in {"posted", "closed"}) != (self.posted_at is not None):
            raise ValueError("task posting status and timestamp disagree")
        if latest is not None and (
            self.posted_at is None or latest.reconciled_at <= self.posted_at
        ):
            raise ValueError("reconciliation must follow posting")
        if close is not None and (
            latest is None or close.closed_at <= latest.reconciled_at
        ):
            raise ValueError("close must follow reconciliation")
        if self.status == "closed":
            if (
                latest is None
                or close is None
                or self.state_axes.posting_status != "recorded"
                or reconciliation_status != "recorded"
                or closure_status != "closed"
                or self.allowed_actions
                or any(scope.allowed_actions for scope in self.scopes)
                or any(round_row.allowed_actions for round_row in self.rounds)
                or latest.reconciled_task_version != self.version - 1
                or close.closed_task_version != self.version
                or close.closed_at != self.closed_at
            ):
                raise ValueError("closed task must expose one bound terminal close fact")
        elif self.status == "posted":
            if (
                self.state_axes.posting_status != "recorded"
                or close is not None
                or closure_status != "open"
                or self.closed_at is not None
                or any(scope.allowed_actions for scope in self.scopes)
                or any(round_row.allowed_actions for round_row in self.rounds)
                or any(
                    action not in {"reconcile", "close"}
                    for action in self.allowed_actions
                )
            ):
                raise ValueError("posted task cannot expose a close completion")
            if latest is None:
                if reconciliation_status != "not_reconciled":
                    raise ValueError("missing reconciliation fact requires not_reconciled")
            elif (
                reconciliation_status == "not_reconciled"
                or latest.reconciled_task_version > self.version
                or (
                    reconciliation_status == "recorded"
                    and latest.reconciled_task_version != self.version
                )
            ):
                raise ValueError("posted reconciliation axis disagrees with its fact")
        elif (
            self.state_axes.posting_status != "not_posted"
            or latest is not None
            or close is not None
            or reconciliation_status != "not_reconciled"
            or closure_status != "open"
            or any(action in {"reconcile", "close"} for action in self.allowed_actions)
        ):
            raise ValueError("non-terminal task cannot expose reconciliation or close facts")
        if "close" in self.allowed_actions and (
            self.status != "posted"
            or reconciliation_status != "recorded"
            or latest is None
            or latest.reconciled_task_version != self.version
        ):
            raise ValueError("close action requires the current reconciliation fact")
        if "reconcile" in self.allowed_actions and self.status != "posted":
            raise ValueError("reconcile action requires a posted task")
        return self


__all__ = [
    "SignedQuantityOut",
    "StocktakeAllowedAction",
    "StocktakeCloseCompletionOut",
    "StocktakeCloseControlOut",
    "StocktakeCountLineOut",
    "StocktakeDifferenceCompletionOut",
    "StocktakeDifferenceOut",
    "StocktakeFreezeFactOut",
    "StocktakeLatestCloseReconciliationOut",
    "StocktakeObservationDispositionOut",
    "StocktakeObservationOut",
    "StocktakePostingFactOut",
    "StocktakeRecountAssignmentOut",
    "StocktakeRecountCauseOut",
    "StocktakeReviewFactOut",
    "StocktakeReviewItemOut",
    "StocktakeRoundOut",
    "StocktakeRoundSubmissionOut",
    "StocktakeScopeCountCompletionOut",
    "StocktakeScopeOut",
    "StocktakeSnapshotAccountOut",
    "StocktakeStateAxesOut",
    "StocktakeTaskDetailOut",
    "StocktakeTaskPageOut",
    "StocktakeTaskStatus",
    "StocktakeTaskSummaryOut",
    "StocktakeTaskType",
]
