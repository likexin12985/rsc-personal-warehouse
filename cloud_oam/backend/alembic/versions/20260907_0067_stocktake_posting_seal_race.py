"""Serialize direct SQL seal/completion races on the stocktake task row.

Revision 0067
Revises: 20260906_0066

The service already takes the posting graph lock.  These database triggers are
the final guard for direct SQL writers, so both the completion and outcome
triggers lock the task row before checking the exact request coordinate.
"""

from __future__ import annotations

from alembic import op


revision: str = "20260907_0067"
down_revision: str | None = "20260906_0066"
branch_labels: str | None = None
depends_on: str | None = None

COMPLETION_TRIGGER_OLD = "trg_stocktake_posting_completions_block_sealed_0066"
COMPLETION_FUNCTION_OLD = "rsc_guard_stocktake_posting_completion_sealed_0066"
BINDING_TRIGGER_OLD = "trg_stocktake_posting_completions_block_sealed_0066_binding"
BINDING_FUNCTION_OLD = "rsc_guard_stocktake_posting_completion_sealed_0066_binding"
COMPLETION_TRIGGER = "trg_stocktake_posting_completions_block_sealed_0067"
COMPLETION_FUNCTION = "rsc_guard_stocktake_posting_completion_sealed_0067"
BINDING_TRIGGER = "trg_stocktake_posting_completions_block_sealed_0067_binding"
BINDING_FUNCTION = "rsc_guard_stocktake_posting_completion_sealed_0067_binding"


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0067 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "LOCK TABLE public.stocktake_tasks, public.stocktake_posting_completions, "
            "public.stocktake_posting_command_outcomes IN ACCESS EXCLUSIVE MODE"
        )
        op.execute(f"DROP TRIGGER IF EXISTS {BINDING_TRIGGER_OLD} ON public.stocktake_posting_command_outcomes")
        op.execute(f"DROP FUNCTION IF EXISTS public.{BINDING_FUNCTION_OLD}()")
        op.execute(f"DROP TRIGGER IF EXISTS {COMPLETION_TRIGGER_OLD} ON public.stocktake_posting_completions")
        op.execute(f"DROP FUNCTION IF EXISTS public.{COMPLETION_FUNCTION_OLD}()")
        op.execute(f"""
CREATE FUNCTION public.{BINDING_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM 1 FROM public.stocktake_tasks WHERE id = NEW.task_id FOR UPDATE;
    IF NEW.disposition = 'posted' AND (
        NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 OR NOT EXISTS (
            SELECT 1 FROM public.stocktake_posting_completions
            WHERE id = NEW.completion_id AND task_id = NEW.task_id
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
        op.execute(f"CREATE TRIGGER {BINDING_TRIGGER} BEFORE INSERT OR UPDATE ON public.stocktake_posting_command_outcomes FOR EACH ROW EXECUTE FUNCTION public.{BINDING_FUNCTION}()")
        op.execute(f"ALTER TABLE public.stocktake_posting_command_outcomes ENABLE ALWAYS TRIGGER {BINDING_TRIGGER}")
        op.execute(f"""
CREATE FUNCTION public.{COMPLETION_FUNCTION}()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM 1 FROM public.stocktake_tasks WHERE id = NEW.task_id FOR UPDATE;
    IF NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 THEN
        RAISE EXCEPTION 'new stocktake posting completion requires request reference'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.stocktake_posting_command_outcomes
        WHERE task_id = NEW.task_id AND request_reference = NEW.request_reference
          AND disposition = 'sealed_not_executed'
    ) THEN
        RAISE EXCEPTION 'stocktake posting command was sealed as not executed'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$
""")
        op.execute(f"REVOKE ALL ON FUNCTION public.{COMPLETION_FUNCTION}() FROM PUBLIC")
        op.execute(f"CREATE TRIGGER {COMPLETION_TRIGGER} BEFORE INSERT ON public.stocktake_posting_completions FOR EACH ROW EXECUTE FUNCTION public.{COMPLETION_FUNCTION}()")
        op.execute(f"ALTER TABLE public.stocktake_posting_completions ENABLE ALWAYS TRIGGER {COMPLETION_TRIGGER}")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {BINDING_TRIGGER_OLD}_sqlite")
        op.execute(f"DROP TRIGGER IF EXISTS {COMPLETION_TRIGGER_OLD}_sqlite")
        op.execute(f"DROP TRIGGER IF EXISTS {BINDING_TRIGGER_OLD}_required_reference_sqlite")
        op.execute(f"""CREATE TRIGGER {BINDING_TRIGGER}_sqlite BEFORE INSERT ON stocktake_posting_command_outcomes
WHEN NEW.disposition = 'posted' AND (NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 OR NOT EXISTS
(SELECT 1 FROM stocktake_posting_completions WHERE id = NEW.completion_id AND task_id = NEW.task_id AND expected_task_version = NEW.expected_task_version AND request_sha256 = NEW.request_sha256 AND request_reference = NEW.request_reference))
BEGIN SELECT RAISE(ABORT, 'stocktake posting command outcome is not bound to its completion'); END""")
        op.execute(f"""CREATE TRIGGER {COMPLETION_TRIGGER}_required_reference_sqlite BEFORE INSERT ON stocktake_posting_completions
WHEN NEW.request_reference IS NULL OR length(NEW.request_reference) = 0
BEGIN SELECT RAISE(ABORT, 'new stocktake posting completion requires request reference'); END""")
        op.execute(f"""CREATE TRIGGER {COMPLETION_TRIGGER}_sqlite BEFORE INSERT ON stocktake_posting_completions
WHEN EXISTS (SELECT 1 FROM stocktake_posting_command_outcomes WHERE task_id = NEW.task_id AND request_reference = NEW.request_reference AND disposition = 'sealed_not_executed')
BEGIN SELECT RAISE(ABORT, 'stocktake posting command was sealed as not executed'); END""")


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {BINDING_TRIGGER} ON public.stocktake_posting_command_outcomes")
        op.execute(f"DROP FUNCTION IF EXISTS public.{BINDING_FUNCTION}()")
        op.execute(f"DROP TRIGGER IF EXISTS {COMPLETION_TRIGGER} ON public.stocktake_posting_completions")
        op.execute(f"DROP FUNCTION IF EXISTS public.{COMPLETION_FUNCTION}()")
        op.execute(f"""CREATE FUNCTION public.{BINDING_FUNCTION_OLD}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.disposition = 'posted' AND (NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 OR NOT EXISTS (SELECT 1 FROM public.stocktake_posting_completions WHERE id = NEW.completion_id AND task_id = NEW.task_id AND expected_task_version = NEW.expected_task_version AND request_sha256 = NEW.request_sha256 AND request_reference = NEW.request_reference)) THEN
        RAISE EXCEPTION 'stocktake posting command outcome is not bound to its completion' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$""")
        op.execute(f"CREATE TRIGGER {BINDING_TRIGGER_OLD} BEFORE INSERT OR UPDATE ON public.stocktake_posting_command_outcomes FOR EACH ROW EXECUTE FUNCTION public.{BINDING_FUNCTION_OLD}()")
        op.execute(f"ALTER TABLE public.stocktake_posting_command_outcomes ENABLE ALWAYS TRIGGER {BINDING_TRIGGER_OLD}")
        op.execute(f"""CREATE FUNCTION public.{COMPLETION_FUNCTION_OLD}() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 THEN RAISE EXCEPTION 'new stocktake posting completion requires request reference' USING ERRCODE = '23514'; END IF;
    IF EXISTS (SELECT 1 FROM public.stocktake_posting_command_outcomes WHERE task_id = NEW.task_id AND request_reference = NEW.request_reference AND disposition = 'sealed_not_executed') THEN RAISE EXCEPTION 'stocktake posting command was sealed as not executed' USING ERRCODE = '55000'; END IF;
    RETURN NEW;
END
$$""")
        op.execute(f"CREATE TRIGGER {COMPLETION_TRIGGER_OLD} BEFORE INSERT ON public.stocktake_posting_completions FOR EACH ROW EXECUTE FUNCTION public.{COMPLETION_FUNCTION_OLD}()")
        op.execute(f"ALTER TABLE public.stocktake_posting_completions ENABLE ALWAYS TRIGGER {COMPLETION_TRIGGER_OLD}")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {BINDING_TRIGGER}_sqlite")
        op.execute(f"DROP TRIGGER IF EXISTS {COMPLETION_TRIGGER}_sqlite")
        op.execute(f"DROP TRIGGER IF EXISTS {COMPLETION_TRIGGER}_required_reference_sqlite")
        op.execute(f"""CREATE TRIGGER {BINDING_TRIGGER_OLD}_sqlite BEFORE INSERT ON stocktake_posting_command_outcomes WHEN NEW.disposition = 'posted' AND (NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 OR NOT EXISTS (SELECT 1 FROM stocktake_posting_completions WHERE id = NEW.completion_id AND task_id = NEW.task_id AND expected_task_version = NEW.expected_task_version AND request_sha256 = NEW.request_sha256 AND request_reference = NEW.request_reference)) BEGIN SELECT RAISE(ABORT, 'stocktake posting command outcome is not bound to its completion'); END""")
        op.execute(f"""CREATE TRIGGER {COMPLETION_TRIGGER_OLD}_sqlite BEFORE INSERT ON stocktake_posting_completions WHEN NEW.request_reference IS NULL OR length(NEW.request_reference) = 0 OR EXISTS (SELECT 1 FROM stocktake_posting_command_outcomes WHERE task_id = NEW.task_id AND request_reference = NEW.request_reference AND disposition = 'sealed_not_executed') BEGIN SELECT RAISE(ABORT, 'stocktake posting command was sealed as not executed'); END""")
