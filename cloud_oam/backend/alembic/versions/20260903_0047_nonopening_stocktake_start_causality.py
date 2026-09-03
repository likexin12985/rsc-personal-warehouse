"""Seal the non-opening stocktake start graph before granting runtime writes.

Revision ID: 20260903_0047
Revises: 20260903_0046
Create Date: 2026-09-03

The non-opening start service atomically records a cutoff, scope freezes,
snapshot lines, round one, three transition events and one inventory audit
event.  Revision 0024 intentionally did not grant the runtime role access to
the five sensitive task columns needed by that operation, while revision 0010
protects only opening tasks.  Granting those columns without an equivalent
database-owned causal boundary would make the cutoff and snapshot projection
forgeable.

This revision therefore requires an empty legacy non-opening start graph,
adds one immutable completion fact, derives its graph manifest inside
PostgreSQL, and validates the complete graph at transaction end.  Only after
those guards exist are the five required column-level UPDATE privileges
granted.  No inventory posting, notification, external synchronization, OAM
receipt or personal-warehouse inbound fact is created by this migration.

SQLite remains a test-only compatibility dialect.  Because SQLite has neither
deferred constraint triggers nor a built-in SHA-256 function, its completion
row is the final application-owned insert and an immediate trigger validates
the start graph before installing a database-owned structural seal.  The
PostgreSQL boundary is the production security authority.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "20260903_0047"
down_revision: Union[str, Sequence[str], None] = "20260903_0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
OAM_RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
PREVIOUS_SCHEMA_REVISION = "20260903_0046"

COMPLETION_TABLE = "stocktake_start_completions"
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
POST_START_STATUS_SQL = (
    "('counting', 'submitted', 'region_review', 'hq_review', 'approved', "
    "'recount_required', 'posted', 'closed')"
)
GRAPH_SCHEMA = "cloud_oam.stocktake.start_graph.pg16.v1"
AUTHORIZATION_SCHEMA = "cloud_oam.stocktake.start_authorization.v1"
COMMAND_SCHEMA = "cloud_oam.stocktake.command.v1"

PG_GUARD_FUNCTION = "rsc_guard_stocktake_start_completion_0047"
PG_VALIDATE_FUNCTION = "rsc_validate_nonopening_stocktake_start_causality_0047"
PG_DISPATCH_FUNCTION = "rsc_dispatch_nonopening_stocktake_start_causality_0047"
PG_GUARD_TRIGGER = "trg_stocktake_start_completions_guard_0047"
DEFERRED_TABLES = (
    "stocktake_tasks",
    "stocktake_scopes",
    "inventory_freezes",
    "stocktake_snapshot_lines",
    "stocktake_rounds",
    COMPLETION_TABLE,
    "state_transition_events",
    "audit_events",
)
PG_DEFERRED_TRIGGERS = {
    table_name: f"trg_{table_name}_stocktake_start_causality_0047"
    for table_name in DEFERRED_TABLES
}
SEALED_GUARD_TABLES = tuple(
    table_name for table_name in DEFERRED_TABLES if table_name != COMPLETION_TABLE
)
PG_SEALED_GUARD_TRIGGERS = {
    table_name: f"trg_{table_name}_stocktake_start_sealed_0047"
    for table_name in SEALED_GUARD_TABLES
}

TASK_START_UPDATE_COLUMNS = (
    "cutoff_ledger_cursor",
    "cutoff_at",
    "snapshot_manifest_sha256",
    "issued_at",
    "frozen_at",
)

SQLITE_VALIDATE_TRIGGER = "trg_stocktake_start_completions_validate_0047"
SQLITE_SEAL_TRIGGER = "trg_stocktake_start_completions_seal_0047"
SQLITE_GUARD_UPDATE_TRIGGER = (
    "trg_stocktake_start_completions_guard_update_0047"
)
SQLITE_GUARD_DELETE_TRIGGER = (
    "trg_stocktake_start_completions_guard_delete_0047"
)
SQLITE_TASK_SEAL_TRIGGER = "trg_stocktake_tasks_start_sealed_update_0047"
SQLITE_SNAPSHOT_UPDATE_TRIGGER = (
    "trg_stocktake_snapshot_lines_start_sealed_update_0047"
)
SQLITE_SNAPSHOT_DELETE_TRIGGER = (
    "trg_stocktake_snapshot_lines_start_sealed_delete_0047"
)
SQLITE_ROUND_SEAL_TRIGGER = "trg_stocktake_rounds_start_sealed_update_0047"
SQLITE_ADDITIONAL_SEAL_TRIGGERS = (
    "trg_stocktake_tasks_start_graph_sealed_update_0047",
    "trg_stocktake_scopes_start_sealed_insert_0047",
    "trg_stocktake_scopes_start_sealed_update_0047",
    "trg_stocktake_scopes_start_sealed_delete_0047",
    "trg_inventory_freezes_start_sealed_insert_0047",
    "trg_inventory_freezes_start_sealed_update_0047",
    "trg_inventory_freezes_start_sealed_delete_0047",
    "trg_stocktake_snapshot_lines_start_sealed_insert_0047",
    "trg_stocktake_rounds_start_sealed_insert_0047",
    "trg_stocktake_rounds_start_sealed_delete_0047",
    "trg_state_transition_events_start_sealed_insert_0047",
    "trg_audit_events_start_sealed_insert_0047",
)

UPGRADE_BLOCKER = (
    "0047 preflight failed: existing non-opening stocktake start facts require "
    "manual evidence quarantine"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0047 while non-opening stocktake start completion facts exist"
)
GRAPH_ERROR = "non-opening stocktake start causality is invalid"
IMMUTABLE_ERROR = "non-opening stocktake start completion is immutable"


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0047 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0047 SQLite upgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
        _require_empty_sqlite_start_graph()
    else:
        _lock_postgresql_start_graph(include_completion=False)
        _emit_empty_postgresql_start_graph_guard()

    _create_completion_table()
    if dialect == "sqlite":
        _create_sqlite_triggers()
        return

    _create_postgresql_functions()
    _create_postgresql_triggers()
    _apply_postgresql_acl()
    _replace_oam_runtime_ready_function(revision)


def downgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        raise RuntimeError("0047 downgrade requires an online evidence check")

    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
        _require_empty_completion_table()
        _require_empty_sqlite_start_graph()
        _drop_sqlite_triggers()
        op.drop_table(COMPLETION_TABLE)
        return

    _lock_postgresql_start_graph(include_completion=True)
    _require_empty_completion_table()
    _require_empty_postgresql_start_graph()
    _replace_oam_runtime_ready_function(PREVIOUS_SCHEMA_REVISION)
    _revoke_postgresql_acl()
    for table_name in reversed(DEFERRED_TABLES):
        op.execute(
            f"DROP TRIGGER {PG_DEFERRED_TRIGGERS[table_name]} "
            f"ON public.{table_name}"
        )
    for table_name in reversed(SEALED_GUARD_TABLES):
        op.execute(
            f"DROP TRIGGER {PG_SEALED_GUARD_TRIGGERS[table_name]} "
            f"ON public.{table_name}"
        )
    op.execute(
        f"DROP TRIGGER {PG_GUARD_TRIGGER} ON public.{COMPLETION_TABLE}"
    )
    for function_name, argument_types in (
        (PG_DISPATCH_FUNCTION, ""),
        (PG_VALIDATE_FUNCTION, "uuid"),
        (PG_GUARD_FUNCTION, ""),
    ):
        op.execute(f"DROP FUNCTION public.{function_name}({argument_types})")
    op.drop_table(COMPLETION_TABLE, schema="public")


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _legacy_start_exists_sql(prefix: str) -> str:
    return f"""
EXISTS (
    SELECT 1
      FROM {prefix}stocktake_tasks AS task
     WHERE task.task_type IN {NONOPENING_SQL}
       AND (
           task.status <> 'draft'
           OR task.current_round_no <> 0
           OR task.cutoff_ledger_cursor IS NOT NULL
           OR task.cutoff_at IS NOT NULL
           OR task.snapshot_manifest_sha256 IS NOT NULL
           OR task.issued_at IS NOT NULL
           OR task.frozen_at IS NOT NULL
           OR EXISTS (
               SELECT 1 FROM {prefix}inventory_freezes AS freeze_row
                WHERE freeze_row.task_id = task.id
           )
           OR EXISTS (
               SELECT 1 FROM {prefix}stocktake_snapshot_lines AS snapshot
                WHERE snapshot.task_id = task.id
           )
           OR EXISTS (
               SELECT 1 FROM {prefix}stocktake_rounds AS round_row
                WHERE round_row.task_id = task.id
           )
           OR EXISTS (
               SELECT 1 FROM {prefix}state_transition_events AS event
                WHERE event.aggregate_type = 'stocktake_task'
                  AND lower(replace(event.aggregate_id, '-', '')) =
                      lower(replace(CAST(task.id AS text), '-', ''))
                  AND (
                      event.reason IN (
                          'stocktake_task_issued',
                          'stocktake_task_frozen',
                          'stocktake_initial_round_started'
                      )
                      OR event.idempotency_key LIKE
                         ('stocktake' || ':' || 'start' || ':' || '%')
                      OR (
                          {"event.metadata_jsonb->>'schema'" if prefix else "json_extract(event.metadata_jsonb, '$.schema')"}
                              = '{COMMAND_SCHEMA}'
                          AND {"event.metadata_jsonb->>'operation'" if prefix else "json_extract(event.metadata_jsonb, '$.operation')"}
                              = 'start'
                      )
                  )
           )
           OR EXISTS (
               SELECT 1 FROM {prefix}audit_events AS audit
                WHERE audit.aggregate_type = 'stocktake_task'
                  AND lower(replace(audit.aggregate_id, '-', '')) =
                      lower(replace(CAST(task.id AS text), '-', ''))
                  AND audit.action = 'stocktake.task.started'
           )
       )
)
""".strip()


def _require_empty_sqlite_start_graph() -> None:
    found = op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {_legacy_start_exists_sql('')}"
    ).first()
    if found is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _emit_empty_postgresql_start_graph_guard() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF {_legacy_start_exists_sql('public.')} THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _require_empty_completion_table() -> None:
    prefix = "public." if _dialect_name() == "postgresql" else ""
    found = op.get_bind().exec_driver_sql(
        f"SELECT 1 FROM {prefix}{COMPLETION_TABLE} LIMIT 1"
    ).first()
    if found is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _require_empty_postgresql_start_graph() -> None:
    statement = sa.text(
        f"SELECT 1 WHERE {_legacy_start_exists_sql('public.')} LIMIT 1"
    )
    found = op.get_bind().execute(statement).first()
    if found is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _lock_postgresql_start_graph(*, include_completion: bool) -> None:
    tables = list(DEFERRED_TABLES)
    if not include_completion:
        tables.remove(COMPLETION_TABLE)
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in tables)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _create_completion_table() -> None:
    schema = "public" if _dialect_name() == "postgresql" else None
    op.create_table(
        COMPLETION_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("initial_round_id", sa.Uuid(), nullable=False),
        sa.Column("expected_task_version", sa.BigInteger(), nullable=False),
        sa.Column("started_task_version", sa.BigInteger(), nullable=False),
        sa.Column("cutoff_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_line_count", sa.Integer(), nullable=False),
        sa.Column("active_freeze_count", sa.Integer(), nullable=False),
        sa.Column("scope_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("started_by_user_id", sa.String(36), nullable=False),
        sa.Column("started_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("started_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(40), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(80), nullable=False),
        sa.Column("authorization_sha256", sa.String(64), nullable=False),
        sa.Column(
            "graph_manifest_sha256",
            sa.String(64),
            nullable=_dialect_name() == "sqlite",
            comment="Database-owned non-opening stocktake start graph digest",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "id", name="pk_stocktake_start_completions_0047"
        ),
        sa.UniqueConstraint(
            "task_id", name="uq_stocktake_start_completions_task_0047"
        ),
        sa.UniqueConstraint(
            "initial_round_id",
            name="uq_stocktake_start_completions_round_0047",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_start_completions_idempotency_0047",
        ),
        sa.UniqueConstraint(
            "id", "task_id", name="uq_stocktake_start_completions_id_task_0047"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["stocktake_tasks.id"],
            name="fk_stocktake_start_completions_task_0047",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["initial_round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_start_completions_round_0047",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["started_by_user_id"],
            ["users.id"],
            name="fk_stocktake_start_completions_user_0047",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["started_by_person_id"],
            ["people.id"],
            name="fk_stocktake_start_completions_person_0047",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["started_role_assignment_id"],
            ["role_assignments.id"],
            name="fk_stocktake_start_completions_assignment_0047",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "expected_task_version >= 0 AND "
            "started_task_version = expected_task_version + 1",
            name="ck_stocktake_start_completions_versions_0047",
        ),
        sa.CheckConstraint(
            "cutoff_ledger_cursor >= 0 AND scope_count > 0 AND "
            "snapshot_line_count >= 0 AND active_freeze_count = scope_count",
            name="ck_stocktake_start_completions_counts_0047",
        ),
        sa.CheckConstraint(
            "authorization_version > 0 AND "
            "((role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*') OR "
            "(role_code = 'provincial_manager' AND "
            "scope_type = 'organization' AND "
            "length(trim(scope_id_snapshot)) > 0) OR "
            "(role_code = 'technician' AND scope_type = 'person' AND "
            "length(trim(scope_id_snapshot)) > 0))",
            name="ck_stocktake_start_completions_authorization_0047",
        ),
        sa.CheckConstraint(
            "length(scope_manifest_sha256) = 64 AND "
            "length(snapshot_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64 AND "
            "length(graph_manifest_sha256) = 64",
            name="ck_stocktake_start_completions_hashes_0047",
        ),
        sa.CheckConstraint(
            "cutoff_at <= started_at AND created_at = started_at",
            name="ck_stocktake_start_completions_chronology_0047",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_stocktake_start_completions_actor_0047",
        COMPLETION_TABLE,
        ["started_by_user_id", "started_at"],
        schema=schema,
    )


def _utc_timestamp_json(expression: str) -> str:
    return (
        "pg_catalog.to_char(pg_catalog.timezone('UTC', "
        f"{expression}), 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
    )


def _authorization_document_expression(alias: str) -> str:
    started_at = _utc_timestamp_json(f"{alias}.started_at")
    # Python uses json.dumps(sort_keys=True, separators=(",", ":")).  Every
    # textual value here is an identifier or fixed enum, so to_jsonb supplies
    # the exact required JSON quoting without locale-dependent formatting.
    return f"""
pg_catalog.concat(
    '{{"assignment_id":', pg_catalog.to_jsonb({alias}.started_role_assignment_id::text)::text,
    ',"authorization_version":', {alias}.authorization_version::text,
    ',"person_id":', pg_catalog.to_jsonb({alias}.started_by_person_id::text)::text,
    ',"role_code":', pg_catalog.to_jsonb({alias}.role_code)::text,
    ',"schema":"{AUTHORIZATION_SCHEMA}"',
    ',"scope_id":', pg_catalog.to_jsonb({alias}.scope_id_snapshot)::text,
    ',"scope_type":', pg_catalog.to_jsonb({alias}.scope_type)::text,
    ',"started_at":', pg_catalog.to_jsonb({started_at})::text,
    ',"user_id":', pg_catalog.to_jsonb({alias}.started_by_user_id)::text,
    '}}'
)
""".strip()


def _postgresql_json_sha256(document_sql: str) -> str:
    """Match the compact, key-sorted JSON SHA used by the Python service."""

    return (
        "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
        "public.rsc_canonical_reconciliation_json_0026("
        f"{document_sql}), 'UTF8')), 'hex')"
    )


def _postgresql_quantity_text(expression: str) -> str:
    return f"pg_catalog.to_char({expression}, 'FM9999999999999990.000')"


def _graph_manifest_expression(alias: str) -> str:
    cutoff_at = _utc_timestamp_json(f"{alias}.cutoff_at")
    started_at = _utc_timestamp_json(f"{alias}.started_at")
    created_at = _utc_timestamp_json(f"{alias}.created_at")
    return f"""
pg_catalog.encode(
    pg_catalog.sha256(
        pg_catalog.convert_to(
            pg_catalog.jsonb_build_object(
                'schema', '{GRAPH_SCHEMA}',
                'completion', pg_catalog.jsonb_build_object(
                    'id', {alias}.id::text,
                    'task_id', {alias}.task_id::text,
                    'initial_round_id', {alias}.initial_round_id::text,
                    'expected_task_version', {alias}.expected_task_version,
                    'started_task_version', {alias}.started_task_version,
                    'cutoff_ledger_cursor', {alias}.cutoff_ledger_cursor,
                    'cutoff_at', {cutoff_at},
                    'scope_count', {alias}.scope_count,
                    'snapshot_line_count', {alias}.snapshot_line_count,
                    'active_freeze_count', {alias}.active_freeze_count,
                    'scope_manifest_sha256', {alias}.scope_manifest_sha256,
                    'snapshot_manifest_sha256', {alias}.snapshot_manifest_sha256,
                    'request_sha256', {alias}.request_sha256,
                    'idempotency_key_hash', {alias}.idempotency_key_hash,
                    'started_by_user_id', {alias}.started_by_user_id,
                    'started_by_person_id', {alias}.started_by_person_id::text,
                    'started_role_assignment_id', {alias}.started_role_assignment_id::text,
                    'authorization_version', {alias}.authorization_version,
                    'role_code', {alias}.role_code,
                    'scope_type', {alias}.scope_type,
                    'scope_id_snapshot', {alias}.scope_id_snapshot,
                    'authorization_sha256', {alias}.authorization_sha256,
                    'started_at', {started_at},
                    'created_at', {created_at}
                ),
                'task', (
                    SELECT pg_catalog.jsonb_build_object(
                        'id', task.id::text,
                        'task_no', task.task_no,
                        'task_type', task.task_type,
                        'region_org_id', task.region_org_id::text,
                        'blind_count', task.blind_count,
                        'created_by_user_id', task.created_by_user_id,
                        'deadline', {_utc_timestamp_json('task.deadline')},
                        'note', task.note,
                        'cutoff_ledger_cursor', task.cutoff_ledger_cursor,
                        'cutoff_at', {_utc_timestamp_json('task.cutoff_at')},
                        'scope_manifest_sha256', task.scope_manifest_sha256,
                        'snapshot_manifest_sha256', task.snapshot_manifest_sha256,
                        'control_source_system_id', CASE
                            WHEN task.control_source_system_id IS NULL THEN NULL
                            ELSE task.control_source_system_id::text END,
                        'control_sync_run_id', CASE
                            WHEN task.control_sync_run_id IS NULL THEN NULL
                            ELSE task.control_sync_run_id::text END,
                        'control_snapshot_at', {_utc_timestamp_json('task.control_snapshot_at')},
                        'control_manifest_sha256', task.control_manifest_sha256,
                        'current_round_no_at_least', 1,
                        'issued_at', {_utc_timestamp_json('task.issued_at')},
                        'frozen_at', {_utc_timestamp_json('task.frozen_at')},
                        'created_at', {_utc_timestamp_json('task.created_at')}
                    )
                      FROM public.stocktake_tasks AS task
                     WHERE task.id = {alias}.task_id
                ),
                'scopes', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        pg_catalog.jsonb_build_object(
                            'id', scope.id::text,
                            'scope_no', scope.scope_no,
                            'scope_mode', scope.scope_mode,
                            'location_id', scope.location_id::text,
                            'owner_org_id', scope.owner_org_id::text,
                            'custodian_person_id_snapshot',
                                CASE WHEN scope.custodian_person_id_snapshot IS NULL
                                     THEN NULL ELSE scope.custodian_person_id_snapshot::text END,
                            'assignee_user_id', scope.assignee_user_id,
                            'material_id', CASE WHEN scope.material_id IS NULL
                                THEN NULL ELSE scope.material_id::text END,
                            'condition_code', scope.condition_code,
                            'availability_bucket', scope.availability_bucket,
                            'scope_key', scope.scope_key,
                            'scope_sha256', scope.scope_sha256,
                            'created_at', {_utc_timestamp_json('scope.created_at')}
                        ) ORDER BY scope.scope_no, scope.id
                    )
                      FROM public.stocktake_scopes AS scope
                     WHERE scope.task_id = {alias}.task_id
                ), '[]'::jsonb),
                'freezes', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        pg_catalog.jsonb_build_object(
                            'id', freeze_row.id::text,
                            'stocktake_scope_id', freeze_row.stocktake_scope_id::text,
                            'scope_key', freeze_row.scope_key,
                            'freeze_mode', freeze_row.freeze_mode,
                            'valid_from', {_utc_timestamp_json('freeze_row.valid_from')},
                            'created_by_user_id', freeze_row.created_by_user_id,
                            'created_at', {_utc_timestamp_json('freeze_row.created_at')}
                        ) ORDER BY freeze_row.stocktake_scope_id, freeze_row.id
                    )
                      FROM public.inventory_freezes AS freeze_row
                     WHERE freeze_row.task_id = {alias}.task_id
                ), '[]'::jsonb),
                'snapshots', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        pg_catalog.jsonb_build_object(
                            'id', snapshot.id::text,
                            'scope_id', snapshot.scope_id::text,
                            'stock_account_id', snapshot.stock_account_id::text,
                            'book_qty', snapshot.book_qty::numeric(18,3)::text,
                            'ledger_cursor', snapshot.ledger_cursor,
                            'account_dimension_sha256', snapshot.account_dimension_sha256,
                            'serial_snapshot', snapshot.serial_snapshot_jsonb,
                            'serial_snapshot_sha256', snapshot.serial_snapshot_sha256,
                            'serial_count', snapshot.serial_count,
                            'created_at', {_utc_timestamp_json('snapshot.created_at')}
                        ) ORDER BY snapshot.scope_id, snapshot.stock_account_id, snapshot.id
                    )
                      FROM public.stocktake_snapshot_lines AS snapshot
                     WHERE snapshot.task_id = {alias}.task_id
                ), '[]'::jsonb),
                'initial_round', (
                    SELECT pg_catalog.jsonb_build_object(
                        'id', round_row.id::text,
                        'round_no', round_row.round_no,
                        'round_type', round_row.round_type,
                        'started_at', {_utc_timestamp_json('round_row.started_at')},
                        'idempotency_key_hash', round_row.idempotency_key_hash,
                        'recount_case_id', CASE WHEN round_row.recount_case_id IS NULL
                            THEN NULL ELSE round_row.recount_case_id::text END,
                        'created_at', {_utc_timestamp_json('round_row.created_at')}
                    )
                      FROM public.stocktake_rounds AS round_row
                     WHERE round_row.id = {alias}.initial_round_id
                       AND round_row.task_id = {alias}.task_id
                ),
                'transitions', COALESCE((
                    SELECT pg_catalog.jsonb_agg(
                        pg_catalog.jsonb_build_object(
                            'id', event.id::text,
                            'from_status', event.from_status,
                            'to_status', event.to_status,
                            'reason', event.reason,
                            'actor_id', event.actor_id,
                            'idempotency_key', event.idempotency_key,
                            'occurred_at', {_utc_timestamp_json('event.occurred_at')},
                            'metadata', event.metadata_jsonb,
                            'created_at', {_utc_timestamp_json('event.created_at')}
                        ) ORDER BY event.occurred_at, event.id
                    )
                      FROM public.state_transition_events AS event
                     WHERE event.aggregate_type = 'stocktake_task'
                       AND lower(replace(event.aggregate_id, '-', '')) =
                           replace({alias}.task_id::text, '-', '')
                       AND event.idempotency_key IN (
                           'stocktake' || ':' || 'start' || ':' ||
                               {alias}.idempotency_key_hash || ':' || 'issued',
                           'stocktake' || ':' || 'start' || ':' ||
                               {alias}.idempotency_key_hash || ':' || 'frozen',
                           'stocktake' || ':' || 'start' || ':' ||
                               {alias}.idempotency_key_hash || ':' || 'counting'
                       )
                ), '[]'::jsonb),
                'audit', (
                    SELECT pg_catalog.jsonb_build_object(
                        'id', audit.id::text,
                        'stream_key', audit.stream_key,
                        'stream_version', audit.stream_version,
                        'actor_user_id', audit.actor_user_id,
                        'action', audit.action,
                        'before', audit.before_jsonb,
                        'after', audit.after_jsonb,
                        'request_id', audit.request_id,
                        'previous_hash', audit.previous_hash,
                        'event_hash', audit.event_hash,
                        'occurred_at', {_utc_timestamp_json('audit.occurred_at')},
                        'created_at', {_utc_timestamp_json('audit.created_at')}
                    )
                      FROM public.audit_events AS audit
                     WHERE audit.stream_key = 'inventory'
                       AND audit.aggregate_type = 'stocktake_task'
                       AND lower(replace(audit.aggregate_id, '-', '')) =
                           replace({alias}.task_id::text, '-', '')
                       AND audit.action = 'stocktake.task.started'
                       AND audit.actor_user_id = {alias}.started_by_user_id
                       AND audit.occurred_at = {alias}.started_at
                     ORDER BY audit.sequence_no, audit.id
                )
            )::text,
            'UTF8'
        )
    ),
    'hex'
)
""".strip()


def _postgresql_guard_function_sql() -> str:
    graph_manifest = _graph_manifest_expression("NEW")
    scope_document = """pg_catalog.jsonb_build_object(
        'assignee_user_id', scope_row.assignee_user_id,
        'availability_bucket', scope_row.availability_bucket,
        'condition_code', scope_row.condition_code,
        'custodian_person_id', CASE
            WHEN scope_row.custodian_person_id_snapshot IS NULL THEN NULL
            ELSE scope_row.custodian_person_id_snapshot::text END,
        'freeze_mode', scope_row.freeze_mode,
        'location_id', scope_row.location_id::text,
        'material_id', CASE WHEN scope_row.material_id IS NULL THEN NULL
            ELSE scope_row.material_id::text END,
        'owner_org_id', scope_row.owner_org_id::text,
        'schema', 'cloud_oam.stocktake.scope.v1',
        'scope_key', scope_row.scope_key,
        'scope_mode', scope_row.scope_mode,
        'scope_no', scope_row.scope_no)"""
    scope_hash = _postgresql_json_sha256(scope_document)
    scope_manifest_document = """pg_catalog.jsonb_build_object(
        'region_org_id', guard_task.region_org_id::text,
        'schema', 'cloud_oam.stocktake.scope_manifest.v1',
        'scopes', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'scope_id', scope.id::text,
                    'scope_no', scope.scope_no,
                    'scope_sha256', scope.scope_sha256)
                ORDER BY scope.scope_no)
              FROM public.stocktake_scopes AS scope
             WHERE scope.task_id = NEW.task_id), '[]'::jsonb),
        'task_type', guard_task.task_type)"""
    scope_manifest_hash = _postgresql_json_sha256(scope_manifest_document)
    account_dimension_document = """pg_catalog.jsonb_build_object(
        'availability_bucket', snapshot_row.availability_bucket,
        'condition_code', snapshot_row.condition_code,
        'custodian_person_id', CASE
            WHEN snapshot_row.custodian_person_id IS NULL THEN NULL
            ELSE snapshot_row.custodian_person_id::text END,
        'location_id', snapshot_row.location_id::text,
        'lot_id', CASE WHEN snapshot_row.lot_id IS NULL THEN NULL
            ELSE snapshot_row.lot_id::text END,
        'material_id', snapshot_row.material_id::text,
        'owner_org_id', snapshot_row.owner_org_id::text,
        'schema', 'cloud_oam.stocktake.account_dimension.v1',
        'stock_account_id', snapshot_row.stock_account_id::text)"""
    account_dimension_hash = _postgresql_json_sha256(account_dimension_document)
    serial_snapshot_document = """pg_catalog.jsonb_build_object(
        'schema', 'cloud_oam.stocktake.serial_snapshot.v1',
        'serials', expected_serial_snapshot,
        'stock_account_id', snapshot_row.stock_account_id::text)"""
    serial_snapshot_hash = _postgresql_json_sha256(serial_snapshot_document)
    snapshot_manifest_document = f"""pg_catalog.jsonb_build_object(
        'cutoff_ledger_cursor', NEW.cutoff_ledger_cursor,
        'lines', COALESCE((
            SELECT pg_catalog.jsonb_agg(
                pg_catalog.jsonb_build_object(
                    'account_dimension_sha256', snapshot.account_dimension_sha256,
                    'book_qty', {_postgresql_quantity_text('snapshot.book_qty')},
                    'scope_id', snapshot.scope_id::text,
                    'serial_snapshot_sha256', snapshot.serial_snapshot_sha256,
                    'stock_account_id', snapshot.stock_account_id::text)
                ORDER BY scope.scope_no, snapshot.stock_account_id)
              FROM public.stocktake_snapshot_lines AS snapshot
              JOIN public.stocktake_scopes AS scope
                ON scope.id = snapshot.scope_id
               AND scope.task_id = snapshot.task_id
             WHERE snapshot.task_id = NEW.task_id), '[]'::jsonb),
        'schema', 'cloud_oam.stocktake.snapshot.v1',
        'scope_sha256', COALESCE((
            SELECT pg_catalog.jsonb_agg(scope.scope_sha256 ORDER BY scope.scope_no)
              FROM public.stocktake_scopes AS scope
             WHERE scope.task_id = NEW.task_id), '[]'::jsonb),
        'task_id', NEW.task_id::text)"""
    snapshot_manifest_hash = _postgresql_json_sha256(snapshot_manifest_document)
    return f"""
CREATE FUNCTION public.{PG_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict error
DECLARE
    guard_task public.stocktake_tasks%ROWTYPE;
    ledger_next_cursor bigint;
    computed_scope_sha256 text;
    computed_scope_manifest_sha256 text;
    computed_account_dimension_sha256 text;
    computed_serial_snapshot_sha256 text;
    computed_snapshot_manifest_sha256 text;
    expected_book_qty numeric(18,3);
    expected_balance_cursor bigint;
    expected_balance_version bigint;
    projected_balance_count bigint;
    expected_serial_count bigint;
    expected_serial_snapshot jsonb;
    scope_row record;
    snapshot_row record;
    current_org_id uuid;
    current_parent_org_id uuid;
    current_org_status text;
    current_org_type text;
    visited_org_ids uuid[];
    reached_region boolean;
    current_location_id uuid;
    current_parent_location_id uuid;
    current_location_owner_org_id uuid;
    current_location_status text;
    current_location_type text;
    target_location_type text;
    target_location_custodian_id uuid;
    visited_location_ids uuid[];
    current_custody_count bigint;
    current_custodian_id uuid;
    old_document jsonb;
    new_document jsonb;
    old_task_text text;
    new_task_text text;
    old_task_id uuid;
    new_task_id uuid;
    old_sealed boolean := false;
    new_sealed boolean := false;
BEGIN
    IF TG_TABLE_NAME <> '{COMPLETION_TABLE}' THEN
        IF TG_OP <> 'INSERT' THEN
            old_document := pg_catalog.to_jsonb(OLD);
            IF TG_TABLE_NAME IN ('state_transition_events', 'audit_events') THEN
                IF old_document->>'aggregate_type' = 'stocktake_task' THEN
                    old_task_text := old_document->>'aggregate_id';
                END IF;
            ELSIF TG_TABLE_NAME = 'stocktake_tasks' THEN
                old_task_text := old_document->>'id';
            ELSE
                old_task_text := old_document->>'task_id';
            END IF;
        END IF;
        IF TG_OP <> 'DELETE' THEN
            new_document := pg_catalog.to_jsonb(NEW);
            IF TG_TABLE_NAME IN ('state_transition_events', 'audit_events') THEN
                IF new_document->>'aggregate_type' = 'stocktake_task' THEN
                    new_task_text := new_document->>'aggregate_id';
                END IF;
            ELSIF TG_TABLE_NAME = 'stocktake_tasks' THEN
                new_task_text := new_document->>'id';
            ELSE
                new_task_text := new_document->>'task_id';
            END IF;
        END IF;
        IF old_task_text IS NOT NULL THEN
            SELECT task.id INTO old_task_id
              FROM public.stocktake_tasks AS task
             WHERE replace(task.id::text, '-', '') =
                   lower(replace(old_task_text, '-', ''))
             FOR UPDATE OF task;
            IF FOUND THEN
                SELECT EXISTS (
                    SELECT 1 FROM public.{COMPLETION_TABLE} AS completion
                     WHERE completion.task_id = old_task_id
                ) INTO old_sealed;
            END IF;
        END IF;
        IF new_task_text IS NOT NULL THEN
            SELECT task.id INTO new_task_id
              FROM public.stocktake_tasks AS task
             WHERE replace(task.id::text, '-', '') =
                   lower(replace(new_task_text, '-', ''))
             FOR UPDATE OF task;
            IF FOUND THEN
                SELECT EXISTS (
                    SELECT 1 FROM public.{COMPLETION_TABLE} AS completion
                     WHERE completion.task_id = new_task_id
                ) INTO new_sealed;
            END IF;
        END IF;
        IF old_sealed OR new_sealed THEN
            IF TG_TABLE_NAME IN ('stocktake_scopes', 'stocktake_snapshot_lines') THEN
                RAISE EXCEPTION '{GRAPH_ERROR}';
            ELSIF TG_TABLE_NAME = 'stocktake_tasks' THEN
                IF TG_OP <> 'UPDATE'
                   OR (old_document - ARRAY[
                       'status', 'current_round_no', 'submitted_at', 'posted_at',
                       'closed_at', 'cancelled_at', 'version', 'updated_at'
                   ]::text[]) IS DISTINCT FROM
                      (new_document - ARRAY[
                       'status', 'current_round_no', 'submitted_at', 'posted_at',
                       'closed_at', 'cancelled_at', 'version', 'updated_at'
                   ]::text[]) THEN
                    RAISE EXCEPTION '{GRAPH_ERROR}';
                END IF;
            ELSIF TG_TABLE_NAME = 'inventory_freezes' THEN
                IF TG_OP <> 'UPDATE'
                   OR (old_document - ARRAY[
                       'status', 'valid_to', 'released_by_user_id',
                       'release_reason', 'version', 'updated_at'
                   ]::text[]) IS DISTINCT FROM
                      (new_document - ARRAY[
                       'status', 'valid_to', 'released_by_user_id',
                       'release_reason', 'version', 'updated_at'
                   ]::text[]) THEN
                    RAISE EXCEPTION '{GRAPH_ERROR}';
                END IF;
            ELSIF TG_TABLE_NAME = 'stocktake_rounds' THEN
                IF EXISTS (
                       SELECT 1
                         FROM public.{COMPLETION_TABLE} AS completion
                        WHERE completion.initial_round_id::text IN (
                            old_document->>'id', new_document->>'id')
                   )
                   AND (
                       TG_OP <> 'UPDATE'
                       OR (old_document - ARRAY[
                           'status', 'submitted_by_user_id', 'submitted_at',
                           'count_manifest_sha256', 'updated_at'
                       ]::text[]) IS DISTINCT FROM
                          (new_document - ARRAY[
                           'status', 'submitted_by_user_id', 'submitted_at',
                           'count_manifest_sha256', 'updated_at'
                       ]::text[])
                   ) THEN
                    RAISE EXCEPTION '{GRAPH_ERROR}';
                END IF;
            ELSIF TG_TABLE_NAME = 'state_transition_events' THEN
                IF (old_sealed AND (
                        old_document->>'reason' IN (
                            'stocktake_task_issued', 'stocktake_task_frozen',
                            'stocktake_initial_round_started')
                        OR old_document->>'idempotency_key' LIKE
                           ('stocktake' || ':' || 'start' || ':' || '%')
                        OR (old_document->'metadata_jsonb'->>'schema' = '{COMMAND_SCHEMA}'
                            AND old_document->'metadata_jsonb'->>'operation' = 'start')
                    ))
                   OR (new_sealed AND (
                        new_document->>'reason' IN (
                            'stocktake_task_issued', 'stocktake_task_frozen',
                            'stocktake_initial_round_started')
                        OR new_document->>'idempotency_key' LIKE
                           ('stocktake' || ':' || 'start' || ':' || '%')
                        OR (new_document->'metadata_jsonb'->>'schema' = '{COMMAND_SCHEMA}'
                            AND new_document->'metadata_jsonb'->>'operation' = 'start')
                    )) THEN
                    RAISE EXCEPTION '{GRAPH_ERROR}';
                END IF;
            ELSIF TG_TABLE_NAME = 'audit_events' THEN
                IF (old_sealed AND old_document->>'action' = 'stocktake.task.started')
                   OR (new_sealed AND new_document->>'action' = 'stocktake.task.started') THEN
                    RAISE EXCEPTION '{GRAPH_ERROR}';
                END IF;
            END IF;
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '{IMMUTABLE_ERROR}';
    END IF;
    SELECT head.next_cursor INTO ledger_next_cursor
      FROM public.inventory_ledger_heads AS head
     WHERE head.id = '40000000-0000-4000-8000-000000000001'::uuid
       AND head.stream_key = 'inventory'
     FOR UPDATE OF head;
    IF NOT FOUND OR ledger_next_cursor IS NULL OR ledger_next_cursor <= 0
       OR NEW.cutoff_ledger_cursor <> ledger_next_cursor - 1
       OR NEW.cutoff_ledger_cursor <> COALESCE((
           SELECT max(transaction_row.ledger_cursor)
             FROM public.inventory_transactions AS transaction_row
            WHERE transaction_row.status = 'posted'), 0)
       OR NEW.cutoff_ledger_cursor <> (
           SELECT count(*)
             FROM public.inventory_transactions AS transaction_row
            WHERE transaction_row.status = 'posted')
       OR (NEW.cutoff_ledger_cursor = 0 AND (
           SELECT min(transaction_row.ledger_cursor)
             FROM public.inventory_transactions AS transaction_row
            WHERE transaction_row.status = 'posted') IS NOT NULL)
       OR (NEW.cutoff_ledger_cursor > 0 AND (
           SELECT min(transaction_row.ledger_cursor)
             FROM public.inventory_transactions AS transaction_row
            WHERE transaction_row.status = 'posted') <> 1) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    SELECT task.* INTO guard_task
      FROM public.stocktake_tasks AS task
     WHERE task.id = NEW.task_id
     FOR UPDATE;
    IF NOT FOUND
       OR guard_task.task_type NOT IN {NONOPENING_SQL}
       OR guard_task.status <> 'counting'
       OR guard_task.version <> NEW.started_task_version
       OR guard_task.current_round_no <> 1
       OR guard_task.cutoff_ledger_cursor IS DISTINCT FROM
          NEW.cutoff_ledger_cursor
       OR guard_task.cutoff_at IS DISTINCT FROM NEW.cutoff_at
       OR guard_task.scope_manifest_sha256 IS DISTINCT FROM
          NEW.scope_manifest_sha256
       OR guard_task.snapshot_manifest_sha256 IS DISTINCT FROM
          NEW.snapshot_manifest_sha256
       OR guard_task.control_source_system_id IS NOT NULL
       OR guard_task.control_sync_run_id IS NOT NULL
       OR guard_task.control_snapshot_at IS NOT NULL
       OR guard_task.control_manifest_sha256 IS NOT NULL
       OR guard_task.issued_at IS DISTINCT FROM NEW.started_at
       OR guard_task.frozen_at IS DISTINCT FROM NEW.started_at
       OR guard_task.updated_at IS DISTINCT FROM NEW.started_at
       OR guard_task.created_at > NEW.cutoff_at
       OR (guard_task.deadline IS NOT NULL
           AND guard_task.deadline <= NEW.started_at)
       OR NEW.cutoff_at < pg_catalog.transaction_timestamp()
       OR NEW.started_at < pg_catalog.transaction_timestamp()
       OR NEW.started_at > pg_catalog.clock_timestamp()
       OR guard_task.submitted_at IS NOT NULL
       OR guard_task.posted_at IS NOT NULL
       OR guard_task.closed_at IS NOT NULL
       OR guard_task.cancelled_at IS NOT NULL
       OR EXISTS (
           SELECT 1 FROM public.stocktake_postings AS posting
            WHERE posting.task_id = NEW.task_id
    ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    IF (SELECT count(*) FROM public.stocktake_scopes AS scope
         WHERE scope.task_id = NEW.task_id) <> NEW.scope_count
       OR (SELECT count(*) FROM public.inventory_freezes AS freeze_row
            WHERE freeze_row.task_id = NEW.task_id) <> NEW.active_freeze_count
       OR (SELECT count(*) FROM public.stocktake_snapshot_lines AS snapshot
            WHERE snapshot.task_id = NEW.task_id) <> NEW.snapshot_line_count
       OR EXISTS (
           SELECT 1
             FROM public.stocktake_scopes AS left_scope
             JOIN public.stocktake_scopes AS right_scope
               ON right_scope.task_id = left_scope.task_id
              AND right_scope.id > left_scope.id
              AND right_scope.owner_org_id = left_scope.owner_org_id
              AND right_scope.location_id = left_scope.location_id
              AND (right_scope.material_id IS NULL
                   OR left_scope.material_id IS NULL
                   OR right_scope.material_id = left_scope.material_id)
              AND (right_scope.condition_code IS NULL
                   OR left_scope.condition_code IS NULL
                   OR right_scope.condition_code = left_scope.condition_code)
              AND (right_scope.availability_bucket IS NULL
                   OR left_scope.availability_bucket IS NULL
                   OR right_scope.availability_bucket = left_scope.availability_bucket)
            WHERE left_scope.task_id = NEW.task_id
       )
       OR (SELECT min(scope.scope_no) FROM public.stocktake_scopes AS scope
            WHERE scope.task_id = NEW.task_id) <> 1
       OR (SELECT max(scope.scope_no) FROM public.stocktake_scopes AS scope
            WHERE scope.task_id = NEW.task_id) <> NEW.scope_count THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    IF NOT EXISTS (
           SELECT 1 FROM public.organizations AS region
            WHERE region.id = guard_task.region_org_id
              AND region.status = 'active'
              AND region.org_type = 'region_company'
       ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    PERFORM 1
      FROM public.stocktake_scopes AS candidate_scope
      JOIN public.stocktake_scopes AS existing_scope
        ON existing_scope.task_id <> candidate_scope.task_id
       AND existing_scope.owner_org_id = candidate_scope.owner_org_id
       AND existing_scope.location_id = candidate_scope.location_id
       AND (existing_scope.material_id IS NULL
            OR candidate_scope.material_id IS NULL
            OR existing_scope.material_id = candidate_scope.material_id)
       AND (existing_scope.condition_code IS NULL
            OR candidate_scope.condition_code IS NULL
            OR existing_scope.condition_code = candidate_scope.condition_code)
       AND (existing_scope.availability_bucket IS NULL
            OR candidate_scope.availability_bucket IS NULL
            OR existing_scope.availability_bucket =
               candidate_scope.availability_bucket)
      JOIN public.inventory_freezes AS existing_freeze
        ON existing_freeze.task_id = existing_scope.task_id
       AND existing_freeze.stocktake_scope_id = existing_scope.id
       AND existing_freeze.status = 'active'
     WHERE candidate_scope.task_id = NEW.task_id
     ORDER BY existing_freeze.id
     FOR UPDATE OF existing_freeze;
    IF FOUND THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    FOR scope_row IN
        SELECT scope.*, freeze_row.freeze_mode
          FROM public.stocktake_scopes AS scope
          JOIN public.inventory_freezes AS freeze_row
            ON freeze_row.task_id = scope.task_id
           AND freeze_row.stocktake_scope_id = scope.id
           AND freeze_row.scope_key = scope.scope_key
         WHERE scope.task_id = NEW.task_id
         ORDER BY scope.scope_no
    LOOP
        IF scope_row.created_at IS NULL
           OR scope_row.created_at > NEW.cutoff_at
           OR (guard_task.task_type = 'full'
            AND scope_row.scope_mode <> 'location_all')
           OR (guard_task.task_type IN ('termination', 'personal')
               AND scope_row.scope_mode <> 'location_all') THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;

        current_org_id := scope_row.owner_org_id;
        visited_org_ids := ARRAY[]::uuid[];
        reached_region := false;
        WHILE current_org_id IS NOT NULL LOOP
            IF current_org_id = ANY(visited_org_ids) THEN
                RAISE EXCEPTION '{GRAPH_ERROR}';
            END IF;
            visited_org_ids := pg_catalog.array_append(
                visited_org_ids, current_org_id);
            SELECT organization.parent_id, organization.status,
                   organization.org_type
              INTO current_parent_org_id, current_org_status,
                   current_org_type
              FROM public.organizations AS organization
             WHERE organization.id = current_org_id
             FOR SHARE OF organization;
            IF NOT FOUND OR current_org_status <> 'active'
               OR (current_org_id = scope_row.owner_org_id
                   AND current_org_type <> 'region_company') THEN
                RAISE EXCEPTION '{GRAPH_ERROR}';
            END IF;
            IF current_org_id = guard_task.region_org_id THEN
                reached_region := true;
                EXIT;
            END IF;
            current_org_id := current_parent_org_id;
        END LOOP;
        IF NOT reached_region THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;

        current_location_id := scope_row.location_id;
        visited_location_ids := ARRAY[]::uuid[];
        target_location_type := NULL;
        target_location_custodian_id := NULL;
        WHILE current_location_id IS NOT NULL LOOP
            IF current_location_id = ANY(visited_location_ids) THEN
                RAISE EXCEPTION '{GRAPH_ERROR}';
            END IF;
            visited_location_ids := pg_catalog.array_append(
                visited_location_ids, current_location_id);
            SELECT location.parent_id, location.owner_org_id, location.status,
                   location.location_type, location.custodian_person_id
              INTO current_parent_location_id,
                   current_location_owner_org_id, current_location_status,
                   current_location_type, current_custodian_id
              FROM public.stock_locations AS location
             WHERE location.id = current_location_id
             FOR SHARE OF location;
            IF NOT FOUND OR current_location_status <> 'active' THEN
                RAISE EXCEPTION '{GRAPH_ERROR}';
            END IF;
            IF current_location_id = scope_row.location_id THEN
                target_location_type := current_location_type;
                target_location_custodian_id := current_custodian_id;
                IF current_location_type NOT IN ('region', 'personal') THEN
                    RAISE EXCEPTION '{GRAPH_ERROR}';
                END IF;
            END IF;

            current_org_id := current_location_owner_org_id;
            visited_org_ids := ARRAY[]::uuid[];
            reached_region := false;
            WHILE current_org_id IS NOT NULL LOOP
                IF current_org_id = ANY(visited_org_ids) THEN
                    RAISE EXCEPTION '{GRAPH_ERROR}';
                END IF;
                visited_org_ids := pg_catalog.array_append(
                    visited_org_ids, current_org_id);
                SELECT organization.parent_id, organization.status
                  INTO current_parent_org_id, current_org_status
                  FROM public.organizations AS organization
                 WHERE organization.id = current_org_id
                 FOR SHARE OF organization;
                IF NOT FOUND OR current_org_status <> 'active' THEN
                    RAISE EXCEPTION '{GRAPH_ERROR}';
                END IF;
                IF current_org_id = guard_task.region_org_id THEN
                    reached_region := true;
                    EXIT;
                END IF;
                current_org_id := current_parent_org_id;
            END LOOP;
            IF NOT reached_region THEN
                RAISE EXCEPTION '{GRAPH_ERROR}';
            END IF;
            current_location_id := current_parent_location_id;
        END LOOP;

        SELECT count(*),
               (pg_catalog.array_agg(
                    custody.custodian_person_id ORDER BY custody.id))[1]
          INTO current_custody_count, current_custodian_id
          FROM public.custody_assignments AS custody
         WHERE custody.location_id = scope_row.location_id
           AND custody.valid_from <= NEW.started_at
           AND (custody.valid_to IS NULL
                OR custody.valid_to > NEW.started_at);
        IF current_custody_count > 1
           OR (target_location_type = 'personal' AND (
               current_custody_count <> 1
               OR target_location_custodian_id IS NULL
               OR current_custodian_id IS DISTINCT FROM
                  target_location_custodian_id))
           OR (target_location_type = 'region'
               AND target_location_custodian_id IS NOT NULL
               AND target_location_custodian_id IS DISTINCT FROM
                  current_custodian_id)
           OR scope_row.custodian_person_id_snapshot IS DISTINCT FROM
              current_custodian_id
           OR (guard_task.task_type IN ('termination', 'personal')
               AND target_location_type <> 'personal') THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;

        IF NOT EXISTS (
            WITH RECURSIVE target_organization_path(id, parent_id) AS (
                SELECT organization.id, organization.parent_id
                  FROM public.organizations AS organization
                 WHERE organization.id = CASE
                     WHEN target_location_type = 'personal' THEN (
                         SELECT target_person.organization_id
                           FROM public.people AS target_person
                          WHERE target_person.id = current_custodian_id)
                     ELSE scope_row.owner_org_id END
                   AND organization.status = 'active'
                UNION
                SELECT parent.id, parent.parent_id
                  FROM target_organization_path AS child
                  JOIN public.organizations AS parent
                    ON parent.id = child.parent_id
                 WHERE parent.status = 'active'
            )
            SELECT 1
              FROM public.users AS assignee_user
              JOIN public.people AS assignee_person
                ON assignee_person.id = assignee_user.person_id
              JOIN public.organizations AS assignee_organization
                ON assignee_organization.id = assignee_person.organization_id
              JOIN public.role_assignments AS assignment
                ON assignment.user_id = assignee_user.id
              JOIN public.roles AS assignee_role
                ON assignee_role.id = assignment.role_id
              JOIN public.role_permissions AS permission_binding
                ON permission_binding.role_id = assignee_role.id
               AND permission_binding.effect = 'allow'
              JOIN public.permissions AS permission
                ON permission.id = permission_binding.permission_id
             WHERE assignee_user.id = scope_row.assignee_user_id
               AND assignee_user.account_status = 'active'
               AND assignee_user.is_active
               AND assignee_user.authorization_version > 0
               AND assignee_person.employment_status = 'active'
               AND assignee_organization.status = 'active'
               AND assignee_organization.org_type IN (
                   'headquarters', 'region_company', 'department')
               AND (SELECT count(*) FROM public.users AS same_person_user
                     WHERE same_person_user.person_id = assignee_person.id
                       AND same_person_user.account_status = 'active'
                       AND same_person_user.is_active) = 1
               AND EXISTS (
                   SELECT 1 FROM public.auth_identities AS identity
                    WHERE identity.user_id = assignee_user.id
                      AND identity.status = 'active'
                      AND identity.verified_at IS NOT NULL
                      AND identity.revoked_at IS NULL)
               AND assignee_role.status = 'active'
               AND NOT assignee_role.is_external
               AND assignment.status IN ('scheduled', 'active')
               AND assignment.revoked_at IS NULL
               AND assignment.valid_from <= NEW.started_at
               AND (assignment.valid_to IS NULL
                    OR assignment.valid_to > NEW.started_at)
               AND permission.resource = 'stocktake'
               AND permission.action = 'count'
               AND permission.field_code = ''
               AND (
                   (assignee_role.code = 'admin'
                    AND assignment.scope_type = 'national'
                    AND assignment.scope_id = '*'
                    AND assignee_organization.org_type = 'headquarters')
                   OR
                   (assignee_role.code = 'provincial_manager'
                    AND assignment.scope_type = 'organization'
                    AND EXISTS (
                        SELECT 1 FROM public.organizations AS assigned_region
                         WHERE assigned_region.id::text = assignment.scope_id
                           AND assigned_region.status = 'active'
                           AND assigned_region.org_type = 'region_company')
                    AND EXISTS (
                        SELECT 1 FROM target_organization_path AS target_path
                         WHERE target_path.id::text = assignment.scope_id))
                   OR
                   (assignee_role.code = 'technician'
                    AND assignment.scope_type = 'person'
                    AND assignment.scope_id = assignee_person.id::text
                    AND target_location_type = 'personal'
                    AND assignee_person.id = current_custodian_id)
               )
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
                    WHERE deny_assignment.user_id = assignee_user.id
                      AND deny_assignment.status IN (
                          'scheduled', 'active', 'expired', 'revoked')
                      AND deny_assignment.valid_from <= NEW.started_at
                      AND (deny_assignment.valid_to IS NULL
                           OR deny_assignment.valid_to > NEW.started_at)
                      AND (deny_assignment.revoked_at IS NULL
                           OR deny_assignment.revoked_at > NEW.started_at)
                      AND denied_permission.resource = 'stocktake'
                      AND denied_permission.action = 'count'
                      AND denied_permission.field_code = ''
                      AND (
                          (deny_assignment.scope_type = 'national'
                           AND deny_assignment.scope_id = '*')
                          OR
                          (target_location_type = 'personal'
                           AND deny_assignment.scope_type = 'person'
                           AND deny_assignment.scope_id =
                               assignee_person.id::text)
                          OR
                          (deny_assignment.scope_type = 'organization'
                           AND EXISTS (
                               SELECT 1
                                 FROM target_organization_path AS target_path
                                WHERE target_path.id::text =
                                      deny_assignment.scope_id))
                      )
               )
        ) THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;
        IF scope_row.material_id IS NOT NULL AND (
               NOT EXISTS (
                   SELECT 1 FROM public.materials AS material
                    WHERE material.id = scope_row.material_id
                      AND material.status = 'active')
               OR (SELECT count(*)
                     FROM public.material_inventory_policies AS policy
                    WHERE policy.material_id = scope_row.material_id
                      AND policy.effective_from <= NEW.cutoff_at
                      AND (policy.effective_to IS NULL
                           OR policy.effective_to > NEW.cutoff_at)) <> 1
           ) THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;

        IF scope_row.scope_key IS DISTINCT FROM pg_catalog.concat(
               'stocktake-v1', ':', scope_row.owner_org_id::text, ':',
               scope_row.location_id::text, ':',
               COALESCE(scope_row.material_id::text, '*'), ':',
               COALESCE(scope_row.condition_code, '*'), ':',
               COALESCE(scope_row.availability_bucket, '*')) THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;
        computed_scope_sha256 := {scope_hash};
        IF scope_row.scope_sha256 IS DISTINCT FROM computed_scope_sha256 THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;
    END LOOP;
    IF guard_task.task_type = 'personal' AND (
           NEW.scope_count <> 1
           OR (SELECT count(*)
                 FROM public.stock_locations AS personal_location
                WHERE personal_location.location_type = 'personal'
                  AND personal_location.custodian_person_id =
                      NEW.started_by_person_id
                  AND personal_location.status = 'active') <> 1
           OR EXISTS (
               SELECT 1 FROM public.stocktake_scopes AS scope
                WHERE scope.task_id = NEW.task_id
                  AND (scope.assignee_user_id <> NEW.started_by_user_id
                       OR scope.custodian_person_id_snapshot IS DISTINCT FROM
                          NEW.started_by_person_id))
       ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    IF guard_task.task_type = 'termination' AND (
           (SELECT count(DISTINCT scope.custodian_person_id_snapshot)
              FROM public.stocktake_scopes AS scope
             WHERE scope.task_id = NEW.task_id) <> 1
           OR EXISTS (
               SELECT 1 FROM public.stocktake_scopes AS scope
                WHERE scope.task_id = NEW.task_id
                  AND scope.custodian_person_id_snapshot IS NULL)
           OR EXISTS (
               WITH RECURSIVE region_descendants(id) AS (
                   SELECT region.id
                     FROM public.organizations AS region
                    WHERE region.id = guard_task.region_org_id
                      AND region.status = 'active'
                   UNION
                   SELECT child.id
                     FROM region_descendants AS parent
                     JOIN public.organizations AS child
                       ON child.parent_id = parent.id
                      AND child.status = 'active'
               ), selected_custodian AS (
                   SELECT scope.custodian_person_id_snapshot AS person_id
                     FROM public.stocktake_scopes AS scope
                    WHERE scope.task_id = NEW.task_id
                    LIMIT 1
               )
               SELECT 1
                 FROM public.stock_locations AS location
                 CROSS JOIN selected_custodian AS custodian
                WHERE location.location_type = 'personal'
                  AND location.status = 'active'
                  AND location.custodian_person_id = custodian.person_id
                  AND location.owner_org_id IN (
                      SELECT id FROM region_descendants)
                  AND NOT EXISTS (
                      SELECT 1 FROM public.stocktake_scopes AS scope
                       WHERE scope.task_id = NEW.task_id
                         AND scope.location_id = location.id)
           )
       ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    computed_scope_manifest_sha256 := {scope_manifest_hash};
    IF computed_scope_manifest_sha256 IS DISTINCT FROM
           guard_task.scope_manifest_sha256
       OR computed_scope_manifest_sha256 IS DISTINCT FROM
           NEW.scope_manifest_sha256 THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    IF EXISTS (
           SELECT 1
             FROM public.stocktake_scopes AS scope
             JOIN public.stock_accounts AS stock_account
               ON stock_account.owner_org_id = scope.owner_org_id
              AND stock_account.location_id = scope.location_id
              AND (scope.material_id IS NULL
                   OR scope.material_id = stock_account.material_id)
              AND (scope.condition_code IS NULL
                   OR scope.condition_code = stock_account.condition_code)
              AND (scope.availability_bucket IS NULL
                   OR scope.availability_bucket = stock_account.availability_bucket)
             LEFT JOIN public.stocktake_snapshot_lines AS snapshot
               ON snapshot.task_id = scope.task_id
              AND snapshot.scope_id = scope.id
              AND snapshot.stock_account_id = stock_account.id
            WHERE scope.task_id = NEW.task_id
              AND snapshot.id IS NULL
       )
       OR EXISTS (
           SELECT 1
             FROM public.stocktake_snapshot_lines AS snapshot
             JOIN public.stocktake_scopes AS scope
               ON scope.id = snapshot.scope_id
              AND scope.task_id = snapshot.task_id
             LEFT JOIN public.stock_accounts AS stock_account
               ON stock_account.id = snapshot.stock_account_id
              AND stock_account.owner_org_id = scope.owner_org_id
              AND stock_account.location_id = scope.location_id
              AND (scope.material_id IS NULL
                   OR scope.material_id = stock_account.material_id)
              AND (scope.condition_code IS NULL
                   OR scope.condition_code = stock_account.condition_code)
              AND (scope.availability_bucket IS NULL
                   OR scope.availability_bucket = stock_account.availability_bucket)
            WHERE snapshot.task_id = NEW.task_id
              AND stock_account.id IS NULL
       )
       OR EXISTS (
           SELECT 1
             FROM public.stocktake_scopes AS scope
             JOIN public.stock_locations AS location
               ON location.id = scope.location_id
              AND location.location_type = 'personal'
             JOIN public.stock_accounts AS stock_account
               ON stock_account.owner_org_id = scope.owner_org_id
              AND stock_account.location_id = scope.location_id
            WHERE scope.task_id = NEW.task_id
              AND stock_account.custodian_person_id IS DISTINCT FROM
                  scope.custodian_person_id_snapshot
       ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    IF EXISTS (
        WITH selected_accounts AS (
            SELECT snapshot.stock_account_id AS id
              FROM public.stocktake_snapshot_lines AS snapshot
             WHERE snapshot.task_id = NEW.task_id
        ),
        relevant_serials AS (
            SELECT DISTINCT binding.serial_id
              FROM public.inventory_movement_serials AS binding
              JOIN public.inventory_movements AS movement
                ON movement.id = binding.movement_id
               AND movement.transaction_id = binding.transaction_id
             WHERE movement.from_account_id IN (SELECT id FROM selected_accounts)
                OR movement.to_account_id IN (SELECT id FROM selected_accounts)
            UNION
            SELECT position.serial_id
              FROM public.serial_current_positions AS position
             WHERE position.stock_account_id IN (SELECT id FROM selected_accounts)
        ),
        latest AS (
            SELECT DISTINCT ON (binding.serial_id)
                   binding.serial_id,
                   movement.id AS movement_id,
                   movement.to_account_id,
                   transaction_row.ledger_cursor
              FROM public.inventory_movement_serials AS binding
              JOIN public.inventory_movements AS movement
                ON movement.id = binding.movement_id
               AND movement.transaction_id = binding.transaction_id
              JOIN public.inventory_transactions AS transaction_row
                ON transaction_row.id = binding.transaction_id
               AND transaction_row.status = 'posted'
             WHERE binding.serial_id IN (SELECT serial_id FROM relevant_serials)
             ORDER BY binding.serial_id, transaction_row.ledger_cursor DESC,
                      movement.line_no DESC, movement.id DESC
        )
        SELECT 1
          FROM relevant_serials AS relevant
          LEFT JOIN latest ON latest.serial_id = relevant.serial_id
          LEFT JOIN public.serial_current_positions AS position
            ON position.serial_id = relevant.serial_id
         WHERE latest.movement_id IS NULL
            OR latest.ledger_cursor > NEW.cutoff_ledger_cursor
            OR (
                latest.to_account_id IN (SELECT id FROM selected_accounts)
                AND (position.stock_account_id IS DISTINCT FROM latest.to_account_id
                     OR position.last_movement_id IS DISTINCT FROM latest.movement_id)
            )
            OR (
                latest.to_account_id NOT IN (SELECT id FROM selected_accounts)
                AND position.stock_account_id IN (SELECT id FROM selected_accounts)
            )
    ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    FOR snapshot_row IN
        SELECT snapshot.id AS snapshot_id,
               snapshot.scope_id,
               snapshot.stock_account_id,
               snapshot.book_qty,
               snapshot.ledger_cursor,
               snapshot.account_dimension_sha256,
               snapshot.serial_snapshot_jsonb,
               snapshot.serial_snapshot_sha256,
               snapshot.serial_count,
               snapshot.created_at,
               stock_account.owner_org_id,
               stock_account.custodian_person_id,
               stock_account.location_id,
               stock_account.material_id,
               stock_account.condition_code,
               stock_account.availability_bucket,
               stock_account.lot_id
          FROM public.stocktake_snapshot_lines AS snapshot
          JOIN public.stock_accounts AS stock_account
            ON stock_account.id = snapshot.stock_account_id
         WHERE snapshot.task_id = NEW.task_id
         ORDER BY snapshot.scope_id, snapshot.stock_account_id
    LOOP
        computed_account_dimension_sha256 := {account_dimension_hash};
        IF snapshot_row.account_dimension_sha256 IS DISTINCT FROM
               computed_account_dimension_sha256
           OR snapshot_row.ledger_cursor <> NEW.cutoff_ledger_cursor
           OR snapshot_row.created_at IS DISTINCT FROM NEW.started_at THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;

        SELECT COALESCE(sum(
                   CASE WHEN movement.to_account_id = snapshot_row.stock_account_id
                        THEN movement.quantity ELSE 0 END
                   - CASE WHEN movement.from_account_id = snapshot_row.stock_account_id
                          THEN movement.quantity ELSE 0 END), 0),
               COALESCE(max(transaction_row.ledger_cursor), 0),
               count(DISTINCT transaction_row.id)
          INTO expected_book_qty, expected_balance_cursor,
               expected_balance_version
          FROM public.inventory_transactions AS transaction_row
          JOIN public.inventory_movements AS movement
            ON movement.transaction_id = transaction_row.id
         WHERE transaction_row.status = 'posted'
           AND transaction_row.ledger_cursor <= NEW.cutoff_ledger_cursor
           AND (movement.from_account_id = snapshot_row.stock_account_id
                OR movement.to_account_id = snapshot_row.stock_account_id);
        SELECT count(*) INTO projected_balance_count
          FROM public.stock_balances AS balance
         WHERE balance.stock_account_id = snapshot_row.stock_account_id
           AND balance.quantity = expected_book_qty
           AND balance.ledger_cursor = expected_balance_cursor
           AND balance.version = expected_balance_version;
        IF expected_book_qty < 0
           OR snapshot_row.book_qty IS DISTINCT FROM expected_book_qty
           OR projected_balance_count <> 1
              AND NOT (
                  projected_balance_count = 0
                  AND expected_book_qty = 0
                  AND expected_balance_cursor = 0
                  AND expected_balance_version = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM public.stock_balances AS balance
                       WHERE balance.stock_account_id = snapshot_row.stock_account_id))
           OR EXISTS (
               SELECT 1
                 FROM public.inventory_transactions AS transaction_row
                 JOIN public.inventory_movements AS movement
                   ON movement.transaction_id = transaction_row.id
                WHERE transaction_row.status = 'posted'
                  AND transaction_row.ledger_cursor > NEW.cutoff_ledger_cursor
                  AND (movement.from_account_id = snapshot_row.stock_account_id
                       OR movement.to_account_id = snapshot_row.stock_account_id)
           ) THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;

        SELECT COALESCE(pg_catalog.jsonb_agg(
                   pg_catalog.jsonb_build_object(
                       'last_movement_id', movement.id::text,
                       'ledger_cursor', transaction_row.ledger_cursor,
                       'lot_id', CASE WHEN serial.lot_id IS NULL THEN NULL
                           ELSE serial.lot_id::text END,
                       'qr_code', serial.qr_code,
                       'serial_id', serial.id::text,
                       'serial_no', serial.serial_no)
                   ORDER BY serial.id), '[]'::jsonb),
               count(*)
          INTO expected_serial_snapshot, expected_serial_count
          FROM public.serial_current_positions AS position
          JOIN public.inventory_serials AS serial
            ON serial.id = position.serial_id
           AND serial.lifecycle_status = 'active'
           AND serial.material_id = snapshot_row.material_id
           AND serial.lot_id IS NOT DISTINCT FROM snapshot_row.lot_id
          JOIN public.inventory_movements AS movement
            ON movement.id = position.last_movement_id
           AND movement.to_account_id = snapshot_row.stock_account_id
          JOIN public.inventory_movement_serials AS binding
            ON binding.movement_id = movement.id
           AND binding.transaction_id = movement.transaction_id
           AND binding.serial_id = serial.id
          JOIN public.inventory_transactions AS transaction_row
            ON transaction_row.id = movement.transaction_id
           AND transaction_row.status = 'posted'
           AND transaction_row.ledger_cursor <= NEW.cutoff_ledger_cursor
         WHERE position.stock_account_id = snapshot_row.stock_account_id;
        IF expected_serial_count <> (
               SELECT count(*) FROM public.serial_current_positions AS position
                WHERE position.stock_account_id = snapshot_row.stock_account_id)
           OR snapshot_row.serial_snapshot_jsonb IS DISTINCT FROM
              expected_serial_snapshot
           OR snapshot_row.serial_count <> expected_serial_count THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;
        computed_serial_snapshot_sha256 := {serial_snapshot_hash};
        IF snapshot_row.serial_snapshot_sha256 IS DISTINCT FROM
               computed_serial_snapshot_sha256
           OR (SELECT count(*)
                 FROM public.material_inventory_policies AS policy
                WHERE policy.material_id = snapshot_row.material_id
                  AND policy.effective_from <= NEW.cutoff_at
                  AND (policy.effective_to IS NULL
                       OR policy.effective_to > NEW.cutoff_at)) <> 1
           OR NOT EXISTS (
               SELECT 1
                 FROM public.materials AS material
                 JOIN public.material_inventory_policies AS policy
                   ON policy.material_id = material.id
                  AND policy.effective_from <= NEW.cutoff_at
                  AND (policy.effective_to IS NULL
                       OR policy.effective_to > NEW.cutoff_at)
                WHERE material.id = snapshot_row.material_id
                  AND material.status = 'active'
                  AND snapshot_row.book_qty = pg_catalog.round(
                      snapshot_row.book_qty, policy.quantity_scale)
                  AND (policy.allow_fraction
                       OR snapshot_row.book_qty = pg_catalog.trunc(snapshot_row.book_qty))
                  AND (
                      (policy.tracking_mode IN ('none', 'serial')
                       AND snapshot_row.lot_id IS NULL)
                      OR
                      (policy.tracking_mode IN ('lot', 'lot_and_serial')
                       AND EXISTS (
                           SELECT 1 FROM public.inventory_lots AS lot
                            WHERE lot.id = snapshot_row.lot_id
                              AND lot.material_id = snapshot_row.material_id))
                  )
                  AND (
                      (policy.tracking_mode IN ('none', 'lot')
                       AND expected_serial_count = 0)
                      OR
                      (policy.tracking_mode IN ('serial', 'lot_and_serial')
                       AND snapshot_row.book_qty = pg_catalog.trunc(snapshot_row.book_qty)
                       AND expected_serial_count = snapshot_row.book_qty)
                  )
           ) THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;
    END LOOP;

    computed_snapshot_manifest_sha256 := {snapshot_manifest_hash};
    IF computed_snapshot_manifest_sha256 IS DISTINCT FROM
           guard_task.snapshot_manifest_sha256
       OR computed_snapshot_manifest_sha256 IS DISTINCT FROM
           NEW.snapshot_manifest_sha256 THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    IF NOT EXISTS (
           SELECT 1
             FROM public.users AS actor_user
             JOIN public.people AS actor_person
               ON actor_person.id = actor_user.person_id
             JOIN public.organizations AS person_organization
               ON person_organization.id = actor_person.organization_id
             JOIN public.role_assignments AS assignment
               ON assignment.id = NEW.started_role_assignment_id
              AND assignment.user_id = actor_user.id
             JOIN public.roles AS actor_role
               ON actor_role.id = assignment.role_id
             LEFT JOIN public.organizations AS target_organization
               ON target_organization.id::text = assignment.scope_id
             JOIN public.role_permissions AS permission_binding
               ON permission_binding.role_id = actor_role.id
              AND permission_binding.effect = 'allow'
             JOIN public.permissions AS permission
               ON permission.id = permission_binding.permission_id
            WHERE actor_user.id = NEW.started_by_user_id
              AND actor_user.person_id = NEW.started_by_person_id
              AND actor_user.account_status = 'active'
              AND actor_user.is_active
              AND actor_user.authorization_version = NEW.authorization_version
              AND actor_person.employment_status = 'active'
              AND person_organization.status = 'active'
              AND actor_role.status = 'active'
              AND NOT actor_role.is_external
              AND actor_role.code = NEW.role_code
              AND assignment.scope_type = NEW.scope_type
              AND assignment.scope_id = NEW.scope_id_snapshot
              AND assignment.status IN ('scheduled', 'active')
              AND assignment.revoked_at IS NULL
              AND NEW.started_at <= pg_catalog.clock_timestamp()
              AND assignment.valid_from <= NEW.started_at
              AND (assignment.valid_to IS NULL
                   OR assignment.valid_to > NEW.started_at)
              AND EXISTS (
                  SELECT 1
                    FROM public.auth_identities AS identity
                   WHERE identity.user_id = actor_user.id
                     AND identity.status = 'active'
                     AND identity.verified_at IS NOT NULL
                     AND identity.revoked_at IS NULL
              )
              AND permission.resource = 'stocktake'
              AND permission.field_code = ''
              AND permission.action = CASE WHEN guard_task.task_type = 'personal'
                                           THEN 'count' ELSE 'manage' END
              AND (
                  (guard_task.task_type = 'personal'
                   AND guard_task.created_by_user_id = actor_user.id
                   AND actor_role.code = 'technician'
                   AND assignment.scope_type = 'person'
                   AND assignment.scope_id = actor_user.person_id::text
                   AND person_organization.org_type IN (
                       'headquarters', 'region_company', 'department'))
                  OR
                  (guard_task.task_type <> 'personal'
                   AND ((actor_role.code = 'admin'
                         AND assignment.scope_type = 'national'
                         AND assignment.scope_id = '*'
                         AND person_organization.org_type = 'headquarters')
                        OR
                        (actor_role.code = 'provincial_manager'
                         AND assignment.scope_type = 'organization'
                         AND assignment.scope_id = guard_task.region_org_id::text
                         AND person_organization.org_type IN (
                             'headquarters', 'region_company', 'department')
                         AND target_organization.status = 'active'
                         AND target_organization.org_type = 'region_company')))
              )
              AND NOT EXISTS (
                  SELECT 1
                    FROM public.role_assignments AS deny_assignment
                    JOIN public.roles AS deny_role
                      ON deny_role.id = deny_assignment.role_id
                     AND deny_role.status = 'active'
                    JOIN public.role_permissions AS deny_binding
                      ON deny_binding.role_id = deny_role.id
                    JOIN public.permissions AS denied_permission
                      ON denied_permission.id = deny_binding.permission_id
                   WHERE deny_assignment.user_id = actor_user.id
                     AND deny_assignment.status IN (
                         'scheduled', 'active', 'expired', 'revoked')
                     AND deny_assignment.valid_from <= NEW.started_at
                     AND (deny_assignment.valid_to IS NULL
                          OR deny_assignment.valid_to > NEW.started_at)
                     AND (deny_assignment.revoked_at IS NULL
                          OR deny_assignment.revoked_at > NEW.started_at)
                     AND deny_binding.effect = 'deny'
                     AND denied_permission.resource = 'stocktake'
                     AND denied_permission.action = permission.action
                     AND denied_permission.field_code = ''
                     AND (
                         (deny_assignment.scope_type = 'national'
                          AND deny_assignment.scope_id = '*')
                         OR (guard_task.task_type = 'personal'
                             AND deny_assignment.scope_type = 'person'
                             AND deny_assignment.scope_id = actor_user.person_id::text)
                         OR (guard_task.task_type <> 'personal'
                             AND deny_assignment.scope_type = 'organization'
                             AND EXISTS (
                                 WITH RECURSIVE target_path(id, parent_id) AS (
                                     SELECT organization.id,
                                            organization.parent_id
                                       FROM public.organizations AS organization
                                      WHERE organization.id =
                                            guard_task.region_org_id
                                     UNION
                                     SELECT parent.id, parent.parent_id
                                       FROM target_path AS child
                                       JOIN public.organizations AS parent
                                         ON parent.id = child.parent_id
                                        AND parent.status = 'active'
                                 )
                                 SELECT 1 FROM target_path
                                  WHERE target_path.id::text =
                                        deny_assignment.scope_id))
                         OR (guard_task.task_type = 'personal'
                             AND deny_assignment.scope_type = 'organization'
                             AND EXISTS (
                                 WITH RECURSIVE person_path(id, parent_id) AS (
                                     SELECT organization.id,
                                            organization.parent_id
                                       FROM public.organizations AS organization
                                      WHERE organization.id =
                                            actor_person.organization_id
                                     UNION
                                     SELECT parent.id, parent.parent_id
                                       FROM person_path AS child
                                       JOIN public.organizations AS parent
                                         ON parent.id = child.parent_id
                                        AND parent.status = 'active'
                                 )
                                 SELECT 1 FROM person_path
                                  WHERE person_path.id::text =
                                        deny_assignment.scope_id))
                     )
              )
       ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    IF (SELECT count(*) FROM public.stocktake_rounds AS round_row
         WHERE round_row.task_id = NEW.task_id) <> 1
       OR EXISTS (
           SELECT 1
             FROM public.inventory_freezes AS freeze_row
            WHERE freeze_row.task_id = NEW.task_id
              AND (
                  freeze_row.status <> 'active'
                  OR freeze_row.valid_to IS NOT NULL
                  OR freeze_row.released_by_user_id IS NOT NULL
                  OR freeze_row.release_reason IS DISTINCT FROM ''
                  OR freeze_row.version <> 0
                  OR freeze_row.valid_from IS DISTINCT FROM NEW.started_at
                  OR freeze_row.created_at IS DISTINCT FROM NEW.started_at
                  OR freeze_row.updated_at IS DISTINCT FROM NEW.started_at
                  OR freeze_row.created_by_user_id IS DISTINCT FROM
                     NEW.started_by_user_id
              )
       )
       OR NOT EXISTS (
           SELECT 1
             FROM public.stocktake_rounds AS round_row
            WHERE round_row.id = NEW.initial_round_id
              AND round_row.task_id = NEW.task_id
              AND round_row.round_no = 1
              AND round_row.round_type = 'initial'
              AND round_row.status = 'counting'
              AND round_row.submitted_by_user_id IS NULL
              AND round_row.started_at = NEW.started_at
              AND round_row.submitted_at IS NULL
              AND round_row.count_manifest_sha256 IS NULL
              AND round_row.idempotency_key_hash = NEW.idempotency_key_hash
              AND round_row.recount_case_id IS NULL
              AND round_row.created_at = NEW.started_at
              AND round_row.updated_at = NEW.started_at
       ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    NEW.graph_manifest_sha256 := {graph_manifest};
    IF NEW.graph_manifest_sha256 IS NULL
       OR NEW.graph_manifest_sha256 !~ '^[0-9a-f]{{64}}$' THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_validator_function_sql() -> str:
    graph_manifest = _graph_manifest_expression("completion")
    authorization_document = _authorization_document_expression("completion")
    return f"""
CREATE FUNCTION public.{PG_VALIDATE_FUNCTION}(checked_task_id uuid)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict error
DECLARE
    task_row public.stocktake_tasks%ROWTYPE;
    completion public.{COMPLETION_TABLE}%ROWTYPE;
    computed_graph_manifest text;
    computed_authorization_sha256 text;
    expected_transition_metadata jsonb;
    expected_counting_metadata jsonb;
    scope_total bigint;
    freeze_total bigint;
    snapshot_total bigint;
    transition_candidate_total bigint;
    transition_total bigint;
    audit_candidate_total bigint;
    audit_total bigint;
BEGIN
    SELECT task.* INTO task_row
      FROM public.stocktake_tasks AS task
     WHERE task.id = checked_task_id
     FOR UPDATE;
    IF NOT FOUND OR task_row.task_type NOT IN {NONOPENING_SQL} THEN
        RETURN;
    END IF;

    SELECT start_completion.* INTO completion
      FROM public.{COMPLETION_TABLE} AS start_completion
     WHERE start_completion.task_id = checked_task_id;
    IF NOT FOUND THEN
        IF task_row.status <> 'draft'
           OR task_row.current_round_no <> 0
           OR task_row.cutoff_ledger_cursor IS NOT NULL
           OR task_row.cutoff_at IS NOT NULL
           OR task_row.snapshot_manifest_sha256 IS NOT NULL
           OR task_row.issued_at IS NOT NULL
           OR task_row.frozen_at IS NOT NULL
           OR EXISTS (SELECT 1 FROM public.inventory_freezes WHERE task_id = checked_task_id)
           OR EXISTS (SELECT 1 FROM public.stocktake_snapshot_lines WHERE task_id = checked_task_id)
           OR EXISTS (SELECT 1 FROM public.stocktake_rounds WHERE task_id = checked_task_id)
           OR EXISTS (
               SELECT 1 FROM public.state_transition_events AS event
                WHERE event.aggregate_type = 'stocktake_task'
                  AND lower(replace(event.aggregate_id, '-', '')) =
                      replace(checked_task_id::text, '-', '')
                  AND (
                      event.reason IN (
                          'stocktake_task_issued', 'stocktake_task_frozen',
                          'stocktake_initial_round_started')
                      OR event.idempotency_key LIKE
                         ('stocktake' || ':' || 'start' || ':' || '%')
                      OR (
                          event.metadata_jsonb->>'schema' = '{COMMAND_SCHEMA}'
                          AND event.metadata_jsonb->>'operation' = 'start'
                      )))
           OR EXISTS (
               SELECT 1 FROM public.audit_events AS audit
                WHERE audit.aggregate_type = 'stocktake_task'
                  AND lower(replace(audit.aggregate_id, '-', '')) =
                      replace(checked_task_id::text, '-', '')
                  AND audit.action = 'stocktake.task.started') THEN
            RAISE EXCEPTION '{GRAPH_ERROR}';
        END IF;
        RETURN;
    END IF;

    IF task_row.status NOT IN {POST_START_STATUS_SQL}
       OR task_row.version < completion.started_task_version
       OR task_row.cutoff_ledger_cursor IS DISTINCT FROM completion.cutoff_ledger_cursor
       OR task_row.cutoff_at IS DISTINCT FROM completion.cutoff_at
       OR task_row.scope_manifest_sha256 IS DISTINCT FROM completion.scope_manifest_sha256
       OR task_row.snapshot_manifest_sha256 IS DISTINCT FROM completion.snapshot_manifest_sha256
       OR task_row.current_round_no < 1
       OR task_row.issued_at IS DISTINCT FROM completion.started_at
       OR task_row.frozen_at IS DISTINCT FROM completion.started_at
       OR completion.created_at IS DISTINCT FROM completion.started_at
       OR completion.cutoff_at > completion.started_at
       OR completion.started_task_version <> completion.expected_task_version + 1
       OR completion.active_freeze_count <> completion.scope_count THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    SELECT count(*) INTO scope_total
      FROM public.stocktake_scopes AS scope
     WHERE scope.task_id = checked_task_id;
    SELECT count(*) INTO freeze_total
      FROM public.inventory_freezes AS freeze_row
     WHERE freeze_row.task_id = checked_task_id;
    SELECT count(*) INTO snapshot_total
      FROM public.stocktake_snapshot_lines AS snapshot
     WHERE snapshot.task_id = checked_task_id;
    IF scope_total <> completion.scope_count
       OR freeze_total <> completion.active_freeze_count
       OR snapshot_total <> completion.snapshot_line_count
       OR EXISTS (
           SELECT 1
             FROM public.stocktake_scopes AS scope
             LEFT JOIN public.inventory_freezes AS freeze_row
               ON freeze_row.task_id = scope.task_id
              AND freeze_row.stocktake_scope_id = scope.id
              AND freeze_row.scope_key = scope.scope_key
            WHERE scope.task_id = checked_task_id
              AND (
                  freeze_row.id IS NULL
                  OR freeze_row.valid_from <> completion.started_at
                  OR freeze_row.created_at <> completion.started_at
                  OR freeze_row.created_by_user_id <> completion.started_by_user_id
              )
       )
       OR EXISTS (
           SELECT 1 FROM public.stocktake_snapshot_lines AS snapshot
            WHERE snapshot.task_id = checked_task_id
              AND snapshot.ledger_cursor <> completion.cutoff_ledger_cursor
       )
       OR NOT EXISTS (
           SELECT 1 FROM public.stocktake_rounds AS round_row
            WHERE round_row.id = completion.initial_round_id
              AND round_row.task_id = checked_task_id
              AND round_row.round_no = 1
              AND round_row.round_type = 'initial'
              AND round_row.started_at = completion.started_at
              AND round_row.created_at = completion.started_at
              AND round_row.idempotency_key_hash = completion.idempotency_key_hash
              AND round_row.recount_case_id IS NULL
       ) THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    expected_transition_metadata := pg_catalog.jsonb_build_object(
        'authorization_version', completion.authorization_version,
        'cutoff_ledger_cursor', completion.cutoff_ledger_cursor,
        'operation', 'start',
        'request_hash', completion.request_sha256,
        'schema', '{COMMAND_SCHEMA}',
        'scope_manifest_sha256', completion.scope_manifest_sha256,
        'snapshot_manifest_sha256', completion.snapshot_manifest_sha256
    );
    expected_counting_metadata := expected_transition_metadata ||
        pg_catalog.jsonb_build_object(
            'result', pg_catalog.jsonb_build_object(
                'active_freeze_count', completion.active_freeze_count,
                'cutoff_ledger_cursor', completion.cutoff_ledger_cursor,
                'initial_round_id', completion.initial_round_id::text,
                'scope_count', completion.scope_count,
                'snapshot_line_count', completion.snapshot_line_count,
                'status', 'counting',
                'task_id', completion.task_id::text,
                'task_type', task_row.task_type,
                'version', completion.started_task_version
            )
        );

    SELECT count(*) INTO transition_candidate_total
      FROM public.state_transition_events AS event
     WHERE event.aggregate_type = 'stocktake_task'
       AND lower(replace(event.aggregate_id, '-', '')) =
           replace(checked_task_id::text, '-', '')
       AND (
           event.reason IN (
               'stocktake_task_issued', 'stocktake_task_frozen',
               'stocktake_initial_round_started')
           OR event.idempotency_key IN (
               'stocktake' || ':' || 'start' || ':' ||
                   completion.idempotency_key_hash || ':' || 'issued',
               'stocktake' || ':' || 'start' || ':' ||
                   completion.idempotency_key_hash || ':' || 'frozen',
               'stocktake' || ':' || 'start' || ':' ||
                   completion.idempotency_key_hash || ':' || 'counting')
           OR (
               event.metadata_jsonb->>'schema' = '{COMMAND_SCHEMA}'
               AND event.metadata_jsonb->>'operation' = 'start')
       );
    IF transition_candidate_total <> 3 THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    SELECT count(*) INTO transition_total
      FROM public.state_transition_events AS event
     WHERE event.aggregate_type = 'stocktake_task'
       AND lower(replace(event.aggregate_id, '-', '')) =
           replace(checked_task_id::text, '-', '')
       AND event.idempotency_key IN (
           'stocktake' || ':' || 'start' || ':' ||
               completion.idempotency_key_hash || ':' || 'issued',
           'stocktake' || ':' || 'start' || ':' ||
               completion.idempotency_key_hash || ':' || 'frozen',
           'stocktake' || ':' || 'start' || ':' ||
               completion.idempotency_key_hash || ':' || 'counting'
       )
       AND event.actor_id = completion.started_by_user_id
       AND event.occurred_at = completion.started_at
       AND event.created_at = completion.started_at
       AND (
           (event.from_status = 'draft' AND event.to_status = 'issued'
            AND event.reason = 'stocktake_task_issued'
            AND event.idempotency_key =
                'stocktake' || ':' || 'start' || ':' ||
                completion.idempotency_key_hash || ':' || 'issued'
            AND event.metadata_jsonb = expected_transition_metadata)
           OR
           (event.from_status = 'issued' AND event.to_status = 'frozen'
            AND event.reason = 'stocktake_task_frozen'
            AND event.idempotency_key =
                'stocktake' || ':' || 'start' || ':' ||
                completion.idempotency_key_hash || ':' || 'frozen'
            AND event.metadata_jsonb = expected_transition_metadata)
           OR
           (event.from_status = 'frozen' AND event.to_status = 'counting'
            AND event.reason = 'stocktake_initial_round_started'
            AND event.idempotency_key =
                'stocktake' || ':' || 'start' || ':' ||
                completion.idempotency_key_hash || ':' || 'counting'
            AND event.metadata_jsonb = expected_counting_metadata)
       );
    IF transition_total <> 3 THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    SELECT count(*) INTO audit_candidate_total
      FROM public.audit_events AS audit
     WHERE audit.aggregate_type = 'stocktake_task'
       AND lower(replace(audit.aggregate_id, '-', '')) =
           replace(checked_task_id::text, '-', '')
       AND audit.action = 'stocktake.task.started';
    IF audit_candidate_total <> 1 THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    SELECT count(*) INTO audit_total
      FROM public.audit_events AS audit
     WHERE audit.stream_key = 'inventory'
       AND audit.aggregate_type = 'stocktake_task'
       AND lower(replace(audit.aggregate_id, '-', '')) =
           replace(checked_task_id::text, '-', '')
       AND audit.action = 'stocktake.task.started'
       AND audit.actor_user_id = completion.started_by_user_id
       AND audit.occurred_at = completion.started_at
       AND audit.created_at = completion.started_at
       AND audit.request_id ~ '^stocktake-request-[0-9a-f]{{64}}$'
       AND audit.before_jsonb = pg_catalog.jsonb_build_object(
           'status', 'draft', 'version', completion.expected_task_version)
       AND audit.after_jsonb = pg_catalog.jsonb_build_object(
           'active_freeze_count', completion.active_freeze_count,
           'cutoff_ledger_cursor', completion.cutoff_ledger_cursor,
           'initial_round_id', completion.initial_round_id::text,
           'scope_count', completion.scope_count,
           'snapshot_line_count', completion.snapshot_line_count,
           'snapshot_manifest_sha256', completion.snapshot_manifest_sha256,
           'status', 'counting',
           'task_type', task_row.task_type,
           'version', completion.started_task_version
       );
    IF audit_total <> 1 THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;

    computed_authorization_sha256 := pg_catalog.encode(
        pg_catalog.sha256(pg_catalog.convert_to(
            {authorization_document}, 'UTF8')), 'hex');
    computed_graph_manifest := {graph_manifest};
    IF completion.authorization_sha256 IS DISTINCT FROM computed_authorization_sha256
       OR completion.graph_manifest_sha256 IS DISTINCT FROM computed_graph_manifest
       OR completion.graph_manifest_sha256 !~ '^[0-9a-f]{{64}}$' THEN
        RAISE EXCEPTION '{GRAPH_ERROR}';
    END IF;
END
$$
"""


def _postgresql_dispatch_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_DISPATCH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict error
DECLARE
    old_document jsonb;
    new_document jsonb;
    old_task_text text;
    new_task_text text;
    old_task_id uuid;
    new_task_id uuid;
    old_has_completion boolean := false;
    new_has_completion boolean := false;
    start_completion record;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_document := pg_catalog.to_jsonb(OLD);
        IF TG_TABLE_NAME IN ('state_transition_events', 'audit_events') THEN
            IF old_document->>'aggregate_type' = 'stocktake_task' THEN
                old_task_text := old_document->>'aggregate_id';
            END IF;
        ELSIF TG_TABLE_NAME = 'stocktake_tasks' THEN
            old_task_text := old_document->>'id';
        ELSE
            old_task_text := old_document->>'task_id';
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_document := pg_catalog.to_jsonb(NEW);
        IF TG_TABLE_NAME IN ('state_transition_events', 'audit_events') THEN
            IF new_document->>'aggregate_type' = 'stocktake_task' THEN
                new_task_text := new_document->>'aggregate_id';
            END IF;
        ELSIF TG_TABLE_NAME = 'stocktake_tasks' THEN
            new_task_text := new_document->>'id';
        ELSE
            new_task_text := new_document->>'task_id';
        END IF;
    END IF;
    IF TG_TABLE_NAME = 'stocktake_tasks' AND new_task_text IS NOT NULL THEN
        SELECT completion.* INTO start_completion
          FROM public.{COMPLETION_TABLE} AS completion
         WHERE replace(completion.task_id::text, '-', '') =
               lower(replace(new_task_text, '-', ''));
        IF FOUND AND (
               TG_OP <> 'UPDATE'
               OR old_document->>'cutoff_ledger_cursor' IS NULL
               OR new_document->>'cutoff_ledger_cursor' IS NULL
           ) THEN
            IF TG_OP <> 'UPDATE'
               OR old_document->>'status' <> 'draft'
               OR (old_document->>'current_round_no')::integer <> 0
               OR (old_document->>'version')::bigint <>
                  start_completion.expected_task_version
               OR old_document->>'cutoff_ledger_cursor' IS NOT NULL
               OR old_document->>'cutoff_at' IS NOT NULL
               OR old_document->>'snapshot_manifest_sha256' IS NOT NULL
               OR old_document->>'issued_at' IS NOT NULL
               OR old_document->>'frozen_at' IS NOT NULL
               OR old_document->>'submitted_at' IS NOT NULL
               OR old_document->>'posted_at' IS NOT NULL
               OR old_document->>'closed_at' IS NOT NULL
               OR old_document->>'cancelled_at' IS NOT NULL
               OR new_document->>'status' <> 'counting'
               OR (new_document->>'current_round_no')::integer <> 1
               OR (new_document->>'version')::bigint <>
                  start_completion.started_task_version
               OR (new_document->>'cutoff_ledger_cursor')::bigint <>
                  start_completion.cutoff_ledger_cursor
               OR (new_document->>'cutoff_at')::timestamptz IS DISTINCT FROM
                  start_completion.cutoff_at
               OR new_document->>'scope_manifest_sha256' IS DISTINCT FROM
                  start_completion.scope_manifest_sha256
               OR new_document->>'snapshot_manifest_sha256' IS DISTINCT FROM
                  start_completion.snapshot_manifest_sha256
               OR (new_document->>'issued_at')::timestamptz IS DISTINCT FROM
                  start_completion.started_at
               OR (new_document->>'frozen_at')::timestamptz IS DISTINCT FROM
                  start_completion.started_at
               OR new_document->>'submitted_at' IS NOT NULL
               OR new_document->>'posted_at' IS NOT NULL
               OR new_document->>'closed_at' IS NOT NULL
               OR new_document->>'cancelled_at' IS NOT NULL
               OR (old_document - ARRAY[
                      'status', 'current_round_no', 'cutoff_ledger_cursor',
                      'cutoff_at', 'snapshot_manifest_sha256', 'issued_at',
                      'frozen_at', 'version', 'updated_at'
                  ]::text[]) IS DISTINCT FROM
                  (new_document - ARRAY[
                      'status', 'current_round_no', 'cutoff_ledger_cursor',
                      'cutoff_at', 'snapshot_manifest_sha256', 'issued_at',
                      'frozen_at', 'version', 'updated_at'
                  ]::text[]) THEN
                RAISE EXCEPTION '{GRAPH_ERROR}';
            END IF;
        END IF;
    END IF;
    IF old_task_text IS NOT NULL THEN
        SELECT completion.task_id INTO old_task_id
          FROM public.{COMPLETION_TABLE} AS completion
         WHERE replace(completion.task_id::text, '-', '') =
               lower(replace(old_task_text, '-', ''));
        old_has_completion := FOUND;
        IF NOT old_has_completion THEN
            SELECT task.id INTO old_task_id
              FROM public.stocktake_tasks AS task
             WHERE replace(task.id::text, '-', '') =
                   lower(replace(old_task_text, '-', ''));
        END IF;
        IF old_task_id IS NOT NULL
           AND (TG_TABLE_NAME = '{COMPLETION_TABLE}' OR NOT old_has_completion) THEN
            PERFORM public.{PG_VALIDATE_FUNCTION}(old_task_id);
        END IF;
    END IF;
    IF new_task_text IS NOT NULL THEN
        SELECT completion.task_id INTO new_task_id
          FROM public.{COMPLETION_TABLE} AS completion
         WHERE replace(completion.task_id::text, '-', '') =
               lower(replace(new_task_text, '-', ''));
        new_has_completion := FOUND;
        IF NOT new_has_completion THEN
            SELECT task.id INTO new_task_id
              FROM public.stocktake_tasks AS task
             WHERE replace(task.id::text, '-', '') =
                   lower(replace(new_task_text, '-', ''));
        END IF;
        IF new_task_id IS NOT NULL
           AND (old_task_id IS NULL OR new_task_id <> old_task_id) THEN
            IF TG_TABLE_NAME = '{COMPLETION_TABLE}' OR NOT new_has_completion THEN
                PERFORM public.{PG_VALIDATE_FUNCTION}(new_task_id);
            END IF;
        ELSIF new_task_id IS NOT NULL
              AND TG_TABLE_NAME = '{COMPLETION_TABLE}' THEN
            PERFORM public.{PG_VALIDATE_FUNCTION}(new_task_id);
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$
"""


def _create_postgresql_functions() -> None:
    signatures = (
        (PG_GUARD_FUNCTION, ""),
        (PG_VALIDATE_FUNCTION, "uuid"),
        (PG_DISPATCH_FUNCTION, ""),
    )
    for statement in (
        _postgresql_guard_function_sql(),
        _postgresql_validator_function_sql(),
        _postgresql_dispatch_function_sql(),
    ):
        op.execute(statement)
    for function_name, argument_types in signatures:
        signature = f"public.{function_name}({argument_types})"
        op.execute(f"ALTER FUNCTION {signature} OWNER TO {MIGRATION_ROLE}")
        op.execute(
            f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, {PRODUCTION_API_ROLE}"
        )


def _create_postgresql_triggers() -> None:
    op.execute(
        f"CREATE TRIGGER {PG_GUARD_TRIGGER} "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.{COMPLETION_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_GUARD_FUNCTION}()"
    )
    op.execute(
        f"ALTER TABLE public.{COMPLETION_TABLE} "
        f"ENABLE ALWAYS TRIGGER {PG_GUARD_TRIGGER}"
    )
    for table_name in SEALED_GUARD_TABLES:
        trigger_name = PG_SEALED_GUARD_TRIGGERS[table_name]
        op.execute(
            f"CREATE TRIGGER {trigger_name} "
            f"BEFORE INSERT OR UPDATE OR DELETE ON public.{table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{PG_GUARD_FUNCTION}()"
        )
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )
    for table_name in DEFERRED_TABLES:
        trigger_name = PG_DEFERRED_TRIGGERS[table_name]
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} "
            f"AFTER INSERT OR UPDATE OR DELETE ON public.{table_name} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_DISPATCH_FUNCTION}()"
        )
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )


def _apply_postgresql_acl() -> None:
    op.execute(f"REVOKE ALL ON TABLE public.{COMPLETION_TABLE} FROM PUBLIC")
    op.execute(
        f"REVOKE ALL ON TABLE public.{COMPLETION_TABLE} FROM {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE public.{COMPLETION_TABLE} "
        f"TO {PRODUCTION_API_ROLE}"
    )
    columns = ", ".join(TASK_START_UPDATE_COLUMNS)
    op.execute(
        f"GRANT UPDATE ({columns}) ON TABLE public.stocktake_tasks "
        f"TO {PRODUCTION_API_ROLE}"
    )


def _revoke_postgresql_acl() -> None:
    columns = ", ".join(TASK_START_UPDATE_COLUMNS)
    op.execute(
        f"REVOKE UPDATE ({columns}) ON TABLE public.stocktake_tasks "
        f"FROM {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON TABLE public.{COMPLETION_TABLE} FROM {PRODUCTION_API_ROLE}"
    )


def _sqlite_uuid_equal(left: str, right: str) -> str:
    return (
        f"lower(replace(CAST({left} AS TEXT), '-', '')) = "
        f"lower(replace(CAST({right} AS TEXT), '-', ''))"
    )


def _create_sqlite_triggers() -> None:
    task_id_matches = _sqlite_uuid_equal("task.id", "NEW.task_id")
    round_id_matches = _sqlite_uuid_equal("round_row.id", "NEW.initial_round_id")
    round_task_matches = _sqlite_uuid_equal("round_row.task_id", "NEW.task_id")
    actor_person_matches = _sqlite_uuid_equal("actor.person_id", "NEW.started_by_person_id")
    assignment_matches = _sqlite_uuid_equal(
        "assignment.id", "NEW.started_role_assignment_id"
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_VALIDATE_TRIGGER}
BEFORE INSERT ON {COMPLETION_TABLE}
WHEN NOT EXISTS (
    SELECT 1
      FROM stocktake_tasks AS task
     WHERE {task_id_matches}
       AND task.task_type IN {NONOPENING_SQL}
       AND task.status = 'counting'
       AND task.version = NEW.started_task_version
       AND NEW.started_task_version = NEW.expected_task_version + 1
       AND task.cutoff_ledger_cursor = NEW.cutoff_ledger_cursor
       AND task.cutoff_at = NEW.cutoff_at
       AND task.scope_manifest_sha256 = NEW.scope_manifest_sha256
       AND task.snapshot_manifest_sha256 = NEW.snapshot_manifest_sha256
       AND task.current_round_no = 1
       AND task.control_source_system_id IS NULL
       AND task.control_sync_run_id IS NULL
       AND task.control_snapshot_at IS NULL
       AND task.control_manifest_sha256 IS NULL
       AND task.issued_at = NEW.started_at
       AND task.frozen_at = NEW.started_at
       AND task.submitted_at IS NULL
       AND task.posted_at IS NULL
       AND task.closed_at IS NULL
       AND task.cancelled_at IS NULL
       AND NEW.cutoff_at <= NEW.started_at
       AND NEW.created_at = NEW.started_at
       AND NEW.scope_count = (
           SELECT count(*) FROM stocktake_scopes AS scope
            WHERE {_sqlite_uuid_equal('scope.task_id', 'NEW.task_id')})
       AND NOT EXISTS (
           SELECT 1 FROM stocktake_scopes AS scope
            WHERE {_sqlite_uuid_equal('scope.task_id', 'NEW.task_id')}
              AND (scope.created_at IS NULL
                   OR scope.created_at > NEW.cutoff_at))
       AND NEW.active_freeze_count = NEW.scope_count
       AND NEW.active_freeze_count = (
           SELECT count(*) FROM inventory_freezes AS freeze_row
            WHERE {_sqlite_uuid_equal('freeze_row.task_id', 'NEW.task_id')}
              AND freeze_row.status = 'active'
              AND freeze_row.valid_from = NEW.started_at
              AND freeze_row.valid_to IS NULL
              AND freeze_row.created_at = NEW.started_at
              AND freeze_row.updated_at = NEW.started_at
              AND freeze_row.created_by_user_id = NEW.started_by_user_id
              AND freeze_row.released_by_user_id IS NULL
              AND freeze_row.release_reason = ''
              AND freeze_row.version = 0)
       AND NEW.snapshot_line_count = (
           SELECT count(*) FROM stocktake_snapshot_lines AS snapshot
            WHERE {_sqlite_uuid_equal('snapshot.task_id', 'NEW.task_id')}
              AND snapshot.ledger_cursor = NEW.cutoff_ledger_cursor)
       AND (SELECT count(*) FROM stocktake_rounds AS round_row
             WHERE {_sqlite_uuid_equal('round_row.task_id', 'NEW.task_id')}) = 1
       AND EXISTS (
           SELECT 1 FROM stocktake_rounds AS round_row
            WHERE {round_id_matches} AND {round_task_matches}
              AND round_row.round_no = 1
              AND round_row.round_type = 'initial'
              AND round_row.status = 'counting'
              AND round_row.submitted_by_user_id IS NULL
              AND round_row.started_at = NEW.started_at
              AND round_row.submitted_at IS NULL
              AND round_row.count_manifest_sha256 IS NULL
              AND round_row.created_at = NEW.started_at
              AND round_row.updated_at = NEW.started_at
              AND round_row.idempotency_key_hash = NEW.idempotency_key_hash
              AND round_row.recount_case_id IS NULL)
       AND (SELECT count(*) FROM state_transition_events AS event
             WHERE event.aggregate_type = 'stocktake_task'
               AND lower(replace(event.aggregate_id, '-', '')) = lower(replace(CAST(NEW.task_id AS TEXT), '-', ''))
               AND (
                   event.reason IN (
                       'stocktake_task_issued', 'stocktake_task_frozen',
                       'stocktake_initial_round_started')
                   OR event.idempotency_key LIKE
                      ('stocktake' || ':' || 'start' || ':' || '%')
                   OR (json_extract(event.metadata_jsonb, '$.schema') = '{COMMAND_SCHEMA}'
                       AND json_extract(event.metadata_jsonb, '$.operation') = 'start')
               )) = 3
       AND (SELECT count(*) FROM state_transition_events AS event
             WHERE event.aggregate_type = 'stocktake_task'
               AND lower(replace(event.aggregate_id, '-', '')) = lower(replace(CAST(NEW.task_id AS TEXT), '-', ''))
               AND event.idempotency_key IN (
                   'stocktake' || ':' || 'start' || ':' ||
                       NEW.idempotency_key_hash || ':' || 'issued',
                   'stocktake' || ':' || 'start' || ':' ||
                       NEW.idempotency_key_hash || ':' || 'frozen',
                   'stocktake' || ':' || 'start' || ':' ||
                       NEW.idempotency_key_hash || ':' || 'counting')
               AND event.actor_id = NEW.started_by_user_id
               AND event.occurred_at = NEW.started_at
               AND json_extract(event.metadata_jsonb, '$.schema') = '{COMMAND_SCHEMA}'
               AND json_extract(event.metadata_jsonb, '$.operation') = 'start'
               AND json_extract(event.metadata_jsonb, '$.request_hash') = NEW.request_sha256
               AND json_extract(event.metadata_jsonb, '$.scope_manifest_sha256') = NEW.scope_manifest_sha256
               AND json_extract(event.metadata_jsonb, '$.snapshot_manifest_sha256') = NEW.snapshot_manifest_sha256
               AND json_extract(event.metadata_jsonb, '$.cutoff_ledger_cursor') = NEW.cutoff_ledger_cursor
               AND json_extract(event.metadata_jsonb, '$.authorization_version') = NEW.authorization_version
               AND (
                   (event.from_status = 'draft'
                    AND event.to_status = 'issued'
                    AND event.reason = 'stocktake_task_issued'
                    AND event.idempotency_key =
                        'stocktake' || ':' || 'start' || ':' ||
                        NEW.idempotency_key_hash || ':' || 'issued'
                    AND (SELECT count(*) FROM json_each(event.metadata_jsonb)) = 7)
                   OR
                   (event.from_status = 'issued'
                    AND event.to_status = 'frozen'
                    AND event.reason = 'stocktake_task_frozen'
                    AND event.idempotency_key =
                        'stocktake' || ':' || 'start' || ':' ||
                        NEW.idempotency_key_hash || ':' || 'frozen'
                    AND (SELECT count(*) FROM json_each(event.metadata_jsonb)) = 7)
                   OR
                   (event.from_status = 'frozen'
                    AND event.to_status = 'counting'
                    AND event.reason = 'stocktake_initial_round_started'
                    AND event.idempotency_key =
                        'stocktake' || ':' || 'start' || ':' ||
                        NEW.idempotency_key_hash || ':' || 'counting'
                    AND (SELECT count(*) FROM json_each(event.metadata_jsonb)) = 8
                    AND (SELECT count(*) FROM json_each(
                        event.metadata_jsonb, '$.result')) = 9
                    AND json_extract(event.metadata_jsonb, '$.result.active_freeze_count') = NEW.active_freeze_count
                    AND json_extract(event.metadata_jsonb, '$.result.cutoff_ledger_cursor') = NEW.cutoff_ledger_cursor
                    AND replace(json_extract(event.metadata_jsonb, '$.result.initial_round_id'), '-', '') = replace(CAST(NEW.initial_round_id AS TEXT), '-', '')
                    AND json_extract(event.metadata_jsonb, '$.result.scope_count') = NEW.scope_count
                    AND json_extract(event.metadata_jsonb, '$.result.snapshot_line_count') = NEW.snapshot_line_count
                    AND json_extract(event.metadata_jsonb, '$.result.status') = 'counting'
                    AND replace(json_extract(event.metadata_jsonb, '$.result.task_id'), '-', '') = replace(CAST(NEW.task_id AS TEXT), '-', '')
                    AND json_extract(event.metadata_jsonb, '$.result.task_type') = task.task_type
                    AND json_extract(event.metadata_jsonb, '$.result.version') = NEW.started_task_version)
               )
           ) = 3
       AND (SELECT count(*) FROM audit_events AS audit
             WHERE audit.aggregate_type = 'stocktake_task'
               AND lower(replace(audit.aggregate_id, '-', '')) = lower(replace(CAST(NEW.task_id AS TEXT), '-', ''))
               AND audit.action = 'stocktake.task.started') = 1
       AND (SELECT count(*) FROM audit_events AS audit
             WHERE audit.stream_key = 'inventory'
               AND audit.aggregate_type = 'stocktake_task'
               AND lower(replace(audit.aggregate_id, '-', '')) = lower(replace(CAST(NEW.task_id AS TEXT), '-', ''))
               AND audit.action = 'stocktake.task.started'
               AND audit.actor_user_id = NEW.started_by_user_id
               AND audit.occurred_at = NEW.started_at) = 1
       AND EXISTS (
           SELECT 1
             FROM users AS actor
             JOIN people AS person ON person.id = actor.person_id
             JOIN role_assignments AS assignment
               ON {assignment_matches} AND assignment.user_id = actor.id
             JOIN roles AS actor_role ON actor_role.id = assignment.role_id
            WHERE actor.id = NEW.started_by_user_id
              AND {actor_person_matches}
              AND actor.account_status = 'active'
              AND actor.is_active = 1
              AND actor.authorization_version = NEW.authorization_version
              AND person.employment_status = 'active'
              AND actor_role.status = 'active'
              AND actor_role.is_external = 0
              AND actor_role.code = NEW.role_code
              AND assignment.scope_type = NEW.scope_type
              AND assignment.scope_id = NEW.scope_id_snapshot
              AND assignment.status IN ('scheduled', 'active')
              AND assignment.revoked_at IS NULL
              AND assignment.valid_from <= NEW.started_at
              AND (assignment.valid_to IS NULL OR assignment.valid_to > NEW.started_at)
              AND ((task.task_type = 'personal'
                    AND task.created_by_user_id = actor.id
                    AND actor_role.code = 'technician'
                    AND assignment.scope_type = 'person'
                    AND replace(assignment.scope_id, '-', '') = replace(CAST(actor.person_id AS TEXT), '-', ''))
                   OR (task.task_type <> 'personal'
                       AND ((actor_role.code = 'admin'
                             AND assignment.scope_type = 'national'
                             AND assignment.scope_id = '*')
                            OR (actor_role.code = 'provincial_manager'
                                AND assignment.scope_type = 'organization'
                                AND replace(assignment.scope_id, '-', '') = replace(CAST(task.region_org_id AS TEXT), '-', '')))))
       )
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    # SQLite has no SHA-256 core function.  A database-owned UUID-derived seal
    # still proves that the application did not supply the committed value;
    # the INSERT validator above is the test-dialect graph boundary.
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_SEAL_TRIGGER}
AFTER INSERT ON {COMPLETION_TABLE}
BEGIN
    UPDATE {COMPLETION_TABLE}
       SET graph_manifest_sha256 =
           lower(replace(CAST(NEW.id AS TEXT), '-', '') ||
                 replace(CAST(NEW.id AS TEXT), '-', ''))
     WHERE id = NEW.id;
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_GUARD_UPDATE_TRIGGER}
BEFORE UPDATE ON {COMPLETION_TABLE}
WHEN NOT (
    OLD.graph_manifest_sha256 IS NULL
    AND NEW.graph_manifest_sha256 =
        lower(replace(CAST(NEW.id AS TEXT), '-', '') ||
              replace(CAST(NEW.id AS TEXT), '-', ''))
    AND NEW.id IS OLD.id
    AND NEW.task_id IS OLD.task_id
    AND NEW.initial_round_id IS OLD.initial_round_id
    AND NEW.expected_task_version IS OLD.expected_task_version
    AND NEW.started_task_version IS OLD.started_task_version
    AND NEW.cutoff_ledger_cursor IS OLD.cutoff_ledger_cursor
    AND NEW.cutoff_at IS OLD.cutoff_at
    AND NEW.scope_count IS OLD.scope_count
    AND NEW.snapshot_line_count IS OLD.snapshot_line_count
    AND NEW.active_freeze_count IS OLD.active_freeze_count
    AND NEW.scope_manifest_sha256 IS OLD.scope_manifest_sha256
    AND NEW.snapshot_manifest_sha256 IS OLD.snapshot_manifest_sha256
    AND NEW.request_sha256 IS OLD.request_sha256
    AND NEW.idempotency_key_hash IS OLD.idempotency_key_hash
    AND NEW.started_by_user_id IS OLD.started_by_user_id
    AND NEW.started_by_person_id IS OLD.started_by_person_id
    AND NEW.started_role_assignment_id IS OLD.started_role_assignment_id
    AND NEW.authorization_version IS OLD.authorization_version
    AND NEW.role_code IS OLD.role_code
    AND NEW.scope_type IS OLD.scope_type
    AND NEW.scope_id_snapshot IS OLD.scope_id_snapshot
    AND NEW.authorization_sha256 IS OLD.authorization_sha256
    AND NEW.started_at IS OLD.started_at
    AND NEW.created_at IS OLD.created_at
)
BEGIN
    SELECT RAISE(ABORT, '{IMMUTABLE_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_GUARD_DELETE_TRIGGER}
BEFORE DELETE ON {COMPLETION_TABLE}
BEGIN
    SELECT RAISE(ABORT, '{IMMUTABLE_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_TASK_SEAL_TRIGGER}
BEFORE UPDATE OF cutoff_ledger_cursor, cutoff_at, snapshot_manifest_sha256,
                 issued_at, frozen_at ON stocktake_tasks
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'OLD.id')}
)
AND (
    NEW.cutoff_ledger_cursor IS NOT OLD.cutoff_ledger_cursor
    OR NEW.cutoff_at IS NOT OLD.cutoff_at
    OR NEW.snapshot_manifest_sha256 IS NOT OLD.snapshot_manifest_sha256
    OR NEW.issued_at IS NOT OLD.issued_at
    OR NEW.frozen_at IS NOT OLD.frozen_at
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[0]}
BEFORE UPDATE OF task_no, task_type, region_org_id, blind_count,
                 scope_manifest_sha256, control_source_system_id,
                 control_sync_run_id, control_snapshot_at,
                 control_manifest_sha256, created_by_user_id, deadline,
                 note, created_at
ON stocktake_tasks
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'OLD.id')}
)
AND (
    NEW.task_no IS NOT OLD.task_no OR NEW.task_type IS NOT OLD.task_type
    OR NEW.region_org_id IS NOT OLD.region_org_id
    OR NEW.blind_count IS NOT OLD.blind_count
    OR NEW.scope_manifest_sha256 IS NOT OLD.scope_manifest_sha256
    OR NEW.control_source_system_id IS NOT OLD.control_source_system_id
    OR NEW.control_sync_run_id IS NOT OLD.control_sync_run_id
    OR NEW.control_snapshot_at IS NOT OLD.control_snapshot_at
    OR NEW.control_manifest_sha256 IS NOT OLD.control_manifest_sha256
    OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
    OR NEW.deadline IS NOT OLD.deadline OR NEW.note IS NOT OLD.note
    OR NEW.created_at IS NOT OLD.created_at
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    for trigger_name, operation, task_expression in (
        (SQLITE_ADDITIONAL_SEAL_TRIGGERS[1], "INSERT", "NEW.task_id"),
        (SQLITE_ADDITIONAL_SEAL_TRIGGERS[2], "UPDATE", "OLD.task_id"),
        (SQLITE_ADDITIONAL_SEAL_TRIGGERS[3], "DELETE", "OLD.task_id"),
    ):
        op.execute(
            f"""
CREATE TRIGGER {trigger_name}
BEFORE {operation} ON stocktake_scopes
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', task_expression)}
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
        )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[4]}
BEFORE INSERT ON inventory_freezes
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'NEW.task_id')}
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[5]}
BEFORE UPDATE OF id, task_id, stocktake_scope_id, scope_key, freeze_mode,
                 valid_from, created_by_user_id, created_at
ON inventory_freezes
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'OLD.task_id')}
)
AND (
    NEW.id IS NOT OLD.id OR NEW.task_id IS NOT OLD.task_id
    OR NEW.stocktake_scope_id IS NOT OLD.stocktake_scope_id
    OR NEW.scope_key IS NOT OLD.scope_key
    OR NEW.freeze_mode IS NOT OLD.freeze_mode
    OR NEW.valid_from IS NOT OLD.valid_from
    OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
    OR NEW.created_at IS NOT OLD.created_at
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[6]}
BEFORE DELETE ON inventory_freezes
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'OLD.task_id')}
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[7]}
BEFORE INSERT ON stocktake_snapshot_lines
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'NEW.task_id')}
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[8]}
BEFORE INSERT ON stocktake_rounds
WHEN NEW.round_no = 1 AND EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'NEW.task_id')}
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[9]}
BEFORE DELETE ON stocktake_rounds
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.initial_round_id', 'OLD.id')}
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[10]}
BEFORE INSERT ON state_transition_events
WHEN NEW.aggregate_type = 'stocktake_task'
AND EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'NEW.aggregate_id')}
)
AND (
    NEW.reason IN ('stocktake_task_issued', 'stocktake_task_frozen',
                   'stocktake_initial_round_started')
    OR NEW.idempotency_key LIKE
       ('stocktake' || ':' || 'start' || ':' || '%')
    OR (json_extract(NEW.metadata_jsonb, '$.schema') = '{COMMAND_SCHEMA}'
        AND json_extract(NEW.metadata_jsonb, '$.operation') = 'start')
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ADDITIONAL_SEAL_TRIGGERS[11]}
BEFORE INSERT ON audit_events
WHEN NEW.aggregate_type = 'stocktake_task'
AND NEW.action = 'stocktake.task.started'
AND EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'NEW.aggregate_id')}
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )
    for trigger_name, operation in (
        (SQLITE_SNAPSHOT_UPDATE_TRIGGER, "UPDATE"),
        (SQLITE_SNAPSHOT_DELETE_TRIGGER, "DELETE"),
    ):
        op.execute(
            f"""
CREATE TRIGGER {trigger_name}
BEFORE {operation} ON stocktake_snapshot_lines
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.task_id', 'OLD.task_id')}
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
        )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_ROUND_SEAL_TRIGGER}
BEFORE UPDATE OF id, task_id, round_no, round_type, started_at,
                 idempotency_key_hash, recount_case_id, created_at
ON stocktake_rounds
WHEN EXISTS (
    SELECT 1 FROM {COMPLETION_TABLE} AS completion
     WHERE {_sqlite_uuid_equal('completion.initial_round_id', 'OLD.id')}
)
AND (
    NEW.id IS NOT OLD.id OR NEW.task_id IS NOT OLD.task_id
    OR NEW.round_no IS NOT OLD.round_no OR NEW.round_type IS NOT OLD.round_type
    OR NEW.started_at IS NOT OLD.started_at
    OR NEW.idempotency_key_hash IS NOT OLD.idempotency_key_hash
    OR NEW.recount_case_id IS NOT OLD.recount_case_id
    OR NEW.created_at IS NOT OLD.created_at
)
BEGIN
    SELECT RAISE(ABORT, '{GRAPH_ERROR}');
END
"""
    )


def _drop_sqlite_triggers() -> None:
    for trigger_name in (
        *reversed(SQLITE_ADDITIONAL_SEAL_TRIGGERS),
        SQLITE_ROUND_SEAL_TRIGGER,
        SQLITE_SNAPSHOT_DELETE_TRIGGER,
        SQLITE_SNAPSHOT_UPDATE_TRIGGER,
        SQLITE_TASK_SEAL_TRIGGER,
        SQLITE_GUARD_DELETE_TRIGGER,
        SQLITE_GUARD_UPDATE_TRIGGER,
        SQLITE_SEAL_TRIGGER,
        SQLITE_VALIDATE_TRIGGER,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _replace_oam_runtime_ready_function(expected_revision: str) -> None:
    op.execute(_oam_runtime_ready_function_sql(expected_revision))


def _oam_runtime_ready_function_sql(expected_revision: str) -> str:
    if expected_revision not in {PREVIOUS_SCHEMA_REVISION, revision}:
        raise ValueError("unsupported OAM runtime readiness revision")
    return f"""
CREATE OR REPLACE FUNCTION public.{OAM_RUNTIME_READY_FUNCTION}()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT (
        SELECT pg_catalog.count(*) = 1
           AND pg_catalog.min(version_num) = '{expected_revision}'
          FROM public.alembic_version
    ) AND CASE session_user::text
        WHEN 'edge_inbox' THEN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS ingress
             WHERE ingress.enabled
               AND ingress.principal_name = session_user::text
               AND ingress.capability = 'edge_ingress'
               AND (
                   ingress.entity_type <> 'work_order'
                   OR EXISTS (
                       SELECT 1
                         FROM public.source_systems AS source
                        WHERE source.code = ingress.source_system
                          AND source.mode = 'read_only'
                          AND source.enabled
                          AND source.configuration_jsonb =
                              pg_catalog.jsonb_build_object(
                                  'projection_schema',
                                  'rsc.oam_work_order_projection.v1',
                                  'edge_source_instance', ingress.source_instance,
                                  'work_order_company_id', ingress.company_id,
                                  'work_order_org_code', ingress.org_code,
                                  'work_order_scope_key', ingress.scope_key
                              )
                   )
               )
        )
        WHEN 'star_oam_projector' THEN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS write_work_order
              JOIN public.oam_sync_scope_bindings AS read_work_order
                ON read_work_order.source_system = write_work_order.source_system
               AND read_work_order.source_instance = write_work_order.source_instance
               AND read_work_order.scope_key = write_work_order.scope_key
               AND read_work_order.company_id = write_work_order.company_id
               AND read_work_order.org_code = write_work_order.org_code
               AND read_work_order.enabled
               AND read_work_order.principal_name = write_work_order.principal_name
               AND read_work_order.capability = 'projector_read'
               AND read_work_order.entity_type = 'work_order'
              JOIN public.oam_sync_scope_bindings AS read_employee
                ON read_employee.source_system = write_work_order.source_system
               AND read_employee.source_instance = write_work_order.source_instance
               AND read_employee.scope_key = write_work_order.scope_key
               AND read_employee.company_id = write_work_order.company_id
               AND read_employee.org_code = write_work_order.org_code
               AND read_employee.enabled
               AND read_employee.principal_name = write_work_order.principal_name
               AND read_employee.capability = 'projector_read'
               AND read_employee.entity_type = 'employee'
              JOIN public.source_systems AS source
                ON source.code = write_work_order.source_system
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', write_work_order.source_instance,
                   'work_order_company_id', write_work_order.company_id,
                   'work_order_org_code', write_work_order.org_code,
                   'work_order_scope_key', write_work_order.scope_key
               )
             WHERE write_work_order.enabled
               AND write_work_order.principal_name = session_user::text
               AND write_work_order.capability = 'projector_write'
               AND write_work_order.entity_type = 'work_order'
        ) AND (
            SELECT pg_catalog.count(*) = 3
              FROM public.oam_sync_scope_bindings AS binding
             WHERE binding.enabled
               AND binding.principal_name = session_user::text
        )
        ELSE false
    END
$$
"""
