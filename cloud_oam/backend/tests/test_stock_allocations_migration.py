from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260908_0068_stock_allocations.py"


def test_0068_is_linear_and_keeps_allocation_before_reservation():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20260908_0068"' in source
    assert 'down_revision: str | None = "20260907_0067"' in source
    assert '"stock_allocations"' in source
    assert '"stock_allocation_serials"' in source
    assert "GRANT SELECT, INSERT ON TABLE" in source
    assert "GRANT UPDATE ON TABLE" not in source
    assert "GRANT DELETE ON TABLE" not in source
    assert "reservation" not in source.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()


def test_0068_advances_runtime_readiness_with_an_exact_reversible_replacement():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'RUNTIME_READY_PREVIOUS_REVISION = "20260906_0063"' in source
    assert "RUNTIME_READY_BODY_SHA256_0063" in source
    assert "RUNTIME_READY_BODY_SHA256_0068" in source
    assert "_verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0063)" in source
    assert "_verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0068)" in source
    assert "old_revision=RUNTIME_READY_PREVIOUS_REVISION" in source
    assert "new_revision=revision" in source
    assert "old_revision=revision" in source
    assert "new_revision=RUNTIME_READY_PREVIOUS_REVISION" in source
    assert "pg_get_functiondef" in source
    assert "readiness source mismatch" in source
    assert "readiness replacement drift" in source


def test_0068_model_has_projection_and_request_coordinate_columns():
    source = (ROOT / "app" / "inventory_models.py").read_text(encoding="utf-8")
    allocation = source.split("class StockAllocation", 1)[1].split("class StockAllocationSerial", 1)[0]
    for field in (
        "request_id",
        "request_line_id",
        "revision_id",
        "revision_no",
        "request_version",
        "source_stock_account_id",
        "allocated_qty",
        "source_balance_version",
        "source_ledger_cursor",
        "idempotency_key_hash",
    ):
        assert f"{field}: Mapped" in allocation
