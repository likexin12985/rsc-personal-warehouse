import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.database_security import FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST

MIGRATION = Path(__file__).parents[1] / "alembic/versions/20261001_0091_work_order_account_admission.py"


def test_account_admission_preserves_existing_opening_and_receipt_branches():
    migration = runpy.run_path(str(MIGRATION))
    receipt = runpy.run_path(str(MIGRATION.with_name("20260928_0088_receipt_account_admission.py")))
    old, new = migration["_account_sources"]()
    assert old == receipt["_account_sources"]()[1]
    assert new.replace(migration["ACCOUNT_BRANCH"], "", 1) == old
    assert hashlib.sha256(old.encode()).hexdigest() == migration["ACCOUNT_OLD_HASH"]
    assert hashlib.sha256(new.encode()).hexdigest() == migration["ACCOUNT_NEW_HASH"]
    assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[("rsc_require_opening_observation_account_0023", "")] == migration["ACCOUNT_NEW_HASH"]
    assert OAM_SYNC_FUNCTION_MANIFEST["rsc_oam_runtime_binding_ready_0044()"][6] == migration["NEW_HASH"]


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
def test_account_admission_sql_is_locked_pinned_and_preserves_acl(monkeypatch, operation):
    migration = runpy.run_path(str(MIGRATION))
    statements = []
    monkeypatch.setattr(migration["op"], "get_bind", lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(migration["op"], "execute", statements.append)
    migration[operation]()
    assert statements[0] == "LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE"
    assert all(table in statements[1] for table in ("stock_accounts", "work_order_material_operations", "inventory_movements"))
    if operation == "downgrade":
        assert "0091 downgrade blocked" in statements[2]
    for sql in statements[-2:]:
        assert "original_owner" in sql and "original_acl" in sql and "replacement drift" in sql
    parser = pytest.importorskip("pglast.parser")
    for sql in statements:
        assert not sa.text(sql)._bindparams
        parser.parse_sql(sql)
        if sql.lstrip().startswith("DO "):
            parser.parse_plpgsql_json(sql)
