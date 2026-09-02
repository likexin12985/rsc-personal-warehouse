"""Guard non-opening stocktake review and append-only recount graphs.

Revision ID: 20260901_0032
Revises: 20260901_0031
Create Date: 2026-09-01

Opening-stocktake behavior remains on its historical all-scope recount path.
For full/sample/ad_hoc/personal/termination tasks this revision adds a
two-stage immutable review graph and allows a recount successor to contain
only the exact scopes selected by the terminal per-difference decisions.

No inventory, posting, notification, outbox or other business rows are
created or rewritten by this structural migration, and no runtime table DML
privilege is granted.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260901_0032"
down_revision: Union[str, Sequence[str], None] = "20260901_0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
MAXIMUM_LOCK_ROWS = 100_000
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"

OLD_CASE_TRIGGER = "trg_stocktake_recount_cases_review_path_0021"
OLD_TASK_TRIGGER = "trg_stocktake_tasks_recount_causality_0018"
OLD_ROUND_TRIGGER = "trg_stocktake_rounds_recount_causality_0018"
OLD_SQLITE_ROUND_INSERT = f"{OLD_ROUND_TRIGGER}_insert"
OLD_SQLITE_ROUND_UPDATE = f"{OLD_ROUND_TRIGGER}_update"
OLD_GRAPH_FUNCTION = "rsc_require_stocktake_recount_graph_0018"
OLD_GRAPH_TRIGGERS = {
    "stocktake_tasks": "trg_stocktake_recount_graph_task_0018",
    "stocktake_rounds": "trg_stocktake_recount_graph_round_0018",
    "stocktake_recount_cases": "trg_stocktake_recount_graph_case_0018",
    "stocktake_recount_scope_assignments": (
        "trg_stocktake_recount_graph_assignment_0018"
    ),
}

PG_LOCK_FUNCTION = "rsc_lock_nonopening_stocktake_review_graph_0032"
PG_REVIEW_GRAPH_FUNCTION = "rsc_require_nonopening_stocktake_review_graph_0032"
PG_CASE_FUNCTION = "rsc_validate_stocktake_recount_case_0032"
PG_SCOPE_GRAPH_FUNCTION = "rsc_stocktake_recount_scope_graph_valid_0032"
PG_TASK_FUNCTION = "rsc_validate_stocktake_recount_task_advance_0032"
PG_ROUND_FUNCTION = "rsc_validate_stocktake_recount_round_0032"
PG_RECOUNT_GRAPH_FUNCTION = "rsc_require_stocktake_recount_graph_0032"

REVIEW_TASK_TRIGGER = "trg_stocktake_tasks_nonopening_review_graph_0032"
REVIEW_GRAPH_TRIGGERS = {
    "stocktake_tasks": "trg_nonopening_review_graph_task_0032",
    "stocktake_reviews": "trg_nonopening_review_graph_review_0032",
    "stocktake_review_items": "trg_nonopening_review_graph_item_0032",
}
CASE_TRIGGER = "trg_stocktake_recount_cases_review_path_0032"
TASK_TRIGGER = "trg_stocktake_tasks_recount_causality_0032"
ROUND_TRIGGER = "trg_stocktake_rounds_recount_causality_0032"
SQLITE_ROUND_INSERT = f"{ROUND_TRIGGER}_insert"
SQLITE_ROUND_UPDATE = f"{ROUND_TRIGGER}_update"
RECOUNT_GRAPH_TRIGGERS = {
    "stocktake_tasks": "trg_stocktake_recount_graph_task_0032",
    "stocktake_rounds": "trg_stocktake_recount_graph_round_0032",
    "stocktake_recount_cases": "trg_stocktake_recount_graph_case_0032",
    "stocktake_recount_scope_assignments": (
        "trg_stocktake_recount_graph_assignment_0032"
    ),
}

UPGRADE_BLOCKER = (
    "0032 preflight failed: existing non-opening review/recount facts require "
    "manual evidence quarantine"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0032 while non-opening review or recount facts exist"
)


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0032 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite" and context.is_offline_mode():
        raise RuntimeError("0032 SQLite upgrade requires an online connection")
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
        _sqlite_preflight()
        _upgrade_sqlite()
    else:
        _postgresql_preflight()
        _upgrade_postgresql()


def downgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        raise RuntimeError("0032 downgrade requires an online evidence check")
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    _require_safe_downgrade(dialect)
    if dialect == "sqlite":
        _downgrade_sqlite()
    else:
        _downgrade_postgresql()


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _postgresql_preflight() -> None:
    op.execute(
        "LOCK TABLE public.stocktake_tasks, public.stocktake_rounds, "
        "public.stocktake_scopes, public.inventory_freezes, "
        "public.stocktake_difference_set_completions, "
        "public.stocktake_differences, public.stocktake_reviews, "
        "public.stocktake_review_items, public.stocktake_recount_cases, "
        "public.stocktake_recount_scope_assignments IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
         WHERE task.task_type IN {NONOPENING_SQL}
           AND (EXISTS (SELECT 1 FROM public.stocktake_reviews AS review
                         WHERE review.task_id = task.id)
                OR EXISTS (SELECT 1 FROM public.stocktake_recount_cases AS recount
                            WHERE recount.task_id = task.id))
    ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _sqlite_preflight() -> None:
    if op.get_bind().exec_driver_sql(
        f"""
SELECT 1
  FROM stocktake_tasks AS task
 WHERE task.task_type IN {NONOPENING_SQL}
   AND (EXISTS (SELECT 1 FROM stocktake_reviews AS review
                 WHERE review.task_id = task.id)
        OR EXISTS (SELECT 1 FROM stocktake_recount_cases AS recount
                    WHERE recount.task_id = task.id))
 LIMIT 1
"""
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _require_safe_downgrade(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    if op.get_bind().exec_driver_sql(
        f"""
SELECT 1
  FROM {prefix}stocktake_tasks AS task
 WHERE task.task_type IN {NONOPENING_SQL}
   AND (EXISTS (SELECT 1 FROM {prefix}stocktake_reviews AS review
                 WHERE review.task_id = task.id)
        OR EXISTS (SELECT 1 FROM {prefix}stocktake_recount_cases AS recount
                    WHERE recount.task_id = task.id)
        OR EXISTS (SELECT 1 FROM {prefix}stocktake_rounds AS round_row
                    WHERE round_row.task_id = task.id AND round_row.round_no > 1))
 LIMIT 1
"""
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _upgrade_postgresql() -> None:
    for table_name, trigger_name in OLD_GRAPH_TRIGGERS.items():
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
    op.execute(
        f"DROP TRIGGER IF EXISTS {OLD_CASE_TRIGGER} "
        "ON public.stocktake_recount_cases"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {OLD_TASK_TRIGGER} ON public.stocktake_tasks"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {OLD_ROUND_TRIGGER} ON public.stocktake_rounds"
    )

    for sql in (
        _postgresql_lock_function_sql(),
        _postgresql_review_graph_function_sql(),
        _postgresql_scope_graph_function_sql(),
        _postgresql_case_function_sql(),
        _postgresql_task_function_sql(),
        _postgresql_round_function_sql(),
        _postgresql_recount_graph_function_sql(),
    ):
        op.execute(sql)

    for table_name, trigger_name in REVIEW_GRAPH_TRIGGERS.items():
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} AFTER INSERT OR UPDATE OR DELETE "
            f"ON public.{table_name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_REVIEW_GRAPH_FUNCTION}()"
        )
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )
    for table_name, trigger_name, function_name, event in (
        (
            "stocktake_recount_cases",
            CASE_TRIGGER,
            PG_CASE_FUNCTION,
            "BEFORE INSERT",
        ),
        ("stocktake_tasks", TASK_TRIGGER, PG_TASK_FUNCTION, "BEFORE UPDATE"),
        (
            "stocktake_rounds",
            ROUND_TRIGGER,
            PG_ROUND_FUNCTION,
            "BEFORE INSERT OR UPDATE",
        ),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} {event} ON public.{table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{function_name}()"
        )
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )
    for table_name, trigger_name in RECOUNT_GRAPH_TRIGGERS.items():
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} AFTER INSERT OR UPDATE OR DELETE "
            f"ON public.{table_name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_RECOUNT_GRAPH_FUNCTION}()"
        )
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )
    _apply_postgresql_function_acl()


def _downgrade_postgresql() -> None:
    for table_name, trigger_name in REVIEW_GRAPH_TRIGGERS.items():
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
    for table_name, trigger_name in RECOUNT_GRAPH_TRIGGERS.items():
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
    for table_name, trigger_name in (
        ("stocktake_recount_cases", CASE_TRIGGER),
        ("stocktake_tasks", TASK_TRIGGER),
        ("stocktake_rounds", ROUND_TRIGGER),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
    for function_name, signature in (
        (PG_RECOUNT_GRAPH_FUNCTION, ""),
        (PG_ROUND_FUNCTION, ""),
        (PG_TASK_FUNCTION, ""),
        (PG_CASE_FUNCTION, ""),
        (PG_SCOPE_GRAPH_FUNCTION, "uuid"),
        (PG_REVIEW_GRAPH_FUNCTION, ""),
        (PG_LOCK_FUNCTION, "uuid, uuid"),
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function_name}({signature})")

    op.execute(
        f"CREATE TRIGGER {OLD_CASE_TRIGGER} BEFORE INSERT ON "
        "public.stocktake_recount_cases FOR EACH ROW EXECUTE FUNCTION "
        "public.rsc_validate_stocktake_recount_case_0021()"
    )
    op.execute(
        f"CREATE TRIGGER {OLD_TASK_TRIGGER} BEFORE UPDATE ON public.stocktake_tasks "
        "FOR EACH ROW EXECUTE FUNCTION "
        "public.rsc_validate_stocktake_recount_task_advance_0018()"
    )
    op.execute(
        f"CREATE TRIGGER {OLD_ROUND_TRIGGER} BEFORE INSERT OR UPDATE ON "
        "public.stocktake_rounds FOR EACH ROW EXECUTE FUNCTION "
        "public.rsc_validate_stocktake_recount_round_0018()"
    )
    for table_name, trigger_name in OLD_GRAPH_TRIGGERS.items():
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} AFTER INSERT OR UPDATE OR DELETE "
            f"ON public.{table_name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION public.{OLD_GRAPH_FUNCTION}()"
        )
    for table_name, trigger_name in (
        ("stocktake_recount_cases", OLD_CASE_TRIGGER),
        ("stocktake_tasks", OLD_TASK_TRIGGER),
        ("stocktake_rounds", OLD_ROUND_TRIGGER),
        *OLD_GRAPH_TRIGGERS.items(),
    ):
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )


def _apply_postgresql_function_acl() -> None:
    guard_signatures = (
        f"public.{PG_REVIEW_GRAPH_FUNCTION}()",
        f"public.{PG_SCOPE_GRAPH_FUNCTION}(uuid)",
        f"public.{PG_CASE_FUNCTION}()",
        f"public.{PG_TASK_FUNCTION}()",
        f"public.{PG_ROUND_FUNCTION}()",
        f"public.{PG_RECOUNT_GRAPH_FUNCTION}()",
    )
    for signature in guard_signatures:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    lock_signature = f"public.{PG_LOCK_FUNCTION}(uuid, uuid)"
    op.execute(f"REVOKE EXECUTE ON FUNCTION {lock_signature} FROM PUBLIC")
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname = '{MIGRATION_ROLE}') THEN
        EXECUTE 'ALTER FUNCTION {lock_signature} OWNER TO {MIGRATION_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname = '{PRODUCTION_API_ROLE}') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION {lock_signature} '
                'TO {PRODUCTION_API_ROLE}';
        {''.join(f"EXECUTE 'REVOKE EXECUTE ON FUNCTION {value} FROM {PRODUCTION_API_ROLE}';" for value in guard_signatures)}
    END IF;
END
$$
"""
    )


def _postgresql_historical_actor_sql(
    review_alias: str,
    *,
    role_code: str,
    scope_type: str,
    scope_id_sql: str,
) -> str:
    return f"""EXISTS (
        SELECT 1
          FROM public.role_assignments AS historical_assignment
          JOIN public.roles AS historical_role
            ON historical_role.id = historical_assignment.role_id
          JOIN public.users AS historical_user
            ON historical_user.id = historical_assignment.user_id
         WHERE historical_assignment.id =
               {review_alias}.reviewer_role_assignment_id
           AND historical_assignment.user_id = {review_alias}.reviewer_user_id
           AND historical_user.person_id = {review_alias}.reviewer_person_id
           AND historical_user.authorization_version >=
               {review_alias}.authorization_version
           AND historical_assignment.status IN ('active', 'expired', 'revoked')
           AND historical_assignment.valid_from <= {review_alias}.reviewed_at
           AND (historical_assignment.valid_to IS NULL
                OR {review_alias}.reviewed_at < historical_assignment.valid_to)
           AND (historical_assignment.revoked_at IS NULL
                OR {review_alias}.reviewed_at < historical_assignment.revoked_at)
           AND historical_role.is_external = false
           AND historical_role.status = 'active'
           AND historical_role.code = '{role_code}'
           AND historical_assignment.scope_type = '{scope_type}'
           AND replace(historical_assignment.scope_id, '-', '') =
               replace({scope_id_sql}, '-', '')
    )"""


def _postgresql_review_items_valid_sql(review_alias: str) -> str:
    return f"""(
        (SELECT count(*) FROM public.stocktake_review_items AS item
          WHERE item.review_id = {review_alias}.id) =
        (SELECT count(*) FROM public.stocktake_differences AS difference
          WHERE difference.task_id = {review_alias}.task_id
            AND difference.round_id = {review_alias}.round_id)
        AND NOT EXISTS (
            SELECT 1
              FROM public.stocktake_review_items AS item
              JOIN public.stocktake_differences AS difference
                ON difference.id = item.difference_id
               AND difference.task_id = item.task_id
               AND difference.round_id = item.round_id
             WHERE item.review_id = {review_alias}.id
               AND (length(btrim(item.comment)) = 0
                    OR (difference.reason_code = 'stocktake_pending_verification'
                        AND item.decision NOT IN
                            ('pending_verification', 'recount')))
        )
        AND (
            ({review_alias}.decision = 'approve'
             AND NOT EXISTS (
                SELECT 1 FROM public.stocktake_review_items AS item
                 WHERE item.review_id = {review_alias}.id
                   AND item.decision NOT IN
                       ('accept_for_posting', 'no_adjustment'))
             AND NOT EXISTS (
                SELECT 1 FROM public.stocktake_differences AS difference
                 WHERE difference.task_id = {review_alias}.task_id
                   AND difference.round_id = {review_alias}.round_id
                   AND difference.reason_code =
                       'stocktake_pending_verification'))
            OR
            ({review_alias}.decision = 'recount'
             AND length(btrim({review_alias}.comment)) > 0
             AND EXISTS (SELECT 1 FROM public.stocktake_review_items AS item
                          WHERE item.review_id = {review_alias}.id
                            AND item.decision IN
                                ('pending_verification', 'recount'))
             AND NOT EXISTS (SELECT 1 FROM public.stocktake_review_items AS item
                              WHERE item.review_id = {review_alias}.id
                                AND item.decision = 'reject'))
            OR
            ({review_alias}.decision = 'reject'
             AND length(btrim({review_alias}.comment)) > 0
             AND EXISTS (SELECT 1 FROM public.stocktake_review_items AS item
                          WHERE item.review_id = {review_alias}.id)
             AND NOT EXISTS (SELECT 1 FROM public.stocktake_review_items AS item
                              WHERE item.review_id = {review_alias}.id
                                AND item.decision <> 'reject'))
        )
    )"""


def _postgresql_lock_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_LOCK_FUNCTION}(
    requested_task_id uuid,
    requested_round_id uuid
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    graph_count bigint;
    locked_count bigint;
BEGIN
    IF requested_task_id IS NULL OR requested_round_id IS NULL THEN
        RAISE EXCEPTION 'non-opening stocktake lock graph invariant violated';
    END IF;
    PERFORM task.id
      FROM public.stocktake_tasks AS task
     WHERE task.id = requested_task_id
       AND task.task_type IN {NONOPENING_SQL}
     ORDER BY task.id FOR UPDATE OF task;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'non-opening stocktake lock graph invariant violated';
    END IF;
    PERFORM round_row.id
      FROM public.stocktake_rounds AS round_row
     WHERE round_row.id = requested_round_id
       AND round_row.task_id = requested_task_id
     ORDER BY round_row.id FOR UPDATE OF round_row;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'non-opening stocktake lock graph invariant violated';
    END IF;

    SELECT
        (SELECT count(*) FROM public.stocktake_scopes
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.inventory_freezes
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_snapshot_lines
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_rounds
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_count_lines
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_count_observations
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_scope_count_completions
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_round_submissions
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_difference_set_completions
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_differences
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_reviews
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_review_items
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_recount_cases
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_recount_scope_assignments
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.state_transition_events
          WHERE aggregate_type = 'stocktake_task'
            AND aggregate_id = requested_task_id::text)
      + (SELECT count(*)
           FROM public.stocktake_count_serials AS serial_row
           JOIN public.stocktake_count_lines AS count_line
             ON count_line.id = serial_row.count_line_id
            AND count_line.round_id = serial_row.round_id
          WHERE count_line.task_id = requested_task_id)
      INTO graph_count;
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION 'non-opening stocktake lock graph invariant violated';
    END IF;

    PERFORM scope.id FROM public.stocktake_scopes AS scope
     WHERE scope.task_id = requested_task_id
     ORDER BY scope.scope_no, scope.id FOR UPDATE OF scope;
    PERFORM freeze_row.id FROM public.inventory_freezes AS freeze_row
     WHERE freeze_row.task_id = requested_task_id
     ORDER BY freeze_row.stocktake_scope_id, freeze_row.id
     FOR UPDATE OF freeze_row;
    PERFORM snapshot.id FROM public.stocktake_snapshot_lines AS snapshot
     WHERE snapshot.task_id = requested_task_id
     ORDER BY snapshot.scope_id, snapshot.stock_account_id, snapshot.id
     FOR UPDATE OF snapshot;
    PERFORM round_row.id FROM public.stocktake_rounds AS round_row
     WHERE round_row.task_id = requested_task_id
     ORDER BY round_row.round_no, round_row.id FOR UPDATE OF round_row;
    PERFORM count_line.id FROM public.stocktake_count_lines AS count_line
     WHERE count_line.task_id = requested_task_id
     ORDER BY count_line.round_id, count_line.scope_id,
              count_line.stock_account_id, count_line.id
     FOR UPDATE OF count_line;
    PERFORM serial_row.serial_id
      FROM public.stocktake_count_serials AS serial_row
      JOIN public.stocktake_count_lines AS count_line
        ON count_line.id = serial_row.count_line_id
       AND count_line.round_id = serial_row.round_id
     WHERE count_line.task_id = requested_task_id
     ORDER BY serial_row.round_id, serial_row.count_line_id,
              serial_row.serial_id FOR UPDATE OF serial_row;
    PERFORM observation.id
      FROM public.stocktake_count_observations AS observation
     WHERE observation.task_id = requested_task_id
     ORDER BY observation.round_id, observation.scope_id,
              observation.observation_no, observation.id
     FOR UPDATE OF observation;
    PERFORM completion.id
      FROM public.stocktake_scope_count_completions AS completion
     WHERE completion.task_id = requested_task_id
     ORDER BY completion.round_id, completion.scope_id, completion.id
     FOR UPDATE OF completion;
    PERFORM submission.id FROM public.stocktake_round_submissions AS submission
     WHERE submission.task_id = requested_task_id
     ORDER BY submission.round_id, submission.id FOR UPDATE OF submission;
    PERFORM completion.id
      FROM public.stocktake_difference_set_completions AS completion
     WHERE completion.task_id = requested_task_id
     ORDER BY completion.round_id, completion.id FOR UPDATE OF completion;
    PERFORM difference.id FROM public.stocktake_differences AS difference
     WHERE difference.task_id = requested_task_id
     ORDER BY difference.round_id, difference.difference_no, difference.id
     FOR UPDATE OF difference;
    PERFORM review.id FROM public.stocktake_reviews AS review
     WHERE review.task_id = requested_task_id
     ORDER BY review.round_id, review.review_stage, review.id
     FOR UPDATE OF review;
    PERFORM item.review_id FROM public.stocktake_review_items AS item
     WHERE item.task_id = requested_task_id
     ORDER BY item.round_id, item.review_id, item.difference_id
     FOR UPDATE OF item;
    PERFORM recount.id FROM public.stocktake_recount_cases AS recount
     WHERE recount.task_id = requested_task_id
     ORDER BY recount.next_round_no, recount.id FOR UPDATE OF recount;
    PERFORM assignment.id
      FROM public.stocktake_recount_scope_assignments AS assignment
     WHERE assignment.task_id = requested_task_id
     ORDER BY assignment.recount_case_id, assignment.scope_id, assignment.id
     FOR UPDATE OF assignment;
    PERFORM transition.id FROM public.state_transition_events AS transition
     WHERE transition.aggregate_type = 'stocktake_task'
       AND transition.aggregate_id = requested_task_id::text
     ORDER BY transition.occurred_at, transition.id FOR UPDATE OF transition;

    -- Lock the exact shared reference union only after task-local evidence.
    PERFORM account.id FROM public.stock_accounts AS account
     WHERE account.id IN (
        SELECT snapshot.stock_account_id
          FROM public.stocktake_snapshot_lines AS snapshot
         WHERE snapshot.task_id = requested_task_id)
     ORDER BY account.id FOR UPDATE OF account;
    PERFORM organization.id FROM public.organizations AS organization
     WHERE organization.id IN (
        SELECT scope.owner_org_id FROM public.stocktake_scopes AS scope
         WHERE scope.task_id = requested_task_id
        UNION
        SELECT observation.owner_org_id
          FROM public.stocktake_count_observations AS observation
         WHERE observation.task_id = requested_task_id)
     ORDER BY organization.id FOR UPDATE OF organization;
    PERFORM location.id FROM public.stock_locations AS location
     WHERE location.id IN (
        SELECT scope.location_id FROM public.stocktake_scopes AS scope
         WHERE scope.task_id = requested_task_id
        UNION
        SELECT observation.location_id
          FROM public.stocktake_count_observations AS observation
         WHERE observation.task_id = requested_task_id)
     ORDER BY location.id FOR UPDATE OF location;
    PERFORM person.id FROM public.people AS person
     WHERE person.id IN (
        SELECT scope.custodian_person_id_snapshot
          FROM public.stocktake_scopes AS scope
         WHERE scope.task_id = requested_task_id
           AND scope.custodian_person_id_snapshot IS NOT NULL
        UNION
        SELECT observation.custodian_person_id_snapshot
          FROM public.stocktake_count_observations AS observation
         WHERE observation.task_id = requested_task_id
           AND observation.custodian_person_id_snapshot IS NOT NULL)
     ORDER BY person.id FOR UPDATE OF person;
    PERFORM material.id FROM public.materials AS material
     WHERE material.id IN (
        SELECT account.material_id
          FROM public.stock_accounts AS account
          JOIN public.stocktake_snapshot_lines AS snapshot
            ON snapshot.stock_account_id = account.id
         WHERE snapshot.task_id = requested_task_id
        UNION
        SELECT observation.material_id
          FROM public.stocktake_count_observations AS observation
         WHERE observation.task_id = requested_task_id
           AND observation.material_id IS NOT NULL)
     ORDER BY material.id FOR UPDATE OF material;
    PERFORM policy.id FROM public.material_inventory_policies AS policy
     WHERE policy.material_id IN (
        SELECT material.id FROM public.materials AS material
         WHERE material.id IN (
            SELECT account.material_id
              FROM public.stock_accounts AS account
              JOIN public.stocktake_snapshot_lines AS snapshot
                ON snapshot.stock_account_id = account.id
             WHERE snapshot.task_id = requested_task_id
            UNION
            SELECT observation.material_id
              FROM public.stocktake_count_observations AS observation
             WHERE observation.task_id = requested_task_id
               AND observation.material_id IS NOT NULL))
     ORDER BY policy.material_id, policy.effective_from, policy.id
     FOR UPDATE OF policy;
    PERFORM lot.id FROM public.inventory_lots AS lot
     WHERE lot.id IN (
        SELECT account.lot_id
          FROM public.stock_accounts AS account
          JOIN public.stocktake_snapshot_lines AS snapshot
            ON snapshot.stock_account_id = account.id
         WHERE snapshot.task_id = requested_task_id
           AND account.lot_id IS NOT NULL
        UNION
        SELECT observation.lot_id
          FROM public.stocktake_count_observations AS observation
         WHERE observation.task_id = requested_task_id
           AND observation.lot_id IS NOT NULL)
     ORDER BY lot.id FOR UPDATE OF lot;
    PERFORM serial.id FROM public.inventory_serials AS serial
     WHERE serial.id IN (
        SELECT serial_row.serial_id
          FROM public.stocktake_count_serials AS serial_row
          JOIN public.stocktake_count_lines AS count_line
            ON count_line.id = serial_row.count_line_id
           AND count_line.round_id = serial_row.round_id
         WHERE count_line.task_id = requested_task_id
        UNION
        SELECT observation.serial_id
          FROM public.stocktake_count_observations AS observation
         WHERE observation.task_id = requested_task_id
           AND observation.serial_id IS NOT NULL)
     ORDER BY serial.id FOR UPDATE OF serial;
END
$$
"""


def _postgresql_review_graph_function_sql() -> str:
    region_actor = _postgresql_historical_actor_sql(
        "review_row",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_sql="task_row.region_org_id::text",
    )
    headquarters_actor = _postgresql_historical_actor_sql(
        "review_row",
        role_code="admin",
        scope_type="national",
        scope_id_sql="'*'",
    )
    item_graph = _postgresql_review_items_valid_sql("review_row")
    return f"""
CREATE FUNCTION public.{PG_REVIEW_GRAPH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    affected_task_id uuid;
    task_row public.stocktake_tasks%ROWTYPE;
    review_row public.stocktake_reviews%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'stocktake_tasks' THEN
        affected_task_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSE
        affected_task_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.task_id ELSE NEW.task_id END;
    END IF;
    SELECT * INTO task_row FROM public.stocktake_tasks
     WHERE id = affected_task_id;
    IF task_row.id IS NULL OR task_row.task_type NOT IN {NONOPENING_SQL} THEN
        RETURN NULL;
    END IF;

    FOR review_row IN
        SELECT * FROM public.stocktake_reviews
         WHERE task_id = affected_task_id
         ORDER BY round_id, review_stage, id
    LOOP
        IF review_row.created_at IS DISTINCT FROM review_row.reviewed_at
           OR NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_rounds AS round_row
                  JOIN public.stocktake_difference_set_completions AS completion
                    ON completion.task_id = round_row.task_id
                   AND completion.round_id = round_row.id
                 WHERE round_row.id = review_row.round_id
                   AND round_row.task_id = review_row.task_id
                   AND round_row.status = 'submitted'
                   AND round_row.submitted_at IS NOT NULL
                   AND completion.completed_at >= round_row.submitted_at
                   AND completion.completed_at <= review_row.reviewed_at)
           OR NOT ({item_graph})
           OR (review_row.review_stage = 'region' AND NOT ({region_actor}))
           OR (review_row.review_stage = 'headquarters' AND NOT ({headquarters_actor}))
           OR (review_row.review_stage = 'headquarters' AND NOT EXISTS (
                SELECT 1 FROM public.stocktake_reviews AS region_review
                 WHERE region_review.task_id = review_row.task_id
                   AND region_review.round_id = review_row.round_id
                   AND region_review.review_stage = 'region'
                   AND region_review.decision = 'approve'
                   AND region_review.reviewed_at < review_row.reviewed_at
                   AND region_review.reviewer_user_id <> review_row.reviewer_user_id
                   AND region_review.reviewer_person_id <> review_row.reviewer_person_id
                   AND region_review.reviewer_role_assignment_id <>
                       review_row.reviewer_role_assignment_id))
           OR (review_row.review_stage = 'headquarters'
               AND review_row.decision = 'approve'
               AND EXISTS (
                    SELECT 1
                      FROM public.stocktake_review_items AS headquarters_item
                      JOIN public.stocktake_reviews AS region_review
                        ON region_review.task_id = review_row.task_id
                       AND region_review.round_id = review_row.round_id
                       AND region_review.review_stage = 'region'
                      LEFT JOIN public.stocktake_review_items AS region_item
                        ON region_item.review_id = region_review.id
                       AND region_item.difference_id =
                           headquarters_item.difference_id
                     WHERE headquarters_item.review_id = review_row.id
                       AND region_item.decision IS DISTINCT FROM
                           headquarters_item.decision))
        THEN
            RAISE EXCEPTION 'non-opening stocktake review graph is invalid';
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
          FROM public.stocktake_rounds AS current_round
         WHERE current_round.task_id = task_row.id
           AND current_round.round_no = task_row.current_round_no
           AND EXISTS (SELECT 1 FROM public.stocktake_reviews AS current_review
                        WHERE current_review.task_id = task_row.id
                          AND current_review.round_id = current_round.id)
           AND NOT (
                (task_row.status = 'hq_review'
                 AND EXISTS (SELECT 1 FROM public.stocktake_reviews AS region_review
                              WHERE region_review.task_id = task_row.id
                                AND region_review.round_id = current_round.id
                                AND region_review.review_stage = 'region'
                                AND region_review.decision = 'approve')
                 AND NOT EXISTS (SELECT 1 FROM public.stocktake_reviews AS hq_review
                                  WHERE hq_review.task_id = task_row.id
                                    AND hq_review.round_id = current_round.id
                                    AND hq_review.review_stage = 'headquarters'))
                OR
                (task_row.status = 'approved'
                 AND EXISTS (
                    SELECT 1 FROM public.stocktake_reviews AS region_review
                    JOIN public.stocktake_reviews AS hq_review
                      ON hq_review.task_id = region_review.task_id
                     AND hq_review.round_id = region_review.round_id
                   WHERE region_review.task_id = task_row.id
                     AND region_review.round_id = current_round.id
                     AND region_review.review_stage = 'region'
                     AND region_review.decision = 'approve'
                     AND hq_review.review_stage = 'headquarters'
                     AND hq_review.decision = 'approve'))
                OR
                (task_row.status = 'recount_required'
                 AND (
                    EXISTS (SELECT 1 FROM public.stocktake_reviews AS region_review
                             WHERE region_review.task_id = task_row.id
                               AND region_review.round_id = current_round.id
                               AND region_review.review_stage = 'region'
                               AND region_review.decision IN ('recount', 'reject')
                               AND NOT EXISTS (
                                  SELECT 1 FROM public.stocktake_reviews AS hq_review
                                   WHERE hq_review.task_id = task_row.id
                                     AND hq_review.round_id = current_round.id
                                     AND hq_review.review_stage = 'headquarters'))
                    OR EXISTS (
                        SELECT 1 FROM public.stocktake_reviews AS region_review
                        JOIN public.stocktake_reviews AS hq_review
                          ON hq_review.task_id = region_review.task_id
                         AND hq_review.round_id = region_review.round_id
                       WHERE region_review.task_id = task_row.id
                         AND region_review.round_id = current_round.id
                         AND region_review.review_stage = 'region'
                         AND region_review.decision = 'approve'
                         AND hq_review.review_stage = 'headquarters'
                         AND hq_review.decision IN ('recount', 'reject'))))
           )
    ) THEN
        RAISE EXCEPTION 'non-opening stocktake review task state is invalid';
    END IF;
    RETURN NULL;
END
$$
"""


def _postgresql_scope_graph_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_SCOPE_GRAPH_FUNCTION}(requested_case_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM public.stocktake_recount_cases AS recount_case
          JOIN public.stocktake_tasks AS task ON task.id = recount_case.task_id
          JOIN public.stocktake_reviews AS trigger_review
            ON trigger_review.id = recount_case.trigger_review_id
           AND trigger_review.task_id = recount_case.task_id
           AND trigger_review.round_id = recount_case.source_round_id
         WHERE recount_case.id = requested_case_id
           AND recount_case.scope_count > 0
           AND recount_case.scope_count = (
                SELECT count(*)
                  FROM public.stocktake_recount_scope_assignments AS assignment
                 WHERE assignment.recount_case_id = recount_case.id)
           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_recount_scope_assignments AS assignment
                 WHERE assignment.recount_case_id = recount_case.id
                   AND NOT EXISTS (
                        SELECT 1 FROM public.stocktake_scopes AS scope
                         WHERE scope.id = assignment.scope_id
                           AND scope.task_id = recount_case.task_id))
           AND (
                (task.task_type = 'opening'
                 AND recount_case.scope_count = (
                    SELECT count(*) FROM public.stocktake_scopes AS scope
                     WHERE scope.task_id = recount_case.task_id)
                 AND NOT EXISTS (
                    SELECT 1 FROM public.stocktake_scopes AS scope
                     WHERE scope.task_id = recount_case.task_id
                       AND NOT EXISTS (
                          SELECT 1
                            FROM public.stocktake_recount_scope_assignments AS assignment
                           WHERE assignment.recount_case_id = recount_case.id
                             AND assignment.scope_id = scope.id)))
                OR
                (task.task_type IN {NONOPENING_SQL}
                 AND recount_case.scope_count = (
                    SELECT count(DISTINCT difference.scope_id)
                      FROM public.stocktake_review_items AS item
                      JOIN public.stocktake_differences AS difference
                        ON difference.id = item.difference_id
                       AND difference.task_id = item.task_id
                       AND difference.round_id = item.round_id
                     WHERE item.review_id = trigger_review.id
                       AND (trigger_review.decision = 'reject'
                            OR item.decision IN
                               ('pending_verification', 'recount')))
                 AND NOT EXISTS (
                    SELECT 1
                      FROM public.stocktake_review_items AS item
                      JOIN public.stocktake_differences AS difference
                        ON difference.id = item.difference_id
                       AND difference.task_id = item.task_id
                       AND difference.round_id = item.round_id
                     WHERE item.review_id = trigger_review.id
                       AND (trigger_review.decision = 'reject'
                            OR item.decision IN
                               ('pending_verification', 'recount'))
                       AND (difference.scope_id IS NULL OR NOT EXISTS (
                          SELECT 1
                            FROM public.stocktake_recount_scope_assignments AS assignment
                           WHERE assignment.recount_case_id = recount_case.id
                             AND assignment.scope_id = difference.scope_id)))
                 AND NOT EXISTS (
                    SELECT 1
                      FROM public.stocktake_recount_scope_assignments AS assignment
                     WHERE assignment.recount_case_id = recount_case.id
                       AND NOT EXISTS (
                          SELECT 1
                            FROM public.stocktake_review_items AS item
                            JOIN public.stocktake_differences AS difference
                              ON difference.id = item.difference_id
                             AND difference.task_id = item.task_id
                             AND difference.round_id = item.round_id
                           WHERE item.review_id = trigger_review.id
                             AND difference.scope_id = assignment.scope_id
                             AND (trigger_review.decision = 'reject'
                                  OR item.decision IN
                                     ('pending_verification', 'recount')))))
           )
    )
$$
"""


def _postgresql_case_function_sql() -> str:
    source_region_actor = _postgresql_historical_actor_sql(
        "source_review",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_sql="task_row.region_org_id::text",
    )
    source_hq_actor = _postgresql_historical_actor_sql(
        "source_review",
        role_code="admin",
        scope_type="national",
        scope_id_sql="'*'",
    )
    prior_region_actor = _postgresql_historical_actor_sql(
        "region_review",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_sql="task_row.region_org_id::text",
    )
    return f"""
CREATE FUNCTION public.{PG_CASE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    task_row public.stocktake_tasks%ROWTYPE;
    source_round public.stocktake_rounds%ROWTYPE;
    source_submission public.stocktake_round_submissions%ROWTYPE;
    source_completion public.stocktake_difference_set_completions%ROWTYPE;
    source_review public.stocktake_reviews%ROWTYPE;
    region_review public.stocktake_reviews%ROWTYPE;
    required_scope_count bigint;
BEGIN
    SELECT * INTO task_row FROM public.stocktake_tasks
     WHERE id = NEW.task_id FOR UPDATE;
    SELECT * INTO source_round FROM public.stocktake_rounds
     WHERE id = NEW.source_round_id AND task_id = NEW.task_id FOR UPDATE;
    SELECT * INTO source_submission FROM public.stocktake_round_submissions
     WHERE id = NEW.source_round_submission_id;
    SELECT * INTO source_completion
      FROM public.stocktake_difference_set_completions
     WHERE id = NEW.source_difference_completion_id;
    SELECT * INTO source_review FROM public.stocktake_reviews
     WHERE id = NEW.trigger_review_id
       AND task_id = NEW.task_id AND round_id = NEW.source_round_id;
    SELECT * INTO region_review FROM public.stocktake_reviews
     WHERE task_id = NEW.task_id AND round_id = NEW.source_round_id
       AND review_stage = 'region';

    IF task_row.task_type = 'opening' THEN
        SELECT count(*) INTO required_scope_count
          FROM public.stocktake_scopes WHERE task_id = NEW.task_id;
    ELSE
        SELECT count(DISTINCT difference.scope_id) INTO required_scope_count
          FROM public.stocktake_review_items AS item
          JOIN public.stocktake_differences AS difference
            ON difference.id = item.difference_id
           AND difference.task_id = item.task_id
           AND difference.round_id = item.round_id
         WHERE item.review_id = NEW.trigger_review_id
           AND (source_review.decision = 'reject'
                OR item.decision IN ('pending_verification', 'recount'));
    END IF;

    IF task_row.id IS NULL
       OR task_row.task_type NOT IN
          ('opening', 'full', 'sample', 'ad_hoc', 'personal', 'termination')
       OR task_row.status <> 'recount_required'
       OR source_round.id IS NULL OR source_round.status <> 'submitted'
       OR source_round.submitted_at IS NULL
       OR task_row.current_round_no IS DISTINCT FROM source_round.round_no
       OR NEW.next_round_no <> source_round.round_no + 1
       OR source_submission.id IS NULL
       OR source_submission.task_id <> NEW.task_id
       OR source_submission.round_id <> NEW.source_round_id
       OR source_completion.id IS NULL
       OR source_completion.task_id <> NEW.task_id
       OR source_completion.round_id <> NEW.source_round_id
       OR source_completion.round_submission_id <> NEW.source_round_submission_id
       OR source_review.id IS NULL
       OR source_submission.submitted_at > source_completion.completed_at
       OR source_completion.completed_at > source_review.reviewed_at
       OR source_review.reviewed_at > NEW.opened_at
       OR required_scope_count = 0
       OR NEW.scope_count <> required_scope_count
       OR EXISTS (SELECT 1 FROM public.stocktake_reviews AS later_review
                   WHERE later_review.task_id = NEW.task_id
                     AND later_review.round_id = NEW.source_round_id
                     AND later_review.reviewed_at > source_review.reviewed_at)
       OR EXISTS (SELECT 1 FROM public.stocktake_postings
                   WHERE task_id = NEW.task_id AND round_id = NEW.source_round_id)
       OR EXISTS (SELECT 1 FROM public.stocktake_rounds
                   WHERE task_id = NEW.task_id AND round_no = NEW.next_round_no)
       OR NOT public.rsc_stocktake_actor_assignment_valid_0011(
            NEW.opened_by_user_id, NEW.opened_by_person_id,
            NEW.opened_role_assignment_id, NEW.authorization_version,
            NEW.opened_at, NEW.role_code, NEW.scope_type,
            NEW.scope_id_snapshot)
       OR NOT (
            (NEW.role_code = 'admin' AND NEW.scope_type = 'national'
             AND NEW.scope_id_snapshot = '*')
            OR (NEW.role_code = 'provincial_manager'
                AND NEW.scope_type = 'organization'
                AND NEW.scope_id_snapshot = task_row.region_org_id::text))
       OR (task_row.task_type = 'opening' AND (
            NEW.scope_manifest_sha256 IS DISTINCT FROM
                task_row.scope_manifest_sha256
            OR EXISTS (SELECT 1 FROM public.inventory_opening_establishments
                        WHERE task_id = NEW.task_id
                          AND round_id = NEW.source_round_id)
            OR NOT (
                (source_review.review_stage = 'region'
                 AND source_review.decision IN ('recount', 'reject')
                 AND {source_region_actor}
                 AND NOT EXISTS (
                    SELECT 1 FROM public.stocktake_reviews AS hq_review
                     WHERE hq_review.task_id = NEW.task_id
                       AND hq_review.round_id = NEW.source_round_id
                       AND hq_review.review_stage = 'headquarters'))
                OR
                (source_review.review_stage = 'headquarters'
                 AND source_review.decision = 'reject'
                 AND {source_hq_actor}
                 AND region_review.id IS NOT NULL
                 AND region_review.decision = 'approve'
                 AND source_completion.completed_at <= region_review.reviewed_at
                 AND region_review.reviewed_at < source_review.reviewed_at
                 AND region_review.reviewer_user_id <>
                     source_review.reviewer_user_id
                 AND region_review.reviewer_person_id <>
                     source_review.reviewer_person_id
                 AND region_review.reviewer_role_assignment_id <>
                     source_review.reviewer_role_assignment_id
                 AND {prior_region_actor}))))
       OR (task_row.task_type IN {NONOPENING_SQL} AND (
            EXISTS (
                SELECT 1
                  FROM public.stocktake_review_items AS item
                  JOIN public.stocktake_differences AS difference
                    ON difference.id = item.difference_id
                   AND difference.task_id = item.task_id
                   AND difference.round_id = item.round_id
                 WHERE item.review_id = NEW.trigger_review_id
                   AND (source_review.decision = 'reject'
                        OR item.decision IN ('pending_verification', 'recount'))
                   AND difference.scope_id IS NULL)
            OR NOT (
                (source_review.review_stage = 'region'
                 AND source_review.decision IN ('recount', 'reject')
                 AND {source_region_actor}
                 AND NOT EXISTS (
                    SELECT 1 FROM public.stocktake_reviews AS hq_review
                     WHERE hq_review.task_id = NEW.task_id
                       AND hq_review.round_id = NEW.source_round_id
                       AND hq_review.review_stage = 'headquarters'))
                OR
                (source_review.review_stage = 'headquarters'
                 AND source_review.decision IN ('recount', 'reject')
                 AND {source_hq_actor}
                 AND region_review.id IS NOT NULL
                 AND region_review.decision = 'approve'
                 AND source_completion.completed_at <= region_review.reviewed_at
                 AND region_review.reviewed_at < source_review.reviewed_at
                 AND region_review.reviewer_user_id <>
                     source_review.reviewer_user_id
                 AND region_review.reviewer_person_id <>
                     source_review.reviewer_person_id
                 AND region_review.reviewer_role_assignment_id <>
                     source_review.reviewer_role_assignment_id
                 AND {prior_region_actor}))))
    ) THEN
        RAISE EXCEPTION 'stocktake recount case causality is invalid';
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_task_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_TASK_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    case_row public.stocktake_recount_cases%ROWTYPE;
BEGIN
    IF NEW.task_type NOT IN
       ('opening', 'full', 'sample', 'ad_hoc', 'personal', 'termination') THEN
        RETURN NEW;
    END IF;
    IF NEW.current_round_no IS DISTINCT FROM OLD.current_round_no
       AND NEW.current_round_no > 1 THEN
        SELECT * INTO case_row FROM public.stocktake_recount_cases
         WHERE task_id = NEW.id
           AND source_round_id = (
                SELECT id FROM public.stocktake_rounds
                 WHERE task_id = NEW.id AND round_no = OLD.current_round_no)
           AND next_round_no = NEW.current_round_no FOR UPDATE;
        IF OLD.status <> 'recount_required' OR NEW.status <> 'counting'
           OR NEW.current_round_no <> OLD.current_round_no + 1
           OR case_row.id IS NULL
           OR EXISTS (SELECT 1 FROM public.stocktake_rounds
                       WHERE task_id = NEW.id
                         AND round_no = NEW.current_round_no)
           OR NOT public.{PG_SCOPE_GRAPH_FUNCTION}(case_row.id)
        THEN
            RAISE EXCEPTION 'stocktake recount task advance is invalid';
        END IF;
    ELSIF OLD.status = 'recount_required' AND NEW.status = 'counting' THEN
        RAISE EXCEPTION 'stocktake recount task advance requires next round';
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_round_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_ROUND_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    parent_task public.stocktake_tasks%ROWTYPE;
    case_row public.stocktake_recount_cases%ROWTYPE;
BEGIN
    IF NEW.status = 'superseded' THEN
        RAISE EXCEPTION 'submitted stocktake rounds remain immutable predecessors';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        OLD.task_id IS DISTINCT FROM NEW.task_id
        OR OLD.round_no IS DISTINCT FROM NEW.round_no
        OR OLD.round_type IS DISTINCT FROM NEW.round_type
        OR OLD.recount_case_id IS DISTINCT FROM NEW.recount_case_id
    ) THEN
        RAISE EXCEPTION 'stocktake round causal identity is immutable';
    END IF;
    IF NEW.round_no = 1 THEN
        IF NEW.round_type <> 'initial' OR NEW.recount_case_id IS NOT NULL THEN
            RAISE EXCEPTION 'initial stocktake round cannot bind a recount case';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO parent_task FROM public.stocktake_tasks
     WHERE id = NEW.task_id FOR UPDATE;
    SELECT * INTO case_row FROM public.stocktake_recount_cases
     WHERE id = NEW.recount_case_id AND task_id = NEW.task_id
       AND next_round_no = NEW.round_no FOR UPDATE;
    IF NEW.round_type <> 'recount' OR case_row.id IS NULL
       OR parent_task.id IS NULL OR parent_task.status <> 'counting'
       OR parent_task.current_round_no <> NEW.round_no
       OR NOT EXISTS (SELECT 1 FROM public.stocktake_rounds AS source
                       WHERE source.id = case_row.source_round_id
                         AND source.task_id = NEW.task_id
                         AND source.round_no = NEW.round_no - 1
                         AND source.status = 'submitted')
       OR NEW.started_at < case_row.opened_at
       OR NOT public.{PG_SCOPE_GRAPH_FUNCTION}(case_row.id)
    THEN
        RAISE EXCEPTION 'stocktake recount round causality is invalid';
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_recount_graph_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_RECOUNT_GRAPH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    affected_task_id uuid;
    task_round_no integer;
BEGIN
    IF TG_TABLE_NAME = 'stocktake_tasks' THEN
        affected_task_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSE
        affected_task_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.task_id ELSE NEW.task_id END;
    END IF;
    SELECT current_round_no INTO task_round_no
      FROM public.stocktake_tasks WHERE id = affected_task_id;
    IF task_round_no IS NULL THEN
        RETURN NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM public.stocktake_rounds
                WHERE task_id = affected_task_id AND status = 'superseded')
       OR (task_round_no = 0 AND EXISTS (
            SELECT 1 FROM public.stocktake_rounds
             WHERE task_id = affected_task_id))
       OR (task_round_no > 0 AND (
            SELECT count(*) FROM public.stocktake_rounds
             WHERE task_id = affected_task_id) <> task_round_no)
       OR (task_round_no > 0 AND (
            SELECT min(round_no) FROM public.stocktake_rounds
             WHERE task_id = affected_task_id) <> 1)
       OR (task_round_no > 0 AND (
            SELECT max(round_no) FROM public.stocktake_rounds
             WHERE task_id = affected_task_id) <> task_round_no)
       OR EXISTS (
            SELECT 1 FROM public.stocktake_rounds AS successor
             LEFT JOIN public.stocktake_recount_cases AS recount_case
               ON recount_case.id = successor.recount_case_id
              AND recount_case.task_id = successor.task_id
              AND recount_case.next_round_no = successor.round_no
             LEFT JOIN public.stocktake_rounds AS source
               ON source.id = recount_case.source_round_id
              AND source.task_id = successor.task_id
              AND source.round_no = successor.round_no - 1
              AND source.status = 'submitted'
            WHERE successor.task_id = affected_task_id
              AND successor.round_no > 1
              AND (recount_case.id IS NULL OR source.id IS NULL))
       OR EXISTS (
            SELECT 1 FROM public.stocktake_recount_cases AS recount_case
             WHERE recount_case.task_id = affected_task_id
               AND (1 <> (SELECT count(*) FROM public.stocktake_rounds AS successor
                           WHERE successor.recount_case_id = recount_case.id
                             AND successor.task_id = recount_case.task_id
                             AND successor.round_no = recount_case.next_round_no)
                    OR NOT public.{PG_SCOPE_GRAPH_FUNCTION}(recount_case.id)))
       OR EXISTS (SELECT 1 FROM public.stocktake_rounds
                   WHERE task_id = affected_task_id AND status = 'counting'
                     AND round_no <> task_round_no)
    THEN
        RAISE EXCEPTION
            'stocktake recount graph is incomplete or non-contiguous';
    END IF;
    RETURN NULL;
END
$$
"""


def _upgrade_sqlite() -> None:
    for trigger_name in (
        OLD_CASE_TRIGGER,
        OLD_TASK_TRIGGER,
        OLD_SQLITE_ROUND_INSERT,
        OLD_SQLITE_ROUND_UPDATE,
        REVIEW_TASK_TRIGGER,
        CASE_TRIGGER,
        TASK_TRIGGER,
        SQLITE_ROUND_INSERT,
        SQLITE_ROUND_UPDATE,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    op.execute(_sqlite_review_task_trigger_sql())
    op.execute(_sqlite_case_trigger_sql(opening_only=False))
    op.execute(_sqlite_task_trigger_sql(opening_only=False))
    op.execute(_sqlite_round_insert_sql(opening_only=False))
    op.execute(_sqlite_round_update_sql(opening_only=False))


def _downgrade_sqlite() -> None:
    for trigger_name in (
        REVIEW_TASK_TRIGGER,
        CASE_TRIGGER,
        TASK_TRIGGER,
        SQLITE_ROUND_INSERT,
        SQLITE_ROUND_UPDATE,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    op.execute(_sqlite_case_trigger_sql(opening_only=True))
    op.execute(_sqlite_task_trigger_sql(opening_only=True))
    op.execute(_sqlite_round_insert_sql(opening_only=True))
    op.execute(_sqlite_round_update_sql(opening_only=True))


def _sqlite_historical_actor_sql(
    review_alias: str,
    *,
    role_code: str,
    scope_type: str,
    scope_id_sql: str,
) -> str:
    return f"""EXISTS (
        SELECT 1
          FROM role_assignments AS historical_assignment
          JOIN roles AS historical_role
            ON historical_role.id = historical_assignment.role_id
          JOIN users AS historical_user
            ON historical_user.id = historical_assignment.user_id
         WHERE historical_assignment.id =
               {review_alias}.reviewer_role_assignment_id
           AND historical_assignment.user_id = {review_alias}.reviewer_user_id
           AND historical_user.person_id = {review_alias}.reviewer_person_id
           AND historical_user.authorization_version >=
               {review_alias}.authorization_version
           AND historical_assignment.status IN ('active', 'expired', 'revoked')
           AND historical_assignment.valid_from <= {review_alias}.reviewed_at
           AND (historical_assignment.valid_to IS NULL
                OR {review_alias}.reviewed_at < historical_assignment.valid_to)
           AND (historical_assignment.revoked_at IS NULL
                OR {review_alias}.reviewed_at < historical_assignment.revoked_at)
           AND historical_role.is_external = 0
           AND historical_role.status = 'active'
           AND historical_role.code = '{role_code}'
           AND historical_assignment.scope_type = '{scope_type}'
           AND replace(historical_assignment.scope_id, '-', '') =
               replace({scope_id_sql}, '-', '')
    )"""


def _sqlite_review_items_valid_sql(review_alias: str) -> str:
    return f"""(
        (SELECT count(*) FROM stocktake_review_items AS item
          WHERE replace(item.review_id, '-', '') =
                replace({review_alias}.id, '-', '')) =
        (SELECT count(*) FROM stocktake_differences AS difference
          WHERE difference.task_id = {review_alias}.task_id
            AND difference.round_id = {review_alias}.round_id)
        AND NOT EXISTS (
            SELECT 1
              FROM stocktake_review_items AS item
              JOIN stocktake_differences AS difference
                ON difference.id = item.difference_id
               AND difference.task_id = item.task_id
               AND difference.round_id = item.round_id
             WHERE replace(item.review_id, '-', '') =
                   replace({review_alias}.id, '-', '')
               AND (length(trim(item.comment)) = 0
                    OR (difference.reason_code = 'stocktake_pending_verification'
                        AND item.decision NOT IN
                            ('pending_verification', 'recount')))
        )
        AND (
            ({review_alias}.decision = 'approve'
             AND NOT EXISTS (
                SELECT 1 FROM stocktake_review_items AS item
                 WHERE replace(item.review_id, '-', '') =
                       replace({review_alias}.id, '-', '')
                   AND item.decision NOT IN
                       ('accept_for_posting', 'no_adjustment'))
             AND NOT EXISTS (
                SELECT 1 FROM stocktake_differences AS difference
                 WHERE difference.task_id = {review_alias}.task_id
                   AND difference.round_id = {review_alias}.round_id
                   AND difference.reason_code =
                       'stocktake_pending_verification'))
            OR
            ({review_alias}.decision = 'recount'
             AND length(trim({review_alias}.comment)) > 0
             AND EXISTS (SELECT 1 FROM stocktake_review_items AS item
                          WHERE replace(item.review_id, '-', '') =
                                replace({review_alias}.id, '-', '')
                            AND item.decision IN
                                ('pending_verification', 'recount'))
             AND NOT EXISTS (SELECT 1 FROM stocktake_review_items AS item
                              WHERE replace(item.review_id, '-', '') =
                                    replace({review_alias}.id, '-', '')
                                AND item.decision = 'reject'))
            OR
            ({review_alias}.decision = 'reject'
             AND length(trim({review_alias}.comment)) > 0
             AND EXISTS (SELECT 1 FROM stocktake_review_items AS item
                          WHERE replace(item.review_id, '-', '') =
                                replace({review_alias}.id, '-', ''))
             AND NOT EXISTS (SELECT 1 FROM stocktake_review_items AS item
                              WHERE replace(item.review_id, '-', '') =
                                    replace({review_alias}.id, '-', '')
                                AND item.decision <> 'reject'))
        )
    )"""


def _sqlite_review_task_trigger_sql() -> str:
    region_actor = _sqlite_historical_actor_sql(
        "region_review",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_sql="CAST(NEW.region_org_id AS TEXT)",
    )
    hq_actor = _sqlite_historical_actor_sql(
        "hq_review",
        role_code="admin",
        scope_type="national",
        scope_id_sql="'*'",
    )
    region_items = _sqlite_review_items_valid_sql("region_review")
    hq_items = _sqlite_review_items_valid_sql("hq_review")
    return f"""
CREATE TRIGGER {REVIEW_TASK_TRIGGER}
BEFORE UPDATE ON stocktake_tasks
WHEN NEW.task_type IN {NONOPENING_SQL}
 AND ((OLD.status = 'submitted'
       AND NEW.status IN ('hq_review', 'recount_required'))
      OR (OLD.status = 'hq_review'
          AND NEW.status IN ('approved', 'recount_required')))
 AND NOT EXISTS (
    SELECT 1
      FROM stocktake_rounds AS source_round
      JOIN stocktake_difference_set_completions AS completion
        ON completion.task_id = NEW.id
       AND completion.round_id = source_round.id
      JOIN stocktake_reviews AS region_review
        ON region_review.task_id = NEW.id
       AND region_review.round_id = source_round.id
       AND region_review.review_stage = 'region'
      LEFT JOIN stocktake_reviews AS hq_review
        ON hq_review.task_id = NEW.id
       AND hq_review.round_id = source_round.id
       AND hq_review.review_stage = 'headquarters'
     WHERE source_round.task_id = NEW.id
       AND source_round.round_no = NEW.current_round_no
       AND source_round.status = 'submitted'
       AND source_round.submitted_at IS NOT NULL
       AND completion.completed_at >= source_round.submitted_at
       AND region_review.created_at = region_review.reviewed_at
       AND completion.completed_at <= region_review.reviewed_at
       AND {region_actor}
       AND {region_items}
       AND (
            (OLD.status = 'submitted'
             AND hq_review.id IS NULL
             AND ((region_review.decision = 'approve'
                   AND NEW.status = 'hq_review')
                  OR (region_review.decision IN ('recount', 'reject')
                      AND NEW.status = 'recount_required')))
            OR
            (OLD.status = 'hq_review'
             AND region_review.decision = 'approve'
             AND hq_review.id IS NOT NULL
             AND hq_review.created_at = hq_review.reviewed_at
             AND region_review.reviewed_at < hq_review.reviewed_at
             AND region_review.reviewer_user_id <> hq_review.reviewer_user_id
             AND region_review.reviewer_person_id <> hq_review.reviewer_person_id
             AND region_review.reviewer_role_assignment_id <>
                 hq_review.reviewer_role_assignment_id
             AND {hq_actor}
             AND {hq_items}
             AND ((hq_review.decision = 'approve' AND NEW.status = 'approved'
                   AND NOT EXISTS (
                      SELECT 1
                        FROM stocktake_review_items AS hq_item
                        LEFT JOIN stocktake_review_items AS region_item
                          ON region_item.review_id = region_review.id
                         AND region_item.difference_id = hq_item.difference_id
                       WHERE hq_item.review_id = hq_review.id
                         AND region_item.decision IS NOT hq_item.decision))
                  OR (hq_review.decision IN ('recount', 'reject')
                      AND NEW.status = 'recount_required')))
       )
 )
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake review graph is invalid');
END
"""


def _sqlite_scope_graph_sql(case_alias: str, task_alias: str) -> str:
    return f"""(
        {case_alias}.scope_count > 0
        AND {case_alias}.scope_count = (
            SELECT count(*) FROM stocktake_recount_scope_assignments AS assignment
             WHERE assignment.recount_case_id = {case_alias}.id)
        AND NOT EXISTS (
            SELECT 1 FROM stocktake_recount_scope_assignments AS assignment
             WHERE assignment.recount_case_id = {case_alias}.id
               AND NOT EXISTS (
                  SELECT 1 FROM stocktake_scopes AS scope
                   WHERE scope.id = assignment.scope_id
                     AND scope.task_id = {case_alias}.task_id))
        AND (
            ({task_alias}.task_type = 'opening'
             AND {case_alias}.scope_count = (
                SELECT count(*) FROM stocktake_scopes AS scope
                 WHERE scope.task_id = {case_alias}.task_id)
             AND NOT EXISTS (
                SELECT 1 FROM stocktake_scopes AS scope
                 WHERE scope.task_id = {case_alias}.task_id
                   AND NOT EXISTS (
                      SELECT 1
                        FROM stocktake_recount_scope_assignments AS assignment
                       WHERE assignment.recount_case_id = {case_alias}.id
                         AND assignment.scope_id = scope.id)))
            OR
            ({task_alias}.task_type IN {NONOPENING_SQL}
             AND {case_alias}.scope_count = (
                SELECT count(DISTINCT difference.scope_id)
                  FROM stocktake_review_items AS item
                  JOIN stocktake_differences AS difference
                    ON difference.id = item.difference_id
                   AND difference.task_id = item.task_id
                   AND difference.round_id = item.round_id
                  JOIN stocktake_reviews AS trigger_review
                    ON trigger_review.id = {case_alias}.trigger_review_id
                 WHERE item.review_id = trigger_review.id
                   AND (trigger_review.decision = 'reject'
                        OR item.decision IN
                           ('pending_verification', 'recount')))
             AND NOT EXISTS (
                SELECT 1
                  FROM stocktake_review_items AS item
                  JOIN stocktake_differences AS difference
                    ON difference.id = item.difference_id
                   AND difference.task_id = item.task_id
                   AND difference.round_id = item.round_id
                  JOIN stocktake_reviews AS trigger_review
                    ON trigger_review.id = {case_alias}.trigger_review_id
                 WHERE item.review_id = trigger_review.id
                   AND (trigger_review.decision = 'reject'
                        OR item.decision IN
                           ('pending_verification', 'recount'))
                   AND (difference.scope_id IS NULL OR NOT EXISTS (
                      SELECT 1
                        FROM stocktake_recount_scope_assignments AS assignment
                       WHERE assignment.recount_case_id = {case_alias}.id
                         AND assignment.scope_id = difference.scope_id)))
             AND NOT EXISTS (
                SELECT 1
                  FROM stocktake_recount_scope_assignments AS assignment
                 WHERE assignment.recount_case_id = {case_alias}.id
                   AND NOT EXISTS (
                      SELECT 1
                        FROM stocktake_review_items AS item
                        JOIN stocktake_differences AS difference
                          ON difference.id = item.difference_id
                         AND difference.task_id = item.task_id
                         AND difference.round_id = item.round_id
                        JOIN stocktake_reviews AS trigger_review
                          ON trigger_review.id = {case_alias}.trigger_review_id
                       WHERE item.review_id = trigger_review.id
                         AND difference.scope_id = assignment.scope_id
                         AND (trigger_review.decision = 'reject'
                              OR item.decision IN
                                 ('pending_verification', 'recount')))))
        )
    )"""


def _sqlite_case_trigger_sql(*, opening_only: bool) -> str:
    trigger_name = OLD_CASE_TRIGGER if opening_only else CASE_TRIGGER
    task_types = "('opening')" if opening_only else (
        "('opening', 'full', 'sample', 'ad_hoc', 'personal', 'termination')"
    )
    region_actor = _sqlite_historical_actor_sql(
        "review",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_sql="CAST(task.region_org_id AS TEXT)",
    )
    hq_actor = _sqlite_historical_actor_sql(
        "review",
        role_code="admin",
        scope_type="national",
        scope_id_sql="'*'",
    )
    prior_region_actor = _sqlite_historical_actor_sql(
        "region_review",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_sql="CAST(task.region_org_id AS TEXT)",
    )
    hq_decisions = "('reject')" if opening_only else "('recount', 'reject')"
    scope_rule = (
        "NEW.scope_manifest_sha256 = task.scope_manifest_sha256 "
        "AND NEW.scope_count = (SELECT count(*) FROM stocktake_scopes "
        "WHERE task_id = task.id)"
        if opening_only
        else f"""(
            (task.task_type = 'opening'
             AND NEW.scope_manifest_sha256 = task.scope_manifest_sha256
             AND NEW.scope_count = (
                SELECT count(*) FROM stocktake_scopes WHERE task_id = task.id))
            OR
            (task.task_type IN {NONOPENING_SQL}
             AND NEW.scope_count = (
                SELECT count(DISTINCT difference.scope_id)
                  FROM stocktake_review_items AS item
                  JOIN stocktake_differences AS difference
                    ON difference.id = item.difference_id
                   AND difference.task_id = item.task_id
                   AND difference.round_id = item.round_id
                 WHERE item.review_id = review.id
                   AND (review.decision = 'reject'
                        OR item.decision IN
                           ('pending_verification', 'recount')))
             AND NOT EXISTS (
                SELECT 1
                  FROM stocktake_review_items AS item
                  JOIN stocktake_differences AS difference
                    ON difference.id = item.difference_id
                   AND difference.task_id = item.task_id
                   AND difference.round_id = item.round_id
                 WHERE item.review_id = review.id
                   AND (review.decision = 'reject'
                        OR item.decision IN
                           ('pending_verification', 'recount'))
                   AND difference.scope_id IS NULL))
        )"""
    )
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON stocktake_recount_cases
WHEN NOT EXISTS (
    SELECT 1
      FROM stocktake_tasks AS task
      JOIN stocktake_rounds AS source
        ON source.id = NEW.source_round_id AND source.task_id = task.id
      JOIN stocktake_round_submissions AS submission
        ON submission.id = NEW.source_round_submission_id
       AND submission.task_id = task.id AND submission.round_id = source.id
      JOIN stocktake_difference_set_completions AS completion
        ON completion.id = NEW.source_difference_completion_id
       AND completion.task_id = task.id AND completion.round_id = source.id
       AND completion.round_submission_id = submission.id
      JOIN stocktake_reviews AS review
        ON review.id = NEW.trigger_review_id
       AND review.task_id = task.id AND review.round_id = source.id
     WHERE task.id = NEW.task_id AND task.task_type IN {task_types}
       AND task.status = 'recount_required'
       AND task.current_round_no = source.round_no
       AND source.status = 'submitted' AND source.submitted_at IS NOT NULL
       AND NEW.next_round_no = source.round_no + 1
       AND submission.submitted_at <= completion.completed_at
       AND completion.completed_at <= review.reviewed_at
       AND review.reviewed_at <= NEW.opened_at
       AND NEW.scope_count > 0
       AND {scope_rule}
       AND (
            (review.review_stage = 'region'
             AND review.decision IN ('recount', 'reject')
             AND {region_actor}
             AND NOT EXISTS (
                SELECT 1 FROM stocktake_reviews AS hq_review
                 WHERE hq_review.task_id = task.id
                   AND hq_review.round_id = source.id
                   AND hq_review.review_stage = 'headquarters'))
            OR
            (review.review_stage = 'headquarters'
             AND review.decision IN {hq_decisions}
             AND {hq_actor}
             AND EXISTS (
                SELECT 1 FROM stocktake_reviews AS region_review
                 WHERE region_review.task_id = task.id
                   AND region_review.round_id = source.id
                   AND region_review.review_stage = 'region'
                   AND region_review.decision = 'approve'
                   AND completion.completed_at <= region_review.reviewed_at
                   AND region_review.reviewed_at < review.reviewed_at
                   AND region_review.reviewer_user_id <> review.reviewer_user_id
                   AND region_review.reviewer_person_id <> review.reviewer_person_id
                   AND region_review.reviewer_role_assignment_id <>
                       review.reviewer_role_assignment_id
                   AND {prior_region_actor}))
       )
       AND NOT EXISTS (SELECT 1 FROM stocktake_reviews AS later_review
                        WHERE later_review.task_id = task.id
                          AND later_review.round_id = source.id
                          AND later_review.reviewed_at > review.reviewed_at)
       AND NOT EXISTS (SELECT 1 FROM stocktake_postings
                        WHERE task_id = task.id AND round_id = source.id)
       AND NOT EXISTS (SELECT 1 FROM stocktake_rounds
                        WHERE task_id = task.id
                          AND round_no = NEW.next_round_no)
       AND EXISTS (
            SELECT 1 FROM role_assignments AS assignment
            JOIN roles AS role ON role.id = assignment.role_id
            JOIN users AS actor ON actor.id = assignment.user_id
             WHERE assignment.id = NEW.opened_role_assignment_id
               AND assignment.user_id = NEW.opened_by_user_id
               AND actor.person_id = NEW.opened_by_person_id
               AND actor.authorization_version = NEW.authorization_version
               AND assignment.status IN ('active', 'expired', 'revoked')
               AND assignment.valid_from <= NEW.opened_at
               AND (assignment.valid_to IS NULL
                    OR NEW.opened_at < assignment.valid_to)
               AND (assignment.revoked_at IS NULL
                    OR NEW.opened_at < assignment.revoked_at)
               AND role.is_external = 0 AND role.code = NEW.role_code
               AND assignment.scope_type = NEW.scope_type
               AND assignment.scope_id = NEW.scope_id_snapshot
               AND ((NEW.role_code = 'admin'
                     AND NEW.scope_type = 'national'
                     AND NEW.scope_id_snapshot = '*')
                    OR (NEW.role_code = 'provincial_manager'
                        AND NEW.scope_type = 'organization'
                        AND replace(NEW.scope_id_snapshot, '-', '') =
                            replace(task.region_org_id, '-', '')))
       )
 )
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount case causality is invalid');
END
"""


def _sqlite_task_trigger_sql(*, opening_only: bool) -> str:
    trigger_name = OLD_TASK_TRIGGER if opening_only else TASK_TRIGGER
    type_predicate = (
        "NEW.task_type = 'opening'"
        if opening_only
        else f"NEW.task_type IN ('opening', {', '.join(repr(value) for value in ('full', 'sample', 'ad_hoc', 'personal', 'termination'))})"
    )
    scope_graph = (
        """recount_case.scope_count = (
                 SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.id)
             AND recount_case.scope_count = (
                 SELECT count(*) FROM stocktake_recount_scope_assignments
                  WHERE recount_case_id = recount_case.id)
             AND NOT EXISTS (
                 SELECT 1 FROM stocktake_scopes AS scope
                  WHERE scope.task_id = NEW.id AND NOT EXISTS (
                      SELECT 1
                        FROM stocktake_recount_scope_assignments AS assignment
                       WHERE assignment.recount_case_id = recount_case.id
                         AND assignment.task_id = NEW.id
                         AND assignment.scope_id = scope.id))"""
        if opening_only
        else _sqlite_scope_graph_sql("recount_case", "NEW")
    )
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE UPDATE ON stocktake_tasks
WHEN {type_predicate}
 AND ((NEW.current_round_no IS NOT OLD.current_round_no
       AND NEW.current_round_no > 1)
      OR (OLD.status = 'recount_required' AND NEW.status = 'counting'))
 AND NOT (
      OLD.status = 'recount_required' AND NEW.status = 'counting'
      AND NEW.current_round_no = OLD.current_round_no + 1
      AND EXISTS (
          SELECT 1 FROM stocktake_recount_cases AS recount_case
           WHERE recount_case.task_id = NEW.id
             AND recount_case.next_round_no = NEW.current_round_no
             AND recount_case.source_round_id = (
                 SELECT id FROM stocktake_rounds
                  WHERE task_id = NEW.id AND round_no = OLD.current_round_no)
             AND {scope_graph}
      )
      AND NOT EXISTS (SELECT 1 FROM stocktake_rounds
                       WHERE task_id = NEW.id
                         AND round_no = NEW.current_round_no)
 )
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount task advance is invalid');
END
"""


def _sqlite_round_insert_sql(*, opening_only: bool) -> str:
    trigger_name = OLD_SQLITE_ROUND_INSERT if opening_only else SQLITE_ROUND_INSERT
    scope_graph = (
        """recount_case.scope_count = (
               SELECT count(*) FROM stocktake_recount_scope_assignments
                WHERE recount_case_id = recount_case.id)
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_scopes AS scope
                WHERE scope.task_id = NEW.task_id AND NOT EXISTS (
                    SELECT 1
                      FROM stocktake_recount_scope_assignments AS assignment
                     WHERE assignment.recount_case_id = recount_case.id
                       AND assignment.task_id = NEW.task_id
                       AND assignment.scope_id = scope.id))"""
        if opening_only
        else _sqlite_scope_graph_sql("recount_case", "task")
    )
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON stocktake_rounds
WHEN NEW.status = 'superseded'
 OR (NEW.round_no = 1 AND (
        NEW.round_type <> 'initial' OR NEW.recount_case_id IS NOT NULL))
 OR (NEW.round_no > 1 AND NOT EXISTS (
        SELECT 1 FROM stocktake_recount_cases AS recount_case
        JOIN stocktake_tasks AS task ON task.id = recount_case.task_id
        JOIN stocktake_rounds AS source
          ON source.id = recount_case.source_round_id
         AND source.task_id = recount_case.task_id
         WHERE recount_case.id = NEW.recount_case_id
           AND recount_case.task_id = NEW.task_id
           AND recount_case.next_round_no = NEW.round_no
           AND NEW.round_type = 'recount'
           AND task.status = 'counting'
           AND task.current_round_no = NEW.round_no
           AND source.round_no = NEW.round_no - 1
           AND source.status = 'submitted'
           AND NEW.started_at >= recount_case.opened_at
           AND {scope_graph}
     ))
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount round causality is invalid');
END
"""


def _sqlite_round_update_sql(*, opening_only: bool) -> str:
    trigger_name = OLD_SQLITE_ROUND_UPDATE if opening_only else SQLITE_ROUND_UPDATE
    # At update time the immutable assignment graph was already checked by the
    # successor insert.  Re-check it for the 0032 path so later bypass writes
    # cannot detach a selected scope.
    scope_graph = (
        "1 = 1"
        if opening_only
        else _sqlite_scope_graph_sql("recount_case", "task")
    )
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE UPDATE ON stocktake_rounds
WHEN NEW.status = 'superseded'
 OR OLD.task_id IS NOT NEW.task_id OR OLD.round_no IS NOT NEW.round_no
 OR OLD.round_type IS NOT NEW.round_type
 OR OLD.recount_case_id IS NOT NEW.recount_case_id
 OR (NEW.round_no > 1 AND NOT EXISTS (
        SELECT 1 FROM stocktake_recount_cases AS recount_case
        JOIN stocktake_tasks AS task ON task.id = recount_case.task_id
        JOIN stocktake_rounds AS source
          ON source.id = recount_case.source_round_id
         AND source.task_id = recount_case.task_id
         WHERE recount_case.id = NEW.recount_case_id
           AND recount_case.task_id = NEW.task_id
           AND recount_case.next_round_no = NEW.round_no
           AND task.current_round_no = NEW.round_no
           AND source.round_no = NEW.round_no - 1
           AND source.status = 'submitted'
           AND {scope_graph}))
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount round causality is invalid');
END
"""
