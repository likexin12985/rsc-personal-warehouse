"""Real physical outbound from the picking gate's immutable original documents."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def assert_outbound_gate(api_engine, *, security_engine, admin_user_id, worlds):
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.demand_models import MaterialRequest, MaterialRequestCommand
    from app.inventory_models import (
        StockAccount, StockBalance, OutboundPosting, OutboundOrder, OutboundLine,
        SerialCurrentPosition, InventoryTransaction, InventoryMovement, OutboundPostingSerial,
    )
    from app.foundation_models import AuditEvent, OutboxEvent, NotificationEvent
    from app.formal_services import material_request_outbound as outbound
    from app.routers import formal_material_requests
    from test_material_request_approval_service import _principal
    from test_material_request_draft_service import SECRET
    from pg16_reservation_release_gate import _assert_release_catalog
    _assert_release_catalog(api_engine, security_engine, "_0072")

    def api_db():
        with Session(api_engine) as db:
            assert db.scalar(text("SELECT current_user")) == "star_oam_api"
            yield db
    def principal():
        with Session(api_engine) as db: return _principal(db, admin_user_id)
    settings = Settings(_env_file=None, environment="test", database_url="sqlite+pysqlite:///:memory:",
        database_schema_mode="alembic", material_request_writes_enabled=True,
        material_request_idempotency_hmac_secret=SECRET.decode(),
        material_request_contact_mobile_hmac_secret="pg16-outbound-contact-mobile-secret",
        material_request_contact_mobile_hash_version=1, material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id="kms-pg16-outbound-contact", auth_idempotency_kms_key_id="kms-pg16-auth-distinct")
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
                tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity, StockBalance.version, StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id))),
                tuple(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id, SerialCurrentPosition.last_movement_id).order_by(SerialCurrentPosition.serial_id))),
                tuple(db.execute(select(MaterialRequest.id, MaterialRequest.version, MaterialRequest.outbound_status, MaterialRequest.shipment_status).order_by(MaterialRequest.id))),
                tuple(db.scalar(select(func.count()).select_from(model)) for model in
                    (OutboundPosting, OutboundPostingSerial, MaterialRequestCommand, InventoryTransaction, InventoryMovement, AuditEvent, OutboxEvent, NotificationEvent)),
            )

    for world in worlds:
        request_id, line_id = world["request_id"], world["line_id"]
        first_pick, final_pick = world["first_pick"], world["final_pick"]
        source_id = UUID(first_pick["target_stock_account_id"])
        token = f"pg16-outbound-{'serial' if world['serial_mode'] else 'quantity'}"
        with Session(security_engine) as db:
            db.execute(text("SET LOCAL ROLE star_oam_migrator"))
            source = db.get(StockAccount, source_id)
            target = StockAccount(id=uuid4(), availability_bucket="in_transit", **{name: getattr(source, name)
                for name in ("owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id")})
            db.add(target)
            db.flush()
            target_id = target.id
            db.add(StockBalance(stock_account_id=target_id, quantity=Decimal("0.000"), version=0, ledger_cursor=0))
            db.commit()

        def payload(pick_id, qty, serials):
            stable = snapshot()
            with TestClient(app) as client:
                response = client.get(f"/api/v1/material-requests/{request_id}/outbound-options", params={"request_line_id": str(line_id)})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"].startswith("no-store") and snapshot() == stable
            page = response.json()
            row = next(item for item in page["items"] if item["pick_id"] == pick_id)
            assert Decimal(row["picked_qty"]) == Decimal(row["outbound_qty"]) + Decimal(row["outboundable_qty"])
            return dict(expected_request_version=page["request_version"], pick_id=pick_id,
                target_stock_account_id=row["target_stock_account_id"], outbound_qty=qty, reason="PG16 实物已离开来源位置",
                source_balance_version=row["source_balance_version"], source_ledger_cursor=row["source_ledger_cursor"],
                serial_ids=serials)

        def post(body, suffix):
            headers = {"Idempotency-Key": f"{token}-{suffix}", "X-Request-ID": f"{token}-{suffix}-trace"}
            with TestClient(app) as client:
                response = client.post(f"/api/v1/material-requests/{request_id}/outbounds", json=body, headers=headers)
            return response, headers

        def confirmed(body, suffix):
            response, headers = post(body, suffix)
            if response.status_code != 201:
                with Session(api_engine) as db:
                    outbound.create_outbound(db, actor=_principal(db, admin_user_id), material_request_id=request_id,
                        expected_request_version=body["expected_request_version"],
                        outbound=outbound.OutboundInput(UUID(body["pick_id"]), UUID(body["target_stock_account_id"]),
                            Decimal(body["outbound_qty"]), body["reason"], body["source_balance_version"],
                            body["source_ledger_cursor"], tuple(UUID(s) for s in body["serial_ids"])),
                        idempotency_key=headers["Idempotency-Key"], idempotency_hmac_secret=SECRET, trace_request_id=headers["X-Request-ID"])
                    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                    db.rollback()
            assert response.status_code == 201, response.text
            return response.json(), headers

        body = payload(first_pick["pick_id"], "1.000" if world["serial_mode"] else "0.250", first_pick["serial_ids"])
        before = snapshot()
        for invalid, expected, suffix in (
            ({**body, "outbound_qty": "3.000"}, 412, "over"),
            ({**body, "source_balance_version": body["source_balance_version"] + 1}, 409, "stale"),
            ({**body, "target_stock_account_id": str(uuid4())}, 409, "wrong-target"),
        ):
            response, _ = post(invalid, suffix)
            assert response.status_code == expected and snapshot() == before, response.text
        if world["serial_mode"]:
            response, _ = post({**body, "serial_ids": final_pick["serial_ids"]}, "cross-original-sn")
            assert response.status_code == 412 and snapshot() == before
        with patch.object(outbound, "append_audit_event", side_effect=outbound.AuditChainError("injected")):
            response, _ = post(body, "rollback")
        assert response.status_code == 503 and snapshot() == before
        first, headers = confirmed(body, "first")
        stable = snapshot()
        replay, _ = post(body, "first")
        assert replay.status_code == 201 and replay.json()["idempotency_replayed"]
        assert replay.json()["posting_id"] == first["posting_id"] and snapshot() == stable
        if not world["serial_mode"]:
            # The same original outbound line is posted in two independent slices.
            remaining = payload(first_pick["pick_id"], "0.250", [])
            confirmed(remaining, "same-line-second")
        remaining = payload(final_pick["pick_id"], final_pick["picked_qty"], final_pick["serial_ids"])
        barrier = Barrier(2)
        def race(suffix):
            barrier.wait(timeout=30)
            return post(remaining, suffix)[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(race, ("race-a", "race-b")))
        assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
        final = next(r.json() for r in results if r.status_code == 201)
        assert final["state_axes"]["outbound_status"] == ("outbound" if world["serial_mode"] else "pending_pick")
        assert final["state_axes"]["shipment_status"] == "not_started"
        stable = snapshot()
        with TestClient(app) as client:
            recovered = client.get("/api/v1/material-request-outbound-command-status", headers={"X-Request-ID": headers["X-Request-ID"]})
            old_pick = client.get("/api/v1/material-request-reservation-pick-command-status", headers={"X-Request-ID": world["pick_trace"]})
            empty = client.get(f"/api/v1/material-requests/{request_id}/outbound-options", params={"request_line_id": str(line_id)})
        assert recovered.status_code == old_pick.status_code == empty.status_code == 200, (recovered.text, old_pick.text, empty.text)
        assert recovered.json()["command"]["posting_id"] == first["posting_id"]
        assert old_pick.json()["command"]["pick_id"] == first_pick["pick_id"]
        assert old_pick.json()["command"]["current_request_version"] == final["request_version"]
        assert empty.json()["items"] == [] and snapshot() == stable
        with Session(api_engine) as db:
            total = Decimal(first_pick["picked_qty"]) + Decimal(final_pick["picked_qty"])
            assert db.get(StockBalance, source_id).quantity == 0
            assert db.get(StockBalance, target_id).quantity == total
            assert all(db.get(SerialCurrentPosition, s).stock_account_id == target_id for s in world["serials"])
            tx = db.get(InventoryTransaction, UUID(first["outbound_transaction_id"]))
            assert tx.status == "posted" and tx.movement_type == "outbound"
            assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.aggregate_id == str(tx.id))) == 1
            assert db.get(OutboundOrder, UUID(first_pick["outbound_id"])).outbound_at is None
            assert db.get(OutboundLine, UUID(first_pick["outbound_line_id"])).outbound_qty == 0
        with security_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET LOCAL session_replication_role = replica"))
                connection.execute(text("SET LOCAL ROLE star_oam_migrator"))
                with pytest.raises(DBAPIError, match="append-only"):
                    connection.execute(text("UPDATE public.outbound_postings SET reason = 'forbidden'"))
            finally:
                transaction.rollback()
        with Session(api_engine) as db:
            with pytest.raises(DBAPIError):
                db.execute(text("""
                    INSERT INTO public.outbound_postings SELECT
                    (jsonb_populate_record(NULL::public.outbound_postings, to_jsonb(p) ||
                        jsonb_build_object('id', CAST(:new_id AS text), 'posting_no', CAST(:number AS text),
                            'idempotency_key_hash', CAST(:key AS text)))).*
                    FROM public.outbound_postings p WHERE p.id = :original
                """), {"new_id": str(uuid4()), "number": f"{token}-clone", "key": "d" * 64, "original": UUID(first["posting_id"])})
                db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.rollback()
        assert snapshot() == stable
