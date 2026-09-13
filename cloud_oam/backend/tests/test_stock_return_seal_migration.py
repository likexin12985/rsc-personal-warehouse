"""Seal schema and guards retain every historical return fact."""
from datetime import datetime, timezone
from io import StringIO
import hashlib
from pathlib import Path
import runpy
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0101
from app.stock_operation_models import StockOperationCommandSeal

PATH = Path(__file__).parents[1] / "alembic/versions/20261011_0101_stock_return_request_seals.py"


def test_seal_runtime_capabilities_and_exact_sources():
    migration = runpy.run_path(str(PATH))
    assert migration["down_revision"] == "20261010_0100"
    assert OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0101["rsc_oam_runtime_binding_ready_0044()"][6] == migration["NEW_HASH"]
    assert migration["TABLE"] in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
    assert migration["TABLE"] not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES
    for key, digest in migration["FUNCTION_HASHES"].items():
        replacement = runpy.run_path(str(PATH.with_name("20261013_0103_stock_return_outbounds.py")))["_sources"]()[f"public.{key[0]}({key[1]})"]
        assert hashlib.sha256(replacement[0].encode()).hexdigest() == digest
        successor = runpy.run_path(str(PATH.with_name("20261014_0104_stock_return_shipments.py")))["_sources"]()[f"public.{key[0]}({key[1]})"]
        assert successor[0] == replacement[1]
        replacement = successor
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key] == hashlib.sha256(replacement[1].encode()).hexdigest()
        assert key in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    for name, (table, _event, function, flags, deferred) in migration["TRIGGERS"].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table, function, "A", flags, deferred, deferred, deferred)
        if table == "audit_events": assert security.EXPECTED_AUDIT_TRIGGERS[name] == (table, function, flags, deferred, deferred, deferred)


@pytest.mark.parametrize("action", ["upgrade", "downgrade"])
def test_seal_transition_sql_parses_and_does_not_bind_json_literals(action):
    migration = runpy.run_path(str(PATH)); output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})):
        migration[action]()
    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(output.getvalue())
    for (name, _), (args, result, body) in migration["FUNCTIONS"].items():
        sql = f"CREATE FUNCTION {name}({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$"
        assert not sa.text(sql)._bindparams
        parser.parse_plpgsql_json(sql)


def test_empty_seal_schema_reversible_but_recorded_seals_immutable():
    migration = runpy.run_path(str(PATH)); engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
        migration["upgrade"](); migration["downgrade"](); migration["upgrade"]()
        row = dict(id=uuid4(), actor_user_id="synthetic-user", operator_person_id=uuid4(), oam_work_order_id=uuid4(),
            operation_id=None, operation_type="submit_return", authorization_version=1, request_id=uuid4().hex,
            request_reference="synthetic-reference", request_hash="a" * 64, created_at=datetime.now(timezone.utc))
        db.execute(StockOperationCommandSeal.__table__.insert().values(**row))
        for sql in ("DELETE FROM stock_operation_command_seals", "UPDATE stock_operation_command_seals SET request_hash=request_hash"):
            with pytest.raises(sa.exc.IntegrityError, match="append-only"): db.exec_driver_sql(sql)
        with pytest.raises(RuntimeError, match="immutable return request seals must be retained"): migration["downgrade"]()
        assert db.exec_driver_sql("SELECT count(*) FROM stock_operation_command_seals").scalar_one() == 1
