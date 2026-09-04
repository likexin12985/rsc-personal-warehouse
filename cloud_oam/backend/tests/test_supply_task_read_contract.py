"""A supply acknowledgement carries exact plan anchors, never fulfilment."""

from copy import deepcopy
import uuid

import pytest
from pydantic import ValidationError

from app.material_request_read_schemas import MaterialRequestSupplyTaskMutationOut


def _result():
    return {
        "schema_version": "1.0", "request_id": str(uuid.uuid4()),
        "action": "create_supply_task", "request_version": 8,
        "revision_id": str(uuid.uuid4()), "revision_no": 1,
        "approval_instance_id": str(uuid.uuid4()), "approval_attempt_no": 1,
        "current_step_id": None, "idempotency_replayed": False,
        "supply_task_id": str(uuid.uuid4()), "task_no": "SUP-TEST-001",
        "task_status": "open", "task_version": 0,
        "states": {
            "request_status": "approved", "allocation_status": "not_allocated",
            "reservation_status": "not_reserved", "outbound_status": "not_started",
            "shipment_status": "not_started", "logistics_signature_status": "not_signed",
            "oam_receipt_status": "not_occurred", "personal_inbound_status": "not_started",
            "notification_status": "not_started", "reconciliation_status": "not_started",
        },
    }


def test_supply_acknowledgement_keeps_request_and_task_anchors_separate():
    body = _result()
    parsed = MaterialRequestSupplyTaskMutationOut.model_validate(body)
    assert parsed.request_id != parsed.supply_task_id
    assert parsed.request_version == 8
    assert parsed.task_version == 0
    assert parsed.states.personal_inbound_status == "not_started"
    for field in ("supply_task_id", "task_no", "task_status", "task_version"):
        missing = deepcopy(body)
        del missing[field]
        with pytest.raises(ValidationError):
            MaterialRequestSupplyTaskMutationOut.model_validate(missing)


@pytest.mark.parametrize("changes", [
    {"task_status": "fulfilled"},
    {"task_status": "shipped"},
    {"task_status": "cancelled"},
    {"action": "cancel_supply_task", "task_status": "open"},
    {"action": "create_supply_task", "task_status": "closed_no_supply"},
    {"approval_instance_id": None, "approval_attempt_no": None},
    {"current_step_id": str(uuid.uuid4())},
])
def test_supply_acknowledgement_rejects_inconsistent_facts(changes):
    with pytest.raises(ValidationError):
        MaterialRequestSupplyTaskMutationOut.model_validate({**_result(), **changes})


def test_supply_acknowledgement_requires_final_approval():
    body = _result()
    body["states"]["request_status"] = "approval_in_progress"
    with pytest.raises(ValidationError):
        MaterialRequestSupplyTaskMutationOut.model_validate(body)
