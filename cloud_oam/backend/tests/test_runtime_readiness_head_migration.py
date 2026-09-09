from pathlib import Path
import hashlib
import runpy
from types import SimpleNamespace

import pytest
import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/20260919_0079_runtime_readiness_head.py"


def test_readiness_marker_hashes_preserve_the_historical_downgrade_source():
    migration = runpy.run_path(str(MIGRATION))
    template = runpy.run_path(str(MIGRATION.with_name(
        "20260903_0052_opening_terminal_guard_execution.py"
    )))
    body = template["_oam_runtime_ready_function_sql"](template["revision"])
    body = body.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    previous_marker = migration["RUNTIME_READY_SCHEMA_REVISION_0078"]
    previous = body.replace(template["revision"], previous_marker)
    assert hashlib.sha256(previous.encode()).hexdigest() == migration[
        "RUNTIME_READY_BODY_SHA256_0072"
    ]
    assert previous.count(previous_marker) == 1
    current = previous.replace(previous_marker, migration["revision"])
    assert hashlib.sha256(current.encode()).hexdigest() == migration[
        "RUNTIME_READY_BODY_SHA256_0079"
    ]
    assert current.replace(migration["revision"], previous_marker) == previous
    assert migration["down_revision"] == "20260918_0078"


@pytest.mark.parametrize("operation", ["upgrade", "downgrade"])
def test_readiness_repair_locks_and_parses_with_catalog_checks(monkeypatch, operation):
    migration = runpy.run_path(str(MIGRATION))
    statements = []
    monkeypatch.setattr(migration["op"], "execute", statements.append)
    monkeypatch.setattr(migration["op"], "get_bind", lambda: SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql")
    ))
    migration[operation]()
    assert statements[0] == "LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE"
    assert len(statements) == 4
    assert "original_acl" in statements[2]
    assert "original_owner" in statements[2]
    assert "replacement drift" in statements[2]
    parser = pytest.importorskip("pglast.parser")
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith("DO "):
            parser.parse_plpgsql_json(statement)
