from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260909_0069_stock_reservations.py"


def test_0069_is_linear_and_reservation_facts_are_append_only() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20260909_0069"' in source
    assert 'down_revision: str | None = "20260908_0068"' in source
    assert '"stock_reservations"' in source
    assert '"stock_reservation_serials"' in source
    assert "GRANT SELECT, INSERT ON TABLE" in source
    assert "REVOKE UPDATE" in source
    assert "0069 reservation facts are append-only" in source
    assert "source_stock_account_id" in source
    assert "stock_account_id" in source
    # The line table's published unique key is three columns; the reservation
    # FK must not invent a fourth revision_no component.
    assert '["request_line_id", "request_id", "revision_id"]' in source
    assert "revision_no > 0" in source


def test_0069_opens_only_the_next_fulfilment_operations_and_readiness() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "'allocate', 'reserve', 'release'" in source
    assert "RUNTIME_READY_BODY_SHA256_0068" in source
    assert "RUNTIME_READY_BODY_SHA256_0069" in source
    assert "APPROVAL_PROJECTION_BODY_SHA256_0059" in source
    assert "APPROVAL_PROJECTION_BODY_SHA256_0069" in source
    assert "SUPPLY_VALIDATE_CEILING_NEW" in source
    assert "APPROVAL_PROJECTION_APPROVED_NEW" in source
    assert "old_revision=revision" in source
    assert "new_revision=RUNTIME_READY_PREVIOUS_REVISION" in source


def test_0069_model_exposes_source_target_and_serial_facts() -> None:
    source = (ROOT / "app" / "inventory_models.py").read_text(encoding="utf-8")
    reservation = source.split("class StockReservation", 1)[1].split(
        "class InventoryTransaction", 1
    )[0]
    for field in (
        "reservation_no",
        "request_id",
        "request_line_id",
        "revision_id",
        "revision_no",
        "allocation_id",
        "source_stock_account_id",
        "stock_account_id",
        "reserved_qty",
        "reserve_transaction_id",
        "request_version",
        "idempotency_key_hash",
    ):
        assert f"{field}: Mapped" in reservation
    assert "class StockReservationSerial" in reservation

