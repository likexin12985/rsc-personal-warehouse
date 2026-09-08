"""Preparation read semantics; PG16 gate independently proves real projections."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.demand_models import MaterialRequestCommand
from app.foundation_models import AuditEvent, OutboxEvent, Permission, RolePermission
from app.inventory_models import InventoryMovement, InventoryTransaction, SerialCurrentPosition, StockAccount, StockBalance, StockReservationRelease
from app.formal_services import material_request_fulfillment_preparation as preparation
from app.material_request_fulfillment_preparation_schemas import FulfillmentPreparationOut
from app.database import Base
from test_material_request_approval_service import _principal
from test_material_request_reservation_release import release_world, _create, _input

REAL_REQUIRE_READ = preparation.inventory_query._require_inventory_read


@pytest.fixture
def approval_db():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture
def world(release_world, monkeypatch):
    # The underlying fixture uses synthetic posting; no local test claims PG
    # projection integrity. The actual API role/projection is tested in CI.
    monkeypatch.setattr(preparation.inventory_query, "_projection_snapshot", lambda db:
        SimpleNamespace(ledger_cursor=1000, projected_at=datetime.now(timezone.utc)))
    monkeypatch.setattr(preparation.inventory_query, "_ensure_projection_snapshot_current", lambda *a: None)
    monkeypatch.setattr(preparation.inventory_query, "_require_inventory_read", REAL_REQUIRE_READ)
    return release_world


def read(world, **overrides):
    db, actor, request, original, *_ = world
    return preparation.list_fulfillment_preparation(db, **{
        "actor": actor, "material_request_id": request.id, "request_line_id": original.request_line_id, **overrides})


def snapshot(world):
    db, _, request, *_ = world
    return (
        request.version, preparation.reserve._state_axes(request),
        tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity, StockBalance.version, StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id)).all()),
        tuple(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id).order_by(SerialCurrentPosition.serial_id)).all()),
        tuple(db.scalar(select(func.count()).select_from(model)) for model in
            (StockReservationRelease, InventoryTransaction, InventoryMovement, MaterialRequestCommand, AuditEvent, OutboxEvent)),
    )


def test_original_partial_and_full_release_are_read_only_and_conserve(world):
    db, _, _, original, serials, calls = world
    before = snapshot(world)
    page = read(world)
    row = page.items[0]
    assert row.reservation_id == original.id and row.allocation_id == original.allocation_id
    assert row.preparation_status == "ready_for_review"
    assert Decimal(row.verified_held_qty) == original.reserved_qty
    assert {s.serial_id for s in row.serials} == set(serials)
    assert snapshot(world) == before and not calls
    amount = Decimal(1) if serials else Decimal("0.125")
    _create(world, _input(db, original, str(amount), serials[:1]))
    before = snapshot(world)
    row = read(world).items[0]
    assert Decimal(row.released_qty) == amount
    assert Decimal(row.remaining_reserved_qty) == original.reserved_qty - amount
    assert row.verified_held_qty == row.remaining_reserved_qty
    assert {s.serial_id for s in row.serials} == set(serials[1:])
    assert snapshot(world) == before
    _create(world, _input(db, original, str(original.reserved_qty - amount), serials[1:]), key="prepare-full-release-002")
    before = snapshot(world)
    row = read(world).items[0]
    assert row.preparation_status == "released" and row.remaining_reserved_qty == row.verified_held_qty == "0.000"
    assert not row.serials and not row.blockers
    assert snapshot(world) == before


def test_authorized_reader_can_verify_release_after_original_authorization_version_changes(world):
    from app.models import User
    db, actor, _, original, serials, _ = world
    _create(world, _input(db, original, "1.000" if serials else "0.125", serials[:1]))
    user = db.get(User, actor.user_id)
    user.authorization_version += 1
    db.flush()
    current = _principal(db, actor.user_id)
    assert current.authorization_version != actor.authorization_version
    assert read(world, actor=current).items[0].preparation_status == "ready_for_review"
    # This is a new authorized read, not permission to recover the old command.
    with pytest.raises(preparation.release.MaterialRequestReservationReleaseError):
        preparation.release.release_command_status(db, actor=current, trace_request_id="trace-release-test-command-0001")


@pytest.mark.parametrize("failure", ["pool_shortfall", "source_inactive", "serial_mismatch"])
def test_unavailable_evidence_is_blocked_instead_of_borrowing_inventory(world, failure):
    db, _, _, original, serials, _ = world
    if failure == "pool_shortfall":
        db.get(StockBalance, original.stock_account_id).quantity -= Decimal("0.001")
    elif failure == "source_inactive":
        from app.inventory_models import StockLocation
        account = db.get(StockAccount, original.stock_account_id)
        db.get(StockLocation, account.location_id).status = "inactive"
    else:
        if not serials:
            pytest.skip("SN evidence is applicable to serial-tracked material")
        db.get(SerialCurrentPosition, serials[0]).stock_account_id = original.source_stock_account_id
    db.flush()
    before = snapshot(world)
    row = read(world).items[0]
    assert row.preparation_status == "blocked" and failure in row.blockers
    assert row.verified_held_qty == "0.000" and not row.serials
    assert Decimal(row.remaining_reserved_qty) == original.reserved_qty
    assert snapshot(world) == before


@pytest.mark.parametrize("tamper", ["release_movement", "release_hash", "reserve_hash"])
def test_preparation_verifies_immutable_reserve_and_release_history(world, tamper):
    db, _, _, original, serials, _ = world
    _create(world, _input(db, original, "1.000" if serials else "0.125", serials[:1]))
    if tamper == "release_movement":
        fact = db.scalar(select(StockReservationRelease))
        db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == fact.release_transaction_id)).quantity += Decimal("0.001")
    else:
        operation = "reserve" if tamper == "reserve_hash" else "release"
        db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == operation)).result_hash = "f" * 64
    db.flush()
    with pytest.raises(preparation.reserve.MaterialRequestReservationError, match="证据|历史"):
        read(world)


@pytest.mark.parametrize("failure", ["line", "outbound", "read_denied", "scope_denied", "opening", "snapshot", "request_change", "limit"])
def test_preparation_fails_closed_on_scope_version_or_evidence_changes(world, monkeypatch, failure):
    db, actor, request, original, *_ = world
    kwargs = {}
    if failure == "line":
        kwargs["request_line_id"] = uuid4()
    elif failure == "outbound":
        request.outbound_status = "pending_pick"
        db.flush()
    elif failure == "read_denied":
        permission = db.scalar(select(Permission).where(Permission.resource == "inventory", Permission.action == "read"))
        db.scalar(select(RolePermission).where(RolePermission.permission_id == permission.id)).effect = "deny"
        db.flush()
        kwargs["actor"] = _principal(db, actor.user_id)
    elif failure == "scope_denied":
        monkeypatch.setattr(preparation.inventory_query, "_account_allowed", lambda *a: False)
    elif failure == "opening":
        monkeypatch.setattr(preparation.inventory_query, "_validated_opening_evidence", lambda *a, **k: SimpleNamespace(complete=False))
    elif failure == "snapshot":
        def changed(*args):
            raise preparation.inventory_query.InventoryReadError(code="inventory_projection_changed", status_code=409, message="快照变化")
        monkeypatch.setattr(preparation.inventory_query, "_ensure_projection_snapshot_current", changed)
    elif failure == "request_change":
        original_read = preparation.material_request_query.material_request_detail
        count = 0
        def change(*args, **kwargs):
            nonlocal count
            count += 1
            value = original_read(*args, **kwargs)
            return value.model_copy(update={"request_version": value.request_version + 1}) if count == 2 else value
        monkeypatch.setattr(preparation.material_request_query, "material_request_detail", change)
    else:
        monkeypatch.setattr(preparation, "MAX_RESERVATIONS", 0)
    with pytest.raises((preparation.reserve.MaterialRequestReservationError, preparation.material_request_query.MaterialRequestReadError)):
        read(world, **kwargs)


@pytest.mark.parametrize("bad", ["negative_zero", "scientific", "unbalanced", "duplicate", "over_cursor", "false_ready", "naive_time", "zero_uuid"])
def test_schema_rejects_misleading_preparation_evidence(world, bad):
    payload = read(world).model_dump(mode="json")
    row = payload["items"][0]
    if bad == "negative_zero": row["released_qty"] = "-0.000"
    elif bad == "scientific": row["reserved_qty"] = "1e0"
    elif bad == "unbalanced": row["released_qty"] = "999.000"
    elif bad == "duplicate": payload["items"].append(row.copy())
    elif bad == "over_cursor": row["source_ledger_cursor"] = payload["ledger_cursor"] + 1
    elif bad == "false_ready": row["blockers"] = ["pool_shortfall"]
    elif bad == "naive_time": payload["projected_at"] = "2026-09-08T00:00:00"
    else: row["reservation_id"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValidationError): FulfillmentPreparationOut(**payload)


def test_http_read_is_no_store_and_has_no_write_method(world):
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.routers import formal_material_requests
    db, actor, request, original, *_ = world
    app = FastAPI()
    app.include_router(formal_material_requests.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_formal_principal] = lambda: actor
    path = f"/api/v1/material-requests/{request.id}/fulfillment-preparation"
    before = snapshot(world)
    with TestClient(app) as client:
        result = client.get(path, params={"request_line_id": str(original.request_line_id)})
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"].startswith("no-store")
        assert result.json()["scope"] == "authorized_sources"
        assert client.post(path, json={}).status_code == 405
    assert snapshot(world) == before
