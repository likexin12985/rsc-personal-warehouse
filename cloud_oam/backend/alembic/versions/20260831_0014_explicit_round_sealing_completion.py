"""Persist the exact scope completion that sealed a stocktake round.

Revision ID: 20260831_0014
Revises: 20260831_0013
Create Date: 2026-08-31

Earlier code inferred the sealing completion from actor and timestamp equality.
Two legitimate scope submissions can share a database microsecond, so that
inference is not an immutable identity.  This revision backfills only when one
exact historical completion is provable and requires every future submission
to name a completion from the same task and round.
"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260831_0014"
down_revision: Union[str, Sequence[str], None] = "20260831_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "stocktake_round_submissions"
COLUMN_NAME = "sealing_completion_id"
INDEX_NAME = "uq_stocktake_round_submissions_sealing_completion"
TRIGGER_NAME = "trg_stocktake_round_submissions_sealing_completion_0014"
FUNCTION_NAME = "rsc_validate_round_sealing_completion_0014"
PG_IMMUTABILITY_TRIGGER = "trg_stocktake_round_submissions_immutable_0011"
SQLITE_IMMUTABILITY_UPDATE_TRIGGER = (
    "trg_stocktake_round_submissions_immutable_update_0011"
)
UPGRADE_BLOCKER = (
    "0014 preflight failed: round submission sealing completion is missing "
    "or ambiguous"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0014: round submissions require explicit sealing "
    "completion evidence"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0014 supports only PostgreSQL production and SQLite local test schemas"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0014 SQLite upgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
        # A killed SQLite migration may have stopped while the old UPDATE
        # trigger was temporarily absent.  Restore the exact 0011 boundary
        # before inspecting or changing anything else.
        _ensure_sqlite_submission_update_immutability()
    _assert_historical_sealing_completion_is_unique(dialect)
    if dialect == "postgresql":
        op.add_column(
            TABLE_NAME,
            sa.Column(COLUMN_NAME, sa.Uuid(), nullable=True),
        )
        # 0011 deliberately rejects every UPDATE to the immutable submission.
        # The table lock acquired by the preflight blocks concurrent DML while
        # this reviewed migration temporarily suspends only that trigger.
        op.execute(
            f"ALTER TABLE {TABLE_NAME} DISABLE TRIGGER "
            f"{PG_IMMUTABILITY_TRIGGER}"
        )
        op.execute(_backfill_sql())
        op.execute(
            f"ALTER TABLE {TABLE_NAME} ENABLE TRIGGER "
            f"{PG_IMMUTABILITY_TRIGGER}"
        )
        op.create_index(
            INDEX_NAME,
            TABLE_NAME,
            [COLUMN_NAME],
            unique=True,
        )
        _create_validation_guard(dialect)
        return

    _upgrade_sqlite_retryable()


def _ensure_sqlite_migration_transaction() -> None:
    """Force SQLite DDL/DML for this revision into one real transaction."""

    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _upgrade_sqlite_retryable() -> None:
    """Complete 0014 safely even after a non-transactional SQLite stop."""

    if not _sqlite_column_exists(COLUMN_NAME):
        op.add_column(
            TABLE_NAME,
            sa.Column(COLUMN_NAME, sa.Uuid(), nullable=True),
        )

    # Remove a guard left by an interrupted retry before the deterministic
    # backfill.  The 0011 UPDATE trigger is always restored in ``finally``.
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_IMMUTABILITY_UPDATE_TRIGGER}")
    try:
        op.execute(_backfill_sql())
    finally:
        _ensure_sqlite_submission_update_immutability()

    invalid = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM stocktake_round_submissions AS submission "
        "WHERE submission.sealing_completion_id IS NULL OR NOT EXISTS ("
        "SELECT 1 FROM stocktake_scope_count_completions AS completion "
        "WHERE completion.id = submission.sealing_completion_id AND "
        f"{_candidate_predicate()}) LIMIT 1"
    ).first()
    if invalid is not None:
        raise RuntimeError(UPGRADE_BLOCKER)

    indexes = {
        row["name"]: row
        for row in sa.inspect(op.get_bind()).get_indexes(TABLE_NAME)
    }
    existing_index = indexes.get(INDEX_NAME)
    if existing_index is None:
        op.create_index(
            INDEX_NAME,
            TABLE_NAME,
            [COLUMN_NAME],
            unique=True,
        )
    elif (
        existing_index.get("column_names") != [COLUMN_NAME]
        or not existing_index.get("unique")
    ):
        raise RuntimeError(
            "0014 partial schema conflict: sealing completion index shape "
            "is not canonical"
        )
    _create_validation_guard("sqlite")


def _sqlite_column_exists(column_name: str) -> bool:
    return column_name in {
        row["name"]
        for row in sa.inspect(op.get_bind()).get_columns(TABLE_NAME)
    }


def _ensure_sqlite_submission_update_immutability() -> None:
    trigger_names = {
        row[0]
        for row in op.get_bind().exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND tbl_name = ?",
            (TABLE_NAME,),
        ).all()
    }
    if SQLITE_IMMUTABILITY_UPDATE_TRIGGER in trigger_names:
        return
    op.execute(
        f"CREATE TRIGGER {SQLITE_IMMUTABILITY_UPDATE_TRIGGER} "
        f"BEFORE UPDATE ON {TABLE_NAME} BEGIN SELECT RAISE(ABORT, "
        "'opening count observation and completion facts are immutable'); END"
    )


def _candidate_predicate() -> str:
    return """
completion.task_id = submission.task_id
AND completion.round_id = submission.round_id
AND completion.completed_by_user_id = submission.submitted_by_user_id
AND completion.completed_by_person_id = submission.submitted_by_person_id
AND completion.completed_role_assignment_id = submission.submitted_role_assignment_id
AND completion.authorization_version = submission.authorization_version
AND completion.completed_at = submission.submitted_at
"""


def _assert_historical_sealing_completion_is_unique(dialect: str) -> None:
    predicate = _candidate_predicate()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0014 SQLite upgrade requires an online connection")
        op.execute(
            "LOCK TABLE stocktake_round_submissions, "
            "stocktake_scope_count_completions IN SHARE ROW EXCLUSIVE MODE"
        )
        op.execute(
            f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM stocktake_round_submissions AS submission
         WHERE (
             SELECT count(*)
               FROM stocktake_scope_count_completions AS completion
              WHERE {predicate}
         ) <> 1
    ) THEN
        RAISE EXCEPTION
            '0014 preflight failed: round submission sealing completion is missing or ambiguous';
    END IF;
END $$
"""
        )
        return

    bind = op.get_bind()
    if dialect == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE stocktake_round_submissions, "
            "stocktake_scope_count_completions IN SHARE ROW EXCLUSIVE MODE"
        )
    invalid = bind.exec_driver_sql(
        "SELECT 1 FROM stocktake_round_submissions AS submission "
        "WHERE (SELECT count(*) FROM stocktake_scope_count_completions "
        f"AS completion WHERE {predicate}) <> 1 LIMIT 1"
    ).first()
    if invalid is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _backfill_sql() -> str:
    return f"""
UPDATE stocktake_round_submissions AS submission
   SET sealing_completion_id = (
       SELECT completion.id
         FROM stocktake_scope_count_completions AS completion
        WHERE {_candidate_predicate()}
   )
"""


def _create_validation_guard(dialect: str) -> None:
    if dialect == "postgresql":
        op.execute(
            f"""
CREATE FUNCTION {FUNCTION_NAME}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.sealing_completion_id IS NULL OR NOT EXISTS (
        SELECT 1
          FROM stocktake_scope_count_completions AS completion
         WHERE completion.id = NEW.sealing_completion_id
           AND completion.task_id = NEW.task_id
           AND completion.round_id = NEW.round_id
           AND completion.completed_by_user_id = NEW.submitted_by_user_id
           AND completion.completed_by_person_id = NEW.submitted_by_person_id
           AND completion.completed_role_assignment_id =
               NEW.submitted_role_assignment_id
           AND completion.authorization_version = NEW.authorization_version
           AND completion.completed_at = NEW.submitted_at
    ) THEN
        RAISE EXCEPTION
            'round submission sealing completion is missing or inconsistent';
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        op.execute(
            f"CREATE TRIGGER {TRIGGER_NAME} "
            "BEFORE INSERT ON stocktake_round_submissions FOR EACH ROW "
            f"EXECUTE FUNCTION {FUNCTION_NAME}()"
        )
        return

    op.execute(
        f"""
CREATE TRIGGER {TRIGGER_NAME}
BEFORE INSERT ON stocktake_round_submissions
WHEN NEW.sealing_completion_id IS NULL OR NOT EXISTS (
    SELECT 1
      FROM stocktake_scope_count_completions AS completion
     WHERE completion.id = NEW.sealing_completion_id
       AND completion.task_id = NEW.task_id
       AND completion.round_id = NEW.round_id
       AND completion.completed_by_user_id = NEW.submitted_by_user_id
       AND completion.completed_by_person_id = NEW.submitted_by_person_id
       AND completion.completed_role_assignment_id =
           NEW.submitted_role_assignment_id
       AND completion.authorization_version = NEW.authorization_version
       AND completion.completed_at = NEW.submitted_at
)
BEGIN
    SELECT RAISE(ABORT,
        'round submission sealing completion is missing or inconsistent');
END
"""
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0014 downgrade requires an online connection for fail-closed "
            "round-submission evidence checks"
        )
    dialect = _dialect_name()
    bind = op.get_bind()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        bind.exec_driver_sql(
            "LOCK TABLE stocktake_round_submissions IN ACCESS EXCLUSIVE MODE"
        )
    if bind.exec_driver_sql(
        "SELECT 1 FROM stocktake_round_submissions LIMIT 1"
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)

    op.execute(
        (
            f"DROP TRIGGER {TRIGGER_NAME} ON stocktake_round_submissions"
            if dialect == "postgresql"
            else f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}"
        )
    )
    if dialect == "postgresql":
        op.execute(f"DROP FUNCTION {FUNCTION_NAME}()")
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
        op.drop_column(TABLE_NAME, COLUMN_NAME)
        return

    # An empty-table downgrade is safe to resume after an older process lost
    # the guard or index before Alembic could advance the version marker.
    if INDEX_NAME in {
        row["name"] for row in sa.inspect(bind).get_indexes(TABLE_NAME)
    }:
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
    if _sqlite_column_exists(COLUMN_NAME):
        op.drop_column(TABLE_NAME, COLUMN_NAME)
