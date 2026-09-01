from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
NOW = "2026-08-30 00:00:00+00:00"


def _migrated_engine(
    tmp_path: Path,
    name: str,
    *,
    revision: str = "head",
) -> tuple[Config, sa.Engine]:
    database_url = f"sqlite+pysqlite:///{tmp_path / name}"
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)
    return config, sa.create_engine(database_url)


def _insert_stock_account(
    engine: sa.Engine,
    *,
    account_id: str,
    custodian_person_id: str | None,
    lot_id: str | None,
) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO stock_accounts "
            "(id, owner_org_id, custodian_person_id, location_id, material_id, "
            "condition_code, availability_bucket, lot_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_id,
                "91000000000040008000000000000001",
                custodian_person_id,
                "92000000000040008000000000000001",
                "93000000000040008000000000000001",
                "new",
                "available",
                lot_id,
                NOW,
                NOW,
            ),
        )


def test_stock_account_null_dimensions_are_unique_in_all_four_partitions(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated_engine(tmp_path, "account-null-uniqueness.db")
    combinations = (
        (None, None),
        ("94000000000040008000000000000001", None),
        (None, "95000000000040008000000000000001"),
        (
            "94000000000040008000000000000001",
            "95000000000040008000000000000001",
        ),
    )
    try:
        for ordinal, (custodian_id, lot_id) in enumerate(combinations, start=1):
            _insert_stock_account(
                engine,
                account_id=f"9600000000004000800000000000000{ordinal}",
                custodian_person_id=custodian_id,
                lot_id=lot_id,
            )
            with pytest.raises(sa.exc.IntegrityError):
                _insert_stock_account(
                    engine,
                    account_id=f"9700000000004000800000000000000{ordinal}",
                    custodian_person_id=custodian_id,
                    lot_id=lot_id,
                )
    finally:
        engine.dispose()


def test_movement_boundary_checks_and_ledger_rows_are_immutable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated_engine(tmp_path, "immutable-ledger.db")
    transaction_id = "a1000000000040008000000000000001"
    movement_id = "a2000000000040008000000000000001"
    second_movement_id = "a2000000000040008000000000000002"
    serial_id = "a3000000000040008000000000000001"
    account_id = "a4000000000040008000000000000001"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO inventory_transactions "
                "(id, transaction_no, movement_type, source_document_type, "
                "source_document_id, posting_key, idempotency_key_hash, "
                "request_hash, status, effective_at, posted_at, ledger_cursor, "
                "reversed_transaction_id, actor_user_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transaction_id,
                    "IT-0001",
                    "opening",
                    "opening_stocktake",
                    "OPENING-0001",
                    "opening:OPENING-0001",
                    "a" * 64,
                    "b" * 64,
                    "posted",
                    NOW,
                    NOW,
                    1,
                    None,
                    "00000000-0000-0000-0000-000000000001",
                    NOW,
                ),
            )

        with engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO inventory_movements "
                "(id, transaction_id, line_no, from_account_id, to_account_id, "
                "external_boundary_code, quantity, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "a2000000000040008000000000000011",
                    transaction_id,
                    1,
                    None,
                    None,
                    "OPENING",
                    1,
                    NOW,
                ),
            )

        with engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO inventory_movements "
                "(id, transaction_id, line_no, from_account_id, to_account_id, "
                "external_boundary_code, quantity, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "a2000000000040008000000000000012",
                    transaction_id,
                    1,
                    account_id,
                    "a4000000000040008000000000000002",
                    "SHOULD-BE-NULL",
                    1,
                    NOW,
                ),
            )

        with engine.begin() as connection:
            for row_id, line_no in (
                (movement_id, 1),
                (second_movement_id, 2),
            ):
                connection.exec_driver_sql(
                    "INSERT INTO inventory_movements "
                    "(id, transaction_id, line_no, from_account_id, to_account_id, "
                    "external_boundary_code, quantity, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row_id,
                        transaction_id,
                        line_no,
                        None,
                        account_id,
                        "OPENING",
                        1,
                        NOW,
                    ),
                )
            connection.exec_driver_sql(
                "INSERT INTO inventory_movement_serials "
                "(movement_id, transaction_id, serial_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (movement_id, transaction_id, serial_id, NOW),
            )

        with engine.begin() as connection, pytest.raises(
            sa.exc.IntegrityError
        ):
            connection.exec_driver_sql(
                "INSERT INTO inventory_movement_serials "
                "(movement_id, transaction_id, serial_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (second_movement_id, transaction_id, serial_id, NOW),
            )

        immutable_statements = (
            "UPDATE inventory_transactions SET status = 'posted'",
            "DELETE FROM inventory_transactions",
            "UPDATE inventory_movements SET quantity = quantity",
            "DELETE FROM inventory_movements",
            "UPDATE inventory_movement_serials SET transaction_id = transaction_id",
            "DELETE FROM inventory_movement_serials",
        )
        for statement in immutable_statements:
            with engine.begin() as connection, pytest.raises(
                sa.exc.IntegrityError, match="immutable"
            ):
                connection.exec_driver_sql(statement)
    finally:
        engine.dispose()


def test_inventory_schema_keeps_external_serial_position_and_restrictive_fks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    _, engine = _migrated_engine(tmp_path, "inventory-fks.db")
    try:
        inspector = inspect(engine)
        position_columns = {
            column["name"]: column
            for column in inspector.get_columns("serial_current_positions")
        }
        assert position_columns["stock_account_id"]["nullable"] is True

        for table_name in ("stock_accounts", "inventory_serials"):
            for foreign_key in inspector.get_foreign_keys(table_name):
                assert (foreign_key.get("options") or {}).get("ondelete") == "RESTRICT"

        serial_link_fks = inspector.get_foreign_keys(
            "inventory_movement_serials"
        )
        assert any(
            tuple(foreign_key["constrained_columns"])
            == ("movement_id", "transaction_id")
            and foreign_key["referred_table"] == "inventory_movements"
            and tuple(foreign_key["referred_columns"])
            == ("id", "transaction_id")
            for foreign_key in serial_link_fks
        )
    finally:
        engine.dispose()


def test_0009_downgrade_fails_after_ledger_head_advances(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    config, engine = _migrated_engine(
        tmp_path,
        "used-inventory-head.db",
        revision="20260830_0009",
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE inventory_ledger_heads SET next_cursor = 2 "
                "WHERE stream_key = 'inventory'"
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="ledger head is not the unused seed"):
        command.downgrade(config, "20260830_0008")

    verification_engine = sa.create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0009"
            assert connection.exec_driver_sql(
                "SELECT next_cursor FROM inventory_ledger_heads"
            ).scalar_one() == 2
    finally:
        verification_engine.dispose()
