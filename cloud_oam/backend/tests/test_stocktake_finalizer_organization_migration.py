from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

from app.database_security import RUNTIME_EXECUTE_FUNCTIONS, RUNTIME_FUNCTION_BODY_SHA256


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "backend/alembic/versions/20260906_0064_stocktake_finalizer_organization_lock.py"


def _migration():
    spec = importlib.util.spec_from_file_location("stocktake_finalizer_organization_0064", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0064_is_forward_only_and_keeps_review_status_revision_reserved():
    migration = _migration()
    assert migration.revision == "20260906_0064"
    assert migration.down_revision == "20260905_0062"
    assert "0063" in migration.__doc__


def test_0064_lock_function_is_exact_runtime_security_capability():
    migration = _migration()
    coordinate = (migration.LOCK_FUNCTION, "uuid")
    sql = migration._postgresql_lock_function_sql()
    body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == migration.LOCK_BODY_SHA256
    assert RUNTIME_EXECUTE_FUNCTIONS[coordinate] == (
        "v", True, "plpgsql", ("search_path=pg_catalog, public",)
    )
    assert RUNTIME_FUNCTION_BODY_SHA256[coordinate] == migration.LOCK_BODY_SHA256
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "FOR SHARE OF organizations" in sql
    assert "org_type = 'headquarters'" in sql
    assert "status = 'active'" in sql
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "GRANT UPDATE" not in source
    assert "GRANT EXECUTE ON FUNCTION" in source
