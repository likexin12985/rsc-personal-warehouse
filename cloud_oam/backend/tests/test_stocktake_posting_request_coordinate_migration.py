from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260906_0066_stocktake_posting_request_coordinate.py"


def test_0066_is_linear_and_keeps_historical_completion_rows_compatible() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20260906_0066"' in source
    assert 'down_revision: str | None = "20260906_0065"' in source
    assert 'sa.Column("request_reference", sa.String(length=160), nullable=True)' in source
    assert "Historical completions predate the coordinate column" in source


def test_0066_replaces_task_wide_late_post_guard_with_exact_coordinate_guard() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'OLD_TRIGGER = "trg_stocktake_posting_completions_block_sealed_0065"' in source
    assert 'OLD_FUNCTION = "rsc_guard_stocktake_posting_completion_sealed_0065"' in source
    assert 'OLD_BINDING_FUNCTION = "rsc_guard_stocktake_posting_command_outcome_binding_0065"' in source
    assert 'BINDING_FUNCTION = f"{FUNCTION}_binding"' in source
    assert "request_reference = NEW.request_reference" in source
    assert "AND request_reference = NEW.request_reference" in source
    assert "NEW.request_reference IS NULL OR length(NEW.request_reference) = 0" in source
    assert "new stocktake posting completion requires request reference" in source
    assert "stocktake posting command was sealed as not executed" in source
    upgrade_source = source.split("\ndef downgrade()", 1)[0]
    assert "task_id = NEW.task_id AND disposition = 'sealed_not_executed'" not in upgrade_source
