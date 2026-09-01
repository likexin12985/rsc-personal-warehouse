"""Seal one resolved physical serial observation per stocktake round.

Revision ID: 20260831_0012
Revises: 20260830_0011
Create Date: 2026-08-31

The migration adds only a partial unique index.  It does not map a raw
identifier, create a serial, rewrite an observation, or infer inventory.  Any
pre-existing duplicate resolved-serial evidence is an ambiguity that requires
explicit review, so the upgrade fails before changing the schema.
"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260831_0012"
down_revision: Union[str, Sequence[str], None] = "20260830_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "stocktake_count_observations"
INDEX_NAME = "uq_stocktake_count_observations_round_serial_id"
INDEX_PREDICATE = sa.text("serial_id IS NOT NULL")
UPGRADE_BLOCKER = (
    "0012 preflight failed: duplicate resolved stocktake serial observations "
    "must be reviewed before migration"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0012: resolved stocktake serial observations require "
    "the round serial uniqueness guard"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0012 supports only PostgreSQL production and SQLite local test schemas"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    _assert_no_duplicate_resolved_serial_observations(dialect)
    op.create_index(
        INDEX_NAME,
        TABLE_NAME,
        ["round_id", "serial_id"],
        unique=True,
        postgresql_where=INDEX_PREDICATE,
        sqlite_where=INDEX_PREDICATE,
    )


def _assert_no_duplicate_resolved_serial_observations(dialect: str) -> None:
    """Refuse to guess which immutable physical observation is canonical."""

    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0012 SQLite upgrade requires an online connection")
        # PostgreSQL is the only supported production database.  Include the
        # same value-free preflight in reviewable offline SQL instead of
        # relying on CREATE UNIQUE INDEX to expose conflicting business keys.
        op.execute(
            sa.text(
                """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM stocktake_count_observations
         WHERE serial_id IS NOT NULL
         GROUP BY round_id, serial_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            '0012 preflight failed: duplicate resolved stocktake serial observations must be reviewed before migration';
    END IF;
END $$
"""
            )
        )
        return

    bind = op.get_bind()
    if dialect == "postgresql":
        # Hold the lock through index creation so a writer cannot insert a
        # duplicate between the explicit preflight and the new unique guard.
        bind.exec_driver_sql(
            "LOCK TABLE stocktake_count_observations "
            "IN SHARE ROW EXCLUSIVE MODE"
        )

    observation = sa.table(
        TABLE_NAME,
        sa.column("round_id", sa.Uuid()),
        sa.column("serial_id", sa.Uuid()),
    )
    duplicate = bind.execute(
        sa.select(sa.literal(1))
        .select_from(observation)
        .where(observation.c.serial_id.is_not(None))
        .group_by(observation.c.round_id, observation.c.serial_id)
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if duplicate is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0012 downgrade requires an online connection for fail-closed "
            "resolved-serial evidence checks"
        )

    dialect = _dialect_name()
    bind = op.get_bind()
    if dialect == "postgresql":
        # Close the evidence-check/drop race.  The guard remains present when
        # any resolved serial evidence exists, so downgrade cannot silently
        # weaken an active immutable stocktake record.
        bind.exec_driver_sql(
            "LOCK TABLE stocktake_count_observations IN ACCESS EXCLUSIVE MODE"
        )

    observation = sa.table(TABLE_NAME, sa.column("serial_id", sa.Uuid()))
    has_resolved_serial = bind.execute(
        sa.select(sa.literal(1))
        .select_from(observation)
        .where(observation.c.serial_id.is_not(None))
        .limit(1)
    ).first()
    if has_resolved_serial is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)

    op.drop_index(
        INDEX_NAME,
        table_name=TABLE_NAME,
        postgresql_where=INDEX_PREDICATE,
        sqlite_where=INDEX_PREDICATE,
    )
