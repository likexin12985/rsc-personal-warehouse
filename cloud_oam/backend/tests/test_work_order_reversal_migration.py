"""The inverse's source label cannot hide a work-order origin from PostgreSQL."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST

MIGRATION = Path(__file__).parents[1] / "alembic/versions/20261007_0097_work_order_reversal_boundary.py"


def test_reversal_boundary_extends_the_existing_whole_transaction_guard():
    m = runpy.run_path(str(MIGRATION))
    old, new = m["sources"]()
    assert m["down_revision"] == "20261006_0096"
    assert new.replace(m["GUARD"], "") == old
    assert new.index("0097 work order reversal") < new.index("IF tx.source_document_type <>")
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[
        ("rsc_check_work_order_material_transaction_0090", "uuid")] == hashlib.sha256(new.encode()).hexdigest()
    assert OAM_SYNC_FUNCTION_MANIFEST["rsc_oam_runtime_binding_ready_0044()"][6] == m["NEW_HASH"]


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
def test_reversal_guard_migration_sql_and_plpgsql_parse(operation):
    m = runpy.run_path(str(MIGRATION))
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})):
        m[operation]()
    parser = pytest.importorskip("pglast.parser")
    sql = output.getvalue()
    parser.parse_sql(sql)
    parser.parse_plpgsql_json("CREATE FUNCTION checked(checked_transaction uuid) RETURNS void LANGUAGE plpgsql AS $body$" + m["sources"]()[1] + "$body$")
    assert sql.index("0097 transition blocked") < sql.index("work_order_reversal_boundary_0097")
    assert "SHARE ROW EXCLUSIVE" in sql
    assert "DROP TABLE" not in sql and "GRANT " not in sql


@pytest.mark.parametrize("binding", ["original_source", "inverse_source", "original_operation", "unrelated"])
def test_transition_preserves_ordinary_history_and_refuses_detached_inverses(binding):
    m = runpy.run_path(str(MIGRATION))
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql("CREATE TABLE inventory_transactions (id TEXT PRIMARY KEY, source_document_type TEXT, reversed_transaction_id TEXT)")
        db.exec_driver_sql("CREATE TABLE work_order_material_operations (posting_transaction_id TEXT)")
        db.exec_driver_sql("INSERT INTO inventory_transactions VALUES ('original', ?, NULL)",
            ("work_order_material" if binding == "original_source" else "generic",))
        db.exec_driver_sql("INSERT INTO inventory_transactions VALUES ('inverse', ?, 'original')",
            ("work_order_material" if binding == "inverse_source" else "generic",))
        if binding == "original_operation":
            db.exec_driver_sql("INSERT INTO work_order_material_operations VALUES ('original')")
        db.commit()
        before = db.exec_driver_sql("SELECT * FROM inventory_transactions ORDER BY id").all()
        for action in ("upgrade", "downgrade"):
            if binding == "unrelated":
                m[action]()
            else:
                with pytest.raises(RuntimeError, match="0097 transition blocked"):
                    m[action]()
            assert db.exec_driver_sql("SELECT * FROM inventory_transactions ORDER BY id").all() == before
    engine.dispose()
