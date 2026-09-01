from __future__ import annotations

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
REVISION_0021 = "20260831_0021"
REVISION_0022 = "20260831_0022"
TERMINAL_TABLES = (
    "stocktake_postings",
    "stocktake_posting_items",
    "inventory_opening_establishments",
)
LEDGER_FACT_TABLES = (
    "inventory_transactions",
    "inventory_movements",
    "inventory_movement_serials",
)
POSTGRESQL_COMMIT_TRIGGERS = (
    "trg_inventory_transactions_opening_commit_0022",
    "trg_inventory_movements_opening_commit_0022",
    "trg_inventory_movement_serials_opening_commit_0022",
    "trg_stocktake_postings_opening_commit_0022",
    "trg_stocktake_posting_items_opening_commit_0022",
    "trg_inventory_opening_establishments_commit_0022",
    "trg_inventory_freezes_opening_commit_0022",
    "trg_stocktake_tasks_opening_commit_0022",
    "trg_state_transition_events_opening_commit_0022",
    "trg_outbox_events_opening_commit_0022",
    "trg_audit_events_opening_commit_0022",
)


def _config(database_url: str, *, output_buffer=None) -> Config:
    config = Config(str(ALEMBIC_INI), output_buffer=output_buffer)
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _sqlite_url(tmp_path: Path, name: str) -> str:
    return f"sqlite+pysqlite:///{tmp_path / name}"


def _seed_task_and_posting(
    connection: sa.Connection,
    *,
    second_opening: bool = False,
) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
    connection.exec_driver_sql(
        "DROP TRIGGER IF EXISTS "
        "trg_stocktake_postings_chronology_insert_0010"
    )
    timestamp = "2026-08-31 12:00:00+00:00"
    task_id = "92000000000040008000000000000001"
    connection.exec_driver_sql(
        "INSERT INTO stocktake_tasks "
        "(id, task_no, task_type, region_org_id, status, blind_count, "
        "current_round_no, created_by_user_id, version, note, created_at, "
        "updated_at) VALUES (?, ?, 'opening', ?, 'draft', 1, 0, ?, 0, '', "
        "?, ?)",
        (
            task_id,
            "OPENING-0022",
            "92000000000040008000000000000002",
            "runtime-user",
            timestamp,
            timestamp,
        ),
    )

    def insert_posting(posting_id: str, round_id: str, key: str) -> None:
        connection.exec_driver_sql(
            "INSERT INTO stocktake_postings "
            "(id, task_id, round_id, posting_kind, inventory_transaction_id, "
            "total_quantity, idempotency_key_hash, request_hash, "
            "posted_by_user_id, posted_at, created_at) "
            "VALUES (?, ?, ?, 'opening', NULL, 0, ?, ?, ?, ?, ?)",
            (
                posting_id,
                task_id,
                round_id,
                key * 64,
                ("f" if key != "f" else "e") * 64,
                "runtime-user",
                timestamp,
                timestamp,
            ),
        )

    insert_posting(
        "92000000000040008000000000000003",
        "92000000000040008000000000000004",
        "a",
    )
    if second_opening:
        insert_posting(
            "92000000000040008000000000000005",
            "92000000000040008000000000000006",
            "b",
        )


def _seed_other_terminal_rows(connection: sa.Connection) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
    for trigger_name in (
        "trg_stocktake_posting_items_validate_insert_0010",
        "trg_stocktake_posting_items_chronology_insert_0010",
        "trg_inventory_opening_establishments_chronology_insert_0010",
    ):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger_name}")
    timestamp = "2026-08-31 12:00:00+00:00"
    connection.exec_driver_sql(
        "INSERT INTO stocktake_posting_items "
        "(posting_id, inventory_movement_id, task_id, round_id, "
        "count_line_id, difference_id, quantity, created_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, 1, ?)",
        (
            "92000000000040008000000000000003",
            "92000000000040008000000000000007",
            "92000000000040008000000000000001",
            "92000000000040008000000000000004",
            "92000000000040008000000000000008",
            timestamp,
        ),
    )
    connection.exec_driver_sql(
        "INSERT INTO inventory_opening_establishments "
        "(id, task_id, scope_id, owner_org_id, location_id, round_id, "
        "posting_id, regional_review_id, headquarters_review_id, "
        "cutoff_ledger_cursor, cutoff_at, established_ledger_cursor, "
        "scope_manifest_sha256, snapshot_manifest_sha256, "
        "count_manifest_sha256, control_manifest_sha256, "
        "has_pending_control_difference, established_by_user_id, "
        "established_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?, ?, 0, ?, ?, ?)",
        (
            "92000000000040008000000000000009",
            "92000000000040008000000000000001",
            "9200000000004000800000000000000a",
            "9200000000004000800000000000000b",
            "9200000000004000800000000000000c",
            "92000000000040008000000000000004",
            "92000000000040008000000000000003",
            "9200000000004000800000000000000d",
            "9200000000004000800000000000000e",
            timestamp,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            "f" * 64,
            "runtime-user",
            timestamp,
            timestamp,
        ),
    )


def test_0022_sqlite_real_upgrade_installs_terminal_guards_and_uniqueness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = _sqlite_url(tmp_path, "opening-terminal.db")
    config = _config(database_url)
    command.upgrade(config, REVISION_0022)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            version = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            assert version == REVISION_0022
            trigger_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            for table_name in TERMINAL_TABLES:
                assert f"trg_{table_name}_terminal_update_0022" in trigger_names
                assert f"trg_{table_name}_terminal_delete_0022" in trigger_names
                assert (
                    f"trg_{table_name}_immutable_update_0010"
                    not in trigger_names
                )
                assert (
                    f"trg_{table_name}_immutable_delete_0010"
                    not in trigger_names
                )
            assert "trg_stocktake_postings_opening_unique_0022" in trigger_names
            # SQLite deliberately has no fake deferred/replica/TRUNCATE
            # emulation.  The original 0009 row guards remain the executable
            # local boundary while the service performs its final graph proof.
            assert not set(POSTGRESQL_COMMIT_TRIGGERS) & trigger_names
            for table_name in LEDGER_FACT_TABLES:
                assert f"trg_{table_name}_immutable_update" in trigger_names
                assert f"trg_{table_name}_immutable_delete" in trigger_names

            _seed_task_and_posting(connection)
            _seed_other_terminal_rows(connection)
            with pytest.raises(sa.exc.IntegrityError, match="already has a posting"):
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_postings "
                    "(id, task_id, round_id, posting_kind, "
                    "inventory_transaction_id, total_quantity, "
                    "idempotency_key_hash, request_hash, posted_by_user_id, "
                    "posted_at, created_at) VALUES (?, ?, ?, 'opening', NULL, "
                    "0, ?, ?, ?, ?, ?)",
                    (
                        "92000000000040008000000000000010",
                        "92000000000040008000000000000001",
                        "92000000000040008000000000000011",
                        "1" * 64,
                        "2" * 64,
                        "runtime-user",
                        "2026-08-31 12:00:00+00:00",
                        "2026-08-31 12:00:00+00:00",
                    ),
                )
            for table_name in TERMINAL_TABLES:
                with pytest.raises(
                    sa.exc.IntegrityError,
                    match="opening terminal facts are immutable",
                ):
                    connection.exec_driver_sql(
                        f"UPDATE {table_name} SET created_at = created_at"
                    )
                with pytest.raises(
                    sa.exc.IntegrityError,
                    match="opening terminal facts are immutable",
                ):
                    connection.exec_driver_sql(f"DELETE FROM {table_name}")
    finally:
        engine.dispose()


def test_0022_sqlite_upgrade_preflight_and_downgrade_fail_closed_on_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    duplicate_url = _sqlite_url(tmp_path, "opening-duplicate.db")
    duplicate_config = _config(duplicate_url)
    command.upgrade(duplicate_config, REVISION_0021)
    duplicate_engine = sa.create_engine(duplicate_url)
    try:
        with duplicate_engine.begin() as connection:
            _seed_task_and_posting(connection, second_opening=True)
        with pytest.raises(RuntimeError, match="0022 preflight failed"):
            command.upgrade(duplicate_config, REVISION_0022)
        with duplicate_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0021
    finally:
        duplicate_engine.dispose()

    guarded_url = _sqlite_url(tmp_path, "opening-downgrade-block.db")
    guarded_config = _config(guarded_url)
    command.upgrade(guarded_config, REVISION_0022)
    guarded_engine = sa.create_engine(guarded_url)
    try:
        with guarded_engine.begin() as connection:
            _seed_task_and_posting(connection)
        with pytest.raises(RuntimeError, match="cannot downgrade 0022"):
            command.downgrade(guarded_config, REVISION_0021)
        with guarded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0022
    finally:
        guarded_engine.dispose()


def test_0022_sqlite_empty_round_trip_restores_0010_guard_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = _sqlite_url(tmp_path, "opening-round-trip.db")
    config = _config(database_url)
    command.upgrade(config, REVISION_0022)
    command.downgrade(config, REVISION_0021)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            trigger_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            for table_name in TERMINAL_TABLES:
                assert f"trg_{table_name}_immutable_update_0010" in trigger_names
                assert f"trg_{table_name}_immutable_delete_0010" in trigger_names
                assert f"trg_{table_name}_terminal_update_0022" not in trigger_names
                assert f"trg_{table_name}_terminal_delete_0022" not in trigger_names
            assert "trg_stocktake_postings_opening_unique_0022" not in trigger_names
    finally:
        engine.dispose()


def test_0022_postgresql_offline_sql_proves_guards_acl_and_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = "postgresql+psycopg://offline:offline@localhost/offline"
    upgrade_output = io.StringIO()
    command.upgrade(
        _config(database_url, output_buffer=upgrade_output),
        f"{REVISION_0021}:{REVISION_0022}",
        sql=True,
    )
    upgrade_sql = upgrade_output.getvalue()
    assert "GROUP BY task_id" in upgrade_sql
    assert "HAVING count(*) > 1" in upgrade_sql
    assert "NOT public.rsc_opening_terminal_graph_complete_0022" in upgrade_sql
    assert "FOR UPDATE" in upgrade_sql
    assert "SET search_path = pg_catalog, public" in upgrade_sql
    assert "BEFORE TRUNCATE ON public.stocktake_postings" in upgrade_sql
    assert "BEFORE TRUNCATE ON public.stocktake_posting_items" in upgrade_sql
    assert (
        "BEFORE TRUNCATE ON public.inventory_opening_establishments"
        in upgrade_sql
    )
    for trigger_name in (
        "trg_stocktake_postings_terminal_0022",
        "trg_stocktake_posting_items_terminal_0022",
        "trg_inventory_opening_establishments_terminal_0022",
        "trg_stocktake_postings_opening_unique_0022",
    ):
        assert f"ENABLE ALWAYS TRIGGER {trigger_name}" in upgrade_sql
    for trigger_name in POSTGRESQL_COMMIT_TRIGGERS:
        assert f"CREATE CONSTRAINT TRIGGER {trigger_name}" in upgrade_sql
        assert "DEFERRABLE INITIALLY DEFERRED" in upgrade_sql
        assert f"ENABLE ALWAYS TRIGGER {trigger_name}" in upgrade_sql
    assert (
        "CREATE CONSTRAINT TRIGGER "
        "trg_stocktake_tasks_opening_commit_0022 AFTER UPDATE"
        in upgrade_sql
    )
    assert (
        "CREATE CONSTRAINT TRIGGER "
        "trg_inventory_freezes_opening_commit_0022 AFTER UPDATE"
        in upgrade_sql
    )
    for table_name in LEDGER_FACT_TABLES:
        assert (
            f"BEFORE TRUNCATE ON public.{table_name}" in upgrade_sql
        )
        assert (
            f"ENABLE ALWAYS TRIGGER trg_{table_name}_immutable_0022"
            in upgrade_sql
        )
        assert (
            f"ENABLE ALWAYS TRIGGER "
            f"trg_{table_name}_immutable_truncate_0022"
            in upgrade_sql
        )
    assert "'OPEN-' || replace(task.id::text, '-', '')" in upgrade_sql
    assert "'opening-stocktake:' || task.id::text" in upgrade_sql
    assert "'approved-opening-stocktake'" in upgrade_sql
    assert "transaction-idempotency.v1" in upgrade_sql
    assert "transaction-request.v1" in upgrade_sql
    assert "opening_stocktake_closed" in upgrade_sql
    assert "stocktake.opening.closed" in upgrade_sql
    assert "opening post and close require independent transactions" in upgrade_sql
    assert "NEW.status <> 'pending'" in upgrade_sql
    assert (
        "GRANT UPDATE (status, posted_at, closed_at, version, updated_at) "
        "ON TABLE public.stocktake_tasks TO star_oam_api"
    ) in upgrade_sql
    assert "current_round_no" not in upgrade_sql.split(
        "ON TABLE public.stocktake_tasks TO star_oam_api"
    )[0].rsplit("GRANT UPDATE (", 1)[-1]
    assert (
        "GRANT UPDATE (status, valid_to, released_by_user_id, release_reason, "
        "version, updated_at) ON TABLE public.inventory_freezes "
        "TO star_oam_api"
    ) in upgrade_sql
    for table_name in TERMINAL_TABLES:
        assert f"public.{table_name}" in upgrade_sql
        assert f"GRANT DELETE ON TABLE public.{table_name}" not in upgrade_sql
        assert f"GRANT TRUNCATE ON TABLE public.{table_name}" not in upgrade_sql

    downgrade_output = io.StringIO()
    command.downgrade(
        _config(database_url, output_buffer=downgrade_output),
        f"{REVISION_0022}:{REVISION_0021}",
        sql=True,
    )
    downgrade_sql = downgrade_output.getvalue()
    assert "cannot downgrade 0022" in downgrade_sql
    assert "DROP FUNCTION public.rsc_block_opening_terminal_mutation_0022" in (
        downgrade_sql
    )
    assert "rsc_block_stocktake_fact_mutation_0010" in downgrade_sql
    assert (
        "REVOKE EXECUTE ON FUNCTION "
        "public.rsc_block_stocktake_fact_mutation_0010() "
        "FROM PUBLIC, star_oam_api"
    ) in downgrade_sql
    assert "rsc_block_inventory_ledger_mutation_0009" in downgrade_sql
    assert (
        "REVOKE EXECUTE ON FUNCTION "
        "public.rsc_block_inventory_ledger_mutation_0009() "
        "FROM PUBLIC, star_oam_api"
    ) in downgrade_sql
    for table_name in LEDGER_FACT_TABLES:
        assert (
            f"CREATE TRIGGER trg_{table_name}_immutable BEFORE UPDATE OR DELETE"
            in downgrade_sql
        )
    assert (
        "GRANT UPDATE ON TABLE public.audit_chain_heads, "
        "public.auth_idempotency_operations"
    ) in downgrade_sql
