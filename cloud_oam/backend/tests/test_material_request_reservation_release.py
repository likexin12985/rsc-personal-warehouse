"""Local command/recovery regression; real posting is covered by the PG16 gate."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.demand_models import MaterialRequest, MaterialRequestCommand
from app.foundation_models import AuditEvent, Permission, Role, RolePermission
from app.inventory_models import (
    InventoryMovement, InventoryMovementSerial, InventoryTransaction,
    SerialCurrentPosition, StockBalance, StockReservation, StockReservationRelease,
)
from app.formal_services import material_request_reservation_release as release
from app.formal_services.inventory_posting import InventoryPostingResult, _posting_request_hash, _storage_hash, _validate_posting_command
from app.material_request_reservation_release_schemas import ReservationReleaseIn, ReservationReleaseOut
from test_material_request_approval_service import _principal, approval_db
from test_material_request_draft_service import SECRET
import test_material_request_reservation as reservation_cases


@pytest.fixture(params=[False, True], ids=["quantity", "serial"])
def release_world(approval_db, monkeypatch, request):
    db = approval_db
    # Reuse the already verified approval -> allocation -> reservation fixture.
    # It deliberately advances mutable projections after the historical write.
    reservation_cases.test_reservation_posts_inventory_and_recovers_exact_command(
        db, monkeypatch, request.param, None,
    )
    original = db.scalar(select(StockReservation))
    material_request = db.get(MaterialRequest, original.request_id)
    for resource, action in (("inventory_transaction", "post"), ("inventory", "read")):
        permission = Permission(id=uuid4(), resource=resource, action=action, field_code="")
        db.add(permission)
        db.flush()
        db.add(RolePermission(role_id=db.scalar(select(Role.id).where(Role.code == "admin")), permission_id=permission.id, effect="allow"))
        db.flush()
    actor = _principal(db, original.actor_user_id)
    serial_ids = tuple(db.scalars(select(InventoryMovementSerial.serial_id).where(
        InventoryMovementSerial.transaction_id == original.reserve_transaction_id,
    ).order_by(InventoryMovementSerial.serial_id)).all())
    for serial_id in serial_ids:
        db.get(SerialCurrentPosition, serial_id).stock_account_id = original.stock_account_id
    calls = []
    def synthetic_post(db, *, actor, command, idempotency_key, request_id):
        calls.append(command)
        command = _validate_posting_command(command)
        move = command.movements[0]
        source = db.get(StockBalance, move.from_account_id)
        target = db.get(StockBalance, move.to_account_id)
        cursor = max(source.ledger_cursor, target.ledger_cursor) + 1
        transaction_id, movement_id = uuid4(), uuid4()
        db.add(InventoryTransaction(
            id=transaction_id, transaction_no=command.transaction_no, movement_type=command.movement_type,
            source_document_type=command.source_document_type, source_document_id=command.source_document_id,
            posting_key=command.posting_key, idempotency_key_hash=_storage_hash(idempotency_key),
            request_hash=_posting_request_hash(actor, command), status="posted", effective_at=command.effective_at,
            posted_at=command.effective_at, ledger_cursor=cursor, actor_user_id=actor.user_id,
        ))
        db.flush()
        db.add(InventoryMovement(id=movement_id, transaction_id=transaction_id, line_no=1,
            from_account_id=move.from_account_id, to_account_id=move.to_account_id, quantity=move.quantity))
        db.flush()
        for serial_id in move.serial_ids:
            db.add(InventoryMovementSerial(movement_id=movement_id, transaction_id=transaction_id, serial_id=serial_id))
            position = db.get(SerialCurrentPosition, serial_id)
            assert position.stock_account_id == source.stock_account_id
            position.stock_account_id = target.stock_account_id
            position.last_movement_id = movement_id
        source.quantity -= move.quantity
        target.quantity += move.quantity
        for balance in (source, target):
            balance.version += 1
            balance.ledger_cursor = cursor
        db.flush()
        return InventoryPostingResult(transaction_id=transaction_id, transaction_no=command.transaction_no, ledger_cursor=cursor)
    monkeypatch.setattr(release, "post_inventory_transaction", synthetic_post)
    db.flush()
    return db, actor, material_request, original, serial_ids, calls


def _input(db, original, quantity, serial_ids=()):
    balance = db.get(StockBalance, original.stock_account_id)
    return release.ReservationReleaseInput(original.id, Decimal(quantity), "现场需求调整，释放未使用物资",
        balance.version, balance.ledger_cursor, serial_ids)


def _create(world, value, *, key="release-test-command-0001", version=None):
    db, actor, request, *_ = world
    return release.create_release(db, actor=actor, material_request_id=request.id,
        expected_request_version=request.version if version is None else version, release=value,
        idempotency_key=key, idempotency_hmac_secret=SECRET, trace_request_id=f"trace-{key}")


def test_partial_then_full_release_preserves_original_and_recovers(release_world):
    db, actor, request, original, serials, calls = release_world
    quantity = "1.000" if serials else "0.125"
    value = _input(db, original, quantity, serials[:1])
    before_version = request.version
    before_axes = release.reserve._state_axes(request)
    first = _create(release_world, value)
    assert ReservationReleaseOut(**first).state_axes.reservation_status == "partially_released"
    assert original.status == "reserved" and original.released_qty == 0 and original.release_transaction_id is None
    for key, before in before_axes.items():
        if key != "reservation_status": assert first["state_axes"][key] == before
    replay = _create(release_world, value, version=before_version)
    assert replay["release_id"] == first["release_id"] and replay["idempotency_replayed"]
    assert len(calls) == 1
    second = _create(release_world, _input(db, original, str(original.reserved_qty - Decimal(quantity)), serials[1:]), key="release-test-command-0002")
    assert second["state_axes"]["reservation_status"] == "released"
    recovered = release.release_command_status(db, actor=actor, trace_request_id="trace-release-test-command-0001")
    assert recovered["request_version"] == first["request_version"]
    assert recovered["current_request_version"] == second["request_version"]
    assert recovered["state_axes"]["reservation_status"] == "partially_released"
    assert db.get(StockBalance, original.stock_account_id).quantity == 0
    assert release.released_quantity(db, original.id) == original.reserved_qty
    assert len(calls) == 2
    with pytest.raises(release.MaterialRequestReservationReleaseError) as caught:
        _create(release_world, _input(db, original, quantity, serials[:1]), key="release-exhausted-command")
    assert caught.value.http_status_code == 412
    assert len(calls) == 2


@pytest.mark.parametrize("bad", ["over", "stale", "wrong_serial", "other_reservation", "empty_reason", "fraction_precision", "version"])
def test_release_rejects_invalid_slice_before_post(release_world, bad):
    db, actor, request, original, serials, calls = release_world
    value = _input(db, original, "1.000" if serials else "0.125", serials[:1])
    expected_version = request.version
    if bad == "over": value = replace(value, released_qty=original.reserved_qty + 1)
    elif bad == "stale": value = replace(value, source_balance_version=value.source_balance_version + 1)
    elif bad == "wrong_serial": value = replace(value, serial_ids=(uuid4(),))
    elif bad == "other_reservation": value = replace(value, reservation_id=uuid4())
    elif bad == "empty_reason": value = replace(value, reason=" ")
    elif bad == "fraction_precision": value = replace(value, released_qty=Decimal("0.0001"))
    elif bad == "version": expected_version -= 1
    with pytest.raises(release.reserve.MaterialRequestReservationError):
        _create(release_world, value, version=expected_version)
    assert not calls
    assert not db.scalars(select(StockReservationRelease)).all()


@pytest.mark.parametrize("tamper", ["transaction", "movement", "command", "audit", "coordinate"])
def test_release_recovery_requires_exact_immutable_graph(release_world, tamper):
    db, actor, request, original, serials, calls = release_world
    result = _create(release_world, _input(db, original, "1.000" if serials else "0.125", serials[:1]))
    from uuid import UUID
    fact = db.get(StockReservationRelease, UUID(result["release_id"]))
    if tamper == "transaction": db.get(InventoryTransaction, fact.release_transaction_id).source_document_id = str(uuid4())
    elif tamper == "movement":
        db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == fact.release_transaction_id)).quantity += Decimal("0.001")
    elif tamper == "command":
        command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == "release"))
        command.result_jsonb = {**command.result_jsonb, "source_balance_version": 999}
    elif tamper == "audit":
        db.scalar(select(AuditEvent).where(AuditEvent.action == release.ACTION)).event_hash = "a" * 64
    elif tamper == "coordinate": fact.source_ledger_cursor += 1
    db.flush()
    with pytest.raises(release.MaterialRequestReservationReleaseError) as caught:
        release.release_command_status(db, actor=actor, trace_request_id="trace-release-test-command-0001")
    assert caught.value.code.endswith("history_invalid")
    assert len(calls) == 1


def test_release_audit_failure_rolls_back_whole_command(release_world, monkeypatch):
    db, actor, request, original, serials, calls = release_world
    db.commit()
    version = request.version
    quantity = db.get(StockBalance, original.stock_account_id).quantity
    value = _input(db, original, "1.000" if serials else "0.125", serials[:1])
    def fail(*args, **kwargs): raise release.AuditChainError("injected")
    monkeypatch.setattr(release, "append_audit_event", fail)
    with pytest.raises(release.MaterialRequestReservationReleaseError):
        _create(release_world, value)
    db.rollback()
    assert request.version == version
    assert db.get(StockBalance, original.stock_account_id).quantity == quantity
    assert not db.scalars(select(StockReservationRelease)).all()
    assert not db.scalars(select(InventoryTransaction).where(InventoryTransaction.movement_type == "release")).all()


def test_release_options_follow_exact_remaining_reservation(release_world, monkeypatch):
    from types import SimpleNamespace
    from app.formal_services.material_request_reservation_release_options import list_release_options
    db, actor, request, original, serials, calls = release_world
    monkeypatch.setattr(release.inventory_query, "_projection_snapshot", lambda db: SimpleNamespace(ledger_cursor=1000))
    before = list_release_options(db, actor=actor, material_request_id=request.id, request_line_id=original.request_line_id)
    assert len(before.items) == 1
    row = before.items[0]
    assert row.reservation_id == original.id and Decimal(row.releasable_qty) == original.reserved_qty
    assert {s.serial_id for s in row.serials} == set(serials)
    _create(release_world, _input(db, original, "1.000" if serials else "0.125", serials[:1]))
    after = list_release_options(db, actor=actor, material_request_id=request.id, request_line_id=original.request_line_id)
    assert Decimal(after.items[0].releasable_qty) == original.reserved_qty - (Decimal(1) if serials else Decimal("0.125"))
    if serials: assert {s.serial_id for s in after.items[0].serials} == set(serials[1:])


@pytest.mark.parametrize("quantity", ["1e0", 1, True, "0", "0.0001", "NaN"])
def test_release_http_schema_rejects_noncanonical_quantity(quantity):
    with pytest.raises(ValidationError):
        ReservationReleaseIn(expected_request_version=1, reservation_id=uuid4(), released_qty=quantity,
            reason="调整需求", source_balance_version=1, source_ledger_cursor=1)
