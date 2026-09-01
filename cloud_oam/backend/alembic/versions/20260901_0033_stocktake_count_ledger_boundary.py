"""Seal each non-opening stocktake scope at an immutable ledger cursor.

Revision ID: 20260901_0033
Revises: 20260901_0032
Create Date: 2026-09-01

The new column stays nullable so evidence written before this revision remains
distinguishable and can fail closed in application replay.  New non-opening
scope completions must carry the exact current inventory-ledger cursor; opening
stocktake completions continue to carry no count cursor.  No historical fact is
backfilled or guessed and no runtime table DML privilege is granted.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260901_0033"
down_revision: Union[str, Sequence[str], None] = "20260901_0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "stocktake_scope_count_completions"
COLUMN = "count_ledger_cursor"
CHECK = "ck_stocktake_scope_count_completions_count_cursor"
PG_FUNCTION = "rsc_validate_stocktake_count_ledger_boundary_0033"
PG_TRIGGER = "trg_stocktake_scope_count_ledger_boundary_0033"
SQLITE_TRIGGER = "trg_stocktake_scope_count_ledger_boundary_0033"
PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
LEDGER_HEAD_ID = "40000000-0000-4000-8000-000000000001"
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0033 while count ledger cursor evidence exists"
)


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0033 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite" and context.is_offline_mode():
        raise RuntimeError("0033 SQLite upgrade requires an online connection")
    if dialect == "postgresql":
        op.execute(
            "LOCK TABLE public.stocktake_scope_count_completions, "
            "public.stocktake_tasks, public.inventory_ledger_heads "
            "IN ACCESS EXCLUSIVE MODE"
        )
        op.add_column(TABLE, sa.Column(COLUMN, sa.BigInteger(), nullable=True))
        op.create_check_constraint(
            CHECK,
            TABLE,
            f"{COLUMN} IS NULL OR {COLUMN} >= 0",
        )
        op.execute(_postgresql_function_sql())
        op.execute(
            f"CREATE TRIGGER {PG_TRIGGER} BEFORE INSERT ON public.{TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{PG_FUNCTION}()"
        )
        op.execute(
            f"ALTER TABLE public.{TABLE} ENABLE ALWAYS TRIGGER {PG_TRIGGER}"
        )
        _apply_postgresql_function_acl()
        return

    op.add_column(
        TABLE,
        sa.Column(
            COLUMN,
            sa.BigInteger(),
            sa.CheckConstraint(
                f"{COLUMN} IS NULL OR {COLUMN} >= 0",
                name=CHECK,
            ),
            nullable=True,
        ),
    )
    op.execute(_sqlite_trigger_sql())


def downgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        raise RuntimeError("0033 downgrade requires an online evidence check")
    bind = op.get_bind()
    if dialect == "postgresql":
        bind.exec_driver_sql(
            f"LOCK TABLE public.{TABLE} IN ACCESS EXCLUSIVE MODE"
        )
    prefix = "public." if dialect == "postgresql" else ""
    if bind.exec_driver_sql(
        f"SELECT 1 FROM {prefix}{TABLE} "
        f"WHERE {COLUMN} IS NOT NULL LIMIT 1"
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)

    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {PG_TRIGGER} ON public.{TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_FUNCTION}()")
        op.drop_constraint(CHECK, TABLE, type_="check")
        op.drop_column(TABLE, COLUMN)
        return

    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_TRIGGER}")
    op.drop_column(TABLE, COLUMN)


def _apply_postgresql_function_acl() -> None:
    signature = f"public.{PG_FUNCTION}()"
    op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname = '{MIGRATION_ROLE}') THEN
        EXECUTE 'ALTER FUNCTION {signature} OWNER TO {MIGRATION_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname = '{PRODUCTION_API_ROLE}') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {signature} '
                'FROM {PRODUCTION_API_ROLE}';
    END IF;
END
$$
"""
    )


def _postgresql_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    task_kind text;
    cutoff_cursor bigint;
    head_next_cursor bigint;
BEGIN
    SELECT task.task_type, task.cutoff_ledger_cursor
      INTO task_kind, cutoff_cursor
      FROM public.stocktake_tasks AS task
     WHERE task.id = NEW.task_id;
    IF NOT FOUND OR task_kind NOT IN
       ('opening', 'full', 'sample', 'ad_hoc', 'personal', 'termination') THEN
        RAISE EXCEPTION 'stocktake count ledger boundary task is invalid';
    END IF;
    IF task_kind = 'opening' THEN
        IF NEW.count_ledger_cursor IS NOT NULL THEN
            RAISE EXCEPTION 'opening stocktake count ledger cursor must be null';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.count_ledger_cursor IS NULL OR cutoff_cursor IS NULL
       OR NEW.count_ledger_cursor < cutoff_cursor THEN
        RAISE EXCEPTION 'non-opening stocktake count ledger cursor is invalid';
    END IF;
    SELECT head.next_cursor
      INTO head_next_cursor
      FROM public.inventory_ledger_heads AS head
     WHERE head.id = '{LEDGER_HEAD_ID}'::uuid
       AND head.stream_key = 'inventory'
     FOR UPDATE OF head;
    IF NOT FOUND OR head_next_cursor IS NULL OR head_next_cursor <= 0
       OR NEW.count_ledger_cursor <> head_next_cursor - 1 THEN
        RAISE EXCEPTION 'non-opening stocktake count ledger cursor is not current';
    END IF;
    RETURN NEW;
END
$$
"""


def _sqlite_trigger_sql() -> str:
    compact_head_id = LEDGER_HEAD_ID.replace("-", "")
    return f"""
CREATE TRIGGER {SQLITE_TRIGGER}
BEFORE INSERT ON {TABLE}
WHEN NOT EXISTS (
        SELECT 1 FROM stocktake_tasks AS task WHERE task.id = NEW.task_id
     )
 OR COALESCE((SELECT task.task_type FROM stocktake_tasks AS task
               WHERE task.id = NEW.task_id), 'invalid') NOT IN
       ('opening', 'full', 'sample', 'ad_hoc', 'personal', 'termination')
 OR ((SELECT task.task_type FROM stocktake_tasks AS task
       WHERE task.id = NEW.task_id) = 'opening'
     AND NEW.count_ledger_cursor IS NOT NULL)
 OR ((SELECT task.task_type FROM stocktake_tasks AS task
       WHERE task.id = NEW.task_id) IN {NONOPENING_SQL}
     AND (
          NEW.count_ledger_cursor IS NULL
          OR (SELECT task.cutoff_ledger_cursor FROM stocktake_tasks AS task
               WHERE task.id = NEW.task_id) IS NULL
          OR NEW.count_ledger_cursor <
             (SELECT task.cutoff_ledger_cursor FROM stocktake_tasks AS task
               WHERE task.id = NEW.task_id)
          OR NOT EXISTS (
               SELECT 1 FROM inventory_ledger_heads AS head
                WHERE replace(CAST(head.id AS TEXT), '-', '') =
                      '{compact_head_id}'
                  AND head.stream_key = 'inventory'
                  AND head.next_cursor > 0
          )
          OR NEW.count_ledger_cursor <>
             (SELECT head.next_cursor - 1
                FROM inventory_ledger_heads AS head
               WHERE replace(CAST(head.id AS TEXT), '-', '') =
                     '{compact_head_id}'
                 AND head.stream_key = 'inventory')
     ))
BEGIN
    SELECT RAISE(ABORT, 'stocktake count ledger boundary is invalid');
END
"""
