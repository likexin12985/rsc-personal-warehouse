"""Pinned PostgreSQL proof bodies, role boundaries and migration DDL."""
import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app import database_security as security

MIGRATION = Path(__file__).parents[1] / "alembic/versions/20260930_0090_work_order_reservation_causality.py"


def test_work_order_guard_bodies_are_pinned_and_not_executable_by_api():
    migration = runpy.run_path(str(MIGRATION))
    for coordinate, (_, _, body) in migration["FUNCTIONS"].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate] == hashlib.sha256(body.encode()).hexdigest()
        assert coordinate in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    assert ("rsc_check_work_order_material_transaction_0090", "uuid") in security.MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS
    for table in migration["PROOF_TABLES"]:
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[f"trg_{table}_proof_0090"] == (
            table, "rsc_dispatch_work_order_material_0090", "A", 5, True, True, True)


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
def test_work_order_migration_locks_history_and_parses_all_sql(monkeypatch, operation):
    migration = runpy.run_path(str(MIGRATION))
    statements = []
    monkeypatch.setattr(migration["op"], "get_bind", lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(migration["op"], "execute", statements.append)
    indexes = []
    monkeypatch.setattr(migration["op"], "create_index", lambda *args, **kwargs: indexes.append((args, kwargs)))
    monkeypatch.setattr(migration["op"], "drop_index", lambda *args, **kwargs: indexes.append((args, kwargs)))
    migration[operation]()
    assert statements[0] == "LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE"
    assert all("public." + table in statements[1] for table in migration["PROOF_TABLES"])
    assert "0090 transition blocked" in statements[2]
    assert all(table in statements[2] for table in migration["FACT_TABLES"])
    assert "source_document_type = 'work_order_material'" in statements[2]
    assert indexes[0][0][0] == "uq_work_order_material_posting_0090"
    parser = pytest.importorskip("pglast.parser")
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith(("CREATE FUNCTION", "DO ")):
            parser.parse_plpgsql_json(statement)
    if operation == "upgrade":
        for (name, signature) in migration["FUNCTIONS"]:
            assert f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api" in statements
        assert sum("CREATE CONSTRAINT TRIGGER" in sql for sql in statements) == len(migration["PROOF_TABLES"])
    assert "work_order_readiness_0090" in statements[-1] and "original_acl" in statements[-1]


@pytest.mark.parametrize("change", [{"is_unique": False}, {"predicate": "posting_transaction_id IS NOT NULL"},
    {"is_valid": False}, {"is_ready": False}, {"key_column": "oam_work_order_id"}, {"column_count": 2}])
def test_work_order_posting_index_drift_is_rejected(change):
    row = dict(table_name="work_order_material_operations", is_unique=True, is_valid=True, is_ready=True,
        is_live=True, key_count=1, column_count=1, access_method="btree", key_column="posting_transaction_id", predicate=None)
    security._assert_work_order_posting_index([row])
    with pytest.raises(security.DatabaseSecurityBoundaryError, match="work order posting uniqueness"):
        security._assert_work_order_posting_index([{**row, **change}])
    with pytest.raises(security.DatabaseSecurityBoundaryError):
        security._assert_work_order_posting_index([])
