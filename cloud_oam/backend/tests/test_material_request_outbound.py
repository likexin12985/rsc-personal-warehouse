"""Service/API regressions; PG16 separately proves real ledger enforcement."""
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, func

from app.demand_models import MaterialRequestCommand
from app.foundation_models import AuditEvent
from app.inventory_models import (
    StockAccount, StockBalance, StockReservationPick, OutboundPosting, OutboundOrder, OutboundLine,
    InventoryMovement, InventoryTransaction, SerialCurrentPosition,
)
from app.formal_services import material_request_outbound as outbound, material_request_picking as picking
from app.material_request_outbound_schemas import OutboundOut, OutboundIn
from test_material_request_picking import pick_world, _create as create_pick, _input as pick_input
from test_material_request_reservation_release import release_world
from test_material_request_fulfillment_preparation import approval_db
from test_material_request_draft_service import SECRET


@pytest.fixture
def outbound_world(pick_world, monkeypatch):
    db, actor, request, original, serials, calls = pick_world
    picked = create_pick(pick_world, pick_input(pick_world, str(original.reserved_qty), serials))
    fact = db.get(StockReservationPick, UUID(picked["pick_id"]))
    source = db.get(StockAccount, fact.target_stock_account_id)
    target = StockAccount(id=uuid4(), availability_bucket="in_transit", **{
        name: getattr(source, name) for name in ("owner_org_id", "custodian_person_id", "location_id",
                                               "material_id", "condition_code", "lot_id")})
    db.add(target)
    db.flush()
    db.add(StockBalance(stock_account_id=target.id, quantity=Decimal("0.000"), version=0, ledger_cursor=0))
    db.flush()
    monkeypatch.setattr(outbound, "post_inventory_transaction", picking.post_inventory_transaction)
    calls.clear()
    return db, actor, request, fact, serials, calls, target.id


def _input(world, qty=None, serials=None):
    db, _, _, fact, bound, _, target_id = world
    balance = db.get(StockBalance, fact.target_stock_account_id)
    return outbound.OutboundInput(fact.id, target_id, Decimal(qty or ("1.000" if bound else "0.125")),
        "确认实物已离开来源位置", balance.version, balance.ledger_cursor, bound[:1] if serials is None else serials)


def _create(world, value=None, key="outbound-test-command-0001", version=None):
    db, actor, request, *_ = world
    return outbound.create_outbound(db, actor=actor, material_request_id=request.id,
        expected_request_version=request.version if version is None else version,
        outbound=value or _input(world), idempotency_key=key,
        idempotency_hmac_secret=SECRET, trace_request_id=f"trace-{key}")


def _snapshot(world):
    db, _, request, fact, _, _, target_id = world
    return (
        request.version, picking.reserve._state_axes(request),
        tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity, StockBalance.version,
                                StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id))),
        tuple(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id,
                                SerialCurrentPosition.last_movement_id).order_by(SerialCurrentPosition.serial_id))),
        tuple(db.scalar(select(func.count()).select_from(model)) for model in
              (OutboundPosting, InventoryTransaction, InventoryMovement, MaterialRequestCommand, AuditEvent)),
    )


def test_split_outbound_conserves_stock_and_old_pick_recovery(outbound_world):
    db, actor, request, fact, serials, calls, target_id = outbound_world
    before_axes = picking.reserve._state_axes(request)
    before_version = request.version
    value = _input(outbound_world)
    first = _create(outbound_world, value)
    assert OutboundOut(**first).pick_id == fact.id
    assert all(first["state_axes"][k] == v for k, v in before_axes.items() if k != "outbound_status")
    stable = _snapshot(outbound_world)
    replay = _create(outbound_world, value, version=before_version)
    assert replay["posting_id"] == first["posting_id"] and replay["idempotency_replayed"]
    assert _snapshot(outbound_world) == stable and len(calls) == 1
    rest = fact.picked_qty - value.outbound_qty
    second = _create(outbound_world, _input(outbound_world, str(rest), serials[1:]), key="outbound-test-command-0002")
    assert outbound.outbound_quantity(db, fact.id) == fact.picked_qty
    assert db.get(StockBalance, fact.target_stock_account_id).quantity == 0
    assert db.get(StockBalance, target_id).quantity == fact.picked_qty
    assert all(db.get(SerialCurrentPosition, s).stock_account_id == target_id for s in serials)
    stable = _snapshot(outbound_world)
    recovered = outbound.outbound_command_status(db, actor=actor, trace_request_id="trace-outbound-test-command-0001")
    assert recovered["request_version"] == first["request_version"]
    assert recovered["current_request_version"] == second["request_version"]
    old_pick = picking.pick_command_status(db, actor=actor, trace_request_id="trace-picking-test-command-0001")
    assert old_pick["pick_id"] == str(fact.id) and old_pick["current_request_version"] == second["request_version"]
    assert _snapshot(outbound_world) == stable and len(calls) == 2
    assert db.get(OutboundLine, fact.outbound_line_id).outbound_qty == 0
    assert db.get(OutboundOrder, fact.id).outbound_at is None
    with pytest.raises(outbound.MaterialRequestOutboundError):
        _create(outbound_world, _input(outbound_world), key="outbound-exhausted-command")
    assert len(calls) == 2


@pytest.mark.parametrize("bad", ["over", "stale", "serial", "pick", "target", "version", "reason", "precision", "pool"])
def test_invalid_outbound_has_no_effect(outbound_world, bad):
    db, actor, request, fact, serials, calls, target_id = outbound_world
    value = _input(outbound_world)
    version = request.version
    if bad == "over": value = replace(value, outbound_qty=fact.picked_qty + 1)
    elif bad == "stale": value = replace(value, source_balance_version=value.source_balance_version + 1)
    elif bad == "serial": value = replace(value, serial_ids=(uuid4(),))
    elif bad == "pick": value = replace(value, pick_id=uuid4())
    elif bad == "target": value = replace(value, target_stock_account_id=uuid4())
    elif bad == "version": version -= 1
    elif bad == "reason": value = replace(value, reason=" ")
    elif bad == "precision": value = replace(value, outbound_qty=Decimal("0.0001"))
    elif bad == "pool": db.get(StockBalance, fact.target_stock_account_id).quantity -= Decimal("0.001"); db.flush()
    stable = _snapshot(outbound_world)
    with pytest.raises(outbound.MaterialRequestOutboundError):
        _create(outbound_world, value, version=version)
    assert _snapshot(outbound_world) == stable and not calls


def test_outbound_audit_failure_rolls_back_entire_posting(outbound_world, monkeypatch):
    db, *_ = outbound_world
    db.commit()
    stable = _snapshot(outbound_world)
    def fail(*args, **kwargs): raise outbound.AuditChainError("injected")
    monkeypatch.setattr(outbound, "append_audit_event", fail)
    with pytest.raises(outbound.MaterialRequestOutboundError):
        _create(outbound_world)
    db.rollback()
    assert _snapshot(outbound_world) == stable


@pytest.mark.parametrize("tamper", ["command", "movement", "audit", "coordinate", "original", "authorization"])
def test_outbound_recovery_requires_original_evidence(outbound_world, tamper):
    db, actor, request, fact, serials, calls, target_id = outbound_world
    result = _create(outbound_world)
    posted = db.get(OutboundPosting, UUID(result["posting_id"]))
    if tamper == "command":
        db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == "outbound")).result_hash = "f" * 64
    elif tamper == "movement":
        db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == posted.outbound_transaction_id)).quantity += 1
    elif tamper == "audit":
        db.scalar(select(AuditEvent).where(AuditEvent.action == outbound.ACTION)).event_hash = "f" * 64
    elif tamper == "coordinate": posted.source_ledger_cursor += 1
    elif tamper == "original": posted.pick_id = uuid4()
    else: actor = replace(actor, authorization_version=actor.authorization_version + 1)
    if tamper == "original":
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        assert len(calls) == 1
        return
    db.flush()
    with pytest.raises(outbound.MaterialRequestOutboundError):
        outbound.outbound_command_status(db, actor=actor, trace_request_id="trace-outbound-test-command-0001")
    assert len(calls) == 1


def test_outbound_options_follow_original_quantity_and_serials_without_writes(outbound_world, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.formal_services import material_request_outbound_options as options
    from test_material_request_fulfillment_preparation import REAL_REQUIRE_READ
    monkeypatch.setattr(options.inventory_query, "_require_inventory_read", REAL_REQUIRE_READ)
    monkeypatch.setattr(options.inventory_query, "_projection_snapshot", lambda db:
        SimpleNamespace(ledger_cursor=1000, projected_at=datetime.now(timezone.utc)))
    monkeypatch.setattr(options.inventory_query, "_ensure_projection_snapshot_current", lambda *args: None)
    db, actor, request, fact, serials, calls, _ = outbound_world
    def read():
        return options.list_outbound_options(db, actor=actor, material_request_id=request.id, request_line_id=fact.request_line_id)
    stable = _snapshot(outbound_world)
    row = read().items[0]
    assert Decimal(row.outboundable_qty) == fact.picked_qty and row.last_outbound_at is None
    assert _snapshot(outbound_world) == stable
    result = _create(outbound_world)
    stable = _snapshot(outbound_world)
    row = read().items[0]
    assert Decimal(row.outbound_qty) == Decimal(result["outbound_qty"])
    assert Decimal(row.outboundable_qty) + Decimal(row.outbound_qty) == fact.picked_qty
    assert row.last_outbound_at is not None
    assert {s.serial_id for s in row.serials} == set(serials[1:])
    assert _snapshot(outbound_world) == stable


def test_http_outbound_and_trace_recovery(outbound_world):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.routers import formal_material_requests
    db, actor, request, fact, serials, calls, target_id = outbound_world
    app = FastAPI()
    app.include_router(formal_material_requests.router, prefix="/api")
    app.include_router(formal_material_requests.command_status_router, prefix="/api")
    def get_session(): yield db
    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[get_formal_principal] = lambda: actor
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, environment="test",
        database_url="sqlite+pysqlite:///:memory:", database_schema_mode="alembic", material_request_writes_enabled=True,
        material_request_idempotency_hmac_secret=SECRET.decode(),
        material_request_contact_mobile_hmac_secret="outbound-http-contact-mobile-secret",
        material_request_contact_mobile_hash_version=1, material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id="kms-outbound-http-contact", auth_idempotency_kms_key_id="kms-outbound-http-auth")
    value = _input(outbound_world)
    body = OutboundIn(expected_request_version=request.version, pick_id=fact.id, target_stock_account_id=target_id,
        outbound_qty=str(value.outbound_qty), reason=value.reason, source_balance_version=value.source_balance_version,
        source_ledger_cursor=value.source_ledger_cursor, serial_ids=value.serial_ids).model_dump(mode="json")
    headers = {"Idempotency-Key": "outbound-http-command-key", "X-Request-ID": "outbound-http-original-trace"}
    with TestClient(app) as client:
        response = client.post(f"/api/v1/material-requests/{request.id}/outbounds", json=body, headers=headers)
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"].startswith("no-store")
        assert OutboundOut(**response.json()).pick_id == fact.id
        replay = client.post(f"/api/v1/material-requests/{request.id}/outbounds", json=body, headers=headers)
        assert replay.status_code == 201 and replay.json()["idempotency_replayed"]
        stable = _snapshot(outbound_world)
        recovered = client.get("/api/v1/material-request-outbound-command-status", headers={"X-Request-ID": headers["X-Request-ID"]})
        assert recovered.status_code == 200 and recovered.json()["lookup_status"] == "confirmed", recovered.text
        assert recovered.json()["command"]["posting_id"] == response.json()["posting_id"]
        assert _snapshot(outbound_world) == stable and len(calls) == 1
