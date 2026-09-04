"""Fix the PostgreSQL supply state-event idempotency-key expression.

Revision ID: 20260905_0061
Revises: 20260905_0060
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260905_0061"
down_revision: str | None = "20260905_0060"
branch_labels: str | None = None
depends_on: str | None = None

MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PROJECTOR_ROLE = "star_oam_projector"
EDGE_ROLE = "edge_inbox"

VALIDATOR_SIGNATURE = (
    "public.rsc_validate_material_request_supply_causality_0059(uuid, bigint)"
)
DISPATCHER_SIGNATURE = (
    "public.rsc_dispatch_material_request_supply_causality_0059()"
)
WRITE_GUARD_SIGNATURE = "public.rsc_guard_material_request_supply_write_0060()"
RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"

PRIOR_VALIDATOR_BODY_SHA256 = (
    "092a41ff7072397c8b8311f3bab658dcc4a69212a4bf893dc9e9271a0dd625f0"
)
FIXED_VALIDATOR_BODY_SHA256 = (
    "f5803e9a3e0c931228692260c04c9bd4e544eb147aea45192277e57c7b969403"
)
DISPATCHER_BODY_SHA256 = (
    "935be3c5144f0bb9d5dad8652bb8284bc7116caaefc9ccb5d177e922b41530c5"
)
WRITE_GUARD_BODY_SHA256 = (
    "0fe289826a8aa4d14e2ecd48a9900484bf8929a054dbfa52340fa27786453f26"
)
RUNTIME_READY_BODY_SHA256_0060 = (
    "47ede12ff8b812e78aa0bb325ce6445b3dd41f6d1fd512ee86055c7418e00bf3"
)
RUNTIME_READY_BODY_SHA256_0061 = (
    "900dd22c6ff47e807dddd6dd6110449e433dde3bc410057ad03472e7216bc53c"
)

LEGACY_EVENT_KEY_EXPRESSION = """           AND event.idempotency_key = 'mr-supply:' ||
               command_row.idempotency_key_hash || ':' ||
               command_row.result_jsonb->>'task_version'"""
FIXED_EVENT_KEY_EXPRESSION = """           AND event.idempotency_key = 'mr-supply:' ||
               command_row.idempotency_key_hash || ':' ||
               (command_row.result_jsonb->>'task_version')"""

CATALOG_ERROR = "0061 material request supply catalog verification failed"
DOWNGRADE_BLOCKER = "cannot downgrade 0061 while supply-task facts exist"

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

SUPPLY_CAUSALITY_TRIGGERS = tuple(
    (table_name, f"trg_{table_name}_supply_causality_0059")
    for table_name in (
        "audit_events",
        "material_request_commands",
        "material_requests",
        "state_transition_events",
        "supply_tasks",
    )
)


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    _lock_execution_boundary()
    _verify_postgresql_catalog(
        validator_hash=PRIOR_VALIDATOR_BODY_SHA256,
        readiness_hash=RUNTIME_READY_BODY_SHA256_0060,
    )
    _replace_function_source(
        signature=VALIDATOR_SIGNATURE,
        expected_hash=PRIOR_VALIDATOR_BODY_SHA256,
        replacement_hash=FIXED_VALIDATOR_BODY_SHA256,
        old=LEGACY_EVENT_KEY_EXPRESSION,
        new=FIXED_EVENT_KEY_EXPRESSION,
    )
    _audit_existing_supply_graph()
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_hash=RUNTIME_READY_BODY_SHA256_0060,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0061,
        old=down_revision,
        new=revision,
    )
    _verify_postgresql_catalog(
        validator_hash=FIXED_VALIDATOR_BODY_SHA256,
        readiness_hash=RUNTIME_READY_BODY_SHA256_0061,
    )


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        _require_empty_sqlite_supply_graph()
        return
    _lock_execution_boundary()
    _require_empty_postgresql_supply_graph()
    _verify_postgresql_catalog(
        validator_hash=FIXED_VALIDATOR_BODY_SHA256,
        readiness_hash=RUNTIME_READY_BODY_SHA256_0061,
    )
    _replace_function_source(
        signature=VALIDATOR_SIGNATURE,
        expected_hash=FIXED_VALIDATOR_BODY_SHA256,
        replacement_hash=PRIOR_VALIDATOR_BODY_SHA256,
        old=FIXED_EVENT_KEY_EXPRESSION,
        new=LEGACY_EVENT_KEY_EXPRESSION,
    )
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_hash=RUNTIME_READY_BODY_SHA256_0061,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0060,
        old=revision,
        new=down_revision,
    )
    _verify_postgresql_catalog(
        validator_hash=PRIOR_VALIDATOR_BODY_SHA256,
        readiness_hash=RUNTIME_READY_BODY_SHA256_0060,
    )


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0061 supports only PostgreSQL and SQLite")
    return dialect


def _lock_execution_boundary() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _replace_function_source(
    *,
    signature: str,
    expected_hash: str,
    replacement_hash: str,
    old: str,
    new: str,
) -> None:
    op.execute(
        f"""
DO $rsc_0061_replace$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{signature}');
    original_owner oid;
    original_acl aclitem[];
    original_security boolean;
    original_config text[];
    function_source text;
    function_definition text;
BEGIN
    SELECT function_row.proowner, function_row.proacl,
           function_row.prosecdef, function_row.proconfig,
           function_row.prosrc, pg_catalog.pg_get_functiondef(function_row.oid)
      INTO original_owner, original_acl, original_security, original_config,
           function_source, function_definition
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex') = '{expected_hash}';
    IF function_source IS NULL
       OR (pg_catalog.length(function_source) - pg_catalog.length(
               pg_catalog.replace(function_source, $old${old}$old$, ''))
          ) / pg_catalog.length($old${old}$old$) <> 1
       OR pg_catalog.strpos(function_source, $new${new}$new$) <> 0 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: source replacement mismatch';
    END IF;
    function_definition := pg_catalog.replace(
        function_definition, $old${old}$old$, $new${new}$new$);
    EXECUTE function_definition;
    IF pg_catalog.to_regprocedure('{signature}') IS DISTINCT FROM function_oid
       OR (SELECT function_row.proowner IS DISTINCT FROM original_owner
                  OR function_row.proacl IS DISTINCT FROM original_acl
                  OR function_row.prosecdef IS DISTINCT FROM original_security
                  OR function_row.proconfig IS DISTINCT FROM original_config
                  OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                         function_row.prosrc, 'UTF8')), 'hex') IS DISTINCT FROM
                     '{replacement_hash}'
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: replacement drift';
    END IF;
END
$rsc_0061_replace$
"""
    )


def _audit_existing_supply_graph() -> None:
    op.execute(
        f"""
DO $rsc_0061_existing$
DECLARE checked_request_id uuid;
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.audit_events AS audit
         WHERE audit.action IN (
             'material_request.supply_task.create',
             'material_request.supply_task.update',
             'material_request.supply_task.cancel')
           AND (
               audit.aggregate_type <> 'material_request'
               OR NOT EXISTS (
                   SELECT 1
                     FROM public.material_requests AS request
                    WHERE audit.aggregate_id = request.id::text))
    ) OR EXISTS (
        SELECT 1
          FROM public.state_transition_events AS event
         WHERE event.aggregate_type = 'supply_task'
           AND NOT EXISTS (
               SELECT 1
                 FROM public.supply_tasks AS task
                WHERE event.aggregate_id = task.id::text)
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: orphan supply fact';
    END IF;
    FOR checked_request_id IN
        SELECT request_graph.request_id
          FROM (
              SELECT command.request_id
                FROM public.material_request_commands AS command
               WHERE command.operation IN (
                   'create_supply_task','update_supply_task','cancel_supply_task')
              UNION
              SELECT line.request_id
                FROM public.supply_tasks AS task
                JOIN public.material_request_lines AS line
                  ON line.id = task.request_line_id
              UNION
              SELECT request.id
                FROM public.audit_events AS audit
                JOIN public.material_requests AS request
                  ON audit.aggregate_id = request.id::text
               WHERE audit.action IN (
                   'material_request.supply_task.create',
                   'material_request.supply_task.update',
                   'material_request.supply_task.cancel')
          ) AS request_graph
         ORDER BY request_graph.request_id
    LOOP
        PERFORM public.rsc_validate_material_request_approval_projection_0045(
            checked_request_id);
    END LOOP;
END
$rsc_0061_existing$
"""
    )


def _supply_fact_exists_sql(prefix: str) -> str:
    return f"""EXISTS (SELECT 1 FROM {prefix}supply_tasks)
       OR EXISTS (SELECT 1 FROM {prefix}material_request_commands
                   WHERE operation IN (
                       'create_supply_task','update_supply_task','cancel_supply_task'))
       OR EXISTS (SELECT 1 FROM {prefix}audit_events
                   WHERE action IN (
                       'material_request.supply_task.create',
                       'material_request.supply_task.update',
                       'material_request.supply_task.cancel'))
       OR EXISTS (SELECT 1 FROM {prefix}state_transition_events
                   WHERE aggregate_type = 'supply_task')"""


def _require_empty_postgresql_supply_graph() -> None:
    op.execute(
        f"""
DO $rsc_0061_empty$
BEGIN
    IF {_supply_fact_exists_sql('public.')} THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$rsc_0061_empty$
"""
    )


def _require_empty_sqlite_supply_graph() -> None:
    found = op.get_bind().execute(
        sa.text(f"SELECT 1 WHERE {_supply_fact_exists_sql('')} LIMIT 1")
    ).first()
    if found is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _verify_postgresql_catalog(*, validator_hash: str, readiness_hash: str) -> None:
    trigger_values = ",\n                ".join(
        f"('{table_name}','{trigger_name}')"
        for table_name, trigger_name in SUPPLY_CAUSALITY_TRIGGERS
    )
    op.execute(
        f"""
DO $rsc_0061_catalog$
DECLARE
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    projector_oid oid := pg_catalog.to_regrole('{PROJECTOR_ROLE}');
    edge_oid oid := pg_catalog.to_regrole('{EDGE_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR migrator_oid IS NULL OR api_oid IS NULL
       OR projector_oid IS NULL OR edge_oid IS NULL THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: role mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES
                ('{VALIDATOR_SIGNATURE}','plpgsql','void','uuid, bigint',
                 'checked_request_id,approval_command_version',true,'v',
                 ARRAY['search_path=pg_catalog, public']::text[],'{validator_hash}'),
                ('{DISPATCHER_SIGNATURE}','plpgsql','trigger','','',true,'v',
                 ARRAY['search_path=pg_catalog, public']::text[],'{DISPATCHER_BODY_SHA256}'),
                ('{WRITE_GUARD_SIGNATURE}','plpgsql','trigger','','',true,'v',
                 ARRAY['search_path=pg_catalog, public']::text[],'{WRITE_GUARD_BODY_SHA256}'),
                ('{RUNTIME_READY_SIGNATURE}','sql','boolean','','',true,'s',
                 ARRAY['search_path=pg_catalog']::text[],'{readiness_hash}')
          ) AS expected(signature, language_name, return_type, arguments,
                        argument_names, security_definer, volatility,
                        function_config, body_hash)
          LEFT JOIN pg_catalog.pg_proc AS function_row
            ON function_row.oid = pg_catalog.to_regprocedure(expected.signature)
          LEFT JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
          LEFT JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid IS NULL
            OR namespace_row.nspname <> 'public'
            OR function_row.proowner <> migrator_oid
            OR function_row.prokind <> 'f' OR function_row.proretset
            OR pg_catalog.pg_get_function_result(function_row.oid) <>
               expected.return_type
            OR pg_catalog.oidvectortypes(function_row.proargtypes) <>
               expected.arguments
            OR COALESCE(pg_catalog.array_to_string(
                   function_row.proargnames, ','), '') <> expected.argument_names
            OR function_row.proallargtypes IS NOT NULL
            OR function_row.proargmodes IS NOT NULL
            OR function_row.pronargdefaults <> 0
            OR function_row.proargdefaults IS NOT NULL
            OR function_row.provariadic <> 0
            OR language_row.lanname <> expected.language_name
            OR function_row.provolatile <> expected.volatility
            OR function_row.prosecdef <> expected.security_definer
            OR function_row.proisstrict OR function_row.proleakproof
            OR function_row.proparallel <> 'u'
            OR function_row.proconfig IS DISTINCT FROM expected.function_config
            OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                   function_row.prosrc, 'UTF8')), 'hex') <> expected.body_hash
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: function shape mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES ('{VALIDATOR_SIGNATURE}'),('{DISPATCHER_SIGNATURE}'),
                       ('{WRITE_GUARD_SIGNATURE}')) AS expected(signature)
          JOIN pg_catalog.pg_proc AS function_row
            ON function_row.oid = pg_catalog.to_regprocedure(expected.signature)
          CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(
              function_row.proacl,
              pg_catalog.acldefault('f', function_row.proowner))) AS acl
         WHERE acl.privilege_type <> 'EXECUTE'
            OR acl.grantee <> migrator_oid OR acl.grantor <> migrator_oid
            OR acl.is_grantable
    ) OR (
        SELECT count(*)
          FROM (VALUES ('{VALIDATOR_SIGNATURE}'),('{DISPATCHER_SIGNATURE}'),
                       ('{WRITE_GUARD_SIGNATURE}')) AS expected(signature)
          JOIN pg_catalog.pg_proc AS function_row
            ON function_row.oid = pg_catalog.to_regprocedure(expected.signature)
          CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(
              function_row.proacl,
              pg_catalog.acldefault('f', function_row.proowner))) AS acl
         WHERE acl.privilege_type = 'EXECUTE'
           AND acl.grantee = migrator_oid AND acl.grantor = migrator_oid
           AND NOT acl.is_grantable
    ) <> 3 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: private function ACL mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(
              function_row.proacl,
              pg_catalog.acldefault('f', function_row.proowner))) AS acl
         WHERE function_row.oid = pg_catalog.to_regprocedure(
                   '{RUNTIME_READY_SIGNATURE}')
           AND (acl.privilege_type <> 'EXECUTE'
                OR acl.grantee NOT IN (migrator_oid, projector_oid, edge_oid)
                OR acl.grantor <> migrator_oid OR acl.is_grantable)
    ) OR (
        SELECT count(*)
          FROM pg_catalog.pg_proc AS function_row
          CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(
              function_row.proacl,
              pg_catalog.acldefault('f', function_row.proowner))) AS acl
         WHERE function_row.oid = pg_catalog.to_regprocedure(
                   '{RUNTIME_READY_SIGNATURE}')
           AND acl.privilege_type = 'EXECUTE'
           AND acl.grantee IN (migrator_oid, projector_oid, edge_oid)
           AND acl.grantor = migrator_oid AND NOT acl.is_grantable
    ) <> 3 OR pg_catalog.has_function_privilege(
                 api_oid, '{RUNTIME_READY_SIGNATURE}', 'EXECUTE') THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness ACL mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES {trigger_values}) AS expected(table_name, trigger_name)
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
            OR trigger_row.tgenabled <> 'A' OR trigger_row.tgtype <> 29
            OR function_row.oid <> pg_catalog.to_regprocedure(
                   '{DISPATCHER_SIGNATURE}')
            OR trigger_row.tgconstraint = 0
            OR NOT trigger_row.tgdeferrable OR NOT trigger_row.tginitdeferred
            OR trigger_row.tgnargs <> 0 OR trigger_row.tgqual IS NOT NULL
            OR trigger_row.tgoldtable IS NOT NULL
            OR trigger_row.tgnewtable IS NOT NULL
            OR trigger_row.tgparentid <> 0
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: trigger mismatch';
    END IF;
END
$rsc_0061_catalog$
"""
    )
