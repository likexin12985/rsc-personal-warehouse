"""Add append-only, causally sealed stocktake recount rounds.

Revision ID: 20260831_0018
Revises: 20260831_0017
Create Date: 2026-08-31

The submitted source round remains immutable.  A recount is represented by an
immutable case edge, a complete per-scope assignment snapshot and one new,
contiguous counting round.  PostgreSQL enforces the final graph at transaction
commit; SQLite supplies the same immediate row checks but cannot emulate a
deferred commit trigger and is not a supported production database.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260831_0018"
down_revision: Union[str, Sequence[str], None] = "20260831_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CASE_TABLE = "stocktake_recount_cases"
ASSIGNMENT_TABLE = "stocktake_recount_scope_assignments"
PRODUCTION_API_ROLE = "star_oam_api"

UPGRADE_BLOCKER = (
    "0018 preflight failed: existing stocktake round history is not a clean "
    "single-round graph"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0018: stocktake recount evidence or successor-round "
    "state exists"
)

ROUND_CASE_FK = "fk_stocktake_rounds_recount_case_0018"
ROUND_CASE_INDEX = "uq_stocktake_rounds_recount_case_0018"
ONE_COUNTING_INDEX = "uq_stocktake_rounds_one_counting_0018"

PG_CASE_VALIDATE_FUNCTION = "rsc_validate_stocktake_recount_case_0018"
PG_ASSIGNMENT_VALIDATE_FUNCTION = (
    "rsc_validate_stocktake_recount_scope_assignment_0018"
)
PG_IMMUTABLE_FUNCTION = "rsc_block_stocktake_recount_fact_mutation_0018"
PG_TASK_FUNCTION = "rsc_validate_stocktake_recount_task_advance_0018"
PG_ROUND_FUNCTION = "rsc_validate_stocktake_recount_round_0018"
PG_GRAPH_FUNCTION = "rsc_require_stocktake_recount_graph_0018"

PG_CASE_VALIDATE_TRIGGER = "trg_stocktake_recount_cases_validate_0018"
PG_CASE_IMMUTABLE_TRIGGER = "trg_stocktake_recount_cases_immutable_0018"
PG_CASE_TRUNCATE_TRIGGER = (
    "trg_stocktake_recount_cases_immutable_truncate_0018"
)
PG_ASSIGNMENT_VALIDATE_TRIGGER = (
    "trg_stocktake_recount_scope_assignments_validate_0018"
)
PG_ASSIGNMENT_IMMUTABLE_TRIGGER = (
    "trg_stocktake_recount_scope_assignments_immutable_0018"
)
PG_ASSIGNMENT_TRUNCATE_TRIGGER = (
    "trg_stocktake_recount_scope_assignments_immutable_truncate_0018"
)
PG_TASK_TRIGGER = "trg_stocktake_tasks_recount_causality_0018"
PG_ROUND_TRIGGER = "trg_stocktake_rounds_recount_causality_0018"
PG_GRAPH_TRIGGERS = {
    "stocktake_tasks": "trg_stocktake_recount_graph_task_0018",
    "stocktake_rounds": "trg_stocktake_recount_graph_round_0018",
    CASE_TABLE: "trg_stocktake_recount_graph_case_0018",
    ASSIGNMENT_TABLE: "trg_stocktake_recount_graph_assignment_0018",
}


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0018 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0018 SQLite upgrade requires an online connection")
        _postgresql_preflight(offline=True)
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_stocktake_graph()
        _online_preflight()

    _create_tables()
    if dialect == "postgresql":
        op.add_column(
            "stocktake_rounds",
            sa.Column("recount_case_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            ROUND_CASE_FK,
            "stocktake_rounds",
            CASE_TABLE,
            ["recount_case_id"],
            ["id"],
            ondelete="RESTRICT",
            use_alter=True,
        )
    else:
        # SQLite supports a nullable REFERENCES clause on ADD COLUMN even
        # though it cannot add a standalone table constraint.
        op.execute(
            "ALTER TABLE stocktake_rounds ADD COLUMN recount_case_id CHAR(32) "
            f"REFERENCES {CASE_TABLE}(id) "
            "ON DELETE RESTRICT"
        )
    op.create_index(
        ROUND_CASE_INDEX,
        "stocktake_rounds",
        ["recount_case_id"],
        unique=True,
        postgresql_where=sa.text("recount_case_id IS NOT NULL"),
        sqlite_where=sa.text("recount_case_id IS NOT NULL"),
    )
    op.create_index(
        ONE_COUNTING_INDEX,
        "stocktake_rounds",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("status = 'counting'"),
        sqlite_where=sa.text("status = 'counting'"),
    )

    if dialect == "postgresql":
        _create_postgresql_guards()
        _apply_postgresql_acl()
    else:
        _create_sqlite_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0018 downgrade requires an online connection for fail-closed "
            "recount evidence checks"
        )
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_stocktake_graph(include_new_tables=True)
    _assert_empty_recount_evidence()

    if dialect == "postgresql":
        _drop_postgresql_guards()
    else:
        _drop_sqlite_guards()

    op.drop_index(ONE_COUNTING_INDEX, table_name="stocktake_rounds")
    op.drop_index(ROUND_CASE_INDEX, table_name="stocktake_rounds")
    if dialect == "postgresql":
        op.drop_constraint(ROUND_CASE_FK, "stocktake_rounds", type_="foreignkey")
        op.drop_column("stocktake_rounds", "recount_case_id")
    else:
        # SQLite 3.35+ can safely remove an unused nullable column once all
        # indexes/triggers that mention it are gone.  A batch rebuild would
        # silently discard the many 0010-0016 triggers on this table.
        op.execute("ALTER TABLE stocktake_rounds DROP COLUMN recount_case_id")
    op.drop_table(ASSIGNMENT_TABLE)
    op.drop_table(CASE_TABLE)


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_stocktake_graph(*, include_new_tables: bool = False) -> None:
    tables = [
        "stocktake_tasks",
        "stocktake_scopes",
        "stocktake_rounds",
        "stocktake_round_submissions",
        "stocktake_difference_set_completions",
        "stocktake_reviews",
        "stocktake_postings",
        "inventory_opening_establishments",
    ]
    if include_new_tables:
        tables.extend([CASE_TABLE, ASSIGNMENT_TABLE])
    op.get_bind().exec_driver_sql(
        f"LOCK TABLE {', '.join(tables)} IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_preflight(*, offline: bool) -> None:
    if not offline:
        _lock_postgresql_stocktake_graph()
    else:
        op.execute(
            "LOCK TABLE stocktake_tasks, stocktake_scopes, stocktake_rounds, "
            "stocktake_round_submissions, stocktake_difference_set_completions, "
            "stocktake_reviews, stocktake_postings, "
            "inventory_opening_establishments IN ACCESS EXCLUSIVE MODE"
        )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM stocktake_rounds AS round_row
         WHERE round_row.round_no <> 1
            OR round_row.round_type <> 'initial'
            OR round_row.status = 'superseded'
    ) OR EXISTS (
        SELECT 1
          FROM stocktake_tasks AS task
         WHERE task.current_round_no NOT IN (0, 1)
            OR (task.current_round_no = 0 AND EXISTS (
                SELECT 1 FROM stocktake_rounds AS round_row
                 WHERE round_row.task_id = task.id
            ))
            OR (task.current_round_no = 1 AND (
                SELECT count(*) FROM stocktake_rounds AS round_row
                 WHERE round_row.task_id = task.id
            ) <> 1)
            OR (task.current_round_no = 1 AND NOT EXISTS (
                SELECT 1 FROM stocktake_rounds AS round_row
                 WHERE round_row.task_id = task.id
                   AND round_row.round_no = 1
                   AND round_row.round_type = 'initial'
            ))
    ) OR EXISTS (
        SELECT 1 FROM stocktake_rounds AS round_row
         WHERE NOT EXISTS (
             SELECT 1 FROM stocktake_tasks AS task
              WHERE task.id = round_row.task_id
                AND task.current_round_no = 1
         )
    ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_preflight() -> None:
    bind = op.get_bind()
    polluted = bind.exec_driver_sql(
        """
SELECT 1
 WHERE EXISTS (
        SELECT 1 FROM stocktake_rounds
         WHERE round_no <> 1 OR round_type <> 'initial'
            OR status = 'superseded'
     )
    OR EXISTS (
        SELECT 1 FROM stocktake_tasks AS task
         WHERE task.current_round_no NOT IN (0, 1)
            OR (task.current_round_no = 0 AND EXISTS (
                SELECT 1 FROM stocktake_rounds AS round_row
                 WHERE round_row.task_id = task.id
            ))
            OR (task.current_round_no = 1 AND (
                SELECT count(*) FROM stocktake_rounds AS round_row
                 WHERE round_row.task_id = task.id
            ) <> 1)
            OR (task.current_round_no = 1 AND NOT EXISTS (
                SELECT 1 FROM stocktake_rounds AS round_row
                 WHERE round_row.task_id = task.id AND round_row.round_no = 1
                   AND round_row.round_type = 'initial'
            ))
     )
    OR EXISTS (
        SELECT 1 FROM stocktake_rounds AS round_row
         WHERE NOT EXISTS (
             SELECT 1 FROM stocktake_tasks AS task
              WHERE task.id = round_row.task_id AND task.current_round_no = 1
         )
     )
"""
    ).first()
    if polluted is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _assert_empty_recount_evidence() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql(
        f"""
SELECT 1
 WHERE EXISTS (SELECT 1 FROM {CASE_TABLE})
    OR EXISTS (SELECT 1 FROM {ASSIGNMENT_TABLE})
    OR EXISTS (SELECT 1 FROM stocktake_rounds
                WHERE recount_case_id IS NOT NULL OR round_no > 1
                   OR round_type = 'recount' OR status = 'superseded')
    OR EXISTS (SELECT 1 FROM stocktake_tasks WHERE current_round_no > 1)
"""
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _create_tables() -> None:
    op.create_table(
        CASE_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("source_round_id", sa.Uuid(), nullable=False),
        sa.Column("source_round_submission_id", sa.Uuid(), nullable=False),
        sa.Column("source_difference_completion_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_review_id", sa.Uuid(), nullable=False),
        sa.Column("next_round_no", sa.Integer(), nullable=False),
        sa.Column("scope_count", sa.Integer(), nullable=False),
        sa.Column("scope_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "assignment_manifest_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("recount_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("opened_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("opened_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("opened_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(length=40), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(length=80), nullable=False),
        sa.Column("authorization_sha256", sa.String(length=64), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_recount_cases"),
        sa.UniqueConstraint(
            "source_round_id",
            name="uq_stocktake_recount_cases_source_round_0018",
        ),
        sa.UniqueConstraint(
            "source_round_submission_id",
            name="uq_stocktake_recount_cases_source_submission_0018",
        ),
        sa.UniqueConstraint(
            "source_difference_completion_id",
            name="uq_stocktake_recount_cases_source_completion_0018",
        ),
        sa.UniqueConstraint(
            "trigger_review_id",
            name="uq_stocktake_recount_cases_trigger_review_0018",
        ),
        sa.UniqueConstraint(
            "task_id",
            "next_round_no",
            name="uq_stocktake_recount_cases_task_next_round_0018",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_recount_cases_idempotency_0018",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "source_round_id",
            name="uq_stocktake_recount_cases_id_task_source_0018",
        ),
        sa.CheckConstraint(
            "next_round_no > 1 AND scope_count > 0",
            name="ck_stocktake_recount_cases_round_0018",
        ),
        sa.CheckConstraint(
            "length(scope_manifest_sha256) = 64 AND "
            "length(assignment_manifest_sha256) = 64 AND "
            "length(recount_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_recount_cases_hashes_0018",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_recount_cases_authorization_version_0018",
        ),
        sa.CheckConstraint(
            "(role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*') OR "
            "(role_code = 'provincial_manager' AND "
            "scope_type = 'organization' AND "
            "length(trim(scope_id_snapshot)) > 0)",
            name="ck_stocktake_recount_cases_authorization_snapshot_0018",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_stocktake_recount_cases_reason_0018",
        ),
        sa.CheckConstraint(
            "created_at = opened_at",
            name="ck_stocktake_recount_cases_chronology_0018",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["stocktake_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_recount_cases_source_round_0018",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_round_submission_id"],
            ["stocktake_round_submissions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_difference_completion_id"],
            ["stocktake_difference_set_completions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_review_id", "task_id", "source_round_id"],
            [
                "stocktake_reviews.id",
                "stocktake_reviews.task_id",
                "stocktake_reviews.round_id",
            ],
            name="fk_stocktake_recount_cases_trigger_review_0018",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["opened_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_stocktake_recount_cases_task_0018",
        CASE_TABLE,
        ["task_id", "next_round_no"],
    )

    op.create_table(
        ASSIGNMENT_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recount_case_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("source_round_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_user_id", sa.String(length=36), nullable=False),
        sa.Column("assignee_person_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(length=40), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(length=80), nullable=False),
        sa.Column("authorization_sha256", sa.String(length=64), nullable=False),
        sa.Column("assignment_sha256", sa.String(length=64), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "id", name="pk_stocktake_recount_scope_assignments"
        ),
        sa.UniqueConstraint(
            "recount_case_id",
            "scope_id",
            name="uq_stocktake_recount_scope_assignments_case_scope_0018",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_recount_scope_assignments_auth_version_0018",
        ),
        sa.CheckConstraint(
            "role_code IN ('admin', 'provincial_manager', 'technician') AND "
            "((role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*') OR "
            "(role_code = 'provincial_manager' AND "
            "scope_type = 'organization' AND "
            "length(trim(scope_id_snapshot)) > 0) OR "
            "(role_code = 'technician' AND scope_type = 'person' AND "
            "length(trim(scope_id_snapshot)) > 0))",
            name="ck_recount_scope_assignments_auth_snapshot_0018",
        ),
        sa.CheckConstraint(
            "length(authorization_sha256) = 64 AND "
            "length(assignment_sha256) = 64",
            name="ck_stocktake_recount_scope_assignments_hashes_0018",
        ),
        sa.CheckConstraint(
            "created_at = assigned_at",
            name="ck_stocktake_recount_scope_assignments_chronology_0018",
        ),
        sa.ForeignKeyConstraint(
            ["recount_case_id", "task_id", "source_round_id"],
            [
                f"{CASE_TABLE}.id",
                f"{CASE_TABLE}.task_id",
                f"{CASE_TABLE}.source_round_id",
            ],
            name="fk_stocktake_recount_scope_assignments_case_0018",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_recount_scope_assignments_scope_0018",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assignee_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assignee_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_stocktake_recount_scope_assignments_assignee_0018",
        ASSIGNMENT_TABLE,
        ["assignee_user_id", "task_id"],
    )


def _create_postgresql_guards() -> None:
    op.execute(_postgresql_case_validate_sql())
    op.execute(
        f"CREATE TRIGGER {PG_CASE_VALIDATE_TRIGGER} BEFORE INSERT ON "
        f"{CASE_TABLE} FOR EACH ROW EXECUTE FUNCTION "
        f"{PG_CASE_VALIDATE_FUNCTION}()"
    )
    op.execute(_postgresql_assignment_validate_sql())
    op.execute(
        f"CREATE TRIGGER {PG_ASSIGNMENT_VALIDATE_TRIGGER} BEFORE INSERT ON "
        f"{ASSIGNMENT_TABLE} FOR EACH ROW EXECUTE FUNCTION "
        f"{PG_ASSIGNMENT_VALIDATE_FUNCTION}()"
    )
    op.execute(
        f"""
CREATE FUNCTION {PG_IMMUTABLE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'stocktake recount facts are immutable';
END;
$$
"""
    )
    for table_name, row_trigger, truncate_trigger in (
        (CASE_TABLE, PG_CASE_IMMUTABLE_TRIGGER, PG_CASE_TRUNCATE_TRIGGER),
        (
            ASSIGNMENT_TABLE,
            PG_ASSIGNMENT_IMMUTABLE_TRIGGER,
            PG_ASSIGNMENT_TRUNCATE_TRIGGER,
        ),
    ):
        op.execute(
            f"CREATE TRIGGER {row_trigger} BEFORE UPDATE OR DELETE ON "
            f"{table_name} FOR EACH ROW EXECUTE FUNCTION "
            f"{PG_IMMUTABLE_FUNCTION}()"
        )
        op.execute(
            f"CREATE TRIGGER {truncate_trigger} BEFORE TRUNCATE ON "
            f"{table_name} FOR EACH STATEMENT EXECUTE FUNCTION "
            f"{PG_IMMUTABLE_FUNCTION}()"
        )

    op.execute(_postgresql_task_validate_sql())
    op.execute(
        f"CREATE TRIGGER {PG_TASK_TRIGGER} BEFORE UPDATE ON stocktake_tasks "
        f"FOR EACH ROW EXECUTE FUNCTION {PG_TASK_FUNCTION}()"
    )
    op.execute(_postgresql_round_validate_sql())
    op.execute(
        f"CREATE TRIGGER {PG_ROUND_TRIGGER} BEFORE INSERT OR UPDATE ON "
        f"stocktake_rounds FOR EACH ROW EXECUTE FUNCTION {PG_ROUND_FUNCTION}()"
    )
    op.execute(_postgresql_graph_sql())
    for table_name, trigger_name in PG_GRAPH_TRIGGERS.items():
        event = "INSERT OR UPDATE OR DELETE"
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} AFTER {event} ON "
            f"{table_name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION {PG_GRAPH_FUNCTION}()"
        )

    for table_name, trigger_name in (
        (CASE_TABLE, PG_CASE_VALIDATE_TRIGGER),
        (CASE_TABLE, PG_CASE_IMMUTABLE_TRIGGER),
        (CASE_TABLE, PG_CASE_TRUNCATE_TRIGGER),
        (ASSIGNMENT_TABLE, PG_ASSIGNMENT_VALIDATE_TRIGGER),
        (ASSIGNMENT_TABLE, PG_ASSIGNMENT_IMMUTABLE_TRIGGER),
        (ASSIGNMENT_TABLE, PG_ASSIGNMENT_TRUNCATE_TRIGGER),
        ("stocktake_tasks", PG_TASK_TRIGGER),
        ("stocktake_rounds", PG_ROUND_TRIGGER),
        *((table_name, trigger_name) for table_name, trigger_name in PG_GRAPH_TRIGGERS.items()),
    ):
        op.execute(f"ALTER TABLE {table_name} ENABLE ALWAYS TRIGGER {trigger_name}")


def _postgresql_case_validate_sql() -> str:
    return f"""
CREATE FUNCTION {PG_CASE_VALIDATE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    task_row stocktake_tasks%ROWTYPE;
    source_round stocktake_rounds%ROWTYPE;
    source_submission stocktake_round_submissions%ROWTYPE;
    source_completion stocktake_difference_set_completions%ROWTYPE;
    source_review stocktake_reviews%ROWTYPE;
    actual_scope_count bigint;
BEGIN
    SELECT * INTO task_row FROM stocktake_tasks
     WHERE id = NEW.task_id FOR UPDATE;
    SELECT * INTO source_round FROM stocktake_rounds
     WHERE id = NEW.source_round_id AND task_id = NEW.task_id FOR UPDATE;
    SELECT * INTO source_submission FROM stocktake_round_submissions
     WHERE id = NEW.source_round_submission_id;
    SELECT * INTO source_completion FROM stocktake_difference_set_completions
     WHERE id = NEW.source_difference_completion_id;
    SELECT * INTO source_review FROM stocktake_reviews
     WHERE id = NEW.trigger_review_id
       AND task_id = NEW.task_id AND round_id = NEW.source_round_id;
    SELECT count(*) INTO actual_scope_count FROM stocktake_scopes
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
       OR source_completion.round_submission_id <> NEW.source_round_submission_id
       OR source_review.id IS NULL OR source_review.decision <> 'recount'
       OR source_submission.submitted_at > source_completion.completed_at
       OR source_completion.completed_at > source_review.reviewed_at
       OR source_review.reviewed_at > NEW.opened_at
       OR actual_scope_count = 0 OR NEW.scope_count <> actual_scope_count
       OR EXISTS (SELECT 1 FROM stocktake_reviews
                   WHERE task_id = NEW.task_id AND round_id = NEW.source_round_id
                     AND reviewed_at > source_review.reviewed_at)
       OR EXISTS (SELECT 1 FROM stocktake_postings
                   WHERE task_id = NEW.task_id AND round_id = NEW.source_round_id)
       OR EXISTS (SELECT 1 FROM inventory_opening_establishments
                   WHERE task_id = NEW.task_id AND round_id = NEW.source_round_id)
       OR EXISTS (SELECT 1 FROM stocktake_rounds
                   WHERE task_id = NEW.task_id AND round_no = NEW.next_round_no)
       OR NOT rsc_stocktake_actor_assignment_valid_0011(
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


def _postgresql_assignment_validate_sql() -> str:
    return f"""
CREATE FUNCTION {PG_ASSIGNMENT_VALIDATE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    case_row {CASE_TABLE}%ROWTYPE;
    scope_row stocktake_scopes%ROWTYPE;
BEGIN
    SELECT * INTO case_row FROM {CASE_TABLE}
     WHERE id = NEW.recount_case_id AND task_id = NEW.task_id
       AND source_round_id = NEW.source_round_id FOR UPDATE;
    SELECT * INTO scope_row FROM stocktake_scopes
     WHERE id = NEW.scope_id AND task_id = NEW.task_id;
    IF case_row.id IS NULL OR scope_row.id IS NULL
       OR NEW.assigned_at < case_row.opened_at
       OR EXISTS (SELECT 1 FROM stocktake_rounds
                   WHERE recount_case_id = NEW.recount_case_id)
       OR NOT rsc_stocktake_actor_assignment_valid_0011(
            NEW.assignee_user_id, NEW.assignee_person_id,
            NEW.assignee_role_assignment_id, NEW.authorization_version,
            NEW.assigned_at, NEW.role_code, NEW.scope_type,
            NEW.scope_id_snapshot)
       OR NOT (
            (NEW.role_code = 'admin' AND NEW.scope_type = 'national'
             AND NEW.scope_id_snapshot = '*')
            OR (NEW.role_code = 'provincial_manager'
                AND NEW.scope_type = 'organization'
                AND NEW.scope_id_snapshot = scope_row.owner_org_id::text)
            OR (NEW.role_code = 'technician' AND NEW.scope_type = 'person'
                AND scope_row.custodian_person_id_snapshot IS NOT NULL
                AND NEW.scope_id_snapshot =
                    scope_row.custodian_person_id_snapshot::text)
       ) THEN
        RAISE EXCEPTION 'stocktake recount scope assignment is invalid';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_task_validate_sql() -> str:
    return f"""
CREATE FUNCTION {PG_TASK_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    case_row {CASE_TABLE}%ROWTYPE;
BEGIN
    IF NEW.task_type <> 'opening' THEN
        RETURN NEW;
    END IF;
    IF NEW.current_round_no IS DISTINCT FROM OLD.current_round_no
       AND NEW.current_round_no > 1 THEN
        SELECT * INTO case_row FROM {CASE_TABLE}
         WHERE task_id = NEW.id AND source_round_id = (
             SELECT id FROM stocktake_rounds
              WHERE task_id = NEW.id AND round_no = OLD.current_round_no
         ) AND next_round_no = NEW.current_round_no FOR UPDATE;
        IF OLD.status <> 'recount_required' OR NEW.status <> 'counting'
           OR NEW.current_round_no <> OLD.current_round_no + 1
           OR case_row.id IS NULL
           OR EXISTS (SELECT 1 FROM stocktake_rounds
                       WHERE task_id = NEW.id
                         AND round_no = NEW.current_round_no)
           OR case_row.scope_count <> (
                SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.id)
           OR case_row.scope_count <> (
                SELECT count(*) FROM {ASSIGNMENT_TABLE}
                 WHERE recount_case_id = case_row.id)
           OR EXISTS (
                SELECT 1 FROM stocktake_scopes AS scope
                 WHERE scope.task_id = NEW.id AND NOT EXISTS (
                     SELECT 1 FROM {ASSIGNMENT_TABLE} AS assignment
                      WHERE assignment.recount_case_id = case_row.id
                        AND assignment.task_id = NEW.id
                        AND assignment.scope_id = scope.id
                 )
           ) THEN
            RAISE EXCEPTION 'stocktake recount task advance is invalid';
        END IF;
    ELSIF OLD.status = 'recount_required' AND NEW.status = 'counting' THEN
        RAISE EXCEPTION 'stocktake recount task advance requires next round';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_round_validate_sql() -> str:
    return f"""
CREATE FUNCTION {PG_ROUND_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_task stocktake_tasks%ROWTYPE;
    case_row {CASE_TABLE}%ROWTYPE;
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
    SELECT * INTO parent_task FROM stocktake_tasks
     WHERE id = NEW.task_id FOR UPDATE;
    SELECT * INTO case_row FROM {CASE_TABLE}
     WHERE id = NEW.recount_case_id AND task_id = NEW.task_id
       AND next_round_no = NEW.round_no FOR UPDATE;
    IF NEW.round_type <> 'recount' OR case_row.id IS NULL
       OR parent_task.id IS NULL OR parent_task.status <> 'counting'
       OR parent_task.current_round_no <> NEW.round_no
       OR NOT EXISTS (SELECT 1 FROM stocktake_rounds AS source
                       WHERE source.id = case_row.source_round_id
                         AND source.task_id = NEW.task_id
                         AND source.round_no = NEW.round_no - 1
                         AND source.status = 'submitted')
       OR NEW.started_at < case_row.opened_at
       OR case_row.scope_count <> (
            SELECT count(*) FROM {ASSIGNMENT_TABLE}
             WHERE recount_case_id = case_row.id)
       OR EXISTS (
            SELECT 1 FROM stocktake_scopes AS scope
             WHERE scope.task_id = NEW.task_id AND NOT EXISTS (
                 SELECT 1 FROM {ASSIGNMENT_TABLE} AS assignment
                  WHERE assignment.recount_case_id = case_row.id
                    AND assignment.scope_id = scope.id
                    AND assignment.task_id = NEW.task_id
             )
       ) THEN
        RAISE EXCEPTION 'stocktake recount round causality is invalid';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_graph_sql() -> str:
    return f"""
CREATE FUNCTION {PG_GRAPH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
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
    SELECT current_round_no INTO task_round_no FROM stocktake_tasks
     WHERE id = affected_task_id;
    IF task_round_no IS NULL THEN
        RETURN NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM stocktake_rounds
                WHERE task_id = affected_task_id AND status = 'superseded')
       OR (task_round_no = 0 AND EXISTS (
            SELECT 1 FROM stocktake_rounds WHERE task_id = affected_task_id))
       OR (task_round_no > 0 AND (
            SELECT count(*) FROM stocktake_rounds
             WHERE task_id = affected_task_id) <> task_round_no)
       OR (task_round_no > 0 AND (
            SELECT min(round_no) FROM stocktake_rounds
             WHERE task_id = affected_task_id) <> 1)
       OR (task_round_no > 0 AND (
            SELECT max(round_no) FROM stocktake_rounds
             WHERE task_id = affected_task_id) <> task_round_no)
       OR EXISTS (
            SELECT 1 FROM stocktake_rounds AS successor
             LEFT JOIN {CASE_TABLE} AS recount_case
               ON recount_case.id = successor.recount_case_id
              AND recount_case.task_id = successor.task_id
              AND recount_case.next_round_no = successor.round_no
             LEFT JOIN stocktake_rounds AS source
               ON source.id = recount_case.source_round_id
              AND source.task_id = successor.task_id
              AND source.round_no = successor.round_no - 1
              AND source.status = 'submitted'
            WHERE successor.task_id = affected_task_id
              AND successor.round_no > 1
              AND (recount_case.id IS NULL OR source.id IS NULL)
       ) OR EXISTS (
            SELECT 1 FROM {CASE_TABLE} AS recount_case
             WHERE recount_case.task_id = affected_task_id
               AND (1 <> (SELECT count(*) FROM stocktake_rounds AS successor
                           WHERE successor.recount_case_id = recount_case.id
                             AND successor.task_id = recount_case.task_id
                             AND successor.round_no = recount_case.next_round_no)
                    OR recount_case.scope_count <> (
                        SELECT count(*) FROM {ASSIGNMENT_TABLE} AS assignment
                         WHERE assignment.recount_case_id = recount_case.id)
                    OR recount_case.scope_count <> (
                        SELECT count(*) FROM stocktake_scopes AS scope
                         WHERE scope.task_id = recount_case.task_id)
                    OR EXISTS (
                        SELECT 1 FROM stocktake_scopes AS scope
                         WHERE scope.task_id = recount_case.task_id
                           AND NOT EXISTS (
                               SELECT 1 FROM {ASSIGNMENT_TABLE} AS assignment
                                WHERE assignment.recount_case_id = recount_case.id
                                  AND assignment.task_id = recount_case.task_id
                                  AND assignment.scope_id = scope.id)))
       ) OR EXISTS (
            SELECT 1 FROM stocktake_rounds
             WHERE task_id = affected_task_id AND status = 'counting'
               AND round_no <> task_round_no
       ) THEN
        RAISE EXCEPTION 'stocktake recount graph is incomplete or non-contiguous';
    END IF;
    RETURN NULL;
END;
$$
"""


def _create_sqlite_guards() -> None:
    for table_name in (CASE_TABLE, ASSIGNMENT_TABLE):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable_"
                f"{operation.lower()}_0018 BEFORE {operation} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, 'stocktake recount facts are "
                "immutable'); END"
            )
    op.execute(_sqlite_case_validate_sql())
    op.execute(_sqlite_assignment_validate_sql())
    op.execute(_sqlite_task_validate_sql())
    op.execute(_sqlite_round_insert_sql())
    op.execute(_sqlite_round_update_sql())


def _sqlite_case_validate_sql() -> str:
    return f"""
CREATE TRIGGER {PG_CASE_VALIDATE_TRIGGER}
BEFORE INSERT ON {CASE_TABLE}
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
           AND review.decision = 'recount'
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


def _sqlite_assignment_validate_sql() -> str:
    return f"""
CREATE TRIGGER {PG_ASSIGNMENT_VALIDATE_TRIGGER}
BEFORE INSERT ON {ASSIGNMENT_TABLE}
WHEN NOT EXISTS (
        SELECT 1 FROM {CASE_TABLE} AS recount_case
        JOIN stocktake_scopes AS scope
          ON scope.id = NEW.scope_id AND scope.task_id = recount_case.task_id
         WHERE recount_case.id = NEW.recount_case_id
           AND recount_case.task_id = NEW.task_id
           AND recount_case.source_round_id = NEW.source_round_id
           AND NEW.assigned_at >= recount_case.opened_at
           AND NOT EXISTS (SELECT 1 FROM stocktake_rounds
                            WHERE recount_case_id = NEW.recount_case_id)
           AND EXISTS (
                SELECT 1 FROM role_assignments AS assignment
                JOIN roles AS role ON role.id = assignment.role_id
                JOIN users AS actor ON actor.id = assignment.user_id
                 WHERE assignment.id = NEW.assignee_role_assignment_id
                   AND assignment.user_id = NEW.assignee_user_id
                   AND actor.person_id = NEW.assignee_person_id
                   AND actor.authorization_version = NEW.authorization_version
                   AND assignment.status IN ('active', 'expired', 'revoked')
                   AND assignment.valid_from <= NEW.assigned_at
                   AND (assignment.valid_to IS NULL
                        OR NEW.assigned_at < assignment.valid_to)
                   AND (assignment.revoked_at IS NULL
                        OR NEW.assigned_at < assignment.revoked_at)
                   AND role.is_external = 0 AND role.code = NEW.role_code
                   AND assignment.scope_type = NEW.scope_type
                   AND assignment.scope_id = NEW.scope_id_snapshot
                   AND ((NEW.role_code = 'admin'
                         AND NEW.scope_type = 'national'
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
           )
     )
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount scope assignment is invalid');
END
"""


def _sqlite_task_validate_sql() -> str:
    return f"""
CREATE TRIGGER {PG_TASK_TRIGGER}
BEFORE UPDATE ON stocktake_tasks
WHEN NEW.task_type = 'opening'
 AND ((NEW.current_round_no IS NOT OLD.current_round_no
       AND NEW.current_round_no > 1)
      OR (OLD.status = 'recount_required' AND NEW.status = 'counting'))
 AND NOT (
      OLD.status = 'recount_required' AND NEW.status = 'counting'
      AND NEW.current_round_no = OLD.current_round_no + 1
      AND EXISTS (
          SELECT 1 FROM {CASE_TABLE} AS recount_case
           WHERE recount_case.task_id = NEW.id
             AND recount_case.next_round_no = NEW.current_round_no
             AND recount_case.source_round_id = (
                 SELECT id FROM stocktake_rounds
                  WHERE task_id = NEW.id AND round_no = OLD.current_round_no)
             AND recount_case.scope_count = (
                 SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.id)
             AND recount_case.scope_count = (
                 SELECT count(*) FROM {ASSIGNMENT_TABLE}
                  WHERE recount_case_id = recount_case.id)
             AND NOT EXISTS (
                 SELECT 1 FROM stocktake_scopes AS scope
                  WHERE scope.task_id = NEW.id AND NOT EXISTS (
                      SELECT 1 FROM {ASSIGNMENT_TABLE} AS assignment
                       WHERE assignment.recount_case_id = recount_case.id
                         AND assignment.task_id = NEW.id
                         AND assignment.scope_id = scope.id))
      )
      AND NOT EXISTS (SELECT 1 FROM stocktake_rounds
                       WHERE task_id = NEW.id
                         AND round_no = NEW.current_round_no)
 )
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount task advance is invalid');
END
"""


def _sqlite_round_insert_sql() -> str:
    return f"""
CREATE TRIGGER {PG_ROUND_TRIGGER}_insert
BEFORE INSERT ON stocktake_rounds
WHEN NEW.status = 'superseded'
 OR (NEW.round_no = 1 AND (
        NEW.round_type <> 'initial' OR NEW.recount_case_id IS NOT NULL))
 OR (NEW.round_no > 1 AND NOT EXISTS (
        SELECT 1 FROM {CASE_TABLE} AS recount_case
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
           AND recount_case.scope_count = (
               SELECT count(*) FROM {ASSIGNMENT_TABLE}
                WHERE recount_case_id = recount_case.id)
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_scopes AS scope
                WHERE scope.task_id = NEW.task_id AND NOT EXISTS (
                    SELECT 1 FROM {ASSIGNMENT_TABLE} AS assignment
                     WHERE assignment.recount_case_id = recount_case.id
                       AND assignment.task_id = NEW.task_id
                       AND assignment.scope_id = scope.id))
     ))
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount round causality is invalid');
END
"""


def _sqlite_round_update_sql() -> str:
    return f"""
CREATE TRIGGER {PG_ROUND_TRIGGER}_update
BEFORE UPDATE ON stocktake_rounds
WHEN NEW.status = 'superseded'
 OR OLD.task_id IS NOT NEW.task_id OR OLD.round_no IS NOT NEW.round_no
 OR OLD.round_type IS NOT NEW.round_type
 OR OLD.recount_case_id IS NOT NEW.recount_case_id
 OR (NEW.round_no > 1 AND NOT EXISTS (
        SELECT 1 FROM {CASE_TABLE} AS recount_case
        JOIN stocktake_tasks AS task ON task.id = recount_case.task_id
        JOIN stocktake_rounds AS source
          ON source.id = recount_case.source_round_id
         AND source.task_id = recount_case.task_id
         WHERE recount_case.id = NEW.recount_case_id
           AND recount_case.task_id = NEW.task_id
           AND recount_case.next_round_no = NEW.round_no
           AND task.current_round_no = NEW.round_no
           AND source.round_no = NEW.round_no - 1
           AND source.status = 'submitted'))
BEGIN
    SELECT RAISE(ABORT, 'stocktake recount round causality is invalid');
END
"""


def _apply_postgresql_acl() -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE {CASE_TABLE}, {ASSIGNMENT_TABLE} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    for function_name in (
        PG_CASE_VALIDATE_FUNCTION,
        PG_ASSIGNMENT_VALIDATE_FUNCTION,
        PG_IMMUTABLE_FUNCTION,
        PG_TASK_FUNCTION,
        PG_ROUND_FUNCTION,
        PG_GRAPH_FUNCTION,
    ):
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION {function_name}() "
            f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
        )


def _drop_postgresql_guards() -> None:
    for table_name, trigger_name in PG_GRAPH_TRIGGERS.items():
        op.execute(f"DROP TRIGGER {trigger_name} ON {table_name}")
    op.execute(f"DROP TRIGGER {PG_ROUND_TRIGGER} ON stocktake_rounds")
    op.execute(f"DROP TRIGGER {PG_TASK_TRIGGER} ON stocktake_tasks")
    for table_name, trigger_name in (
        (ASSIGNMENT_TABLE, PG_ASSIGNMENT_TRUNCATE_TRIGGER),
        (ASSIGNMENT_TABLE, PG_ASSIGNMENT_IMMUTABLE_TRIGGER),
        (ASSIGNMENT_TABLE, PG_ASSIGNMENT_VALIDATE_TRIGGER),
        (CASE_TABLE, PG_CASE_TRUNCATE_TRIGGER),
        (CASE_TABLE, PG_CASE_IMMUTABLE_TRIGGER),
        (CASE_TABLE, PG_CASE_VALIDATE_TRIGGER),
    ):
        op.execute(f"DROP TRIGGER {trigger_name} ON {table_name}")
    for function_name in (
        PG_GRAPH_FUNCTION,
        PG_ROUND_FUNCTION,
        PG_TASK_FUNCTION,
        PG_IMMUTABLE_FUNCTION,
        PG_ASSIGNMENT_VALIDATE_FUNCTION,
        PG_CASE_VALIDATE_FUNCTION,
    ):
        op.execute(f"DROP FUNCTION {function_name}()")


def _drop_sqlite_guards() -> None:
    for trigger_name in (
        f"{PG_ROUND_TRIGGER}_update",
        f"{PG_ROUND_TRIGGER}_insert",
        PG_TASK_TRIGGER,
        PG_ASSIGNMENT_VALIDATE_TRIGGER,
        PG_CASE_VALIDATE_TRIGGER,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    for table_name in (CASE_TABLE, ASSIGNMENT_TABLE):
        for operation in ("update", "delete"):
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_"
                f"{operation}_0018"
            )


def _assert_sqlite_foreign_keys_clean() -> None:
    rows = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").all()
    if rows:
        raise RuntimeError("0018 SQLite migration left foreign-key violations")
