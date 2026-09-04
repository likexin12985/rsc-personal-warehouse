from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest
from pydantic import ValidationError

from app.demand_schemas import (
    ExternalApprovalRegistrationIn,
    ExternalApprovalVerificationIn,
    MaterialRequestApprovalDecisionIn,
    MaterialRequestCreateIn,
    MaterialRequestStateAxesOut,
    SupplyTaskCreateIn,
    SupplyTaskUpdateIn,
)


MATERIAL_A = uuid.UUID("10000000-0000-4000-8000-000000000001")
MATERIAL_B = uuid.UUID("10000000-0000-4000-8000-000000000002")
LINE_A = uuid.UUID("20000000-0000-4000-8000-000000000001")
FILE_A = uuid.UUID("30000000-0000-4000-8000-000000000001")


def _draft_payload() -> dict[str, object]:
    return {
        "purpose": "现场故障处理",
        "urgency": "urgent",
        "expected_date": "2026-09-05",
        "address": {
            "province_code": "320000",
            "province_name": "江苏省",
            "city_name": "南京市",
            "district_name": "建邺区",
            "detail": "测试收货点 1 号",
        },
        "contact": {"name": "测试工程师", "mobile": "+86 13800000000"},
        "attachment_file_ids": [str(FILE_A)],
        "note": "仅为本地测试",
        "lines": [
            {
                "material_id": str(MATERIAL_A),
                "requested_qty": "2.500",
                "required_date": "2026-09-05",
            }
        ],
    }


def test_create_contract_owns_no_requester_region_or_status_input() -> None:
    parsed = MaterialRequestCreateIn.model_validate(_draft_payload())
    assert parsed.lines[0].requested_qty == Decimal("2.500")

    for forbidden in (
        "requester_user_id",
        "requester_person_id",
        "requester_org_id",
        "status",
        "approval_status",
    ):
        payload = _draft_payload()
        payload[forbidden] = "injected"
        with pytest.raises(ValidationError):
            MaterialRequestCreateIn.model_validate(payload)


@pytest.mark.parametrize(
    "quantity",
    [2, 2.5, "", "01", "1.", ".5", "1.0000", "1e2", "0", "1000000000000000"],
)
def test_requested_quantity_requires_positive_canonical_decimal_text(
    quantity: object,
) -> None:
    payload = _draft_payload()
    payload["lines"][0]["requested_qty"] = quantity
    with pytest.raises(ValidationError):
        MaterialRequestCreateIn.model_validate(payload)


def test_draft_rejects_duplicate_line_dimensions_and_attachment_ids() -> None:
    payload = _draft_payload()
    payload["lines"].append(dict(payload["lines"][0]))
    with pytest.raises(ValidationError):
        MaterialRequestCreateIn.model_validate(payload)

    payload = _draft_payload()
    payload["attachment_file_ids"] = [str(FILE_A), str(FILE_A)]
    with pytest.raises(ValidationError):
        MaterialRequestCreateIn.model_validate(payload)


def test_approval_contract_separates_decisions_from_return_instructions() -> None:
    approved = MaterialRequestApprovalDecisionIn.model_validate(
        {
            "expected_request_version": 3,
            "expected_step_version": 1,
            "action": "approve",
            "lines": [
                {
                    "request_line_id": str(LINE_A),
                    "approved_qty": "1.250",
                    "reason": "部分批准",
                }
            ],
        }
    )
    assert approved.lines[0].approved_qty == Decimal("1.250")

    returned = MaterialRequestApprovalDecisionIn.model_validate(
        {
            "expected_request_version": 3,
            "expected_step_version": 1,
            "action": "return",
            "return_lines": [
                {
                    "request_line_id": str(LINE_A),
                    "requested_reapproval_qty": "2.000",
                    "reason": "请前级重新核实数量",
                }
            ],
            "comment": "补充说明",
        }
    )
    assert returned.return_lines[0].requested_reapproval_qty == Decimal("2.000")

    with pytest.raises(ValidationError):
        MaterialRequestApprovalDecisionIn.model_validate(
            {
                "expected_request_version": 3,
                "expected_step_version": 1,
                "action": "return",
                "lines": [
                    {
                        "request_line_id": str(LINE_A),
                        "approved_qty": "1",
                    }
                ],
                "comment": "补充说明",
            }
        )
    with pytest.raises(ValidationError):
        MaterialRequestApprovalDecisionIn.model_validate(
            {
                "expected_request_version": 3,
                "expected_step_version": 1,
                "action": "return",
                "return_lines": [
                    {
                        "request_line_id": str(LINE_A),
                        "requested_reapproval_qty": "0.000",
                        "reason": "零数量应直接驳回",
                    }
                ],
                "comment": "补充说明",
            }
        )
    with pytest.raises(ValidationError):
        MaterialRequestApprovalDecisionIn.model_validate(
            {
                "expected_request_version": 3,
                "expected_step_version": 1,
                "action": "reject",
            }
        )


def test_external_registration_is_explicit_evidence_not_a_star_actor() -> None:
    parsed = ExternalApprovalRegistrationIn.model_validate(
        {
            "expected_request_version": 5,
            "expected_step_version": 0,
            "evidence_file_id": str(FILE_A),
            "external_approver_name": "星星总部审批人",
            "external_reference_no": "STAR-APPROVAL-2026-001",
            "external_decided_at": datetime(2026, 8, 31, tzinfo=timezone.utc).isoformat(),
            "action": "approve",
            "lines": [
                {
                    "request_line_id": str(LINE_A),
                    "approved_qty": "1.000",
                }
            ],
        }
    )
    dumped = parsed.model_dump()
    assert "actor_id" not in dumped
    assert "assignee_user_id" not in dumped
    assert parsed.external_approver_name == "星星总部审批人"

    with pytest.raises(ValidationError):
        ExternalApprovalRegistrationIn.model_validate(
            {
                **parsed.model_dump(mode="json"),
                "external_decided_at": "2026-08-31T12:00:00",
            }
        )

    returned = ExternalApprovalRegistrationIn.model_validate(
        {
            "expected_request_version": 5,
            "expected_step_version": 0,
            "evidence_file_id": str(FILE_A),
            "external_approver_name": "星星总部审批人",
            "external_reference_no": "STAR-RETURN-2026-001",
            "external_decided_at": datetime(
                2026, 8, 31, tzinfo=timezone.utc
            ).isoformat(),
            "action": "return",
            "return_lines": [
                {
                    "request_line_id": str(LINE_A),
                    "requested_reapproval_qty": "1.500",
                    "reason": "请蔚来总部复核数量",
                }
            ],
            "comment": "外部审批退回",
        }
    )
    assert returned.lines == ()


def test_external_verification_rejection_requires_reason() -> None:
    with pytest.raises(ValidationError):
        ExternalApprovalVerificationIn.model_validate(
            {
                "expected_request_version": 5,
                "expected_step_version": 0,
                "decision": "reject",
            }
        )


def test_supply_task_contract_contains_no_inventory_state_transition() -> None:
    parsed = SupplyTaskCreateIn.model_validate(
        {
            "expected_request_version": 7,
            "request_line_id": str(LINE_A),
            "supply_type": "star_replenishment",
            "reference_no": "STAR-SUPPLY-001",
            "expected_qty": "2.000",
            "expected_date": "2026-09-15",
        }
    )
    assert parsed.expected_qty == Decimal("2.000")
    dumped = parsed.model_dump()
    assert not {
        "allocation_status",
        "reservation_status",
        "shipment_status",
        "personal_inbound_status",
    }.intersection(dumped)


def test_supply_task_update_cannot_claim_fulfillment() -> None:
    with pytest.raises(ValidationError):
        SupplyTaskUpdateIn.model_validate(
            {
                "expected_request_version": 7,
                "expected_task_version": 0,
                "status": "fulfilled",
            }
        )

    closed = SupplyTaskUpdateIn.model_validate(
        {
            "expected_request_version": 7,
            "expected_task_version": 0,
            "status": "closed_no_supply",
            "comment": "需求方取消补货计划",
        }
    )
    assert closed.status == "closed_no_supply"


@pytest.mark.parametrize("bad_version", [True, "1", 1.5, -1])
def test_supply_task_versions_are_strict(bad_version) -> None:
    with pytest.raises(ValidationError):
        SupplyTaskCreateIn.model_validate({
            "expected_request_version": bad_version,
            "request_line_id": str(LINE_A),
            "supply_type": "star_replenishment",
            "expected_qty": "2.000",
        })
    for field in ("expected_request_version", "expected_task_version"):
        with pytest.raises(ValidationError):
            SupplyTaskUpdateIn.model_validate({
                "expected_request_version": 7, "expected_task_version": 0,
                "status": "open", field: bad_version,
            })


def test_supply_reference_matches_database_size_and_required_state() -> None:
    create = {
        "expected_request_version": 7, "request_line_id": str(LINE_A),
        "supply_type": "star_replenishment", "expected_qty": "2.000",
        "reference_no": "A" * 160,
    }
    assert len(SupplyTaskCreateIn.model_validate(create).reference_no) == 160
    with pytest.raises(ValidationError):
        SupplyTaskCreateIn.model_validate({**create, "reference_no": "A" * 161})
    update = {"expected_request_version": 7, "expected_task_version": 0,
              "status": "reference_registered", "reference_no": "A" * 160}
    assert len(SupplyTaskUpdateIn.model_validate(update).reference_no) == 160
    for reference in (None, "A" * 161, "", " REF "):
        with pytest.raises(ValidationError):
            SupplyTaskUpdateIn.model_validate({**update, "reference_no": reference})


def test_state_axes_require_ten_independent_explicit_values() -> None:
    axes = MaterialRequestStateAxesOut.model_validate(
        {
            "request_status": "approved",
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
    )
    assert axes.request_status == "approved"
    assert axes.allocation_status == "not_allocated"

    incomplete = axes.model_dump()
    del incomplete["logistics_signature_status"]
    with pytest.raises(ValidationError):
        MaterialRequestStateAxesOut.model_validate(incomplete)


def test_suggested_substitute_cannot_equal_requested_material() -> None:
    payload = _draft_payload()
    payload["lines"][0]["suggested_substitute_material_id"] = str(MATERIAL_A)
    with pytest.raises(ValidationError):
        MaterialRequestCreateIn.model_validate(payload)

    payload["lines"][0]["suggested_substitute_material_id"] = str(MATERIAL_B)
    parsed = MaterialRequestCreateIn.model_validate(payload)
    assert parsed.lines[0].suggested_substitute_material_id == MATERIAL_B
