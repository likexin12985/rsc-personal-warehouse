from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.oam_receipt_projection_security import (
    verify_oam_receipt_projection_database_boundary,
)


def test_receipt_boundary_is_noop_for_non_postgresql_engines() -> None:
    verify_oam_receipt_projection_database_boundary(
        SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    )


def test_receipt_boundary_migration_contains_separate_scope_and_acl_graph() -> None:
    path = Path(__file__).parents[1] / "alembic/versions/20260922_0082_oam_receipt_projector_boundary.py"
    source = path.read_text(encoding="utf-8")
    for marker in (
        "20260922_0082",
        "oam_receipt_sync_scope_bindings",
        "rsc_oam_receipt_rls_check_0082",
        "oam_receipt_evidence",
        "FORCE ROW LEVEL SECURITY",
        "GRANT INSERT (id, external_object_id, shipment_id, status, source_time",
    ):
        assert marker in source


def test_receipt_boundary_does_not_grant_projector_delete_or_update() -> None:
    path = Path(__file__).parents[1] / "alembic/versions/20260922_0082_oam_receipt_projector_boundary.py"
    source = path.read_text(encoding="utf-8")
    assert "GRANT UPDATE" not in source
    assert "GRANT DELETE" not in source
