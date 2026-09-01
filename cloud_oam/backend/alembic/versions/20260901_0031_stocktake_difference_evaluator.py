"""Add an independent authorization snapshot for stocktake difference evaluation.

Revision ID: 20260901_0031
Revises: 20260901_0030
Create Date: 2026-09-01

Opening stocktakes retain the 0016 rule that their difference seal is created
atomically by the final scope submitter.  Non-opening stocktakes instead bind
the real, current headquarters administrator or region manager and the real
evaluation time.  This structural revision grants no runtime DML privileges.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260901_0031"
down_revision: Union[str, Sequence[str], None] = "20260901_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE = "stocktake_difference_set_completions"
OLD_PG_FUNCTION = "rsc_validate_stocktake_difference_set_completion_0016"
OLD_PG_TRIGGER = "trg_stocktake_difference_set_completions_validate_0016"
OLD_SQLITE_TRIGGER = "trg_stocktake_difference_set_completions_validate_insert_0016"
PG_FUNCTION = "rsc_validate_stocktake_difference_set_completion_0031"
PG_TRIGGER = "trg_stocktake_difference_set_completions_validate_0031"
SQLITE_TRIGGER = "trg_stocktake_difference_set_completions_validate_insert_0031"
SQLITE_IMMUTABLE_UPDATE = (
    "trg_stocktake_difference_set_completions_immutable_update_0016"
)
SQLITE_IMMUTABLE_DELETE = (
    "trg_stocktake_difference_set_completions_immutable_delete_0016"
)
SQLITE_DEPENDENT_TRIGGERS = (
    "trg_stocktake_differences_completion_seal_insert_0016",
    "trg_stocktake_reviews_difference_completion_insert_0016",
    "trg_stocktake_recount_cases_review_path_0021",
)
HASH_CHECK = "ck_stocktake_difference_set_completions_hashes"
AUTH_CHECK = (
    "ck_stocktake_diff_completions_authorization_0031"
)
UPGRADE_BLOCKER = (
    "0031 stocktake difference evaluator upgrade failed: non-opening completion "
    "cannot be reattributed"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0031 while independently evaluated non-opening differences exist"
)


def upgrade() -> None:
    dialect = _dialect()
    if dialect == "postgresql":
        _upgrade_postgresql()
    else:
        if context.is_offline_mode():
            raise RuntimeError("0031 SQLite upgrade requires an online connection")
        _upgrade_sqlite()


def downgrade() -> None:
    dialect = _dialect()
    if context.is_offline_mode() and dialect != "postgresql":
        raise RuntimeError("0031 SQLite downgrade requires an online connection")
    _require_safe_downgrade(dialect)
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {PG_TRIGGER} ON public.{TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_FUNCTION}()")
        op.drop_constraint(AUTH_CHECK, TABLE, type_="check")
        op.drop_constraint(HASH_CHECK, TABLE, type_="check")
        for column in (
            "authorization_sha256",
            "scope_id_snapshot",
            "scope_type",
            "role_code",
        ):
            op.drop_column(TABLE, column)
        op.create_check_constraint(
            HASH_CHECK,
            TABLE,
            "length(difference_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND length(idempotency_key_hash) = 64",
        )
        op.execute(_postgresql_0016_function_sql())
        op.execute(
            f"CREATE TRIGGER {OLD_PG_TRIGGER} BEFORE INSERT ON public.{TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{OLD_PG_FUNCTION}()"
        )
    else:
        dependent_trigger_sql = _capture_sqlite_dependent_trigger_sql()
        _drop_sqlite_completion_triggers()
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            batch.drop_constraint(AUTH_CHECK, type_="check")
            batch.drop_constraint(HASH_CHECK, type_="check")
            for column in (
                "authorization_sha256",
                "scope_id_snapshot",
                "scope_type",
                "role_code",
            ):
                batch.drop_column(column)
            batch.create_check_constraint(
                HASH_CHECK,
                "length(difference_manifest_sha256) = 64 AND "
                "length(request_sha256) = 64 AND length(idempotency_key_hash) = 64",
            )
        _create_sqlite_immutable_triggers()
        op.execute(_sqlite_0016_trigger_sql())
        _restore_sqlite_dependent_triggers(dependent_trigger_sql)


def _dialect() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0031 supports only PostgreSQL and SQLite")
    return dialect


def _upgrade_postgresql() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM {TABLE} AS completion
        JOIN stocktake_tasks AS task ON task.id = completion.task_id
        WHERE task.task_type <> 'opening'
    ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END;
$$
"""
    )
    op.execute(f"DROP TRIGGER IF EXISTS {OLD_PG_TRIGGER} ON public.{TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{OLD_PG_FUNCTION}()")
    _add_columns()
    op.execute(_backfill_sql("postgresql"))
    for column in (
        "role_code",
        "scope_type",
        "scope_id_snapshot",
        "authorization_sha256",
    ):
        op.alter_column(TABLE, column, existing_type=sa.String(), nullable=False)
    op.drop_constraint(HASH_CHECK, TABLE, type_="check")
    op.create_check_constraint(
        HASH_CHECK,
        TABLE,
        "length(difference_manifest_sha256) = 64 AND "
        "length(request_sha256) = 64 AND length(idempotency_key_hash) = 64 "
        "AND length(authorization_sha256) = 64",
    )
    op.create_check_constraint(
        AUTH_CHECK,
        TABLE,
        "role_code IN ('admin', 'provincial_manager', 'technician') AND "
        "scope_type IN ('national', 'organization', 'person') AND "
        "length(trim(scope_id_snapshot)) > 0",
    )
    op.execute(_postgresql_0031_function_sql())
    op.execute(
        f"CREATE TRIGGER {PG_TRIGGER} BEFORE INSERT ON public.{TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_FUNCTION}()"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{PG_FUNCTION}() FROM PUBLIC"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'star_oam_api'
    ) THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION public.{PG_FUNCTION}() FROM star_oam_api';
    END IF;
END
$$
"""
    )


def _upgrade_sqlite() -> None:
    connection = op.get_bind()
    if connection.exec_driver_sql(
        f"SELECT count(*) FROM {TABLE} AS completion "
        "JOIN stocktake_tasks AS task ON task.id = completion.task_id "
        "WHERE task.task_type <> 'opening'"
    ).scalar_one():
        raise RuntimeError(UPGRADE_BLOCKER)
    dependent_trigger_sql = _capture_sqlite_dependent_trigger_sql()
    _drop_sqlite_completion_triggers()
    with op.batch_alter_table(TABLE, recreate="always") as batch:
        batch.add_column(sa.Column("role_code", sa.String(40), nullable=True))
        batch.add_column(sa.Column("scope_type", sa.String(24), nullable=True))
        batch.add_column(sa.Column("scope_id_snapshot", sa.String(80), nullable=True))
        batch.add_column(sa.Column("authorization_sha256", sa.String(64), nullable=True))
    op.execute(_backfill_sql("sqlite"))
    with op.batch_alter_table(TABLE, recreate="always") as batch:
        batch.alter_column("role_code", existing_type=sa.String(40), nullable=False)
        batch.alter_column("scope_type", existing_type=sa.String(24), nullable=False)
        batch.alter_column(
            "scope_id_snapshot", existing_type=sa.String(80), nullable=False
        )
        batch.alter_column(
            "authorization_sha256", existing_type=sa.String(64), nullable=False
        )
        batch.drop_constraint(HASH_CHECK, type_="check")
        batch.create_check_constraint(
            HASH_CHECK,
            "length(difference_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND length(idempotency_key_hash) = 64 "
            "AND length(authorization_sha256) = 64",
        )
        batch.create_check_constraint(
            AUTH_CHECK,
            "role_code IN ('admin', 'provincial_manager', 'technician') AND "
            "scope_type IN ('national', 'organization', 'person') AND "
            "length(trim(scope_id_snapshot)) > 0",
        )
    _create_sqlite_immutable_triggers()
    op.execute(_sqlite_0031_trigger_sql())
    _restore_sqlite_dependent_triggers(dependent_trigger_sql)


def _add_columns() -> None:
    op.add_column(TABLE, sa.Column("role_code", sa.String(40), nullable=True))
    op.add_column(TABLE, sa.Column("scope_type", sa.String(24), nullable=True))
    op.add_column(TABLE, sa.Column("scope_id_snapshot", sa.String(80), nullable=True))
    op.add_column(TABLE, sa.Column("authorization_sha256", sa.String(64), nullable=True))


def _backfill_sql(dialect: str) -> str:
    if dialect == "postgresql":
        return f"""
UPDATE {TABLE} AS completion
   SET role_code = sealing.role_code,
       scope_type = sealing.scope_type,
       scope_id_snapshot = sealing.scope_id_snapshot,
       authorization_sha256 = sealing.authorization_sha256
  FROM stocktake_round_submissions AS submission,
       stocktake_scope_count_completions AS sealing
 WHERE submission.id = completion.round_submission_id
   AND sealing.id = submission.sealing_completion_id
"""
    return f"""
UPDATE {TABLE}
   SET role_code = (SELECT sealing.role_code
                      FROM stocktake_round_submissions AS submission
                      JOIN stocktake_scope_count_completions AS sealing
                        ON sealing.id = submission.sealing_completion_id
                     WHERE submission.id = {TABLE}.round_submission_id),
       scope_type = (SELECT sealing.scope_type
                       FROM stocktake_round_submissions AS submission
                       JOIN stocktake_scope_count_completions AS sealing
                         ON sealing.id = submission.sealing_completion_id
                      WHERE submission.id = {TABLE}.round_submission_id),
       scope_id_snapshot = (SELECT sealing.scope_id_snapshot
                              FROM stocktake_round_submissions AS submission
                              JOIN stocktake_scope_count_completions AS sealing
                                ON sealing.id = submission.sealing_completion_id
                             WHERE submission.id = {TABLE}.round_submission_id),
       authorization_sha256 = (SELECT sealing.authorization_sha256
                                 FROM stocktake_round_submissions AS submission
                                 JOIN stocktake_scope_count_completions AS sealing
                                   ON sealing.id = submission.sealing_completion_id
                                WHERE submission.id = {TABLE}.round_submission_id)
"""


def _postgresql_0031_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    task_kind text;
    task_region uuid;
    actual_count integer;
    actual_physical integer;
    actual_control integer;
    actual_pending integer;
    actual_total numeric(18, 3);
    actual_min integer;
    actual_max integer;
BEGIN
    SELECT task.task_type, task.region_org_id
      INTO task_kind, task_region
      FROM stocktake_tasks AS task
      JOIN stocktake_rounds AS round_row
        ON round_row.task_id = task.id
       AND round_row.id = NEW.round_id
       AND round_row.status = 'submitted'
      JOIN stocktake_round_submissions AS submission
        ON submission.id = NEW.round_submission_id
       AND submission.task_id = NEW.task_id
       AND submission.round_id = NEW.round_id
     WHERE task.id = NEW.task_id
     FOR UPDATE OF task, round_row;
    IF task_kind IS NULL
       OR NEW.authorization_sha256 !~ '^[0-9a-f]{64}$'
       OR EXISTS (SELECT 1 FROM stocktake_reviews
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
       OR EXISTS (SELECT 1 FROM stocktake_postings
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
       OR EXISTS (SELECT 1 FROM inventory_opening_establishments
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id) THEN
        RAISE EXCEPTION 'stocktake difference completion is not canonical';
    END IF;

    IF task_kind = 'opening' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM stocktake_round_submissions AS submission
              JOIN stocktake_scope_count_completions AS sealing
                ON sealing.id = submission.sealing_completion_id
             WHERE submission.id = NEW.round_submission_id
               AND submission.task_id = NEW.task_id
               AND submission.round_id = NEW.round_id
               AND submission.submitted_by_user_id = NEW.completed_by_user_id
               AND submission.submitted_by_person_id = NEW.completed_by_person_id
               AND submission.submitted_role_assignment_id =
                   NEW.completed_role_assignment_id
               AND submission.authorization_version = NEW.authorization_version
               AND submission.submitted_at = NEW.completed_at
               AND sealing.task_id = NEW.task_id
               AND sealing.round_id = NEW.round_id
               AND sealing.completed_by_user_id = NEW.completed_by_user_id
               AND sealing.completed_by_person_id = NEW.completed_by_person_id
               AND sealing.completed_role_assignment_id =
                   NEW.completed_role_assignment_id
               AND sealing.authorization_version = NEW.authorization_version
               AND sealing.completed_at = NEW.completed_at
               AND sealing.role_code = NEW.role_code
               AND sealing.scope_type = NEW.scope_type
               AND sealing.scope_id_snapshot = NEW.scope_id_snapshot
               AND sealing.authorization_sha256 = NEW.authorization_sha256
        ) THEN
            RAISE EXCEPTION 'opening stocktake difference completion changed semantics';
        END IF;
    ELSIF task_kind IN ('full', 'sample', 'ad_hoc', 'personal', 'termination') THEN
        IF NEW.completed_at < (
               SELECT submitted_at FROM stocktake_round_submissions
                WHERE id = NEW.round_submission_id
           )
           OR NEW.control_difference_count <> 0
           OR EXISTS (
               SELECT 1 FROM stocktake_differences
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
                  AND difference_type = 'control_unassigned'
           )
           OR NOT EXISTS (
               SELECT 1
                 FROM users AS app_user
                 JOIN people AS person ON person.id = app_user.person_id
                 JOIN role_assignments AS assignment
                   ON assignment.id = NEW.completed_role_assignment_id
                  AND assignment.user_id = app_user.id
                 JOIN roles AS role ON role.id = assignment.role_id
                WHERE app_user.id = NEW.completed_by_user_id
                  AND app_user.person_id = NEW.completed_by_person_id
                  AND app_user.account_status = 'active'
                  AND app_user.is_active
                  AND app_user.authorization_version = NEW.authorization_version
                  AND person.employment_status = 'active'
                  AND assignment.status = 'active'
                  AND assignment.revoked_at IS NULL
                  AND assignment.valid_from <= NEW.completed_at
                  AND (assignment.valid_to IS NULL OR assignment.valid_to > NEW.completed_at)
                  AND role.status = 'active'
                  AND NOT role.is_external
                  AND role.code = NEW.role_code
                  AND assignment.scope_type = NEW.scope_type
                  AND assignment.scope_id = NEW.scope_id_snapshot
                  AND (
                      (role.code = 'admin' AND assignment.scope_type = 'national'
                       AND assignment.scope_id = '*')
                      OR
                      (role.code = 'provincial_manager'
                       AND assignment.scope_type = 'organization'
                       AND assignment.scope_id = task_region::text)
                  )
                  AND EXISTS (
                      SELECT 1 FROM role_permissions AS role_permission
                      JOIN permissions AS permission
                        ON permission.id = role_permission.permission_id
                       AND permission.resource = 'stocktake'
                       AND permission.action = 'manage'
                       AND permission.field_code = ''
                     WHERE role_permission.role_id = role.id
                       AND role_permission.effect = 'allow'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM role_permissions AS role_permission
                      JOIN permissions AS permission
                        ON permission.id = role_permission.permission_id
                       AND permission.resource = 'stocktake'
                       AND permission.action = 'manage'
                       AND permission.field_code = ''
                     WHERE role_permission.role_id = role.id
                       AND role_permission.effect = 'deny'
                  )
           ) THEN
            RAISE EXCEPTION 'non-opening stocktake evaluator is not canonical';
        END IF;
    ELSE
        RAISE EXCEPTION 'stocktake difference completion task type is invalid';
    END IF;

    SELECT count(*),
           count(*) FILTER (WHERE difference_type <> 'control_unassigned'),
           count(*) FILTER (WHERE difference_type = 'control_unassigned'),
           count(*) FILTER (WHERE observed_line_id IS NOT NULL AND EXISTS (
               SELECT 1 FROM stocktake_count_observations AS observation
                WHERE observation.id = difference.observed_line_id
                  AND observation.verification_status = 'pending_verification'
           )),
           COALESCE(sum(affected_qty), 0), min(difference_no), max(difference_no)
      INTO actual_count, actual_physical, actual_control, actual_pending,
           actual_total, actual_min, actual_max
      FROM stocktake_differences AS difference
     WHERE difference.task_id = NEW.task_id AND difference.round_id = NEW.round_id;
    IF NEW.difference_count <> actual_count
       OR NEW.physical_difference_count <> actual_physical
       OR NEW.control_difference_count <> actual_control
       OR NEW.pending_observation_difference_count <> actual_pending
       OR NEW.total_affected_qty <> actual_total
       OR (actual_count > 0 AND (actual_min <> 1 OR actual_max <> actual_count)) THEN
        RAISE EXCEPTION 'stocktake difference completion totals are not canonical';
    END IF;
    RETURN NEW;
END;
$$
"""


def _sqlite_0031_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_TRIGGER}
BEFORE INSERT ON {TABLE}
WHEN length(NEW.authorization_sha256) <> 64
 OR NEW.authorization_sha256 GLOB '*[^0-9a-f]*'
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_rounds AS round_row
        JOIN stocktake_round_submissions AS submission
          ON submission.id = NEW.round_submission_id
         AND submission.task_id = NEW.task_id
         AND submission.round_id = NEW.round_id
        JOIN stocktake_tasks AS task ON task.id = NEW.task_id
         WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
           AND round_row.status = 'submitted'
     )
 OR EXISTS (SELECT 1 FROM stocktake_reviews
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_postings
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM inventory_opening_establishments
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR (
      (SELECT task_type FROM stocktake_tasks WHERE id = NEW.task_id) = 'opening'
      AND NOT EXISTS (
        SELECT 1 FROM stocktake_round_submissions AS submission
        JOIN stocktake_scope_count_completions AS sealing
          ON sealing.id = submission.sealing_completion_id
         WHERE submission.id = NEW.round_submission_id
           AND submission.task_id = NEW.task_id
           AND submission.round_id = NEW.round_id
           AND submission.submitted_by_user_id = NEW.completed_by_user_id
           AND submission.submitted_by_person_id = NEW.completed_by_person_id
           AND submission.submitted_role_assignment_id =
               NEW.completed_role_assignment_id
           AND submission.authorization_version = NEW.authorization_version
           AND submission.submitted_at = NEW.completed_at
           AND sealing.task_id = NEW.task_id AND sealing.round_id = NEW.round_id
           AND sealing.completed_by_user_id = NEW.completed_by_user_id
           AND sealing.completed_by_person_id = NEW.completed_by_person_id
           AND sealing.completed_role_assignment_id =
               NEW.completed_role_assignment_id
           AND sealing.authorization_version = NEW.authorization_version
           AND sealing.completed_at = NEW.completed_at
           AND sealing.role_code = NEW.role_code
           AND sealing.scope_type = NEW.scope_type
           AND sealing.scope_id_snapshot = NEW.scope_id_snapshot
           AND sealing.authorization_sha256 = NEW.authorization_sha256
      )
 )
 OR (
      (SELECT task_type FROM stocktake_tasks WHERE id = NEW.task_id)
        IN ('full', 'sample', 'ad_hoc', 'personal', 'termination')
      AND (
        NEW.completed_at < (SELECT submitted_at FROM stocktake_round_submissions
                              WHERE id = NEW.round_submission_id)
        OR NEW.control_difference_count <> 0
        OR EXISTS (SELECT 1 FROM stocktake_differences
                    WHERE task_id = NEW.task_id AND round_id = NEW.round_id
                      AND difference_type = 'control_unassigned')
        OR NOT EXISTS (
          SELECT 1 FROM users AS app_user
          JOIN people AS person ON person.id = app_user.person_id
          JOIN role_assignments AS assignment
            ON assignment.id = NEW.completed_role_assignment_id
           AND assignment.user_id = app_user.id
          JOIN roles AS role ON role.id = assignment.role_id
          JOIN stocktake_tasks AS task ON task.id = NEW.task_id
         WHERE app_user.id = NEW.completed_by_user_id
           AND app_user.person_id = NEW.completed_by_person_id
           AND app_user.account_status = 'active'
           AND app_user.is_active = 1
           AND app_user.authorization_version = NEW.authorization_version
           AND person.employment_status = 'active'
           AND assignment.status = 'active'
           AND assignment.revoked_at IS NULL
           AND assignment.valid_from <= NEW.completed_at
           AND (assignment.valid_to IS NULL OR assignment.valid_to > NEW.completed_at)
           AND role.status = 'active' AND role.is_external = 0
           AND role.code = NEW.role_code
           AND assignment.scope_type = NEW.scope_type
           AND assignment.scope_id = NEW.scope_id_snapshot
           AND (
             (role.code = 'admin' AND assignment.scope_type = 'national'
              AND assignment.scope_id = '*')
             OR
             (role.code = 'provincial_manager'
              AND assignment.scope_type = 'organization'
              AND replace(assignment.scope_id, '-', '') =
                  replace(CAST(task.region_org_id AS TEXT), '-', ''))
           )
           AND EXISTS (
             SELECT 1 FROM role_permissions AS role_permission
             JOIN permissions AS permission
               ON permission.id = role_permission.permission_id
              AND permission.resource = 'stocktake'
              AND permission.action = 'manage'
              AND permission.field_code = ''
            WHERE role_permission.role_id = role.id
              AND role_permission.effect = 'allow'
           )
           AND NOT EXISTS (
             SELECT 1 FROM role_permissions AS role_permission
             JOIN permissions AS permission
               ON permission.id = role_permission.permission_id
              AND permission.resource = 'stocktake'
              AND permission.action = 'manage'
              AND permission.field_code = ''
            WHERE role_permission.role_id = role.id
              AND role_permission.effect = 'deny'
           )
        )
      )
 )
 OR (SELECT task_type FROM stocktake_tasks WHERE id = NEW.task_id)
       NOT IN ('opening', 'full', 'sample', 'ad_hoc', 'personal', 'termination')
 OR NEW.difference_count <> (SELECT count(*) FROM stocktake_differences
                              WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NEW.physical_difference_count <> (SELECT count(*) FROM stocktake_differences
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND difference_type <> 'control_unassigned')
 OR NEW.control_difference_count <> (SELECT count(*) FROM stocktake_differences
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND difference_type = 'control_unassigned')
 OR NEW.pending_observation_difference_count <> (
        SELECT count(*) FROM stocktake_differences AS difference
        JOIN stocktake_count_observations AS observation
          ON observation.id = difference.observed_line_id
         WHERE difference.task_id = NEW.task_id
           AND difference.round_id = NEW.round_id
           AND observation.verification_status = 'pending_verification')
 OR NEW.total_affected_qty <> COALESCE((SELECT sum(affected_qty)
      FROM stocktake_differences WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR ((SELECT count(*) FROM stocktake_differences
       WHERE task_id = NEW.task_id AND round_id = NEW.round_id) > 0 AND (
        (SELECT min(difference_no) FROM stocktake_differences
          WHERE task_id = NEW.task_id AND round_id = NEW.round_id) <> 1
        OR (SELECT max(difference_no) FROM stocktake_differences
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id) <>
           (SELECT count(*) FROM stocktake_differences
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)))
BEGIN
    SELECT RAISE(ABORT, 'stocktake difference completion is not canonical');
END
"""


def _postgresql_0016_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{OLD_PG_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    actual_count integer;
    actual_physical integer;
    actual_control integer;
    actual_pending integer;
    actual_total numeric(18, 3);
    actual_min integer;
    actual_max integer;
BEGIN
    PERFORM 1 FROM stocktake_rounds AS round_row
     WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
       AND round_row.status = 'submitted' FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1 FROM stocktake_round_submissions AS submission
        JOIN stocktake_scope_count_completions AS sealing
          ON sealing.id = submission.sealing_completion_id
         WHERE submission.id = NEW.round_submission_id
           AND submission.task_id = NEW.task_id
           AND submission.round_id = NEW.round_id
           AND submission.submitted_by_user_id = NEW.completed_by_user_id
           AND submission.submitted_by_person_id = NEW.completed_by_person_id
           AND submission.submitted_role_assignment_id = NEW.completed_role_assignment_id
           AND submission.authorization_version = NEW.authorization_version
           AND submission.submitted_at = NEW.completed_at
           AND sealing.task_id = NEW.task_id AND sealing.round_id = NEW.round_id
           AND sealing.completed_by_user_id = NEW.completed_by_user_id
           AND sealing.completed_by_person_id = NEW.completed_by_person_id
           AND sealing.completed_role_assignment_id = NEW.completed_role_assignment_id
           AND sealing.authorization_version = NEW.authorization_version
           AND sealing.completed_at = NEW.completed_at
    ) OR EXISTS (SELECT 1 FROM stocktake_reviews
                  WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
      OR EXISTS (SELECT 1 FROM stocktake_postings
                  WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
      OR EXISTS (SELECT 1 FROM inventory_opening_establishments
                  WHERE task_id = NEW.task_id AND round_id = NEW.round_id) THEN
        RAISE EXCEPTION 'stocktake difference completion is not canonical';
    END IF;
    SELECT count(*),
           count(*) FILTER (WHERE difference_type <> 'control_unassigned'),
           count(*) FILTER (WHERE difference_type = 'control_unassigned'),
           count(*) FILTER (WHERE observed_line_id IS NOT NULL AND EXISTS (
               SELECT 1 FROM stocktake_count_observations AS observation
                WHERE observation.id = difference.observed_line_id
                  AND observation.verification_status = 'pending_verification')),
           COALESCE(sum(affected_qty), 0), min(difference_no), max(difference_no)
      INTO actual_count, actual_physical, actual_control, actual_pending,
           actual_total, actual_min, actual_max
      FROM stocktake_differences AS difference
     WHERE difference.task_id = NEW.task_id AND difference.round_id = NEW.round_id;
    IF NEW.difference_count <> actual_count
       OR NEW.physical_difference_count <> actual_physical
       OR NEW.control_difference_count <> actual_control
       OR NEW.pending_observation_difference_count <> actual_pending
       OR NEW.total_affected_qty <> actual_total
       OR (actual_count > 0 AND (actual_min <> 1 OR actual_max <> actual_count)) THEN
        RAISE EXCEPTION 'stocktake difference completion totals are not canonical';
    END IF;
    RETURN NEW;
END;
$$
"""


def _sqlite_0016_trigger_sql() -> str:
    # The downgrade is blocked once a non-opening completion exists, so the
    # original 0016 identity/time rule is sufficient here.
    return f"""
CREATE TRIGGER {OLD_SQLITE_TRIGGER}
BEFORE INSERT ON {TABLE}
WHEN NOT EXISTS (
        SELECT 1 FROM stocktake_rounds AS round_row
        JOIN stocktake_round_submissions AS submission
          ON submission.id = NEW.round_submission_id
         AND submission.task_id = NEW.task_id
         AND submission.round_id = NEW.round_id
        JOIN stocktake_scope_count_completions AS sealing
          ON sealing.id = submission.sealing_completion_id
         WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
           AND round_row.status = 'submitted'
           AND submission.submitted_by_user_id = NEW.completed_by_user_id
           AND submission.submitted_by_person_id = NEW.completed_by_person_id
           AND submission.submitted_role_assignment_id = NEW.completed_role_assignment_id
           AND submission.authorization_version = NEW.authorization_version
           AND submission.submitted_at = NEW.completed_at
           AND sealing.task_id = NEW.task_id AND sealing.round_id = NEW.round_id
           AND sealing.completed_by_user_id = NEW.completed_by_user_id
           AND sealing.completed_by_person_id = NEW.completed_by_person_id
           AND sealing.completed_role_assignment_id = NEW.completed_role_assignment_id
           AND sealing.authorization_version = NEW.authorization_version
           AND sealing.completed_at = NEW.completed_at)
 OR EXISTS (SELECT 1 FROM stocktake_reviews
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_postings
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM inventory_opening_establishments
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NEW.difference_count <> (SELECT count(*) FROM stocktake_differences
                              WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NEW.physical_difference_count <> (SELECT count(*) FROM stocktake_differences
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND difference_type <> 'control_unassigned')
 OR NEW.control_difference_count <> (SELECT count(*) FROM stocktake_differences
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND difference_type = 'control_unassigned')
 OR NEW.pending_observation_difference_count <> (
        SELECT count(*) FROM stocktake_differences AS difference
        JOIN stocktake_count_observations AS observation
          ON observation.id = difference.observed_line_id
         WHERE difference.task_id = NEW.task_id
           AND difference.round_id = NEW.round_id
           AND observation.verification_status = 'pending_verification')
 OR NEW.total_affected_qty <> COALESCE((SELECT sum(affected_qty)
      FROM stocktake_differences WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
BEGIN
    SELECT RAISE(ABORT, 'stocktake difference completion is not canonical');
END
"""


def _drop_sqlite_completion_triggers() -> None:
    for name in (
        OLD_SQLITE_TRIGGER,
        SQLITE_TRIGGER,
        SQLITE_IMMUTABLE_UPDATE,
        SQLITE_IMMUTABLE_DELETE,
        *SQLITE_DEPENDENT_TRIGGERS,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")


def _capture_sqlite_dependent_trigger_sql() -> tuple[str, ...]:
    placeholders = ", ".join("?" for _ in SQLITE_DEPENDENT_TRIGGERS)
    rows = op.get_bind().exec_driver_sql(
        "SELECT name, sql FROM sqlite_master "
        f"WHERE type = 'trigger' AND name IN ({placeholders}) ORDER BY name",
        SQLITE_DEPENDENT_TRIGGERS,
    ).all()
    present = {row[0] for row in rows}
    missing = set(SQLITE_DEPENDENT_TRIGGERS) - present
    if missing:
        raise RuntimeError(
            "0031 SQLite migration requires canonical dependent triggers: "
            + ", ".join(sorted(missing))
        )
    return tuple(row[1] for row in rows)


def _restore_sqlite_dependent_triggers(trigger_sql: tuple[str, ...]) -> None:
    for statement in trigger_sql:
        op.execute(statement)


def _create_sqlite_immutable_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_{TABLE}_immutable_{operation.lower()}_0016 "
            f"BEFORE {operation} ON {TABLE} BEGIN SELECT RAISE(ABORT, "
            "'opening review evidence is immutable'); END"
        )


def _require_safe_downgrade(dialect: str) -> None:
    if dialect == "postgresql":
        op.execute(
            f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM {TABLE} AS completion
        JOIN stocktake_tasks AS task ON task.id = completion.task_id
        WHERE task.task_type <> 'opening'
    ) THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END;
$$
"""
        )
    elif op.get_bind().exec_driver_sql(
        f"SELECT count(*) FROM {TABLE} AS completion "
        "JOIN stocktake_tasks AS task ON task.id = completion.task_id "
        "WHERE task.task_type <> 'opening'"
    ).scalar_one():
        raise RuntimeError(DOWNGRADE_BLOCKER)
