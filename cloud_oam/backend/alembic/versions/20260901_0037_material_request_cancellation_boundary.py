"""Seal material-request runtime ACL and direct-cancellation causality.

Revision ID: 20260901_0037
Revises: 20260901_0036
Create Date: 2026-09-01

The direct-cancel path is deliberately narrower than a compensation flow.  A
requester may cancel only while every downstream state axis is neutral and no
active substitution or supply task exists.  The accepted command/action and
one immutable fact for every positive approved line are the source of truth;
the request and line columns remain projections.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260901_0037"
down_revision: Union[str, Sequence[str], None] = "20260901_0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
FACT_TABLE = "material_request_cancellation_line_facts"
ACTION_IDENTITY_INDEX = "uq_approval_actions_cancel_fact_identity_0037"
PG_VALIDATE_FUNCTION = "rsc_validate_material_request_cancellation_0037"
PG_DISPATCH_FUNCTION = "rsc_require_material_request_cancellation_graph_0037"
PG_FACT_GUARD_FUNCTION = "rsc_guard_material_request_cancellation_fact_0037"
PG_ACTION_GUARD_FUNCTION = "rsc_guard_material_request_cancel_action_0037"
PG_PARENT_LOCK_FUNCTION = "rsc_lock_material_request_parent_write_0037"
GUARD_ERROR = "material request cancellation graph is invalid"
UPGRADE_BLOCKER = (
    "0037 preflight failed: legacy cancellation state requires quarantine"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0037 after a material request cancellation was recorded"
)

READ_TABLES = (
    "approval_actions",
    "approval_external_registration_lines",
    "approval_external_registrations",
    "approval_instances",
    "approval_return_line_facts",
    "approval_route_step_defs",
    "approval_route_versions",
    "approval_step_candidates",
    "approval_step_line_decisions",
    "approval_steps",
    FACT_TABLE,
    "material_request_commands",
    "material_request_files",
    "material_request_lines",
    "material_request_revisions",
    "material_requests",
    "material_substitutions",
    "notification_events",
    "oam_work_orders",
    "substitution_decisions",
    "supply_tasks",
)
INSERT_TABLES = (
    "approval_actions",
    "approval_external_registration_lines",
    "approval_external_registrations",
    "approval_instances",
    "approval_return_line_facts",
    "approval_step_candidates",
    "approval_step_line_decisions",
    "approval_steps",
    FACT_TABLE,
    "material_request_commands",
    "material_request_files",
    "material_request_lines",
    "material_request_revisions",
    "material_requests",
)
DELETE_TABLES = ("material_request_files", "material_request_lines")
UPDATE_COLUMNS = {
    "approval_external_registrations": (
        "status",
        "verified_by_user_id",
        "verified_by_person_id",
        "verified_role_assignment_id",
        "verified_authorization_version",
        "verified_at",
        "verification_comment",
        "version",
        "updated_at",
    ),
    "approval_instances": (
        "status",
        "current_step_no",
        "current_step_id",
        "completed_at",
        "version",
        "updated_at",
    ),
    "approval_steps": (
        "status",
        "opened_at",
        "decided_at",
        "decision_manifest_sha256",
        "version",
        "updated_at",
    ),
    "material_request_lines": (
        "status",
        "final_approved_qty",
        "cancelled_qty",
        "version",
        "updated_at",
    ),
    "material_request_revisions": (
        "work_order_id",
        "purpose",
        "urgency",
        "expected_date",
        "address_snapshot_jsonb",
        "address_masked_jsonb",
        "contact_snapshot_jsonb",
        "contact_masked_jsonb",
        "note",
        "status",
        "content_manifest_sha256",
        "sealed_at",
        "sealed_by_user_id",
        "updated_at",
    ),
    "material_requests": (
        "work_order_id",
        "purpose",
        "urgency",
        "expected_date",
        "address_snapshot_jsonb",
        "address_masked_jsonb",
        "contact_snapshot_jsonb",
        "contact_masked_jsonb",
        "note",
        "revision_no",
        "status",
        "submitted_at",
        "decided_at",
        "withdrawn_at",
        "cancelled_at",
        "version",
        "updated_at",
    ),
}


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0037 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0037 SQLite upgrade requires an online connection")
        _postgresql_lock_and_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_graph()
        _online_preflight(dialect)

    op.create_index(
        ACTION_IDENTITY_INDEX,
        "approval_actions",
        ["id", "instance_id", "command_id"],
        unique=True,
    )
    _create_fact_table()

    if dialect == "postgresql":
        _create_postgresql_functions()
        _create_postgresql_triggers()
        _apply_postgresql_acl()
    else:
        _create_sqlite_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0037 downgrade requires an online cancellation check")
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_graph()
    _require_safe_downgrade(dialect)

    if dialect == "postgresql":
        _drop_postgresql_triggers()
        for function_name, argument_types in (
            (PG_DISPATCH_FUNCTION, ""),
            (PG_FACT_GUARD_FUNCTION, ""),
            (PG_ACTION_GUARD_FUNCTION, ""),
            (PG_PARENT_LOCK_FUNCTION, ""),
            (PG_VALIDATE_FUNCTION, "uuid"),
        ):
            op.execute(
                f"DROP FUNCTION IF EXISTS public.{function_name}({argument_types})"
            )
        _restore_postgresql_acl()
    else:
        for trigger_name in _sqlite_trigger_names():
            op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    op.drop_index(
        "ix_material_request_cancel_facts_command_0037",
        table_name=FACT_TABLE,
    )
    op.drop_index(
        "ix_material_request_cancel_facts_request_0037",
        table_name=FACT_TABLE,
    )
    op.drop_index(
        "ix_material_request_cancel_facts_instance_0037",
        table_name=FACT_TABLE,
    )
    op.drop_table(FACT_TABLE)
    op.drop_index(ACTION_IDENTITY_INDEX, table_name="approval_actions")


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_graph() -> None:
    bind = op.get_bind()
    for table_name in _postgresql_lock_tables():
        bind.exec_driver_sql(
            f"LOCK TABLE public.{table_name} IN ACCESS EXCLUSIVE MODE"
        )


def _postgresql_lock_and_preflight() -> None:
    for table_name in _postgresql_lock_tables():
        op.execute(f"LOCK TABLE public.{table_name} IN ACCESS EXCLUSIVE MODE")
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


def _postgresql_lock_tables() -> tuple[str, ...]:
    return (
        "material_requests",
        "material_request_revisions",
        "material_request_lines",
        "material_request_commands",
        "approval_instances",
        "approval_actions",
        "substitution_decisions",
        "supply_tasks",
        "inventory_transactions",
        "notification_events",
        "outbox_events",
        "state_transition_events",
        "audit_events",
    )


def _upgrade_blocker_sql(prefix: str) -> str:
    return f"""
EXISTS (
    SELECT 1 FROM {prefix}material_requests
     WHERE status = 'cancelled' OR cancelled_at IS NOT NULL
)
OR EXISTS (
    SELECT 1 FROM {prefix}material_request_lines
     WHERE status = 'cancelled' OR cancelled_qty <> 0
)
OR EXISTS (
    SELECT 1 FROM {prefix}material_request_commands WHERE operation = 'cancel'
)
OR EXISTS (
    SELECT 1 FROM {prefix}approval_actions WHERE action = 'cancel'
)
OR EXISTS (
    SELECT 1 FROM {prefix}state_transition_events
     WHERE aggregate_type = 'material_request' AND to_status = 'cancelled'
)
OR EXISTS (
    SELECT 1 FROM {prefix}audit_events
     WHERE aggregate_type = 'material_request'
       AND action = 'material_request.cancel'
)
"""


def _online_preflight(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {_upgrade_blocker_sql(prefix)}"
    ).first():
        raise RuntimeError(UPGRADE_BLOCKER)


def _require_safe_downgrade(dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE EXISTS (SELECT 1 FROM {prefix}{FACT_TABLE}) "
        f"OR {_upgrade_blocker_sql(prefix)}"
    ).first():
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _create_fact_table() -> None:
    op.create_table(
        FACT_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cancel_action_id", sa.Uuid(), nullable=False),
        sa.Column("cancel_command_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_revision_id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column(
            "final_approved_qty_before", sa.Numeric(18, 3), nullable=False
        ),
        sa.Column("cancelled_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "final_approved_qty_before > 0 "
            "AND cancelled_qty = final_approved_qty_before",
            name="ck_material_request_cancel_facts_quantities_0037",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0 AND length(reason) <= 4000",
            name="ck_material_request_cancel_facts_reason_0037",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_material_request_cancel_facts_authorization_0037",
        ),
        sa.CheckConstraint(
            "occurred_at = created_at",
            name="ck_material_request_cancel_facts_time_0037",
        ),
        sa.ForeignKeyConstraint(
            ["cancel_action_id", "instance_id", "cancel_command_id"],
            [
                "approval_actions.id",
                "approval_actions.instance_id",
                "approval_actions.command_id",
            ],
            name="fk_material_request_cancel_facts_action_0037",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_line_id", "request_id", "request_revision_id"],
            [
                "material_request_lines.id",
                "material_request_lines.request_id",
                "material_request_lines.revision_id",
            ],
            name="fk_material_request_cancel_facts_request_line_0037",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cancel_action_id",
            "request_line_id",
            name="uq_material_request_cancel_facts_action_line_0037",
        ),
        sa.UniqueConstraint(
            "cancel_command_id",
            "request_line_id",
            name="uq_material_request_cancel_facts_command_line_0037",
        ),
        sa.UniqueConstraint(
            "request_line_id",
            name="uq_material_request_cancel_facts_request_line_0037",
        ),
    )
    op.create_index(
        "ix_material_request_cancel_facts_instance_0037",
        FACT_TABLE,
        ["instance_id", "occurred_at"],
    )
    op.create_index(
        "ix_material_request_cancel_facts_request_0037",
        FACT_TABLE,
        ["request_id"],
    )
    op.create_index(
        "ix_material_request_cancel_facts_command_0037",
        FACT_TABLE,
        ["cancel_command_id"],
    )


def _postgresql_request_coordinate_match_sql(
    value_sql: str, request_id_sql: str
) -> str:
    """Match exact business numbers and normalized UUID graph coordinates."""

    return f"""EXISTS (
    SELECT 1
      FROM (
            SELECT request.request_no AS coordinate,
                   false AS is_uuid_coordinate
              FROM public.material_requests AS request
             WHERE request.id = {request_id_sql}
            UNION ALL
            SELECT request.id::text, true
              FROM public.material_requests AS request
             WHERE request.id = {request_id_sql}
            UNION ALL
            SELECT revision.id::text, true
              FROM public.material_request_revisions AS revision
             WHERE revision.request_id = {request_id_sql}
            UNION ALL
            SELECT line.id::text, true
              FROM public.material_request_lines AS line
             WHERE line.request_id = {request_id_sql}
            UNION ALL
            SELECT decision.id::text, true
              FROM public.substitution_decisions AS decision
              JOIN public.material_request_lines AS line
                ON line.id = decision.request_line_id
             WHERE line.request_id = {request_id_sql}
            UNION ALL
            SELECT task.id::text, true
              FROM public.supply_tasks AS task
              JOIN public.material_request_lines AS line
                ON line.id = task.request_line_id
             WHERE line.request_id = {request_id_sql}
      ) AS candidate
     WHERE (
            candidate.is_uuid_coordinate
            AND replace(lower(candidate.coordinate), '-', '') =
                replace(lower({value_sql}), '-', '')
           )
        OR (
            NOT candidate.is_uuid_coordinate
            AND candidate.coordinate = {value_sql}
           )
)"""


def _postgresql_validator_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_VALIDATE_FUNCTION}(p_request_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    request_row public.material_requests%ROWTYPE;
BEGIN
    SELECT * INTO request_row
      FROM public.material_requests
     WHERE id = p_request_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF request_row.status <> 'cancelled' THEN
        IF request_row.cancelled_at IS NOT NULL
           OR EXISTS (
                SELECT 1 FROM public.material_request_commands
                 WHERE request_id = p_request_id AND operation = 'cancel'
           )
           OR EXISTS (
                SELECT 1
                  FROM public.approval_actions AS action
                  JOIN public.material_request_commands AS command
                    ON command.id = action.command_id
                 WHERE command.request_id = p_request_id
                   AND action.action = 'cancel'
           )
           OR EXISTS (
                SELECT 1 FROM public.{FACT_TABLE}
                 WHERE request_id = p_request_id
           )
           OR EXISTS (
                SELECT 1 FROM public.material_request_lines
                 WHERE request_id = p_request_id
                   AND (status = 'cancelled' OR cancelled_qty <> 0)
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN;
    END IF;

    IF request_row.cancelled_at IS NULL
       OR request_row.allocation_status <> 'not_allocated'
       OR request_row.reservation_status <> 'not_reserved'
       OR request_row.outbound_status <> 'not_started'
       OR request_row.shipment_status <> 'not_started'
       OR request_row.logistics_signature_status <> 'not_signed'
       OR request_row.oam_receipt_status <> 'not_occurred'
       OR request_row.personal_inbound_status <> 'not_started'
       OR request_row.notification_status <> 'not_started'
       OR request_row.reconciliation_status <> 'not_started'
       OR (SELECT count(*) FROM public.material_request_commands
            WHERE request_id = p_request_id AND operation = 'cancel') <> 1
       OR (SELECT count(*)
             FROM public.approval_actions AS action
             JOIN public.material_request_commands AS command
               ON command.id = action.command_id
            WHERE command.request_id = p_request_id
              AND action.action = 'cancel') <> 1
       OR NOT EXISTS (
            SELECT 1
              FROM public.material_request_revisions AS revision
              JOIN public.material_request_commands AS command
                ON command.request_id = request_row.id
               AND command.operation = 'cancel'
              JOIN public.approval_actions AS action
                ON action.command_id = command.id
               AND action.action = 'cancel'
              JOIN public.approval_instances AS instance
                ON instance.id = action.instance_id
               AND instance.request_id = request_row.id
              JOIN public.users AS actor_user
                ON actor_user.id = command.actor_user_id
              JOIN public.people AS actor_person
                ON actor_person.id = command.actor_person_id
              JOIN public.role_assignments AS assignment
                ON assignment.id = command.actor_role_assignment_id
              JOIN public.roles AS role ON role.id = assignment.role_id
             WHERE revision.request_id = request_row.id
               AND revision.revision_no = request_row.revision_no
               AND command.target_version = request_row.version
               AND command.actor_user_id = request_row.requester_user_id
               AND command.actor_person_id = request_row.requester_person_id
               AND request_row.created_by_user_id = command.actor_user_id
               AND command.occurred_at = command.created_at
               AND command.occurred_at = request_row.cancelled_at
               AND action.step_id IS NULL
               AND action.source_mode = 'internal'
               AND btrim(action.comment) <> ''
               AND length(action.comment) <= 4000
               AND action.actor_user_id = command.actor_user_id
               AND action.actor_person_id = command.actor_person_id
               AND action.actor_role_assignment_id = command.actor_role_assignment_id
               AND action.authorization_version = command.authorization_version
               AND action.occurred_at = command.occurred_at
               AND action.created_at = command.created_at
               AND instance.status IN ('returned', 'completed')
               AND instance.current_step_no IS NULL
               AND instance.current_step_id IS NULL
               AND instance.completed_at IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1 FROM public.approval_instances AS newer
                     WHERE newer.request_id = instance.request_id
                       AND newer.attempt_no > instance.attempt_no
               )
               AND actor_user.person_id = actor_person.id
               AND actor_user.account_status = 'active'
               AND actor_person.employment_status = 'active'
               AND actor_user.authorization_version = command.authorization_version
               AND assignment.user_id = actor_user.id
               AND assignment.scope_type = 'person'
               AND assignment.scope_id = actor_person.id::text
               AND assignment.status = 'active'
               AND assignment.valid_from <= command.occurred_at
               AND (assignment.valid_to IS NULL OR assignment.valid_to > command.occurred_at)
               AND role.code = 'technician'
               AND jsonb_typeof(command.request_jsonb) = 'object'
               AND (SELECT count(*) FROM jsonb_object_keys(command.request_jsonb)) = 10
               AND command.request_jsonb ?& ARRAY[
                    'schema','operation','request_id','revision_id','revision_no',
                    'target_version','payload_sha256','sensitive_fields',
                    'cancellation_fact_count','cancellation_fact_manifest_sha256'
               ]
               AND command.request_jsonb->>'schema' =
                   'rsc.material_request_lifecycle_command.v1'
               AND command.request_jsonb->>'operation' = 'cancel'
               AND command.request_jsonb->>'request_id' = request_row.id::text
               AND command.request_jsonb->>'revision_id' = revision.id::text
               AND command.request_jsonb->>'revision_no' = revision.revision_no::text
               AND command.request_jsonb->>'target_version' = request_row.version::text
               AND command.request_jsonb->>'payload_sha256' = command.request_hash
               AND command.request_jsonb->>'sensitive_fields' = 'excluded'
               AND jsonb_typeof(command.request_jsonb->'revision_no') = 'number'
               AND jsonb_typeof(command.request_jsonb->'target_version') = 'number'
               AND jsonb_typeof(command.request_jsonb->'cancellation_fact_count') = 'number'
               AND (command.request_jsonb->>'cancellation_fact_manifest_sha256')
                   ~ '^[0-9a-f]{{64}}$'
               AND (command.request_jsonb->>'cancellation_fact_count')::bigint =
                   (SELECT count(*) FROM public.{FACT_TABLE} AS fact
                     WHERE fact.cancel_command_id = command.id)
       )
       OR EXISTS (
            SELECT 1 FROM public.material_request_lines AS line
             JOIN public.material_request_revisions AS revision
               ON revision.id = line.revision_id
              AND revision.request_id = line.request_id
            WHERE line.request_id = p_request_id
              AND revision.revision_no = request_row.revision_no
              AND (line.status <> 'cancelled'
                   OR line.cancelled_qty <> line.final_approved_qty)
       )
       OR EXISTS (
            SELECT 1
              FROM public.material_request_lines AS line
              JOIN public.material_request_revisions AS revision
                ON revision.id = line.revision_id
               AND revision.request_id = line.request_id
             WHERE line.request_id = p_request_id
               AND revision.revision_no = request_row.revision_no
               AND line.final_approved_qty > 0
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.{FACT_TABLE} AS fact
                      JOIN public.approval_actions AS action
                        ON action.id = fact.cancel_action_id
                       AND action.instance_id = fact.instance_id
                       AND action.command_id = fact.cancel_command_id
                      JOIN public.material_request_commands AS command
                        ON command.id = fact.cancel_command_id
                     WHERE fact.request_line_id = line.id
                       AND fact.request_id = request_row.id
                       AND fact.request_revision_id = revision.id
                       AND command.request_id = request_row.id
                       AND command.operation = 'cancel'
                       AND action.action = 'cancel'
                       AND action.step_id IS NULL
                       AND fact.final_approved_qty_before = line.final_approved_qty
                       AND fact.cancelled_qty = line.final_approved_qty
                       AND fact.actor_user_id = command.actor_user_id
                       AND fact.actor_person_id = command.actor_person_id
                       AND fact.actor_role_assignment_id = command.actor_role_assignment_id
                       AND fact.authorization_version = command.authorization_version
                       AND fact.occurred_at = command.occurred_at
                       AND fact.created_at = command.created_at
               )
       )
       OR EXISTS (
            SELECT 1 FROM public.{FACT_TABLE} AS fact
             JOIN public.material_request_lines AS line
               ON line.id = fact.request_line_id
             JOIN public.material_request_revisions AS revision
               ON revision.id = line.revision_id
            WHERE fact.request_id = p_request_id
              AND (line.request_id <> p_request_id
                   OR fact.request_revision_id <> line.revision_id
                   OR revision.revision_no <> request_row.revision_no
                   OR line.final_approved_qty <= 0)
       )
       OR EXISTS (
            SELECT 1
              FROM public.substitution_decisions AS decision
              JOIN public.material_request_lines AS line
                ON line.id = decision.request_line_id
             WHERE line.request_id = p_request_id
               AND decision.status IN ('proposed', 'confirmed')
       )
       OR EXISTS (
            SELECT 1
              FROM public.supply_tasks AS task
              JOIN public.material_request_lines AS line
                ON line.id = task.request_line_id
             WHERE line.request_id = p_request_id
               AND task.status IN ('open', 'reference_registered', 'awaiting_supply')
       )
       OR EXISTS (
            SELECT 1 FROM public.inventory_transactions AS fact
             WHERE fact.source_document_type = 'material_request'
               AND {_postgresql_request_coordinate_match_sql(
                    'fact.source_document_id', 'p_request_id'
               )}
       )
       OR EXISTS (
            SELECT 1 FROM public.notification_events AS fact
             WHERE fact.business_type = 'material_request'
               AND {_postgresql_request_coordinate_match_sql(
                    'fact.business_id', 'p_request_id'
               )}
       )
       OR EXISTS (
            SELECT 1 FROM public.outbox_events AS fact
             WHERE fact.aggregate_type = 'material_request'
               AND {_postgresql_request_coordinate_match_sql(
                    'fact.aggregate_id', 'p_request_id'
               )}
       )
       OR (SELECT count(*) FROM public.state_transition_events AS event
            WHERE event.aggregate_type = 'material_request'
              AND event.aggregate_id = request_row.id::text
              AND event.to_status = 'cancelled'
              AND event.from_status IN ('returned','approved','partially_approved','cancellation_pending')
              AND event.reason = 'material_request_safely_cancelled_by_requester'
              AND event.actor_id = request_row.requester_user_id
              AND event.occurred_at = request_row.cancelled_at) <> 1
       OR (SELECT count(*) FROM public.audit_events AS event
            WHERE event.stream_key = 'material_request'
              AND event.action = 'material_request.cancel'
              AND event.aggregate_type = 'material_request'
              AND event.aggregate_id = request_row.id::text
              AND event.actor_user_id = request_row.requester_user_id
              AND event.occurred_at = request_row.cancelled_at) <> 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
END
$$
"""


def _postgresql_dispatch_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_DISPATCH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    target_request_id uuid;
    target_coordinate text;
    coordinate_match_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'material_requests' THEN
        target_request_id := NEW.id;
    ELSIF TG_TABLE_NAME IN ('material_request_lines', 'material_request_commands',
                            '{FACT_TABLE}', 'approval_instances') THEN
        target_request_id := NEW.request_id;
    ELSIF TG_TABLE_NAME = 'approval_actions' THEN
        SELECT request_id INTO target_request_id
          FROM public.material_request_commands WHERE id = NEW.command_id;
    ELSIF TG_TABLE_NAME IN ('substitution_decisions', 'supply_tasks') THEN
        SELECT request_id INTO target_request_id
          FROM public.material_request_lines WHERE id = NEW.request_line_id;
    ELSIF TG_TABLE_NAME = 'inventory_transactions' THEN
        IF NEW.source_document_type <> 'material_request' THEN RETURN NEW; END IF;
        target_coordinate := NEW.source_document_id;
    ELSIF TG_TABLE_NAME = 'notification_events' THEN
        IF NEW.business_type <> 'material_request' THEN RETURN NEW; END IF;
        target_coordinate := NEW.business_id;
    ELSIF TG_TABLE_NAME = 'outbox_events' THEN
        IF NEW.aggregate_type <> 'material_request' THEN RETURN NEW; END IF;
        target_coordinate := NEW.aggregate_id;
    ELSIF TG_TABLE_NAME IN ('state_transition_events', 'audit_events')
          AND NEW.aggregate_type = 'material_request' THEN
        SELECT id INTO target_request_id
          FROM public.material_requests WHERE id::text = NEW.aggregate_id;
    END IF;
    IF target_coordinate IS NOT NULL THEN
        SELECT (array_agg(DISTINCT candidate.request_id))[1],
               count(DISTINCT candidate.request_id)
          INTO target_request_id, coordinate_match_count
          FROM (
                SELECT request.id AS request_id, request.id::text AS coordinate,
                       true AS is_uuid_coordinate
                  FROM public.material_requests AS request
                UNION ALL
                SELECT request.id, request.request_no, false
                  FROM public.material_requests AS request
                UNION ALL
                SELECT revision.request_id, revision.id::text, true
                  FROM public.material_request_revisions AS revision
                UNION ALL
                SELECT line.request_id, line.id::text, true
                  FROM public.material_request_lines AS line
                UNION ALL
                SELECT line.request_id, decision.id::text, true
                  FROM public.substitution_decisions AS decision
                  JOIN public.material_request_lines AS line
                    ON line.id = decision.request_line_id
                UNION ALL
                SELECT line.request_id, task.id::text, true
                  FROM public.supply_tasks AS task
                  JOIN public.material_request_lines AS line
                    ON line.id = task.request_line_id
          ) AS candidate
         WHERE (
                candidate.is_uuid_coordinate
                AND replace(lower(candidate.coordinate), '-', '') =
                    replace(lower(target_coordinate), '-', '')
               )
            OR (
                NOT candidate.is_uuid_coordinate
                AND candidate.coordinate = target_coordinate
               );
        IF coordinate_match_count > 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    IF target_request_id IS NOT NULL THEN
        PERFORM public.{PG_VALIDATE_FUNCTION}(target_request_id);
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_fact_guard_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_FACT_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    PERFORM 1 FROM public.material_requests
     WHERE id = NEW.request_id FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM public.material_requests AS request
          JOIN public.material_request_revisions AS revision
            ON revision.id = NEW.request_revision_id
           AND revision.request_id = request.id
           AND revision.revision_no = request.revision_no
          JOIN public.material_request_lines AS line
            ON line.id = NEW.request_line_id
           AND line.request_id = request.id
           AND line.revision_id = revision.id
          JOIN public.approval_actions AS action
            ON action.id = NEW.cancel_action_id
           AND action.instance_id = NEW.instance_id
           AND action.command_id = NEW.cancel_command_id
          JOIN public.material_request_commands AS command
            ON command.id = NEW.cancel_command_id
           AND command.request_id = request.id
          JOIN public.approval_instances AS instance
            ON instance.id = NEW.instance_id
           AND instance.request_id = request.id
         WHERE request.id = NEW.request_id
           AND request.status IN ('returned','approved','partially_approved','cancellation_pending')
           AND request.cancelled_at IS NULL
           AND command.operation = 'cancel'
           AND command.target_version = request.version + 1
           AND action.action = 'cancel'
           AND action.step_id IS NULL
           AND action.source_mode = 'internal'
           AND instance.status IN ('returned','completed')
           AND instance.current_step_id IS NULL
           AND instance.current_step_no IS NULL
           AND line.final_approved_qty > 0
           AND line.cancelled_qty = 0
           AND NEW.final_approved_qty_before = line.final_approved_qty
           AND NEW.cancelled_qty = line.final_approved_qty
           AND NEW.actor_user_id = command.actor_user_id
           AND NEW.actor_person_id = command.actor_person_id
           AND NEW.actor_role_assignment_id = command.actor_role_assignment_id
           AND NEW.authorization_version = command.authorization_version
           AND NEW.occurred_at = command.occurred_at
           AND NEW.created_at = command.created_at
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_action_guard_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_ACTION_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    command_operation text;
BEGIN
    SELECT operation INTO command_operation
      FROM public.material_request_commands WHERE id = NEW.command_id;
    IF NEW.action = 'cancel' OR command_operation = 'cancel' THEN
        IF NEW.action <> 'cancel' OR command_operation <> 'cancel'
           OR NEW.step_id IS NOT NULL OR NEW.source_mode <> 'internal'
           OR btrim(NEW.comment) = '' OR length(NEW.comment) > 4000
           OR NOT EXISTS (
                SELECT 1
                  FROM public.material_request_commands AS command
                  JOIN public.approval_instances AS instance
                    ON instance.id = NEW.instance_id
                   AND instance.request_id = command.request_id
                 WHERE command.id = NEW.command_id
                   AND command.actor_user_id = NEW.actor_user_id
                   AND command.actor_person_id = NEW.actor_person_id
                   AND command.actor_role_assignment_id = NEW.actor_role_assignment_id
                   AND command.authorization_version = NEW.authorization_version
                   AND command.occurred_at = NEW.occurred_at
                   AND command.created_at = NEW.created_at
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""


def _postgresql_parent_lock_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_PARENT_LOCK_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    target_request_id uuid;
    target_status text;
    target_coordinate text;
    coordinate_match_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'material_request_commands' THEN
        IF NEW.operation <> 'cancel' THEN RETURN NEW; END IF;
        target_request_id := NEW.request_id;
    ELSIF TG_TABLE_NAME IN ('substitution_decisions', 'supply_tasks') THEN
        SELECT request_id INTO target_request_id
          FROM public.material_request_lines WHERE id = NEW.request_line_id;
    ELSIF TG_TABLE_NAME = 'inventory_transactions' THEN
        IF NEW.source_document_type <> 'material_request' THEN RETURN NEW; END IF;
        target_coordinate := NEW.source_document_id;
    ELSIF TG_TABLE_NAME = 'notification_events' THEN
        IF NEW.business_type <> 'material_request' THEN RETURN NEW; END IF;
        target_coordinate := NEW.business_id;
    ELSIF TG_TABLE_NAME = 'outbox_events' THEN
        IF NEW.aggregate_type <> 'material_request' THEN RETURN NEW; END IF;
        target_coordinate := NEW.aggregate_id;
    END IF;
    IF target_coordinate IS NOT NULL THEN
        SELECT (array_agg(DISTINCT candidate.request_id))[1],
               count(DISTINCT candidate.request_id)
          INTO target_request_id, coordinate_match_count
          FROM (
                SELECT request.id AS request_id, request.id::text AS coordinate,
                       true AS is_uuid_coordinate
                  FROM public.material_requests AS request
                UNION ALL
                SELECT request.id, request.request_no, false
                  FROM public.material_requests AS request
                UNION ALL
                SELECT revision.request_id, revision.id::text, true
                  FROM public.material_request_revisions AS revision
                UNION ALL
                SELECT line.request_id, line.id::text, true
                  FROM public.material_request_lines AS line
                UNION ALL
                SELECT line.request_id, decision.id::text, true
                  FROM public.substitution_decisions AS decision
                  JOIN public.material_request_lines AS line
                    ON line.id = decision.request_line_id
                UNION ALL
                SELECT line.request_id, task.id::text, true
                  FROM public.supply_tasks AS task
                  JOIN public.material_request_lines AS line
                    ON line.id = task.request_line_id
          ) AS candidate
         WHERE (
                candidate.is_uuid_coordinate
                AND replace(lower(candidate.coordinate), '-', '') =
                    replace(lower(target_coordinate), '-', '')
               )
            OR (
                NOT candidate.is_uuid_coordinate
                AND candidate.coordinate = target_coordinate
               );
        IF coordinate_match_count > 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    IF target_request_id IS NULL THEN RETURN NEW; END IF;
    SELECT status INTO target_status
      FROM public.material_requests
     WHERE id = target_request_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF target_status = 'cancelled' THEN
        IF TG_TABLE_NAME = 'substitution_decisions' THEN
            IF NEW.status IN ('proposed','confirmed') THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSIF TG_TABLE_NAME = 'supply_tasks' THEN
            IF NEW.status IN ('open','reference_registered','awaiting_supply') THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSIF TG_TABLE_NAME IN (
            'inventory_transactions','notification_events','outbox_events'
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""


def _create_postgresql_functions() -> None:
    for sql in (
        _postgresql_validator_sql(),
        _postgresql_dispatch_sql(),
        _postgresql_fact_guard_sql(),
        _postgresql_action_guard_sql(),
        _postgresql_parent_lock_sql(),
    ):
        op.execute(sql)


def _create_postgresql_triggers() -> None:
    op.execute(
        f"CREATE TRIGGER trg_material_request_cancellation_facts_guard_0037 "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.{FACT_TABLE} FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_FACT_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_material_request_cancellation_facts_no_truncate_0037 "
        f"BEFORE TRUNCATE ON public.{FACT_TABLE} FOR EACH STATEMENT "
        f"EXECUTE FUNCTION public.{PG_FACT_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_approval_actions_cancel_guard_0037 BEFORE INSERT "
        f"ON public.approval_actions FOR EACH ROW EXECUTE FUNCTION "
        f"public.{PG_ACTION_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_material_request_commands_parent_lock_0037 "
        f"BEFORE INSERT ON public.material_request_commands FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_PARENT_LOCK_FUNCTION}()"
    )
    for table_name in ("substitution_decisions", "supply_tasks"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_request_parent_lock_0037 "
            f"BEFORE INSERT OR UPDATE ON public.{table_name} FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_PARENT_LOCK_FUNCTION}()"
        )
    for table_name in (
        "inventory_transactions",
        "notification_events",
        "outbox_events",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_request_parent_lock_0037 "
            f"BEFORE INSERT ON public.{table_name} FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_PARENT_LOCK_FUNCTION}()"
        )
    trigger_operations = {
        "material_requests": "UPDATE",
        "material_request_lines": "INSERT OR UPDATE",
        "material_request_commands": "INSERT",
        "approval_instances": "UPDATE",
        "approval_actions": "INSERT",
        FACT_TABLE: "INSERT",
        "substitution_decisions": "INSERT OR UPDATE",
        "supply_tasks": "INSERT OR UPDATE",
        "state_transition_events": "INSERT",
        "audit_events": "INSERT",
        "inventory_transactions": "INSERT",
        "notification_events": "INSERT",
        "outbox_events": "INSERT",
    }
    for table_name, operations in trigger_operations.items():
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_{table_name}_cancellation_graph_0037 "
            f"AFTER {operations} ON public.{table_name} DEFERRABLE INITIALLY DEFERRED "
            f"FOR EACH ROW EXECUTE FUNCTION public.{PG_DISPATCH_FUNCTION}()"
        )
    for trigger_name, table_name in _postgresql_triggers().items():
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )


def _drop_postgresql_triggers() -> None:
    for trigger_name, table_name in _postgresql_triggers().items():
        op.execute(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}"
        )


def _postgresql_triggers() -> dict[str, str]:
    rows = {
        "trg_material_request_cancellation_facts_guard_0037": FACT_TABLE,
        "trg_material_request_cancellation_facts_no_truncate_0037": FACT_TABLE,
        "trg_approval_actions_cancel_guard_0037": "approval_actions",
        "trg_material_request_commands_parent_lock_0037": "material_request_commands",
        "trg_substitution_decisions_request_parent_lock_0037": "substitution_decisions",
        "trg_supply_tasks_request_parent_lock_0037": "supply_tasks",
        "trg_inventory_transactions_request_parent_lock_0037": "inventory_transactions",
        "trg_notification_events_request_parent_lock_0037": "notification_events",
        "trg_outbox_events_request_parent_lock_0037": "outbox_events",
    }
    for table_name in (
        "material_requests",
        "material_request_lines",
        "material_request_commands",
        "approval_instances",
        "approval_actions",
        FACT_TABLE,
        "substitution_decisions",
        "supply_tasks",
        "state_transition_events",
        "audit_events",
        "inventory_transactions",
        "notification_events",
        "outbox_events",
    ):
        rows[f"trg_{table_name}_cancellation_graph_0037"] = table_name
    return rows


def _sqlite_trigger_names() -> tuple[str, ...]:
    return (
        "trg_material_request_cancellation_facts_insert_guard_0037",
        "trg_material_request_cancellation_facts_immutable_update_0037",
        "trg_material_request_cancellation_facts_immutable_delete_0037",
        "trg_approval_actions_cancel_insert_guard_0037",
        "trg_material_request_lines_cancel_update_guard_0037",
        "trg_material_requests_cancel_update_guard_0037",
        "trg_substitution_decisions_cancelled_request_insert_guard_0037",
        "trg_substitution_decisions_cancelled_request_update_guard_0037",
        "trg_supply_tasks_cancelled_request_insert_guard_0037",
        "trg_supply_tasks_cancelled_request_update_guard_0037",
        "trg_inventory_transactions_cancelled_request_guard_0037",
        "trg_notification_events_cancelled_request_guard_0037",
        "trg_outbox_events_cancelled_request_guard_0037",
    )


def _create_sqlite_guards() -> None:
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_cancellation_facts_insert_guard_0037
BEFORE INSERT ON {FACT_TABLE}
WHEN NOT EXISTS (
    SELECT 1
      FROM material_requests AS request
      JOIN material_request_revisions AS revision
        ON revision.id = NEW.request_revision_id
       AND revision.request_id = request.id
       AND revision.revision_no = request.revision_no
      JOIN material_request_lines AS line
        ON line.id = NEW.request_line_id
       AND line.request_id = request.id
       AND line.revision_id = revision.id
      JOIN approval_actions AS action
        ON action.id = NEW.cancel_action_id
       AND action.instance_id = NEW.instance_id
       AND action.command_id = NEW.cancel_command_id
      JOIN material_request_commands AS command
        ON command.id = NEW.cancel_command_id
       AND command.request_id = request.id
      JOIN approval_instances AS instance
        ON instance.id = NEW.instance_id
       AND instance.request_id = request.id
     WHERE request.id = NEW.request_id
       AND request.status IN ('returned','approved','partially_approved','cancellation_pending')
       AND request.cancelled_at IS NULL
       AND command.operation = 'cancel'
       AND command.target_version = request.version + 1
       AND action.action = 'cancel' AND action.step_id IS NULL
       AND action.source_mode = 'internal'
       AND instance.status IN ('returned','completed')
       AND instance.current_step_id IS NULL AND instance.current_step_no IS NULL
       AND line.final_approved_qty > 0 AND line.cancelled_qty = 0
       AND NEW.final_approved_qty_before = line.final_approved_qty
       AND NEW.cancelled_qty = line.final_approved_qty
       AND NEW.actor_user_id = command.actor_user_id
       AND NEW.actor_person_id = command.actor_person_id
       AND NEW.actor_role_assignment_id = command.actor_role_assignment_id
       AND NEW.authorization_version = command.authorization_version
       AND NEW.occurred_at = command.occurred_at
       AND NEW.created_at = command.created_at
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"""
CREATE TRIGGER trg_material_request_cancellation_facts_immutable_{operation.lower()}_0037
BEFORE {operation} ON {FACT_TABLE}
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
        )
    op.execute(
        f"""
CREATE TRIGGER trg_approval_actions_cancel_insert_guard_0037
BEFORE INSERT ON approval_actions
WHEN (NEW.action = 'cancel' OR EXISTS (
    SELECT 1 FROM material_request_commands
     WHERE id = NEW.command_id AND operation = 'cancel'
)) AND NOT EXISTS (
    SELECT 1
      FROM material_request_commands AS command
      JOIN approval_instances AS instance
        ON instance.id = NEW.instance_id
       AND instance.request_id = command.request_id
     WHERE command.id = NEW.command_id
       AND command.operation = 'cancel' AND NEW.action = 'cancel'
       AND NEW.step_id IS NULL AND NEW.source_mode = 'internal'
       AND length(trim(NEW.comment)) BETWEEN 1 AND 4000
       AND command.actor_user_id = NEW.actor_user_id
       AND command.actor_person_id = NEW.actor_person_id
       AND command.actor_role_assignment_id = NEW.actor_role_assignment_id
       AND command.authorization_version = NEW.authorization_version
       AND command.occurred_at = NEW.occurred_at
       AND command.created_at = NEW.created_at
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_request_lines_cancel_update_guard_0037
BEFORE UPDATE ON material_request_lines
WHEN NEW.status = 'cancelled' AND NOT EXISTS (
    SELECT 1
      FROM material_requests AS request
      JOIN material_request_commands AS command
        ON command.request_id = request.id AND command.operation = 'cancel'
      JOIN approval_actions AS action
        ON action.command_id = command.id AND action.action = 'cancel'
       AND action.step_id IS NULL
     WHERE request.id = NEW.request_id
       AND request.status IN ('returned','approved','partially_approved','cancellation_pending')
       AND NEW.cancelled_qty = NEW.final_approved_qty
       AND command.target_version = request.version + 1
       AND (
            NEW.final_approved_qty = 0
            OR EXISTS (
                SELECT 1 FROM {FACT_TABLE} AS fact
                 WHERE fact.request_line_id = NEW.id
                   AND fact.cancel_command_id = command.id
                   AND fact.cancel_action_id = action.id
                   AND fact.cancelled_qty = NEW.final_approved_qty
            )
       )
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_material_requests_cancel_update_guard_0037
BEFORE UPDATE ON material_requests
WHEN OLD.status = 'cancelled' OR (
    NEW.status = 'cancelled' AND (
       OLD.status NOT IN ('returned','approved','partially_approved','cancellation_pending')
       OR NEW.cancelled_at IS NULL
       OR NEW.allocation_status <> 'not_allocated'
       OR NEW.reservation_status <> 'not_reserved'
       OR NEW.outbound_status <> 'not_started'
       OR NEW.shipment_status <> 'not_started'
       OR NEW.logistics_signature_status <> 'not_signed'
       OR NEW.oam_receipt_status <> 'not_occurred'
       OR NEW.personal_inbound_status <> 'not_started'
       OR NEW.notification_status <> 'not_started'
       OR NEW.reconciliation_status <> 'not_started'
       OR (SELECT count(*) FROM material_request_commands
            WHERE request_id = NEW.id AND operation = 'cancel') <> 1
       OR (SELECT count(*) FROM approval_actions AS action
            JOIN material_request_commands AS command ON command.id = action.command_id
            WHERE command.request_id = NEW.id AND action.action = 'cancel') <> 1
       OR NOT EXISTS (
            SELECT 1
              FROM material_request_revisions AS revision
              JOIN material_request_commands AS command
                ON command.request_id = NEW.id AND command.operation = 'cancel'
              JOIN approval_actions AS action
                ON action.command_id = command.id AND action.action = 'cancel'
              JOIN approval_instances AS instance ON instance.id = action.instance_id
             WHERE revision.request_id = NEW.id AND revision.revision_no = NEW.revision_no
               AND command.target_version = NEW.version
               AND command.actor_user_id = NEW.requester_user_id
               AND command.actor_person_id = NEW.requester_person_id
               AND NEW.created_by_user_id = command.actor_user_id
               AND action.step_id IS NULL AND action.source_mode = 'internal'
               AND action.actor_user_id = command.actor_user_id
               AND action.actor_person_id = command.actor_person_id
               AND action.actor_role_assignment_id = command.actor_role_assignment_id
               AND action.authorization_version = command.authorization_version
               AND action.occurred_at = command.occurred_at
               AND command.occurred_at = NEW.cancelled_at
               AND instance.request_id = NEW.id
               AND instance.status IN ('returned','completed')
               AND instance.current_step_id IS NULL AND instance.current_step_no IS NULL
               AND json_valid(command.request_jsonb)
               AND json_extract(command.request_jsonb, '$.schema') =
                   'rsc.material_request_lifecycle_command.v1'
               AND json_extract(command.request_jsonb, '$.operation') = 'cancel'
               AND lower(replace(json_extract(command.request_jsonb, '$.request_id'),'-','')) = lower(replace(NEW.id,'-',''))
               AND lower(replace(json_extract(command.request_jsonb, '$.revision_id'),'-','')) = lower(replace(revision.id,'-',''))
               AND json_extract(command.request_jsonb, '$.revision_no') = revision.revision_no
               AND json_extract(command.request_jsonb, '$.target_version') = NEW.version
               AND json_extract(command.request_jsonb, '$.payload_sha256') = command.request_hash
               AND json_extract(command.request_jsonb, '$.sensitive_fields') = 'excluded'
               AND json_type(command.request_jsonb, '$.cancellation_fact_count') = 'integer'
               AND length(json_extract(command.request_jsonb, '$.cancellation_fact_manifest_sha256')) = 64
               AND (SELECT count(*) FROM json_each(command.request_jsonb)) = 10
               AND json_extract(command.request_jsonb, '$.cancellation_fact_count') =
                   (SELECT count(*) FROM {FACT_TABLE} AS fact
                     WHERE fact.cancel_command_id = command.id)
       )
       OR EXISTS (
            SELECT 1 FROM material_request_lines AS line
             JOIN material_request_revisions AS revision ON revision.id = line.revision_id
            WHERE line.request_id = NEW.id AND revision.revision_no = NEW.revision_no
              AND (line.status <> 'cancelled' OR line.cancelled_qty <> line.final_approved_qty)
       )
       OR EXISTS (
            SELECT 1 FROM material_request_lines AS line
             JOIN material_request_revisions AS revision ON revision.id = line.revision_id
            WHERE line.request_id = NEW.id AND revision.revision_no = NEW.revision_no
              AND line.final_approved_qty > 0
              AND NOT EXISTS (
                  SELECT 1 FROM {FACT_TABLE} AS fact
                   JOIN material_request_commands AS command ON command.id = fact.cancel_command_id
                   JOIN approval_actions AS action ON action.id = fact.cancel_action_id
                  WHERE fact.request_line_id = line.id
                    AND fact.request_id = NEW.id
                    AND fact.request_revision_id = revision.id
                    AND action.command_id = command.id
                    AND action.instance_id = fact.instance_id
                    AND fact.final_approved_qty_before = line.final_approved_qty
                    AND fact.cancelled_qty = line.final_approved_qty
                    AND fact.actor_user_id = command.actor_user_id
                    AND fact.actor_person_id = command.actor_person_id
                    AND fact.actor_role_assignment_id = command.actor_role_assignment_id
                    AND fact.authorization_version = command.authorization_version
                    AND fact.occurred_at = command.occurred_at
              )
       )
       OR EXISTS (
            SELECT 1 FROM substitution_decisions AS decision
             JOIN material_request_lines AS line ON line.id = decision.request_line_id
            WHERE line.request_id = NEW.id AND decision.status IN ('proposed','confirmed')
       )
       OR EXISTS (
            SELECT 1 FROM supply_tasks AS task
             JOIN material_request_lines AS line ON line.id = task.request_line_id
            WHERE line.request_id = NEW.id
              AND task.status IN ('open','reference_registered','awaiting_supply')
       )
       OR EXISTS (
            SELECT 1 FROM inventory_transactions AS fact
             WHERE fact.source_document_type = 'material_request'
               AND (
                    fact.source_document_id = NEW.request_no
                    OR lower(replace(fact.source_document_id,'-','')) = lower(replace(NEW.id,'-',''))
                    OR EXISTS (
                        SELECT 1 FROM material_request_revisions AS revision
                         WHERE revision.request_id = NEW.id
                           AND lower(replace(revision.id,'-','')) = lower(replace(fact.source_document_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM material_request_lines AS line
                         WHERE line.request_id = NEW.id
                           AND lower(replace(line.id,'-','')) = lower(replace(fact.source_document_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM substitution_decisions AS decision
                         JOIN material_request_lines AS line ON line.id = decision.request_line_id
                        WHERE line.request_id = NEW.id
                          AND lower(replace(decision.id,'-','')) = lower(replace(fact.source_document_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM supply_tasks AS task
                         JOIN material_request_lines AS line ON line.id = task.request_line_id
                        WHERE line.request_id = NEW.id
                          AND lower(replace(task.id,'-','')) = lower(replace(fact.source_document_id,'-',''))
                    )
               )
       )
       OR EXISTS (
            SELECT 1 FROM notification_events AS fact
             WHERE fact.business_type = 'material_request'
               AND (
                    fact.business_id = NEW.request_no
                    OR lower(replace(fact.business_id,'-','')) = lower(replace(NEW.id,'-',''))
                    OR EXISTS (
                        SELECT 1 FROM material_request_revisions AS revision
                         WHERE revision.request_id = NEW.id
                           AND lower(replace(revision.id,'-','')) = lower(replace(fact.business_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM material_request_lines AS line
                         WHERE line.request_id = NEW.id
                           AND lower(replace(line.id,'-','')) = lower(replace(fact.business_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM substitution_decisions AS decision
                         JOIN material_request_lines AS line ON line.id = decision.request_line_id
                        WHERE line.request_id = NEW.id
                          AND lower(replace(decision.id,'-','')) = lower(replace(fact.business_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM supply_tasks AS task
                         JOIN material_request_lines AS line ON line.id = task.request_line_id
                        WHERE line.request_id = NEW.id
                          AND lower(replace(task.id,'-','')) = lower(replace(fact.business_id,'-',''))
                    )
               )
       )
       OR EXISTS (
            SELECT 1 FROM outbox_events AS fact
             WHERE fact.aggregate_type = 'material_request'
               AND (
                    fact.aggregate_id = NEW.request_no
                    OR lower(replace(fact.aggregate_id,'-','')) = lower(replace(NEW.id,'-',''))
                    OR EXISTS (
                        SELECT 1 FROM material_request_revisions AS revision
                         WHERE revision.request_id = NEW.id
                           AND lower(replace(revision.id,'-','')) = lower(replace(fact.aggregate_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM material_request_lines AS line
                         WHERE line.request_id = NEW.id
                           AND lower(replace(line.id,'-','')) = lower(replace(fact.aggregate_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM substitution_decisions AS decision
                         JOIN material_request_lines AS line ON line.id = decision.request_line_id
                        WHERE line.request_id = NEW.id
                          AND lower(replace(decision.id,'-','')) = lower(replace(fact.aggregate_id,'-',''))
                    )
                    OR EXISTS (
                        SELECT 1 FROM supply_tasks AS task
                         JOIN material_request_lines AS line ON line.id = task.request_line_id
                        WHERE line.request_id = NEW.id
                          AND lower(replace(task.id,'-','')) = lower(replace(fact.aggregate_id,'-',''))
                    )
               )
       )
    )
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
    )
    for table_name, active_statuses in (
        ("substitution_decisions", "'proposed','confirmed'"),
        ("supply_tasks", "'open','reference_registered','awaiting_supply'"),
    ):
        for operation in ("INSERT", "UPDATE"):
            op.execute(
                f"""
CREATE TRIGGER trg_{table_name}_cancelled_request_{operation.lower()}_guard_0037
BEFORE {operation} ON {table_name}
WHEN NEW.status IN ({active_statuses}) AND EXISTS (
    SELECT 1 FROM material_request_lines AS line
     JOIN material_requests AS request ON request.id = line.request_id
    WHERE line.id = NEW.request_line_id AND request.status = 'cancelled'
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
            )

    for table_name, type_column, id_column in (
        ("inventory_transactions", "source_document_type", "source_document_id"),
        ("notification_events", "business_type", "business_id"),
        ("outbox_events", "aggregate_type", "aggregate_id"),
    ):
        op.execute(
            f"""
CREATE TRIGGER trg_{table_name}_cancelled_request_guard_0037
BEFORE INSERT ON {table_name}
WHEN NEW.{type_column} = 'material_request' AND (
  (SELECT count(DISTINCT request.id) FROM material_requests AS request
     WHERE NEW.{id_column} = request.request_no
        OR lower(replace(NEW.{id_column},'-','')) = lower(replace(request.id,'-',''))
        OR EXISTS (
            SELECT 1 FROM material_request_revisions AS revision
             WHERE revision.request_id = request.id
               AND lower(replace(revision.id,'-','')) = lower(replace(NEW.{id_column},'-',''))
        )
        OR EXISTS (
            SELECT 1 FROM material_request_lines AS line
             WHERE line.request_id = request.id
               AND lower(replace(line.id,'-','')) = lower(replace(NEW.{id_column},'-',''))
        )
        OR EXISTS (
            SELECT 1 FROM substitution_decisions AS decision
             JOIN material_request_lines AS line ON line.id = decision.request_line_id
            WHERE line.request_id = request.id
              AND lower(replace(decision.id,'-','')) = lower(replace(NEW.{id_column},'-',''))
        )
        OR EXISTS (
            SELECT 1 FROM supply_tasks AS task
             JOIN material_request_lines AS line ON line.id = task.request_line_id
            WHERE line.request_id = request.id
              AND lower(replace(task.id,'-','')) = lower(replace(NEW.{id_column},'-',''))
        )) > 1
  OR EXISTS (
    SELECT 1 FROM material_requests AS request
     WHERE request.status = 'cancelled'
       AND (
            NEW.{id_column} = request.request_no
            OR lower(replace(NEW.{id_column},'-','')) = lower(replace(request.id,'-',''))
            OR EXISTS (
                SELECT 1 FROM material_request_revisions AS revision
                 WHERE revision.request_id = request.id
                   AND lower(replace(revision.id,'-','')) = lower(replace(NEW.{id_column},'-',''))
            )
            OR EXISTS (
                SELECT 1 FROM material_request_lines AS line
                 WHERE line.request_id = request.id
                   AND lower(replace(line.id,'-','')) = lower(replace(NEW.{id_column},'-',''))
            )
            OR EXISTS (
                SELECT 1 FROM substitution_decisions AS decision
                 JOIN material_request_lines AS line ON line.id = decision.request_line_id
                WHERE line.request_id = request.id
                  AND lower(replace(decision.id,'-','')) = lower(replace(NEW.{id_column},'-',''))
            )
            OR EXISTS (
                SELECT 1 FROM supply_tasks AS task
                 JOIN material_request_lines AS line ON line.id = task.request_line_id
                WHERE line.request_id = request.id
                  AND lower(replace(task.id,'-','')) = lower(replace(NEW.{id_column},'-',''))
            )
       )
  )
)
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
"""
        )


def _apply_postgresql_acl() -> None:
    all_tables = tuple(sorted(set(READ_TABLES) | set(INSERT_TABLES) | set(DELETE_TABLES)))
    qualified = ", ".join(f"public.{name}" for name in all_tables)
    op.execute(f"REVOKE ALL ON TABLE {qualified} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON TABLE {qualified} FROM {PRODUCTION_API_ROLE}")
    op.execute(
        "GRANT SELECT ON TABLE "
        + ", ".join(f"public.{name}" for name in READ_TABLES)
        + f" TO {PRODUCTION_API_ROLE}"
    )
    op.execute(
        "GRANT INSERT ON TABLE "
        + ", ".join(f"public.{name}" for name in INSERT_TABLES)
        + f" TO {PRODUCTION_API_ROLE}"
    )
    op.execute(
        "GRANT DELETE ON TABLE "
        + ", ".join(f"public.{name}" for name in DELETE_TABLES)
        + f" TO {PRODUCTION_API_ROLE}"
    )
    for table_name, columns in UPDATE_COLUMNS.items():
        op.execute(
            f"GRANT UPDATE ({', '.join(columns)}) ON TABLE public.{table_name} "
            f"TO {PRODUCTION_API_ROLE}"
        )
    for function_name, argument_types in (
        (PG_VALIDATE_FUNCTION, "uuid"),
        (PG_DISPATCH_FUNCTION, ""),
        (PG_FACT_GUARD_FUNCTION, ""),
        (PG_ACTION_GUARD_FUNCTION, ""),
        (PG_PARENT_LOCK_FUNCTION, ""),
    ):
        signature = f"public.{function_name}({argument_types})"
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION {signature} FROM {PRODUCTION_API_ROLE}"
        )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{MIGRATION_ROLE}') THEN
        EXECUTE 'ALTER FUNCTION public.{PG_VALIDATE_FUNCTION}(uuid) OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION public.{PG_DISPATCH_FUNCTION}() OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION public.{PG_FACT_GUARD_FUNCTION}() OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION public.{PG_ACTION_GUARD_FUNCTION}() OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION public.{PG_PARENT_LOCK_FUNCTION}() OWNER TO {MIGRATION_ROLE}';
    END IF;
END
$$
"""
    )


def _restore_postgresql_acl() -> None:
    all_tables = tuple(sorted(set(READ_TABLES) | set(INSERT_TABLES) | set(DELETE_TABLES)))
    qualified = ", ".join(f"public.{name}" for name in all_tables)
    op.execute(f"REVOKE ALL ON TABLE {qualified} FROM {PRODUCTION_API_ROLE}")
