from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.formal_services.material_request_allocation import AllocationCreateInput
from app.formal_services.material_request_allocation import (
    MaterialRequestAllocationError,
    allocation_command_status,
    create_allocation,
)
from app.formal_services import inventory_query
from app.material_request_allocation_schemas import AllocationCreateIn
from app.material_request_allocation_schemas import AllocationMutationOut
from app.formal_services.material_request_allocation import _quantity_text
from app.inventory_models import MaterialInventoryPolicy, StockAccount, StockBalance, StockLocation
from app.foundation_models import AuditEvent
from test_material_request_approval_service import _principal, approval_db
from test_material_request_lifecycle_service import _approved_request
from test_material_request_draft_service import NOW, SECRET


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
        "source_balance_version": 7, "source_ledger_cursor": 9,
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


def test_allocation_service_maps_inventory_read_failures_to_stable_domain_errors(monkeypatch):
    monkeypatch.setattr(
        "app.formal_services.material_request_allocation._create_allocation_impl",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            inventory_query.InventoryReadError(
                code="inventory_projection_invalid", status_code=503, message="库存投影无效"
            )
        ),
    )
    with pytest.raises(MaterialRequestAllocationError) as caught:
        create_allocation(
            None, actor=object(), material_request_id=_id(30), expected_request_version=1,
            allocation=AllocationCreateInput(
                request_line_id=_id(31), source_stock_account_id=_id(32),
                allocated_qty=Decimal("1.000"), source_balance_version=1,
                source_ledger_cursor=1,
            ), idempotency_key="key", idempotency_hmac_secret=b"s" * 32,
            trace_request_id="trace-1234",
        )
    assert caught.value.category == "service_unavailable"


def test_allocation_command_status_returns_not_observed_without_audit_rows():
    actor = type("Actor", (), {
        "role_codes": ("admin",), "user_id": "u", "person_id": _id(40),
        "authorization_version": 1, "account_status": "active",
        "employment_status": "active", "access_mode": "active",
    })()

    class Result:
        def all(self):
            return []

    class DB:
        class _NoAutoflush:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        no_autoflush = _NoAutoflush()

        def scalars(self, _statement):
            return Result()

    assert allocation_command_status(DB(), actor=actor, trace_request_id="trace-1234") is None


def test_allocation_command_status_fails_closed_on_mismatched_audit_object():
    actor = type("Actor", (), {
        "role_codes": ("admin",), "user_id": "u", "person_id": _id(41),
        "authorization_version": 1, "account_status": "active",
        "employment_status": "active", "access_mode": "active",
    })()
    audit = type("Audit", (), {
        "action": "material_request_allocation_created",
        "aggregate_type": "stock_allocation",
        "aggregate_id": str(_id(99)),
        "after_jsonb": {"allocation_id": str(_id(42))},
    })()

    class Result:
        def all(self):
            return [audit]

    class DB:
        class _NoAutoflush:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        no_autoflush = _NoAutoflush()

        def scalars(self, _statement):
            return Result()

    with pytest.raises(MaterialRequestAllocationError) as caught:
        allocation_command_status(DB(), actor=actor, trace_request_id="trace-1234")
    assert caught.value.code == "material_request_allocation_history_invalid"
    assert caught.value.category == "service_unavailable"


def test_real_allocation_and_recovery_bind_audit_quantity_and_historical_version(
    approval_db, monkeypatch
):
    db = approval_db
    world, request, line, expected_version = _approved_request(
        db, key="allocation-real-chain"
    )
    actor = _principal(db, world.admin_users[0].id)
    location = StockLocation(
        id=_id(1000), code="HQ-SOURCE-1000", name="总部货源库",
        location_type="headquarters", owner_org_id=world.headquarters.id,
        parent_id=None, status="active",
    )
    account = StockAccount(
        id=_id(1001), owner_org_id=world.headquarters.id, location_id=location.id,
        material_id=line.material_id, condition_code="new", availability_bucket="available",
    )
    balance = StockBalance(
        stock_account_id=account.id, quantity=Decimal("4.000"),
        ledger_cursor=9, version=7,
    )
    policy = MaterialInventoryPolicy(
        id=_id(1002), material_id=line.material_id, tracking_mode="none",
        quantity_scale=3, allow_fraction=True, effective_from=NOW - timedelta(days=1),
        effective_to=None,
    )
    db.add(location)
    db.flush()
    db.add_all((account, balance, policy))
    db.flush()
    source_row = SimpleNamespace(
        account=account, location=location, material=world.materials[0],
        owner_org=world.headquarters, location_owner_org=world.headquarters,
        custodian=None, lot=None, balance=balance,
    )
    snapshot = SimpleNamespace(ledger_cursor=9, projected_at=NOW)
    monkeypatch.setattr(inventory_query, "_require_inventory_read", lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, "_projection_snapshot", lambda *a, **k: snapshot)
    monkeypatch.setattr(inventory_query, "_authorized_account_rows", lambda *a, **k: [source_row])
    monkeypatch.setattr(inventory_query, "_validate_current_projection_integrity", lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, "_validated_opening_evidence", lambda *a, **k: SimpleNamespace(complete=True))

    allocation = AllocationCreateInput(
        request_line_id=line.id, source_stock_account_id=account.id,
        allocated_qty=Decimal("0.500"), source_balance_version=7,
        source_ledger_cursor=9,
    )
    result = create_allocation(
        db, actor=actor, material_request_id=request.id,
        expected_request_version=expected_version, allocation=allocation,
        idempotency_key="allocation-real-chain-key-0001", idempotency_hmac_secret=SECRET,
        trace_request_id="allocation-real-chain-trace-0001",
    )
    assert result.allocated_qty == Decimal("0.500")
    assert result.request_version == expected_version + 1
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "material_request_allocation_created",
            AuditEvent.request_id == "allocation-real-chain-trace-0001",
        )
    )
    assert audit is not None
    assert audit.after_jsonb["allocation_id"] == str(result.allocation_id)
    assert audit.after_jsonb["allocated_qty"] == "0.500"
    assert audit.before_jsonb["request_version"] == expected_version
    assert audit.after_jsonb["request_version"] == expected_version + 1

    recovered = allocation_command_status(
        db, actor=actor, trace_request_id="allocation-real-chain-trace-0001"
    )
    assert recovered is not None
    assert recovered.request_version == expected_version + 1
    assert recovered.current_request_version == expected_version + 1
    assert recovered.allocated_qty == Decimal("0.500")

    audit.after_jsonb = {**audit.after_jsonb, "allocated_qty": "0.501"}
    db.flush()
    with pytest.raises(MaterialRequestAllocationError) as tampered:
        allocation_command_status(
            db, actor=actor, trace_request_id="allocation-real-chain-trace-0001"
        )
    assert tampered.value.code == "material_request_allocation_history_invalid"
    audit.after_jsonb = {**audit.after_jsonb, "allocated_qty": "0.500"}
    db.flush()

    request.version += 3
    db.flush()
    replay = create_allocation(
        db, actor=actor, material_request_id=request.id,
        expected_request_version=expected_version, allocation=allocation,
        idempotency_key="allocation-real-chain-key-0001", idempotency_hmac_secret=SECRET,
        trace_request_id="allocation-real-chain-replay-trace-0001",
    )
    assert replay.replayed is True
    assert replay.request_version == expected_version + 1
    assert replay.current_request_version == expected_version + 4


def test_allocation_output_accepts_fractional_fixed_scale_quantity():
    axes = {
        "request_status": "approved", "allocation_status": "allocated",
        "reservation_status": "not_reserved", "outbound_status": "not_started",
        "shipment_status": "not_started", "logistics_signature_status": "not_signed",
        "oam_receipt_status": "not_occurred", "personal_inbound_status": "not_started",
        "notification_status": "not_started", "reconciliation_status": "not_started",
    }
    assert AllocationMutationOut(
        request_id=_id(1100), allocation_id=_id(1101), allocation_no="AL-1",
        request_version=2, current_request_version=2, revision_id=_id(1102), revision_no=1,
        request_line_id=_id(1103), source_stock_account_id=_id(1104),
        source_balance_version=7, source_ledger_cursor=9, allocated_qty="0.500",
        allocation_status="allocated", request_status="approved", state_axes=axes,
    ).allocated_qty == "0.500"
