"""Picking command tests use synthetic posting here; PG16 proves real posting."""
from decimal import Decimal
from uuid import uuid4, UUID
from dataclasses import replace
import pytest
from sqlalchemy import select
from app.inventory_models import StockAccount, StockBalance, StockReservationPick, OutboundOrder, OutboundLine
from app.formal_services import material_request_picking as pick
from app.formal_services import material_request_reservation_release as release
from app.material_request_picking_schemas import ReservationPickOut
from test_material_request_reservation_release import release_world
from test_material_request_fulfillment_preparation import approval_db
from test_material_request_draft_service import SECRET


@pytest.fixture
def pick_world(release_world, monkeypatch):
    db, actor, request, original, serials, calls = release_world
    source = db.get(StockAccount, original.stock_account_id)
    target = StockAccount(id=uuid4(), availability_bucket="picking", **{
        name: getattr(source, name) for name in ("owner_org_id", "custodian_person_id", "location_id",
                                               "material_id", "condition_code", "lot_id")})
    db.add(target)
    db.flush()
    db.add(StockBalance(stock_account_id=target.id, quantity=Decimal("0.000"), version=0, ledger_cursor=0))
    db.flush()
    monkeypatch.setattr(pick, "post_inventory_transaction", release.post_inventory_transaction)
    return release_world


def _input(world, qty=None, serials=None):
    db, actor, request, original, bound, calls = world
    balance = db.get(StockBalance, original.stock_account_id)
    return pick.ReservationPickInput(original.id, Decimal(qty or ("1.000" if bound else "0.125")),
        "实物与原占用核对一致", balance.version, balance.ledger_cursor, bound[:1] if serials is None else serials)


def _create(world, value=None, key="picking-test-command-0001", version=None):
    db, actor, request, *_ = world
    return pick.create_pick(db, actor=actor, material_request_id=request.id,
        expected_request_version=request.version if version is None else version,
        pick=value or _input(world), idempotency_key=key,
        idempotency_hmac_secret=SECRET, trace_request_id=f"trace-{key}")


def test_split_pick_preserves_original_and_recovers(pick_world):
    db, actor, request, original, serials, calls = pick_world
    before = release.reserve._state_axes(request)
    version = request.version
    value = _input(pick_world)
    first = _create(pick_world, value)
    assert ReservationPickOut(**first).state_axes.outbound_status == "pending_pick"
    assert original.released_qty == 0 and original.status == "reserved"
    assert all(first["state_axes"][k] == v for k, v in before.items() if k != "outbound_status")
    assert _create(pick_world, value, version=version)["pick_id"] == first["pick_id"]
    assert len(calls) == 1
    assert db.get(OutboundLine, UUID(first["outbound_line_id"])).outbound_qty == 0
    order = db.scalar(select(OutboundOrder))
    assert order.status == "picked" and order.outbound_at is None
    rest = original.reserved_qty - value.picked_qty
    second = _create(pick_world, _input(pick_world, str(rest), serials[1:]), key="picking-test-command-0002")
    recovered = pick.pick_command_status(db, actor=actor, trace_request_id="trace-picking-test-command-0001")
    assert recovered["pick_id"] == first["pick_id"] and recovered["current_request_version"] == second["request_version"]
    assert pick.picked_quantity(db, original.id) == original.reserved_qty
    assert db.get(StockBalance, original.stock_account_id).quantity == 0
    assert len(calls) == 2


@pytest.mark.parametrize("bad", ["over", "stale", "serial", "version", "target"])
def test_invalid_pick_never_posts(pick_world, bad):
    db, actor, request, original, serials, calls = pick_world
    value = _input(pick_world)
    version = request.version
    if bad == "over": value = replace(value, picked_qty=original.reserved_qty + 1)
    if bad == "stale": value = replace(value, source_balance_version=value.source_balance_version + 1)
    if bad == "serial": value = replace(value, serial_ids=(uuid4(),))
    if bad == "version": version -= 1
    if bad == "target":
        db.scalar(select(StockAccount).where(StockAccount.availability_bucket == "picking")).condition_code = "damaged"
        db.flush()
    with pytest.raises(pick.MaterialRequestReservationPickError):
        _create(pick_world, value, version=version)
    assert not calls
    assert db.scalar(select(StockReservationPick)) is None
    assert db.scalar(select(OutboundOrder)) is None


def test_released_slice_cannot_be_picked(pick_world):
    from test_material_request_reservation_release import _create as do_release, _input as release_input
    db, actor, request, original, serials, calls = pick_world
    value = release_input(db, original, "1.000" if serials else "0.125", serials[:1])
    do_release(pick_world, value)
    with pytest.raises(pick.MaterialRequestReservationPickError):
        _create(pick_world, _input(pick_world, str(original.reserved_qty), serials))
    assert len(calls) == 1
    _create(pick_world, _input(pick_world, str(original.reserved_qty - value.released_qty), serials[1:]))
    assert len(calls) == 2


def test_pick_audit_failure_rolls_back_every_fact(pick_world, monkeypatch):
    db, actor, request, original, serials, calls = pick_world
    db.commit()
    value = _input(pick_world)
    version, quantity = request.version, db.get(StockBalance, original.stock_account_id).quantity
    def fail(*args, **kwargs): raise pick.AuditChainError("injected")
    monkeypatch.setattr(pick, "append_audit_event", fail)
    with pytest.raises(pick.MaterialRequestReservationPickError):
        _create(pick_world, value)
    db.rollback()
    assert request.version == version
    assert db.get(StockBalance, original.stock_account_id).quantity == quantity
    assert not db.scalars(select(StockReservationPick)).all()
    assert not db.scalars(select(OutboundOrder)).all()


def test_options_exclude_consumed_original_quantity_and_serials(pick_world, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.formal_services import material_request_picking_options as options
    from test_material_request_fulfillment_preparation import REAL_REQUIRE_READ, snapshot
    monkeypatch.setattr(options.inventory_query, "_require_inventory_read", REAL_REQUIRE_READ)
    monkeypatch.setattr(options.inventory_query, "_projection_snapshot", lambda db:
        SimpleNamespace(ledger_cursor=1000, projected_at=datetime.now(timezone.utc)))
    monkeypatch.setattr(options.inventory_query, "_ensure_projection_snapshot_current", lambda *args: None)
    db, actor, request, original, serials, calls = pick_world
    def read():
        return options.list_picking_options(db, actor=actor, material_request_id=request.id, request_line_id=original.request_line_id)
    before = snapshot(pick_world)
    row = read().items[0]
    assert Decimal(row.pickable_qty) == original.reserved_qty
    assert snapshot(pick_world) == before
    value = _input(pick_world)
    _create(pick_world, value)
    before = snapshot(pick_world)
    row = read().items[0]
    assert Decimal(row.picked_qty) == value.picked_qty
    assert Decimal(row.pickable_qty) == original.reserved_qty - value.picked_qty
    assert {s.serial_id for s in row.serials} == set(serials[1:])
    assert snapshot(pick_world) == before


@pytest.mark.parametrize("tamper", ["line", "order", "command", "movement", "audit", "authorization"])
def test_recovery_rejects_broken_evidence_or_changed_identity(pick_world, tamper):
    from app.demand_models import MaterialRequestCommand
    from app.foundation_models import AuditEvent
    from app.inventory_models import InventoryMovement
    db, actor, request, original, serials, calls = pick_world
    result = _create(pick_world)
    if tamper == "line": db.get(OutboundLine, UUID(result["outbound_line_id"])).picked_qty += 1
    elif tamper == "order": db.scalar(select(OutboundOrder)).status = "outbound"
    elif tamper == "command": db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == "pick")).result_hash = "f" * 64
    elif tamper == "movement": db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == UUID(result["pick_transaction_id"]))).quantity += 1
    elif tamper == "audit": db.scalar(select(AuditEvent).where(AuditEvent.action == pick.ACTION)).event_hash = "f" * 64
    else: actor = replace(actor, authorization_version=actor.authorization_version + 1)
    # Invalid document CHECKs reject corruption at flush; other immutable
    # evidence corruption is rejected by the historical verifier itself.
    if tamper in {"line", "order"}:
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError): db.flush()
        db.rollback()
    else:
        db.flush()
        with pytest.raises(pick.MaterialRequestReservationPickError):
            pick.pick_command_status(db, actor=actor, trace_request_id="trace-picking-test-command-0001")
    assert len(calls) == 1


def test_http_pick_and_original_trace_recovery(pick_world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.routers import formal_material_requests
    db, actor, request, original, serials, calls = pick_world
    app = FastAPI()
    app.include_router(formal_material_requests.router, prefix="/api")
    app.include_router(formal_material_requests.command_status_router, prefix="/api")
    def get_session(): yield db
    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[get_formal_principal] = lambda: actor
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, environment="test",
        database_url="sqlite+pysqlite:///:memory:", database_schema_mode="alembic", material_request_writes_enabled=True,
        material_request_idempotency_hmac_secret=SECRET.decode(),
        material_request_contact_mobile_hmac_secret="picking-http-contact-mobile-secret",
        material_request_contact_mobile_hash_version=1, material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id="kms-picking-http-contact", auth_idempotency_kms_key_id="kms-picking-http-auth")
    value = _input(pick_world)
    body = dict(expected_request_version=request.version, reservation_id=str(original.id), picked_qty=str(value.picked_qty),
        reason=value.reason, source_balance_version=value.source_balance_version, source_ledger_cursor=value.source_ledger_cursor,
        serial_ids=[str(s) for s in value.serial_ids])
    headers = {"Idempotency-Key": "picking-http-idempotency-key", "X-Request-ID": "picking-http-original-trace"}
    with TestClient(app) as client:
        response = client.post(f"/api/v1/material-requests/{request.id}/reservation-picks", json=body, headers=headers)
        assert response.status_code == 201, response.text
        result = ReservationPickOut(**response.json())
        assert result.outbound_id == result.pick_id
        assert response.headers["cache-control"].startswith("no-store")
        replay = client.post(f"/api/v1/material-requests/{request.id}/reservation-picks", json=body, headers=headers)
        assert replay.status_code == 201 and replay.json()["idempotency_replayed"]
        before_calls = len(calls)
        recovered = client.get("/api/v1/material-request-reservation-pick-command-status", headers={"X-Request-ID": headers["X-Request-ID"]})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["lookup_status"] == "confirmed"
        assert recovered.json()["command"]["pick_id"] == str(result.pick_id)
        assert len(calls) == before_calls == 1
