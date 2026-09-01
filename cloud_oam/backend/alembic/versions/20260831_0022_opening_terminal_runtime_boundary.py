"""Harden opening terminal facts and activate their minimum runtime ACL.

Revision ID: 20260831_0022
Revises: 20260831_0021
Create Date: 2026-08-31

The opening posting, its movement bindings and every establishment are final
facts.  Revision 0010 rejected row updates/deletes, but its PostgreSQL guards
were origin-only and did not cover TRUNCATE.  It also allowed one opening
posting per round, rather than one opening posting for the whole task.  This
revision installs round-independent, task-serialized uniqueness and ALWAYS
terminal guards without rewriting persisted business data.

PostgreSQL also receives the exact API-role privileges required by the
currently mounted opening-finalization boundary.  Projection and terminal
state changes use column UPDATE grants; no business table receives DELETE,
TRUNCATE, REFERENCES or TRIGGER.  SQLite remains an ACL-free local test
dialect.  It exercises UPDATE/DELETE immutability and uniqueness, but SQLite
has neither PostgreSQL deferred constraint triggers nor table TRUNCATE; the
service's in-transaction final re-proof remains the local-only commit guard.
"""

from __future__ import annotations

from typing import Mapping, Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260831_0022"
down_revision: Union[str, Sequence[str], None] = "20260831_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"

TERMINAL_TABLES = (
    "stocktake_postings",
    "stocktake_posting_items",
    "inventory_opening_establishments",
)
LEDGER_FACT_TABLES = (
    "inventory_transactions",
    "inventory_movements",
    "inventory_movement_serials",
)
PG_IMMUTABLE_FUNCTION = "rsc_block_opening_terminal_mutation_0022"
PG_LEDGER_IMMUTABLE_FUNCTION = "rsc_block_inventory_ledger_mutation_0022"
PG_UNIQUENESS_FUNCTION = "rsc_validate_opening_posting_unique_0022"
PG_GRAPH_CHECK_FUNCTION = "rsc_opening_terminal_graph_complete_0022"
PG_COMMIT_FUNCTION = "rsc_require_opening_terminal_graph_0022"
PG_TRANSACTION_COMMIT_TRIGGER = (
    "trg_inventory_transactions_opening_commit_0022"
)
PG_MOVEMENT_COMMIT_TRIGGER = "trg_inventory_movements_opening_commit_0022"
PG_MOVEMENT_SERIAL_COMMIT_TRIGGER = (
    "trg_inventory_movement_serials_opening_commit_0022"
)
PG_POSTING_COMMIT_TRIGGER = "trg_stocktake_postings_opening_commit_0022"
PG_POSTING_ITEM_COMMIT_TRIGGER = (
    "trg_stocktake_posting_items_opening_commit_0022"
)
PG_ESTABLISHMENT_COMMIT_TRIGGER = (
    "trg_inventory_opening_establishments_commit_0022"
)
PG_FREEZE_COMMIT_TRIGGER = "trg_inventory_freezes_opening_commit_0022"
PG_TASK_COMMIT_TRIGGER = "trg_stocktake_tasks_opening_commit_0022"
PG_STATE_COMMIT_TRIGGER = (
    "trg_state_transition_events_opening_commit_0022"
)
PG_OUTBOX_COMMIT_TRIGGER = "trg_outbox_events_opening_commit_0022"
PG_AUDIT_COMMIT_TRIGGER = "trg_audit_events_opening_commit_0022"
PG_COMMIT_TRIGGER_SPECS = (
    ("inventory_transactions", PG_TRANSACTION_COMMIT_TRIGGER, "INSERT"),
    ("inventory_movements", PG_MOVEMENT_COMMIT_TRIGGER, "INSERT"),
    (
        "inventory_movement_serials",
        PG_MOVEMENT_SERIAL_COMMIT_TRIGGER,
        "INSERT",
    ),
    ("stocktake_postings", PG_POSTING_COMMIT_TRIGGER, "INSERT"),
    ("stocktake_posting_items", PG_POSTING_ITEM_COMMIT_TRIGGER, "INSERT"),
    (
        "inventory_opening_establishments",
        PG_ESTABLISHMENT_COMMIT_TRIGGER,
        "INSERT",
    ),
    ("inventory_freezes", PG_FREEZE_COMMIT_TRIGGER, "UPDATE"),
    ("stocktake_tasks", PG_TASK_COMMIT_TRIGGER, "UPDATE"),
    ("state_transition_events", PG_STATE_COMMIT_TRIGGER, "INSERT"),
    ("outbox_events", PG_OUTBOX_COMMIT_TRIGGER, "INSERT"),
    ("audit_events", PG_AUDIT_COMMIT_TRIGGER, "INSERT"),
)
PG_GRAPH_TABLES = (
    "audit_events",
    "inventory_freezes",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_transactions",
    "outbox_events",
    "state_transition_events",
    "stocktake_count_lines",
    "stocktake_count_serials",
    "stocktake_posting_items",
    "stocktake_postings",
    "stocktake_reviews",
    "stocktake_rounds",
    "stocktake_scopes",
    "stocktake_tasks",
)
OPENING_POSTING_UNIQUE_INDEX = (
    "uq_stocktake_postings_one_opening_task_0022"
)
PG_ROW_TRIGGERS = {
    table_name: f"trg_{table_name}_terminal_0022"
    for table_name in TERMINAL_TABLES
}
PG_TRUNCATE_TRIGGERS = {
    table_name: f"trg_{table_name}_terminal_truncate_0022"
    for table_name in TERMINAL_TABLES
}
PG_LEDGER_ROW_TRIGGERS = {
    table_name: f"trg_{table_name}_immutable_0022"
    for table_name in LEDGER_FACT_TABLES
}
PG_LEDGER_TRUNCATE_TRIGGERS = {
    table_name: f"trg_{table_name}_immutable_truncate_0022"
    for table_name in LEDGER_FACT_TABLES
}
OLD_PG_LEDGER_TRIGGERS = {
    table_name: f"trg_{table_name}_immutable"
    for table_name in LEDGER_FACT_TABLES
}
PG_UNIQUENESS_TRIGGER = "trg_stocktake_postings_opening_unique_0022"
OLD_PG_IMMUTABLE_TRIGGERS = {
    table_name: f"trg_{table_name}_immutable_0010"
    for table_name in TERMINAL_TABLES
}

SQLITE_UPDATE_TRIGGERS = {
    table_name: f"trg_{table_name}_terminal_update_0022"
    for table_name in TERMINAL_TABLES
}
SQLITE_DELETE_TRIGGERS = {
    table_name: f"trg_{table_name}_terminal_delete_0022"
    for table_name in TERMINAL_TABLES
}
SQLITE_UNIQUENESS_TRIGGER = PG_UNIQUENESS_TRIGGER
OLD_SQLITE_UPDATE_TRIGGERS = {
    table_name: f"trg_{table_name}_immutable_update_0010"
    for table_name in TERMINAL_TABLES
}
OLD_SQLITE_DELETE_TRIGGERS = {
    table_name: f"trg_{table_name}_immutable_delete_0010"
    for table_name in TERMINAL_TABLES
}

API_READ_TABLES = (
    "audit_chain_heads",
    "audit_events",
    "auth_identities",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "custody_assignments",
    "external_objects",
    "external_object_versions",
    "inventory_freezes",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_serials",
    "inventory_transactions",
    "login_challenges",
    "material_inventory_policies",
    "materials",
    "organizations",
    "outbox_events",
    "people",
    "permissions",
    "role_assignments",
    "role_permissions",
    "roles",
    "serial_current_positions",
    "source_systems",
    "state_transition_events",
    "stock_accounts",
    "stock_balances",
    "stock_locations",
    "stocktake_control_snapshot_lines",
    "stocktake_count_lines",
    "stocktake_count_observations",
    "stocktake_count_serials",
    "stocktake_difference_set_completions",
    "stocktake_differences",
    "stocktake_observation_dispositions",
    "stocktake_posting_items",
    "stocktake_postings",
    "stocktake_recount_cases",
    "stocktake_recount_scope_assignments",
    "stocktake_review_items",
    "stocktake_reviews",
    "stocktake_round_submissions",
    "stocktake_rounds",
    "stocktake_scope_count_completions",
    "stocktake_scopes",
    "stocktake_snapshot_lines",
    "stocktake_tasks",
    "sync_batches",
    "sync_inbox_events",
    "sync_runs",
    "users",
)
API_INSERT_TABLES = (
    "audit_events",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_transactions",
    "login_challenges",
    "outbox_events",
    "role_assignments",
    "serial_current_positions",
    "state_transition_events",
    "stock_balances",
    "stocktake_posting_items",
    "stocktake_postings",
)
API_UPDATE_TABLES = (
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "login_challenges",
    "role_assignments",
    "users",
)
API_DELETE_TABLES = ("auth_login_rate_limit_buckets",)
API_UPDATE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "audit_chain_heads": (
        "last_event_id",
        "last_hash",
        "version",
        "updated_at",
    ),
    "inventory_freezes": (
        "status",
        "valid_to",
        "released_by_user_id",
        "release_reason",
        "version",
        "updated_at",
    ),
    "inventory_ledger_heads": ("next_cursor", "updated_at"),
    "serial_current_positions": (
        "stock_account_id",
        "last_movement_id",
        "updated_at",
    ),
    "stock_balances": (
        "quantity",
        "ledger_cursor",
        "version",
        "updated_at",
    ),
    "stocktake_tasks": (
        "status",
        "posted_at",
        "closed_at",
        "version",
        "updated_at",
    ),
}

PREVIOUS_API_READ_TABLES = (
    "audit_chain_heads",
    "audit_events",
    "auth_identities",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "custody_assignments",
    "inventory_ledger_heads",
    "inventory_transactions",
    "login_challenges",
    "organizations",
    "people",
    "permissions",
    "role_assignments",
    "role_permissions",
    "roles",
    "state_transition_events",
    "stock_accounts",
    "stock_locations",
    "users",
)
PREVIOUS_API_INSERT_TABLES = (
    "audit_events",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "login_challenges",
    "role_assignments",
    "state_transition_events",
)
PREVIOUS_API_UPDATE_TABLES = (
    "audit_chain_heads",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "login_challenges",
    "role_assignments",
    "users",
)
PREVIOUS_API_DELETE_TABLES = API_DELETE_TABLES

UPGRADE_BLOCKER = (
    "0022 preflight failed: existing opening posting or transaction graph is "
    "duplicate, incomplete or inconsistent"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0022: opening posting or establishment facts require "
    "terminal database guards"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0022 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0022 SQLite upgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
        _online_preflight()
        _replace_sqlite_terminal_guards(harden=True)
        return

    _postgresql_upgrade_preflight()
    _replace_postgresql_terminal_guards(harden=True)
    _apply_postgresql_runtime_acl(current=True)


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0022 SQLite downgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
        _online_downgrade_preflight()
        _replace_sqlite_terminal_guards(harden=False)
        return

    _postgresql_downgrade_preflight()
    _apply_postgresql_runtime_acl(current=False)
    _replace_postgresql_terminal_guards(harden=False)


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _online_preflight() -> None:
    # SQLite has no deferred constraint triggers.  Do not pretend that an
    # online row-by-row query is equivalent to the PostgreSQL commit guard for
    # a pre-existing graph; local databases with opening facts must be
    # reviewed/rebuilt rather than silently grandfathered.
    if op.get_bind().exec_driver_sql(
        "SELECT 1 WHERE EXISTS (SELECT 1 FROM stocktake_postings "
        "WHERE posting_kind = 'opening') OR EXISTS ("
        "SELECT 1 FROM inventory_transactions WHERE movement_type = 'opening')"
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _online_downgrade_preflight() -> None:
    conditions = " OR ".join(
        f"EXISTS (SELECT 1 FROM {table_name})" for table_name in TERMINAL_TABLES
    )
    conditions += (
        " OR EXISTS (SELECT 1 FROM inventory_transactions "
        "WHERE movement_type = 'opening')"
    )
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {conditions}"
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _postgresql_upgrade_preflight() -> None:
    graph_tables = ", ".join(
        f"public.{table_name}" for table_name in PG_GRAPH_TABLES
    )
    op.execute(f"LOCK TABLE {graph_tables} IN ACCESS EXCLUSIVE MODE")
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_postings
         WHERE posting_kind = 'opening'
         GROUP BY task_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _postgresql_downgrade_preflight() -> None:
    graph_tables = ", ".join(
        f"public.{table_name}" for table_name in PG_GRAPH_TABLES
    )
    op.execute(f"LOCK TABLE {graph_tables} IN ACCESS EXCLUSIVE MODE")
    conditions = " OR ".join(
        f"EXISTS (SELECT 1 FROM public.{table_name})"
        for table_name in TERMINAL_TABLES
    )
    conditions += (
        " OR EXISTS (SELECT 1 FROM public.inventory_transactions "
        "WHERE movement_type = 'opening')"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF {conditions} THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _postgresql_graph_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_GRAPH_CHECK_FUNCTION}(
    p_task_id uuid,
    p_transaction_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
          JOIN public.stocktake_postings AS posting
            ON posting.task_id = task.id
           AND posting.posting_kind = 'opening'
          JOIN public.stocktake_rounds AS round_row
            ON round_row.id = posting.round_id
           AND round_row.task_id = task.id
          LEFT JOIN public.inventory_transactions AS transaction_row
            ON transaction_row.id = posting.inventory_transaction_id
         WHERE task.id = p_task_id
           AND task.task_type = 'opening'
           AND task.status IN ('posted', 'closed')
           AND task.posted_at IS NOT NULL
           AND task.posted_at = posting.posted_at
           AND (
                (task.status = 'posted'
                 AND task.closed_at IS NULL
                 AND task.updated_at = task.posted_at)
                OR
                (task.status = 'closed'
                 AND task.closed_at IS NOT NULL
                 AND task.closed_at > task.posted_at
                 AND task.updated_at = task.closed_at)
           )
           AND round_row.round_no = task.current_round_no
           AND round_row.status = 'submitted'
           AND round_row.count_manifest_sha256 IS NOT NULL
           AND posting.created_at <= posting.posted_at
           AND posting.idempotency_key_hash ~ '^[0-9a-f]{{64}}$'
           AND posting.request_hash ~ '^[0-9a-f]{{64}}$'
           AND posting.total_quantity = (
                SELECT COALESCE(sum(count_line.counted_qty), 0)
                  FROM public.stocktake_count_lines AS count_line
                 WHERE count_line.task_id = task.id
                   AND count_line.round_id = posting.round_id
           )
           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_lines AS count_line
                 WHERE count_line.task_id = task.id
                   AND count_line.round_id = posting.round_id
                   AND count_line.counted_qty > 0
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_posting_items AS item
                        WHERE item.posting_id = posting.id
                          AND item.task_id = task.id
                          AND item.round_id = posting.round_id
                          AND item.count_line_id = count_line.id
                          AND item.difference_id IS NULL
                          AND item.quantity = count_line.counted_qty
                   )
           )
           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_posting_items AS item
                  LEFT JOIN public.stocktake_count_lines AS count_line
                    ON count_line.id = item.count_line_id
                   AND count_line.task_id = item.task_id
                   AND count_line.round_id = item.round_id
                   AND count_line.counted_qty = item.quantity
                   AND count_line.counted_qty > 0
                 WHERE item.posting_id = posting.id
                   AND count_line.id IS NULL
           )
           AND (
                (
                    posting.total_quantity = 0
                    AND posting.inventory_transaction_id IS NULL
                    AND p_transaction_id IS NULL
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.inventory_transactions AS stray
                         WHERE stray.movement_type = 'opening'
                           AND stray.source_document_type = 'opening_stocktake'
                           AND stray.source_document_id = task.id::text
                    )
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_posting_items AS item
                         WHERE item.posting_id = posting.id
                    )
                )
                OR
                (
                    posting.total_quantity > 0
                    AND transaction_row.id IS NOT NULL
                    AND (p_transaction_id IS NULL
                         OR transaction_row.id = p_transaction_id)
                    AND transaction_row.movement_type = 'opening'
                    AND transaction_row.status = 'posted'
                    AND transaction_row.source_document_type =
                        'opening_stocktake'
                    AND transaction_row.source_document_id = task.id::text
                    AND transaction_row.transaction_no =
                        'OPEN-' || replace(task.id::text, '-', '')
                    AND transaction_row.posting_key =
                        'opening-stocktake:' || task.id::text
                    AND transaction_row.idempotency_key_hash =
                        pg_catalog.encode(
                            pg_catalog.sha256(
                                pg_catalog.convert_to(
                                    'cloud_oam.opening_stocktake.finalize.transaction-idempotency.v1',
                                    'UTF8'
                                )
                                || pg_catalog.decode('00', 'hex')
                                || pg_catalog.convert_to(
                                    posting.idempotency_key_hash,
                                    'UTF8'
                                )
                            ),
                            'hex'
                        )
                    AND transaction_row.request_hash =
                        pg_catalog.encode(
                            pg_catalog.sha256(
                                pg_catalog.convert_to(
                                    'cloud_oam.opening_stocktake.finalize.transaction-request.v1',
                                    'UTF8'
                                )
                                || pg_catalog.decode('00', 'hex')
                                || pg_catalog.convert_to(
                                    posting.request_hash,
                                    'UTF8'
                                )
                            ),
                            'hex'
                        )
                    AND transaction_row.actor_user_id =
                        posting.posted_by_user_id
                    AND transaction_row.effective_at = task.cutoff_at
                    AND transaction_row.posted_at = posting.posted_at
                    AND (
                        SELECT count(*)
                          FROM public.inventory_transactions AS candidate
                         WHERE candidate.movement_type = 'opening'
                           AND candidate.source_document_type =
                               'opening_stocktake'
                           AND candidate.source_document_id = task.id::text
                    ) = 1
                    AND (
                        SELECT COALESCE(sum(movement.quantity), 0)
                          FROM public.inventory_movements AS movement
                         WHERE movement.transaction_id = transaction_row.id
                    ) = posting.total_quantity
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.inventory_movements AS movement
                         WHERE movement.transaction_id = transaction_row.id
                           AND (
                                movement.from_account_id IS NOT NULL
                                OR movement.to_account_id IS NULL
                                OR movement.external_boundary_code <>
                                   'approved-opening-stocktake'
                           )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.inventory_movements AS movement
                          LEFT JOIN public.stocktake_posting_items AS item
                            ON item.posting_id = posting.id
                           AND item.inventory_movement_id = movement.id
                           AND item.task_id = task.id
                           AND item.round_id = posting.round_id
                           AND item.quantity = movement.quantity
                         WHERE movement.transaction_id = transaction_row.id
                           AND item.inventory_movement_id IS NULL
                    )
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_posting_items AS item
                          LEFT JOIN public.inventory_movements AS movement
                            ON movement.id = item.inventory_movement_id
                           AND movement.transaction_id = transaction_row.id
                           AND movement.quantity = item.quantity
                         WHERE item.posting_id = posting.id
                           AND movement.id IS NULL
                    )
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_posting_items AS item
                          JOIN public.inventory_movement_serials AS movement_serial
                            ON movement_serial.movement_id =
                               item.inventory_movement_id
                           AND movement_serial.transaction_id =
                               transaction_row.id
                          LEFT JOIN public.stocktake_count_serials AS count_serial
                            ON count_serial.count_line_id = item.count_line_id
                           AND count_serial.round_id = item.round_id
                           AND count_serial.serial_id = movement_serial.serial_id
                         WHERE item.posting_id = posting.id
                           AND count_serial.serial_id IS NULL
                    )
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_posting_items AS item
                          JOIN public.stocktake_count_serials AS count_serial
                            ON count_serial.count_line_id = item.count_line_id
                           AND count_serial.round_id = item.round_id
                          LEFT JOIN public.inventory_movement_serials AS movement_serial
                            ON movement_serial.movement_id =
                               item.inventory_movement_id
                           AND movement_serial.transaction_id =
                               transaction_row.id
                           AND movement_serial.serial_id = count_serial.serial_id
                         WHERE item.posting_id = posting.id
                           AND movement_serial.serial_id IS NULL
                    )
                )
           )
           AND (SELECT count(*) FROM public.stocktake_scopes AS scope
                 WHERE scope.task_id = task.id) > 0
           AND (SELECT count(*)
                  FROM public.inventory_opening_establishments AS establishment
                 WHERE establishment.task_id = task.id)
               = (SELECT count(*) FROM public.stocktake_scopes AS scope
                   WHERE scope.task_id = task.id)
           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_scopes AS scope
                 WHERE scope.task_id = task.id
                   AND NOT EXISTS (
                        SELECT 1
                          FROM public.inventory_opening_establishments
                               AS establishment
                         WHERE establishment.task_id = task.id
                           AND establishment.scope_id = scope.id
                           AND establishment.owner_org_id = scope.owner_org_id
                           AND establishment.location_id = scope.location_id
                           AND establishment.round_id = posting.round_id
                           AND establishment.posting_id = posting.id
                           AND establishment.cutoff_ledger_cursor =
                               task.cutoff_ledger_cursor
                           AND establishment.cutoff_at = task.cutoff_at
                           AND establishment.established_ledger_cursor =
                               COALESCE(
                                   transaction_row.ledger_cursor,
                                   task.cutoff_ledger_cursor
                               )
                           AND establishment.scope_manifest_sha256 =
                               task.scope_manifest_sha256
                           AND establishment.snapshot_manifest_sha256 =
                               task.snapshot_manifest_sha256
                           AND establishment.count_manifest_sha256 =
                               round_row.count_manifest_sha256
                           AND establishment.control_manifest_sha256 =
                               task.control_manifest_sha256
                           AND establishment.established_by_user_id =
                               posting.posted_by_user_id
                           AND establishment.established_at = posting.posted_at
                           AND establishment.created_at =
                               establishment.established_at
                           AND EXISTS (
                               SELECT 1
                                 FROM public.stocktake_reviews AS region_review
                                 JOIN public.stocktake_reviews AS hq_review
                                   ON hq_review.id =
                                      establishment.headquarters_review_id
                                  AND hq_review.task_id = task.id
                                  AND hq_review.round_id = posting.round_id
                                WHERE region_review.id =
                                      establishment.regional_review_id
                                  AND region_review.task_id = task.id
                                  AND region_review.round_id = posting.round_id
                                  AND region_review.review_stage = 'region'
                                  AND region_review.decision = 'approve'
                                  AND hq_review.review_stage = 'headquarters'
                                  AND hq_review.decision = 'approve'
                                  AND region_review.reviewer_user_id <>
                                      hq_review.reviewer_user_id
                                  AND region_review.reviewer_person_id <>
                                      hq_review.reviewer_person_id
                                  AND region_review.reviewed_at <
                                      hq_review.reviewed_at
                                  AND hq_review.reviewed_at <=
                                      posting.posted_at
                           )
                   )
           )
           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_scopes AS scope
                 WHERE scope.task_id = task.id
                   AND NOT EXISTS (
                        SELECT 1
                          FROM public.inventory_freezes AS freeze
                         WHERE freeze.task_id = task.id
                           AND freeze.stocktake_scope_id = scope.id
                           AND freeze.scope_key = scope.scope_key
                           AND freeze.status = 'released'
                           AND freeze.valid_to = posting.posted_at
                           AND freeze.released_by_user_id =
                               posting.posted_by_user_id
                           AND freeze.release_reason =
                               '期初实盘及两级复核已完成并原子入账'
                           AND freeze.updated_at = freeze.valid_to
                   )
           )
           AND (
                SELECT count(*)
                  FROM public.state_transition_events AS state_event
                 WHERE state_event.aggregate_type = 'stocktake_task'
                   AND state_event.aggregate_id = task.id::text
                   AND state_event.reason = 'opening_stocktake_posted'
           ) = 1
           AND EXISTS (
                SELECT 1
                  FROM public.state_transition_events AS state_event
                 WHERE state_event.aggregate_type = 'stocktake_task'
                   AND state_event.aggregate_id = task.id::text
                   AND state_event.from_status = 'approved'
                   AND state_event.to_status = 'posted'
                   AND state_event.reason = 'opening_stocktake_posted'
                   AND state_event.actor_id = posting.posted_by_user_id
                   AND state_event.occurred_at = posting.posted_at
                   AND state_event.created_at = posting.posted_at
           )
           AND (
                SELECT count(*)
                  FROM public.outbox_events AS outbox_event
                 WHERE outbox_event.aggregate_type = 'stocktake_task'
                   AND outbox_event.aggregate_id = task.id::text
                   AND outbox_event.event_type = 'stocktake.opening.posted'
           ) = 1
           AND EXISTS (
                SELECT 1
                  FROM public.outbox_events AS outbox_event
                 WHERE outbox_event.aggregate_type = 'stocktake_task'
                   AND outbox_event.aggregate_id = task.id::text
                   AND outbox_event.event_type = 'stocktake.opening.posted'
                   AND outbox_event.available_at = posting.posted_at
                   AND outbox_event.created_at = posting.posted_at
                   AND outbox_event.updated_at >= outbox_event.created_at
           )
           AND (
                SELECT count(*)
                  FROM public.audit_events AS audit_event
                 WHERE audit_event.stream_key = 'inventory'
                   AND audit_event.aggregate_type = 'stocktake_posting'
                   AND audit_event.aggregate_id = posting.id::text
                   AND audit_event.action = 'stocktake.opening.posted'
           ) = 1
           AND EXISTS (
                SELECT 1
                  FROM public.audit_events AS audit_event
                 WHERE audit_event.stream_key = 'inventory'
                   AND audit_event.aggregate_type = 'stocktake_posting'
                   AND audit_event.aggregate_id = posting.id::text
                   AND audit_event.action = 'stocktake.opening.posted'
                   AND audit_event.actor_user_id = posting.posted_by_user_id
                   AND audit_event.occurred_at = posting.posted_at
           )
           AND (
                task.status <> 'closed'
                OR (
                    (SELECT count(*)
                       FROM public.state_transition_events AS state_event
                      WHERE state_event.aggregate_type = 'stocktake_task'
                        AND state_event.aggregate_id = task.id::text
                        AND state_event.reason = 'opening_stocktake_closed'
                    ) = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.state_transition_events AS state_event
                         WHERE state_event.aggregate_type = 'stocktake_task'
                           AND state_event.aggregate_id = task.id::text
                           AND state_event.from_status = 'posted'
                           AND state_event.to_status = 'closed'
                           AND state_event.reason = 'opening_stocktake_closed'
                           AND state_event.actor_id IS NOT NULL
                           AND state_event.occurred_at = task.closed_at
                           AND state_event.created_at = task.closed_at
                    )
                    AND (SELECT count(*)
                           FROM public.outbox_events AS outbox_event
                          WHERE outbox_event.aggregate_type = 'stocktake_task'
                            AND outbox_event.aggregate_id = task.id::text
                            AND outbox_event.event_type =
                                'stocktake.opening.closed') = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.outbox_events AS outbox_event
                         WHERE outbox_event.aggregate_type = 'stocktake_task'
                           AND outbox_event.aggregate_id = task.id::text
                           AND outbox_event.event_type =
                               'stocktake.opening.closed'
                           AND outbox_event.available_at = task.closed_at
                           AND outbox_event.created_at = task.closed_at
                           AND outbox_event.updated_at >=
                               outbox_event.created_at
                    )
                    AND (SELECT count(*)
                           FROM public.audit_events AS audit_event
                          WHERE audit_event.stream_key = 'inventory'
                            AND audit_event.aggregate_type = 'stocktake_task'
                            AND audit_event.aggregate_id = task.id::text
                            AND audit_event.action =
                                'stocktake.opening.closed') = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.audit_events AS audit_event
                         WHERE audit_event.stream_key = 'inventory'
                           AND audit_event.aggregate_type = 'stocktake_task'
                           AND audit_event.aggregate_id = task.id::text
                           AND audit_event.action =
                               'stocktake.opening.closed'
                           AND audit_event.actor_user_id = (
                               SELECT state_event.actor_id
                                 FROM public.state_transition_events
                                      AS state_event
                                WHERE state_event.aggregate_type =
                                      'stocktake_task'
                                  AND state_event.aggregate_id = task.id::text
                                  AND state_event.reason =
                                      'opening_stocktake_closed'
                           )
                           AND audit_event.occurred_at = task.closed_at
                    )
                )
           )
           AND (
                posting.inventory_transaction_id IS NULL
                OR (
                    (SELECT count(*)
                       FROM public.state_transition_events AS state_event
                      WHERE state_event.aggregate_type =
                            'inventory_transaction'
                        AND state_event.aggregate_id = transaction_row.id::text
                    ) = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.state_transition_events AS state_event
                         WHERE state_event.aggregate_type =
                               'inventory_transaction'
                           AND state_event.aggregate_id =
                               transaction_row.id::text
                           AND state_event.from_status IS NULL
                           AND state_event.to_status = 'posted'
                           AND state_event.reason =
                               'inventory_transaction_posted'
                           AND state_event.actor_id =
                               transaction_row.actor_user_id
                           AND state_event.occurred_at =
                               transaction_row.posted_at
                           AND state_event.created_at =
                               transaction_row.posted_at
                    )
                    AND (SELECT count(*)
                           FROM public.outbox_events AS outbox_event
                          WHERE outbox_event.aggregate_type =
                                'inventory_transaction'
                            AND outbox_event.aggregate_id =
                                transaction_row.id::text
                            AND outbox_event.event_type =
                                'inventory.transaction.posted') = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.outbox_events AS outbox_event
                         WHERE outbox_event.aggregate_type =
                               'inventory_transaction'
                           AND outbox_event.aggregate_id =
                               transaction_row.id::text
                           AND outbox_event.event_type =
                               'inventory.transaction.posted'
                           AND outbox_event.available_at =
                               transaction_row.posted_at
                           AND outbox_event.created_at =
                               transaction_row.posted_at
                           AND outbox_event.updated_at >=
                               outbox_event.created_at
                    )
                    AND (SELECT count(*)
                           FROM public.audit_events AS audit_event
                          WHERE audit_event.stream_key = 'inventory'
                            AND audit_event.aggregate_type =
                                'inventory_transaction'
                            AND audit_event.aggregate_id =
                                transaction_row.id::text
                            AND audit_event.action =
                                'inventory.transaction.posted') = 1
                    AND EXISTS (
                        SELECT 1
                          FROM public.audit_events AS audit_event
                         WHERE audit_event.stream_key = 'inventory'
                           AND audit_event.aggregate_type =
                               'inventory_transaction'
                           AND audit_event.aggregate_id =
                               transaction_row.id::text
                           AND audit_event.action =
                               'inventory.transaction.posted'
                           AND audit_event.actor_user_id =
                               transaction_row.actor_user_id
                           AND audit_event.occurred_at =
                               transaction_row.posted_at
                    )
                )
           )
    )
$$
"""


def _postgresql_commit_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_COMMIT_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    opening_task_id uuid;
    opening_transaction_id uuid;
    current_task_status text;
    parent_task_type text;
BEGIN
    IF TG_TABLE_NAME = 'inventory_transactions' THEN
        IF NEW.movement_type <> 'opening' THEN
            RETURN NEW;
        END IF;
        IF NEW.source_document_type <> 'opening_stocktake'
           OR NEW.source_document_id !~*
              '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
            RAISE EXCEPTION 'opening inventory transaction source is invalid'
                USING ERRCODE = '23514';
        END IF;
        opening_task_id := NEW.source_document_id::uuid;
        opening_transaction_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'inventory_movements' THEN
        SELECT transaction_row.source_document_id::uuid, transaction_row.id
          INTO opening_task_id, opening_transaction_id
          FROM public.inventory_transactions AS transaction_row
         WHERE transaction_row.id = NEW.transaction_id
           AND transaction_row.movement_type = 'opening'
           AND transaction_row.source_document_type = 'opening_stocktake'
           AND transaction_row.source_document_id ~*
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$';
        IF NOT FOUND THEN
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'inventory_movement_serials' THEN
        SELECT transaction_row.source_document_id::uuid, transaction_row.id
          INTO opening_task_id, opening_transaction_id
          FROM public.inventory_transactions AS transaction_row
         WHERE transaction_row.id = NEW.transaction_id
           AND transaction_row.movement_type = 'opening'
           AND transaction_row.source_document_type = 'opening_stocktake'
           AND transaction_row.source_document_id ~*
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$';
        IF NOT FOUND THEN
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'stocktake_postings' THEN
        IF NEW.posting_kind <> 'opening' THEN
            RETURN NEW;
        END IF;
        opening_task_id := NEW.task_id;
        opening_transaction_id := NEW.inventory_transaction_id;
    ELSIF TG_TABLE_NAME = 'stocktake_posting_items' THEN
        SELECT posting.task_id, posting.inventory_transaction_id
          INTO opening_task_id, opening_transaction_id
          FROM public.stocktake_postings AS posting
         WHERE posting.id = NEW.posting_id
           AND posting.posting_kind = 'opening';
        IF NOT FOUND THEN
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'inventory_opening_establishments' THEN
        opening_task_id := NEW.task_id;
        SELECT posting.inventory_transaction_id
          INTO opening_transaction_id
          FROM public.stocktake_postings AS posting
         WHERE posting.id = NEW.posting_id
           AND posting.task_id = NEW.task_id
           AND posting.posting_kind = 'opening';
    ELSIF TG_TABLE_NAME = 'inventory_freezes' THEN
        IF NEW.status <> 'released' THEN
            RETURN NEW;
        END IF;
        SELECT task.task_type
          INTO parent_task_type
          FROM public.stocktake_tasks AS task
         WHERE task.id = NEW.task_id;
        IF parent_task_type IS DISTINCT FROM 'opening' THEN
            RETURN NEW;
        END IF;
        IF OLD.id IS DISTINCT FROM NEW.id
           OR OLD.task_id IS DISTINCT FROM NEW.task_id
           OR OLD.stocktake_scope_id IS DISTINCT FROM
              NEW.stocktake_scope_id
           OR OLD.scope_key IS DISTINCT FROM NEW.scope_key
           OR OLD.freeze_mode IS DISTINCT FROM NEW.freeze_mode
           OR OLD.valid_from IS DISTINCT FROM NEW.valid_from
           OR OLD.created_by_user_id IS DISTINCT FROM
              NEW.created_by_user_id
           OR OLD.created_at IS DISTINCT FROM NEW.created_at
           OR OLD.status <> 'active'
           OR NEW.valid_to IS NULL
           OR NEW.released_by_user_id IS NULL
           OR NEW.release_reason <>
              '期初实盘及两级复核已完成并原子入账'
           OR NEW.version <> OLD.version + 1
           OR NEW.updated_at IS DISTINCT FROM NEW.valid_to THEN
            RAISE EXCEPTION 'opening freeze release is not terminal evidence'
                USING ERRCODE = '23514';
        END IF;
        opening_task_id := NEW.task_id;
        SELECT posting.inventory_transaction_id
          INTO opening_transaction_id
          FROM public.stocktake_postings AS posting
         WHERE posting.task_id = NEW.task_id
           AND posting.posting_kind = 'opening';
    ELSIF TG_TABLE_NAME = 'stocktake_tasks' THEN
        IF OLD.task_type <> 'opening' AND NEW.task_type <> 'opening' THEN
            RETURN NEW;
        END IF;
        IF OLD.id IS DISTINCT FROM NEW.id
           OR OLD.task_no IS DISTINCT FROM NEW.task_no
           OR OLD.task_type IS DISTINCT FROM NEW.task_type
           OR OLD.region_org_id IS DISTINCT FROM NEW.region_org_id
           OR OLD.blind_count IS DISTINCT FROM NEW.blind_count
           OR OLD.cutoff_ledger_cursor IS DISTINCT FROM
              NEW.cutoff_ledger_cursor
           OR OLD.cutoff_at IS DISTINCT FROM NEW.cutoff_at
           OR OLD.scope_manifest_sha256 IS DISTINCT FROM
              NEW.scope_manifest_sha256
           OR OLD.snapshot_manifest_sha256 IS DISTINCT FROM
              NEW.snapshot_manifest_sha256
           OR OLD.control_source_system_id IS DISTINCT FROM
              NEW.control_source_system_id
           OR OLD.control_sync_run_id IS DISTINCT FROM
              NEW.control_sync_run_id
           OR OLD.control_snapshot_at IS DISTINCT FROM
              NEW.control_snapshot_at
           OR OLD.control_manifest_sha256 IS DISTINCT FROM
              NEW.control_manifest_sha256
           OR OLD.current_round_no IS DISTINCT FROM NEW.current_round_no
           OR OLD.created_by_user_id IS DISTINCT FROM
              NEW.created_by_user_id
           OR OLD.deadline IS DISTINCT FROM NEW.deadline
           OR OLD.issued_at IS DISTINCT FROM NEW.issued_at
           OR OLD.frozen_at IS DISTINCT FROM NEW.frozen_at
           OR OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
           OR OLD.cancelled_at IS DISTINCT FROM NEW.cancelled_at
           OR OLD.note IS DISTINCT FROM NEW.note
           OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'opening terminal task binding is immutable'
                USING ERRCODE = '55000';
        END IF;
        IF OLD.status = 'approved' AND NEW.status = 'posted' THEN
            IF OLD.task_type <> 'opening'
               OR NEW.task_type <> 'opening'
               OR OLD.posted_at IS NOT NULL
               OR NEW.posted_at IS NULL
               OR NEW.closed_at IS NOT NULL
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at IS DISTINCT FROM NEW.posted_at THEN
                RAISE EXCEPTION 'opening task post transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.status = 'posted' AND NEW.status = 'closed' THEN
            IF OLD.task_type <> 'opening'
               OR NEW.task_type <> 'opening'
               OR OLD.posted_at IS NULL
               OR NEW.posted_at IS DISTINCT FROM OLD.posted_at
               OR OLD.closed_at IS NOT NULL
               OR NEW.closed_at IS NULL
               OR NEW.closed_at <= NEW.posted_at
               OR NEW.version <> OLD.version + 1
               OR NEW.updated_at IS DISTINCT FROM NEW.closed_at THEN
                RAISE EXCEPTION 'opening task close transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF OLD.status IN ('posted', 'closed')
              OR NEW.status IN ('posted', 'closed') THEN
            RAISE EXCEPTION 'opening terminal task facts are immutable'
                USING ERRCODE = '55000';
        ELSE
            RETURN NEW;
        END IF;
        SELECT task.status
          INTO current_task_status
          FROM public.stocktake_tasks AS task
         WHERE task.id = NEW.id;
        IF current_task_status IS DISTINCT FROM NEW.status THEN
            RAISE EXCEPTION
                'opening post and close require independent transactions'
                USING ERRCODE = '23514';
        END IF;
        opening_task_id := NEW.id;
        SELECT posting.inventory_transaction_id
          INTO opening_transaction_id
          FROM public.stocktake_postings AS posting
         WHERE posting.task_id = NEW.id
           AND posting.posting_kind = 'opening';
    ELSIF TG_TABLE_NAME = 'state_transition_events' THEN
        IF NEW.aggregate_type = 'stocktake_task'
           AND NEW.reason IN (
               'opening_stocktake_posted',
               'opening_stocktake_closed'
           ) THEN
            IF NEW.aggregate_id !~*
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
                RAISE EXCEPTION 'opening task state aggregate is invalid'
                    USING ERRCODE = '23514';
            END IF;
            opening_task_id := NEW.aggregate_id::uuid;
            SELECT posting.inventory_transaction_id
              INTO opening_transaction_id
              FROM public.stocktake_postings AS posting
             WHERE posting.task_id = opening_task_id
               AND posting.posting_kind = 'opening';
        ELSIF NEW.aggregate_type = 'inventory_transaction'
              AND NEW.reason = 'inventory_transaction_posted' THEN
            IF NEW.aggregate_id !~*
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
                RETURN NEW;
            END IF;
            SELECT transaction_row.source_document_id::uuid,
                   transaction_row.id
              INTO opening_task_id, opening_transaction_id
              FROM public.inventory_transactions AS transaction_row
             WHERE transaction_row.id = NEW.aggregate_id::uuid
               AND transaction_row.movement_type = 'opening'
               AND transaction_row.source_document_type =
                   'opening_stocktake'
               AND transaction_row.source_document_id ~*
                   '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$';
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
        ELSE
            RETURN NEW;
        END IF;
    ELSIF TG_TABLE_NAME = 'outbox_events' THEN
        IF NEW.aggregate_type = 'stocktake_task'
           AND NEW.event_type IN (
               'stocktake.opening.posted',
               'stocktake.opening.closed'
           ) THEN
            IF NEW.aggregate_id !~*
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
                RAISE EXCEPTION 'opening task outbox aggregate is invalid'
                    USING ERRCODE = '23514';
            END IF;
            opening_task_id := NEW.aggregate_id::uuid;
            SELECT posting.inventory_transaction_id
              INTO opening_transaction_id
              FROM public.stocktake_postings AS posting
             WHERE posting.task_id = opening_task_id
               AND posting.posting_kind = 'opening';
        ELSIF NEW.aggregate_type = 'inventory_transaction'
              AND NEW.event_type = 'inventory.transaction.posted' THEN
            IF NEW.aggregate_id !~*
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
                RETURN NEW;
            END IF;
            SELECT transaction_row.source_document_id::uuid,
                   transaction_row.id
              INTO opening_task_id, opening_transaction_id
              FROM public.inventory_transactions AS transaction_row
             WHERE transaction_row.id = NEW.aggregate_id::uuid
               AND transaction_row.movement_type = 'opening'
               AND transaction_row.source_document_type =
                   'opening_stocktake'
               AND transaction_row.source_document_id ~*
                   '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$';
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
        ELSE
            RETURN NEW;
        END IF;
        IF NEW.status <> 'pending'
           OR NEW.attempts <> 0
           OR NEW.locked_at IS NOT NULL
           OR NEW.locked_by IS NOT NULL
           OR NEW.published_at IS NOT NULL
           OR NEW.last_error IS NOT NULL
           OR NEW.available_at IS DISTINCT FROM NEW.created_at
           OR NEW.updated_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'opening outbox creation state is invalid'
                USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'audit_events' THEN
        IF NEW.aggregate_type = 'stocktake_posting'
           AND NEW.action = 'stocktake.opening.posted' THEN
            IF NEW.stream_key <> 'inventory'
               OR NEW.aggregate_id !~*
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
                RAISE EXCEPTION 'opening posting audit aggregate is invalid'
                    USING ERRCODE = '23514';
            END IF;
            SELECT posting.task_id, posting.inventory_transaction_id
              INTO opening_task_id, opening_transaction_id
              FROM public.stocktake_postings AS posting
             WHERE posting.id = NEW.aggregate_id::uuid
               AND posting.posting_kind = 'opening';
        ELSIF NEW.aggregate_type = 'stocktake_task'
              AND NEW.action = 'stocktake.opening.closed' THEN
            IF NEW.stream_key <> 'inventory'
               OR NEW.aggregate_id !~*
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
                RAISE EXCEPTION 'opening close audit aggregate is invalid'
                    USING ERRCODE = '23514';
            END IF;
            opening_task_id := NEW.aggregate_id::uuid;
            SELECT posting.inventory_transaction_id
              INTO opening_transaction_id
              FROM public.stocktake_postings AS posting
             WHERE posting.task_id = opening_task_id
               AND posting.posting_kind = 'opening';
        ELSIF NEW.aggregate_type = 'inventory_transaction'
              AND NEW.action = 'inventory.transaction.posted' THEN
            IF NEW.aggregate_id !~*
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
                RETURN NEW;
            END IF;
            SELECT transaction_row.source_document_id::uuid,
                   transaction_row.id
              INTO opening_task_id, opening_transaction_id
              FROM public.inventory_transactions AS transaction_row
             WHERE transaction_row.id = NEW.aggregate_id::uuid
               AND transaction_row.movement_type = 'opening'
               AND transaction_row.source_document_type =
                   'opening_stocktake'
               AND transaction_row.source_document_id ~*
                   '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$';
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
        ELSE
            RETURN NEW;
        END IF;
    ELSE
        RAISE EXCEPTION 'unsupported opening terminal commit target'
            USING ERRCODE = '55000';
    END IF;

    IF NOT public.{PG_GRAPH_CHECK_FUNCTION}(
        opening_task_id,
        opening_transaction_id
    ) THEN
        RAISE EXCEPTION 'opening terminal graph is incomplete at commit'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_existing_graph_preflight() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.inventory_transactions AS transaction_row
         WHERE transaction_row.movement_type = 'opening'
           AND (
                transaction_row.source_document_type <>
                    'opening_stocktake'
                OR transaction_row.source_document_id !~*
                   '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
           )
    ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.inventory_transactions AS transaction_row
         WHERE transaction_row.movement_type = 'opening'
           AND NOT public.{PG_GRAPH_CHECK_FUNCTION}(
               transaction_row.source_document_id::uuid,
               transaction_row.id
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_postings AS posting
         WHERE posting.posting_kind = 'opening'
           AND NOT public.{PG_GRAPH_CHECK_FUNCTION}(
               posting.task_id,
               posting.inventory_transaction_id
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
         WHERE task.task_type = 'opening'
           AND task.status IN ('posted', 'closed')
           AND NOT public.{PG_GRAPH_CHECK_FUNCTION}(
               task.id,
               (
                   SELECT posting.inventory_transaction_id
                     FROM public.stocktake_postings AS posting
                    WHERE posting.task_id = task.id
                      AND posting.posting_kind = 'opening'
               )
           )
    ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _replace_postgresql_terminal_guards(*, harden: bool) -> None:
    if harden:
        for table_name in TERMINAL_TABLES:
            op.execute(
                f"DROP TRIGGER {OLD_PG_IMMUTABLE_TRIGGERS[table_name]} "
                f"ON public.{table_name}"
            )
        for table_name in LEDGER_FACT_TABLES:
            op.execute(
                f"DROP TRIGGER {OLD_PG_LEDGER_TRIGGERS[table_name]} "
                f"ON public.{table_name}"
            )
        op.execute(
            f"""
CREATE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION 'opening terminal facts are immutable'
        USING ERRCODE = '55000';
END;
$$
"""
        )
        op.execute(
            f"""
CREATE FUNCTION public.{PG_LEDGER_IMMUTABLE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION 'posted inventory ledger rows are immutable'
        USING ERRCODE = '55000';
END;
$$
"""
        )
        op.execute(
            f"""
CREATE FUNCTION public.{PG_UNIQUENESS_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.posting_kind <> 'opening' THEN
        RETURN NEW;
    END IF;
    PERFORM 1
      FROM public.stocktake_tasks
     WHERE id = NEW.task_id
     FOR UPDATE;
    IF NOT FOUND OR EXISTS (
        SELECT 1
          FROM public.stocktake_postings AS existing
         WHERE existing.task_id = NEW.task_id
           AND existing.posting_kind = 'opening'
    ) THEN
        RAISE EXCEPTION 'opening stocktake task already has a posting'
            USING ERRCODE = '23505';
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        op.execute(_postgresql_graph_function_sql())
        op.execute(_postgresql_commit_function_sql())
        _postgresql_existing_graph_preflight()
        op.create_index(
            OPENING_POSTING_UNIQUE_INDEX,
            "stocktake_postings",
            ["task_id"],
            unique=True,
            postgresql_where=sa.text("posting_kind = 'opening'"),
        )
        for table_name in TERMINAL_TABLES:
            row_trigger = PG_ROW_TRIGGERS[table_name]
            truncate_trigger = PG_TRUNCATE_TRIGGERS[table_name]
            op.execute(
                f"CREATE TRIGGER {row_trigger} BEFORE UPDATE OR DELETE ON "
                f"public.{table_name} FOR EACH ROW EXECUTE FUNCTION "
                f"public.{PG_IMMUTABLE_FUNCTION}()"
            )
            op.execute(
                f"CREATE TRIGGER {truncate_trigger} BEFORE TRUNCATE ON "
                f"public.{table_name} FOR EACH STATEMENT EXECUTE FUNCTION "
                f"public.{PG_IMMUTABLE_FUNCTION}()"
            )
            op.execute(
                f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
                f"{row_trigger}"
            )
            op.execute(
                f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
                f"{truncate_trigger}"
            )
        for table_name in LEDGER_FACT_TABLES:
            row_trigger = PG_LEDGER_ROW_TRIGGERS[table_name]
            truncate_trigger = PG_LEDGER_TRUNCATE_TRIGGERS[table_name]
            op.execute(
                f"CREATE TRIGGER {row_trigger} BEFORE UPDATE OR DELETE ON "
                f"public.{table_name} FOR EACH ROW EXECUTE FUNCTION "
                f"public.{PG_LEDGER_IMMUTABLE_FUNCTION}()"
            )
            op.execute(
                f"CREATE TRIGGER {truncate_trigger} BEFORE TRUNCATE ON "
                f"public.{table_name} FOR EACH STATEMENT EXECUTE FUNCTION "
                f"public.{PG_LEDGER_IMMUTABLE_FUNCTION}()"
            )
            op.execute(
                f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
                f"{row_trigger}"
            )
            op.execute(
                f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
                f"{truncate_trigger}"
            )
        op.execute(
            f"CREATE TRIGGER {PG_UNIQUENESS_TRIGGER} BEFORE INSERT ON "
            "public.stocktake_postings FOR EACH ROW EXECUTE FUNCTION "
            f"public.{PG_UNIQUENESS_FUNCTION}()"
        )
        op.execute(
            "ALTER TABLE public.stocktake_postings ENABLE ALWAYS TRIGGER "
            f"{PG_UNIQUENESS_TRIGGER}"
        )
        for table_name, trigger_name, event in PG_COMMIT_TRIGGER_SPECS:
            op.execute(
                f"CREATE CONSTRAINT TRIGGER {trigger_name} AFTER {event} ON "
                f"public.{table_name} DEFERRABLE INITIALLY DEFERRED "
                f"FOR EACH ROW EXECUTE FUNCTION public.{PG_COMMIT_FUNCTION}()"
            )
            op.execute(
                f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
                f"{trigger_name}"
            )
        for function_name, signature in (
            (PG_IMMUTABLE_FUNCTION, ""),
            (PG_LEDGER_IMMUTABLE_FUNCTION, ""),
            (PG_UNIQUENESS_FUNCTION, ""),
            (PG_GRAPH_CHECK_FUNCTION, "uuid, uuid"),
            (PG_COMMIT_FUNCTION, ""),
        ):
            op.execute(
                f"REVOKE EXECUTE ON FUNCTION public.{function_name}({signature}) "
                f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
            )
        return

    for table_name, trigger_name, _event in reversed(PG_COMMIT_TRIGGER_SPECS):
        op.execute(
            f"DROP TRIGGER {trigger_name} ON public.{table_name}"
        )
    op.execute(
        f"DROP TRIGGER {PG_UNIQUENESS_TRIGGER} ON public.stocktake_postings"
    )
    for table_name in TERMINAL_TABLES:
        op.execute(
            f"DROP TRIGGER {PG_TRUNCATE_TRIGGERS[table_name]} "
            f"ON public.{table_name}"
        )
        op.execute(
            f"DROP TRIGGER {PG_ROW_TRIGGERS[table_name]} "
            f"ON public.{table_name}"
        )
    for table_name in LEDGER_FACT_TABLES:
        op.execute(
            f"DROP TRIGGER {PG_LEDGER_TRUNCATE_TRIGGERS[table_name]} "
            f"ON public.{table_name}"
        )
        op.execute(
            f"DROP TRIGGER {PG_LEDGER_ROW_TRIGGERS[table_name]} "
            f"ON public.{table_name}"
        )
    op.drop_index(
        OPENING_POSTING_UNIQUE_INDEX,
        table_name="stocktake_postings",
    )
    op.execute(f"DROP FUNCTION public.{PG_COMMIT_FUNCTION}()")
    op.execute(f"DROP FUNCTION public.{PG_GRAPH_CHECK_FUNCTION}(uuid, uuid)")
    op.execute(f"DROP FUNCTION public.{PG_UNIQUENESS_FUNCTION}()")
    op.execute(f"DROP FUNCTION public.{PG_LEDGER_IMMUTABLE_FUNCTION}()")
    op.execute(f"DROP FUNCTION public.{PG_IMMUTABLE_FUNCTION}()")
    for table_name in TERMINAL_TABLES:
        op.execute(
            f"CREATE TRIGGER {OLD_PG_IMMUTABLE_TRIGGERS[table_name]} "
            f"BEFORE UPDATE OR DELETE ON public.{table_name} FOR EACH ROW "
            "EXECUTE FUNCTION public.rsc_block_stocktake_fact_mutation_0010()"
        )
    for table_name in LEDGER_FACT_TABLES:
        op.execute(
            f"CREATE TRIGGER {OLD_PG_LEDGER_TRIGGERS[table_name]} "
            f"BEFORE UPDATE OR DELETE ON public.{table_name} FOR EACH ROW "
            "EXECUTE FUNCTION public.rsc_block_inventory_ledger_mutation_0009()"
        )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "public.rsc_block_stocktake_fact_mutation_0010() "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "public.rsc_block_inventory_ledger_mutation_0009() "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )


def _replace_sqlite_terminal_guards(*, harden: bool) -> None:
    if harden:
        op.create_index(
            OPENING_POSTING_UNIQUE_INDEX,
            "stocktake_postings",
            ["task_id"],
            unique=True,
            sqlite_where=sa.text("posting_kind = 'opening'"),
        )
        for table_name in TERMINAL_TABLES:
            op.execute(
                f"DROP TRIGGER {OLD_SQLITE_UPDATE_TRIGGERS[table_name]}"
            )
            op.execute(
                f"DROP TRIGGER {OLD_SQLITE_DELETE_TRIGGERS[table_name]}"
            )
            op.execute(
                f"""
CREATE TRIGGER {SQLITE_UPDATE_TRIGGERS[table_name]}
BEFORE UPDATE ON {table_name}
BEGIN
    SELECT RAISE(ABORT, 'opening terminal facts are immutable');
END
"""
            )
            op.execute(
                f"""
CREATE TRIGGER {SQLITE_DELETE_TRIGGERS[table_name]}
BEFORE DELETE ON {table_name}
BEGIN
    SELECT RAISE(ABORT, 'opening terminal facts are immutable');
END
"""
            )
        op.execute(
            f"""
CREATE TRIGGER {SQLITE_UNIQUENESS_TRIGGER}
BEFORE INSERT ON stocktake_postings
WHEN NEW.posting_kind = 'opening'
 AND (
      NOT EXISTS (SELECT 1 FROM stocktake_tasks WHERE id = NEW.task_id)
      OR EXISTS (
          SELECT 1 FROM stocktake_postings AS existing
           WHERE existing.task_id = NEW.task_id
             AND existing.posting_kind = 'opening'
      )
 )
BEGIN
    SELECT RAISE(ABORT, 'opening stocktake task already has a posting');
END
"""
        )
        return

    op.execute(f"DROP TRIGGER {SQLITE_UNIQUENESS_TRIGGER}")
    op.drop_index(
        OPENING_POSTING_UNIQUE_INDEX,
        table_name="stocktake_postings",
    )
    for table_name in TERMINAL_TABLES:
        op.execute(f"DROP TRIGGER {SQLITE_UPDATE_TRIGGERS[table_name]}")
        op.execute(f"DROP TRIGGER {SQLITE_DELETE_TRIGGERS[table_name]}")
        for operation, trigger_name in (
            ("UPDATE", OLD_SQLITE_UPDATE_TRIGGERS[table_name]),
            ("DELETE", OLD_SQLITE_DELETE_TRIGGERS[table_name]),
        ):
            op.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE {operation} ON "
                f"{table_name} BEGIN SELECT RAISE(ABORT, "
                "'formal stocktake fact rows are immutable'); END"
            )


def _apply_postgresql_runtime_acl(*, current: bool) -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}'
    ) THEN
        RAISE EXCEPTION
            '0022 requires the provisioned star_oam_api runtime role';
    END IF;
END
$$
"""
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    _revoke_postgresql_column_acl()
    op.execute(
        "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    read_tables = API_READ_TABLES if current else PREVIOUS_API_READ_TABLES
    insert_tables = API_INSERT_TABLES if current else PREVIOUS_API_INSERT_TABLES
    update_tables = API_UPDATE_TABLES if current else PREVIOUS_API_UPDATE_TABLES
    delete_tables = API_DELETE_TABLES if current else PREVIOUS_API_DELETE_TABLES
    _grant_table_privileges("SELECT", read_tables)
    _grant_table_privileges("INSERT", insert_tables)
    _grant_table_privileges("UPDATE", update_tables)
    _grant_table_privileges("DELETE", delete_tables)
    if current:
        for table_name, columns in sorted(API_UPDATE_COLUMNS.items()):
            op.execute(
                f"GRANT UPDATE ({', '.join(columns)}) ON TABLE "
                f"public.{table_name} TO {PRODUCTION_API_ROLE}"
            )


def _grant_table_privileges(
    privileges: str, table_names: Sequence[str]
) -> None:
    if table_names:
        op.execute(
            f"GRANT {privileges} ON TABLE "
            f"{', '.join(f'public.{name}' for name in table_names)} "
            f"TO {PRODUCTION_API_ROLE}"
        )


def _revoke_postgresql_column_acl() -> None:
    op.execute(
        f"""
DO $$
DECLARE
    column_row record;
BEGIN
    FOR column_row IN
        SELECT class_row.relname, attribute_row.attname
          FROM pg_class AS class_row
          JOIN pg_namespace AS namespace_row
            ON namespace_row.oid = class_row.relnamespace
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = class_row.oid
         WHERE namespace_row.nspname = 'public'
           AND class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
         ORDER BY class_row.relname, attribute_row.attnum
    LOOP
        EXECUTE format(
            'REVOKE SELECT (%1$I), INSERT (%1$I), UPDATE (%1$I), '
            'REFERENCES (%1$I) ON TABLE public.%2$I FROM PUBLIC, '
            '{PRODUCTION_API_ROLE}',
            column_row.attname,
            column_row.relname
        );
    END LOOP;
END
$$
"""
    )
