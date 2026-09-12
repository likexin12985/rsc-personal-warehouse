"""Actual disposable PG16 proofs for SN consumption; never synthetic balances."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import psycopg
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as service
from app.formal_services.inventory_query import inventory_transaction_detail, list_inventory_accounts
from app.foundation_models import OutboxEvent
from app.inventory_models import InventorySerial, SerialCurrentPosition
from pg16_work_order_material_gate import _checkpoint, _db_denies, _run, _snapshot, _worlds


def assert_serial_migration_roundtrip(gate):
    """Run before terminal SN facts; an existing active history is allowed."""
    parameters = gate._connection_parameters(role="star_oam_migrator", password=gate._role_password("star_oam_migrator"))
    signature = "public.rsc_oam_runtime_binding_ready_0044()"

    def catalog():
        with psycopg.connect(**parameters) as db:
            ready = db.execute("SELECT oid, proowner, proacl, prosecdef, proconfig, prosrc FROM pg_proc WHERE oid = CAST(%s AS regprocedure)", (signature,)).fetchone()
            functions = db.execute("SELECT proname, prosrc, proacl FROM pg_proc WHERE proname LIKE '%%_0092' ORDER BY proname").fetchall()
            triggers = db.execute("SELECT tgname, tgenabled, tgtype, tgdeferrable, tginitdeferred FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'trg_%%_0092' ORDER BY tgname").fetchall()
            updates = db.execute("SELECT attname, has_column_privilege('star_oam_api', attrelid, attnum, 'UPDATE') FROM pg_attribute WHERE attrelid = 'public.inventory_serials'::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attname").fetchall()
            return ready, functions, triggers, updates

    before = catalog()
    assert len(before[1]) == 3 and len(before[2]) == 6
    assert {name for name, permitted in before[3] if permitted} == {"lifecycle_status", "updated_at"}
    gate._run_alembic("downgrade", "20261001_0091")
    previous = catalog()
    assert previous[0][:5] == before[0][:5] and previous[0][5] != before[0][5]
    assert not previous[1] and not previous[2] and not any(row[1] for row in previous[3])
    with psycopg.connect(**parameters) as db:
        definition = db.execute("SELECT pg_get_functiondef(CAST(%s AS regprocedure))", (signature,)).fetchone()[0]
        db.execute(definition.replace("AS $function$", "AS $function$\n-- isolated 0092 late CAS failure\n"))
    try:
        drifted = catalog()
        blocked = gate._run_alembic("upgrade", "head", expect_success=False)
        assert "serial_lifecycle_readiness_0092" in blocked.stdout + blocked.stderr
        assert catalog() == drifted and gate._current_revision() == "20261001_0091"
    finally:
        with psycopg.connect(**parameters) as db:
            db.execute(definition)
    assert catalog() == previous
    gate._run_alembic("upgrade", "head")
    assert catalog() == before and gate._current_revision() == gate.HEAD_REVISION

    # A migration may not silently remove a changed security-definer function.
    target = "public.rsc_check_serial_lifecycle_0092(uuid)"
    with psycopg.connect(**parameters) as db:
        definition = db.execute("SELECT pg_get_functiondef(CAST(%s AS regprocedure))", (target,)).fetchone()[0]
        db.execute(definition.replace("AS $function$", "AS $function$\n-- isolated source drift\n"))
    try:
        drifted = catalog()
        blocked = gate._run_alembic("downgrade", "20261001_0091", expect_success=False)
        assert "0092 function source or ownership drift" in blocked.stdout + blocked.stderr
        assert catalog() == drifted and gate._current_revision() == gate.HEAD_REVISION
    finally:
        with psycopg.connect(**parameters) as db:
            db.execute(definition)
    with psycopg.connect(**parameters) as db:
        db.execute(f"GRANT EXECUTE ON FUNCTION {target} TO star_oam_api")
    try:
        drifted = catalog()
        blocked = gate._run_alembic("downgrade", "20261001_0091", expect_success=False)
        assert "0092 function source or ownership drift" in blocked.stdout + blocked.stderr
        assert catalog() == drifted and gate._current_revision() == gate.HEAD_REVISION
    finally:
        with psycopg.connect(**parameters) as db:
            db.execute(f"REVOKE EXECUTE ON FUNCTION {target} FROM star_oam_api")
    assert catalog() == before
    print("PG16 0092 roundtrip, late CAS atomic rollback, source/ACL drift rejection PASS", flush=True)


def assert_serial_consumption_gate(api_engine, fixture_engine):
    world = _worlds(fixture_engine)["serial"]
    serial_id = world.serial_ids[0]

    def raw_lifecycle(db):
        db.execute(text("UPDATE inventory_serials SET lifecycle_status = 'consumed', updated_at = now() WHERE id = :id"), {"id": serial_id})
    _db_denies(raw_lifecycle, api_engine, "0092 serial lifecycle or position projection drift")

    def timestamp_only(db):
        db.execute(text("UPDATE inventory_serials SET updated_at = updated_at + interval '1 second' WHERE id = :id"), {"id": serial_id})
    _db_denies(timestamp_only, api_engine, "0092 serial identity is immutable")

    def wrong_identity(db):
        db.execute(text("UPDATE inventory_serials SET serial_no = serial_no || '-changed' WHERE id = :id"), {"id": serial_id})
    # Ordinary API cannot change identity columns; owner is also trigger-bound.
    with Session(api_engine) as db:
        try:
            wrong_identity(db)
        except DBAPIError as exc:
            assert exc.orig.sqlstate == "42501"
        else:
            raise AssertionError("API received serial identity update privilege")
        finally:
            db.rollback()
    _db_denies(wrong_identity, fixture_engine, "0092 serial identity is immutable")

    before = _snapshot(api_engine)
    with Session(api_engine) as db:
        _run(db, world, "occupy"); _checkpoint(db)
        key = uuid4().hex
        first, posted = _run(db, world, "consume", key=key); _checkpoint(db)
        serial = db.get(InventorySerial, serial_id)
        position = db.get(SerialCurrentPosition, serial_id)
        assert serial.lifecycle_status == "consumed" and position.stock_account_id is None
        again, replay = _run(db, world, "consume", key=key); _checkpoint(db)
        assert again.id == first.id and replay.transaction_id == posted.transaction_id
        db.rollback()
    assert _snapshot(api_engine) == before

    def missing_lifecycle(db):
        _run(db, world, "occupy"); _checkpoint(db)
        serial = db.get(InventorySerial, serial_id)
        old_time = serial.updated_at
        _run(db, world, "consume")
        serial.lifecycle_status = "active"
        serial.updated_at = old_time
    _db_denies(missing_lifecycle, api_engine, "0092 serial lifecycle or position projection drift")

    def missing_position(db):
        _run(db, world, "occupy"); _checkpoint(db)
        position = db.get(SerialCurrentPosition, serial_id)
        old_movement = position.last_movement_id
        _run(db, world, "consume")
        position.stock_account_id = world.reserved_id
        position.last_movement_id = old_movement
    _db_denies(missing_position, api_engine, "0092 serial lifecycle or position projection drift")

    def missing_outbox(db):
        _run(db, world, "occupy"); _checkpoint(db)
        _run(db, world, "consume")
        events = [row for row in db.new if isinstance(row, OutboxEvent) and row.event_type == "work_order_material_operation_posted"]
        assert len(events) == 1
        db.expunge(events[0])
    _db_denies(missing_outbox, api_engine, "0090 work order command audit or outbox mismatch")

    append = service.append_audit_event
    def corrupt_audit(*args, **kwargs):
        kwargs["after_jsonb"]["command"]["lines"][0]["quantity"] = "9.000"
        return append(*args, **kwargs)
    def wrong_audit(db):
        _run(db, world, "occupy"); _checkpoint(db)
        with patch.object(service, "append_audit_event", side_effect=corrupt_audit):
            _run(db, world, "consume")
    _db_denies(wrong_audit, api_engine, "0090 work order command audit or outbox mismatch")

    def wrong_serial(db):
        _run(db, world, "occupy"); _checkpoint(db)
        _run(db, world, "occupy", order=1, serials=world.serial_ids[1:2]); _checkpoint(db)
        try:
            _run(db, world, "consume", serials=world.serial_ids[1:2])
        except service.InventoryPostingError as exc:
            assert exc.code == "work_order_serial_not_reserved"
        else:
            raise AssertionError("consumed another work order's serial")
        with patch.object(service, "require_work_order_reservations", return_value=None):
            _run(db, world, "consume", serials=world.serial_ids[1:2])
    _db_denies(wrong_serial, api_engine, "0090 serial is not reserved by this work order")

    # Real commits only after every rollback proof; retain the second SN.
    with Session(api_engine) as db:
        _run(db, world, "occupy"); db.commit()
    barrier = Barrier(2)
    def worker():
        key = uuid4().hex
        with Session(api_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '15000ms'"))
            barrier.wait(timeout=10)
            try:
                operation, posted = _run(db, world, "consume", key=key)
                db.commit()
                return "posted", operation.id, posted.transaction_id, key
            except service.InventoryPostingError as exc:
                db.rollback()
                return exc.code, None, None, key
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(row[0] for row in results) == ["posted", "work_order_reservation_insufficient"]
    winner = next(row for row in results if row[0] == "posted")
    before = _snapshot(api_engine)
    with Session(api_engine) as db:
        replay, posted = _run(db, world, "consume", key=winner[3])
        assert replay.id == winner[1] and posted.transaction_id == winner[2]
        db.commit()
        actor = load_formal_principal(db, world.actor_id)
        page = list_inventory_accounts(db, actor=actor, limit=100)
        assert any(item.stock_account_id == world.reserved_id for item in page.items)
        detail = inventory_transaction_detail(db, actor=actor, transaction_id=winner[2])
        assert detail.movements[0].to_account_id is None
        serial = db.get(InventorySerial, serial_id)
        position = db.get(SerialCurrentPosition, serial_id)
        assert serial.lifecycle_status == "consumed" and position.stock_account_id is None
    assert _snapshot(api_engine) == before

    def reactivate(db):
        db.execute(text("UPDATE inventory_serials SET lifecycle_status = 'active', updated_at = now() WHERE id = :id"), {"id": serial_id})
    _db_denies(reactivate, api_engine, "0092 serial lifecycle or position projection drift")
    print("PG16 SN consumption, lifecycle/position/identity bypass denial, audit/outbox rollback, own-SN isolation, concurrency and replay PASS", flush=True)
