"""Enforce one active authentication session per device family.

Revision ID: 20260830_0007
Revises: 20260830_0006
Create Date: 2026-08-30

The migration adds only a partial unique index.  It never selects a winning
session, revokes a duplicate, or rewrites existing authentication evidence.
Prototype data that contains duplicate unrevoked device families must be
reviewed and explicitly closed before this migration is retried.
"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260830_0007"
down_revision: Union[str, Sequence[str], None] = "20260830_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "uq_auth_sessions_active_device_family"
TABLE_NAME = "auth_sessions"
ACTIVE_PREDICATE = sa.text("revoked_at IS NULL")


def upgrade() -> None:
    _assert_no_duplicate_active_device_families()
    op.create_index(
        INDEX_NAME,
        TABLE_NAME,
        ["user_id", "client_type", "device_id"],
        unique=True,
        postgresql_where=ACTIVE_PREDICATE,
        sqlite_where=ACTIVE_PREDICATE,
    )


def _assert_no_duplicate_active_device_families() -> None:
    """Refuse to guess which existing unrevoked session should survive."""

    if context.is_offline_mode():
        # PostgreSQL is the only supported production database.  Emit the
        # online preflight in reviewable offline SQL instead of silently
        # relying on CREATE UNIQUE INDEX to report an opaque conflict.
        op.execute(
            sa.text(
                """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM auth_sessions
        WHERE revoked_at IS NULL
        GROUP BY user_id, client_type, device_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            '0007 preflight failed: duplicate active authentication device families';
    END IF;
END $$
"""
            )
        )
        return

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Close the check/create race against authentication writers.  The
        # lock is held by Alembic's migration transaction until the partial
        # unique index exists.
        bind.exec_driver_sql("LOCK TABLE auth_sessions IN SHARE ROW EXCLUSIVE MODE")

    session_table = sa.table(
        TABLE_NAME,
        sa.column("user_id", sa.String(length=36)),
        sa.column("client_type", sa.String(length=24)),
        sa.column("device_id", sa.String(length=128)),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
    )
    duplicate = bind.execute(
        sa.select(sa.literal(1))
        .select_from(session_table)
        .where(session_table.c.revoked_at.is_(None))
        .group_by(
            session_table.c.user_id,
            session_table.c.client_type,
            session_table.c.device_id,
        )
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "0007 preflight failed: duplicate active authentication device "
            "families must be reviewed and explicitly revoked before migration"
        )


def downgrade() -> None:
    if not context.is_offline_mode():
        bind = op.get_bind()
        if bind.dialect.name == "postgresql":
            # This only protects the DDL transaction.  Operators must stop all
            # authentication writers for the entire downgrade/cutover window.
            bind.exec_driver_sql("LOCK TABLE auth_sessions IN ACCESS EXCLUSIVE MODE")
    op.drop_index(
        INDEX_NAME,
        table_name=TABLE_NAME,
        postgresql_where=ACTIVE_PREDICATE,
        sqlite_where=ACTIVE_PREDICATE,
    )
