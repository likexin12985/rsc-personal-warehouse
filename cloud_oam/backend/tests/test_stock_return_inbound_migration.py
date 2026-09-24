"""0106 SQL-created inbound tables: real posting, retention and catalog pins.

This fixture applies 0106 over isolated metadata prerequisites. It is not the
full migration chain and cannot prove PostgreSQL deferred guards or role ACLs.
"""
from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.database import Base
from app import database_security as security
from app.oam_sync_scope_security import (OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0105,
    OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0106)
from app.stock_operation_models import StockOperationReturnInbound
from test_stock_return_inbound import (world, stock, recovered, destination, prepared, parcel,
    incoming, acceptance, inbound_accounts, accepted, command, commands, snapshot)

PATH = Path(__file__).parents[1] / "alembic/versions/20261016_0106_stock_return_inbounds.py"


@pytest.fixture
def db():
    migration = runpy.run_path(str(PATH))
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    @sa.event.listens_for(engine, "connect")
    def foreign_keys(connection, _record): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine, tables=[table for table in Base.metadata.tables.values()
        if table.name not in migration["TABLES"]])
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration["upgrade"]()
    with Session(engine) as session: yield session
    engine.dispose()


def test_real_posting_on_0106_tables_retains_facts_and_rejects_downgrade(db, accepted):
    migration = runpy.run_path(str(PATH))
    for table in migration["TABLES"]:
        reflected = sa.Table(table, sa.MetaData(), autoload_with=db.connection(), resolve_fks=False)
        assert set(reflected.columns.keys()) == set(Base.metadata.tables[table].columns.keys())
    value = command(db, accepted)
    result = commands.execute_return_inbound(db, **value); db.commit(); before = snapshot(db)
    assert db.get(StockOperationReturnInbound, result["inbound_id"]).receipt_id == accepted.receipt.receipt_id
    for table in migration["TABLES"]:
        if table.endswith("serials") and not db.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            continue
        for sql in (f"DELETE FROM {table}", f"UPDATE {table} SET id=id"):
            with pytest.raises(sa.exc.IntegrityError, match="append-only"), db.begin_nested():
                db.execute(sa.text(sql))
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError, match="facts must be retained"): migration["downgrade"]()
    assert snapshot(db) == before
    assert commands.execute_return_inbound(db, **value) == {**result, "replayed": True}


def test_empty_0106_roundtrip_preserves_prerequisite_tables(db):
    migration = runpy.run_path(str(PATH)); engine = db.get_bind()
    with Operations.context(MigrationContext.configure(db.connection())):
        migration["downgrade"]()
        assert not set(migration["TABLES"]) & set(sa.inspect(db.connection()).get_table_names())
        assert "stock_operation_receipts" in sa.inspect(db.connection()).get_table_names()
        migration["upgrade"]()
    db.commit()
    assert set(migration["TABLES"]) <= set(sa.inspect(engine).get_table_names())


def test_0106_current_manifest_acl_and_postgresql_function_syntax():
    migration = runpy.run_path(str(PATH))
    assert migration["OLD_HASH"] == OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0105["rsc_oam_runtime_binding_ready_0044()"][6]
    assert migration["NEW_HASH"] == OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0106["rsc_oam_runtime_binding_ready_0044()"][6]
    for table in migration["TABLES"]:
        assert table in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
        assert table not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES
    parser = pytest.importorskip("pglast.parser")
    for key, digest in migration["FUNCTION_HASHES"].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key] == digest
        assert key in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
        args, result, body = migration["FUNCTIONS"][key]
        parser.parse_plpgsql_json(f"CREATE FUNCTION {key[0]}({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$")
    for action in ("upgrade", "downgrade"):
        output = StringIO()
        with Operations.context(MigrationContext.configure(dialect_name="postgresql",
                opts={"as_sql": True, "output_buffer": output})): migration[action]()
        parser.parse_sql(output.getvalue())


@pytest.mark.parametrize("name", ["rsc_check_stock_return_inbound_0106", "rsc_dispatch_stock_return_inbound_0106"])
@pytest.mark.parametrize("field,value", [("source_body", "BEGIN RETURN; END;"), ("owner_name", "star_oam_api"),
    ("can_execute", True), ("configuration", ["search_path=public"])])
def test_inbound_function_body_owner_and_execute_drift_are_rejected(monkeypatch, name, field, value):
    from test_database_security import (_valid_material_request_approval_function_rows,
        _assert_valid_material_request_approval_catalog)
    rows = _valid_material_request_approval_function_rows(monkeypatch)
    next(row for row in rows if row["function_name"] == name)[field] = value
    with pytest.raises(security.DatabaseSecurityBoundaryError):
        _assert_valid_material_request_approval_catalog(monkeypatch, functions=rows)
