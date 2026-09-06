"""Static and SQLite acceptance for the 0063 review command-version migration."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

from app.oam_sync_scope_security import (
    OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0063,
    OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0064,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260906_0063_review_command_status.py"
)
READY_SIGNATURE = "rsc_oam_runtime_binding_ready_0044()"


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_0063", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _body(sql: str) -> str:
    return sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]


def test_0063_revision_coordinates_and_readiness_manifest_are_exact() -> None:
    migration = _migration_module()

    assert migration.revision == "20260906_0063"
    assert migration.down_revision == "20260906_0064"
    assert migration.PREVIOUS_SCHEMA_REVISION == migration.down_revision
    assert migration.RUNTIME_READY_BODY_SHA256_0064 == (
        OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0064[READY_SIGNATURE][-1]
    )
    assert migration.RUNTIME_READY_BODY_SHA256_0063 == (
        OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0063[READY_SIGNATURE][-1]
    )
    assert migration.RUNTIME_READY_BODY_SHA256_0063 != (
        migration.RUNTIME_READY_BODY_SHA256_0064
    )

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert migration.revision in {
        revision.revision for revision in script.iterate_revisions(heads[0], "base")
    }


def test_0063_trigger_function_body_hash_and_security_contract_are_pinned() -> None:
    migration = _migration_module()
    sql = migration._postgresql_trigger_function_sql()
    body = _body(sql)

    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == migration.TRIGGER_BODY_SHA256
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "FOR SHARE OF task" in sql
    assert "task_kind IN ('full', 'sample', 'ad_hoc', 'personal', 'termination')" in sql
    assert "NEW.expected_task_version IS NULL" in sql
    assert "NEW.resulting_task_version <> NEW.expected_task_version + 1" in sql
    assert "NEW.expected_task_version < 0" in sql
    assert "NEW.resulting_task_version IS NULL" in sql
    assert "RAISE EXCEPTION 'non-opening stocktake review version pair is required'" in sql

    migration_source = MIGRATION.read_text(encoding="utf-8")
    assert "expected_task_version IS NULL AND resulting_task_version IS NULL" in migration_source
    assert "expected_task_version IS NOT NULL AND resulting_task_version IS NOT NULL" in migration_source

    # The migration-owned trigger has no caller-controlled bind parameters or
    # write statements beyond the trigger's NEW-row validation contract.
    assert not sa.text(sql)._bindparams
    assert "INSERT INTO" not in body
    assert "UPDATE public." not in body
    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(sql)
    parser.parse_plpgsql_json(sql)


def test_0063_orm_declares_postgresql_pair_check_without_changing_sqlite_snapshot() -> None:
    from app.stocktake_models import StocktakeReview

    source = inspect.getsource(StocktakeReview)
    assert "ck_stocktake_reviews_task_version_pair_0063" in source
    assert '.ddl_if(dialect="postgresql")' in source


def test_0063_sqlite_upgrade_validates_review_version_pair_and_downgrades(
    monkeypatch,
) -> None:
    migration = _migration_module()
    # This test invokes the revision functions directly with Alembic's
    # Operations facade.  There is no EnvironmentContext proxy in that mode;
    # explicitly model the online path so the production downgrade guard is
    # exercised instead of failing in Alembic's test-only proxy.
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "stocktake_tasks",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_type", sa.String(24), nullable=False),
    )
    sa.Table(
        "stocktake_reviews",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(stocktake_reviews)"
            )
        }
        assert {"expected_task_version", "resulting_task_version"} <= columns
        trigger_names = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert {
            migration.SQLITE_INSERT_TRIGGER,
            migration.SQLITE_UPDATE_TRIGGER,
        } <= trigger_names

        connection.exec_driver_sql(
            "INSERT INTO stocktake_tasks(id, task_type) VALUES (?, ?)",
            ("opening-task", "opening"),
        )
        connection.exec_driver_sql(
            "INSERT INTO stocktake_tasks(id, task_type) VALUES (?, ?)",
            ("full-task", "full"),
        )
        connection.exec_driver_sql(
            "INSERT INTO stocktake_reviews(id, task_id) VALUES (?, ?)",
            ("opening-review", "opening-task"),
        )
        with pytest.raises(sa.exc.IntegrityError, match="version pair is required"):
            connection.exec_driver_sql(
                "INSERT INTO stocktake_reviews(id, task_id) VALUES (?, ?)",
                ("invalid-review", "full-task"),
            )
        with pytest.raises(sa.exc.IntegrityError, match="version pair is required"):
            connection.exec_driver_sql(
                "INSERT INTO stocktake_reviews"
                "(id, task_id, expected_task_version, resulting_task_version) "
                "VALUES (?, ?, ?, ?)",
                ("half-empty-left", "full-task", None, 1),
            )
        with pytest.raises(sa.exc.IntegrityError, match="version pair is required"):
            connection.exec_driver_sql(
                "INSERT INTO stocktake_reviews"
                "(id, task_id, expected_task_version, resulting_task_version) "
                "VALUES (?, ?, ?, ?)",
                ("half-empty-right", "full-task", 1, None),
            )
        connection.exec_driver_sql(
            "INSERT INTO stocktake_reviews"
            "(id, task_id, expected_task_version, resulting_task_version) "
            "VALUES (?, ?, ?, ?)",
            ("valid-review", "full-task", 4, 5),
        )
        with pytest.raises(sa.exc.IntegrityError, match="version pair is required"):
            connection.exec_driver_sql(
                "UPDATE stocktake_reviews SET resulting_task_version = ? "
                "WHERE id = ?",
                (7, "valid-review"),
            )

    # A non-opening fact blocks downgrade.  Remove it, then prove the
    # reversible schema boundary removes both columns and trigger objects.
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="cannot downgrade 0063"):
            migration.downgrade()
        connection.exec_driver_sql(
            "DELETE FROM stocktake_reviews WHERE id = ?", ("valid-review",)
        )
        migration.downgrade()
        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(stocktake_reviews)"
            )
        }
        assert "expected_task_version" not in columns
        assert "resulting_task_version" not in columns
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
            "AND name IN (?, ?)",
            (migration.SQLITE_INSERT_TRIGGER, migration.SQLITE_UPDATE_TRIGGER),
        ).scalar_one() == 0


def test_0063_offline_downgrade_fails_before_catalog_mutation(monkeypatch) -> None:
    migration = _migration_module()
    monkeypatch.setattr(migration, "_is_offline_mode", lambda: True)

    with pytest.raises(
        RuntimeError,
        match="0063 downgrade requires online catalog and readiness checks",
    ):
        migration.downgrade()
