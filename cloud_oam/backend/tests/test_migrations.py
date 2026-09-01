from sqlalchemy import create_engine, inspect

from app.migrations import run_compatibility_migrations


def test_legacy_database_migration_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE warehouses ("
            "id VARCHAR(36) PRIMARY KEY, "
            "warehouse_type VARCHAR(32) NOT NULL"
            ")"
        )
        connection.exec_driver_sql(
            "CREATE TABLE transfers (id VARCHAR(36) PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO warehouses (id, warehouse_type) VALUES "
            "('central-1', 'central'), "
            "('personal-1', 'personal_backpack'), "
            "('service-1', 'service_backpack')"
        )

    run_compatibility_migrations(engine)
    run_compatibility_migrations(engine)

    inspector = inspect(engine)
    warehouse_columns = {column["name"] for column in inspector.get_columns("warehouses")}
    transfer_columns = {column["name"] for column in inspector.get_columns("transfers")}
    assert {
        "warehouse_level",
        "ownership_type",
        "position_scope",
        "parent_warehouse_id",
    } <= warehouse_columns
    assert {
        "source_holder_user_id",
        "requester_user_id",
        "approved_by_id",
        "work_order_number",
        "approved_at",
    } <= transfer_columns

    with engine.connect() as connection:
        rows = {
            row.id: (row.warehouse_level, row.position_scope)
            for row in connection.exec_driver_sql(
                "SELECT id, warehouse_level, position_scope FROM warehouses"
            )
        }
    assert rows["central-1"] == ("headquarters", "unrestricted")
    assert rows["personal-1"] == ("network", "employee")
    assert rows["service-1"] == ("network", "service_provider")
