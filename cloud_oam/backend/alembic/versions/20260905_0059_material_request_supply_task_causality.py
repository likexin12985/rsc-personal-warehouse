"""Activate bounded, causal shortage-supply planning commands.

Revision ID: 20260905_0059
Revises: 20260905_0058
Create Date: 2026-09-05

Supply tasks are planning facts only.  This migration does not create an
allocation, reservation, outbound, shipment, receipt, inbound, notification,
reconciliation or inventory-ledger fact.

The historical 0029 quantity guard reads sibling supply tasks before the 0037
request-parent trigger takes its row lock.  A new alphabetically-first trigger
therefore takes the request owner lock and validates the bounded state machine
before the historical guard rechecks the aggregate.  The 0045 terminal and
projection validators are repaired in-place, under exact body hashes, so the
completed approval command remains the immutable approval anchor while later
supply commands advance the request command/version sequence.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260905_0059"
down_revision: Union[str, Sequence[str], None] = "20260905_0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PREVIOUS_SCHEMA_REVISION = "20260905_0058"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"
GUARD_ERROR = "formal material request supply projection is invalid"
CATALOG_ERROR = "0059 material request supply catalog verification failed"
UPGRADE_BLOCKER = "0059 requires an empty supply-task command graph"
DOWNGRADE_BLOCKER = "cannot downgrade 0059 while supply-task facts exist"

OWNER_GUARD_FUNCTION = "rsc_guard_material_request_supply_task_0059"
SUPPLY_VALIDATE_FUNCTION = "rsc_validate_material_request_supply_causality_0059"
SUPPLY_DISPATCH_FUNCTION = "rsc_dispatch_material_request_supply_causality_0059"
OWNER_GUARD_TRIGGER = "trg_supply_tasks_00_owner_guard_0059"
SUPPLY_TRIGGER_TABLES = (
    "audit_events",
    "material_request_commands",
    "material_requests",
    "state_transition_events",
    "supply_tasks",
)
SUPPLY_TRIGGER_BINDINGS = tuple(
    (table_name, f"trg_{table_name}_supply_causality_0059")
    for table_name in SUPPLY_TRIGGER_TABLES
)

TERMINAL_FUNCTION = "rsc_validate_material_request_terminal_causality_0045"
TERMINAL_SIGNATURE = f"public.{TERMINAL_FUNCTION}(uuid, uuid, uuid, bigint)"
PROJECTION_FUNCTION = "rsc_validate_material_request_approval_projection_0045"
PROJECTION_SIGNATURE = f"public.{PROJECTION_FUNCTION}(uuid)"
TERMINAL_BODY_SHA256_0045 = (
    "5934dcd4d93d6b5a0e127f8cd019b102303c1b85dbd9b50c171000f4ca42bcbe"
)
PROJECTION_BODY_SHA256_0045 = (
    "1b08d93ca30dda0446528533543243cb94a91dad153bb56893bf89223af05b63"
)
# Filled from the deterministic source replacements below.  Kept explicit so
# production readiness can pin the exact installed function bodies.
TERMINAL_BODY_SHA256_0059 = (
    "4f7ce45088d9005b70d17418b6677344be11c6bdc4a2026d0c24f979083d2a47"
)
PROJECTION_BODY_SHA256_0059 = (
    "b51b11c63f1ec0e5659a9a1b1d1cdce03b32c5c9b604b5c1cdf669781c9b6ca4"
)

RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"
RUNTIME_READY_BODY_SHA256_0058 = (
    "194c166aa7eeca78e70b9ab9376f060457d7e9715fd9bead34b3cf1703f32907"
)
RUNTIME_READY_BODY_SHA256_0059 = (
    "21859de4675a39652ffe7cda14887c2d644b05d208d32e361ecfad7f9dd4b963"
)

TERMINAL_LEGACY_FRAGMENT = """       OR (request_row.status <> 'cancelled' AND (
           request_row.status <> derived_request_status
           OR request_row.version <> expected_command_version
           OR request_row.updated_at IS DISTINCT FROM instance_row.completed_at
       )) THEN"""
TERMINAL_FIXED_FRAGMENT = """       OR (request_row.status <> 'cancelled' AND (
           request_row.status <> derived_request_status
           OR request_row.version < expected_command_version
           OR (request_row.version = expected_command_version
               AND request_row.updated_at IS DISTINCT FROM
                   instance_row.completed_at)
       )) THEN"""

PROJECTION_DECLARATION_LEGACY = """    terminal_step_count bigint;
    terminal_step_id uuid;
    approval_instance_id uuid;"""
PROJECTION_DECLARATION_FIXED = """    terminal_step_count bigint;
    terminal_step_id uuid;
    terminal_command_count bigint;
    terminal_command_version bigint;
    approval_instance_id uuid;"""
PROJECTION_TERMINAL_CALL_LEGACY = """        IF terminal_step_count <> 1 THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        PERFORM public.rsc_validate_material_request_terminal_causality_0045(
            checked_request_id,
            latest_instance.id,
            terminal_step_id,
            request_row.version
        );"""
PROJECTION_TERMINAL_CALL_FIXED = """        IF terminal_step_count <> 1 THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        SELECT count(*), min(command.target_version)
          INTO terminal_command_count, terminal_command_version
          FROM public.approval_actions AS action
          JOIN public.material_request_commands AS command
            ON command.id = action.command_id
         WHERE action.instance_id = latest_instance.id
           AND action.step_id = terminal_step_id
           AND action.action = 'verify_external_accept'
           AND command.operation = 'verify_external';
        IF terminal_command_count <> 1 THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        PERFORM public.rsc_validate_material_request_terminal_causality_0045(
            checked_request_id,
            latest_instance.id,
            terminal_step_id,
            terminal_command_version
        );"""
PROJECTION_CANCEL_TERMINAL_CALL_LEGACY = """        PERFORM public.\
rsc_validate_material_request_terminal_causality_0045(
            checked_request_id,
            latest_instance.id,
            terminal_step_id,
            request_row.version - 1
        );"""
PROJECTION_CANCEL_TERMINAL_CALL_FIXED = """        SELECT count(*), min(command.target_version)
          INTO terminal_command_count, terminal_command_version
          FROM public.approval_actions AS action
          JOIN public.material_request_commands AS command
            ON command.id = action.command_id
         WHERE action.instance_id = latest_instance.id
           AND action.step_id = terminal_step_id
           AND action.action = 'verify_external_accept'
           AND command.operation = 'verify_external';
        IF terminal_command_count <> 1 THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        PERFORM public.rsc_validate_material_request_terminal_causality_0045(
            checked_request_id,
            latest_instance.id,
            terminal_step_id,
            terminal_command_version
        );
        IF EXISTS (
            SELECT 1
              FROM public.material_request_commands AS command
             WHERE command.request_id = checked_request_id
               AND command.operation IN (
                   'create_supply_task', 'update_supply_task',
                   'cancel_supply_task'
               )
        ) THEN
            PERFORM public.rsc_validate_material_request_supply_causality_0059(
                checked_request_id, terminal_command_version
            );
        END IF;"""
PROJECTION_APPROVED_RETURN_LEGACY = """        )) THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        RETURN;
    END IF;

    IF request_row.status = 'cancelled' THEN"""
PROJECTION_APPROVED_RETURN_FIXED = """        )) THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM public.material_request_commands AS command
             WHERE command.request_id = checked_request_id
               AND command.operation IN (
                   'create_supply_task', 'update_supply_task',
                   'cancel_supply_task'
               )
        ) THEN
            PERFORM public.rsc_validate_material_request_supply_causality_0059(
                checked_request_id, terminal_command_version
            );
        ELSIF terminal_command_version <> request_row.version THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        RETURN;
    END IF;

    IF request_row.status = 'cancelled' THEN"""

LOCK_TABLES = (
    "alembic_version",
    "approval_actions",
    "approval_instances",
    "approval_step_line_decisions",
    "approval_steps",
    "audit_events",
    "material_request_commands",
    "material_request_lines",
    "material_request_revisions",
    "material_requests",
    "people",
    "permissions",
    "role_assignments",
    "role_permissions",
    "roles",
    "state_transition_events",
    "supply_tasks",
    "users",
)


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        _require_empty_sqlite_supply_graph(UPGRADE_BLOCKER)
        _create_sqlite_guards()
        return

    _lock_execution_boundary()
    _require_empty_supply_graph(UPGRADE_BLOCKER)
    _replace_function_source(
        signature=TERMINAL_SIGNATURE,
        expected_hash=TERMINAL_BODY_SHA256_0045,
        replacement_hash=TERMINAL_BODY_SHA256_0059,
        replacements=((TERMINAL_LEGACY_FRAGMENT, TERMINAL_FIXED_FRAGMENT),),
    )
    op.execute(_owner_guard_sql())
    op.execute(_supply_validator_sql())
    op.execute(_supply_dispatcher_sql())
    _create_postgresql_triggers()
    _replace_function_source(
        signature=PROJECTION_SIGNATURE,
        expected_hash=PROJECTION_BODY_SHA256_0045,
        replacement_hash=PROJECTION_BODY_SHA256_0059,
        replacements=(
            (PROJECTION_DECLARATION_LEGACY, PROJECTION_DECLARATION_FIXED),
            (PROJECTION_TERMINAL_CALL_LEGACY, PROJECTION_TERMINAL_CALL_FIXED),
            (PROJECTION_APPROVED_RETURN_LEGACY, PROJECTION_APPROVED_RETURN_FIXED),
            (
                PROJECTION_CANCEL_TERMINAL_CALL_LEGACY,
                PROJECTION_CANCEL_TERMINAL_CALL_FIXED,
            ),
        ),
    )
    _grant_runtime_supply_dml()
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0058,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0059,
        old_revision=PREVIOUS_SCHEMA_REVISION,
        new_revision=revision,
    )
    _verify_postgresql_catalog()


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        _require_empty_sqlite_supply_graph(DOWNGRADE_BLOCKER)
        _drop_sqlite_guards()
        return

    _lock_execution_boundary()
    _require_empty_supply_graph(DOWNGRADE_BLOCKER)
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0059,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0058,
        old_revision=revision,
        new_revision=PREVIOUS_SCHEMA_REVISION,
    )
    op.execute(
        f"REVOKE INSERT ON TABLE public.supply_tasks FROM {PRODUCTION_API_ROLE}"
    )
    op.execute(
        "REVOKE UPDATE (reference_no, expected_date, status, "
        "cancelled_by_user_id, cancelled_at, version, updated_at) "
        f"ON TABLE public.supply_tasks FROM {PRODUCTION_API_ROLE}"
    )
    for table_name, trigger_name in reversed(SUPPLY_TRIGGER_BINDINGS):
        op.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
    op.execute(f"DROP TRIGGER {OWNER_GUARD_TRIGGER} ON public.supply_tasks")
    _replace_function_source(
        signature=PROJECTION_SIGNATURE,
        expected_hash=PROJECTION_BODY_SHA256_0059,
        replacement_hash=PROJECTION_BODY_SHA256_0045,
        replacements=(
            (PROJECTION_APPROVED_RETURN_FIXED, PROJECTION_APPROVED_RETURN_LEGACY),
            (
                PROJECTION_CANCEL_TERMINAL_CALL_FIXED,
                PROJECTION_CANCEL_TERMINAL_CALL_LEGACY,
            ),
            (PROJECTION_TERMINAL_CALL_FIXED, PROJECTION_TERMINAL_CALL_LEGACY),
            (PROJECTION_DECLARATION_FIXED, PROJECTION_DECLARATION_LEGACY),
        ),
    )
    op.execute(
        f"DROP FUNCTION public.{SUPPLY_DISPATCH_FUNCTION}(), "
        f"public.{SUPPLY_VALIDATE_FUNCTION}(uuid, bigint), "
        f"public.{OWNER_GUARD_FUNCTION}()"
    )
    _replace_function_source(
        signature=TERMINAL_SIGNATURE,
        expected_hash=TERMINAL_BODY_SHA256_0059,
        replacement_hash=TERMINAL_BODY_SHA256_0045,
        replacements=((TERMINAL_FIXED_FRAGMENT, TERMINAL_LEGACY_FRAGMENT),),
    )


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0059 supports only PostgreSQL and SQLite")
    return dialect


def _lock_execution_boundary() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _require_empty_supply_graph(blocker: str) -> None:
    if blocker not in {UPGRADE_BLOCKER, DOWNGRADE_BLOCKER}:
        raise ValueError("unsupported 0059 supply graph blocker")
    op.execute(
        f"""
DO $rsc_0059_empty$
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: migration role mismatch';
    END IF;
    IF EXISTS (SELECT 1 FROM public.supply_tasks)
       OR EXISTS (
           SELECT 1 FROM public.material_request_commands
            WHERE operation IN (
                'create_supply_task','update_supply_task','cancel_supply_task'
            )
       ) OR EXISTS (
           SELECT 1 FROM public.audit_events
            WHERE action IN (
                'material_request.supply_task.create',
                'material_request.supply_task.update',
                'material_request.supply_task.cancel'
            )
       ) OR EXISTS (
           SELECT 1 FROM public.state_transition_events
            WHERE aggregate_type = 'supply_task'
       ) THEN
        RAISE EXCEPTION '{blocker}';
    END IF;
END
$rsc_0059_empty$
"""
    )


def _require_empty_sqlite_supply_graph(blocker: str) -> None:
    if blocker not in {UPGRADE_BLOCKER, DOWNGRADE_BLOCKER}:
        raise ValueError("unsupported 0059 supply graph blocker")
    has_supply_facts = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM supply_tasks) "
            "OR EXISTS (SELECT 1 FROM material_request_commands "
            "WHERE operation IN ('create_supply_task', 'update_supply_task', "
            "'cancel_supply_task')) "
            "OR EXISTS (SELECT 1 FROM audit_events WHERE action IN ("
            "'material_request.supply_task.create', "
            "'material_request.supply_task.update', "
            "'material_request.supply_task.cancel')) "
            "OR EXISTS (SELECT 1 FROM state_transition_events "
            "WHERE aggregate_type = 'supply_task')"
        )
    ).scalar_one()
    if bool(has_supply_facts):
        raise RuntimeError(blocker)


def _owner_guard_sql() -> str:
    return f"""
CREATE FUNCTION public.{OWNER_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    target_request_id uuid;
    target_request_status text;
    target_line public.material_request_lines%ROWTYPE;
    active_total numeric(18,3);
    actor_match_count bigint;
    task_count bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.request_line_id <> OLD.request_line_id THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT line.request_id INTO target_request_id
      FROM public.material_request_lines AS line
     WHERE line.id = NEW.request_line_id;
    IF target_request_id IS NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT request.status INTO target_request_status
      FROM public.material_requests AS request
     WHERE request.id = target_request_id
       FOR UPDATE;
    IF NOT FOUND OR target_request_status NOT IN ('approved','partially_approved') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT line.* INTO target_line
      FROM public.material_request_lines AS line
      JOIN public.material_requests AS request ON request.id = line.request_id
     WHERE line.id = NEW.request_line_id
       AND line.revision_no = request.revision_no
       AND line.status IN ('approved','partially_approved')
       FOR UPDATE OF line;
    IF NOT FOUND OR target_line.final_approved_qty - target_line.cancelled_qty <= 0
       OR NEW.substitution_decision_id IS NOT NULL
       OR NEW.expected_qty <> NEW.original_equivalent_qty
       OR pg_catalog.btrim(NEW.task_no) = ''
       OR (NEW.reference_no IS NOT NULL
           AND pg_catalog.btrim(NEW.reference_no) = '') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.material_requests AS request
         WHERE request.id = target_request_id
           AND (request.allocation_status <> 'not_allocated'
                OR request.reservation_status <> 'not_reserved'
                OR request.outbound_status <> 'not_started'
                OR request.shipment_status <> 'not_started'
                OR request.logistics_signature_status <> 'not_signed'
                OR request.oam_receipt_status <> 'not_occurred'
                OR request.personal_inbound_status <> 'not_started'
                OR request.notification_status <> 'not_started'
                OR request.reconciliation_status <> 'not_started')
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.version <> 0 OR NEW.created_at IS DISTINCT FROM NEW.updated_at
           OR NEW.status NOT IN ('open','reference_registered')
           OR (NEW.status = 'open' AND NEW.reference_no IS NOT NULL)
           OR (NEW.status = 'reference_registered' AND NEW.reference_no IS NULL)
           OR NEW.cancelled_by_user_id IS NOT NULL OR NEW.cancelled_at IS NOT NULL THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        SELECT count(*) INTO actor_match_count
          FROM public.users AS actor_user
          JOIN public.people AS actor_person
            ON actor_person.id = actor_user.person_id
          JOIN public.role_assignments AS assignment
            ON assignment.id = NEW.created_role_assignment_id
           AND assignment.user_id = actor_user.id
          JOIN public.roles AS role
            ON role.id = assignment.role_id
          JOIN public.role_permissions AS role_permission
            ON role_permission.role_id = role.id
          JOIN public.permissions AS permission
            ON permission.id = role_permission.permission_id
         WHERE actor_user.id = NEW.created_by_user_id
           AND actor_person.id = NEW.created_by_person_id
           AND actor_user.account_status = 'active'
           AND actor_person.employment_status = 'active'
           AND actor_user.authorization_version = NEW.authorization_version
           AND role.code = 'admin' AND role.status = 'active'
           AND assignment.scope_type = 'national' AND assignment.scope_id = '*'
           AND assignment.status = 'active'
           AND assignment.valid_from <= NEW.created_at
           AND (assignment.valid_to IS NULL OR assignment.valid_to > NEW.created_at)
           AND permission.resource = 'supply_task'
           AND permission.action = 'manage' AND permission.field_code = '';
        IF actor_match_count <> 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.task_no IS DISTINCT FROM OLD.task_no
           OR NEW.request_line_id IS DISTINCT FROM OLD.request_line_id
           OR NEW.substitution_decision_id IS DISTINCT FROM OLD.substitution_decision_id
           OR NEW.supply_type IS DISTINCT FROM OLD.supply_type
           OR NEW.expected_qty IS DISTINCT FROM OLD.expected_qty
           OR NEW.original_equivalent_qty IS DISTINCT FROM OLD.original_equivalent_qty
           OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
           OR NEW.created_by_person_id IS DISTINCT FROM OLD.created_by_person_id
           OR NEW.created_role_assignment_id IS DISTINCT FROM OLD.created_role_assignment_id
           OR NEW.authorization_version IS DISTINCT FROM OLD.authorization_version
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.version <> OLD.version + 1
           OR NEW.updated_at <= OLD.updated_at
           OR OLD.status IN ('cancelled','closed_no_supply')
           OR NOT (
               (OLD.status = 'open' AND NEW.status IN (
                   'open','reference_registered','awaiting_supply',
                   'cancelled','closed_no_supply'))
               OR (OLD.status = 'reference_registered' AND NEW.status IN (
                   'reference_registered','awaiting_supply',
                   'cancelled','closed_no_supply'))
               OR (OLD.status = 'awaiting_supply' AND NEW.status IN (
                   'awaiting_supply','cancelled','closed_no_supply'))
           )
           OR (NEW.status = 'reference_registered' AND NEW.reference_no IS NULL)
           OR (NEW.status = 'cancelled' AND (
               NEW.cancelled_by_user_id IS NULL OR NEW.cancelled_at IS NULL
               OR NEW.cancelled_at IS DISTINCT FROM NEW.updated_at
               OR NEW.reference_no IS DISTINCT FROM OLD.reference_no
               OR NEW.expected_date IS DISTINCT FROM OLD.expected_date))
           OR (NEW.status <> 'cancelled' AND (
               NEW.cancelled_by_user_id IS NOT NULL OR NEW.cancelled_at IS NOT NULL)) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;

    SELECT count(*) INTO task_count
      FROM public.supply_tasks AS task
      JOIN public.material_request_lines AS line ON line.id = task.request_line_id
     WHERE line.request_id = target_request_id;
    IF task_count > 10000 OR (TG_OP = 'INSERT' AND task_count >= 10000) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT COALESCE(pg_catalog.sum(task.original_equivalent_qty), 0)
      INTO active_total
      FROM public.supply_tasks AS task
     WHERE task.request_line_id = NEW.request_line_id
       AND task.status NOT IN ('cancelled','closed_no_supply')
       AND (TG_OP = 'INSERT' OR task.id <> NEW.id);
    IF NEW.status NOT IN ('cancelled','closed_no_supply') THEN
        active_total := active_total + NEW.original_equivalent_qty;
    END IF;
    IF active_total > target_line.final_approved_qty - target_line.cancelled_qty THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""


def _supply_validator_sql() -> str:
    return f"""
CREATE FUNCTION public.{SUPPLY_VALIDATE_FUNCTION}(
    checked_request_id uuid,
    approval_command_version bigint
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
    task_row public.supply_tasks%ROWTYPE;
    command_row public.material_request_commands%ROWTYPE;
    previous_result jsonb;
    previous_status text;
    supply_request_status text;
    supply_ceiling_version bigint;
    command_count bigint;
    task_count bigint;
    task_command_count bigint;
    state_count bigint;
    audit_count bigint;
    minimum_version bigint;
    maximum_version bigint;
BEGIN
    SELECT * INTO request_row FROM public.material_requests
     WHERE id = checked_request_id FOR UPDATE;
    IF NOT FOUND OR request_row.status NOT IN (
           'approved','partially_approved','cancelled')
       OR request_row.allocation_status <> 'not_allocated'
       OR request_row.reservation_status <> 'not_reserved'
       OR request_row.outbound_status <> 'not_started'
       OR request_row.shipment_status <> 'not_started'
       OR request_row.logistics_signature_status <> 'not_signed'
       OR request_row.oam_receipt_status <> 'not_occurred'
       OR request_row.personal_inbound_status <> 'not_started'
       OR request_row.notification_status <> 'not_started'
       OR request_row.reconciliation_status <> 'not_started' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF request_row.status = 'cancelled' THEN
        supply_ceiling_version := request_row.version - 1;
        IF (SELECT count(*) FROM public.material_request_commands AS command
             WHERE command.request_id = checked_request_id
               AND command.target_version = request_row.version
               AND command.operation = 'cancel') <> 1
           OR EXISTS (
               SELECT 1
                 FROM public.supply_tasks AS task
                 JOIN public.material_request_lines AS line
                   ON line.id = task.request_line_id
                WHERE line.request_id = checked_request_id
                  AND task.status NOT IN ('cancelled','closed_no_supply')
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        supply_ceiling_version := request_row.version;
    END IF;
    IF supply_ceiling_version <= approval_command_version THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT * INTO revision_row FROM public.material_request_revisions
     WHERE request_id = checked_request_id
       AND revision_no = request_row.revision_no;
    SELECT * INTO instance_row FROM public.approval_instances
     WHERE request_id = checked_request_id ORDER BY attempt_no DESC LIMIT 1;
    IF revision_row.id IS NULL OR revision_row.status <> 'sealed'
       OR instance_row.id IS NULL OR instance_row.status <> 'completed'
       OR instance_row.request_revision_id <> revision_row.id
       OR instance_row.revision_no <> revision_row.revision_no
       OR instance_row.current_step_id IS NOT NULL
       OR instance_row.current_step_no IS NOT NULL
       OR instance_row.completed_at IS NULL
       OR request_row.decided_at IS DISTINCT FROM instance_row.completed_at THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT CASE WHEN EXISTS (
               SELECT 1 FROM public.material_request_lines AS line
                WHERE line.request_id = checked_request_id
                  AND line.revision_id = revision_row.id
                  AND line.final_approved_qty < line.requested_qty
           ) THEN 'partially_approved' ELSE 'approved' END
      INTO supply_request_status;
    IF request_row.status <> 'cancelled'
       AND request_row.status <> supply_request_status THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT count(*), min(target_version), max(target_version)
      INTO command_count, minimum_version, maximum_version
      FROM public.material_request_commands
     WHERE request_id = checked_request_id
       AND operation IN (
           'create_supply_task','update_supply_task','cancel_supply_task'
       );
    IF command_count <> supply_ceiling_version - approval_command_version
       OR minimum_version <> approval_command_version + 1
       OR maximum_version <> supply_ceiling_version
       OR command_count > 100000
       OR EXISTS (
           SELECT 1 FROM public.material_request_commands
            WHERE request_id = checked_request_id
              AND target_version > approval_command_version
              AND target_version <= supply_ceiling_version
              AND operation NOT IN (
                  'create_supply_task','update_supply_task','cancel_supply_task'
              )
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    FOR command_row IN
        SELECT * FROM public.material_request_commands
         WHERE request_id = checked_request_id
           AND operation IN (
               'create_supply_task','update_supply_task','cancel_supply_task'
           )
         ORDER BY target_version, id
    LOOP
        IF command_row.target_version <= approval_command_version
           OR command_row.projection_manifest_sha256 IS NOT NULL
           OR command_row.created_at IS DISTINCT FROM command_row.occurred_at
           OR command_row.idempotency_key_hash !~ '^[0-9a-f]{{64}}$'
           OR command_row.request_hash !~ '^[0-9a-f]{{64}}$'
           OR command_row.result_hash !~ '^[0-9a-f]{{64}}$'
           OR pg_catalog.jsonb_typeof(command_row.request_jsonb) <> 'object'
           OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(
                   command_row.request_jsonb)) <> 13
           OR NOT command_row.request_jsonb ?& ARRAY[
               'schema','operation','request_id','revision_id','revision_no',
               'request_line_id','supply_task_id','task_no','target_version',
               'target_task_version','payload_sha256','comment_sha256',
               'sensitive_fields']
           OR command_row.request_jsonb->>'schema' <>
               'rsc.material_request_supply_command.v1'
           OR command_row.request_jsonb->>'operation' <> command_row.operation
           OR command_row.request_jsonb->>'request_id' <> checked_request_id::text
           OR command_row.request_jsonb->>'revision_id' <> revision_row.id::text
           OR command_row.request_jsonb->>'revision_no' <>
               revision_row.revision_no::text
           OR command_row.request_jsonb->>'target_version' <>
               command_row.target_version::text
           OR command_row.request_jsonb->>'payload_sha256' <>
               command_row.request_hash
           OR command_row.request_jsonb->>'comment_sha256' !~ '^[0-9a-f]{{64}}$'
           OR command_row.request_jsonb->>'sensitive_fields' <> 'excluded'
           OR command_row.request_jsonb->>'target_task_version' !~ '^[0-9]+$'
           OR command_row.request_reference <> (CASE command_row.operation
               WHEN 'create_supply_task' THEN
                   '/api/v1/material-requests/' || checked_request_id::text ||
                   '/supply-tasks'
               ELSE '/api/v1/material-requests/' || checked_request_id::text ||
                   '/supply-tasks/' ||
                   (command_row.request_jsonb->>'supply_task_id') END)
           OR pg_catalog.jsonb_typeof(command_row.result_jsonb) <> 'object'
           OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(
                   command_row.result_jsonb)) <> 24
           OR NOT command_row.result_jsonb ?& ARRAY[
               'kind','schema_version','request_id','request_no','action',
               'request_status','request_version','revision_id','revision_no',
               'approval_instance_id','approval_attempt_no','current_step_id',
               'state_axes','supply_task_id','task_no','task_status',
               'task_version','request_line_id','substitution_decision_id',
               'supply_type','reference_no','expected_qty',
               'original_equivalent_qty','expected_date']
           OR command_row.result_jsonb->>'kind' <> 'supply_task'
           OR command_row.result_jsonb->>'schema_version' <> '1.0'
           OR command_row.result_jsonb->>'request_id' <> checked_request_id::text
           OR command_row.result_jsonb->>'request_no' <> request_row.request_no
           OR command_row.result_jsonb->>'action' <> command_row.operation
           OR command_row.result_jsonb->>'request_status' <> supply_request_status
           OR command_row.result_jsonb->>'request_version' <>
               command_row.target_version::text
           OR command_row.result_jsonb->>'revision_id' <> revision_row.id::text
           OR command_row.result_jsonb->>'revision_no' <>
               revision_row.revision_no::text
           OR command_row.result_jsonb->>'approval_instance_id' <>
               instance_row.id::text
           OR command_row.result_jsonb->>'approval_attempt_no' <>
               instance_row.attempt_no::text
           OR command_row.result_jsonb->'current_step_id' <> 'null'::jsonb
           OR command_row.result_jsonb->>'supply_task_id' <>
               command_row.request_jsonb->>'supply_task_id'
           OR command_row.result_jsonb->>'task_no' <>
               command_row.request_jsonb->>'task_no'
           OR command_row.result_jsonb->>'task_version' <>
               command_row.request_jsonb->>'target_task_version'
           OR command_row.result_jsonb->>'request_line_id' <>
               command_row.request_jsonb->>'request_line_id'
           OR command_row.result_jsonb->'substitution_decision_id' <>
               'null'::jsonb
           OR command_row.result_jsonb->>'expected_qty' !~
               '^[0-9]+(\\.[0-9]{{3}})$'
           OR command_row.result_jsonb->>'original_equivalent_qty' <>
               command_row.result_jsonb->>'expected_qty'
           OR NOT EXISTS (
               SELECT 1
                 FROM public.supply_tasks AS task
                 JOIN public.material_request_lines AS line
                   ON line.id = task.request_line_id
                WHERE task.id::text =
                      command_row.result_jsonb->>'supply_task_id'
                  AND task.task_no = command_row.result_jsonb->>'task_no'
                  AND line.id::text =
                      command_row.result_jsonb->>'request_line_id'
                  AND line.request_id = checked_request_id
                  AND line.revision_id = revision_row.id
                  AND line.revision_no = revision_row.revision_no
           )
           OR command_row.result_jsonb->'state_axes' <>
               pg_catalog.jsonb_build_object(
                   'allocation_status', request_row.allocation_status,
                   'reservation_status', request_row.reservation_status,
                   'outbound_status', request_row.outbound_status,
                   'shipment_status', request_row.shipment_status,
                   'logistics_signature_status',
                       request_row.logistics_signature_status,
                   'oam_receipt_status', request_row.oam_receipt_status,
                   'personal_inbound_status', request_row.personal_inbound_status,
                   'notification_status', request_row.notification_status,
                   'reconciliation_status', request_row.reconciliation_status)
           OR (SELECT count(*)
                 FROM public.users AS actor_user
                 JOIN public.people AS actor_person
                   ON actor_person.id = actor_user.person_id
                 JOIN public.role_assignments AS assignment
                   ON assignment.id = command_row.actor_role_assignment_id
                  AND assignment.user_id = actor_user.id
                 JOIN public.roles AS role ON role.id = assignment.role_id
                 JOIN public.role_permissions AS role_permission
                   ON role_permission.role_id = role.id
                 JOIN public.permissions AS permission
                   ON permission.id = role_permission.permission_id
                WHERE actor_user.id = command_row.actor_user_id
                  AND actor_person.id = command_row.actor_person_id
                  AND actor_user.account_status = 'active'
                  AND actor_person.employment_status = 'active'
                  AND actor_user.authorization_version =
                      command_row.authorization_version
                  AND role.code = 'admin' AND role.status = 'active'
                  AND assignment.scope_type = 'national'
                  AND assignment.scope_id = '*' AND assignment.status = 'active'
                  AND assignment.valid_from <= command_row.occurred_at
                  AND (assignment.valid_to IS NULL
                       OR assignment.valid_to > command_row.occurred_at)
                  AND permission.resource = 'supply_task'
                  AND permission.action = 'manage'
                  AND permission.field_code = '') <> 1 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        SELECT previous.result_jsonb INTO previous_result
          FROM public.material_request_commands AS previous
         WHERE previous.request_id = checked_request_id
           AND previous.request_jsonb->>'supply_task_id' =
               command_row.request_jsonb->>'supply_task_id'
           AND previous.request_jsonb->>'target_task_version' =
               (((command_row.request_jsonb->>'target_task_version')::bigint - 1)::text)
         LIMIT 1;
        previous_status := previous_result->>'task_status';
        IF command_row.operation = 'cancel_supply_task'
           AND (command_row.result_jsonb->'reference_no' IS DISTINCT FROM
                    previous_result->'reference_no'
                OR command_row.result_jsonb->'expected_date' IS DISTINCT FROM
                    previous_result->'expected_date') THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        SELECT count(*) INTO audit_count
          FROM public.audit_events AS audit
         WHERE audit.stream_key = 'material_request'
           AND audit.aggregate_type = 'material_request'
           AND audit.aggregate_id = checked_request_id::text
           AND audit.action = CASE command_row.operation
               WHEN 'create_supply_task' THEN 'material_request.supply_task.create'
               WHEN 'cancel_supply_task' THEN 'material_request.supply_task.cancel'
               ELSE 'material_request.supply_task.update' END
           AND audit.actor_user_id = command_row.actor_user_id
           AND audit.occurred_at = command_row.occurred_at
           AND audit.created_at = command_row.created_at
           AND audit.request_id <> ''
           AND audit.after_jsonb =
               (command_row.result_jsonb - ARRAY[
                   'kind','schema_version','action','current_step_id'
               ]) || pg_catalog.jsonb_build_object(
                   'schema', 'rsc.material_request_supply_audit.v1',
                   'operation', command_row.operation,
                   'command_id', command_row.id::text,
                   'idempotency_key_hash', command_row.idempotency_key_hash,
                   'comment_sha256',
                       command_row.request_jsonb->>'comment_sha256',
                   'sensitive_fields', 'excluded')
           AND audit.before_jsonb = CASE command_row.operation
               WHEN 'create_supply_task' THEN '{{}}'::jsonb
               ELSE pg_catalog.jsonb_build_object(
                   'schema', 'rsc.material_request_supply_audit.v1',
                   'request_id', checked_request_id::text,
                   'supply_task_id', previous_result->>'supply_task_id',
                   'task_no', previous_result->>'task_no',
                   'task_status', previous_result->>'task_status',
                   'task_version',
                       (previous_result->>'task_version')::bigint,
                   'reference_no', previous_result->'reference_no',
                   'expected_date', previous_result->'expected_date',
                   'sensitive_fields', 'excluded') END;
        IF audit_count <> 1 THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;

        SELECT count(*) INTO state_count
          FROM public.state_transition_events AS event
         WHERE event.aggregate_type = 'supply_task'
           AND event.aggregate_id = command_row.result_jsonb->>'supply_task_id'
           AND event.actor_id = command_row.actor_user_id
           AND event.occurred_at = command_row.occurred_at
           AND event.created_at = command_row.created_at
           AND event.idempotency_key = 'mr-supply:' ||
               command_row.idempotency_key_hash || ':' ||
               command_row.result_jsonb->>'task_version'
           AND event.from_status IS NOT DISTINCT FROM previous_status
           AND event.to_status = command_row.result_jsonb->>'task_status'
           AND event.reason = CASE
               WHEN command_row.operation = 'create_supply_task'
                   THEN 'supply_task_created'
               WHEN command_row.operation = 'cancel_supply_task'
                   THEN 'supply_task_cancelled'
               ELSE 'supply_task_status_changed' END
           AND event.metadata_jsonb = pg_catalog.jsonb_build_object(
               'schema', 'rsc.supply_task_state_transition.v1',
               'request_id', checked_request_id::text,
               'request_no', request_row.request_no,
               'revision_id', revision_row.id::text,
               'revision_no', revision_row.revision_no,
               'request_line_id',
                   command_row.result_jsonb->>'request_line_id',
               'supply_task_id',
                   command_row.result_jsonb->>'supply_task_id',
               'task_no', command_row.result_jsonb->>'task_no',
               'request_version', command_row.target_version,
               'task_version',
                   (command_row.result_jsonb->>'task_version')::bigint,
               'command_id', command_row.id::text,
               'idempotency_key_hash', command_row.idempotency_key_hash);
        IF (previous_status IS NULL OR previous_status <>
                command_row.result_jsonb->>'task_status') THEN
            IF state_count <> 1 THEN RAISE EXCEPTION '{GUARD_ERROR}'; END IF;
        ELSIF state_count <> 0 THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
          FROM public.state_transition_events AS event
          JOIN public.supply_tasks AS task
            ON task.id::text = event.aggregate_id
          JOIN public.material_request_lines AS line
            ON line.id = task.request_line_id
         WHERE line.request_id = checked_request_id
           AND event.aggregate_type = 'supply_task'
           AND NOT EXISTS (
               SELECT 1 FROM public.material_request_commands AS command
                WHERE command.request_id = checked_request_id
                  AND command.id::text = event.metadata_jsonb->>'command_id'
                  AND command.idempotency_key_hash =
                      event.metadata_jsonb->>'idempotency_key_hash'
                  AND command.request_jsonb->>'supply_task_id' = event.aggregate_id
                  AND command.result_jsonb->>'task_version' =
                      event.metadata_jsonb->>'task_version'
                  AND command.actor_user_id IS NOT DISTINCT FROM event.actor_id
                  AND command.occurred_at = event.occurred_at
           )
    ) OR EXISTS (
        SELECT 1
          FROM public.audit_events AS audit
         WHERE audit.aggregate_type = 'material_request'
           AND audit.aggregate_id = checked_request_id::text
           AND audit.action IN (
               'material_request.supply_task.create',
               'material_request.supply_task.update',
               'material_request.supply_task.cancel'
           )
           AND NOT EXISTS (
               SELECT 1 FROM public.material_request_commands AS command
                WHERE command.request_id = checked_request_id
                  AND command.id::text = audit.after_jsonb->>'command_id'
                  AND command.idempotency_key_hash =
                      audit.after_jsonb->>'idempotency_key_hash'
                  AND command.actor_user_id = audit.actor_user_id
                  AND command.occurred_at = audit.occurred_at
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT count(*) INTO task_count
      FROM public.supply_tasks AS task
      JOIN public.material_request_lines AS line ON line.id = task.request_line_id
     WHERE line.request_id = checked_request_id;
    IF task_count < 1 OR task_count > 10000 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    FOR task_row IN
        SELECT task.* FROM public.supply_tasks AS task
        JOIN public.material_request_lines AS line ON line.id = task.request_line_id
         WHERE line.request_id = checked_request_id
         ORDER BY task.id
    LOOP
        SELECT count(*), min((command.request_jsonb->>'target_task_version')::bigint),
               max((command.request_jsonb->>'target_task_version')::bigint)
          INTO task_command_count, minimum_version, maximum_version
          FROM public.material_request_commands AS command
         WHERE command.request_id = checked_request_id
           AND command.request_jsonb->>'supply_task_id' = task_row.id::text;
        IF task_command_count <> task_row.version + 1
           OR minimum_version <> 0 OR maximum_version <> task_row.version
           OR (SELECT count(*) FROM public.material_request_commands AS command
                WHERE command.request_id = checked_request_id
                  AND command.request_jsonb->>'supply_task_id' = task_row.id::text
                  AND command.operation = 'create_supply_task'
                  AND command.request_jsonb->>'target_task_version' = '0') <> 1
           OR EXISTS (
               WITH ordered AS (
                   SELECT command.operation,
                          command.result_jsonb->>'task_status' AS task_status,
                          pg_catalog.lag(command.result_jsonb->>'task_status') OVER (
                              ORDER BY (command.request_jsonb->>'target_task_version')::bigint
                          ) AS prior_status,
                          (command.request_jsonb->>'target_task_version')::bigint AS task_version
                     FROM public.material_request_commands AS command
                    WHERE command.request_id = checked_request_id
                      AND command.request_jsonb->>'supply_task_id' = task_row.id::text
               )
               SELECT 1 FROM ordered
                WHERE (task_version = 0 AND (
                           operation <> 'create_supply_task'
                           OR task_status NOT IN ('open','reference_registered')))
                   OR (task_version > 0 AND operation = 'create_supply_task')
                   OR (operation = 'cancel_supply_task'
                       AND task_status <> 'cancelled')
                   OR (operation = 'update_supply_task'
                       AND task_status = 'cancelled')
                   OR (prior_status = 'open' AND task_status NOT IN (
                       'open','reference_registered','awaiting_supply',
                       'cancelled','closed_no_supply'))
                   OR (prior_status = 'reference_registered' AND task_status NOT IN (
                       'reference_registered','awaiting_supply',
                       'cancelled','closed_no_supply'))
                   OR (prior_status = 'awaiting_supply' AND task_status NOT IN (
                       'awaiting_supply','cancelled','closed_no_supply'))
                   OR prior_status IN ('cancelled','closed_no_supply')
           )
           OR EXISTS (
               SELECT 1
                 FROM public.material_request_commands AS command
                 JOIN public.material_request_commands AS created
                   ON created.request_id = command.request_id
                  AND created.request_jsonb->>'supply_task_id' =
                      command.request_jsonb->>'supply_task_id'
                  AND created.operation = 'create_supply_task'
                  AND created.request_jsonb->>'target_task_version' = '0'
                WHERE command.request_id = checked_request_id
                  AND command.request_jsonb->>'supply_task_id' = task_row.id::text
                  AND (command.result_jsonb->>'task_no' <>
                           created.result_jsonb->>'task_no'
                       OR command.result_jsonb->>'request_line_id' <>
                           created.result_jsonb->>'request_line_id'
                       OR command.result_jsonb->'substitution_decision_id'
                           IS DISTINCT FROM
                           created.result_jsonb->'substitution_decision_id'
                       OR command.result_jsonb->>'supply_type' <>
                           created.result_jsonb->>'supply_type'
                       OR command.result_jsonb->>'expected_qty' <>
                           created.result_jsonb->>'expected_qty'
                       OR command.result_jsonb->>'original_equivalent_qty' <>
                           created.result_jsonb->>'original_equivalent_qty')
           )
           OR NOT EXISTS (
               SELECT 1 FROM public.material_request_commands AS latest
                WHERE latest.request_id = checked_request_id
                  AND latest.request_jsonb->>'supply_task_id' = task_row.id::text
                  AND latest.request_jsonb->>'target_task_version' =
                      task_row.version::text
                  AND latest.result_jsonb->>'task_no' = task_row.task_no
                  AND latest.result_jsonb->>'request_line_id' =
                      task_row.request_line_id::text
                  AND latest.result_jsonb->'substitution_decision_id' = 'null'::jsonb
                  AND latest.result_jsonb->>'supply_type' = task_row.supply_type
                  AND latest.result_jsonb->>'reference_no'
                      IS NOT DISTINCT FROM task_row.reference_no
                  AND latest.result_jsonb->>'expected_qty' = task_row.expected_qty::text
                  AND latest.result_jsonb->>'original_equivalent_qty' =
                      task_row.original_equivalent_qty::text
                  AND latest.result_jsonb->>'expected_date'
                      IS NOT DISTINCT FROM task_row.expected_date::text
                  AND latest.result_jsonb->>'task_status' = task_row.status
                  AND latest.occurred_at = task_row.updated_at
                  AND (task_row.status <> 'cancelled' OR (
                      latest.operation = 'cancel_supply_task'
                      AND task_row.cancelled_by_user_id = latest.actor_user_id
                      AND task_row.cancelled_at = latest.occurred_at))
           )
           OR NOT EXISTS (
               SELECT 1 FROM public.material_request_commands AS created
                WHERE created.request_id = checked_request_id
                  AND created.request_jsonb->>'supply_task_id' = task_row.id::text
                  AND created.operation = 'create_supply_task'
                  AND created.request_jsonb->>'target_task_version' = '0'
                  AND created.result_jsonb->>'task_no' = task_row.task_no
                  AND created.result_jsonb->>'request_line_id' =
                      task_row.request_line_id::text
                  AND created.result_jsonb->>'supply_type' = task_row.supply_type
                  AND created.result_jsonb->>'expected_qty' = task_row.expected_qty::text
                  AND created.result_jsonb->>'original_equivalent_qty' =
                      task_row.original_equivalent_qty::text
                  AND created.actor_user_id = task_row.created_by_user_id
                  AND created.actor_person_id = task_row.created_by_person_id
                  AND created.actor_role_assignment_id =
                      task_row.created_role_assignment_id
                  AND created.authorization_version = task_row.authorization_version
                  AND created.occurred_at = task_row.created_at
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END LOOP;
END
$$
"""


def _supply_dispatcher_sql() -> str:
    return f"""
CREATE FUNCTION public.{SUPPLY_DISPATCH_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    target_request_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'material_requests' THEN
        target_request_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_TABLE_NAME = 'material_request_commands' THEN
        target_request_id := COALESCE(NEW.request_id, OLD.request_id);
    ELSIF TG_TABLE_NAME = 'supply_tasks' THEN
        SELECT line.request_id INTO target_request_id
          FROM public.material_request_lines AS line
         WHERE line.id = COALESCE(NEW.request_line_id, OLD.request_line_id);
    ELSIF TG_TABLE_NAME IN ('state_transition_events','audit_events') THEN
        IF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'material_request' THEN
            SELECT request.id INTO target_request_id
              FROM public.material_requests AS request
             WHERE request.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        ELSIF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'supply_task' THEN
            SELECT line.request_id INTO target_request_id
              FROM public.supply_tasks AS task
              JOIN public.material_request_lines AS line
                ON line.id = task.request_line_id
             WHERE task.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        END IF;
    END IF;
    IF target_request_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.material_request_commands AS command
         WHERE command.request_id = target_request_id
           AND command.operation IN (
               'create_supply_task','update_supply_task','cancel_supply_task'
           )
    ) THEN
        PERFORM public.rsc_validate_material_request_approval_projection_0045(
            target_request_id
        );
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$
"""


def _create_postgresql_triggers() -> None:
    op.execute(
        f"CREATE TRIGGER {OWNER_GUARD_TRIGGER} BEFORE INSERT OR UPDATE OR DELETE "
        f"ON public.supply_tasks FOR EACH ROW EXECUTE FUNCTION "
        f"public.{OWNER_GUARD_FUNCTION}()"
    )
    for table_name, trigger_name in SUPPLY_TRIGGER_BINDINGS:
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} AFTER INSERT OR UPDATE OR DELETE "
            f"ON public.{table_name} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION public.{SUPPLY_DISPATCH_FUNCTION}()"
        )
    for table_name, trigger_name in (
        ("supply_tasks", OWNER_GUARD_TRIGGER), *SUPPLY_TRIGGER_BINDINGS
    ):
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )
    for function_signature in (
        f"public.{OWNER_GUARD_FUNCTION}()",
        f"public.{SUPPLY_VALIDATE_FUNCTION}(uuid, bigint)",
        f"public.{SUPPLY_DISPATCH_FUNCTION}()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function_signature} FROM PUBLIC")
        op.execute(
            f"REVOKE ALL ON FUNCTION {function_signature} FROM {PRODUCTION_API_ROLE}"
        )


def _grant_runtime_supply_dml() -> None:
    op.execute(
        f"GRANT INSERT ON TABLE public.supply_tasks TO {PRODUCTION_API_ROLE}"
    )
    op.execute(
        "GRANT UPDATE (reference_no, expected_date, status, "
        "cancelled_by_user_id, cancelled_at, version, updated_at) "
        f"ON TABLE public.supply_tasks TO {PRODUCTION_API_ROLE}"
    )


def _replace_function_source(
    *,
    signature: str,
    expected_hash: str,
    replacement_hash: str,
    replacements: tuple[tuple[str, str], ...],
) -> None:
    replacement_sql = "\n".join(
        f"""
    IF (pg_catalog.length(function_source) - pg_catalog.length(
            pg_catalog.replace(function_source, $old${old}$old$, ''))
        ) / pg_catalog.length($old${old}$old$) <> 1
       OR pg_catalog.strpos(function_source, $new${new}$new$) <> 0 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: source replacement mismatch';
    END IF;
    function_source := pg_catalog.replace(
        function_source, $old${old}$old$, $new${new}$new$
    );
"""
        for old, new in replacements
    )
    definition_replacement_sql = "\n".join(
        f"""
    function_definition := pg_catalog.replace(
        function_definition, $old${old}$old$, $new${new}$new$
    );
"""
        for old, new in replacements
    )
    op.execute(
        f"""
DO $rsc_0059_replace$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{signature}');
    function_source text;
    function_definition text;
BEGIN
    SELECT function_row.prosrc INTO function_source
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(pg_catalog.sha256(
               pg_catalog.convert_to(function_row.prosrc, 'UTF8')), 'hex') =
           '{expected_hash}';
    IF function_source IS NULL THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: prerequisite function mismatch';
    END IF;
    {replacement_sql}
    function_definition := pg_catalog.pg_get_functiondef(function_oid);
    {definition_replacement_sql}
    EXECUTE function_definition;
    IF pg_catalog.to_regprocedure('{signature}') <> function_oid OR (
        SELECT pg_catalog.encode(pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')), 'hex')
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid = function_oid
    ) IS DISTINCT FROM '{replacement_hash}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: replacement function mismatch';
    END IF;
END
$rsc_0059_replace$
"""
    )


def _replace_runtime_ready(
    *, expected_hash: str, replacement_hash: str,
    old_revision: str, new_revision: str,
) -> None:
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_hash=expected_hash,
        replacement_hash=replacement_hash,
        replacements=((old_revision, new_revision),),
    )


def _verify_postgresql_catalog() -> None:
    trigger_values = ",\n                ".join(
        f"('{table_name}', '{trigger_name}', "
        f"'{OWNER_GUARD_FUNCTION if trigger_name == OWNER_GUARD_TRIGGER else SUPPLY_DISPATCH_FUNCTION}', "  # noqa: E501
        f"{31 if trigger_name == OWNER_GUARD_TRIGGER else 29}, "
        f"{str(trigger_name != OWNER_GUARD_TRIGGER).lower()})"
        for table_name, trigger_name in (
            ("supply_tasks", OWNER_GUARD_TRIGGER), *SUPPLY_TRIGGER_BINDINGS
        )
    )
    function_values = ",\n                ".join(
        f"('{name}', '{arguments}', '{argument_names}', '{return_type}', '{body_hash}')"
        for name, arguments, argument_names, return_type, body_hash in (
            (
                OWNER_GUARD_FUNCTION,
                "",
                "",
                "trigger",
                "913d606ff9f47fd05feda92d75ef76477daf6823b71ecdb9c47cabf5355a5398",
            ),
            (
                SUPPLY_VALIDATE_FUNCTION,
                "uuid, bigint",
                "checked_request_id,approval_command_version",
                "void",
                "ce370ea355224013645f399b2176aceedf92e51c01f8159866a822bb33120f46",
            ),
            (
                SUPPLY_DISPATCH_FUNCTION,
                "",
                "",
                "trigger",
                "efab0c6eee9c8fbaccb1e334b0dc28d10fc85a4fb097a508b422c30b0cb034ed",
            ),
            (
                TERMINAL_FUNCTION,
                "uuid, uuid, uuid, bigint",
                "checked_request_id,checked_instance_id,checked_step_id,expected_command_version",
                "void",
                TERMINAL_BODY_SHA256_0059,
            ),
            (
                PROJECTION_FUNCTION,
                "uuid",
                "checked_request_id",
                "void",
                PROJECTION_BODY_SHA256_0059,
            ),
        )
    )
    op.execute(
        f"""
DO $rsc_0059_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR migrator_oid IS NULL THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: role mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES
                {function_values}
          ) AS expected(
              function_name, arguments, argument_names, return_type, body_hash)
          LEFT JOIN pg_catalog.pg_proc AS function_row
            ON function_row.oid = pg_catalog.to_regprocedure(
                'public.' || expected.function_name || '(' ||
                pg_catalog.replace(expected.arguments, ', ', ',') || ')'
            )
          LEFT JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
          LEFT JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid IS NULL
            OR namespace_row.nspname <> 'public'
            OR function_row.proowner <> migrator_oid
            OR function_row.prokind <> 'f'
            OR function_row.prorettype <> expected.return_type::pg_catalog.regtype
            OR function_row.proretset
            OR pg_catalog.oidvectortypes(function_row.proargtypes) <>
               expected.arguments
            OR COALESCE(pg_catalog.array_to_string(
                   function_row.proargnames, ','), '') <>
               expected.argument_names
            OR function_row.proallargtypes IS NOT NULL
            OR function_row.proargmodes IS NOT NULL
            OR function_row.pronargdefaults <> 0
            OR function_row.proargdefaults IS NOT NULL
            OR function_row.provariadic <> 0
            OR language_row.lanname <> 'plpgsql'
            OR function_row.provolatile <> 'v'
            OR NOT function_row.prosecdef
            OR function_row.proisstrict OR function_row.proleakproof
            OR function_row.proparallel <> 'u'
            OR function_row.proconfig <>
               ARRAY['{FIXED_SEARCH_PATH}']::text[]
            OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                   function_row.prosrc, 'UTF8')), 'hex') <> expected.body_hash
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: function shape mismatch';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (VALUES
                ('{OWNER_GUARD_FUNCTION}', ''),
                ('{SUPPLY_VALIDATE_FUNCTION}', 'uuid, bigint'),
                ('{SUPPLY_DISPATCH_FUNCTION}', '')
        ) AS expected(function_name, arguments)
        JOIN pg_catalog.pg_proc AS function_row
          ON function_row.oid = pg_catalog.to_regprocedure(
              'public.' || expected.function_name || '(' ||
              pg_catalog.replace(expected.arguments, ', ', ',') || ')')
        CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(
            function_row.proacl,
            pg_catalog.acldefault('f', function_row.proowner))) AS acl
        WHERE acl.privilege_type <> 'EXECUTE'
           OR acl.grantee <> migrator_oid
           OR acl.grantor <> migrator_oid
           OR acl.is_grantable
    ) OR (
        SELECT count(*) FROM (VALUES
                ('{OWNER_GUARD_FUNCTION}', ''),
                ('{SUPPLY_VALIDATE_FUNCTION}', 'uuid, bigint'),
                ('{SUPPLY_DISPATCH_FUNCTION}', '')
        ) AS expected(function_name, arguments)
        JOIN pg_catalog.pg_proc AS function_row
          ON function_row.oid = pg_catalog.to_regprocedure(
              'public.' || expected.function_name || '(' ||
              pg_catalog.replace(expected.arguments, ', ', ',') || ')')
        CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(
            function_row.proacl,
            pg_catalog.acldefault('f', function_row.proowner))) AS acl
        WHERE acl.privilege_type = 'EXECUTE'
          AND acl.grantee = migrator_oid
          AND acl.grantor = migrator_oid
          AND NOT acl.is_grantable
    ) <> 3 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: function ACL mismatch';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (VALUES
                {trigger_values}
        ) AS expected(table_name, trigger_name, function_name,
                      trigger_type, is_constraint)
        LEFT JOIN pg_catalog.pg_class AS table_row
          ON table_row.oid = pg_catalog.to_regclass(
              'public.' || expected.table_name)
        LEFT JOIN pg_catalog.pg_trigger AS trigger_row
          ON trigger_row.tgrelid = table_row.oid
         AND trigger_row.tgname = expected.trigger_name
         AND NOT trigger_row.tgisinternal
        LEFT JOIN pg_catalog.pg_proc AS function_row
          ON function_row.oid = trigger_row.tgfoid
        WHERE trigger_row.oid IS NULL
           OR trigger_row.tgenabled <> 'A'
           OR trigger_row.tgtype <> expected.trigger_type
           OR function_row.proname <> expected.function_name
           OR (trigger_row.tgconstraint <> 0) <> expected.is_constraint
           OR trigger_row.tgdeferrable <> expected.is_constraint
           OR trigger_row.tginitdeferred <> expected.is_constraint
           OR trigger_row.tgnargs <> 0 OR trigger_row.tgqual IS NOT NULL
           OR trigger_row.tgoldtable IS NOT NULL
           OR trigger_row.tgnewtable IS NOT NULL
           OR trigger_row.tgparentid <> 0
           OR (expected.is_constraint AND NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_constraint AS constraint_row
                WHERE constraint_row.oid = trigger_row.tgconstraint
                  AND constraint_row.contype = 't'
                  AND constraint_row.condeferrable
                  AND constraint_row.condeferred))
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: trigger mismatch';
    END IF;
    IF NOT pg_catalog.has_table_privilege(
           api_oid, 'public.supply_tasks', 'SELECT')
       OR NOT pg_catalog.has_table_privilege(
           api_oid, 'public.supply_tasks', 'INSERT')
       OR pg_catalog.has_table_privilege(
           api_oid, 'public.supply_tasks', 'DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR pg_catalog.has_table_privilege(
           api_oid, 'public.supply_tasks', 'UPDATE')
       OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_attribute AS attribute_row
            WHERE attribute_row.attrelid = 'public.supply_tasks'::pg_catalog.regclass
              AND attribute_row.attnum > 0 AND NOT attribute_row.attisdropped
              AND pg_catalog.has_column_privilege(
                  api_oid, 'public.supply_tasks', attribute_row.attname, 'UPDATE')
              AND attribute_row.attname NOT IN (
                  'reference_no','expected_date','status','cancelled_by_user_id',
                  'cancelled_at','version','updated_at'))
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('reference_no'),('expected_date'),('status'),
               ('cancelled_by_user_id'),('cancelled_at'),('version'),('updated_at')
           ) AS expected(column_name)
           WHERE NOT pg_catalog.has_column_privilege(
               api_oid, 'public.supply_tasks', expected.column_name, 'UPDATE'))
    THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: runtime ACL mismatch';
    END IF;
    IF (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
            function_row.prosrc, 'UTF8')), 'hex')
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid = pg_catalog.to_regprocedure(
             '{RUNTIME_READY_SIGNATURE}')) <>
       '{RUNTIME_READY_BODY_SHA256_0059}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness mismatch';
    END IF;
END
$rsc_0059_catalog$
"""
    )


def _create_sqlite_guards() -> None:
    op.execute(
        f"""
CREATE TRIGGER trg_supply_tasks_state_guard_0059
BEFORE INSERT ON supply_tasks
WHEN NEW.substitution_decision_id IS NOT NULL
  OR NEW.expected_qty <> NEW.original_equivalent_qty
  OR NEW.version <> 0
  OR NEW.created_at IS NOT NEW.updated_at
  OR NEW.status NOT IN ('open','reference_registered')
  OR (NEW.status = 'open' AND NEW.reference_no IS NOT NULL)
  OR (NEW.status = 'reference_registered' AND NEW.reference_no IS NULL)
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_supply_tasks_transition_guard_0059
BEFORE UPDATE ON supply_tasks
WHEN NEW.id IS NOT OLD.id OR NEW.task_no IS NOT OLD.task_no
  OR NEW.request_line_id IS NOT OLD.request_line_id
  OR NEW.substitution_decision_id IS NOT OLD.substitution_decision_id
  OR NEW.supply_type IS NOT OLD.supply_type
  OR NEW.expected_qty IS NOT OLD.expected_qty
  OR NEW.original_equivalent_qty IS NOT OLD.original_equivalent_qty
  OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
  OR NEW.created_by_person_id IS NOT OLD.created_by_person_id
  OR NEW.created_role_assignment_id IS NOT OLD.created_role_assignment_id
  OR NEW.authorization_version IS NOT OLD.authorization_version
  OR NEW.created_at IS NOT OLD.created_at OR NEW.version <> OLD.version + 1
  OR NEW.updated_at <= OLD.updated_at
  OR OLD.status IN ('cancelled','closed_no_supply')
  OR NOT ((OLD.status='open' AND NEW.status IN (
      'open','reference_registered','awaiting_supply','cancelled','closed_no_supply'))
      OR (OLD.status='reference_registered' AND NEW.status IN (
      'reference_registered','awaiting_supply','cancelled','closed_no_supply'))
      OR (OLD.status='awaiting_supply' AND NEW.status IN (
      'awaiting_supply','cancelled','closed_no_supply')))
  OR (NEW.status='reference_registered' AND NEW.reference_no IS NULL)
  OR (NEW.status='cancelled' AND (NEW.cancelled_by_user_id IS NULL
      OR NEW.cancelled_at IS NULL OR NEW.cancelled_at IS NOT NEW.updated_at
      OR NEW.reference_no IS NOT OLD.reference_no
      OR NEW.expected_date IS NOT OLD.expected_date))
  OR (NEW.status<>'cancelled' AND (NEW.cancelled_by_user_id IS NOT NULL
      OR NEW.cancelled_at IS NOT NULL))
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )


def _drop_sqlite_guards() -> None:
    op.execute("DROP TRIGGER trg_supply_tasks_transition_guard_0059")
    op.execute("DROP TRIGGER trg_supply_tasks_state_guard_0059")
