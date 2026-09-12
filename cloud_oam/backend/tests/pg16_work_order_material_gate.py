"""Actual API-role work-order postings in a disposable PostgreSQL 16 database.

Fixture credentials seed only OAM references and empty reserved dimensions.
Every quantity change uses the common ledger with the actual engineer identity.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.demand_models import OamWorkOrder
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as service
from app.foundation_models import ExternalObject, OutboxEvent, SourceSystem
from app.inventory_models import (FormalMaterial, InventorySerial, InventoryTransaction,
    SerialCurrentPosition, StockAccount, StockBalance, StockLocation)
from app.models import User


@dataclass(frozen=True)
class World:
    actor_id: object
    account_id: object
    reserved_id: object
    orders: tuple
    serial_ids: tuple


def _worlds(engine):
    worlds = {}
    with Session(engine) as db:
        candidates = db.scalars(select(StockAccount).join(StockBalance,
            StockBalance.stock_account_id == StockAccount.id).join(StockLocation,
            StockLocation.id == StockAccount.location_id).where(
                StockLocation.location_type == "personal", StockLocation.status == "active",
                StockAccount.availability_bucket == "available", StockBalance.quantity >= 1,
            ).order_by(StockBalance.quantity.desc(), StockAccount.id)).all()
        source = SourceSystem(id=uuid4(), code="pg16-wo-" + uuid4().hex,
            name="Isolated work-order gate", mode="read_only", enabled=True, configuration_jsonb={})
        db.add(source); db.flush()
        for account in candidates:
            serials = tuple(db.scalars(select(SerialCurrentPosition.serial_id).where(
                SerialCurrentPosition.stock_account_id == account.id).order_by(SerialCurrentPosition.serial_id)))
            kind = "serial" if serials else "quantity"
            if kind in worlds:
                continue
            actor_id = db.scalar(select(User.id).where(User.person_id == account.custodian_person_id))
            if actor_id is None:
                continue
            dimensions = {key: getattr(account, key) for key in (
                "owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id")}
            target = db.scalar(select(StockAccount).filter_by(**dimensions, availability_bucket="reserved"))
            if target is None:
                target = StockAccount(id=uuid4(), **dimensions, availability_bucket="reserved")
                db.add(target); db.flush()
                db.add(StockBalance(stock_account_id=target.id, quantity=Decimal(0), ledger_cursor=0, version=0))
            orders = []
            for _ in range(2):
                external = ExternalObject(id=uuid4(), source_system_id=source.id,
                    entity_type="work_order", external_id="PG16-" + uuid4().hex)
                db.add(external); db.flush()
                order = OamWorkOrder(id=uuid4(), external_object_id=external.id,
                    work_order_no="PG16-" + uuid4().hex, organization_id=account.owner_org_id,
                    engineer_person_id=account.custodian_person_id, status="active",
                    source_updated_at=datetime.now(timezone.utc))
                db.add(order); orders.append(order.id)
            worlds[kind] = World(actor_id, account.id, target.id, tuple(orders), serials)
        db.commit()
    assert set(worlds) == {"quantity", "serial"}, "gate requires both real personal quantity and SN stock"
    assert len(worlds["serial"].serial_ids) >= 2, "gate requires two SNs to prove same-account cross-order isolation"
    return worlds


def _run(db, world, operation, *, order=0, serials=None, key=None):
    key = key or uuid4().hex
    account = db.get(StockAccount, world.account_id)
    ids = tuple(world.serial_ids[:1] if serials is None else serials)
    material = db.get(FormalMaterial, account.material_id)
    proofs = tuple(service.SerialVerificationInput(serial.id, material.sku_code, serial.serial_no, serial.qr_code)
        for serial in db.scalars(select(InventorySerial).where(InventorySerial.id.in_(ids))))
    line = service.WorkOrderMaterialLineInput(material.id,
        world.account_id if operation == "occupy" else world.reserved_id, Decimal(1),
        serial_ids=ids, serial_verifications=proofs, condition_before=account.condition_code,
        target_stock_account_id=world.reserved_id if operation == "occupy" else world.account_id if operation == "release" else None)
    return getattr(service, f"execute_{operation}_operation")(db,
        actor=load_formal_principal(db, world.actor_id), work_order_id=world.orders[order],
        lines=(line,), idempotency_key=key, request_id="pg16-wo-" + key)


def _checkpoint(db):
    db.flush()
    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    db.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def _snapshot(engine):
    with engine.connect() as c:
        counts = tuple(c.execute(text("SELECT count(*) FROM " + table)).scalar_one()
            for table in ("stock_accounts", "stock_balances", "inventory_transactions", "inventory_movements", "inventory_movement_serials",
                          "work_order_material_operations", "work_order_material_lines", "work_order_material_serials",
                          "audit_events", "outbox_events"))
        balances = c.execute(text("SELECT stock_account_id, quantity, ledger_cursor, version FROM stock_balances ORDER BY stock_account_id")).all()
        serials = c.execute(text("SELECT serial_id, stock_account_id, last_movement_id FROM serial_current_positions ORDER BY serial_id")).all()
        ledger = c.execute(text("SELECT id, next_cursor FROM inventory_ledger_heads ORDER BY id")).all()
        audit = c.execute(text("SELECT stream_key, version, last_event_id, last_hash FROM audit_chain_heads ORDER BY stream_key")).all()
        return counts, balances, serials, ledger, audit


def _db_denies(callback, engine, message):
    before = _snapshot(engine)
    with Session(engine) as db:
        try:
            callback(db)
            _checkpoint(db)
        except DBAPIError as exc:
            assert exc.orig.sqlstate == "23514" and message in str(exc.orig), str(exc.orig)
        else:
            raise AssertionError("database accepted invalid work-order evidence")
        finally:
            db.rollback()
    assert _snapshot(engine) == before


def assert_work_order_material_gate(api_engine, fixture_engine):
    worlds = _worlds(fixture_engine)
    for kind, world in worlds.items():
        finish = "release" if kind == "serial" else "consume"
        before = _snapshot(api_engine)
        with Session(api_engine) as db:
            _run(db, world, "occupy"); _checkpoint(db)
            count = db.scalar(select(func.count()).select_from(InventoryTransaction))
            try:
                _run(db, world, finish, order=1)
            except service.InventoryPostingError as exc:
                assert exc.code == "work_order_reservation_insufficient"
            else:
                raise AssertionError("another work order used pooled stock")
            assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == count
            key = uuid4().hex
            first, posted = _run(db, world, finish, key=key); _checkpoint(db)
            again, replay = _run(db, world, finish, key=key); _checkpoint(db)
            assert first.id == again.id and posted.transaction_id == replay.transaction_id
            db.rollback()
        assert _snapshot(api_engine) == before

        def cross_order(db):
            _run(db, world, "occupy"); _checkpoint(db)
            # Deliberately bypass Python preflight to exercise the database proof.
            with patch.object(service, "require_work_order_reservations", return_value=None):
                _run(db, world, finish, order=1)
        _db_denies(cross_order, api_engine, "0090 work order reserved quantity exhausted")

        def orphan(db):
            with patch.object(service, "record_posted_operation", return_value=SimpleNamespace(id=uuid4())):
                _run(db, world, "occupy")
        _db_denies(orphan, api_engine, "0090 work order transaction requires one supported operation")

        def missing_outbox(db):
            _run(db, world, "occupy")
            pending = [row for row in db.new if isinstance(row, OutboxEvent)
                       and row.event_type == "work_order_material_operation_posted"]
            assert len(pending) == 1
            db.expunge(pending[0])
        _db_denies(missing_outbox, api_engine, "0090 work order command audit or outbox mismatch")

        append = service.append_audit_event
        def corrupt_audit(*args, **kwargs):
            kwargs["after_jsonb"]["command"]["lines"][0]["quantity"] = "9.000"
            return append(*args, **kwargs)
        def wrong_command(db):
            with patch.object(service, "append_audit_event", side_effect=corrupt_audit):
                _run(db, world, "occupy")
        _db_denies(wrong_command, api_engine, "0090 work order command audit or outbox mismatch")

    sn_world = worlds["serial"]
    def wrong_serial(db):
        _run(db, sn_world, "occupy", serials=sn_world.serial_ids[:1]); _checkpoint(db)
        _run(db, sn_world, "occupy", order=1, serials=sn_world.serial_ids[1:2]); _checkpoint(db)
        try:
            _run(db, sn_world, "release", serials=sn_world.serial_ids[1:2])
        except service.InventoryPostingError as exc:
            assert exc.code == "work_order_serial_not_reserved"
        else:
            raise AssertionError("work order used another order's SN")
        with patch.object(service, "require_work_order_reservations", return_value=None):
            _run(db, sn_world, "release", serials=sn_world.serial_ids[1:2])
    _db_denies(wrong_serial, api_engine, "0090 serial is not reserved by this work order")

    # Terminal fixtures: simultaneous different keys can spend this reservation only once.
    for kind, world in worlds.items():
        finish = "release" if kind == "serial" else "consume"
        with Session(api_engine) as db:
            _run(db, world, "occupy"); db.commit()
        barrier = Barrier(2)
        def worker():
            key = uuid4().hex
            with Session(api_engine) as db:
                db.execute(text("SET LOCAL statement_timeout = '15000ms'"))
                barrier.wait(timeout=10)
                try:
                    operation, _ = _run(db, world, finish, key=key)
                    result = ("posted", operation.id, key)
                    db.commit()
                    return result
                except service.InventoryPostingError as exc:
                    db.rollback()
                    return (exc.code, None, key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        assert sorted(result[0] for result in results) == ["posted", "work_order_reservation_insufficient"]
        winner = next(result for result in results if result[0] == "posted")
        before = _snapshot(api_engine)
        with Session(api_engine) as db:
            replay, _ = _run(db, world, finish, key=winner[2])
            assert replay.id == winner[1]
            db.commit()
        assert _snapshot(api_engine) == before
    print("PG16 work-order quantity/SN causality, raw bypass denial, audit/outbox rollback, replay and concurrency PASS", flush=True)
