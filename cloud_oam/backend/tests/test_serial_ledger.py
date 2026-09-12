"""Read-only serial history reconstruction; PG16 separately proves posting."""
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.inventory_models import InventoryMovement, InventoryMovementSerial, InventorySerial, InventoryTransaction, StockAccount
from app.formal_services.serial_ledger import SerialLedgerError, rebuild_serial_states
from test_inventory_posting import db, world, make_account, make_material, NOW


@pytest.fixture
def history(db, world):
    material = make_material(db, world.source, tracking_mode="serial", quantity_scale=0, allow_fraction=False)
    available = make_account(db, organization=world.organization, material=material, established=False)
    reserved = StockAccount(id=uuid4(), owner_org_id=available.owner_org_id,
        custodian_person_id=available.custodian_person_id, location_id=available.location_id,
        material_id=material.id, condition_code=available.condition_code,
        lot_id=None, availability_bucket="reserved")
    serial = InventorySerial(id=uuid4(), material_id=material.id, serial_no="SERIAL-LEDGER-TEST",
        qr_code="QR-SERIAL-LEDGER-TEST", lifecycle_status="active")
    db.add_all((reserved, serial)); db.flush()
    return SimpleNamespace(serial=serial, available=available, reserved=reserved, actor_id=world.user.id)


def append(db, history, cursor, kind, source, target, *, document="work_order_material", boundary=None):
    key = uuid4().hex
    transaction = InventoryTransaction(id=uuid4(), transaction_no=key, movement_type=kind,
        source_document_type=document, source_document_id=uuid4().hex, posting_key=key,
        idempotency_key_hash=key * 2, request_hash=key * 2, status="posted",
        effective_at=NOW, posted_at=NOW, ledger_cursor=cursor, actor_user_id=history.actor_id)
    db.add(transaction); db.flush()
    movement = InventoryMovement(id=uuid4(), transaction_id=transaction.id, line_no=1,
        from_account_id=source, to_account_id=target, quantity=Decimal(1), external_boundary_code=boundary)
    db.add(movement); db.flush()
    db.add(InventoryMovementSerial(movement_id=movement.id, transaction_id=transaction.id, serial_id=history.serial.id))
    db.flush()
    return movement


def occupy(db, history):
    append(db, history, 1, "inbound", None, history.available.id, document="fixture", boundary="fixture")
    return append(db, history, 2, "reserve", history.available.id, history.reserved.id)


def test_consumed_status_and_previous_cursor_are_derived_without_mutating_master(db, history):
    reserve = occupy(db, history)
    consumed = append(db, history, 3, "consume", history.reserved.id, None, boundary="work_order_material_consume")
    db.commit()
    # Deliberately leave the projection wrong: immutable history wins.
    assert history.serial.lifecycle_status == "active"
    state = rebuild_serial_states(db, [history.serial.id])[history.serial.id]
    assert (state.lifecycle_status, state.stock_account_id, state.last_movement_id) == ("consumed", None, consumed.id)
    previous = rebuild_serial_states(db, [history.serial.id], through_cursor=2)[history.serial.id]
    assert (previous.lifecycle_status, previous.stock_account_id, previous.last_movement_id) == ("active", history.reserved.id, reserve.id)
    assert history.serial.lifecycle_status == "active" and not db.dirty and not db.new


@pytest.mark.parametrize("corruption", ["wrong_source", "different_document", "different_boundary", "repeat_consume", "uncontrolled_return"])
def test_invalid_history_cannot_be_reinterpreted_as_available_stock(db, history, corruption):
    occupy(db, history)
    append(db, history, 3, "consume", history.available.id if corruption == "wrong_source" else history.reserved.id,
        None, document="unrelated" if corruption == "different_document" else "work_order_material",
        boundary="unrelated" if corruption == "different_boundary" else "work_order_material_consume")
    if corruption == "repeat_consume":
        append(db, history, 4, "consume", history.reserved.id, None, boundary="work_order_material_consume")
    elif corruption == "uncontrolled_return":
        append(db, history, 4, "inbound", None, history.available.id, boundary="work_order_material_recover")
    with pytest.raises(SerialLedgerError):
        rebuild_serial_states(db, [history.serial.id])
    assert rebuild_serial_states(db, [history.serial.id], through_cursor=2)[history.serial.id].stock_account_id == history.reserved.id


def test_new_serial_has_no_position_and_empty_selection_does_not_query(db, history):
    state = rebuild_serial_states(db, [history.serial.id])[history.serial.id]
    assert (state.lifecycle_status, state.stock_account_id, state.last_movement_id, state.ledger_cursor) == ("active", None, None, 0)
    assert rebuild_serial_states(None, []) == {}
