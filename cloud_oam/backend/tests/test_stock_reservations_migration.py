from pathlib import Path
import hashlib
import runpy

import pytest
import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260909_0069_stock_reservations.py"


def _migration(filename: str):
    return runpy.run_path(str(MIGRATION.parent / filename))


def test_0069_supply_replacements_pass_preflight_and_restore_published_body(
    monkeypatch,
) -> None:
    """Rebuild the deployed 0061 body, including the 0060 security changes.

    A correct final hash alone missed the neutral-fragment preflight failure:
    its replacement text was already present as a suffix of the old fragment.
    Check both forward and reverse preconditions on the published function.
    """
    m59 = _migration("20260905_0059_material_request_supply_task_causality.py")
    m60 = _migration("20260905_0060_material_request_supply_security_hardening.py")
    m61 = _migration("20260905_0061_material_request_supply_event_key_expression.py")
    m69 = _migration(MIGRATION.name)
    body = m59["_supply_validator_sql"]().split("AS $$", 1)[1].rsplit("$$", 1)[0]
    for name in ("DECLARATION", "ACTOR", "TASK_COUNT", "TASK_IF", "ORDERED", "SEQUENCE"):
        body = body.replace(m60[f"VALIDATOR_{name}_0059"], m60[f"VALIDATOR_{name}_0060"])
    body = body.replace(m61["LEGACY_EVENT_KEY_EXPRESSION"], m61["FIXED_EVENT_KEY_EXPRESSION"])
    published_body = body
    assert hashlib.sha256(body.encode()).hexdigest() == m69["SUPPLY_VALIDATE_BODY_SHA256_0061"]

    replacements = tuple(
        (m69[f"SUPPLY_VALIDATE_{name}_OLD"], m69[f"SUPPLY_VALIDATE_{name}_NEW"])
        for name in ("CEILING", "NEUTRAL", "STATE_AXES")
    )
    for old, new in replacements:
        assert body.count(old) == 1
        assert new not in body
        body = body.replace(old, new)
    assert hashlib.sha256(body.encode()).hexdigest() == m69["SUPPLY_VALIDATE_BODY_SHA256_0069"]
    for old, new in reversed(replacements):
        assert body.count(new) == 1
        assert old not in body
        body = body.replace(new, old)
    assert body == published_body

    statements: list[str] = []
    monkeypatch.setattr(m69["op"], "execute", statements.append)
    m69["_replace_supply_functions"](upgrade=True)
    m69["_replace_supply_functions"](upgrade=False)
    parser = pytest.importorskip("pglast.parser")
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)


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
