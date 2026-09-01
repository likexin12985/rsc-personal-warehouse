"""Read-only wire contracts for formal V1.0 material requests.

The response surface intentionally contains masked contact/address snapshots
only.  It projects approval, allocation, reservation, outbound, shipment,
signature, OAM receipt, personal inbound, notification and reconciliation as
independent facts; a completed approval is never used as a shortcut for any
fulfilment axis.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictStr,
    field_validator,
    model_validator,
)

from .demand_schemas import MaterialRequestStateAxesOut


_MASK = re.compile(r"[＊*•]")
_LONG_PLAINTEXT_DIGITS = re.compile(r"\d{7,}")

QuantityOut = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=3),
    PlainSerializer(lambda value: f"{value:.3f}", return_type=str, when_used="json"),
]
PositiveQuantityOut = Annotated[
    Decimal,
    Field(gt=0, max_digits=18, decimal_places=3),
    PlainSerializer(lambda value: f"{value:.3f}", return_type=str, when_used="json"),
]

MaterialRequestStatus = Literal[
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
MaterialRequestAllowedAction = Literal[
    "update",
    "submit",
    "withdraw",
    "cancel",
    "approve",
    "return",
    "reject",
    "register_external_approval",
    "verify_external_approval",
    "propose_substitution",
    "confirm_substitution",
    "reject_substitution",
    "create_supply_task",
]
SupplyTaskAllowedAction = Literal["update_supply_task", "cancel_supply_task"]


class _StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaterialRequestAddressSnapshotOut(_StrictOutputModel):
    province_code: StrictStr = Field(min_length=1, max_length=12)
    province_name: StrictStr = Field(min_length=1, max_length=80)
    city_name: StrictStr = Field(min_length=1, max_length=80)
    district_name: StrictStr = Field(min_length=1, max_length=80)
    detail_masked: StrictStr = Field(min_length=1, max_length=500)

    @field_validator(
        "province_code",
        "province_name",
        "city_name",
        "district_name",
        "detail_masked",
    )
    @classmethod
    def validate_trimmed(cls, value: str, info) -> str:
        if value != value.strip():
            raise ValueError(f"{info.field_name} cannot have surrounding whitespace")
        if info.field_name == "detail_masked" and _MASK.search(value) is None:
            raise ValueError("address detail must be masked")
        return value


class MaterialRequestContactMaskedOut(_StrictOutputModel):
    name_masked: StrictStr = Field(min_length=1, max_length=120)
    mobile_masked: StrictStr = Field(min_length=1, max_length=32)

    @field_validator("name_masked", "mobile_masked")
    @classmethod
    def validate_masked(cls, value: str, info) -> str:
        if value != value.strip() or _MASK.search(value) is None:
            raise ValueError(f"{info.field_name} must be masked")
        if info.field_name == "mobile_masked" and _LONG_PLAINTEXT_DIGITS.search(value):
            raise ValueError("masked mobile contains a plaintext digit run")
        return value


class MaterialRequestAttachmentRefOut(_StrictOutputModel):
    revision_id: UUID
    revision_no: int = Field(ge=1)
    request_line_id: UUID | None
    file_id: UUID
    display_name: StrictStr = Field(min_length=1, max_length=255)
    purpose: Literal["request_attachment", "request_line_attachment"]

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.purpose == "request_attachment") != (self.request_line_id is None):
            raise ValueError("attachment purpose and request-line anchor disagree")
        return self


class MaterialRequestRevisionSummaryOut(_StrictOutputModel):
    revision_id: UUID
    revision_no: int = Field(ge=1)
    previous_revision_id: UUID | None
    status: Literal["draft", "sealed"]
    line_count: int = Field(ge=1, le=200)
    attachment_count: int = Field(ge=0)
    sealed_at: AwareDatetime | None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_seal_state(self):
        if (self.status == "sealed") != (self.sealed_at is not None):
            raise ValueError("revision status and seal time disagree")
        if self.sealed_at is not None and self.sealed_at < self.created_at:
            raise ValueError("revision seal time precedes creation")
        return self


class MaterialRequestApprovalAssigneeSnapshotOut(_StrictOutputModel):
    """Permission-minimal frozen display identity for an approval step."""

    name_masked: StrictStr = Field(min_length=1, max_length=120)
    role_code: Literal[
        "admin",
        "provincial_manager",
        "star_headquarters_approver",
    ]

    @field_validator("name_masked")
    @classmethod
    def validate_name_masked(cls, value: str) -> str:
        if value != value.strip() or _MASK.search(value) is None:
            raise ValueError("approval assignee name must be masked")
        return value


class MaterialRequestApprovalCandidatePoolSummaryOut(_StrictOutputModel):
    """Count/kind-only projection; no candidate identities or permissions."""

    candidate_count: int = Field(ge=1)
    candidate_kinds: tuple[Literal["assignee", "registrar", "verifier"], ...] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def validate_kinds(self):
        canonical = ("assignee", "registrar", "verifier")
        if len(set(self.candidate_kinds)) != len(self.candidate_kinds):
            raise ValueError("candidate pool kinds cannot repeat")
        if self.candidate_kinds != tuple(
            item for item in canonical if item in self.candidate_kinds
        ):
            raise ValueError("candidate pool kinds must use canonical order")
        return self


class MaterialRequestApprovalLineDecisionOut(_StrictOutputModel):
    decision_id: UUID
    step_id: UUID
    request_revision_id: UUID
    revision_no: int = Field(ge=1)
    request_line_id: UUID
    input_qty: PositiveQuantityOut
    approved_qty: QuantityOut
    rejected_qty: QuantityOut
    reason: StrictStr = Field(default="", max_length=4000)
    decision_source: Literal["internal", "external_registration", "direct_star"]
    external_registration_id: UUID | None
    decided_at: AwareDatetime

    @field_validator("reason")
    @classmethod
    def validate_reason_trimmed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("approval decision reason cannot have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_decision(self):
        if self.approved_qty + self.rejected_qty != self.input_qty:
            raise ValueError("approval decision quantities do not conserve input")
        if self.rejected_qty > 0 and not self.reason:
            raise ValueError("a rejected quantity requires a reason")
        if (self.decision_source == "external_registration") != (
            self.external_registration_id is not None
        ):
            raise ValueError("external decision source and registration anchor disagree")
        return self


class MaterialRequestExternalEvidenceSummaryOut(_StrictOutputModel):
    """Permission-gated external approval evidence without raw approver PII."""

    registration_id: UUID
    registration_no: StrictStr = Field(min_length=1, max_length=100)
    step_id: UUID
    external_action: Literal["approve", "partial_approve", "reject", "return"]
    status: Literal["pending_verification", "accepted", "rejected", "superseded"]
    evidence_file_id: UUID
    external_approver_name_masked: StrictStr = Field(min_length=1, max_length=120)
    external_decided_at: AwareDatetime
    registered_at: AwareDatetime
    verified_at: AwareDatetime | None
    version: int = Field(ge=0)

    @field_validator("registration_no", "external_approver_name_masked")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        if value != value.strip():
            raise ValueError(f"{info.field_name} cannot have surrounding whitespace")
        if (
            info.field_name == "external_approver_name_masked"
            and _MASK.search(value) is None
        ):
            raise ValueError("external approver name must be masked")
        return value

    @model_validator(mode="after")
    def validate_verification_state(self):
        if self.external_decided_at > self.registered_at:
            raise ValueError("external decision time follows evidence registration")
        if (self.status == "pending_verification") != (self.verified_at is None):
            raise ValueError("external evidence status and verification time disagree")
        if self.verified_at is not None and self.verified_at < self.registered_at:
            raise ValueError("external evidence verification precedes registration")
        return self


class MaterialRequestReturnLineFactOut(_StrictOutputModel):
    """Permission-gated, immutable line fact for a return instruction."""

    return_fact_id: UUID
    return_action_id: UUID
    instance_id: UUID
    returned_from_step_id: UUID
    target_kind: Literal["requester_revision", "approval_step"]
    target_step_id: UUID | None
    request_revision_id: UUID
    revision_no: int = Field(ge=1)
    request_line_id: UUID
    returned_step_input_qty: PositiveQuantityOut
    target_step_max_qty: PositiveQuantityOut
    required_review_qty: PositiveQuantityOut
    reason: StrictStr = Field(min_length=1, max_length=4000)
    occurred_at: AwareDatetime

    @field_validator("reason")
    @classmethod
    def validate_reason_trimmed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("return reason cannot have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_target_and_quantity(self):
        if (self.target_kind == "requester_revision") != (
            self.target_step_id is None
        ):
            raise ValueError("return target kind and target step disagree")
        if self.required_review_qty > self.target_step_max_qty:
            raise ValueError("required review quantity exceeds target-step maximum")
        return self


class MaterialRequestApprovalStepOut(_StrictOutputModel):
    step_id: UUID
    step_no: Literal[1, 2, 3]
    attempt_no: int = Field(ge=1)
    predecessor_step_id: UUID | None
    supersedes_step_id: UUID | None
    reopened_from_step_id: UUID | None
    source_mode: Literal["internal", "external_registration", "direct_star"]
    status: Literal[
        "pending",
        "open",
        "awaiting_external_evidence",
        "evidence_pending_verification",
        "approved",
        "partially_approved",
        "rejected",
        "returned",
        "cancelled",
        "superseded",
    ]
    assignee_snapshot: MaterialRequestApprovalAssigneeSnapshotOut | None
    candidate_pool_summary: MaterialRequestApprovalCandidatePoolSummaryOut | None
    opened_at: AwareDatetime | None
    decided_at: AwareDatetime | None
    version: int = Field(ge=0)
    line_decisions: tuple[MaterialRequestApprovalLineDecisionOut, ...]

    @model_validator(mode="after")
    def validate_state_and_decisions(self):
        if self.step_no == 1 and self.predecessor_step_id is not None:
            raise ValueError("first approval level cannot have a predecessor")
        if self.step_no > 1 and self.predecessor_step_id is None:
            raise ValueError("later approval level requires a predecessor")
        if (self.attempt_no == 1) != (self.supersedes_step_id is None):
            raise ValueError("approval step attempt and supersedes anchor disagree")
        if (
            self.source_mode == "internal"
            and self.assignee_snapshot is None
            and self.candidate_pool_summary is None
        ):
            raise ValueError("internal approval step requires an assignee or candidate pool")
        if self.source_mode != "internal" and self.assignee_snapshot is not None:
            raise ValueError("external approval step cannot expose an internal assignee")
        if (
            self.source_mode == "external_registration"
            and (
                self.candidate_pool_summary is None
                or set(self.candidate_pool_summary.candidate_kinds)
                != {"registrar", "verifier"}
            )
        ):
            raise ValueError("external registration step requires registrar/verifier pool")
        decided_statuses = {"approved", "partially_approved", "rejected", "returned"}
        if (self.status in decided_statuses) != (self.decided_at is not None):
            raise ValueError("approval step status and decision time disagree")
        opened_statuses = {
            "open",
            "awaiting_external_evidence",
            "evidence_pending_verification",
            *decided_statuses,
        }
        if self.status in opened_statuses and self.opened_at is None:
            raise ValueError("opened or decided approval step requires an opened time")
        if self.status == "pending" and (
            self.opened_at is not None or self.decided_at is not None
        ):
            raise ValueError("pending approval step cannot contain action times")
        if self.status in {"cancelled", "superseded"} and self.decided_at is not None:
            raise ValueError("cancelled or superseded step cannot fabricate a decision time")
        if (
            self.opened_at is not None
            and self.decided_at is not None
            and self.decided_at < self.opened_at
        ):
            raise ValueError("approval decision precedes step opening")
        decision_ids = {item.decision_id for item in self.line_decisions}
        line_ids = {item.request_line_id for item in self.line_decisions}
        if len(decision_ids) != len(self.line_decisions) or len(line_ids) != len(
            self.line_decisions
        ):
            raise ValueError("approval line decisions cannot repeat")
        if any(item.step_id != self.step_id for item in self.line_decisions):
            raise ValueError("approval line decision is anchored to another step")
        if self.status in {"approved", "partially_approved", "rejected"} and not (
            self.line_decisions
        ):
            raise ValueError("decided approval step requires line decisions")
        return self


class MaterialRequestApprovalInstanceOut(_StrictOutputModel):
    instance_id: UUID
    request_revision_id: UUID
    revision_no: int = Field(ge=1)
    attempt_no: int = Field(ge=1)
    status: Literal[
        "active",
        "returned",
        "completed",
        "rejected",
        "withdrawn",
        "cancelled",
        "superseded",
    ]
    current_step_no: Literal[1, 2, 3] | None
    current_step_id: UUID | None
    version: int = Field(ge=0)
    steps: tuple[MaterialRequestApprovalStepOut, ...] = Field(
        min_length=3,
    )
    external_evidence_summaries: tuple[
        MaterialRequestExternalEvidenceSummaryOut, ...
    ] | None
    return_line_facts: tuple[MaterialRequestReturnLineFactOut, ...] | None

    @model_validator(mode="after")
    def validate_route_and_state(self):
        ordered_coordinates = tuple((step.step_no, step.attempt_no) for step in self.steps)
        if ordered_coordinates != tuple(sorted(ordered_coordinates)):
            raise ValueError("approval step history must use canonical causal order")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("approval step ids must be unique")
        if len(set(ordered_coordinates)) != len(ordered_coordinates):
            raise ValueError("approval step attempts must be unique")
        expected_sources = {1: "internal", 2: "internal", 3: "external_registration"}
        if any(step.source_mode != expected_sources[step.step_no] for step in self.steps):
            raise ValueError("phase-one approval route must use external registration")
        by_id = {step.step_id: step for step in self.steps}
        for step_no in (1, 2, 3):
            attempts = [step for step in self.steps if step.step_no == step_no]
            if tuple(step.attempt_no for step in attempts) != tuple(
                range(1, len(attempts) + 1)
            ):
                raise ValueError("approval step attempt numbers must be contiguous")
            for index, step in enumerate(attempts):
                expected_superseded = None if index == 0 else attempts[index - 1].step_id
                if step.supersedes_step_id != expected_superseded:
                    raise ValueError("approval supersedes chain is not contiguous")
        for step in self.steps:
            if step.predecessor_step_id is not None:
                predecessor = by_id.get(step.predecessor_step_id)
                if predecessor is None or predecessor.step_no != step.step_no - 1:
                    raise ValueError("approval predecessor is not the immediate lower level")
            if step.reopened_from_step_id is not None:
                source = by_id.get(step.reopened_from_step_id)
                if (
                    source is None
                    or source.step_no != step.step_no + 1
                    or source.status != "returned"
                ):
                    raise ValueError("reopened approval step lacks its returning source")
        if self.status == "active" and (
            self.current_step_no is None or self.current_step_id is None
        ):
            raise ValueError("active approval instance requires a current step")
        if self.status in {
            "returned",
            "completed",
            "rejected",
            "withdrawn",
            "cancelled",
            "superseded",
        } and (self.current_step_no is not None or self.current_step_id is not None):
            raise ValueError("terminal approval instance cannot have a current step")
        if self.current_step_id is not None:
            current = by_id.get(self.current_step_id)
            if (
                current is None
                or current.step_no != self.current_step_no
                or current.status
                not in {
                    "open",
                    "awaiting_external_evidence",
                    "evidence_pending_verification",
                }
            ):
                raise ValueError("current approval pointer does not identify an open step")
        evidence = self.external_evidence_summaries
        if evidence is not None:
            if len({item.registration_id for item in evidence}) != len(evidence):
                raise ValueError("external evidence registrations cannot repeat")
            if len({item.registration_no for item in evidence}) != len(evidence):
                raise ValueError("external evidence registration numbers cannot repeat")
            if any(
                item.step_id not in by_id
                or by_id[item.step_id].source_mode != "external_registration"
                for item in evidence
            ):
                raise ValueError("external evidence is not anchored to an external step")
        facts = self.return_line_facts
        if facts is not None:
            if len({item.return_fact_id for item in facts}) != len(facts):
                raise ValueError("return line facts cannot repeat")
            if len({(item.return_action_id, item.request_line_id) for item in facts}) != len(
                facts
            ):
                raise ValueError("return action line facts cannot repeat")
            for fact in facts:
                source = by_id.get(fact.returned_from_step_id)
                target = by_id.get(fact.target_step_id) if fact.target_step_id else None
                if (
                    fact.instance_id != self.instance_id
                    or fact.request_revision_id != self.request_revision_id
                    or fact.revision_no != self.revision_no
                    or source is None
                    or source.status != "returned"
                ):
                    raise ValueError("return line fact is not anchored to this instance")
                if fact.target_kind == "requester_revision":
                    if source.step_no != 1:
                        raise ValueError("only first-level return can target requester revision")
                elif (
                    target is None
                    or target.step_no != source.step_no - 1
                    or target.reopened_from_step_id != source.step_id
                ):
                    raise ValueError("return line fact target lacks a causal reopened step")
            returned_step_ids = {
                step.step_id for step in self.steps if step.status == "returned"
            }
            if {fact.returned_from_step_id for fact in facts} != returned_step_ids:
                raise ValueError("visible return facts must cover every returned step")
        if self.status == "returned" and not any(
            step.status == "returned" for step in self.steps
        ):
            raise ValueError("returned approval instance lacks a returned step")
        return self


class MaterialRequestLineOut(_StrictOutputModel):
    request_line_id: UUID
    revision_id: UUID
    revision_no: int = Field(ge=1)
    line_no: int = Field(ge=1)
    material_id: UUID
    requested_qty: PositiveQuantityOut
    required_date: date | None
    suggested_substitute_material_id: UUID | None
    note: StrictStr = Field(default="", max_length=2000)
    final_approved_qty: QuantityOut
    cancelled_qty: QuantityOut
    status: Literal[
        "draft",
        "approval_pending",
        "approved",
        "partially_approved",
        "rejected",
        "cancelled",
    ]
    version: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_quantities_and_substitute(self):
        if self.final_approved_qty > self.requested_qty:
            raise ValueError("final approved quantity exceeds requested quantity")
        if self.cancelled_qty > self.final_approved_qty:
            raise ValueError("cancelled quantity exceeds final approved quantity")
        if self.suggested_substitute_material_id == self.material_id:
            raise ValueError("suggested substitute must differ from material")
        return self


class MaterialRequestSupplyTaskOut(_StrictOutputModel):
    id: UUID
    task_no: StrictStr = Field(min_length=1, max_length=100)
    request_line_id: UUID
    substitution_decision_id: UUID | None
    supply_type: Literal[
        "cross_region_transfer",
        "headquarters_replenishment",
        "star_replenishment",
        "external_procurement_reference",
    ]
    reference_no: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    expected_qty: PositiveQuantityOut
    original_equivalent_qty: PositiveQuantityOut
    expected_date: date | None
    status: Literal[
        "open",
        "reference_registered",
        "awaiting_supply",
        "cancelled",
        "closed_no_supply",
    ]
    version: int = Field(ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    allowed_actions: tuple[SupplyTaskAllowedAction, ...]

    @model_validator(mode="after")
    def validate_actions_and_time(self):
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("supply task allowed actions cannot repeat")
        if self.status in {"cancelled", "closed_no_supply"} and self.allowed_actions:
            raise ValueError("closed supply task cannot expose write actions")
        if self.updated_at < self.created_at:
            raise ValueError("supply task update time precedes creation")
        return self


class _MaterialRequestCommonOut(_StrictOutputModel):
    request_id: UUID
    request_no: StrictStr = Field(min_length=1, max_length=100)
    request_version: int = Field(ge=0)
    current_revision_id: UUID
    current_revision_no: int = Field(ge=1)
    work_order_id: UUID | None
    requester_person_id: UUID
    requester_org_id: UUID
    purpose: StrictStr = Field(min_length=1, max_length=4000)
    urgency: Literal["normal", "urgent", "emergency"]
    expected_date: date | None
    address_snapshot: MaterialRequestAddressSnapshotOut
    contact_masked: MaterialRequestContactMaskedOut
    note: StrictStr = Field(default="", max_length=10000)
    attachment_refs: tuple[MaterialRequestAttachmentRefOut, ...]
    approval_mode: Literal["external_registration"]
    states: MaterialRequestStateAxesOut
    approval_instance: MaterialRequestApprovalInstanceOut | None
    allowed_actions: tuple[MaterialRequestAllowedAction, ...]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    submitted_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_common_state(self):
        if len({item.file_id for item in self.attachment_refs}) != len(
            self.attachment_refs
        ):
            raise ValueError("attachment references cannot repeat")
        if any(
            item.revision_id != self.current_revision_id
            or item.revision_no != self.current_revision_no
            for item in self.attachment_refs
        ):
            raise ValueError("attachment reference is not anchored to current revision")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed actions cannot repeat")
        if self.updated_at < self.created_at:
            raise ValueError("request update time precedes creation")
        status = self.states.request_status
        if (status == "draft") != (self.submitted_at is None):
            raise ValueError("request status and submission time disagree")
        if self.submitted_at is not None and self.submitted_at < self.created_at:
            raise ValueError("submission time precedes creation")
        expected_instance_status = {
            "submitted": "active",
            "approval_in_progress": "active",
            "returned": "returned",
            "partially_approved": "completed",
            "approved": "completed",
            "rejected": "rejected",
            "withdrawn": "withdrawn",
            "cancellation_pending": "completed",
        }.get(status)
        if status == "draft" and self.approval_instance is not None:
            raise ValueError("draft request cannot have an approval instance")
        if expected_instance_status and (
            self.approval_instance is None
            or self.approval_instance.status != expected_instance_status
        ):
            raise ValueError("request and approval instance states disagree")
        self._validate_allowed_actions(status)
        return self

    def _validate_allowed_actions(self, status: MaterialRequestStatus) -> None:
        compatible = {
            "update": {"draft", "returned"},
            "submit": {"draft", "returned"},
            "withdraw": {"submitted", "approval_in_progress"},
            "cancel": {
                "draft",
                "returned",
                "partially_approved",
                "approved",
                "cancellation_pending",
            },
            "propose_substitution": {"partially_approved", "approved"},
            "confirm_substitution": {"partially_approved", "approved"},
            "reject_substitution": {"partially_approved", "approved"},
            "create_supply_task": {"partially_approved", "approved"},
        }
        current_step = None
        if self.approval_instance is not None:
            current_step_id = self.approval_instance.current_step_id
            if current_step_id is not None:
                current_step = next(
                    (
                        step
                        for step in self.approval_instance.steps
                        if step.step_id == current_step_id
                    ),
                    None,
                )
        for action in self.allowed_actions:
            if action in compatible and status not in compatible[action]:
                raise ValueError(f"{action} is incompatible with request status")
            if action in {"approve", "return", "reject"} and not (
                current_step is not None
                and current_step.status == "open"
                and current_step.source_mode == "internal"
            ):
                raise ValueError(f"{action} requires an open internal approval step")
            if action == "register_external_approval" and not (
                current_step is not None
                and current_step.status == "awaiting_external_evidence"
                and current_step.source_mode == "external_registration"
            ):
                raise ValueError("external approval registration is not currently open")
            if action == "verify_external_approval" and not (
                current_step is not None
                and current_step.status == "evidence_pending_verification"
                and current_step.source_mode == "external_registration"
            ):
                raise ValueError("external approval verification is not currently open")


class MaterialRequestSummaryOut(_MaterialRequestCommonOut):
    line_count: int = Field(ge=1)


class MaterialRequestDetailOut(_MaterialRequestCommonOut):
    schema_version: Literal["1.0"] = "1.0"
    lines: tuple[MaterialRequestLineOut, ...] = Field(min_length=1, max_length=200)
    revision_history: tuple[MaterialRequestRevisionSummaryOut, ...] = Field(
        min_length=1
    )
    approval_history: tuple[MaterialRequestApprovalInstanceOut, ...]
    supply_tasks: tuple[MaterialRequestSupplyTaskOut, ...]

    @model_validator(mode="after")
    def validate_lines_and_supply_anchors(self):
        if len({line.request_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("request line ids cannot repeat")
        if len({line.line_no for line in self.lines}) != len(self.lines):
            raise ValueError("request line numbers cannot repeat")
        if any(
            line.revision_id != self.current_revision_id
            or line.revision_no != self.current_revision_no
            for line in self.lines
        ):
            raise ValueError("request line is not anchored to current revision")
        revisions = self.revision_history
        if tuple(item.revision_no for item in revisions) != tuple(
            range(1, len(revisions) + 1)
        ):
            raise ValueError("revision history must be complete and ordered")
        if len({item.revision_id for item in revisions}) != len(revisions):
            raise ValueError("revision history contains duplicate revisions")
        for index, revision in enumerate(revisions):
            expected_previous = None if index == 0 else revisions[index - 1].revision_id
            if revision.previous_revision_id != expected_previous:
                raise ValueError("revision history chain is broken")
            if index < len(revisions) - 1 and revision.status != "sealed":
                raise ValueError("only the current revision may remain a draft")
        current_revision = revisions[-1]
        if (
            current_revision.revision_id != self.current_revision_id
            or current_revision.revision_no != self.current_revision_no
        ):
            raise ValueError("current revision coordinate is not the history head")
        if current_revision.line_count != len(self.lines):
            raise ValueError("current revision line count disagrees with detail")
        if current_revision.attachment_count != len(self.attachment_refs):
            raise ValueError("current revision attachment count disagrees with detail")
        instances = self.approval_history
        instance = self.approval_instance
        if (instance is None) != (not instances):
            raise ValueError("approval shortcut and complete history disagree")
        if instance is not None and instances[-1] != instance:
            raise ValueError("approval shortcut is not the latest history instance")
        if tuple(item.attempt_no for item in instances) != tuple(
            range(1, len(instances) + 1)
        ):
            raise ValueError("approval instance history must be complete and ordered")
        if len({item.instance_id for item in instances}) != len(instances):
            raise ValueError("approval instance history contains duplicate instances")
        revision_numbers = tuple(item.revision_no for item in instances)
        if revision_numbers != tuple(sorted(set(revision_numbers))):
            raise ValueError("approval instance revisions must increase strictly")
        if any(item.status == "active" for item in instances[:-1]):
            raise ValueError("only the latest approval instance may remain active")
        for historical_instance in instances:
            instance_revision = next(
                (
                    item
                    for item in revisions
                    if item.revision_id == historical_instance.request_revision_id
                ),
                None,
            )
            if (
                instance_revision is None
                or instance_revision.revision_no != historical_instance.revision_no
                or instance_revision.status != "sealed"
            ):
                raise ValueError("approval instance is not anchored to a sealed revision")
            visible_registration_ids = (
                None
                if historical_instance.external_evidence_summaries is None
                else {
                    item.registration_id
                    for item in historical_instance.external_evidence_summaries
                }
            )
            for step in historical_instance.steps:
                for decision in step.line_decisions:
                    if (
                        decision.request_revision_id
                        != historical_instance.request_revision_id
                        or decision.revision_no != historical_instance.revision_no
                    ):
                        raise ValueError(
                            "approval decision is not anchored to the instance revision"
                        )
                    if (
                        visible_registration_ids is not None
                        and decision.external_registration_id is not None
                        and decision.external_registration_id
                        not in visible_registration_ids
                    ):
                        raise ValueError(
                            "external decision lacks its visible evidence summary"
                        )
            if historical_instance.request_revision_id == self.current_revision_id:
                line_ids = {line.request_line_id for line in self.lines}
                if any(
                    decision.request_line_id not in line_ids
                    for step in historical_instance.steps
                    for decision in step.line_decisions
                ):
                    raise ValueError("approval decision is not anchored to a request line")
                if historical_instance.return_line_facts is not None and any(
                    fact.request_line_id not in line_ids
                    for fact in historical_instance.return_line_facts
                ):
                    raise ValueError("return fact is not anchored to a request line")
        if len({task.id for task in self.supply_tasks}) != len(self.supply_tasks):
            raise ValueError("supply task ids cannot repeat")
        if len({task.task_no for task in self.supply_tasks}) != len(self.supply_tasks):
            raise ValueError("supply task numbers cannot repeat")
        line_ids = {line.request_line_id for line in self.lines}
        if any(task.request_line_id not in line_ids for task in self.supply_tasks):
            raise ValueError("supply task is not anchored to a request line")
        return self


class MaterialRequestPageOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[MaterialRequestSummaryOut, ...]
    next_after_id: UUID | None

    @model_validator(mode="after")
    def validate_page_anchors(self):
        item_ids = tuple(item.request_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("material request page contains duplicate requests")
        if self.next_after_id in set(item_ids):
            raise ValueError("next cursor cannot point into the current page")
        return self


class MaterialRequestMutationOut(_StrictOutputModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    action: Literal[
        "update",
        "submit",
        "withdraw",
        "cancel",
        "approve",
        "return",
        "reject",
        "register_external_approval",
        "verify_external_approval",
        "propose_substitution",
        "confirm_substitution",
        "reject_substitution",
        "create_supply_task",
        "update_supply_task",
        "cancel_supply_task",
    ]
    request_version: int = Field(ge=0)
    revision_id: UUID
    revision_no: int = Field(ge=1)
    approval_instance_id: UUID | None
    approval_attempt_no: int | None = Field(ge=1)
    current_step_id: UUID | None
    states: MaterialRequestStateAxesOut
    idempotency_replayed: bool

    @model_validator(mode="after")
    def validate_approval_anchor(self):
        if (self.approval_instance_id is None) != (self.approval_attempt_no is None):
            raise ValueError("approval instance and attempt anchors must appear together")
        approval_actions = {
            "submit",
            "withdraw",
            "cancel",
            "approve",
            "return",
            "reject",
            "register_external_approval",
            "verify_external_approval",
        }
        if self.action in approval_actions and self.approval_instance_id is None:
            raise ValueError("approval mutation requires an approval instance anchor")
        if self.action == "submit" and self.current_step_id is None:
            raise ValueError("submit mutation requires the opened current step")
        if self.action == "withdraw" and self.states.request_status != "withdrawn":
            raise ValueError("withdraw mutation requires the withdrawn request state")
        if self.action == "cancel" and self.states.request_status != "cancelled":
            raise ValueError("cancel mutation requires the cancelled request state")
        if self.action in {"withdraw", "cancel"} and self.current_step_id is not None:
            raise ValueError("terminal lifecycle mutation cannot expose a current step")
        if self.approval_instance_id is None and self.current_step_id is not None:
            raise ValueError("current approval step requires an approval instance")
        return self


class MaterialRequestCreateOut(_StrictOutputModel):
    """Create is separate because no server request id exists beforehand."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    action: Literal["create"] = "create"
    request_version: Literal[0] = 0
    revision_id: UUID
    revision_no: Literal[1] = 1
    states: MaterialRequestStateAxesOut
    idempotency_replayed: bool

    @model_validator(mode="after")
    def validate_new_draft_state(self):
        if self.states.request_status != "draft":
            raise ValueError("new material request must remain a draft")
        neutral = {
            "allocation_status": "not_allocated",
            "reservation_status": "not_reserved",
            "outbound_status": "not_started",
            "shipment_status": "not_started",
            "logistics_signature_status": "not_signed",
            "oam_receipt_status": "not_occurred",
            "personal_inbound_status": "not_started",
            "notification_status": "not_started",
            "reconciliation_status": "not_started",
        }
        if any(getattr(self.states, key) != value for key, value in neutral.items()):
            raise ValueError("new material request cannot advance a fulfilment axis")
        return self


__all__ = [
    "MaterialRequestAddressSnapshotOut",
    "MaterialRequestAllowedAction",
    "MaterialRequestApprovalAssigneeSnapshotOut",
    "MaterialRequestApprovalCandidatePoolSummaryOut",
    "MaterialRequestApprovalInstanceOut",
    "MaterialRequestApprovalLineDecisionOut",
    "MaterialRequestApprovalStepOut",
    "MaterialRequestAttachmentRefOut",
    "MaterialRequestContactMaskedOut",
    "MaterialRequestCreateOut",
    "MaterialRequestDetailOut",
    "MaterialRequestLineOut",
    "MaterialRequestMutationOut",
    "MaterialRequestPageOut",
    "MaterialRequestExternalEvidenceSummaryOut",
    "MaterialRequestReturnLineFactOut",
    "MaterialRequestRevisionSummaryOut",
    "MaterialRequestStatus",
    "MaterialRequestSummaryOut",
    "MaterialRequestSupplyTaskOut",
    "PositiveQuantityOut",
    "QuantityOut",
    "SupplyTaskAllowedAction",
]
