"""The complete compensation boundary is a versioned database invariant."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0098 as OAM_SYNC_FUNCTION_MANIFEST

MIGRATION = Path(__file__).parents[1] / "alembic/versions/20261008_0098_work_order_reversals.py"


def test_reversal_head_pins_full_sources_and_minimum_append_only_permissions():
    m = runpy.run_path(str(MIGRATION))
    previous = runpy.run_path(str(MIGRATION.with_name("20261007_0097_work_order_reversal_boundary.py")))
    assert m["down_revision"] == previous["revision"] and m["OLD_HASH"] == previous["NEW_HASH"]
    assert OAM_SYNC_FUNCTION_MANIFEST["rsc_oam_runtime_binding_ready_0044()"][6] == m["NEW_HASH"]
    for key, (_, result, body) in m["FUNCTIONS"].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key] == hashlib.sha256(body.encode()).hexdigest()
        assert key in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
        if result == "void": assert key in security.MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS
    for signature, (_, body) in m["_sources"]().items():
        name, args = signature.removeprefix("public.").split("(")
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[name,args[:-1]] == hashlib.sha256(body.encode()).hexdigest()
    for name, (table, _, function, bits, deferred) in m["TRIGGERS"].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table,function,"A",bits,deferred,deferred,deferred)
    for table in m["TABLES"]:
        assert table in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
        assert table not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
def test_reversal_migration_and_inner_function_bodies_parse(operation):
    m = runpy.run_path(str(MIGRATION)); output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True,"output_buffer":output})):
        m[operation]()
    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(output.getvalue())
    for (name,_),(args,result,body) in m["FUNCTIONS"].items():
        parser.parse_plpgsql_json(f"CREATE FUNCTION {name}({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$")
    for signature, (_, body) in m["_sources"]().items():
        args = "checked_transaction uuid" if "work_order_material_transaction" in signature else "checked_serial uuid" if "serial_lifecycle" in signature else ""
        result = "void" if args else "trigger"
        parser.parse_plpgsql_json(f"CREATE FUNCTION checked({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$")


def test_sqlite_immutable_parent_history_prevents_lossy_downgrade():
    m = runpy.run_path(str(MIGRATION)); engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection, Operations.context(MigrationContext.configure(connection)):
        connection.exec_driver_sql("CREATE TABLE inventory_transactions(id TEXT, reversed_transaction_id TEXT, source_document_type TEXT)")
        connection.exec_driver_sql("CREATE TABLE work_order_material_operations(id TEXT, posting_transaction_id TEXT)")
        m["upgrade"](); m["downgrade"](); m["upgrade"]()
        connection.exec_driver_sql("""INSERT INTO work_order_reversals
            (id,reversal_no,oam_work_order_id,actor_user_id,operator_person_id,authorization_version,original_operation_id,
             original_replacement_id,reason,request_id,idempotency_key_hash,request_hash,plan_hash,command_jsonb,plan_jsonb,created_at)
            VALUES ('1','WOV-SYNTHETIC','2','actor','3',1,'4',NULL,'synthetic','synthetic-request','key','request','plan','{}','{}','2026-09-13')""")
        for command in ("UPDATE work_order_reversals SET reason=reason", "DELETE FROM work_order_reversals"):
            with pytest.raises(sa.exc.IntegrityError,match="append-only"): connection.exec_driver_sql(command)
        with pytest.raises(RuntimeError, match="immutable reversal history must be retained"):
            m["downgrade"]()
        assert connection.exec_driver_sql("SELECT count(*) FROM work_order_reversals").scalar_one() == 1
