"""Strict HTTP contracts for formal non-opening stocktake commands.

The client may submit physical facts and an expected task version only. Actor
identity, ledger cursors, workflow status, authorization evidence and every
manifest/hash remain server-owned.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated


_ZERO_UUID = UUID(int=0)
_DECIMAL_TEXT = re.compile(r"^(0|[1-9]\d*)(?:\.\d{1,3})?$")
_MAX_QUANTITY = Decimal("1000000000000000")

QuantityOut = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=3),
    PlainSerializer(lambda value: f"{value:.3f}", return_type=str, when_used="json"),
]


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_uuid(value: UUID, field_name: str) -> UUID:
    if value == _ZERO_UUID:
        raise ValueError(f"{field_name} cannot be the zero UUID")
    return value


def _quantity_text(value: object, *, field_name: str, positive: bool) -> Decimal:
    # JSON numbers can already have lost decimal precision. Requiring the wire
    # value to be a canonical string keeps the service request HMAC exact.
    if not isinstance(value, str) or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a non-exponent decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:  # defensive; regex excludes this normally
        raise ValueError(f"{field_name} is invalid") from exc
    if (
        not parsed.is_finite()
        or parsed >= _MAX_QUANTITY
        or (positive and parsed <= 0)
        or (not positive and parsed < 0)
    ):
        raise ValueError(f"{field_name} is outside the allowed range")
    return parsed


def _trimmed(value: str | None, field_name: str) -> str | None:
    if value is not None and value != value.strip():
        raise ValueError(f"{field_name} cannot have surrounding whitespace")
    return value


class StocktakeSnapshotCountIn(_StrictRequestModel):
    stock_account_id: UUID
    counted_qty: Decimal
    count_method: Literal["scan", "manual", "import"] = "manual"
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=10000)
    book_qty_confirmation: Decimal | None = None
    reason_code: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    remark: StrictStr = Field(default="", max_length=10000)

    @field_validator("stock_account_id")
    @classmethod
    def validate_account_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "stock_account_id")

    @field_validator("serial_ids")
    @classmethod
    def validate_serial_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        checked = tuple(_required_uuid(row, "serial_id") for row in value)
        if len(checked) != len(set(checked)):
            raise ValueError("serial_ids cannot contain duplicates")
        return checked

    @field_validator("counted_qty", mode="before")
    @classmethod
    def validate_counted_qty(cls, value: object) -> Decimal:
        return _quantity_text(value, field_name="counted_qty", positive=False)

    @field_validator("book_qty_confirmation", mode="before")
    @classmethod
    def validate_book_qty_confirmation(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        return _quantity_text(
            value,
            field_name="book_qty_confirmation",
            positive=False,
        )

    @field_validator("reason_code", "remark")
    @classmethod
    def validate_text(cls, value: str | None, info) -> str | None:
        return _trimmed(value, info.field_name)


class StocktakePhysicalObservationIn(_StrictRequestModel):
    material_id: UUID | None = None
    material_identifier_raw: StrictStr = Field(min_length=1, max_length=300)
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
    counted_qty: Decimal
    lot_id: UUID | None = None
    lot_no_raw: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    serial_id: UUID | None = None
    serial_no_raw: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    serial_identifier_type: Literal["serial_no", "qr_code", "unknown"] | None = None
    count_method: Literal["scan", "manual", "import"] = "manual"
    reason_code: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    remark: StrictStr = Field(default="", max_length=10000)

    @field_validator("material_id", "lot_id", "serial_id")
    @classmethod
    def validate_ids(cls, value: UUID | None, info) -> UUID | None:
        return None if value is None else _required_uuid(value, info.field_name)

    @field_validator("counted_qty", mode="before")
    @classmethod
    def validate_counted_qty(cls, value: object) -> Decimal:
        return _quantity_text(value, field_name="counted_qty", positive=True)

    @field_validator(
        "material_identifier_raw",
        "lot_no_raw",
        "serial_no_raw",
        "reason_code",
        "remark",
    )
    @classmethod
    def validate_text(cls, value: str | None, info) -> str | None:
        return _trimmed(value, info.field_name)

    @model_validator(mode="after")
    def validate_dimension_bindings(self):
        if (self.serial_no_raw is None) != (self.serial_identifier_type is None):
            raise ValueError(
                "serial_no_raw and serial_identifier_type must be paired"
            )
        if self.serial_id is not None and self.serial_no_raw is None:
            raise ValueError("serial_id requires serial_no_raw")
        if self.lot_id is not None and self.lot_no_raw is None:
            raise ValueError("lot_id requires lot_no_raw")
        return self


class StocktakeInitialScopeCountIn(_StrictRequestModel):
    count_mode: Literal["blind", "open"]
    account_counts: tuple[StocktakeSnapshotCountIn, ...] = Field(
        default=(), max_length=10000
    )
    physical_observations: tuple[StocktakePhysicalObservationIn, ...] = Field(
        default=(), max_length=10000
    )
    evidence_file_ids: tuple[UUID, ...] = Field(default=(), max_length=50)
    zero_confirmed: StrictBool = False

    @field_validator("evidence_file_ids")
    @classmethod
    def validate_evidence_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        checked = tuple(_required_uuid(row, "evidence_file_id") for row in value)
        if len(checked) != len(set(checked)):
            raise ValueError("evidence_file_ids cannot contain duplicates")
        return checked

    @model_validator(mode="after")
    def validate_count_shape(self):
        account_ids = [row.stock_account_id for row in self.account_counts]
        if len(account_ids) != len(set(account_ids)):
            raise ValueError("account_counts cannot contain duplicate accounts")
        serial_ids = [
            serial_id
            for row in self.account_counts
            for serial_id in row.serial_ids
        ]
        serial_ids.extend(
            row.serial_id
            for row in self.physical_observations
            if row.serial_id is not None
        )
        if len(serial_ids) != len(set(serial_ids)):
            raise ValueError("one serial cannot appear more than once")
        if self.count_mode == "blind" and any(
            row.book_qty_confirmation is not None for row in self.account_counts
        ):
            raise ValueError("blind count cannot echo book quantities")
        if self.count_mode == "open" and any(
            row.book_qty_confirmation is None for row in self.account_counts
        ):
            raise ValueError("open count must confirm every book quantity")
        if self.zero_confirmed and (self.account_counts or self.physical_observations):
            raise ValueError("zero confirmation cannot be combined with count lines")
        return self


class StocktakeDifferenceGenerateIn(_StrictRequestModel):
    expected_task_version: int = Field(ge=0)


class StocktakeRecountScopeAssignmentIn(_StrictRequestModel):
    scope_id: UUID
    assignee_user_id: StrictStr = Field(min_length=1, max_length=160)

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "scope_id")

    @field_validator("assignee_user_id")
    @classmethod
    def validate_assignee_user_id(cls, value: str) -> str:
        return _trimmed(value, "assignee_user_id") or ""


class StocktakeRecountOpenIn(_StrictRequestModel):
    expected_task_version: int = Field(ge=0)
    assignments: tuple[StocktakeRecountScopeAssignmentIn, ...] = Field(
        min_length=1,
        max_length=10000,
    )
    reason: StrictStr = Field(min_length=1, max_length=10000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _trimmed(value, "reason") or ""

    @model_validator(mode="after")
    def validate_assignment_uniqueness(self):
        scope_ids = [row.scope_id for row in self.assignments]
        if len(scope_ids) != len(set(scope_ids)):
            raise ValueError("assignments cannot contain duplicate scope_id")
        return self


class StocktakeRecountScopeCountIn(StocktakeInitialScopeCountIn):
    """Physical facts for one immutable, selected recount scope."""


class StocktakeReviewItemIn(_StrictRequestModel):
    difference_id: UUID
    decision: Literal[
        "accept_for_posting",
        "pending_verification",
        "no_adjustment",
        "recount",
        "reject",
    ]
    comment: StrictStr = Field(default="", max_length=10000)

    @field_validator("difference_id")
    @classmethod
    def validate_difference_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "difference_id")

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        return _trimmed(value, "comment") or ""


class StocktakeReviewIn(_StrictRequestModel):
    expected_task_version: int = Field(ge=0)
    decision: Literal["approve", "recount", "reject"]
    items: tuple[StocktakeReviewItemIn, ...] = Field(default=(), max_length=10000)
    comment: StrictStr = Field(default="", max_length=10000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        return _trimmed(value, "comment") or ""

    @model_validator(mode="after")
    def validate_review_shape(self):
        difference_ids = [row.difference_id for row in self.items]
        if len(difference_ids) != len(set(difference_ids)):
            raise ValueError("items cannot contain duplicate difference_id")
        if self.decision != "approve" and not self.comment:
            raise ValueError("recount or reject requires a comment")
        return self


class StocktakeDifferencePostIn(_StrictRequestModel):
    """Only the caller-observed approved task version crosses the wire."""

    expected_task_version: StrictInt = Field(ge=0)


class StocktakeTerminalIn(_StrictRequestModel):
    """Optimistic coordinate shared by independent reconcile/close commands."""

    expected_task_version: StrictInt = Field(ge=0)


class StocktakeInitialScopeCountOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    task_status: StrictStr
    round_status: Literal["counting", "submitted", "superseded"]
    task_version: int = Field(ge=0)
    scope_completed: bool
    round_submitted: bool
    evidence_file_count: int = Field(ge=0, le=50)
    replayed: bool


class StocktakeDifferenceGenerateOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    round_id: UUID
    completion_id: UUID
    task_status: StrictStr
    round_status: Literal["submitted", "superseded"]
    task_version: int = Field(ge=0)
    difference_status: Literal["evaluated"] = "evaluated"
    difference_count: int = Field(ge=0)
    physical_difference_count: int = Field(ge=0)
    pending_observation_difference_count: int = Field(ge=0)
    total_affected_qty: QuantityOut
    difference_manifest_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    replayed: bool


class StocktakeRecountOpenOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    recount_case_id: UUID
    task_id: UUID
    source_round_id: UUID
    next_round_id: UUID
    next_round_no: int = Field(ge=2)
    scope_count: int = Field(ge=1)
    assignment_count: int = Field(ge=1)
    resulting_task_status: Literal["counting"]
    task_version: int = Field(ge=0)
    replayed: bool


class StocktakeRecountScopeCountOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    recount_case_id: UUID
    task_status: StrictStr
    round_status: Literal["counting", "submitted"]
    task_version: int = Field(ge=0)
    scope_completed: bool
    round_submitted: bool
    count_ledger_cursor: int = Field(ge=0)
    evidence_file_count: int = Field(ge=0, le=50)
    replayed: bool


class StocktakeRecountDifferenceOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    round_id: UUID
    recount_case_id: UUID
    completion_id: UUID
    task_status: StrictStr
    round_status: Literal["submitted"]
    task_version: int = Field(ge=0)
    difference_status: Literal["evaluated"] = "evaluated"
    difference_count: int = Field(ge=0)
    physical_difference_count: int = Field(ge=0)
    pending_observation_difference_count: int = Field(ge=0)
    total_affected_qty: QuantityOut
    difference_manifest_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    selected_scope_count: int = Field(ge=1)
    replayed: bool


class StocktakeReviewOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    review_id: UUID
    task_id: UUID
    round_id: UUID
    review_stage: Literal["region", "headquarters"]
    decision: Literal["approve", "recount", "reject"]
    resulting_task_status: StrictStr
    task_version: int = Field(ge=0)
    item_count: int = Field(ge=0)
    pending_verification_count: int = Field(ge=0)
    ready_for_posting: bool
    replayed: bool


class StocktakeDifferencePostOut(_StrictOutputModel):
    """Independent ``approved -> posted`` completion; never a close result."""

    schema_version: Literal["1.0"] = "1.0"
    completion_id: UUID
    task_id: UUID
    terminal_round_id: UUID
    resulting_task_status: Literal["posted"]
    task_version: int = Field(ge=1)
    scope_count: int = Field(ge=1)
    difference_count: int = Field(ge=0)
    accepted_difference_count: int = Field(ge=0)
    no_adjustment_count: int = Field(ge=0)
    transaction_count: int = Field(ge=0)
    movement_count: int = Field(ge=0)
    total_quantity: QuantityOut
    first_ledger_cursor: int | None = Field(default=None, ge=1)
    last_ledger_cursor: int | None = Field(default=None, ge=1)
    replayed: bool

    @model_validator(mode="after")
    def validate_posting_totals(self):
        if (
            self.accepted_difference_count + self.no_adjustment_count
            != self.difference_count
        ):
            raise ValueError("posting decisions must cover every difference")
        if self.movement_count != self.accepted_difference_count:
            raise ValueError("every accepted difference must have one movement")
        if self.transaction_count == 0:
            if (
                self.first_ledger_cursor is not None
                or self.last_ledger_cursor is not None
                or self.movement_count != 0
                or self.total_quantity != 0
            ):
                raise ValueError("zero-transaction posting has invalid ledger totals")
        elif (
            self.first_ledger_cursor is None
            or self.last_ledger_cursor is None
            or self.last_ledger_cursor - self.first_ledger_cursor + 1
            != self.transaction_count
            or self.movement_count <= 0
            or self.total_quantity <= 0
        ):
            raise ValueError("posting ledger cursor range is invalid")
        return self


class StocktakeCloseReconciliationOut(_StrictOutputModel):
    """Internal local proof only; this response never claims task closure."""

    schema_version: Literal["1.0"] = "1.0"
    completion_id: UUID
    task_id: UUID
    posting_completion_id: UUID
    reconciliation_no: int = Field(ge=1)
    reconciliation_ledger_cursor: int = Field(ge=0)
    resulting_task_status: Literal["posted"]
    task_version: int = Field(ge=1)
    scope_count: int = Field(ge=1)
    account_count: int = Field(ge=0)
    scoped_account_count: int = Field(ge=0)
    serial_count: int = Field(ge=0)
    transaction_count: int = Field(ge=0)
    movement_count: int = Field(ge=0)
    book_total_qty: QuantityOut
    physical_total_qty: QuantityOut
    reconciled_at: AwareDatetime
    replayed: bool

    @model_validator(mode="after")
    def validate_reconciliation_totals(self):
        if self.scoped_account_count > self.account_count:
            raise ValueError("scoped accounts exceed reconciliation accounts")
        if self.book_total_qty != self.physical_total_qty:
            raise ValueError("book and physical totals must reconcile")
        return self


class StocktakeCloseOut(_StrictOutputModel):
    """Independent ``posted -> closed`` completion."""

    schema_version: Literal["1.0"] = "1.0"
    completion_id: UUID
    task_id: UUID
    reconciliation_completion_id: UUID
    reconciliation_no: int = Field(ge=1)
    reconciliation_ledger_cursor: int = Field(ge=0)
    resulting_task_status: Literal["closed"]
    task_version: int = Field(ge=1)
    closed_at: AwareDatetime
    replayed: bool


__all__ = [
    "StocktakeCloseOut",
    "StocktakeCloseReconciliationOut",
    "StocktakeDifferencePostIn",
    "StocktakeDifferencePostOut",
    "StocktakeDifferenceGenerateIn",
    "StocktakeDifferenceGenerateOut",
    "StocktakeInitialScopeCountIn",
    "StocktakeInitialScopeCountOut",
    "StocktakePhysicalObservationIn",
    "StocktakeRecountDifferenceOut",
    "StocktakeRecountOpenIn",
    "StocktakeRecountOpenOut",
    "StocktakeRecountScopeAssignmentIn",
    "StocktakeRecountScopeCountIn",
    "StocktakeRecountScopeCountOut",
    "StocktakeReviewIn",
    "StocktakeReviewItemIn",
    "StocktakeReviewOut",
    "StocktakeSnapshotCountIn",
    "StocktakeTerminalIn",
]
