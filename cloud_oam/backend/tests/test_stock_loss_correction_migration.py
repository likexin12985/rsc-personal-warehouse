"""Frozen SQLite boundary only; the full revision graph has a separate test.

The metadata-backed empty predecessor is deliberately synthetic. Invalid rows
below are used only to exercise UPDATE/DELETE refusal, never as business proof.
"""
from pathlib import Path
import runpy

import pytest
import sqlalchemy as sa

from app.models import Base


@pytest.fixture(params=[False, True], ids=["legacy-migration-fk-off", "application-fk-on"])
def frozen(tmp_path, request):
    support = runpy.run_path(str(Path(__file__).parents[1] /
        "alembic/stock_loss_corrections_0159/sqlite_install.py"))
    engine = sa.create_engine("sqlite+pysqlite:///" + str(tmp_path / "fixture.db"))

    @sa.event.listens_for(engine, "connect")
    def enable_foreign_keys(db, _):
        db.execute("PRAGMA foreign_keys=" + ("ON" if request.param else "OFF"))

    # 0165 descendants require composite keys absent from frozen 0159.
    # This synthetic predecessor must not include those future child tables.
    predecessor_tables = [table for table in Base.metadata.tables.values()
                          if not table.name.startswith('stock_scrap_')]
    Base.metadata.create_all(engine, tables=predecessor_tables)
    with engine.begin() as db:
        for table in reversed(support["TABLES"]):
            db.exec_driver_sql('DROP TABLE "' + table + '"')
        db.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('20261207_0158')")
    try:
        yield engine, support
    finally:
        engine.dispose()


def test_frozen_sqlite_install_rollback_roundtrip_and_missing_guard(frozen):
    engine, core = frozen
    with engine.connect() as db:
        core["install"](db)
        db.rollback()
        assert not db.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE name=?", (core["TABLES"][0],)).first()
        core["install"](db)
        core["remove_empty"](db)
        core["install"](db)
        db.commit()
        core["verify"](db)
        with pytest.raises(ValueError, match="already exists"):
            core["install"](db)
        db.rollback()
        core["prepare"](db)
        trigger = next(iter(core["DATA"]["triggers"]))
        db.exec_driver_sql('DROP TRIGGER "' + trigger + '"')
        with pytest.raises(ValueError, match="object set mismatch"):
            core["verify"](db)
        db.rollback()
        core["verify"](db)
        assert db.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_sqlite_refuses_all_correction_writes_and_retains_even_invalid_history(frozen):
    engine, core = frozen
    with engine.connect() as db:
        core["install"](db)
        db.commit()
        # Explicitly corrupt this disposable fixture to reach UPDATE/DELETE
        # triggers. Restore every guard and pragma before exercising refusals.
        db.exec_driver_sql("PRAGMA foreign_keys=OFF")
        db.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        for name in core["DATA"]["triggers"]:
            db.exec_driver_sql('DROP TRIGGER "' + name + '"')
        for table in core["TABLES"]:
            columns = db.exec_driver_sql('PRAGMA table_info("' + table + '")').all()
            values = tuple(1 if any(t in c[2].upper() for t in ("INT", "NUMERIC")) else "a" * 64 for c in columns)
            db.exec_driver_sql('INSERT INTO "' + table + '" VALUES (' + ','.join('?' for _ in columns) + ')', values)
        for row in core["DATA"]["triggers"].values():
            db.exec_driver_sql(row["sql"])
        db.commit()
        db.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
        db.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert db.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        core["verify"](db)
        for table in core["TABLES"]:
            column = next(row for row in core["DATA"]["tables"] if row["name"] == table)["columnNames"][0]
            for statement, message in (
                ('INSERT INTO "' + table + '" DEFAULT VALUES', "PostgreSQL correction proof required"),
                ('UPDATE "' + table + '" SET "' + column + '"="' + column + '"', "immutable correction history"),
                ('DELETE FROM "' + table + '"', "immutable correction history"),
            ):
                with pytest.raises(sa.exc.IntegrityError, match=message):
                    db.exec_driver_sql(statement)
                db.rollback()
        with pytest.raises(ValueError, match="immutable business history requires retention"):
            core["remove_empty"](db)
        db.rollback()
        core["verify"](db)
        assert all(db.exec_driver_sql('SELECT count(*) FROM "' + table + '"').scalar_one() == 1 for table in core["TABLES"])
