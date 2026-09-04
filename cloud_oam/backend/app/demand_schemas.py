"""Strict HTTP contracts for formal V1.0 material requests.

The public inputs contain only user-entered business facts.  Requester,
region, approver, workflow state, quantity projections, audit coordinates and
hashes are server-owned and therefore cannot be injected by a client.

Quantities are accepted only as canonical decimal strings.  This avoids the
precision loss and exponent ambiguity of JSON numbers before a request hash
or an approval conservation check is calculated.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


_DECIMAL_TEXT = re.compile(r"^(0|[1-9]\d*)(?:\.\d{1,3})?$")
_CONTACT_MOBILE = re.compile(r"^\+?[0-9][0-9 -]{4,30}[0-9]$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]*$")
_ZERO_UUID = UUID(int=0)
_MAX_QUANTITY = Decimal("1000000000000000")


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_uuid(value: UUID, field_name: str) -> UUID:
    if value == _ZERO_UUID:
        raise ValueError(f"{field_name} cannot be the zero UUID")
    return value


def _trimmed(value: str, field_name: str) -> str:
    if value != value.strip():
        raise ValueError(f"{field_name} cannot have surrounding whitespace")
    return value


def _decimal_text(
    value: object,
    *,
    field_name: str,
    positive: bool,
) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be a non-exponent decimal string with at most "
            "three fractional digits"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:  # defensive after the regular expression
        raise ValueError(f"{field_name} is invalid") from exc
    if (
        not parsed.is_finite()
        or parsed >= _MAX_QUANTITY
        or (positive and parsed <= 0)
        or (not positive and parsed < 0)
    ):
        raise ValueError(f"{field_name} is outside the allowed range")
    return parsed


def _unique_nonzero_uuids(
    values: tuple[UUID, ...],
    field_name: str,
) -> tuple[UUID, ...]:
    checked = tuple(_required_uuid(value, field_name) for value in values)
    if len(set(checked)) != len(checked):
        raise ValueError(f"{field_name} cannot contain duplicates")
    return checked


class MaterialRequestAddressIn(_StrictRequestModel):
    province_code: StrictStr = Field(min_length=1, max_length=12)
    province_name: StrictStr = Field(min_length=1, max_length=80)
    city_name: StrictStr = Field(min_length=1, max_length=80)
    district_name: StrictStr = Field(min_length=1, max_length=80)
    detail: StrictStr = Field(min_length=1, max_length=500)

    @field_validator(
        "province_code",
        "province_name",
        "city_name",
        "district_name",
        "detail",
    )
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _trimmed(value, info.field_name)


class MaterialRequestContactIn(_StrictRequestModel):
    name: StrictStr = Field(min_length=1, max_length=120)
    mobile: StrictStr = Field(min_length=6, max_length=32)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _trimmed(value, "name")

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, value: str) -> str:
        checked = _trimmed(value, "mobile")
        if _CONTACT_MOBILE.fullmatch(checked) is None:
            raise ValueError("mobile must be a plausible international number")
        return checked


class MaterialRequestLineDraftIn(_StrictRequestModel):
    material_id: UUID
    requested_qty: Decimal
    required_date: date | None = None
    suggested_substitute_material_id: UUID | None = None
    note: StrictStr = Field(default="", max_length=2000)

    @field_validator("material_id", "suggested_substitute_material_id")
    @classmethod
    def validate_ids(cls, value: UUID | None, info) -> UUID | None:
        return None if value is None else _required_uuid(value, info.field_name)

    @field_validator("requested_qty", mode="before")
    @classmethod
    def validate_requested_qty(cls, value: object) -> Decimal:
        return _decimal_text(
            value,
            field_name="requested_qty",
            positive=True,
        )

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        return _trimmed(value, "note")

    @model_validator(mode="after")
    def validate_substitute_is_different(self):
        if self.suggested_substitute_material_id == self.material_id:
            raise ValueError("suggested substitute must differ from material")
        return self


class MaterialRequestDraftFields(_StrictRequestModel):
    work_order_id: UUID | None = None
    purpose: StrictStr = Field(min_length=1, max_length=4000)
    urgency: Literal["normal", "urgent", "emergency"] = "normal"
    expected_date: date | None = None
    address: MaterialRequestAddressIn
    contact: MaterialRequestContactIn
    attachment_file_ids: tuple[UUID, ...] = Field(default=(), max_length=20)
    note: StrictStr = Field(default="", max_length=10000)
    lines: tuple[MaterialRequestLineDraftIn, ...] = Field(
        min_length=1,
        max_length=200,
    )

    @field_validator("work_order_id")
    @classmethod
    def validate_work_order_id(cls, value: UUID | None) -> UUID | None:
        return None if value is None else _required_uuid(value, "work_order_id")

    @field_validator("attachment_file_ids")
    @classmethod
    def validate_attachment_ids(
        cls, values: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        return _unique_nonzero_uuids(values, "attachment_file_ids")

    @field_validator("purpose", "note")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _trimmed(value, info.field_name)

    @model_validator(mode="after")
    def validate_line_dimensions_are_unique(self):
        dimensions = tuple(
            (
                row.material_id,
                row.required_date,
                row.suggested_substitute_material_id,
            )
            for row in self.lines
        )
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("request lines contain duplicate dimensions")
        return self


class MaterialRequestCreateIn(MaterialRequestDraftFields):
    """Create a draft; requester and region are always derived server-side."""


class MaterialRequestAmendIn(MaterialRequestDraftFields):
    expected_version: int = Field(ge=0)


class MaterialRequestSubmitIn(_StrictRequestModel):
    expected_version: int = Field(ge=0)


class _StrictLifecycleCommandModel(_StrictRequestModel):
    """Lifecycle commands must not accept Python/JSON scalar coercion."""

    # Fields use StrictInt/StrictStr and explicit decimal/UUID validators.
    # Keeping model-wide strict mode off is intentional: JSON arrays must be
    # materialized as immutable tuples and UUIDs arrive over JSON as strings.
    model_config = ConfigDict(extra="forbid")


class MaterialRequestWithdrawIn(_StrictLifecycleCommandModel):
    expected_version: StrictInt = Field(ge=0)
    reason: StrictStr = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _trimmed(value, "reason")


class MaterialRequestCancellationLineIn(_StrictLifecycleCommandModel):
    request_line_id: UUID
    cancelled_qty: Decimal
    reason: StrictStr = Field(min_length=1, max_length=4000)

    @field_validator("request_line_id", mode="before")
    @classmethod
    def parse_line_id(cls, value: object) -> UUID:
        if isinstance(value, UUID):
            return value
        if type(value) is not str:
            raise ValueError("request_line_id must be a UUID string")
        try:
            return UUID(value)
        except ValueError as exc:
            raise ValueError("request_line_id must be a UUID string") from exc

    @field_validator("request_line_id")
    @classmethod
    def validate_line_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "request_line_id")

    @field_validator("cancelled_qty", mode="before")
    @classmethod
    def validate_cancelled_qty(cls, value: object) -> Decimal:
        return _decimal_text(
            value,
            field_name="cancelled_qty",
            positive=True,
        )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _trimmed(value, "reason")


class MaterialRequestCancelIn(_StrictLifecycleCommandModel):
    expected_version: StrictInt = Field(ge=0)
    reason: StrictStr = Field(min_length=1, max_length=4000)
    lines: tuple[MaterialRequestCancellationLineIn, ...] = Field(
        default=(),
        max_length=200,
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _trimmed(value, "reason")

    @model_validator(mode="after")
    def validate_unique_lines(self):
        line_ids = tuple(row.request_line_id for row in self.lines)
        if len(set(line_ids)) != len(line_ids):
            raise ValueError("cancellation lines cannot repeat a request line")
        return self


class ApprovalLineDecisionIn(_StrictRequestModel):
    request_line_id: UUID
    approved_qty: Decimal
    reason: StrictStr = Field(default="", max_length=4000)

    @field_validator("request_line_id")
    @classmethod
    def validate_line_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "request_line_id")

    @field_validator("approved_qty", mode="before")
    @classmethod
    def validate_approved_qty(cls, value: object) -> Decimal:
        return _decimal_text(
            value,
            field_name="approved_qty",
            positive=False,
        )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _trimmed(value, "reason")


class ApprovalReturnLineIn(_StrictRequestModel):
    """One immutable line-level instruction when a later step reopens a prior step."""

    request_line_id: UUID
    requested_reapproval_qty: Decimal
    reason: StrictStr = Field(min_length=1, max_length=4000)

    @field_validator("request_line_id")
    @classmethod
    def validate_line_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "request_line_id")

    @field_validator("requested_reapproval_qty", mode="before")
    @classmethod
    def validate_requested_reapproval_qty(cls, value: object) -> Decimal:
        return _decimal_text(
            value,
            field_name="requested_reapproval_qty",
            positive=True,
        )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _trimmed(value, "reason")


class MaterialRequestApprovalDecisionIn(_StrictRequestModel):
    expected_request_version: int = Field(ge=0)
    expected_step_version: int = Field(ge=0)
    action: Literal["approve", "return", "reject"]
    lines: tuple[ApprovalLineDecisionIn, ...] = Field(
        default=(),
        max_length=200,
    )
    return_lines: tuple[ApprovalReturnLineIn, ...] = Field(
        default=(),
        max_length=200,
    )
    comment: StrictStr = Field(default="", max_length=4000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        return _trimmed(value, "comment")

    @model_validator(mode="after")
    def validate_action_shape(self):
        if self.action == "approve":
            if not self.lines:
                raise ValueError("approve requires an exact line decision set")
            if self.return_lines:
                raise ValueError("approve does not accept return instructions")
            line_ids = tuple(row.request_line_id for row in self.lines)
            if len(set(line_ids)) != len(line_ids):
                raise ValueError("approval decisions cannot repeat a request line")
        elif self.action == "return":
            if self.lines:
                raise ValueError("return does not accept approval quantities")
            if not self.return_lines:
                raise ValueError("return requires line-level reapproval instructions")
            line_ids = tuple(row.request_line_id for row in self.return_lines)
            if len(set(line_ids)) != len(line_ids):
                raise ValueError("return instructions cannot repeat a request line")
        elif self.lines or self.return_lines:
            raise ValueError("reject does not accept line quantities")
        if self.action in {"return", "reject"} and not self.comment:
            raise ValueError("return and reject require a comment")
        return self


class ExternalApprovalRegistrationIn(_StrictRequestModel):
    expected_request_version: int = Field(ge=0)
    expected_step_version: int = Field(ge=0)
    evidence_file_id: UUID
    external_approver_name: StrictStr = Field(min_length=1, max_length=160)
    external_reference_no: StrictStr = Field(
        min_length=1,
        max_length=200,
        pattern=_SAFE_REFERENCE.pattern,
    )
    external_decided_at: AwareDatetime
    action: Literal["approve", "return", "reject"]
    lines: tuple[ApprovalLineDecisionIn, ...] = Field(
        default=(),
        max_length=200,
    )
    return_lines: tuple[ApprovalReturnLineIn, ...] = Field(
        default=(),
        max_length=200,
    )
    comment: StrictStr = Field(default="", max_length=4000)

    @field_validator("evidence_file_id")
    @classmethod
    def validate_file_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "evidence_file_id")

    @field_validator(
        "external_approver_name",
        "external_reference_no",
        "comment",
    )
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _trimmed(value, info.field_name)

    @model_validator(mode="after")
    def validate_registration_shape(self):
        if self.action == "approve":
            if not self.lines:
                raise ValueError("external approval requires line decisions")
            if self.return_lines:
                raise ValueError("external approval does not accept return instructions")
            line_ids = tuple(row.request_line_id for row in self.lines)
            if len(set(line_ids)) != len(line_ids):
                raise ValueError("external decisions cannot repeat a request line")
        elif self.action == "return":
            if self.lines:
                raise ValueError("external return does not accept approval quantities")
            if not self.return_lines:
                raise ValueError("external return requires line-level instructions")
            line_ids = tuple(row.request_line_id for row in self.return_lines)
            if len(set(line_ids)) != len(line_ids):
                raise ValueError("external return instructions cannot repeat a line")
        elif self.lines or self.return_lines:
            raise ValueError("external rejection does not accept quantities")
        if self.action in {"return", "reject"} and not self.comment:
            raise ValueError("external return and reject require a comment")
        return self


class ExternalApprovalVerificationIn(_StrictRequestModel):
    expected_request_version: int = Field(ge=0)
    expected_step_version: int = Field(ge=0)
    decision: Literal["accept", "reject"]
    comment: StrictStr = Field(default="", max_length=4000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        return _trimmed(value, "comment")

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.decision == "reject" and not self.comment:
            raise ValueError("verification rejection requires a comment")
        return self


class SubstitutionProposalIn(_StrictRequestModel):
    expected_request_version: int = Field(ge=0)
    substitute_material_id: UUID
    ratio: Decimal
    reason: StrictStr = Field(min_length=1, max_length=4000)

    @field_validator("substitute_material_id")
    @classmethod
    def validate_material_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "substitute_material_id")

    @field_validator("ratio", mode="before")
    @classmethod
    def validate_ratio(cls, value: object) -> Decimal:
        return _decimal_text(value, field_name="ratio", positive=True)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _trimmed(value, "reason")


class SubstitutionConfirmationIn(_StrictRequestModel):
    expected_request_version: int = Field(ge=0)
    decision: Literal["confirm", "reject"]
    comment: StrictStr = Field(default="", max_length=4000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        return _trimmed(value, "comment")

    @model_validator(mode="after")
    def validate_rejection_reason(self):
        if self.decision == "reject" and not self.comment:
            raise ValueError("substitution rejection requires a comment")
        return self


class SupplyTaskCreateIn(_StrictRequestModel):
    expected_request_version: StrictInt = Field(ge=0)
    request_line_id: UUID
    supply_type: Literal[
        "cross_region_transfer",
        "headquarters_replenishment",
        "star_replenishment",
        "external_procurement_reference",
    ]
    reference_no: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=_SAFE_REFERENCE.pattern,
    )
    expected_qty: Decimal
    expected_date: date | None = None
    note: StrictStr = Field(default="", max_length=4000)

    @field_validator("request_line_id")
    @classmethod
    def validate_line_id(cls, value: UUID) -> UUID:
        return _required_uuid(value, "request_line_id")

    @field_validator("expected_qty", mode="before")
    @classmethod
    def validate_expected_qty(cls, value: object) -> Decimal:
        return _decimal_text(
            value,
            field_name="expected_qty",
            positive=True,
        )

    @field_validator("reference_no", "note")
    @classmethod
    def validate_text(cls, value: str | None, info) -> str | None:
        return None if value is None else _trimmed(value, info.field_name)


class SupplyTaskUpdateIn(_StrictRequestModel):
    expected_request_version: StrictInt = Field(ge=0)
    expected_task_version: StrictInt = Field(ge=0)
    # A shortage-planning task records only an external/cross-region supply
    # reference.  It must not claim that allocation, shipment or receipt has
    # happened; those facts belong to their own later bounded services.
    status: Literal[
        "open",
        "reference_registered",
        "awaiting_supply",
        "cancelled",
        "closed_no_supply",
    ]
    reference_no: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=_SAFE_REFERENCE.pattern,
    )
    expected_date: date | None = None
    comment: StrictStr = Field(default="", max_length=4000)

    @field_validator("reference_no", "comment")
    @classmethod
    def validate_text(cls, value: str | None, info) -> str | None:
        return None if value is None else _trimmed(value, info.field_name)

    @model_validator(mode="after")
    def validate_terminal_comment(self):
        if self.status in {"cancelled", "closed_no_supply"} and not self.comment:
            raise ValueError("closing a supply task without supply requires a comment")
        if self.status == "reference_registered" and self.reference_no is None:
            raise ValueError("registered supply reference requires a reference number")
        return self


class MaterialRequestStateAxesOut(BaseModel):
    """Independent projections; approval must never advance another axis."""

    model_config = ConfigDict(extra="forbid")

    request_status: Literal[
        "draft",
        "submitted",
        "approval_in_progress",
        "returned",
        "partially_approved",
        "approved",
        "rejected",
        "withdrawn",
        "cancellation_pending",
        "cancelled",
    ]
    allocation_status: Literal[
        "not_allocated", "partially_allocated", "allocated", "shortage"
    ]
    reservation_status: Literal[
        "not_reserved", "pending", "reserved", "partially_released", "released", "fulfilled"
    ]
    outbound_status: Literal["not_started", "pending_pick", "picked", "outbound"]
    shipment_status: Literal[
        "not_started", "pending_handover", "shipped", "in_transit", "exception"
    ]
    logistics_signature_status: Literal[
        "not_signed", "signed", "refused", "exception"
    ]
    oam_receipt_status: Literal["not_occurred", "synced", "exception"]
    personal_inbound_status: Literal[
        "pending_acceptance", "partially_accepted", "accepted", "posted", "not_started"
    ]
    notification_status: Literal[
        "not_started", "queued", "sent", "delivered", "read", "failed"
    ]
    reconciliation_status: Literal[
        "pending", "staged", "validated", "reconciled", "conflict", "failed", "not_started"
    ]


__all__ = [
    "ApprovalLineDecisionIn",
    "ApprovalReturnLineIn",
    "ExternalApprovalRegistrationIn",
    "ExternalApprovalVerificationIn",
    "MaterialRequestAddressIn",
    "MaterialRequestAmendIn",
    "MaterialRequestApprovalDecisionIn",
    "MaterialRequestCancelIn",
    "MaterialRequestContactIn",
    "MaterialRequestCreateIn",
    "MaterialRequestLineDraftIn",
    "MaterialRequestStateAxesOut",
    "MaterialRequestSubmitIn",
    "MaterialRequestWithdrawIn",
    "SubstitutionConfirmationIn",
    "SubstitutionProposalIn",
    "SupplyTaskCreateIn",
    "SupplyTaskUpdateIn",
]
