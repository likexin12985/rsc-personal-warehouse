from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.formal_services.material_request_allocation import AllocationCreateInput
from app.formal_services.material_request_allocation import MaterialRequestAllocationError, create_allocation
from app.material_request_allocation_schemas import AllocationCreateIn
from app.material_request_allocation_schemas import AllocationMutationOut


def _id(value: int) -> UUID:
    return UUID(f"10000000-0000-4000-8000-{value:012d}")


def test_allocation_input_requires_canonical_positive_quantity_and_unique_serials():
    value = AllocationCreateIn(
        expected_request_version=3,
        request_line_id=_id(1),
        source_stock_account_id=_id(2),
        allocated_qty="1.500",
        source_balance_version=7,
        source_ledger_cursor=9,
        serial_ids=(_id(3),),
    )
    assert value.allocated_qty == Decimal("1.500")
    assert value.serial_ids == (_id(3),)
    with pytest.raises(ValidationError):
        AllocationCreateIn(
            expected_request_version=3, request_line_id=_id(1), source_stock_account_id=_id(2),
            allocated_qty="1e0", source_balance_version=7, source_ledger_cursor=9,
        )
    with pytest.raises(ValidationError):
        AllocationCreateIn(
            expected_request_version=3, request_line_id=_id(1), source_stock_account_id=_id(2),
            allocated_qty="0", source_balance_version=7, source_ledger_cursor=9,
        )
    with pytest.raises(ValidationError):
        AllocationCreateIn(
            expected_request_version=3, request_line_id=_id(1), source_stock_account_id=_id(2),
            allocated_qty="2", source_balance_version=7, source_ledger_cursor=9,
            serial_ids=(_id(3), _id(3)),
        )


def test_allocation_domain_input_is_write_payload_only():
    value = AllocationCreateInput(
        request_line_id=_id(1), source_stock_account_id=_id(2), allocated_qty=Decimal("1.000"),
        source_balance_version=7, source_ledger_cursor=9,
    )
    assert value.allocated_qty == Decimal("1.000")


def test_allocation_response_keeps_fixed_quantity_and_state_anchor():
    axes = {
        "request_status": "approved", "allocation_status": "allocated",
        "reservation_status": "not_reserved", "outbound_status": "not_started",
        "shipment_status": "not_started", "logistics_signature_status": "not_signed",
        "oam_receipt_status": "not_occurred", "personal_inbound_status": "not_started",
        "notification_status": "not_started", "reconciliation_status": "not_started",
    }
    common = {
        "request_id": _id(10), "allocation_id": _id(11), "allocation_no": "AL-1",
        "request_version": 2, "revision_id": _id(12), "revision_no": 1,
        "request_line_id": _id(13), "source_stock_account_id": _id(14),
        "allocation_status": "allocated", "request_status": "approved", "state_axes": axes,
    }
    assert AllocationMutationOut(**common, allocated_qty="1.000").allocated_qty == "1.000"
    with pytest.raises(ValidationError):
        AllocationMutationOut(**common, allocated_qty="1")
    with pytest.raises(ValidationError):
        AllocationMutationOut(**{**common, "request_status": "approved", "state_axes": {**axes, "request_status": "cancelled"}}, allocated_qty="1.000")


def test_allocation_service_rejects_technician_before_database_access():
    actor = type("Actor", (), {
        "role_codes": ("technician",), "user_id": "u", "person_id": _id(20),
        "authorization_version": 1, "account_status": "active",
        "employment_status": "active", "access_mode": "active",
    })()
    with pytest.raises(MaterialRequestAllocationError) as caught:
        create_allocation(
            None,
            actor=actor,
            material_request_id=_id(21),
            expected_request_version=0,
            allocation=AllocationCreateInput(
                request_line_id=_id(22), source_stock_account_id=_id(23),
                allocated_qty=Decimal("1.000"), source_balance_version=0,
                source_ledger_cursor=0,
            ),
            idempotency_key="allocation-key",
            idempotency_hmac_secret=b"s" * 32,
            trace_request_id="allocation-trace",
        )
    assert caught.value.code == "material_request_allocation_forbidden"
