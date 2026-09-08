from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from app.formal_services import inventory_query
from app.formal_services.inventory_posting import (
    InventoryPostingResult,
    _posting_request_hash,
    _storage_hash,
    _validate_posting_command,
)
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
from app.foundation_models import AuditEvent, StateTransitionEvent
from app.inventory_models import (
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    SerialCurrentPosition,
    StockAccount,
    StockAllocationSerial,
    StockBalance,
    StockLocation,
    StockReservation,
    StockReservationSerial,
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
        source_balance_version=7,
        source_ledger_cursor=9,
        serial_ids=(),
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


_COMMON_HISTORY_CASES = (
    None,
    "movement_quantity",
    "movement_source",
    "transaction_source_type",
    "transaction_request_hash",
    "command_result_coordinate",
    "command_hashed_coordinates",
    "coordinated_fact_command_hashes",
    "state_event_metadata",
    "audit_contents",
    "audit_hash",
)
_SERIAL_HISTORY_CASES = (
    "missing_reservation_serial",
    "wrong_reservation_serial",
    "missing_movement_serial",
    "wrong_movement_serial",
)


@pytest.mark.parametrize(
    ("serial_mode", "tamper"),
    [(serial_mode, tamper) for serial_mode in (False, True) for tamper in _COMMON_HISTORY_CASES]
    + [(True, tamper) for tamper in _SERIAL_HISTORY_CASES],
)
def test_reservation_posts_inventory_and_recovers_exact_command(
    approval_db, monkeypatch, serial_mode, tamper
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
        quantity=Decimal("2.000") if serial_mode else Decimal("4.000"),
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
        tracking_mode="serial" if serial_mode else "none",
        quantity_scale=3,
        allow_fraction=not serial_mode,
        effective_from=NOW - timedelta(days=1),
        effective_to=None,
    )
    db.add(location)
    db.flush()
    db.add_all((source, target, source_balance, target_balance, policy))
    db.flush()

    # Use reverse UUID order to prove recovery preserves the originally
    # submitted SN sequence, not an incidental query/set ordering.
    serial_ids = (_id(1204), _id(1203)) if serial_mode else ()
    reserved_qty = Decimal("2.000") if serial_mode else Decimal("0.500")
    if serial_mode:
        db.add(InventoryTransaction(
            id=_id(1200), transaction_no="INV-RES-OPENING", movement_type="opening",
            source_document_type="opening_stocktake", source_document_id=str(_id(1201)),
            posting_key="reservation-serial-opening", idempotency_key_hash="1" * 64,
            request_hash="2" * 64, status="posted", effective_at=NOW, posted_at=NOW,
            ledger_cursor=9, actor_user_id=actor.user_id,
        ))
        db.flush()
        db.add(InventoryMovement(
            id=_id(1202), transaction_id=_id(1200), line_no=1,
            from_account_id=None, to_account_id=source.id,
            external_boundary_code="opening", quantity=Decimal("2.000"),
        ))
        for serial_id in serial_ids:
            db.add(InventorySerial(
                id=serial_id, material_id=line.material_id,
                serial_no=f"SN-{serial_id.hex[-4:]}", qr_code=f"QR-{serial_id.hex[-4:]}",
                lifecycle_status="active",
            ))
        db.flush()
        for serial_id in serial_ids:
            db.add(InventoryMovementSerial(
                transaction_id=_id(1200), movement_id=_id(1202), serial_id=serial_id,
            ))
            db.add(SerialCurrentPosition(
                serial_id=serial_id, stock_account_id=source.id,
                last_movement_id=_id(1202),
            ))
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
            allocated_qty=reserved_qty,
            source_balance_version=7,
            source_ledger_cursor=9,
            serial_ids=serial_ids,
        ),
        idempotency_key="reservation-allocation-key-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="reservation-allocation-trace-0001",
    )

    def post_inventory(_db, *, actor, command, idempotency_key, request_id):
        del request_id
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
                idempotency_key_hash=_storage_hash(idempotency_key),
                request_hash=_posting_request_hash(actor, _validate_posting_command(command)),
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
        _db.flush()
        for serial_id in movement.serial_ids:
            _db.add(InventoryMovementSerial(
                movement_id=movement_id, transaction_id=transaction_id,
                serial_id=serial_id,
            ))
            position = _db.get(SerialCurrentPosition, serial_id)
            position.stock_account_id = movement.to_account_id
            position.last_movement_id = movement_id
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
    reservation_input = ReservationCreateInput(
        request_line_id=line.id,
        allocation_id=allocation.allocation_id,
        reserved_qty=reserved_qty,
        source_balance_version=7,
        source_ledger_cursor=9,
        serial_ids=serial_ids,
    )
    executed_selects = []
    def capture_select(orm_execute_state):
        if orm_execute_state.is_select:
            executed_selects.append(str(orm_execute_state.statement.compile(dialect=postgresql.dialect())))
    event.listen(db, "do_orm_execute", capture_select)
    result = create_reservation(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_request_version=allocation.request_version,
        reservation=reservation_input,
        idempotency_key="reservation-real-chain-key-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="reservation-real-chain-trace-0001",
    )
    assert result.request_version == allocation.request_version + 1
    assert result.source_balance_version == 7
    assert result.source_ledger_cursor == 9
    assert result.serial_ids == serial_ids
    assert result.reserved_qty == reserved_qty
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

    assert {
        row.serial_id for row in db.scalars(select(StockAllocationSerial).where(
            StockAllocationSerial.allocation_id == allocation.allocation_id
        )).all()
    } == set(serial_ids)
    assert {
        row.serial_id for row in db.scalars(select(StockReservationSerial).where(
            StockReservationSerial.reservation_id == result.reservation_id
        )).all()
    } == set(serial_ids)
    assert {
        row.serial_id for row in db.scalars(select(InventoryMovementSerial).where(
            InventoryMovementSerial.transaction_id == result.reserve_transaction_id
        )).all()
    } == set(serial_ids)

    # Later inventory projections can advance; only this command's original
    # immutable facts are relevant to a historical recovery/replay.
    source_balance.version = 88
    source_balance.ledger_cursor = 99
    for serial_id in serial_ids:
        db.get(SerialCurrentPosition, serial_id).stock_account_id = None
    db.flush()
    assert reservation_command_status(
        db, actor=actor, trace_request_id="reservation-never-observed-trace"
    ) is None

    recovered = reservation_command_status(
        db, actor=actor, trace_request_id="reservation-real-chain-trace-0001"
    )
    assert recovered is not None
    assert recovered.reservation_id == result.reservation_id
    assert recovered.reserve_transaction_no == result.reserve_transaction_no
    assert recovered.replayed is True
    assert recovered.source_balance_version == 7
    assert recovered.source_ledger_cursor == 9
    assert recovered.serial_ids == serial_ids

    replay = create_reservation(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_request_version=allocation.request_version,
        reservation=reservation_input,
        idempotency_key="reservation-real-chain-key-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="reservation-real-chain-replay-0001",
    )
    assert replay.replayed is True
    assert replay.reservation_id == result.reservation_id
    assert replay.serial_ids == serial_ids
    assert replay.source_balance_version == 7
    assert replay.source_ledger_cursor == 9
    event.remove(db, "do_orm_execute", capture_select)
    # SQLite discards FOR UPDATE in rendered SQL; compiling captured ORM
    # statements for PostgreSQL protects the API's SELECT/INSERT-only facts.
    fact_reads = [statement for statement in executed_selects if any(
        f"FROM {table}" in statement for table in ("stock_allocations", "stock_reservations")
    )]
    assert any("FROM stock_allocations" in statement for statement in fact_reads)
    assert any("FROM stock_reservations" in statement for statement in fact_reads)
    assert all("FOR UPDATE" not in statement for statement in fact_reads)

    if tamper is None:
        return

    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "material_request_reservation_created",
            AuditEvent.request_id == "reservation-real-chain-trace-0001",
        )
    )
    assert audit is not None
    fact = db.get(StockReservation, result.reservation_id)
    transaction = db.get(InventoryTransaction, result.reserve_transaction_id)
    movement = db.get(InventoryMovement, _id(1101))
    state_event = db.scalar(select(StateTransitionEvent).where(
        StateTransitionEvent.idempotency_key == f"reservation-state-{fact.idempotency_key_hash}"
    ))
    replay_input = reservation_input
    if tamper == "movement_quantity":
        movement.quantity += Decimal("0.001")
    elif tamper == "movement_source":
        wrong_source = StockAccount(
            id=_id(1400), owner_org_id=world.headquarters.id, location_id=location.id,
            material_id=line.material_id, condition_code="used", availability_bucket="available",
        )
        db.add(wrong_source)
        db.flush()
        movement.from_account_id = wrong_source.id
    elif tamper == "transaction_source_type":
        transaction.source_document_type = "stock_operation"
    elif tamper == "transaction_request_hash":
        transaction.request_hash = "d" * 64
    elif tamper == "command_result_coordinate":
        command.result_jsonb = {**command.result_jsonb, "source_balance_version": 8}
    elif tamper in {"command_hashed_coordinates", "coordinated_fact_command_hashes"}:
        # Even coordinated edits to command content, result hash and embedded
        # payload hash cannot replace the independently anchored request hash.
        command.result_jsonb = {**command.result_jsonb, "source_balance_version": 8}
        command.result_hash = _json_hash(command.result_jsonb)
        forged_hash = _json_hash({
            "request_id": str(request.id), "expected_request_version": allocation.request_version,
            "request_line_id": str(line.id), "allocation_id": str(allocation.allocation_id),
            "reserved_qty": format(reserved_qty, ".3f"), "source_balance_version": 8,
            "source_ledger_cursor": 9, "serial_ids": [str(value) for value in serial_ids],
            "actor_user_id": actor.user_id, "actor_person_id": str(actor.person_id),
            "authorization_version": actor.authorization_version,
        })
        assert forged_hash != fact.request_hash
        command.request_jsonb = {**command.request_jsonb, "source_balance_version": 8, "payload_sha256": forged_hash}
        if tamper == "coordinated_fact_command_hashes":
            # Also forge both stored request hashes and replay the forged input;
            # the immutable audit must still reject the coordinated rewrite.
            command.request_hash = fact.request_hash = forged_hash
            replay_input = replace(reservation_input, source_balance_version=8)
    elif tamper == "state_event_metadata":
        state_event.metadata_jsonb = {**state_event.metadata_jsonb, "command_id": str(_id(9999))}
    elif tamper == "audit_contents":
        audit.after_jsonb = {**audit.after_jsonb, "reserved_qty": "0.501"}
    elif tamper == "audit_hash":
        original_contents = dict(audit.after_jsonb)
        audit.event_hash = "e" * 64
        assert audit.after_jsonb == original_contents
    elif tamper in _SERIAL_HISTORY_CASES:
        if "reservation_serial" in tamper:
            evidence = db.get(StockReservationSerial, (result.reservation_id, allocation.allocation_id, serial_ids[0]))
        else:
            evidence = db.get(InventoryMovementSerial, (_id(1101), serial_ids[0]))
        db.delete(evidence)
        db.flush()
        if tamper.startswith("wrong_"):
            wrong_serial_id = _id(1300)
            db.add(InventorySerial(
                id=wrong_serial_id, material_id=line.material_id,
                serial_no="SN-WRONG-HISTORY", qr_code="QR-WRONG-HISTORY", lifecycle_status="active",
            ))
            db.flush()
            if "reservation_serial" in tamper:
                db.add(StockAllocationSerial(allocation_id=allocation.allocation_id, serial_id=wrong_serial_id))
                db.flush()
                db.add(StockReservationSerial(
                    reservation_id=result.reservation_id, allocation_id=allocation.allocation_id, serial_id=wrong_serial_id,
                ))
            else:
                db.add(InventoryMovementSerial(
                    movement_id=_id(1101), transaction_id=result.reserve_transaction_id, serial_id=wrong_serial_id,
                ))
    else:
        raise AssertionError(f"unknown tamper case: {tamper}")
    db.flush()
    with pytest.raises(MaterialRequestReservationError) as tampered:
        reservation_command_status(
            db, actor=actor, trace_request_id="reservation-real-chain-trace-0001"
        )
    assert tampered.value.code == "material_request_reservation_history_invalid"
    with pytest.raises(MaterialRequestReservationError) as tampered_replay:
        create_reservation(
            db, actor=actor, material_request_id=request.id,
            expected_request_version=allocation.request_version, reservation=replay_input,
            idempotency_key="reservation-real-chain-key-0001", idempotency_hmac_secret=SECRET,
            trace_request_id="reservation-tampered-replay-trace",
        )
    assert tampered_replay.value.code == "material_request_reservation_history_invalid"


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
