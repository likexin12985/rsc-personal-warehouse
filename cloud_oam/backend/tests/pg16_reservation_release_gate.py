"""Release existing real PG16 reservation slices through the restricted API."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def assert_release_gate(api_engine, *, security_engine, admin_user_id, inventory_fixture):
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.demand_models import MaterialRequest
    from app.dependencies import get_formal_principal
    from app.formal_services import material_request_reservation_release as release
    from app.formal_services.audit_chain import AuditChainError
    from app.foundation_models import AuditEvent, OutboxEvent, NotificationEvent
    from app.inventory_models import (
        StockReservation, StockReservationRelease, StockReservationSerial,
        StockBalance, InventoryTransaction, InventoryMovement, SerialCurrentPosition,
    )
    from app.routers import formal_material_requests
    from test_material_request_approval_service import _principal
    from test_material_request_draft_service import SECRET

    def api_db():
        with Session(api_engine) as db: yield db
    def principal():
        with Session(api_engine) as db: return _principal(db, admin_user_id)
    settings = Settings(_env_file=None, environment="test", database_url="sqlite+pysqlite:///:memory:",
        database_schema_mode="alembic", material_request_writes_enabled=True,
        material_request_idempotency_hmac_secret=SECRET.decode(),
        material_request_contact_mobile_hmac_secret="pg16-release-contact-mobile-secret",
        material_request_contact_mobile_hash_version=1, material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id="kms-pg16-release-contact", auth_idempotency_kms_key_id="kms-pg16-auth-distinct")
    app = FastAPI()
    formal_material_requests.install_formal_material_request_validation_exception_handler(app)
    app.include_router(formal_material_requests.router, prefix="/api")
    app.include_router(formal_material_requests.command_status_router, prefix="/api")
    app.dependency_overrides[get_db] = api_db
    app.dependency_overrides[get_formal_principal] = principal
    app.dependency_overrides[get_settings] = lambda: settings

    def snapshot():
        with Session(api_engine) as db:
            return (
                tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity, StockBalance.version, StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id)).all()),
                tuple(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id, SerialCurrentPosition.last_movement_id).order_by(SerialCurrentPosition.serial_id)).all()),
                tuple(db.execute(select(MaterialRequest.id, MaterialRequest.version, MaterialRequest.reservation_status).order_by(MaterialRequest.id)).all()),
                tuple(db.scalar(select(func.count()).select_from(model)) for model in (
                    StockReservationRelease, InventoryTransaction, InventoryMovement, AuditEvent, OutboxEvent, NotificationEvent)),
            )

    def options(fact):
        with TestClient(app) as client:
            result = client.get(f"/api/v1/material-requests/{fact.request_id}/reservation-release-options", params={"request_line_id": str(fact.request_line_id)})
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"].startswith("no-store")
        return result.json()

    def payload(fact, qty, serial_ids=()):
        page = options(fact)
        row = next(x for x in page["items"] if x["reservation_id"] == str(fact.id))
        return dict(expected_request_version=page["request_version"], reservation_id=str(fact.id),
            released_qty=qty, reason="PG16 释放验收", source_balance_version=row["source_balance_version"],
            source_ledger_cursor=row["source_ledger_cursor"], serial_ids=[str(s) for s in serial_ids])

    def post(fact, body, token):
        headers = {"Idempotency-Key": f"pg16-release-key-{token}", "X-Request-ID": f"pg16-release-trace-{token}"}
        with TestClient(app) as client:
            response = client.post(f"/api/v1/material-requests/{fact.request_id}/reservation-releases", json=body, headers=headers)
        return response, headers

    def successful(fact, body, token):
        response, headers = post(fact, body, token)
        if response.status_code != 201:
            # The enclosing diagnostics wrapper sanitizes constraint failures.
            # Reproduction is rollback-only, never another committed POST.
            with Session(api_engine) as db:
                release.create_release(db, actor=_principal(db, admin_user_id), material_request_id=fact.request_id,
                    expected_request_version=body["expected_request_version"],
                    release=release.ReservationReleaseInput(fact.id, Decimal(body["released_qty"]), body["reason"],
                        body["source_balance_version"], body["source_ledger_cursor"], tuple(uuid.UUID(s) for s in body["serial_ids"])),
                    idempotency_key=headers["Idempotency-Key"], idempotency_hmac_secret=SECRET, trace_request_id=headers["X-Request-ID"])
                db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                db.rollback()
        assert response.status_code == 201, response.text
        with Session(api_engine) as db:
            original = db.get(StockReservation, fact.id)
            assert original.status == "reserved" and original.released_qty == 0 and original.release_transaction_id is None
            tx = db.get(InventoryTransaction, uuid.UUID(response.json()["release_transaction_id"]))
            assert tx.movement_type == "release" and tx.status == "posted"
            assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.aggregate_id == str(tx.id))) == 1
        return response.json(), headers

    with Session(api_engine) as db:
        facts = tuple(db.scalars(select(StockReservation).where(
            StockReservation.source_stock_account_id.in_((inventory_fixture["account_id"], inventory_fixture["serial_replay_account_id"])),
        ).order_by(StockReservation.source_stock_account_id, StockReservation.request_version)).all())
        assert len(facts) == 3
        notification_count = db.scalar(select(func.count()).select_from(NotificationEvent))
    for index, fact in enumerate(facts):
        with Session(api_engine) as db:
            serials = tuple(db.scalars(select(StockReservationSerial.serial_id).where(
                StockReservationSerial.reservation_id == fact.id).order_by(StockReservationSerial.serial_id)).all())
        partial_qty = Decimal("1.000") if serials else fact.reserved_qty / 2
        body = payload(fact, format(partial_qty, ".3f"), serials[:1])
        before = snapshot()
        over, _ = post(fact, {**body, "released_qty": format(fact.reserved_qty + 1, ".3f")}, f"over-{index}")
        assert over.status_code == 412 and snapshot() == before
        stale, _ = post(fact, {**body, "source_balance_version": body["source_balance_version"] + 1}, f"stale-{index}")
        assert stale.status_code == 409 and snapshot() == before
        with patch.object(release, "append_audit_event", side_effect=AuditChainError("injected PG16 release audit failure")):
            failed, _ = post(fact, body, f"rollback-{index}")
        assert failed.status_code == 503 and snapshot() == before
        first, first_headers = successful(fact, body, f"partial-{index}")
        assert first["state_axes"]["reservation_status"] == "partially_released"
        replay, _ = post(fact, body, f"partial-{index}")
        assert replay.status_code == 201 and replay.json()["release_id"] == first["release_id"]
        rest = payload(fact, format(fact.reserved_qty - partial_qty, ".3f"), serials[1:])
        if serials:
            before = snapshot()
            duplicate, _ = post(fact, {**rest, "serial_ids": body["serial_ids"]}, f"duplicate-{index}")
            assert duplicate.status_code == 412 and snapshot() == before
        barrier = Barrier(2)
        def worker(worker_id):
            barrier.wait(timeout=10)
            return post(fact, rest, f"concurrent-{index}-{worker_id}")[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(pool.map(worker, range(2)))
        assert sorted(r.status_code for r in outcomes) == [201, 409]
        winner = next(r.json() for r in outcomes if r.status_code == 201)
        with TestClient(app) as client:
            recovered = client.get("/api/v1/material-request-reservation-release-command-status", headers=first_headers)
        assert recovered.status_code == 200, recovered.text
        history = recovered.json()["command"]
        assert history["release_id"] == first["release_id"] and history["request_version"] == first["request_version"]
        assert history["current_request_version"] == winner["request_version"]
        assert history["state_axes"]["reservation_status"] == "partially_released"
        assert all(row["reservation_id"] != str(fact.id) for row in options(fact)["items"])
        with Session(api_engine) as db:
            assert all(db.get(SerialCurrentPosition, s).stock_account_id == fact.source_stock_account_id for s in serials)
    with Session(api_engine) as db:
        assert db.scalar(select(func.count()).select_from(NotificationEvent)) == notification_count
    with security_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET LOCAL ROLE star_oam_migrator"))
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(text("UPDATE public.stock_reservation_releases SET reason = 'rewrite forbidden'"))
        finally:
            transaction.rollback()
