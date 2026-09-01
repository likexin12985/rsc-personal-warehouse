"""Add repeatable non-opening stocktake reconciliation and independent close.

Revision ID: 20260901_0038
Revises: 20260901_0037
Create Date: 2026-09-01

This revision is intentionally local-only.  It seals book/physical/ledger/SN
evidence but creates neither notification nor external OAM synchronization
facts.  A reconciliation may be repeated after legitimate later inventory
postings; every proof remains immutable and close binds only the current tail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260901_0038"
down_revision: Union[str, Sequence[str], None] = "20260901_0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
RECONCILIATION = "stocktake_close_reconciliation_completions"
RECONCILIATION_ACCOUNT = "stocktake_close_reconciliation_accounts"
RECONCILIATION_SERIAL = "stocktake_close_reconciliation_serials"
CLOSE_COMPLETION = "stocktake_close_completions"
TRANSITION_ACK = "stocktake_close_transition_acks"
COMMAND_FACT_TABLES = (
    RECONCILIATION,
    RECONCILIATION_ACCOUNT,
    RECONCILIATION_SERIAL,
    CLOSE_COMPLETION,
)
NEW_TABLES = (TRANSITION_ACK, *COMMAND_FACT_TABLES)

RECONCILE_PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000057")
CLOSE_PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000058")
RECONCILE_ROLE_PERMISSION_ID = uuid.UUID("21000000-0000-4000-8000-000000000101")
CLOSE_ROLE_PERMISSION_ID = uuid.UUID("21000000-0000-4000-8000-000000000102")
ADMIN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")

PG_LOCK_FUNCTION = "rsc_lock_nonopening_stocktake_close_graph_0038"
PG_GRAPH_FUNCTION = "rsc_require_nonopening_stocktake_close_graph_0038"
PG_IMMUTABLE_FUNCTION = "rsc_reject_nonopening_stocktake_close_mutation_0038"
PG_ACK_FUNCTION = "rsc_record_nonopening_stocktake_close_ack_0038"
PG_ACK_GUARD_FUNCTION = "rsc_guard_nonopening_stocktake_close_ack_0038"
PG_EVENT_GUARD_FUNCTION = "rsc_guard_nonopening_stocktake_close_event_0038"
PG_GRAPH_TRIGGERS = {
    "stocktake_tasks": "trg_stocktake_tasks_close_graph_0038",
    RECONCILIATION: "trg_stocktake_close_reconciliations_graph_0038",
    RECONCILIATION_ACCOUNT: "trg_stocktake_close_reconciliation_accounts_graph_0038",
    RECONCILIATION_SERIAL: "trg_stocktake_close_reconciliation_serials_graph_0038",
    CLOSE_COMPLETION: "trg_stocktake_close_completions_graph_0038",
    TRANSITION_ACK: "trg_stocktake_close_transition_acks_graph_0038",
}
PG_ACK_TRIGGER = "trg_stocktake_tasks_close_ack_0038"
PG_ACK_GUARD_TRIGGER = "trg_stocktake_close_transition_acks_insert_0038"
PG_AUDIT_EVENT_GUARD_TRIGGER = (
    "trg_audit_events_nonopening_stocktake_close_guard_0038"
)
PG_STATE_EVENT_GUARD_TRIGGER = (
    "trg_state_events_nonopening_stocktake_close_guard_0038"
)
SQLITE_RECONCILIATION_INSERT = "trg_stocktake_close_reconciliation_insert_0038"
SQLITE_ACCOUNT_INSERT = "trg_stocktake_close_reconciliation_account_insert_0038"
SQLITE_SERIAL_INSERT = "trg_stocktake_close_reconciliation_serial_insert_0038"
SQLITE_CLOSE_INSERT = "trg_stocktake_close_completion_insert_0038"
SQLITE_TASK_TERMINAL = "trg_stocktake_close_task_terminal_update_0038"
SQLITE_ACK_INSERT = "trg_stocktake_close_transition_ack_insert_0038"
SQLITE_TASK_ACK = "trg_stocktake_close_task_ack_0038"
SQLITE_AUDIT_EVENT_GUARD = (
    "trg_audit_events_nonopening_stocktake_close_guard_0038"
)
SQLITE_STATE_EVENT_GUARD = (
    "trg_state_events_nonopening_stocktake_close_guard_0038"
)

UPGRADE_BLOCKER = (
    "0038 preflight failed: legacy closed non-opening stocktakes or reserved "
    "permission identities require manual quarantine"
)
DOWNGRADE_BLOCKER = "cannot downgrade 0038 while reconciliation or close facts exist"


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0038 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0038 SQLite upgrade requires an online connection")
        _postgresql_lock_and_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_upgrade_graph()
        _online_preflight(dialect)

    _create_tables()
    _seed_permissions()
    if dialect == "postgresql":
        op.execute(_postgresql_lock_function_sql())
        op.execute(_postgresql_immutable_function_sql())
        _create_postgresql_immutable_triggers()
        op.execute(_postgresql_ack_guard_function_sql())
        op.execute(_postgresql_ack_function_sql())
        _create_postgresql_ack_triggers()
        op.execute(_postgresql_event_guard_function_sql())
        _create_postgresql_event_guard_triggers()
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
    else:
        _create_sqlite_immutability_triggers()
        op.execute(_sqlite_ack_insert_sql())
        op.execute(_sqlite_reconciliation_insert_sql())
        op.execute(_sqlite_account_insert_sql())
        op.execute(_sqlite_serial_insert_sql())
        op.execute(_sqlite_close_insert_sql())
        op.execute(_sqlite_audit_event_guard_sql())
        op.execute(_sqlite_state_event_guard_sql())
        op.execute(_sqlite_task_terminal_sql())
        op.execute(_sqlite_task_ack_sql())


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0038 downgrade requires an online evidence check")
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_upgrade_graph()
    _require_safe_downgrade(dialect)
    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {PG_ACK_TRIGGER} ON public.stocktake_tasks"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {PG_ACK_GUARD_TRIGGER} "
            f"ON public.{TRANSITION_ACK}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {PG_AUDIT_EVENT_GUARD_TRIGGER} "
            "ON public.audit_events"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {PG_STATE_EVENT_GUARD_TRIGGER} "
            "ON public.state_transition_events"
        )
        for table_name, trigger_name in PG_GRAPH_TRIGGERS.items():
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
        for table_name in NEW_TABLES:
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_0038 "
                f"ON public.{table_name}"
            )
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_no_truncate_0038 "
                f"ON public.{table_name}"
            )
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_GRAPH_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_ACK_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_ACK_GUARD_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_EVENT_GUARD_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_IMMUTABLE_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_LOCK_FUNCTION}(uuid)")
        _revoke_postgresql_acl()
    else:
        for trigger_name in (
            SQLITE_RECONCILIATION_INSERT,
            SQLITE_ACCOUNT_INSERT,
            SQLITE_SERIAL_INSERT,
            SQLITE_CLOSE_INSERT,
            SQLITE_TASK_TERMINAL,
            SQLITE_ACK_INSERT,
            SQLITE_TASK_ACK,
            SQLITE_AUDIT_EVENT_GUARD,
            SQLITE_STATE_EVENT_GUARD,
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
        for table_name in NEW_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f"DROP TRIGGER IF EXISTS "
                    f"trg_{table_name}_immutable_{operation}_0038"
                )
    _delete_permissions()
    for table_name in reversed(NEW_TABLES):
        op.drop_table(table_name)


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_upgrade_graph() -> None:
    op.get_bind().exec_driver_sql(
        "LOCK TABLE public.stocktake_tasks, public.stocktake_posting_completions, "
        "public.inventory_ledger_heads, public.audit_events, "
        "public.state_transition_events, public.permissions, "
        "public.role_permissions "
        "IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_lock_and_preflight() -> None:
    op.execute(
        "LOCK TABLE public.stocktake_tasks, public.stocktake_posting_completions, "
        "public.inventory_ledger_heads, public.audit_events, "
        "public.state_transition_events, public.permissions, "
        "public.role_permissions "
        "IN ACCESS EXCLUSIVE MODE"
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
    permission_ids = (RECONCILE_PERMISSION_ID.hex, CLOSE_PERMISSION_ID.hex)
    role_permission_ids = (
        RECONCILE_ROLE_PERMISSION_ID.hex,
        CLOSE_ROLE_PERMISSION_ID.hex,
    )
    return f"""EXISTS (
        SELECT 1 FROM {prefix}stocktake_tasks
         WHERE task_type IN {NONOPENING_SQL} AND status = 'closed'
    ) OR EXISTS (
        SELECT 1
          FROM {prefix}stocktake_tasks AS task
         WHERE task.task_type IN {NONOPENING_SQL}
           AND task.status = 'posted'
           AND (
               task.closed_at IS NOT NULL
               OR task.posted_at IS NULL
               OR task.updated_at IS NULL
               OR (SELECT count(*)
                     FROM {prefix}stocktake_posting_completions AS posting
                    WHERE posting.task_id = task.id) <> 1
               OR NOT EXISTS (
                   SELECT 1
                     FROM {prefix}stocktake_posting_completions AS posting
                    WHERE posting.task_id = task.id
                      AND task.version = posting.posted_task_version
                      AND task.posted_at = posting.posted_at
                      AND task.updated_at = posting.posted_at)
           )
    ) OR EXISTS (
        SELECT 1 FROM {prefix}state_transition_events AS event
        JOIN {prefix}stocktake_tasks AS task
          ON replace(event.aggregate_id, '-', '') =
             replace(CAST(task.id AS TEXT), '-', '')
         WHERE event.aggregate_type = 'stocktake_task'
           AND (event.to_status = 'closed'
                OR event.reason =
                   'nonopening_stocktake_closed_after_internal_reconciliation')
           AND task.task_type IN {NONOPENING_SQL}
    ) OR EXISTS (
        SELECT 1 FROM {prefix}audit_events
         WHERE action IN ('stocktake.nonopening.closed',
                          'stocktake.nonopening.close_reconciliation_recorded')
    ) OR EXISTS (
        SELECT 1 FROM {prefix}permissions
         WHERE replace(CAST(id AS TEXT), '-', '') IN {permission_ids}
            OR (resource = 'stocktake' AND action IN ('reconcile', 'close')
                AND field_code = '')
    ) OR EXISTS (
        SELECT 1 FROM {prefix}role_permissions
         WHERE replace(CAST(id AS TEXT), '-', '') IN {role_permission_ids}
    )"""


def _require_safe_downgrade(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    predicates = " OR ".join(
        f"EXISTS (SELECT 1 FROM {prefix}{table_name})" for table_name in NEW_TABLES
    )
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {predicates}"
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _create_tables() -> None:
    quantity = sa.Numeric(18, 3)
    op.create_table(
        TRANSITION_ACK,
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("target_task_version", sa.BigInteger(), nullable=False),
        sa.Column("transition_kind", sa.String(24), nullable=False),
        sa.Column("reconciliation_completion_id", sa.Uuid(), nullable=True),
        sa.Column("close_completion_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "task_id",
            "target_task_version",
            name="pk_stocktake_close_transition_acks_0038",
        ),
        sa.UniqueConstraint(
            "task_id",
            "target_task_version",
            "reconciliation_completion_id",
            name="uq_stocktake_close_transition_ack_reconciliation_0038",
        ),
        sa.UniqueConstraint(
            "task_id",
            "target_task_version",
            "close_completion_id",
            name="uq_stocktake_close_transition_ack_close_0038",
        ),
        sa.UniqueConstraint(
            "reconciliation_completion_id",
            name="uq_stocktake_close_transition_ack_reconciliation_id_0038",
        ),
        sa.UniqueConstraint(
            "close_completion_id",
            name="uq_stocktake_close_transition_ack_close_id_0038",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["stocktake_tasks.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(transition_kind = 'reconciliation' AND "
            "reconciliation_completion_id IS NOT NULL AND "
            "close_completion_id IS NULL) OR "
            "(transition_kind = 'close' AND close_completion_id IS NOT NULL "
            "AND reconciliation_completion_id IS NULL)",
            name="ck_stocktake_close_transition_ack_binding_0038",
        ),
        sa.CheckConstraint(
            "target_task_version > 0 AND created_at = occurred_at",
            name="ck_stocktake_close_transition_ack_chronology_0038",
        ),
    )
    op.create_index(
        "ix_stocktake_close_transition_acks_kind_0038",
        TRANSITION_ACK,
        ["transition_kind", "occurred_at"],
    )
    op.create_table(
        RECONCILIATION,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("posting_completion_id", sa.Uuid(), nullable=False),
        sa.Column("previous_reconciliation_id", sa.Uuid(), nullable=True),
        sa.Column("reconciliation_no", sa.Integer(), nullable=False),
        sa.Column("expected_task_version", sa.BigInteger(), nullable=False),
        sa.Column("reconciled_task_version", sa.BigInteger(), nullable=False),
        sa.Column("reconciliation_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("scope_count", sa.Integer(), nullable=False),
        sa.Column("account_count", sa.Integer(), nullable=False),
        sa.Column("scoped_account_count", sa.Integer(), nullable=False),
        sa.Column("serial_count", sa.Integer(), nullable=False),
        sa.Column("transaction_count", sa.Integer(), nullable=False),
        sa.Column("movement_count", sa.Integer(), nullable=False),
        sa.Column("book_total_qty", quantity, nullable=False),
        sa.Column("physical_total_qty", quantity, nullable=False),
        sa.Column("posting_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("account_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("serial_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("reconciliation_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("reconciled_by_user_id", sa.String(36), nullable=False),
        sa.Column("reconciled_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("reconciled_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(80), nullable=False),
        sa.Column("authorization_sha256", sa.String(64), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_close_reconciliation_completions_0038"),
        sa.UniqueConstraint("id", "task_id", name="uq_stocktake_close_reconciliation_id_task_0038"),
        sa.UniqueConstraint("task_id", "reconciliation_no", name="uq_stocktake_close_reconciliation_task_no_0038"),
        sa.UniqueConstraint("task_id", "reconciled_task_version", name="uq_stocktake_close_reconciliation_task_version_0038"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_stocktake_close_reconciliation_idempotency_0038"),
        sa.ForeignKeyConstraint(
            ["posting_completion_id", "task_id"],
            ["stocktake_posting_completions.id", "stocktake_posting_completions.task_id"],
            name="fk_stocktake_close_reconciliation_posting_0038",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_reconciliation_id", "task_id"],
            [f"{RECONCILIATION}.id", f"{RECONCILIATION}.task_id"],
            name="fk_stocktake_close_reconciliation_previous_0038",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "reconciled_task_version", "id"],
            [
                f"{TRANSITION_ACK}.task_id",
                f"{TRANSITION_ACK}.target_task_version",
                f"{TRANSITION_ACK}.reconciliation_completion_id",
            ],
            name="fk_stocktake_close_reconciliation_transition_ack_0038",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["reconciled_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reconciled_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reconciled_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "(reconciliation_no = 1 AND previous_reconciliation_id IS NULL) OR "
            "(reconciliation_no > 1 AND previous_reconciliation_id IS NOT NULL)",
            name="ck_stocktake_close_reconciliation_chain_0038",
        ),
        sa.CheckConstraint(
            "expected_task_version >= 0 AND reconciled_task_version = expected_task_version + 1",
            name="ck_stocktake_close_reconciliation_versions_0038",
        ),
        sa.CheckConstraint(
            "reconciliation_ledger_cursor >= 0 AND scope_count > 0 AND "
            "account_count >= scoped_account_count AND scoped_account_count >= 0 AND "
            "serial_count >= 0 AND transaction_count >= 0 AND movement_count >= 0 AND "
            "book_total_qty >= 0 AND physical_total_qty >= 0 AND "
            "book_total_qty = physical_total_qty",
            name="ck_stocktake_close_reconciliation_totals_0038",
        ),
        sa.CheckConstraint(
            "authorization_version > 0 AND role_code = 'admin' AND "
            "scope_type = 'national' AND scope_id_snapshot = '*'",
            name="ck_stocktake_close_reconciliation_authorization_0038",
        ),
        sa.CheckConstraint(
            "length(posting_manifest_sha256) = 64 AND length(account_manifest_sha256) = 64 "
            "AND length(serial_manifest_sha256) = 64 AND "
            "length(reconciliation_manifest_sha256) = 64 AND length(request_sha256) = 64 "
            "AND length(idempotency_key_hash) = 64 AND length(authorization_sha256) = 64",
            name="ck_stocktake_close_reconciliation_hashes_0038",
        ),
        sa.CheckConstraint("created_at = reconciled_at", name="ck_stocktake_close_reconciliation_chronology_0038"),
    )
    op.create_index("ix_stocktake_close_reconciliation_task_0038", RECONCILIATION, ["task_id", "reconciliation_no"])

    op.create_table(
        RECONCILIATION_ACCOUNT,
        sa.Column("completion_id", sa.Uuid(), nullable=False),
        sa.Column("stock_account_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("effective_round_id", sa.Uuid(), nullable=True),
        sa.Column("account_role", sa.String(24), nullable=False),
        sa.Column("count_ledger_cursor", sa.BigInteger(), nullable=True),
        sa.Column("book_qty_at_count", quantity, nullable=True),
        sa.Column("physical_qty_at_count", quantity, nullable=True),
        sa.Column("ledger_delta_after_count", quantity, nullable=True),
        sa.Column("physical_delta_after_count", quantity, nullable=True),
        sa.Column("expected_physical_qty", quantity, nullable=True),
        sa.Column("ledger_qty", quantity, nullable=False),
        sa.Column("balance_qty", quantity, nullable=False),
        sa.Column("last_touch_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("balance_ledger_cursor", sa.BigInteger(), nullable=True),
        sa.Column("account_dimension_sha256", sa.String(64), nullable=False),
        sa.Column("item_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("completion_id", "stock_account_id", name="pk_stocktake_close_reconciliation_accounts_0038"),
        sa.ForeignKeyConstraint(
            ["completion_id", "task_id"],
            [f"{RECONCILIATION}.id", f"{RECONCILIATION}.task_id"],
            name="fk_stocktake_close_reconciliation_accounts_completion_0038",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["stock_account_id"], ["stock_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["scope_id", "task_id"], ["stocktake_scopes.id", "stocktake_scopes.task_id"], name="fk_stocktake_close_reconciliation_accounts_scope_0038", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["effective_round_id", "task_id"], ["stocktake_rounds.id", "stocktake_rounds.task_id"], name="fk_stocktake_close_reconciliation_accounts_round_0038", ondelete="RESTRICT"),
        sa.CheckConstraint("account_role IN ('scope', 'posting_counterpart')", name="ck_stocktake_close_reconciliation_accounts_role_0038"),
        sa.CheckConstraint(
            "(account_role = 'scope' AND scope_id IS NOT NULL AND effective_round_id IS NOT NULL "
            "AND count_ledger_cursor >= 0 AND book_qty_at_count IS NOT NULL "
            "AND physical_qty_at_count IS NOT NULL AND ledger_delta_after_count IS NOT NULL "
            "AND physical_delta_after_count IS NOT NULL AND expected_physical_qty IS NOT NULL "
            "AND book_qty_at_count >= 0 AND physical_qty_at_count >= 0 "
            "AND expected_physical_qty >= 0 "
            "AND expected_physical_qty = physical_qty_at_count + physical_delta_after_count "
            "AND ledger_qty = book_qty_at_count + ledger_delta_after_count "
            "AND expected_physical_qty = ledger_qty) OR "
            "(account_role = 'posting_counterpart' AND scope_id IS NULL "
            "AND effective_round_id IS NULL AND count_ledger_cursor IS NULL "
            "AND book_qty_at_count IS NULL AND physical_qty_at_count IS NULL "
            "AND ledger_delta_after_count IS NULL AND physical_delta_after_count IS NULL "
            "AND expected_physical_qty IS NULL)",
            name="ck_stocktake_close_reconciliation_accounts_physical_0038",
        ),
        sa.CheckConstraint(
            "ledger_qty >= 0 AND balance_qty >= 0 AND ledger_qty = balance_qty "
            "AND last_touch_ledger_cursor >= 0 AND "
            "((balance_ledger_cursor IS NULL AND balance_qty = 0 AND last_touch_ledger_cursor = 0) "
            "OR (balance_ledger_cursor = last_touch_ledger_cursor AND balance_ledger_cursor > 0))",
            name="ck_stocktake_close_reconciliation_accounts_ledger_0038",
        ),
        sa.CheckConstraint("length(account_dimension_sha256) = 64 AND length(item_manifest_sha256) = 64", name="ck_stocktake_close_reconciliation_accounts_hashes_0038"),
    )
    op.create_index("ix_stocktake_close_reconciliation_accounts_scope_0038", RECONCILIATION_ACCOUNT, ["task_id", "scope_id"])

    op.create_table(
        RECONCILIATION_SERIAL,
        sa.Column("completion_id", sa.Uuid(), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_scope_id", sa.Uuid(), nullable=True),
        sa.Column("effective_round_id", sa.Uuid(), nullable=True),
        sa.Column("count_ledger_cursor", sa.BigInteger(), nullable=True),
        sa.Column("physical_present_at_count", sa.Boolean(), nullable=True),
        sa.Column("physical_account_id_at_count", sa.Uuid(), nullable=True),
        sa.Column("expected_current_account_id", sa.Uuid(), nullable=True),
        sa.Column("ledger_last_movement_id", sa.Uuid(), nullable=False),
        sa.Column("current_position_account_id", sa.Uuid(), nullable=True),
        sa.Column("current_position_last_movement_id", sa.Uuid(), nullable=False),
        sa.Column("item_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("completion_id", "serial_id", name="pk_stocktake_close_reconciliation_serials_0038"),
        sa.ForeignKeyConstraint(["completion_id", "task_id"], [f"{RECONCILIATION}.id", f"{RECONCILIATION}.task_id"], name="fk_stocktake_close_reconciliation_serials_completion_0038", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["serial_id"], ["inventory_serials.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_scope_id", "task_id"], ["stocktake_scopes.id", "stocktake_scopes.task_id"], name="fk_stocktake_close_reconciliation_serials_scope_0038", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["effective_round_id", "task_id"], ["stocktake_rounds.id", "stocktake_rounds.task_id"], name="fk_stocktake_close_reconciliation_serials_round_0038", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["physical_account_id_at_count"], ["stock_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["expected_current_account_id"], ["stock_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ledger_last_movement_id"], ["inventory_movements.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_position_account_id"], ["stock_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_position_last_movement_id"], ["inventory_movements.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "(evidence_scope_id IS NULL AND effective_round_id IS NULL AND count_ledger_cursor IS NULL "
            "AND physical_present_at_count IS NULL AND physical_account_id_at_count IS NULL) OR "
            "(evidence_scope_id IS NOT NULL AND effective_round_id IS NOT NULL "
            "AND count_ledger_cursor >= 0 AND physical_present_at_count IS NOT NULL "
            "AND ((physical_present_at_count AND physical_account_id_at_count IS NOT NULL) "
            "OR (NOT physical_present_at_count AND physical_account_id_at_count IS NULL)))",
            name="ck_stocktake_close_reconciliation_serials_physical_0038",
        ),
        sa.CheckConstraint(
            "ledger_last_movement_id = current_position_last_movement_id AND "
            "((expected_current_account_id IS NULL AND current_position_account_id IS NULL) "
            "OR expected_current_account_id = current_position_account_id)",
            name="ck_stocktake_close_reconciliation_serials_position_0038",
        ),
        sa.CheckConstraint("length(item_manifest_sha256) = 64", name="ck_stocktake_close_reconciliation_serials_hash_0038"),
    )
    op.create_index("ix_stocktake_close_reconciliation_serials_scope_0038", RECONCILIATION_SERIAL, ["task_id", "evidence_scope_id"])

    op.create_table(
        CLOSE_COMPLETION,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("posting_completion_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_completion_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_no", sa.Integer(), nullable=False),
        sa.Column("reconciliation_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("expected_task_version", sa.BigInteger(), nullable=False),
        sa.Column("closed_task_version", sa.BigInteger(), nullable=False),
        sa.Column("posting_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("reconciliation_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("close_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("closed_by_user_id", sa.String(36), nullable=False),
        sa.Column("closed_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("closed_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(80), nullable=False),
        sa.Column("authorization_sha256", sa.String(64), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_close_completions_0038"),
        sa.UniqueConstraint("task_id", name="uq_stocktake_close_completions_task_0038"),
        sa.UniqueConstraint("reconciliation_completion_id", name="uq_stocktake_close_completions_reconciliation_0038"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_stocktake_close_completions_idempotency_0038"),
        sa.ForeignKeyConstraint(["posting_completion_id", "task_id"], ["stocktake_posting_completions.id", "stocktake_posting_completions.task_id"], name="fk_stocktake_close_completions_posting_0038", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reconciliation_completion_id", "task_id"], [f"{RECONCILIATION}.id", f"{RECONCILIATION}.task_id"], name="fk_stocktake_close_completions_reconciliation_0038", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["task_id", "closed_task_version", "id"],
            [
                f"{TRANSITION_ACK}.task_id",
                f"{TRANSITION_ACK}.target_task_version",
                f"{TRANSITION_ACK}.close_completion_id",
            ],
            name="fk_stocktake_close_completion_transition_ack_0038",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["closed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["closed_by_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["closed_role_assignment_id"], ["role_assignments.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("expected_task_version >= 0 AND closed_task_version = expected_task_version + 1 AND reconciliation_no > 0 AND reconciliation_ledger_cursor >= 0", name="ck_stocktake_close_completions_versions_0038"),
        sa.CheckConstraint("authorization_version > 0 AND role_code = 'admin' AND scope_type = 'national' AND scope_id_snapshot = '*'", name="ck_stocktake_close_completions_authorization_0038"),
        sa.CheckConstraint(
            "length(posting_manifest_sha256) = 64 AND length(reconciliation_manifest_sha256) = 64 "
            "AND length(close_manifest_sha256) = 64 AND length(request_sha256) = 64 "
            "AND length(idempotency_key_hash) = 64 AND length(authorization_sha256) = 64",
            name="ck_stocktake_close_completions_hashes_0038",
        ),
        sa.CheckConstraint("created_at = closed_at", name="ck_stocktake_close_completions_chronology_0038"),
    )
    op.create_index("ix_stocktake_close_completions_actor_0038", CLOSE_COMPLETION, ["closed_by_user_id", "closed_at"])


def _seed_permissions() -> None:
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
        [
            {"id": RECONCILE_PERMISSION_ID, "resource": "stocktake", "action": "reconcile", "field_code": "", "description": "Append an internal non-opening stocktake close reconciliation", "created_at": seeded_at, "updated_at": seeded_at},
            {"id": CLOSE_PERMISSION_ID, "resource": "stocktake", "action": "close", "field_code": "", "description": "Close a currently reconciled non-opening stocktake", "created_at": seeded_at, "updated_at": seeded_at},
        ],
    )
    op.bulk_insert(
        role_permission_table,
        [
            {"id": RECONCILE_ROLE_PERMISSION_ID, "role_id": ADMIN_ROLE_ID, "permission_id": RECONCILE_PERMISSION_ID, "effect": "allow", "created_at": seeded_at},
            {"id": CLOSE_ROLE_PERMISSION_ID, "role_id": ADMIN_ROLE_ID, "permission_id": CLOSE_PERMISSION_ID, "effect": "allow", "created_at": seeded_at},
        ],
    )


def _delete_permissions() -> None:
    role_permission_table = sa.table("role_permissions", sa.column("id", sa.Uuid()))
    permission_table = sa.table("permissions", sa.column("id", sa.Uuid()))
    op.execute(role_permission_table.delete().where(role_permission_table.c.id.in_((RECONCILE_ROLE_PERMISSION_ID, CLOSE_ROLE_PERMISSION_ID))))
    op.execute(permission_table.delete().where(permission_table.c.id.in_((RECONCILE_PERMISSION_ID, CLOSE_PERMISSION_ID))))


def _create_sqlite_immutability_triggers() -> None:
    for table_name in NEW_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable_{operation.lower()}_0038 "
                f"BEFORE {operation} ON {table_name} BEGIN "
                "SELECT RAISE(ABORT, 'non-opening stocktake close facts are immutable'); END"
            )


def _sqlite_ack_insert_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_ACK_INSERT}
BEFORE INSERT ON {TRANSITION_ACK}
WHEN NOT (
    NEW.transition_kind = 'reconciliation'
    AND EXISTS (
        SELECT 1 FROM stocktake_tasks AS task
        JOIN {RECONCILIATION} AS completion
          ON completion.id = NEW.reconciliation_completion_id
         AND completion.task_id = task.id
         WHERE task.id = NEW.task_id
           AND task.task_type IN {NONOPENING_SQL}
           AND task.status = 'posted' AND task.closed_at IS NULL
           AND task.version = NEW.target_task_version
           AND completion.reconciled_task_version = task.version
           AND completion.expected_task_version = task.version - 1
           AND completion.reconciled_at = task.updated_at
           AND NEW.close_completion_id IS NULL
           AND NEW.occurred_at = completion.reconciled_at
           AND NEW.created_at = NEW.occurred_at)
) AND NOT (
    NEW.transition_kind = 'close'
    AND EXISTS (
        SELECT 1 FROM stocktake_tasks AS task
        JOIN {CLOSE_COMPLETION} AS completion
          ON completion.id = NEW.close_completion_id
         AND completion.task_id = task.id
         WHERE task.id = NEW.task_id
           AND task.task_type IN {NONOPENING_SQL}
           AND task.status = 'closed'
           AND task.version = NEW.target_task_version
           AND completion.closed_task_version = task.version
           AND completion.expected_task_version = task.version - 1
           AND completion.closed_at = task.closed_at
           AND task.updated_at = task.closed_at
           AND NEW.reconciliation_completion_id IS NULL
           AND NEW.occurred_at = completion.closed_at
           AND NEW.created_at = NEW.occurred_at)
)
BEGIN
    SELECT RAISE(ABORT, 'stocktake close transition ack is trigger-only');
END
"""


def _sqlite_reconciliation_insert_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_RECONCILIATION_INSERT}
BEFORE INSERT ON {RECONCILIATION}
WHEN NOT EXISTS (
    SELECT 1
      FROM stocktake_tasks AS task
      JOIN stocktake_posting_completions AS posting
        ON posting.id = NEW.posting_completion_id AND posting.task_id = task.id
     WHERE task.id = NEW.task_id
       AND task.task_type IN {NONOPENING_SQL}
       AND task.status = 'posted' AND task.closed_at IS NULL
       AND task.version = NEW.expected_task_version
       AND posting.posted_task_version <= task.version
       AND posting.posting_manifest_sha256 = NEW.posting_manifest_sha256
       AND NEW.reconciliation_ledger_cursor =
           (SELECT next_cursor - 1 FROM inventory_ledger_heads WHERE stream_key = 'inventory')
       AND NEW.reconciled_at > posting.posted_at
       AND ((NEW.reconciliation_no = 1
             AND NEW.previous_reconciliation_id IS NULL
             AND NEW.expected_task_version = posting.posted_task_version
             AND NOT EXISTS (SELECT 1 FROM {RECONCILIATION} WHERE task_id = NEW.task_id))
            OR (NEW.reconciliation_no > 1
                AND EXISTS (
                    SELECT 1 FROM {RECONCILIATION} AS previous
                     WHERE previous.id = NEW.previous_reconciliation_id
                       AND previous.task_id = NEW.task_id
                       AND previous.reconciliation_no = NEW.reconciliation_no - 1
                       AND previous.reconciled_task_version = NEW.expected_task_version
                       AND previous.reconciliation_ledger_cursor <= NEW.reconciliation_ledger_cursor
                       AND previous.reconciled_at < NEW.reconciled_at
                       AND NOT EXISTS (
                           SELECT 1 FROM {RECONCILIATION} AS later
                            WHERE later.task_id = NEW.task_id
                              AND later.reconciliation_no > previous.reconciliation_no))))
)
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake reconciliation chain is invalid');
END
"""


def _sqlite_account_insert_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_ACCOUNT_INSERT}
BEFORE INSERT ON {RECONCILIATION_ACCOUNT}
WHEN NOT EXISTS (
    SELECT 1 FROM {RECONCILIATION} AS completion
    JOIN stocktake_tasks AS task ON task.id = completion.task_id
     WHERE completion.id = NEW.completion_id AND completion.task_id = NEW.task_id
       AND task.status = 'posted' AND task.version = completion.expected_task_version
       AND NEW.last_touch_ledger_cursor <= completion.reconciliation_ledger_cursor
       AND (NEW.count_ledger_cursor IS NULL OR
            NEW.count_ledger_cursor <= completion.reconciliation_ledger_cursor)
)
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake reconciliation account is invalid');
END
"""


def _sqlite_serial_insert_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_SERIAL_INSERT}
BEFORE INSERT ON {RECONCILIATION_SERIAL}
WHEN NOT EXISTS (
    SELECT 1 FROM {RECONCILIATION} AS completion
    JOIN stocktake_tasks AS task ON task.id = completion.task_id
     WHERE completion.id = NEW.completion_id AND completion.task_id = NEW.task_id
       AND task.status = 'posted' AND task.version = completion.expected_task_version
       AND (NEW.count_ledger_cursor IS NULL OR
            NEW.count_ledger_cursor <= completion.reconciliation_ledger_cursor)
       AND (NEW.evidence_scope_id IS NULL OR EXISTS (
           WITH physical_evidence(scope_id, stock_account_id) AS (
               SELECT line.scope_id, line.stock_account_id
                 FROM stocktake_count_serials AS counted_serial
                 JOIN stocktake_count_lines AS line
                   ON line.id = counted_serial.count_line_id
                 JOIN stocktake_effective_approval_scopes AS selected
                   ON selected.completion_id = (
                       SELECT posting.effective_approval_completion_id
                         FROM stocktake_posting_completions AS posting
                        WHERE posting.id = completion.posting_completion_id)
                  AND selected.scope_id = line.scope_id
                  AND selected.source_round_id = line.round_id
                WHERE line.task_id = NEW.task_id
                  AND counted_serial.serial_id = NEW.serial_id
               UNION ALL
               SELECT observation.scope_id, stock_account.id
                 FROM stocktake_count_observations AS observation
                 JOIN stocktake_effective_approval_scopes AS selected
                   ON selected.completion_id = (
                       SELECT posting.effective_approval_completion_id
                         FROM stocktake_posting_completions AS posting
                        WHERE posting.id = completion.posting_completion_id)
                  AND selected.scope_id = observation.scope_id
                  AND selected.source_round_id = observation.round_id
                 JOIN stock_accounts AS stock_account
                   ON stock_account.owner_org_id = observation.owner_org_id
                  AND stock_account.location_id = observation.location_id
                  AND stock_account.custodian_person_id
                      IS observation.custodian_person_id_snapshot
                  AND stock_account.material_id = observation.material_id
                  AND stock_account.condition_code = observation.condition_code
                  AND stock_account.availability_bucket = observation.availability_bucket
                  AND stock_account.lot_id IS observation.lot_id
                WHERE observation.task_id = NEW.task_id
                  AND observation.serial_id = NEW.serial_id
                  AND observation.verification_status = 'verified'
                  AND observation.material_id IS NOT NULL
           )
           SELECT 1
            WHERE NEW.effective_round_id IS (
                      SELECT selected.source_round_id
                        FROM stocktake_posting_completions AS posting
                        JOIN stocktake_effective_approval_scopes AS selected
                          ON selected.completion_id =
                             posting.effective_approval_completion_id
                       WHERE posting.id = completion.posting_completion_id
                         AND selected.scope_id = NEW.evidence_scope_id)
              AND NEW.count_ledger_cursor IS (
                      SELECT count_completion.count_ledger_cursor
                        FROM stocktake_scope_count_completions AS count_completion
                       WHERE count_completion.task_id = NEW.task_id
                         AND count_completion.scope_id = NEW.evidence_scope_id
                         AND count_completion.round_id = NEW.effective_round_id)
              AND (SELECT count(*) FROM physical_evidence) <= 1
              AND NOT EXISTS (
                      SELECT 1 FROM physical_evidence AS evidence
                       WHERE evidence.scope_id IS NOT NEW.evidence_scope_id)
              AND COALESCE(CAST(NEW.physical_present_at_count AS INTEGER), -1) =
                  CASE WHEN (SELECT count(*) FROM physical_evidence) = 1
                       THEN 1 ELSE 0 END
              AND NEW.physical_account_id_at_count IS
                  CASE WHEN (SELECT count(*) FROM physical_evidence) = 1
                       THEN (SELECT stock_account_id
                               FROM physical_evidence LIMIT 1)
                       ELSE NULL END
       ))
)
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake reconciliation serial is invalid');
END
"""


def _sqlite_close_insert_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_CLOSE_INSERT}
BEFORE INSERT ON {CLOSE_COMPLETION}
WHEN NOT EXISTS (
    SELECT 1 FROM stocktake_tasks AS task
    JOIN stocktake_posting_completions AS posting
      ON posting.id = NEW.posting_completion_id AND posting.task_id = task.id
    JOIN {RECONCILIATION} AS reconciliation
      ON reconciliation.id = NEW.reconciliation_completion_id
     AND reconciliation.task_id = task.id
     WHERE task.id = NEW.task_id AND task.task_type IN {NONOPENING_SQL}
       AND task.status = 'posted' AND task.closed_at IS NULL
       AND task.version = NEW.expected_task_version
       AND reconciliation.reconciled_task_version = task.version
       AND reconciliation.reconciliation_no = NEW.reconciliation_no
       AND reconciliation.reconciliation_ledger_cursor = NEW.reconciliation_ledger_cursor
       AND reconciliation.posting_completion_id = posting.id
       AND reconciliation.posting_manifest_sha256 = NEW.posting_manifest_sha256
       AND reconciliation.reconciliation_manifest_sha256 = NEW.reconciliation_manifest_sha256
       AND reconciliation.reconciliation_ledger_cursor =
           (SELECT next_cursor - 1 FROM inventory_ledger_heads WHERE stream_key = 'inventory')
       AND NEW.closed_at > reconciliation.reconciled_at
       AND NOT EXISTS (
           SELECT 1 FROM {RECONCILIATION} AS later
            WHERE later.task_id = task.id
              AND later.reconciliation_no > reconciliation.reconciliation_no)
)
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake close completion is invalid');
END
"""


def _sqlite_uuid_text(expression: str) -> str:
    normalized = f"replace(lower(CAST({expression} AS TEXT)), '-', '')"
    return (
        f"(substr({normalized}, 1, 8) || '-' || "
        f"substr({normalized}, 9, 4) || '-' || "
        f"substr({normalized}, 13, 4) || '-' || "
        f"substr({normalized}, 17, 4) || '-' || "
        f"substr({normalized}, 21, 12))"
    )


def _sqlite_audit_event_guard_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_AUDIT_EVENT_GUARD}
BEFORE INSERT ON audit_events
WHEN (
    NEW.action IN (
        'stocktake.nonopening.close_reconciliation_recorded',
        'stocktake.nonopening.closed'
    )
    OR NEW.aggregate_type IN (
        'stocktake_close_reconciliation',
        'stocktake_close_completion'
    )
) AND NOT (
    NEW.stream_key = 'inventory'
    AND NEW.action = 'stocktake.nonopening.close_reconciliation_recorded'
    AND NEW.aggregate_type = 'stocktake_close_reconciliation'
    AND EXISTS (
        SELECT 1
          FROM {RECONCILIATION} AS completion
         WHERE NEW.aggregate_id = {_sqlite_uuid_text('completion.id')}
           AND NEW.actor_user_id = completion.reconciled_by_user_id
           AND NEW.occurred_at = completion.reconciled_at
           AND json_valid(NEW.before_jsonb)
           AND json_type(NEW.before_jsonb) = 'object'
           AND (SELECT count(*) FROM json_each(NEW.before_jsonb)) = 2
           AND json_extract(NEW.before_jsonb, '$.status') = 'posted'
           AND json_extract(NEW.before_jsonb, '$.task_version') =
               completion.expected_task_version
           AND json_valid(NEW.after_jsonb)
           AND json_type(NEW.after_jsonb) = 'object'
           AND (SELECT count(*) FROM json_each(NEW.after_jsonb)) = 13
           AND json_extract(NEW.after_jsonb, '$.account_count') =
               completion.account_count
           AND json_extract(NEW.after_jsonb, '$.book_total_qty') =
               printf('%.3f', completion.book_total_qty)
           AND json_extract(NEW.after_jsonb, '$.completion_id') =
               {_sqlite_uuid_text('completion.id')}
           AND json_extract(NEW.after_jsonb, '$.physical_total_qty') =
               printf('%.3f', completion.physical_total_qty)
           AND json_extract(NEW.after_jsonb, '$.posting_completion_id') =
               {_sqlite_uuid_text('completion.posting_completion_id')}
           AND json_extract(NEW.after_jsonb, '$.reconciled_task_version') =
               completion.reconciled_task_version
           AND json_extract(NEW.after_jsonb, '$.reconciliation_ledger_cursor') =
               completion.reconciliation_ledger_cursor
           AND json_extract(NEW.after_jsonb, '$.reconciliation_manifest_sha256') =
               completion.reconciliation_manifest_sha256
           AND json_extract(NEW.after_jsonb, '$.reconciliation_no') =
               completion.reconciliation_no
           AND json_extract(NEW.after_jsonb, '$.schema') =
               'cloud_oam.stocktake.nonopening_close_reconciled_event.v1'
           AND json_extract(NEW.after_jsonb, '$.serial_count') =
               completion.serial_count
           AND json_extract(NEW.after_jsonb, '$.status') = 'posted'
           AND json_extract(NEW.after_jsonb, '$.task_id') =
               {_sqlite_uuid_text('completion.task_id')}
           AND NEW.request_id LIKE 'stocktake-close-reconcile-request-%'
           AND length(NEW.request_id) =
               length('stocktake-close-reconcile-request-') + 64
           AND substr(
                   NEW.request_id,
                   length('stocktake-close-reconcile-request-') + 1
               ) NOT GLOB '*[^0-9a-f]*'
           AND (SELECT count(*)
                  FROM audit_events AS existing
                 WHERE existing.stream_key = 'inventory'
                   AND existing.action =
                       'stocktake.nonopening.close_reconciliation_recorded'
                   AND existing.aggregate_type =
                       'stocktake_close_reconciliation'
                   AND existing.aggregate_id = NEW.aggregate_id) = 0
    )
) AND NOT (
    NEW.stream_key = 'inventory'
    AND NEW.action = 'stocktake.nonopening.closed'
    AND NEW.aggregate_type = 'stocktake_close_completion'
    AND EXISTS (
        SELECT 1
          FROM {CLOSE_COMPLETION} AS close_row
         WHERE NEW.aggregate_id = {_sqlite_uuid_text('close_row.id')}
           AND NEW.actor_user_id = close_row.closed_by_user_id
           AND NEW.occurred_at = close_row.closed_at
           AND json_valid(NEW.before_jsonb)
           AND json_type(NEW.before_jsonb) = 'object'
           AND (SELECT count(*) FROM json_each(NEW.before_jsonb)) = 2
           AND json_extract(NEW.before_jsonb, '$.status') = 'posted'
           AND json_extract(NEW.before_jsonb, '$.task_version') =
               close_row.expected_task_version
           AND json_valid(NEW.after_jsonb)
           AND json_type(NEW.after_jsonb) = 'object'
           AND (SELECT count(*) FROM json_each(NEW.after_jsonb)) = 11
           AND json_extract(NEW.after_jsonb, '$.close_completion_id') =
               {_sqlite_uuid_text('close_row.id')}
           AND json_extract(NEW.after_jsonb, '$.close_manifest_sha256') =
               close_row.close_manifest_sha256
           AND json_extract(NEW.after_jsonb, '$.closed_task_version') =
               close_row.closed_task_version
           AND json_extract(NEW.after_jsonb, '$.posting_completion_id') =
               {_sqlite_uuid_text('close_row.posting_completion_id')}
           AND json_extract(NEW.after_jsonb, '$.reconciliation_completion_id') =
               {_sqlite_uuid_text('close_row.reconciliation_completion_id')}
           AND json_extract(NEW.after_jsonb, '$.reconciliation_ledger_cursor') =
               close_row.reconciliation_ledger_cursor
           AND json_extract(NEW.after_jsonb, '$.reconciliation_manifest_sha256') =
               close_row.reconciliation_manifest_sha256
           AND json_extract(NEW.after_jsonb, '$.reconciliation_no') =
               close_row.reconciliation_no
           AND json_extract(NEW.after_jsonb, '$.schema') =
               'cloud_oam.stocktake.nonopening_closed_event.v1'
           AND json_extract(NEW.after_jsonb, '$.status') = 'closed'
           AND json_extract(NEW.after_jsonb, '$.task_id') =
               {_sqlite_uuid_text('close_row.task_id')}
           AND NEW.request_id LIKE 'stocktake-close-close-request-%'
           AND length(NEW.request_id) =
               length('stocktake-close-close-request-') + 64
           AND substr(
                   NEW.request_id,
                   length('stocktake-close-close-request-') + 1
               ) NOT GLOB '*[^0-9a-f]*'
           AND (SELECT count(*)
                  FROM audit_events AS existing
                 WHERE existing.stream_key = 'inventory'
                   AND existing.action = 'stocktake.nonopening.closed'
                   AND existing.aggregate_type = 'stocktake_close_completion'
                   AND existing.aggregate_id = NEW.aggregate_id) = 0
    )
)
BEGIN
    SELECT RAISE(
        ABORT,
        'non-opening stocktake close audit event is invalid or duplicate'
    );
END
"""


def _sqlite_state_event_guard_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_STATE_EVENT_GUARD}
BEFORE INSERT ON state_transition_events
WHEN NEW.reason = 'nonopening_stocktake_closed_after_internal_reconciliation'
 AND NOT EXISTS (
    SELECT 1
      FROM {CLOSE_COMPLETION} AS close_row
     WHERE NEW.aggregate_type = 'stocktake_task'
       AND NEW.aggregate_id = {_sqlite_uuid_text('close_row.task_id')}
       AND NEW.from_status = 'posted'
       AND NEW.to_status = 'closed'
       AND NEW.actor_id = close_row.closed_by_user_id
       AND NEW.occurred_at = close_row.closed_at
       AND json_valid(NEW.metadata_jsonb)
       AND json_type(NEW.metadata_jsonb) = 'object'
       AND (SELECT count(*) FROM json_each(NEW.metadata_jsonb)) = 11
       AND json_extract(NEW.metadata_jsonb, '$.close_completion_id') =
           {_sqlite_uuid_text('close_row.id')}
       AND json_extract(NEW.metadata_jsonb, '$.close_manifest_sha256') =
           close_row.close_manifest_sha256
       AND json_extract(NEW.metadata_jsonb, '$.closed_task_version') =
           close_row.closed_task_version
       AND json_extract(NEW.metadata_jsonb, '$.posting_completion_id') =
           {_sqlite_uuid_text('close_row.posting_completion_id')}
       AND json_extract(NEW.metadata_jsonb, '$.reconciliation_completion_id') =
           {_sqlite_uuid_text('close_row.reconciliation_completion_id')}
       AND json_extract(NEW.metadata_jsonb, '$.reconciliation_ledger_cursor') =
           close_row.reconciliation_ledger_cursor
       AND json_extract(NEW.metadata_jsonb, '$.reconciliation_manifest_sha256') =
           close_row.reconciliation_manifest_sha256
       AND json_extract(NEW.metadata_jsonb, '$.reconciliation_no') =
           close_row.reconciliation_no
       AND json_extract(NEW.metadata_jsonb, '$.schema') =
           'cloud_oam.stocktake.nonopening_closed_event.v1'
       AND json_extract(NEW.metadata_jsonb, '$.status') = 'closed'
       AND json_extract(NEW.metadata_jsonb, '$.task_id') =
           {_sqlite_uuid_text('close_row.task_id')}
       AND NEW.idempotency_key LIKE 'stocktake-close-close-state-%'
       AND length(NEW.idempotency_key) =
           length('stocktake-close-close-state-') + 64
       AND substr(
               NEW.idempotency_key,
               length('stocktake-close-close-state-') + 1
           ) NOT GLOB '*[^0-9a-f]*'
       AND (SELECT count(*)
              FROM state_transition_events AS existing
             WHERE existing.aggregate_type = 'stocktake_task'
               AND existing.aggregate_id = NEW.aggregate_id
               AND existing.reason =
                   'nonopening_stocktake_closed_after_internal_reconciliation') = 0
 )
BEGIN
    SELECT RAISE(
        ABORT,
        'non-opening stocktake close state event is invalid or duplicate'
    );
END
"""


def _sqlite_task_terminal_sql() -> str:
    unchanged_columns = (
        "id",
        "task_no",
        "task_type",
        "region_org_id",
        "blind_count",
        "cutoff_ledger_cursor",
        "cutoff_at",
        "scope_manifest_sha256",
        "snapshot_manifest_sha256",
        "control_source_system_id",
        "control_sync_run_id",
        "control_snapshot_at",
        "control_manifest_sha256",
        "current_round_no",
        "created_by_user_id",
        "deadline",
        "issued_at",
        "frozen_at",
        "submitted_at",
        "posted_at",
        "cancelled_at",
        "note",
        "created_at",
    )
    unchanged = " AND ".join(
        f"NEW.{column_name} IS OLD.{column_name}"
        for column_name in unchanged_columns
    )
    account_ledger = f"""account.ledger_qty = COALESCE((
        SELECT sum(CASE WHEN movement.to_account_id = account.stock_account_id
                        THEN movement.quantity ELSE 0 END
                 - CASE WHEN movement.from_account_id = account.stock_account_id
                        THEN movement.quantity ELSE 0 END)
          FROM inventory_movements AS movement
          JOIN inventory_transactions AS transaction_row
            ON transaction_row.id = movement.transaction_id
         WHERE transaction_row.status = 'posted'
           AND transaction_row.ledger_cursor <= completion.reconciliation_ledger_cursor
           AND (movement.from_account_id = account.stock_account_id
                OR movement.to_account_id = account.stock_account_id)), 0)"""
    exact_coordinate_union = f"""NOT EXISTS (
        WITH scope_account_ids AS (
            SELECT DISTINCT stock_account.id
              FROM stock_accounts AS stock_account
              JOIN stocktake_scopes AS scope
                ON scope.task_id = NEW.id
               AND scope.owner_org_id = stock_account.owner_org_id
               AND scope.location_id = stock_account.location_id
               AND (scope.material_id IS NULL OR
                    scope.material_id = stock_account.material_id)
               AND (scope.condition_code IS NULL OR
                    scope.condition_code = stock_account.condition_code)
               AND (scope.availability_bucket IS NULL OR
                    scope.availability_bucket = stock_account.availability_bucket)
        ),
        base_account_ids AS (
            SELECT snapshot.stock_account_id AS id
              FROM stocktake_snapshot_lines AS snapshot
             WHERE snapshot.task_id = NEW.id
            UNION SELECT id FROM scope_account_ids
            UNION
            SELECT movement.from_account_id
              FROM stocktake_posting_completion_items AS item
              JOIN inventory_movements AS movement
                ON movement.id = item.inventory_movement_id
             WHERE item.completion_id = completion.posting_completion_id
               AND movement.from_account_id IS NOT NULL
            UNION
            SELECT movement.to_account_id
              FROM stocktake_posting_completion_items AS item
              JOIN inventory_movements AS movement
                ON movement.id = item.inventory_movement_id
             WHERE item.completion_id = completion.posting_completion_id
               AND movement.to_account_id IS NOT NULL
        ),
        expected_account_ids AS (
            SELECT id FROM base_account_ids WHERE id IS NOT NULL
            UNION
            SELECT movement.from_account_id
              FROM inventory_movements AS movement
             WHERE movement.from_account_id IS NOT NULL
               AND (movement.from_account_id IN (SELECT id FROM base_account_ids)
                    OR movement.to_account_id IN (SELECT id FROM base_account_ids))
            UNION
            SELECT movement.to_account_id
              FROM inventory_movements AS movement
             WHERE movement.to_account_id IS NOT NULL
               AND (movement.from_account_id IN (SELECT id FROM base_account_ids)
                    OR movement.to_account_id IN (SELECT id FROM base_account_ids))
        ),
        expected_serial_ids AS (
            SELECT CASE
                       WHEN json_type(serial_json.value) = 'object'
                        AND json_type(serial_json.value, '$.serial_id') = 'text'
                        AND length(replace(lower(
                            json_extract(serial_json.value, '$.serial_id')), '-', '')) = 32
                        AND replace(lower(
                            json_extract(serial_json.value, '$.serial_id')), '-', '')
                            NOT GLOB '*[^0-9a-f]*'
                        AND replace(lower(
                            json_extract(serial_json.value, '$.serial_id')), '-', '') <>
                            '00000000000000000000000000000000'
                       THEN replace(lower(
                            json_extract(serial_json.value, '$.serial_id')), '-', '')
                       ELSE NULL
                   END AS id
              FROM stocktake_snapshot_lines AS snapshot,
                   json_each(snapshot.serial_snapshot_jsonb) AS serial_json
             WHERE snapshot.task_id = NEW.id
            UNION
            SELECT serial.serial_id
              FROM stocktake_count_serials AS serial
              JOIN stocktake_count_lines AS line
                ON line.id = serial.count_line_id
             WHERE line.task_id = NEW.id
            UNION
            SELECT observation.serial_id
              FROM stocktake_count_observations AS observation
             WHERE observation.task_id = NEW.id
               AND observation.serial_id IS NOT NULL
            UNION
            SELECT difference.serial_id
              FROM stocktake_differences AS difference
             WHERE difference.task_id = NEW.id
               AND difference.serial_id IS NOT NULL
            UNION
            SELECT binding.serial_id
              FROM stocktake_posting_completion_items AS item
              JOIN inventory_movement_serials AS binding
                ON binding.movement_id = item.inventory_movement_id
             WHERE item.completion_id = completion.posting_completion_id
            UNION
            SELECT position.serial_id
              FROM serial_current_positions AS position
             WHERE position.stock_account_id IN (SELECT id FROM scope_account_ids)
            UNION
            SELECT binding.serial_id
              FROM inventory_movement_serials AS binding
              JOIN inventory_movements AS movement
                ON movement.id = binding.movement_id
             WHERE movement.from_account_id IN (SELECT id FROM expected_account_ids)
                OR movement.to_account_id IN (SELECT id FROM expected_account_ids)
        )
        SELECT 1
         WHERE EXISTS (
             SELECT 1 FROM expected_account_ids AS expected
              WHERE NOT EXISTS (
                  SELECT 1 FROM {RECONCILIATION_ACCOUNT} AS actual
                   WHERE actual.completion_id = completion.id
                     AND actual.stock_account_id = expected.id))
            OR EXISTS (
             SELECT 1 FROM {RECONCILIATION_ACCOUNT} AS actual
              WHERE actual.completion_id = completion.id
                AND NOT EXISTS (
                    SELECT 1 FROM expected_account_ids AS expected
                     WHERE expected.id = actual.stock_account_id))
            OR EXISTS (
             SELECT 1 FROM expected_serial_ids AS expected
              WHERE NOT EXISTS (
                  SELECT 1 FROM {RECONCILIATION_SERIAL} AS actual
                   WHERE actual.completion_id = completion.id
                     AND replace(lower(CAST(actual.serial_id AS TEXT)), '-', '') =
                         expected.id))
            OR EXISTS (
             SELECT 1 FROM {RECONCILIATION_SERIAL} AS actual
              WHERE actual.completion_id = completion.id
                AND NOT EXISTS (
                    SELECT 1 FROM expected_serial_ids AS expected
                     WHERE expected.id = replace(
                         lower(CAST(actual.serial_id AS TEXT)), '-', ''))))"""
    ledger_counts = f"""completion.transaction_count = (
        SELECT count(DISTINCT transaction_row.id)
          FROM inventory_transactions AS transaction_row
          JOIN inventory_movements AS movement
            ON movement.transaction_id = transaction_row.id
         WHERE transaction_row.status = 'posted'
           AND transaction_row.ledger_cursor <= completion.reconciliation_ledger_cursor
           AND EXISTS (
               SELECT 1 FROM {RECONCILIATION_ACCOUNT} AS account
                WHERE account.completion_id = completion.id
                  AND (account.stock_account_id = movement.from_account_id
                       OR account.stock_account_id = movement.to_account_id)))
       AND completion.movement_count = (
        SELECT count(DISTINCT movement.id)
          FROM inventory_transactions AS transaction_row
          JOIN inventory_movements AS movement
            ON movement.transaction_id = transaction_row.id
         WHERE transaction_row.status = 'posted'
           AND transaction_row.ledger_cursor <= completion.reconciliation_ledger_cursor
           AND EXISTS (
               SELECT 1 FROM {RECONCILIATION_ACCOUNT} AS account
                WHERE account.completion_id = completion.id
                  AND (account.stock_account_id = movement.from_account_id
                       OR account.stock_account_id = movement.to_account_id)))"""
    account_physical_reproof = f"""NOT EXISTS (
        SELECT 1
          FROM {RECONCILIATION_ACCOUNT} AS account
          JOIN stock_accounts AS stock_account
            ON stock_account.id = account.stock_account_id
         WHERE account.completion_id = completion.id
           AND account.scope_id IS NOT NULL
           AND ((SELECT count(*)
                  FROM stocktake_count_lines AS line
                 WHERE line.task_id = NEW.id
                   AND line.scope_id = account.scope_id
                   AND line.round_id = account.effective_round_id
                   AND line.stock_account_id = account.stock_account_id)
                +
                (SELECT count(*)
                   FROM stocktake_count_observations AS observation
                  WHERE observation.task_id = NEW.id
                    AND observation.scope_id = account.scope_id
                    AND observation.round_id = account.effective_round_id
                    AND observation.verification_status = 'verified'
                    AND observation.material_id = stock_account.material_id
                    AND observation.owner_org_id = stock_account.owner_org_id
                    AND observation.location_id = stock_account.location_id
                    AND observation.custodian_person_id_snapshot IS stock_account.custodian_person_id
                    AND observation.condition_code = stock_account.condition_code
                    AND observation.availability_bucket = stock_account.availability_bucket
                    AND observation.lot_id IS stock_account.lot_id)) > 1
                OR account.physical_qty_at_count IS NOT (
                    COALESCE((
                        SELECT sum(line.counted_qty)
                          FROM stocktake_count_lines AS line
                         WHERE line.task_id = NEW.id
                           AND line.scope_id = account.scope_id
                           AND line.round_id = account.effective_round_id
                           AND line.stock_account_id = account.stock_account_id), 0)
                    + COALESCE((
                        SELECT sum(observation.counted_qty)
                          FROM stocktake_count_observations AS observation
                         WHERE observation.task_id = NEW.id
                           AND observation.scope_id = account.scope_id
                           AND observation.round_id = account.effective_round_id
                           AND observation.verification_status = 'verified'
                           AND observation.material_id = stock_account.material_id
                           AND observation.owner_org_id = stock_account.owner_org_id
                           AND observation.location_id = stock_account.location_id
                           AND observation.custodian_person_id_snapshot IS stock_account.custodian_person_id
                           AND observation.condition_code = stock_account.condition_code
                           AND observation.availability_bucket = stock_account.availability_bucket
                           AND observation.lot_id IS stock_account.lot_id), 0))
                OR account.book_qty_at_count IS NOT COALESCE((
                    SELECT sum(
                        CASE WHEN movement.to_account_id = account.stock_account_id
                             THEN movement.quantity ELSE 0 END
                        - CASE WHEN movement.from_account_id = account.stock_account_id
                               THEN movement.quantity ELSE 0 END)
                      FROM inventory_transactions AS transaction_row
                      JOIN inventory_movements AS movement
                        ON movement.transaction_id = transaction_row.id
                     WHERE transaction_row.status = 'posted'
                       AND transaction_row.ledger_cursor <= account.count_ledger_cursor
                       AND (movement.from_account_id = account.stock_account_id
                            OR movement.to_account_id = account.stock_account_id)), 0)
                OR account.ledger_delta_after_count IS NOT COALESCE((
                    SELECT sum(
                        CASE WHEN movement.to_account_id = account.stock_account_id
                             THEN movement.quantity ELSE 0 END
                        - CASE WHEN movement.from_account_id = account.stock_account_id
                               THEN movement.quantity ELSE 0 END)
                      FROM inventory_transactions AS transaction_row
                      JOIN inventory_movements AS movement
                        ON movement.transaction_id = transaction_row.id
                     WHERE transaction_row.status = 'posted'
                       AND transaction_row.ledger_cursor > account.count_ledger_cursor
                       AND transaction_row.ledger_cursor <= completion.reconciliation_ledger_cursor
                       AND (movement.from_account_id = account.stock_account_id
                            OR movement.to_account_id = account.stock_account_id)), 0)
                OR account.physical_delta_after_count IS NOT COALESCE((
                    SELECT sum(
                        CASE WHEN movement.to_account_id = account.stock_account_id
                             THEN movement.quantity ELSE 0 END
                        - CASE WHEN movement.from_account_id = account.stock_account_id
                               THEN movement.quantity ELSE 0 END)
                      FROM inventory_transactions AS transaction_row
                      JOIN inventory_movements AS movement
                        ON movement.transaction_id = transaction_row.id
                     WHERE transaction_row.status = 'posted'
                       AND transaction_row.ledger_cursor > account.count_ledger_cursor
                       AND transaction_row.ledger_cursor <= completion.reconciliation_ledger_cursor
                       AND NOT EXISTS (
                           SELECT 1 FROM stocktake_posting_completion_items AS own_item
                            WHERE own_item.completion_id = completion.posting_completion_id
                              AND own_item.inventory_transaction_id = transaction_row.id)
                       AND (movement.from_account_id = account.stock_account_id
                            OR movement.to_account_id = account.stock_account_id)), 0))
       OR EXISTS (
        SELECT 1
          FROM stocktake_count_observations AS observation
          JOIN stocktake_posting_completions AS posting
            ON posting.id = completion.posting_completion_id
           AND posting.task_id = NEW.id
          JOIN stocktake_effective_approval_scopes AS selected
            ON selected.completion_id = posting.effective_approval_completion_id
           AND selected.scope_id = observation.scope_id
           AND selected.source_round_id = observation.round_id
         WHERE observation.task_id = NEW.id
           AND (observation.verification_status <> 'verified'
                OR observation.material_id IS NULL
                OR (SELECT count(*)
                      FROM stock_accounts AS stock_account
                     WHERE stock_account.owner_org_id = observation.owner_org_id
                       AND stock_account.location_id = observation.location_id
                       AND stock_account.custodian_person_id IS observation.custodian_person_id_snapshot
                       AND stock_account.material_id = observation.material_id
                       AND stock_account.condition_code = observation.condition_code
                       AND stock_account.availability_bucket = observation.availability_bucket
                       AND stock_account.lot_id IS observation.lot_id
                       AND EXISTS (
                           SELECT 1 FROM {RECONCILIATION_ACCOUNT} AS account
                            WHERE account.completion_id = completion.id
                              AND account.scope_id = observation.scope_id
                              AND account.stock_account_id = stock_account.id)) <> 1))"""
    account_scope_reproof = f"""NOT EXISTS (
        SELECT 1
          FROM {RECONCILIATION_ACCOUNT} AS account
          JOIN stock_accounts AS stock_account
            ON stock_account.id = account.stock_account_id
          JOIN stocktake_posting_completions AS posting
            ON posting.id = completion.posting_completion_id
           AND posting.task_id = NEW.id
         WHERE account.completion_id = completion.id
           AND ((SELECT count(*)
                  FROM stocktake_scopes AS scope
                 WHERE scope.task_id = NEW.id
                   AND scope.owner_org_id = stock_account.owner_org_id
                   AND scope.location_id = stock_account.location_id
                   AND (scope.material_id IS NULL OR
                        scope.material_id = stock_account.material_id)
                   AND (scope.condition_code IS NULL OR
                        scope.condition_code = stock_account.condition_code)
                   AND (scope.availability_bucket IS NULL OR
                        scope.availability_bucket = stock_account.availability_bucket)) > 1
                OR ((SELECT count(*)
                       FROM stocktake_scopes AS scope
                      WHERE scope.task_id = NEW.id
                        AND scope.owner_org_id = stock_account.owner_org_id
                        AND scope.location_id = stock_account.location_id
                        AND (scope.material_id IS NULL OR
                             scope.material_id = stock_account.material_id)
                        AND (scope.condition_code IS NULL OR
                             scope.condition_code = stock_account.condition_code)
                        AND (scope.availability_bucket IS NULL OR
                             scope.availability_bucket = stock_account.availability_bucket)) = 1
                    AND (account.account_role <> 'scope'
                         OR account.scope_id IS NOT (
                             SELECT scope.id FROM stocktake_scopes AS scope
                              WHERE scope.task_id = NEW.id
                                AND scope.owner_org_id = stock_account.owner_org_id
                                AND scope.location_id = stock_account.location_id
                                AND (scope.material_id IS NULL OR
                                     scope.material_id = stock_account.material_id)
                                AND (scope.condition_code IS NULL OR
                                     scope.condition_code = stock_account.condition_code)
                                AND (scope.availability_bucket IS NULL OR
                                     scope.availability_bucket = stock_account.availability_bucket))
                         OR account.effective_round_id IS NOT (
                             SELECT selected.source_round_id
                               FROM stocktake_effective_approval_scopes AS selected
                              WHERE selected.completion_id = posting.effective_approval_completion_id
                                AND selected.scope_id = account.scope_id)
                         OR account.count_ledger_cursor IS NOT (
                             SELECT count_completion.count_ledger_cursor
                               FROM stocktake_scope_count_completions AS count_completion
                              WHERE count_completion.task_id = NEW.id
                                AND count_completion.scope_id = account.scope_id
                                AND count_completion.round_id = account.effective_round_id)))
                OR ((SELECT count(*)
                       FROM stocktake_scopes AS scope
                      WHERE scope.task_id = NEW.id
                        AND scope.owner_org_id = stock_account.owner_org_id
                        AND scope.location_id = stock_account.location_id
                        AND (scope.material_id IS NULL OR
                             scope.material_id = stock_account.material_id)
                        AND (scope.condition_code IS NULL OR
                             scope.condition_code = stock_account.condition_code)
                        AND (scope.availability_bucket IS NULL OR
                             scope.availability_bucket = stock_account.availability_bucket)) = 0
                    AND (account.account_role <> 'posting_counterpart'
                         OR account.scope_id IS NOT NULL
                         OR account.effective_round_id IS NOT NULL))))"""
    serial_physical_reproof = f"""NOT EXISTS (
        SELECT 1
          FROM {RECONCILIATION_SERIAL} AS serial
         WHERE serial.completion_id = completion.id
           AND EXISTS (
            WITH candidate_scopes(scope_id) AS (
                SELECT line.scope_id
                  FROM stocktake_count_serials AS counted_serial
                  JOIN stocktake_count_lines AS line
                    ON line.id = counted_serial.count_line_id
                  JOIN stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting.effective_approval_completion_id
                   AND selected.scope_id = line.scope_id
                   AND selected.source_round_id = line.round_id
                 WHERE line.task_id = NEW.id
                   AND counted_serial.serial_id = serial.serial_id
                UNION
                SELECT observation.scope_id
                  FROM stocktake_count_observations AS observation
                  JOIN stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting.effective_approval_completion_id
                   AND selected.scope_id = observation.scope_id
                   AND selected.source_round_id = observation.round_id
                 WHERE observation.task_id = NEW.id
                   AND observation.serial_id = serial.serial_id
                   AND observation.verification_status = 'verified'
                   AND observation.material_id IS NOT NULL
                UNION
                SELECT scope.id
                  FROM inventory_movement_serials AS binding
                  JOIN inventory_movements AS movement
                    ON movement.id = binding.movement_id
                   AND movement.transaction_id = binding.transaction_id
                  JOIN inventory_transactions AS transaction_row
                    ON transaction_row.id = binding.transaction_id
                  JOIN stock_accounts AS endpoint
                    ON endpoint.id = movement.from_account_id
                    OR endpoint.id = movement.to_account_id
                  JOIN stocktake_scopes AS scope
                    ON scope.task_id = NEW.id
                   AND scope.owner_org_id = endpoint.owner_org_id
                   AND scope.location_id = endpoint.location_id
                   AND (scope.material_id IS NULL OR
                        scope.material_id = endpoint.material_id)
                   AND (scope.condition_code IS NULL OR
                        scope.condition_code = endpoint.condition_code)
                   AND (scope.availability_bucket IS NULL OR
                        scope.availability_bucket = endpoint.availability_bucket)
                  JOIN stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting.effective_approval_completion_id
                   AND selected.scope_id = scope.id
                  JOIN stocktake_scope_count_completions AS count_completion
                    ON count_completion.task_id = NEW.id
                   AND count_completion.scope_id = scope.id
                   AND count_completion.round_id = selected.source_round_id
                 WHERE binding.serial_id = serial.serial_id
                   AND transaction_row.status = 'posted'
                   AND transaction_row.ledger_cursor <=
                       completion.reconciliation_ledger_cursor
                   AND transaction_row.ledger_cursor <=
                       count_completion.count_ledger_cursor
            ),
            physical_evidence(scope_id, stock_account_id) AS (
                SELECT line.scope_id, line.stock_account_id
                  FROM stocktake_count_serials AS counted_serial
                  JOIN stocktake_count_lines AS line
                    ON line.id = counted_serial.count_line_id
                  JOIN stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting.effective_approval_completion_id
                   AND selected.scope_id = line.scope_id
                   AND selected.source_round_id = line.round_id
                 WHERE line.task_id = NEW.id
                   AND counted_serial.serial_id = serial.serial_id
                UNION ALL
                SELECT observation.scope_id, stock_account.id
                  FROM stocktake_count_observations AS observation
                  JOIN stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting.effective_approval_completion_id
                   AND selected.scope_id = observation.scope_id
                   AND selected.source_round_id = observation.round_id
                  JOIN stock_accounts AS stock_account
                    ON stock_account.owner_org_id = observation.owner_org_id
                   AND stock_account.location_id = observation.location_id
                   AND stock_account.custodian_person_id
                       IS observation.custodian_person_id_snapshot
                   AND stock_account.material_id = observation.material_id
                   AND stock_account.condition_code = observation.condition_code
                   AND stock_account.availability_bucket = observation.availability_bucket
                   AND stock_account.lot_id IS observation.lot_id
                  JOIN {RECONCILIATION_ACCOUNT} AS account
                    ON account.completion_id = completion.id
                   AND account.scope_id = observation.scope_id
                   AND account.stock_account_id = stock_account.id
                 WHERE observation.task_id = NEW.id
                   AND observation.serial_id = serial.serial_id
                   AND observation.verification_status = 'verified'
                   AND observation.material_id IS NOT NULL
            ),
            later_movements AS (
                SELECT movement.from_account_id,
                       movement.to_account_id,
                       row_number() OVER (
                           ORDER BY transaction_row.ledger_cursor,
                                    movement.line_no, movement.id) AS movement_no,
                       lag(movement.to_account_id) OVER (
                           ORDER BY transaction_row.ledger_cursor,
                                    movement.line_no, movement.id) AS previous_to_account_id
                  FROM inventory_movement_serials AS binding
                  JOIN inventory_movements AS movement
                    ON movement.id = binding.movement_id
                   AND movement.transaction_id = binding.transaction_id
                  JOIN inventory_transactions AS transaction_row
                    ON transaction_row.id = binding.transaction_id
                 WHERE binding.serial_id = serial.serial_id
                   AND transaction_row.status = 'posted'
                   AND transaction_row.ledger_cursor > serial.count_ledger_cursor
                   AND transaction_row.ledger_cursor <=
                       completion.reconciliation_ledger_cursor
                   AND NOT EXISTS (
                       SELECT 1
                         FROM stocktake_posting_completion_items AS own_item
                        WHERE own_item.completion_id = completion.posting_completion_id
                          AND own_item.inventory_transaction_id = transaction_row.id)
            )
            SELECT 1
             WHERE (SELECT count(*) FROM candidate_scopes) > 1
                OR serial.evidence_scope_id IS NOT (
                    SELECT scope_id FROM candidate_scopes LIMIT 1)
                OR (serial.evidence_scope_id IS NULL AND
                    (serial.effective_round_id IS NOT NULL
                     OR serial.count_ledger_cursor IS NOT NULL
                     OR serial.physical_present_at_count IS NOT NULL
                     OR serial.physical_account_id_at_count IS NOT NULL))
                OR (serial.evidence_scope_id IS NOT NULL AND
                    (serial.effective_round_id IS NOT (
                         SELECT selected.source_round_id
                           FROM stocktake_effective_approval_scopes AS selected
                          WHERE selected.completion_id = posting.effective_approval_completion_id
                            AND selected.scope_id = serial.evidence_scope_id)
                     OR serial.count_ledger_cursor IS NOT (
                         SELECT count_completion.count_ledger_cursor
                           FROM stocktake_scope_count_completions AS count_completion
                          WHERE count_completion.task_id = NEW.id
                            AND count_completion.scope_id = serial.evidence_scope_id
                            AND count_completion.round_id = serial.effective_round_id)
                     OR (SELECT count(*) FROM physical_evidence) > 1
                     OR EXISTS (
                         SELECT 1 FROM physical_evidence AS evidence
                          WHERE evidence.scope_id IS NOT serial.evidence_scope_id)
                     OR COALESCE(CAST(serial.physical_present_at_count AS INTEGER), -1) <>
                        CASE WHEN (SELECT count(*) FROM physical_evidence) = 1
                             THEN 1 ELSE 0 END
                     OR serial.physical_account_id_at_count IS NOT
                        CASE WHEN (SELECT count(*) FROM physical_evidence) = 1
                             THEN (SELECT stock_account_id FROM physical_evidence LIMIT 1)
                             ELSE NULL END
                     OR EXISTS (
                         SELECT 1 FROM later_movements AS movement
                          WHERE movement.from_account_id IS NOT
                                CASE WHEN movement.movement_no = 1
                                     THEN serial.physical_account_id_at_count
                                     ELSE movement.previous_to_account_id END)
                     OR serial.expected_current_account_id IS NOT
                        CASE WHEN EXISTS (SELECT 1 FROM later_movements)
                             THEN (SELECT movement.to_account_id
                                     FROM later_movements AS movement
                                    ORDER BY movement.movement_no DESC LIMIT 1)
                             ELSE serial.physical_account_id_at_count END))
        ))"""
    reconciliation_authorization = """EXISTS (
        SELECT 1
          FROM users AS actor_user
          JOIN people AS actor_person
            ON actor_person.id = actor_user.person_id
          JOIN organizations AS actor_org
            ON actor_org.id = actor_person.organization_id
          JOIN role_assignments AS assignment
            ON assignment.id = completion.reconciled_role_assignment_id
           AND assignment.user_id = actor_user.id
          JOIN roles AS actor_role
            ON actor_role.id = assignment.role_id
          JOIN role_permissions AS role_permission
            ON role_permission.role_id = actor_role.id
           AND role_permission.effect = 'allow'
          JOIN permissions AS permission
            ON permission.id = role_permission.permission_id
         WHERE actor_user.id = completion.reconciled_by_user_id
           AND actor_user.person_id = completion.reconciled_by_person_id
           AND actor_user.account_status = 'active'
           AND actor_user.is_active = 1
           AND actor_user.authorization_version = completion.authorization_version
           AND EXISTS (
               SELECT 1 FROM auth_identities AS identity
                WHERE identity.user_id = actor_user.id
                  AND identity.status = 'active'
                  AND identity.verified_at IS NOT NULL
                  AND identity.revoked_at IS NULL)
           AND actor_person.employment_status = 'active'
           AND actor_org.status = 'active'
           AND actor_org.org_type = 'headquarters'
           AND actor_role.code = 'admin'
           AND actor_role.status = 'active'
           AND actor_role.is_external = 0
           AND assignment.scope_type = 'national'
           AND assignment.scope_id = '*'
           AND assignment.status = 'active'
           AND assignment.revoked_at IS NULL
           AND assignment.valid_from <= completion.reconciled_at
           AND (assignment.valid_to IS NULL OR assignment.valid_to > completion.reconciled_at)
           AND (SELECT count(*)
                  FROM role_assignments AS current_assignment
                  JOIN roles AS effective_role
                    ON effective_role.id = current_assignment.role_id
                   AND effective_role.status = 'active'
                 WHERE current_assignment.user_id = actor_user.id
                   AND current_assignment.status IN ('scheduled', 'active')
                   AND current_assignment.revoked_at IS NULL
                   AND current_assignment.valid_from <= completion.reconciled_at
                   AND (current_assignment.valid_to IS NULL OR
                        current_assignment.valid_to > completion.reconciled_at)
                   AND effective_role.code = 'admin'
                   AND current_assignment.scope_type = 'national'
                   AND current_assignment.scope_id = '*') = 1
           AND permission.resource = 'stocktake'
           AND permission.action = 'reconcile'
           AND permission.field_code = ''
           AND NOT EXISTS (
               SELECT 1
                 FROM role_assignments AS deny_assignment
                 JOIN roles AS deny_role
                   ON deny_role.id = deny_assignment.role_id
                  AND deny_role.status = 'active'
                 JOIN role_permissions AS deny_binding
                   ON deny_binding.role_id = deny_role.id
                  AND deny_binding.effect = 'deny'
                 JOIN permissions AS denied_permission
                   ON denied_permission.id = deny_binding.permission_id
                WHERE deny_assignment.user_id = actor_user.id
                  AND deny_assignment.status IN ('scheduled', 'active')
                  AND deny_assignment.revoked_at IS NULL
                  AND deny_assignment.valid_from <= completion.reconciled_at
                  AND (deny_assignment.valid_to IS NULL OR
                       deny_assignment.valid_to > completion.reconciled_at)
                  AND deny_assignment.scope_type = 'national'
                  AND deny_assignment.scope_id = '*'
                  AND denied_permission.resource = 'stocktake'
                  AND denied_permission.action = 'reconcile'
                  AND denied_permission.field_code = ''))"""
    close_authorization = """EXISTS (
        SELECT 1
          FROM users AS actor_user
          JOIN people AS actor_person
            ON actor_person.id = actor_user.person_id
          JOIN organizations AS actor_org
            ON actor_org.id = actor_person.organization_id
          JOIN role_assignments AS assignment
            ON assignment.id = close_row.closed_role_assignment_id
           AND assignment.user_id = actor_user.id
          JOIN roles AS actor_role
            ON actor_role.id = assignment.role_id
          JOIN role_permissions AS role_permission
            ON role_permission.role_id = actor_role.id
           AND role_permission.effect = 'allow'
          JOIN permissions AS permission
            ON permission.id = role_permission.permission_id
         WHERE actor_user.id = close_row.closed_by_user_id
           AND actor_user.person_id = close_row.closed_by_person_id
           AND actor_user.account_status = 'active'
           AND actor_user.is_active = 1
           AND actor_user.authorization_version = close_row.authorization_version
           AND EXISTS (
               SELECT 1 FROM auth_identities AS identity
                WHERE identity.user_id = actor_user.id
                  AND identity.status = 'active'
                  AND identity.verified_at IS NOT NULL
                  AND identity.revoked_at IS NULL)
           AND actor_person.employment_status = 'active'
           AND actor_org.status = 'active'
           AND actor_org.org_type = 'headquarters'
           AND actor_role.code = 'admin'
           AND actor_role.status = 'active'
           AND actor_role.is_external = 0
           AND assignment.scope_type = 'national'
           AND assignment.scope_id = '*'
           AND assignment.status = 'active'
           AND assignment.revoked_at IS NULL
           AND assignment.valid_from <= close_row.closed_at
           AND (assignment.valid_to IS NULL OR assignment.valid_to > close_row.closed_at)
           AND (SELECT count(*)
                  FROM role_assignments AS current_assignment
                  JOIN roles AS effective_role
                    ON effective_role.id = current_assignment.role_id
                   AND effective_role.status = 'active'
                 WHERE current_assignment.user_id = actor_user.id
                   AND current_assignment.status IN ('scheduled', 'active')
                   AND current_assignment.revoked_at IS NULL
                   AND current_assignment.valid_from <= close_row.closed_at
                   AND (current_assignment.valid_to IS NULL OR
                        current_assignment.valid_to > close_row.closed_at)
                   AND effective_role.code = 'admin'
                   AND current_assignment.scope_type = 'national'
                   AND current_assignment.scope_id = '*') = 1
           AND permission.resource = 'stocktake'
           AND permission.action = 'close'
           AND permission.field_code = ''
           AND NOT EXISTS (
               SELECT 1
                 FROM role_assignments AS deny_assignment
                 JOIN roles AS deny_role
                   ON deny_role.id = deny_assignment.role_id
                  AND deny_role.status = 'active'
                 JOIN role_permissions AS deny_binding
                   ON deny_binding.role_id = deny_role.id
                  AND deny_binding.effect = 'deny'
                 JOIN permissions AS denied_permission
                   ON denied_permission.id = deny_binding.permission_id
                WHERE deny_assignment.user_id = actor_user.id
                  AND deny_assignment.status IN ('scheduled', 'active')
                  AND deny_assignment.revoked_at IS NULL
                  AND deny_assignment.valid_from <= close_row.closed_at
                  AND (deny_assignment.valid_to IS NULL OR
                       deny_assignment.valid_to > close_row.closed_at)
                  AND deny_assignment.scope_type = 'national'
                  AND deny_assignment.scope_id = '*'
                  AND denied_permission.resource = 'stocktake'
                  AND denied_permission.action = 'close'
                  AND denied_permission.field_code = ''))"""
    reconciliation_audit = """(SELECT count(*)
        FROM audit_events AS event
       WHERE event.stream_key = 'inventory'
         AND event.action = 'stocktake.nonopening.close_reconciliation_recorded'
         AND event.aggregate_type = 'stocktake_close_reconciliation'
         AND replace(lower(event.aggregate_id), '-', '') =
             replace(lower(CAST(completion.id AS TEXT)), '-', '')
         AND event.actor_user_id = completion.reconciled_by_user_id
         AND event.occurred_at = completion.reconciled_at
         AND json_valid(event.before_jsonb)
         AND json_type(event.before_jsonb) = 'object'
         AND (SELECT count(*) FROM json_each(event.before_jsonb)) = 2
         AND json_extract(event.before_jsonb, '$.status') = 'posted'
         AND json_extract(event.before_jsonb, '$.task_version') =
             completion.expected_task_version
         AND json_valid(event.after_jsonb)
         AND json_type(event.after_jsonb) = 'object'
         AND (SELECT count(*) FROM json_each(event.after_jsonb)) = 13
         AND json_extract(event.after_jsonb, '$.account_count') = completion.account_count
         AND json_extract(event.after_jsonb, '$.book_total_qty') =
             printf('%.3f', completion.book_total_qty)
         AND replace(lower(json_extract(event.after_jsonb, '$.completion_id')), '-', '') =
             replace(lower(CAST(completion.id AS TEXT)), '-', '')
         AND json_extract(event.after_jsonb, '$.physical_total_qty') =
             printf('%.3f', completion.physical_total_qty)
         AND replace(lower(json_extract(event.after_jsonb, '$.posting_completion_id')), '-', '') =
             replace(lower(CAST(completion.posting_completion_id AS TEXT)), '-', '')
         AND json_extract(event.after_jsonb, '$.reconciled_task_version') =
             completion.reconciled_task_version
         AND json_extract(event.after_jsonb, '$.reconciliation_ledger_cursor') =
             completion.reconciliation_ledger_cursor
         AND json_extract(event.after_jsonb, '$.reconciliation_manifest_sha256') =
             completion.reconciliation_manifest_sha256
         AND json_extract(event.after_jsonb, '$.reconciliation_no') =
             completion.reconciliation_no
         AND json_extract(event.after_jsonb, '$.schema') =
             'cloud_oam.stocktake.nonopening_close_reconciled_event.v1'
         AND json_extract(event.after_jsonb, '$.serial_count') = completion.serial_count
         AND json_extract(event.after_jsonb, '$.status') = 'posted'
         AND replace(lower(json_extract(event.after_jsonb, '$.task_id')), '-', '') =
             replace(lower(CAST(completion.task_id AS TEXT)), '-', '')
         AND event.request_id LIKE 'stocktake-close-reconcile-request-%'
         AND length(event.request_id) =
             length('stocktake-close-reconcile-request-') + 64
         AND substr(event.request_id,
                    length('stocktake-close-reconcile-request-') + 1)
             NOT GLOB '*[^0-9a-f]*') = 1"""
    close_state = """(SELECT count(*)
        FROM state_transition_events AS event
       WHERE event.aggregate_type = 'stocktake_task'
         AND replace(lower(event.aggregate_id), '-', '') =
             replace(lower(CAST(NEW.id AS TEXT)), '-', '')
         AND event.from_status = 'posted'
         AND event.to_status = 'closed'
         AND event.reason = 'nonopening_stocktake_closed_after_internal_reconciliation'
         AND event.actor_id = close_row.closed_by_user_id
         AND event.occurred_at = close_row.closed_at
         AND json_valid(event.metadata_jsonb)
         AND json_type(event.metadata_jsonb) = 'object'
         AND (SELECT count(*) FROM json_each(event.metadata_jsonb)) = 11
         AND replace(lower(json_extract(event.metadata_jsonb, '$.close_completion_id')), '-', '') =
             replace(lower(CAST(close_row.id AS TEXT)), '-', '')
         AND json_extract(event.metadata_jsonb, '$.close_manifest_sha256') =
             close_row.close_manifest_sha256
         AND json_extract(event.metadata_jsonb, '$.closed_task_version') =
             close_row.closed_task_version
         AND replace(lower(json_extract(event.metadata_jsonb, '$.posting_completion_id')), '-', '') =
             replace(lower(CAST(close_row.posting_completion_id AS TEXT)), '-', '')
         AND replace(lower(json_extract(event.metadata_jsonb, '$.reconciliation_completion_id')), '-', '') =
             replace(lower(CAST(close_row.reconciliation_completion_id AS TEXT)), '-', '')
         AND json_extract(event.metadata_jsonb, '$.reconciliation_ledger_cursor') =
             close_row.reconciliation_ledger_cursor
         AND json_extract(event.metadata_jsonb, '$.reconciliation_manifest_sha256') =
             close_row.reconciliation_manifest_sha256
         AND json_extract(event.metadata_jsonb, '$.reconciliation_no') =
             close_row.reconciliation_no
         AND json_extract(event.metadata_jsonb, '$.schema') =
             'cloud_oam.stocktake.nonopening_closed_event.v1'
         AND json_extract(event.metadata_jsonb, '$.status') = 'closed'
         AND replace(lower(json_extract(event.metadata_jsonb, '$.task_id')), '-', '') =
             replace(lower(CAST(close_row.task_id AS TEXT)), '-', '')
         AND event.idempotency_key LIKE 'stocktake-close-close-state-%'
         AND length(event.idempotency_key) =
             length('stocktake-close-close-state-') + 64
         AND substr(event.idempotency_key,
                    length('stocktake-close-close-state-') + 1)
             NOT GLOB '*[^0-9a-f]*') = 1"""
    close_audit = """(SELECT count(*)
        FROM audit_events AS event
       WHERE event.stream_key = 'inventory'
         AND event.action = 'stocktake.nonopening.closed'
         AND event.aggregate_type = 'stocktake_close_completion'
         AND replace(lower(event.aggregate_id), '-', '') =
             replace(lower(CAST(close_row.id AS TEXT)), '-', '')
         AND event.actor_user_id = close_row.closed_by_user_id
         AND event.occurred_at = close_row.closed_at
         AND json_valid(event.before_jsonb)
         AND json_type(event.before_jsonb) = 'object'
         AND (SELECT count(*) FROM json_each(event.before_jsonb)) = 2
         AND json_extract(event.before_jsonb, '$.status') = 'posted'
         AND json_extract(event.before_jsonb, '$.task_version') =
             close_row.expected_task_version
         AND json_valid(event.after_jsonb)
         AND json_type(event.after_jsonb) = 'object'
         AND (SELECT count(*) FROM json_each(event.after_jsonb)) = 11
         AND replace(lower(json_extract(event.after_jsonb, '$.close_completion_id')), '-', '') =
             replace(lower(CAST(close_row.id AS TEXT)), '-', '')
         AND json_extract(event.after_jsonb, '$.close_manifest_sha256') =
             close_row.close_manifest_sha256
         AND json_extract(event.after_jsonb, '$.closed_task_version') =
             close_row.closed_task_version
         AND replace(lower(json_extract(event.after_jsonb, '$.posting_completion_id')), '-', '') =
             replace(lower(CAST(close_row.posting_completion_id AS TEXT)), '-', '')
         AND replace(lower(json_extract(event.after_jsonb, '$.reconciliation_completion_id')), '-', '') =
             replace(lower(CAST(close_row.reconciliation_completion_id AS TEXT)), '-', '')
         AND json_extract(event.after_jsonb, '$.reconciliation_ledger_cursor') =
             close_row.reconciliation_ledger_cursor
         AND json_extract(event.after_jsonb, '$.reconciliation_manifest_sha256') =
             close_row.reconciliation_manifest_sha256
         AND json_extract(event.after_jsonb, '$.reconciliation_no') =
             close_row.reconciliation_no
         AND json_extract(event.after_jsonb, '$.schema') =
             'cloud_oam.stocktake.nonopening_closed_event.v1'
         AND json_extract(event.after_jsonb, '$.status') = 'closed'
         AND replace(lower(json_extract(event.after_jsonb, '$.task_id')), '-', '') =
             replace(lower(CAST(close_row.task_id AS TEXT)), '-', '')
         AND event.request_id LIKE 'stocktake-close-close-request-%'
         AND length(event.request_id) =
             length('stocktake-close-close-request-') + 64
         AND substr(event.request_id,
                    length('stocktake-close-close-request-') + 1)
             NOT GLOB '*[^0-9a-f]*') = 1"""
    return f"""
CREATE TRIGGER {SQLITE_TASK_TERMINAL}
BEFORE UPDATE ON stocktake_tasks
WHEN OLD.task_type IN {NONOPENING_SQL} AND OLD.status IN ('posted', 'closed')
 AND NOT (
    OLD.status = 'posted' AND NEW.status = 'posted'
    AND NEW.version = OLD.version + 1 AND NEW.closed_at IS NULL
    AND {unchanged}
    AND EXISTS (
        SELECT 1 FROM {RECONCILIATION} AS completion
        JOIN stocktake_posting_completions AS posting
          ON posting.id = completion.posting_completion_id
         AND posting.task_id = NEW.id
         WHERE completion.task_id = NEW.id
           AND completion.expected_task_version = OLD.version
           AND completion.reconciled_task_version = NEW.version
           AND completion.reconciled_at = NEW.updated_at
           AND completion.scope_count =
               (SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.id)
           AND completion.account_count =
               (SELECT count(*) FROM {RECONCILIATION_ACCOUNT}
                 WHERE completion_id = completion.id)
           AND completion.scoped_account_count =
               (SELECT count(*) FROM {RECONCILIATION_ACCOUNT}
                 WHERE completion_id = completion.id AND scope_id IS NOT NULL)
           AND completion.serial_count =
               (SELECT count(*) FROM {RECONCILIATION_SERIAL}
                 WHERE completion_id = completion.id)
           AND completion.book_total_qty =
               (SELECT COALESCE(sum(ledger_qty), 0) FROM {RECONCILIATION_ACCOUNT}
                 WHERE completion_id = completion.id AND scope_id IS NOT NULL)
           AND completion.physical_total_qty = completion.book_total_qty
           AND {ledger_counts}
           AND {exact_coordinate_union}
           AND {account_scope_reproof}
           AND {account_physical_reproof}
           AND {serial_physical_reproof}
           AND {reconciliation_authorization}
           AND completion.reconciliation_ledger_cursor =
               (SELECT next_cursor - 1 FROM inventory_ledger_heads WHERE stream_key = 'inventory')
           AND NOT EXISTS (
               SELECT 1 FROM {RECONCILIATION_ACCOUNT} AS account
                LEFT JOIN stock_balances AS balance
                  ON balance.stock_account_id = account.stock_account_id
               WHERE account.completion_id = completion.id
                 AND (COALESCE(balance.quantity, 0) <> account.balance_qty
                      OR NOT (balance.ledger_cursor IS account.balance_ledger_cursor)
                      OR NOT ({account_ledger})))
           AND NOT EXISTS (
               SELECT 1 FROM {RECONCILIATION_SERIAL} AS serial
                LEFT JOIN serial_current_positions AS position
                  ON position.serial_id = serial.serial_id
               WHERE serial.completion_id = completion.id
                 AND (position.last_movement_id IS NULL
                      OR position.last_movement_id <> serial.current_position_last_movement_id
                      OR NOT (position.stock_account_id IS serial.current_position_account_id)))
           AND {reconciliation_audit}
           AND NOT EXISTS (SELECT 1 FROM {CLOSE_COMPLETION} WHERE task_id = NEW.id)
    )
    OR (
    OLD.status = 'posted' AND NEW.status = 'closed'
    AND NEW.version = OLD.version + 1 AND NEW.closed_at = NEW.updated_at
    AND {unchanged}
    AND EXISTS (
        SELECT 1 FROM {CLOSE_COMPLETION} AS close_row
        JOIN {RECONCILIATION} AS completion
          ON completion.id = close_row.reconciliation_completion_id
         AND completion.task_id = NEW.id
        JOIN stocktake_posting_completions AS posting
          ON posting.id = close_row.posting_completion_id
         AND posting.task_id = NEW.id
         WHERE close_row.task_id = NEW.id
           AND close_row.expected_task_version = OLD.version
           AND close_row.closed_task_version = NEW.version
           AND close_row.closed_at = NEW.closed_at
           AND completion.reconciled_task_version = OLD.version
           AND completion.reconciliation_no = close_row.reconciliation_no
           AND completion.reconciliation_ledger_cursor = close_row.reconciliation_ledger_cursor
           AND completion.posting_completion_id = posting.id
           AND completion.reconciliation_ledger_cursor =
               (SELECT next_cursor - 1 FROM inventory_ledger_heads WHERE stream_key = 'inventory')
           AND {ledger_counts}
           AND {exact_coordinate_union}
           AND {account_scope_reproof}
           AND {account_physical_reproof}
           AND {serial_physical_reproof}
           AND {close_authorization}
           AND NOT EXISTS (
               SELECT 1 FROM {RECONCILIATION} AS later
                WHERE later.task_id = NEW.id
                  AND later.reconciliation_no > completion.reconciliation_no)
           AND NOT EXISTS (
               SELECT 1 FROM {RECONCILIATION_ACCOUNT} AS account
                LEFT JOIN stock_balances AS balance
                  ON balance.stock_account_id = account.stock_account_id
               WHERE account.completion_id = completion.id
                 AND (COALESCE(balance.quantity, 0) <> account.balance_qty
                      OR NOT (balance.ledger_cursor IS account.balance_ledger_cursor)
                      OR NOT ({account_ledger})))
           AND NOT EXISTS (
               SELECT 1 FROM {RECONCILIATION_SERIAL} AS serial
                LEFT JOIN serial_current_positions AS position
                  ON position.serial_id = serial.serial_id
               WHERE serial.completion_id = completion.id
                 AND (position.last_movement_id IS NULL
                      OR position.last_movement_id <> serial.current_position_last_movement_id
                      OR NOT (position.stock_account_id IS serial.current_position_account_id)))
           AND {close_state}
           AND {close_audit}
    )))
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake reconciliation/close transition is invalid');
END
"""


def _sqlite_task_ack_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_TASK_ACK}
AFTER UPDATE OF status, version, updated_at, closed_at ON stocktake_tasks
WHEN OLD.task_type IN {NONOPENING_SQL} AND OLD.status = 'posted'
 AND ((NEW.status = 'posted' AND NEW.version = OLD.version + 1)
      OR (NEW.status = 'closed' AND NEW.version = OLD.version + 1))
BEGIN
    INSERT INTO {TRANSITION_ACK} (
        task_id,
        target_task_version,
        transition_kind,
        reconciliation_completion_id,
        close_completion_id,
        occurred_at,
        created_at
    )
    SELECT NEW.id, NEW.version, 'reconciliation', completion.id, NULL,
           completion.reconciled_at, completion.reconciled_at
      FROM {RECONCILIATION} AS completion
     WHERE NEW.status = 'posted'
       AND completion.task_id = NEW.id
       AND completion.expected_task_version = OLD.version
       AND completion.reconciled_task_version = NEW.version
       AND completion.reconciled_at = NEW.updated_at
    UNION ALL
    SELECT NEW.id, NEW.version, 'close', NULL, completion.id,
           completion.closed_at, completion.closed_at
      FROM {CLOSE_COMPLETION} AS completion
     WHERE NEW.status = 'closed'
       AND completion.task_id = NEW.id
       AND completion.expected_task_version = OLD.version
       AND completion.closed_task_version = NEW.version
       AND completion.closed_at = NEW.closed_at;
    SELECT RAISE(ABORT, 'stocktake close transition ack was not recorded')
     WHERE changes() <> 1;
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
BEGIN
    IF requested_task_id IS NULL THEN
        RAISE EXCEPTION 'non-opening stocktake close lock graph invariant violated';
    END IF;
    PERFORM public.rsc_lock_nonopening_stocktake_posting_graph_0035(requested_task_id);
    SELECT (SELECT count(*) FROM public.{RECONCILIATION} WHERE task_id = requested_task_id)
         + (SELECT count(*) FROM public.{RECONCILIATION_ACCOUNT} WHERE task_id = requested_task_id)
         + (SELECT count(*) FROM public.{RECONCILIATION_SERIAL} WHERE task_id = requested_task_id)
         + (SELECT count(*) FROM public.{CLOSE_COMPLETION} WHERE task_id = requested_task_id)
         + (SELECT count(*) FROM public.{TRANSITION_ACK} WHERE task_id = requested_task_id)
      INTO graph_count;
    IF graph_count > 100000 THEN
        RAISE EXCEPTION 'non-opening stocktake close lock graph invariant violated';
    END IF;
    PERFORM id FROM public.{RECONCILIATION}
     WHERE task_id = requested_task_id ORDER BY reconciliation_no, id FOR UPDATE;
    PERFORM completion_id FROM public.{RECONCILIATION_ACCOUNT}
     WHERE task_id = requested_task_id ORDER BY completion_id, stock_account_id FOR UPDATE;
    PERFORM completion_id FROM public.{RECONCILIATION_SERIAL}
     WHERE task_id = requested_task_id ORDER BY completion_id, serial_id FOR UPDATE;
    PERFORM id FROM public.{CLOSE_COMPLETION}
     WHERE task_id = requested_task_id ORDER BY id FOR UPDATE;
    PERFORM target_task_version FROM public.{TRANSITION_ACK}
     WHERE task_id = requested_task_id ORDER BY target_task_version FOR UPDATE;
END
$$
"""


def _postgresql_immutable_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION 'non-opening stocktake close facts are immutable';
END
$$
"""


def _postgresql_ack_guard_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_ACK_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.transition_kind = 'reconciliation' AND EXISTS (
        SELECT 1 FROM public.stocktake_tasks AS task
        JOIN public.{RECONCILIATION} AS completion
          ON completion.id = NEW.reconciliation_completion_id
         AND completion.task_id = task.id
         WHERE task.id = NEW.task_id
           AND task.task_type IN {NONOPENING_SQL}
           AND task.status = 'posted' AND task.closed_at IS NULL
           AND task.version = NEW.target_task_version
           AND completion.reconciled_task_version = task.version
           AND completion.expected_task_version = task.version - 1
           AND completion.reconciled_at = task.updated_at
           AND NEW.close_completion_id IS NULL
           AND NEW.occurred_at = completion.reconciled_at
           AND NEW.created_at = NEW.occurred_at
    ) THEN
        RETURN NEW;
    END IF;
    IF NEW.transition_kind = 'close' AND EXISTS (
        SELECT 1 FROM public.stocktake_tasks AS task
        JOIN public.{CLOSE_COMPLETION} AS completion
          ON completion.id = NEW.close_completion_id
         AND completion.task_id = task.id
         WHERE task.id = NEW.task_id
           AND task.task_type IN {NONOPENING_SQL}
           AND task.status = 'closed'
           AND task.version = NEW.target_task_version
           AND completion.closed_task_version = task.version
           AND completion.expected_task_version = task.version - 1
           AND completion.closed_at = task.closed_at
           AND task.updated_at = task.closed_at
           AND NEW.reconciliation_completion_id IS NULL
           AND NEW.occurred_at = completion.closed_at
           AND NEW.created_at = NEW.occurred_at
    ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'stocktake close transition ack is trigger-only';
END
$$
"""


def _postgresql_ack_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_ACK_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    inserted_count bigint;
BEGIN
    IF OLD.task_type NOT IN {NONOPENING_SQL} OR OLD.status <> 'posted' THEN
        RETURN NEW;
    END IF;
    IF NEW.status = 'posted' AND NEW.version = OLD.version + 1 THEN
        INSERT INTO public.{TRANSITION_ACK} (
            task_id, target_task_version, transition_kind,
            reconciliation_completion_id, close_completion_id,
            occurred_at, created_at
        )
        SELECT NEW.id, NEW.version, 'reconciliation', completion.id, NULL,
               completion.reconciled_at, completion.reconciled_at
          FROM public.{RECONCILIATION} AS completion
         WHERE completion.task_id = NEW.id
           AND completion.expected_task_version = OLD.version
           AND completion.reconciled_task_version = NEW.version
           AND completion.reconciled_at = NEW.updated_at;
    ELSIF NEW.status = 'closed' AND NEW.version = OLD.version + 1 THEN
        INSERT INTO public.{TRANSITION_ACK} (
            task_id, target_task_version, transition_kind,
            reconciliation_completion_id, close_completion_id,
            occurred_at, created_at
        )
        SELECT NEW.id, NEW.version, 'close', NULL, completion.id,
               completion.closed_at, completion.closed_at
          FROM public.{CLOSE_COMPLETION} AS completion
         WHERE completion.task_id = NEW.id
           AND completion.expected_task_version = OLD.version
           AND completion.closed_task_version = NEW.version
           AND completion.closed_at = NEW.closed_at;
    ELSE
        RETURN NEW;
    END IF;
    GET DIAGNOSTICS inserted_count = ROW_COUNT;
    IF inserted_count <> 1 THEN
        RAISE EXCEPTION 'stocktake close transition ack was not recorded';
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_event_guard_function_sql() -> str:
    reconciliation_document = f"""jsonb_build_object(
        'account_count', reconciliation_row.account_count,
        'book_total_qty',
            {_postgresql_quantity_text('reconciliation_row.book_total_qty')},
        'completion_id', reconciliation_row.id::text,
        'physical_total_qty',
            {_postgresql_quantity_text('reconciliation_row.physical_total_qty')},
        'posting_completion_id', reconciliation_row.posting_completion_id::text,
        'reconciled_task_version', reconciliation_row.reconciled_task_version,
        'reconciliation_ledger_cursor',
            reconciliation_row.reconciliation_ledger_cursor,
        'reconciliation_manifest_sha256',
            reconciliation_row.reconciliation_manifest_sha256,
        'reconciliation_no', reconciliation_row.reconciliation_no,
        'schema', 'cloud_oam.stocktake.nonopening_close_reconciled_event.v1',
        'serial_count', reconciliation_row.serial_count,
        'status', 'posted',
        'task_id', reconciliation_row.task_id::text)"""
    close_document = """jsonb_build_object(
        'close_completion_id', close_row.id::text,
        'close_manifest_sha256', close_row.close_manifest_sha256,
        'closed_task_version', close_row.closed_task_version,
        'posting_completion_id', close_row.posting_completion_id::text,
        'reconciliation_completion_id', close_row.reconciliation_completion_id::text,
        'reconciliation_ledger_cursor', close_row.reconciliation_ledger_cursor,
        'reconciliation_manifest_sha256', close_row.reconciliation_manifest_sha256,
        'reconciliation_no', close_row.reconciliation_no,
        'schema', 'cloud_oam.stocktake.nonopening_closed_event.v1',
        'status', 'closed',
        'task_id', close_row.task_id::text)"""
    return f"""
CREATE FUNCTION public.{PG_EVENT_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    reconciliation_row public.{RECONCILIATION}%ROWTYPE;
    close_row public.{CLOSE_COMPLETION}%ROWTYPE;
    existing_coordinate_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'audit_events' THEN
        IF NEW.action = 'stocktake.nonopening.close_reconciliation_recorded'
           OR NEW.aggregate_type = 'stocktake_close_reconciliation' THEN
            IF NEW.action <> 'stocktake.nonopening.close_reconciliation_recorded'
               OR NEW.aggregate_type <> 'stocktake_close_reconciliation' THEN
                RAISE EXCEPTION 'non-opening stocktake reconciliation audit coordinate is reserved';
            END IF;
            SELECT * INTO reconciliation_row
              FROM public.{RECONCILIATION}
             WHERE id::text = NEW.aggregate_id
             FOR UPDATE;
            SELECT count(*) INTO existing_coordinate_count
              FROM public.audit_events AS event
             WHERE event.stream_key = 'inventory'
               AND event.action = 'stocktake.nonopening.close_reconciliation_recorded'
               AND event.aggregate_type = 'stocktake_close_reconciliation'
               AND event.aggregate_id = NEW.aggregate_id;
            IF reconciliation_row.id IS NULL
               OR existing_coordinate_count <> 0
               OR NEW.stream_key <> 'inventory'
               OR NEW.actor_user_id IS DISTINCT FROM
                    reconciliation_row.reconciled_by_user_id
               OR NEW.occurred_at IS DISTINCT FROM reconciliation_row.reconciled_at
               OR NEW.before_jsonb IS DISTINCT FROM jsonb_build_object(
                    'status', 'posted',
                    'task_version', reconciliation_row.expected_task_version)
               OR NEW.after_jsonb IS DISTINCT FROM {reconciliation_document}
               OR NEW.request_id !~
                    '^stocktake-close-reconcile-request-[0-9a-f]{{64}}$' THEN
                RAISE EXCEPTION 'non-opening stocktake reconciliation audit event is invalid';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.action = 'stocktake.nonopening.closed'
           OR NEW.aggregate_type = 'stocktake_close_completion' THEN
            IF NEW.action <> 'stocktake.nonopening.closed'
               OR NEW.aggregate_type <> 'stocktake_close_completion' THEN
                RAISE EXCEPTION 'non-opening stocktake close audit coordinate is reserved';
            END IF;
            SELECT * INTO close_row
              FROM public.{CLOSE_COMPLETION}
             WHERE id::text = NEW.aggregate_id
             FOR UPDATE;
            SELECT count(*) INTO existing_coordinate_count
              FROM public.audit_events AS event
             WHERE event.stream_key = 'inventory'
               AND event.action = 'stocktake.nonopening.closed'
               AND event.aggregate_type = 'stocktake_close_completion'
               AND event.aggregate_id = NEW.aggregate_id;
            IF close_row.id IS NULL
               OR existing_coordinate_count <> 0
               OR NEW.stream_key <> 'inventory'
               OR NEW.actor_user_id IS DISTINCT FROM close_row.closed_by_user_id
               OR NEW.occurred_at IS DISTINCT FROM close_row.closed_at
               OR NEW.before_jsonb IS DISTINCT FROM jsonb_build_object(
                    'status', 'posted',
                    'task_version', close_row.expected_task_version)
               OR NEW.after_jsonb IS DISTINCT FROM {close_document}
               OR NEW.request_id !~
                    '^stocktake-close-close-request-[0-9a-f]{{64}}$' THEN
                RAISE EXCEPTION 'non-opening stocktake close audit event is invalid';
            END IF;
            RETURN NEW;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'state_transition_events' THEN
        IF NEW.reason <>
           'nonopening_stocktake_closed_after_internal_reconciliation' THEN
            RETURN NEW;
        END IF;
        SELECT * INTO close_row
          FROM public.{CLOSE_COMPLETION}
         WHERE id::text = NEW.metadata_jsonb ->> 'close_completion_id'
           AND task_id::text = NEW.aggregate_id
         FOR UPDATE;
        SELECT count(*) INTO existing_coordinate_count
          FROM public.state_transition_events AS event
         WHERE event.aggregate_type = 'stocktake_task'
           AND event.aggregate_id = NEW.aggregate_id
           AND event.reason =
               'nonopening_stocktake_closed_after_internal_reconciliation';
        IF close_row.id IS NULL
           OR existing_coordinate_count <> 0
           OR NEW.aggregate_type <> 'stocktake_task'
           OR NEW.from_status IS DISTINCT FROM 'posted'
           OR NEW.to_status <> 'closed'
           OR NEW.actor_id IS DISTINCT FROM close_row.closed_by_user_id
           OR NEW.occurred_at IS DISTINCT FROM close_row.closed_at
           OR NEW.metadata_jsonb IS DISTINCT FROM {close_document}
           OR NEW.idempotency_key <>
                'stocktake-close-close-state-' || encode(sha256(convert_to(
                    'close-state' || close_row.id::text, 'UTF8')), 'hex') THEN
            RAISE EXCEPTION 'non-opening stocktake close state event is invalid';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'non-opening stocktake close event guard table is invalid';
END
$$
"""


def _create_postgresql_event_guard_triggers() -> None:
    op.execute(
        f"CREATE TRIGGER {PG_AUDIT_EVENT_GUARD_TRIGGER} BEFORE INSERT "
        "ON public.audit_events FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_EVENT_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {PG_STATE_EVENT_GUARD_TRIGGER} BEFORE INSERT "
        "ON public.state_transition_events FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_EVENT_GUARD_FUNCTION}()"
    )
    op.execute(
        "ALTER TABLE public.audit_events ENABLE ALWAYS TRIGGER "
        f"{PG_AUDIT_EVENT_GUARD_TRIGGER}"
    )
    op.execute(
        "ALTER TABLE public.state_transition_events ENABLE ALWAYS TRIGGER "
        f"{PG_STATE_EVENT_GUARD_TRIGGER}"
    )


def _create_postgresql_ack_triggers() -> None:
    op.execute(
        f"CREATE TRIGGER {PG_ACK_GUARD_TRIGGER} BEFORE INSERT "
        f"ON public.{TRANSITION_ACK} FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_ACK_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {PG_ACK_TRIGGER} AFTER UPDATE OF status, version, "
        "updated_at, closed_at ON public.stocktake_tasks FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_ACK_FUNCTION}()"
    )
    op.execute(
        f"ALTER TABLE public.{TRANSITION_ACK} ENABLE ALWAYS TRIGGER "
        f"{PG_ACK_GUARD_TRIGGER}"
    )
    op.execute(
        f"ALTER TABLE public.stocktake_tasks ENABLE ALWAYS TRIGGER {PG_ACK_TRIGGER}"
    )


def _create_postgresql_immutable_triggers() -> None:
    for table_name in NEW_TABLES:
        mutation = f"trg_{table_name}_immutable_0038"
        truncate = f"trg_{table_name}_no_truncate_0038"
        op.execute(
            f"CREATE TRIGGER {mutation} BEFORE UPDATE OR DELETE ON public.{table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()"
        )
        op.execute(
            f"CREATE TRIGGER {truncate} BEFORE TRUNCATE ON public.{table_name} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()"
        )
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {mutation}")
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {truncate}")


def _postgresql_json_sha256(document_sql: str) -> str:
    return (
        "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
        "public.rsc_canonical_reconciliation_json_0026("
        f"{document_sql}), 'UTF8')), 'hex')"
    )


def _postgresql_quantity_text(expression: str) -> str:
    return f"to_char({expression}, 'FM9999999999999990.000')"


def _postgresql_timestamp_text(expression: str) -> str:
    return (
        f"to_char({expression} AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
    )


def _postgresql_graph_function_sql() -> str:
    account_dimension_document = """jsonb_build_object(
        'availability_bucket', stock_account.availability_bucket,
        'condition_code', stock_account.condition_code,
        'custodian_person_id', CASE WHEN stock_account.custodian_person_id IS NULL
            THEN NULL ELSE stock_account.custodian_person_id::text END,
        'location_id', stock_account.location_id::text,
        'lot_id', CASE WHEN stock_account.lot_id IS NULL
            THEN NULL ELSE stock_account.lot_id::text END,
        'material_id', stock_account.material_id::text,
        'owner_org_id', stock_account.owner_org_id::text,
        'schema', 'cloud_oam.stocktake.close_reconciliation.account_dimension.v1',
        'stock_account_id', stock_account.id::text)"""
    account_dimension_hash = _postgresql_json_sha256(account_dimension_document)
    account_document = f"""jsonb_build_object(
        'account_dimension_sha256', account.account_dimension_sha256,
        'account_role', account.account_role,
        'balance_ledger_cursor', account.balance_ledger_cursor,
        'balance_qty', {_postgresql_quantity_text('account.balance_qty')},
        'book_qty_at_count', CASE WHEN account.book_qty_at_count IS NULL THEN NULL
            ELSE {_postgresql_quantity_text('account.book_qty_at_count')} END,
        'completion_id', account.completion_id::text,
        'count_ledger_cursor', account.count_ledger_cursor,
        'effective_round_id', CASE WHEN account.effective_round_id IS NULL THEN NULL
            ELSE account.effective_round_id::text END,
        'expected_physical_qty', CASE WHEN account.expected_physical_qty IS NULL THEN NULL
            ELSE {_postgresql_quantity_text('account.expected_physical_qty')} END,
        'last_touch_ledger_cursor', account.last_touch_ledger_cursor,
        'ledger_delta_after_count', CASE WHEN account.ledger_delta_after_count IS NULL THEN NULL
            ELSE {_postgresql_quantity_text('account.ledger_delta_after_count')} END,
        'ledger_qty', {_postgresql_quantity_text('account.ledger_qty')},
        'physical_delta_after_count', CASE WHEN account.physical_delta_after_count IS NULL THEN NULL
            ELSE {_postgresql_quantity_text('account.physical_delta_after_count')} END,
        'physical_qty_at_count', CASE WHEN account.physical_qty_at_count IS NULL THEN NULL
            ELSE {_postgresql_quantity_text('account.physical_qty_at_count')} END,
        'schema', 'cloud_oam.stocktake.close_reconciliation.account.v1',
        'scope_id', CASE WHEN account.scope_id IS NULL THEN NULL
            ELSE account.scope_id::text END,
        'stock_account_id', account.stock_account_id::text,
        'task_id', account.task_id::text)"""
    account_item_hash = _postgresql_json_sha256(account_document)
    serial_document = """jsonb_build_object(
        'completion_id', serial.completion_id::text,
        'count_ledger_cursor', serial.count_ledger_cursor,
        'current_position_account_id', CASE WHEN serial.current_position_account_id IS NULL
            THEN NULL ELSE serial.current_position_account_id::text END,
        'current_position_last_movement_id', serial.current_position_last_movement_id::text,
        'effective_round_id', CASE WHEN serial.effective_round_id IS NULL
            THEN NULL ELSE serial.effective_round_id::text END,
        'evidence_scope_id', CASE WHEN serial.evidence_scope_id IS NULL
            THEN NULL ELSE serial.evidence_scope_id::text END,
        'expected_current_account_id', CASE WHEN serial.expected_current_account_id IS NULL
            THEN NULL ELSE serial.expected_current_account_id::text END,
        'ledger_last_movement_id', serial.ledger_last_movement_id::text,
        'physical_account_id_at_count', CASE WHEN serial.physical_account_id_at_count IS NULL
            THEN NULL ELSE serial.physical_account_id_at_count::text END,
        'physical_present_at_count', serial.physical_present_at_count,
        'schema', 'cloud_oam.stocktake.close_reconciliation.serial.v1',
        'serial_id', serial.serial_id::text,
        'task_id', serial.task_id::text)"""
    serial_item_hash = _postgresql_json_sha256(serial_document)
    account_manifest_hash = _postgresql_json_sha256(
        f"""jsonb_build_object(
            'accounts', COALESCE((
                SELECT jsonb_agg({account_document} ORDER BY account.stock_account_id)
                  FROM public.{RECONCILIATION_ACCOUNT} AS account
                 WHERE account.completion_id = row.id), '[]'::jsonb),
            'schema', 'cloud_oam.stocktake.close_reconciliation.accounts.v1',
            'task_id', row.task_id::text)"""
    )
    serial_manifest_hash = _postgresql_json_sha256(
        f"""jsonb_build_object(
            'schema', 'cloud_oam.stocktake.close_reconciliation.serials.v1',
            'serials', COALESCE((
                SELECT jsonb_agg({serial_document} ORDER BY serial.serial_id)
                  FROM public.{RECONCILIATION_SERIAL} AS serial
                 WHERE serial.completion_id = row.id), '[]'::jsonb),
            'task_id', row.task_id::text)"""
    )
    reconciliation_manifest_hash = _postgresql_json_sha256(
        f"""jsonb_build_object(
            'account_count', row.account_count,
            'account_manifest_sha256', row.account_manifest_sha256,
            'book_total_qty', {_postgresql_quantity_text('row.book_total_qty')},
            'completion_id', row.id::text,
            'expected_task_version', row.expected_task_version,
            'movement_count', row.movement_count,
            'physical_total_qty', {_postgresql_quantity_text('row.physical_total_qty')},
            'posting_completion_id', row.posting_completion_id::text,
            'posting_manifest_sha256', row.posting_manifest_sha256,
            'previous_reconciliation_id', CASE WHEN row.previous_reconciliation_id IS NULL
                THEN NULL ELSE row.previous_reconciliation_id::text END,
            'reconciled_task_version', row.reconciled_task_version,
            'reconciliation_ledger_cursor', row.reconciliation_ledger_cursor,
            'reconciliation_no', row.reconciliation_no,
            'schema', 'cloud_oam.stocktake.nonopening_close_reconciliation.v1',
            'scope_count', row.scope_count,
            'scoped_account_count', row.scoped_account_count,
            'serial_count', row.serial_count,
            'serial_manifest_sha256', row.serial_manifest_sha256,
            'task_id', row.task_id::text,
            'transaction_count', row.transaction_count)"""
    )
    reconciliation_authorization_hash = _postgresql_json_sha256(
        f"""jsonb_build_object(
            'assignment_id', row.reconciled_role_assignment_id::text,
            'authorization_version', row.authorization_version,
            'kind', 'reconcile',
            'occurred_at', {_postgresql_timestamp_text('row.reconciled_at')},
            'person_id', row.reconciled_by_person_id::text,
            'role_code', 'admin',
            'schema', 'cloud_oam.stocktake.nonopening_close_authorization.v1',
            'scope_id', '*',
            'scope_type', 'national',
            'user_id', row.reconciled_by_user_id)"""
    )
    close_authorization_hash = _postgresql_json_sha256(
        f"""jsonb_build_object(
            'assignment_id', close_row.closed_role_assignment_id::text,
            'authorization_version', close_row.authorization_version,
            'kind', 'close',
            'occurred_at', {_postgresql_timestamp_text('close_row.closed_at')},
            'person_id', close_row.closed_by_person_id::text,
            'role_code', 'admin',
            'schema', 'cloud_oam.stocktake.nonopening_close_authorization.v1',
            'scope_id', '*',
            'scope_type', 'national',
            'user_id', close_row.closed_by_user_id)"""
    )
    reconciliation_request_hash = _postgresql_json_sha256(
        """jsonb_build_object(
            'actor', jsonb_build_object(
                'authorization_version', row.authorization_version,
                'person_id', row.reconciled_by_person_id::text,
                'user_id', row.reconciled_by_user_id),
            'expected_task_version', row.expected_task_version,
            'kind', 'reconcile',
            'posting_completion_id', row.posting_completion_id::text,
            'posting_manifest_sha256', row.posting_manifest_sha256,
            'reconciliation_completion_id', NULL,
            'reconciliation_manifest_sha256', NULL,
            'schema', 'cloud_oam.stocktake.nonopening_close_request.v1',
            'task_id', row.task_id::text)"""
    )
    close_request_hash = _postgresql_json_sha256(
        """jsonb_build_object(
            'actor', jsonb_build_object(
                'authorization_version', close_row.authorization_version,
                'person_id', close_row.closed_by_person_id::text,
                'user_id', close_row.closed_by_user_id),
            'expected_task_version', close_row.expected_task_version,
            'kind', 'close',
            'posting_completion_id', close_row.posting_completion_id::text,
            'posting_manifest_sha256', close_row.posting_manifest_sha256,
            'reconciliation_completion_id', close_row.reconciliation_completion_id::text,
            'reconciliation_manifest_sha256', close_row.reconciliation_manifest_sha256,
            'schema', 'cloud_oam.stocktake.nonopening_close_request.v1',
            'task_id', close_row.task_id::text)"""
    )
    close_manifest_hash = _postgresql_json_sha256(
        """jsonb_build_object(
            'close_id', close_row.id::text,
            'closed_task_version', close_row.closed_task_version,
            'expected_task_version', close_row.expected_task_version,
            'posting_completion_id', close_row.posting_completion_id::text,
            'posting_manifest_sha256', close_row.posting_manifest_sha256,
            'reconciliation_completion_id', close_row.reconciliation_completion_id::text,
            'reconciliation_ledger_cursor', close_row.reconciliation_ledger_cursor,
            'reconciliation_manifest_sha256', close_row.reconciliation_manifest_sha256,
            'reconciliation_no', close_row.reconciliation_no,
            'schema', 'cloud_oam.stocktake.nonopening_close_completion.v1',
        'task_id', close_row.task_id::text)"""
    )
    serial_physical_reproof = f"""NOT EXISTS (
        SELECT 1
          FROM public.{RECONCILIATION_SERIAL} AS serial
         WHERE serial.completion_id = row.id
           AND EXISTS (
            WITH candidate_scopes(scope_id) AS (
                SELECT line.scope_id
                  FROM public.stocktake_count_serials AS counted_serial
                  JOIN public.stocktake_count_lines AS line
                    ON line.id = counted_serial.count_line_id
                  JOIN public.stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting_row.effective_approval_completion_id
                   AND selected.scope_id = line.scope_id
                   AND selected.source_round_id = line.round_id
                 WHERE line.task_id = row.task_id
                   AND counted_serial.serial_id = serial.serial_id
                UNION
                SELECT observation.scope_id
                  FROM public.stocktake_count_observations AS observation
                  JOIN public.stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting_row.effective_approval_completion_id
                   AND selected.scope_id = observation.scope_id
                   AND selected.source_round_id = observation.round_id
                 WHERE observation.task_id = row.task_id
                   AND observation.serial_id = serial.serial_id
                   AND observation.verification_status = 'verified'
                   AND observation.material_id IS NOT NULL
                UNION
                SELECT scope.id
                  FROM public.inventory_movement_serials AS binding
                  JOIN public.inventory_movements AS movement
                    ON movement.id = binding.movement_id
                   AND movement.transaction_id = binding.transaction_id
                  JOIN public.inventory_transactions AS transaction_row
                    ON transaction_row.id = binding.transaction_id
                  JOIN public.stock_accounts AS endpoint
                    ON endpoint.id = movement.from_account_id
                    OR endpoint.id = movement.to_account_id
                  JOIN public.stocktake_scopes AS scope
                    ON scope.task_id = row.task_id
                   AND scope.owner_org_id = endpoint.owner_org_id
                   AND scope.location_id = endpoint.location_id
                   AND (scope.material_id IS NULL OR
                        scope.material_id = endpoint.material_id)
                   AND (scope.condition_code IS NULL OR
                        scope.condition_code = endpoint.condition_code)
                   AND (scope.availability_bucket IS NULL OR
                        scope.availability_bucket = endpoint.availability_bucket)
                  JOIN public.stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting_row.effective_approval_completion_id
                   AND selected.scope_id = scope.id
                  JOIN public.stocktake_scope_count_completions AS count_completion
                    ON count_completion.task_id = row.task_id
                   AND count_completion.scope_id = scope.id
                   AND count_completion.round_id = selected.source_round_id
                 WHERE binding.serial_id = serial.serial_id
                   AND transaction_row.status = 'posted'
                   AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                   AND transaction_row.ledger_cursor <=
                       count_completion.count_ledger_cursor
            ),
            physical_evidence(scope_id, stock_account_id) AS (
                SELECT line.scope_id, line.stock_account_id
                  FROM public.stocktake_count_serials AS counted_serial
                  JOIN public.stocktake_count_lines AS line
                    ON line.id = counted_serial.count_line_id
                  JOIN public.stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting_row.effective_approval_completion_id
                   AND selected.scope_id = line.scope_id
                   AND selected.source_round_id = line.round_id
                 WHERE line.task_id = row.task_id
                   AND counted_serial.serial_id = serial.serial_id
                UNION ALL
                SELECT observation.scope_id, stock_account.id
                  FROM public.stocktake_count_observations AS observation
                  JOIN public.stocktake_effective_approval_scopes AS selected
                    ON selected.completion_id = posting_row.effective_approval_completion_id
                   AND selected.scope_id = observation.scope_id
                   AND selected.source_round_id = observation.round_id
                  JOIN public.stock_accounts AS stock_account
                    ON stock_account.owner_org_id = observation.owner_org_id
                   AND stock_account.location_id = observation.location_id
                   AND stock_account.custodian_person_id
                       IS NOT DISTINCT FROM observation.custodian_person_id_snapshot
                   AND stock_account.material_id = observation.material_id
                   AND stock_account.condition_code = observation.condition_code
                   AND stock_account.availability_bucket = observation.availability_bucket
                   AND stock_account.lot_id IS NOT DISTINCT FROM observation.lot_id
                  JOIN public.{RECONCILIATION_ACCOUNT} AS account
                    ON account.completion_id = row.id
                   AND account.scope_id = observation.scope_id
                   AND account.stock_account_id = stock_account.id
                 WHERE observation.task_id = row.task_id
                   AND observation.serial_id = serial.serial_id
                   AND observation.verification_status = 'verified'
                   AND observation.material_id IS NOT NULL
            ),
            later_movements AS (
                SELECT movement.from_account_id,
                       movement.to_account_id,
                       row_number() OVER (
                           ORDER BY transaction_row.ledger_cursor,
                                    movement.line_no, movement.id) AS movement_no,
                       lag(movement.to_account_id) OVER (
                           ORDER BY transaction_row.ledger_cursor,
                                    movement.line_no, movement.id) AS previous_to_account_id
                  FROM public.inventory_movement_serials AS binding
                  JOIN public.inventory_movements AS movement
                    ON movement.id = binding.movement_id
                   AND movement.transaction_id = binding.transaction_id
                  JOIN public.inventory_transactions AS transaction_row
                    ON transaction_row.id = binding.transaction_id
                 WHERE binding.serial_id = serial.serial_id
                   AND transaction_row.status = 'posted'
                   AND transaction_row.ledger_cursor > serial.count_ledger_cursor
                   AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_posting_completion_items AS own_item
                        WHERE own_item.completion_id = row.posting_completion_id
                          AND own_item.inventory_transaction_id = transaction_row.id)
            )
            SELECT 1
             WHERE (SELECT count(*) FROM candidate_scopes) > 1
                OR serial.evidence_scope_id IS DISTINCT FROM (
                    SELECT scope_id FROM candidate_scopes LIMIT 1)
                OR (serial.evidence_scope_id IS NULL AND
                    (serial.effective_round_id IS NOT NULL
                     OR serial.count_ledger_cursor IS NOT NULL
                     OR serial.physical_present_at_count IS NOT NULL
                     OR serial.physical_account_id_at_count IS NOT NULL))
                OR (serial.evidence_scope_id IS NOT NULL AND
                    (serial.effective_round_id IS DISTINCT FROM (
                         SELECT selected.source_round_id
                           FROM public.stocktake_effective_approval_scopes AS selected
                          WHERE selected.completion_id = posting_row.effective_approval_completion_id
                            AND selected.scope_id = serial.evidence_scope_id)
                     OR serial.count_ledger_cursor IS DISTINCT FROM (
                         SELECT count_completion.count_ledger_cursor
                           FROM public.stocktake_scope_count_completions AS count_completion
                          WHERE count_completion.task_id = row.task_id
                            AND count_completion.scope_id = serial.evidence_scope_id
                            AND count_completion.round_id = serial.effective_round_id)
                     OR (SELECT count(*) FROM physical_evidence) > 1
                     OR EXISTS (
                         SELECT 1 FROM physical_evidence AS evidence
                          WHERE evidence.scope_id IS DISTINCT FROM serial.evidence_scope_id)
                     OR serial.physical_present_at_count IS DISTINCT FROM
                        ((SELECT count(*) FROM physical_evidence) = 1)
                     OR serial.physical_account_id_at_count IS DISTINCT FROM
                        CASE WHEN (SELECT count(*) FROM physical_evidence) = 1
                             THEN (SELECT stock_account_id FROM physical_evidence LIMIT 1)
                             ELSE NULL END
                     OR EXISTS (
                         SELECT 1 FROM later_movements AS movement
                          WHERE movement.from_account_id IS DISTINCT FROM
                                CASE WHEN movement.movement_no = 1
                                     THEN serial.physical_account_id_at_count
                                     ELSE movement.previous_to_account_id END)
                     OR serial.expected_current_account_id IS DISTINCT FROM
                        CASE WHEN EXISTS (SELECT 1 FROM later_movements)
                             THEN (SELECT movement.to_account_id
                                     FROM later_movements AS movement
                                    ORDER BY movement.movement_no DESC LIMIT 1)
                             ELSE serial.physical_account_id_at_count END))
        ))"""
    reconciliation_event_document = f"""jsonb_build_object(
        'account_count', row.account_count,
        'book_total_qty', {_postgresql_quantity_text('row.book_total_qty')},
        'completion_id', row.id::text,
        'physical_total_qty', {_postgresql_quantity_text('row.physical_total_qty')},
        'posting_completion_id', row.posting_completion_id::text,
        'reconciled_task_version', row.reconciled_task_version,
        'reconciliation_ledger_cursor', row.reconciliation_ledger_cursor,
        'reconciliation_manifest_sha256', row.reconciliation_manifest_sha256,
        'reconciliation_no', row.reconciliation_no,
        'schema', 'cloud_oam.stocktake.nonopening_close_reconciled_event.v1',
        'serial_count', row.serial_count,
        'status', 'posted',
        'task_id', row.task_id::text)"""
    close_event_document = """jsonb_build_object(
        'close_completion_id', close_row.id::text,
        'close_manifest_sha256', close_row.close_manifest_sha256,
        'closed_task_version', close_row.closed_task_version,
        'posting_completion_id', close_row.posting_completion_id::text,
        'reconciliation_completion_id', close_row.reconciliation_completion_id::text,
        'reconciliation_ledger_cursor', close_row.reconciliation_ledger_cursor,
        'reconciliation_manifest_sha256', close_row.reconciliation_manifest_sha256,
        'reconciliation_no', close_row.reconciliation_no,
        'schema', 'cloud_oam.stocktake.nonopening_closed_event.v1',
        'status', 'closed',
        'task_id', close_row.task_id::text)"""
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
    posting_row public.stocktake_posting_completions%ROWTYPE;
    reconciliation_count bigint;
    close_count bigint;
    ack_count bigint;
    latest public.{RECONCILIATION}%ROWTYPE;
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
    SELECT * INTO posting_row FROM public.stocktake_posting_completions
     WHERE task_id = target_task_id;
    SELECT count(*) INTO reconciliation_count FROM public.{RECONCILIATION}
     WHERE task_id = target_task_id;
    SELECT count(*) INTO close_count FROM public.{CLOSE_COMPLETION}
     WHERE task_id = target_task_id;
    SELECT count(*) INTO ack_count FROM public.{TRANSITION_ACK}
     WHERE task_id = target_task_id;
    IF ack_count <> reconciliation_count + close_count THEN
        RAISE EXCEPTION 'non-opening stocktake transition acknowledgement graph is invalid';
    END IF;
    IF TG_TABLE_NAME = 'stocktake_tasks' AND TG_OP = 'UPDATE' THEN
        IF OLD.task_type IN {NONOPENING_SQL} AND OLD.status = 'posted' THEN
            IF NEW.status = 'posted' THEN
                IF NEW.version <> OLD.version + 1
                   OR NEW.closed_at IS NOT NULL
                   OR (to_jsonb(NEW) - ARRAY['version', 'updated_at']) IS DISTINCT FROM
                      (to_jsonb(OLD) - ARRAY['version', 'updated_at'])
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.{RECONCILIATION} AS transition_fact
                         JOIN public.users AS actor_user
                           ON actor_user.id = transition_fact.reconciled_by_user_id
                          AND actor_user.person_id = transition_fact.reconciled_by_person_id
                         JOIN public.people AS actor_person
                           ON actor_person.id = actor_user.person_id
                         JOIN public.organizations AS actor_org
                           ON actor_org.id = actor_person.organization_id
                         JOIN public.role_assignments AS assignment
                           ON assignment.id = transition_fact.reconciled_role_assignment_id
                          AND assignment.user_id = actor_user.id
                         JOIN public.roles AS actor_role
                           ON actor_role.id = assignment.role_id
                         JOIN public.role_permissions AS role_permission
                           ON role_permission.role_id = actor_role.id
                          AND role_permission.effect = 'allow'
                         JOIN public.permissions AS permission
                           ON permission.id = role_permission.permission_id
                        WHERE transition_fact.task_id = NEW.id
                          AND transition_fact.expected_task_version = OLD.version
                          AND transition_fact.reconciled_task_version = NEW.version
                          AND transition_fact.reconciled_at = NEW.updated_at
                          AND actor_user.account_status = 'active'
                          AND actor_user.is_active
                          AND actor_user.authorization_version = transition_fact.authorization_version
                          AND EXISTS (
                              SELECT 1 FROM public.auth_identities AS identity
                               WHERE identity.user_id = actor_user.id
                                 AND identity.status = 'active'
                                 AND identity.verified_at IS NOT NULL
                                 AND identity.revoked_at IS NULL)
                          AND actor_person.employment_status = 'active'
                          AND actor_org.status = 'active'
                          AND actor_org.org_type = 'headquarters'
                          AND actor_role.code = 'admin'
                          AND actor_role.status = 'active'
                          AND NOT actor_role.is_external
                          AND assignment.scope_type = 'national'
                          AND assignment.scope_id = '*'
                          AND assignment.status = 'active'
                          AND assignment.revoked_at IS NULL
                          AND assignment.valid_from <= transition_fact.reconciled_at
                          AND (assignment.valid_to IS NULL OR
                               assignment.valid_to > transition_fact.reconciled_at)
                          AND (SELECT count(*)
                                 FROM public.role_assignments AS current_assignment
                                 JOIN public.roles AS effective_role
                                   ON effective_role.id = current_assignment.role_id
                                  AND effective_role.status = 'active'
                                WHERE current_assignment.user_id = actor_user.id
                                  AND current_assignment.status IN ('scheduled', 'active')
                                  AND current_assignment.revoked_at IS NULL
                                  AND current_assignment.valid_from <= transition_fact.reconciled_at
                                  AND (current_assignment.valid_to IS NULL OR
                                       current_assignment.valid_to > transition_fact.reconciled_at)
                                  AND effective_role.code = 'admin'
                                  AND current_assignment.scope_type = 'national'
                                  AND current_assignment.scope_id = '*') = 1
                          AND permission.resource = 'stocktake'
                          AND permission.action = 'reconcile'
                          AND permission.field_code = ''
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.role_assignments AS deny_assignment
                                JOIN public.roles AS deny_role
                                  ON deny_role.id = deny_assignment.role_id
                                 AND deny_role.status = 'active'
                                JOIN public.role_permissions AS deny_binding
                                  ON deny_binding.role_id = deny_role.id
                                 AND deny_binding.effect = 'deny'
                                JOIN public.permissions AS denied_permission
                                  ON denied_permission.id = deny_binding.permission_id
                               WHERE deny_assignment.user_id = actor_user.id
                                 AND deny_assignment.status IN ('scheduled', 'active')
                                 AND deny_assignment.revoked_at IS NULL
                                 AND deny_assignment.valid_from <= transition_fact.reconciled_at
                                 AND (deny_assignment.valid_to IS NULL OR
                                      deny_assignment.valid_to > transition_fact.reconciled_at)
                                 AND deny_assignment.scope_type = 'national'
                                 AND deny_assignment.scope_id = '*'
                                 AND denied_permission.resource = 'stocktake'
                                 AND denied_permission.action = 'reconcile'
                                 AND denied_permission.field_code = '')
                          AND transition_fact.reconciliation_ledger_cursor = (
                              SELECT head.next_cursor - 1
                                FROM public.inventory_ledger_heads AS head
                               WHERE head.stream_key = 'inventory'))
                   OR (SELECT count(*) FROM public.{RECONCILIATION} AS transition_fact
                        WHERE transition_fact.task_id = NEW.id
                          AND transition_fact.expected_task_version = OLD.version) <> 1 THEN
                    RAISE EXCEPTION 'posted non-opening stocktake reconciliation transition is invalid';
                END IF;
            ELSIF NEW.status = 'closed' THEN
                IF NEW.version <> OLD.version + 1
                   OR NEW.closed_at IS NULL
                   OR NEW.updated_at IS DISTINCT FROM NEW.closed_at
                   OR (to_jsonb(NEW) - ARRAY['status', 'version', 'updated_at', 'closed_at'])
                      IS DISTINCT FROM
                      (to_jsonb(OLD) - ARRAY['status', 'version', 'updated_at', 'closed_at'])
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.{CLOSE_COMPLETION} AS transition_fact
                         JOIN public.users AS actor_user
                           ON actor_user.id = transition_fact.closed_by_user_id
                          AND actor_user.person_id = transition_fact.closed_by_person_id
                         JOIN public.people AS actor_person
                           ON actor_person.id = actor_user.person_id
                         JOIN public.organizations AS actor_org
                           ON actor_org.id = actor_person.organization_id
                         JOIN public.role_assignments AS assignment
                           ON assignment.id = transition_fact.closed_role_assignment_id
                          AND assignment.user_id = actor_user.id
                         JOIN public.roles AS actor_role
                           ON actor_role.id = assignment.role_id
                         JOIN public.role_permissions AS role_permission
                           ON role_permission.role_id = actor_role.id
                          AND role_permission.effect = 'allow'
                         JOIN public.permissions AS permission
                           ON permission.id = role_permission.permission_id
                        WHERE transition_fact.task_id = NEW.id
                          AND transition_fact.expected_task_version = OLD.version
                          AND transition_fact.closed_task_version = NEW.version
                          AND transition_fact.closed_at = NEW.closed_at
                          AND actor_user.account_status = 'active'
                          AND actor_user.is_active
                          AND actor_user.authorization_version = transition_fact.authorization_version
                          AND EXISTS (
                              SELECT 1 FROM public.auth_identities AS identity
                               WHERE identity.user_id = actor_user.id
                                 AND identity.status = 'active'
                                 AND identity.verified_at IS NOT NULL
                                 AND identity.revoked_at IS NULL)
                          AND actor_person.employment_status = 'active'
                          AND actor_org.status = 'active'
                          AND actor_org.org_type = 'headquarters'
                          AND actor_role.code = 'admin'
                          AND actor_role.status = 'active'
                          AND NOT actor_role.is_external
                          AND assignment.scope_type = 'national'
                          AND assignment.scope_id = '*'
                          AND assignment.status = 'active'
                          AND assignment.revoked_at IS NULL
                          AND assignment.valid_from <= transition_fact.closed_at
                          AND (assignment.valid_to IS NULL OR
                               assignment.valid_to > transition_fact.closed_at)
                          AND (SELECT count(*)
                                 FROM public.role_assignments AS current_assignment
                                 JOIN public.roles AS effective_role
                                   ON effective_role.id = current_assignment.role_id
                                  AND effective_role.status = 'active'
                                WHERE current_assignment.user_id = actor_user.id
                                  AND current_assignment.status IN ('scheduled', 'active')
                                  AND current_assignment.revoked_at IS NULL
                                  AND current_assignment.valid_from <= transition_fact.closed_at
                                  AND (current_assignment.valid_to IS NULL OR
                                       current_assignment.valid_to > transition_fact.closed_at)
                                  AND effective_role.code = 'admin'
                                  AND current_assignment.scope_type = 'national'
                                  AND current_assignment.scope_id = '*') = 1
                          AND permission.resource = 'stocktake'
                          AND permission.action = 'close'
                          AND permission.field_code = ''
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.role_assignments AS deny_assignment
                                JOIN public.roles AS deny_role
                                  ON deny_role.id = deny_assignment.role_id
                                 AND deny_role.status = 'active'
                                JOIN public.role_permissions AS deny_binding
                                  ON deny_binding.role_id = deny_role.id
                                 AND deny_binding.effect = 'deny'
                                JOIN public.permissions AS denied_permission
                                  ON denied_permission.id = deny_binding.permission_id
                               WHERE deny_assignment.user_id = actor_user.id
                                 AND deny_assignment.status IN ('scheduled', 'active')
                                 AND deny_assignment.revoked_at IS NULL
                                 AND deny_assignment.valid_from <= transition_fact.closed_at
                                 AND (deny_assignment.valid_to IS NULL OR
                                      deny_assignment.valid_to > transition_fact.closed_at)
                                 AND deny_assignment.scope_type = 'national'
                                 AND deny_assignment.scope_id = '*'
                                 AND denied_permission.resource = 'stocktake'
                                 AND denied_permission.action = 'close'
                                 AND denied_permission.field_code = '')) THEN
                    RAISE EXCEPTION 'non-opening stocktake close transition is invalid';
                END IF;
            ELSE
                RAISE EXCEPTION 'posted non-opening stocktake terminal transition is invalid';
            END IF;
        ELSIF NEW.status = 'closed' AND OLD.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'non-opening stocktake must close from posted';
        ELSIF NEW.status = 'posted' AND reconciliation_count > 0
              AND OLD.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'non-opening reconciliation requires an independent posted transition';
        END IF;
    END IF;
    IF reconciliation_count > 0 THEN
        IF posting_row.id IS NULL OR EXISTS (
            SELECT 1 FROM public.{RECONCILIATION} AS row
             WHERE row.task_id = target_task_id
               AND (row.posting_completion_id <> posting_row.id
                    OR row.posting_manifest_sha256 <> posting_row.posting_manifest_sha256
                    OR row.reconciliation_no <> 1 + (
                        SELECT count(*) FROM public.{RECONCILIATION} AS prior
                         WHERE prior.task_id = target_task_id
                           AND prior.reconciliation_no < row.reconciliation_no)
                    OR row.expected_task_version < posting_row.posted_task_version
                    OR row.reconciled_task_version <> row.expected_task_version + 1
                    OR row.reconciled_at <= posting_row.posted_at
                    OR row.authorization_sha256 <> {reconciliation_authorization_hash}
                    OR row.request_sha256 <> {reconciliation_request_hash}
                    OR row.expected_task_version <>
                       posting_row.posted_task_version + row.reconciliation_no - 1
                    OR (row.reconciliation_no = 1 AND row.previous_reconciliation_id IS NOT NULL)
                    OR (row.reconciliation_no > 1 AND row.previous_reconciliation_id IS DISTINCT FROM (
                        SELECT prior.id FROM public.{RECONCILIATION} AS prior
                         WHERE prior.task_id = target_task_id
                           AND prior.reconciliation_no = row.reconciliation_no - 1))
                    OR (row.reconciliation_no > 1 AND row.reconciled_at <= (
                        SELECT prior.reconciled_at FROM public.{RECONCILIATION} AS prior
                         WHERE prior.task_id = target_task_id
                           AND prior.reconciliation_no = row.reconciliation_no - 1))
                    OR row.scope_count <> (SELECT count(*) FROM public.stocktake_scopes WHERE task_id = target_task_id)
                    OR row.account_count <> (SELECT count(*) FROM public.{RECONCILIATION_ACCOUNT} WHERE completion_id = row.id)
                    OR row.scoped_account_count <> (SELECT count(*) FROM public.{RECONCILIATION_ACCOUNT} WHERE completion_id = row.id AND scope_id IS NOT NULL)
                    OR row.serial_count <> (SELECT count(*) FROM public.{RECONCILIATION_SERIAL} WHERE completion_id = row.id)
                    OR row.transaction_count <> (
                        SELECT count(DISTINCT transaction_row.id)
                          FROM public.inventory_transactions AS transaction_row
                          JOIN public.inventory_movements AS movement
                            ON movement.transaction_id = transaction_row.id
                         WHERE transaction_row.status = 'posted'
                           AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                           AND EXISTS (
                               SELECT 1 FROM public.{RECONCILIATION_ACCOUNT} AS account
                                WHERE account.completion_id = row.id
                                  AND (account.stock_account_id = movement.from_account_id
                                       OR account.stock_account_id = movement.to_account_id)))
                    OR row.movement_count <> (
                        SELECT count(DISTINCT movement.id)
                          FROM public.inventory_transactions AS transaction_row
                          JOIN public.inventory_movements AS movement
                            ON movement.transaction_id = transaction_row.id
                         WHERE transaction_row.status = 'posted'
                           AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                           AND EXISTS (
                               SELECT 1 FROM public.{RECONCILIATION_ACCOUNT} AS account
                                WHERE account.completion_id = row.id
                                  AND (account.stock_account_id = movement.from_account_id
                                       OR account.stock_account_id = movement.to_account_id)))
                    OR row.book_total_qty <> (SELECT COALESCE(sum(ledger_qty), 0) FROM public.{RECONCILIATION_ACCOUNT} WHERE completion_id = row.id AND scope_id IS NOT NULL)
                    OR row.physical_total_qty <> row.book_total_qty
                    OR row.account_manifest_sha256 <> {account_manifest_hash}
                    OR row.serial_manifest_sha256 <> {serial_manifest_hash}
                    OR row.reconciliation_manifest_sha256 <>
                       {reconciliation_manifest_hash}
                    OR row.reconciliation_ledger_cursor > (SELECT next_cursor - 1 FROM public.inventory_ledger_heads WHERE stream_key = 'inventory')
                    OR EXISTS (
                        WITH scope_account_ids AS (
                            SELECT DISTINCT stock_account.id
                              FROM public.stock_accounts AS stock_account
                              JOIN public.stocktake_scopes AS scope
                                ON scope.task_id = row.task_id
                               AND scope.owner_org_id = stock_account.owner_org_id
                               AND scope.location_id = stock_account.location_id
                               AND (scope.material_id IS NULL OR
                                    scope.material_id = stock_account.material_id)
                               AND (scope.condition_code IS NULL OR
                                    scope.condition_code = stock_account.condition_code)
                               AND (scope.availability_bucket IS NULL OR
                                    scope.availability_bucket = stock_account.availability_bucket)
                        ),
                        base_account_ids AS (
                            SELECT snapshot.stock_account_id AS id
                              FROM public.stocktake_snapshot_lines AS snapshot
                             WHERE snapshot.task_id = row.task_id
                            UNION
                            SELECT id FROM scope_account_ids
                            UNION
                            SELECT movement.from_account_id
                              FROM public.stocktake_posting_completion_items AS item
                              JOIN public.inventory_movements AS movement
                                ON movement.id = item.inventory_movement_id
                             WHERE item.completion_id = row.posting_completion_id
                               AND movement.from_account_id IS NOT NULL
                            UNION
                            SELECT movement.to_account_id
                              FROM public.stocktake_posting_completion_items AS item
                              JOIN public.inventory_movements AS movement
                                ON movement.id = item.inventory_movement_id
                             WHERE item.completion_id = row.posting_completion_id
                               AND movement.to_account_id IS NOT NULL
                        ),
                        expected_account_ids AS (
                            SELECT id FROM base_account_ids WHERE id IS NOT NULL
                            UNION
                            SELECT movement.from_account_id
                              FROM public.inventory_movements AS movement
                             WHERE movement.from_account_id IS NOT NULL
                               AND (movement.from_account_id IN (SELECT id FROM base_account_ids)
                                    OR movement.to_account_id IN (SELECT id FROM base_account_ids))
                            UNION
                            SELECT movement.to_account_id
                              FROM public.inventory_movements AS movement
                             WHERE movement.to_account_id IS NOT NULL
                               AND (movement.from_account_id IN (SELECT id FROM base_account_ids)
                                    OR movement.to_account_id IN (SELECT id FROM base_account_ids))
                        ),
                        expected_serial_ids AS (
                            SELECT CASE
                                       WHEN jsonb_typeof(serial_json.value) = 'object'
                                        AND (serial_json.value ->> 'serial_id') ~*
                                            '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
                                        AND (serial_json.value ->> 'serial_id') <>
                                            '00000000-0000-0000-0000-000000000000'
                                       THEN (serial_json.value ->> 'serial_id')::uuid
                                       ELSE NULL
                                   END AS id
                              FROM public.stocktake_snapshot_lines AS snapshot
                              CROSS JOIN LATERAL jsonb_array_elements(
                                  CASE WHEN jsonb_typeof(snapshot.serial_snapshot_jsonb) = 'array'
                                       THEN snapshot.serial_snapshot_jsonb
                                       ELSE '[]'::jsonb END
                              ) AS serial_json(value)
                             WHERE snapshot.task_id = row.task_id
                            UNION
                            SELECT serial.serial_id
                              FROM public.stocktake_count_serials AS serial
                              JOIN public.stocktake_count_lines AS line
                                ON line.id = serial.count_line_id
                             WHERE line.task_id = row.task_id
                            UNION
                            SELECT observation.serial_id
                              FROM public.stocktake_count_observations AS observation
                             WHERE observation.task_id = row.task_id
                               AND observation.serial_id IS NOT NULL
                            UNION
                            SELECT difference.serial_id
                              FROM public.stocktake_differences AS difference
                             WHERE difference.task_id = row.task_id
                               AND difference.serial_id IS NOT NULL
                            UNION
                            SELECT binding.serial_id
                              FROM public.stocktake_posting_completion_items AS item
                              JOIN public.inventory_movement_serials AS binding
                                ON binding.movement_id = item.inventory_movement_id
                             WHERE item.completion_id = row.posting_completion_id
                            UNION
                            SELECT position.serial_id
                              FROM public.serial_current_positions AS position
                             WHERE position.stock_account_id IN (
                                 SELECT id FROM scope_account_ids)
                            UNION
                            SELECT binding.serial_id
                              FROM public.inventory_movement_serials AS binding
                              JOIN public.inventory_movements AS movement
                                ON movement.id = binding.movement_id
                             WHERE movement.from_account_id IN (
                                       SELECT id FROM expected_account_ids)
                                OR movement.to_account_id IN (
                                       SELECT id FROM expected_account_ids)
                        )
                        SELECT 1
                         WHERE EXISTS (
                             SELECT 1 FROM expected_account_ids AS expected
                              WHERE NOT EXISTS (
                                  SELECT 1 FROM public.{RECONCILIATION_ACCOUNT} AS actual
                                   WHERE actual.completion_id = row.id
                                     AND actual.stock_account_id = expected.id))
                            OR EXISTS (
                             SELECT 1 FROM public.{RECONCILIATION_ACCOUNT} AS actual
                              WHERE actual.completion_id = row.id
                                AND NOT EXISTS (
                                    SELECT 1 FROM expected_account_ids AS expected
                                     WHERE expected.id = actual.stock_account_id))
                            OR EXISTS (
                             SELECT 1 FROM expected_serial_ids AS expected
                              WHERE NOT EXISTS (
                                  SELECT 1 FROM public.{RECONCILIATION_SERIAL} AS actual
                                   WHERE actual.completion_id = row.id
                                     AND actual.serial_id = expected.id))
                            OR EXISTS (
                             SELECT 1 FROM public.{RECONCILIATION_SERIAL} AS actual
                              WHERE actual.completion_id = row.id
                                AND NOT EXISTS (
                                    SELECT 1 FROM expected_serial_ids AS expected
                                     WHERE expected.id = actual.serial_id)))
                    OR EXISTS (
                        SELECT 1
                          FROM public.{RECONCILIATION_ACCOUNT} AS account
                          JOIN public.stock_accounts AS stock_account
                            ON stock_account.id = account.stock_account_id
                         WHERE account.completion_id = row.id
                           AND ((SELECT count(*)
                                  FROM public.stocktake_scopes AS scope
                                 WHERE scope.task_id = row.task_id
                                   AND scope.owner_org_id = stock_account.owner_org_id
                                   AND scope.location_id = stock_account.location_id
                                   AND (scope.material_id IS NULL OR
                                        scope.material_id = stock_account.material_id)
                                   AND (scope.condition_code IS NULL OR
                                        scope.condition_code = stock_account.condition_code)
                                   AND (scope.availability_bucket IS NULL OR
                                        scope.availability_bucket = stock_account.availability_bucket)) > 1
                                OR ((SELECT count(*)
                                       FROM public.stocktake_scopes AS scope
                                      WHERE scope.task_id = row.task_id
                                        AND scope.owner_org_id = stock_account.owner_org_id
                                        AND scope.location_id = stock_account.location_id
                                        AND (scope.material_id IS NULL OR
                                             scope.material_id = stock_account.material_id)
                                        AND (scope.condition_code IS NULL OR
                                             scope.condition_code = stock_account.condition_code)
                                        AND (scope.availability_bucket IS NULL OR
                                             scope.availability_bucket = stock_account.availability_bucket)) = 1
                                    AND (account.account_role <> 'scope'
                                         OR account.scope_id IS DISTINCT FROM (
                                             SELECT scope.id
                                               FROM public.stocktake_scopes AS scope
                                              WHERE scope.task_id = row.task_id
                                                AND scope.owner_org_id = stock_account.owner_org_id
                                                AND scope.location_id = stock_account.location_id
                                                AND (scope.material_id IS NULL OR
                                                     scope.material_id = stock_account.material_id)
                                                AND (scope.condition_code IS NULL OR
                                                     scope.condition_code = stock_account.condition_code)
                                                AND (scope.availability_bucket IS NULL OR
                                                     scope.availability_bucket = stock_account.availability_bucket))
                                         OR account.effective_round_id IS DISTINCT FROM (
                                             SELECT selected.source_round_id
                                               FROM public.stocktake_effective_approval_scopes AS selected
                                              WHERE selected.completion_id = posting_row.effective_approval_completion_id
                                                AND selected.scope_id = account.scope_id)
                                         OR account.count_ledger_cursor IS DISTINCT FROM (
                                             SELECT completion.count_ledger_cursor
                                               FROM public.stocktake_scope_count_completions AS completion
                                              WHERE completion.task_id = row.task_id
                                                AND completion.scope_id = account.scope_id
                                                AND completion.round_id = account.effective_round_id)))
                                OR ((SELECT count(*)
                                       FROM public.stocktake_scopes AS scope
                                      WHERE scope.task_id = row.task_id
                                        AND scope.owner_org_id = stock_account.owner_org_id
                                        AND scope.location_id = stock_account.location_id
                                        AND (scope.material_id IS NULL OR
                                             scope.material_id = stock_account.material_id)
                                        AND (scope.condition_code IS NULL OR
                                             scope.condition_code = stock_account.condition_code)
                                        AND (scope.availability_bucket IS NULL OR
                                             scope.availability_bucket = stock_account.availability_bucket)) = 0
                                    AND (account.account_role <> 'posting_counterpart'
                                         OR account.scope_id IS NOT NULL
                                         OR account.effective_round_id IS NOT NULL))))
                    OR EXISTS (
                        SELECT 1
                          FROM public.{RECONCILIATION_ACCOUNT} AS account
                          JOIN public.stock_accounts AS stock_account
                            ON stock_account.id = account.stock_account_id
                         WHERE account.completion_id = row.id
                           AND account.scope_id IS NOT NULL
                           AND ((SELECT count(*)
                                  FROM public.stocktake_count_lines AS line
                                 WHERE line.task_id = row.task_id
                                   AND line.scope_id = account.scope_id
                                   AND line.round_id = account.effective_round_id
                                   AND line.stock_account_id = account.stock_account_id)
                                +
                                (SELECT count(*)
                                   FROM public.stocktake_count_observations AS observation
                                  WHERE observation.task_id = row.task_id
                                    AND observation.scope_id = account.scope_id
                                    AND observation.round_id = account.effective_round_id
                                    AND observation.verification_status = 'verified'
                                    AND observation.material_id = stock_account.material_id
                                    AND observation.owner_org_id = stock_account.owner_org_id
                                    AND observation.location_id = stock_account.location_id
                                    AND observation.custodian_person_id_snapshot
                                        IS NOT DISTINCT FROM stock_account.custodian_person_id
                                    AND observation.condition_code = stock_account.condition_code
                                    AND observation.availability_bucket = stock_account.availability_bucket
                                    AND observation.lot_id IS NOT DISTINCT FROM stock_account.lot_id) > 1
                                OR account.physical_qty_at_count IS DISTINCT FROM
                                   COALESCE((
                                       SELECT sum(line.counted_qty)
                                         FROM public.stocktake_count_lines AS line
                                        WHERE line.task_id = row.task_id
                                          AND line.scope_id = account.scope_id
                                          AND line.round_id = account.effective_round_id
                                          AND line.stock_account_id = account.stock_account_id), 0)
                                   + COALESCE((
                                       SELECT sum(observation.counted_qty)
                                         FROM public.stocktake_count_observations AS observation
                                        WHERE observation.task_id = row.task_id
                                          AND observation.scope_id = account.scope_id
                                          AND observation.round_id = account.effective_round_id
                                          AND observation.verification_status = 'verified'
                                          AND observation.material_id = stock_account.material_id
                                          AND observation.owner_org_id = stock_account.owner_org_id
                                          AND observation.location_id = stock_account.location_id
                                          AND observation.custodian_person_id_snapshot
                                              IS NOT DISTINCT FROM stock_account.custodian_person_id
                                          AND observation.condition_code = stock_account.condition_code
                                          AND observation.availability_bucket = stock_account.availability_bucket
                                          AND observation.lot_id IS NOT DISTINCT FROM stock_account.lot_id), 0)
                                OR account.book_qty_at_count IS DISTINCT FROM COALESCE((
                                    SELECT sum(
                                        CASE WHEN movement.to_account_id = account.stock_account_id
                                             THEN movement.quantity ELSE 0 END
                                        - CASE WHEN movement.from_account_id = account.stock_account_id
                                               THEN movement.quantity ELSE 0 END)
                                      FROM public.inventory_transactions AS transaction_row
                                      JOIN public.inventory_movements AS movement
                                        ON movement.transaction_id = transaction_row.id
                                     WHERE transaction_row.status = 'posted'
                                       AND transaction_row.ledger_cursor <= account.count_ledger_cursor
                                       AND (movement.from_account_id = account.stock_account_id
                                            OR movement.to_account_id = account.stock_account_id)), 0)
                                OR account.ledger_delta_after_count IS DISTINCT FROM COALESCE((
                                    SELECT sum(
                                        CASE WHEN movement.to_account_id = account.stock_account_id
                                             THEN movement.quantity ELSE 0 END
                                        - CASE WHEN movement.from_account_id = account.stock_account_id
                                               THEN movement.quantity ELSE 0 END)
                                      FROM public.inventory_transactions AS transaction_row
                                      JOIN public.inventory_movements AS movement
                                        ON movement.transaction_id = transaction_row.id
                                     WHERE transaction_row.status = 'posted'
                                       AND transaction_row.ledger_cursor > account.count_ledger_cursor
                                       AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                                       AND (movement.from_account_id = account.stock_account_id
                                            OR movement.to_account_id = account.stock_account_id)), 0)
                                OR account.physical_delta_after_count IS DISTINCT FROM COALESCE((
                                    SELECT sum(
                                        CASE WHEN movement.to_account_id = account.stock_account_id
                                             THEN movement.quantity ELSE 0 END
                                        - CASE WHEN movement.from_account_id = account.stock_account_id
                                               THEN movement.quantity ELSE 0 END)
                                      FROM public.inventory_transactions AS transaction_row
                                      JOIN public.inventory_movements AS movement
                                        ON movement.transaction_id = transaction_row.id
                                     WHERE transaction_row.status = 'posted'
                                       AND transaction_row.ledger_cursor > account.count_ledger_cursor
                                       AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                                       AND NOT EXISTS (
                                           SELECT 1
                                             FROM public.stocktake_posting_completion_items AS own_item
                                            WHERE own_item.completion_id = row.posting_completion_id
                                              AND own_item.inventory_transaction_id = transaction_row.id)
                                       AND (movement.from_account_id = account.stock_account_id
                                            OR movement.to_account_id = account.stock_account_id)), 0)))
                    OR EXISTS (
                        SELECT 1
                          FROM public.stocktake_count_observations AS observation
                          JOIN public.stocktake_effective_approval_scopes AS selected
                            ON selected.completion_id = posting_row.effective_approval_completion_id
                           AND selected.scope_id = observation.scope_id
                           AND selected.source_round_id = observation.round_id
                         WHERE observation.task_id = row.task_id
                           AND (observation.verification_status <> 'verified'
                                OR observation.material_id IS NULL
                                OR (SELECT count(*)
                                      FROM public.stock_accounts AS stock_account
                                     WHERE stock_account.owner_org_id = observation.owner_org_id
                                       AND stock_account.location_id = observation.location_id
                                       AND stock_account.custodian_person_id
                                           IS NOT DISTINCT FROM observation.custodian_person_id_snapshot
                                       AND stock_account.material_id = observation.material_id
                                       AND stock_account.condition_code = observation.condition_code
                                       AND stock_account.availability_bucket = observation.availability_bucket
                                       AND stock_account.lot_id IS NOT DISTINCT FROM observation.lot_id
                                       AND EXISTS (
                                           SELECT 1 FROM public.{RECONCILIATION_ACCOUNT} AS account
                                            WHERE account.completion_id = row.id
                                              AND account.scope_id = observation.scope_id
                                              AND account.stock_account_id = stock_account.id)) <> 1))
                    OR NOT ({serial_physical_reproof})
                    OR EXISTS (
                        SELECT 1
                          FROM public.{RECONCILIATION_ACCOUNT} AS account
                          JOIN public.stock_accounts AS stock_account
                            ON stock_account.id = account.stock_account_id
                         WHERE account.completion_id = row.id
                           AND (account.account_dimension_sha256 <>
                                    {account_dimension_hash}
                                OR account.item_manifest_sha256 <>
                                    {account_item_hash}))
                    OR EXISTS (
                        SELECT 1 FROM public.{RECONCILIATION_ACCOUNT} AS account
                         WHERE account.completion_id = row.id
                           AND (account.ledger_qty <> COALESCE((
                               SELECT sum(
                                   CASE WHEN movement.to_account_id = account.stock_account_id
                                        THEN movement.quantity ELSE 0 END
                                   - CASE WHEN movement.from_account_id = account.stock_account_id
                                          THEN movement.quantity ELSE 0 END)
                                 FROM public.inventory_transactions AS transaction_row
                                 JOIN public.inventory_movements AS movement
                                   ON movement.transaction_id = transaction_row.id
                                WHERE transaction_row.status = 'posted'
                                  AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                                  AND (movement.from_account_id = account.stock_account_id
                                       OR movement.to_account_id = account.stock_account_id)), 0)
                                OR account.last_touch_ledger_cursor <> COALESCE((
                                    SELECT max(transaction_row.ledger_cursor)
                                      FROM public.inventory_transactions AS transaction_row
                                      JOIN public.inventory_movements AS movement
                                        ON movement.transaction_id = transaction_row.id
                                     WHERE transaction_row.status = 'posted'
                                       AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                                       AND ((movement.from_account_id = account.stock_account_id
                                             AND movement.to_account_id IS DISTINCT FROM account.stock_account_id)
                                            OR (movement.to_account_id = account.stock_account_id
                                                AND movement.from_account_id IS DISTINCT FROM account.stock_account_id))), 0)))
                    OR EXISTS (
                        SELECT 1 FROM public.{RECONCILIATION_SERIAL} AS serial
                         WHERE serial.completion_id = row.id
                           AND (serial.item_manifest_sha256 <> {serial_item_hash}
                                OR serial.ledger_last_movement_id IS DISTINCT FROM (
                               SELECT movement.id
                                 FROM public.inventory_movement_serials AS binding
                                 JOIN public.inventory_movements AS movement
                                   ON movement.id = binding.movement_id
                                  AND movement.transaction_id = binding.transaction_id
                                 JOIN public.inventory_transactions AS transaction_row
                                   ON transaction_row.id = binding.transaction_id
                                WHERE binding.serial_id = serial.serial_id
                                  AND transaction_row.status = 'posted'
                                  AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                                ORDER BY transaction_row.ledger_cursor DESC,
                                         movement.line_no DESC, movement.id DESC
                                LIMIT 1)
                                OR serial.expected_current_account_id IS DISTINCT FROM (
                                    SELECT movement.to_account_id
                                      FROM public.inventory_movement_serials AS binding
                                      JOIN public.inventory_movements AS movement
                                        ON movement.id = binding.movement_id
                                       AND movement.transaction_id = binding.transaction_id
                                      JOIN public.inventory_transactions AS transaction_row
                                        ON transaction_row.id = binding.transaction_id
                                     WHERE binding.serial_id = serial.serial_id
                                       AND transaction_row.status = 'posted'
                                       AND transaction_row.ledger_cursor <= row.reconciliation_ledger_cursor
                                     ORDER BY transaction_row.ledger_cursor DESC,
                                              movement.line_no DESC, movement.id DESC
                                     LIMIT 1)))
                    OR (SELECT count(*)
                          FROM public.audit_events AS event
                         WHERE event.stream_key = 'inventory'
                           AND event.action = 'stocktake.nonopening.close_reconciliation_recorded'
                           AND event.aggregate_type = 'stocktake_close_reconciliation'
                           AND event.aggregate_id = CAST(row.id AS text)
                           AND event.actor_user_id = row.reconciled_by_user_id
                           AND event.occurred_at = row.reconciled_at
                           AND event.before_jsonb IS NOT DISTINCT FROM
                               jsonb_build_object(
                                   'status', 'posted',
                                   'task_version', row.expected_task_version)
                           AND event.after_jsonb IS NOT DISTINCT FROM
                               {reconciliation_event_document}
                           AND event.request_id ~
                               '^stocktake-close-reconcile-request-[0-9a-f]{{64}}$') <> 1)
        ) THEN
            RAISE EXCEPTION 'non-opening stocktake reconciliation graph is invalid';
        END IF;
        SELECT * INTO latest FROM public.{RECONCILIATION}
         WHERE task_id = target_task_id ORDER BY reconciliation_no DESC LIMIT 1;
        IF latest.reconciliation_ledger_cursor = (
               SELECT head.next_cursor - 1
                 FROM public.inventory_ledger_heads AS head
                WHERE head.stream_key = 'inventory')
           AND (EXISTS (
               SELECT 1 FROM public.{RECONCILIATION_ACCOUNT} AS account
                LEFT JOIN public.stock_balances AS balance
                  ON balance.stock_account_id = account.stock_account_id
               WHERE account.completion_id = latest.id
                 AND (COALESCE(balance.quantity, 0) <> account.balance_qty
                      OR balance.ledger_cursor IS DISTINCT FROM account.balance_ledger_cursor))
                OR EXISTS (
               SELECT 1 FROM public.{RECONCILIATION_SERIAL} AS serial
                LEFT JOIN public.serial_current_positions AS position
                  ON position.serial_id = serial.serial_id
               WHERE serial.completion_id = latest.id
                 AND (position.last_movement_id IS DISTINCT FROM
                          serial.current_position_last_movement_id
                      OR position.stock_account_id IS DISTINCT FROM
                          serial.current_position_account_id))) THEN
            RAISE EXCEPTION 'current non-opening stocktake reconciliation projection is invalid';
        END IF;
    END IF;
    IF task_row.status = 'posted' THEN
        IF close_count <> 0 OR task_row.closed_at IS NOT NULL OR
           task_row.version <> COALESCE(latest.reconciled_task_version, posting_row.posted_task_version) THEN
            RAISE EXCEPTION 'posted non-opening stocktake reconciliation tail is invalid';
        END IF;
    ELSIF task_row.status = 'closed' THEN
        IF reconciliation_count = 0 OR close_count <> 1 OR NOT EXISTS (
            SELECT 1 FROM public.{CLOSE_COMPLETION} AS close_row
             WHERE close_row.task_id = target_task_id
               AND close_row.reconciliation_completion_id = latest.id
               AND close_row.posting_completion_id = posting_row.id
               AND close_row.reconciliation_no = latest.reconciliation_no
               AND close_row.reconciliation_ledger_cursor = latest.reconciliation_ledger_cursor
               AND close_row.expected_task_version = latest.reconciled_task_version
               AND close_row.closed_task_version = task_row.version
               AND close_row.closed_task_version = close_row.expected_task_version + 1
               AND close_row.closed_at = task_row.closed_at
               AND task_row.updated_at = task_row.closed_at
               AND close_row.closed_at > latest.reconciled_at
               AND close_row.posting_manifest_sha256 = posting_row.posting_manifest_sha256
               AND close_row.reconciliation_manifest_sha256 = latest.reconciliation_manifest_sha256
               AND close_row.authorization_sha256 = {close_authorization_hash}
               AND close_row.request_sha256 = {close_request_hash}
               AND close_row.close_manifest_sha256 = {close_manifest_hash}
               AND close_row.reconciliation_ledger_cursor = (
                   SELECT head.next_cursor - 1
                     FROM public.inventory_ledger_heads AS head
                    WHERE head.stream_key = 'inventory')
               AND (SELECT count(*)
                      FROM public.state_transition_events AS event
                     WHERE event.aggregate_type = 'stocktake_task'
                       AND event.aggregate_id = CAST(target_task_id AS text)
                       AND event.from_status = 'posted' AND event.to_status = 'closed'
                       AND event.reason = 'nonopening_stocktake_closed_after_internal_reconciliation'
                       AND event.actor_id = close_row.closed_by_user_id
                       AND event.occurred_at = close_row.closed_at
                       AND event.metadata_jsonb IS NOT DISTINCT FROM
                           {close_event_document}
                       AND event.idempotency_key =
                           'stocktake-close-close-state-' ||
                           encode(sha256(convert_to(
                               'close-state' || close_row.id::text, 'UTF8')), 'hex')) = 1
               AND (SELECT count(*)
                      FROM public.audit_events AS event
                     WHERE event.stream_key = 'inventory'
                       AND event.action = 'stocktake.nonopening.closed'
                       AND event.aggregate_type = 'stocktake_close_completion'
                       AND event.aggregate_id = CAST(close_row.id AS text)
                       AND event.actor_user_id = close_row.closed_by_user_id
                       AND event.occurred_at = close_row.closed_at
                       AND event.before_jsonb IS NOT DISTINCT FROM
                           jsonb_build_object(
                               'status', 'posted',
                               'task_version', close_row.expected_task_version)
                       AND event.after_jsonb IS NOT DISTINCT FROM
                           {close_event_document}
                       AND event.request_id ~
                           '^stocktake-close-close-request-[0-9a-f]{{64}}$') = 1) THEN
            RAISE EXCEPTION 'closed non-opening stocktake graph is invalid';
        END IF;
    ELSIF reconciliation_count <> 0 OR close_count <> 0 THEN
        RAISE EXCEPTION 'non-opening reconciliation cannot precede posting';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$
"""


def _apply_postgresql_acl() -> None:
    tables = ", ".join(f"public.{name}" for name in NEW_TABLES)
    command_tables = ", ".join(
        f"public.{name}" for name in COMMAND_FACT_TABLES
    )
    op.execute(f"REVOKE ALL ON TABLE {tables} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON TABLE {tables} FROM {PRODUCTION_API_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE {command_tables} TO {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"GRANT SELECT ON TABLE public.{TRANSITION_ACK} TO {PRODUCTION_API_ROLE}"
    )
    lock_signature = f"public.{PG_LOCK_FUNCTION}(uuid)"
    graph_signature = f"public.{PG_GRAPH_FUNCTION}()"
    immutable_signature = f"public.{PG_IMMUTABLE_FUNCTION}()"
    ack_signature = f"public.{PG_ACK_FUNCTION}()"
    ack_guard_signature = f"public.{PG_ACK_GUARD_FUNCTION}()"
    event_guard_signature = f"public.{PG_EVENT_GUARD_FUNCTION}()"
    internal_signatures = (
        graph_signature,
        immutable_signature,
        ack_signature,
        ack_guard_signature,
        event_guard_signature,
    )
    for signature in (lock_signature, *internal_signatures):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{MIGRATION_ROLE}') THEN
        EXECUTE 'ALTER FUNCTION {lock_signature} OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION {graph_signature} OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION {immutable_signature} OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION {ack_signature} OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION {ack_guard_signature} OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION {event_guard_signature} OWNER TO {MIGRATION_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION {lock_signature} TO {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {graph_signature} FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {immutable_signature} FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {ack_signature} FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {ack_guard_signature} FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {event_guard_signature} FROM {PRODUCTION_API_ROLE}';
    END IF;
END
$$
"""
    )


def _revoke_postgresql_acl() -> None:
    tables = ", ".join(f"public.{name}" for name in NEW_TABLES)
    op.execute(f"REVOKE ALL ON TABLE {tables} FROM {PRODUCTION_API_ROLE}")
