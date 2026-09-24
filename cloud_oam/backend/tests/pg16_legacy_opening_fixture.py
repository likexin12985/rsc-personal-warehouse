"""Frozen synthetic 0051 facts; deliberately independent of current ORM/services.

The SQL trace came from archived 7556de2 services on owned native PG16, not
production data. Later request columns are absent in every executed statement.
Only the exact already-broken 0022 task UPDATE trigger has the historical seed
exception, inside the count transaction; every other SQL guard remains active.
"""
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from psycopg.types.json import Jsonb
from sqlalchemy import text

FIXTURE_PATH = Path(__file__).parent / "fixtures/opening_0051_observation.json"
FIXTURE_SHA256 = "60e9eb7955a51c66af1f7fba22a3431aec905691cdd709b09454ef147b4538a1"
LEGACY_REVISION = "20260903_0051"
LEGACY_TASK_TRIGGER = "trg_stocktake_tasks_opening_commit_0022"


def load_fixture():
    raw = FIXTURE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise ValueError("frozen synthetic 0051 fixture changed")
    fixture = json.loads(raw)
    assert fixture["syntheticOnly"] and fixture["legacyRevision"] == LEGACY_REVISION
    return fixture


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if set(value) == {"type", "value"}:
            return {"uuid": UUID, "datetime": datetime.fromisoformat,
                    "decimal": Decimal, "jsonb": Jsonb}[value["type"]](value["value"])
        return {k: decode(v) for k, v in value.items()}
    return value


def _legacy_columns_and_trigger(db):
    assert db.scalar(text("SELECT version_num FROM alembic_version")) == LEGACY_REVISION
    assert not db.scalar(text("""SELECT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND
          ((table_name='stocktake_scope_count_completions'
            AND column_name IN ('request_jsonb','request_resolution_jsonb'))
           OR (table_name='stocktake_tasks' AND column_name='opening_authorization_version')))"""))
    assert db.execute(text("""SELECT tgtype,tgenabled FROM pg_trigger
        WHERE tgrelid='public.stocktake_tasks'::regclass AND tgname=:name"""),
        {"name": LEGACY_TASK_TRIGGER}).one() == (17, "A")


def seed_legacy_completion(engine, *, mutate_policy_after_completion=False):
    if engine.dialect.name != "postgresql":
        raise ValueError("legacy fixture requires isolated PostgreSQL 16")
    fixture = load_fixture()
    with engine.connect() as db:
        assert db.execute(text("SELECT current_user,session_user")).one() == (
            "star_oam_migrator", "star_oam_migrator")
        assert int(db.scalar(text("SHOW server_version_num"))) // 10000 == 16
        _legacy_columns_and_trigger(db)
        # This is a fresh isolated migration fixture, never an append operation
        # on a populated candidate. Seeded role/permission/audit-head rows come
        # from real migrations and are neither replaced nor re-created here.
        for table in fixture["tables"]:
            assert re.fullmatch(r"[a-z_]+", table)
            if table != "audit_chain_heads":
                assert not db.scalar(text("SELECT EXISTS (SELECT 1 FROM public." + table + ")"))
        assert not db.scalar(text("SELECT EXISTS (SELECT 1 FROM inventory_transactions)"))

    for phase in range(3):
        with engine.begin() as db:
            db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            _legacy_columns_and_trigger(db)
            if phase == 2:
                db.execute(text("ALTER TABLE public.stocktake_tasks DISABLE TRIGGER " + LEGACY_TASK_TRIGGER))
            for row in fixture["statements"]:
                if row["phase"] != phase:
                    continue
                statement = row["sql"]
                assert re.match(r"(?:INSERT INTO|UPDATE) [a-z_]+ ", statement)
                assert ";" not in statement and "request_jsonb" not in statement
                assert "request_resolution_jsonb" not in statement
                db.exec_driver_sql(statement, decode(row["parameters"])).close()
            # Flush all queued historical constraints before restoring the one
            # trigger whose legacy timestamp branch cannot accept this update.
            db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            if phase == 2:
                db.execute(text("ALTER TABLE public.stocktake_tasks ENABLE ALWAYS TRIGGER " + LEGACY_TASK_TRIGGER))
            _legacy_columns_and_trigger(db)

    evidence = decode(fixture["evidence"])
    with engine.begin() as db:
        assert db.execute(text("""SELECT count_line_count,observation_line_count,total_counted_qty,
            request_sha256 FROM stocktake_scope_count_completions WHERE id=:id"""),
            {"id": evidence["completion_id"]}).one() == (1, 1, Decimal(1), evidence["request_sha256"])
        assert db.execute(text("SELECT counted_qty FROM stocktake_count_lines")).one() == (Decimal(0),)
        assert db.execute(text("""SELECT verification_status,serial_id FROM stocktake_count_observations
            WHERE id=:id"""), {"id": evidence["observation_id"]}).one() == ("pending_verification", None)
        if mutate_policy_after_completion:
            db.execute(text("""UPDATE material_inventory_policies
                SET updated_at=(SELECT completed_at+interval '1 second'
                    FROM stocktake_scope_count_completions WHERE id=:completion)
                WHERE id=:policy"""),
                {"completion": evidence["completion_id"], "policy": evidence["policy_id"]})
        _legacy_columns_and_trigger(db)
    return evidence
