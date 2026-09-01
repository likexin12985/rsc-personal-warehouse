"""Strict HTTP contracts for the formal opening-stocktake write boundary.

The request models contain only user-supplied business facts. Actor identity,
workflow state and all evidence/request hashes are server-owned and therefore
cannot be injected through this boundary.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
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


_SAFE_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]*$"
_DECIMAL_TEXT = re.compile(r"^(0|[1-9]\d*)(?:\.\d{1,3})?$")
_ZERO_UUID = UUID(int=0)
_MAX_OPENING_QUANTITY = Decimal("1000000000000000")


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_uuid(value: UUID, field_name: str) -> UUID:
    if value == _ZERO_UUID:
        raise ValueError(f"{field_name} cannot be the zero UUID")
    return value


def _decimal_text(
    value: object,
    *,
    field_name: str,
    positive: bool,
) -> Decimal:
    # JSON numbers may already have lost decimal precision. Requiring a
    # canonical string keeps the HTTP representation and request hash exact.
    if not isinstance(value, str) or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a non-exponent decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:  # defensive; the regex should exclude it
        raise ValueError(f"{field_name} is invalid") from exc
    if (
        not parsed.is_finite()
        or parsed >= _MAX_OPENING_QUANTITY
        or (positive and parsed <= 0)
        or (not positive and parsed < 0)
    ):
        raise ValueError(f"{field_name} is outside the allowed range")
    return parsed


def _require_aware_json_timestamp(value: object, field_name: str) -> object:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field_name} must be an RFC 3339 string")
    return value


class OpeningStocktakeScopeIn(_StrictRequestModel):
    owner_org_id: UUID
    location_id: UUID
    assignee_user_id: StrictStr = Field(min_length=1, max_length=36)
    freeze_mode: Literal["hard", "cutoff_replay"] = "hard"

    @field_validator("owner_org_id", "location_id")
    @classmethod
    def validate_ids(cls, value: UUID, info) -> UUID:
        return _required_uuid(value, info.field_name)

    @field_validator("assignee_user_id")
    @classmethod
    def validate_assignee(cls, value: str) -> str:
        if value != value.strip() or any(
            not 33 <= ord(char) <= 126 for char in value
        ):
            raise ValueError(
                "assignee_user_id must be printable ASCII without spaces"
            )
        return value


class OpeningControlLineIn(_StrictRequestModel):
    sync_inbox_event_id: UUID
    external_object_version_id: UUID
    external_business_key: StrictStr = Field(
        min_length=1,
        max_length=300,
        pattern=_SAFE_REFERENCE_PATTERN,
    )
    material_id: UUID | None = None
    condition_code: Literal["new", "used", "damaged", "scrapped"] | None = None
    control_qty: Decimal
    mapping_status: Literal["resolved", "unresolved"]
    source_updated_at: AwareDatetime | None = None
    mapping_note: StrictStr = Field(default="", max_length=4000)

    @field_validator(
        "sync_inbox_event_id",
        "external_object_version_id",
        "material_id",
    )
    @classmethod
    def validate_ids(cls, value: UUID | None, info) -> UUID | None:
        return None if value is None else _required_uuid(value, info.field_name)

    @field_validator("control_qty", mode="before")
    @classmethod
    def validate_control_quantity(cls, value: object) -> Decimal:
        return _decimal_text(value, field_name="control_qty", positive=False)

    @field_validator("source_updated_at", mode="before")
    @classmethod
    def validate_source_time(cls, value: object) -> object:
        return _require_aware_json_timestamp(value, "source_updated_at")

    @field_validator("mapping_note")
    @classmethod
    def validate_mapping_note(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("mapping_note cannot have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_mapping_binding(self):
        if self.mapping_status == "resolved":
            if (
                self.material_id is None
                or self.condition_code is None
                or self.mapping_note
            ):
                raise ValueError(
                    "resolved control lines require material and condition only"
                )
        elif (
            self.material_id is not None
            or self.condition_code is not None
            or not self.mapping_note
        ):
            raise ValueError(
                "unresolved control lines require mapping_note only"
            )
        return self


class OpeningStocktakeStartIn(_StrictRequestModel):
    task_no: StrictStr = Field(
        min_length=1,
        max_length=100,
        pattern=_SAFE_REFERENCE_PATTERN,
    )
    region_org_id: UUID
    control_source_system_id: UUID
    control_sync_run_id: UUID
    control_sync_scope_key: StrictStr = Field(
        min_length=1,
        max_length=200,
        pattern=_SAFE_REFERENCE_PATTERN,
    )
    scopes: tuple[OpeningStocktakeScopeIn, ...] = Field(min_length=1)
    control_lines: tuple[OpeningControlLineIn, ...] = ()
    blind_count: StrictBool = True
    deadline: AwareDatetime | None = None
    note: StrictStr = Field(default="", max_length=10000)

    @field_validator(
        "region_org_id",
        "control_source_system_id",
        "control_sync_run_id",
    )
    @classmethod
    def validate_ids(cls, value: UUID, info) -> UUID:
        return _required_uuid(value, info.field_name)

    @field_validator("deadline", mode="before")
    @classmethod
    def validate_deadline(cls, value: object) -> object:
        return _require_aware_json_timestamp(value, "deadline")

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("note cannot have surrounding whitespace")
        return value


class OpeningStocktakeStartOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    task_no: str
    status: Literal["counting"]
    cutoff_ledger_cursor: int = Field(ge=0)
    initial_round_id: UUID
    scope_count: int = Field(ge=1)
    snapshot_line_count: int = Field(ge=0)
    control_line_count: int = Field(ge=0)
    replayed: bool


class OpeningPhysicalObservationIn(_StrictRequestModel):
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
    material_id: UUID | None = None
    lot_id: UUID | None = None
    lot_no_raw: StrictStr | None = Field(
        default=None, min_length=1, max_length=160
    )
    serial_id: UUID | None = None
    serial_no_raw: StrictStr | None = Field(
        default=None, min_length=1, max_length=200
    )
    serial_identifier_type: Literal["serial_no", "qr_code", "unknown"] | None = None
    count_method: Literal["scan", "manual", "import"] = "manual"
    reason_code: StrictStr | None = Field(
        default=None, min_length=1, max_length=80
    )
    remark: StrictStr = Field(default="", max_length=4000)

    @field_validator("material_id", "lot_id", "serial_id")
    @classmethod
    def validate_ids(cls, value: UUID | None, info) -> UUID | None:
        return None if value is None else _required_uuid(value, info.field_name)

    @field_validator("counted_qty", mode="before")
    @classmethod
    def validate_counted_quantity(cls, value: object) -> Decimal:
        return _decimal_text(value, field_name="counted_qty", positive=True)

    @field_validator(
        "material_identifier_raw",
        "lot_no_raw",
        "serial_no_raw",
        "reason_code",
        "remark",
    )
    @classmethod
    def validate_text(cls, value: str | None, info) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError(
                f"{info.field_name} cannot have surrounding whitespace"
            )
        return value

    @model_validator(mode="after")
    def validate_identifier_bindings(self):
        if (self.serial_no_raw is None) != (
            self.serial_identifier_type is None
        ):
            raise ValueError(
                "serial_no_raw and serial_identifier_type must be paired"
            )
        if self.serial_id is not None and self.serial_no_raw is None:
            raise ValueError("serial_id requires serial_no_raw")
        if self.lot_id is not None and self.lot_no_raw is None:
            raise ValueError("lot_id requires lot_no_raw")
        return self


class OpeningStocktakeCountIn(_StrictRequestModel):
    physical_observations: tuple[OpeningPhysicalObservationIn, ...] = ()
    zero_confirmed: StrictBool = False


class OpeningStocktakeCountOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    task_status: Literal[
        "counting",
        "submitted",
        "region_review",
        "hq_review",
        "approved",
        "recount_required",
        "posted",
        "closed",
    ]
    round_status: Literal["counting", "submitted", "superseded"]
    scope_completed: bool
    round_sealed: bool
    has_pending_verification: bool
    replayed: bool


class OpeningStocktakeReviewItemIn(_StrictRequestModel):
    difference_id: UUID
    decision: Literal[
        "accept_for_posting", "pending_verification", "recount", "reject"
    ]
    comment: StrictStr = Field(default="", max_length=4000)

    @field_validator("difference_id")
    @classmethod
    def validate_difference_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "difference_id")

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("comment cannot have surrounding whitespace")
        return value


class OpeningStocktakeReviewIn(_StrictRequestModel):
    decision: Literal["approve", "recount", "reject"]
    items: tuple[OpeningStocktakeReviewItemIn, ...] = ()
    comment: StrictStr = Field(default="", max_length=10000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("comment cannot have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_decision_comment(self):
        if self.decision != "approve" and not self.comment:
            raise ValueError("recount or reject requires comment")
        difference_ids = [row.difference_id for row in self.items]
        if len(set(difference_ids)) != len(difference_ids):
            raise ValueError("duplicate review difference_id")
        return self


class OpeningStocktakeHeadquartersReviewIn(_StrictRequestModel):
    decision: Literal["approve", "reject"]
    items: tuple[OpeningStocktakeReviewItemIn, ...] = ()
    comment: StrictStr = Field(default="", max_length=10000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("comment cannot have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_decision_comment(self):
        if self.decision == "reject" and not self.comment:
            raise ValueError("reject requires comment")
        difference_ids = [row.difference_id for row in self.items]
        if len(set(difference_ids)) != len(difference_ids):
            raise ValueError("duplicate review difference_id")
        return self


class OpeningStocktakeReviewOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    review_id: UUID
    task_id: UUID
    round_id: UUID
    review_stage: Literal["region", "headquarters"]
    decision: Literal["approve", "recount", "reject"]
    resulting_task_status: Literal[
        "hq_review", "approved", "recount_required"
    ]
    item_count: int = Field(ge=0)
    pending_control_count: int = Field(ge=0)
    replayed: bool


class OpeningStocktakeRecountAssignmentIn(_StrictRequestModel):
    scope_id: UUID
    assignee_user_id: StrictStr = Field(min_length=1, max_length=36)

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "scope_id")

    @field_validator("assignee_user_id")
    @classmethod
    def validate_assignee(cls, value: str) -> str:
        if value != value.strip() or any(
            not 33 <= ord(char) <= 126 for char in value
        ):
            raise ValueError(
                "assignee_user_id must be printable ASCII without spaces"
            )
        return value


class OpeningStocktakeRecountIn(_StrictRequestModel):
    assignments: tuple[OpeningStocktakeRecountAssignmentIn, ...] = Field(
        min_length=1
    )
    reason: StrictStr = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reason cannot have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_assignments(self):
        scope_ids = [row.scope_id for row in self.assignments]
        if len(set(scope_ids)) != len(scope_ids):
            raise ValueError("duplicate recount scope_id")
        return self


class OpeningStocktakeRecountOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    recount_case_id: UUID
    task_id: UUID
    source_round_id: UUID
    next_round_id: UUID
    next_round_no: int = Field(ge=2)
    scope_count: int = Field(ge=1)
    resulting_task_status: Literal["counting"]
    replayed: bool


class OpeningObservationDispositionIn(_StrictRequestModel):
    disposition: Literal[
        "resolved_existing_master", "pending_verification", "requires_recount"
    ]
    reason_code: StrictStr = Field(min_length=1, max_length=80)
    comment: StrictStr = Field(default="", max_length=4000)
    resolved_material_id: UUID | None = None
    resolved_lot_id: UUID | None = None
    resolved_serial_id: UUID | None = None

    @field_validator(
        "resolved_material_id",
        "resolved_lot_id",
        "resolved_serial_id",
    )
    @classmethod
    def validate_ids(cls, value: UUID | None, info) -> UUID | None:
        return None if value is None else _required_uuid(value, info.field_name)

    @field_validator("reason_code", "comment")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        if value != value.strip():
            raise ValueError(
                f"{info.field_name} cannot have surrounding whitespace"
            )
        return value

    @model_validator(mode="after")
    def validate_resolution_binding(self):
        resolved_ids = (
            self.resolved_material_id,
            self.resolved_lot_id,
            self.resolved_serial_id,
        )
        if self.disposition == "resolved_existing_master":
            if self.resolved_material_id is None:
                raise ValueError(
                    "resolved_existing_master requires material_id"
                )
        elif any(value is not None for value in resolved_ids) or not self.comment:
            raise ValueError(
                "unresolved dispositions require comment and forbid master IDs"
            )
        return self


class OpeningObservationDispositionOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    disposition_id: UUID
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    observation_id: UUID
    disposition: Literal[
        "resolved_existing_master", "pending_verification", "requires_recount"
    ]
    resolved_material_id: UUID | None
    resolved_lot_id: UUID | None
    resolved_serial_id: UUID | None
    disposition_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replayed: bool


class OpeningStocktakeTerminalIn(_StrictRequestModel):
    expected_version: int = Field(ge=0, strict=True)


class OpeningStocktakePostOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    round_id: UUID
    posting_id: UUID
    inventory_transaction_id: UUID | None
    resulting_task_status: Literal["posted"]
    task_version: int = Field(ge=0)
    total_quantity: str = Field(pattern=r"^(0|[1-9]\d*)\.\d{3}$")
    established_scope_count: int = Field(ge=1)
    pending_control_difference_count: int = Field(ge=0)
    ledger_cursor: int = Field(ge=0)
    replayed: bool


class OpeningStocktakeCloseOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    posting_id: UUID
    inventory_transaction_id: UUID | None
    resulting_task_status: Literal["closed"]
    task_version: int = Field(ge=0)
    closed_at: datetime
    replayed: bool
