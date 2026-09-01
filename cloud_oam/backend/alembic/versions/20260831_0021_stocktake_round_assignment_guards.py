"""Bind every stocktake round fact to its exact round assignment.

Revision ID: 20260831_0021
Revises: 20260831_0020
Create Date: 2026-08-31

Revision 0018 made recount rounds append-only and contiguous, but the count
line, observation and scope-completion guards inherited the frozen initial
scope assignee from revision 0011.  This revision keeps every existing seal,
snapshot, policy, serial, total and historical-RBAC check while resolving the
actor from the initial scope for round one and from the immutable recount-case
assignment for every later round.

The recount-case review edge is also made round-generic.  A successor may be
opened only from a terminal regional recount/reject (with no headquarters
review), or from a headquarters reject following an earlier approved regional
review by a different person.  No table data is rewritten.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260831_0021"
down_revision: Union[str, Sequence[str], None] = "20260831_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"

PG_ACTOR_FUNCTION = "rsc_stocktake_round_assignment_valid_0021"
PG_COUNT_LINE_FUNCTION = "rsc_validate_stocktake_count_line_insert_0021"
PG_OBSERVATION_FUNCTION = "rsc_validate_stocktake_observation_insert_0021"
PG_COMPLETION_FUNCTION = "rsc_validate_stocktake_scope_completion_insert_0021"
PG_CASE_FUNCTION = "rsc_validate_stocktake_recount_case_0021"

COUNT_LINE_TRIGGER = "trg_stocktake_count_lines_assignment_0021"
OBSERVATION_TRIGGER = "trg_stocktake_count_observations_assignment_0021"
COMPLETION_TRIGGER = "trg_stocktake_scope_completions_assignment_0021"
CASE_TRIGGER = "trg_stocktake_recount_cases_review_path_0021"

OLD_PG_COUNT_LINE_TRIGGER = "trg_stocktake_count_lines_completion_seal_0011"
OLD_SQLITE_COUNT_LINE_TRIGGER = (
    "trg_stocktake_count_lines_completion_seal_insert_0011"
)
OLD_OBSERVATION_TRIGGER = (
    "trg_stocktake_count_observations_validate_insert_0013"
)
OLD_COMPLETION_TRIGGER = (
    "trg_stocktake_scope_count_completions_validate_insert_0011"
)
OLD_CASE_TRIGGER = "trg_stocktake_recount_cases_validate_0018"

UPGRADE_BLOCKER = (
    "0021 preflight failed: existing recount evidence or trigger review graph "
    "does not match its immutable round assignment"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0021: stocktake recount graph or round facts exist"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0021 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0021 SQLite upgrade requires an online connection")
        _postgresql_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_graph()
        _online_preflight(dialect)

    if dialect == "postgresql":
        _replace_postgresql_guards(round_aware=True)
        _apply_postgresql_acl()
    else:
        _replace_sqlite_guards(round_aware=True)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0021 downgrade requires an online connection for fail-closed "
            "recount evidence checks"
        )
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_graph()
    _assert_no_recount_graph_or_facts()

    if dialect == "postgresql":
        _replace_postgresql_guards(round_aware=False)
    else:
        _replace_sqlite_guards(round_aware=False)


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
        "public.stocktake_count_lines, public.stocktake_count_observations, "
        "public.stocktake_scope_count_completions, "
        "public.stocktake_round_submissions, "
        "public.stocktake_difference_set_completions, "
        "public.stocktake_reviews, public.stocktake_postings, "
        "public.inventory_opening_establishments "
        "IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_preflight() -> None:
    op.execute(
        "LOCK TABLE public.stocktake_tasks, public.stocktake_rounds, "
        "public.stocktake_scopes, public.stocktake_recount_cases, "
        "public.stocktake_recount_scope_assignments, "
        "public.stocktake_count_lines, public.stocktake_count_observations, "
        "public.stocktake_scope_count_completions, "
        "public.stocktake_round_submissions, "
        "public.stocktake_difference_set_completions, "
        "public.stocktake_reviews, public.stocktake_postings, "
        "public.inventory_opening_establishments "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF {_invalid_existing_recount_evidence_sql('postgresql', schema_prefix='public.')}
       OR {_invalid_existing_recount_case_sql('postgresql', schema_prefix='public.')}
    THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_preflight(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    invalid = (
        f"{_invalid_existing_recount_evidence_sql(dialect, schema_prefix=prefix)} "
        f"OR {_invalid_existing_recount_case_sql(dialect, schema_prefix=prefix)}"
    )
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {invalid}"
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _assert_no_recount_graph_or_facts() -> None:
    if op.get_bind().exec_driver_sql(
        """
SELECT 1
 WHERE EXISTS (SELECT 1 FROM stocktake_recount_cases)
    OR EXISTS (SELECT 1 FROM stocktake_recount_scope_assignments)
    OR EXISTS (SELECT 1 FROM stocktake_rounds
                WHERE round_no > 1 OR round_type = 'recount'
                   OR recount_case_id IS NOT NULL)
    OR EXISTS (SELECT 1 FROM stocktake_tasks WHERE current_round_no > 1)
    OR EXISTS (SELECT 1 FROM stocktake_count_lines AS fact
                JOIN stocktake_rounds AS round_row ON round_row.id = fact.round_id
               WHERE round_row.round_no > 1)
    OR EXISTS (SELECT 1 FROM stocktake_count_observations AS fact
                JOIN stocktake_rounds AS round_row ON round_row.id = fact.round_id
               WHERE round_row.round_no > 1)
    OR EXISTS (SELECT 1 FROM stocktake_scope_count_completions AS fact
                JOIN stocktake_rounds AS round_row ON round_row.id = fact.round_id
               WHERE round_row.round_no > 1)
"""
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _invalid_existing_recount_evidence_sql(
    dialect: str, *, schema_prefix: str
) -> str:
    rounds = f"{schema_prefix}stocktake_rounds"
    assignments = f"{schema_prefix}stocktake_recount_scope_assignments"
    lines = f"{schema_prefix}stocktake_count_lines"
    observations = f"{schema_prefix}stocktake_count_observations"
    completions = f"{schema_prefix}stocktake_scope_count_completions"
    uuid_equal = (
        lambda left, right: f"{left} = {right}"
        if dialect == "postgresql"
        else f"replace(CAST({left} AS TEXT), '-', '') = replace(CAST({right} AS TEXT), '-', '')"
    )
    line_live_assignment = _existing_assignment_live_sql(
        dialect,
        schema_prefix=schema_prefix,
        fact_alias="fact",
        occurred_column="counted_at",
        require_fact_snapshot=False,
    )
    completion_live_assignment = _existing_assignment_live_sql(
        dialect,
        schema_prefix=schema_prefix,
        fact_alias="fact",
        occurred_column="completed_at",
        require_fact_snapshot=True,
    )
    return f"""EXISTS (
        SELECT 1
          FROM {rounds} AS round_row
          JOIN {lines} AS fact ON fact.round_id = round_row.id
           AND fact.task_id = round_row.task_id
         WHERE round_row.round_no > 1
           AND (round_row.round_type <> 'recount'
                OR round_row.recount_case_id IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM {assignments} AS assignment
                     WHERE {uuid_equal('assignment.recount_case_id', 'round_row.recount_case_id')}
                       AND assignment.task_id = fact.task_id
                       AND assignment.scope_id = fact.scope_id
                       AND assignment.assignee_user_id = fact.counted_by_user_id
                       AND assignment.assigned_at <= fact.counted_at
                       AND {line_live_assignment}))
    ) OR EXISTS (
        SELECT 1
          FROM {rounds} AS round_row
          JOIN {observations} AS fact ON fact.round_id = round_row.id
           AND fact.task_id = round_row.task_id
         WHERE round_row.round_no > 1
           AND (round_row.round_type <> 'recount'
                OR round_row.recount_case_id IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM {assignments} AS assignment
                     WHERE {uuid_equal('assignment.recount_case_id', 'round_row.recount_case_id')}
                       AND assignment.task_id = fact.task_id
                       AND assignment.scope_id = fact.scope_id
                       AND assignment.assignee_user_id = fact.counted_by_user_id
                       AND assignment.assigned_at <= fact.counted_at
                       AND {line_live_assignment}))
    ) OR EXISTS (
        SELECT 1
          FROM {rounds} AS round_row
          JOIN {completions} AS fact ON fact.round_id = round_row.id
           AND fact.task_id = round_row.task_id
         WHERE round_row.round_no > 1
           AND (round_row.round_type <> 'recount'
                OR round_row.recount_case_id IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM {assignments} AS assignment
                     WHERE {uuid_equal('assignment.recount_case_id', 'round_row.recount_case_id')}
                       AND assignment.task_id = fact.task_id
                       AND assignment.scope_id = fact.scope_id
                       AND assignment.assignee_user_id = fact.completed_by_user_id
                       AND assignment.assignee_person_id = fact.completed_by_person_id
                       AND assignment.assignee_role_assignment_id = fact.completed_role_assignment_id
                       AND fact.authorization_version >= assignment.authorization_version
                       AND assignment.role_code = fact.role_code
                       AND assignment.scope_type = fact.scope_type
                       AND assignment.scope_id_snapshot = fact.scope_id_snapshot
                       AND assignment.assigned_at <= fact.completed_at
                       AND {completion_live_assignment}))
    )"""


def _existing_assignment_live_sql(
    dialect: str,
    *,
    schema_prefix: str,
    fact_alias: str,
    occurred_column: str,
    require_fact_snapshot: bool,
) -> str:
    external_false = "false" if dialect == "postgresql" else "0"
    actor_version = (
        f"actor.authorization_version >= {fact_alias}.authorization_version"
        if require_fact_snapshot
        else "actor.authorization_version >= assignment.authorization_version"
    )
    return f"""EXISTS (
        SELECT 1
          FROM {schema_prefix}role_assignments AS live_assignment
          JOIN {schema_prefix}roles AS live_role
            ON live_role.id = live_assignment.role_id
          JOIN {schema_prefix}users AS actor
            ON actor.id = live_assignment.user_id
         WHERE live_assignment.id = assignment.assignee_role_assignment_id
           AND live_assignment.user_id = assignment.assignee_user_id
           AND actor.person_id = assignment.assignee_person_id
           AND {actor_version}
           AND live_assignment.status IN ('active', 'expired', 'revoked')
           AND live_assignment.valid_from <= {fact_alias}.{occurred_column}
           AND (live_assignment.valid_to IS NULL
                OR {fact_alias}.{occurred_column} < live_assignment.valid_to)
           AND (live_assignment.revoked_at IS NULL
                OR {fact_alias}.{occurred_column} < live_assignment.revoked_at)
           AND live_role.is_external = {external_false}
           AND live_role.code = assignment.role_code
           AND live_assignment.scope_type = assignment.scope_type
           AND live_assignment.scope_id = assignment.scope_id_snapshot
    )"""


def _invalid_existing_recount_case_sql(
    dialect: str, *, schema_prefix: str
) -> str:
    cases = f"{schema_prefix}stocktake_recount_cases"
    tasks = f"{schema_prefix}stocktake_tasks"
    reviews = f"{schema_prefix}stocktake_reviews"
    completions = f"{schema_prefix}stocktake_difference_set_completions"
    region_scope = (
        "manager_assignment.scope_id = task.region_org_id::text"
        if dialect == "postgresql"
        else "replace(manager_assignment.scope_id, '-', '') = task.region_org_id"
    )
    return f"""EXISTS (
        SELECT 1
          FROM {cases} AS recount_case
          JOIN {tasks} AS task ON task.id = recount_case.task_id
          JOIN {completions} AS completion
            ON completion.id = recount_case.source_difference_completion_id
          JOIN {reviews} AS trigger_review
            ON trigger_review.id = recount_case.trigger_review_id
           AND trigger_review.task_id = recount_case.task_id
           AND trigger_review.round_id = recount_case.source_round_id
         WHERE NOT (
            completion.task_id = recount_case.task_id
            AND completion.round_id = recount_case.source_round_id
            AND completion.completed_at <= trigger_review.reviewed_at
            AND trigger_review.reviewed_at <= recount_case.opened_at
            AND NOT EXISTS (
                SELECT 1 FROM {reviews} AS later_review
                 WHERE later_review.task_id = recount_case.task_id
                   AND later_review.round_id = recount_case.source_round_id
                   AND later_review.reviewed_at > trigger_review.reviewed_at)
            AND (
                (trigger_review.review_stage = 'region'
                 AND trigger_review.decision IN ('recount', 'reject')
                 AND EXISTS (
                    SELECT 1
                      FROM {schema_prefix}role_assignments AS manager_assignment
                      JOIN {schema_prefix}roles AS manager_role
                        ON manager_role.id = manager_assignment.role_id
                      JOIN {schema_prefix}users AS manager_user
                        ON manager_user.id = manager_assignment.user_id
                     WHERE manager_assignment.id = trigger_review.reviewer_role_assignment_id
                       AND manager_assignment.user_id = trigger_review.reviewer_user_id
                       AND manager_user.person_id = trigger_review.reviewer_person_id
                       AND manager_user.authorization_version >= trigger_review.authorization_version
                       AND manager_assignment.status IN ('active', 'expired', 'revoked')
                       AND manager_assignment.valid_from <= trigger_review.reviewed_at
                       AND (manager_assignment.valid_to IS NULL OR trigger_review.reviewed_at < manager_assignment.valid_to)
                       AND (manager_assignment.revoked_at IS NULL OR trigger_review.reviewed_at < manager_assignment.revoked_at)
                       AND manager_role.is_external = {('false' if dialect == 'postgresql' else '0')}
                       AND manager_role.code = 'provincial_manager'
                       AND manager_assignment.scope_type = 'organization'
                       AND {region_scope})
                 AND NOT EXISTS (
                    SELECT 1 FROM {reviews} AS headquarters_review
                     WHERE headquarters_review.task_id = recount_case.task_id
                       AND headquarters_review.round_id = recount_case.source_round_id
                       AND headquarters_review.review_stage = 'headquarters'))
                OR
                (trigger_review.review_stage = 'headquarters'
                 AND trigger_review.decision = 'reject'
                 AND EXISTS (
                    SELECT 1
                      FROM {schema_prefix}role_assignments AS headquarters_assignment
                      JOIN {schema_prefix}roles AS headquarters_role
                        ON headquarters_role.id = headquarters_assignment.role_id
                      JOIN {schema_prefix}users AS headquarters_user
                        ON headquarters_user.id = headquarters_assignment.user_id
                     WHERE headquarters_assignment.id = trigger_review.reviewer_role_assignment_id
                       AND headquarters_assignment.user_id = trigger_review.reviewer_user_id
                       AND headquarters_user.person_id = trigger_review.reviewer_person_id
                       AND headquarters_user.authorization_version >= trigger_review.authorization_version
                       AND headquarters_assignment.status IN ('active', 'expired', 'revoked')
                       AND headquarters_assignment.valid_from <= trigger_review.reviewed_at
                       AND (headquarters_assignment.valid_to IS NULL OR trigger_review.reviewed_at < headquarters_assignment.valid_to)
                       AND (headquarters_assignment.revoked_at IS NULL OR trigger_review.reviewed_at < headquarters_assignment.revoked_at)
                       AND headquarters_role.is_external = {('false' if dialect == 'postgresql' else '0')}
                       AND headquarters_role.code = 'admin'
                       AND headquarters_assignment.scope_type = 'national'
                       AND headquarters_assignment.scope_id = '*')
                 AND EXISTS (
                    SELECT 1 FROM {reviews} AS region_review
                     WHERE region_review.task_id = recount_case.task_id
                       AND region_review.round_id = recount_case.source_round_id
                       AND region_review.review_stage = 'region'
                       AND region_review.decision = 'approve'
                       AND completion.completed_at <= region_review.reviewed_at
                       AND region_review.reviewed_at < trigger_review.reviewed_at
                       AND region_review.reviewer_user_id <> trigger_review.reviewer_user_id
                       AND region_review.reviewer_person_id <> trigger_review.reviewer_person_id
                       AND region_review.reviewer_role_assignment_id <> trigger_review.reviewer_role_assignment_id
                       AND EXISTS (
                          SELECT 1
                            FROM {schema_prefix}role_assignments AS manager_assignment
                            JOIN {schema_prefix}roles AS manager_role
                              ON manager_role.id = manager_assignment.role_id
                            JOIN {schema_prefix}users AS manager_user
                              ON manager_user.id = manager_assignment.user_id
                           WHERE manager_assignment.id = region_review.reviewer_role_assignment_id
                             AND manager_assignment.user_id = region_review.reviewer_user_id
                             AND manager_user.person_id = region_review.reviewer_person_id
                             AND manager_user.authorization_version >= region_review.authorization_version
                             AND manager_assignment.status IN ('active', 'expired', 'revoked')
                             AND manager_assignment.valid_from <= region_review.reviewed_at
                             AND (manager_assignment.valid_to IS NULL OR region_review.reviewed_at < manager_assignment.valid_to)
                             AND (manager_assignment.revoked_at IS NULL OR region_review.reviewed_at < manager_assignment.revoked_at)
                             AND manager_role.is_external = {('false' if dialect == 'postgresql' else '0')}
                             AND manager_role.code = 'provincial_manager'
                             AND manager_assignment.scope_type = 'organization'
                             AND {region_scope}))
                )
            )
         )
    )"""


def _replace_postgresql_guards(*, round_aware: bool) -> None:
    if round_aware:
        op.execute(
            f"DROP TRIGGER {OLD_PG_COUNT_LINE_TRIGGER} "
            "ON public.stocktake_count_lines"
        )
        op.execute(
            f"DROP TRIGGER {OLD_OBSERVATION_TRIGGER} "
            "ON public.stocktake_count_observations"
        )
        op.execute(
            "DROP FUNCTION public.rsc_validate_stocktake_observation_insert_0013()"
        )
        op.execute(
            f"DROP TRIGGER {OLD_COMPLETION_TRIGGER} "
            "ON public.stocktake_scope_count_completions"
        )
        op.execute(
            "DROP FUNCTION public.rsc_validate_stocktake_scope_completion_insert_0011()"
        )
        op.execute(
            f"DROP TRIGGER {OLD_CASE_TRIGGER} ON public.stocktake_recount_cases"
        )
        op.execute(
            "DROP FUNCTION public.rsc_validate_stocktake_recount_case_0018()"
        )

        op.execute(_postgresql_actor_function_sql())
        op.execute(_postgresql_count_line_function_sql())
        op.execute(_postgresql_observation_function_sql(round_aware=True))
        op.execute(_postgresql_completion_function_sql(round_aware=True))
        op.execute(_postgresql_case_function_sql(round_aware=True))
        for table_name, trigger_name, function_name in (
            ("stocktake_count_lines", COUNT_LINE_TRIGGER, PG_COUNT_LINE_FUNCTION),
            (
                "stocktake_count_observations",
                OBSERVATION_TRIGGER,
                PG_OBSERVATION_FUNCTION,
            ),
            (
                "stocktake_scope_count_completions",
                COMPLETION_TRIGGER,
                PG_COMPLETION_FUNCTION,
            ),
            ("stocktake_recount_cases", CASE_TRIGGER, PG_CASE_FUNCTION),
        ):
            op.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON public.{table_name} "
                f"FOR EACH ROW EXECUTE FUNCTION public.{function_name}()"
            )
            op.execute(
                f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
            )
        return

    for table_name, trigger_name in (
        ("stocktake_count_lines", COUNT_LINE_TRIGGER),
        ("stocktake_count_observations", OBSERVATION_TRIGGER),
        ("stocktake_scope_count_completions", COMPLETION_TRIGGER),
        ("stocktake_recount_cases", CASE_TRIGGER),
    ):
        op.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
    for function_name in (
        PG_CASE_FUNCTION,
        PG_COMPLETION_FUNCTION,
        PG_OBSERVATION_FUNCTION,
        PG_COUNT_LINE_FUNCTION,
        PG_ACTOR_FUNCTION,
    ):
        signature = ""
        if function_name == PG_ACTOR_FUNCTION:
            signature = (
                "uuid, uuid, uuid, text, uuid, uuid, bigint, text, text, "
                "text, timestamptz, boolean"
            )
        op.execute(f"DROP FUNCTION public.{function_name}({signature})")

    op.execute(_postgresql_observation_function_sql(round_aware=False))
    op.execute(_postgresql_completion_function_sql(round_aware=False))
    op.execute(_postgresql_case_function_sql(round_aware=False))
    op.execute(
        f"CREATE TRIGGER {OLD_PG_COUNT_LINE_TRIGGER} BEFORE INSERT ON "
        "public.stocktake_count_lines FOR EACH ROW EXECUTE FUNCTION "
        "public.rsc_validate_stocktake_existing_count_child_0011()"
    )
    op.execute(
        f"CREATE TRIGGER {OLD_OBSERVATION_TRIGGER} BEFORE INSERT ON "
        "public.stocktake_count_observations FOR EACH ROW EXECUTE FUNCTION "
        "public.rsc_validate_stocktake_observation_insert_0013()"
    )
    op.execute(
        f"CREATE TRIGGER {OLD_COMPLETION_TRIGGER} BEFORE INSERT ON "
        "public.stocktake_scope_count_completions FOR EACH ROW EXECUTE FUNCTION "
        "public.rsc_validate_stocktake_scope_completion_insert_0011()"
    )
    op.execute(
        f"CREATE TRIGGER {OLD_CASE_TRIGGER} BEFORE INSERT ON "
        "public.stocktake_recount_cases FOR EACH ROW EXECUTE FUNCTION "
        "public.rsc_validate_stocktake_recount_case_0018()"
    )
    # Restore the exact historical origin-only enablement of the 0011/0013
    # validators; revision 0018's recount case trigger was ALWAYS.
    op.execute(
        "ALTER TABLE public.stocktake_recount_cases ENABLE ALWAYS TRIGGER "
        f"{OLD_CASE_TRIGGER}"
    )
    # These functions originally inherited the migrator-only ACL established
    # by 0015/0018.  Re-creation on downgrade must not restore PostgreSQL's
    # default PUBLIC EXECUTE grant.  Their owner is the migration identity
    # executing this revision, exactly as on the original upgrades.
    for function_name in (
        "rsc_validate_stocktake_observation_insert_0013",
        "rsc_validate_stocktake_scope_completion_insert_0011",
        "rsc_validate_stocktake_recount_case_0018",
    ):
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION public.{function_name}() "
            f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
        )


def _apply_postgresql_acl() -> None:
    for function_name, signature in (
        (
            PG_ACTOR_FUNCTION,
            "uuid, uuid, uuid, text, uuid, uuid, bigint, text, text, text, "
            "timestamptz, boolean",
        ),
        (PG_COUNT_LINE_FUNCTION, ""),
        (PG_OBSERVATION_FUNCTION, ""),
        (PG_COMPLETION_FUNCTION, ""),
        (PG_CASE_FUNCTION, ""),
    ):
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION public.{function_name}({signature}) "
            f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
        )


def _postgresql_actor_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_ACTOR_FUNCTION}(
    p_task_id uuid,
    p_round_id uuid,
    p_scope_id uuid,
    p_user_id text,
    p_person_id uuid,
    p_assignment_id uuid,
    p_authorization_version bigint,
    p_role_code text,
    p_scope_type text,
    p_scope_id_snapshot text,
    p_occurred_at timestamptz,
    p_require_full_snapshot boolean
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
          JOIN public.stocktake_rounds AS round_row
            ON round_row.task_id = task.id
           AND round_row.id = p_round_id
          JOIN public.stocktake_scopes AS scope
            ON scope.task_id = task.id
           AND scope.id = p_scope_id
         WHERE task.id = p_task_id
           AND task.task_type = 'opening'
           AND round_row.status = 'counting'
           AND (
                (round_row.round_no = 1
                 AND round_row.round_type = 'initial'
                 AND round_row.recount_case_id IS NULL
                 AND scope.assignee_user_id = p_user_id
                 AND (
                    NOT p_require_full_snapshot
                    OR (
                        public.rsc_stocktake_actor_assignment_valid_0011(
                            p_user_id, p_person_id, p_assignment_id,
                            p_authorization_version, p_occurred_at,
                            p_role_code, p_scope_type, p_scope_id_snapshot)
                        AND (
                            (p_role_code = 'admin'
                             AND p_scope_type = 'national'
                             AND p_scope_id_snapshot = '*')
                            OR (p_role_code = 'provincial_manager'
                                AND p_scope_type = 'organization'
                                AND p_scope_id_snapshot = scope.owner_org_id::text)
                            OR (p_role_code = 'technician'
                                AND p_scope_type = 'person'
                                AND scope.custodian_person_id_snapshot IS NOT NULL
                                AND p_scope_id_snapshot =
                                    scope.custodian_person_id_snapshot::text)
                        )
                    )
                 ))
                OR
                (round_row.round_no > 1
                 AND round_row.round_type = 'recount'
                 AND round_row.recount_case_id IS NOT NULL
                 AND task.status = 'counting'
                 AND task.current_round_no = round_row.round_no
                 AND EXISTS (
                    SELECT 1
                      FROM public.stocktake_recount_scope_assignments AS assignment
                     WHERE assignment.recount_case_id = round_row.recount_case_id
                       AND assignment.task_id = task.id
                       AND assignment.scope_id = scope.id
                       AND assignment.assignee_user_id = p_user_id
                       AND assignment.assigned_at <= p_occurred_at
                       AND (
                            NOT p_require_full_snapshot
                            OR (
                                assignment.assignee_person_id = p_person_id
                                AND assignment.assignee_role_assignment_id =
                                    p_assignment_id
                                AND p_authorization_version >=
                                    assignment.authorization_version
                                AND assignment.role_code = p_role_code
                                AND assignment.scope_type = p_scope_type
                                AND assignment.scope_id_snapshot =
                                    p_scope_id_snapshot
                            )
                       )
                       AND (
                            (p_require_full_snapshot
                             AND public.rsc_stocktake_actor_assignment_valid_0011(
                                p_user_id, p_person_id, p_assignment_id,
                                p_authorization_version, p_occurred_at,
                                p_role_code, p_scope_type,
                                p_scope_id_snapshot))
                            OR
                            (NOT p_require_full_snapshot AND EXISTS (
                                SELECT 1
                                  FROM public.role_assignments AS live_assignment
                                  JOIN public.roles AS live_role
                                    ON live_role.id = live_assignment.role_id
                                  JOIN public.users AS actor
                                    ON actor.id = live_assignment.user_id
                                 WHERE live_assignment.id =
                                       assignment.assignee_role_assignment_id
                                   AND live_assignment.user_id =
                                       assignment.assignee_user_id
                                   AND actor.person_id =
                                       assignment.assignee_person_id
                                   AND actor.authorization_version >=
                                       assignment.authorization_version
                                   AND live_assignment.status IN
                                       ('active', 'expired', 'revoked')
                                   AND live_assignment.valid_from <= p_occurred_at
                                   AND (live_assignment.valid_to IS NULL
                                        OR p_occurred_at < live_assignment.valid_to)
                                   AND (live_assignment.revoked_at IS NULL
                                        OR p_occurred_at <
                                           live_assignment.revoked_at)
                                   AND live_role.is_external = false
                                   AND live_role.code = assignment.role_code
                                   AND live_assignment.scope_type =
                                       assignment.scope_type
                                   AND live_assignment.scope_id =
                                       assignment.scope_id_snapshot
                            ))
                       )
                 ))
           )
    )
$$
"""


def _postgresql_count_line_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_COUNT_LINE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    parent_status text;
    parent_started_at timestamptz;
BEGIN
    SELECT status, started_at INTO parent_status, parent_started_at
      FROM public.stocktake_rounds
     WHERE id = NEW.round_id AND task_id = NEW.task_id
     FOR UPDATE;
    IF parent_status IS DISTINCT FROM 'counting'
       OR NEW.counted_at < parent_started_at
       OR EXISTS (SELECT 1 FROM public.stocktake_round_submissions
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
       OR EXISTS (SELECT 1 FROM public.stocktake_scope_count_completions
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id
                     AND scope_id = NEW.scope_id)
       OR NOT public.{PG_ACTOR_FUNCTION}(
            NEW.task_id, NEW.round_id, NEW.scope_id,
            NEW.counted_by_user_id, NULL, NULL, NULL, NULL, NULL, NULL,
            NEW.counted_at, false)
       OR NOT EXISTS (
            SELECT 1
              FROM public.stocktake_scopes AS scope
              JOIN public.stock_accounts AS account
                ON account.id = NEW.stock_account_id
              JOIN public.stocktake_snapshot_lines AS snapshot
                ON snapshot.task_id = NEW.task_id
               AND snapshot.scope_id = NEW.scope_id
               AND snapshot.stock_account_id = NEW.stock_account_id
             WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
               AND scope.owner_org_id = account.owner_org_id
               AND scope.location_id = account.location_id
               AND (scope.scope_mode = 'location_all' OR (
                    (scope.material_id IS NULL
                     OR scope.material_id = account.material_id)
                AND (scope.condition_code IS NULL
                     OR scope.condition_code = account.condition_code)
                AND (scope.availability_bucket IS NULL
                     OR scope.availability_bucket = account.availability_bucket)
               ))
       ) THEN
        RAISE EXCEPTION
            'stocktake count child is sealed, outside snapshot, or unassigned';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_observation_function_sql(*, round_aware: bool) -> str:
    function_name = (
        f"public.{PG_OBSERVATION_FUNCTION}"
        if round_aware
        else "public.rsc_validate_stocktake_observation_insert_0013"
    )
    assignment_declaration = "" if round_aware else "    scope_assignee text;\n"
    assignment_select = "" if round_aware else "assignee_user_id, "
    assignment_into = "" if round_aware else "scope_assignee, "
    actor_check = (
        f"OR NOT public.{PG_ACTOR_FUNCTION}(\n"
        "            NEW.task_id, NEW.round_id, NEW.scope_id,\n"
        "            NEW.counted_by_user_id, NULL, NULL, NULL, NULL, NULL, NULL,\n"
        "            NEW.counted_at, false)"
        if round_aware
        else "OR NEW.counted_by_user_id IS DISTINCT FROM scope_assignee"
    )
    search_path = (
        "SET search_path = pg_catalog, public\n" if round_aware else ""
    )
    return f"""
CREATE FUNCTION {function_name}()
RETURNS trigger
LANGUAGE plpgsql
{search_path}AS $$
DECLARE
    parent_status text;
    parent_started_at timestamptz;
    parent_cutoff_at timestamptz;
    scope_owner uuid;
    scope_location uuid;
    scope_custodian uuid;
{assignment_declaration}    scope_mode text;
    scope_material uuid;
    scope_condition text;
    scope_availability text;
    policy_count integer;
    policy_tracking text;
    policy_scale integer;
BEGIN
    SELECT round_row.status, round_row.started_at, task.cutoff_at
      INTO parent_status, parent_started_at, parent_cutoff_at
      FROM public.stocktake_rounds AS round_row
      JOIN public.stocktake_tasks AS task ON task.id = round_row.task_id
     WHERE round_row.id = NEW.round_id
       AND round_row.task_id = NEW.task_id
     FOR UPDATE OF round_row;
    IF parent_status IS DISTINCT FROM 'counting'
       OR parent_cutoff_at IS NULL
       OR NEW.counted_at < parent_started_at
       OR EXISTS (
           SELECT 1 FROM public.stocktake_round_submissions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       )
       OR EXISTS (
           SELECT 1 FROM public.stocktake_scope_count_completions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
              AND scope_id = NEW.scope_id
       ) THEN
        RAISE EXCEPTION 'stocktake observation scope is already sealed';
    END IF;

    SELECT owner_org_id, location_id, custodian_person_id_snapshot,
           {assignment_select}scope_mode, material_id, condition_code,
           availability_bucket
      INTO scope_owner, scope_location, scope_custodian,
           {assignment_into}scope_mode, scope_material, scope_condition,
           scope_availability
      FROM public.stocktake_scopes
     WHERE id = NEW.scope_id AND task_id = NEW.task_id;
    IF scope_owner IS NULL
       OR NEW.owner_org_id IS DISTINCT FROM scope_owner
       OR NEW.location_id IS DISTINCT FROM scope_location
       OR NEW.custodian_person_id_snapshot IS DISTINCT FROM scope_custodian
       {actor_check}
       OR (scope_mode = 'filtered' AND (
           (scope_material IS NOT NULL
            AND NEW.material_id IS DISTINCT FROM scope_material)
           OR (scope_condition IS NOT NULL
               AND NEW.condition_code IS DISTINCT FROM scope_condition)
           OR (scope_availability IS NOT NULL
               AND NEW.availability_bucket IS DISTINCT FROM scope_availability)
       )) THEN
        RAISE EXCEPTION 'stocktake observation is outside its frozen scope';
    END IF;
    IF NEW.material_id IS NOT NULL THEN
        SELECT count(*), min(tracking_mode), min(quantity_scale)
          INTO policy_count, policy_tracking, policy_scale
          FROM public.material_inventory_policies
         WHERE material_id = NEW.material_id
           AND effective_from <= parent_cutoff_at
           AND (effective_to IS NULL OR parent_cutoff_at < effective_to);
        IF policy_count <> 1
           OR NEW.counted_qty <> round(NEW.counted_qty, policy_scale)
           OR (policy_tracking = 'none' AND
               (NEW.lot_no_raw IS NOT NULL OR NEW.serial_no_raw IS NOT NULL))
           OR (policy_tracking = 'lot' AND
               (NEW.lot_no_raw IS NULL OR NEW.serial_no_raw IS NOT NULL))
           OR (policy_tracking = 'serial' AND
               (NEW.lot_no_raw IS NOT NULL OR NEW.serial_no_raw IS NULL))
           OR (policy_tracking = 'lot_and_serial' AND
               (NEW.lot_no_raw IS NULL OR NEW.serial_no_raw IS NULL)) THEN
            RAISE EXCEPTION
                'stocktake observation violates cutoff tracking policy';
        END IF;
    END IF;
    IF NEW.lot_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.inventory_lots
         WHERE id = NEW.lot_id AND material_id = NEW.material_id
           AND lot_no = NEW.lot_no_raw
    ) THEN
        RAISE EXCEPTION 'stocktake observation lot mapping is inconsistent';
    END IF;
    IF NEW.serial_id IS NOT NULL THEN
        IF NEW.serial_identifier_type IS NULL
           OR NEW.serial_identifier_type = 'unknown' THEN
            RAISE EXCEPTION
                'stocktake observation serial identifier mapping is inconsistent';
        ELSIF NEW.serial_identifier_type = 'serial_no' THEN
            IF NOT EXISTS (
                SELECT 1 FROM public.inventory_serials AS serial
                 WHERE serial.id = NEW.serial_id
                   AND serial.material_id = NEW.material_id
                   AND serial.serial_no = NEW.serial_no_raw
                   AND serial.lot_id IS NOT DISTINCT FROM NEW.lot_id
            ) THEN
                RAISE EXCEPTION
                    'stocktake observation serial identifier mapping is inconsistent';
            END IF;
        ELSIF NEW.serial_identifier_type = 'qr_code' THEN
            IF NOT EXISTS (
                SELECT 1 FROM public.inventory_serials AS serial
                 WHERE serial.id = NEW.serial_id
                   AND serial.material_id = NEW.material_id
                   AND serial.qr_code = NEW.serial_no_raw
                   AND serial.lot_id IS NOT DISTINCT FROM NEW.lot_id
            ) OR (
                SELECT count(*) FROM public.qr_codes AS mapping
                 WHERE mapping.code = NEW.serial_no_raw
                   AND mapping.object_type = 'serial'
                   AND mapping.object_id = NEW.serial_id
                   AND mapping.status = 'active'
            ) <> 1 THEN
                RAISE EXCEPTION
                    'stocktake observation serial identifier mapping is inconsistent';
            END IF;
        ELSE
            RAISE EXCEPTION
                'stocktake observation serial identifier mapping is inconsistent';
        END IF;
    END IF;

    IF NEW.verification_status = 'verified' AND (
        EXISTS (
            SELECT 1 FROM public.stock_accounts AS account
             WHERE account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS NOT DISTINCT FROM
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NOT DISTINCT FROM NEW.lot_id
               AND account.created_at <= parent_cutoff_at
        ) OR EXISTS (
            SELECT 1
              FROM public.stocktake_snapshot_lines AS snapshot
              JOIN public.stock_accounts AS account
                ON account.id = snapshot.stock_account_id
             WHERE snapshot.task_id = NEW.task_id
               AND snapshot.scope_id = NEW.scope_id
               AND account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS NOT DISTINCT FROM
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NOT DISTINCT FROM NEW.lot_id
        )
    ) THEN
        RAISE EXCEPTION
            'verified observation must use the cutoff account count line';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_completion_function_sql(*, round_aware: bool) -> str:
    function_name = (
        f"public.{PG_COMPLETION_FUNCTION}"
        if round_aware
        else "public.rsc_validate_stocktake_scope_completion_insert_0011"
    )
    search_path = (
        "SET search_path = pg_catalog, public\n" if round_aware else ""
    )
    actor_guard = (
        f"NOT public.{PG_ACTOR_FUNCTION}(\n"
        "        NEW.task_id, NEW.round_id, NEW.scope_id,\n"
        "        NEW.completed_by_user_id, NEW.completed_by_person_id,\n"
        "        NEW.completed_role_assignment_id, NEW.authorization_version,\n"
        "        NEW.role_code, NEW.scope_type, NEW.scope_id_snapshot,\n"
        "        NEW.completed_at, true)"
        if round_aware
        else """NEW.completed_by_user_id IS DISTINCT FROM scope_assignee
       OR NOT public.rsc_stocktake_actor_assignment_valid_0011(
            NEW.completed_by_user_id, NEW.completed_by_person_id,
            NEW.completed_role_assignment_id, NEW.authorization_version,
            NEW.completed_at, NEW.role_code, NEW.scope_type,
            NEW.scope_id_snapshot)
       OR NOT (
            (NEW.role_code = 'admin' AND NEW.scope_type = 'national'
             AND NEW.scope_id_snapshot = '*')
            OR (NEW.role_code = 'provincial_manager'
                AND NEW.scope_type = 'organization'
                AND NEW.scope_id_snapshot = scope_owner::text)
            OR (NEW.role_code = 'technician' AND NEW.scope_type = 'person'
                AND scope_custodian IS NOT NULL
                AND NEW.scope_id_snapshot = scope_custodian::text)
       )"""
    )
    return f"""
CREATE FUNCTION {function_name}()
RETURNS trigger
LANGUAGE plpgsql
{search_path}AS $$
DECLARE
    parent_status text;
    parent_started_at timestamptz;
    parent_cutoff_at timestamptz;
    scope_assignee text;
    scope_owner uuid;
    scope_custodian uuid;
    actual_count_lines integer;
    actual_observations integer;
    actual_serials integer;
    actual_total numeric(18, 3);
BEGIN
    SELECT round_row.status, round_row.started_at, task.cutoff_at
      INTO parent_status, parent_started_at, parent_cutoff_at
      FROM public.stocktake_rounds AS round_row
      JOIN public.stocktake_tasks AS task ON task.id = round_row.task_id
     WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
     FOR UPDATE OF round_row;
    SELECT assignee_user_id, owner_org_id, custodian_person_id_snapshot
      INTO scope_assignee, scope_owner, scope_custodian
      FROM public.stocktake_scopes
     WHERE id = NEW.scope_id AND task_id = NEW.task_id;
    IF parent_status IS DISTINCT FROM 'counting'
       OR scope_assignee IS NULL
       OR {actor_guard}
       OR NEW.completed_at < parent_started_at
       OR EXISTS (SELECT 1 FROM public.stocktake_round_submissions
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id) THEN
        RAISE EXCEPTION 'stocktake scope completion is invalid or sealed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.stocktake_count_lines
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id
           AND counted_by_user_id <> NEW.completed_by_user_id
    ) OR EXISTS (
        SELECT 1 FROM public.stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id
           AND counted_by_user_id <> NEW.completed_by_user_id
    ) THEN
        RAISE EXCEPTION 'scope completion cannot combine different count actors';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.stocktake_snapshot_lines AS snapshot
         WHERE snapshot.task_id = NEW.task_id AND snapshot.scope_id = NEW.scope_id
           AND NOT EXISTS (
               SELECT 1 FROM public.stocktake_count_lines AS line
                WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
                  AND line.scope_id = NEW.scope_id
                  AND line.stock_account_id = snapshot.stock_account_id
           )
    ) OR EXISTS (
        SELECT 1 FROM public.stocktake_count_lines AS line
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND NOT EXISTS (
               SELECT 1 FROM public.stocktake_snapshot_lines AS snapshot
                WHERE snapshot.task_id = NEW.task_id
                  AND snapshot.scope_id = NEW.scope_id
                  AND snapshot.stock_account_id = line.stock_account_id
           )
    ) THEN
        RAISE EXCEPTION
            'scope completion must cover every cutoff snapshot account exactly once';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_count_lines AS line
          JOIN public.stock_accounts AS account ON account.id = line.stock_account_id
          JOIN LATERAL (
              SELECT count(*) AS policy_count,
                     min(policy.tracking_mode) AS tracking_mode,
                     min(policy.quantity_scale) AS quantity_scale,
                     bool_and(policy.allow_fraction) AS allow_fraction
                FROM public.material_inventory_policies AS policy
               WHERE policy.material_id = account.material_id
                 AND policy.effective_from <= parent_cutoff_at
                 AND (policy.effective_to IS NULL
                      OR parent_cutoff_at < policy.effective_to)
          ) AS cutoff_policy ON true
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND (
               cutoff_policy.policy_count <> 1
               OR line.counted_qty <> round(
                   line.counted_qty, cutoff_policy.quantity_scale)
               OR (NOT cutoff_policy.allow_fraction
                   AND line.counted_qty <> trunc(line.counted_qty))
               OR (cutoff_policy.tracking_mode IN ('none', 'serial')
                   AND account.lot_id IS NOT NULL)
               OR (cutoff_policy.tracking_mode IN ('lot', 'lot_and_serial')
                   AND account.lot_id IS NULL)
               OR (cutoff_policy.tracking_mode IN ('none', 'lot') AND EXISTS (
                   SELECT 1 FROM public.stocktake_count_serials AS serial_row
                    WHERE serial_row.count_line_id = line.id
                      AND serial_row.round_id = line.round_id
               ))
               OR (cutoff_policy.tracking_mode IN ('serial', 'lot_and_serial')
                   AND line.counted_qty <> (
                       SELECT count(*)
                         FROM public.stocktake_count_serials AS serial_row
                        WHERE serial_row.count_line_id = line.id
                          AND serial_row.round_id = line.round_id
                          AND serial_row.result <> 'missing'
                   ))
           )
    ) THEN
        RAISE EXCEPTION 'count line violates its cutoff inventory tracking policy';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_count_serials AS serial_row
          JOIN public.stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
          JOIN public.stock_accounts AS account ON account.id = line.stock_account_id
          JOIN public.inventory_serials AS serial ON serial.id = serial_row.serial_id
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND (serial.material_id IS DISTINCT FROM account.material_id
                OR serial.lot_id IS DISTINCT FROM account.lot_id)
    ) THEN
        RAISE EXCEPTION 'count serial material or lot does not match its account';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_count_serials AS serial_row
          JOIN public.stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
          JOIN public.inventory_serials AS serial ON serial.id = serial_row.serial_id
          JOIN public.stocktake_count_observations AS observation
            ON observation.round_id = line.round_id
           AND observation.serial_no_raw IS NOT NULL
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND (
               observation.serial_id = serial_row.serial_id
               OR (observation.serial_identifier_type IN ('qr_code', 'unknown')
                   AND observation.serial_no_raw = serial.qr_code)
               OR (observation.serial_identifier_type IN ('serial_no', 'unknown')
                   AND observation.serial_no_raw = serial.serial_no
                   AND (observation.material_id IS NULL
                        OR observation.material_id = serial.material_id))
           )
    ) THEN
        RAISE EXCEPTION
            'one physical serial cannot be both count serial and observation';
    END IF;
    SELECT count(*), COALESCE(sum(counted_qty), 0)
      INTO actual_count_lines, actual_total
      FROM public.stocktake_count_lines
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND scope_id = NEW.scope_id;
    SELECT count(*), actual_total + COALESCE(sum(counted_qty), 0)
      INTO actual_observations, actual_total
      FROM public.stocktake_count_observations
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND scope_id = NEW.scope_id;
    SELECT count(*) INTO actual_serials
      FROM public.stocktake_count_serials AS serial_row
      JOIN public.stocktake_count_lines AS line
        ON line.id = serial_row.count_line_id AND line.round_id = serial_row.round_id
     WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
       AND line.scope_id = NEW.scope_id;
    actual_serials := actual_serials + (
        SELECT count(*) FROM public.stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id AND serial_no_raw IS NOT NULL
    );
    IF NEW.count_line_count <> actual_count_lines
       OR NEW.observation_line_count <> actual_observations
       OR NEW.serial_count <> actual_serials
       OR NEW.total_counted_qty <> actual_total
       OR (NEW.zero_confirmed AND EXISTS (
           SELECT 1 FROM public.stocktake_snapshot_lines
            WHERE task_id = NEW.task_id AND scope_id = NEW.scope_id
       )) THEN
        RAISE EXCEPTION 'stocktake scope completion totals are not canonical';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_historical_review_actor_sql(
    review_alias: str, *, role_code: str, scope_type: str, scope_id_sql: str
) -> str:
    return f"""EXISTS (
        SELECT 1
          FROM public.role_assignments AS review_assignment
          JOIN public.roles AS review_role
            ON review_role.id = review_assignment.role_id
          JOIN public.users AS review_actor
            ON review_actor.id = review_assignment.user_id
         WHERE review_assignment.id =
               {review_alias}.reviewer_role_assignment_id
           AND review_assignment.user_id = {review_alias}.reviewer_user_id
           AND review_actor.person_id = {review_alias}.reviewer_person_id
           AND review_actor.authorization_version >=
               {review_alias}.authorization_version
           AND review_assignment.status IN ('active', 'expired', 'revoked')
           AND review_assignment.valid_from <= {review_alias}.reviewed_at
           AND (review_assignment.valid_to IS NULL
                OR {review_alias}.reviewed_at < review_assignment.valid_to)
           AND (review_assignment.revoked_at IS NULL
                OR {review_alias}.reviewed_at < review_assignment.revoked_at)
           AND review_role.is_external = false
           AND review_role.code = '{role_code}'
           AND review_assignment.scope_type = '{scope_type}'
           AND review_assignment.scope_id = {scope_id_sql}
    )"""


def _postgresql_case_function_sql(*, round_aware: bool) -> str:
    function_name = (
        f"public.{PG_CASE_FUNCTION}"
        if round_aware
        else "public.rsc_validate_stocktake_recount_case_0018"
    )
    search_path = (
        "SET search_path = pg_catalog, public\n" if round_aware else ""
    )
    region_actor = _postgresql_historical_review_actor_sql(
        "source_review",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_sql="task_row.region_org_id::text",
    )
    headquarters_actor = _postgresql_historical_review_actor_sql(
        "source_review",
        role_code="admin",
        scope_type="national",
        scope_id_sql="'*'",
    )
    prior_region_actor = _postgresql_historical_review_actor_sql(
        "region_review",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_sql="task_row.region_org_id::text",
    )
    review_guard = (
        f"""NOT (
            (source_review.review_stage = 'region'
             AND source_review.decision IN ('recount', 'reject')
             AND {region_actor}
             AND NOT EXISTS (
                SELECT 1 FROM public.stocktake_reviews AS headquarters_review
                 WHERE headquarters_review.task_id = NEW.task_id
                   AND headquarters_review.round_id = NEW.source_round_id
                   AND headquarters_review.review_stage = 'headquarters'))
            OR
            (source_review.review_stage = 'headquarters'
             AND source_review.decision = 'reject'
             AND {headquarters_actor}
             AND EXISTS (
                SELECT 1 FROM public.stocktake_reviews AS region_review
                 WHERE region_review.task_id = NEW.task_id
                   AND region_review.round_id = NEW.source_round_id
                   AND region_review.review_stage = 'region'
                   AND region_review.decision = 'approve'
                   AND source_completion.completed_at <= region_review.reviewed_at
                   AND region_review.reviewed_at < source_review.reviewed_at
                   AND region_review.reviewer_user_id <>
                       source_review.reviewer_user_id
                   AND region_review.reviewer_person_id <>
                       source_review.reviewer_person_id
                   AND region_review.reviewer_role_assignment_id <>
                       source_review.reviewer_role_assignment_id
                   AND {prior_region_actor}))
       )"""
        if round_aware
        else "source_review.decision <> 'recount'"
    )
    return f"""
CREATE FUNCTION {function_name}()
RETURNS trigger
LANGUAGE plpgsql
{search_path}AS $$
DECLARE
    task_row public.stocktake_tasks%ROWTYPE;
    source_round public.stocktake_rounds%ROWTYPE;
    source_submission public.stocktake_round_submissions%ROWTYPE;
    source_completion public.stocktake_difference_set_completions%ROWTYPE;
    source_review public.stocktake_reviews%ROWTYPE;
    actual_scope_count bigint;
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
    SELECT count(*) INTO actual_scope_count FROM public.stocktake_scopes
     WHERE task_id = NEW.task_id;

    IF task_row.id IS NULL OR task_row.task_type <> 'opening'
       OR task_row.status <> 'recount_required'
       OR task_row.current_round_no IS DISTINCT FROM source_round.round_no
       OR task_row.scope_manifest_sha256 IS NULL
       OR NEW.scope_manifest_sha256 IS DISTINCT FROM task_row.scope_manifest_sha256
       OR source_round.id IS NULL OR source_round.status <> 'submitted'
       OR source_round.submitted_at IS NULL
       OR NEW.next_round_no <> source_round.round_no + 1
       OR source_submission.id IS NULL
       OR source_submission.task_id <> NEW.task_id
       OR source_submission.round_id <> NEW.source_round_id
       OR source_completion.id IS NULL
       OR source_completion.task_id <> NEW.task_id
       OR source_completion.round_id <> NEW.source_round_id
       OR source_completion.round_submission_id <>
          NEW.source_round_submission_id
       OR source_review.id IS NULL OR {review_guard}
       OR source_submission.submitted_at > source_completion.completed_at
       OR source_completion.completed_at > source_review.reviewed_at
       OR source_review.reviewed_at > NEW.opened_at
       OR actual_scope_count = 0 OR NEW.scope_count <> actual_scope_count
       OR EXISTS (SELECT 1 FROM public.stocktake_reviews
                   WHERE task_id = NEW.task_id
                     AND round_id = NEW.source_round_id
                     AND reviewed_at > source_review.reviewed_at)
       OR EXISTS (SELECT 1 FROM public.stocktake_postings
                   WHERE task_id = NEW.task_id
                     AND round_id = NEW.source_round_id)
       OR EXISTS (SELECT 1 FROM public.inventory_opening_establishments
                   WHERE task_id = NEW.task_id
                     AND round_id = NEW.source_round_id)
       OR EXISTS (SELECT 1 FROM public.stocktake_rounds
                   WHERE task_id = NEW.task_id
                     AND round_no = NEW.next_round_no)
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
                AND NEW.scope_id_snapshot = task_row.region_org_id::text)
       ) THEN
        RAISE EXCEPTION 'stocktake recount case causality is invalid';
    END IF;
    RETURN NEW;
END;
$$
"""


def _replace_sqlite_guards(*, round_aware: bool) -> None:
    for trigger_name in (
        OLD_SQLITE_COUNT_LINE_TRIGGER,
        OLD_OBSERVATION_TRIGGER,
        OLD_COMPLETION_TRIGGER,
        OLD_CASE_TRIGGER,
        COUNT_LINE_TRIGGER,
        OBSERVATION_TRIGGER,
        COMPLETION_TRIGGER,
        CASE_TRIGGER,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    op.execute(_sqlite_count_line_trigger_sql(round_aware=round_aware))
    op.execute(_sqlite_observation_trigger_sql(round_aware=round_aware))
    op.execute(_sqlite_completion_trigger_sql(round_aware=round_aware))
    op.execute(_sqlite_case_trigger_sql(round_aware=round_aware))


def _sqlite_actor_predicate(
    *,
    full_snapshot: bool,
    user_column: str,
    occurred_column: str,
    person_column: str = "NULL",
    assignment_column: str = "NULL",
    authorization_version_column: str = "NULL",
    role_column: str = "NULL",
    scope_type_column: str = "NULL",
    scope_id_column: str = "NULL",
) -> str:
    recount_snapshot = (
        f"""AND assignment.assignee_person_id = {person_column}
                       AND assignment.assignee_role_assignment_id = {assignment_column}
                       AND {authorization_version_column} >= assignment.authorization_version
                       AND assignment.role_code = {role_column}
                       AND assignment.scope_type = {scope_type_column}
                       AND assignment.scope_id_snapshot = {scope_id_column}"""
        if full_snapshot
        else ""
    )
    live_version = (
        f"actor.authorization_version = {authorization_version_column}"
        if full_snapshot
        else "actor.authorization_version >= assignment.authorization_version"
    )
    initial_snapshot = (
        f"""AND EXISTS (
                    SELECT 1
                      FROM role_assignments AS initial_assignment
                      JOIN roles AS initial_role
                        ON initial_role.id = initial_assignment.role_id
                      JOIN users AS initial_actor
                        ON initial_actor.id = initial_assignment.user_id
                     WHERE initial_assignment.id = {assignment_column}
                       AND initial_assignment.user_id = {user_column}
                       AND initial_actor.person_id = {person_column}
                       AND initial_actor.authorization_version =
                           {authorization_version_column}
                       AND initial_assignment.status IN
                           ('active', 'expired', 'revoked')
                       AND initial_assignment.valid_from <= {occurred_column}
                       AND (initial_assignment.valid_to IS NULL
                            OR {occurred_column} < initial_assignment.valid_to)
                       AND (initial_assignment.revoked_at IS NULL
                            OR {occurred_column} < initial_assignment.revoked_at)
                       AND initial_role.is_external = 0
                       AND initial_role.code = {role_column}
                       AND initial_assignment.scope_type = {scope_type_column}
                       AND initial_assignment.scope_id = {scope_id_column})
                 AND (({role_column} = 'admin'
                       AND {scope_type_column} = 'national'
                       AND {scope_id_column} = '*')
                      OR ({role_column} = 'provincial_manager'
                          AND {scope_type_column} = 'organization'
                          AND replace({scope_id_column}, '-', '') =
                              scope.owner_org_id)
                      OR ({role_column} = 'technician'
                          AND {scope_type_column} = 'person'
                          AND scope.custodian_person_id_snapshot IS NOT NULL
                          AND replace({scope_id_column}, '-', '') =
                              scope.custodian_person_id_snapshot))"""
        if full_snapshot
        else ""
    )
    return f"""EXISTS (
        SELECT 1
          FROM stocktake_tasks AS task
          JOIN stocktake_rounds AS round_row
            ON round_row.task_id = task.id AND round_row.id = NEW.round_id
          JOIN stocktake_scopes AS scope
            ON scope.task_id = task.id AND scope.id = NEW.scope_id
         WHERE task.id = NEW.task_id
           AND task.task_type = 'opening'
           AND round_row.status = 'counting'
           AND (
                (round_row.round_no = 1
                 AND round_row.round_type = 'initial'
                 AND round_row.recount_case_id IS NULL
                 AND scope.assignee_user_id = {user_column}
                 {initial_snapshot})
                OR
                (round_row.round_no > 1
                 AND round_row.round_type = 'recount'
                 AND round_row.recount_case_id IS NOT NULL
                 AND task.status = 'counting'
                 AND task.current_round_no = round_row.round_no
                 AND EXISTS (
                    SELECT 1
                      FROM stocktake_recount_scope_assignments AS assignment
                     WHERE assignment.recount_case_id = round_row.recount_case_id
                       AND assignment.task_id = task.id
                       AND assignment.scope_id = scope.id
                       AND assignment.assignee_user_id = {user_column}
                       AND assignment.assigned_at <= {occurred_column}
                       {recount_snapshot}
                       AND EXISTS (
                          SELECT 1
                            FROM role_assignments AS live_assignment
                            JOIN roles AS live_role
                              ON live_role.id = live_assignment.role_id
                            JOIN users AS actor
                              ON actor.id = live_assignment.user_id
                           WHERE live_assignment.id =
                                 assignment.assignee_role_assignment_id
                             AND live_assignment.user_id =
                                 assignment.assignee_user_id
                             AND actor.person_id = assignment.assignee_person_id
                             AND {live_version}
                             AND live_assignment.status IN
                                 ('active', 'expired', 'revoked')
                             AND live_assignment.valid_from <= {occurred_column}
                             AND (live_assignment.valid_to IS NULL
                                  OR {occurred_column} < live_assignment.valid_to)
                             AND (live_assignment.revoked_at IS NULL
                                  OR {occurred_column} <
                                     live_assignment.revoked_at)
                             AND live_role.is_external = 0
                             AND live_role.code = assignment.role_code
                             AND live_assignment.scope_type =
                                 assignment.scope_type
                             AND live_assignment.scope_id =
                                 assignment.scope_id_snapshot)
                 ))
           )
    )"""


def _sqlite_count_line_trigger_sql(*, round_aware: bool) -> str:
    trigger_name = (
        COUNT_LINE_TRIGGER if round_aware else OLD_SQLITE_COUNT_LINE_TRIGGER
    )
    actor_predicate = (
        _sqlite_actor_predicate(
            full_snapshot=False,
            user_column="NEW.counted_by_user_id",
            occurred_column="NEW.counted_at",
        )
        if round_aware
        else """EXISTS (
            SELECT 1 FROM stocktake_scopes AS scope
             WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
               AND scope.assignee_user_id = NEW.counted_by_user_id)"""
    )
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON stocktake_count_lines
WHEN COALESCE((SELECT status FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
       <> 'counting'
 OR NEW.counted_at < (SELECT started_at FROM stocktake_rounds
                       WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM stocktake_round_submissions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_scope_count_completions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id
               AND scope_id = NEW.scope_id)
 OR NOT ({actor_predicate})
 OR NOT EXISTS (
    SELECT 1
      FROM stocktake_scopes AS scope
      JOIN stock_accounts AS account ON account.id = NEW.stock_account_id
      JOIN stocktake_snapshot_lines AS snapshot
        ON snapshot.task_id = NEW.task_id
       AND snapshot.scope_id = NEW.scope_id
       AND snapshot.stock_account_id = NEW.stock_account_id
     WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
       AND scope.owner_org_id = account.owner_org_id
       AND scope.location_id = account.location_id
       AND (scope.scope_mode = 'location_all' OR (
            (scope.material_id IS NULL OR scope.material_id = account.material_id)
        AND (scope.condition_code IS NULL
             OR scope.condition_code = account.condition_code)
        AND (scope.availability_bucket IS NULL
             OR scope.availability_bucket = account.availability_bucket)
       ))
 )
BEGIN
    SELECT RAISE(ABORT,
        'stocktake count child is sealed, outside snapshot, or unassigned');
END
"""


def _sqlite_observation_trigger_sql(*, round_aware: bool) -> str:
    trigger_name = OBSERVATION_TRIGGER if round_aware else OLD_OBSERVATION_TRIGGER
    actor_predicate = (
        _sqlite_actor_predicate(
            full_snapshot=False,
            user_column="NEW.counted_by_user_id",
            occurred_column="NEW.counted_at",
        )
        if round_aware
        else """EXISTS (
            SELECT 1 FROM stocktake_scopes AS scope
             WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
               AND scope.assignee_user_id = NEW.counted_by_user_id)"""
    )
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON stocktake_count_observations
WHEN COALESCE((SELECT status FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
       <> 'counting'
 OR (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id) IS NULL
 OR NEW.counted_at < (SELECT started_at FROM stocktake_rounds
                       WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM stocktake_round_submissions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_scope_count_completions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id
               AND scope_id = NEW.scope_id)
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_scopes AS scope
         WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
           AND scope.owner_org_id = NEW.owner_org_id
           AND scope.location_id = NEW.location_id
           AND scope.custodian_person_id_snapshot IS
               NEW.custodian_person_id_snapshot
           AND (scope.scope_mode = 'location_all' OR (
                (scope.material_id IS NULL OR scope.material_id = NEW.material_id)
            AND (scope.condition_code IS NULL OR
                 scope.condition_code = NEW.condition_code)
            AND (scope.availability_bucket IS NULL OR
                 scope.availability_bucket = NEW.availability_bucket)
           ))
    )
 OR NOT ({actor_predicate})
 OR (NEW.material_id IS NOT NULL AND (
        (SELECT count(*) FROM material_inventory_policies
          WHERE material_id = NEW.material_id
            AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                     WHERE id = NEW.task_id)
            AND (effective_to IS NULL OR
                 (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                 < effective_to)) <> 1
        OR NEW.counted_qty <> round(
            NEW.counted_qty,
            (SELECT quantity_scale FROM material_inventory_policies
              WHERE material_id = NEW.material_id
                AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                         WHERE id = NEW.task_id)
                AND (effective_to IS NULL OR
                     (SELECT cutoff_at FROM stocktake_tasks
                       WHERE id = NEW.task_id) < effective_to))
        )
        OR CASE (SELECT tracking_mode FROM material_inventory_policies
                  WHERE material_id = NEW.material_id
                    AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                             WHERE id = NEW.task_id)
                    AND (effective_to IS NULL OR
                         (SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id) < effective_to))
             WHEN 'none' THEN NEW.lot_no_raw IS NOT NULL
                              OR NEW.serial_no_raw IS NOT NULL
             WHEN 'lot' THEN NEW.lot_no_raw IS NULL
                             OR NEW.serial_no_raw IS NOT NULL
             WHEN 'serial' THEN NEW.lot_no_raw IS NOT NULL
                                OR NEW.serial_no_raw IS NULL
             WHEN 'lot_and_serial' THEN NEW.lot_no_raw IS NULL
                                        OR NEW.serial_no_raw IS NULL
             ELSE 1
           END
    ))
 OR (NEW.lot_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_lots WHERE id = NEW.lot_id
          AND material_id = NEW.material_id AND lot_no = NEW.lot_no_raw
    ))
 OR (NEW.serial_id IS NOT NULL AND (
        NEW.serial_identifier_type IS NULL
        OR NEW.serial_identifier_type = 'unknown'
        OR (NEW.serial_identifier_type = 'serial_no' AND NOT EXISTS (
            SELECT 1 FROM inventory_serials AS serial
             WHERE serial.id = NEW.serial_id
               AND serial.material_id = NEW.material_id
               AND serial.serial_no = NEW.serial_no_raw
               AND serial.lot_id IS NEW.lot_id
        ))
        OR (NEW.serial_identifier_type = 'qr_code' AND (
            NOT EXISTS (
                SELECT 1 FROM inventory_serials AS serial
                 WHERE serial.id = NEW.serial_id
                   AND serial.material_id = NEW.material_id
                   AND serial.qr_code = NEW.serial_no_raw
                   AND serial.lot_id IS NEW.lot_id
            )
            OR (SELECT count(*) FROM qr_codes AS mapping
                 WHERE mapping.code = NEW.serial_no_raw
                   AND mapping.object_type = 'serial'
                   AND mapping.object_id = NEW.serial_id
                   AND mapping.status = 'active') <> 1
        ))
        OR NEW.serial_identifier_type NOT IN ('serial_no', 'qr_code', 'unknown')
    ))
 OR (NEW.verification_status = 'verified' AND (
        EXISTS (
            SELECT 1 FROM stock_accounts AS account
             WHERE account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NEW.lot_id
               AND account.created_at <=
                   (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
        ) OR EXISTS (
            SELECT 1
              FROM stocktake_snapshot_lines AS snapshot
              JOIN stock_accounts AS account
                ON account.id = snapshot.stock_account_id
             WHERE snapshot.task_id = NEW.task_id
               AND snapshot.scope_id = NEW.scope_id
               AND account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NEW.lot_id
        )
    ))
BEGIN
    SELECT RAISE(ABORT,
        'stocktake observation is invalid, sealed, outside scope, or unassigned');
END
"""


def _sqlite_completion_trigger_sql(*, round_aware: bool) -> str:
    trigger_name = COMPLETION_TRIGGER if round_aware else OLD_COMPLETION_TRIGGER
    actor_predicate = (
        _sqlite_actor_predicate(
            full_snapshot=True,
            user_column="NEW.completed_by_user_id",
            person_column="NEW.completed_by_person_id",
            assignment_column="NEW.completed_role_assignment_id",
            authorization_version_column="NEW.authorization_version",
            role_column="NEW.role_code",
            scope_type_column="NEW.scope_type",
            scope_id_column="NEW.scope_id_snapshot",
            occurred_column="NEW.completed_at",
        )
        if round_aware
        else """EXISTS (
        SELECT 1
          FROM role_assignments AS assignment
          JOIN roles AS role ON role.id = assignment.role_id
          JOIN users AS actor ON actor.id = assignment.user_id
          JOIN stocktake_scopes AS scope
            ON scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
         WHERE scope.assignee_user_id = NEW.completed_by_user_id
           AND assignment.id = NEW.completed_role_assignment_id
           AND assignment.user_id = NEW.completed_by_user_id
           AND actor.person_id = NEW.completed_by_person_id
           AND actor.authorization_version = NEW.authorization_version
           AND assignment.status IN ('active', 'expired', 'revoked')
           AND assignment.valid_from <= NEW.completed_at
           AND (assignment.valid_to IS NULL
                OR NEW.completed_at < assignment.valid_to)
           AND (assignment.revoked_at IS NULL
                OR NEW.completed_at < assignment.revoked_at)
           AND role.is_external = 0 AND role.code = NEW.role_code
           AND assignment.scope_type = NEW.scope_type
           AND assignment.scope_id = NEW.scope_id_snapshot
           AND ((NEW.role_code = 'admin' AND NEW.scope_type = 'national'
                 AND NEW.scope_id_snapshot = '*')
                OR (NEW.role_code = 'provincial_manager'
                    AND NEW.scope_type = 'organization'
                    AND replace(NEW.scope_id_snapshot, '-', '') =
                        scope.owner_org_id)
                OR (NEW.role_code = 'technician'
                    AND NEW.scope_type = 'person'
                    AND scope.custodian_person_id_snapshot IS NOT NULL
                    AND replace(NEW.scope_id_snapshot, '-', '') =
                        scope.custodian_person_id_snapshot))
    )"""
    )
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON stocktake_scope_count_completions
WHEN COALESCE((SELECT status FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
       <> 'counting'
 OR NEW.completed_at < (SELECT started_at FROM stocktake_rounds
                         WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM stocktake_round_submissions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NOT ({actor_predicate})
 OR EXISTS (SELECT 1 FROM stocktake_count_lines
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id
               AND scope_id = NEW.scope_id
               AND counted_by_user_id <> NEW.completed_by_user_id)
 OR EXISTS (SELECT 1 FROM stocktake_count_observations
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id
               AND scope_id = NEW.scope_id
               AND counted_by_user_id <> NEW.completed_by_user_id)
 OR EXISTS (
        SELECT 1 FROM stocktake_snapshot_lines AS snapshot
         WHERE snapshot.task_id = NEW.task_id AND snapshot.scope_id = NEW.scope_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_count_lines AS line
                WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
                  AND line.scope_id = NEW.scope_id
                  AND line.stock_account_id = snapshot.stock_account_id
           )
    )
 OR EXISTS (
        SELECT 1 FROM stocktake_count_lines AS line
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_snapshot_lines AS snapshot
                WHERE snapshot.task_id = NEW.task_id
                  AND snapshot.scope_id = NEW.scope_id
                  AND snapshot.stock_account_id = line.stock_account_id
           )
    )
 OR EXISTS (
        SELECT 1
          FROM stocktake_count_lines AS line
          JOIN stock_accounts AS account ON account.id = line.stock_account_id
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND (
               (SELECT count(*) FROM material_inventory_policies AS policy
                 WHERE policy.material_id = account.material_id
                   AND policy.effective_from <= (
                       SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                   AND (policy.effective_to IS NULL OR (
                       SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                       < policy.effective_to)) <> 1
               OR line.counted_qty <> round(
                   line.counted_qty,
                   COALESCE((SELECT min(policy.quantity_scale)
                     FROM material_inventory_policies AS policy
                    WHERE policy.material_id = account.material_id
                      AND policy.effective_from <= (
                          SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id)
                      AND (policy.effective_to IS NULL OR (
                          SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id) < policy.effective_to)), 0))
               OR (COALESCE((SELECT min(CAST(policy.allow_fraction AS INTEGER))
                     FROM material_inventory_policies AS policy
                    WHERE policy.material_id = account.material_id
                      AND policy.effective_from <= (
                          SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id)
                      AND (policy.effective_to IS NULL OR (
                          SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id) < policy.effective_to)), 0) = 0
                   AND line.counted_qty <> round(line.counted_qty, 0))
               OR ((SELECT min(policy.tracking_mode)
                      FROM material_inventory_policies AS policy
                     WHERE policy.material_id = account.material_id
                       AND policy.effective_from <= (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id)
                       AND (policy.effective_to IS NULL OR (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id) < policy.effective_to))
                   IN ('none', 'serial') AND account.lot_id IS NOT NULL)
               OR ((SELECT min(policy.tracking_mode)
                      FROM material_inventory_policies AS policy
                     WHERE policy.material_id = account.material_id
                       AND policy.effective_from <= (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id)
                       AND (policy.effective_to IS NULL OR (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id) < policy.effective_to))
                   IN ('lot', 'lot_and_serial') AND account.lot_id IS NULL)
               OR ((SELECT min(policy.tracking_mode)
                      FROM material_inventory_policies AS policy
                     WHERE policy.material_id = account.material_id
                       AND policy.effective_from <= (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id)
                       AND (policy.effective_to IS NULL OR (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id) < policy.effective_to))
                   IN ('none', 'lot') AND EXISTS (
                       SELECT 1 FROM stocktake_count_serials AS serial_row
                        WHERE serial_row.count_line_id = line.id
                          AND serial_row.round_id = line.round_id))
               OR ((SELECT min(policy.tracking_mode)
                      FROM material_inventory_policies AS policy
                     WHERE policy.material_id = account.material_id
                       AND policy.effective_from <= (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id)
                       AND (policy.effective_to IS NULL OR (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id) < policy.effective_to))
                   IN ('serial', 'lot_and_serial')
                   AND line.counted_qty <> (
                       SELECT count(*) FROM stocktake_count_serials AS serial_row
                        WHERE serial_row.count_line_id = line.id
                          AND serial_row.round_id = line.round_id
                          AND serial_row.result <> 'missing'))
           )
    )
 OR EXISTS (
        SELECT 1
          FROM stocktake_count_serials AS serial_row
          JOIN stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
          JOIN stock_accounts AS account ON account.id = line.stock_account_id
          JOIN inventory_serials AS serial ON serial.id = serial_row.serial_id
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND (serial.material_id IS NOT account.material_id
                OR serial.lot_id IS NOT account.lot_id)
    )
 OR EXISTS (
        SELECT 1
          FROM stocktake_count_serials AS serial_row
          JOIN stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
          JOIN inventory_serials AS serial ON serial.id = serial_row.serial_id
          JOIN stocktake_count_observations AS observation
            ON observation.round_id = line.round_id
           AND observation.serial_no_raw IS NOT NULL
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND (
               observation.serial_id = serial_row.serial_id
               OR (observation.serial_identifier_type IN ('qr_code', 'unknown')
                   AND observation.serial_no_raw = serial.qr_code)
               OR (observation.serial_identifier_type IN ('serial_no', 'unknown')
                   AND observation.serial_no_raw = serial.serial_no
                   AND (observation.material_id IS NULL
                        OR observation.material_id = serial.material_id))
           )
    )
 OR NEW.count_line_count <> (
        SELECT count(*) FROM stocktake_count_lines
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id)
 OR NEW.observation_line_count <> (
        SELECT count(*) FROM stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id)
 OR NEW.serial_count <> (
        SELECT count(*)
          FROM stocktake_count_serials AS serial_row
          JOIN stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id) + (
        SELECT count(*) FROM stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id AND serial_no_raw IS NOT NULL)
 OR NEW.total_counted_qty <> COALESCE((
        SELECT sum(counted_qty) FROM stocktake_count_lines
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id), 0) + COALESCE((
        SELECT sum(counted_qty) FROM stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id), 0)
 OR (NEW.zero_confirmed AND EXISTS (
        SELECT 1 FROM stocktake_snapshot_lines
         WHERE task_id = NEW.task_id AND scope_id = NEW.scope_id))
BEGIN
    SELECT RAISE(ABORT, 'stocktake scope completion is not canonical');
END
"""


def _sqlite_case_trigger_sql(*, round_aware: bool) -> str:
    trigger_name = CASE_TRIGGER if round_aware else OLD_CASE_TRIGGER
    review_path = (
        """(
                (review.review_stage = 'region'
                 AND review.decision IN ('recount', 'reject')
                 AND EXISTS (
                    SELECT 1 FROM role_assignments AS manager_assignment
                    JOIN roles AS manager_role
                      ON manager_role.id = manager_assignment.role_id
                    JOIN users AS manager_user
                      ON manager_user.id = manager_assignment.user_id
                     WHERE manager_assignment.id =
                           review.reviewer_role_assignment_id
                       AND manager_assignment.user_id = review.reviewer_user_id
                       AND manager_user.person_id = review.reviewer_person_id
                       AND manager_user.authorization_version >=
                           review.authorization_version
                       AND manager_assignment.status IN
                           ('active', 'expired', 'revoked')
                       AND manager_assignment.valid_from <= review.reviewed_at
                       AND (manager_assignment.valid_to IS NULL
                            OR review.reviewed_at < manager_assignment.valid_to)
                       AND (manager_assignment.revoked_at IS NULL
                            OR review.reviewed_at < manager_assignment.revoked_at)
                       AND manager_role.is_external = 0
                       AND manager_role.code = 'provincial_manager'
                       AND manager_assignment.scope_type = 'organization'
                       AND replace(manager_assignment.scope_id, '-', '') =
                           task.region_org_id)
                 AND NOT EXISTS (
                    SELECT 1 FROM stocktake_reviews AS headquarters_review
                     WHERE headquarters_review.task_id = task.id
                       AND headquarters_review.round_id = source.id
                       AND headquarters_review.review_stage = 'headquarters'))
                OR
                (review.review_stage = 'headquarters'
                 AND review.decision = 'reject'
                 AND EXISTS (
                    SELECT 1 FROM role_assignments AS headquarters_assignment
                    JOIN roles AS headquarters_role
                      ON headquarters_role.id = headquarters_assignment.role_id
                    JOIN users AS headquarters_user
                      ON headquarters_user.id = headquarters_assignment.user_id
                     WHERE headquarters_assignment.id =
                           review.reviewer_role_assignment_id
                       AND headquarters_assignment.user_id =
                           review.reviewer_user_id
                       AND headquarters_user.person_id = review.reviewer_person_id
                       AND headquarters_user.authorization_version >=
                           review.authorization_version
                       AND headquarters_assignment.status IN
                           ('active', 'expired', 'revoked')
                       AND headquarters_assignment.valid_from <= review.reviewed_at
                       AND (headquarters_assignment.valid_to IS NULL
                            OR review.reviewed_at < headquarters_assignment.valid_to)
                       AND (headquarters_assignment.revoked_at IS NULL
                            OR review.reviewed_at <
                               headquarters_assignment.revoked_at)
                       AND headquarters_role.is_external = 0
                       AND headquarters_role.code = 'admin'
                       AND headquarters_assignment.scope_type = 'national'
                       AND headquarters_assignment.scope_id = '*')
                 AND EXISTS (
                    SELECT 1 FROM stocktake_reviews AS region_review
                     WHERE region_review.task_id = task.id
                       AND region_review.round_id = source.id
                       AND region_review.review_stage = 'region'
                       AND region_review.decision = 'approve'
                       AND completion.completed_at <= region_review.reviewed_at
                       AND region_review.reviewed_at < review.reviewed_at
                       AND region_review.reviewer_user_id <>
                           review.reviewer_user_id
                       AND region_review.reviewer_person_id <>
                           review.reviewer_person_id
                       AND region_review.reviewer_role_assignment_id <>
                           review.reviewer_role_assignment_id
                       AND EXISTS (
                          SELECT 1
                            FROM role_assignments AS manager_assignment
                            JOIN roles AS manager_role
                              ON manager_role.id = manager_assignment.role_id
                            JOIN users AS manager_user
                              ON manager_user.id = manager_assignment.user_id
                           WHERE manager_assignment.id =
                                 region_review.reviewer_role_assignment_id
                             AND manager_assignment.user_id =
                                 region_review.reviewer_user_id
                             AND manager_user.person_id =
                                 region_review.reviewer_person_id
                             AND manager_user.authorization_version >=
                                 region_review.authorization_version
                             AND manager_assignment.status IN
                                 ('active', 'expired', 'revoked')
                             AND manager_assignment.valid_from <=
                                 region_review.reviewed_at
                             AND (manager_assignment.valid_to IS NULL
                                  OR region_review.reviewed_at <
                                     manager_assignment.valid_to)
                             AND (manager_assignment.revoked_at IS NULL
                                  OR region_review.reviewed_at <
                                     manager_assignment.revoked_at)
                             AND manager_role.is_external = 0
                             AND manager_role.code = 'provincial_manager'
                             AND manager_assignment.scope_type = 'organization'
                             AND replace(manager_assignment.scope_id, '-', '') =
                                 task.region_org_id))
                )
           )"""
        if round_aware
        else "review.decision = 'recount'"
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
         WHERE task.id = NEW.task_id AND task.task_type = 'opening'
           AND task.status = 'recount_required'
           AND task.current_round_no = source.round_no
           AND task.scope_manifest_sha256 = NEW.scope_manifest_sha256
           AND source.status = 'submitted' AND source.submitted_at IS NOT NULL
           AND NEW.next_round_no = source.round_no + 1
           AND {review_path}
           AND submission.submitted_at <= completion.completed_at
           AND completion.completed_at <= review.reviewed_at
           AND review.reviewed_at <= NEW.opened_at
           AND NEW.scope_count = (
                SELECT count(*) FROM stocktake_scopes WHERE task_id = task.id)
           AND NEW.scope_count > 0
           AND NOT EXISTS (SELECT 1 FROM stocktake_reviews AS later_review
                            WHERE later_review.task_id = task.id
                              AND later_review.round_id = source.id
                              AND later_review.reviewed_at > review.reviewed_at)
           AND NOT EXISTS (SELECT 1 FROM stocktake_postings
                            WHERE task_id = task.id AND round_id = source.id)
           AND NOT EXISTS (SELECT 1 FROM inventory_opening_establishments
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
                                task.region_org_id))
           )
     )
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount case causality is invalid');
END
"""
