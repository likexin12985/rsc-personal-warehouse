from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest
from pydantic import ValidationError

from app.material_request_read_schemas import (
    MaterialRequestCreateOut,
    MaterialRequestDetailOut,
    MaterialRequestMutationOut,
    MaterialRequestPageOut,
    MaterialRequestSummaryOut,
)


NOW = datetime(2026, 9, 1, 1, 2, 3, tzinfo=timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


def _axes(status: str = "draft") -> dict[str, str]:
    return {
        "request_status": status,
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


def _assignee(role_code: str) -> dict[str, str]:
    return {"name_masked": "审＊人", "role_code": role_code}


def _decision(
    *,
    step_id: str,
    revision_id: str,
    revision_no: int,
    line_id: str,
    source: str = "internal",
    registration_id: str | None = None,
    approved: str = "2.000",
    rejected: str = "0.000",
    reason: str = "",
) -> dict[str, object]:
    return {
        "decision_id": _uuid(),
        "step_id": step_id,
        "request_revision_id": revision_id,
        "revision_no": revision_no,
        "request_line_id": line_id,
        "input_qty": "2.000",
        "approved_qty": approved,
        "rejected_qty": rejected,
        "reason": reason,
        "decision_source": source,
        "external_registration_id": registration_id,
        "decided_at": NOW,
    }


def _step(
    *,
    step_id: str,
    step_no: int,
    status: str,
    source_mode: str,
    attempt_no: int = 1,
    predecessor_step_id: str | None = None,
    supersedes_step_id: str | None = None,
    reopened_from_step_id: str | None = None,
    decisions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    pending = status == "pending"
    decided = status in {"approved", "partially_approved", "rejected", "returned"}
    role = "provincial_manager" if step_no == 1 else "admin"
    fixed_assignee = source_mode == "internal" and step_no == 1
    candidate_pool = None
    if not fixed_assignee:
        candidate_pool = {
            "candidate_count": 4,
            "candidate_kinds": ["assignee"]
            if source_mode == "internal"
            else ["registrar", "verifier"],
        }
    return {
        "step_id": step_id,
        "step_no": step_no,
        "attempt_no": attempt_no,
        "predecessor_step_id": predecessor_step_id,
        "supersedes_step_id": supersedes_step_id,
        "reopened_from_step_id": reopened_from_step_id,
        "source_mode": source_mode,
        "status": status,
        "assignee_snapshot": _assignee(role) if fixed_assignee else None,
        "candidate_pool_summary": candidate_pool,
        "opened_at": None if pending else NOW,
        "decided_at": NOW if decided else None,
        "version": 1 if decided else 0,
        "line_decisions": decisions or [],
    }


def _detail(*, status: str = "draft") -> dict[str, object]:
    request_id = _uuid()
    revision_id = _uuid()
    line_id = _uuid()
    attachment_id = _uuid()
    terminal = status == "approved"
    approval_instance: dict[str, object] | None = None
    if terminal:
        step_1_id, step_2_id, step_3_id = _uuid(), _uuid(), _uuid()
        registration_id = _uuid()
        steps = [
            _step(
                step_id=step_1_id,
                step_no=1,
                status="approved",
                source_mode="internal",
                decisions=[
                    _decision(
                        step_id=step_1_id,
                        revision_id=revision_id,
                        revision_no=1,
                        line_id=line_id,
                    )
                ],
            ),
            _step(
                step_id=step_2_id,
                step_no=2,
                status="approved",
                source_mode="internal",
                predecessor_step_id=step_1_id,
                decisions=[
                    _decision(
                        step_id=step_2_id,
                        revision_id=revision_id,
                        revision_no=1,
                        line_id=line_id,
                    )
                ],
            ),
            _step(
                step_id=step_3_id,
                step_no=3,
                status="approved",
                source_mode="external_registration",
                predecessor_step_id=step_2_id,
                decisions=[
                    _decision(
                        step_id=step_3_id,
                        revision_id=revision_id,
                        revision_no=1,
                        line_id=line_id,
                        source="external_registration",
                        registration_id=registration_id,
                    )
                ],
            ),
        ]
        approval_instance = {
            "instance_id": _uuid(),
            "request_revision_id": revision_id,
            "revision_no": 1,
            "attempt_no": 1,
            "status": "completed",
            "current_step_no": None,
            "current_step_id": None,
            "version": 6,
            "steps": steps,
            "external_evidence_summaries": [
                {
                    "registration_id": registration_id,
                    "registration_no": "EXT-20260901-001",
                    "step_id": step_3_id,
                    "external_action": "approve",
                    "status": "accepted",
                    "evidence_file_id": _uuid(),
                    "external_approver_name_masked": "星＊审批人",
                    "external_decided_at": NOW,
                    "registered_at": NOW,
                    "verified_at": NOW,
                    "version": 1,
                }
            ],
            "return_line_facts": [],
        }
    return {
        "schema_version": "1.0",
        "request_id": request_id,
        "request_no": "MR202609010001",
        "request_version": 0 if status == "draft" else 7,
        "current_revision_id": revision_id,
        "current_revision_no": 1,
        "work_order_id": _uuid(),
        "requester_person_id": _uuid(),
        "requester_org_id": _uuid(),
        "purpose": "现场维修",
        "urgency": "normal",
        "expected_date": "2026-09-03",
        "address_snapshot": {
            "province_code": "320000",
            "province_name": "江苏省",
            "city_name": "南京市",
            "district_name": "建邺区",
            "detail_masked": "江东中路＊＊号",
        },
        "contact_masked": {
            "name_masked": "李＊",
            "mobile_masked": "138****1234",
        },
        "note": "",
        "attachment_refs": [
            {
                "revision_id": revision_id,
                "revision_no": 1,
                "request_line_id": None,
                "file_id": attachment_id,
                "display_name": "现场照片.jpg",
                "purpose": "request_attachment",
            }
        ],
        "approval_mode": "external_registration",
        "states": _axes(status),
        "approval_instance": approval_instance,
        "lines": [
            {
                "request_line_id": line_id,
                "revision_id": revision_id,
                "revision_no": 1,
                "line_no": 1,
                "material_id": _uuid(),
                "requested_qty": Decimal("2"),
                "required_date": "2026-09-03",
                "suggested_substitute_material_id": None,
                "note": "",
                "final_approved_qty": Decimal("2") if terminal else Decimal("0"),
                "cancelled_qty": Decimal("0"),
                "status": "approved" if terminal else "draft",
                "version": 0 if not terminal else 3,
            }
        ],
        "revision_history": [
            {
                "revision_id": revision_id,
                "revision_no": 1,
                "previous_revision_id": None,
                "status": "sealed" if terminal else "draft",
                "line_count": 1,
                "attachment_count": 1,
                "sealed_at": NOW if terminal else None,
                "created_at": NOW,
            }
        ],
        "approval_history": [] if approval_instance is None else [approval_instance],
        "supply_tasks": [],
        "allowed_actions": ["create_supply_task"]
        if terminal
        else ["update", "submit", "cancel"],
        "created_at": NOW,
        "updated_at": NOW,
        "submitted_at": NOW if terminal else None,
    }


def _reopened_detail() -> dict[str, object]:
    payload = _detail(status="approved")
    instance = payload["approval_instance"]
    assert isinstance(instance, dict)
    revision_id = payload["current_revision_id"]
    line_id = payload["lines"][0]["request_line_id"]  # type: ignore[index]
    step_1a_id, step_1b_id, step_2_id, step_3_id = (_uuid() for _ in range(4))
    step_1a = _step(
        step_id=step_1a_id,
        step_no=1,
        status="approved",
        source_mode="internal",
        decisions=[
            _decision(
                step_id=step_1a_id,
                revision_id=str(revision_id),
                revision_no=1,
                line_id=str(line_id),
            )
        ],
    )
    step_1b = _step(
        step_id=step_1b_id,
        step_no=1,
        attempt_no=2,
        status="open",
        source_mode="internal",
        supersedes_step_id=step_1a_id,
        reopened_from_step_id=step_2_id,
    )
    step_2 = _step(
        step_id=step_2_id,
        step_no=2,
        status="returned",
        source_mode="internal",
        predecessor_step_id=step_1a_id,
    )
    step_3 = _step(
        step_id=step_3_id,
        step_no=3,
        status="pending",
        source_mode="external_registration",
        predecessor_step_id=step_2_id,
    )
    instance.update(
        {
            "status": "active",
            "current_step_no": 1,
            "current_step_id": step_1b_id,
            "steps": [step_1a, step_1b, step_2, step_3],
            "external_evidence_summaries": None,
            "return_line_facts": [
                {
                    "return_fact_id": _uuid(),
                    "return_action_id": _uuid(),
                    "instance_id": instance["instance_id"],
                    "returned_from_step_id": step_2_id,
                    "target_kind": "approval_step",
                    "target_step_id": step_1b_id,
                    "request_revision_id": revision_id,
                    "revision_no": 1,
                    "request_line_id": line_id,
                    "returned_step_input_qty": "2.000",
                    "target_step_max_qty": "2.000",
                    "required_review_qty": "2.000",
                    "reason": "补充区域核验",
                    "occurred_at": NOW,
                }
            ],
        }
    )
    payload["states"] = _axes("approval_in_progress")
    payload["allowed_actions"] = ["approve", "return", "reject"]
    payload["lines"][0]["status"] = "approval_pending"  # type: ignore[index]
    return payload


def _two_instance_attempt_detail() -> dict[str, object]:
    payload = _detail(status="approved")
    previous = payload["approval_instance"]
    assert isinstance(previous, dict)
    revision_1_id = str(payload["current_revision_id"])
    line_id = str(payload["lines"][0]["request_line_id"])  # type: ignore[index]

    returned_step_id, cancelled_step_2_id, cancelled_step_3_id = (
        _uuid() for _ in range(3)
    )
    returned_step = _step(
        step_id=returned_step_id,
        step_no=1,
        status="returned",
        source_mode="internal",
    )
    cancelled_step_2 = _step(
        step_id=cancelled_step_2_id,
        step_no=2,
        status="cancelled",
        source_mode="internal",
        predecessor_step_id=returned_step_id,
    )
    cancelled_step_2["opened_at"] = None
    cancelled_step_3 = _step(
        step_id=cancelled_step_3_id,
        step_no=3,
        status="cancelled",
        source_mode="external_registration",
        predecessor_step_id=cancelled_step_2_id,
    )
    cancelled_step_3["opened_at"] = None
    previous.update(
        {
            "status": "superseded",
            "current_step_no": None,
            "current_step_id": None,
            "steps": [returned_step, cancelled_step_2, cancelled_step_3],
            "external_evidence_summaries": None,
            "return_line_facts": [
                {
                    "return_fact_id": _uuid(),
                    "return_action_id": _uuid(),
                    "instance_id": previous["instance_id"],
                    "returned_from_step_id": returned_step_id,
                    "target_kind": "requester_revision",
                    "target_step_id": None,
                    "request_revision_id": revision_1_id,
                    "revision_no": 1,
                    "request_line_id": line_id,
                    "returned_step_input_qty": "2.000",
                    "target_step_max_qty": "2.000",
                    "required_review_qty": "2.000",
                    "reason": "补充现场证据",
                    "occurred_at": NOW,
                }
            ],
        }
    )

    revision_2_id = _uuid()
    current_step_1_id, current_step_2_id, current_step_3_id = (
        _uuid() for _ in range(3)
    )
    current = {
        "instance_id": _uuid(),
        "request_revision_id": revision_2_id,
        "revision_no": 2,
        "attempt_no": 2,
        "status": "active",
        "current_step_no": 1,
        "current_step_id": current_step_1_id,
        "version": 0,
        "steps": [
            _step(
                step_id=current_step_1_id,
                step_no=1,
                status="open",
                source_mode="internal",
            ),
            _step(
                step_id=current_step_2_id,
                step_no=2,
                status="pending",
                source_mode="internal",
                predecessor_step_id=current_step_1_id,
            ),
            _step(
                step_id=current_step_3_id,
                step_no=3,
                status="pending",
                source_mode="external_registration",
                predecessor_step_id=current_step_2_id,
            ),
        ],
        "external_evidence_summaries": None,
        "return_line_facts": [],
    }
    payload["current_revision_id"] = revision_2_id
    payload["current_revision_no"] = 2
    payload["attachment_refs"][0]["revision_id"] = revision_2_id  # type: ignore[index]
    payload["attachment_refs"][0]["revision_no"] = 2  # type: ignore[index]
    payload["lines"][0]["revision_id"] = revision_2_id  # type: ignore[index]
    payload["lines"][0]["revision_no"] = 2  # type: ignore[index]
    payload["lines"][0]["final_approved_qty"] = "0.000"  # type: ignore[index]
    payload["lines"][0]["status"] = "approval_pending"  # type: ignore[index]
    payload["revision_history"].append(  # type: ignore[union-attr]
        {
            "revision_id": revision_2_id,
            "revision_no": 2,
            "previous_revision_id": revision_1_id,
            "status": "sealed",
            "line_count": 1,
            "attachment_count": 1,
            "sealed_at": NOW,
            "created_at": NOW,
        }
    )
    payload["approval_instance"] = current
    payload["approval_history"] = [previous, current]
    payload["states"] = _axes("approval_in_progress")
    payload["allowed_actions"] = ["approve", "return", "reject"]
    payload["request_version"] = 8
    return payload


def test_detail_serializes_revision_anchored_privacy_minimal_shape() -> None:
    payload = _detail(status="approved")
    result = MaterialRequestDetailOut.model_validate(payload).model_dump(mode="json")
    assert result["current_revision_id"] == payload["current_revision_id"]
    assert result["lines"][0]["revision_id"] == payload["current_revision_id"]
    assert result["attachment_refs"][0]["revision_id"] == payload["current_revision_id"]
    assert result["lines"][0]["requested_qty"] == "2.000"
    assert result["approval_instance"]["request_revision_id"] == payload["current_revision_id"]
    assert len(result["revision_history"]) == 1
    serialized = repr(result)
    for forbidden in (
        "ciphertext_b64",
        "nonce_b64",
        "permission_keys",
        "registered_by_user_id",
        "external_approver_snapshot_jsonb",
    ):
        assert forbidden not in serialized


def test_detail_preserves_complete_ordered_approval_instance_history() -> None:
    payload = _two_instance_attempt_detail()
    result = MaterialRequestDetailOut.model_validate(payload)
    assert [item.attempt_no for item in result.approval_history] == [1, 2]
    assert result.approval_history[0].status == "superseded"
    assert result.approval_history[0].steps[0].status == "returned"
    assert result.approval_history[0].return_line_facts
    assert result.approval_instance == result.approval_history[-1]
    assert result.approval_instance.current_step_id == result.approval_instance.steps[0].step_id

    missing_old_attempt = deepcopy(payload)
    missing_old_attempt["approval_history"] = missing_old_attempt["approval_history"][1:]
    with pytest.raises(ValidationError, match="complete and ordered"):
        MaterialRequestDetailOut.model_validate(missing_old_attempt)

    stale_shortcut = deepcopy(payload)
    stale_shortcut["approval_instance"] = deepcopy(stale_shortcut["approval_instance"])
    stale_shortcut["approval_instance"]["instance_id"] = _uuid()
    with pytest.raises(ValidationError, match="latest history"):
        MaterialRequestDetailOut.model_validate(stale_shortcut)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("contact_masked", "mobile_masked"), "13800138000"),
        (("address_snapshot", "detail_masked"), "江东中路100号"),
        (("approval_mode",), "direct_star"),
    ],
)
def test_detail_fails_closed_on_plaintext_or_unsupported_mode(path, value) -> None:
    payload = _detail()
    target = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(ValidationError):
        MaterialRequestDetailOut.model_validate(payload)


def test_historical_steps_use_current_step_id_not_step_number_position() -> None:
    payload = _reopened_detail()
    result = MaterialRequestDetailOut.model_validate(payload)
    assert len(result.approval_instance.steps) == 4  # type: ignore[union-attr]
    assert result.approval_instance.current_step_id == result.approval_instance.steps[1].step_id  # type: ignore[union-attr]

    drift = deepcopy(payload)
    drift["approval_instance"]["current_step_id"] = drift["approval_instance"]["steps"][0]["step_id"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="current approval pointer"):
        MaterialRequestDetailOut.model_validate(drift)


def test_never_opened_cancelled_step_does_not_fabricate_action_times() -> None:
    payload = _detail(status="approved")
    step = payload["approval_instance"]["steps"][2]  # type: ignore[index]
    step["status"] = "cancelled"
    step["opened_at"] = None
    step["decided_at"] = None
    step["line_decisions"] = []
    payload["approval_instance"]["external_evidence_summaries"] = []  # type: ignore[index]
    assert MaterialRequestDetailOut.model_validate(payload).approval_instance is not None

    fabricated = deepcopy(payload)
    fabricated["approval_instance"]["steps"][2]["decided_at"] = NOW  # type: ignore[index]
    with pytest.raises(ValidationError, match="decision time disagree"):
        MaterialRequestDetailOut.model_validate(fabricated)


def test_open_headquarters_candidate_pool_need_not_fabricate_assignee() -> None:
    payload = _detail(status="approved")
    step_2 = payload["approval_instance"]["steps"][1]  # type: ignore[index]
    step_2["status"] = "open"
    step_2["opened_at"] = NOW
    step_2["decided_at"] = None
    step_2["line_decisions"] = []
    step_2["assignee_snapshot"] = None
    step_2["candidate_pool_summary"] = {
        "candidate_count": 4,
        "candidate_kinds": ["assignee"],
    }
    payload["approval_instance"]["current_step_no"] = 2  # type: ignore[index]
    payload["approval_instance"]["current_step_id"] = step_2["step_id"]  # type: ignore[index]
    payload["approval_instance"]["status"] = "active"  # type: ignore[index]
    payload["approval_instance"]["external_evidence_summaries"] = []  # type: ignore[index]
    payload["approval_instance"]["steps"][2]["status"] = "pending"  # type: ignore[index]
    payload["approval_instance"]["steps"][2]["opened_at"] = None  # type: ignore[index]
    payload["approval_instance"]["steps"][2]["decided_at"] = None  # type: ignore[index]
    payload["approval_instance"]["steps"][2]["line_decisions"] = []  # type: ignore[index]
    payload["states"] = _axes("approval_in_progress")
    payload["allowed_actions"] = ["approve", "return", "reject"]
    payload["lines"][0]["status"] = "approval_pending"  # type: ignore[index]
    assert MaterialRequestDetailOut.model_validate(payload).approval_instance is not None


def test_rejects_broken_step_attempt_and_return_causality() -> None:
    payload = _reopened_detail()
    payload["approval_instance"]["steps"][1]["supersedes_step_id"] = _uuid()  # type: ignore[index]
    with pytest.raises(ValidationError, match="supersedes chain"):
        MaterialRequestDetailOut.model_validate(payload)

    payload = _reopened_detail()
    payload["approval_instance"]["return_line_facts"][0]["target_step_id"] = payload["approval_instance"]["steps"][0]["step_id"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="causal reopened step"):
        MaterialRequestDetailOut.model_validate(payload)


def test_rejects_revision_line_attachment_and_history_anchor_drift() -> None:
    payload = _detail()
    payload["lines"][0]["revision_id"] = _uuid()  # type: ignore[index]
    with pytest.raises(ValidationError, match="current revision"):
        MaterialRequestDetailOut.model_validate(payload)

    payload = _detail()
    payload["attachment_refs"][0]["revision_no"] = 2  # type: ignore[index]
    with pytest.raises(ValidationError, match="current revision"):
        MaterialRequestDetailOut.model_validate(payload)

    payload = _detail()
    payload["revision_history"][0]["previous_revision_id"] = _uuid()  # type: ignore[index]
    with pytest.raises(ValidationError, match="chain"):
        MaterialRequestDetailOut.model_validate(payload)


def test_external_evidence_and_assignee_reject_raw_sensitive_fields() -> None:
    payload = _detail(status="approved")
    payload["approval_instance"]["steps"][0]["assignee_snapshot"]["permission_keys"] = ["*"]  # type: ignore[index]
    with pytest.raises(ValidationError):
        MaterialRequestDetailOut.model_validate(payload)

    payload = _detail(status="approved")
    payload["approval_instance"]["external_evidence_summaries"][0]["external_approver_name_masked"] = "张三"  # type: ignore[index]
    with pytest.raises(ValidationError, match="masked"):
        MaterialRequestDetailOut.model_validate(payload)

    payload = _detail(status="approved")
    payload["approval_instance"]["external_evidence_summaries"][0]["external_approver_snapshot_jsonb"] = {"mobile": "13800138000"}  # type: ignore[index]
    with pytest.raises(ValidationError):
        MaterialRequestDetailOut.model_validate(payload)


def test_decision_quantity_source_and_revision_anchors_fail_closed() -> None:
    payload = _detail(status="approved")
    decision = payload["approval_instance"]["steps"][0]["line_decisions"][0]  # type: ignore[index]
    decision["approved_qty"] = "1.000"
    with pytest.raises(ValidationError, match="conserve"):
        MaterialRequestDetailOut.model_validate(payload)

    payload = _detail(status="approved")
    decision = payload["approval_instance"]["steps"][2]["line_decisions"][0]  # type: ignore[index]
    decision["external_registration_id"] = None
    with pytest.raises(ValidationError, match="registration anchor"):
        MaterialRequestDetailOut.model_validate(payload)


def test_returned_request_can_expose_amend_and_resubmit_actions() -> None:
    payload = _detail(status="approved")
    payload["states"] = _axes("returned")
    instance = payload["approval_instance"]
    assert isinstance(instance, dict)
    returned_step = instance["steps"][0]  # type: ignore[index]
    returned_step["status"] = "returned"
    returned_step["line_decisions"] = []
    instance["steps"][1]["status"] = "cancelled"  # type: ignore[index]
    instance["steps"][1]["opened_at"] = None  # type: ignore[index]
    instance["steps"][1]["decided_at"] = None  # type: ignore[index]
    instance["steps"][1]["line_decisions"] = []  # type: ignore[index]
    instance["steps"][2]["status"] = "cancelled"  # type: ignore[index]
    instance["steps"][2]["opened_at"] = None  # type: ignore[index]
    instance["steps"][2]["decided_at"] = None  # type: ignore[index]
    instance["steps"][2]["line_decisions"] = []  # type: ignore[index]
    instance["external_evidence_summaries"] = None
    instance["return_line_facts"] = [
        {
            "return_fact_id": _uuid(),
            "return_action_id": _uuid(),
            "instance_id": instance["instance_id"],
            "returned_from_step_id": returned_step["step_id"],  # type: ignore[index]
            "target_kind": "requester_revision",
            "target_step_id": None,
            "request_revision_id": instance["request_revision_id"],
            "revision_no": 1,
            "request_line_id": payload["lines"][0]["request_line_id"],  # type: ignore[index]
            "returned_step_input_qty": "2.000",
            "target_step_max_qty": "2.000",
            "required_review_qty": "2.000",
            "reason": "补充现场证据",
            "occurred_at": NOW,
        }
    ]
    instance["status"] = "returned"
    payload["allowed_actions"] = ["update", "submit"]
    result = MaterialRequestDetailOut.model_validate(payload)
    assert result.allowed_actions == ("update", "submit")


def test_page_and_mutations_keep_exact_revision_and_state_contract() -> None:
    detail = _detail(status="approved")
    summary = deepcopy(detail)
    summary.pop("schema_version")
    summary.pop("lines")
    summary.pop("revision_history")
    summary.pop("approval_history")
    summary.pop("supply_tasks")
    summary["line_count"] = 1
    page = MaterialRequestPageOut.model_validate(
        {"schema_version": "1.0", "items": [summary], "next_after_id": None}
    )
    assert isinstance(page.items[0], MaterialRequestSummaryOut)

    instance = detail["approval_instance"]
    mutation = MaterialRequestMutationOut.model_validate(
        {
            "schema_version": "1.0",
            "request_id": detail["request_id"],
            "action": "submit",
            "request_version": 8,
            "revision_id": detail["current_revision_id"],
            "revision_no": 1,
            "approval_instance_id": instance["instance_id"],  # type: ignore[index]
            "approval_attempt_no": 1,
            "current_step_id": instance["steps"][0]["step_id"],  # type: ignore[index]
            "states": _axes("submitted"),
            "idempotency_replayed": False,
        }
    ).model_dump(mode="json")
    assert mutation["states"]["request_status"] == "submitted"
    assert mutation["revision_id"] == detail["current_revision_id"]

    missing = deepcopy(mutation)
    missing.pop("revision_id")
    with pytest.raises(ValidationError):
        MaterialRequestMutationOut.model_validate(missing)


def test_create_has_revision_one_and_neutral_draft_contract() -> None:
    result = MaterialRequestCreateOut.model_validate(
        {
            "schema_version": "1.0",
            "request_id": _uuid(),
            "action": "create",
            "request_version": 0,
            "revision_id": _uuid(),
            "revision_no": 1,
            "states": _axes("draft"),
            "idempotency_replayed": True,
        }
    ).model_dump(mode="json")
    assert result["action"] == "create"
    assert result["revision_no"] == 1

    advanced = deepcopy(result)
    advanced["states"]["shipment_status"] = "shipped"
    with pytest.raises(ValidationError, match="cannot advance"):
        MaterialRequestCreateOut.model_validate(advanced)

    wrong_revision = deepcopy(result)
    wrong_revision["revision_no"] = 2
    with pytest.raises(ValidationError):
        MaterialRequestCreateOut.model_validate(wrong_revision)
