"""Seal non-opening recount rounds against their exact selected assignments.

Revision ID: 20260901_0034
Revises: 20260901_0033
Create Date: 2026-09-01

Revision 0021 binds every later-round count fact to its immutable recount
assignment, but the older 0011 round-submission guard still requires every
task scope.  That makes a legitimate partial-scope recount impossible.  This
revision keeps round-one and opening-stocktake semantics unchanged while
requiring a non-opening recount completion/submission graph to equal, without
missing or extra scopes, the current round's immutable recount assignments.

No business fact is rewritten and no runtime DML privilege is granted.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260901_0034"
down_revision: Union[str, Sequence[str], None] = "20260901_0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
OLD_PG_FUNCTION = "rsc_validate_stocktake_round_submission_insert_0011"
OLD_ROUND_TRIGGER = "trg_stocktake_round_submissions_validate_insert_0011"
PG_COMPLETION_FUNCTION = "rsc_validate_recount_completion_scope_0034"
PG_COMPLETION_TRIGGER = "trg_stocktake_recount_completion_scope_0034"
PG_ROUND_FUNCTION = "rsc_validate_stocktake_round_submission_insert_0034"
PG_ROUND_TRIGGER = "trg_stocktake_round_submissions_validate_insert_0034"
SQLITE_COMPLETION_TRIGGER = PG_COMPLETION_TRIGGER
SQLITE_ROUND_TRIGGER = PG_ROUND_TRIGGER
PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
UPGRADE_BLOCKER = (
    "0034 preflight failed: existing stocktake completion/submission scopes "
    "do not equal their immutable round assignment graph"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0034 while non-opening recount count facts exist"
)


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0034 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0034 SQLite upgrade requires an online connection")
        _postgresql_lock_and_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_graph()
        _online_preflight(dialect)

    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER {OLD_ROUND_TRIGGER} "
            "ON public.stocktake_round_submissions"
        )
        op.execute(f"DROP FUNCTION public.{OLD_PG_FUNCTION}()")
        op.execute(_postgresql_completion_function_sql())
        op.execute(_postgresql_round_submission_function_sql(round_aware=True))
        op.execute(
            f"CREATE TRIGGER {PG_COMPLETION_TRIGGER} BEFORE INSERT ON "
            "public.stocktake_scope_count_completions FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_COMPLETION_FUNCTION}()"
        )
        op.execute(
            f"CREATE TRIGGER {PG_ROUND_TRIGGER} BEFORE INSERT ON "
            "public.stocktake_round_submissions FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_ROUND_FUNCTION}()"
        )
        op.execute(
            "ALTER TABLE public.stocktake_scope_count_completions ENABLE ALWAYS "
            f"TRIGGER {PG_COMPLETION_TRIGGER}"
        )
        op.execute(
            "ALTER TABLE public.stocktake_round_submissions ENABLE ALWAYS "
            f"TRIGGER {PG_ROUND_TRIGGER}"
        )
        _apply_postgresql_acl()
        return

    op.execute(f"DROP TRIGGER {OLD_ROUND_TRIGGER}")
    op.execute(_sqlite_completion_trigger_sql())
    op.execute(_sqlite_round_submission_trigger_sql(round_aware=True))


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0034 downgrade requires an online evidence check")
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_graph()
    prefix = "public." if dialect == "postgresql" else ""
    if op.get_bind().exec_driver_sql(
        f"""
SELECT 1
  FROM {prefix}stocktake_rounds AS round_row
  JOIN {prefix}stocktake_tasks AS task ON task.id = round_row.task_id
 WHERE task.task_type IN {NONOPENING_SQL}
   AND round_row.round_no > 1
   AND (EXISTS (
        SELECT 1 FROM {prefix}stocktake_scope_count_completions AS completion
         WHERE completion.task_id = task.id
           AND completion.round_id = round_row.id)
        OR EXISTS (
        SELECT 1 FROM {prefix}stocktake_round_submissions AS submission
         WHERE submission.task_id = task.id
           AND submission.round_id = round_row.id))
 LIMIT 1
"""
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)

    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER {PG_ROUND_TRIGGER} "
            "ON public.stocktake_round_submissions"
        )
        op.execute(
            f"DROP TRIGGER {PG_COMPLETION_TRIGGER} "
            "ON public.stocktake_scope_count_completions"
        )
        op.execute(f"DROP FUNCTION public.{PG_ROUND_FUNCTION}()")
        op.execute(f"DROP FUNCTION public.{PG_COMPLETION_FUNCTION}()")
        op.execute(_postgresql_round_submission_function_sql(round_aware=False))
        op.execute(
            f"CREATE TRIGGER {OLD_ROUND_TRIGGER} BEFORE INSERT ON "
            "public.stocktake_round_submissions FOR EACH ROW "
            f"EXECUTE FUNCTION public.{OLD_PG_FUNCTION}()"
        )
        op.execute(
            "ALTER TABLE public.stocktake_round_submissions ENABLE ALWAYS "
            f"TRIGGER {OLD_ROUND_TRIGGER}"
        )
        _apply_old_postgresql_acl()
        return

    op.execute(f"DROP TRIGGER {SQLITE_ROUND_TRIGGER}")
    op.execute(f"DROP TRIGGER {SQLITE_COMPLETION_TRIGGER}")
    op.execute(_sqlite_round_submission_trigger_sql(round_aware=False))


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_graph() -> None:
    op.get_bind().exec_driver_sql(
        "LOCK TABLE public.stocktake_tasks, public.stocktake_rounds, "
        "public.stocktake_scopes, public.stocktake_recount_cases, "
        "public.stocktake_recount_scope_assignments, "
        "public.stocktake_scope_count_completions, "
        "public.stocktake_round_submissions, public.role_assignments, "
        "public.roles, public.users IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_lock_and_preflight() -> None:
    op.execute(
        "LOCK TABLE public.stocktake_tasks, public.stocktake_rounds, "
        "public.stocktake_scopes, public.stocktake_recount_cases, "
        "public.stocktake_recount_scope_assignments, "
        "public.stocktake_scope_count_completions, "
        "public.stocktake_round_submissions, public.role_assignments, "
        "public.roles, public.users IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF {_invalid_existing_graph_sql('postgresql', prefix='public.')} THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_preflight(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {_invalid_existing_graph_sql(dialect, prefix=prefix)}"
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _uuid_equal(dialect: str, left: str, right: str) -> str:
    if dialect == "postgresql":
        return f"{left} = {right}"
    return (
        f"replace(CAST({left} AS TEXT), '-', '') = "
        f"replace(CAST({right} AS TEXT), '-', '')"
    )


def _invalid_existing_graph_sql(dialect: str, *, prefix: str) -> str:
    tasks = f"{prefix}stocktake_tasks"
    rounds = f"{prefix}stocktake_rounds"
    scopes = f"{prefix}stocktake_scopes"
    cases = f"{prefix}stocktake_recount_cases"
    assignments = f"{prefix}stocktake_recount_scope_assignments"
    completions = f"{prefix}stocktake_scope_count_completions"
    submissions = f"{prefix}stocktake_round_submissions"
    eq = lambda left, right: _uuid_equal(dialect, left, right)
    return f"""EXISTS (
        SELECT 1
          FROM {completions} AS completion
          JOIN {rounds} AS round_row
            ON round_row.id = completion.round_id
           AND round_row.task_id = completion.task_id
          JOIN {tasks} AS task ON task.id = round_row.task_id
         WHERE task.task_type IN {NONOPENING_SQL}
           AND round_row.round_no > 1
           AND (
                round_row.round_type <> 'recount'
                OR round_row.recount_case_id IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM {cases} AS recount_case
                     WHERE {eq('recount_case.id', 'round_row.recount_case_id')}
                       AND recount_case.task_id = task.id
                       AND recount_case.next_round_no = round_row.round_no
                       AND recount_case.scope_count > 0
                       AND recount_case.scope_count = (
                           SELECT count(*) FROM {assignments} AS assignment
                            WHERE {eq('assignment.recount_case_id', 'recount_case.id')}
                              AND assignment.task_id = task.id))
                OR NOT EXISTS (
                    SELECT 1 FROM {assignments} AS assignment
                     WHERE {eq('assignment.recount_case_id', 'round_row.recount_case_id')}
                       AND assignment.task_id = task.id
                       AND assignment.scope_id = completion.scope_id)
           )
    ) OR EXISTS (
        SELECT 1
          FROM {rounds} AS round_row
          JOIN {tasks} AS task ON task.id = round_row.task_id
         WHERE EXISTS (
               SELECT 1 FROM {submissions} AS submission
                WHERE submission.task_id = task.id
                  AND submission.round_id = round_row.id)
           AND (
             (task.task_type IN {NONOPENING_SQL}
              AND round_row.round_no > 1
              AND (
                round_row.round_type <> 'recount'
                OR round_row.recount_case_id IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM {cases} AS recount_case
                     WHERE {eq('recount_case.id', 'round_row.recount_case_id')}
                       AND recount_case.task_id = task.id
                       AND recount_case.next_round_no = round_row.round_no
                       AND recount_case.scope_count > 0
                       AND recount_case.scope_count = (
                           SELECT count(*) FROM {assignments} AS assignment
                            WHERE {eq('assignment.recount_case_id', 'recount_case.id')}
                              AND assignment.task_id = task.id)
                )
                OR (SELECT count(*) FROM {completions} AS completion
                     WHERE completion.task_id = task.id
                       AND completion.round_id = round_row.id) <>
                   (SELECT count(*) FROM {assignments} AS assignment
                     WHERE {eq('assignment.recount_case_id', 'round_row.recount_case_id')}
                       AND assignment.task_id = task.id)
                OR EXISTS (
                    SELECT 1 FROM {assignments} AS assignment
                     WHERE {eq('assignment.recount_case_id', 'round_row.recount_case_id')}
                       AND assignment.task_id = task.id
                       AND NOT EXISTS (
                           SELECT 1 FROM {completions} AS completion
                            WHERE completion.task_id = task.id
                              AND completion.round_id = round_row.id
                              AND completion.scope_id = assignment.scope_id))
                OR EXISTS (
                    SELECT 1 FROM {completions} AS completion
                     WHERE completion.task_id = task.id
                       AND completion.round_id = round_row.id
                       AND NOT EXISTS (
                           SELECT 1 FROM {assignments} AS assignment
                            WHERE {eq('assignment.recount_case_id', 'round_row.recount_case_id')}
                              AND assignment.task_id = task.id
                              AND assignment.scope_id = completion.scope_id))
              ))
             OR
             ((task.task_type NOT IN {NONOPENING_SQL}
               OR round_row.round_no = 1)
              AND (
                (SELECT count(*) FROM {scopes} AS scope
                  WHERE scope.task_id = task.id) = 0
                OR (SELECT count(*) FROM {completions} AS completion
                     WHERE completion.task_id = task.id
                       AND completion.round_id = round_row.id) <>
                   (SELECT count(*) FROM {scopes} AS scope
                     WHERE scope.task_id = task.id)
                OR EXISTS (
                    SELECT 1 FROM {scopes} AS scope
                     WHERE scope.task_id = task.id
                       AND NOT EXISTS (
                           SELECT 1 FROM {completions} AS completion
                            WHERE completion.task_id = task.id
                              AND completion.round_id = round_row.id
                              AND completion.scope_id = scope.id))
              ))
           )
    )"""


def _postgresql_completion_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_COMPLETION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    task_kind text;
    round_number integer;
    round_kind text;
    case_id uuid;
    case_scope_count integer;
    assignment_count integer;
BEGIN
    SELECT task.task_type, round_row.round_no, round_row.round_type,
           round_row.recount_case_id
      INTO task_kind, round_number, round_kind, case_id
      FROM public.stocktake_rounds AS round_row
      JOIN public.stocktake_tasks AS task ON task.id = round_row.task_id
     WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stocktake recount completion parent is invalid';
    END IF;
    IF task_kind NOT IN {NONOPENING_SQL} OR round_number = 1 THEN
        RETURN NEW;
    END IF;
    IF round_number <= 1 OR round_kind <> 'recount' OR case_id IS NULL THEN
        RAISE EXCEPTION 'non-opening recount completion round is invalid';
    END IF;
    SELECT recount_case.scope_count INTO case_scope_count
      FROM public.stocktake_recount_cases AS recount_case
     WHERE recount_case.id = case_id
       AND recount_case.task_id = NEW.task_id
       AND recount_case.next_round_no = round_number;
    SELECT count(*) INTO assignment_count
      FROM public.stocktake_recount_scope_assignments AS assignment
     WHERE assignment.recount_case_id = case_id
       AND assignment.task_id = NEW.task_id;
    IF case_scope_count IS NULL OR case_scope_count <= 0
       OR case_scope_count <> assignment_count
       OR NOT EXISTS (
           SELECT 1
             FROM public.stocktake_recount_scope_assignments AS assignment
            WHERE assignment.recount_case_id = case_id
              AND assignment.task_id = NEW.task_id
              AND assignment.scope_id = NEW.scope_id)
    THEN
        RAISE EXCEPTION 'non-opening recount completion is outside selected assignments';
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_round_submission_function_sql(*, round_aware: bool) -> str:
    function_name = PG_ROUND_FUNCTION if round_aware else OLD_PG_FUNCTION
    selected_branch = f"""
    IF task_kind IN {NONOPENING_SQL} AND round_number > 1 THEN
        IF round_kind <> 'recount' OR case_id IS NULL THEN
            RAISE EXCEPTION 'non-opening recount submission round is invalid';
        END IF;
        SELECT recount_case.scope_count INTO case_scope_count
          FROM public.stocktake_recount_cases AS recount_case
         WHERE recount_case.id = case_id
           AND recount_case.task_id = NEW.task_id
           AND recount_case.next_round_no = round_number;
        SELECT count(*) INTO required_scope_count
          FROM public.stocktake_recount_scope_assignments AS assignment
         WHERE assignment.recount_case_id = case_id
           AND assignment.task_id = NEW.task_id;
        IF case_scope_count IS NULL OR case_scope_count <= 0
           OR case_scope_count <> required_scope_count
           OR EXISTS (
                SELECT 1
                  FROM public.stocktake_recount_scope_assignments AS assignment
                 WHERE assignment.recount_case_id = case_id
                   AND assignment.task_id = NEW.task_id
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_scope_count_completions AS completion
                        WHERE completion.task_id = NEW.task_id
                          AND completion.round_id = NEW.round_id
                          AND completion.scope_id = assignment.scope_id))
           OR EXISTS (
                SELECT 1
                  FROM public.stocktake_scope_count_completions AS completion
                 WHERE completion.task_id = NEW.task_id
                   AND completion.round_id = NEW.round_id
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_recount_scope_assignments AS assignment
                        WHERE assignment.recount_case_id = case_id
                          AND assignment.task_id = NEW.task_id
                          AND assignment.scope_id = completion.scope_id))
        THEN
            RAISE EXCEPTION 'non-opening recount submission requires exact selected scopes';
        END IF;
    ELSE
        SELECT count(*) INTO required_scope_count
          FROM public.stocktake_scopes WHERE task_id = NEW.task_id;
        IF required_scope_count = 0 OR EXISTS (
            SELECT 1 FROM public.stocktake_scopes AS scope
             WHERE scope.task_id = NEW.task_id
               AND NOT EXISTS (
                   SELECT 1 FROM public.stocktake_scope_count_completions AS completion
                    WHERE completion.task_id = NEW.task_id
                      AND completion.round_id = NEW.round_id
                      AND completion.scope_id = scope.id))
        THEN
            RAISE EXCEPTION 'stocktake round submission requires every scope completion';
        END IF;
    END IF;
""" if round_aware else """
    SELECT count(*) INTO required_scope_count
      FROM public.stocktake_scopes WHERE task_id = NEW.task_id;
    IF required_scope_count = 0 OR EXISTS (
        SELECT 1 FROM public.stocktake_scopes AS scope
         WHERE scope.task_id = NEW.task_id
           AND NOT EXISTS (
               SELECT 1 FROM public.stocktake_scope_count_completions AS completion
                WHERE completion.task_id = NEW.task_id
                  AND completion.round_id = NEW.round_id
                  AND completion.scope_id = scope.id))
    THEN
        RAISE EXCEPTION 'stocktake round submission requires every scope completion';
    END IF;
"""
    return f"""
CREATE FUNCTION public.{function_name}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    parent_status text;
    parent_started_at timestamptz;
    task_kind text;
    round_number integer;
    round_kind text;
    case_id uuid;
    case_scope_count integer;
    required_scope_count integer;
    actual_scope_count integer;
    actual_zero_count integer;
    actual_count_lines integer;
    actual_observations integer;
    actual_serials integer;
    actual_total numeric(18, 3);
    latest_completion_at timestamptz;
BEGIN
    SELECT round_row.status, round_row.started_at, task.task_type,
           round_row.round_no, round_row.round_type, round_row.recount_case_id
      INTO parent_status, parent_started_at, task_kind,
           round_number, round_kind, case_id
      FROM public.stocktake_rounds AS round_row
      JOIN public.stocktake_tasks AS task ON task.id = round_row.task_id
     WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
     FOR UPDATE OF round_row;
    IF parent_status IS DISTINCT FROM 'counting'
       OR NEW.submitted_at < parent_started_at
       OR NOT public.rsc_stocktake_actor_assignment_valid_0011(
           NEW.submitted_by_user_id, NEW.submitted_by_person_id,
           NEW.submitted_role_assignment_id, NEW.authorization_version,
           NEW.submitted_at, NULL, NULL, NULL)
       OR NOT EXISTS (
           SELECT 1 FROM public.stocktake_scope_count_completions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
              AND completed_by_user_id = NEW.submitted_by_user_id
              AND completed_by_person_id = NEW.submitted_by_person_id)
    THEN
        RAISE EXCEPTION 'stocktake round submission actor or chronology is invalid';
    END IF;
{selected_branch}
    SELECT count(*), count(*) FILTER (WHERE zero_confirmed),
           COALESCE(sum(count_line_count), 0),
           COALESCE(sum(observation_line_count), 0),
           COALESCE(sum(serial_count), 0),
           COALESCE(sum(total_counted_qty), 0), max(completed_at)
      INTO actual_scope_count, actual_zero_count, actual_count_lines,
           actual_observations, actual_serials, actual_total, latest_completion_at
      FROM public.stocktake_scope_count_completions
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id;
    IF actual_scope_count <> required_scope_count
       OR NEW.scope_count <> actual_scope_count
       OR NEW.zero_scope_count <> actual_zero_count
       OR NEW.count_line_count <> actual_count_lines
       OR NEW.observation_line_count <> actual_observations
       OR NEW.serial_count <> actual_serials
       OR NEW.total_counted_qty <> actual_total
       OR NEW.submitted_at < latest_completion_at
    THEN
        RAISE EXCEPTION 'stocktake round submission totals are not canonical';
    END IF;
    RETURN NEW;
END
$$
"""


def _sqlite_completion_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_COMPLETION_TRIGGER}
BEFORE INSERT ON stocktake_scope_count_completions
WHEN COALESCE((
        SELECT task.task_type IN {NONOPENING_SQL}
               AND round_row.round_no > 1
          FROM stocktake_rounds AS round_row
          JOIN stocktake_tasks AS task ON task.id = round_row.task_id
         WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
    ), 0) = 1
 AND (
      COALESCE((SELECT round_row.round_type FROM stocktake_rounds AS round_row
                 WHERE round_row.id = NEW.round_id
                   AND round_row.task_id = NEW.task_id), 'invalid') <> 'recount'
      OR (SELECT round_row.recount_case_id FROM stocktake_rounds AS round_row
           WHERE round_row.id = NEW.round_id
             AND round_row.task_id = NEW.task_id) IS NULL
      OR NOT EXISTS (
          SELECT 1
            FROM stocktake_rounds AS round_row
            JOIN stocktake_recount_cases AS recount_case
              ON recount_case.id = round_row.recount_case_id
             AND recount_case.task_id = NEW.task_id
             AND recount_case.next_round_no = round_row.round_no
           WHERE round_row.id = NEW.round_id
             AND round_row.task_id = NEW.task_id
             AND recount_case.scope_count > 0
             AND recount_case.scope_count = (
                 SELECT count(*)
                   FROM stocktake_recount_scope_assignments AS assignment
                  WHERE assignment.recount_case_id = recount_case.id
                    AND assignment.task_id = NEW.task_id)
             AND EXISTS (
                 SELECT 1
                   FROM stocktake_recount_scope_assignments AS assignment
                  WHERE assignment.recount_case_id = recount_case.id
                    AND assignment.task_id = NEW.task_id
                    AND assignment.scope_id = NEW.scope_id)
      )
 )
BEGIN
    SELECT RAISE(ABORT,
        'non-opening recount completion is outside selected assignments');
END
"""


def _sqlite_round_submission_trigger_sql(*, round_aware: bool) -> str:
    selected_guard = f"""
 OR (
      COALESCE((SELECT task.task_type FROM stocktake_tasks AS task
                 WHERE task.id = NEW.task_id), 'invalid') IN {NONOPENING_SQL}
      AND COALESCE((SELECT round_row.round_no FROM stocktake_rounds AS round_row
                     WHERE round_row.id = NEW.round_id
                       AND round_row.task_id = NEW.task_id), 0) > 1
      AND (
        COALESCE((SELECT round_row.round_type FROM stocktake_rounds AS round_row
                   WHERE round_row.id = NEW.round_id
                     AND round_row.task_id = NEW.task_id), 'invalid') <> 'recount'
        OR (SELECT round_row.recount_case_id FROM stocktake_rounds AS round_row
             WHERE round_row.id = NEW.round_id
               AND round_row.task_id = NEW.task_id) IS NULL
        OR NOT EXISTS (
            SELECT 1
              FROM stocktake_rounds AS round_row
              JOIN stocktake_recount_cases AS recount_case
                ON recount_case.id = round_row.recount_case_id
               AND recount_case.task_id = NEW.task_id
               AND recount_case.next_round_no = round_row.round_no
             WHERE round_row.id = NEW.round_id
               AND round_row.task_id = NEW.task_id
               AND recount_case.scope_count > 0
               AND recount_case.scope_count = (
                   SELECT count(*)
                     FROM stocktake_recount_scope_assignments AS assignment
                    WHERE assignment.recount_case_id = recount_case.id
                      AND assignment.task_id = NEW.task_id))
        OR (SELECT count(*) FROM stocktake_scope_count_completions AS completion
             WHERE completion.task_id = NEW.task_id
               AND completion.round_id = NEW.round_id) <>
           (SELECT count(*) FROM stocktake_recount_scope_assignments AS assignment
             WHERE assignment.recount_case_id = (
                   SELECT round_row.recount_case_id FROM stocktake_rounds AS round_row
                    WHERE round_row.id = NEW.round_id
                      AND round_row.task_id = NEW.task_id)
               AND assignment.task_id = NEW.task_id)
        OR EXISTS (
            SELECT 1 FROM stocktake_recount_scope_assignments AS assignment
             WHERE assignment.recount_case_id = (
                   SELECT round_row.recount_case_id FROM stocktake_rounds AS round_row
                    WHERE round_row.id = NEW.round_id
                      AND round_row.task_id = NEW.task_id)
               AND assignment.task_id = NEW.task_id
               AND NOT EXISTS (
                   SELECT 1 FROM stocktake_scope_count_completions AS completion
                    WHERE completion.task_id = NEW.task_id
                      AND completion.round_id = NEW.round_id
                      AND completion.scope_id = assignment.scope_id))
        OR EXISTS (
            SELECT 1 FROM stocktake_scope_count_completions AS completion
             WHERE completion.task_id = NEW.task_id
               AND completion.round_id = NEW.round_id
               AND NOT EXISTS (
                   SELECT 1 FROM stocktake_recount_scope_assignments AS assignment
                    WHERE assignment.recount_case_id = (
                          SELECT round_row.recount_case_id FROM stocktake_rounds AS round_row
                           WHERE round_row.id = NEW.round_id
                             AND round_row.task_id = NEW.task_id)
                      AND assignment.task_id = NEW.task_id
                      AND assignment.scope_id = completion.scope_id))
      )
 )
 OR (
      (COALESCE((SELECT task.task_type FROM stocktake_tasks AS task
                  WHERE task.id = NEW.task_id), 'invalid') NOT IN {NONOPENING_SQL}
       OR COALESCE((SELECT round_row.round_no FROM stocktake_rounds AS round_row
                     WHERE round_row.id = NEW.round_id
                       AND round_row.task_id = NEW.task_id), 0) = 1)
      AND (
        (SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.task_id) = 0
        OR (SELECT count(*) FROM stocktake_scope_count_completions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id) <>
           (SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.task_id)
        OR EXISTS (
            SELECT 1 FROM stocktake_scopes AS scope
             WHERE scope.task_id = NEW.task_id
               AND NOT EXISTS (
                   SELECT 1 FROM stocktake_scope_count_completions AS completion
                    WHERE completion.task_id = NEW.task_id
                      AND completion.round_id = NEW.round_id
                      AND completion.scope_id = scope.id))
      )
 )
""" if round_aware else """
 OR (SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.task_id) = 0
 OR EXISTS (
        SELECT 1 FROM stocktake_scopes AS scope
         WHERE scope.task_id = NEW.task_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_scope_count_completions AS completion
                WHERE completion.task_id = NEW.task_id
                  AND completion.round_id = NEW.round_id
                  AND completion.scope_id = scope.id))
"""
    trigger_name = SQLITE_ROUND_TRIGGER if round_aware else OLD_ROUND_TRIGGER
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON stocktake_round_submissions
WHEN COALESCE((SELECT status FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
       <> 'counting'
 OR NEW.submitted_at < (SELECT started_at FROM stocktake_rounds
                         WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR NOT EXISTS (
        SELECT 1
          FROM role_assignments AS assignment
          JOIN roles AS role ON role.id = assignment.role_id
          JOIN users AS actor ON actor.id = assignment.user_id
         WHERE assignment.id = NEW.submitted_role_assignment_id
           AND assignment.user_id = NEW.submitted_by_user_id
           AND actor.person_id = NEW.submitted_by_person_id
           AND actor.authorization_version = NEW.authorization_version
           AND assignment.status IN ('active', 'expired', 'revoked')
           AND assignment.valid_from <= NEW.submitted_at
           AND (assignment.valid_to IS NULL OR NEW.submitted_at < assignment.valid_to)
           AND (assignment.revoked_at IS NULL OR NEW.submitted_at < assignment.revoked_at)
           AND role.is_external = 0
           AND role.code IN ('admin', 'provincial_manager', 'technician'))
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_scope_count_completions
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND completed_by_user_id = NEW.submitted_by_user_id
           AND completed_by_person_id = NEW.submitted_by_person_id)
{selected_guard}
 OR NEW.scope_count <> (SELECT count(*) FROM stocktake_scope_count_completions
                          WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NEW.zero_scope_count <> (SELECT count(*) FROM stocktake_scope_count_completions
                              WHERE task_id = NEW.task_id AND round_id = NEW.round_id
                                AND zero_confirmed = 1)
 OR NEW.count_line_count <> COALESCE((SELECT sum(count_line_count)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR NEW.observation_line_count <> COALESCE((SELECT sum(observation_line_count)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR NEW.serial_count <> COALESCE((SELECT sum(serial_count)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR NEW.total_counted_qty <> COALESCE((SELECT sum(total_counted_qty)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR NEW.submitted_at < (SELECT max(completed_at)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id)
BEGIN
    SELECT RAISE(ABORT, 'stocktake round submission is not canonical');
END
"""


def _apply_postgresql_acl() -> None:
    for function_name in (PG_COMPLETION_FUNCTION, PG_ROUND_FUNCTION):
        signature = f"public.{function_name}()"
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


def _apply_old_postgresql_acl() -> None:
    signature = f"public.{OLD_PG_FUNCTION}()"
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
