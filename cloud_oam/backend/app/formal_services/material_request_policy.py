"""Pure V1.0 material-request policy checks.

The functions in this module do not read or write a database.  Transactional
services must first lock and reload their principals, request, approval steps
and reference rows, then pass the resulting exact snapshots through these
checks before persisting any transition.

Keeping these invariants pure makes it difficult for the PC, mini program or
an external-evidence adapter to accidentally obtain different approval rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Mapping, Sequence
import uuid


MAX_QUANTITY = Decimal("1000000000000000")
QUANTITY_QUANTUM = Decimal("0.001")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

NON_APPROVAL_STATE_AXES = (
    "allocation_status",
    "reservation_status",
    "outbound_status",
    "shipment_status",
    "logistics_signature_status",
    "oam_receipt_status",
    "personal_inbound_status",
    "notification_status",
    "reconciliation_status",
)

REQUEST_STATUS_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "draft": frozenset({"submitted", "cancelled"}),
    "submitted": frozenset({"approval_in_progress", "withdrawn"}),
    "approval_in_progress": frozenset(
        {
            "returned",
            "partially_approved",
            "approved",
            "rejected",
            "withdrawn",
        }
    ),
    "returned": frozenset({"submitted", "withdrawn", "cancelled"}),
    "partially_approved": frozenset({"cancellation_pending", "cancelled"}),
    "approved": frozenset({"cancellation_pending", "cancelled"}),
    "cancellation_pending": frozenset({"cancelled"}),
    "rejected": frozenset(),
    "withdrawn": frozenset(),
    "cancelled": frozenset(),
}


class MaterialRequestPolicyError(ValueError):
    """Stable business-policy failure without database details."""

    def __init__(self, code: str, category: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message


@dataclass(frozen=True, slots=True)
class ApprovalCandidateSnapshot:
    user_id: str
    person_id: uuid.UUID
    role_assignment_id: uuid.UUID
    role_code: str
    scope_type: str
    scope_id: str
    authorization_version: int
    authorization_sha256: str


@dataclass(frozen=True, slots=True)
class ApprovalLineInput:
    request_line_id: uuid.UUID
    input_qty: Decimal


@dataclass(frozen=True, slots=True)
class ApprovalLineDecision:
    request_line_id: uuid.UUID
    approved_qty: Decimal
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalLineOutcome:
    request_line_id: uuid.UUID
    input_qty: Decimal
    approved_qty: Decimal
    rejected_qty: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class FinalApprovalQuantity:
    """Final quantity reconstructed from the complete immutable step chain."""

    request_line_id: uuid.UUID
    requested_qty: Decimal
    approved_qty: Decimal


@dataclass(frozen=True, slots=True)
class ApprovalReturnInstruction:
    request_line_id: uuid.UUID
    required_review_qty: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class ApprovalReturnLineFact:
    request_line_id: uuid.UUID
    returned_step_input_qty: Decimal
    target_step_max_qty: Decimal
    required_review_qty: Decimal
    reason: str


def require_request_status_transition(from_status: str, to_status: str) -> None:
    allowed = REQUEST_STATUS_TRANSITIONS.get(from_status)
    if allowed is None or to_status not in allowed:
        _fail(
            "material_request_status_transition_invalid",
            "conflict",
            "需求单状态不允许执行该动作",
        )


def require_unique_regional_approver(
    candidates: Sequence[ApprovalCandidateSnapshot],
    *,
    requester_user_id: str,
    requester_person_id: uuid.UUID,
) -> ApprovalCandidateSnapshot:
    checked = _validated_candidates(
        candidates,
        expected_role_code="provincial_manager",
        expected_scope_type="organization",
    )
    if len(checked) == 0:
        _fail(
            "request_region_approver_unavailable",
            "precondition_failed",
            "所属区域尚未配置当前有效的区域负责人",
        )
    if len(checked) > 1:
        _fail(
            "request_region_approver_ambiguous",
            "conflict",
            "所属区域配置了多名当前区域负责人，需管理员先明确路由",
        )
    candidate = checked[0]
    _require_not_requester(
        candidate.user_id,
        candidate.person_id,
        requester_user_id=requester_user_id,
        requester_person_id=requester_person_id,
    )
    return candidate


def require_external_registration_admin_pool(
    candidates: Sequence[ApprovalCandidateSnapshot],
    *,
    requester_user_id: str,
    requester_person_id: uuid.UUID,
) -> tuple[ApprovalCandidateSnapshot, ...]:
    checked = tuple(
        candidate
        for candidate in _validated_candidates(
            candidates,
            expected_role_code="admin",
            expected_scope_type="national",
        )
        if candidate.user_id != requester_user_id
        and candidate.person_id != requester_person_id
    )
    if len(checked) < 2:
        _fail(
            "request_external_registration_reviewer_pool_insufficient",
            "precondition_failed",
            "外部审批登记至少需要两名非申请人的当前总部管理员",
        )
    return checked


def validate_line_approval_decisions(
    inputs: Sequence[ApprovalLineInput],
    decisions: Sequence[ApprovalLineDecision],
) -> tuple[ApprovalLineOutcome, ...]:
    input_by_line = _unique_line_inputs(inputs)
    decision_by_line = _unique_line_decisions(decisions)
    if set(decision_by_line) != set(input_by_line):
        _fail(
            "material_request_approval_line_set_mismatch",
            "precondition_failed",
            "审批必须完整覆盖当前申请明细集合",
        )

    outcomes: list[ApprovalLineOutcome] = []
    for line_id in sorted(input_by_line, key=str):
        input_qty = input_by_line[line_id]
        decision = decision_by_line[line_id]
        approved_qty = _quantity(
            decision.approved_qty,
            field="approved_qty",
            positive=False,
        )
        if approved_qty > input_qty:
            _fail(
                "material_request_approval_quantity_exceeds_input",
                "precondition_failed",
                "本级批准数量不得超过本级输入数量",
            )
        reason = _reason(decision.reason)
        rejected_qty = input_qty - approved_qty
        if rejected_qty > 0 and not reason:
            _fail(
                "material_request_approval_reason_required",
                "invalid_request",
                "部分驳回或全部驳回必须逐行填写理由",
            )
        outcomes.append(
            ApprovalLineOutcome(
                request_line_id=line_id,
                input_qty=input_qty,
                approved_qty=approved_qty,
                rejected_qty=rejected_qty,
                reason=reason,
            )
        )
    return tuple(outcomes)


def next_step_inputs(
    previous_outcomes: Sequence[ApprovalLineOutcome],
) -> tuple[ApprovalLineInput, ...]:
    seen: set[uuid.UUID] = set()
    result: list[ApprovalLineInput] = []
    for outcome in sorted(previous_outcomes, key=lambda row: str(row.request_line_id)):
        if outcome.request_line_id in seen:
            _fail(
                "material_request_approval_previous_line_duplicate",
                "precondition_failed",
                "上一级审批明细存在重复行",
            )
        seen.add(outcome.request_line_id)
        approved_qty = _quantity(
            outcome.approved_qty,
            field="approved_qty",
            positive=False,
        )
        input_qty = _quantity(
            outcome.input_qty,
            field="input_qty",
            positive=True,
        )
        rejected_qty = _quantity(
            outcome.rejected_qty,
            field="rejected_qty",
            positive=False,
        )
        if approved_qty + rejected_qty != input_qty:
            _fail(
                "material_request_approval_previous_conservation_invalid",
                "precondition_failed",
                "上一级审批数量不守恒",
            )
        # A zero approval is terminal for that line. Persisted inputs are
        # strictly positive, while final state is rebuilt from the full chain.
        if approved_qty > 0:
            result.append(
                ApprovalLineInput(
                    request_line_id=outcome.request_line_id,
                    input_qty=approved_qty,
                )
            )
    return tuple(result)


def reconstruct_final_approval_quantities(
    *,
    requested_quantities: Mapping[uuid.UUID, Decimal],
    step_outcome_chain: Sequence[Sequence[ApprovalLineOutcome]],
) -> tuple[FinalApprovalQuantity, ...]:
    """Rebuild final quantities without resurrecting an earlier zero line.

    Every supplied step must cover exactly the positive outputs of the
    preceding step. Lines reduced to zero disappear from later persisted
    decisions but remain zero in this full-line projection.
    """

    if not requested_quantities:
        _fail(
            "material_request_lines_required",
            "precondition_failed",
            "需求单没有有效申请明细",
        )
    if not step_outcome_chain:
        _fail(
            "material_request_approval_chain_required",
            "precondition_failed",
            "最终审批结果缺少逐级审批事实",
        )

    requested: dict[uuid.UUID, Decimal] = {}
    current: dict[uuid.UUID, Decimal] = {}
    for raw_line_id, raw_quantity in requested_quantities.items():
        line_id = _line_id(raw_line_id)
        if line_id in requested:
            _fail(
                "material_request_requested_line_duplicate",
                "precondition_failed",
                "原申请明细存在重复行",
            )
        quantity = _quantity(
            raw_quantity,
            field="requested_qty",
            positive=True,
        )
        requested[line_id] = quantity
        current[line_id] = quantity

    for outcomes in step_outcome_chain:
        expected_ids = {
            line_id for line_id, quantity in current.items() if quantity > 0
        }
        outcome_by_line: dict[uuid.UUID, ApprovalLineOutcome] = {}
        for outcome in outcomes:
            line_id = _line_id(outcome.request_line_id)
            if line_id in outcome_by_line:
                _fail(
                    "material_request_approval_chain_line_duplicate",
                    "precondition_failed",
                    "逐级审批事实存在重复明细",
                )
            outcome_by_line[line_id] = outcome
        if set(outcome_by_line) != expected_ids:
            _fail(
                "material_request_approval_chain_line_set_mismatch",
                "precondition_failed",
                "本级审批事实未精确覆盖上一级正数批准明细",
            )
        for line_id in sorted(expected_ids, key=str):
            outcome = outcome_by_line[line_id]
            input_qty = _quantity(
                outcome.input_qty,
                field="input_qty",
                positive=True,
            )
            approved_qty = _quantity(
                outcome.approved_qty,
                field="approved_qty",
                positive=False,
            )
            rejected_qty = _quantity(
                outcome.rejected_qty,
                field="rejected_qty",
                positive=False,
            )
            if (
                input_qty != current[line_id]
                or approved_qty + rejected_qty != input_qty
            ):
                _fail(
                    "material_request_approval_chain_quantity_invalid",
                    "precondition_failed",
                    "逐级审批数量与上一级批准事实不守恒",
                )
            if rejected_qty > 0 and not _reason(outcome.reason):
                _fail(
                    "material_request_approval_reason_required",
                    "precondition_failed",
                    "部分驳回或全部驳回必须逐行填写理由",
                )
            current[line_id] = approved_qty

    return tuple(
        FinalApprovalQuantity(
            request_line_id=line_id,
            requested_qty=requested[line_id],
            approved_qty=current[line_id],
        )
        for line_id in sorted(requested, key=str)
    )


def final_request_approval_status_from_chain(
    *,
    requested_quantities: Mapping[uuid.UUID, Decimal],
    step_outcome_chain: Sequence[Sequence[ApprovalLineOutcome]],
) -> str:
    quantities = reconstruct_final_approval_quantities(
        requested_quantities=requested_quantities,
        step_outcome_chain=step_outcome_chain,
    )
    requested_total = sum(
        (row.requested_qty for row in quantities),
        Decimal("0.000"),
    )
    approved_total = sum(
        (row.approved_qty for row in quantities),
        Decimal("0.000"),
    )
    if approved_total == 0:
        return "rejected"
    if approved_total == requested_total:
        return "approved"
    return "partially_approved"


def validate_return_reapproval_instructions(
    *,
    returned_step_inputs: Sequence[ApprovalLineInput],
    target_step_max_quantities: Mapping[uuid.UUID, Decimal],
    instructions: Sequence[ApprovalReturnInstruction],
) -> tuple[ApprovalReturnLineFact, ...]:
    """Validate the complete line facts used to reopen the preceding stage.

    ``returned_step_inputs`` are what the returning approver received.
    ``target_step_max_quantities`` are the quantities the reopened stage may
    approve without skipping its own predecessor. The instruction set is
    deliberately exact so a whole-document return cannot silently drop a
    positive line from the audit trail.
    """

    current = _unique_line_inputs(returned_step_inputs)
    maximums: dict[uuid.UUID, Decimal] = {}
    for raw_line_id, raw_quantity in target_step_max_quantities.items():
        line_id = _line_id(raw_line_id)
        if line_id in maximums:
            _fail(
                "material_request_return_target_line_duplicate",
                "invalid_request",
                "退回目标数量存在重复明细",
            )
        maximums[line_id] = _quantity(
            raw_quantity,
            field="target_step_max_qty",
            positive=True,
        )
    if set(maximums) != set(current):
        _fail(
            "material_request_return_target_line_set_mismatch",
            "precondition_failed",
            "退回目标上限未精确覆盖当前审批明细",
        )
    if any(current[line_id] > maximums[line_id] for line_id in current):
        _fail(
            "material_request_return_current_exceeds_target_max",
            "precondition_failed",
            "当前审批输入已超过可退回前级的批准上限",
        )

    by_line: dict[uuid.UUID, ApprovalReturnInstruction] = {}
    for instruction in instructions:
        line_id = _line_id(instruction.request_line_id)
        if line_id in by_line:
            _fail(
                "material_request_return_instruction_duplicate",
                "invalid_request",
                "退回重审指令不得重复明细",
            )
        by_line[line_id] = instruction
    if set(by_line) != set(current):
        _fail(
            "material_request_return_instruction_line_set_mismatch",
            "invalid_request",
            "退回重审指令必须覆盖当前全部正数审批明细",
        )

    facts: list[ApprovalReturnLineFact] = []
    for line_id in sorted(current, key=str):
        instruction = by_line[line_id]
        required_qty = _quantity(
            instruction.required_review_qty,
            field="required_review_qty",
            positive=True,
        )
        if required_qty > maximums[line_id]:
            _fail(
                "material_request_return_quantity_exceeds_target_max",
                "invalid_request",
                "要求重审数量不得超过前级可批准上限",
            )
        reason = _reason(instruction.reason)
        if not reason:
            _fail(
                "material_request_return_reason_required",
                "invalid_request",
                "退回重审必须逐行填写理由",
            )
        facts.append(
            ApprovalReturnLineFact(
                request_line_id=line_id,
                returned_step_input_qty=current[line_id],
                target_step_max_qty=maximums[line_id],
                required_review_qty=required_qty,
                reason=reason,
            )
        )
    return tuple(facts)


def final_request_approval_status(
    *,
    requested_quantities: Mapping[uuid.UUID, Decimal],
    final_outcomes: Sequence[ApprovalLineOutcome],
) -> str:
    if not requested_quantities:
        _fail(
            "material_request_lines_required",
            "precondition_failed",
            "需求单没有有效申请明细",
        )
    outcome_by_line = {row.request_line_id: row for row in final_outcomes}
    if len(outcome_by_line) != len(final_outcomes) or set(outcome_by_line) != set(
        requested_quantities
    ):
        _fail(
            "material_request_final_line_set_mismatch",
            "precondition_failed",
            "三级审批结果与原申请明细集合不一致",
        )

    approved_total = Decimal("0.000")
    requested_total = Decimal("0.000")
    for line_id, requested_value in requested_quantities.items():
        requested_qty = _quantity(
            requested_value,
            field="requested_qty",
            positive=True,
        )
        approved_qty = _quantity(
            outcome_by_line[line_id].approved_qty,
            field="approved_qty",
            positive=False,
        )
        if approved_qty > requested_qty:
            _fail(
                "material_request_final_quantity_exceeds_requested",
                "precondition_failed",
                "最终批准数量不得超过原申请数量",
            )
        requested_total += requested_qty
        approved_total += approved_qty
    if approved_total == 0:
        return "rejected"
    if approved_total == requested_total:
        return "approved"
    return "partially_approved"


def require_external_registration_review_separation(
    *,
    requester_user_id: str,
    requester_person_id: uuid.UUID,
    registrant_user_id: str,
    registrant_person_id: uuid.UUID,
    reviewer_user_id: str,
    reviewer_person_id: uuid.UUID,
) -> None:
    _require_not_requester(
        registrant_user_id,
        registrant_person_id,
        requester_user_id=requester_user_id,
        requester_person_id=requester_person_id,
    )
    _require_not_requester(
        reviewer_user_id,
        reviewer_person_id,
        requester_user_id=requester_user_id,
        requester_person_id=requester_person_id,
    )
    if (
        registrant_user_id == reviewer_user_id
        or registrant_person_id == reviewer_person_id
    ):
        _fail(
            "request_external_registration_self_review_forbidden",
            "forbidden",
            "外部审批登记必须由另一名总部管理员复核",
        )


def assert_approval_did_not_advance_fulfillment_axes(
    before: Mapping[str, str],
    after: Mapping[str, str],
) -> None:
    for axis in NON_APPROVAL_STATE_AXES:
        if axis not in before or axis not in after or before[axis] != after[axis]:
            _fail(
                "material_request_approval_changed_fulfillment_axis",
                "precondition_failed",
                "审批事务不得推进分配、占用、履约、通知或对账状态",
            )


def _validated_candidates(
    candidates: Sequence[ApprovalCandidateSnapshot],
    *,
    expected_role_code: str,
    expected_scope_type: str,
) -> tuple[ApprovalCandidateSnapshot, ...]:
    checked: list[ApprovalCandidateSnapshot] = []
    identities: set[tuple[str, uuid.UUID, uuid.UUID]] = set()
    for candidate in candidates:
        if (
            not candidate.user_id
            or not isinstance(candidate.person_id, uuid.UUID)
            or candidate.person_id.int == 0
            or not isinstance(candidate.role_assignment_id, uuid.UUID)
            or candidate.role_assignment_id.int == 0
            or candidate.role_code != expected_role_code
            or candidate.scope_type != expected_scope_type
            or not candidate.scope_id
            or not isinstance(candidate.authorization_version, int)
            or isinstance(candidate.authorization_version, bool)
            or candidate.authorization_version <= 0
            or _SHA256.fullmatch(candidate.authorization_sha256) is None
        ):
            _fail(
                "material_request_approval_candidate_invalid",
                "precondition_failed",
                "审批候选人快照无效",
            )
        identity = (
            candidate.user_id,
            candidate.person_id,
            candidate.role_assignment_id,
        )
        if identity in identities:
            _fail(
                "material_request_approval_candidate_duplicate",
                "precondition_failed",
                "审批候选人快照存在重复",
            )
        identities.add(identity)
        checked.append(candidate)
    return tuple(sorted(checked, key=lambda row: (row.user_id, str(row.person_id))))


def _unique_line_inputs(
    rows: Sequence[ApprovalLineInput],
) -> dict[uuid.UUID, Decimal]:
    result: dict[uuid.UUID, Decimal] = {}
    for row in rows:
        line_id = _line_id(row.request_line_id)
        if line_id in result:
            _fail(
                "material_request_approval_input_line_duplicate",
                "invalid_request",
                "审批输入明细不得重复",
            )
        result[line_id] = _quantity(
            row.input_qty,
            field="input_qty",
            positive=True,
        )
    if not result:
        _fail(
            "material_request_approval_inputs_required",
            "precondition_failed",
            "审批缺少有效输入明细",
        )
    return result


def _unique_line_decisions(
    rows: Sequence[ApprovalLineDecision],
) -> dict[uuid.UUID, ApprovalLineDecision]:
    result: dict[uuid.UUID, ApprovalLineDecision] = {}
    for row in rows:
        line_id = _line_id(row.request_line_id)
        if line_id in result:
            _fail(
                "material_request_approval_decision_line_duplicate",
                "invalid_request",
                "审批决定明细不得重复",
            )
        result[line_id] = row
    return result


def _line_id(value: uuid.UUID) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(
            "material_request_line_id_invalid",
            "invalid_request",
            "申请明细标识无效",
        )
    return value


def _quantity(value: Decimal, *, field: str, positive: bool) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value >= MAX_QUANTITY
        or value.as_tuple().exponent < -3
        or (positive and value <= 0)
        or (not positive and value < 0)
    ):
        _fail(
            f"material_request_{field}_invalid",
            "invalid_request",
            "数量必须是小于 10^15 且最多三位小数的有效十进制数",
        )
    return value.quantize(QUANTITY_QUANTUM)


def _reason(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 4000:
        _fail(
            "material_request_approval_reason_invalid",
            "invalid_request",
            "审批理由格式无效",
        )
    return value


def _require_not_requester(
    actor_user_id: str,
    actor_person_id: uuid.UUID,
    *,
    requester_user_id: str,
    requester_person_id: uuid.UUID,
) -> None:
    if (
        actor_user_id == requester_user_id
        or actor_person_id == requester_person_id
    ):
        _fail(
            "material_request_applicant_self_approval_forbidden",
            "forbidden",
            "申请人不得审批或复核自己的需求",
        )


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestPolicyError(code, category, message)


__all__ = [
    "ApprovalCandidateSnapshot",
    "ApprovalLineDecision",
    "ApprovalLineInput",
    "ApprovalLineOutcome",
    "ApprovalReturnInstruction",
    "ApprovalReturnLineFact",
    "FinalApprovalQuantity",
    "MaterialRequestPolicyError",
    "NON_APPROVAL_STATE_AXES",
    "REQUEST_STATUS_TRANSITIONS",
    "assert_approval_did_not_advance_fulfillment_axes",
    "final_request_approval_status",
    "final_request_approval_status_from_chain",
    "next_step_inputs",
    "reconstruct_final_approval_quantities",
    "require_external_registration_admin_pool",
    "require_external_registration_review_separation",
    "require_request_status_transition",
    "require_unique_regional_approver",
    "validate_return_reapproval_instructions",
    "validate_line_approval_decisions",
]
