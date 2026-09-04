"""Harden formal supply-plan authorization and causal ownership.

Revision ID: 20260905_0060
Revises: 20260905_0059
Create Date: 2026-09-05

This is a forward-only correction to the already-published 0059 migration.
Supply tasks remain planning facts and do not mutate any fulfilment axis or
inventory balance.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260905_0060"
down_revision: Union[str, Sequence[str], None] = "20260905_0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PREVIOUS_SCHEMA_REVISION = "20260905_0059"
GUARD_ERROR = "formal material request supply projection is invalid"
CATALOG_ERROR = "0060 material request supply catalog verification failed"
DOWNGRADE_BLOCKER = "cannot downgrade 0060 while supply-task facts exist"

WRITE_GUARD_FUNCTION = "rsc_guard_material_request_supply_write_0060"
COMMAND_GUARD_TRIGGER = "trg_material_request_commands_000_supply_owner_guard_0060"
TASK_GUARD_TRIGGER = "trg_supply_tasks_000_owner_guard_0060"
VALIDATOR_FUNCTION = "rsc_validate_material_request_supply_causality_0059"
VALIDATOR_SIGNATURE = f"public.{VALIDATOR_FUNCTION}(uuid, bigint)"
DISPATCHER_FUNCTION = "rsc_dispatch_material_request_supply_causality_0059"
DISPATCHER_SIGNATURE = f"public.{DISPATCHER_FUNCTION}()"
RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"

VALIDATOR_BODY_SHA256_0059 = (
    "ce370ea355224013645f399b2176aceedf92e51c01f8159866a822bb33120f46"
)
DISPATCHER_BODY_SHA256_0059 = (
    "efab0c6eee9c8fbaccb1e334b0dc28d10fc85a4fb097a508b422c30b0cb034ed"
)
RUNTIME_READY_BODY_SHA256_0059 = (
    "21859de4675a39652ffe7cda14887c2d644b05d208d32e361ecfad7f9dd4b963"
)
# Filled after deterministic source construction below.
WRITE_GUARD_BODY_SHA256_0060 = (
    "0fe289826a8aa4d14e2ecd48a9900484bf8929a054dbfa52340fa27786453f26"
)
VALIDATOR_BODY_SHA256_0060 = (
    "092a41ff7072397c8b8311f3bab658dcc4a69212a4bf893dc9e9271a0dd625f0"
)
DISPATCHER_BODY_SHA256_0060 = (
    "935be3c5144f0bb9d5dad8652bb8284bc7116caaefc9ccb5d177e922b41530c5"
)
RUNTIME_READY_BODY_SHA256_0060 = (
    "47ede12ff8b812e78aa0bb325ce6445b3dd41f6d1fd512ee86055c7418e00bf3"
)

LOCK_TABLES = (
    "alembic_version",
    "audit_events",
    "auth_identities",
    "material_request_commands",
    "material_request_lines",
    "material_requests",
    "organizations",
    "people",
    "permissions",
    "role_assignments",
    "role_permissions",
    "roles",
    "state_transition_events",
    "supply_tasks",
    "users",
)

VALIDATOR_DECLARATION_0059 = """    task_command_count bigint;
    state_count bigint;"""
VALIDATOR_DECLARATION_0060 = """    task_command_count bigint;
    distinct_task_version_count bigint;
    state_count bigint;"""

VALIDATOR_ACTOR_0059 = """           OR (SELECT count(*)
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
                  AND permission.field_code = '') <> 1 THEN"""
VALIDATOR_ACTOR_0060 = """           OR NOT EXISTS (
               SELECT 1
                 FROM public.users AS actor_user
                 JOIN public.people AS actor_person
                   ON actor_person.id = actor_user.person_id
                 JOIN public.role_assignments AS assignment
                   ON assignment.id = command_row.actor_role_assignment_id
                  AND assignment.user_id = actor_user.id
                 JOIN public.roles AS role ON role.id = assignment.role_id
                WHERE actor_user.id = command_row.actor_user_id
                  AND actor_person.id = command_row.actor_person_id
                  AND role.code = 'admin'
                  AND assignment.scope_type = 'national'
                  AND assignment.scope_id = '*'
                  AND assignment.valid_from <= command_row.occurred_at
                  AND (assignment.valid_to IS NULL
                       OR assignment.valid_to > command_row.occurred_at)
                  AND (assignment.revoked_at IS NULL
                       OR assignment.revoked_at > command_row.occurred_at)
           ) THEN"""

VALIDATOR_TASK_COUNT_0059 = """        SELECT count(*), min((command.request_jsonb->>'target_task_version')::bigint),
               max((command.request_jsonb->>'target_task_version')::bigint)
          INTO task_command_count, minimum_version, maximum_version"""
VALIDATOR_TASK_COUNT_0060 = """        SELECT count(*),
               count(DISTINCT (command.request_jsonb->>'target_task_version')::bigint),
               min((command.request_jsonb->>'target_task_version')::bigint),
               max((command.request_jsonb->>'target_task_version')::bigint)
          INTO task_command_count, distinct_task_version_count,
               minimum_version, maximum_version"""
VALIDATOR_TASK_IF_0059 = """        IF task_command_count <> task_row.version + 1
           OR minimum_version <> 0 OR maximum_version <> task_row.version"""
VALIDATOR_TASK_IF_0060 = """        IF task_command_count <> task_row.version + 1
           OR distinct_task_version_count <> task_row.version + 1
           OR minimum_version <> 0 OR maximum_version <> task_row.version"""
VALIDATOR_ORDERED_0059 = """                          pg_catalog.lag(command.result_jsonb->>'task_status') OVER (
                              ORDER BY (command.request_jsonb->>'target_task_version')::bigint
                          ) AS prior_status,
                          (command.request_jsonb->>'target_task_version')::bigint AS task_version"""
VALIDATOR_ORDERED_0060 = """                          pg_catalog.lag(command.result_jsonb->>'task_status') OVER (
                              ORDER BY (command.request_jsonb->>'target_task_version')::bigint,
                                       command.target_version, command.id
                          ) AS prior_status,
                          (command.request_jsonb->>'target_task_version')::bigint AS task_version,
                          pg_catalog.row_number() OVER (
                              ORDER BY (command.request_jsonb->>'target_task_version')::bigint,
                                       command.target_version, command.id
                          ) - 1 AS expected_task_version"""
VALIDATOR_SEQUENCE_0059 = """               SELECT 1 FROM ordered
                WHERE (task_version = 0 AND ("""
VALIDATOR_SEQUENCE_0060 = """               SELECT 1 FROM ordered
                WHERE task_version <> expected_task_version
                   OR (task_version = 0 AND ("""


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        _create_sqlite_guards()
        return
    _lock_execution_boundary()
    _verify_prerequisite_hashes()
    op.execute(_write_guard_sql())
    _create_postgresql_triggers()
    _replace_function_source(
        signature=VALIDATOR_SIGNATURE,
        expected_hash=VALIDATOR_BODY_SHA256_0059,
        replacement_hash=VALIDATOR_BODY_SHA256_0060,
        replacements=(
            (VALIDATOR_DECLARATION_0059, VALIDATOR_DECLARATION_0060),
            (VALIDATOR_ACTOR_0059, VALIDATOR_ACTOR_0060),
            (VALIDATOR_TASK_COUNT_0059, VALIDATOR_TASK_COUNT_0060),
            (VALIDATOR_TASK_IF_0059, VALIDATOR_TASK_IF_0060),
            (VALIDATOR_ORDERED_0059, VALIDATOR_ORDERED_0060),
            (VALIDATOR_SEQUENCE_0059, VALIDATOR_SEQUENCE_0060),
        ),
    )
    _replace_function_source(
        signature=DISPATCHER_SIGNATURE,
        expected_hash=DISPATCHER_BODY_SHA256_0059,
        replacement_hash=DISPATCHER_BODY_SHA256_0060,
        replacements=((_dispatcher_body_0059(), _dispatcher_body_0060()),),
    )
    _audit_existing_graph()
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0059,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0060,
        old_revision=PREVIOUS_SCHEMA_REVISION,
        new_revision=revision,
    )
    _verify_postgresql_catalog()


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        _require_empty_sqlite_supply_graph()
        _drop_sqlite_guards()
        return
    _lock_execution_boundary()
    _require_empty_supply_graph()
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0060,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0059,
        old_revision=revision,
        new_revision=PREVIOUS_SCHEMA_REVISION,
    )
    _replace_function_source(
        signature=DISPATCHER_SIGNATURE,
        expected_hash=DISPATCHER_BODY_SHA256_0060,
        replacement_hash=DISPATCHER_BODY_SHA256_0059,
        replacements=((_dispatcher_body_0060(), _dispatcher_body_0059()),),
    )
    _replace_function_source(
        signature=VALIDATOR_SIGNATURE,
        expected_hash=VALIDATOR_BODY_SHA256_0060,
        replacement_hash=VALIDATOR_BODY_SHA256_0059,
        replacements=(
            (VALIDATOR_SEQUENCE_0060, VALIDATOR_SEQUENCE_0059),
            (VALIDATOR_ORDERED_0060, VALIDATOR_ORDERED_0059),
            (VALIDATOR_TASK_IF_0060, VALIDATOR_TASK_IF_0059),
            (VALIDATOR_TASK_COUNT_0060, VALIDATOR_TASK_COUNT_0059),
            (VALIDATOR_ACTOR_0060, VALIDATOR_ACTOR_0059),
            (VALIDATOR_DECLARATION_0060, VALIDATOR_DECLARATION_0059),
        ),
    )
    _drop_postgresql_triggers()
    op.execute(f"DROP FUNCTION public.{WRITE_GUARD_FUNCTION}()")


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0060 supports only PostgreSQL and SQLite")
    return dialect


def _lock_execution_boundary() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _write_guard_sql() -> str:
    return f"""
CREATE FUNCTION public.{WRITE_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    checked_request_id uuid;
    checked_actor_user_id text;
    checked_actor_person_id uuid;
    checked_assignment_id uuid;
    checked_authorization_version bigint;
    checked_occurred_at timestamptz;
    requester_org_id uuid;
    allow_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'material_request_commands' THEN
        IF NEW.operation NOT IN (
            'create_supply_task','update_supply_task','cancel_supply_task'
        ) THEN
            RETURN NEW;
        END IF;
        checked_request_id := NEW.request_id;
        checked_actor_user_id := NEW.actor_user_id;
        checked_actor_person_id := NEW.actor_person_id;
        checked_assignment_id := NEW.actor_role_assignment_id;
        checked_authorization_version := NEW.authorization_version;
        checked_occurred_at := NEW.occurred_at;
    ELSE
        SELECT line.request_id INTO checked_request_id
          FROM public.material_request_lines AS line
         WHERE line.id = NEW.request_line_id;
        checked_actor_user_id := NEW.created_by_user_id;
        checked_actor_person_id := NEW.created_by_person_id;
        checked_assignment_id := NEW.created_role_assignment_id;
        checked_authorization_version := NEW.authorization_version;
        checked_occurred_at := NEW.created_at;
    END IF;
    SELECT request.requester_org_id INTO requester_org_id
      FROM public.material_requests AS request
     WHERE request.id = checked_request_id
       AND request.status IN ('approved','partially_approved')
       FOR UPDATE;
    IF NOT FOUND OR checked_occurred_at > pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
    END IF;
    SELECT count(*) INTO allow_count
      FROM public.users AS actor_user
      JOIN public.people AS actor_person
        ON actor_person.id = actor_user.person_id
      JOIN public.organizations AS actor_org
        ON actor_org.id = actor_person.organization_id
      JOIN public.role_assignments AS assignment
        ON assignment.id = checked_assignment_id
       AND assignment.user_id = actor_user.id
      JOIN public.roles AS role ON role.id = assignment.role_id
      JOIN public.role_permissions AS binding
        ON binding.role_id = role.id AND binding.effect = 'allow'
      JOIN public.permissions AS permission
        ON permission.id = binding.permission_id
     WHERE actor_user.id = checked_actor_user_id
       AND actor_person.id = checked_actor_person_id
       AND actor_user.account_status = 'active' AND actor_user.is_active
       AND actor_user.authorization_version = checked_authorization_version
       AND actor_person.employment_status = 'active'
       AND actor_org.status = 'active' AND actor_org.org_type = 'headquarters'
       AND role.code = 'admin' AND role.status = 'active' AND NOT role.is_external
       AND assignment.scope_type = 'national' AND assignment.scope_id = '*'
       AND assignment.status = 'active' AND assignment.revoked_at IS NULL
       AND assignment.valid_from <= checked_occurred_at
       AND (assignment.valid_to IS NULL OR assignment.valid_to > checked_occurred_at)
       AND assignment.valid_from <= pg_catalog.clock_timestamp()
       AND (assignment.valid_to IS NULL
            OR assignment.valid_to > pg_catalog.clock_timestamp())
       AND permission.resource = 'supply_task'
       AND permission.action = 'manage' AND permission.field_code = ''
       AND EXISTS (
           SELECT 1 FROM public.auth_identities AS identity
            WHERE identity.user_id = actor_user.id
              AND identity.status = 'active'
              AND identity.verified_at IS NOT NULL
              AND identity.revoked_at IS NULL)
       AND NOT EXISTS (
           WITH RECURSIVE ancestors AS (
               SELECT organization.id, organization.parent_id
                 FROM public.organizations AS organization
                WHERE organization.id = requester_org_id
               UNION
               SELECT parent.id, parent.parent_id
                 FROM public.organizations AS parent
                 JOIN ancestors AS child ON child.parent_id = parent.id
           )
           SELECT 1
             FROM public.role_assignments AS denied_assignment
             JOIN public.roles AS denied_role
               ON denied_role.id = denied_assignment.role_id
              AND denied_role.status = 'active'
             JOIN public.role_permissions AS denied_binding
               ON denied_binding.role_id = denied_role.id
              AND denied_binding.effect = 'deny'
             JOIN public.permissions AS denied_permission
               ON denied_permission.id = denied_binding.permission_id
            WHERE denied_assignment.user_id = actor_user.id
              AND denied_assignment.status = 'active'
              AND denied_assignment.revoked_at IS NULL
              AND denied_assignment.valid_from <= pg_catalog.clock_timestamp()
              AND (denied_assignment.valid_to IS NULL
                   OR denied_assignment.valid_to > pg_catalog.clock_timestamp())
              AND denied_permission.resource = 'supply_task'
              AND denied_permission.action = 'manage'
              AND denied_permission.field_code = ''
              AND ((denied_assignment.scope_type = 'national'
                    AND denied_assignment.scope_id = '*')
                   OR (denied_assignment.scope_type = 'organization'
                       AND denied_assignment.scope_id IN (
                           SELECT ancestor.id::text FROM ancestors AS ancestor)))
       );
    IF allow_count <> 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$
"""


def _create_postgresql_triggers() -> None:
    op.execute(
        f"CREATE TRIGGER {COMMAND_GUARD_TRIGGER} BEFORE INSERT ON "
        f"public.material_request_commands FOR EACH ROW EXECUTE FUNCTION "
        f"public.{WRITE_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {TASK_GUARD_TRIGGER} BEFORE INSERT ON public.supply_tasks "
        f"FOR EACH ROW EXECUTE FUNCTION public.{WRITE_GUARD_FUNCTION}()"
    )
    for table_name, trigger_name in (
        ("material_request_commands", COMMAND_GUARD_TRIGGER),
        ("supply_tasks", TASK_GUARD_TRIGGER),
    ):
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}")
    op.execute(f"REVOKE ALL ON FUNCTION public.{WRITE_GUARD_FUNCTION}() FROM PUBLIC")
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{WRITE_GUARD_FUNCTION}() "
        f"FROM {PRODUCTION_API_ROLE}"
    )


def _drop_postgresql_triggers() -> None:
    op.execute(f"DROP TRIGGER {TASK_GUARD_TRIGGER} ON public.supply_tasks")
    op.execute(
        f"DROP TRIGGER {COMMAND_GUARD_TRIGGER} ON public.material_request_commands"
    )


def _dispatcher_body_0059() -> str:
    return """DECLARE
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
END"""


def _dispatcher_body_0060() -> str:
    return f"""DECLARE
    target_request_id uuid;
    supply_fact boolean := false;
    has_supply_command boolean := false;
BEGIN
    IF TG_TABLE_NAME = 'material_requests' THEN
        target_request_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_TABLE_NAME = 'material_request_commands' THEN
        target_request_id := COALESCE(NEW.request_id, OLD.request_id);
        supply_fact := COALESCE(NEW.operation, OLD.operation) IN (
            'create_supply_task','update_supply_task','cancel_supply_task');
    ELSIF TG_TABLE_NAME = 'supply_tasks' THEN
        supply_fact := true;
        SELECT line.request_id INTO target_request_id
          FROM public.material_request_lines AS line
         WHERE line.id = COALESCE(NEW.request_line_id, OLD.request_line_id);
    ELSIF TG_TABLE_NAME = 'state_transition_events' THEN
        supply_fact := COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'supply_task';
        IF supply_fact THEN
            SELECT line.request_id INTO target_request_id
              FROM public.supply_tasks AS task
              JOIN public.material_request_lines AS line
                ON line.id = task.request_line_id
             WHERE task.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        ELSIF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'material_request' THEN
            SELECT request.id INTO target_request_id
              FROM public.material_requests AS request
             WHERE request.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        END IF;
    ELSIF TG_TABLE_NAME = 'audit_events' THEN
        supply_fact := COALESCE(NEW.action, OLD.action) IN (
            'material_request.supply_task.create',
            'material_request.supply_task.update',
            'material_request.supply_task.cancel');
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
    IF supply_fact AND target_request_id IS NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
    END IF;
    IF target_request_id IS NOT NULL THEN
        SELECT EXISTS (
            SELECT 1 FROM public.material_request_commands AS command
             WHERE command.request_id = target_request_id
               AND command.operation IN (
                   'create_supply_task','update_supply_task','cancel_supply_task'
               )) INTO has_supply_command;
        IF supply_fact AND NOT has_supply_command THEN
            RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
        END IF;
        IF has_supply_command THEN
            PERFORM public.rsc_validate_material_request_approval_projection_0045(
                target_request_id);
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END"""


def _replace_function_source(*, signature: str, expected_hash: str,
                             replacement_hash: str,
                             replacements: tuple[tuple[str, str], ...]) -> None:
    checks = "\n".join(
        f"""IF (pg_catalog.length(function_source) - pg_catalog.length(
            pg_catalog.replace(function_source, $old${old}$old$, ''))
        ) / pg_catalog.length($old${old}$old$) <> 1
       OR pg_catalog.strpos(function_source, $new${new}$new$) <> 0 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: source replacement mismatch';
    END IF;
    function_source := pg_catalog.replace(
        function_source, $old${old}$old$, $new${new}$new$);"""
        for old, new in replacements
    )
    definition_replacements = "\n".join(
        f"function_definition := pg_catalog.replace("
        f"function_definition, $old${old}$old$, $new${new}$new$);"
        for old, new in replacements
    )
    op.execute(f"""
DO $rsc_0060_replace$
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
    {checks}
    function_definition := pg_catalog.pg_get_functiondef(function_oid);
    {definition_replacements}
    EXECUTE function_definition;
    IF (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid = function_oid) IS DISTINCT FROM
       '{replacement_hash}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: replacement function mismatch';
    END IF;
END
$rsc_0060_replace$
""")


def _verify_prerequisite_hashes() -> None:
    op.execute(f"""
DO $rsc_0060_prerequisites$
BEGIN
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = pg_catalog.to_regprocedure(
                '{VALIDATOR_SIGNATURE}')) IS DISTINCT FROM
          '{VALIDATOR_BODY_SHA256_0059}'
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = pg_catalog.to_regprocedure(
                '{DISPATCHER_SIGNATURE}')) IS DISTINCT FROM
          '{DISPATCHER_BODY_SHA256_0059}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: prerequisite mismatch';
    END IF;
END
$rsc_0060_prerequisites$
""")


def _audit_existing_graph() -> None:
    op.execute(f"""
DO $rsc_0060_existing$
DECLARE request_id uuid;
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.audit_events AS audit
         WHERE audit.action IN (
             'material_request.supply_task.create',
             'material_request.supply_task.update',
             'material_request.supply_task.cancel')
           AND NOT EXISTS (
               SELECT 1 FROM public.material_requests AS request
                WHERE audit.aggregate_type = 'material_request'
                  AND audit.aggregate_id = request.id::text)
    ) OR EXISTS (
        SELECT 1 FROM public.state_transition_events AS event
         WHERE event.aggregate_type = 'supply_task'
           AND NOT EXISTS (
               SELECT 1 FROM public.supply_tasks AS task
                WHERE task.id::text = event.aggregate_id)
    ) OR EXISTS (
        SELECT 1
          FROM public.material_request_commands AS command
          JOIN public.material_requests AS request ON request.id = command.request_id
         WHERE command.operation IN (
             'create_supply_task','update_supply_task','cancel_supply_task')
           AND (
               NOT EXISTS (
                   SELECT 1
                     FROM public.users AS actor_user
                     JOIN public.people AS actor_person
                       ON actor_person.id = actor_user.person_id
                     JOIN public.organizations AS actor_org
                       ON actor_org.id = actor_person.organization_id
                     JOIN public.role_assignments AS assignment
                       ON assignment.id = command.actor_role_assignment_id
                      AND assignment.user_id = actor_user.id
                     JOIN public.roles AS role ON role.id = assignment.role_id
                     JOIN public.role_permissions AS binding
                       ON binding.role_id = role.id AND binding.effect = 'allow'
                     JOIN public.permissions AS permission
                       ON permission.id = binding.permission_id
                    WHERE actor_user.id = command.actor_user_id
                      AND actor_person.id = command.actor_person_id
                      AND actor_org.org_type = 'headquarters'
                      AND role.code = 'admin' AND NOT role.is_external
                      AND assignment.scope_type = 'national'
                      AND assignment.scope_id = '*'
                      AND assignment.valid_from <= command.occurred_at
                      AND (assignment.valid_to IS NULL
                           OR assignment.valid_to > command.occurred_at)
                      AND (assignment.revoked_at IS NULL
                           OR assignment.revoked_at > command.occurred_at)
                      AND permission.resource = 'supply_task'
                      AND permission.action = 'manage'
                      AND permission.field_code = ''
                      AND EXISTS (
                          SELECT 1 FROM public.auth_identities AS identity
                           WHERE identity.user_id = actor_user.id
                             AND identity.verified_at IS NOT NULL
                             AND identity.verified_at <= command.occurred_at
                             AND (identity.revoked_at IS NULL
                                  OR identity.revoked_at > command.occurred_at))
               ) OR EXISTS (
                   WITH RECURSIVE ancestors AS (
                       SELECT organization.id, organization.parent_id
                         FROM public.organizations AS organization
                        WHERE organization.id = request.requester_org_id
                       UNION
                       SELECT parent.id, parent.parent_id
                         FROM public.organizations AS parent
                         JOIN ancestors AS child ON child.parent_id = parent.id
                   )
                   SELECT 1
                     FROM public.role_assignments AS denied_assignment
                     JOIN public.roles AS denied_role
                       ON denied_role.id = denied_assignment.role_id
                     JOIN public.role_permissions AS denied_binding
                       ON denied_binding.role_id = denied_role.id
                      AND denied_binding.effect = 'deny'
                     JOIN public.permissions AS denied_permission
                       ON denied_permission.id = denied_binding.permission_id
                    WHERE denied_assignment.user_id = command.actor_user_id
                      AND denied_assignment.valid_from <= command.occurred_at
                      AND (denied_assignment.valid_to IS NULL
                           OR denied_assignment.valid_to > command.occurred_at)
                      AND (denied_assignment.revoked_at IS NULL
                           OR denied_assignment.revoked_at > command.occurred_at)
                      AND denied_permission.resource = 'supply_task'
                      AND denied_permission.action = 'manage'
                      AND denied_permission.field_code = ''
                      AND ((denied_assignment.scope_type = 'national'
                            AND denied_assignment.scope_id = '*')
                           OR (denied_assignment.scope_type = 'organization'
                               AND denied_assignment.scope_id IN (
                                   SELECT ancestor.id::text
                                     FROM ancestors AS ancestor)))
               )
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
    END IF;
    FOR request_id IN
        SELECT DISTINCT command.request_id
          FROM public.material_request_commands AS command
         WHERE command.operation IN (
             'create_supply_task','update_supply_task','cancel_supply_task')
         ORDER BY command.request_id
    LOOP
        PERFORM public.rsc_validate_material_request_approval_projection_0045(request_id);
    END LOOP;
END
$rsc_0060_existing$
""")


def _replace_runtime_ready(*, expected_hash: str, replacement_hash: str,
                           old_revision: str, new_revision: str) -> None:
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_hash=expected_hash,
        replacement_hash=replacement_hash,
        replacements=((old_revision, new_revision),),
    )


def _require_empty_supply_graph() -> None:
    op.execute(f"""
DO $rsc_0060_empty$
BEGIN
    IF EXISTS (SELECT 1 FROM public.supply_tasks)
       OR EXISTS (SELECT 1 FROM public.material_request_commands WHERE operation IN (
           'create_supply_task','update_supply_task','cancel_supply_task'))
       OR EXISTS (SELECT 1 FROM public.audit_events WHERE action IN (
           'material_request.supply_task.create','material_request.supply_task.update',
           'material_request.supply_task.cancel'))
       OR EXISTS (SELECT 1 FROM public.state_transition_events
                   WHERE aggregate_type = 'supply_task') THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$rsc_0060_empty$
""")


def _verify_postgresql_catalog() -> None:
    op.execute(f"""
DO $rsc_0060_catalog$
DECLARE
    migrator_oid oid := (SELECT oid FROM pg_catalog.pg_roles
                          WHERE rolname = '{MIGRATION_ROLE}');
    api_oid oid := (SELECT oid FROM pg_catalog.pg_roles
                     WHERE rolname = '{PRODUCTION_API_ROLE}');
    function_oid oid := pg_catalog.to_regprocedure(
        'public.{WRITE_GUARD_FUNCTION}()');
BEGIN
    IF function_oid IS NULL OR migrator_oid IS NULL OR api_oid IS NULL
       OR (SELECT function_row.proowner <> migrator_oid
                  OR NOT function_row.prosecdef OR function_row.provolatile <> 'v'
                  OR function_row.prokind <> 'f' OR function_row.proretset
                  OR function_row.pronargs <> 0 OR function_row.pronargdefaults <> 0
                  OR function_row.provariadic <> 0
                  OR function_row.proargnames IS NOT NULL
                  OR function_row.proargmodes IS NOT NULL
                  OR function_row.proallargtypes IS NOT NULL
                  OR function_row.proargdefaults IS NOT NULL
                  OR function_row.proisstrict OR function_row.proleakproof
                  OR function_row.proparallel <> 'u'
                  OR function_row.proconfig <> ARRAY['search_path=pg_catalog, public']
                  OR pg_catalog.pg_get_function_result(function_row.oid) <> 'trigger'
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid)
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row WHERE function_row.oid=function_oid)
          IS DISTINCT FROM '{WRITE_GUARD_BODY_SHA256_0060}'
       OR pg_catalog.has_function_privilege(api_oid, function_oid, 'EXECUTE')
       OR EXISTS (
           SELECT 1 FROM pg_catalog.aclexplode(
               (SELECT proacl FROM pg_catalog.pg_proc WHERE oid=function_oid)) AS acl
            WHERE acl.grantee = 0 OR acl.grantor <> migrator_oid
               OR acl.is_grantable)
       OR (SELECT count(*) FROM pg_catalog.pg_trigger AS trigger_row
            WHERE trigger_row.tgname IN ('{COMMAND_GUARD_TRIGGER}','{TASK_GUARD_TRIGGER}')
              AND trigger_row.tgfoid=function_oid AND trigger_row.tgenabled='A'
              AND NOT trigger_row.tgisinternal AND trigger_row.tgtype=7
              AND trigger_row.tgconstraint=0 AND trigger_row.tgnargs=0
              AND trigger_row.tgqual IS NULL AND trigger_row.tgparentid=0
              AND trigger_row.tgoldtable IS NULL
              AND trigger_row.tgnewtable IS NULL) <> 2
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('material_request_commands','{COMMAND_GUARD_TRIGGER}'),
               ('supply_tasks','{TASK_GUARD_TRIGGER}')
           ) AS expected(table_name, trigger_name)
           LEFT JOIN pg_catalog.pg_trigger AS trigger_row
             ON trigger_row.tgname=expected.trigger_name
           LEFT JOIN pg_catalog.pg_class AS table_row
             ON table_row.oid=trigger_row.tgrelid
           LEFT JOIN pg_catalog.pg_namespace AS namespace_row
             ON namespace_row.oid=table_row.relnamespace
          WHERE table_row.relname IS DISTINCT FROM expected.table_name
             OR namespace_row.nspname IS DISTINCT FROM 'public')
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('{VALIDATOR_SIGNATURE}','void','uuid, bigint',
                'checked_request_id,approval_command_version'),
               ('{DISPATCHER_SIGNATURE}','trigger','','')
           ) AS expected(signature, return_type, arguments, argument_names)
           LEFT JOIN pg_catalog.pg_proc AS function_row
             ON function_row.oid=pg_catalog.to_regprocedure(expected.signature)
           LEFT JOIN pg_catalog.pg_language AS language_row
             ON language_row.oid=function_row.prolang
          WHERE function_row.oid IS NULL OR function_row.proowner<>migrator_oid
             OR NOT function_row.prosecdef OR function_row.provolatile<>'v'
             OR function_row.prokind<>'f' OR function_row.proretset
             OR function_row.pronargdefaults<>0 OR function_row.provariadic<>0
             OR function_row.proargmodes IS NOT NULL
             OR function_row.proallargtypes IS NOT NULL
             OR function_row.proargdefaults IS NOT NULL
             OR function_row.proisstrict OR function_row.proleakproof
             OR function_row.proparallel<>'u' OR language_row.lanname<>'plpgsql'
             OR function_row.proconfig<>
                ARRAY['search_path=pg_catalog, public']
             OR pg_catalog.pg_get_function_result(function_row.oid)<>
                expected.return_type
             OR pg_catalog.oidvectortypes(function_row.proargtypes)<>
                expected.arguments
             OR COALESCE(pg_catalog.array_to_string(
                    function_row.proargnames,','),'')<>expected.argument_names
             OR pg_catalog.has_function_privilege(
                api_oid,function_row.oid,'EXECUTE')
             OR EXISTS (SELECT 1 FROM pg_catalog.aclexplode(function_row.proacl) acl
                         WHERE acl.grantee=0 OR acl.grantor<>migrator_oid
                            OR acl.is_grantable))
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid=pg_catalog.to_regprocedure('{VALIDATOR_SIGNATURE}'))
          IS DISTINCT FROM '{VALIDATOR_BODY_SHA256_0060}'
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid=pg_catalog.to_regprocedure('{DISPATCHER_SIGNATURE}'))
          IS DISTINCT FROM '{DISPATCHER_BODY_SHA256_0060}'
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid=pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}'))
          IS DISTINCT FROM '{RUNTIME_READY_BODY_SHA256_0060}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}';
    END IF;
END
$rsc_0060_catalog$
""")


def _create_sqlite_guards() -> None:
    op.execute(f"""
CREATE TRIGGER trg_supply_audit_owner_guard_0060 BEFORE INSERT ON audit_events
WHEN NEW.action IN ('material_request.supply_task.create',
                    'material_request.supply_task.update',
                    'material_request.supply_task.cancel')
 AND (NEW.aggregate_type <> 'material_request' OR NOT EXISTS (
     SELECT 1 FROM material_requests AS request
      WHERE lower(replace(request.id, '-', '')) =
            lower(replace(NEW.aggregate_id, '-', ''))))
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
""")
    op.execute(f"""
CREATE TRIGGER trg_supply_state_owner_guard_0060
BEFORE INSERT ON state_transition_events
WHEN NEW.aggregate_type = 'supply_task' AND NOT EXISTS (
    SELECT 1 FROM supply_tasks AS task
     WHERE lower(replace(task.id, '-', '')) =
           lower(replace(NEW.aggregate_id, '-', '')))
BEGIN SELECT RAISE(ABORT, '{GUARD_ERROR}'); END
""")


def _drop_sqlite_guards() -> None:
    op.execute("DROP TRIGGER trg_supply_state_owner_guard_0060")
    op.execute("DROP TRIGGER trg_supply_audit_owner_guard_0060")


def _require_empty_sqlite_supply_graph() -> None:
    has_facts = op.get_bind().exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM supply_tasks) OR EXISTS ("
        "SELECT 1 FROM material_request_commands WHERE operation IN ("
        "'create_supply_task','update_supply_task','cancel_supply_task')) OR EXISTS ("
        "SELECT 1 FROM audit_events WHERE action IN ("
        "'material_request.supply_task.create','material_request.supply_task.update',"
        "'material_request.supply_task.cancel')) OR EXISTS ("
        "SELECT 1 FROM state_transition_events WHERE aggregate_type='supply_task')"
    ).scalar_one()
    if bool(has_facts):
        raise RuntimeError(DOWNGRADE_BLOCKER)
