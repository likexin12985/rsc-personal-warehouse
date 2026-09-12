"""Receipt worker startup requires the shared exact catalog proof and a scope."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .oam_projection_security import (
    MIGRATION_ROLE,
    PROJECTOR_ROLE,
    verify_oam_projection_database_boundary,
)


class OamReceiptProjectionDatabaseBoundaryError(RuntimeError):
    """The worker lacks an enabled read/write receipt scope coordinate."""


def verify_oam_receipt_projection_database_boundary(
    engine: Engine,
    *,
    expected_role: str = PROJECTOR_ROLE,
    expected_migration_role: str = MIGRATION_ROLE,
) -> None:
    """Check catalog closure before calling the pinned migration-owned helper.

    The shared verifier covers receipt policies, their complete expressions,
    function bodies/ACLs, binding constraints, FORCE RLS and append-only guards.
    It runs for both projector workers, so receipt grants cannot weaken the
    work-order worker's boundary. Zero receipt bindings are allowed in the
    shared schema, but cannot start this receipt worker.
    """
    if engine.dialect.name != "postgresql":
        return
    verify_oam_projection_database_boundary(
        engine,
        expected_role=expected_role,
        expected_migration_role=expected_migration_role,
    )
    with engine.connect() as connection:
        ready = connection.scalar(text(
            "SELECT public.rsc_oam_receipt_rls_check_0082("
            "'select', 'projector_read', 'oam_receipt_sync_scope_bindings', '{}'::jsonb)"
        ))
    if ready is not True:
        raise OamReceiptProjectionDatabaseBoundaryError(
            "OAM receipt projector database boundary failed: bindings.read_write_pair"
        )


__all__ = [
    "OamReceiptProjectionDatabaseBoundaryError",
    "verify_oam_receipt_projection_database_boundary",
]
