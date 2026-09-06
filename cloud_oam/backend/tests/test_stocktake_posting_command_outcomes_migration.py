from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260906_0065_stocktake_posting_command_outcomes.py"


def _load():
    spec = importlib.util.spec_from_file_location("rsc_migration_0065", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0065_is_linear_after_review_status_and_is_append_only() -> None:
    migration = _load()
    assert migration.revision == "20260906_0065"
    assert migration.down_revision == "20260906_0063"
    source = MIGRATION.read_text(encoding="utf-8")
    assert "stocktake_posting_command_outcomes" in source
    assert "sealed_not_executed" in source
    assert "ENABLE ALWAYS TRIGGER" in source
    assert "REVOKE UPDATE, DELETE, TRUNCATE" in source
    assert "GRANT SELECT, INSERT" in source
    assert "cannot downgrade 0065 while stocktake posting command outcomes exist" in source


def test_0065_allows_multiple_request_coordinates_per_task() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert '"task_id",\n            "request_reference"' in source
    assert 'sa.UniqueConstraint(\n            "task_id", name="uq_stocktake_posting_command_outcomes_task_0065"' not in source
