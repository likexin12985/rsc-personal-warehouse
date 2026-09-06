"""Bind posting completions to their request coordinate.

Revision 0065 accidentally made the late-post database trigger task-wide,
while the service and the append-only outcome table intentionally model
idempotency per ``task_id`` + ``request_reference``.  This forward migration
keeps old completion rows readable (their coordinate is unknown), requires a
coordinate on every newly inserted completion, and makes the trigger reject
only a completion carrying an explicitly sealed coordinate.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260906_0066"
down_revision: str | None = "20260906_0065"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "stocktake_posting_completions"
OUTCOMES = "stocktake_posting_command_outcomes"
OLD_TRIGGER = "trg_stocktake_posting_completions_block_sealed_0065"
OLD_FUNCTION = "rsc_guard_stocktake_posting_completion_sealed_0065"
OLD_BINDING_TRIGGER = "trg_stocktake_posting_command_outcomes_binding_0065"
OLD_BINDING_FUNCTION = "rsc_guard_stocktake_posting_command_outcome_binding_0065"
TRIGGER = "trg_stocktake_posting_completions_block_sealed_0066"
FUNCTION = "rsc_guard_stocktake_posting_completion_sealed_0066"
SQLITE_TRIGGER = f"{TRIGGER}_sqlite"
SQLITE_REQUIRED_TRIGGER = f"{TRIGGER}_required_reference_sqlite"
SQLITE_BINDING_TRIGGER = f"{TRIGGER}_binding_sqlite"
BINDING_TRIGGER = f"{TRIGGER}_binding"
BINDING_FUNCTION = f"{FUNCTION}_binding"


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0066 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            f"LOCK TABLE public.{TABLE}, public.{OUTCOMES} IN ACCESS EXCLUSIVE MODE"
        )
    op.add_column(TABLE, sa.Column("request_reference", sa.String(length=160), nullable=True))

    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {OLD_BINDING_TRIGGER} ON public.{OUTCOMES}"
        )
        op.execute(f"DROP FUNCTION IF EXISTS public.{OLD_BINDING_FUNCTION}()")
        op.execute(
            f"DROP TRIGGER IF EXISTS {OLD_TRIGGER} ON public.{TABLE}"
        )
        op.execute(f"DROP FUNCTION IF EXISTS public.{OLD_FUNCTION}()")
        op.execute(f"""
CREATE FUNCTION public.{BINDING_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.disposition = 'posted' AND (
        NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 OR NOT EXISTS (
            SELECT 1 FROM public.{TABLE}
            WHERE id = NEW.completion_id
              AND task_id = NEW.task_id
              AND expected_task_version = NEW.expected_task_version
              AND request_sha256 = NEW.request_sha256
              AND request_reference = NEW.request_reference
        )
    ) THEN
        RAISE EXCEPTION 'stocktake posting command outcome is not bound to its completion'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$
""")
        op.execute(f"REVOKE ALL ON FUNCTION public.{BINDING_FUNCTION}() FROM PUBLIC")
        op.execute(
            f"CREATE TRIGGER {BINDING_TRIGGER} BEFORE INSERT OR UPDATE ON public.{OUTCOMES} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{BINDING_FUNCTION}()"
        )
        op.execute(f"ALTER TABLE public.{OUTCOMES} ENABLE ALWAYS TRIGGER {BINDING_TRIGGER}")
        op.execute(f"""
CREATE FUNCTION public.{FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    -- Historical completions predate the coordinate column and remain
    -- readable.  Every new completion must carry an explicit coordinate.
    IF NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 THEN
        RAISE EXCEPTION 'new stocktake posting completion requires request reference'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.{OUTCOMES}
        WHERE task_id = NEW.task_id
          AND request_reference = NEW.request_reference
          AND disposition = 'sealed_not_executed'
    ) THEN
        RAISE EXCEPTION 'stocktake posting command was sealed as not executed'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$
""")
        op.execute(f"REVOKE ALL ON FUNCTION public.{FUNCTION}() FROM PUBLIC")
        op.execute(
            f"CREATE TRIGGER {TRIGGER} BEFORE INSERT ON public.{TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}()"
        )
        op.execute(f"ALTER TABLE public.{TABLE} ENABLE ALWAYS TRIGGER {TRIGGER}")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {OLD_TRIGGER}_sqlite")
        op.execute(f"DROP TRIGGER IF EXISTS {OLD_BINDING_TRIGGER}_sqlite")
        op.execute(f"""
CREATE TRIGGER {SQLITE_BINDING_TRIGGER} BEFORE INSERT ON {OUTCOMES}
WHEN NEW.disposition = 'posted' AND (
  NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 OR NOT EXISTS (
    SELECT 1 FROM {TABLE}
    WHERE id = NEW.completion_id
      AND task_id = NEW.task_id
      AND expected_task_version = NEW.expected_task_version
      AND request_sha256 = NEW.request_sha256
      AND request_reference = NEW.request_reference
  )
)
BEGIN
  SELECT RAISE(ABORT, 'stocktake posting command outcome is not bound to its completion');
END
""")
        op.execute(f"""
CREATE TRIGGER {SQLITE_REQUIRED_TRIGGER} BEFORE INSERT ON {TABLE}
WHEN NEW.request_reference IS NULL OR length(NEW.request_reference) = 0
BEGIN
  SELECT RAISE(ABORT, 'new stocktake posting completion requires request reference');
END
""")
        op.execute(f"""
CREATE TRIGGER {SQLITE_TRIGGER} BEFORE INSERT ON {TABLE}
WHEN EXISTS (
      SELECT 1 FROM {OUTCOMES}
      WHERE task_id = NEW.task_id
        AND request_reference = NEW.request_reference
        AND disposition = 'sealed_not_executed'
  )
BEGIN
  SELECT RAISE(ABORT, 'stocktake posting command was sealed as not executed');
END
""")


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {BINDING_TRIGGER} ON public.{OUTCOMES}")
        op.execute(f"DROP FUNCTION IF EXISTS public.{BINDING_FUNCTION}()")
        op.execute(f"""
CREATE FUNCTION public.{OLD_BINDING_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.disposition = 'posted' AND NOT EXISTS (
        SELECT 1 FROM public.{TABLE}
        WHERE id = NEW.completion_id
          AND task_id = NEW.task_id
          AND expected_task_version = NEW.expected_task_version
          AND request_sha256 = NEW.request_sha256
    ) THEN
        RAISE EXCEPTION 'stocktake posting command outcome is not bound to its completion'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$
""")
        op.execute(f"REVOKE ALL ON FUNCTION public.{OLD_BINDING_FUNCTION}() FROM PUBLIC")
        op.execute(
            f"CREATE TRIGGER {OLD_BINDING_TRIGGER} BEFORE INSERT OR UPDATE ON public.{OUTCOMES} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{OLD_BINDING_FUNCTION}()"
        )
        op.execute(f"ALTER TABLE public.{OUTCOMES} ENABLE ALWAYS TRIGGER {OLD_BINDING_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON public.{TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS public.{FUNCTION}()")
        op.execute(f"""
CREATE FUNCTION public.{OLD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.{OUTCOMES}
        WHERE task_id = NEW.task_id AND disposition = 'sealed_not_executed'
    ) THEN
        RAISE EXCEPTION 'stocktake posting command was sealed as not executed'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$
""")
        op.execute(f"REVOKE ALL ON FUNCTION public.{OLD_FUNCTION}() FROM PUBLIC")
        op.execute(
            f"CREATE TRIGGER {OLD_TRIGGER} BEFORE INSERT ON public.{TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{OLD_FUNCTION}()"
        )
        op.execute(f"ALTER TABLE public.{TABLE} ENABLE ALWAYS TRIGGER {OLD_TRIGGER}")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_BINDING_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_REQUIRED_TRIGGER}")
        op.execute(f"""
CREATE TRIGGER {OLD_BINDING_TRIGGER}_sqlite BEFORE INSERT ON {OUTCOMES}
WHEN NEW.disposition = 'posted' AND NOT EXISTS (
  SELECT 1 FROM {TABLE}
  WHERE id = NEW.completion_id
    AND task_id = NEW.task_id
    AND expected_task_version = NEW.expected_task_version
    AND request_sha256 = NEW.request_sha256
)
BEGIN
  SELECT RAISE(ABORT, 'stocktake posting command outcome is not bound to its completion');
END
""")
        op.execute(f"""
CREATE TRIGGER {OLD_TRIGGER}_sqlite BEFORE INSERT ON {TABLE}
WHEN EXISTS (
  SELECT 1 FROM {OUTCOMES}
  WHERE task_id = NEW.task_id AND disposition = 'sealed_not_executed'
)
BEGIN
  SELECT RAISE(ABORT, 'stocktake posting command was sealed as not executed');
END
""")
    op.drop_column(TABLE, "request_reference")
