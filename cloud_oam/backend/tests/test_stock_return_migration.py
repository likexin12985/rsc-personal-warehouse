"""0100 schema, capability pins and lossless transition boundaries."""
from io import StringIO
import hashlib
from pathlib import Path
import runpy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0100
from app.stock_operation_models import StockOperationOrder

MIGRATION = Path(__file__).parents[1] / "alembic/versions/20261010_0100_stock_return_orders.py"


def test_return_guards_and_append_only_capabilities_match_runtime_manifest():
    m = runpy.run_path(str(MIGRATION))
    assert m["down_revision"] == "20261009_0099"
    assert OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0100["rsc_oam_runtime_binding_ready_0044()"][6] == m["NEW_HASH"]
    for key, (_, _, body) in m["FUNCTIONS"].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key] == hashlib.sha256(body.encode()).hexdigest()
        assert key in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    for name, (table, _, function, flags, deferred) in m["TRIGGERS"].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table, function, "A", flags, deferred, deferred, deferred)
    for table in m["TABLES"]:
        assert table in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
        assert table not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES


@pytest.mark.parametrize("action", ["upgrade", "downgrade"])
def test_return_migration_sql_and_plpgsql_are_valid(action):
    m = runpy.run_path(str(MIGRATION)); output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})):
        m[action]()
    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(output.getvalue())
    for (name, _), (args, result, body) in m["FUNCTIONS"].items():
        sql = f"CREATE FUNCTION {name}({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$"
        assert not sa.text(sql)._bindparams
        parser.parse_plpgsql_json(sql)
    for body in m["_account_sources"]():
        parser.parse_plpgsql_json("CREATE FUNCTION checked() RETURNS trigger LANGUAGE plpgsql AS $body$" + body + "$body$")


def test_sqlite_reversible_empty_schema_preserves_permissions_and_refuses_history_loss():
    m = runpy.run_path(str(MIGRATION)); engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql("CREATE TABLE permissions(id CHAR(32),resource TEXT,action TEXT,field_code TEXT,description TEXT,created_at DATETIME,updated_at DATETIME)")
        db.exec_driver_sql("CREATE TABLE role_permissions(id CHAR(32),role_id CHAR(32),permission_id CHAR(32),effect TEXT,created_at DATETIME)")
        db.exec_driver_sql("INSERT INTO permissions(id,resource,action) VALUES ('20000000000040008000000000000062','material_request','receive')")
        m["upgrade"]()
        assert db.exec_driver_sql("SELECT count(*) FROM permissions").scalar_one() == 4
        assert db.exec_driver_sql("SELECT count(DISTINCT id) FROM permissions").scalar_one() == 4
        assert db.exec_driver_sql("SELECT count(*) FROM role_permissions").scalar_one() == 9
        m["downgrade"]()
        assert db.exec_driver_sql("SELECT resource,action FROM permissions").one() == ("material_request", "receive")
        m["upgrade"]()
        value = dict(id=uuid4(), operation_no="RET-SYNTHETIC", operation_type="return", status="submitted", oam_work_order_id=uuid4(),
            source_location_id=uuid4(), target_location_id=uuid4(), transit_location_id=uuid4(), target_custody_assignment_id=uuid4(),
            requester_id=uuid4(), actor_user_id="synthetic-user", authorization_version=1, reason="保留迁移测试历史", request_id="synthetic-request",
            idempotency_key_hash="a" * 64, request_hash="b" * 64, plan_hash="c" * 64, command_jsonb={}, plan_jsonb={},
            posting_transaction_id=uuid4(), created_at=datetime.now(timezone.utc))
        db.execute(StockOperationOrder.__table__.insert().values(**value))
        for sql in ("UPDATE stock_operation_orders SET reason=reason", "DELETE FROM stock_operation_orders"):
            with pytest.raises(sa.exc.IntegrityError, match="append-only"): db.exec_driver_sql(sql)
        with pytest.raises(RuntimeError, match="immutable return history must be retained"): m["downgrade"]()
        assert db.exec_driver_sql("SELECT count(*) FROM stock_operation_orders").scalar_one() == 1
