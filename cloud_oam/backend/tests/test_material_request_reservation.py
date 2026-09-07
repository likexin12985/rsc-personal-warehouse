from datetime import timedelta
from decimal import Decimal
import hashlib
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.formal_services import inventory_query
from app.formal_services.inventory_posting import InventoryPostingResult
from app.formal_services.material_request_allocation import (
    AllocationCreateInput,
    create_allocation,
)
from app.formal_services.material_request_reservation import (
    MaterialRequestReservationError,
    ReservationCreateInput,
    create_reservation,
    reservation_command_status,
)
from app.foundation_models import AuditEvent
from app.inventory_models import (
    InventoryMovement,
    InventoryTransaction,
    MaterialInventoryPolicy,
    StockAccount,
    StockBalance,
    StockLocation,
    StockReservation,
)
from app.material_request_reservation_schemas import (
    ReservationCreateIn,
    ReservationMutationOut,
)
from app.demand_models import MaterialRequestCommand
from test_material_request_approval_service import _principal, approval_db
from test_material_request_draft_service import NOW, SECRET
from test_material_request_lifecycle_service import _approved_request


def _id(value: int) -> UUID:
    return UUID(f"20000000-0000-4000-8000-{value:012d}")


def _axes(reservation_status: str = "pending") -> dict[str, str]:
    return {
        "request_status": "approved",
        "allocation_status": "allocated",
        "reservation_status": reservation_status,
        "outbound_status": "not_started",
        "shipment_status": "not_started",
        "logistics_signature_status": "not_signed",
        "oam_receipt_status": "not_occurred",
        "personal_inbound_status": "not_started",
        "notification_status": "not_started",
        "reconciliation_status": "not_started",
    }


def test_reservation_schema_requires_canonical_quantity_and_keeps_axes_separate():
    value = ReservationCreateIn(
        expected_request_version=4,
        allocation_id=_id(1),
        request_line_id=_id(2),
        reserved_qty="1.250",
        source_balance_version=7,
        source_ledger_cursor=9,
        serial_ids=(),
    )
    assert value.reserved_qty == Decimal("1.250")
    with pytest.raises(ValidationError):
        ReservationCreateIn(
            expected_request_version=4,
            allocation_id=_id(1),
            request_line_id=_id(2),
            reserved_qty="1e0",
            source_balance_version=7,
            source_ledger_cursor=9,
        )
    result = ReservationMutationOut(
        request_id=_id(10),
        reservation_id=_id(11),
        reservation_no="RS-1",
        request_version=5,
        current_request_version=5,
        revision_id=_id(12),
        revision_no=1,
        request_line_id=_id(13),
        allocation_id=_id(14),
        source_stock_account_id=_id(15),
        stock_account_id=_id(16),
        reserve_transaction_id=_id(17),
        reserve_transaction_no="INV-RES-1",
        reserved_qty="1.250",
        reservation_status="reserved",
        request_status="approved",
        state_axes=_axes(),
    )
    assert result.state_axes.outbound_status == "not_started"
    assert result.state_axes.shipment_status == "not_started"


def test_reservation_rejects_technician_before_database_access():
    actor = type(
        "Actor",
        (),
        {
            "role_codes": ("technician",),
            "user_id": "u",
            "person_id": _id(20),
            "authorization_version": 1,
            "account_status": "active",
            "employment_status": "active",
            "access_mode": "active",
        },
    )()
    with pytest.raises(MaterialRequestReservationError) as caught:
        create_reservation(
            None,
            actor=actor,
            material_request_id=_id(21),
            expected_request_version=0,
            reservation=ReservationCreateInput(
                request_line_id=_id(22),
                allocation_id=_id(23),
                reserved_qty=Decimal("1.000"),
                source_balance_version=0,
                source_ledger_cursor=0,
            ),
            idempotency_key="reservation-key-0001",
            idempotency_hmac_secret=b"s" * 32,
            trace_request_id="reservation-trace-0001",
        )
    assert caught.value.code == "material_request_reservation_forbidden"


def test_reservation_posts_inventory_and_recovers_exact_command(
    approval_db, monkeypatch
):
    db = approval_db
    world, request, line, expected_version = _approved_request(
        db, key="reservation-real-chain"
    )
    actor = _principal(db, world.admin_users[0].id)
    location = StockLocation(
        id=_id(1000),
        code="HQ-RES-1000",
        name="总部预约库",
        location_type="headquarters",
        owner_org_id=world.headquarters.id,
        parent_id=None,
        status="active",
    )
    source = StockAccount(
        id=_id(1001),
        owner_org_id=world.headquarters.id,
        location_id=location.id,
        material_id=line.material_id,
        condition_code="new",
        availability_bucket="available",
    )
    target = StockAccount(
        id=_id(1002),
        owner_org_id=world.headquarters.id,
        location_id=location.id,
        material_id=line.material_id,
        condition_code="new",
        availability_bucket="reserved",
    )
    source_balance = StockBalance(
        stock_account_id=source.id,
        quantity=Decimal("4.000"),
        ledger_cursor=9,
        version=7,
    )
    target_balance = StockBalance(
        stock_account_id=target.id,
        quantity=Decimal("0.000"),
        ledger_cursor=0,
        version=0,
    )
    policy = MaterialInventoryPolicy(
        id=_id(1003),
        material_id=line.material_id,
        tracking_mode="none",
        quantity_scale=3,
        allow_fraction=True,
        effective_from=NOW - timedelta(days=1),
        effective_to=None,
    )
    db.add(location)
    db.flush()
    db.add_all((source, target, source_balance, target_balance, policy))
    db.flush()

    source_row = SimpleNamespace(
        account=source,
        location=location,
        material=world.materials[0],
        owner_org=world.headquarters,
        location_owner_org=world.headquarters,
        custodian=None,
        lot=None,
        balance=source_balance,
    )
    snapshot = SimpleNamespace(ledger_cursor=9, projected_at=NOW)
    monkeypatch.setattr(inventory_query, "_require_inventory_read", lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, "_projection_snapshot", lambda *a, **k: snapshot)
    monkeypatch.setattr(inventory_query, "_authorized_account_rows", lambda *a, **k: [source_row])
    monkeypatch.setattr(inventory_query, "_validate_current_projection_integrity", lambda *a, **k: None)
    monkeypatch.setattr(
        inventory_query,
        "_validated_opening_evidence",
        lambda *a, **k: SimpleNamespace(complete=True),
    )
    allocation = create_allocation(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_request_version=expected_version,
        allocation=AllocationCreateInput(
            request_line_id=line.id,
            source_stock_account_id=source.id,
            allocated_qty=Decimal("0.500"),
            source_balance_version=7,
            source_ledger_cursor=9,
        ),
        idempotency_key="reservation-allocation-key-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="reservation-allocation-trace-0001",
    )

    def post_inventory(_db, *, actor, command, idempotency_key, request_id):
        del actor, idempotency_key, request_id
        transaction_id = _id(1100)
        movement_id = _id(1101)
        _db.add(
            InventoryTransaction(
                id=transaction_id,
                transaction_no=command.transaction_no,
                movement_type="reserve",
                source_document_type=command.source_document_type,
                source_document_id=command.source_document_id,
                posting_key=command.posting_key,
                idempotency_key_hash=hashlib.sha256(b"inventory-reserve-key").hexdigest(),
                request_hash=hashlib.sha256(b"inventory-reserve-request").hexdigest(),
                status="posted",
                effective_at=command.effective_at,
                posted_at=command.effective_at,
                ledger_cursor=10,
                reversed_transaction_id=None,
                actor_user_id=world.admin_users[0].id,
                created_at=command.effective_at,
            )
        )
        _db.flush()
        movement = command.movements[0]
        _db.add(
            InventoryMovement(
                id=movement_id,
                transaction_id=transaction_id,
                line_no=1,
                from_account_id=movement.from_account_id,
                to_account_id=movement.to_account_id,
                external_boundary_code=None,
                quantity=movement.quantity,
                created_at=command.effective_at,
            )
        )
        source_balance.quantity -= movement.quantity
        source_balance.version += 1
        source_balance.ledger_cursor = 10
        target_balance.quantity += movement.quantity
        target_balance.version += 1
        target_balance.ledger_cursor = 10
        _db.flush()
        return InventoryPostingResult(
            transaction_id=transaction_id,
            transaction_no=command.transaction_no,
            ledger_cursor=10,
        )

    monkeypatch.setattr(
        "app.formal_services.material_request_reservation.post_inventory_transaction",
        post_inventory,
    )
    result = create_reservation(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_request_version=allocation.request_version,
        reservation=ReservationCreateInput(
            request_line_id=line.id,
            allocation_id=allocation.allocation_id,
            reserved_qty=Decimal("0.500"),
            source_balance_version=7,
            source_ledger_cursor=9,
        ),
        idempotency_key="reservation-real-chain-key-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="reservation-real-chain-trace-0001",
    )
    assert result.request_version == allocation.request_version + 1
    assert result.reserved_qty == Decimal("0.500")
    assert result.state_axes["reservation_status"] in {"pending", "reserved"}
    assert result.state_axes["outbound_status"] == "not_started"
    assert result.state_axes["shipment_status"] == "not_started"
    assert db.get(StockReservation, result.reservation_id) is not None
    command = db.scalar(
        select(MaterialRequestCommand).where(
            MaterialRequestCommand.request_id == request.id,
            MaterialRequestCommand.target_version == result.request_version,
        )
    )
    assert command is not None and command.operation == "reserve"
    assert command.result_jsonb["reserve_transaction_id"] == str(result.reserve_transaction_id)

    recovered = reservation_command_status(
        db, actor=actor, trace_request_id="reservation-real-chain-trace-0001"
    )
    assert recovered is not None
    assert recovered.reservation_id == result.reservation_id
    assert recovered.reserve_transaction_no == result.reserve_transaction_no
    assert recovered.replayed is True

    replay = create_reservation(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_request_version=allocation.request_version,
        reservation=ReservationCreateInput(
            request_line_id=line.id,
            allocation_id=allocation.allocation_id,
            reserved_qty=Decimal("0.500"),
            source_balance_version=7,
            source_ledger_cursor=9,
        ),
        idempotency_key="reservation-real-chain-key-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="reservation-real-chain-replay-0001",
    )
    assert replay.replayed is True
    assert replay.reservation_id == result.reservation_id

    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "material_request_reservation_created",
            AuditEvent.request_id == "reservation-real-chain-trace-0001",
        )
    )
    assert audit is not None
    audit.after_jsonb = {**audit.after_jsonb, "reserved_qty": "0.501"}
    db.flush()
    with pytest.raises(MaterialRequestReservationError) as tampered:
        reservation_command_status(
            db, actor=actor, trace_request_id="reservation-real-chain-trace-0001"
        )
    assert tampered.value.code == "material_request_reservation_history_invalid"
