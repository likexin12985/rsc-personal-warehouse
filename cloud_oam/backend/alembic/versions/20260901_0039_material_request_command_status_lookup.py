"""Add an exact audit sentinel for material-request command recovery.

Revision ID: 20260901_0039
Revises: 20260901_0038
Create Date: 2026-09-01

The index stores no new credential or command payload.  It only guarantees
that one non-secret ``X-Request-ID`` sentinel resolves to at most one withdraw
or cancel event.  Other material-request actions and audit streams retain
their existing replay semantics and independent request-id namespaces.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0039"
down_revision: Union[str, Sequence[str], None] = "20260901_0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "uq_audit_events_material_request_request_id_0039"
PREFLIGHT_ERROR = (
    "0039 preflight failed: duplicate non-empty X-Request-ID values exist "
    "in the material_request lifecycle audit subset"
)
PREFLIGHT_EMPTY_ERROR = (
    "0039 preflight failed: duplicate empty request_id values exist in the "
    "material_request lifecycle audit subset"
)
LIFECYCLE_PREDICATE = (
    "stream_key = 'material_request' AND action IN "
    "('material_request.withdraw', 'material_request.cancel')"
)


def upgrade() -> None:
    # Offline SQL generation has no rows to inspect.  Production upgrades run
    # this fail-closed preflight against the live connection before emitting
    # the unique index.  A duplicate stops the upgrade: preserve the immutable
    # audit rows, investigate, and design a separately reviewed compatibility
    # migration or mapping.  Do not update/delete history as a "cleanup".
    if not op.get_context().as_sql:
        bind = op.get_bind()
        duplicate = bind.execute(
            sa.text(
                "SELECT request_id, COUNT(*) AS duplicate_count "
                "FROM audit_events "
                f"WHERE {LIFECYCLE_PREDICATE} "
                "GROUP BY request_id HAVING COUNT(*) > 1 "
                "ORDER BY request_id LIMIT 1"
            )
        ).first()
        if duplicate is not None:
            raise RuntimeError(
                PREFLIGHT_EMPTY_ERROR if duplicate[0] == "" else PREFLIGHT_ERROR
            )

    op.create_index(
        INDEX_NAME,
        "audit_events",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text(LIFECYCLE_PREDICATE),
        sqlite_where=sa.text(LIFECYCLE_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="audit_events")
