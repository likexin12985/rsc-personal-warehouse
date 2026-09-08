"""Real PG16 picking through the restricted API, including immutable recovery."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4, UUID
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def assert_picking_gate(api_engine, *, security_engine, admin_user_id, inventory_fixture, source_request_id, manager_user_id):
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.demand_models import MaterialRequest
    from app.inventory_models import (StockAccount, StockBalance, StockReservation, StockReservationPick,
        OutboundOrder, OutboundLine, SerialCurrentPosition, InventoryTransaction, InventoryMovement, InventorySerial)
    from app.foundation_models import AuditEvent, OutboxEvent, NotificationEvent
    from app.formal_services import material_request_picking as pick
    from app.formal_services import material_request_reservation_release as release
    from app.formal_services.material_request_allocation import AllocationCreateInput, create_allocation
    from app.formal_services.material_request_reservation import ReservationCreateInput, create_reservation
    from app.routers import formal_material_requests
    from test_material_request_approval_service import _principal
    from test_material_request_draft_service import SECRET
    from pg16_reservation_gate import _approved_request
    from pg16_reservation_release_gate import _assert_release_catalog
    _assert_release_catalog(api_engine, security_engine, "_0071")

    def api_db():
        with Session(api_engine) as db:
            assert db.scalar(text("SELECT current_user")) == "star_oam_api"
            yield db
    def principal():
        with Session(api_engine) as db: return _principal(db, admin_user_id)
    settings = Settings(_env_file=None, environment="test", database_url="sqlite+pysqlite:///:memory:",
        database_schema_mode="alembic", material_request_writes_enabled=True,
        material_request_idempotency_hmac_secret=SECRET.decode(),
        material_request_contact_mobile_hmac_secret="pg16-picking-contact-mobile-secret",
        material_request_contact_mobile_hash_version=1, material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id="kms-pg16-picking-contact", auth_idempotency_kms_key_id="kms-pg16-auth-distinct")
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
                tuple(db.execute(select(MaterialRequest.id, MaterialRequest.version, MaterialRequest.reservation_status, MaterialRequest.outbound_status).order_by(MaterialRequest.id))),
                tuple(db.scalar(select(func.count()).select_from(model)) for model in
                    (StockReservationPick, OutboundOrder, OutboundLine, InventoryTransaction, InventoryMovement, AuditEvent, OutboxEvent, NotificationEvent)),
            )

    # Previously allocated SNs remain bound to immutable allocation history
    # after release. Seed distinct synthetic serials through a real inbound
    # transaction in this disposable gate; never overwrite a stock balance or
    # weaken the existing global allocation-serial uniqueness constraint.
    pick_serials = (uuid4(), uuid4())
    with Session(security_engine) as db:
        db.execute(text("SET LOCAL ROLE star_oam_migrator"))
        serial_source = db.get(StockAccount, inventory_fixture["serial_replay_account_id"])
        now = datetime.now(timezone.utc)
        db.add_all(InventorySerial(
            id=serial_id, material_id=serial_source.material_id, lot_id=serial_source.lot_id,
            serial_no=f"PG16-PICK-{serial_id.hex.upper()}", qr_code=f"PG16-PICK-QR-{serial_id.hex.upper()}",
            lifecycle_status="active", created_at=now, updated_at=now,
        ) for serial_id in pick_serials)
        db.commit()
    from app.formal_services.inventory_posting import (
        InventoryPostingCommand, InventoryMovementCommand, post_inventory_transaction,
    )
    with Session(api_engine) as db:
        fixture_key = f"pg16-picking-fixture-{uuid4()}"
        post_inventory_transaction(db, actor=_principal(db, admin_user_id),
            command=InventoryPostingCommand(
                transaction_no=fixture_key, movement_type="inbound",
                source_document_type="pg16_picking_fixture", source_document_id=fixture_key,
                posting_key=fixture_key, effective_at=datetime.now(timezone.utc),
                movements=(InventoryMovementCommand(
                    from_account_id=None, to_account_id=inventory_fixture["serial_replay_account_id"],
                    quantity=Decimal("2.000"), serial_ids=pick_serials, external_boundary_code="PG16_PICKING_FIXTURE",
                ),),
            ), idempotency_key=fixture_key, request_id=fixture_key)
        db.commit()

    for serial_mode, source_id, serials in (
        (False, inventory_fixture["account_id"], ()),
        (True, inventory_fixture["serial_replay_account_id"], pick_serials),
    ):
        token = f"pg16-picking-{'serial' if serial_mode else 'quantity'}"
        with Session(security_engine) as db:
            db.execute(text("SET LOCAL ROLE star_oam_migrator"))
            source = db.get(StockAccount, source_id)
            material_id = source.material_id
            target = StockAccount(id=uuid4(), availability_bucket="picking", **{name: getattr(source, name)
                for name in ("owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id")})
            db.add(target)
            db.flush()
            db.add(StockBalance(stock_account_id=target.id, quantity=Decimal("0.000"), version=0, ledger_cursor=0))
            db.commit()
        request_id, line_id, version, _ = _approved_request(api_engine, source_request_id=source_request_id,
            material_id=material_id, manager_user_id=manager_user_id, admin_user_id=admin_user_id, token=token)
        with Session(api_engine) as db:
            balance = db.get(StockBalance, source_id)
            allocated = create_allocation(db, actor=_principal(db, admin_user_id), material_request_id=request_id,
                expected_request_version=version, allocation=AllocationCreateInput(line_id, source_id, Decimal("2.000"),
                    balance.version, balance.ledger_cursor, serials), idempotency_key=f"{token}-allocate",
                idempotency_hmac_secret=SECRET, trace_request_id=f"{token}-allocate-trace")
            db.commit()
        with Session(api_engine) as db:
            balance = db.get(StockBalance, source_id)
            reserved = create_reservation(db, actor=_principal(db, admin_user_id), material_request_id=request_id,
                expected_request_version=allocated.request_version, reservation=ReservationCreateInput(line_id,
                    allocated.allocation_id, Decimal("2.000"), balance.version, balance.ledger_cursor, serials),
                idempotency_key=f"{token}-reserve", idempotency_hmac_secret=SECRET, trace_request_id=f"{token}-reserve-trace")
            db.commit()
            fact = db.get(StockReservation, reserved.reservation_id)
            db.expunge(fact)

        def payload(qty, ids):
            stable = snapshot()
            with TestClient(app) as client:
                response = client.get(f"/api/v1/material-requests/{request_id}/reservation-pick-options",
                    params={"request_line_id": str(line_id)})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"].startswith("no-store") and snapshot() == stable
            page = response.json()
            row = next(item for item in page["items"] if item["reservation_id"] == str(fact.id))
            assert Decimal(row["pickable_qty"]) + Decimal(row["picked_qty"]) + Decimal(row["released_qty"]) == fact.reserved_qty
            assert set(str(s) for s in ids) <= {s["serial_id"] for s in row["serials"]}
            return dict(expected_request_version=page["request_version"], reservation_id=str(fact.id), picked_qty=qty,
                reason="PG16 实物拣货核对", source_balance_version=row["source_balance_version"],
                source_ledger_cursor=row["source_ledger_cursor"], serial_ids=[str(s) for s in ids])
        def post(body, suffix):
            headers = {"Idempotency-Key": f"{token}-{suffix}", "X-Request-ID": f"{token}-{suffix}-trace"}
            with TestClient(app) as client:
                response = client.post(f"/api/v1/material-requests/{request_id}/reservation-picks", json=body, headers=headers)
            return response, headers
        def confirmed(body, suffix):
            response, headers = post(body, suffix)
            if response.status_code != 201:
                with Session(api_engine) as db:
                    pick.create_pick(db, actor=_principal(db, admin_user_id), material_request_id=request_id,
                        expected_request_version=body["expected_request_version"],
                        pick=pick.ReservationPickInput(fact.id, Decimal(body["picked_qty"]), body["reason"],
                            body["source_balance_version"], body["source_ledger_cursor"], tuple(UUID(s) for s in body["serial_ids"])),
                        idempotency_key=headers["Idempotency-Key"], idempotency_hmac_secret=SECRET, trace_request_id=headers["X-Request-ID"])
                    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                    db.rollback()
            assert response.status_code == 201, response.text
            return response.json(), headers

        # Quantity release before picking proves exact remaining ownership.
        if not serial_mode:
            with Session(api_engine) as db:
                balance = db.get(StockBalance, fact.stock_account_id)
                release.create_release(db, actor=_principal(db, admin_user_id), material_request_id=request_id,
                    expected_request_version=db.get(MaterialRequest, request_id).version,
                    release=release.ReservationReleaseInput(fact.id, Decimal("0.500"), "部分释放", balance.version, balance.ledger_cursor),
                    idempotency_key=f"{token}-release", idempotency_hmac_secret=SECRET, trace_request_id=f"{token}-release-trace")
                db.commit()
        body = payload("1.000" if serial_mode else "0.500", serials[:1])
        before = snapshot()
        failed, _ = post({**body, "picked_qty": "3.000"}, "over")
        assert failed.status_code == 412 and snapshot() == before
        failed, _ = post({**body, "source_balance_version": body["source_balance_version"] + 1}, "stale")
        assert failed.status_code == 409 and snapshot() == before
        with patch.object(pick, "append_audit_event", side_effect=pick.AuditChainError("injected")):
            failed, _ = post(body, "rollback")
        assert failed.status_code == 503 and snapshot() == before
        first, headers = confirmed(body, "first")
        assert first["state_axes"]["outbound_status"] == "pending_pick"
        assert first["state_axes"]["shipment_status"] == "not_started"
        replay, _ = post(body, "first")
        assert replay.status_code == 201 and replay.json()["idempotency_replayed"]
        assert replay.json()["pick_id"] == first["pick_id"]
        # Different commands race for one original remaining slice.
        body2 = payload("1.000", serials[1:])
        barrier = Barrier(2)
        def race(suffix):
            barrier.wait(timeout=30)
            return post(body2, suffix)[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(race, ("second-a", "second-b")))
        assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
        final = next(r.json() for r in responses if r.status_code == 201)
        assert final["state_axes"]["outbound_status"] == ("picked" if serial_mode else "pending_pick")
        stable = snapshot()
        with TestClient(app) as client:
            recovered = client.get("/api/v1/material-request-reservation-pick-command-status", headers={"X-Request-ID": headers["X-Request-ID"]})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["command"]["pick_id"] == first["pick_id"]
        assert recovered.json()["command"]["current_request_version"] == final["request_version"]
        assert recovered.headers["cache-control"].startswith("no-store") and snapshot() == stable
        with Session(api_engine) as db:
            assert db.get(StockBalance, fact.stock_account_id).quantity == 0
            assert pick.picked_quantity(db, fact.id) == (Decimal("2.000") if serial_mode else Decimal("1.500"))
            tx = db.get(InventoryTransaction, UUID(first["pick_transaction_id"]))
            assert tx.status == "posted" and tx.movement_type == "pick"
            assert db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.aggregate_id == str(tx.id))) == 1
            order = db.get(OutboundOrder, db.get(OutboundLine, UUID(first["outbound_line_id"])).outbound_id)
            assert order.status == "picked" and order.outbound_at is None
        with security_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET LOCAL session_replication_role = replica"))
                connection.execute(text("SET LOCAL ROLE star_oam_migrator"))
                with pytest.raises(DBAPIError, match="append-only"):
                    connection.execute(text("UPDATE public.stock_reservation_picks SET reason = 'forbidden'"))
            finally:
                transaction.rollback()
        # API cannot reuse an existing posted transaction under a cloned fact.
        with Session(api_engine) as db:
            with pytest.raises(DBAPIError):
                db.execute(text("""
                    INSERT INTO public.stock_reservation_picks SELECT
                    (jsonb_populate_record(NULL::public.stock_reservation_picks, to_jsonb(p) ||
                        jsonb_build_object('id', CAST(:new_id AS text), 'pick_no', CAST(:number AS text),
                            'idempotency_key_hash', CAST(:key AS text)))).*
                    FROM public.stock_reservation_picks p WHERE p.id = :original
                """), {"new_id": str(uuid4()), "number": f"{token}-clone", "key": "e" * 64, "original": UUID(first["pick_id"])})
                db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.rollback()
        assert snapshot() == stable
