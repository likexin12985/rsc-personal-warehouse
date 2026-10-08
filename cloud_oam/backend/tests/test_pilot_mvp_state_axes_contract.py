"""The frozen pilot MVP keeps each fulfillment fact on its own state axis."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.demand_schemas import MaterialRequestStateAxesOut


def _neutral() -> dict[str, str]:
    return {
        "request_status": "submitted",
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


def test_frozen_mvp_sequence_advances_only_the_relevant_axis() -> None:
    states = _neutral()

    approved = {**states, "request_status": "approved"}
    allocated = {**approved, "allocation_status": "allocated"}
    reserved = {**allocated, "reservation_status": "reserved"}
    outbound = {**reserved, "outbound_status": "outbound"}
    shipped = {
        **outbound,
        "shipment_status": "shipped",
        "personal_inbound_status": "pending_acceptance",
    }
    received = {**shipped, "personal_inbound_status": "accepted"}
    posted = {**received, "personal_inbound_status": "posted"}

    snapshots = tuple(MaterialRequestStateAxesOut.model_validate(item) for item in (
        states,
        approved,
        allocated,
        reserved,
        outbound,
        shipped,
        received,
        posted,
    ))
    assert snapshots[1].request_status == "approved"
    assert snapshots[2].allocation_status == "allocated"
    assert snapshots[3].reservation_status == "reserved"
    assert snapshots[4].outbound_status == "outbound"
    assert snapshots[5].shipment_status == "shipped"
    assert snapshots[5].personal_inbound_status == "pending_acceptance"
    assert snapshots[6].shipment_status == "shipped"
    assert snapshots[6].personal_inbound_status == "accepted"
    assert snapshots[7].personal_inbound_status == "posted"
    assert snapshots[7].notification_status == "not_started"
    assert snapshots[7].oam_receipt_status == "not_occurred"

    for before, after, changed in zip(
        snapshots,
        snapshots[1:],
        (
            {"request_status"},
            {"allocation_status"},
            {"reservation_status"},
            {"outbound_status"},
            {"shipment_status", "personal_inbound_status"},
            {"personal_inbound_status"},
            {"personal_inbound_status"},
        ),
    ):
        before_values = before.model_dump()
        after_values = after.model_dump()
        assert {key for key in before_values if before_values[key] != after_values[key]} == changed


@pytest.mark.parametrize("mutation", [
    lambda value: value.pop("shipment_status"),
    lambda value: value.update({"personal_inbound_status": "complete"}),
])
def test_state_axes_reject_missing_or_unreviewed_values(mutation) -> None:
    value = deepcopy(_neutral())
    mutation(value)
    with pytest.raises(ValidationError):
        MaterialRequestStateAxesOut.model_validate(value)
