"""Seal and atomically post approved non-opening stocktake differences.

Revision ID: 20260901_0035
Revises: 20260901_0034
Create Date: 2026-09-01

The migration deliberately refuses to invent a task-wide approval seal for
pre-existing non-opening terminal facts.  Final headquarters approval,
inventory posting, notification delivery, reconciliation and task close remain
independent facts.  This revision adds only approval/posting evidence and the
bounded owner lock required by the local posting transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Sequence, Union
import uuid

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260901_0035"
down_revision: Union[str, Sequence[str], None] = "20260901_0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
MAXIMUM_LOCK_ROWS = 100_000
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
NEW_POSTING_KINDS_SQL = (
    "('difference_gain', 'difference_loss', 'difference_transfer', "
    "'difference_status_change')"
)
ALL_POSTING_KINDS_SQL = (
    "('opening', 'difference_adjustment', 'difference_gain', "
    "'difference_loss', 'difference_transfer', 'difference_status_change')"
)
FREEZE_RELEASE_REASON = "非期初盘点差异已安全过账并释放冻结"

EFFECTIVE_COMPLETION = "stocktake_effective_approval_completions"
EFFECTIVE_SCOPE = "stocktake_effective_approval_scopes"
EFFECTIVE_ITEM = "stocktake_effective_approval_items"
POSTING_COMPLETION = "stocktake_posting_completions"
POSTING_COMPLETION_ITEM = "stocktake_posting_completion_items"
NEW_TABLES = (
    EFFECTIVE_COMPLETION,
    EFFECTIVE_SCOPE,
    EFFECTIVE_ITEM,
    POSTING_COMPLETION,
    POSTING_COMPLETION_ITEM,
)

PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000056")
ROLE_PERMISSION_ID = uuid.UUID("21000000-0000-4000-8000-000000000100")
ADMIN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")

PG_LOCK_FUNCTION = "rsc_lock_nonopening_stocktake_posting_graph_0035"
PG_GRAPH_FUNCTION = "rsc_require_nonopening_stocktake_posting_graph_0035"
PG_GRAPH_TRIGGERS = {
    "stocktake_tasks": "trg_stocktake_tasks_nonopening_posting_graph_0035",
    EFFECTIVE_COMPLETION: "trg_stocktake_effective_approval_completion_graph_0035",
    EFFECTIVE_SCOPE: "trg_stocktake_effective_approval_scope_graph_0035",
    EFFECTIVE_ITEM: "trg_stocktake_effective_approval_item_graph_0035",
    "stocktake_postings": "trg_stocktake_difference_posting_graph_0035",
    "stocktake_posting_items": "trg_stocktake_difference_posting_item_graph_0035",
    POSTING_COMPLETION: "trg_stocktake_posting_completion_graph_0035",
    POSTING_COMPLETION_ITEM: "trg_stocktake_posting_completion_item_graph_0035",
}
SQLITE_APPROVAL_TRIGGER = "trg_stocktake_effective_approval_task_update_0035"
SQLITE_POSTING_TRIGGER = "trg_stocktake_posting_completion_task_update_0035"
SQLITE_POSTING_ITEM_TRIGGER = "trg_stocktake_difference_posting_item_insert_0035"

UPGRADE_BLOCKER = (
    "0035 preflight failed: existing non-opening terminal review/posting facts "
    "require manual evidence quarantine"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0035 while effective approval or safe posting facts exist"
)


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0035 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0035 SQLite upgrade requires an online connection")
        _postgresql_lock_and_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_upgrade_graph()
        _online_preflight(dialect)

    _create_effective_approval_tables()
    _extend_stocktake_postings(dialect)
    _create_posting_completion_tables()
    op.create_index(
        "uq_stocktake_posting_items_difference_0035",
        "stocktake_posting_items",
        ["difference_id"],
        unique=True,
        postgresql_where=sa.text("difference_id IS NOT NULL"),
        sqlite_where=sa.text("difference_id IS NOT NULL"),
    )
    _seed_permission()

    if dialect == "postgresql":
        op.execute(_postgresql_lock_function_sql())
        op.execute(_postgresql_graph_function_sql())
        for table_name, trigger_name in PG_GRAPH_TRIGGERS.items():
            op.execute(
                f"CREATE CONSTRAINT TRIGGER {trigger_name} "
                f"AFTER INSERT OR UPDATE OR DELETE ON public.{table_name} "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                f"EXECUTE FUNCTION public.{PG_GRAPH_FUNCTION}()"
            )
            op.execute(
                f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
            )
        _apply_postgresql_acl()
        return

    _create_sqlite_immutability_triggers()
    op.execute(_sqlite_approval_task_trigger_sql())
    op.execute(_sqlite_posting_task_trigger_sql())
    op.execute(_sqlite_posting_item_trigger_sql())


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0035 downgrade requires an online evidence check")
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_upgrade_graph()
    _require_safe_downgrade(dialect)

    if dialect == "postgresql":
        for table_name, trigger_name in PG_GRAPH_TRIGGERS.items():
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_GRAPH_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_LOCK_FUNCTION}(uuid)")
        _revoke_postgresql_acl()
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_APPROVAL_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_POSTING_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_POSTING_ITEM_TRIGGER}")
        for table_name in NEW_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_{operation}_0035"
                )

    _delete_permission()
    op.drop_index(
        "uq_stocktake_posting_items_difference_0035",
        table_name="stocktake_posting_items",
    )
    for table_name in (POSTING_COMPLETION_ITEM, POSTING_COMPLETION):
        op.drop_table(table_name)
    _restore_stocktake_postings(dialect)
    for table_name in (EFFECTIVE_ITEM, EFFECTIVE_SCOPE, EFFECTIVE_COMPLETION):
        op.drop_table(table_name)


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_upgrade_graph() -> None:
    op.get_bind().exec_driver_sql(
        "LOCK TABLE public.stocktake_tasks, public.stocktake_rounds, "
        "public.stocktake_reviews, public.stocktake_postings, "
        "public.stocktake_posting_items, public.permissions, "
        "public.role_permissions IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_lock_and_preflight() -> None:
    op.execute(
        "LOCK TABLE public.stocktake_tasks, public.stocktake_rounds, "
        "public.stocktake_reviews, public.stocktake_postings, "
        "public.stocktake_posting_items, public.permissions, "
        "public.role_permissions IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF {_upgrade_blocker_sql('public.')} THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_preflight(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {_upgrade_blocker_sql(prefix)}"
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _upgrade_blocker_sql(prefix: str) -> str:
    return f"""EXISTS (
        SELECT 1
          FROM {prefix}stocktake_tasks AS task
         WHERE task.task_type IN {NONOPENING_SQL}
           AND (
                task.status IN ('approved', 'posted', 'closed')
                OR EXISTS (
                    SELECT 1 FROM {prefix}stocktake_reviews AS review
                     WHERE review.task_id = task.id
                       AND review.review_stage = 'headquarters'
                       AND review.decision = 'approve')
                OR EXISTS (
                    SELECT 1 FROM {prefix}stocktake_postings AS posting
                     WHERE posting.task_id = task.id)
           )
    ) OR EXISTS (
        SELECT 1 FROM {prefix}permissions
         WHERE replace(CAST(id AS TEXT), '-', '') = '{PERMISSION_ID.hex}'
            OR (resource = 'stocktake' AND action = 'post_difference'
                AND field_code = '')
    ) OR EXISTS (
        SELECT 1 FROM {prefix}role_permissions
         WHERE replace(CAST(id AS TEXT), '-', '') = '{ROLE_PERMISSION_ID.hex}'
    )"""


def _require_safe_downgrade(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    predicates = " OR ".join(
        f"EXISTS (SELECT 1 FROM {prefix}{table_name})" for table_name in NEW_TABLES
    )
    predicates += (
        f" OR EXISTS (SELECT 1 FROM {prefix}stocktake_postings "
        f"WHERE posting_kind IN {NEW_POSTING_KINDS_SQL})"
    )
    if op.get_bind().exec_driver_sql(f"SELECT 1 WHERE {predicates}").first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _create_effective_approval_tables() -> None:
    op.create_table(
        EFFECTIVE_COMPLETION,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("terminal_round_id", sa.Uuid(), nullable=False),
        sa.Column("terminal_headquarters_review_id", sa.Uuid(), nullable=False),
        sa.Column("expected_task_version", sa.BigInteger(), nullable=False),
        sa.Column("approved_task_version", sa.BigInteger(), nullable=False),
        sa.Column("scope_count", sa.Integer(), nullable=False),
        sa.Column("difference_count", sa.Integer(), nullable=False),
        sa.Column("accepted_difference_count", sa.Integer(), nullable=False),
        sa.Column("no_adjustment_count", sa.Integer(), nullable=False),
        sa.Column("approval_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("completed_by_user_id", sa.String(36), nullable=False),
        sa.Column("completed_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("completed_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(80), nullable=False),
        sa.Column("authorization_sha256", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_effective_approval_completions_0035"),
        sa.UniqueConstraint("task_id", name="uq_stocktake_effective_approval_task_0035"),
        sa.UniqueConstraint(
            "terminal_headquarters_review_id",
            name="uq_stocktake_effective_approval_hq_review_0035",
        ),
        sa.UniqueConstraint(
            "id", "task_id", name="uq_stocktake_effective_approval_id_task_0035"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["stocktake_tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["terminal_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_effective_approval_round_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["terminal_headquarters_review_id", "task_id", "terminal_round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_stocktake_effective_approval_hq_review_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["completed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["completed_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["completed_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "scope_count > 0 AND difference_count >= 0 AND "
            "accepted_difference_count >= 0 AND no_adjustment_count >= 0 AND "
            "accepted_difference_count + no_adjustment_count = difference_count",
            name="ck_stocktake_effective_approval_counts_0035",
        ),
        sa.CheckConstraint(
            "expected_task_version >= 0 AND approved_task_version = expected_task_version + 1",
            name="ck_stocktake_effective_approval_versions_0035",
        ),
        sa.CheckConstraint(
            "authorization_version > 0 AND role_code = 'admin' AND "
            "scope_type = 'national' AND scope_id_snapshot = '*'",
            name="ck_stocktake_effective_approval_authorization_0035",
        ),
        sa.CheckConstraint(
            "length(approval_manifest_sha256) = 64 AND length(request_sha256) = 64 "
            "AND length(authorization_sha256) = 64",
            name="ck_stocktake_effective_approval_hashes_0035",
        ),
        sa.CheckConstraint(
            "created_at = completed_at",
            name="ck_stocktake_effective_approval_chronology_0035",
        ),
    )
    op.create_index(
        "ix_stocktake_effective_approval_completed_0035",
        EFFECTIVE_COMPLETION,
        ["completed_by_user_id", "completed_at"],
    )
    op.create_table(
        EFFECTIVE_SCOPE,
        sa.Column("completion_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("source_round_id", sa.Uuid(), nullable=False),
        sa.Column("source_difference_completion_id", sa.Uuid(), nullable=False),
        sa.Column("regional_review_id", sa.Uuid(), nullable=False),
        sa.Column("difference_count", sa.Integer(), nullable=False),
        sa.Column("scope_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "completion_id", "scope_id", name="pk_stocktake_effective_approval_scopes_0035"
        ),
        sa.ForeignKeyConstraint(
            ["completion_id", "task_id"],
            [f"{EFFECTIVE_COMPLETION}.id", f"{EFFECTIVE_COMPLETION}.task_id"],
            name="fk_stocktake_effective_approval_scopes_completion_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_effective_approval_scopes_scope_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_effective_approval_scopes_round_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_difference_completion_id"],
            ["stocktake_difference_set_completions.id"],
            name="fk_stocktake_eff_approval_scopes_diff_completion_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["regional_review_id", "task_id", "source_round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_stocktake_effective_approval_scopes_region_review_0035",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("difference_count >= 0", name="ck_stocktake_effective_approval_scopes_count_0035"),
        sa.CheckConstraint("length(scope_manifest_sha256) = 64", name="ck_stocktake_effective_approval_scopes_hash_0035"),
    )
    op.create_index(
        "ix_stocktake_effective_approval_scopes_source_0035",
        EFFECTIVE_SCOPE,
        ["task_id", "source_round_id"],
    )
    op.create_table(
        EFFECTIVE_ITEM,
        sa.Column("completion_id", sa.Uuid(), nullable=False),
        sa.Column("difference_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("source_round_id", sa.Uuid(), nullable=False),
        sa.Column("regional_review_id", sa.Uuid(), nullable=False),
        sa.Column("regional_decision", sa.String(32), nullable=False),
        sa.Column("headquarters_decision", sa.String(32), nullable=False),
        sa.Column("headquarters_comment", sa.Text(), nullable=False),
        sa.Column("item_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "completion_id", "difference_id", name="pk_stocktake_effective_approval_items_0035"
        ),
        sa.UniqueConstraint("difference_id", name="uq_stocktake_effective_approval_items_difference_0035"),
        sa.ForeignKeyConstraint(
            ["completion_id", "scope_id"],
            [f"{EFFECTIVE_SCOPE}.completion_id", f"{EFFECTIVE_SCOPE}.scope_id"],
            name="fk_stocktake_effective_approval_items_scope_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["difference_id", "task_id", "source_round_id"],
            ["stocktake_differences.id", "stocktake_differences.task_id", "stocktake_differences.round_id"],
            name="fk_stocktake_effective_approval_items_difference_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["regional_review_id", "task_id", "source_round_id"],
            ["stocktake_reviews.id", "stocktake_reviews.task_id", "stocktake_reviews.round_id"],
            name="fk_stocktake_effective_approval_items_region_review_0035",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "regional_decision IN ('accept_for_posting', 'no_adjustment') AND "
            "headquarters_decision = regional_decision",
            name="ck_stocktake_effective_approval_items_decisions_0035",
        ),
        sa.CheckConstraint("length(trim(headquarters_comment)) > 0", name="ck_stocktake_effective_approval_items_comment_0035"),
        sa.CheckConstraint("length(item_manifest_sha256) = 64", name="ck_stocktake_effective_approval_items_hash_0035"),
    )
    op.create_index(
        "ix_stocktake_effective_approval_items_scope_0035",
        EFFECTIVE_ITEM,
        ["completion_id", "scope_id"],
    )


def _capture_and_drop_sqlite_posting_dependency_triggers() -> tuple[str, ...]:
    """Temporarily remove every trigger whose body references the rebuilt table.

    SQLite reparses all trigger bodies while Alembic renames its batch-copy
    table.  A trigger owned by another stocktake table can therefore make the
    rename fail while ``stocktake_postings`` is momentarily absent.  Preserving
    only triggers *owned* by the table is insufficient; capture the complete
    dependency set and recreate it verbatim after the rebuild.
    """

    if _dialect_name() != "sqlite":
        return ()
    rows = tuple(
        op.get_bind().exec_driver_sql(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='trigger' AND sql IS NOT NULL "
            "AND lower(sql) LIKE '%stocktake_postings%' ORDER BY name"
        ).all()
    )
    for name, _sql in rows:
        escaped_name = str(name).replace('"', '""')
        op.execute(f'DROP TRIGGER IF EXISTS "{escaped_name}"')
    return tuple(str(sql) for _name, sql in rows)


def _extend_stocktake_postings(dialect: str) -> None:
    triggers = _capture_and_drop_sqlite_posting_dependency_triggers()
    with op.batch_alter_table(
        "stocktake_postings", recreate="always" if dialect == "sqlite" else "auto"
    ) as batch:
        batch.add_column(
            sa.Column("effective_approval_completion_id", sa.Uuid(), nullable=True)
        )
        batch.drop_constraint("ck_stocktake_postings_kind", type_="check")
        batch.create_check_constraint(
            "ck_stocktake_postings_kind",
            f"posting_kind IN {ALL_POSTING_KINDS_SQL}",
        )
        batch.create_check_constraint(
            "ck_stocktake_postings_effective_approval_0035",
            "(posting_kind IN ('difference_gain', 'difference_loss', "
            "'difference_transfer', 'difference_status_change') AND "
            "effective_approval_completion_id IS NOT NULL) OR "
            "(posting_kind IN ('opening', 'difference_adjustment') AND "
            "effective_approval_completion_id IS NULL)",
        )
        batch.create_foreign_key(
            "fk_stocktake_postings_effective_approval_0035",
            EFFECTIVE_COMPLETION,
            ["effective_approval_completion_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    for sql in triggers:
        op.execute(sql)


def _restore_stocktake_postings(dialect: str) -> None:
    triggers = _capture_and_drop_sqlite_posting_dependency_triggers()
    with op.batch_alter_table(
        "stocktake_postings", recreate="always" if dialect == "sqlite" else "auto"
    ) as batch:
        batch.drop_constraint(
            "fk_stocktake_postings_effective_approval_0035", type_="foreignkey"
        )
        batch.drop_constraint(
            "ck_stocktake_postings_effective_approval_0035", type_="check"
        )
        batch.drop_constraint("ck_stocktake_postings_kind", type_="check")
        batch.create_check_constraint(
            "ck_stocktake_postings_kind",
            "posting_kind IN ('opening', 'difference_adjustment')",
        )
        batch.drop_column("effective_approval_completion_id")
    for sql in triggers:
        op.execute(sql)


def _create_posting_completion_tables() -> None:
    op.create_table(
        POSTING_COMPLETION,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("effective_approval_completion_id", sa.Uuid(), nullable=False),
        sa.Column("terminal_round_id", sa.Uuid(), nullable=False),
        sa.Column("expected_task_version", sa.BigInteger(), nullable=False),
        sa.Column("posted_task_version", sa.BigInteger(), nullable=False),
        sa.Column("scope_count", sa.Integer(), nullable=False),
        sa.Column("difference_count", sa.Integer(), nullable=False),
        sa.Column("accepted_difference_count", sa.Integer(), nullable=False),
        sa.Column("no_adjustment_count", sa.Integer(), nullable=False),
        sa.Column("transaction_count", sa.Integer(), nullable=False),
        sa.Column("movement_count", sa.Integer(), nullable=False),
        sa.Column("total_quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("first_ledger_cursor", sa.BigInteger(), nullable=True),
        sa.Column("last_ledger_cursor", sa.BigInteger(), nullable=True),
        sa.Column("approval_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("posting_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("posted_by_user_id", sa.String(36), nullable=False),
        sa.Column("posted_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("posted_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(80), nullable=False),
        sa.Column("authorization_sha256", sa.String(64), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_posting_completions_0035"),
        sa.UniqueConstraint("task_id", name="uq_stocktake_posting_completions_task_0035"),
        sa.UniqueConstraint("effective_approval_completion_id", name="uq_stocktake_posting_completions_approval_0035"),
        sa.UniqueConstraint("id", "task_id", name="uq_stocktake_posting_completions_id_task_0035"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_stocktake_posting_completions_idempotency_0035"),
        sa.ForeignKeyConstraint(
            ["effective_approval_completion_id", "task_id"],
            [f"{EFFECTIVE_COMPLETION}.id", f"{EFFECTIVE_COMPLETION}.task_id"],
            name="fk_stocktake_posting_completions_approval_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["terminal_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_posting_completions_round_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["posted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["posted_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["posted_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "scope_count > 0 AND difference_count >= 0 AND accepted_difference_count >= 0 "
            "AND no_adjustment_count >= 0 AND accepted_difference_count + no_adjustment_count = difference_count "
            "AND transaction_count >= 0 AND movement_count = accepted_difference_count AND total_quantity >= 0",
            name="ck_stocktake_posting_completions_counts_0035",
        ),
        sa.CheckConstraint(
            "expected_task_version >= 0 AND posted_task_version = expected_task_version + 1",
            name="ck_stocktake_posting_completions_versions_0035",
        ),
        sa.CheckConstraint(
            "(transaction_count = 0 AND first_ledger_cursor IS NULL AND last_ledger_cursor IS NULL "
            "AND movement_count = 0 AND total_quantity = 0) OR "
            "(transaction_count > 0 AND first_ledger_cursor > 0 AND last_ledger_cursor >= first_ledger_cursor "
            "AND last_ledger_cursor - first_ledger_cursor + 1 = transaction_count "
            "AND movement_count > 0 AND total_quantity > 0)",
            name="ck_stocktake_posting_completions_cursors_0035",
        ),
        sa.CheckConstraint(
            "authorization_version > 0 AND role_code = 'admin' AND scope_type = 'national' AND scope_id_snapshot = '*'",
            name="ck_stocktake_posting_completions_authorization_0035",
        ),
        sa.CheckConstraint(
            "length(approval_manifest_sha256) = 64 AND length(posting_manifest_sha256) = 64 "
            "AND length(request_sha256) = 64 AND length(idempotency_key_hash) = 64 "
            "AND length(authorization_sha256) = 64",
            name="ck_stocktake_posting_completions_hashes_0035",
        ),
        sa.CheckConstraint("created_at = posted_at", name="ck_stocktake_posting_completions_chronology_0035"),
    )
    op.create_index(
        "ix_stocktake_posting_completions_actor_0035",
        POSTING_COMPLETION,
        ["posted_by_user_id", "posted_at"],
    )
    op.create_table(
        POSTING_COMPLETION_ITEM,
        sa.Column("completion_id", sa.Uuid(), nullable=False),
        sa.Column("difference_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("source_round_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("posting_kind", sa.String(32), nullable=True),
        sa.Column("posting_id", sa.Uuid(), nullable=True),
        sa.Column("inventory_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("inventory_movement_id", sa.Uuid(), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("item_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("completion_id", "difference_id", name="pk_stocktake_posting_completion_items_0035"),
        sa.UniqueConstraint("difference_id", name="uq_stocktake_posting_completion_items_difference_0035"),
        sa.UniqueConstraint("inventory_movement_id", name="uq_stocktake_posting_completion_items_movement_0035"),
        sa.ForeignKeyConstraint(
            ["completion_id", "task_id"],
            [f"{POSTING_COMPLETION}.id", f"{POSTING_COMPLETION}.task_id"],
            name="fk_stocktake_posting_completion_items_completion_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["difference_id", "task_id", "source_round_id"],
            ["stocktake_differences.id", "stocktake_differences.task_id", "stocktake_differences.round_id"],
            name="fk_stocktake_posting_completion_items_difference_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["posting_id", "task_id", "source_round_id"],
            ["stocktake_postings.id", "stocktake_postings.task_id", "stocktake_postings.round_id"],
            name="fk_stocktake_posting_completion_items_posting_0035",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["inventory_transaction_id"], ["inventory_transactions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["inventory_movement_id"], ["inventory_movements.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("decision IN ('accept_for_posting', 'no_adjustment')", name="ck_stocktake_posting_completion_items_decision_0035"),
        sa.CheckConstraint(
            "(decision = 'accept_for_posting' AND posting_id IS NOT NULL AND inventory_transaction_id IS NOT NULL "
            "AND inventory_movement_id IS NOT NULL AND posting_kind IN ('difference_gain', 'difference_loss', "
            "'difference_transfer', 'difference_status_change') AND quantity > 0) OR "
            "(decision = 'no_adjustment' AND posting_id IS NULL AND inventory_transaction_id IS NULL "
            "AND inventory_movement_id IS NULL AND posting_kind IS NULL AND quantity = 0)",
            name="ck_stocktake_posting_completion_items_binding_0035",
        ),
        sa.CheckConstraint("length(item_manifest_sha256) = 64", name="ck_stocktake_posting_completion_items_hash_0035"),
    )
    op.create_index(
        "ix_stocktake_posting_completion_items_scope_0035",
        POSTING_COMPLETION_ITEM,
        ["completion_id", "scope_id"],
    )


def _seed_permission() -> None:
    seeded_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    permission_table = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("resource", sa.String(100)),
        sa.column("action", sa.String(80)),
        sa.column("field_code", sa.String(100)),
        sa.column("description", sa.String(300)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String(12)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        permission_table,
        [{
            "id": PERMISSION_ID,
            "resource": "stocktake",
            "action": "post_difference",
            "field_code": "",
            "description": "Post a task-wide approved non-opening stocktake difference batch",
            "created_at": seeded_at,
            "updated_at": seeded_at,
        }],
    )
    op.bulk_insert(
        role_permission_table,
        [{
            "id": ROLE_PERMISSION_ID,
            "role_id": ADMIN_ROLE_ID,
            "permission_id": PERMISSION_ID,
            "effect": "allow",
            "created_at": seeded_at,
        }],
    )


def _delete_permission() -> None:
    role_permission_table = sa.table(
        "role_permissions", sa.column("id", sa.Uuid())
    )
    permission_table = sa.table("permissions", sa.column("id", sa.Uuid()))
    op.execute(
        role_permission_table.delete().where(
            role_permission_table.c.id == ROLE_PERMISSION_ID
        )
    )
    op.execute(
        permission_table.delete().where(permission_table.c.id == PERMISSION_ID)
    )


def _create_sqlite_immutability_triggers() -> None:
    for table_name in NEW_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable_{operation.lower()}_0035 "
                f"BEFORE {operation} ON {table_name} BEGIN SELECT RAISE(ABORT, "
                "'stocktake 0035 completion facts are immutable'); END"
            )


def _latest_effective_round_sql(*, task: str, scope: str, source_round: str) -> str:
    return f"""{source_round}.round_no = COALESCE((
        SELECT max(candidate.round_no)
          FROM stocktake_rounds AS candidate
         WHERE candidate.task_id = {task}.id
           AND candidate.status = 'submitted'
           AND (
                candidate.round_no = 1
                OR EXISTS (
                    SELECT 1 FROM stocktake_recount_scope_assignments AS assignment
                     WHERE assignment.task_id = {task}.id
                       AND assignment.recount_case_id = candidate.recount_case_id
                       AND assignment.scope_id = {scope}.id)
           )
    ), 0)"""


def _sqlite_approval_task_trigger_sql() -> str:
    latest = _latest_effective_round_sql(
        task="NEW", scope="scope", source_round="source_round"
    )
    return f"""
CREATE TRIGGER {SQLITE_APPROVAL_TRIGGER}
BEFORE UPDATE ON stocktake_tasks
WHEN NEW.task_type IN {NONOPENING_SQL}
 AND OLD.status = 'hq_review' AND NEW.status = 'approved'
 AND NOT EXISTS (
    SELECT 1
      FROM {EFFECTIVE_COMPLETION} AS completion
      JOIN stocktake_reviews AS hq_review
        ON hq_review.id = completion.terminal_headquarters_review_id
       AND hq_review.task_id = NEW.id
       AND hq_review.round_id = completion.terminal_round_id
     WHERE completion.task_id = NEW.id
       AND completion.terminal_round_id = (
           SELECT id FROM stocktake_rounds
            WHERE task_id = NEW.id AND round_no = NEW.current_round_no)
       AND hq_review.review_stage = 'headquarters'
       AND hq_review.decision = 'approve'
       AND completion.expected_task_version = OLD.version
       AND completion.approved_task_version = NEW.version
       AND completion.completed_at = hq_review.reviewed_at
       AND completion.completed_by_user_id = hq_review.reviewer_user_id
       AND completion.completed_by_person_id = hq_review.reviewer_person_id
       AND completion.completed_role_assignment_id = hq_review.reviewer_role_assignment_id
       AND completion.authorization_version = hq_review.authorization_version
       AND completion.scope_count = (SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.id)
       AND completion.scope_count = (SELECT count(*) FROM {EFFECTIVE_SCOPE} WHERE completion_id = completion.id)
       AND completion.difference_count = (SELECT count(*) FROM {EFFECTIVE_ITEM} WHERE completion_id = completion.id)
       AND completion.accepted_difference_count = (
           SELECT count(*) FROM {EFFECTIVE_ITEM}
            WHERE completion_id = completion.id AND headquarters_decision = 'accept_for_posting')
       AND completion.no_adjustment_count = (
           SELECT count(*) FROM {EFFECTIVE_ITEM}
            WHERE completion_id = completion.id AND headquarters_decision = 'no_adjustment')
       AND NOT EXISTS (
           SELECT 1 FROM stocktake_scopes AS scope
            LEFT JOIN {EFFECTIVE_SCOPE} AS selected
              ON selected.completion_id = completion.id AND selected.scope_id = scope.id
            LEFT JOIN stocktake_rounds AS source_round
              ON source_round.id = selected.source_round_id AND source_round.task_id = NEW.id
            LEFT JOIN stocktake_difference_set_completions AS difference_completion
              ON difference_completion.id = selected.source_difference_completion_id
             AND difference_completion.task_id = NEW.id
             AND difference_completion.round_id = source_round.id
            LEFT JOIN stocktake_reviews AS region_review
              ON region_review.id = selected.regional_review_id
             AND region_review.task_id = NEW.id
             AND region_review.round_id = source_round.id
           WHERE scope.task_id = NEW.id
             AND (selected.scope_id IS NULL OR source_round.id IS NULL
                  OR difference_completion.id IS NULL
                  OR region_review.review_stage <> 'region'
                  OR NOT ({latest})
                  OR selected.difference_count <> (
                       SELECT count(*) FROM stocktake_differences AS difference
                        WHERE difference.task_id = NEW.id
                          AND difference.round_id = source_round.id
                          AND difference.scope_id = scope.id)
                  OR selected.difference_count <> (
                       SELECT count(*) FROM {EFFECTIVE_ITEM} AS item
                        WHERE item.completion_id = completion.id
                          AND item.scope_id = scope.id))
       )
       AND NOT EXISTS (
           SELECT 1
             FROM {EFFECTIVE_ITEM} AS item
             JOIN {EFFECTIVE_SCOPE} AS selected
               ON selected.completion_id = item.completion_id
              AND selected.scope_id = item.scope_id
             JOIN stocktake_review_items AS region_item
               ON region_item.review_id = item.regional_review_id
              AND region_item.difference_id = item.difference_id
            WHERE item.completion_id = completion.id
              AND (item.source_round_id <> selected.source_round_id
                   OR item.task_id <> NEW.id
                   OR item.regional_decision <> region_item.decision
                   OR item.regional_decision NOT IN ('accept_for_posting', 'no_adjustment')
                   OR item.headquarters_decision <> item.regional_decision)
       )
 )
BEGIN
    SELECT RAISE(ABORT, 'non-opening effective approval completion is invalid');
END
"""


def _sqlite_posting_task_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_POSTING_TRIGGER}
BEFORE UPDATE ON stocktake_tasks
WHEN NEW.task_type IN {NONOPENING_SQL}
 AND OLD.status = 'approved' AND NEW.status = 'posted'
 AND NOT EXISTS (
    SELECT 1
      FROM {POSTING_COMPLETION} AS completion
      JOIN {EFFECTIVE_COMPLETION} AS approval
        ON approval.id = completion.effective_approval_completion_id
       AND approval.task_id = NEW.id
     WHERE completion.task_id = NEW.id
       AND completion.terminal_round_id = approval.terminal_round_id
       AND completion.expected_task_version = OLD.version
       AND completion.posted_task_version = NEW.version
       AND completion.posted_at = NEW.posted_at
       AND completion.posted_by_user_id IS NOT NULL
       AND completion.scope_count = approval.scope_count
       AND completion.difference_count = approval.difference_count
       AND completion.accepted_difference_count = approval.accepted_difference_count
       AND completion.no_adjustment_count = approval.no_adjustment_count
       AND completion.approval_manifest_sha256 = approval.approval_manifest_sha256
       AND completion.difference_count = (
           SELECT count(*) FROM {POSTING_COMPLETION_ITEM} AS item
            WHERE item.completion_id = completion.id)
       AND completion.movement_count = (
           SELECT count(*) FROM {POSTING_COMPLETION_ITEM} AS item
            WHERE item.completion_id = completion.id
              AND item.decision = 'accept_for_posting')
       AND completion.no_adjustment_count = (
           SELECT count(*) FROM {POSTING_COMPLETION_ITEM} AS item
            WHERE item.completion_id = completion.id
              AND item.decision = 'no_adjustment')
       AND completion.transaction_count = (
           SELECT count(DISTINCT item.inventory_transaction_id)
             FROM {POSTING_COMPLETION_ITEM} AS item
            WHERE item.completion_id = completion.id
              AND item.inventory_transaction_id IS NOT NULL)
       AND completion.total_quantity = (
           SELECT COALESCE(sum(item.quantity), 0)
             FROM {POSTING_COMPLETION_ITEM} AS item
            WHERE item.completion_id = completion.id)
       AND NOT EXISTS (
           SELECT 1 FROM {EFFECTIVE_ITEM} AS approved_item
            LEFT JOIN {POSTING_COMPLETION_ITEM} AS posted_item
              ON posted_item.completion_id = completion.id
             AND posted_item.difference_id = approved_item.difference_id
             AND posted_item.task_id = NEW.id
             AND posted_item.scope_id = approved_item.scope_id
             AND posted_item.source_round_id = approved_item.source_round_id
             AND posted_item.decision = approved_item.headquarters_decision
           WHERE approved_item.completion_id = approval.id
             AND posted_item.difference_id IS NULL)
       AND NOT EXISTS (
           SELECT 1 FROM {POSTING_COMPLETION_ITEM} AS item
            LEFT JOIN stocktake_postings AS posting
              ON posting.id = item.posting_id
             AND posting.task_id = NEW.id
             AND posting.round_id = item.source_round_id
             AND posting.posting_kind = item.posting_kind
             AND posting.inventory_transaction_id = item.inventory_transaction_id
             AND posting.effective_approval_completion_id = approval.id
            LEFT JOIN stocktake_posting_items AS posting_item
              ON posting_item.posting_id = item.posting_id
             AND posting_item.inventory_movement_id = item.inventory_movement_id
             AND posting_item.difference_id = item.difference_id
             AND posting_item.quantity = item.quantity
            LEFT JOIN inventory_movements AS movement
              ON movement.id = item.inventory_movement_id
             AND movement.transaction_id = item.inventory_transaction_id
             AND movement.quantity = item.quantity
           WHERE item.completion_id = completion.id
             AND item.decision = 'accept_for_posting'
             AND (posting.id IS NULL OR posting_item.difference_id IS NULL OR movement.id IS NULL))
       AND NOT EXISTS (
           SELECT 1 FROM stocktake_scopes AS scope
            LEFT JOIN inventory_freezes AS freeze_row
              ON freeze_row.task_id = NEW.id
             AND freeze_row.stocktake_scope_id = scope.id
             AND freeze_row.scope_key = scope.scope_key
             AND freeze_row.status = 'released'
             AND freeze_row.valid_to = completion.posted_at
             AND freeze_row.released_by_user_id = completion.posted_by_user_id
             AND freeze_row.release_reason = '{FREEZE_RELEASE_REASON}'
           WHERE scope.task_id = NEW.id AND freeze_row.id IS NULL)
       AND EXISTS (
           SELECT 1 FROM state_transition_events AS event
            WHERE event.aggregate_type = 'stocktake_task'
              AND replace(event.aggregate_id, '-', '') =
                  replace(CAST(NEW.id AS TEXT), '-', '')
              AND event.from_status = 'approved'
              AND event.to_status = 'posted'
              AND event.reason = 'nonopening_stocktake_difference_posted'
              AND event.actor_id = completion.posted_by_user_id
              AND event.occurred_at = completion.posted_at)
       AND EXISTS (
           SELECT 1 FROM audit_events AS event
            WHERE event.stream_key = 'inventory'
              AND event.aggregate_type = 'stocktake_posting_completion'
              AND replace(event.aggregate_id, '-', '') =
                  replace(CAST(completion.id AS TEXT), '-', '')
              AND event.action = 'stocktake.nonopening.difference_posted'
              AND event.actor_user_id = completion.posted_by_user_id
              AND event.occurred_at = completion.posted_at)
 )
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake posting completion is invalid');
END
"""


def _sqlite_posting_item_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_POSTING_ITEM_TRIGGER}
BEFORE INSERT ON stocktake_posting_items
WHEN NEW.difference_id IS NOT NULL
 AND EXISTS (
    SELECT 1 FROM stocktake_postings AS guarded_posting
     WHERE guarded_posting.id = NEW.posting_id
       AND guarded_posting.posting_kind IN {NEW_POSTING_KINDS_SQL}
 )
 AND NOT EXISTS (
    SELECT 1
      FROM stocktake_postings AS posting
      JOIN inventory_transactions AS transaction_row
        ON transaction_row.id = posting.inventory_transaction_id
      JOIN inventory_movements AS movement
        ON movement.id = NEW.inventory_movement_id
       AND movement.transaction_id = transaction_row.id
      JOIN stocktake_differences AS difference
        ON difference.id = NEW.difference_id
       AND difference.task_id = NEW.task_id
       AND difference.round_id = NEW.round_id
     WHERE posting.id = NEW.posting_id
       AND posting.task_id = NEW.task_id
       AND posting.round_id = NEW.round_id
       AND posting.posting_kind IN {NEW_POSTING_KINDS_SQL}
       AND posting.effective_approval_completion_id IS NOT NULL
       AND movement.quantity = NEW.quantity
       AND transaction_row.status = 'posted'
       AND transaction_row.source_document_type = 'stocktake_difference'
       AND replace(transaction_row.source_document_id, '-', '') =
           replace(CAST(NEW.task_id AS TEXT), '-', '')
       AND ((posting.posting_kind = 'difference_gain'
             AND transaction_row.movement_type = 'stocktake_gain'
             AND movement.from_account_id IS NULL AND movement.to_account_id IS NOT NULL)
            OR (posting.posting_kind = 'difference_loss'
                AND transaction_row.movement_type = 'stocktake_loss'
                AND movement.from_account_id IS NOT NULL AND movement.to_account_id IS NULL)
            OR (posting.posting_kind = 'difference_transfer'
                AND transaction_row.movement_type = 'transfer'
                AND movement.from_account_id IS NOT NULL AND movement.to_account_id IS NOT NULL)
            OR (posting.posting_kind = 'difference_status_change'
                AND transaction_row.movement_type = 'status_change'
                AND movement.from_account_id IS NOT NULL AND movement.to_account_id IS NOT NULL))
 )
BEGIN
    SELECT RAISE(ABORT, 'stocktake difference posting item is invalid');
END
"""


def _postgresql_lock_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_LOCK_FUNCTION}(requested_task_id uuid)
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
    IF requested_task_id IS NULL THEN
        RAISE EXCEPTION 'non-opening posting lock graph invariant violated';
    END IF;
    PERFORM task.id FROM public.stocktake_tasks AS task
     WHERE task.id = requested_task_id
       AND task.task_type IN {NONOPENING_SQL}
     ORDER BY task.id FOR UPDATE OF task;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'non-opening posting lock graph invariant violated';
    END IF;
    SELECT
        (SELECT count(*) FROM public.stocktake_scopes WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.inventory_freezes WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_snapshot_lines WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_rounds WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_count_lines WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_count_serials AS serial
           JOIN public.stocktake_count_lines AS line ON line.id = serial.count_line_id
          WHERE line.task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_count_observations WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_scope_count_completions WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_round_submissions WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_difference_set_completions WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_differences WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_reviews WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_review_items WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_recount_cases WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_recount_scope_assignments WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.{EFFECTIVE_COMPLETION} WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.{EFFECTIVE_SCOPE} WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.{EFFECTIVE_ITEM} WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_postings WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_posting_items WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.{POSTING_COMPLETION} WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.{POSTING_COMPLETION_ITEM} WHERE task_id = requested_task_id)
      INTO graph_count;
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION 'non-opening posting lock graph invariant violated';
    END IF;
    PERFORM scope.id FROM public.stocktake_scopes AS scope
     WHERE scope.task_id = requested_task_id ORDER BY scope.scope_no, scope.id FOR UPDATE OF scope;
    PERFORM freeze_row.id FROM public.inventory_freezes AS freeze_row
     WHERE freeze_row.task_id = requested_task_id
     ORDER BY freeze_row.stocktake_scope_id, freeze_row.id
     FOR UPDATE OF freeze_row;
    PERFORM snapshot.id FROM public.stocktake_snapshot_lines AS snapshot
     WHERE snapshot.task_id = requested_task_id ORDER BY snapshot.scope_id, snapshot.stock_account_id, snapshot.id FOR UPDATE OF snapshot;
    PERFORM round_row.id FROM public.stocktake_rounds AS round_row
     WHERE round_row.task_id = requested_task_id ORDER BY round_row.round_no, round_row.id FOR UPDATE OF round_row;
    PERFORM line.id FROM public.stocktake_count_lines AS line
     WHERE line.task_id = requested_task_id ORDER BY line.round_id, line.scope_id, line.stock_account_id, line.id FOR UPDATE OF line;
    PERFORM serial.count_line_id FROM public.stocktake_count_serials AS serial
     JOIN public.stocktake_count_lines AS line ON line.id = serial.count_line_id
     WHERE line.task_id = requested_task_id ORDER BY serial.round_id, serial.count_line_id, serial.serial_id FOR UPDATE OF serial;
    PERFORM observation.id FROM public.stocktake_count_observations AS observation
     WHERE observation.task_id = requested_task_id ORDER BY observation.round_id, observation.scope_id, observation.observation_no, observation.id FOR UPDATE OF observation;
    PERFORM completion.id FROM public.stocktake_scope_count_completions AS completion
     WHERE completion.task_id = requested_task_id ORDER BY completion.round_id, completion.scope_id, completion.id FOR UPDATE OF completion;
    PERFORM submission.id FROM public.stocktake_round_submissions AS submission
     WHERE submission.task_id = requested_task_id ORDER BY submission.round_id, submission.id FOR UPDATE OF submission;
    PERFORM completion.id FROM public.stocktake_difference_set_completions AS completion
     WHERE completion.task_id = requested_task_id ORDER BY completion.round_id, completion.id FOR UPDATE OF completion;
    PERFORM difference.id FROM public.stocktake_differences AS difference
     WHERE difference.task_id = requested_task_id ORDER BY difference.round_id, difference.difference_no, difference.id FOR UPDATE OF difference;
    PERFORM review.id FROM public.stocktake_reviews AS review
     WHERE review.task_id = requested_task_id ORDER BY review.round_id, review.review_stage, review.id FOR UPDATE OF review;
    PERFORM item.review_id FROM public.stocktake_review_items AS item
     WHERE item.task_id = requested_task_id ORDER BY item.round_id, item.review_id, item.difference_id FOR UPDATE OF item;
    PERFORM recount.id FROM public.stocktake_recount_cases AS recount
     WHERE recount.task_id = requested_task_id ORDER BY recount.next_round_no, recount.id FOR UPDATE OF recount;
    PERFORM assignment.id FROM public.stocktake_recount_scope_assignments AS assignment
     WHERE assignment.task_id = requested_task_id ORDER BY assignment.recount_case_id, assignment.scope_id, assignment.id FOR UPDATE OF assignment;
    PERFORM completion.id FROM public.{EFFECTIVE_COMPLETION} AS completion
     WHERE completion.task_id = requested_task_id ORDER BY completion.id FOR UPDATE OF completion;
    PERFORM selected.completion_id FROM public.{EFFECTIVE_SCOPE} AS selected
     WHERE selected.task_id = requested_task_id ORDER BY selected.scope_id FOR UPDATE OF selected;
    PERFORM item.completion_id FROM public.{EFFECTIVE_ITEM} AS item
     WHERE item.task_id = requested_task_id ORDER BY item.scope_id, item.difference_id FOR UPDATE OF item;
    PERFORM posting.id FROM public.stocktake_postings AS posting
     WHERE posting.task_id = requested_task_id ORDER BY posting.round_id, posting.posting_kind, posting.id FOR UPDATE OF posting;
    PERFORM item.posting_id FROM public.stocktake_posting_items AS item
     WHERE item.task_id = requested_task_id ORDER BY item.round_id, item.posting_id, item.inventory_movement_id FOR UPDATE OF item;
    PERFORM completion.id FROM public.{POSTING_COMPLETION} AS completion
     WHERE completion.task_id = requested_task_id ORDER BY completion.id FOR UPDATE OF completion;
    PERFORM item.completion_id FROM public.{POSTING_COMPLETION_ITEM} AS item
     WHERE item.task_id = requested_task_id ORDER BY item.scope_id, item.difference_id FOR UPDATE OF item;
END
$$
"""


def _postgresql_graph_function_sql() -> str:
    # PostgreSQL defers this proof to commit.  The service performs the same
    # proof before writing; the constraint function prevents partial facts from
    # being committed by an alternate runtime path.
    return f"""
CREATE FUNCTION public.{PG_GRAPH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    target_task_id uuid;
    task_row public.stocktake_tasks%ROWTYPE;
    approval_count bigint;
    posting_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'stocktake_tasks' THEN
        target_task_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSE
        target_task_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.task_id ELSE NEW.task_id END;
    END IF;
    SELECT * INTO task_row FROM public.stocktake_tasks WHERE id = target_task_id;
    IF NOT FOUND OR task_row.task_type NOT IN {NONOPENING_SQL} THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    SELECT count(*) INTO approval_count
      FROM public.{EFFECTIVE_COMPLETION} AS completion
     WHERE completion.task_id = target_task_id
       AND completion.scope_count = (
           SELECT count(*) FROM public.{EFFECTIVE_SCOPE} AS selected
            WHERE selected.completion_id = completion.id)
       AND completion.difference_count = (
           SELECT count(*) FROM public.{EFFECTIVE_ITEM} AS item
            WHERE item.completion_id = completion.id)
       AND completion.accepted_difference_count + completion.no_adjustment_count = completion.difference_count
       AND NOT EXISTS (
           SELECT 1 FROM public.stocktake_scopes AS scope
            WHERE scope.task_id = target_task_id
              AND NOT EXISTS (
                  SELECT 1 FROM public.{EFFECTIVE_SCOPE} AS selected
                   WHERE selected.completion_id = completion.id
                     AND selected.scope_id = scope.id))
       AND EXISTS (
           SELECT 1 FROM public.stocktake_reviews AS review
            WHERE review.id = completion.terminal_headquarters_review_id
              AND review.task_id = target_task_id
              AND review.round_id = completion.terminal_round_id
              AND review.review_stage = 'headquarters'
              AND review.decision = 'approve');
    IF task_row.status IN ('approved', 'posted', 'closed') AND approval_count <> 1 THEN
        RAISE EXCEPTION 'non-opening effective approval completion is invalid';
    END IF;
    IF task_row.status NOT IN ('approved', 'posted', 'closed') AND approval_count <> 0 THEN
        RAISE EXCEPTION 'non-opening effective approval cannot precede approved task';
    END IF;
    SELECT count(*) INTO posting_count
      FROM public.{POSTING_COMPLETION} AS completion
     WHERE completion.task_id = target_task_id
       AND completion.difference_count = (
           SELECT count(*) FROM public.{POSTING_COMPLETION_ITEM} AS item
            WHERE item.completion_id = completion.id)
       AND completion.movement_count = completion.accepted_difference_count
       AND completion.no_adjustment_count = (
           SELECT count(*) FROM public.{POSTING_COMPLETION_ITEM} AS item
            WHERE item.completion_id = completion.id
              AND item.decision = 'no_adjustment')
       AND NOT EXISTS (
           SELECT 1 FROM public.{EFFECTIVE_ITEM} AS approved_item
            WHERE approved_item.completion_id = completion.effective_approval_completion_id
              AND NOT EXISTS (
                  SELECT 1 FROM public.{POSTING_COMPLETION_ITEM} AS posted_item
                   WHERE posted_item.completion_id = completion.id
                     AND posted_item.difference_id = approved_item.difference_id
                     AND posted_item.decision = approved_item.headquarters_decision));
    IF task_row.status IN ('posted', 'closed') AND posting_count <> 1 THEN
        RAISE EXCEPTION 'non-opening stocktake posting completion is invalid';
    END IF;
    IF task_row.status NOT IN ('posted', 'closed') AND posting_count <> 0 THEN
        RAISE EXCEPTION 'non-opening posting completion cannot precede posted task';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$
"""


def _apply_postgresql_acl() -> None:
    new_table_list = ", ".join(f"public.{name}" for name in NEW_TABLES)
    op.execute(f"REVOKE ALL ON TABLE {new_table_list} FROM PUBLIC")
    op.execute(
        f"REVOKE ALL ON TABLE {new_table_list} FROM {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE {new_table_list} TO {PRODUCTION_API_ROLE}"
    )
    lock_signature = f"public.{PG_LOCK_FUNCTION}(uuid)"
    guard_signature = f"public.{PG_GRAPH_FUNCTION}()"
    op.execute(f"REVOKE EXECUTE ON FUNCTION {lock_signature} FROM PUBLIC")
    op.execute(f"REVOKE EXECUTE ON FUNCTION {guard_signature} FROM PUBLIC")
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{MIGRATION_ROLE}') THEN
        EXECUTE 'ALTER FUNCTION {lock_signature} OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION {guard_signature} OWNER TO {MIGRATION_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION {lock_signature} TO {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {guard_signature} FROM {PRODUCTION_API_ROLE}';
    END IF;
END
$$
"""
    )


def _revoke_postgresql_acl() -> None:
    new_table_list = ", ".join(f"public.{name}" for name in NEW_TABLES)
    op.execute(
        f"REVOKE ALL ON TABLE {new_table_list} FROM {PRODUCTION_API_ROLE}"
    )
