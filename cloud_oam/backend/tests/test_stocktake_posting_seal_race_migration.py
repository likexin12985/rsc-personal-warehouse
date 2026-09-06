from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260907_0067_stocktake_posting_seal_race.py"


def test_0067_is_linear_and_locks_task_before_exact_seal_checks() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20260907_0067"' in source
    assert 'down_revision: str | None = "20260906_0066"' in source
    assert "PERFORM 1 FROM public.stocktake_tasks WHERE id = NEW.task_id FOR UPDATE" in source
    assert "request_reference = NEW.request_reference" in source
    assert "ENABLE ALWAYS TRIGGER" in source
