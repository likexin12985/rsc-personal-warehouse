from __future__ import annotations

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
REVISION_0010 = "20260830_0010"
REVISION_0011 = "20260830_0011"
REVISION_0012 = "20260831_0012"
REVISION_0013 = "20260831_0013"
REVISION_0014 = "20260831_0014"
REVISION_0015 = "20260831_0015"
REVISION_0016 = "20260831_0016"
NOW = "2026-08-30 00:00:00+00:00"
LATER = "2026-08-30 00:00:01+00:00"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
TECHNICIAN_ROLE_ID = "10000000000040008000000000000003"
PROVINCIAL_MANAGER_ROLE_ID = "10000000000040008000000000000002"


def _uuid(ordinal: int) -> str:
    return f"{ordinal:032x}"


def _config(database_url: str, *, output_buffer=None) -> Config:
    config = Config(str(ALEMBIC_INI), output_buffer=output_buffer)
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _upgrade_database(tmp_path: Path, name: str) -> tuple[str, sa.Engine]:
    database_url = f"sqlite+pysqlite:///{tmp_path / name}"
    command.upgrade(_config(database_url), REVISION_0011)
    return database_url, sa.create_engine(database_url)


def _insert_opening_fixture(
    connection: sa.Connection,
    *,
    tracking_mode: str = "none",
) -> dict[str, str]:
    facts = {
        "task": _uuid(1101),
        "round": _uuid(1102),
        "user": "00000000-0000-0000-0000-000000001103",
        "person": _uuid(1104),
        "assignment": _uuid(1105),
        "owner": _uuid(1106),
        "material": _uuid(1107),
        "policy": _uuid(1108),
        "account_cutoff": _uuid(1109),
        "account_late": _uuid(1110),
        "scope_observed": _uuid(1111),
        "scope_snapshot": _uuid(1112),
        "scope_zero": _uuid(1113),
        "location_observed": _uuid(1114),
        "location_snapshot": _uuid(1115),
        "location_zero": _uuid(1116),
        "snapshot": _uuid(1117),
        "count_line": _uuid(1118),
        "observation_pending": _uuid(1119),
        "observation_verified": _uuid(1120),
    }
    connection.exec_driver_sql(
        "INSERT INTO users (id, mobile, name, password_hash, role, province, "
        "is_active, require_password_change, created_at, updated_at, person_id, "
        "account_status, last_login_at, authorization_version) "
        "VALUES (?, '13800001103', 'counter', 'not-a-password', 'technician', "
        "NULL, 1, 0, ?, ?, ?, 'active', NULL, 1)",
        (facts["user"], NOW, NOW, facts["person"]),
    )
    connection.exec_driver_sql(
        "INSERT INTO role_assignments (id, user_id, role_id, scope_type, "
        "scope_id, valid_from, valid_to, status, assigned_by, updated_at, "
        "created_at, revoked_at, revoked_by, reason) VALUES "
        "(?, ?, ?, 'person', ?, ?, NULL, 'active', ?, ?, ?, NULL, NULL, '')",
        (
            facts["assignment"],
            facts["user"],
            TECHNICIAN_ROLE_ID,
            facts["person"],
            NOW,
            facts["user"],
            NOW,
            NOW,
        ),
    )
    connection.exec_driver_sql(
        "INSERT INTO material_inventory_policies "
        "(id, material_id, tracking_mode, quantity_scale, allow_fraction, "
        "effective_from, effective_to, updated_at, created_at) "
        "VALUES (?, ?, ?, 0, 0, ?, NULL, ?, ?)",
        (facts["policy"], facts["material"], tracking_mode, NOW, NOW, NOW),
    )
    connection.exec_driver_sql(
        "INSERT INTO stocktake_tasks "
        "(id, task_no, task_type, region_org_id, status, blind_count, "
        "cutoff_ledger_cursor, cutoff_at, scope_manifest_sha256, "
        "snapshot_manifest_sha256, control_source_system_id, "
        "control_sync_run_id, control_snapshot_at, control_manifest_sha256, "
        "current_round_no, created_by_user_id, deadline, issued_at, frozen_at, "
        "submitted_at, posted_at, closed_at, cancelled_at, version, note, "
        "created_at, updated_at) VALUES (?, 'OPEN-OBS-0011', 'opening', ?, "
        "'counting', 1, 0, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?, NULL, NULL, "
        "NULL, NULL, 1, '', ?, ?)",
        (
            facts["task"],
            facts["owner"],
            NOW,
            HASH_A,
            HASH_B,
            _uuid(1190),
            _uuid(1191),
            NOW,
            HASH_C,
            facts["user"],
            NOW,
            NOW,
            NOW,
            NOW,
        ),
    )
    scopes = (
        (facts["scope_observed"], 1, facts["location_observed"], "scope-observed", HASH_A),
        (facts["scope_snapshot"], 2, facts["location_snapshot"], "scope-snapshot", HASH_B),
        (facts["scope_zero"], 3, facts["location_zero"], "scope-zero", HASH_C),
    )
    for scope_id, scope_no, location_id, scope_key, scope_hash in scopes:
        connection.exec_driver_sql(
            "INSERT INTO stocktake_scopes "
            "(id, task_id, scope_no, scope_mode, location_id, owner_org_id, "
            "custodian_person_id_snapshot, assignee_user_id, material_id, "
            "condition_code, availability_bucket, scope_key, scope_sha256, "
            "created_at) VALUES (?, ?, ?, 'location_all', ?, ?, ?, ?, NULL, "
            "NULL, NULL, ?, ?, ?)",
            (
                scope_id,
                facts["task"],
                scope_no,
                location_id,
                facts["owner"],
                facts["person"],
                facts["user"],
                scope_key,
                scope_hash,
                NOW,
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO inventory_freezes "
            "(id, task_id, stocktake_scope_id, scope_key, freeze_mode, status, "
            "valid_from, valid_to, created_by_user_id, released_by_user_id, "
            "release_reason, version, created_at, updated_at) VALUES "
            "(?, ?, ?, ?, 'hard', 'active', ?, NULL, ?, NULL, '', 0, ?, ?)",
            (_uuid(1200 + scope_no), facts["task"], scope_id, scope_key, NOW,
             facts["user"], NOW, NOW),
        )
    for account_id, location_id, created_at in (
        (facts["account_cutoff"], facts["location_snapshot"], NOW),
        (facts["account_late"], facts["location_observed"], LATER),
    ):
        connection.exec_driver_sql(
            "INSERT INTO stock_accounts "
            "(id, owner_org_id, custodian_person_id, location_id, material_id, "
            "condition_code, availability_bucket, lot_id, updated_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'new', 'available', NULL, ?, ?)",
            (account_id, facts["owner"], facts["person"], location_id,
             facts["material"], created_at, created_at),
        )
    connection.exec_driver_sql(
        "INSERT INTO stocktake_snapshot_lines "
        "(id, task_id, scope_id, stock_account_id, book_qty, ledger_cursor, "
        "account_dimension_sha256, serial_snapshot_jsonb, "
        "serial_snapshot_sha256, serial_count, created_at) VALUES "
        "(?, ?, ?, ?, 0, 0, ?, '[]', ?, 0, ?)",
        (facts["snapshot"], facts["task"], facts["scope_snapshot"],
         facts["account_cutoff"], HASH_A, HASH_B, NOW),
    )
    connection.exec_driver_sql(
        "INSERT INTO stocktake_rounds "
        "(id, task_id, round_no, round_type, status, submitted_by_user_id, "
        "started_at, submitted_at, count_manifest_sha256, idempotency_key_hash, "
        "created_at, updated_at) VALUES (?, ?, 1, 'initial', 'counting', NULL, "
        "?, NULL, NULL, ?, ?, ?)",
        (facts["round"], facts["task"], NOW, HASH_A, NOW, NOW),
    )
    return facts


def _insert_observation(
    connection: sa.Connection,
    facts: dict[str, str],
    *,
    observation_id: str,
    observation_no: int,
    material_id: str | None,
    raw: str,
    status: str,
    quantity: int,
    dimension_hash: str,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stocktake_count_observations "
        "(id, task_id, round_id, scope_id, observation_no, owner_org_id, "
        "location_id, custodian_person_id_snapshot, material_id, "
        "material_identifier_raw, material_identifier_type, condition_code, "
        "availability_bucket, lot_id, lot_no_raw, serial_id, serial_no_raw, "
        "serial_identifier_type, counted_qty, verification_status, count_method, "
        "reason_code, remark, counted_by_user_id, counted_at, dimension_sha256, "
        "request_sha256, idempotency_key_hash, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 'new', 'available', NULL, "
        "NULL, NULL, NULL, NULL, ?, ?, 'manual', NULL, '', ?, ?, ?, ?, ?, ?)",
        (
            observation_id,
            facts["task"],
            facts["round"],
            facts["scope_observed"],
            observation_no,
            facts["owner"],
            facts["location_observed"],
            facts["person"],
            material_id,
            raw,
            quantity,
            status,
            facts["user"],
            NOW,
            dimension_hash,
            HASH_B,
            dimension_hash,
            NOW,
        ),
    )


def _insert_resolved_serial_observation(
    connection: sa.Connection,
    facts: dict[str, str],
    *,
    observation_id: str,
    observation_no: int,
    serial_id: str,
    serial_no: str,
    serial_identifier_type: str,
    dimension_hash: str,
    idempotency_hash: str,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stocktake_count_observations "
        "(id, task_id, round_id, scope_id, observation_no, owner_org_id, "
        "location_id, custodian_person_id_snapshot, material_id, "
        "material_identifier_raw, material_identifier_type, condition_code, "
        "availability_bucket, lot_id, lot_no_raw, serial_id, serial_no_raw, "
        "serial_identifier_type, counted_qty, verification_status, count_method, "
        "reason_code, remark, counted_by_user_id, counted_at, dimension_sha256, "
        "request_sha256, idempotency_key_hash, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, 'SKU-0012-SERIAL', 'sku_code', 'new', "
        "'available', NULL, NULL, ?, ?, ?, 1, 'verified', 'scan', NULL, '', "
        "?, ?, ?, ?, ?, ?)",
        (
            observation_id,
            facts["task"],
            facts["round"],
            facts["scope_observed"],
            observation_no,
            facts["owner"],
            facts["location_observed"],
            facts["person"],
            facts["material"],
            serial_id,
            serial_no,
            serial_identifier_type,
            facts["user"],
            NOW,
            dimension_hash,
            HASH_B,
            idempotency_hash,
            NOW,
        ),
    )


def _insert_completion(
    connection: sa.Connection,
    facts: dict[str, str],
    *,
    completion_id: str,
    scope_id: str,
    count_lines: int,
    observations: int,
    total: int,
    zero: bool,
    key_hash: str,
    serials: int = 0,
    completed_at: str = NOW,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stocktake_scope_count_completions "
        "(id, task_id, round_id, scope_id, count_line_count, "
        "observation_line_count, serial_count, total_counted_qty, "
        "zero_confirmed, evidence_manifest_sha256, request_sha256, "
        "idempotency_key_hash, completed_by_user_id, completed_by_person_id, "
        "completed_role_assignment_id, authorization_version, role_code, "
        "scope_type, scope_id_snapshot, authorization_sha256, completed_at, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, "
        "'technician', 'person', ?, ?, ?, ?)",
        (
            completion_id,
            facts["task"],
            facts["round"],
            scope_id,
            count_lines,
            observations,
            serials,
            total,
            zero,
            HASH_A,
            HASH_B,
            key_hash,
            facts["user"],
            facts["person"],
            facts["assignment"],
            facts["person"],
            HASH_C,
            completed_at,
            completed_at,
        ),
    )


def _insert_legacy_round_submission_for_0014(
    connection: sa.Connection,
    *,
    candidate_count: int,
) -> tuple[dict[str, str], tuple[str, str, str]]:
    facts = _insert_opening_fixture(connection)
    connection.exec_driver_sql(
        "INSERT INTO stocktake_count_lines "
        "(id, task_id, round_id, scope_id, stock_account_id, counted_qty, "
        "count_method, reason_code, remark, counted_by_user_id, counted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 'manual', NULL, "
        "'', ?, ?, ?, ?)",
        (
            facts["count_line"],
            facts["task"],
            facts["round"],
            facts["scope_snapshot"],
            facts["account_cutoff"],
            facts["user"],
            NOW,
            NOW,
            NOW,
        ),
    )
    completion_ids = (_uuid(1301), _uuid(1302), _uuid(1303))
    completion_times = {
        0: (NOW, NOW, NOW),
        1: (NOW, NOW, LATER),
        2: (NOW, LATER, LATER),
    }[candidate_count]
    _insert_completion(
        connection,
        facts,
        completion_id=completion_ids[0],
        scope_id=facts["scope_observed"],
        count_lines=0,
        observations=0,
        total=0,
        zero=True,
        key_hash="3" * 64,
        completed_at=completion_times[0],
    )
    _insert_completion(
        connection,
        facts,
        completion_id=completion_ids[1],
        scope_id=facts["scope_snapshot"],
        count_lines=1,
        observations=0,
        total=0,
        zero=False,
        key_hash="4" * 64,
        completed_at=completion_times[1],
    )
    _insert_completion(
        connection,
        facts,
        completion_id=completion_ids[2],
        scope_id=facts["scope_zero"],
        count_lines=0,
        observations=0,
        total=0,
        zero=True,
        key_hash="5" * 64,
        completed_at=completion_times[2],
    )
    connection.exec_driver_sql(
        "INSERT INTO stocktake_round_submissions "
        "(id, task_id, round_id, scope_count, zero_scope_count, "
        "count_line_count, observation_line_count, serial_count, "
        "total_counted_qty, round_manifest_sha256, request_sha256, "
        "idempotency_key_hash, submitted_by_user_id, submitted_by_person_id, "
        "submitted_role_assignment_id, authorization_version, submitted_at, "
        "created_at) VALUES (?, ?, ?, 3, 2, 1, 0, 0, 0, ?, ?, ?, ?, ?, ?, "
        "1, ?, ?)",
        (
            _uuid(1304),
            facts["task"],
            facts["round"],
            HASH_A,
            HASH_B,
            "6" * 64,
            facts["user"],
            facts["person"],
            facts["assignment"],
            LATER,
            LATER,
        ),
    )
    return facts, completion_ids


def test_0011_schema_contract_and_sqlite_round_trip(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url, engine = _upgrade_database(tmp_path, "schema.db")
    try:
        inspector = inspect(engine)
        assert {
            "stocktake_count_observations",
            "stocktake_scope_count_completions",
            "stocktake_round_submissions",
        } <= set(inspector.get_table_names())
        observation_columns = {
            column["name"]: column
            for column in inspector.get_columns("stocktake_count_observations")
        }
        assert observation_columns["counted_qty"]["type"].precision == 18
        assert observation_columns["counted_qty"]["type"].scale == 3
        assert {
            "material_identifier_type",
            "material_identifier_raw",
            "lot_no_raw",
            "serial_identifier_type",
            "serial_no_raw",
            "verification_status",
        } <= observation_columns.keys()
        assert "uq_stocktake_count_observations_round_serial_raw" in {
            index["name"]
            for index in inspector.get_indexes("stocktake_count_observations")
        }
        difference_columns = {
            column["name"]
            for column in inspector.get_columns("stocktake_differences")
        }
        assert "observed_line_id" in difference_columns
        assert "ix_stocktake_differences_observed_line" in {
            index["name"]
            for index in inspector.get_indexes("stocktake_differences")
        }
        observed_fks = {
            tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys("stocktake_differences")
            if foreign_key["referred_table"] == "stocktake_count_observations"
        }
        assert (
            "observed_line_id",
            "task_id",
            "round_id",
            "scope_id",
        ) in observed_fks
        trigger_names = {
            row[0]
            for row in engine.connect().exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert {
            "trg_stocktake_count_observations_validate_insert_0011",
            "trg_stocktake_count_observations_immutable_update_0011",
            "trg_stocktake_count_lines_immutable_update_0011",
            "trg_stocktake_count_serials_immutable_delete_0011",
            "trg_stocktake_scope_count_completions_validate_insert_0011",
            "trg_stocktake_round_submissions_validate_insert_0011",
            "trg_stocktake_rounds_submission_manifest_insert_0011",
            "trg_stocktake_rounds_submission_manifest_update_0011",
            "trg_stocktake_differences_observed_line_insert_0011",
            "trg_stocktake_posting_items_validate_insert_0010",
        } <= trigger_names
    finally:
        engine.dispose()

    command.downgrade(_config(database_url), REVISION_0010)
    downgraded = sa.create_engine(database_url)
    try:
        inspector = inspect(downgraded)
        assert not ({
            "stocktake_count_observations",
            "stocktake_scope_count_completions",
            "stocktake_round_submissions",
        } & set(inspector.get_table_names()))
        assert "observed_line_id" not in {
            column["name"]
            for column in inspector.get_columns("stocktake_differences")
        }
        trigger_names = {
            row[0]
            for row in downgraded.connect().exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert "trg_stocktake_differences_chronology_insert_0010" in trigger_names
        assert "trg_stocktake_posting_items_validate_insert_0010" in trigger_names
    finally:
        downgraded.dispose()


def test_0012_sqlite_partial_unique_index_round_trip(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'resolved-serial-index.db'}"
    config = _config(database_url)

    command.upgrade(config, REVISION_0012)
    command.upgrade(config, REVISION_0012)
    engine = sa.create_engine(database_url)
    try:
        indexes = {
            index["name"]: index
            for index in inspect(engine).get_indexes(
                "stocktake_count_observations"
            )
        }
        resolved_serial_index = indexes[
            "uq_stocktake_count_observations_round_serial_id"
        ]
        assert tuple(resolved_serial_index["column_names"]) == (
            "round_id",
            "serial_id",
        )
        assert bool(resolved_serial_index["unique"]) is True
    finally:
        engine.dispose()

    command.downgrade(config, REVISION_0011)
    downgraded = sa.create_engine(database_url)
    try:
        assert "uq_stocktake_count_observations_round_serial_id" not in {
            index["name"]
            for index in inspect(downgraded).get_indexes(
                "stocktake_count_observations"
            )
        }
        assert downgraded.connect().exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0011
    finally:
        downgraded.dispose()


def test_0012_sqlite_upgrade_preflight_rejects_duplicate_resolved_serials(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'duplicate-resolved.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0011)
    engine = sa.create_engine(database_url)
    serial_id = _uuid(1501)
    serial_no = "SN-0012-DUPLICATE-SENTINEL"
    try:
        with engine.begin() as connection:
            facts = _insert_opening_fixture(connection, tracking_mode="serial")
            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, "
                "lifecycle_status, created_at, updated_at) VALUES "
                "(?, ?, ?, 'QR-0012-DUPLICATE', NULL, 'active', ?, ?)",
                (serial_id, facts["material"], serial_no, NOW, NOW),
            )
            _insert_resolved_serial_observation(
                connection,
                facts,
                observation_id=_uuid(1502),
                observation_no=1,
                serial_id=serial_id,
                serial_no=serial_no,
                serial_identifier_type="serial_no",
                dimension_hash="4" * 64,
                idempotency_hash="5" * 64,
            )
            # 0011 keyed raw uniqueness by identifier type, so the same
            # resolved serial could previously be recorded again as unknown.
            _insert_resolved_serial_observation(
                connection,
                facts,
                observation_id=_uuid(1503),
                observation_no=2,
                serial_id=serial_id,
                serial_no=serial_no,
                serial_identifier_type="unknown",
                dimension_hash="6" * 64,
                idempotency_hash="7" * 64,
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError) as exc_info:
        command.upgrade(config, REVISION_0012)
    assert str(exc_info.value) == (
        "0012 preflight failed: duplicate resolved stocktake serial "
        "observations must be reviewed before migration"
    )
    assert serial_no not in str(exc_info.value)
    assert serial_id not in str(exc_info.value)

    verification = sa.create_engine(database_url)
    try:
        assert verification.connect().exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0011
        assert "uq_stocktake_count_observations_round_serial_id" not in {
            index["name"]
            for index in inspect(verification).get_indexes(
                "stocktake_count_observations"
            )
        }
    finally:
        verification.dispose()


def test_0012_sqlite_index_blocks_duplicate_and_used_guard_blocks_downgrade(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'guarded-resolved.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0011)
    engine = sa.create_engine(database_url)
    serial_id = _uuid(1511)
    serial_no = "SN-0012-GUARDED"
    try:
        with engine.begin() as connection:
            facts = _insert_opening_fixture(connection, tracking_mode="serial")
            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, "
                "lifecycle_status, created_at, updated_at) VALUES "
                "(?, ?, ?, 'QR-0012-GUARDED', NULL, 'active', ?, ?)",
                (serial_id, facts["material"], serial_no, NOW, NOW),
            )
            _insert_resolved_serial_observation(
                connection,
                facts,
                observation_id=_uuid(1512),
                observation_no=1,
                serial_id=serial_id,
                serial_no=serial_no,
                serial_identifier_type="serial_no",
                dimension_hash="8" * 64,
                idempotency_hash="9" * 64,
            )
    finally:
        engine.dispose()

    command.upgrade(config, REVISION_0012)
    guarded = sa.create_engine(database_url)
    try:
        with guarded.begin() as connection:
            with pytest.raises(sa.exc.IntegrityError, match="round_id"):
                _insert_resolved_serial_observation(
                    connection,
                    facts,
                    observation_id=_uuid(1513),
                    observation_no=2,
                    serial_id=serial_id,
                    serial_no=serial_no,
                    serial_identifier_type="unknown",
                    dimension_hash="b" * 64,
                    idempotency_hash="c" * 64,
                )
    finally:
        guarded.dispose()

    with pytest.raises(RuntimeError) as exc_info:
        command.downgrade(config, REVISION_0011)
    assert str(exc_info.value) == (
        "cannot downgrade 0012: resolved stocktake serial observations "
        "require the round serial uniqueness guard"
    )
    verification = sa.create_engine(database_url)
    try:
        assert verification.connect().exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0012
        assert "uq_stocktake_count_observations_round_serial_id" in {
            index["name"]
            for index in inspect(verification).get_indexes(
                "stocktake_count_observations"
            )
        }
    finally:
        verification.dispose()


def test_0013_sqlite_accepts_exact_active_serial_qr_and_blocks_downgrade(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'identifier-aware-qr.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0013)
    engine = sa.create_engine(database_url)
    serial_id = _uuid(1521)
    serial_id_by_no = _uuid(1524)
    qr_code = "QR-0013-EXACT-ACTIVE"
    try:
        with engine.begin() as connection:
            facts = _insert_opening_fixture(connection, tracking_mode="serial")
            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, "
                "lifecycle_status, created_at, updated_at) VALUES "
                "(?, ?, 'SN-0013-EXACT-ACTIVE', ?, NULL, 'active', ?, ?)",
                (serial_id, facts["material"], qr_code, NOW, NOW),
            )
            connection.exec_driver_sql(
                "INSERT INTO qr_codes "
                "(id, code, object_type, object_id, status, printed_at, "
                "created_at, updated_at) VALUES "
                "(?, ?, 'serial', ?, 'active', NULL, ?, ?)",
                (_uuid(1522), qr_code, serial_id, NOW, NOW),
            )
            _insert_resolved_serial_observation(
                connection,
                facts,
                observation_id=_uuid(1523),
                observation_no=1,
                serial_id=serial_id,
                serial_no=qr_code,
                serial_identifier_type="qr_code",
                dimension_hash="d" * 64,
                idempotency_hash="e" * 64,
            )
            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, "
                "lifecycle_status, created_at, updated_at) VALUES "
                "(?, ?, 'SN-0013-BY-NUMBER', 'QR-0013-BY-NUMBER', NULL, "
                "'active', ?, ?)",
                (serial_id_by_no, facts["material"], NOW, NOW),
            )
            _insert_resolved_serial_observation(
                connection,
                facts,
                observation_id=_uuid(1525),
                observation_no=2,
                serial_id=serial_id_by_no,
                serial_no="SN-0013-BY-NUMBER",
                serial_identifier_type="serial_no",
                dimension_hash="f" * 64,
                idempotency_hash="0" * 64,
            )
            assert connection.exec_driver_sql(
                "SELECT serial_identifier_type, serial_no_raw, serial_id "
                "FROM stocktake_count_observations ORDER BY observation_no"
            ).all() == [
                ("qr_code", qr_code, serial_id),
                ("serial_no", "SN-0013-BY-NUMBER", serial_id_by_no),
            ]
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError) as exc_info:
        command.downgrade(config, REVISION_0012)
    assert str(exc_info.value) == (
        "cannot downgrade 0013: resolved stocktake serial observations require "
        "identifier-aware mapping guards"
    )
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0013
    finally:
        verification.dispose()


@pytest.mark.parametrize(
    ("case", "raw", "identifier_type"),
    (
        ("qr_mapping_conflict", "QR-0013-MAPPING-CONFLICT", "qr_code"),
        ("serial_no_mismatch", "SN-0013-WRONG", "serial_no"),
        ("unknown_with_serial_id", "SN-0013-CORRECT", "unknown"),
    ),
)
def test_0013_sqlite_rejects_unproven_resolved_serial_identifiers(
    tmp_path: Path,
    monkeypatch,
    case: str,
    raw: str,
    identifier_type: str,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / f'identifier-{case}.db'}"
    command.upgrade(_config(database_url), REVISION_0013)
    engine = sa.create_engine(database_url)
    serial_id = _uuid(1531)
    try:
        with engine.begin() as connection:
            facts = _insert_opening_fixture(connection, tracking_mode="serial")
            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, "
                "lifecycle_status, created_at, updated_at) VALUES "
                "(?, ?, 'SN-0013-CORRECT', 'QR-0013-MAPPING-CONFLICT', NULL, "
                "'active', ?, ?)",
                (serial_id, facts["material"], NOW, NOW),
            )
            if case == "qr_mapping_conflict":
                connection.exec_driver_sql(
                    "INSERT INTO qr_codes "
                    "(id, code, object_type, object_id, status, printed_at, "
                    "created_at, updated_at) VALUES "
                    "(?, ?, 'serial', ?, 'active', NULL, ?, ?)",
                    (_uuid(1532), raw, _uuid(1539), NOW, NOW),
                )
            with pytest.raises(sa.exc.IntegrityError, match="observation is invalid"):
                _insert_resolved_serial_observation(
                    connection,
                    facts,
                    observation_id=_uuid(1533),
                    observation_no=1,
                    serial_id=serial_id,
                    serial_no=raw,
                    serial_identifier_type=identifier_type,
                    dimension_hash="1" * 64,
                    idempotency_hash="2" * 64,
                )
    finally:
        engine.dispose()


def test_0013_sqlite_upgrade_preflight_rejects_legacy_unknown_serial_binding(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'identifier-preflight.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0012)
    engine = sa.create_engine(database_url)
    serial_id = _uuid(1541)
    serial_no = "SN-0013-PREFLIGHT-SENTINEL"
    try:
        with engine.begin() as connection:
            facts = _insert_opening_fixture(connection, tracking_mode="serial")
            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, "
                "lifecycle_status, created_at, updated_at) VALUES "
                "(?, ?, ?, 'QR-0013-PREFLIGHT', NULL, 'active', ?, ?)",
                (serial_id, facts["material"], serial_no, NOW, NOW),
            )
            _insert_resolved_serial_observation(
                connection,
                facts,
                observation_id=_uuid(1542),
                observation_no=1,
                serial_id=serial_id,
                serial_no=serial_no,
                serial_identifier_type="unknown",
                dimension_hash="3" * 64,
                idempotency_hash="4" * 64,
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError) as exc_info:
        command.upgrade(config, REVISION_0013)
    assert str(exc_info.value) == (
        "0013 preflight failed: existing resolved stocktake serial observations "
        "violate identifier mapping rules"
    )
    assert serial_no not in str(exc_info.value)
    assert serial_id not in str(exc_info.value)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0012
    finally:
        verification.dispose()


def test_0013_sqlite_empty_round_trip_restores_0011_observation_guard(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'identifier-empty-roundtrip.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0013)
    command.downgrade(config, REVISION_0012)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            trigger_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0012
        assert "trg_stocktake_count_observations_validate_insert_0011" in trigger_names
        assert "trg_stocktake_count_observations_validate_insert_0013" not in trigger_names
    finally:
        engine.dispose()


def test_0014_sqlite_backfills_the_one_exact_sealing_completion_and_keeps_seal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0014-history.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0013)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _facts, completion_ids = _insert_legacy_round_submission_for_0014(
                connection,
                candidate_count=1,
            )
    finally:
        engine.dispose()

    command.upgrade(config, REVISION_0014)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT sealing_completion_id FROM stocktake_round_submissions"
            ).scalar_one() == completion_ids[2]
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0014
        with pytest.raises(sa.exc.DatabaseError, match="immutable"):
            with verification.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE stocktake_round_submissions SET scope_count = 2"
                )
        with pytest.raises(sa.exc.DatabaseError, match="immutable"):
            with verification.begin() as connection:
                connection.exec_driver_sql("DELETE FROM stocktake_round_submissions")
    finally:
        verification.dispose()


@pytest.mark.parametrize("candidate_count", [0, 2])
def test_0014_sqlite_ambiguous_history_fails_before_any_schema_change(
    tmp_path: Path,
    monkeypatch,
    candidate_count: int,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = (
        f"sqlite+pysqlite:///{tmp_path / f'0014-invalid-{candidate_count}.db'}"
    )
    config = _config(database_url)
    command.upgrade(config, REVISION_0013)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_legacy_round_submission_for_0014(
                connection,
                candidate_count=candidate_count,
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="missing or ambiguous"):
        command.upgrade(config, REVISION_0014)

    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0013
        assert "sealing_completion_id" not in {
            row["name"]
            for row in inspect(verification).get_columns(
                "stocktake_round_submissions"
            )
        }
    finally:
        verification.dispose()


def test_0014_sqlite_resumes_a_partial_nontransactional_upgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0014-partial-retry.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0013)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _facts, completion_ids = _insert_legacy_round_submission_for_0014(
                connection,
                candidate_count=1,
            )
            connection.exec_driver_sql(
                "ALTER TABLE stocktake_round_submissions "
                "ADD COLUMN sealing_completion_id CHAR(32)"
            )
            connection.exec_driver_sql(
                "DROP TRIGGER "
                "trg_stocktake_round_submissions_immutable_update_0011"
            )
            connection.exec_driver_sql(
                "UPDATE stocktake_round_submissions "
                "SET sealing_completion_id = ?",
                (completion_ids[2],),
            )
    finally:
        engine.dispose()

    command.upgrade(config, REVISION_0014)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0014
            assert connection.exec_driver_sql(
                "SELECT sealing_completion_id FROM stocktake_round_submissions"
            ).scalar_one() == completion_ids[2]
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'stocktake_round_submissions'"
                ).all()
            }
        assert {
            "trg_stocktake_round_submissions_immutable_update_0011",
            "trg_stocktake_round_submissions_immutable_delete_0011",
            "trg_stocktake_round_submissions_sealing_completion_0014",
        } <= triggers
    finally:
        verification.dispose()


def test_0013_sqlite_resumes_partial_trigger_replacement_in_both_directions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0013-partial-trigger.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0012)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER "
                "trg_stocktake_count_observations_validate_insert_0011"
            )
    finally:
        engine.dispose()

    command.upgrade(config, REVISION_0013)
    upgraded = sa.create_engine(database_url)
    try:
        with upgraded.begin() as connection:
            trigger_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'stocktake_count_observations'"
                ).all()
            }
            assert (
                "trg_stocktake_count_observations_validate_insert_0013"
                in trigger_names
            )
            connection.exec_driver_sql(
                "DROP TRIGGER "
                "trg_stocktake_count_observations_validate_insert_0013"
            )
    finally:
        upgraded.dispose()

    command.downgrade(config, REVISION_0012)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            trigger_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'stocktake_count_observations'"
                ).all()
            }
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0012
            assert (
                "trg_stocktake_count_observations_validate_insert_0011"
                in trigger_names
            )
    finally:
        verification.dispose()


def test_0014_sqlite_resumes_partial_empty_downgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0014-partial-down.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0014)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER "
                "trg_stocktake_round_submissions_sealing_completion_0014"
            )
            connection.exec_driver_sql(
                "DROP INDEX uq_stocktake_round_submissions_sealing_completion"
            )
    finally:
        engine.dispose()

    command.downgrade(config, REVISION_0013)
    verification = sa.create_engine(database_url)
    try:
        assert "sealing_completion_id" not in {
            row["name"]
            for row in inspect(verification).get_columns(
                "stocktake_round_submissions"
            )
        }
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0013
    finally:
        verification.dispose()


def test_0011_observation_completion_submission_and_difference_seals(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _database_url, engine = _upgrade_database(tmp_path, "facts.db")
    try:
        with engine.begin() as connection:
            facts = _insert_opening_fixture(connection)
            _insert_observation(
                connection,
                facts,
                observation_id=facts["observation_pending"],
                observation_no=1,
                material_id=None,
                raw="unresolved-code-from-site",
                status="pending_verification",
                quantity=2,
                dimension_hash=HASH_A,
            )
            # An exact account that first appeared after cutoff does not turn
            # the physical observation into an account-bound count line.
            _insert_observation(
                connection,
                facts,
                observation_id=facts["observation_verified"],
                observation_no=2,
                material_id=facts["material"],
                raw="SKU-VERIFIED",
                status="verified",
                quantity=1,
                dimension_hash=HASH_B,
            )
            # The exact dimension already present at cutoff must instead use
            # the snapshot account count line.
            with pytest.raises(sa.exc.DatabaseError, match="not unexpected"):
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_count_observations "
                    "(id, task_id, round_id, scope_id, observation_no, "
                    "owner_org_id, location_id, custodian_person_id_snapshot, "
                    "material_id, material_identifier_raw, "
                    "material_identifier_type, condition_code, "
                    "availability_bucket, lot_id, lot_no_raw, serial_id, "
                    "serial_no_raw, serial_identifier_type, counted_qty, "
                    "verification_status, count_method, reason_code, remark, "
                    "counted_by_user_id, counted_at, dimension_sha256, "
                    "request_sha256, idempotency_key_hash, created_at) VALUES "
                    "(?, ?, ?, ?, 3, ?, ?, ?, ?, 'SKU-CUTOFF', 'sku_code', "
                    "'new', 'available', NULL, NULL, NULL, NULL, NULL, 1, "
                    "'verified', 'manual', NULL, '', ?, ?, ?, ?, ?, ?)",
                    (
                        _uuid(1121), facts["task"], facts["round"],
                        facts["scope_snapshot"], facts["owner"],
                        facts["location_snapshot"], facts["person"],
                        facts["material"], facts["user"], NOW, HASH_C,
                        HASH_A, HASH_C, NOW,
                    ),
                )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_count_lines "
                "(id, task_id, round_id, scope_id, stock_account_id, "
                "counted_qty, count_method, reason_code, remark, "
                "counted_by_user_id, counted_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 'manual', NULL, '', ?, ?, ?, ?)",
                (facts["count_line"], facts["task"], facts["round"],
                 facts["scope_snapshot"], facts["account_cutoff"],
                 facts["user"], NOW, NOW, NOW),
            )
            with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                connection.exec_driver_sql(
                    "UPDATE stocktake_count_lines SET remark = 'rewrite' WHERE id = ?",
                    (facts["count_line"],),
                )
            with pytest.raises(sa.exc.IntegrityError):
                _insert_completion(
                    connection,
                    facts,
                    completion_id=_uuid(1122),
                    scope_id=facts["scope_zero"],
                    count_lines=0,
                    observations=0,
                    total=0,
                    zero=False,
                    key_hash="d" * 64,
                )
            _insert_completion(
                connection, facts, completion_id=_uuid(1123),
                scope_id=facts["scope_observed"], count_lines=0,
                observations=2, total=3, zero=False, key_hash="e" * 64,
            )
            _insert_completion(
                connection, facts, completion_id=_uuid(1124),
                scope_id=facts["scope_snapshot"], count_lines=1,
                observations=0, total=0, zero=False, key_hash="f" * 64,
            )
            _insert_completion(
                connection, facts, completion_id=_uuid(1125),
                scope_id=facts["scope_zero"], count_lines=0,
                observations=0, total=0, zero=True, key_hash="0" * 64,
            )
            with pytest.raises(sa.exc.DatabaseError, match="sealed"):
                _insert_observation(
                    connection, facts, observation_id=_uuid(1126),
                    observation_no=4, material_id=None, raw="late",
                    status="pending_verification", quantity=1,
                    dimension_hash="1" * 64,
                )
            with pytest.raises(sa.exc.DatabaseError, match="manifest"):
                connection.exec_driver_sql(
                    "UPDATE stocktake_rounds SET status = 'submitted', "
                    "submitted_by_user_id = ?, submitted_at = ?, "
                    "count_manifest_sha256 = ?, updated_at = ? WHERE id = ?",
                    (facts["user"], LATER, HASH_C, LATER, facts["round"]),
                )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_round_submissions "
                "(id, task_id, round_id, scope_count, zero_scope_count, "
                "count_line_count, observation_line_count, serial_count, "
                "total_counted_qty, round_manifest_sha256, request_sha256, "
                "idempotency_key_hash, submitted_by_user_id, "
                "submitted_by_person_id, submitted_role_assignment_id, "
                "authorization_version, submitted_at, created_at) VALUES "
                "(?, ?, ?, 3, 1, 1, 2, 0, 3, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (_uuid(1127), facts["task"], facts["round"], HASH_C, HASH_B,
                 "2" * 64, facts["user"], facts["person"],
                 facts["assignment"], LATER, LATER),
            )
            connection.exec_driver_sql(
                "UPDATE stocktake_rounds SET status = 'submitted', "
                "submitted_by_user_id = ?, submitted_at = ?, "
                "count_manifest_sha256 = ?, updated_at = ? WHERE id = ?",
                (facts["user"], LATER, HASH_C, LATER, facts["round"]),
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_differences "
                "(id, task_id, round_id, scope_id, control_snapshot_line_id, "
                "difference_no, difference_type, material_id, "
                "expected_account_id, observed_account_id, observed_line_id, "
                "serial_id, book_qty, counted_qty, difference_qty, affected_qty, "
                "reason_code, reason_text, evidence_required, created_at) VALUES "
                "(?, ?, ?, ?, NULL, 1, 'excess', NULL, NULL, NULL, ?, NULL, "
                "0, 2, 2, 2, NULL, 'pending material verification', 1, ?)",
                (_uuid(1128), facts["task"], facts["round"],
                 facts["scope_observed"], facts["observation_pending"], LATER),
            )
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM stock_accounts"
            ).scalar_one() == 2
            assert connection.exec_driver_sql(
                "SELECT verification_status FROM stocktake_count_observations "
                "WHERE id = ?", (facts["observation_pending"],)
            ).scalar_one() == "pending_verification"
            with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                connection.exec_driver_sql(
                    "UPDATE stocktake_scope_count_completions "
                    "SET total_counted_qty = 99 WHERE id = ?", (_uuid(1123),)
                )
    finally:
        engine.dispose()


def test_0011_completion_enforces_cutoff_policy_and_serial_identity(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _database_url, engine = _upgrade_database(tmp_path, "serial-contract.db")
    try:
        with engine.begin() as connection:
            facts = _insert_opening_fixture(connection)
            with pytest.raises(sa.exc.DatabaseError, match="manifest"):
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_rounds "
                    "(id, task_id, round_no, round_type, status, "
                    "submitted_by_user_id, started_at, submitted_at, "
                    "count_manifest_sha256, idempotency_key_hash, created_at, "
                    "updated_at) VALUES (?, ?, 2, 'recount', 'submitted', ?, ?, "
                    "?, ?, ?, ?, ?)",
                    (_uuid(1401), facts["task"], facts["user"], NOW, LATER,
                     HASH_C, "3" * 64, NOW, LATER),
                )

            scale_savepoint = connection.begin_nested()
            connection.exec_driver_sql(
                "INSERT INTO stocktake_count_lines "
                "(id, task_id, round_id, scope_id, stock_account_id, "
                "counted_qty, count_method, reason_code, remark, "
                "counted_by_user_id, counted_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0.5, 'manual', NULL, '', ?, ?, ?, ?)",
                (_uuid(1402), facts["task"], facts["round"],
                 facts["scope_snapshot"], facts["account_cutoff"],
                 facts["user"], NOW, NOW, NOW),
            )
            with pytest.raises(sa.exc.DatabaseError, match="canonical"):
                _insert_completion(
                    connection, facts, completion_id=_uuid(1403),
                    scope_id=facts["scope_snapshot"], count_lines=1,
                    observations=0, total=0.5, zero=False,
                    key_hash="4" * 64,
                )
            scale_savepoint.rollback()

            connection.exec_driver_sql(
                "UPDATE material_inventory_policies SET tracking_mode = 'serial', "
                "quantity_scale = 0, allow_fraction = 0 WHERE id = ?",
                (facts["policy"],),
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_count_lines "
                "(id, task_id, round_id, scope_id, stock_account_id, "
                "counted_qty, count_method, reason_code, remark, "
                "counted_by_user_id, counted_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, 'scan', NULL, '', ?, ?, ?, ?)",
                (facts["count_line"], facts["task"], facts["round"],
                 facts["scope_snapshot"], facts["account_cutoff"],
                 facts["user"], NOW, NOW, NOW),
            )
            with pytest.raises(sa.exc.DatabaseError, match="canonical"):
                _insert_completion(
                    connection, facts, completion_id=_uuid(1404),
                    scope_id=facts["scope_snapshot"], count_lines=1,
                    observations=0, total=1, zero=False,
                    key_hash="5" * 64, serials=0,
                )

            wrong_binding = connection.begin_nested()
            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, lifecycle_status, "
                "created_at, updated_at) VALUES (?, ?, 'SN-WRONG', 'QR-WRONG', "
                "NULL, 'active', ?, ?)",
                (_uuid(1405), _uuid(1499), NOW, NOW),
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_count_serials "
                "(count_line_id, round_id, serial_id, result, created_at) "
                "VALUES (?, ?, ?, 'present', ?)",
                (facts["count_line"], facts["round"], _uuid(1405), NOW),
            )
            with pytest.raises(sa.exc.DatabaseError, match="canonical"):
                _insert_completion(
                    connection, facts, completion_id=_uuid(1406),
                    scope_id=facts["scope_snapshot"], count_lines=1,
                    observations=0, total=1, zero=False,
                    key_hash="6" * 64, serials=1,
                )
            wrong_binding.rollback()

            missing_is_not_physical = connection.begin_nested()
            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, lifecycle_status, "
                "created_at, updated_at) VALUES (?, ?, 'SN-MISSING', "
                "'QR-MISSING', NULL, 'active', ?, ?)",
                (_uuid(1407), facts["material"], NOW, NOW),
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_count_serials "
                "(count_line_id, round_id, serial_id, result, created_at) "
                "VALUES (?, ?, ?, 'missing', ?)",
                (facts["count_line"], facts["round"], _uuid(1407), NOW),
            )
            with pytest.raises(sa.exc.DatabaseError, match="canonical"):
                _insert_completion(
                    connection, facts, completion_id=_uuid(1408),
                    scope_id=facts["scope_snapshot"], count_lines=1,
                    observations=0, total=1, zero=False,
                    key_hash="7" * 64, serials=1,
                )
            missing_is_not_physical.rollback()

            connection.exec_driver_sql(
                "INSERT INTO inventory_serials "
                "(id, material_id, serial_no, qr_code, lot_id, lifecycle_status, "
                "created_at, updated_at) VALUES (?, ?, 'SN-PRESENT', "
                "'QR-PRESENT', NULL, 'active', ?, ?)",
                (_uuid(1409), facts["material"], NOW, NOW),
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_count_serials "
                "(count_line_id, round_id, serial_id, result, created_at) "
                "VALUES (?, ?, ?, 'present', ?)",
                (facts["count_line"], facts["round"], _uuid(1409), NOW),
            )

            duplicate_evidence = connection.begin_nested()
            connection.exec_driver_sql(
                "INSERT INTO stocktake_count_observations "
                "(id, task_id, round_id, scope_id, observation_no, owner_org_id, "
                "location_id, custodian_person_id_snapshot, material_id, "
                "material_identifier_raw, material_identifier_type, "
                "condition_code, availability_bucket, lot_id, lot_no_raw, "
                "serial_id, serial_no_raw, serial_identifier_type, counted_qty, "
                "verification_status, count_method, reason_code, remark, "
                "counted_by_user_id, counted_at, dimension_sha256, request_sha256, "
                "idempotency_key_hash, created_at) VALUES "
                "(?, ?, ?, ?, 1, ?, ?, ?, NULL, 'unresolved-material', 'unknown', "
                "'new', 'available', NULL, NULL, NULL, 'SN-PRESENT', 'serial_no', "
                "1, 'pending_verification', 'scan', NULL, '', ?, ?, ?, ?, ?, ?)",
                (_uuid(1410), facts["task"], facts["round"],
                 facts["scope_observed"], facts["owner"],
                 facts["location_observed"], facts["person"], facts["user"],
                 NOW, "8" * 64, HASH_B, "8" * 64, NOW),
            )
            with pytest.raises(sa.exc.DatabaseError, match="canonical"):
                _insert_completion(
                    connection, facts, completion_id=_uuid(1411),
                    scope_id=facts["scope_snapshot"], count_lines=1,
                    observations=0, total=1, zero=False,
                    key_hash="9" * 64, serials=1,
                )
            duplicate_evidence.rollback()

            _insert_completion(
                connection, facts, completion_id=_uuid(1412),
                scope_id=facts["scope_snapshot"], count_lines=1,
                observations=0, total=1, zero=False,
                key_hash="1" * 64, serials=1,
            )
    finally:
        engine.dispose()


def test_0011_postgresql_offline_ddl_contains_production_seals(monkeypatch) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(
        _config(
            "postgresql+psycopg://migration_user:migration_password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0010}:{REVISION_0011}",
        sql=True,
    )
    sql = output.getvalue()
    assert "CREATE TABLE stocktake_count_observations" in sql
    assert "counted_qty NUMERIC(18, 3)" in sql
    assert "ADD COLUMN observed_line_id UUID" in sql
    assert "fk_stocktake_differences_observed_line" in sql
    assert "rsc_validate_stocktake_scope_completion_insert_0011" in sql
    assert "must cover every cutoff snapshot account exactly once" in sql
    assert "trg_stocktake_count_lines_immutable_0011" in sql
    assert "trg_stocktake_rounds_submission_manifest_update_0011" in sql
    assert "BEFORE INSERT OR UPDATE ON stocktake_rounds" in sql
    assert "TG_OP = 'INSERT'" in sql
    assert "count line violates its cutoff inventory tracking policy" in sql
    assert "count serial material or lot does not match its account" in sql
    assert "one physical serial cannot be both count serial and observation" in sql
    assert "rsc_stocktake_actor_assignment_valid_0011" in sql


def test_0012_postgresql_offline_ddl_has_value_free_duplicate_preflight(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(
        _config(
            "postgresql+psycopg://migration_user:migration_password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0011}:{REVISION_0012}",
        sql=True,
    )
    sql = output.getvalue()
    assert (
        "0012 preflight failed: duplicate resolved stocktake serial "
        "observations must be reviewed before migration"
    ) in sql
    assert "GROUP BY round_id, serial_id" in sql
    assert (
        "CREATE UNIQUE INDEX uq_stocktake_count_observations_round_serial_id "
        "ON stocktake_count_observations (round_id, serial_id) "
        "WHERE serial_id IS NOT NULL"
    ) in sql

    with pytest.raises(RuntimeError, match="requires an online connection"):
        command.downgrade(
            _config(
                "postgresql+psycopg://migration_user:migration_password@localhost/rsc",
                output_buffer=io.StringIO(),
            ),
            f"{REVISION_0012}:{REVISION_0011}",
            sql=True,
        )


def test_0013_postgresql_offline_ddl_is_identifier_aware_and_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(
        _config(
            "postgresql+psycopg://migration_user:migration_password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0012}:{REVISION_0013}",
        sql=True,
    )
    sql = output.getvalue()
    assert "LOCK TABLE stocktake_count_observations" in sql
    assert "IN SHARE ROW EXCLUSIVE MODE" in sql
    assert (
        "0013 preflight failed: existing resolved stocktake serial observations "
        "violate identifier mapping rules"
    ) in sql
    assert "DROP TRIGGER trg_stocktake_count_observations_validate_insert_0011" in sql
    assert "DROP FUNCTION rsc_validate_stocktake_observation_insert_0011()" in sql
    assert "rsc_validate_stocktake_observation_insert_0013" in sql
    assert "NEW.serial_identifier_type = 'serial_no'" in sql
    assert "serial.serial_no = NEW.serial_no_raw" in sql
    assert "NEW.serial_identifier_type = 'qr_code'" in sql
    assert "serial.qr_code = NEW.serial_no_raw" in sql
    assert "mapping.object_type = 'serial'" in sql
    assert "mapping.object_id = NEW.serial_id" in sql
    assert "mapping.status = 'active'" in sql
    assert "NEW.serial_identifier_type = 'unknown'" in sql

    with pytest.raises(RuntimeError, match="requires an online connection"):
        command.downgrade(
            _config(
                "postgresql+psycopg://migration_user:migration_password@localhost/rsc",
                output_buffer=io.StringIO(),
            ),
            f"{REVISION_0013}:{REVISION_0012}",
            sql=True,
        )


def test_0011_refuses_to_invent_manifests_for_preexisting_submitted_round(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'sealed-0010.db'}"
    command.upgrade(_config(database_url), REVISION_0010)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO stocktake_tasks "
                "(id, task_no, task_type, region_org_id, status, blind_count, "
                "cutoff_ledger_cursor, cutoff_at, scope_manifest_sha256, "
                "snapshot_manifest_sha256, control_source_system_id, "
                "control_sync_run_id, control_snapshot_at, "
                "control_manifest_sha256, current_round_no, created_by_user_id, "
                "deadline, issued_at, frozen_at, submitted_at, posted_at, "
                "closed_at, cancelled_at, version, note, created_at, updated_at) "
                "VALUES (?, 'SEALED-BEFORE-0011', 'full', ?, 'submitted', 1, "
                "0, ?, ?, ?, NULL, NULL, NULL, NULL, 1, ?, NULL, ?, ?, ?, NULL, "
                "NULL, NULL, 1, '', ?, ?)",
                (_uuid(1301), _uuid(1302), NOW, HASH_A, HASH_B, _uuid(1303),
                 NOW, NOW, NOW, NOW, NOW),
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_rounds "
                "(id, task_id, round_no, round_type, status, "
                "submitted_by_user_id, started_at, submitted_at, "
                "count_manifest_sha256, idempotency_key_hash, created_at, "
                "updated_at) VALUES (?, ?, 1, 'initial', 'submitted', ?, ?, ?, "
                "?, ?, ?, ?)",
                (_uuid(1304), _uuid(1301), _uuid(1303), NOW, NOW, HASH_C,
                 HASH_A, NOW, NOW),
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="submitted without an immutable"):
        command.upgrade(_config(database_url), REVISION_0011)
    verification = sa.create_engine(database_url)
    try:
        assert verification.connect().exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0010
        assert "stocktake_count_observations" not in set(
            inspect(verification).get_table_names()
        )
    finally:
        verification.dispose()


def _insert_0016_submitted_fixture(
    connection: sa.Connection,
    *,
    pending_observation: bool = True,
) -> tuple[dict[str, str], str]:
    """Create only reviewed local evidence; no inventory fact is constructed."""

    facts = _insert_opening_fixture(connection)
    connection.exec_driver_sql(
        "INSERT INTO stocktake_count_lines "
        "(id, task_id, round_id, scope_id, stock_account_id, counted_qty, "
        "count_method, reason_code, remark, counted_by_user_id, counted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 'manual', NULL, "
        "'', ?, ?, ?, ?)",
        (
            facts["count_line"],
            facts["task"],
            facts["round"],
            facts["scope_snapshot"],
            facts["account_cutoff"],
            facts["user"],
            NOW,
            NOW,
            NOW,
        ),
    )
    if pending_observation:
        _insert_observation(
            connection,
            facts,
            observation_id=facts["observation_pending"],
            observation_no=1,
            material_id=None,
            raw="unresolved-site-code",
            status="pending_verification",
            quantity=2,
            dimension_hash=HASH_A,
        )
    _insert_completion(
        connection,
        facts,
        completion_id=_uuid(1601),
        scope_id=facts["scope_observed"],
        count_lines=0,
        observations=1 if pending_observation else 0,
        total=2 if pending_observation else 0,
        zero=not pending_observation,
        key_hash="1" * 64,
    )
    _insert_completion(
        connection,
        facts,
        completion_id=_uuid(1602),
        scope_id=facts["scope_snapshot"],
        count_lines=1,
        observations=0,
        total=0,
        zero=False,
        key_hash="2" * 64,
    )
    _insert_completion(
        connection,
        facts,
        completion_id=_uuid(1603),
        scope_id=facts["scope_zero"],
        count_lines=0,
        observations=0,
        total=0,
        zero=True,
        key_hash="3" * 64,
        completed_at=LATER,
    )
    submission_id = _uuid(1604)
    connection.exec_driver_sql(
        "INSERT INTO stocktake_round_submissions "
        "(id, task_id, round_id, sealing_completion_id, scope_count, "
        "zero_scope_count, count_line_count, observation_line_count, "
        "serial_count, total_counted_qty, round_manifest_sha256, "
        "count_manifest_sha256, request_sha256, idempotency_key_hash, "
        "submitted_by_user_id, submitted_by_person_id, "
        "submitted_role_assignment_id, authorization_version, submitted_at, "
        "created_at) VALUES (?, ?, ?, ?, 3, ?, 1, ?, 0, ?, ?, ?, ?, ?, ?, ?, "
        "?, 1, ?, ?)",
        (
            submission_id,
            facts["task"],
            facts["round"],
            _uuid(1603),
            1 if pending_observation else 2,
            1 if pending_observation else 0,
            2 if pending_observation else 0,
            HASH_A,
            HASH_B,
            HASH_C,
            "4" * 64,
            facts["user"],
            facts["person"],
            facts["assignment"],
            LATER,
            LATER,
        ),
    )
    connection.exec_driver_sql(
        "UPDATE stocktake_rounds SET status = 'submitted', "
        "submitted_by_user_id = ?, submitted_at = ?, "
        "count_manifest_sha256 = ?, updated_at = ? WHERE id = ?",
        (facts["user"], LATER, HASH_B, LATER, facts["round"]),
    )
    if pending_observation:
        connection.exec_driver_sql(
            "INSERT INTO stocktake_differences "
            "(id, task_id, round_id, scope_id, control_snapshot_line_id, "
            "difference_no, difference_type, material_id, expected_account_id, "
            "observed_account_id, observed_line_id, serial_id, book_qty, "
            "counted_qty, difference_qty, affected_qty, reason_code, "
            "reason_text, evidence_required, created_at) VALUES "
            "(?, ?, ?, ?, NULL, 1, 'excess', NULL, NULL, NULL, ?, NULL, 0, "
            "2, 2, 2, 'opening_physical_excess', 'physical only', 1, ?)",
            (
                _uuid(1605),
                facts["task"],
                facts["round"],
                facts["scope_observed"],
                facts["observation_pending"],
                LATER,
            ),
        )
    return facts, submission_id


def _insert_0016_manager(
    connection: sa.Connection, facts: dict[str, str]
) -> tuple[str, str, str]:
    user_id = "00000000-0000-0000-0000-000000001606"
    person_id = _uuid(1607)
    assignment_id = _uuid(1608)
    connection.exec_driver_sql(
        "INSERT INTO people (id, external_object_id, organization_id, "
        "employee_no, name, mobile_encrypted, mobile_hash, employment_status, "
        "source_updated_at, created_at, updated_at) VALUES "
        "(?, NULL, ?, 'MGR-0016', 'manager', NULL, NULL, 'active', NULL, ?, ?)",
        (person_id, facts["owner"], NOW, NOW),
    )
    connection.exec_driver_sql(
        "INSERT INTO users (id, mobile, name, password_hash, role, province, "
        "is_active, require_password_change, created_at, updated_at, person_id, "
        "account_status, last_login_at, authorization_version) VALUES "
        "(?, '13800001606', 'manager', 'not-a-password', "
        "'provincial_manager', NULL, 1, 0, ?, ?, ?, 'active', NULL, 1)",
        (user_id, NOW, NOW, person_id),
    )
    connection.exec_driver_sql(
        "INSERT INTO role_assignments (id, user_id, role_id, scope_type, "
        "scope_id, valid_from, valid_to, status, assigned_by, updated_at, "
        "created_at, revoked_at, revoked_by, reason) VALUES "
        "(?, ?, ?, 'organization', ?, ?, NULL, 'active', ?, ?, ?, NULL, NULL, '')",
        (
            assignment_id,
            user_id,
            PROVINCIAL_MANAGER_ROLE_ID,
            facts["owner"],
            NOW,
            user_id,
            NOW,
            NOW,
        ),
    )
    return user_id, person_id, assignment_id


def test_0016_schema_round_trip_and_postgresql_acl_sql(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0016-schema.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0016)
    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {
            "stocktake_observation_dispositions",
            "stocktake_difference_set_completions",
        } <= set(inspector.get_table_names())
        submission_columns = {
            row["name"]: row
            for row in inspector.get_columns("stocktake_round_submissions")
        }
        assert not submission_columns["count_manifest_sha256"]["nullable"]
        with engine.connect() as connection:
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).all()
            }
        assert {
            "trg_stocktake_rounds_submission_manifest_insert_0016",
            "trg_stocktake_rounds_submission_manifest_update_0016",
            "trg_stocktake_observation_dispositions_validate_insert_0016",
            "trg_stocktake_difference_set_completions_validate_insert_0016",
            "trg_stocktake_differences_completion_seal_insert_0016",
            "trg_stocktake_reviews_difference_completion_insert_0016",
        } <= triggers
    finally:
        engine.dispose()

    command.downgrade(config, REVISION_0015)
    downgraded = sa.create_engine(database_url)
    try:
        inspector = inspect(downgraded)
        assert "count_manifest_sha256" not in {
            row["name"]
            for row in inspector.get_columns("stocktake_round_submissions")
        }
        assert not ({
            "stocktake_observation_dispositions",
            "stocktake_difference_set_completions",
        } & set(inspector.get_table_names()))
    finally:
        downgraded.dispose()

    output = io.StringIO()
    command.upgrade(
        _config(
            "postgresql+psycopg://migration_user:migration_password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0015}:{REVISION_0016}",
        sql=True,
    )
    sql = output.getvalue()
    assert "submission.count_manifest_sha256 = NEW.count_manifest_sha256" in sql
    assert "submission.round_manifest_sha256 = NEW.count_manifest_sha256" not in sql
    assert "CREATE TABLE stocktake_observation_dispositions" in sql
    assert "CREATE TABLE stocktake_difference_set_completions" in sql
    assert "BEFORE TRUNCATE ON stocktake_observation_dispositions" in sql
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE stocktake_observation_dispositions, "
        "stocktake_difference_set_completions FROM PUBLIC, star_oam_api"
    ) in sql
    for function_name in (
        "rsc_require_stocktake_round_submission_0016",
        "rsc_block_stocktake_review_fact_mutation_0016",
        "rsc_validate_stocktake_observation_disposition_0016",
        "rsc_validate_stocktake_difference_set_completion_0016",
        "rsc_block_stocktake_difference_after_seal_0016",
        "rsc_require_stocktake_difference_completion_0016",
    ):
        assert (
            f"REVOKE EXECUTE ON FUNCTION {function_name}() "
            "FROM PUBLIC, star_oam_api"
        ) in sql


def test_0016_upgrade_and_downgrade_refuse_existing_review_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0016-preflight.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0013)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_legacy_round_submission_for_0014(connection, candidate_count=1)
    finally:
        engine.dispose()
    command.upgrade(config, REVISION_0015)

    with pytest.raises(RuntimeError, match="cannot be safely inferred"):
        command.upgrade(config, REVISION_0016)
    verification = sa.create_engine(database_url)
    try:
        assert verification.connect().exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0015
        assert "count_manifest_sha256" not in {
            row["name"]
            for row in inspect(verification).get_columns(
                "stocktake_round_submissions"
            )
        }
    finally:
        verification.dispose()

    empty_url = f"sqlite+pysqlite:///{tmp_path / '0016-down.db'}"
    empty_config = _config(empty_url)
    command.upgrade(empty_config, REVISION_0016)
    sealed = sa.create_engine(empty_url)
    try:
        with sealed.begin() as connection:
            _insert_0016_submitted_fixture(connection, pending_observation=False)
    finally:
        sealed.dispose()
    with pytest.raises(RuntimeError, match="persisted opening review evidence"):
        command.downgrade(empty_config, REVISION_0015)


def test_0016_difference_seal_and_pending_disposition_gate_review(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0016-review-evidence.db'}"
    config = _config(database_url)
    command.upgrade(config, REVISION_0016)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            facts, submission_id = _insert_0016_submitted_fixture(connection)
            manager_user, manager_person, manager_assignment = _insert_0016_manager(
                connection, facts
            )
            review_sql = (
                "INSERT INTO stocktake_reviews "
                "(id, task_id, round_id, review_stage, reviewer_user_id, "
                "reviewer_person_id, reviewer_role_assignment_id, "
                "authorization_version, decision, comment, "
                "decision_manifest_sha256, idempotency_key_hash, reviewed_at, "
                "created_at) VALUES (?, ?, ?, 'region', ?, ?, ?, 1, 'recount', "
                "'pending evidence', ?, ?, ?, ?)"
            )
            with pytest.raises(sa.exc.DatabaseError, match="sealed differences"):
                connection.exec_driver_sql(
                    review_sql,
                    (_uuid(1610), facts["task"], facts["round"], manager_user,
                     manager_person, manager_assignment, HASH_A, "a" * 64,
                     "2026-08-30 00:00:02+00:00",
                     "2026-08-30 00:00:02+00:00"),
                )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_difference_set_completions "
                "(id, task_id, round_id, round_submission_id, difference_count, "
                "physical_difference_count, control_difference_count, "
                "pending_observation_difference_count, total_affected_qty, "
                "difference_manifest_sha256, request_sha256, "
                "idempotency_key_hash, completed_by_user_id, "
                "completed_by_person_id, completed_role_assignment_id, "
                "authorization_version, completed_at, created_at) VALUES "
                "(?, ?, ?, ?, 1, 1, 0, 1, 2, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (_uuid(1611), facts["task"], facts["round"], submission_id,
                 HASH_A, HASH_B, "b" * 64, facts["user"], facts["person"],
                 facts["assignment"], LATER, LATER),
            )
            with pytest.raises(sa.exc.DatabaseError, match="already sealed"):
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_differences "
                    "(id, task_id, round_id, scope_id, control_snapshot_line_id, "
                    "difference_no, difference_type, material_id, "
                    "expected_account_id, observed_account_id, observed_line_id, "
                    "serial_id, book_qty, counted_qty, difference_qty, "
                    "affected_qty, reason_code, reason_text, evidence_required, "
                    "created_at) VALUES (?, ?, ?, ?, NULL, 2, 'excess', NULL, "
                    "NULL, NULL, ?, NULL, 0, 2, 2, 2, NULL, '', 1, ?)",
                    (_uuid(1612), facts["task"], facts["round"],
                     facts["scope_observed"], facts["observation_pending"], LATER),
                )
            with pytest.raises(sa.exc.DatabaseError, match="observation dispositions"):
                connection.exec_driver_sql(
                    review_sql,
                    (_uuid(1613), facts["task"], facts["round"], manager_user,
                     manager_person, manager_assignment, HASH_A, "c" * 64,
                     "2026-08-30 00:00:02+00:00",
                     "2026-08-30 00:00:02+00:00"),
                )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_observation_dispositions "
                "(id, task_id, round_id, scope_id, observation_id, disposition, "
                "resolved_material_id, resolved_lot_id, resolved_serial_id, "
                "reason_code, comment, disposition_manifest_sha256, "
                "request_sha256, idempotency_key_hash, decided_by_user_id, "
                "decided_by_person_id, decided_role_assignment_id, "
                "authorization_version, role_code, scope_type, "
                "scope_id_snapshot, authorization_sha256, decided_at, "
                "created_at) VALUES (?, ?, ?, ?, ?, 'pending_verification', "
                "NULL, NULL, NULL, 'awaiting_master', 'retain raw evidence', "
                "?, ?, ?, ?, ?, ?, 1, 'provincial_manager', 'organization', "
                "?, ?, ?, ?)",
                (_uuid(1614), facts["task"], facts["round"],
                 facts["scope_observed"], facts["observation_pending"], HASH_A,
                 HASH_B, "d" * 64, manager_user, manager_person,
                 manager_assignment, facts["owner"], HASH_C, LATER, LATER),
            )
            connection.exec_driver_sql(
                review_sql,
                (_uuid(1615), facts["task"], facts["round"], manager_user,
                 manager_person, manager_assignment, HASH_A, "e" * 64,
                 "2026-08-30 00:00:02+00:00",
                 "2026-08-30 00:00:02+00:00"),
            )
            with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                connection.exec_driver_sql(
                    "UPDATE stocktake_observation_dispositions SET comment = 'x'"
                )
            with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                connection.exec_driver_sql(
                    "DELETE FROM stocktake_difference_set_completions"
                )
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM stock_accounts"
            ).scalar_one() == 2
    finally:
        engine.dispose()
