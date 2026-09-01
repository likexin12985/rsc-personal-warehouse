from __future__ import annotations

from decimal import Decimal
import uuid

import pytest

from app.formal_services.material_request_policy import (
    ApprovalCandidateSnapshot,
    ApprovalLineDecision,
    ApprovalLineInput,
    ApprovalLineOutcome,
    ApprovalReturnInstruction,
    MaterialRequestPolicyError,
    assert_approval_did_not_advance_fulfillment_axes,
    final_request_approval_status,
    final_request_approval_status_from_chain,
    next_step_inputs,
    reconstruct_final_approval_quantities,
    require_external_registration_admin_pool,
    require_external_registration_review_separation,
    require_request_status_transition,
    require_unique_regional_approver,
    validate_return_reapproval_instructions,
    validate_line_approval_decisions,
)


REQUESTER_USER = "00000000-0000-4000-8000-000000000001"
OTHER_USER = "00000000-0000-4000-8000-000000000002"
THIRD_USER = "00000000-0000-4000-8000-000000000003"
REQUESTER_PERSON = uuid.UUID("10000000-0000-4000-8000-000000000001")
OTHER_PERSON = uuid.UUID("10000000-0000-4000-8000-000000000002")
THIRD_PERSON = uuid.UUID("10000000-0000-4000-8000-000000000003")
LINE_A = uuid.UUID("20000000-0000-4000-8000-000000000001")
LINE_B = uuid.UUID("20000000-0000-4000-8000-000000000002")


def _candidate(
    user_id: str,
    person_id: uuid.UUID,
    *,
    role_code: str,
    scope_type: str,
    sequence: int,
) -> ApprovalCandidateSnapshot:
    return ApprovalCandidateSnapshot(
        user_id=user_id,
        person_id=person_id,
        role_assignment_id=uuid.UUID(
            f"30000000-0000-4000-8000-{sequence:012d}"
        ),
        role_code=role_code,
        scope_type=scope_type,
        scope_id="*" if scope_type == "national" else "region-jiangsu",
        authorization_version=7,
        authorization_sha256=f"{sequence:064x}",
    )


def _error_code(exc_info: pytest.ExceptionInfo[MaterialRequestPolicyError]) -> str:
    return exc_info.value.code


def test_region_approver_must_be_exactly_one_and_not_requester() -> None:
    candidate = _candidate(
        OTHER_USER,
        OTHER_PERSON,
        role_code="provincial_manager",
        scope_type="organization",
        sequence=1,
    )
    assert require_unique_regional_approver(
        (candidate,),
        requester_user_id=REQUESTER_USER,
        requester_person_id=REQUESTER_PERSON,
    ) == candidate

    with pytest.raises(MaterialRequestPolicyError) as missing:
        require_unique_regional_approver(
            (),
            requester_user_id=REQUESTER_USER,
            requester_person_id=REQUESTER_PERSON,
        )
    assert _error_code(missing) == "request_region_approver_unavailable"

    with pytest.raises(MaterialRequestPolicyError) as ambiguous:
        require_unique_regional_approver(
            (
                candidate,
                _candidate(
                    THIRD_USER,
                    THIRD_PERSON,
                    role_code="provincial_manager",
                    scope_type="organization",
                    sequence=2,
                ),
            ),
            requester_user_id=REQUESTER_USER,
            requester_person_id=REQUESTER_PERSON,
        )
    assert _error_code(ambiguous) == "request_region_approver_ambiguous"

    requester_candidate = _candidate(
        REQUESTER_USER,
        REQUESTER_PERSON,
        role_code="provincial_manager",
        scope_type="organization",
        sequence=3,
    )
    with pytest.raises(MaterialRequestPolicyError) as self_approval:
        require_unique_regional_approver(
            (requester_candidate,),
            requester_user_id=REQUESTER_USER,
            requester_person_id=REQUESTER_PERSON,
        )
    assert _error_code(self_approval) == (
        "material_request_applicant_self_approval_forbidden"
    )


def test_external_registration_requires_two_non_requester_admins() -> None:
    first = _candidate(
        OTHER_USER,
        OTHER_PERSON,
        role_code="admin",
        scope_type="national",
        sequence=4,
    )
    second = _candidate(
        THIRD_USER,
        THIRD_PERSON,
        role_code="admin",
        scope_type="national",
        sequence=5,
    )
    assert require_external_registration_admin_pool(
        (first, second),
        requester_user_id=REQUESTER_USER,
        requester_person_id=REQUESTER_PERSON,
    ) == (first, second)

    with pytest.raises(MaterialRequestPolicyError) as insufficient:
        require_external_registration_admin_pool(
            (first,),
            requester_user_id=REQUESTER_USER,
            requester_person_id=REQUESTER_PERSON,
        )
    assert _error_code(insufficient) == (
        "request_external_registration_reviewer_pool_insufficient"
    )


def test_line_approval_is_exact_and_conserves_quantities() -> None:
    outcomes = validate_line_approval_decisions(
        (
            ApprovalLineInput(LINE_A, Decimal("3.000")),
            ApprovalLineInput(LINE_B, Decimal("2.500")),
        ),
        (
            ApprovalLineDecision(LINE_B, Decimal("1.500"), "部分批准"),
            ApprovalLineDecision(LINE_A, Decimal("3.000")),
        ),
    )
    assert tuple(row.request_line_id for row in outcomes) == (LINE_A, LINE_B)
    assert outcomes[0].rejected_qty == Decimal("0.000")
    assert outcomes[1].rejected_qty == Decimal("1.000")

    next_inputs = next_step_inputs(outcomes)
    assert next_inputs == (
        ApprovalLineInput(LINE_A, Decimal("3.000")),
        ApprovalLineInput(LINE_B, Decimal("1.500")),
    )


def test_zero_approved_line_is_terminal_and_not_sent_to_next_step() -> None:
    outcomes = validate_line_approval_decisions(
        (
            ApprovalLineInput(LINE_A, Decimal("3.000")),
            ApprovalLineInput(LINE_B, Decimal("2.500")),
        ),
        (
            ApprovalLineDecision(LINE_A, Decimal("3.000")),
            ApprovalLineDecision(LINE_B, Decimal("0.000"), "本级整行驳回"),
        ),
    )

    assert next_step_inputs(outcomes) == (
        ApprovalLineInput(LINE_A, Decimal("3.000")),
    )


def test_final_quantities_reconstruct_zero_lines_from_full_chain() -> None:
    first = validate_line_approval_decisions(
        (
            ApprovalLineInput(LINE_A, Decimal("3.000")),
            ApprovalLineInput(LINE_B, Decimal("2.000")),
        ),
        (
            ApprovalLineDecision(LINE_A, Decimal("2.000"), "一级部分批准"),
            ApprovalLineDecision(LINE_B, Decimal("0.000"), "一级整行驳回"),
        ),
    )
    second = validate_line_approval_decisions(
        next_step_inputs(first),
        (ApprovalLineDecision(LINE_A, Decimal("1.000"), "二级部分批准"),),
    )
    third = validate_line_approval_decisions(
        next_step_inputs(second),
        (ApprovalLineDecision(LINE_A, Decimal("1.000")),),
    )

    quantities = reconstruct_final_approval_quantities(
        requested_quantities={
            LINE_A: Decimal("3.000"),
            LINE_B: Decimal("2.000"),
        },
        step_outcome_chain=(first, second, third),
    )
    assert tuple((row.request_line_id, row.approved_qty) for row in quantities) == (
        (LINE_A, Decimal("1.000")),
        (LINE_B, Decimal("0.000")),
    )
    assert final_request_approval_status_from_chain(
        requested_quantities={
            LINE_A: Decimal("3.000"),
            LINE_B: Decimal("2.000"),
        },
        step_outcome_chain=(first, second, third),
    ) == "partially_approved"


def test_all_lines_rejected_to_zero_can_finish_without_empty_next_step() -> None:
    first = validate_line_approval_decisions(
        (ApprovalLineInput(LINE_A, Decimal("3.000")),),
        (ApprovalLineDecision(LINE_A, Decimal("0.000"), "全部驳回"),),
    )
    assert next_step_inputs(first) == ()
    assert final_request_approval_status_from_chain(
        requested_quantities={LINE_A: Decimal("3.000")},
        step_outcome_chain=(first,),
    ) == "rejected"


def test_final_chain_cannot_drop_or_resurrect_a_line() -> None:
    first = validate_line_approval_decisions(
        (
            ApprovalLineInput(LINE_A, Decimal("3.000")),
            ApprovalLineInput(LINE_B, Decimal("2.000")),
        ),
        (
            ApprovalLineDecision(LINE_A, Decimal("2.000"), "一级部分批准"),
            ApprovalLineDecision(LINE_B, Decimal("0.000"), "一级整行驳回"),
        ),
    )
    invalid_second = (
        ApprovalLineOutcome(
            request_line_id=LINE_B,
            input_qty=Decimal("2.000"),
            approved_qty=Decimal("1.000"),
            rejected_qty=Decimal("1.000"),
            reason="非法复活",
        ),
    )

    with pytest.raises(MaterialRequestPolicyError) as failure:
        reconstruct_final_approval_quantities(
            requested_quantities={
                LINE_A: Decimal("3.000"),
                LINE_B: Decimal("2.000"),
            },
            step_outcome_chain=(first, invalid_second),
        )
    assert _error_code(failure) == "material_request_approval_chain_line_set_mismatch"


def test_return_instructions_preserve_current_and_target_quantity_evidence() -> None:
    facts = validate_return_reapproval_instructions(
        returned_step_inputs=(
            ApprovalLineInput(LINE_A, Decimal("3.000")),
            ApprovalLineInput(LINE_B, Decimal("1.000")),
        ),
        target_step_max_quantities={
            LINE_A: Decimal("5.000"),
            LINE_B: Decimal("2.000"),
        },
        instructions=(
            ApprovalReturnInstruction(LINE_B, Decimal("2.000"), "恢复前级上限"),
            ApprovalReturnInstruction(LINE_A, Decimal("4.000"), "重新核实数量"),
        ),
    )

    assert tuple(row.request_line_id for row in facts) == (LINE_A, LINE_B)
    assert facts[0].returned_step_input_qty == Decimal("3.000")
    assert facts[0].target_step_max_qty == Decimal("5.000")
    assert facts[0].required_review_qty == Decimal("4.000")


@pytest.mark.parametrize(
    ("instructions", "expected_code"),
    [
        (
            (ApprovalReturnInstruction(LINE_A, Decimal("4.000"), "缺少第二行"),),
            "material_request_return_instruction_line_set_mismatch",
        ),
        (
            (
                ApprovalReturnInstruction(LINE_A, Decimal("6.000"), "超过上限"),
                ApprovalReturnInstruction(LINE_B, Decimal("2.000"), "保持"),
            ),
            "material_request_return_quantity_exceeds_target_max",
        ),
    ],
)
def test_return_instructions_fail_closed(
    instructions: tuple[ApprovalReturnInstruction, ...],
    expected_code: str,
) -> None:
    with pytest.raises(MaterialRequestPolicyError) as failure:
        validate_return_reapproval_instructions(
            returned_step_inputs=(
                ApprovalLineInput(LINE_A, Decimal("3.000")),
                ApprovalLineInput(LINE_B, Decimal("1.000")),
            ),
            target_step_max_quantities={
                LINE_A: Decimal("5.000"),
                LINE_B: Decimal("2.000"),
            },
            instructions=instructions,
        )
    assert _error_code(failure) == expected_code


@pytest.mark.parametrize(
    ("decisions", "expected_code"),
    [
        (
            (ApprovalLineDecision(LINE_A, Decimal("1.000"), "部分批准"),),
            "material_request_approval_line_set_mismatch",
        ),
        (
            (
                ApprovalLineDecision(LINE_A, Decimal("4.000")),
                ApprovalLineDecision(LINE_B, Decimal("2.500")),
            ),
            "material_request_approval_quantity_exceeds_input",
        ),
        (
            (
                ApprovalLineDecision(LINE_A, Decimal("2.000")),
                ApprovalLineDecision(LINE_B, Decimal("2.500")),
            ),
            "material_request_approval_reason_required",
        ),
    ],
)
def test_invalid_line_approval_fails_closed(
    decisions: tuple[ApprovalLineDecision, ...],
    expected_code: str,
) -> None:
    inputs = (
        ApprovalLineInput(LINE_A, Decimal("3.000")),
        ApprovalLineInput(LINE_B, Decimal("2.500")),
    )
    with pytest.raises(MaterialRequestPolicyError) as failure:
        validate_line_approval_decisions(inputs, decisions)
    assert _error_code(failure) == expected_code


def test_final_status_uses_original_request_and_never_overwrites_it() -> None:
    outcomes = validate_line_approval_decisions(
        (
            ApprovalLineInput(LINE_A, Decimal("3.000")),
            ApprovalLineInput(LINE_B, Decimal("2.000")),
        ),
        (
            ApprovalLineDecision(LINE_A, Decimal("3.000")),
            ApprovalLineDecision(LINE_B, Decimal("1.000"), "部分批准"),
        ),
    )
    assert final_request_approval_status(
        requested_quantities={
            LINE_A: Decimal("3.000"),
            LINE_B: Decimal("2.000"),
        },
        final_outcomes=outcomes,
    ) == "partially_approved"


def test_external_registration_and_review_must_be_different_people() -> None:
    require_external_registration_review_separation(
        requester_user_id=REQUESTER_USER,
        requester_person_id=REQUESTER_PERSON,
        registrant_user_id=OTHER_USER,
        registrant_person_id=OTHER_PERSON,
        reviewer_user_id=THIRD_USER,
        reviewer_person_id=THIRD_PERSON,
    )
    with pytest.raises(MaterialRequestPolicyError) as self_review:
        require_external_registration_review_separation(
            requester_user_id=REQUESTER_USER,
            requester_person_id=REQUESTER_PERSON,
            registrant_user_id=OTHER_USER,
            registrant_person_id=OTHER_PERSON,
            reviewer_user_id=OTHER_USER,
            reviewer_person_id=OTHER_PERSON,
        )
    assert _error_code(self_review) == (
        "request_external_registration_self_review_forbidden"
    )


def test_approval_cannot_advance_any_nonapproval_axis() -> None:
    unchanged = {
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
    assert_approval_did_not_advance_fulfillment_axes(unchanged, dict(unchanged))

    changed = dict(unchanged)
    changed["reservation_status"] = "reserved"
    with pytest.raises(MaterialRequestPolicyError) as failure:
        assert_approval_did_not_advance_fulfillment_axes(unchanged, changed)
    assert _error_code(failure) == (
        "material_request_approval_changed_fulfillment_axis"
    )


def test_request_status_graph_does_not_equate_approval_with_fulfillment() -> None:
    require_request_status_transition("approval_in_progress", "approved")
    require_request_status_transition("approved", "cancellation_pending")
    with pytest.raises(MaterialRequestPolicyError):
        require_request_status_transition("approved", "approval_in_progress")
    with pytest.raises(MaterialRequestPolicyError):
        require_request_status_transition("approved", "shipped")
