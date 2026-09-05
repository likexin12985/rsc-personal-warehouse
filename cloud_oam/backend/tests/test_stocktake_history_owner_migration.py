"""Static/SQLite acceptance for the 0062 owner boundary (not a PG substitute)."""

from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
import re
from types import SimpleNamespace
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text

from app.database_security import (
    OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256,
    RUNTIME_EXECUTE_FUNCTIONS,
    RUNTIME_FUNCTION_BODY_SHA256,
    RUNTIME_FUNCTION_SHAPES,
)
from app.formal_services.postgresql_lock_graph import (
    lock_nonopening_stocktake_count_history_graph,
)
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0061


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "backend/alembic/versions/20260905_0062_nonopening_count_history_owner_graph.py"


def _load(path: Path = MIGRATION_PATH):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _config(url: str, output_buffer=None) -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output_buffer)
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_history_owner_function_body_shape_and_manifest_are_exact() -> None:
    migration = _load()
    coordinate = (migration.LOCK_FUNCTION, "uuid, uuid, text")
    sql = migration._postgresql_lock_function_sql()
    body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(body.encode()).hexdigest() == migration.LOCK_BODY_SHA256
    assert RUNTIME_FUNCTION_BODY_SHA256[coordinate] == migration.LOCK_BODY_SHA256
    assert RUNTIME_EXECUTE_FUNCTIONS[coordinate] == (
        "v", True, "plpgsql", ("search_path=pg_catalog, public",)
    )
    assert RUNTIME_FUNCTION_SHAPES[coordinate] == ("f", "void", False)
    assert migration.revision == "20260905_0062"
    assert migration.down_revision == "20260905_0061"
    assert not re.search(r"\b(INSERT|DELETE|TRUNCATE|COMMIT|ROLLBACK)\b", body)
    assert not re.search(r"\bUPDATE\s+public\.", body)
    assert "EXECUTE " not in body
    assert "LIMIT " not in body
    assert not text(sql)._bindparams
    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(sql)
    parser.parse_plpgsql_json(sql)


def test_history_owner_has_one_full_ordered_union_before_ancestor_graph() -> None:
    sql = _load()._postgresql_lock_function_sql()
    tokens = (
        "PERFORM head.id",
        "SELECT task.region_org_id",
        "PERFORM public.rsc_lock_formal_principal_graph_0026(principal_user_ids)",
        "PERFORM round_row.id",
        "PERFORM account.id",
        "PERFORM organization.id",
        "PERFORM location.id",
        "PERFORM person.id",
        "PERFORM custody.id",
        "PERFORM material.id",
        "PERFORM policy.id",
        "PERFORM lot.id",
        "PERFORM serial.id",
        "PERFORM public.rsc_lock_nonopening_stocktake_review_graph_0032",
        "PERFORM id FROM public.stocktake_effective_approval_completions",
        "PERFORM id FROM public.stocktake_close_completions",
        "PERFORM transaction_row.id",
        "PERFORM movement.id",
        "PERFORM movement_serial.serial_id",
        "PERFORM attachment.id",
        "PERFORM file_row.id",
    )
    indices = [sql.index(token) for token in tokens]
    assert indices == sorted(indices)
    assert sql.count("PERFORM file_row.id") == 1
    assert "ORDER BY file_row.id FOR UPDATE OF file_row" in sql
    assert "rsc_lock_nonopening_stocktake_difference_replay_graph_0057" not in sql
    assert "audit_chain_heads" not in sql
    assert "task.current_round_no = 1" not in sql
    assert "pg_catalog.cardinality(completion_ids) <>" not in sql
    assert "'posted', 'closed'" in sql
    assert "max_count_cursor, completion_ids" in sql
    assert "completion.task_id = requested_task_id;" in sql


@pytest.mark.parametrize("table,actor", [
    ("stocktake_tasks", "created_by_user_id"),
    ("stocktake_start_completions", "started_by_user_id"),
    ("stocktake_scopes", "assignee_user_id"),
    ("stocktake_count_lines", "counted_by_user_id"),
    ("stocktake_count_observations", "counted_by_user_id"),
    ("stocktake_scope_count_completions", "completed_by_user_id"),
    ("stocktake_round_submissions", "submitted_by_user_id"),
    ("stocktake_reviews", "reviewer_user_id"),
    ("stocktake_recount_cases", "opened_by_user_id"),
    ("stocktake_recount_scope_assignments", "assignee_user_id"),
    ("stocktake_effective_approval_completions", "completed_by_user_id"),
    ("stocktake_posting_completions", "posted_by_user_id"),
    ("stocktake_close_reconciliation_completions", "reconciled_by_user_id"),
    ("stocktake_close_completions", "closed_by_user_id"),
    ("inventory_freezes", "released_by_user_id"),
])
def test_history_owner_derives_all_historical_principals(table: str, actor: str) -> None:
    source = _load()._postgresql_lock_function_sql()
    union = source.split("INTO principal_user_ids", 1)[1].split("AS principal_union", 1)[0]
    assert f"public.{table}" in union
    assert actor in union
    assert "pg_catalog.cardinality(principal_user_ids) NOT BETWEEN 1 AND 1000" in source
    assert "principal reference missing" in source


def test_history_owner_covers_replay_endpoints_snapshot_serials_and_terminal_rows() -> None:
    sql = _load()._postgresql_lock_function_sql()
    for token in (
        "movement.from_account_id = ANY(scoped_account_ids)",
        "movement.to_account_id = ANY(scoped_account_ids)",
        "UNION SELECT from_account_id", "UNION SELECT to_account_id",
        "transaction_row.ledger_cursor <= max_count_cursor",
        "posting.inventory_transaction_id", "serial_snapshot_jsonb",
        "(element.value->>'serial_id')::uuid", "serial snapshot malformed",
        "serial reference missing", "movement_serial.movement_id = ANY(movement_ids)",
        "stocktake_effective_approval_scopes", "stocktake_effective_approval_items",
        "stocktake_posting_items", "stocktake_posting_completion_items",
        "stocktake_close_transition_acks", "stocktake_close_reconciliation_accounts",
        "stocktake_close_reconciliation_serials", "resolved_serial_id",
        "parent.id", "custody.custodian_person_id", "serial_id FROM public.stocktake_differences",
        "graph_count > 100000", "NOT parent_id = ANY(location_ids)",
        "NOT id = ANY(policy_ids)", "NOT attachment.id = ANY(attachment_ids)",
    ):
        assert token in sql
    # 0032 re-locks all policies, so the union cannot only prelock cutoff rows.
    assert "policy.effective_from <= task_cutoff_at" not in sql
    assert "policy.material_id = ANY(material_ids);" in sql
    assert sql.index("pre-helper owner union changed") < sql.index(
        "PERFORM public.rsc_lock_nonopening_stocktake_review_graph_0032"
    )


def _assert_owner_columns_exist(sql: str) -> None:
    # Parser checks PL/pgSQL syntax, not PostgreSQL relation/column resolution.
    # Verify every real table, explicit table-alias field and simple unaliased
    # SELECT/PERFORM target against the current SQLAlchemy schema as well.
    # This catches regressions such as the prior audit.sequence_no typo.
    from app.database import Base
    import app.models  # noqa: F401
    import app.foundation_models  # noqa: F401
    import app.inventory_models  # noqa: F401
    import app.stocktake_models  # noqa: F401

    tables = Base.metadata.tables
    referenced = set(re.findall(r"\b(?:FROM|JOIN)\s+public\.([a-z_]+)", sql))
    assert referenced
    assert referenced <= set(tables), referenced - set(tables)
    aliases: dict[str, set[str]] = {}
    for table, alias in re.findall(r"\b(?:FROM|JOIN)\s+public\.([a-z_]+)\s+AS\s+([a-z_]+)", sql):
        aliases.setdefault(alias, set()).add(table)
    checked = set()
    for alias, column in re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", sql):
        if alias in aliases:
            assert any(column in tables[name].columns for name in aliases[alias]), (alias, column, aliases[alias])
            checked.add((alias, column))
    assert len(checked) > 60
    for targets, table in re.findall(r"\b(?:SELECT|PERFORM)\s+([a-z_, ]+)\s+FROM\s+public\.([a-z_]+)", sql):
        for column in targets.split(","):
            assert column.strip() in tables[table].columns, (table, column)


def test_history_owner_references_current_table_columns_not_guessed_names() -> None:
    sql = _load()._postgresql_lock_function_sql()
    _assert_owner_columns_exist(sql)
    with pytest.raises(AssertionError):
        _assert_owner_columns_exist(sql.replace("task.cutoff_ledger_cursor", "task.sequence_no"))
    with pytest.raises(AssertionError):
        _assert_owner_columns_exist(sql.replace("PERFORM id FROM public.stocktake_close_completions", "PERFORM sequence_no FROM public.stocktake_close_completions"))


def test_history_owner_readiness_forward_and_backward_hashes_are_exact() -> None:
    migration = _load()
    predecessor = _load(MIGRATION_PATH.parent / "20260903_0047_nonopening_stocktake_start_causality.py")
    original = predecessor._oam_runtime_ready_function_sql(predecessor.revision).split("AS $$", 1)[1].rsplit("$$", 1)[0]
    old = original.replace(predecessor.revision, migration.down_revision)
    new = old.replace(migration.down_revision, migration.revision)
    assert old.count(migration.down_revision) == 1
    assert new.replace(migration.revision, migration.down_revision) == old
    assert hashlib.sha256(old.encode()).hexdigest() == migration.RUNTIME_READY_BODY_SHA256_0061
    assert hashlib.sha256(new.encode()).hexdigest() == migration.RUNTIME_READY_BODY_SHA256_0062
    assert migration.RUNTIME_READY_BODY_SHA256_0061 == OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0061[migration.RUNTIME_READY_SIGNATURE.removeprefix("public.")][6]
    assert migration.RUNTIME_READY_BODY_SHA256_0062 == OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256[("rsc_oam_runtime_binding_ready_0044", "")]


def test_history_owner_catalog_pins_dependencies_acl_arguments_and_seals(monkeypatch) -> None:
    migration = _load()
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration._verify_postgresql_catalog(expected_ready_hash=migration.RUNTIME_READY_BODY_SHA256_0062, installed=True)
    migration._replace_runtime_ready(expected_hash=migration.RUNTIME_READY_BODY_SHA256_0061, replacement_hash=migration.RUNTIME_READY_BODY_SHA256_0062, old_revision=migration.down_revision, new_revision=migration.revision)
    parser = pytest.importorskip("pglast.parser")
    for statement in statements:
        assert not text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)
    sql = "\n".join(statements)
    for token in (
        "row.proargnames", "row.proallargtypes IS NULL", "row.proargmodes IS NULL",
        "row.provariadic = 0", "row.proparallel = 'u'", "NOT row.proleakproof",
        "acl.grantor <> migrator_oid", "acl.is_grantable", "row.proowner = migrator_oid",
        "row.proacl IS DISTINCT FROM original_acl", "trigger_row.tgenabled = 'A'",
        "requested_task_id,requested_round_id,requested_actor_user_id",
        migration.LOCK_BODY_SHA256, migration.REVIEW_HELPER_BODY_SHA256,
        migration.PRINCIPAL_HELPER_BODY_SHA256, migration.SEAL_BODY_SHA256,
        migration.CONTROL_GUARD_BODY_SHA256,
    ):
        assert token in sql
    with pytest.raises(ValueError, match="unsupported 0062"):
        migration._verify_postgresql_catalog(expected_ready_hash="0" * 64, installed=True)


def test_history_owner_upgrade_no_table_grants_or_fact_rewrite(monkeypatch) -> None:
    migration = _load()
    statements = []
    monkeypatch.setattr(migration, "_dialect_name", lambda: "postgresql")
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.upgrade()
    sql = "\n".join(statements)
    assert statements[0].startswith("LOCK TABLE public.alembic_version")
    assert sql.index("function shape or body mismatch") < sql.index(f"CREATE FUNCTION public.{migration.LOCK_FUNCTION}")
    assert f"REVOKE ALL ON FUNCTION {migration.LOCK_SIGNATURE} FROM PUBLIC" in sql
    assert f"GRANT EXECUTE ON FUNCTION {migration.LOCK_SIGNATURE} TO star_oam_api" in sql
    assert "GRANT UPDATE" not in sql and "GRANT SELECT" not in sql
    assert "CREATE TRIGGER" not in sql and "DROP TRIGGER" not in sql
    assert "UPDATE public." not in sql and "INSERT INTO public." not in sql
    assert sql.index("readiness source mismatch") > sql.index(f"CREATE FUNCTION public.{migration.LOCK_FUNCTION}")


def test_history_owner_postgresql_offline_upgrade_and_downgrade_boundary(monkeypatch) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config("postgresql+psycopg://offline:offline@localhost/offline", output)
    command.upgrade(config, "20260905_0061:20260905_0062", sql=True)
    assert "Running upgrade 20260905_0061 -> 20260905_0062" in output.getvalue()
    with pytest.raises(RuntimeError, match="0062 downgrade requires online catalog"):
        command.downgrade(config, "20260905_0062:20260905_0061", sql=True)


def test_history_owner_sqlite_noop_and_unsupported_dialects(monkeypatch) -> None:
    migration = _load()
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    monkeypatch.setattr(migration.op, "get_bind", lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    migration.upgrade()
    migration.downgrade()
    assert statements == []
    monkeypatch.setattr(migration.op, "get_bind", lambda: SimpleNamespace(dialect=SimpleNamespace(name="mysql")))
    with pytest.raises(RuntimeError, match="only PostgreSQL and SQLite"):
        migration.upgrade()


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_history_owner_wrapper_uses_only_fixed_bound_parameters(dialect: str) -> None:
    executed = []
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name=dialect)), execute=lambda statement, params: executed.append((str(statement), params)))
    task_id, round_id = uuid.uuid4(), uuid.uuid4()
    hostile_actor = "'); DROP TABLE files; --"
    lock_nonopening_stocktake_count_history_graph(db, task_id, round_id, hostile_actor)
    if dialect == "sqlite":
        assert executed == []
    else:
        assert executed == [(
            "SELECT public.rsc_lock_nonopening_stocktake_count_history_graph_0062(CAST(:task_id AS uuid), CAST(:round_id AS uuid), CAST(:actor_user_id AS text))",
            {"task_id": str(task_id), "round_id": str(round_id), "actor_user_id": hostile_actor},
        )]
