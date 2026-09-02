"""Activate and close the formal material-request approval boundary.

Revision ID: 20260903_0045
Revises: 20260902_0044
Create Date: 2026-09-03

The 0029/0030 approval graph was protected only while PostgreSQL used the
default replication role, and the mutable request/line projections were not
causally tied back to that graph.  This revision therefore:

* refuses to adopt pre-existing prototype facts without a separate migration;
* makes every 0029/0030 approval trigger fire in every session mode;
* serializes every command insert on its exact parent request;
* constrains request and line state transitions immediately; and
* validates request/line projections, commands, decisions, returns, terminal
  actions, state events and audit events together at transaction end.

It creates no business facts and does not enable the application write flag.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260903_0045"
down_revision: Union[str, Sequence[str], None] = "20260902_0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
OAM_RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
PREVIOUS_SCHEMA_REVISION = "20260902_0044"
GUARD_ERROR = "formal material request approval projection is invalid"
UPGRADE_BLOCKER = (
    "0045 approval activation requires an empty formal material-request graph"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0045 while formal material-request or approval facts exist"
)

PG_APPROVAL_VALIDATE_FUNCTION_0030 = (
    "rsc_validate_approval_instance_causality_0030"
)
PG_APPROVAL_DISPATCH_FUNCTION_0030 = "rsc_dispatch_approval_causality_0030"
PG_STATUS_GUARD_FUNCTION = "rsc_guard_material_request_status_transition_0045"
PG_LINE_GUARD_FUNCTION = "rsc_guard_material_request_line_projection_0045"
PG_COMMAND_PARENT_LOCK_FUNCTION = (
    "rsc_lock_material_request_command_parent_0045"
)
PG_TERMINAL_VALIDATE_FUNCTION = (
    "rsc_validate_material_request_terminal_causality_0045"
)
PG_RETURN_VALIDATE_FUNCTION = (
    "rsc_validate_material_request_return_causality_0045"
)
PG_EXTERNAL_VALIDATE_FUNCTION = (
    "rsc_validate_material_request_external_causality_0045"
)
PG_PROJECTION_VALIDATE_FUNCTION = (
    "rsc_validate_material_request_approval_projection_0045"
)
PG_PROJECTION_DISPATCH_FUNCTION = (
    "rsc_dispatch_material_request_approval_projection_0045"
)

STATUS_TRIGGER = "trg_material_requests_status_transition_0045"
LINE_TRIGGER = "trg_material_request_lines_projection_write_0045"
COMMAND_PARENT_TRIGGER = "trg_material_request_commands_parent_lock_0045"
PROJECTION_TRIGGER_TABLES = (
    "material_requests",
    "material_request_revisions",
    "material_request_lines",
    "material_request_commands",
    "approval_instances",
    "approval_steps",
    "approval_actions",
    "approval_external_registrations",
    "approval_external_registration_lines",
    "approval_step_line_decisions",
    "approval_return_line_facts",
    "state_transition_events",
    "audit_events",
)
PROJECTION_TRIGGER_BINDINGS = tuple(
    (
        table_name,
        (
            "trg_approval_external_registration_lines_projection_0045"
            if table_name == "approval_external_registration_lines"
            else f"trg_{table_name}_approval_projection_0045"
        ),
    )
    for table_name in PROJECTION_TRIGGER_TABLES
)

FACT_TABLES_0029 = (
    "approval_step_candidates",
    "material_request_commands",
    "approval_external_registration_lines",
    "approval_step_line_decisions",
    "approval_actions",
)

LEGACY_TRIGGER_BINDINGS: tuple[tuple[str, str], ...] = (
    *tuple(
        (table_name, f"trg_{table_name}_{suffix}_0029")
        for table_name in FACT_TABLES_0029
        for suffix in ("immutable", "no_truncate")
    ),
    ("material_requests", "trg_material_requests_guard_0029"),
    ("material_request_revisions", "trg_material_request_revisions_guard_0029"),
    ("material_request_lines", "trg_material_request_lines_guard_0029"),
    ("approval_instances", "trg_approval_instances_guard_0029"),
    ("material_request_files", "trg_material_request_files_guard_0029"),
    ("approval_delegations", "trg_approval_delegations_guard_0029"),
    (
        "approval_external_registrations",
        "trg_approval_external_registrations_guard_0029",
    ),
    (
        "approval_external_registration_lines",
        "trg_approval_external_registration_lines_quantity_0029",
    ),
    (
        "approval_step_line_decisions",
        "trg_approval_step_line_decisions_quantity_0029",
    ),
    ("substitution_decisions", "trg_substitution_decisions_guard_0029"),
    ("supply_tasks", "trg_supply_tasks_guard_0029"),
    *tuple(
        (table_name, f"trg_{table_name}_no_truncate_0029")
        for table_name in (
            "material_requests",
            "material_request_revisions",
            "material_request_lines",
            "material_request_files",
            "approval_instances",
            "approval_steps",
            "approval_external_registrations",
            "substitution_decisions",
            "supply_tasks",
            "approval_delegations",
        )
    ),
    ("approval_steps", "trg_approval_steps_no_delete_0029"),
    ("approval_steps", "trg_approval_steps_write_guard_0030"),
    (
        "approval_step_candidates",
        "trg_approval_step_candidates_write_guard_0030",
    ),
    ("approval_actions", "trg_approval_actions_write_guard_0030"),
    (
        "approval_step_line_decisions",
        "trg_approval_step_line_decisions_current_guard_0030",
    ),
    (
        "approval_return_line_facts",
        "trg_approval_return_line_facts_write_guard_0030",
    ),
    (
        "approval_return_line_facts",
        "trg_approval_return_line_facts_immutable_0030",
    ),
    (
        "approval_return_line_facts",
        "trg_approval_return_line_facts_no_truncate_0030",
    ),
    *tuple(
        (table_name, f"trg_{table_name}_causality_0030")
        for table_name in (
            "approval_instances",
            "approval_steps",
            "approval_step_candidates",
            "approval_actions",
            "approval_step_line_decisions",
            "approval_return_line_facts",
        )
    ),
)

NEW_TRIGGER_BINDINGS: tuple[tuple[str, str], ...] = (
    ("material_requests", STATUS_TRIGGER),
    ("material_request_lines", LINE_TRIGGER),
    ("material_request_commands", COMMAND_PARENT_TRIGGER),
    *PROJECTION_TRIGGER_BINDINGS,
)

TRIGGER_BINDINGS = LEGACY_TRIGGER_BINDINGS + NEW_TRIGGER_BINDINGS

APPROVAL_FACT_TABLES = (
    "material_requests",
    "material_request_revisions",
    "material_request_lines",
    "material_request_files",
    "material_request_commands",
    "approval_instances",
    "approval_steps",
    "approval_step_candidates",
    "approval_actions",
    "approval_step_line_decisions",
    "approval_external_registrations",
    "approval_external_registration_lines",
    "approval_return_line_facts",
    "approval_delegations",
    "substitution_decisions",
    "supply_tasks",
)


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0045 supports only PostgreSQL and SQLite test databases")
    _lock_trigger_tables()
    _emit_empty_graph_guard(UPGRADE_BLOCKER)
    _repair_0030_dispatcher_execution_context()
    _create_projection_guards()
    _replace_oam_runtime_ready_function(revision)
    for table_name, trigger_name in TRIGGER_BINDINGS:
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )


def downgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0045 supports only PostgreSQL and SQLite test databases")
    if context.is_offline_mode():
        raise RuntimeError("0045 PostgreSQL downgrade requires an online connection")
    _lock_trigger_tables()
    _emit_empty_graph_guard(DOWNGRADE_BLOCKER)
    for table_name, trigger_name in reversed(NEW_TRIGGER_BINDINGS):
        op.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
    for function_name, argument_types in (
        (PG_PROJECTION_DISPATCH_FUNCTION, ""),
        (PG_PROJECTION_VALIDATE_FUNCTION, "uuid"),
        (PG_RETURN_VALIDATE_FUNCTION, "uuid, uuid"),
        (PG_EXTERNAL_VALIDATE_FUNCTION, "uuid"),
        (PG_TERMINAL_VALIDATE_FUNCTION, "uuid, uuid, uuid, bigint"),
        (PG_COMMAND_PARENT_LOCK_FUNCTION, ""),
        (PG_LINE_GUARD_FUNCTION, ""),
        (PG_STATUS_GUARD_FUNCTION, ""),
    ):
        op.execute(f"DROP FUNCTION public.{function_name}({argument_types})")
    op.execute(
        f"ALTER FUNCTION public.{PG_APPROVAL_DISPATCH_FUNCTION_0030}() "
        "SECURITY INVOKER"
    )
    _replace_oam_runtime_ready_function(PREVIOUS_SCHEMA_REVISION)
    for table_name, trigger_name in LEGACY_TRIGGER_BINDINGS:
        op.execute(f"ALTER TABLE public.{table_name} ENABLE TRIGGER {trigger_name}")


def _lock_trigger_tables() -> None:
    table_names = tuple(dict.fromkeys(row[0] for row in TRIGGER_BINDINGS))
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in table_names)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _emit_empty_graph_guard(message: str) -> None:
    condition = "\n       OR ".join(
        f"EXISTS (SELECT 1 FROM public.{table_name})"
        for table_name in APPROVAL_FACT_TABLES
    )
    op.execute(
        f"""
DO $rsc_0045$
BEGIN
    IF {condition} THEN
        RAISE EXCEPTION '{message}';
    END IF;
END
$rsc_0045$
"""
    )


def _repair_0030_dispatcher_execution_context() -> None:
    op.execute(
        f"ALTER FUNCTION public.{PG_APPROVAL_DISPATCH_FUNCTION_0030}() "
        "SECURITY DEFINER"
    )
    op.execute(
        f"ALTER FUNCTION public.{PG_APPROVAL_DISPATCH_FUNCTION_0030}() "
        f"OWNER TO {MIGRATION_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{PG_APPROVAL_DISPATCH_FUNCTION_0030}() "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )


def _replace_oam_runtime_ready_function(expected_revision: str) -> None:
    op.execute(_oam_runtime_ready_function_sql(expected_revision))


def _create_projection_guards() -> None:
    op.execute(_status_guard_sql())
    op.execute(_line_guard_sql())
    op.execute(_command_parent_lock_sql())
    op.execute(_terminal_validator_sql())
    op.execute(_return_validator_sql())
    op.execute(_external_validator_sql())
    op.execute(_projection_validator_sql())
    op.execute(_projection_dispatcher_sql())
    for function_name, argument_types in (
        (PG_STATUS_GUARD_FUNCTION, ""),
        (PG_LINE_GUARD_FUNCTION, ""),
        (PG_COMMAND_PARENT_LOCK_FUNCTION, ""),
        (PG_TERMINAL_VALIDATE_FUNCTION, "uuid, uuid, uuid, bigint"),
        (PG_RETURN_VALIDATE_FUNCTION, "uuid, uuid"),
        (PG_EXTERNAL_VALIDATE_FUNCTION, "uuid"),
        (PG_PROJECTION_VALIDATE_FUNCTION, "uuid"),
        (PG_PROJECTION_DISPATCH_FUNCTION, ""),
    ):
        op.execute(
            f"ALTER FUNCTION public.{function_name}({argument_types}) "
            f"OWNER TO {MIGRATION_ROLE}"
        )
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}({argument_types}) "
            f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
        )
    op.execute(
        f"CREATE TRIGGER {STATUS_TRIGGER} BEFORE INSERT OR UPDATE "
        "ON public.material_requests FOR EACH ROW EXECUTE FUNCTION "
        f"public.{PG_STATUS_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {LINE_TRIGGER} BEFORE UPDATE "
        "ON public.material_request_lines FOR EACH ROW EXECUTE FUNCTION "
        f"public.{PG_LINE_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {COMMAND_PARENT_TRIGGER} BEFORE INSERT "
        "ON public.material_request_commands FOR EACH ROW EXECUTE FUNCTION "
        f"public.{PG_COMMAND_PARENT_LOCK_FUNCTION}()"
    )
    for table_name, trigger_name in PROJECTION_TRIGGER_BINDINGS:
        operations = (
            "INSERT OR UPDATE"
            if table_name == "material_requests"
            else "INSERT OR UPDATE OR DELETE"
        )
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} "
            f"AFTER {operations} ON public.{table_name} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            f"public.{PG_PROJECTION_DISPATCH_FUNCTION}()"
        )


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
                                  'edge_source_instance',
                                  ingress.source_instance,
                                  'work_order_company_id',
                                  ingress.company_id,
                                  'work_order_org_code',
                                  ingress.org_code,
                                  'work_order_scope_key',
                                  ingress.scope_key
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


def _status_guard_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_STATUS_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT (
           (OLD.status = 'draft'
            AND NEW.status IN ('submitted', 'approval_in_progress'))
           OR (OLD.status = 'submitted'
               AND NEW.status IN ('approval_in_progress', 'withdrawn'))
           OR (OLD.status = 'approval_in_progress'
               AND NEW.status IN ('returned', 'approved',
                                  'partially_approved', 'rejected', 'withdrawn'))
           OR (OLD.status = 'returned'
               AND NEW.status IN ('submitted', 'approval_in_progress',
                                  'cancelled'))
           OR (OLD.status IN ('approved', 'partially_approved')
               AND NEW.status = 'cancelled')
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""


def _line_guard_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_LINE_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    current_revision_no integer;
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status
       AND NEW.final_approved_qty IS NOT DISTINCT FROM OLD.final_approved_qty
       AND NEW.cancelled_qty IS NOT DISTINCT FROM OLD.cancelled_qty THEN
        RETURN NEW;
    END IF;
    SELECT request.revision_no INTO current_revision_no
      FROM public.material_requests AS request
     WHERE request.id = NEW.request_id;
    IF NOT FOUND OR NEW.revision_no <> current_revision_no
       OR (NEW.final_approved_qty IS DISTINCT FROM OLD.final_approved_qty
           AND NOT (
               OLD.status = 'approval_pending'
               AND NEW.status IN ('approved', 'partially_approved', 'rejected')
           ))
       OR NOT (
           (OLD.status = 'draft' AND NEW.status = 'approval_pending'
            AND NEW.final_approved_qty = 0 AND NEW.cancelled_qty = 0)
           OR (OLD.status = 'approval_pending' AND NEW.status = 'approved'
               AND NEW.final_approved_qty = NEW.requested_qty
               AND NEW.cancelled_qty = 0)
           OR (OLD.status = 'approval_pending'
               AND NEW.status = 'partially_approved'
               AND NEW.final_approved_qty > 0
               AND NEW.final_approved_qty < NEW.requested_qty
               AND NEW.cancelled_qty = 0)
           OR (OLD.status = 'approval_pending' AND NEW.status = 'rejected'
               AND NEW.final_approved_qty = 0 AND NEW.cancelled_qty = 0)
           OR (OLD.status IN ('draft', 'approval_pending', 'approved',
                             'partially_approved', 'rejected')
               AND NEW.status = 'cancelled'
               AND NEW.final_approved_qty = OLD.final_approved_qty
               AND NEW.cancelled_qty = NEW.final_approved_qty)
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""


def _command_parent_lock_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_COMMAND_PARENT_LOCK_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    PERFORM 1 FROM public.material_requests
     WHERE id = NEW.request_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""


def _terminal_validator_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_TERMINAL_VALIDATE_FUNCTION}(
    checked_request_id uuid,
    checked_instance_id uuid,
    checked_step_id uuid,
    expected_command_version bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    request_row public.material_requests%ROWTYPE;
    instance_row public.approval_instances%ROWTYPE;
    step_row public.approval_steps%ROWTYPE;
    derived_request_status text;
    matching_action_count bigint;
    terminal_actor_user_id text;
    terminal_key_hash text;
BEGIN
    SELECT * INTO request_row
      FROM public.material_requests
     WHERE id = checked_request_id;
    SELECT * INTO instance_row
      FROM public.approval_instances
     WHERE id = checked_instance_id;
    SELECT * INTO step_row
      FROM public.approval_steps
     WHERE id = checked_step_id;
    IF request_row.id IS NULL
       OR instance_row.id IS NULL
       OR step_row.id IS NULL
       OR instance_row.request_id <> request_row.id
       OR step_row.instance_id <> instance_row.id
       OR step_row.status NOT IN ('approved', 'partially_approved', 'rejected')
       OR step_row.decided_at IS NULL
       OR step_row.decided_at IS DISTINCT FROM instance_row.completed_at
       OR step_row.updated_at IS DISTINCT FROM instance_row.completed_at
       OR instance_row.updated_at IS DISTINCT FROM instance_row.completed_at
       OR instance_row.status NOT IN ('completed', 'rejected')
       OR instance_row.current_step_no IS NOT NULL
       OR instance_row.current_step_id IS NOT NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT CASE
               WHEN bool_and(line.final_approved_qty = line.requested_qty)
                   THEN 'approved'
               WHEN bool_and(line.final_approved_qty = 0)
                   THEN 'rejected'
               ELSE 'partially_approved'
           END
      INTO derived_request_status
      FROM public.material_request_lines AS line
     WHERE line.request_id = checked_request_id
       AND line.revision_id = instance_row.request_revision_id;
    IF derived_request_status IS NULL
       OR (derived_request_status = 'rejected'
           AND instance_row.status <> 'rejected')
       OR (derived_request_status <> 'rejected'
           AND instance_row.status <> 'completed')
       OR request_row.decided_at IS DISTINCT FROM instance_row.completed_at
       OR (request_row.status <> 'cancelled' AND (
           request_row.status <> derived_request_status
           OR request_row.version <> expected_command_version
           OR request_row.updated_at IS DISTINCT FROM instance_row.completed_at
       )) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT count(*), min(action.actor_user_id),
           min(command.idempotency_key_hash)
      INTO matching_action_count, terminal_actor_user_id, terminal_key_hash
      FROM public.approval_actions AS action
      JOIN public.material_request_commands AS command
        ON command.id = action.command_id
     WHERE action.instance_id = instance_row.id
       AND action.step_id = step_row.id
       AND command.request_id = request_row.id
       AND command.target_version = expected_command_version
       AND command.occurred_at = instance_row.completed_at
       AND command.created_at = command.occurred_at
       AND action.occurred_at = command.occurred_at
       AND action.created_at = command.created_at
       AND action.actor_person_id = command.actor_person_id
       AND action.actor_role_assignment_id = command.actor_role_assignment_id
       AND action.authorization_version = command.authorization_version
       AND action.actor_user_id = command.actor_user_id
       AND EXISTS (
           SELECT 1
             FROM public.approval_step_candidates AS candidate
            WHERE candidate.step_id = step_row.id
              AND candidate.user_id = action.actor_user_id
              AND candidate.person_id = action.actor_person_id
              AND candidate.role_assignment_id =
                  action.actor_role_assignment_id
              AND candidate.authorization_version =
                  action.authorization_version
              AND candidate.candidate_kind = CASE
                  WHEN step_row.step_no = 3 THEN 'verifier'
                  ELSE 'assignee'
              END
       )
       AND command.request_hash ~ '^[0-9a-f]{{64}}$'
       AND command.result_hash ~ '^[0-9a-f]{{64}}$'
       AND jsonb_typeof(command.request_jsonb) = 'object'
       AND jsonb_typeof(command.result_jsonb) = 'object'
       AND command.request_jsonb->>'schema' =
           'rsc.material_request_approval_command.v1'
       AND command.request_jsonb->>'request_id' = request_row.id::text
       AND command.request_jsonb->>'revision_id' =
           instance_row.request_revision_id::text
       AND command.request_jsonb->>'revision_no' =
           instance_row.revision_no::text
       AND command.request_jsonb->>'instance_id' = instance_row.id::text
       AND command.request_jsonb->>'target_version' =
           expected_command_version::text
       AND command.request_jsonb->>'payload_sha256' = command.request_hash
       AND command.result_jsonb->>'request_id' = request_row.id::text
       AND command.result_jsonb->>'request_status' = derived_request_status
       AND command.result_jsonb->>'request_version' =
           expected_command_version::text
       AND command.result_jsonb->>'revision_id' =
           instance_row.request_revision_id::text
       AND command.result_jsonb->>'revision_no' = instance_row.revision_no::text
       AND command.result_jsonb->>'instance_id' = instance_row.id::text
       AND command.result_jsonb->>'instance_status' = instance_row.status
       AND NOT EXISTS (
           SELECT 1
             FROM public.approval_step_line_decisions AS decision
            WHERE decision.step_id = step_row.id
              AND (decision.decided_by_user_id <> action.actor_user_id
                   OR decision.decided_by_person_id <>
                      action.actor_person_id
                   OR decision.decided_role_assignment_id <>
                      action.actor_role_assignment_id
                   OR decision.authorization_version <>
                      action.authorization_version
                   OR decision.decided_at IS DISTINCT FROM
                      action.occurred_at)
       )
       AND (
           (step_row.step_no = 1
            AND step_row.source_mode = 'internal'
            AND step_row.status = 'rejected'
            AND action.action = 'reject'
            AND action.source_mode = 'internal'
            AND command.operation = 'region_decide'
            AND command.request_jsonb->>'operation' = 'region_decide'
            AND command.request_jsonb->>'step_id' = step_row.id::text
            AND command.request_jsonb->>'step_no' = '1'
            AND command.request_jsonb->>'step_attempt_no' =
                step_row.attempt_no::text
            AND command.request_jsonb->>'action' = 'reject'
            AND command.result_jsonb->>'kind' = 'approval_decision'
            AND command.result_jsonb->>'decided_step_id' = step_row.id::text
            AND command.result_jsonb->>'decided_step_no' = '1'
            AND command.result_jsonb->>'decided_step_attempt_no' =
                step_row.attempt_no::text
            AND command.result_jsonb->>'decided_step_status' = 'rejected')
           OR (step_row.step_no = 2
               AND step_row.source_mode = 'internal'
               AND step_row.status = 'rejected'
               AND action.action = 'reject'
               AND action.source_mode = 'internal'
               AND command.operation = 'headquarters_decide'
               AND command.request_jsonb->>'operation' =
                   'headquarters_decide'
               AND command.request_jsonb->>'step_id' = step_row.id::text
               AND command.request_jsonb->>'step_no' = '2'
               AND command.request_jsonb->>'step_attempt_no' =
                   step_row.attempt_no::text
               AND command.request_jsonb->>'action' = 'reject'
               AND command.result_jsonb->>'kind' = 'approval_decision'
               AND command.result_jsonb->>'decided_step_id' = step_row.id::text
               AND command.result_jsonb->>'decided_step_no' = '2'
               AND command.result_jsonb->>'decided_step_attempt_no' =
                   step_row.attempt_no::text
               AND command.result_jsonb->>'decided_step_status' = 'rejected')
           OR (step_row.step_no = 3
               AND step_row.source_mode = 'external_registration'
               AND action.action = 'verify_external_accept'
               AND action.source_mode = 'external_registration'
               AND command.operation = 'verify_external'
               AND command.request_jsonb->>'operation' = 'verify_external'
               AND command.request_jsonb->>'verification_decision' = 'accept'
               AND command.request_jsonb->>'registration_id' =
                   command.result_jsonb->>'registration_id'
               AND command.result_jsonb->>'kind' = 'external_verification'
               AND command.result_jsonb->>'step_id' = step_row.id::text
               AND command.result_jsonb->>'step_attempt_no' =
                   step_row.attempt_no::text
               AND command.result_jsonb->>'step_status' = step_row.status
               AND command.result_jsonb->>'verification_decision' = 'accept')
       );
    IF matching_action_count <> 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF (SELECT count(*)
          FROM public.state_transition_events AS event
         WHERE event.aggregate_type = 'material_request'
           AND event.aggregate_id = request_row.id::text
           AND event.from_status = 'approval_in_progress'
           AND event.to_status = derived_request_status
           AND event.reason = CASE
               WHEN step_row.step_no = 3
                   THEN 'external_approval_completed'
               ELSE 'approval_rejected'
           END
           AND event.actor_id = terminal_actor_user_id
           AND event.occurred_at = instance_row.completed_at
           AND event.created_at = event.occurred_at
           AND event.idempotency_key =
               'mr:' || terminal_key_hash || ':' || 'request-' ||
               derived_request_status
           AND event.metadata_jsonb->>'request_id' = request_row.id::text
           AND event.metadata_jsonb->>'revision_id' =
               instance_row.request_revision_id::text
           AND event.metadata_jsonb->>'instance_id' = instance_row.id::text
           AND event.metadata_jsonb->>'step_id' = step_row.id::text
           AND event.metadata_jsonb->>'idempotency_key_hash' =
               terminal_key_hash) <> 1
       OR (SELECT count(*)
             FROM public.audit_events AS event
            WHERE event.stream_key = 'material_request'
              AND event.aggregate_type = 'material_request'
              AND event.aggregate_id = request_row.id::text
              AND event.action = CASE
                  WHEN step_row.step_no = 3
                      THEN 'material_request.external_evidence.verify_accept'
                  ELSE 'material_request.approval.reject'
              END
              AND event.actor_user_id = terminal_actor_user_id
              AND event.occurred_at = instance_row.completed_at
              AND event.request_id <> ''
              AND event.after_jsonb->>'request_id' = request_row.id::text
              AND event.after_jsonb->>'request_status' =
                  derived_request_status
              AND event.after_jsonb->>'request_version' =
                  expected_command_version::text
              AND event.after_jsonb->>'revision_id' =
                  instance_row.request_revision_id::text
              AND event.after_jsonb->>'instance_id' = instance_row.id::text
              AND event.after_jsonb->>'step_id' = step_row.id::text) <> 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
END
$$
"""


def _return_validator_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_RETURN_VALIDATE_FUNCTION}(
    checked_request_id uuid,
    checked_instance_id uuid
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    request_row public.material_requests%ROWTYPE;
    revision_row public.material_request_revisions%ROWTYPE;
    instance_row public.approval_instances%ROWTYPE;
    return_step_id uuid;
    return_step_attempt_no integer;
    return_command_version bigint;
    return_actor_user_id text;
    return_key_hash text;
    matching_count bigint;
BEGIN
    SELECT * INTO request_row FROM public.material_requests
     WHERE id = checked_request_id;
    SELECT * INTO instance_row FROM public.approval_instances
     WHERE id = checked_instance_id;
    SELECT * INTO revision_row FROM public.material_request_revisions
     WHERE id = instance_row.request_revision_id;
    IF request_row.id IS NULL
       OR instance_row.id IS NULL
       OR revision_row.id IS NULL
       OR instance_row.request_id <> request_row.id
       OR instance_row.status <> 'returned'
       OR instance_row.completed_at IS NULL
       OR instance_row.updated_at IS DISTINCT FROM instance_row.completed_at
       OR instance_row.current_step_no IS NOT NULL
       OR instance_row.current_step_id IS NOT NULL
       OR request_row.decided_at IS NOT NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT count(*), (array_agg(step.id ORDER BY step.id))[1],
           (array_agg(step.attempt_no ORDER BY step.id))[1]
      INTO matching_count, return_step_id, return_step_attempt_no
      FROM public.approval_steps AS step
     WHERE step.instance_id = instance_row.id
       AND step.step_no = 1
       AND step.source_mode = 'internal'
       AND step.status = 'returned'
       AND step.decided_at = instance_row.completed_at
       AND step.updated_at = instance_row.completed_at;
    IF matching_count <> 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT count(*), min(command.target_version),
           min(action.actor_user_id), min(command.idempotency_key_hash)
      INTO matching_count, return_command_version,
           return_actor_user_id, return_key_hash
      FROM public.approval_actions AS action
      JOIN public.material_request_commands AS command
        ON command.id = action.command_id
     WHERE action.instance_id = instance_row.id
       AND action.step_id = return_step_id
       AND action.action = 'return'
       AND action.source_mode = 'internal'
       AND command.request_id = request_row.id
       AND command.operation = 'region_decide'
       AND command.occurred_at = instance_row.completed_at
       AND command.created_at = command.occurred_at
       AND action.occurred_at = command.occurred_at
       AND action.created_at = command.created_at
       AND action.actor_user_id = command.actor_user_id
       AND action.actor_person_id = command.actor_person_id
       AND action.actor_role_assignment_id = command.actor_role_assignment_id
       AND action.authorization_version = command.authorization_version
       AND EXISTS (
           SELECT 1
             FROM public.approval_step_candidates AS candidate
            WHERE candidate.step_id = return_step_id
              AND candidate.user_id = action.actor_user_id
              AND candidate.person_id = action.actor_person_id
              AND candidate.role_assignment_id =
                  action.actor_role_assignment_id
              AND candidate.authorization_version =
                  action.authorization_version
              AND candidate.candidate_kind = 'assignee'
       )
       AND command.request_hash ~ '^[0-9a-f]{{64}}$'
       AND command.result_hash ~ '^[0-9a-f]{{64}}$'
       AND jsonb_typeof(command.request_jsonb) = 'object'
       AND jsonb_typeof(command.result_jsonb) = 'object'
       AND command.request_jsonb->>'schema' =
           'rsc.material_request_approval_command.v1'
       AND command.request_jsonb->>'operation' = 'region_decide'
       AND command.request_jsonb->>'request_id' = request_row.id::text
       AND command.request_jsonb->>'revision_id' = revision_row.id::text
       AND command.request_jsonb->>'revision_no' = revision_row.revision_no::text
       AND command.request_jsonb->>'instance_id' = instance_row.id::text
       AND command.request_jsonb->>'target_version' =
           command.target_version::text
       AND command.request_jsonb->>'payload_sha256' = command.request_hash
       AND command.request_jsonb->>'step_id' = return_step_id::text
       AND command.request_jsonb->>'step_no' = '1'
       AND command.request_jsonb->>'step_attempt_no' =
           return_step_attempt_no::text
       AND command.request_jsonb->>'action' = 'return'
       AND jsonb_typeof(command.request_jsonb->'return_lines') = 'array'
       AND command.result_jsonb->>'kind' = 'approval_decision'
       AND command.result_jsonb->>'request_id' = request_row.id::text
       AND command.result_jsonb->>'request_status' = 'returned'
       AND command.result_jsonb->>'request_version' =
           command.target_version::text
       AND command.result_jsonb->>'revision_id' = revision_row.id::text
       AND command.result_jsonb->>'revision_no' = revision_row.revision_no::text
       AND command.result_jsonb->>'instance_id' = instance_row.id::text
       AND command.result_jsonb->>'instance_status' = 'returned'
       AND command.result_jsonb->>'decided_step_id' = return_step_id::text
       AND command.result_jsonb->>'decided_step_no' = '1'
       AND command.result_jsonb->>'decided_step_attempt_no' =
           return_step_attempt_no::text
       AND command.result_jsonb->>'decided_step_status' = 'returned'
       AND NOT EXISTS (
           SELECT 1 FROM public.approval_return_line_facts AS fact
            WHERE fact.return_action_id = action.id
              AND (fact.actor_user_id <> action.actor_user_id
                   OR fact.actor_person_id <> action.actor_person_id
                   OR fact.actor_role_assignment_id <>
                      action.actor_role_assignment_id
                   OR fact.authorization_version <>
                      action.authorization_version
                   OR fact.occurred_at IS DISTINCT FROM action.occurred_at)
       );
    IF matching_count <> 1
       OR return_command_version IS NULL
       OR return_command_version > request_row.version THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF request_row.status = 'returned' THEN
        IF request_row.revision_no = revision_row.revision_no THEN
            IF request_row.version <> return_command_version
               OR request_row.updated_at IS DISTINCT FROM
                  instance_row.completed_at THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSIF request_row.revision_no = revision_row.revision_no + 1 THEN
            IF request_row.version <= return_command_version
               OR EXISTS (
                   SELECT 1 FROM public.material_request_commands AS command
                    WHERE command.request_id = request_row.id
                      AND command.target_version > return_command_version
                      AND command.operation <> 'update_draft'
               ) THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSE
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF request_row.status = 'cancelled' THEN
        IF request_row.version <= return_command_version
           OR EXISTS (
               SELECT 1 FROM public.material_request_commands AS command
                WHERE command.request_id = request_row.id
                  AND command.target_version > return_command_version
                  AND command.target_version < request_row.version
                  AND command.operation <> 'update_draft'
           )
           OR NOT EXISTS (
               SELECT 1 FROM public.material_request_commands AS command
                WHERE command.request_id = request_row.id
                  AND command.target_version = request_row.version
                  AND command.operation = 'cancel'
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF (SELECT count(*)
          FROM public.state_transition_events AS event
         WHERE event.aggregate_type = 'material_request'
           AND event.aggregate_id = request_row.id::text
           AND event.from_status = 'approval_in_progress'
           AND event.to_status = 'returned'
           AND event.reason = 'regional_approval_returned_to_requester'
           AND event.actor_id = return_actor_user_id
           AND event.occurred_at = instance_row.completed_at
           AND event.created_at = event.occurred_at
           AND event.idempotency_key =
               'mr:' || return_key_hash || ':' || 'request-returned'
           AND event.metadata_jsonb->>'request_id' = request_row.id::text
           AND event.metadata_jsonb->>'revision_id' = revision_row.id::text
           AND event.metadata_jsonb->>'instance_id' = instance_row.id::text
           AND event.metadata_jsonb->>'step_id' = return_step_id::text
           AND event.metadata_jsonb->>'idempotency_key_hash' =
               return_key_hash) <> 1
       OR (SELECT count(*)
             FROM public.audit_events AS event
            WHERE event.stream_key = 'material_request'
              AND event.aggregate_type = 'material_request'
              AND event.aggregate_id = request_row.id::text
              AND event.action = 'material_request.approval.return'
              AND event.actor_user_id = return_actor_user_id
              AND event.occurred_at = instance_row.completed_at
              AND event.request_id <> ''
              AND event.after_jsonb->>'request_id' = request_row.id::text
              AND event.after_jsonb->>'request_status' = 'returned'
              AND event.after_jsonb->>'request_version' =
                  return_command_version::text
              AND event.after_jsonb->>'revision_id' = revision_row.id::text
              AND event.after_jsonb->>'instance_id' = instance_row.id::text
              AND event.after_jsonb->>'step_id' = return_step_id::text) <> 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
END
$$
"""


def _external_validator_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_EXTERNAL_VALIDATE_FUNCTION}(
    checked_instance_id uuid
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    instance_row public.approval_instances%ROWTYPE;
    step_row public.approval_steps%ROWTYPE;
    registration_row record;
    matching_count bigint;
    register_command_version bigint;
    register_actor_user_id text;
    register_key_hash text;
    verify_command_version bigint;
    verify_actor_user_id text;
    verify_key_hash text;
    expected_verification_decision text;
    expected_verification_action text;
    expected_step_status text;
    expected_request_status text;
    expected_instance_status text;
BEGIN
    SELECT * INTO instance_row
      FROM public.approval_instances
     WHERE id = checked_instance_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.approval_steps AS step
         WHERE step.instance_id = checked_instance_id
           AND step.source_mode = 'external_registration'
           AND (
               (step.status = 'evidence_pending_verification' AND (
                   (SELECT count(*)
                      FROM public.approval_external_registrations AS registration
                     WHERE registration.step_id = step.id
                       AND registration.status = 'pending_verification') <> 1
                   OR EXISTS (
                       SELECT 1
                         FROM public.approval_external_registrations AS registration
                        WHERE registration.step_id = step.id
                          AND registration.status = 'accepted'
                   )
               ))
               OR (step.status IN (
                       'approved', 'partially_approved', 'rejected'
                   ) AND (SELECT count(*)
                            FROM public.approval_external_registrations
                                 AS registration
                           WHERE registration.step_id = step.id
                             AND registration.status = 'accepted'
                             AND registration.external_action = CASE step.status
                                 WHEN 'approved' THEN 'approve'
                                 WHEN 'partially_approved' THEN 'partial_approve'
                                 ELSE 'reject'
                             END) <> 1)
               OR (step.status = 'returned' AND (
                   SELECT count(*)
                     FROM public.approval_external_registrations AS registration
                    WHERE registration.step_id = step.id
                      AND registration.status = 'accepted'
                      AND registration.external_action = 'return'
               ) <> 1)
               OR (step.status IN (
                       'pending', 'open', 'awaiting_external_evidence',
                       'cancelled', 'superseded'
                   ) AND EXISTS (
                       SELECT 1
                         FROM public.approval_external_registrations AS registration
                        WHERE registration.step_id = step.id
                          AND registration.status IN (
                              'pending_verification', 'accepted'
                          )
                   ))
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.approval_actions AS action
          JOIN public.material_request_commands AS command
            ON command.id = action.command_id
         WHERE action.instance_id = checked_instance_id
           AND (
               (command.operation = 'register_external' AND (
                   action.action <> 'register_external_evidence'
                   OR action.source_mode <> 'external_registration'
                   OR action.step_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.approval_external_registrations
                              AS registration
                        WHERE registration.step_id = action.step_id
                          AND registration.id::text =
                              command.request_jsonb->>'registration_id'
                          AND registration.id::text =
                              command.result_jsonb->>'registration_id'
                   )
               ))
               OR (action.action = 'register_external_evidence' AND (
                   command.operation <> 'register_external'
                   OR action.source_mode <> 'external_registration'
                   OR action.step_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.approval_external_registrations
                              AS registration
                        WHERE registration.step_id = action.step_id
                          AND registration.id::text =
                              command.request_jsonb->>'registration_id'
                          AND registration.id::text =
                              command.result_jsonb->>'registration_id'
                   )
               ))
               OR (command.operation = 'verify_external' AND (
                   action.action NOT IN (
                       'verify_external_accept', 'verify_external_reject'
                   )
                   OR action.source_mode <> 'external_registration'
                   OR action.step_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.approval_external_registrations
                              AS registration
                        WHERE registration.step_id = action.step_id
                          AND registration.id::text =
                              command.request_jsonb->>'registration_id'
                          AND registration.id::text =
                              command.result_jsonb->>'registration_id'
                   )
               ))
               OR (action.action IN (
                       'verify_external_accept', 'verify_external_reject'
                   ) AND (
                   command.operation <> 'verify_external'
                   OR action.source_mode <> 'external_registration'
                   OR action.step_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                         FROM public.approval_external_registrations
                              AS registration
                        WHERE registration.step_id = action.step_id
                          AND registration.id::text =
                              command.request_jsonb->>'registration_id'
                          AND registration.id::text =
                              command.result_jsonb->>'registration_id'
                   )
               ))
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    FOR registration_row IN
        SELECT registration.*
          FROM public.approval_external_registrations AS registration
          JOIN public.approval_steps AS step
            ON step.id = registration.step_id
         WHERE step.instance_id = checked_instance_id
         ORDER BY registration.registered_at, registration.id
    LOOP
        SELECT * INTO step_row
          FROM public.approval_steps
         WHERE id = registration_row.step_id;
        IF step_row.id IS NULL
           OR step_row.instance_id <> instance_row.id
           OR step_row.step_no <> 3
           OR step_row.source_mode <> 'external_registration'
           OR step_row.predecessor_step_id IS NULL
           OR registration_row.created_at IS DISTINCT FROM
              registration_row.registered_at
           OR registration_row.external_decided_at >
              registration_row.registered_at
           OR step_row.opened_at IS NULL
           OR registration_row.external_decided_at < step_row.opened_at
           OR registration_row.status = 'superseded'
           OR (
               registration_row.external_action IN (
                   'approve', 'partial_approve', 'reject'
               )
               AND (
                   (SELECT count(*)
                      FROM public.approval_external_registration_lines AS line
                     WHERE line.registration_id = registration_row.id)
                   <>
                   (SELECT count(*)
                      FROM public.approval_step_line_decisions AS decision
                     WHERE decision.step_id = step_row.predecessor_step_id
                       AND decision.approved_qty > 0)
                   OR EXISTS (
                       SELECT 1
                         FROM public.approval_step_line_decisions AS decision
                        WHERE decision.step_id = step_row.predecessor_step_id
                          AND decision.approved_qty > 0
                          AND NOT EXISTS (
                              SELECT 1
                                FROM public.approval_external_registration_lines
                                     AS line
                               WHERE line.registration_id = registration_row.id
                                 AND line.request_line_id =
                                     decision.request_line_id
                                 AND line.input_qty = decision.approved_qty
                          )
                   )
                   OR EXISTS (
                       SELECT 1
                         FROM public.approval_external_registration_lines AS line
                        WHERE line.registration_id = registration_row.id
                          AND (
                              (registration_row.external_action = 'approve'
                               AND line.rejected_qty <> 0)
                              OR (
                                  registration_row.external_action = 'reject'
                                  AND (
                                      line.approved_qty <> 0
                                      OR line.rejected_qty <> line.input_qty
                                      OR btrim(line.reason) = ''
                                  )
                              )
                          )
                   )
                   OR (
                       registration_row.external_action = 'partial_approve'
                       AND (
                           NOT EXISTS (
                               SELECT 1
                                 FROM public.approval_external_registration_lines
                                      AS line
                                WHERE line.registration_id = registration_row.id
                                  AND line.approved_qty > 0
                           )
                           OR NOT EXISTS (
                               SELECT 1
                                 FROM public.approval_external_registration_lines
                                      AS line
                                WHERE line.registration_id = registration_row.id
                                  AND line.rejected_qty > 0
                           )
                       )
                   )
               )
           )
           OR (
               registration_row.external_action = 'return'
               AND EXISTS (
                   SELECT 1
                     FROM public.approval_external_registration_lines AS line
                    WHERE line.registration_id = registration_row.id
               )
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        SELECT count(*), min(command.target_version),
               min(action.actor_user_id), min(command.idempotency_key_hash)
          INTO matching_count, register_command_version,
               register_actor_user_id, register_key_hash
          FROM public.approval_actions AS action
          JOIN public.material_request_commands AS command
            ON command.id = action.command_id
         WHERE action.instance_id = instance_row.id
           AND action.step_id = step_row.id
           AND action.action = 'register_external_evidence'
           AND action.source_mode = 'external_registration'
           AND command.request_id = instance_row.request_id
           AND command.operation = 'register_external'
           AND command.occurred_at = registration_row.registered_at
           AND command.created_at = command.occurred_at
           AND action.occurred_at = command.occurred_at
           AND action.created_at = command.created_at
           AND action.actor_user_id = command.actor_user_id
           AND action.actor_person_id = command.actor_person_id
           AND action.actor_role_assignment_id =
               command.actor_role_assignment_id
           AND action.authorization_version = command.authorization_version
           AND action.actor_user_id = registration_row.registered_by_user_id
           AND action.actor_person_id = registration_row.registered_by_person_id
           AND action.actor_role_assignment_id =
               registration_row.registered_role_assignment_id
           AND action.authorization_version =
               registration_row.authorization_version
           AND EXISTS (
               SELECT 1
                 FROM public.approval_step_candidates AS candidate
                WHERE candidate.step_id = step_row.id
                  AND candidate.user_id = action.actor_user_id
                  AND candidate.person_id = action.actor_person_id
                  AND candidate.role_assignment_id =
                      action.actor_role_assignment_id
                  AND candidate.authorization_version =
                      action.authorization_version
                  AND candidate.candidate_kind = 'registrar'
           )
           AND command.request_hash ~ '^[0-9a-f]{{64}}$'
           AND command.result_hash ~ '^[0-9a-f]{{64}}$'
           AND command.request_jsonb->>'schema' =
               'rsc.material_request_approval_command.v1'
           AND command.request_jsonb->>'operation' = 'register_external'
           AND command.request_jsonb->>'request_id' =
               instance_row.request_id::text
           AND command.request_jsonb->>'revision_id' =
               instance_row.request_revision_id::text
           AND command.request_jsonb->>'revision_no' =
               instance_row.revision_no::text
           AND command.request_jsonb->>'instance_id' = instance_row.id::text
           AND command.request_jsonb->>'target_version' =
               command.target_version::text
           AND command.request_jsonb->>'payload_sha256' = command.request_hash
           AND command.request_jsonb->>'registration_id' =
               registration_row.id::text
           AND command.request_jsonb->>'external_action' =
               registration_row.external_action
           AND command.request_jsonb->>'decision_manifest_sha256' =
               registration_row.decision_manifest_sha256
           AND command.result_jsonb->>'kind' = 'external_registration'
           AND command.result_jsonb->>'request_id' =
               instance_row.request_id::text
           AND command.result_jsonb->>'request_status' =
               'approval_in_progress'
           AND command.result_jsonb->>'request_version' =
               command.target_version::text
           AND command.result_jsonb->>'revision_id' =
               instance_row.request_revision_id::text
           AND command.result_jsonb->>'revision_no' =
               instance_row.revision_no::text
           AND command.result_jsonb->>'instance_id' = instance_row.id::text
           AND command.result_jsonb->>'step_id' = step_row.id::text
           AND command.result_jsonb->>'step_attempt_no' =
               step_row.attempt_no::text
           AND command.result_jsonb->>'step_status' =
               'evidence_pending_verification'
           AND command.result_jsonb->>'registration_id' =
               registration_row.id::text
           AND command.result_jsonb->>'registration_no' =
               registration_row.registration_no
           AND command.result_jsonb->>'registration_status' =
               'pending_verification'
           AND command.result_jsonb->>'external_action' =
               registration_row.external_action
           AND command.result_jsonb->>'decision_manifest_sha256' =
               registration_row.decision_manifest_sha256;
        IF matching_count <> 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        IF (SELECT count(*)
              FROM public.state_transition_events AS event
             WHERE event.aggregate_type = 'approval_step'
               AND event.aggregate_id = step_row.id::text
               AND event.from_status = 'awaiting_external_evidence'
               AND event.to_status = 'evidence_pending_verification'
               AND event.reason = 'external_approval_evidence_registered'
               AND event.actor_id = register_actor_user_id
               AND event.occurred_at = registration_row.registered_at
               AND event.created_at = event.occurred_at
               AND event.idempotency_key =
                   'mr:' || register_key_hash || ':' ||
                   'evidence-pending-verification'
               AND event.metadata_jsonb->>'request_id' =
                   instance_row.request_id::text
               AND event.metadata_jsonb->>'revision_id' =
                   instance_row.request_revision_id::text
               AND event.metadata_jsonb->>'instance_id' = instance_row.id::text
               AND event.metadata_jsonb->>'step_id' = step_row.id::text
               AND event.metadata_jsonb->>'registration_id' =
                   registration_row.id::text
               AND event.metadata_jsonb->>'idempotency_key_hash' =
                   register_key_hash) <> 1
           OR (SELECT count(*)
                 FROM public.audit_events AS event
                WHERE event.stream_key = 'material_request'
                  AND event.aggregate_type = 'material_request'
                  AND event.aggregate_id = instance_row.request_id::text
                  AND event.action =
                      'material_request.external_evidence.register'
                  AND event.actor_user_id = register_actor_user_id
                  AND event.occurred_at = registration_row.registered_at
                  AND event.request_id <> ''
                  AND event.after_jsonb->>'request_id' =
                      instance_row.request_id::text
                  AND event.after_jsonb->>'request_version' =
                      register_command_version::text
                  AND event.after_jsonb->>'revision_id' =
                      instance_row.request_revision_id::text
                  AND event.after_jsonb->>'instance_id' =
                      instance_row.id::text
                  AND event.after_jsonb->>'step_id' = step_row.id::text
                  AND event.after_jsonb->>'registration_id' =
                      registration_row.id::text
                  AND event.after_jsonb->>'registration_status' =
                      'pending_verification'
                  AND event.after_jsonb->>'external_action' =
                      registration_row.external_action
                  AND event.after_jsonb->>'decision_manifest_sha256' =
                      registration_row.decision_manifest_sha256) <> 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        IF registration_row.status = 'pending_verification' THEN
            IF registration_row.version <> 0
               OR registration_row.updated_at IS DISTINCT FROM
                  registration_row.registered_at
               OR instance_row.status <> 'active'
               OR instance_row.current_step_id <> step_row.id
               OR step_row.status <> 'evidence_pending_verification'
               OR EXISTS (
                   SELECT 1
                     FROM public.approval_actions AS action
                     JOIN public.material_request_commands AS command
                       ON command.id = action.command_id
                    WHERE action.instance_id = instance_row.id
                      AND action.step_id = step_row.id
                      AND command.request_jsonb->>'registration_id' =
                          registration_row.id::text
                      AND command.operation = 'verify_external'
               ) THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
            CONTINUE;
        END IF;

        IF registration_row.status = 'rejected' THEN
            expected_verification_decision := 'reject';
            expected_verification_action := 'verify_external_reject';
            expected_step_status := 'awaiting_external_evidence';
            expected_request_status := 'approval_in_progress';
            expected_instance_status := 'active';
        ELSIF registration_row.status = 'accepted' THEN
            expected_verification_decision := 'accept';
            expected_verification_action := 'verify_external_accept';
            expected_step_status := CASE registration_row.external_action
                WHEN 'approve' THEN 'approved'
                WHEN 'partial_approve' THEN 'partially_approved'
                WHEN 'reject' THEN 'rejected'
                ELSE 'returned'
            END;
            IF registration_row.external_action = 'approve' THEN
                expected_request_status := CASE WHEN EXISTS (
                    SELECT 1
                      FROM public.material_request_lines AS line
                      LEFT JOIN public.approval_external_registration_lines
                           AS registered_line
                        ON registered_line.registration_id = registration_row.id
                       AND registered_line.request_line_id = line.id
                     WHERE line.request_id = instance_row.request_id
                       AND line.revision_id = instance_row.request_revision_id
                       AND COALESCE(registered_line.approved_qty, 0) <>
                           line.requested_qty
                ) THEN 'partially_approved' ELSE 'approved' END;
            ELSE
                expected_request_status := CASE registration_row.external_action
                    WHEN 'partial_approve' THEN 'partially_approved'
                    WHEN 'reject' THEN 'rejected'
                    ELSE 'approval_in_progress'
                END;
            END IF;
            expected_instance_status := CASE registration_row.external_action
                WHEN 'approve' THEN 'completed'
                WHEN 'partial_approve' THEN 'completed'
                WHEN 'reject' THEN 'rejected'
                ELSE 'active'
            END;
        ELSE
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        SELECT count(*), min(command.target_version),
               min(action.actor_user_id), min(command.idempotency_key_hash)
          INTO matching_count, verify_command_version,
               verify_actor_user_id, verify_key_hash
          FROM public.approval_actions AS action
          JOIN public.material_request_commands AS command
            ON command.id = action.command_id
         WHERE action.instance_id = instance_row.id
           AND action.step_id = step_row.id
           AND action.action = expected_verification_action
           AND action.source_mode = 'external_registration'
           AND command.request_id = instance_row.request_id
           AND command.operation = 'verify_external'
           AND command.target_version = register_command_version + 1
           AND command.occurred_at = registration_row.verified_at
           AND command.created_at = command.occurred_at
           AND action.occurred_at = command.occurred_at
           AND action.created_at = command.created_at
           AND action.actor_user_id = command.actor_user_id
           AND action.actor_person_id = command.actor_person_id
           AND action.actor_role_assignment_id =
               command.actor_role_assignment_id
           AND action.authorization_version = command.authorization_version
           AND action.actor_user_id = registration_row.verified_by_user_id
           AND action.actor_person_id = registration_row.verified_by_person_id
           AND action.actor_role_assignment_id =
               registration_row.verified_role_assignment_id
           AND action.authorization_version =
               registration_row.verified_authorization_version
           AND EXISTS (
               SELECT 1
                 FROM public.approval_step_candidates AS candidate
                WHERE candidate.step_id = step_row.id
                  AND candidate.user_id = action.actor_user_id
                  AND candidate.person_id = action.actor_person_id
                  AND candidate.role_assignment_id =
                      action.actor_role_assignment_id
                  AND candidate.authorization_version =
                      action.authorization_version
                  AND candidate.candidate_kind = 'verifier'
           )
           AND command.request_hash ~ '^[0-9a-f]{{64}}$'
           AND command.result_hash ~ '^[0-9a-f]{{64}}$'
           AND command.request_jsonb->>'schema' =
               'rsc.material_request_approval_command.v1'
           AND command.request_jsonb->>'operation' = 'verify_external'
           AND command.request_jsonb->>'request_id' =
               instance_row.request_id::text
           AND command.request_jsonb->>'revision_id' =
               instance_row.request_revision_id::text
           AND command.request_jsonb->>'revision_no' =
               instance_row.revision_no::text
           AND command.request_jsonb->>'instance_id' = instance_row.id::text
           AND command.request_jsonb->>'target_version' =
               command.target_version::text
           AND command.request_jsonb->>'payload_sha256' = command.request_hash
           AND command.request_jsonb->>'registration_id' =
               registration_row.id::text
           AND command.request_jsonb->>'verification_decision' =
               expected_verification_decision
           AND command.request_jsonb->>'registration_manifest_sha256' =
               registration_row.decision_manifest_sha256
           AND command.result_jsonb->>'kind' = 'external_verification'
           AND command.result_jsonb->>'request_id' =
               instance_row.request_id::text
           AND command.result_jsonb->>'request_status' =
               expected_request_status
           AND command.result_jsonb->>'request_version' =
               command.target_version::text
           AND command.result_jsonb->>'revision_id' =
               instance_row.request_revision_id::text
           AND command.result_jsonb->>'revision_no' =
               instance_row.revision_no::text
           AND command.result_jsonb->>'instance_id' = instance_row.id::text
           AND command.result_jsonb->>'instance_status' =
               expected_instance_status
           AND command.result_jsonb->>'step_id' = step_row.id::text
           AND command.result_jsonb->>'step_attempt_no' =
               step_row.attempt_no::text
           AND command.result_jsonb->>'step_status' = expected_step_status
           AND command.result_jsonb->>'registration_id' =
               registration_row.id::text
           AND command.result_jsonb->>'registration_status' =
               registration_row.status
           AND command.result_jsonb->>'verification_decision' =
               expected_verification_decision;
        IF matching_count <> 1
           OR registration_row.version <> 1
           OR registration_row.updated_at IS DISTINCT FROM
              registration_row.verified_at THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        IF registration_row.status = 'accepted'
           AND registration_row.external_action IN (
               'approve', 'partial_approve', 'reject'
           ) THEN
            IF (SELECT count(*)
                  FROM public.approval_step_line_decisions AS decision
                 WHERE decision.step_id = step_row.id
                   AND decision.external_registration_id = registration_row.id
                   AND decision.decision_source = 'external_registration'
                   AND decision.decided_by_user_id = verify_actor_user_id
                   AND decision.decided_by_person_id =
                       registration_row.verified_by_person_id
                   AND decision.decided_role_assignment_id =
                       registration_row.verified_role_assignment_id
                   AND decision.authorization_version =
                       registration_row.verified_authorization_version
                   AND decision.decided_at = registration_row.verified_at
                   AND decision.created_at = decision.decided_at)
                <>
                (SELECT count(*)
                   FROM public.approval_external_registration_lines AS line
                  WHERE line.registration_id = registration_row.id)
               OR EXISTS (
                   SELECT 1
                     FROM public.approval_external_registration_lines AS line
                    WHERE line.registration_id = registration_row.id
                      AND NOT EXISTS (
                          SELECT 1
                            FROM public.approval_step_line_decisions AS decision
                           WHERE decision.step_id = step_row.id
                             AND decision.external_registration_id =
                                 registration_row.id
                             AND decision.request_line_id = line.request_line_id
                             AND decision.input_qty = line.input_qty
                             AND decision.approved_qty = line.approved_qty
                             AND decision.rejected_qty = line.rejected_qty
                             AND decision.reason = line.reason
                      )
               ) THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSIF EXISTS (
            SELECT 1
              FROM public.approval_step_line_decisions AS decision
             WHERE decision.external_registration_id = registration_row.id
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        IF (SELECT count(*)
              FROM public.state_transition_events AS event
             WHERE event.aggregate_type = 'approval_step'
               AND event.aggregate_id = step_row.id::text
               AND event.from_status = 'evidence_pending_verification'
               AND event.to_status = expected_step_status
               AND event.reason = CASE expected_verification_decision
                   WHEN 'reject' THEN 'external_approval_evidence_rejected'
                   ELSE 'external_approval_evidence_accepted'
               END
               AND event.actor_id = verify_actor_user_id
               AND event.occurred_at = registration_row.verified_at
               AND event.created_at = event.occurred_at
               AND event.idempotency_key =
                   'mr:' || verify_key_hash || ':' ||
                   'external-verification-' ||
                   expected_verification_decision
               AND event.metadata_jsonb->>'request_id' =
                   instance_row.request_id::text
               AND event.metadata_jsonb->>'revision_id' =
                   instance_row.request_revision_id::text
               AND event.metadata_jsonb->>'instance_id' = instance_row.id::text
               AND event.metadata_jsonb->>'step_id' = step_row.id::text
               AND event.metadata_jsonb->>'registration_id' =
                   registration_row.id::text
               AND event.metadata_jsonb->>'idempotency_key_hash' =
                   verify_key_hash) <> 1
           OR (SELECT count(*)
                 FROM public.audit_events AS event
                WHERE event.stream_key = 'material_request'
                  AND event.aggregate_type = 'material_request'
                  AND event.aggregate_id = instance_row.request_id::text
                  AND event.action =
                      'material_request.external_evidence.verify_' ||
                      expected_verification_decision
                  AND event.actor_user_id = verify_actor_user_id
                  AND event.occurred_at = registration_row.verified_at
                  AND event.request_id <> ''
                  AND event.after_jsonb->>'request_id' =
                      instance_row.request_id::text
                  AND event.after_jsonb->>'request_version' =
                      verify_command_version::text
                  AND event.after_jsonb->>'revision_id' =
                      instance_row.request_revision_id::text
                  AND event.after_jsonb->>'instance_id' =
                      instance_row.id::text
                  AND event.after_jsonb->>'step_id' = step_row.id::text
                  AND event.after_jsonb->>'registration_id' =
                      registration_row.id::text
                  AND event.after_jsonb->>'registration_status' =
                      registration_row.status
                  AND event.after_jsonb->>'external_action' =
                      registration_row.external_action
                  AND event.after_jsonb->>'verification_decision' =
                      expected_verification_decision) <> 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END LOOP;
END
$$
"""


def _projection_validator_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_PROJECTION_VALIDATE_FUNCTION}(checked_request_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    request_row public.material_requests%ROWTYPE;
    revision_row public.material_request_revisions%ROWTYPE;
    latest_instance public.approval_instances%ROWTYPE;
    instance_count bigint;
    minimum_attempt integer;
    maximum_attempt integer;
    command_count bigint;
    minimum_target_version bigint;
    maximum_target_version bigint;
    terminal_step_count bigint;
    terminal_step_id uuid;
    approval_instance_id uuid;
BEGIN
    SELECT * INTO request_row
      FROM public.material_requests
     WHERE id = checked_request_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT * INTO revision_row
      FROM public.material_request_revisions
     WHERE request_id = checked_request_id
       AND revision_no = request_row.revision_no;
    IF NOT FOUND
       OR EXISTS (
           SELECT 1 FROM public.material_request_revisions AS newer
            WHERE newer.request_id = checked_request_id
              AND newer.revision_no > request_row.revision_no
       )
       OR request_row.work_order_id IS DISTINCT FROM revision_row.work_order_id
       OR request_row.purpose IS DISTINCT FROM revision_row.purpose
       OR request_row.urgency IS DISTINCT FROM revision_row.urgency
       OR request_row.expected_date IS DISTINCT FROM revision_row.expected_date
       OR request_row.address_snapshot_jsonb
          IS DISTINCT FROM revision_row.address_snapshot_jsonb
       OR request_row.address_masked_jsonb
          IS DISTINCT FROM revision_row.address_masked_jsonb
       OR request_row.contact_snapshot_jsonb
          IS DISTINCT FROM revision_row.contact_snapshot_jsonb
       OR request_row.contact_masked_jsonb
          IS DISTINCT FROM revision_row.contact_masked_jsonb
       OR request_row.note IS DISTINCT FROM revision_row.note
       OR request_row.approval_mode IS DISTINCT FROM revision_row.approval_mode
       OR NOT EXISTS (
           SELECT 1 FROM public.material_request_lines AS line
            WHERE line.request_id = checked_request_id
              AND line.revision_id = revision_row.id
              AND line.revision_no = revision_row.revision_no
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT count(*), min(target_version), max(target_version)
      INTO command_count, minimum_target_version, maximum_target_version
      FROM public.material_request_commands
     WHERE request_id = checked_request_id;
    IF command_count <> request_row.version + 1
       OR minimum_target_version <> 0
       OR maximum_target_version <> request_row.version
       OR (SELECT count(*)
             FROM public.material_request_commands AS command
            WHERE command.request_id = checked_request_id
              AND command.target_version = request_row.version
              AND command.occurred_at = request_row.updated_at
              AND command.created_at = command.occurred_at
              AND command.request_jsonb->>'request_id' =
                  checked_request_id::text
              AND command.request_jsonb->>'target_version' =
                  request_row.version::text) <> 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT count(*), min(attempt_no), max(attempt_no)
      INTO instance_count, minimum_attempt, maximum_attempt
      FROM public.approval_instances
     WHERE request_id = checked_request_id;
    IF instance_count > 0 THEN
        SELECT * INTO latest_instance
          FROM public.approval_instances
         WHERE request_id = checked_request_id
         ORDER BY attempt_no DESC
         LIMIT 1;
        IF minimum_attempt <> 1 OR maximum_attempt <> instance_count
           OR latest_instance.attempt_no <> maximum_attempt
           OR EXISTS (
               SELECT 1 FROM public.approval_instances AS stale
                WHERE stale.request_id = checked_request_id
                  AND stale.id <> latest_instance.id
                  AND stale.status = 'active'
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        PERFORM public.{PG_APPROVAL_VALIDATE_FUNCTION_0030}(latest_instance.id);
        FOR approval_instance_id IN
            SELECT instance.id
              FROM public.approval_instances AS instance
             WHERE instance.request_id = checked_request_id
             ORDER BY instance.attempt_no
        LOOP
            PERFORM public.{PG_EXTERNAL_VALIDATE_FUNCTION}(
                approval_instance_id
            );
        END LOOP;
        IF EXISTS (
            SELECT 1
              FROM public.approval_instances AS instance
              CROSS JOIN LATERAL (
                  SELECT count(*) FILTER (
                             WHERE action.action <> 'cancel'
                         ) AS lifecycle_action_count,
                         min(action.occurred_at) FILTER (
                             WHERE action.action = 'submit'
                         ) AS submitted_at,
                         max(action.occurred_at) FILTER (
                             WHERE action.action <> 'cancel'
                         ) AS last_lifecycle_action_at
                    FROM public.approval_actions AS action
                   WHERE action.instance_id = instance.id
              ) AS history
             WHERE instance.request_id = checked_request_id
               AND (
                   history.lifecycle_action_count = 0
                   OR instance.version <>
                      history.lifecycle_action_count - 1 +
                      CASE instance.status WHEN 'superseded' THEN 1 ELSE 0 END
                   OR instance.created_at IS DISTINCT FROM history.submitted_at
                   OR (instance.status <> 'superseded'
                       AND instance.updated_at IS DISTINCT FROM
                           history.last_lifecycle_action_at)
                   OR (instance.status = 'superseded' AND NOT EXISTS (
                       SELECT 1
                         FROM public.approval_instances AS successor
                        WHERE successor.request_id = instance.request_id
                          AND successor.attempt_no = instance.attempt_no + 1
                          AND successor.created_at = instance.updated_at
                   ))
               )
        ) OR EXISTS (
            SELECT 1
              FROM public.approval_steps AS step
              JOIN public.approval_instances AS instance
                ON instance.id = step.instance_id
              CROSS JOIN LATERAL (
                  SELECT count(*) AS step_action_count,
                         max(action.occurred_at) AS last_step_action_at
                    FROM public.approval_actions AS action
                   WHERE action.step_id = step.id
              ) AS history
             WHERE instance.request_id = checked_request_id
               AND (
                   step.version <> CASE
                       WHEN step.status = 'cancelled' THEN
                           CASE
                               WHEN step.step_no = 1 AND step.attempt_no = 1
                                   THEN history.step_action_count - 1
                               WHEN step.opened_at IS NULL THEN 0
                               ELSE history.step_action_count + 1
                           END + 1
                       WHEN step.step_no = 1 AND step.attempt_no = 1
                           THEN history.step_action_count - 1
                       WHEN step.opened_at IS NULL THEN 0
                       ELSE history.step_action_count + 1
                   END
                   OR (step.status = 'pending'
                       AND step.updated_at IS DISTINCT FROM step.created_at)
                   OR (step.status = 'superseded' AND NOT EXISTS (
                       SELECT 1
                         FROM public.approval_steps AS successor
                        WHERE successor.supersedes_step_id = step.id
                          AND successor.instance_id = step.instance_id
                          AND successor.created_at = step.updated_at
                   ))
                   OR (step.status = 'cancelled' AND NOT EXISTS (
                       SELECT 1
                         FROM public.approval_actions AS action
                        WHERE action.instance_id = step.instance_id
                          AND action.action = 'withdraw'
                          AND action.occurred_at = step.updated_at
                   ))
                   OR (step.status IN (
                           'open', 'awaiting_external_evidence'
                       ) AND step.updated_at IS DISTINCT FROM COALESCE(
                           history.last_step_action_at, step.opened_at
                       ))
                   OR (step.status IN (
                           'evidence_pending_verification', 'approved',
                           'partially_approved', 'rejected', 'returned'
                       ) AND step.updated_at IS DISTINCT FROM
                           history.last_step_action_at)
               )
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM public.approval_step_line_decisions AS decision
              JOIN public.approval_steps AS step
                ON step.id = decision.step_id
             WHERE step.instance_id = latest_instance.id
               AND step.status NOT IN (
                   'approved', 'partially_approved', 'rejected'
               )
        ) OR EXISTS (
            SELECT 1
              FROM public.approval_return_line_facts AS fact
              JOIN public.approval_steps AS step
                ON step.id = fact.returned_from_step_id
             WHERE fact.instance_id = latest_instance.id
               AND step.status <> 'returned'
        ) OR EXISTS (
            SELECT 1
             FROM public.material_request_commands AS command
             WHERE command.request_id = checked_request_id
               AND command.operation IN (
                   'submit', 'region_decide', 'headquarters_decide',
                   'register_external', 'verify_external',
                   'withdraw', 'cancel'
               )
               AND (SELECT count(*)
                      FROM public.approval_actions AS action
                     WHERE action.command_id = command.id) <> 1
        ) OR EXISTS (
            SELECT 1
              FROM public.approval_actions AS action
              JOIN public.approval_steps AS step
                ON step.id = action.step_id
               AND step.instance_id = action.instance_id
             WHERE action.instance_id = latest_instance.id
               AND (
                   (action.action = 'approve'
                    AND step.status <> 'approved')
                   OR (action.action = 'partial_approve'
                       AND step.status <> 'partially_approved')
                   OR (action.action = 'reject'
                       AND step.status <> 'rejected')
                   OR (action.action = 'return'
                       AND step.status <> 'returned')
                   OR (action.action = 'verify_external_accept'
                       AND step.status NOT IN (
                           'approved', 'partially_approved',
                           'rejected', 'returned'
                       ))
               )
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;

    IF request_row.status = 'draft' THEN
        IF revision_row.status <> 'draft' OR revision_row.revision_no <> 1
           OR revision_row.previous_revision_id IS NOT NULL
           OR instance_count <> 0
           OR EXISTS (
               SELECT 1 FROM public.material_request_lines AS line
                WHERE line.request_id = checked_request_id
                  AND line.revision_id = revision_row.id
                  AND (line.status <> 'draft'
                       OR line.final_approved_qty <> 0
                       OR line.cancelled_qty <> 0
                       OR line.version <> 0
                       OR line.updated_at IS DISTINCT FROM line.created_at)
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN;
    END IF;

    IF instance_count = 0 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF request_row.status IN ('submitted', 'approval_in_progress') THEN
        IF revision_row.status <> 'sealed'
           OR latest_instance.status <> 'active'
           OR latest_instance.request_revision_id <> revision_row.id
           OR latest_instance.revision_no <> revision_row.revision_no
           OR latest_instance.completed_at IS NOT NULL
           OR EXISTS (
               SELECT 1 FROM public.material_request_lines AS line
                WHERE line.request_id = checked_request_id
                  AND line.revision_id = revision_row.id
                  AND (line.status <> 'approval_pending'
                       OR line.final_approved_qty <> 0
                       OR line.cancelled_qty <> 0
                       OR line.version <> 1
                       OR line.updated_at IS DISTINCT FROM
                           latest_instance.created_at)
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN;
    END IF;

    IF request_row.status = 'returned' THEN
        IF latest_instance.status <> 'returned'
           OR latest_instance.completed_at IS NULL
           OR latest_instance.current_step_no IS NOT NULL
           OR latest_instance.current_step_id IS NOT NULL
           OR NOT (
               (revision_row.status = 'sealed'
                AND latest_instance.request_revision_id = revision_row.id
                AND latest_instance.revision_no = revision_row.revision_no)
               OR (revision_row.status = 'draft'
                   AND revision_row.revision_no = latest_instance.revision_no + 1
                   AND revision_row.previous_revision_id =
                       latest_instance.request_revision_id)
           )
           OR EXISTS (
               SELECT 1 FROM public.material_request_lines AS line
                WHERE line.request_id = checked_request_id
                  AND line.revision_id = revision_row.id
                  AND (line.status <> CASE revision_row.status
                                          WHEN 'draft' THEN 'draft'
                                          ELSE 'approval_pending'
                                      END
                       OR line.final_approved_qty <> 0
                       OR line.cancelled_qty <> 0
                       OR line.version <> CASE revision_row.status
                           WHEN 'draft' THEN 0 ELSE 1 END
                       OR line.updated_at IS DISTINCT FROM CASE
                           WHEN revision_row.status = 'draft'
                               THEN line.created_at
                           ELSE latest_instance.created_at
                       END)
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        PERFORM public.{PG_RETURN_VALIDATE_FUNCTION}(
            checked_request_id, latest_instance.id
        );
        RETURN;
    END IF;

    IF request_row.status = 'withdrawn' THEN
        IF revision_row.status <> 'sealed'
           OR latest_instance.status <> 'withdrawn'
           OR latest_instance.request_revision_id <> revision_row.id
           OR latest_instance.revision_no <> revision_row.revision_no
           OR latest_instance.completed_at IS NULL
           OR request_row.withdrawn_at IS DISTINCT FROM latest_instance.completed_at
           OR request_row.updated_at IS DISTINCT FROM request_row.withdrawn_at
           OR latest_instance.updated_at IS DISTINCT FROM request_row.withdrawn_at
           OR latest_instance.current_step_no IS NOT NULL
           OR latest_instance.current_step_id IS NOT NULL
           OR NOT EXISTS (
               SELECT 1 FROM public.approval_steps AS step
                WHERE step.instance_id = latest_instance.id
                  AND step.status = 'cancelled'
                  AND step.updated_at = request_row.withdrawn_at
           )
           OR EXISTS (
               SELECT 1 FROM public.approval_steps AS step
                WHERE step.instance_id = latest_instance.id
                  AND step.status = 'cancelled'
                  AND step.updated_at IS DISTINCT FROM request_row.withdrawn_at
           )
           OR (SELECT count(*)
                 FROM public.material_request_commands AS command
                WHERE command.request_id = checked_request_id
                  AND command.operation = 'withdraw') <> 1
           OR (SELECT count(*)
                 FROM public.material_request_commands AS command
                 JOIN public.approval_actions AS action
                   ON action.command_id = command.id
                 JOIN public.users AS actor_user
                   ON actor_user.id = command.actor_user_id
                 JOIN public.people AS actor_person
                   ON actor_person.id = command.actor_person_id
                 JOIN public.role_assignments AS assignment
                   ON assignment.id = command.actor_role_assignment_id
                 JOIN public.roles AS role
                   ON role.id = assignment.role_id
                WHERE command.request_id = checked_request_id
                  AND command.operation = 'withdraw'
                  AND command.target_version = request_row.version
                  AND command.request_reference =
                      '/api/v1/material-requests/' || checked_request_id::text ||
                      '/withdraw'
                  AND command.actor_user_id = request_row.requester_user_id
                  AND command.actor_person_id = request_row.requester_person_id
                  AND command.occurred_at = request_row.withdrawn_at
                  AND command.created_at = command.occurred_at
                  AND command.request_hash ~ '^[0-9a-f]{{64}}$'
                  AND command.result_hash ~ '^[0-9a-f]{{64}}$'
                  AND jsonb_typeof(command.request_jsonb) = 'object'
                  AND (SELECT count(*)
                         FROM jsonb_object_keys(command.request_jsonb)) = 8
                  AND command.request_jsonb ?& ARRAY[
                      'schema','operation','request_id','revision_id',
                      'revision_no','target_version','payload_sha256',
                      'sensitive_fields'
                  ]
                  AND command.request_jsonb->>'schema' =
                      'rsc.material_request_lifecycle_command.v1'
                  AND command.request_jsonb->>'operation' = 'withdraw'
                  AND command.request_jsonb->>'request_id' =
                      checked_request_id::text
                  AND command.request_jsonb->>'revision_id' =
                      revision_row.id::text
                  AND command.request_jsonb->>'revision_no' =
                      revision_row.revision_no::text
                  AND command.request_jsonb->>'target_version' =
                      request_row.version::text
                  AND command.request_jsonb->>'payload_sha256' =
                      command.request_hash
                  AND command.request_jsonb->>'sensitive_fields' = 'excluded'
                  AND jsonb_typeof(command.request_jsonb->'revision_no') =
                      'number'
                  AND jsonb_typeof(command.request_jsonb->'target_version') =
                      'number'
                  AND jsonb_typeof(command.result_jsonb) = 'object'
                  AND (SELECT count(*)
                         FROM jsonb_object_keys(command.result_jsonb)) = 13
                  AND command.result_jsonb->>'kind' = 'lifecycle'
                  AND command.result_jsonb->>'schema_version' = '1.0'
                  AND command.result_jsonb->>'request_id' =
                      checked_request_id::text
                  AND command.result_jsonb->>'request_no' =
                      request_row.request_no
                  AND command.result_jsonb->>'action' = 'withdraw'
                  AND command.result_jsonb->>'request_status' = 'withdrawn'
                  AND command.result_jsonb->>'request_version' =
                      request_row.version::text
                  AND command.result_jsonb->>'revision_id' =
                      revision_row.id::text
                  AND command.result_jsonb->>'revision_no' =
                      revision_row.revision_no::text
                  AND command.result_jsonb->>'approval_instance_id' =
                      latest_instance.id::text
                  AND command.result_jsonb->>'approval_attempt_no' =
                      latest_instance.attempt_no::text
                  AND command.result_jsonb->'current_step_id' = 'null'::jsonb
                  AND command.result_jsonb->'state_axes' = jsonb_build_object(
                      'allocation_status', request_row.allocation_status,
                      'reservation_status', request_row.reservation_status,
                      'outbound_status', request_row.outbound_status,
                      'shipment_status', request_row.shipment_status,
                      'logistics_signature_status',
                          request_row.logistics_signature_status,
                      'oam_receipt_status', request_row.oam_receipt_status,
                      'personal_inbound_status',
                          request_row.personal_inbound_status,
                      'notification_status', request_row.notification_status,
                      'reconciliation_status',
                          request_row.reconciliation_status
                  )
                  AND action.instance_id = latest_instance.id
                  AND action.step_id IS NULL
                  AND action.action = 'withdraw'
                  AND action.source_mode = 'internal'
                  AND btrim(action.comment) <> ''
                  AND length(action.comment) <= 4000
                  AND action.actor_user_id = command.actor_user_id
                  AND action.actor_person_id = command.actor_person_id
                  AND action.actor_role_assignment_id =
                      command.actor_role_assignment_id
                  AND action.authorization_version =
                      command.authorization_version
                  AND action.occurred_at = command.occurred_at
                  AND action.created_at = command.created_at
                  AND actor_user.person_id = actor_person.id
                  AND actor_user.account_status = 'active'
                  AND actor_person.employment_status = 'active'
                  AND actor_user.authorization_version =
                      command.authorization_version
                  AND assignment.user_id = actor_user.id
                  AND assignment.scope_type = 'person'
                  AND assignment.scope_id = actor_person.id::text
                  AND assignment.status = 'active'
                  AND assignment.valid_from <= command.occurred_at
                  AND (assignment.valid_to IS NULL
                       OR assignment.valid_to > command.occurred_at)
                  AND role.code = 'technician'
                  AND role.status = 'active') <> 1
           OR (SELECT count(*)
                 FROM public.state_transition_events AS event
                WHERE event.aggregate_type = 'material_request'
                  AND event.aggregate_id = checked_request_id::text
                  AND event.from_status IN ('submitted',
                                            'approval_in_progress')
                  AND event.to_status = 'withdrawn'
                  AND event.reason =
                      'material_request_withdrawn_by_requester'
                  AND event.actor_id = request_row.requester_user_id
                  AND event.occurred_at = request_row.withdrawn_at
                  AND event.created_at = event.occurred_at
                  AND jsonb_typeof(event.metadata_jsonb) = 'object'
                  AND event.metadata_jsonb->>'request_id' =
                      checked_request_id::text
                  AND event.metadata_jsonb->>'request_no' =
                      request_row.request_no
                  AND event.metadata_jsonb->>'revision_id' =
                      revision_row.id::text
                  AND event.metadata_jsonb->>'revision_no' =
                      revision_row.revision_no::text
                  AND EXISTS (
                      SELECT 1
                        FROM public.material_request_commands AS command
                       WHERE command.request_id = checked_request_id
                         AND command.operation = 'withdraw'
                         AND command.target_version = request_row.version
                         AND event.metadata_jsonb->>'idempotency_key_hash' =
                             command.idempotency_key_hash
                         AND event.idempotency_key =
                             'mr:' || command.idempotency_key_hash || ':' ||
                             'withdrawn'
                  )) <> 1
           OR (SELECT count(*)
                 FROM public.audit_events AS event
                WHERE event.stream_key = 'material_request'
                  AND event.action = 'material_request.withdraw'
                  AND event.aggregate_type = 'material_request'
                  AND event.aggregate_id = checked_request_id::text
                  AND event.actor_user_id = request_row.requester_user_id
                  AND event.occurred_at = request_row.withdrawn_at
                  AND event.request_id <> ''
                  AND jsonb_typeof(event.after_jsonb) = 'object'
                  AND event.after_jsonb->>'request_id' =
                      checked_request_id::text
                  AND event.after_jsonb->>'request_no' =
                      request_row.request_no
                  AND event.after_jsonb->>'status' = 'withdrawn'
                  AND event.after_jsonb->>'version' =
                      request_row.version::text
                  AND event.after_jsonb->>'revision_id' =
                      revision_row.id::text
                  AND event.after_jsonb->>'revision_no' =
                      revision_row.revision_no::text
                  AND event.after_jsonb->>'approval_instance_id' =
                      latest_instance.id::text) <> 1
           OR EXISTS (
               SELECT 1 FROM public.material_request_lines AS line
                WHERE line.request_id = checked_request_id
                  AND line.revision_id = revision_row.id
                  AND (line.status <> 'approval_pending'
                       OR line.final_approved_qty <> 0
                       OR line.cancelled_qty <> 0
                       OR line.version <> 1
                       OR line.updated_at IS DISTINCT FROM
                           latest_instance.created_at)
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN;
    END IF;

    IF request_row.status IN ('approved', 'partially_approved', 'rejected') THEN
        IF revision_row.status <> 'sealed'
           OR latest_instance.status <> (
              CASE request_row.status WHEN 'rejected' THEN 'rejected'
                                      ELSE 'completed' END
           )
           OR latest_instance.request_revision_id <> revision_row.id
           OR latest_instance.revision_no <> revision_row.revision_no
           OR latest_instance.completed_at IS NULL
           OR request_row.decided_at IS DISTINCT FROM latest_instance.completed_at
           OR latest_instance.current_step_no IS NOT NULL
           OR latest_instance.current_step_id IS NOT NULL THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        SELECT count(*), (array_agg(step.id ORDER BY step.id))[1]
          INTO terminal_step_count, terminal_step_id
          FROM public.approval_steps AS step
         WHERE step.instance_id = latest_instance.id
           AND step.decided_at = latest_instance.completed_at
           AND (
               (latest_instance.status = 'completed'
                AND step.step_no = 3
                AND step.status IN ('approved', 'partially_approved'))
               OR (latest_instance.status = 'rejected'
                   AND step.status = 'rejected')
           );
        IF terminal_step_count <> 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        PERFORM public.{PG_TERMINAL_VALIDATE_FUNCTION}(
            checked_request_id,
            latest_instance.id,
            terminal_step_id,
            request_row.version
        );
        IF EXISTS (
            WITH RECURSIVE causal_steps AS (
                SELECT step.id, step.predecessor_step_id, step.step_no
                  FROM public.approval_steps AS step
                 WHERE step.id = terminal_step_id
                UNION ALL
                SELECT predecessor.id, predecessor.predecessor_step_id,
                       predecessor.step_no
                  FROM public.approval_steps AS predecessor
                  JOIN causal_steps AS successor
                    ON predecessor.id = successor.predecessor_step_id
            )
            SELECT 1
              FROM public.material_request_lines AS line
              LEFT JOIN LATERAL (
                  SELECT decision.approved_qty
                    FROM causal_steps AS causal
                    JOIN public.approval_step_line_decisions AS decision
                      ON decision.step_id = causal.id
                   WHERE decision.request_line_id = line.id
                   ORDER BY causal.step_no DESC
                   LIMIT 1
              ) AS derived ON true
             WHERE line.request_id = checked_request_id
               AND line.revision_id = revision_row.id
               AND (
                   derived.approved_qty IS NULL
                   OR line.final_approved_qty <> derived.approved_qty
                   OR line.cancelled_qty <> 0
                   OR line.version <> 2
                   OR line.updated_at IS DISTINCT FROM
                       latest_instance.completed_at
                   OR line.status <> CASE
                       WHEN line.final_approved_qty = 0 THEN 'rejected'
                       WHEN line.final_approved_qty = line.requested_qty
                           THEN 'approved'
                       ELSE 'partially_approved'
                   END
               )
        )
        OR (request_row.status = 'approved' AND EXISTS (
            SELECT 1 FROM public.material_request_lines AS line
             WHERE line.request_id = checked_request_id
               AND line.revision_id = revision_row.id
               AND line.final_approved_qty <> line.requested_qty
        ))
        OR (request_row.status = 'rejected' AND EXISTS (
            SELECT 1 FROM public.material_request_lines AS line
             WHERE line.request_id = checked_request_id
               AND line.revision_id = revision_row.id
               AND line.final_approved_qty <> 0
        ))
        OR (request_row.status = 'partially_approved' AND (
            NOT EXISTS (
                SELECT 1 FROM public.material_request_lines AS line
                 WHERE line.request_id = checked_request_id
                   AND line.revision_id = revision_row.id
                   AND line.final_approved_qty > 0
            )
            OR NOT EXISTS (
                SELECT 1 FROM public.material_request_lines AS line
                 WHERE line.request_id = checked_request_id
                   AND line.revision_id = revision_row.id
                   AND line.final_approved_qty < line.requested_qty
            )
        )) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN;
    END IF;

    IF request_row.status = 'cancelled' THEN
        IF latest_instance.status = 'returned' THEN
            IF latest_instance.completed_at IS NULL
               OR request_row.decided_at IS NOT NULL
               OR latest_instance.current_step_no IS NOT NULL
               OR latest_instance.current_step_id IS NOT NULL
               OR NOT (
                   (revision_row.status = 'sealed'
                    AND latest_instance.request_revision_id = revision_row.id
                    AND latest_instance.revision_no = revision_row.revision_no)
                   OR (revision_row.status = 'draft'
                       AND revision_row.revision_no =
                           latest_instance.revision_no + 1
                       AND revision_row.previous_revision_id =
                           latest_instance.request_revision_id)
               )
               OR EXISTS (
                   SELECT 1 FROM public.material_request_lines AS line
                    WHERE line.request_id = checked_request_id
                      AND line.revision_id = revision_row.id
                      AND (line.status <> 'cancelled'
                           OR line.final_approved_qty <> 0
                           OR line.cancelled_qty <> 0
                           OR line.version <> CASE revision_row.status
                               WHEN 'draft' THEN 1 ELSE 2 END
                           OR line.updated_at IS DISTINCT FROM
                               request_row.cancelled_at)
               ) THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
            PERFORM public.{PG_RETURN_VALIDATE_FUNCTION}(
                checked_request_id, latest_instance.id
            );
            RETURN;
        END IF;
        IF revision_row.status <> 'sealed'
           OR latest_instance.status <> 'completed'
           OR latest_instance.request_revision_id <> revision_row.id
           OR latest_instance.revision_no <> revision_row.revision_no
           OR latest_instance.completed_at IS NULL
           OR request_row.decided_at IS DISTINCT FROM latest_instance.completed_at
           OR latest_instance.current_step_no IS NOT NULL
           OR latest_instance.current_step_id IS NOT NULL THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        SELECT count(*), (array_agg(step.id ORDER BY step.id))[1]
          INTO terminal_step_count, terminal_step_id
          FROM public.approval_steps AS step
         WHERE step.instance_id = latest_instance.id
           AND step.decided_at = latest_instance.completed_at
           AND step.step_no = 3
           AND step.status IN ('approved', 'partially_approved');
        IF terminal_step_count <> 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        PERFORM public.{PG_TERMINAL_VALIDATE_FUNCTION}(
            checked_request_id,
            latest_instance.id,
            terminal_step_id,
            request_row.version - 1
        );
        IF EXISTS (
            WITH RECURSIVE causal_steps AS (
                SELECT step.id, step.predecessor_step_id, step.step_no
                  FROM public.approval_steps AS step
                 WHERE step.id = terminal_step_id
                UNION ALL
                SELECT predecessor.id, predecessor.predecessor_step_id,
                       predecessor.step_no
                  FROM public.approval_steps AS predecessor
                  JOIN causal_steps AS successor
                    ON predecessor.id = successor.predecessor_step_id
            )
            SELECT 1
              FROM public.material_request_lines AS line
              LEFT JOIN LATERAL (
                  SELECT decision.approved_qty
                    FROM causal_steps AS causal
                    JOIN public.approval_step_line_decisions AS decision
                      ON decision.step_id = causal.id
                   WHERE decision.request_line_id = line.id
                   ORDER BY causal.step_no DESC
                   LIMIT 1
              ) AS derived ON true
             WHERE line.request_id = checked_request_id
               AND line.revision_id = revision_row.id
               AND (derived.approved_qty IS NULL
                    OR line.final_approved_qty <> derived.approved_qty
                    OR line.status <> 'cancelled'
                    OR line.cancelled_qty <> line.final_approved_qty
                    OR line.version <> 3
                    OR line.updated_at IS DISTINCT FROM
                        request_row.cancelled_at)
        ) OR NOT EXISTS (
            SELECT 1 FROM public.material_request_lines AS line
             WHERE line.request_id = checked_request_id
               AND line.revision_id = revision_row.id
               AND line.final_approved_qty > 0
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN;
    END IF;

    RAISE EXCEPTION '{GUARD_ERROR}';
END
$$
"""


def _projection_dispatcher_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_PROJECTION_DISPATCH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    checked_request_id uuid;
    checked_instance_id uuid;
    checked_step_id uuid;
    checked_aggregate_type text;
    checked_aggregate_id text;
BEGIN
    IF TG_TABLE_NAME = 'material_requests' THEN
        checked_request_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_TABLE_NAME IN (
        'material_request_revisions',
        'material_request_lines',
        'material_request_commands',
        'approval_instances',
        'approval_return_line_facts'
    ) THEN
        checked_request_id := COALESCE(NEW.request_id, OLD.request_id);
    ELSIF TG_TABLE_NAME IN ('approval_steps', 'approval_actions') THEN
        checked_instance_id := COALESCE(NEW.instance_id, OLD.instance_id);
        SELECT instance.request_id INTO checked_request_id
          FROM public.approval_instances AS instance
         WHERE instance.id = checked_instance_id;
    ELSIF TG_TABLE_NAME IN (
        'approval_step_line_decisions',
        'approval_external_registrations'
    ) THEN
        checked_step_id := COALESCE(NEW.step_id, OLD.step_id);
        SELECT instance.request_id INTO checked_request_id
          FROM public.approval_steps AS step
          JOIN public.approval_instances AS instance
            ON instance.id = step.instance_id
         WHERE step.id = checked_step_id;
    ELSIF TG_TABLE_NAME = 'approval_external_registration_lines' THEN
        SELECT instance.request_id INTO checked_request_id
          FROM public.approval_external_registrations AS registration
          JOIN public.approval_steps AS step
            ON step.id = registration.step_id
          JOIN public.approval_instances AS instance
            ON instance.id = step.instance_id
         WHERE registration.id = COALESCE(
             NEW.registration_id, OLD.registration_id
         );
    ELSIF TG_TABLE_NAME IN ('state_transition_events', 'audit_events') THEN
        checked_aggregate_type := COALESCE(
            NEW.aggregate_type, OLD.aggregate_type
        );
        checked_aggregate_id := COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        IF checked_aggregate_type = 'material_request' THEN
            SELECT request.id INTO checked_request_id
              FROM public.material_requests AS request
             WHERE request.id::text = checked_aggregate_id;
        ELSIF checked_aggregate_type = 'approval_step' THEN
            SELECT instance.request_id INTO checked_request_id
              FROM public.approval_steps AS step
              JOIN public.approval_instances AS instance
                ON instance.id = step.instance_id
             WHERE step.id::text = checked_aggregate_id;
        END IF;
    END IF;
    IF checked_request_id IS NOT NULL THEN
        PERFORM public.{PG_PROJECTION_VALIDATE_FUNCTION}(checked_request_id);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$
"""
