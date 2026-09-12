"""Pin the SN commit proof, exact update privileges and reversible empty DDL."""
import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST

MIGRATION = Path(__file__).parents[1] / "alembic/versions/20261002_0092_serial_consumption_projection.py"


def test_serial_lifecycle_guards_and_update_columns_are_pinned():
    migration = runpy.run_path(str(MIGRATION))
    assert security.RUNTIME_UPDATE_COLUMNS["inventory_serials"] == {"lifecycle_status", "updated_at"}
    assert "inventory_serials" not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_INSERT_TABLES | security.RUNTIME_DELETE_TABLES
    for coordinate, (_, result, body) in migration["FUNCTIONS"].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate] == hashlib.sha256(body.encode()).hexdigest()
        assert coordinate in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
        if result == "void":
            assert coordinate in security.MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS
    for name, (table, _, function, kind, deferred) in migration["TRIGGERS"].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table, function, "A", kind, deferred, deferred, deferred)
    assert OAM_SYNC_FUNCTION_MANIFEST["rsc_oam_runtime_binding_ready_0044()"][6] == migration["NEW_HASH"]


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
def test_serial_lifecycle_migration_sql_locks_history_and_parses(monkeypatch, operation):
    migration = runpy.run_path(str(MIGRATION))
    statements = []
    monkeypatch.setattr(migration["op"], "get_bind", lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(migration["op"], "execute", statements.append)
    migration[operation]()
    assert statements[0] == "LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE"
    assert all("public." + table in statements[1] for table in migration["TABLES"])
    assert "0092 transition blocked" in statements[2]
    parser = pytest.importorskip("pglast.parser")
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith(("CREATE FUNCTION", "DO ")):
            parser.parse_plpgsql_json(statement)
    assert "serial_lifecycle_readiness_0092" in statements[-1]
    if operation == "upgrade":
        assert "GRANT UPDATE (lifecycle_status, updated_at) ON public.inventory_serials TO star_oam_api" in statements
        for name, signature in migration["FUNCTIONS"]:
            assert f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api" in statements
    else:
        assert sum("0092 function source or ownership drift" in sql for sql in statements) == 3
