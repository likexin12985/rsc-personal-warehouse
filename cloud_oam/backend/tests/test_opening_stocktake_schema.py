from __future__ import annotations

import io
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import event, inspect


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
REVISION_0009 = "20260830_0009"
REVISION_0010 = "20260830_0010"
NOW = "2026-08-30 00:00:00+00:00"
LATER = "2026-08-30 00:00:01+00:00"
HQ_REVIEW_AT = "2026-08-30 00:00:02+00:00"
POSTED_AT = "2026-08-30 00:00:03+00:00"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

FORMAL_TABLES = {
    "stocktake_tasks",
    "stocktake_scopes",
    "stocktake_control_snapshot_lines",
    "inventory_freezes",
    "stocktake_snapshot_lines",
    "stocktake_rounds",
    "stocktake_count_lines",
    "stocktake_count_serials",
    "stocktake_differences",
    "stocktake_reviews",
    "stocktake_review_items",
    "stocktake_postings",
    "stocktake_posting_items",
    "inventory_opening_establishments",
}


def _config(database_url: str, *, output_buffer=None) -> Config:
    config = Config(str(ALEMBIC_INI), output_buffer=output_buffer)
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _engine(database_url: str) -> sa.Engine:
    engine = sa.create_engine(database_url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _uuid(ordinal: int) -> str:
    return f"{ordinal:032x}"


def _insert_legacy_stocktake_sentinel(engine: sa.Engine) -> None:
    user_id = "00000000-0000-0000-0000-000000000901"
    warehouse_id = "00000000-0000-0000-0000-000000000902"
    material_id = "00000000-0000-0000-0000-000000000903"
    task_id = "00000000-0000-0000-0000-000000000904"
    item_id = "00000000-0000-0000-0000-000000000905"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, mobile, name, password_hash, role, province, is_active, "
            "require_password_change, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                "13800000901",
                "legacy-stocktake-sentinel",
                "not-a-real-password-hash",
                "technician",
                "江苏",
                True,
                False,
                NOW,
                NOW,
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO warehouses "
            "(id, code, name, province, city, warehouse_type, condition_scope, "
            "warehouse_level, ownership_type, position_scope, "
            "parent_warehouse_id, manager_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                warehouse_id,
                "LEGACY-STOCKTAKE-WH",
                "legacy stocktake warehouse",
                "江苏",
                "南京",
                "service_backpack",
                "good",
                "network",
                "regular",
                "unrestricted",
                None,
                user_id,
                True,
                NOW,
                NOW,
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO legacy_v09_materials "
            "(id, code, name, specification, category, aliases, unit, "
            "is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                material_id,
                "LEGACY-STOCKTAKE-SKU",
                "legacy stocktake material",
                "",
                "其他",
                "",
                "个",
                True,
                NOW,
                NOW,
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO stocktake_tasks "
            "(id, number, warehouse_id, assignee_id, status, deadline, note, "
            "created_by_id, submitted_at, closed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                "ST-V09-SENTINEL",
                warehouse_id,
                user_id,
                "submitted",
                None,
                "must remain legacy evidence",
                user_id,
                NOW,
                None,
                NOW,
                NOW,
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO stocktake_items "
            "(id, task_id, material_id, condition, expected_quantity, "
            "counted_quantity, remark) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, task_id, material_id, "good", 7, 5, "legacy-only"),
        )


def _insert_formal_task(
    connection: sa.Connection,
    *,
    task_id: str,
    task_no: str,
    region_org_id: str,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stocktake_tasks "
        "(id, task_no, task_type, region_org_id, status, blind_count, "
        "cutoff_ledger_cursor, cutoff_at, scope_manifest_sha256, "
        "snapshot_manifest_sha256, control_source_system_id, "
        "control_sync_run_id, control_snapshot_at, control_manifest_sha256, "
        "current_round_no, created_by_user_id, deadline, issued_at, frozen_at, "
        "submitted_at, posted_at, closed_at, cancelled_at, version, note, "
        "created_at, updated_at) "
        "VALUES (?, ?, 'opening', ?, 'draft', 1, NULL, NULL, NULL, NULL, "
        "NULL, NULL, NULL, NULL, 0, ?, NULL, NULL, NULL, NULL, NULL, NULL, "
        "NULL, 0, '', ?, ?)",
        (task_id, task_no, region_org_id, _uuid(990), NOW, NOW),
    )


def _insert_scope(
    connection: sa.Connection,
    *,
    scope_id: str,
    task_id: str,
    scope_no: int,
    scope_key: str,
    scope_hash: str,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stocktake_scopes "
        "(id, task_id, scope_no, scope_mode, location_id, owner_org_id, "
        "custodian_person_id_snapshot, assignee_user_id, material_id, "
        "condition_code, availability_bucket, scope_key, scope_sha256, created_at) "
        "VALUES (?, ?, ?, 'location_all', ?, ?, NULL, ?, NULL, NULL, NULL, ?, ?, ?)",
        (
            scope_id,
            task_id,
            scope_no,
            _uuid(992),
            _uuid(993),
            _uuid(990),
            scope_key,
            scope_hash,
            NOW,
        ),
    )


def test_0010_renames_v09_stocktake_without_interpreting_rows(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'legacy-isolation.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0009)

    source_engine = _engine(database_url)
    try:
        _insert_legacy_stocktake_sentinel(source_engine)
    finally:
        source_engine.dispose()

    command.upgrade(config, REVISION_0010)
    engine = _engine(database_url)
    try:
        inspector = inspect(engine)
        assert FORMAL_TABLES <= set(inspector.get_table_names())
        assert {
            "legacy_v09_stocktake_tasks",
            "legacy_v09_stocktake_items",
        } <= set(inspector.get_table_names())
        assert {
            index["name"]
            for index in inspector.get_indexes("legacy_v09_stocktake_tasks")
        } == {"ix_stocktake_tasks_number", "ix_stocktake_tasks_status"}
        item_foreign_keys = {
            tuple(foreign_key["constrained_columns"]): foreign_key[
                "referred_table"
            ]
            for foreign_key in inspector.get_foreign_keys(
                "legacy_v09_stocktake_items"
            )
        }
        assert item_foreign_keys[("task_id",)] == "legacy_v09_stocktake_tasks"
        assert item_foreign_keys[("material_id",)] == "legacy_v09_materials"
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT number, status, note FROM legacy_v09_stocktake_tasks"
            ).one() == (
                "ST-V09-SENTINEL",
                "submitted",
                "must remain legacy evidence",
            )
            assert connection.exec_driver_sql(
                "SELECT expected_quantity, counted_quantity, remark "
                "FROM legacy_v09_stocktake_items"
            ).one() == (7, 5, "legacy-only")
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM stocktake_tasks"
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM inventory_opening_establishments"
            ).scalar_one() == 0
    finally:
        engine.dispose()

    command.downgrade(config, REVISION_0009)
    downgraded_engine = _engine(database_url)
    try:
        inspector = inspect(downgraded_engine)
        tables = set(inspector.get_table_names())
        assert {"stocktake_tasks", "stocktake_items"} <= tables
        assert "legacy_v09_stocktake_tasks" not in tables
        assert {
            index["name"] for index in inspector.get_indexes("stocktake_tasks")
        } == {"ix_stocktake_tasks_number", "ix_stocktake_tasks_status"}
        restored_foreign_keys = {
            tuple(foreign_key["constrained_columns"]): foreign_key[
                "referred_table"
            ]
            for foreign_key in inspector.get_foreign_keys("stocktake_items")
        }
        assert restored_foreign_keys[("task_id",)] == "stocktake_tasks"
        assert restored_foreign_keys[("material_id",)] == "legacy_v09_materials"
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT number, status, note FROM stocktake_tasks"
            ).one() == (
                "ST-V09-SENTINEL",
                "submitted",
                "must remain legacy evidence",
            )
            assert connection.exec_driver_sql(
                "SELECT expected_quantity, counted_quantity, remark "
                "FROM stocktake_items"
            ).one() == (7, 5, "legacy-only")
    finally:
        downgraded_engine.dispose()


def test_0010_core_constraints_control_difference_and_fact_immutability(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'core-constraints.db'}"
    command.upgrade(_config(database_url), REVISION_0010)
    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        task_columns = {column["name"] for column in inspector.get_columns("stocktake_tasks")}
        assert "region_org_id" in task_columns
        assert "owner_org_id" not in task_columns
        establishment_uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "inventory_opening_establishments"
            )
        }
        assert ("owner_org_id", "location_id") in establishment_uniques
        establishment_scope_fks = {
            (
                tuple(foreign_key["constrained_columns"]),
                tuple(foreign_key["referred_columns"]),
            )
            for foreign_key in inspector.get_foreign_keys(
                "inventory_opening_establishments"
            )
            if foreign_key["referred_table"] == "stocktake_scopes"
        }
        assert (
            ("scope_id", "task_id", "owner_org_id", "location_id"),
            ("id", "task_id", "owner_org_id", "location_id"),
        ) in establishment_scope_fks
        freeze_scope_fks = {
            (
                tuple(foreign_key["constrained_columns"]),
                tuple(foreign_key["referred_columns"]),
            )
            for foreign_key in inspector.get_foreign_keys("inventory_freezes")
            if foreign_key["referred_table"] == "stocktake_scopes"
        }
        assert (
            ("stocktake_scope_id", "task_id", "scope_key"),
            ("id", "task_id", "scope_key"),
        ) in freeze_scope_fks

        trigger_names = {
            row[0]
            for row in engine.connect().exec_driver_sql(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND name LIKE '%0010'"
            )
        }
        assert {
            "trg_stocktake_differences_immutable_update_0010",
            "trg_stocktake_differences_immutable_delete_0010",
            "trg_stocktake_scopes_immutable_update_0010",
            "trg_stocktake_scopes_immutable_delete_0010",
            "trg_stocktake_scopes_sealed_insert_0010",
            "trg_stocktake_control_snapshot_lines_sealed_insert_0010",
            "trg_stocktake_snapshot_lines_sealed_insert_0010",
            "trg_inventory_freezes_sealed_insert_0010",
            "trg_stocktake_count_lines_submitted_immutable_insert_0010",
            "trg_stocktake_count_lines_submitted_immutable_update_0010",
            "trg_stocktake_count_serials_submitted_immutable_insert_0010",
            "trg_stocktake_posting_items_validate_insert_0010",
            "trg_stocktake_differences_chronology_insert_0010",
            "trg_stocktake_reviews_chronology_insert_0010",
            "trg_stocktake_review_items_chronology_insert_0010",
            "trg_stocktake_postings_chronology_insert_0010",
            "trg_stocktake_posting_items_chronology_insert_0010",
            "trg_inventory_opening_establishments_chronology_insert_0010",
            "trg_stocktake_rounds_opening_insert_0010",
            "trg_stocktake_tasks_opening_sealed_update_0010",
            "trg_stocktake_tasks_opening_post_update_0010",
            "trg_stocktake_tasks_opening_evidence_delete_0010",
            "trg_inventory_freezes_transition_update_0010",
            "trg_inventory_freezes_transition_delete_0010",
            "trg_inventory_opening_establishments_immutable_update_0010",
        } <= trigger_names

        connection = engine.connect()
        try:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            with connection.begin():
                task_id = _uuid(1001)
                scope_1 = _uuid(1002)
                scope_2 = _uuid(1003)
                round_id = _uuid(1004)
                control_line_id = _uuid(1005)
                difference_id = _uuid(1006)
                count_line_id = _uuid(1007)
                regional_review_id = _uuid(1040)
                headquarters_review_id = _uuid(1041)
                _insert_formal_task(
                    connection,
                    task_id=task_id,
                    task_no="OPEN-REGION-1",
                    region_org_id=_uuid(991),
                )
                with pytest.raises(sa.exc.IntegrityError):
                    _insert_formal_task(
                        connection,
                        task_id=_uuid(1008),
                        task_no="OPEN-REGION-1-DUP",
                        region_org_id=_uuid(991),
                    )
                _insert_formal_task(
                    connection,
                    task_id=_uuid(1009),
                    task_no="OPEN-REGION-2",
                    region_org_id=_uuid(994),
                )
                _insert_scope(
                    connection,
                    scope_id=scope_1,
                    task_id=task_id,
                    scope_no=1,
                    scope_key="scope-one",
                    scope_hash=HASH_A,
                )
                _insert_scope(
                    connection,
                    scope_id=scope_2,
                    task_id=task_id,
                    scope_no=2,
                    scope_key="scope-two",
                    scope_hash=HASH_B,
                )
                connection.exec_driver_sql(
                    "INSERT INTO inventory_freezes "
                    "(id, task_id, stocktake_scope_id, scope_key, freeze_mode, "
                    "status, valid_from, valid_to, created_by_user_id, "
                    "released_by_user_id, release_reason, version, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, 'hard', 'active', ?, NULL, "
                    "?, NULL, '', 0, ?, ?)",
                    (
                        _uuid(1010),
                        task_id,
                        scope_1,
                        "scope-one",
                        NOW,
                        _uuid(990),
                        NOW,
                        NOW,
                    ),
                )
                with pytest.raises(sa.exc.IntegrityError):
                    connection.exec_driver_sql(
                        "INSERT INTO inventory_freezes "
                        "(id, task_id, stocktake_scope_id, scope_key, freeze_mode, "
                        "status, valid_from, valid_to, created_by_user_id, "
                        "released_by_user_id, release_reason, version, created_at, "
                        "updated_at) VALUES (?, ?, ?, ?, 'hard', 'active', ?, NULL, "
                        "?, NULL, '', 0, ?, ?)",
                        (
                            _uuid(1011),
                            task_id,
                            scope_2,
                            "scope-one",
                            NOW,
                            _uuid(990),
                            NOW,
                            NOW,
                        ),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE stocktake_scopes SET scope_key = 'scope-mutated' "
                        "WHERE id = ?",
                        (scope_1,),
                    )
                connection.exec_driver_sql(
                    "INSERT INTO inventory_freezes "
                    "(id, task_id, stocktake_scope_id, scope_key, freeze_mode, "
                    "status, valid_from, valid_to, created_by_user_id, "
                    "released_by_user_id, release_reason, version, created_at, "
                    "updated_at) VALUES (?, ?, ?, 'scope-two', 'hard', 'active', "
                    "?, NULL, ?, NULL, '', 0, ?, ?)",
                    (_uuid(1055), task_id, scope_2, NOW, _uuid(990), NOW, NOW),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_control_snapshot_lines "
                    "(id, task_id, line_no, external_business_key, "
                    "external_object_version_id, material_id, condition_code, "
                    "control_qty, mapping_status, source_updated_at, payload_sha256, "
                    "mapping_note, created_at) VALUES (?, ?, 1, ?, NULL, NULL, NULL, "
                    "3, 'unresolved', NULL, ?, 'unmapped control line', ?)",
                    (control_line_id, task_id, "control-1", HASH_A, NOW),
                )
                connection.exec_driver_sql(
                    "UPDATE stocktake_tasks SET status = 'counting', "
                    "cutoff_ledger_cursor = 0, cutoff_at = ?, "
                    "scope_manifest_sha256 = ?, snapshot_manifest_sha256 = ?, "
                    "control_source_system_id = ?, control_sync_run_id = ?, "
                    "control_snapshot_at = ?, control_manifest_sha256 = ?, "
                    "current_round_no = 1, issued_at = ?, frozen_at = ?, "
                    "version = 1, updated_at = ? WHERE id = ?",
                    (
                        NOW,
                        HASH_A,
                        HASH_A,
                        _uuid(1049),
                        _uuid(1050),
                        NOW,
                        HASH_C,
                        NOW,
                        NOW,
                        NOW,
                        task_id,
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_rounds "
                    "(id, task_id, round_no, round_type, status, "
                    "submitted_by_user_id, started_at, submitted_at, "
                    "count_manifest_sha256, idempotency_key_hash, created_at, "
                    "updated_at) VALUES (?, ?, 1, 'initial', 'counting', NULL, ?, "
                    "NULL, NULL, ?, ?, ?)",
                    (round_id, task_id, NOW, HASH_A, NOW, NOW),
                )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE stocktake_tasks SET cutoff_ledger_cursor = 1 "
                        "WHERE id = ?",
                        (task_id,),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="sealed"):
                    connection.exec_driver_sql(
                        "INSERT INTO inventory_freezes "
                        "(id, task_id, stocktake_scope_id, scope_key, freeze_mode, "
                        "status, valid_from, valid_to, created_by_user_id, "
                        "released_by_user_id, release_reason, version, created_at, "
                        "updated_at) VALUES (?, ?, ?, 'late-freeze', 'hard', "
                        "'active', ?, NULL, ?, NULL, '', 0, ?, ?)",
                        (_uuid(1056), task_id, scope_1, NOW, _uuid(990), NOW, NOW),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE inventory_freezes SET freeze_mode = 'cutoff_replay', "
                        "status = 'released', valid_to = ?, released_by_user_id = ?, "
                        "release_reason = 'must not change mode', version = 1, "
                        "updated_at = ? WHERE id = ?",
                        (LATER, _uuid(990), LATER, _uuid(1010)),
                    )
                connection.exec_driver_sql(
                    "UPDATE inventory_freezes SET status = 'released', valid_to = ?, "
                    "released_by_user_id = ?, release_reason = 'reviewed release', "
                    "version = 1, updated_at = ? WHERE id = ?",
                    (LATER, _uuid(990), LATER, _uuid(1010)),
                )
                with pytest.raises(sa.exc.DatabaseError, match="transition"):
                    connection.exec_driver_sql(
                        "UPDATE inventory_freezes SET release_reason = 'changed', "
                        "version = 2, updated_at = ? WHERE id = ?",
                        (LATER, _uuid(1010)),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="sealed"):
                    _insert_scope(
                        connection,
                        scope_id=_uuid(1031),
                        task_id=task_id,
                        scope_no=3,
                        scope_key="late-scope",
                        scope_hash=HASH_C,
                    )
                with pytest.raises(sa.exc.DatabaseError, match="sealed"):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_control_snapshot_lines "
                        "(id, task_id, line_no, external_business_key, "
                        "external_object_version_id, material_id, condition_code, "
                        "control_qty, mapping_status, source_updated_at, "
                        "payload_sha256, mapping_note, created_at) VALUES (?, ?, 2, "
                        "?, NULL, NULL, NULL, 0, 'unresolved', NULL, ?, "
                        "'late control line', ?)",
                        (_uuid(1032), task_id, "late-control", HASH_B, NOW),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="sealed"):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_snapshot_lines "
                        "(id, task_id, scope_id, stock_account_id, book_qty, "
                        "ledger_cursor, account_dimension_sha256, "
                        "serial_snapshot_jsonb, serial_snapshot_sha256, "
                        "serial_count, created_at) VALUES (?, ?, ?, ?, 0, 0, ?, "
                        "'[]', ?, 0, ?)",
                        (
                            _uuid(1033),
                            task_id,
                            scope_1,
                            _uuid(1034),
                            HASH_A,
                            HASH_B,
                            NOW,
                        ),
                    )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_count_lines "
                    "(id, task_id, round_id, scope_id, stock_account_id, "
                    "counted_qty, count_method, reason_code, remark, "
                    "counted_by_user_id, counted_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, 'manual', NULL, '', ?, ?, ?, ?)",
                    (
                        count_line_id,
                        task_id,
                        round_id,
                        scope_1,
                        _uuid(1016),
                        _uuid(990),
                        NOW,
                        NOW,
                        NOW,
                    ),
                )
                connection.exec_driver_sql(
                    "UPDATE stocktake_count_lines SET counted_qty = 2 "
                    "WHERE id = ?",
                    (count_line_id,),
                )
                connection.exec_driver_sql(
                    "UPDATE stocktake_rounds SET status = 'submitted', "
                    "submitted_by_user_id = ?, submitted_at = ?, "
                    "count_manifest_sha256 = ?, updated_at = ? WHERE id = ?",
                    (_uuid(990), NOW, HASH_B, NOW, round_id),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_differences "
                    "(id, task_id, round_id, scope_id, control_snapshot_line_id, "
                    "difference_no, difference_type, material_id, expected_account_id, "
                    "observed_account_id, serial_id, book_qty, counted_qty, "
                    "difference_qty, affected_qty, reason_code, reason_text, "
                    "evidence_required, created_at) VALUES (?, ?, ?, NULL, ?, 1, "
                    "'control_unassigned', NULL, NULL, NULL, NULL, 3, 0, -3, 3, "
                    "NULL, 'not assigned to a physical location', 1, ?)",
                    (difference_id, task_id, round_id, control_line_id, NOW),
                )
                with pytest.raises(sa.exc.IntegrityError):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_differences "
                        "(id, task_id, round_id, scope_id, control_snapshot_line_id, "
                        "difference_no, difference_type, material_id, "
                        "expected_account_id, observed_account_id, serial_id, "
                        "book_qty, counted_qty, difference_qty, affected_qty, "
                        "reason_code, reason_text, evidence_required, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 2, 'control_unassigned', NULL, NULL, "
                        "NULL, NULL, 3, 0, -3, 3, NULL, 'must fail', 1, ?)",
                        (
                            _uuid(1012),
                            task_id,
                            round_id,
                            scope_1,
                            control_line_id,
                            NOW,
                        ),
                    )
                with pytest.raises(sa.exc.IntegrityError):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_differences "
                        "(id, task_id, round_id, scope_id, control_snapshot_line_id, "
                        "difference_no, difference_type, material_id, "
                        "expected_account_id, observed_account_id, serial_id, "
                        "book_qty, counted_qty, difference_qty, affected_qty, "
                        "reason_code, reason_text, evidence_required, created_at) "
                        "VALUES (?, ?, ?, NULL, NULL, 3, 'missing', ?, ?, NULL, NULL, "
                        "3, 0, -3, 3, NULL, 'missing physical scope', 1, ?)",
                        (
                            _uuid(1013),
                            task_id,
                            round_id,
                            _uuid(1014),
                            _uuid(1015),
                            NOW,
                        ),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE stocktake_count_lines SET counted_qty = 3 "
                        "WHERE id = ?",
                        (count_line_id,),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_count_lines "
                        "(id, task_id, round_id, scope_id, stock_account_id, "
                        "counted_qty, count_method, reason_code, remark, "
                        "counted_by_user_id, counted_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 0, 'manual', NULL, '', ?, ?, ?, ?)",
                        (
                            _uuid(1017),
                            task_id,
                            round_id,
                            scope_1,
                            _uuid(1018),
                            _uuid(990),
                            NOW,
                            NOW,
                            NOW,
                        ),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_count_serials "
                        "(count_line_id, round_id, serial_id, result, created_at) "
                        "VALUES (?, ?, ?, 'present', ?)",
                        (count_line_id, round_id, _uuid(1019), NOW),
                    )

                counting_round_id = _uuid(1025)
                counting_line_id = _uuid(1026)
                _insert_scope(
                    connection,
                    scope_id=_uuid(1052),
                    task_id=_uuid(1009),
                    scope_no=1,
                    scope_key="second-task-scope",
                    scope_hash=HASH_C,
                )
                connection.exec_driver_sql(
                    "INSERT INTO inventory_freezes "
                    "(id, task_id, stocktake_scope_id, scope_key, freeze_mode, "
                    "status, valid_from, valid_to, created_by_user_id, "
                    "released_by_user_id, release_reason, version, created_at, "
                    "updated_at) VALUES (?, ?, ?, 'second-task-scope', 'hard', "
                    "'active', ?, NULL, ?, NULL, '', 0, ?, ?)",
                    (
                        _uuid(1057),
                        _uuid(1009),
                        _uuid(1052),
                        NOW,
                        _uuid(990),
                        NOW,
                        NOW,
                    ),
                )
                connection.exec_driver_sql(
                    "UPDATE stocktake_tasks SET status = 'counting', "
                    "cutoff_ledger_cursor = 0, cutoff_at = ?, "
                    "scope_manifest_sha256 = ?, snapshot_manifest_sha256 = ?, "
                    "control_source_system_id = ?, control_sync_run_id = ?, "
                    "control_snapshot_at = ?, control_manifest_sha256 = ?, "
                    "current_round_no = 1, issued_at = ?, frozen_at = ?, "
                    "version = 1, updated_at = ? WHERE id = ?",
                    (
                        NOW,
                        HASH_C,
                        HASH_C,
                        _uuid(1053),
                        _uuid(1054),
                        NOW,
                        HASH_C,
                        NOW,
                        NOW,
                        NOW,
                        _uuid(1009),
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_rounds "
                    "(id, task_id, round_no, round_type, status, "
                    "submitted_by_user_id, started_at, submitted_at, "
                    "count_manifest_sha256, idempotency_key_hash, created_at, "
                    "updated_at) VALUES (?, ?, 1, 'initial', 'counting', NULL, ?, "
                    "NULL, NULL, ?, ?, ?)",
                    (counting_round_id, _uuid(1009), NOW, HASH_C, NOW, NOW),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_count_lines "
                    "(id, task_id, round_id, scope_id, stock_account_id, "
                    "counted_qty, count_method, reason_code, remark, "
                    "counted_by_user_id, counted_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 0, 'manual', NULL, '', ?, ?, ?, ?)",
                    (
                        counting_line_id,
                        _uuid(1009),
                        counting_round_id,
                        scope_1,
                        _uuid(1027),
                        _uuid(990),
                        NOW,
                        NOW,
                        NOW,
                    ),
                )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE stocktake_count_lines SET task_id = ?, round_id = ? "
                        "WHERE id = ?",
                        (task_id, round_id, counting_line_id),
                    )

                connection.exec_driver_sql(
                    "INSERT INTO stocktake_reviews "
                    "(id, task_id, round_id, review_stage, reviewer_user_id, "
                    "reviewer_person_id, reviewer_role_assignment_id, "
                    "authorization_version, decision, comment, "
                    "decision_manifest_sha256, idempotency_key_hash, reviewed_at, "
                    "created_at) VALUES (?, ?, ?, 'region', ?, ?, ?, 1, 'approve', "
                    "'regional approval', ?, ?, ?, ?)",
                    (
                        regional_review_id,
                        task_id,
                        round_id,
                        _uuid(990),
                        _uuid(1044),
                        _uuid(1045),
                        HASH_A,
                        HASH_B,
                        LATER,
                        LATER,
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_review_items "
                    "(review_id, difference_id, task_id, round_id, decision, "
                    "comment, created_at) VALUES (?, ?, ?, ?, "
                    "'pending_verification', 'regional pending', ?)",
                    (regional_review_id, difference_id, task_id, round_id, LATER),
                )
                with pytest.raises(sa.exc.DatabaseError, match="sealed"):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_differences "
                        "(id, task_id, round_id, scope_id, control_snapshot_line_id, "
                        "difference_no, difference_type, material_id, "
                        "expected_account_id, observed_account_id, serial_id, "
                        "book_qty, counted_qty, difference_qty, affected_qty, "
                        "reason_code, reason_text, evidence_required, created_at) "
                        "VALUES (?, ?, ?, NULL, ?, 2, 'control_unassigned', NULL, "
                        "NULL, NULL, NULL, 1, 0, -1, 1, NULL, 'late', 1, ?)",
                        (_uuid(1042), task_id, round_id, control_line_id, LATER),
                    )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_reviews "
                    "(id, task_id, round_id, review_stage, reviewer_user_id, "
                    "reviewer_person_id, reviewer_role_assignment_id, "
                    "authorization_version, decision, comment, "
                    "decision_manifest_sha256, idempotency_key_hash, reviewed_at, "
                    "created_at) VALUES (?, ?, ?, 'headquarters', ?, ?, ?, 1, "
                    "'approve', 'headquarters approval', ?, ?, ?, ?)",
                    (
                        headquarters_review_id,
                        task_id,
                        round_id,
                        _uuid(989),
                        _uuid(1046),
                        _uuid(1047),
                        HASH_A,
                        HASH_C,
                        HQ_REVIEW_AT,
                        HQ_REVIEW_AT,
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_review_items "
                    "(review_id, difference_id, task_id, round_id, decision, "
                    "comment, created_at) VALUES (?, ?, ?, ?, "
                    "'pending_verification', 'headquarters pending', ?)",
                    (
                        headquarters_review_id,
                        difference_id,
                        task_id,
                        round_id,
                        HQ_REVIEW_AT,
                    ),
                )
                with pytest.raises(sa.exc.DatabaseError, match="sealed"):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_review_items "
                        "(review_id, difference_id, task_id, round_id, decision, "
                        "comment, created_at) VALUES (?, ?, ?, ?, 'no_adjustment', "
                        "'late regional item', ?)",
                        (
                            regional_review_id,
                            _uuid(1048),
                            task_id,
                            round_id,
                            HQ_REVIEW_AT,
                        ),
                    )

                inventory_transaction_id = _uuid(1028)
                inventory_movement_id = _uuid(1029)
                posting_id = _uuid(1030)
                connection.exec_driver_sql(
                    "INSERT INTO inventory_transactions "
                    "(id, transaction_no, movement_type, source_document_type, "
                    "source_document_id, posting_key, idempotency_key_hash, "
                    "request_hash, status, effective_at, posted_at, ledger_cursor, "
                    "reversed_transaction_id, actor_user_id, created_at) "
                    "VALUES (?, 'OPENING-TRIGGER-1', 'opening', 'opening_stocktake', "
                    "?, 'opening-trigger-1', ?, ?, 'posted', ?, ?, 1, NULL, ?, ?)",
                    (
                        inventory_transaction_id,
                        task_id,
                        HASH_A,
                        HASH_B,
                        NOW,
                        POSTED_AT,
                        _uuid(990),
                        POSTED_AT,
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO inventory_movements "
                    "(id, transaction_id, line_no, from_account_id, to_account_id, "
                    "external_boundary_code, quantity, created_at) "
                    "VALUES (?, ?, 1, NULL, ?, 'opening-stocktake', 1, ?)",
                    (inventory_movement_id, inventory_transaction_id, _uuid(1016), NOW),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_postings "
                    "(id, task_id, round_id, posting_kind, inventory_transaction_id, "
                    "total_quantity, idempotency_key_hash, request_hash, "
                    "posted_by_user_id, posted_at, created_at) "
                    "VALUES (?, ?, ?, 'opening', ?, 1, ?, ?, ?, ?, ?)",
                    (
                        posting_id,
                        task_id,
                        round_id,
                        inventory_transaction_id,
                        HASH_B,
                        HASH_C,
                        _uuid(990),
                        POSTED_AT,
                        POSTED_AT,
                    ),
                )
                with pytest.raises(
                    sa.exc.DatabaseError,
                    match="control-only difference",
                ):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_posting_items "
                        "(posting_id, inventory_movement_id, task_id, round_id, "
                        "count_line_id, difference_id, quantity, created_at) "
                        "VALUES (?, ?, ?, ?, NULL, ?, 1, ?)",
                        (
                            posting_id,
                            inventory_movement_id,
                            task_id,
                            round_id,
                            difference_id,
                            POSTED_AT,
                        ),
                    )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_posting_items "
                    "(posting_id, inventory_movement_id, task_id, round_id, "
                    "count_line_id, difference_id, quantity, created_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL, 1, ?)",
                    (
                        posting_id,
                        inventory_movement_id,
                        task_id,
                        round_id,
                        count_line_id,
                        POSTED_AT,
                    ),
                )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE stocktake_rounds SET updated_at = ? WHERE id = ?",
                        (NOW, round_id),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "UPDATE stocktake_differences SET reason_text = 'changed' "
                        "WHERE id = ?",
                        (difference_id,),
                    )

                connection.exec_driver_sql(
                    "UPDATE stocktake_tasks SET status = 'approved', "
                    "cutoff_ledger_cursor = 0, cutoff_at = ?, "
                    "scope_manifest_sha256 = ?, snapshot_manifest_sha256 = ?, "
                    "control_source_system_id = ?, control_sync_run_id = ?, "
                    "control_snapshot_at = ?, control_manifest_sha256 = ?, "
                    "current_round_no = 1, issued_at = ?, frozen_at = ?, "
                    "submitted_at = ?, version = 2, updated_at = ? WHERE id = ?",
                    (
                        NOW,
                        HASH_A,
                        HASH_A,
                        _uuid(1049),
                        _uuid(1050),
                        NOW,
                        HASH_C,
                        NOW,
                        NOW,
                        NOW,
                        POSTED_AT,
                        task_id,
                    ),
                )
                establishment_id = _uuid(1020)
                connection.exec_driver_sql(
                    "INSERT INTO inventory_opening_establishments "
                    "(id, task_id, scope_id, owner_org_id, location_id, round_id, "
                    "posting_id, regional_review_id, headquarters_review_id, "
                    "cutoff_ledger_cursor, cutoff_at, established_ledger_cursor, "
                    "scope_manifest_sha256, snapshot_manifest_sha256, "
                    "count_manifest_sha256, control_manifest_sha256, "
                    "has_pending_control_difference, established_by_user_id, "
                    "established_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "0, ?, 0, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        establishment_id,
                        task_id,
                        scope_1,
                        _uuid(993),
                        _uuid(992),
                        round_id,
                        posting_id,
                        regional_review_id,
                        headquarters_review_id,
                        NOW,
                        HASH_A,
                        HASH_A,
                        HASH_B,
                        HASH_C,
                        _uuid(990),
                        POSTED_AT,
                        POSTED_AT,
                    ),
                )
                assert connection.exec_driver_sql(
                    "SELECT has_pending_control_difference "
                    "FROM inventory_opening_establishments WHERE id = ?",
                    (establishment_id,),
                ).scalar_one() == 1
                with pytest.raises(sa.exc.IntegrityError):
                    connection.exec_driver_sql(
                        "INSERT INTO inventory_opening_establishments "
                        "(id, task_id, scope_id, owner_org_id, location_id, round_id, "
                        "posting_id, regional_review_id, headquarters_review_id, "
                        "cutoff_ledger_cursor, cutoff_at, established_ledger_cursor, "
                        "scope_manifest_sha256, snapshot_manifest_sha256, "
                        "count_manifest_sha256, control_manifest_sha256, "
                        "has_pending_control_difference, established_by_user_id, "
                        "established_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                        "0, ?, 0, ?, ?, ?, ?, 1, ?, ?, ?)",
                        (
                            _uuid(1024),
                            task_id,
                            scope_2,
                            _uuid(993),
                            _uuid(992),
                            round_id,
                            posting_id,
                            regional_review_id,
                            headquarters_review_id,
                            NOW,
                            HASH_A,
                            HASH_A,
                            HASH_B,
                            HASH_C,
                            _uuid(990),
                            POSTED_AT,
                            POSTED_AT,
                        ),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="sealed"):
                    connection.exec_driver_sql(
                        "INSERT INTO stocktake_posting_items "
                        "(posting_id, inventory_movement_id, task_id, round_id, "
                        "count_line_id, difference_id, quantity, created_at) "
                        "VALUES (?, ?, ?, ?, ?, NULL, 1, ?)",
                        (
                            posting_id,
                            _uuid(1051),
                            task_id,
                            round_id,
                            count_line_id,
                            POSTED_AT,
                        ),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="every scope"):
                    connection.exec_driver_sql(
                        "UPDATE stocktake_tasks SET status = 'posted', posted_at = ?, "
                        "version = 3, updated_at = ? WHERE id = ?",
                        (POSTED_AT, POSTED_AT, task_id),
                    )
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.exec_driver_sql(
                        "DELETE FROM inventory_opening_establishments WHERE id = ?",
                        (establishment_id,),
                    )
        finally:
            connection.close()
    finally:
        engine.dispose()


def test_0010_unused_downgrade_round_trip_and_used_gate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'downgrade-gate.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0010)
    command.downgrade(config, REVISION_0009)

    engine = sa.create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "stocktake_tasks" in tables
        assert "legacy_v09_stocktake_tasks" not in tables
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0009
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM permissions WHERE resource = 'stocktake'"
            ).scalar_one() == 0
    finally:
        engine.dispose()

    command.upgrade(config, REVISION_0010)
    engine = sa.create_engine(database_url)
    try:
        connection = engine.connect()
        try:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            with connection.begin():
                _insert_formal_task(
                    connection,
                    task_id=_uuid(1101),
                    task_no="USED-OPENING-TASK",
                    region_org_id=_uuid(1102),
                )
        finally:
            connection.close()
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="contains business data"):
        command.downgrade(config, REVISION_0009)
    verification_engine = sa.create_engine(database_url)
    try:
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0010
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM stocktake_tasks"
            ).scalar_one() == 1
    finally:
        verification_engine.dispose()


def test_0010_postgresql_offline_upgrade_contains_safety_ddl(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://migration:secret@db.invalid/cloud_oam",
        output_buffer=output,
    )
    command.upgrade(config, REVISION_0010, sql=True)
    sql = output.getvalue()

    assert "LOCK TABLE stocktake_tasks, stocktake_items IN ACCESS EXCLUSIVE MODE" in sql
    assert "legacy_v09_stocktake_tasks_pkey" in sql
    assert "CREATE TABLE inventory_opening_establishments" in sql
    assert "JSONB" in sql
    assert "rsc_block_stocktake_fact_mutation_0010" in sql
    assert "uq_formal_stocktake_tasks_active_opening_region" in sql
