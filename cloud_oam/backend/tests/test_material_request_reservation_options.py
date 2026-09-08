from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import inventory_query, material_request_query
from app.formal_services import material_request_reservation_options as service
from app.formal_services.material_request_allocation import AllocationCreateInput, create_allocation
from app.foundation_models import Permission, RolePermission
from app.inventory_models import (
    InventoryLot, InventoryMovement, InventorySerial, InventoryTransaction, MaterialInventoryPolicy,
    SerialCurrentPosition, StockAccount, StockAllocation, StockAllocationSerial,
    StockBalance, StockLocation, StockReservation, StockReservationSerial,
)
from app.material_request_reservation_option_schemas import MaterialRequestReservationOptionPageOut
from app.routers import formal_material_requests
from test_material_request_approval_service import _principal, approval_db
from test_material_request_draft_service import NOW, SECRET
from test_material_request_lifecycle_service import _approved_request
from test_material_request_query_service import _grant


def _id(number: int) -> uuid.UUID:
    return uuid.UUID(f"90000000-0000-4000-8000-{number:012d}")


@pytest.fixture()
def candidate(approval_db, monkeypatch):
    db = approval_db
    world, request, line, version = _approved_request(db, key="reservation-options")
    for resource in ("material_request", "inventory"):
        _grant(db, world, resource=resource, action="read", roles=("admin", "provincial_manager", "technician"))
    actor = _principal(db, world.admin_users[0].id)
    location = StockLocation(id=_id(1), code="HQ-OPTIONS", name="总部候选仓", location_type="headquarters", owner_org_id=world.headquarters.id, status="active")
    source = StockAccount(id=_id(2), owner_org_id=world.headquarters.id, location_id=location.id, material_id=line.material_id, condition_code="new", availability_bucket="available")
    target = StockAccount(id=_id(3), owner_org_id=world.headquarters.id, location_id=location.id, material_id=line.material_id, condition_code="new", availability_bucket="reserved")
    balance = StockBalance(stock_account_id=source.id, quantity=Decimal("2.000"), ledger_cursor=9, version=7)
    policy = MaterialInventoryPolicy(id=_id(4), material_id=line.material_id, tracking_mode="none", quantity_scale=3, allow_fraction=True, effective_from=NOW - timedelta(days=1))
    db.add(location)
    db.flush()
    db.add_all((source, target, balance, policy))
    db.flush()
    snapshot = SimpleNamespace(ledger_cursor=9, projected_at=NOW)
    # Authorization and exact account joins are real. These tests exercise the
    # composition boundary; inventory's independent suite proves ledger/opening
    # replay, so only those expensive proof providers are substituted here.
    monkeypatch.setattr(inventory_query, "_projection_snapshot", lambda *a, **k: snapshot)
    monkeypatch.setattr(inventory_query, "_validate_current_projection_integrity", Mock())
    monkeypatch.setattr(inventory_query, "_validated_opening_evidence", Mock(return_value=SimpleNamespace(complete=True)))
    monkeypatch.setattr(inventory_query, "_ensure_projection_snapshot_current", Mock())
    result = create_allocation(
        db, actor=actor, material_request_id=request.id, expected_request_version=version,
        allocation=AllocationCreateInput(request_line_id=line.id, source_stock_account_id=source.id, allocated_qty=Decimal("1.500"), source_balance_version=7, source_ledger_cursor=9),
        idempotency_key="reservation-option-allocation", idempotency_hmac_secret=SECRET, trace_request_id="reservation-option-allocation-trace",
    )
    db.flush()
    allocation = db.get(StockAllocation, result.allocation_id)
    return SimpleNamespace(db=db, world=world, request=request, line=line, actor=actor, source=source, target=target, balance=balance, allocation=allocation, policy=policy)


def _read(c, *, actor=None):
    return service.list_reservation_options(c.db, actor=actor or c.actor, material_request_id=c.request.id, request_line_id=c.line.id)


def _transaction(c, number=20):
    row = InventoryTransaction(id=_id(number), transaction_no=f"T-{number}", movement_type="reserve", source_document_type="material_request_reservation", source_document_id=str(_id(number + 1)), posting_key=f"T-{number}", idempotency_key_hash=f"{number:064x}", request_hash="a" * 64, status="posted", effective_at=NOW, posted_at=NOW, ledger_cursor=number, actor_user_id=c.actor.user_id)
    c.db.add(row)
    c.db.flush()
    return row


def _prior_reservation(c, *, quantity="0.500", status="reserved"):
    transaction = _transaction(c)
    row = StockReservation(id=_id(21), reservation_no="RS-OPTIONS-PRIOR", request_id=c.request.id, request_line_id=c.line.id, revision_id=c.line.revision_id, revision_no=c.line.revision_no, allocation_id=c.allocation.id, source_stock_account_id=c.source.id, stock_account_id=c.target.id, reserved_qty=Decimal(quantity), released_qty=Decimal("0"), reserve_transaction_id=transaction.id, status=status, request_version=c.request.version, idempotency_key_hash="b" * 64, request_hash="c" * 64, actor_user_id=c.actor.user_id, actor_person_id=c.actor.person_id, authorization_version=c.actor.authorization_version)
    c.db.add(row)
    c.db.flush()
    return row


def test_candidates_use_real_allocation_remainder_latest_balance_and_no_writes(candidate):
    c = candidate
    _prior_reservation(c)
    c.balance.quantity = Decimal("0.750")
    c.balance.version = 8
    c.db.flush()
    statements = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())
    event.listen(c.db.get_bind(), "before_cursor_execute", capture)
    try:
        output = _read(c)
    finally:
        event.remove(c.db.get_bind(), "before_cursor_execute", capture)
    assert set(statements) == {"SELECT"}
    assert not c.db.new and not c.db.dirty and not c.db.deleted
    assert output.request_version == c.request.version
    item, = output.items
    assert item.allocation_id == c.allocation.id
    assert item.allocation_no == c.allocation.allocation_no
    assert item.allocated_qty == "1.500"
    assert item.reserved_qty == "0.500"
    assert item.remaining_qty == "1.000"
    assert item.quantity == item.reservable_qty == "0.750"
    assert item.balance_version == 8
    assert item.ledger_cursor == 9
    assert item.tracking_mode == "none" and item.serial_options == ()
    assert c.request.reservation_status == "not_reserved"


def test_fulfilled_facts_do_not_reopen_allocation_capacity(candidate):
    c = candidate
    _prior_reservation(c, quantity="1.500", status="fulfilled")
    assert _read(c).items == ()


@pytest.mark.parametrize("resource", ("inventory", "material_request"))
def test_live_explicit_deny_rejects_previously_loaded_principal(candidate, resource):
    c = candidate
    permission = c.db.scalar(select(Permission).where(Permission.resource == resource, Permission.action == "read"))
    binding = c.db.scalar(select(RolePermission).where(RolePermission.role_id == c.world.roles["admin"].id, RolePermission.permission_id == permission.id))
    binding.effect = "deny"
    c.db.flush()
    with pytest.raises(service.MaterialRequestReservationOptionError) as caught:
        _read(c)
    assert caught.value.http_status_code in {403, 404}


def test_manager_cannot_discover_headquarters_source(candidate):
    c = candidate
    manager = _principal(c.db, c.world.manager_users[0].id)
    with pytest.raises(service.MaterialRequestReservationOptionError) as caught:
        _read(c, actor=manager)
    assert caught.value.code == "material_request_reservation_source_forbidden"
    assert caught.value.http_status_code == 403


def test_technician_is_not_a_reservation_operator(candidate):
    with pytest.raises(service.MaterialRequestReservationOptionError) as caught:
        _read(candidate, actor=_principal(candidate.db, candidate.world.actor_user.id))
    assert caught.value.code == "material_request_reservation_options_forbidden"


@pytest.mark.parametrize("bounded", ("reservations", "serials"))
def test_related_fact_limits_reject_truncation(candidate, monkeypatch, bounded):
    c = candidate
    if bounded == "reservations":
        _prior_reservation(c)
        monkeypatch.setattr(service, "MAX_RESERVATIONS", 0)
    else:
        serial = InventorySerial(id=_id(90), material_id=c.line.material_id, serial_no="SN-BOUND", qr_code="QR-BOUND", lifecycle_status="active")
        c.db.add(serial)
        c.db.flush()
        c.db.add(StockAllocationSerial(allocation_id=c.allocation.id, serial_id=serial.id))
        c.db.flush()
        monkeypatch.setattr(service, "MAX_SERIALS", 0)
    with pytest.raises(service.MaterialRequestReservationOptionError) as caught:
        _read(c)
    assert caught.value.code == "material_request_reservation_options_limit_exceeded"


def test_permission_change_during_read_is_not_returned(candidate, monkeypatch):
    c = candidate
    permission = c.db.scalar(select(Permission).where(Permission.resource == "inventory", Permission.action == "read"))
    binding = c.db.scalar(select(RolePermission).where(RolePermission.role_id == c.world.roles["admin"].id, RolePermission.permission_id == permission.id))
    def permission_changed(*_args):
        binding.effect = "deny"
        c.db.flush()
    monkeypatch.setattr(inventory_query, "_ensure_projection_snapshot_current", permission_changed)
    with pytest.raises(service.MaterialRequestReservationOptionError) as caught:
        _read(c)
    assert caught.value.code == "material_request_reservation_options_authorization_changed"


def _bind_lot(c):
    lot = InventoryLot(id=_id(95), material_id=c.line.material_id, lot_no="LOT-OPTIONS-95")
    c.db.add(lot)
    c.db.flush()
    c.source.lot_id = c.target.lot_id = lot.id
    c.db.flush()
    return lot


@pytest.mark.parametrize(("tracking_mode", "has_lot"), (
    ("none", True), ("serial", True), ("lot", False), ("lot_and_serial", False),
))
def test_current_tracking_policy_rejects_incompatible_account_lot(candidate, tracking_mode, has_lot):
    c = candidate
    c.policy.tracking_mode = tracking_mode
    if has_lot:
        _bind_lot(c)
    c.db.flush()
    with pytest.raises(service.MaterialRequestReservationOptionError) as caught:
        _read(c)
    assert caught.value.code == "material_request_reservation_source_lot_invalid"
    assert caught.value.http_status_code == 409


def test_lot_candidate_preserves_exact_current_material_batch(candidate):
    c = candidate
    c.policy.tracking_mode = "lot"
    lot = _bind_lot(c)
    item, = _read(c).items
    assert item.tracking_mode == "lot"
    assert item.material_id == lot.material_id == c.line.material_id
    assert item.lot_id == lot.id and item.lot_no == lot.lot_no
    assert item.serial_options == ()
    assert item.reservable_qty == "1.500"


@pytest.mark.parametrize("bad_dimension", ("material", "identity", "empty_no", "blank_no"))
def test_invalid_joined_lot_dimensions_fail_with_stable_error(candidate, monkeypatch, bad_dimension):
    c = candidate
    c.policy.tracking_mode = "lot"
    lot = _bind_lot(c)
    invalid = SimpleNamespace(id=lot.id, material_id=lot.material_id, lot_no=lot.lot_no)
    if bad_dimension == "material":
        invalid.material_id = c.world.materials[1].id
    elif bad_dimension == "identity":
        invalid.id = _id(96)
    elif bad_dimension == "empty_no":
        invalid.lot_no = ""
    else:
        invalid.lot_no = " LOT-OPTIONS-95 "
    original = inventory_query._account_row
    # An inconsistent projection from the shared reader must not be presented
    # as a usable candidate, even before DTO validation or the later posting.
    monkeypatch.setattr(inventory_query, "_account_row", lambda values: replace(original(values), lot=invalid))
    with pytest.raises(service.MaterialRequestReservationOptionError) as caught:
        _read(c)
    assert caught.value.code == "material_request_reservation_source_lot_invalid"
    assert caught.value.http_status_code == 409


@pytest.mark.parametrize("failure", ("opening", "ledger", "request", "target", "source", "revision", "limit"))
def test_stale_missing_or_unproven_candidates_fail_closed(candidate, monkeypatch, failure):
    c = candidate
    expected = {
        "opening": "inventory_opening_not_established",
        "ledger": "inventory_projection_changed",
        "request": "material_request_revision_stale",
        "target": "material_request_reservation_target_missing",
        "source": "material_request_reservation_source_invalid",
        "revision": "material_request_reservation_options_fact_invalid",
        "limit": "material_request_reservation_options_limit_exceeded",
    }
    if failure == "opening":
        monkeypatch.setattr(inventory_query, "_validated_opening_evidence", lambda *a, **k: SimpleNamespace(complete=False))
    elif failure == "ledger":
        monkeypatch.setattr(inventory_query, "_ensure_projection_snapshot_current", Mock(side_effect=inventory_query.InventoryReadError(code=expected[failure], status_code=409, message="库存游标已变化")))
    elif failure == "request":
        original = material_request_query.material_request_detail
        calls = 0
        def changed(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = original(*args, **kwargs)
            return result if calls == 1 else result.model_copy(update={"request_version": result.request_version + 1})
        monkeypatch.setattr(material_request_query, "material_request_detail", changed)
    elif failure == "target":
        c.db.delete(c.target)
    elif failure == "source":
        c.source.availability_bucket = "frozen"
    elif failure == "revision":
        c.allocation.revision_no += 1
    elif failure == "limit":
        monkeypatch.setattr(service, "MAX_ALLOCATIONS", 0)
    c.db.flush()
    with pytest.raises(service.MaterialRequestReservationOptionError) as caught:
        _read(c)
    assert caught.value.code == expected[failure]


@pytest.mark.parametrize("tracking_mode", ("serial", "lot_and_serial"))
def test_serial_options_are_unreserved_active_members_at_exact_source(candidate, tracking_mode):
    c = candidate
    c.policy.tracking_mode = tracking_mode
    c.policy.quantity_scale = 0
    c.policy.allow_fraction = False
    c.allocation.allocated_qty = Decimal("2.000")
    lot_id = _bind_lot(c).id if tracking_mode == "lot_and_serial" else None
    c.db.flush()
    prior = _prior_reservation(c, quantity="1.000")
    transaction = c.db.get(InventoryTransaction, prior.reserve_transaction_id)
    movement = InventoryMovement(id=_id(25), transaction_id=transaction.id, line_no=1, from_account_id=c.source.id, to_account_id=c.target.id, quantity=Decimal("1.000"))
    c.db.add(movement)
    c.db.flush()
    for number in (30, 31, 32):
        serial = InventorySerial(id=_id(number), material_id=c.line.material_id, serial_no=f"SN-{number}", qr_code=f"QR-{number}", lifecycle_status="active", lot_id=lot_id)
        c.db.add(serial)
        c.db.flush()
        c.db.add(SerialCurrentPosition(serial_id=serial.id, stock_account_id=c.source.id, last_movement_id=movement.id))
        if number != 32:
            c.db.add(StockAllocationSerial(allocation_id=c.allocation.id, serial_id=serial.id))
    c.db.flush()
    c.db.add(StockReservationSerial(reservation_id=prior.id, allocation_id=c.allocation.id, serial_id=_id(30)))
    c.db.flush()
    item, = _read(c).items
    assert [row.serial_no for row in item.serial_options] == ["SN-31"]
    assert item.lot_id == lot_id
    assert item.serial_options[0].lot_id == lot_id
    assert item.reserved_qty == "1" and item.remaining_qty == item.reservable_qty == "1"
    c.db.get(SerialCurrentPosition, _id(31)).stock_account_id = c.target.id
    c.db.flush()
    assert _read(c).items == ()


def test_router_is_get_only_read_permission_and_no_store(candidate, monkeypatch):
    c = candidate
    output = _read(c)
    api = FastAPI()
    api.include_router(formal_material_requests.router, prefix="/api")
    api.dependency_overrides[get_formal_principal] = lambda: c.actor
    api.dependency_overrides[get_db] = lambda: SimpleNamespace(rollback=Mock())
    mocked = Mock(return_value=output)
    monkeypatch.setattr(service, "list_reservation_options", mocked)
    path = f"/api/v1/material-requests/{c.request.id}/reservation-options"
    with TestClient(api) as client:
        response = client.get(path, params={"request_line_id": str(c.line.id)})
        assert response.status_code == 200
        assert response.json()["items"][0]["allocation_id"] == str(c.allocation.id)
        assert "no-store" in response.headers["Cache-Control"]
        assert client.post(path, json={}).status_code == 405
        for error in (
            service.MaterialRequestReservationOptionError("inventory_read_denied", "forbidden", "没有库存读取权限"),
            OperationalError("secret sql", {}, Exception("db secret")),
        ):
            mocked.side_effect = error
            response = client.get(path, params={"request_line_id": str(c.line.id)})
            assert response.status_code in {403, 503}
            assert "no-store" in response.headers["Cache-Control"]
            assert "secret" not in response.text
        mocked.reset_mock()
        denied_actor = replace(c.actor, entitlements=tuple(row for row in c.actor.entitlements if row.resource != "material_request"))
        api.dependency_overrides[get_formal_principal] = lambda: denied_actor
        assert client.get(path, params={"request_line_id": str(c.line.id)}).status_code == 403
        mocked.assert_not_called()


def test_candidate_schema_rejects_extra_or_inconsistent_capacity(candidate):
    from pydantic import ValidationError
    output = _read(candidate)
    data = output.model_dump()
    data["items"][0]["reservable_qty"] = "99.000"
    with pytest.raises(ValidationError):
        MaterialRequestReservationOptionPageOut.model_validate(data)
    with pytest.raises(ValidationError):
        MaterialRequestReservationOptionPageOut.model_validate({**output.model_dump(), "unexpected": True})
