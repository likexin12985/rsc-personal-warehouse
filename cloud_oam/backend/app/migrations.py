from sqlalchemy import inspect
from sqlalchemy.engine import Engine


COMPATIBILITY_COLUMNS: dict[str, dict[str, str]] = {
    "warehouses": {
        "warehouse_level": "VARCHAR(20) NOT NULL DEFAULT 'network'",
        "ownership_type": "VARCHAR(24) NOT NULL DEFAULT 'regular'",
        "position_scope": "VARCHAR(24) NOT NULL DEFAULT 'unrestricted'",
        "parent_warehouse_id": "VARCHAR(36)",
    },
    "transfers": {
        "source_holder_user_id": "VARCHAR(36)",
        "requester_user_id": "VARCHAR(36)",
        "approved_by_id": "VARCHAR(36)",
        "work_order_number": "VARCHAR(80) NOT NULL DEFAULT ''",
        "approved_at": "TIMESTAMP",
    },
}

POSTGRES_FOREIGN_KEYS = {
    "warehouses": {
        "fk_warehouses_parent_warehouse_id": (
            "parent_warehouse_id",
            "warehouses",
            "id",
        ),
    },
    "transfers": {
        "fk_transfers_source_holder_user_id": (
            "source_holder_user_id",
            "users",
            "id",
        ),
        "fk_transfers_requester_user_id": ("requester_user_id", "users", "id"),
        "fk_transfers_approved_by_id": ("approved_by_id", "users", "id"),
    },
}


def run_compatibility_migrations(engine: Engine) -> None:
    """Add nullable/defaulted columns that SQLAlchemy create_all cannot backfill."""

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, definitions in COMPATIBILITY_COLUMNS.items():
            if table_name not in table_names:
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in definitions.items():
                if column_name in existing:
                    continue
                connection.exec_driver_sql(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                )

        if "warehouses" in table_names:
            connection.exec_driver_sql(
                "UPDATE warehouses SET warehouse_level = 'headquarters' "
                "WHERE warehouse_type = 'central' AND warehouse_level = 'network'"
            )
            connection.exec_driver_sql(
                "UPDATE warehouses SET position_scope = 'employee' "
                "WHERE warehouse_type = 'personal_backpack' "
                "AND position_scope = 'unrestricted'"
            )
            connection.exec_driver_sql(
                "UPDATE warehouses SET position_scope = 'service_provider' "
                "WHERE warehouse_type = 'service_backpack' "
                "AND position_scope = 'unrestricted'"
            )

        if "transfers" in table_names:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_transfers_work_order_number "
                "ON transfers (work_order_number)"
            )

        if engine.dialect.name == "postgresql":
            live_inspector = inspect(connection)
            for table_name, constraints in POSTGRES_FOREIGN_KEYS.items():
                if table_name not in table_names:
                    continue
                existing_specs = {
                    (
                        tuple(key["constrained_columns"]),
                        key["referred_table"],
                        tuple(key["referred_columns"]),
                    )
                    for key in live_inspector.get_foreign_keys(table_name)
                }
                for name, (column, target_table, target_column) in constraints.items():
                    spec = ((column,), target_table, (target_column,))
                    if spec in existing_specs:
                        continue
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD CONSTRAINT {name} "
                        f"FOREIGN KEY ({column}) REFERENCES {target_table} ({target_column})"
                    )
