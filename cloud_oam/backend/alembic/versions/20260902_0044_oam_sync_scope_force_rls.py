"""Bind OAM edge/projector credentials to exact rows with forced RLS.

Revision ID: 20260902_0044
Revises: 20260902_0043
Create Date: 2026-09-02

The v1 baseline makes OAM an immutable external source.  Table/column ACLs
alone cannot stop a compromised edge or projector credential from moving
sideways inside an allowed table, so this revision adds a migrator-owned exact
scope registry and PostgreSQL FORCE ROW LEVEL SECURITY over the complete edge
staging and formal projection subgraphs.  Runtime identity is derived only
from ``session_user``; no caller-controlled GUC participates in authorization.
Because revision 0043 was never approved to carry trusted sync rows, both
upgrade and downgrade require the complete OAM sync graph to be empty.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0044"
down_revision: Union[str, Sequence[str], None] = "20260902_0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
API_ROLE = "star_oam_api"
BACKUP_ROLE = "star_oam_backup"
PROJECTOR_ROLE = "star_oam_projector"
EDGE_ROLE = "edge_inbox"

BINDING_TABLE = "oam_sync_scope_bindings"
RLS_CHECK_FUNCTION = (
    "public.rsc_oam_rls_check_0044(text,text,jsonb)"
)
RUNTIME_READY_FUNCTION = "public.rsc_oam_runtime_binding_ready_0044()"
SCOPE_KEY_FUNCTION = "public.rsc_oam_formal_scope_key_0044(text,text)"
PRIVATE_HELPER_FUNCTIONS = (
    SCOPE_KEY_FUNCTION,
    "public.rsc_oam_binding_allowed_0044(text,text,text,text,text,text,text,text)",
    "public.rsc_oam_run_snapshot_allowed_0044(text,uuid,text,text,text,text,text)",
    "public.rsc_oam_inbox_allowed_0044(text,text,uuid,uuid,text,text,text,text,timestamptz,jsonb,text,text)",
    "public.rsc_oam_external_object_allowed_0044(text,text,uuid,uuid,text,text,uuid)",
    "public.rsc_oam_version_allowed_0044(text,text,uuid,uuid,text,timestamptz,timestamptz,timestamptz,jsonb,text,boolean,timestamptz)",
    "public.rsc_oam_conflict_allowed_0044(text,uuid,uuid,uuid)",
    "public.rsc_oam_organization_allowed_0044(uuid)",
    "public.rsc_oam_person_allowed_0044(uuid)",
    "public.rsc_oam_work_order_allowed_0044(text,text,uuid,text,uuid,uuid,text,timestamptz,timestamptz,timestamptz)",
    "public.rsc_oam_snapshot_transition_guard_0044()",
    "public.rsc_oam_projection_chain_guard_0044()",
)
ALL_RLS_FUNCTIONS = PRIVATE_HELPER_FUNCTIONS + (
    RLS_CHECK_FUNCTION,
    RUNTIME_READY_FUNCTION,
)

STAGING_TABLES = (
    "external_sync_snapshots",
    "external_sync_snapshot_batches",
    "external_sync_snapshot_records",
    "external_sync_current_records",
    "audit_logs",
)
FORMAL_TABLES = (
    "source_systems",
    "sync_runs",
    "sync_batches",
    "sync_inbox_events",
    "external_objects",
    "external_object_versions",
    "external_object_mappings",
    "sync_conflicts",
    "organizations",
    "people",
    "oam_work_orders",
)
RLS_TABLES = (BINDING_TABLE,) + STAGING_TABLES + FORMAL_TABLES
API_SELECT_TABLES = (
    "source_systems",
    "sync_runs",
    "sync_batches",
    "sync_inbox_events",
    "external_objects",
    "external_object_versions",
    "organizations",
    "people",
    "oam_work_orders",
)

PROJECTOR_READ_TABLES = STAGING_TABLES[:4] + FORMAL_TABLES
PROJECTOR_INSERT_COLUMNS = {
    "sync_runs": (
        "id", "source_system_id", "run_key", "scope_key", "mode",
        "watermark_from", "watermark_to", "status", "manifest_sha256",
        "started_at", "completed_at", "failure_code", "failure_detail",
        "created_at", "updated_at",
    ),
    "sync_batches": (
        "id", "run_id", "entity_type", "sequence", "record_count",
        "body_sha256", "status", "received_at", "validated_at", "created_at",
    ),
    "sync_inbox_events": (
        "id", "batch_id", "source_system_id", "external_event_id",
        "entity_type", "external_id", "source_version", "source_updated_at",
        "payload_jsonb", "payload_sha256", "status", "error_code",
        "error_detail", "processed_at", "created_at",
    ),
    "external_objects": (
        "id", "source_system_id", "entity_type", "external_id",
        "current_version_id", "deleted_at", "created_at", "updated_at",
    ),
    "external_object_versions": (
        "id", "external_object_id", "source_version", "source_updated_at",
        "valid_from", "valid_to", "payload_jsonb", "payload_sha256",
        "is_current", "created_at",
    ),
    "sync_conflicts": (
        "id", "run_id", "inbox_event_id", "external_object_id", "dedup_key",
        "conflict_type", "external_value_jsonb", "local_value_jsonb", "status",
        "resolution_jsonb", "resolved_by", "resolved_at", "created_at",
        "updated_at",
    ),
    "oam_work_orders": (
        "id", "external_object_id", "work_order_no", "organization_id",
        "engineer_person_id", "status", "source_updated_at", "created_at",
        "updated_at",
    ),
}
PROJECTOR_UPDATE_COLUMNS = {
    "sync_runs": (
        "status", "manifest_sha256", "completed_at", "failure_code",
        "failure_detail", "updated_at",
    ),
    "sync_batches": ("status", "validated_at"),
    "sync_inbox_events": (
        "status", "error_code", "error_detail", "processed_at",
    ),
    "external_objects": ("current_version_id", "updated_at"),
    "external_object_versions": ("is_current", "valid_to"),
    "sync_conflicts": (
        "status", "resolution_jsonb", "resolved_by", "resolved_at", "updated_at",
    ),
    "oam_work_orders": (
        "work_order_no", "organization_id", "engineer_person_id", "status",
        "source_updated_at", "updated_at",
    ),
}


def _policy_expression(table_name: str, command: str) -> str:
    return (
        "rsc_oam_rls_check_0044("
        f"'{table_name}'::text, '{command}'::text, "
        f"to_jsonb({table_name}.*))"
    )


def _expected_policy_roster() -> tuple[
    tuple[str, str, str, str, str | None, str | None], ...
]:
    policies: list[
        tuple[str, str, str, str, str | None, str | None]
    ] = []
    for table_name in RLS_TABLES:
        policies.extend(
            (
                (
                    table_name,
                    f"{table_name}_migrator_0044",
                    "*",
                    MIGRATION_ROLE,
                    "true",
                    "true",
                ),
                (
                    table_name,
                    f"{table_name}_backup_select_0044",
                    "r",
                    BACKUP_ROLE,
                    "true",
                    None,
                ),
            )
        )
    for table_name in API_SELECT_TABLES:
        policies.append(
            (
                table_name,
                f"{table_name}_api_select_0044",
                "r",
                API_ROLE,
                "true",
                None,
            )
        )
    for table_name in STAGING_TABLES[:4] + FORMAL_TABLES:
        policies.append(
            (
                table_name,
                f"{table_name}_projector_select_0044",
                "r",
                PROJECTOR_ROLE,
                _policy_expression(table_name, "select"),
                None,
            )
        )
    for table_name in PROJECTOR_INSERT_COLUMNS:
        policies.append(
            (
                table_name,
                f"{table_name}_projector_insert_0044",
                "a",
                PROJECTOR_ROLE,
                None,
                _policy_expression(table_name, "insert"),
            )
        )
    for table_name in PROJECTOR_UPDATE_COLUMNS:
        old_command = (
            "update_old"
            if table_name in {
                "external_objects",
                "external_object_versions",
                "oam_work_orders",
            }
            else "update"
        )
        new_command = (
            "update_new"
            if table_name in {
                "external_objects",
                "external_object_versions",
                "oam_work_orders",
            }
            else "update"
        )
        policies.append(
            (
                table_name,
                f"{table_name}_projector_update_0044",
                "w",
                PROJECTOR_ROLE,
                _policy_expression(table_name, old_command),
                _policy_expression(table_name, new_command),
            )
        )
    for table_name in STAGING_TABLES[:4]:
        policies.extend(
            (
                (
                    table_name,
                    f"{table_name}_edge_select_0044",
                    "r",
                    EDGE_ROLE,
                    _policy_expression(table_name, "select"),
                    None,
                ),
                (
                    table_name,
                    f"{table_name}_edge_insert_0044",
                    "a",
                    EDGE_ROLE,
                    None,
                    _policy_expression(table_name, "insert"),
                ),
            )
        )
    for table_name in (
        "external_sync_snapshots",
        "external_sync_current_records",
    ):
        old_command = (
            "select"
            if table_name == "external_sync_current_records"
            else "update"
        )
        new_command = (
            "insert"
            if table_name == "external_sync_current_records"
            else "update"
        )
        policies.append(
            (
                table_name,
                f"{table_name}_edge_update_0044",
                "w",
                EDGE_ROLE,
                _policy_expression(table_name, old_command),
                _policy_expression(table_name, new_command),
            )
        )
    policies.extend(
        (
            (
                "external_sync_current_records",
                "external_sync_current_records_edge_delete_0044",
                "d",
                EDGE_ROLE,
                _policy_expression(
                    "external_sync_current_records", "delete"
                ),
                None,
            ),
            (
                "audit_logs",
                "audit_logs_edge_insert_0044",
                "a",
                EDGE_ROLE,
                None,
                _policy_expression("audit_logs", "insert"),
            ),
        )
    )
    return tuple(policies)


EXPECTED_POLICY_ROSTER = _expected_policy_roster()


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0044 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    _require_roles_and_schema()
    _require_empty_oam_sync_graph()
    op.execute(_SCOPE_KEY_FUNCTION_SQL)
    _create_binding_table()
    _create_rls_functions()
    _apply_forced_rls()
    _restore_projector_acl()
    _close_binding_and_function_acl()
    _verify_installation()


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    # Reopening the 0043 table/column boundary around an established formal
    # projection would discard the row-level provenance guarantee.  Require
    # an explicit, independently reviewed deprojection before downgrade.
    _require_empty_oam_sync_graph()
    # Fail closed before removing any row predicate.  Revision 0043's broad
    # projector ACL is deliberately not restored by this downgrade.
    _revoke_runtime_acl()
    op.execute(
        "DROP TRIGGER IF EXISTS trg_external_sync_snapshot_transition_0044 "
        "ON public.external_sync_snapshots"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_external_objects_chain_0044 "
        "ON public.external_objects"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_external_object_versions_chain_0044 "
        "ON public.external_object_versions"
    )
    for table_name in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table_name}_migrator_0044 ON public.{table_name}")
        op.execute(f"DROP POLICY IF EXISTS {table_name}_backup_select_0044 ON public.{table_name}")
        op.execute(f"DROP POLICY IF EXISTS {table_name}_api_select_0044 ON public.{table_name}")
        for role_prefix in ("edge", "projector"):
            for command in ("select", "insert", "update", "delete"):
                op.execute(
                    f"DROP POLICY IF EXISTS {table_name}_{role_prefix}_{command}_0044 "
                    f"ON public.{table_name}"
                )
        op.execute(f"ALTER TABLE public.{table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY")
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_runtime_binding_ready_0044()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_rls_check_0044(text,text,jsonb)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_work_order_allowed_0044(text,text,uuid,text,uuid,uuid,text,timestamptz,timestamptz,timestamptz)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_snapshot_transition_guard_0044()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_projection_chain_guard_0044()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_person_allowed_0044(uuid)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_version_allowed_0044(text,text,uuid,uuid,text,timestamptz,timestamptz,timestamptz,jsonb,text,boolean,timestamptz)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_conflict_allowed_0044(text,uuid,uuid,uuid)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_organization_allowed_0044(uuid)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_external_object_allowed_0044(text,text,uuid,uuid,text,text,uuid)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_inbox_allowed_0044(text,text,uuid,uuid,text,text,text,text,timestamptz,jsonb,text,text)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_run_snapshot_allowed_0044(text,uuid,text,text,text,text,text)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_binding_allowed_0044(text,text,text,text,text,text,text,text)"
    )
    op.execute("DROP TABLE public.oam_sync_scope_bindings")
    op.execute(
        "DROP FUNCTION IF EXISTS public.rsc_oam_formal_scope_key_0044(text,text)"
    )


def _require_roles_and_schema() -> None:
    required_tables = ", ".join(
        f"'{table_name}'" for table_name in STAGING_TABLES + FORMAL_TABLES
    )
    required_roles = ", ".join(
        f"'{role_name}'"
        for role_name in (
            MIGRATION_ROLE,
            API_ROLE,
            BACKUP_ROLE,
            PROJECTOR_ROLE,
            EDGE_ROLE,
        )
    )
    op.execute(
        f"""
DO $$
DECLARE
    missing_tables text;
    invalid_roles text;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION '0044 must run as the direct migration database role';
    END IF;

    SELECT pg_catalog.string_agg(required_role, ', ' ORDER BY required_role)
      INTO invalid_roles
      FROM pg_catalog.unnest(ARRAY[{required_roles}]) AS required(required_role)
      LEFT JOIN pg_catalog.pg_roles AS role_row
        ON role_row.rolname = required.required_role
     WHERE role_row.oid IS NULL
        OR NOT role_row.rolcanlogin
        OR role_row.rolsuper
        OR role_row.rolcreatedb
        OR role_row.rolcreaterole
        OR role_row.rolreplication
        OR role_row.rolbypassrls;
    IF invalid_roles IS NOT NULL THEN
        RAISE EXCEPTION '0044 missing or privileged roles: %', invalid_roles;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_auth_members AS membership
          JOIN pg_catalog.pg_roles AS member_role
            ON member_role.oid = membership.member
          JOIN pg_catalog.pg_roles AS granted_role
            ON granted_role.oid = membership.roleid
         WHERE member_role.rolname IN ({required_roles})
            OR granted_role.rolname IN ({required_roles})
    ) THEN
        RAISE EXCEPTION '0044 database role membership boundary is not closed';
    END IF;

    IF pg_catalog.pg_get_userbyid((
        SELECT database_row.datdba
          FROM pg_catalog.pg_database AS database_row
         WHERE database_row.datname = pg_catalog.current_database()
    )) <> '{MIGRATION_ROLE}' OR pg_catalog.pg_get_userbyid((
        SELECT schema_row.nspowner
          FROM pg_catalog.pg_namespace AS schema_row
         WHERE schema_row.nspname = 'public'
    )) <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION '0044 requires migration-owned database and public schema';
    END IF;

    SELECT pg_catalog.string_agg(required_table, ', ' ORDER BY required_table)
      INTO missing_tables
      FROM pg_catalog.unnest(ARRAY[{required_tables}]) AS required(required_table)
     WHERE pg_catalog.to_regclass(
         pg_catalog.format('public.%I', required_table)
     ) IS NULL;
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION '0044 row boundary missing tables: %', missing_tables;
    END IF;
END
$$
"""
    )


def _require_empty_oam_sync_graph() -> None:
    # 0043 was never approved for formal projection use: its table/column ACL
    # could not prove source/entity/scope ownership.  No 0043 row can therefore
    # be promoted as trusted merely because its internally supplied hashes are
    # self-consistent.  Lock the complete edge/projector sync graph (including
    # the employee mapping edge it consumes) and require an empty first-install or
    # downgrade boundary.  Pre-0044 edge staging had the same broad-row risk,
    # so it cannot be reused either; the source must be observed again after
    # exact bindings are provisioned.
    sync_graph_tables = (
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
        "sync_runs",
        "sync_batches",
        "sync_inbox_events",
        "external_objects",
        "external_object_versions",
        "external_object_mappings",
        "sync_conflicts",
        "oam_work_orders",
    )
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in sync_graph_tables)
        + " IN SHARE ROW EXCLUSIVE MODE"
    )
    nonempty_checks = "\nUNION ALL\n".join(
        "SELECT '"
        + table_name
        + "'::text AS table_name WHERE EXISTS "
        + f"(SELECT 1 FROM public.{table_name})"
        for table_name in sync_graph_tables
    )
    op.execute(
        f"""
DO $$
DECLARE
    nonempty_tables text;
BEGIN
    SELECT pg_catalog.string_agg(table_name, ', ' ORDER BY table_name)
      INTO nonempty_tables
      FROM (
{nonempty_checks}
      ) AS nonempty;
    IF nonempty_tables IS NOT NULL THEN
        RAISE EXCEPTION
            '0044 requires an empty OAM sync graph; resynchronize from source after upgrade: %',
            nonempty_tables;
    END IF;
END
$$
"""
    )


def _create_binding_table() -> None:
    op.execute(
        """
CREATE TABLE public.oam_sync_scope_bindings (
    id uuid PRIMARY KEY,
    principal_name text NOT NULL,
    capability text NOT NULL,
    source_system text NOT NULL,
    source_instance text NOT NULL,
    scope_key text NOT NULL,
    company_id text NOT NULL,
    org_code text NOT NULL,
    entity_type text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    formal_scope_key text GENERATED ALWAYS AS (
        public.rsc_oam_formal_scope_key_0044(source_instance, scope_key)
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
    CONSTRAINT ck_oam_sync_scope_bindings_principal
        CHECK (principal_name IN ('edge_inbox', 'star_oam_projector')),
    CONSTRAINT ck_oam_sync_scope_bindings_capability
        CHECK (capability IN ('edge_ingress', 'projector_read', 'projector_write')),
    CONSTRAINT ck_oam_sync_scope_bindings_source
        CHECK (source_system = 'starcharge_oam'),
    CONSTRAINT ck_oam_sync_scope_bindings_exact_combination CHECK (
        (principal_name = 'edge_inbox'
         AND capability = 'edge_ingress'
         AND entity_type IN (
             'warehouse',
             'inventory',
             'employee',
             'material_application',
             'material_application_line',
             'work_order'
         ))
        OR
        (principal_name = 'star_oam_projector'
         AND capability = 'projector_read'
         AND entity_type IN ('employee', 'work_order'))
        OR
        (principal_name = 'star_oam_projector'
         AND capability = 'projector_write'
         AND entity_type = 'work_order')
    ),
    CONSTRAINT ck_oam_sync_scope_bindings_scope_entity CHECK (
        (
            (
                entity_type = 'work_order'
                OR (
                    principal_name = 'star_oam_projector'
                    AND capability = 'projector_read'
                    AND entity_type = 'employee'
                )
            )
            AND scope_key ~ '^work-orders:recent-([1-9]|[1-9][0-9]|[1-2][0-9][0-9]|3[0-5][0-9]|36[0-5])d$'
        )
        OR (
            principal_name = 'edge_inbox'
            AND entity_type IN (
                'employee',
                'material_application',
                'material_application_line'
            )
            AND scope_key = 'all'
        )
        OR (
            principal_name = 'edge_inbox'
            AND entity_type IN ('warehouse', 'inventory')
            AND (
                scope_key = 'all'
                OR scope_key ~ '^warehouse:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
            )
        )
    ),
    CONSTRAINT ck_oam_sync_scope_bindings_canonical_text CHECK (
        source_instance = pg_catalog.btrim(source_instance)
        AND scope_key = pg_catalog.btrim(scope_key)
        AND company_id = pg_catalog.btrim(company_id)
        AND org_code = pg_catalog.btrim(org_code)
        AND pg_catalog.length(source_instance) BETWEEN 1 AND 128
        AND pg_catalog.length(scope_key) BETWEEN 1 AND 160
        AND pg_catalog.length(company_id) BETWEEN 1 AND 80
        AND pg_catalog.length(org_code) BETWEEN 1 AND 80
        AND source_instance !~ '[[:cntrl:]]'
        AND scope_key !~ '[[:cntrl:]]'
        AND company_id !~ '[[:cntrl:]]'
        AND org_code !~ '[[:cntrl:]]'
        AND source_instance !~ '[*%?]'
        AND scope_key !~ '[*%?]'
        AND company_id !~ '[*%?]'
        AND org_code !~ '[*%?]'
    ),
    CONSTRAINT ck_oam_sync_scope_bindings_time_order
        CHECK (updated_at >= created_at),
    CONSTRAINT uq_oam_sync_scope_binding_exact UNIQUE (
        principal_name,
        capability,
        source_system,
        source_instance,
        scope_key,
        entity_type
    )
)
"""
    )
    op.execute(
        "CREATE INDEX ix_oam_sync_scope_bindings_runtime ON "
        "public.oam_sync_scope_bindings "
        "(principal_name, capability, enabled, source_system, formal_scope_key, entity_type)"
    )
    op.execute(
        "REVOKE ALL ON TABLE public.oam_sync_scope_bindings FROM PUBLIC, "
        f"{API_ROLE}, {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )
    op.execute(
        f"GRANT SELECT ON TABLE public.oam_sync_scope_bindings TO {BACKUP_ROLE}"
    )


def _create_rls_functions() -> None:
    op.execute(_BINDING_HELPER_SQL)
    op.execute(_RUN_SNAPSHOT_HELPER_SQL)
    op.execute(_INBOX_HELPER_SQL)
    op.execute(_EXTERNAL_OBJECT_HELPER_SQL)
    op.execute(_CONFLICT_HELPER_SQL)
    op.execute(_ORGANIZATION_HELPER_SQL)
    op.execute(_VERSION_HELPER_SQL)
    op.execute(_PERSON_HELPER_SQL)
    op.execute(_WORK_ORDER_HELPER_SQL)
    op.execute(_SNAPSHOT_TRANSITION_GUARD_SQL)
    op.execute(
        "CREATE TRIGGER trg_external_sync_snapshot_transition_0044 "
        "BEFORE UPDATE ON public.external_sync_snapshots FOR EACH ROW "
        "EXECUTE FUNCTION public.rsc_oam_snapshot_transition_guard_0044()"
    )
    op.execute(_PROJECTION_CHAIN_GUARD_SQL)
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_external_objects_chain_0044 "
        "AFTER INSERT OR UPDATE ON public.external_objects "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.rsc_oam_projection_chain_guard_0044()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_external_object_versions_chain_0044 "
        "AFTER INSERT OR UPDATE ON public.external_object_versions "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.rsc_oam_projection_chain_guard_0044()"
    )
    op.execute(_RLS_CHECK_FUNCTION_SQL)
    op.execute(_RUNTIME_READY_FUNCTION_SQL)


def _apply_forced_rls() -> None:
    for table_name in RLS_TABLES:
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_migrator_0044 "
            f"ON public.{table_name} AS PERMISSIVE FOR ALL TO {MIGRATION_ROLE} "
            "USING (true) WITH CHECK (true)"
        )
        op.execute(
            f"CREATE POLICY {table_name}_backup_select_0044 "
            f"ON public.{table_name} AS PERMISSIVE FOR SELECT TO {BACKUP_ROLE} "
            "USING (true)"
        )

    for table_name in API_SELECT_TABLES:
        op.execute(
            f"CREATE POLICY {table_name}_api_select_0044 "
            f"ON public.{table_name} AS PERMISSIVE FOR SELECT TO {API_ROLE} "
            "USING (true)"
        )

    for table_name in STAGING_TABLES[:4]:
        _create_runtime_policy(table_name, "projector", "select", PROJECTOR_ROLE)
    for table_name in FORMAL_TABLES:
        _create_runtime_policy(table_name, "projector", "select", PROJECTOR_ROLE)
    for table_name in PROJECTOR_INSERT_COLUMNS:
        _create_runtime_policy(table_name, "projector", "insert", PROJECTOR_ROLE)
    for table_name in PROJECTOR_UPDATE_COLUMNS:
        _create_runtime_policy(table_name, "projector", "update", PROJECTOR_ROLE)

    for table_name in (
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
    ):
        _create_runtime_policy(table_name, "edge", "select", EDGE_ROLE)
        _create_runtime_policy(table_name, "edge", "insert", EDGE_ROLE)
    for table_name in (
        "external_sync_snapshots",
        "external_sync_current_records",
    ):
        _create_runtime_policy(table_name, "edge", "update", EDGE_ROLE)
    _create_runtime_policy(
        "external_sync_current_records", "edge", "delete", EDGE_ROLE
    )
    _create_runtime_policy("audit_logs", "edge", "insert", EDGE_ROLE)


def _create_runtime_policy(
    table_name: str,
    role_prefix: str,
    command: str,
    role_name: str,
) -> None:
    predicate = (
        "public.rsc_oam_rls_check_0044("
        f"'{table_name}', '{command}', pg_catalog.to_jsonb({table_name}))"
    )
    policy_name = f"{table_name}_{role_prefix}_{command}_0044"
    if command == "insert":
        clause = f"WITH CHECK ({predicate})"
    elif command == "update":
        if (
            role_prefix == "edge"
            and table_name == "external_sync_current_records"
        ):
            old_predicate = (
                "public.rsc_oam_rls_check_0044("
                f"'{table_name}', 'select', "
                f"pg_catalog.to_jsonb({table_name}))"
            )
            new_predicate = (
                "public.rsc_oam_rls_check_0044("
                f"'{table_name}', 'insert', "
                f"pg_catalog.to_jsonb({table_name}))"
            )
            clause = (
                f"USING ({old_predicate}) WITH CHECK ({new_predicate})"
            )
        elif table_name in {
            "external_objects",
            "external_object_versions",
            "oam_work_orders",
        }:
            old_predicate = (
                "public.rsc_oam_rls_check_0044("
                f"'{table_name}', 'update_old', "
                f"pg_catalog.to_jsonb({table_name}))"
            )
            new_predicate = (
                "public.rsc_oam_rls_check_0044("
                f"'{table_name}', 'update_new', "
                f"pg_catalog.to_jsonb({table_name}))"
            )
            clause = (
                f"USING ({old_predicate}) WITH CHECK ({new_predicate})"
            )
        else:
            clause = f"USING ({predicate}) WITH CHECK ({predicate})"
    else:
        clause = f"USING ({predicate})"
    op.execute(
        f"CREATE POLICY {policy_name} ON public.{table_name} AS PERMISSIVE "
        f"FOR {command.upper()} TO {role_name} {clause}"
    )


def _restore_projector_acl() -> None:
    # 0044 downgrade revokes these grants before RLS removal.  Re-applying
    # 0044 directly from 0043 must therefore restore the exact reviewed 0043
    # matrix without relying on 0043 being replayed.
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {PROJECTOR_ROLE}"
    )
    _revoke_runtime_column_acl((PROJECTOR_ROLE,))
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {PROJECTOR_ROLE}"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM {PROJECTOR_ROLE}"
    )
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {PROJECTOR_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {PROJECTOR_ROLE}")
    read_tables = ", ".join(
        f"public.{table_name}" for table_name in PROJECTOR_READ_TABLES
    )
    op.execute(f"GRANT SELECT ON TABLE {read_tables} TO {PROJECTOR_ROLE}")
    for table_name, columns in PROJECTOR_INSERT_COLUMNS.items():
        op.execute(
            f"GRANT INSERT ({', '.join(columns)}) ON TABLE public.{table_name} "
            f"TO {PROJECTOR_ROLE}"
        )
    for table_name, columns in PROJECTOR_UPDATE_COLUMNS.items():
        op.execute(
            f"GRANT UPDATE ({', '.join(columns)}) ON TABLE public.{table_name} "
            f"TO {PROJECTOR_ROLE}"
        )


def _close_binding_and_function_acl() -> None:
    op.execute(
        "REVOKE ALL ON TABLE public.oam_sync_scope_bindings FROM PUBLIC, "
        f"{API_ROLE}, {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.rsc_oam_rls_check_0044(text,text,jsonb) "
        f"FROM PUBLIC, {API_ROLE}, {BACKUP_ROLE}, {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.rsc_oam_runtime_binding_ready_0044() "
        f"FROM PUBLIC, {API_ROLE}, {BACKUP_ROLE}, {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )
    for function_signature in PRIVATE_HELPER_FUNCTIONS:
        op.execute(
            f"REVOKE ALL ON FUNCTION {function_signature} FROM PUBLIC, "
            f"{API_ROLE}, {BACKUP_ROLE}, {PROJECTOR_ROLE}, {EDGE_ROLE}"
        )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.rsc_oam_rls_check_0044(text,text,jsonb) "
        f"TO {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.rsc_oam_runtime_binding_ready_0044() "
        f"TO {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )


def _revoke_runtime_acl() -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )
    _revoke_runtime_column_acl((PROJECTOR_ROLE, EDGE_ROLE))
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON SCHEMA public FROM {PROJECTOR_ROLE}, {EDGE_ROLE}"
    )


def _revoke_runtime_column_acl(role_names: tuple[str, ...]) -> None:
    role_literals = ", ".join(f"'{role_name}'" for role_name in role_names)
    op.execute(
        f"""
DO $$
DECLARE
    column_acl record;
BEGIN
    FOR column_acl IN
        SELECT
            schema_row.nspname AS schema_name,
            relation.relname AS relation_name,
            attribute_row.attname AS column_name,
            acl.privilege_type,
            grantee.rolname AS grantee_name
          FROM pg_catalog.pg_attribute AS attribute_row
          JOIN pg_catalog.pg_class AS relation
            ON relation.oid = attribute_row.attrelid
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = relation.relnamespace
         CROSS JOIN LATERAL pg_catalog.aclexplode(attribute_row.attacl) AS acl
          JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = acl.grantee
         WHERE schema_row.nspname = 'public'
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
           AND grantee.rolname IN ({role_literals})
         ORDER BY relation.relname, attribute_row.attnum, acl.privilege_type
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE %s (%I) ON TABLE %I.%I FROM %I',
            column_acl.privilege_type,
            column_acl.column_name,
            column_acl.schema_name,
            column_acl.relation_name,
            column_acl.grantee_name
        );
    END LOOP;
END
$$
"""
    )


def _verify_installation() -> None:
    table_literals = ", ".join(f"'{name}'" for name in RLS_TABLES)
    function_literals = ", ".join(
        f"'{signature}'::pg_catalog.regprocedure"
        for signature in ALL_RLS_FUNCTIONS
    )
    policy_values = ",\n        ".join(
        "(" + ", ".join(
            "NULL"
            if value is None
            else "'" + value.replace("'", "''") + "'"
            for value in policy
        ) + ")"
        for policy in EXPECTED_POLICY_ROSTER
    )
    op.execute(
        f"""
DO $$
DECLARE
    missing_rls text;
BEGIN
    SELECT pg_catalog.string_agg(required_table, ', ' ORDER BY required_table)
      INTO missing_rls
      FROM pg_catalog.unnest(ARRAY[{table_literals}]) AS required(required_table)
      LEFT JOIN pg_catalog.pg_class AS relation
        ON relation.oid = pg_catalog.to_regclass(
            pg_catalog.format('public.%I', required.required_table)
        )
     WHERE relation.oid IS NULL
        OR NOT relation.relrowsecurity
        OR NOT relation.relforcerowsecurity;
    IF missing_rls IS NOT NULL THEN
        RAISE EXCEPTION '0044 FORCE RLS installation incomplete: %', missing_rls;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid IN ({function_literals})
           AND pg_catalog.pg_get_userbyid(function_row.proowner)
               <> '{MIGRATION_ROLE}'
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid IN ({function_literals})
    ) <> {len(ALL_RLS_FUNCTIONS)} OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
         WHERE schema_row.nspname = 'public'
           AND function_row.proname ~ '^rsc_oam_[[:alnum:]_]+_0044$'
    ) <> {len(ALL_RLS_FUNCTIONS)} THEN
        RAISE EXCEPTION '0044 RLS helpers are not migration-owned';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid IN ({function_literals})
           AND (
               NOT function_row.prosecdef
               OR (
                   function_row.oid =
                       '{SCOPE_KEY_FUNCTION}'::pg_catalog.regprocedure
                   AND function_row.provolatile <> 'i'
               )
               OR (
                   function_row.oid <>
                       '{SCOPE_KEY_FUNCTION}'::pg_catalog.regprocedure
                   AND function_row.oid NOT IN (
                       'public.rsc_oam_snapshot_transition_guard_0044()'
                           ::pg_catalog.regprocedure,
                       'public.rsc_oam_projection_chain_guard_0044()'
                           ::pg_catalog.regprocedure
                   )
                   AND function_row.provolatile <> 's'
               )
               OR (
                   function_row.oid IN (
                       'public.rsc_oam_snapshot_transition_guard_0044()'
                           ::pg_catalog.regprocedure,
                       'public.rsc_oam_projection_chain_guard_0044()'
                           ::pg_catalog.regprocedure
                   )
                   AND function_row.provolatile <> 'v'
               )
               OR function_row.proleakproof
               OR function_row.proconfig IS DISTINCT FROM
                  ARRAY['search_path=pg_catalog']::text[]
           )
    ) THEN
        RAISE EXCEPTION '0044 RLS helper security attributes are invalid';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
             COALESCE(
                 function_row.proacl,
                 pg_catalog.acldefault('f', function_row.proowner)
             )
         ) AS acl
         WHERE function_row.oid IN ({function_literals})
           AND acl.grantee = 0
    ) THEN
        RAISE EXCEPTION '0044 RLS helpers remain executable by PUBLIC';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
             COALESCE(
                 function_row.proacl,
                 pg_catalog.acldefault('f', function_row.proowner)
             )
         ) AS function_acl
          LEFT JOIN pg_catalog.pg_roles AS grantee_role
            ON grantee_role.oid = function_acl.grantee
         WHERE function_row.oid IN ({function_literals})
           AND (
               function_acl.privilege_type <> 'EXECUTE'
               OR function_acl.grantee = 0
               OR (
                   function_acl.grantee <> function_row.proowner
                   AND (
                       function_row.oid NOT IN (
                           '{RLS_CHECK_FUNCTION}'::pg_catalog.regprocedure,
                           '{RUNTIME_READY_FUNCTION}'::pg_catalog.regprocedure
                       )
                       OR grantee_role.rolname NOT IN (
                           '{PROJECTOR_ROLE}', '{EDGE_ROLE}'
                       )
                       OR function_acl.is_grantable
                   )
               )
           )
    ) OR EXISTS (
        SELECT function_row.oid
          FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
             COALESCE(
                 function_row.proacl,
                 pg_catalog.acldefault('f', function_row.proowner)
             )
         ) AS function_acl
         WHERE function_row.oid IN ({function_literals})
         GROUP BY function_row.oid
        HAVING pg_catalog.count(*) FILTER (
                   WHERE function_acl.privilege_type = 'EXECUTE'
               ) <> CASE
                   WHEN function_row.oid IN (
                       '{RLS_CHECK_FUNCTION}'::pg_catalog.regprocedure,
                       '{RUNTIME_READY_FUNCTION}'::pg_catalog.regprocedure
                   ) THEN 3
                   ELSE 1
               END
    ) THEN
        RAISE EXCEPTION '0044 RLS helper ACL closure is invalid';
    END IF;

    IF EXISTS (
        WITH expected_policy(
            table_name,
            policy_name,
            command_code,
            role_name,
            using_expression,
            check_expression
        ) AS (
            VALUES
                {policy_values}
        ),
        actual_policy AS (
            SELECT
                table_row.relname AS table_name,
                policy.polname AS policy_name,
                policy.polcmd AS command_code,
                policy.polpermissive AS is_permissive,
                ARRAY(
                    SELECT CASE
                        WHEN role_oid = 0 THEN 'PUBLIC'
                        ELSE pg_catalog.pg_get_userbyid(role_oid)
                    END
                      FROM pg_catalog.unnest(policy.polroles)
                           AS policy_role(role_oid)
                     ORDER BY 1
                )::text[] AS role_names,
                pg_catalog.pg_get_expr(policy.polqual, policy.polrelid)
                    AS using_expression,
                pg_catalog.pg_get_expr(policy.polwithcheck, policy.polrelid)
                    AS check_expression
              FROM pg_catalog.pg_policy AS policy
              JOIN pg_catalog.pg_class AS table_row
                ON table_row.oid = policy.polrelid
              JOIN pg_catalog.pg_namespace AS schema_row
                ON schema_row.oid = table_row.relnamespace
             WHERE schema_row.nspname = 'public'
               AND table_row.relname IN ({table_literals})
        )
        SELECT 1
          FROM expected_policy AS expected
          FULL JOIN actual_policy AS actual
            ON actual.table_name = expected.table_name
           AND actual.policy_name = expected.policy_name
         WHERE expected.policy_name IS NULL
            OR actual.policy_name IS NULL
            OR actual.command_code <> expected.command_code
            OR actual.is_permissive IS NOT TRUE
            OR actual.role_names IS DISTINCT FROM
               ARRAY[expected.role_name]::text[]
            OR actual.using_expression IS DISTINCT FROM
               expected.using_expression
            OR actual.check_expression IS DISTINCT FROM
               expected.check_expression
    ) THEN
        RAISE EXCEPTION '0044 RLS policy closure is invalid';
    END IF;

    IF (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
          JOIN pg_catalog.pg_class AS table_row
            ON table_row.oid = trigger_row.tgrelid
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgenabled = 'O'
           AND (
               (
                   table_row.oid =
                       'public.external_sync_snapshots'::pg_catalog.regclass
                   AND trigger_row.tgname =
                       'trg_external_sync_snapshot_transition_0044'
                   AND trigger_row.tgfoid =
                       'public.rsc_oam_snapshot_transition_guard_0044()'
                           ::pg_catalog.regprocedure
                   AND trigger_row.tgtype = 19
                   AND trigger_row.tgconstraint = 0
                   AND NOT trigger_row.tgdeferrable
                   AND NOT trigger_row.tginitdeferred
               )
               OR (
                   table_row.oid =
                       'public.external_objects'::pg_catalog.regclass
                   AND trigger_row.tgname =
                       'trg_external_objects_chain_0044'
                   AND trigger_row.tgfoid =
                       'public.rsc_oam_projection_chain_guard_0044()'
                           ::pg_catalog.regprocedure
                   AND trigger_row.tgtype = 21
                   AND trigger_row.tgconstraint <> 0
                   AND trigger_row.tgdeferrable
                   AND trigger_row.tginitdeferred
               )
               OR (
                   table_row.oid =
                       'public.external_object_versions'::pg_catalog.regclass
                   AND trigger_row.tgname =
                       'trg_external_object_versions_chain_0044'
                   AND trigger_row.tgfoid =
                       'public.rsc_oam_projection_chain_guard_0044()'
                           ::pg_catalog.regprocedure
                   AND trigger_row.tgtype = 21
                   AND trigger_row.tgconstraint <> 0
                   AND trigger_row.tgdeferrable
                   AND trigger_row.tginitdeferred
               )
           )
    ) <> 3 OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgrelid IN (
               'public.external_sync_snapshots'::pg_catalog.regclass,
               'public.external_objects'::pg_catalog.regclass,
               'public.external_object_versions'::pg_catalog.regclass
           )
    ) <> 3 THEN
        RAISE EXCEPTION '0044 projection lifecycle triggers are invalid';
    END IF;
END
$$
"""
    )


_SCOPE_KEY_FUNCTION_SQL = r"""
CREATE FUNCTION public.rsc_oam_formal_scope_key_0044(
    source_instance text,
    scope_key text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT 'oam-work-order-scope:' || pg_catalog.encode(
        pg_catalog.sha256(
            pg_catalog.convert_to(source_instance, 'UTF8')
            || pg_catalog.decode('00', 'hex')
            || pg_catalog.convert_to(scope_key, 'UTF8')
        ),
        'hex'
    )
$$
"""


_BINDING_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_binding_allowed_0044(
    p_principal_name text,
    p_capability text,
    p_source_system text,
    p_source_instance text,
    p_scope_key text,
    p_company_id text,
    p_org_code text,
    p_entity_type text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT p_principal_name = session_user::text
       AND EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS binding
             WHERE binding.enabled
               AND binding.principal_name = p_principal_name
               AND binding.capability = p_capability
               AND binding.source_system = p_source_system
               AND binding.source_instance = p_source_instance
               AND binding.scope_key = p_scope_key
               AND binding.company_id = p_company_id
               AND binding.org_code = p_org_code
               AND (
                   p_entity_type IS NULL
                   OR binding.entity_type = p_entity_type
               )
               AND (
                   (
                       binding.principal_name = 'edge_inbox'
                       AND binding.entity_type <> 'work_order'
                   )
                   OR EXISTS (
                       SELECT 1
                         FROM public.source_systems AS source
                        WHERE source.code = binding.source_system
                          AND source.mode = 'read_only'
                          AND source.enabled
                          AND source.configuration_jsonb =
                              pg_catalog.jsonb_build_object(
                                  'projection_schema',
                                  'rsc.oam_work_order_projection.v1',
                                  'edge_source_instance',
                                  binding.source_instance,
                                  'work_order_company_id',
                                  binding.company_id,
                                  'work_order_org_code',
                                  binding.org_code,
                                  'work_order_scope_key',
                                  binding.scope_key
                              )
                   )
               )
       )
$$
"""


_RUN_SNAPSHOT_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_run_snapshot_allowed_0044(
    p_required_capability text,
    p_source_id uuid,
    p_run_key text,
    p_scope_key text,
    p_mode text,
    p_watermark_to text,
    p_manifest_sha256 text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT session_user::text = 'star_oam_projector'
       AND p_required_capability IN ('projector_read', 'projector_write')
       AND EXISTS (
            SELECT 1
              FROM public.external_sync_snapshots AS snapshot
              JOIN public.source_systems AS source
                ON source.id = p_source_id
               AND source.code = snapshot.source_system
              JOIN public.oam_sync_scope_bindings AS binding
                ON binding.enabled
               AND binding.principal_name = session_user::text
               AND binding.capability = p_required_capability
               AND binding.source_system = source.code
               AND binding.source_instance = snapshot.source_instance
               AND binding.scope_key = snapshot.scope_key
               AND binding.company_id = snapshot.company_id
               AND binding.org_code = snapshot.org_code
               AND binding.entity_type = 'work_order'
             WHERE snapshot.status = 'complete'
               AND p_run_key = 'oam-work-order:' || pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(snapshot.source_instance, 'UTF8')
                       || pg_catalog.decode('00', 'hex')
                       || pg_catalog.convert_to(snapshot.snapshot_id, 'UTF8')
                   ),
                   'hex'
               )
               AND p_scope_key = binding.formal_scope_key
               AND p_mode = snapshot.sync_mode
               AND p_watermark_to = (
                   CASE
                       WHEN pg_catalog.date_trunc(
                           'second', snapshot.snapshot_at
                       ) = snapshot.snapshot_at
                       THEN pg_catalog.to_char(
                           snapshot.snapshot_at AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS'
                       )
                       ELSE pg_catalog.to_char(
                           snapshot.snapshot_at AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS.US'
                       )
                   END
               ) || '+00:00'
               AND (
                   p_manifest_sha256 IS NULL
                   OR p_manifest_sha256 = snapshot.manifest_sha256
               )
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', binding.source_instance,
                   'work_order_company_id', binding.company_id,
                   'work_order_org_code', binding.org_code,
                   'work_order_scope_key', binding.scope_key
               )
       )
$$
"""


_INBOX_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_inbox_allowed_0044(
    p_operation_name text,
    p_required_capability text,
    p_batch_id uuid,
    p_source_id uuid,
    p_external_event_id text,
    p_entity_type text,
    p_external_id text,
    p_source_version text,
    p_source_updated_at timestamptz,
    p_payload_jsonb jsonb,
    p_payload_sha256 text,
    p_status text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT session_user::text = 'star_oam_projector'
       AND p_operation_name IN ('select', 'insert', 'update')
       AND p_required_capability IN ('projector_read', 'projector_write')
       AND p_entity_type = 'work_order'
       AND p_status IN ('staged', 'validated', 'applied', 'conflict', 'rejected')
       AND p_payload_jsonb IS NOT NULL
       AND ARRAY(
           SELECT payload_key
             FROM pg_catalog.jsonb_object_keys(p_payload_jsonb)
                  AS payload(payload_key)
            ORDER BY payload_key
       ) = ARRAY[
           'authCompanyId',
           'code',
           'executorId',
           'id',
           'province',
           'statusCode',
           'updateTime'
       ]::text[]
       AND p_payload_jsonb->>'id' = p_external_id
       AND EXISTS (
            SELECT 1
              FROM public.sync_batches AS batch
              JOIN public.sync_runs AS run ON run.id = batch.run_id
              JOIN public.source_systems AS source
                ON source.id = run.source_system_id
               AND source.id = p_source_id
              JOIN public.oam_sync_scope_bindings AS binding
                ON binding.enabled
               AND binding.principal_name = session_user::text
               AND binding.capability = p_required_capability
               AND binding.source_system = source.code
               AND binding.formal_scope_key = run.scope_key
               AND binding.entity_type = 'work_order'
              JOIN public.external_sync_snapshots AS snapshot
                ON snapshot.source_system = source.code
               AND snapshot.source_instance = binding.source_instance
               AND snapshot.scope_key = binding.scope_key
               AND snapshot.company_id = binding.company_id
               AND snapshot.org_code = binding.org_code
               AND snapshot.status = 'complete'
               AND run.run_key = 'oam-work-order:' || pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(snapshot.source_instance, 'UTF8')
                       || pg_catalog.decode('00', 'hex')
                       || pg_catalog.convert_to(snapshot.snapshot_id, 'UTF8')
                   ),
                   'hex'
               )
             WHERE batch.id = p_batch_id
               AND batch.entity_type = 'work_order'
               AND run.scope_key = binding.formal_scope_key
               AND run.mode = snapshot.sync_mode
               AND run.watermark_to::timestamptz = snapshot.snapshot_at
               AND run.manifest_sha256 = snapshot.manifest_sha256
               AND p_payload_jsonb->>'authCompanyId' = binding.company_id
               AND p_external_event_id = snapshot.snapshot_id || ':wo:'
                   || pg_catalog.encode(
                       pg_catalog.sha256(
                           pg_catalog.convert_to(
                               'work-order:' || (p_payload_jsonb->>'code'),
                               'UTF8'
                           )
                       ),
                       'hex'
                   )
               AND p_source_version = 'wo-v1:' || (
                   CASE
                       WHEN pg_catalog.date_trunc(
                           'second', p_source_updated_at
                       ) = p_source_updated_at
                       THEN pg_catalog.to_char(
                           p_source_updated_at AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS'
                       )
                       ELSE pg_catalog.to_char(
                           p_source_updated_at AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS.US'
                       )
                   END
               ) || '+00:00:' || p_payload_sha256
               AND p_payload_jsonb->>'updateTime' ~
                   '^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?([Zz]|[+-][0-9]{2}:[0-9]{2})?$'
               AND (
                   CASE
                       WHEN p_payload_jsonb->>'updateTime' ~
                           '([Zz]|[+-][0-9]{2}:[0-9]{2})$'
                       THEN pg_catalog.replace(
                           p_payload_jsonb->>'updateTime', 'z', 'Z'
                       )::timestamptz
                       ELSE (p_payload_jsonb->>'updateTime')::timestamp
                           AT TIME ZONE 'Asia/Shanghai'
                   END
               ) = p_source_updated_at
               AND (
                   p_operation_name <> 'insert'
                   OR EXISTS (
                       SELECT 1
                         FROM public.external_sync_current_records
                              AS current_record
                        WHERE current_record.last_snapshot_id = snapshot.id
                          AND current_record.source_system = source.code
                          AND current_record.source_instance =
                              binding.source_instance
                          AND current_record.scope_key = binding.scope_key
                          AND current_record.entity_type = 'work_order'
                          AND current_record.business_key =
                              'work-order:' || (p_payload_jsonb->>'code')
                          AND current_record.source_updated_at =
                              p_source_updated_at
                          AND current_record.payload_json::jsonb =
                              p_payload_jsonb
                          AND current_record.payload_sha256 =
                              p_payload_sha256
                   )
               )
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', binding.source_instance,
                   'work_order_company_id', binding.company_id,
                   'work_order_org_code', binding.org_code,
                   'work_order_scope_key', binding.scope_key
               )
       )
$$
"""


_EXTERNAL_OBJECT_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_external_object_allowed_0044(
    p_operation_name text,
    p_required_capability text,
    p_object_id uuid,
    p_source_id uuid,
    p_object_entity_type text,
    p_object_external_id text,
    p_current_version_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN session_user::text <> 'star_oam_projector' THEN false
        WHEN p_object_entity_type = 'work_order'
             AND p_operation_name IN (
                 'select', 'insert', 'update_old', 'update_new'
             )
             AND p_required_capability IN ('projector_read', 'projector_write')
        THEN EXISTS (
            SELECT 1
              FROM public.sync_inbox_events AS inbox
              JOIN public.sync_batches AS batch ON batch.id = inbox.batch_id
              JOIN public.sync_runs AS run ON run.id = batch.run_id
              JOIN public.source_systems AS source
                ON source.id = run.source_system_id
               AND source.id = inbox.source_system_id
              JOIN public.oam_sync_scope_bindings AS binding
                ON binding.enabled
               AND binding.principal_name = session_user::text
               AND binding.capability = p_required_capability
               AND binding.source_system = source.code
               AND binding.formal_scope_key = run.scope_key
               AND binding.entity_type = 'work_order'
             WHERE source.id = p_source_id
               AND inbox.entity_type = 'work_order'
               AND batch.entity_type = 'work_order'
               AND inbox.external_id = p_object_external_id
               AND (
                   p_operation_name IN ('select', 'update_old')
                   OR (
                       run.status = 'projecting'
                       AND batch.status = 'validated'
                       AND inbox.status = 'validated'
                   )
               )
               AND public.rsc_oam_inbox_allowed_0044(
                   'select',
                   p_required_capability,
                   inbox.batch_id,
                   inbox.source_system_id,
                   inbox.external_event_id,
                   inbox.entity_type,
                   inbox.external_id,
                   inbox.source_version,
                   inbox.source_updated_at,
                   inbox.payload_jsonb,
                   inbox.payload_sha256,
                   inbox.status
               )
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', binding.source_instance,
                   'work_order_company_id', binding.company_id,
                   'work_order_org_code', binding.org_code,
                   'work_order_scope_key', binding.scope_key
               )
               AND (
                   (
                       p_operation_name = 'insert'
                       AND p_current_version_id IS NULL
                   )
                   OR (
                       p_operation_name IN ('select', 'update_old')
                       AND (
                           p_current_version_id IS NULL
                           OR EXISTS (
                               SELECT 1
                                 FROM public.external_object_versions AS version
                                WHERE version.id = p_current_version_id
                                  AND version.external_object_id = p_object_id
                                  AND (
                                      (
                                          version.is_current
                                          AND version.valid_to IS NULL
                                      )
                                      OR (
                                          NOT version.is_current
                                          AND version.valid_to IS NOT NULL
                                      )
                                  )
                           )
                       )
                   )
                   OR (
                       p_operation_name = 'update_new'
                       AND p_current_version_id IS NOT NULL
                       AND EXISTS (
                           SELECT 1
                             FROM public.external_object_versions AS version
                            WHERE version.id = p_current_version_id
                              AND version.external_object_id = p_object_id
                              AND version.is_current
                              AND version.valid_to IS NULL
                       )
                   )
               )
        )
        WHEN p_object_entity_type = 'employee'
             AND p_operation_name = 'select'
             AND p_required_capability = 'projector_read'
             AND p_current_version_id IS NOT NULL
        THEN EXISTS (
            SELECT 1
              FROM public.source_systems AS source
              JOIN public.oam_sync_scope_bindings AS binding
                ON binding.enabled
               AND binding.principal_name = session_user::text
               AND binding.capability = 'projector_read'
               AND binding.source_system = source.code
               AND binding.entity_type = 'employee'
              JOIN public.external_object_versions AS version
                ON version.id = p_current_version_id
               AND version.external_object_id = p_object_id
               AND version.is_current
               AND version.valid_to IS NULL
               AND version.payload_jsonb->>'companyId' = binding.company_id
               AND version.payload_jsonb->>'orgCode' = binding.org_code
             WHERE source.id = p_source_id
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', binding.source_instance,
                   'work_order_company_id', binding.company_id,
                   'work_order_org_code', binding.org_code,
                   'work_order_scope_key', binding.scope_key
               )
        )
        ELSE false
    END
$$
"""


_VERSION_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_version_allowed_0044(
    p_operation_name text,
    p_required_capability text,
    p_version_id uuid,
    p_external_object_id uuid,
    p_source_version text,
    p_source_updated_at timestamptz,
    p_valid_from timestamptz,
    p_valid_to timestamptz,
    p_payload_jsonb jsonb,
    p_payload_sha256 text,
    p_is_current boolean,
    p_created_at timestamptz
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT session_user::text = 'star_oam_projector'
       AND p_operation_name IN (
           'select', 'insert', 'update_old', 'update_new'
       )
       AND p_required_capability IN ('projector_read', 'projector_write')
       AND p_created_at = p_valid_from
       AND p_valid_from <= pg_catalog.statement_timestamp()
       AND (
           (p_is_current AND p_valid_to IS NULL)
           OR (
               NOT p_is_current
               AND p_valid_to IS NOT NULL
               AND p_valid_to > p_valid_from
           )
       )
       AND CASE p_operation_name
           WHEN 'select' THEN true
           WHEN 'insert' THEN p_is_current AND p_valid_to IS NULL
           WHEN 'update_old' THEN p_is_current AND p_valid_to IS NULL
           WHEN 'update_new' THEN NOT p_is_current AND p_valid_to IS NOT NULL
           ELSE false
       END
       AND (
           (
               p_operation_name = 'select'
               AND p_required_capability = 'projector_read'
               AND p_is_current
               AND p_valid_to IS NULL
               AND EXISTS (
                   SELECT 1
                     FROM public.external_objects AS employee
                     JOIN public.source_systems AS source
                       ON source.id = employee.source_system_id
                     JOIN public.oam_sync_scope_bindings AS binding
                       ON binding.enabled
                      AND binding.principal_name = session_user::text
                      AND binding.capability = 'projector_read'
                      AND binding.source_system = source.code
                      AND binding.entity_type = 'employee'
                    WHERE employee.id = p_external_object_id
                      AND employee.entity_type = 'employee'
                      AND employee.deleted_at IS NULL
                      AND employee.current_version_id = p_version_id
                      AND p_source_updated_at IS NOT NULL
                      AND p_source_updated_at <= pg_catalog.statement_timestamp()
                      AND ARRAY(
                          SELECT payload_key
                            FROM pg_catalog.jsonb_object_keys(p_payload_jsonb)
                                 AS payload(payload_key)
                           ORDER BY payload_key
                      ) = ARRAY[
                          'accountId', 'companyId', 'orgCode', 'status'
                      ]::text[]
                      AND p_payload_jsonb->>'accountId' = employee.external_id
                      AND p_payload_jsonb->>'companyId' = binding.company_id
                      AND p_payload_jsonb->>'orgCode' = binding.org_code
                      AND p_payload_sha256 = pg_catalog.encode(
                          pg_catalog.sha256(
                              pg_catalog.convert_to(
                                  '{"accountId":'
                                  || pg_catalog.to_jsonb(
                                      p_payload_jsonb->>'accountId'
                                  )::text
                                  || ',"companyId":'
                                  || pg_catalog.to_jsonb(
                                      p_payload_jsonb->>'companyId'
                                  )::text
                                  || ',"orgCode":'
                                  || pg_catalog.to_jsonb(
                                      p_payload_jsonb->>'orgCode'
                                  )::text
                                  || ',"status":'
                                  || pg_catalog.to_jsonb(
                                      p_payload_jsonb->>'status'
                                  )::text
                                  || '}',
                                  'UTF8'
                              )
                          ),
                          'hex'
                      )
                      AND source.mode = 'read_only'
                      AND source.enabled
                      AND source.configuration_jsonb =
                          pg_catalog.jsonb_build_object(
                              'projection_schema',
                              'rsc.oam_work_order_projection.v1',
                              'edge_source_instance',
                              binding.source_instance,
                              'work_order_company_id',
                              binding.company_id,
                              'work_order_org_code',
                              binding.org_code,
                              'work_order_scope_key',
                              binding.scope_key
                          )
               )
           )
           OR (
       (
           (
               p_operation_name IN (
                   'select', 'update_old', 'update_new'
               )
               AND EXISTS (
                    SELECT 1
                      FROM public.external_objects AS external
                      JOIN public.sync_inbox_events AS inbox
                        ON inbox.source_system_id = external.source_system_id
                       AND inbox.entity_type = 'work_order'
                       AND inbox.external_id = external.external_id
                       AND inbox.source_updated_at = p_source_updated_at
                     WHERE external.id = p_external_object_id
                       AND external.entity_type = 'work_order'
                       AND external.deleted_at IS NULL
                       AND ARRAY(
                           SELECT payload_key
                             FROM pg_catalog.jsonb_object_keys(p_payload_jsonb)
                                  AS payload(payload_key)
                            ORDER BY payload_key
                       ) = ARRAY[
                           'engineer_person_id',
                           'organization_id',
                           'status',
                           'work_order_no'
                       ]::text[]
                       AND public.rsc_oam_inbox_allowed_0044(
                           'select',
                           p_required_capability,
                           inbox.batch_id,
                           inbox.source_system_id,
                           inbox.external_event_id,
                           inbox.entity_type,
                           inbox.external_id,
                           inbox.source_version,
                           inbox.source_updated_at,
                           inbox.payload_jsonb,
                           inbox.payload_sha256,
                           inbox.status
                       )
                       AND p_payload_sha256 = pg_catalog.encode(
                           pg_catalog.sha256(
                               pg_catalog.convert_to(
                                   '{"engineer_person_id":'
                                   || pg_catalog.to_jsonb(
                                       p_payload_jsonb->>'engineer_person_id'
                                   )::text
                                   || ',"organization_id":'
                                   || pg_catalog.to_jsonb(
                                       p_payload_jsonb->>'organization_id'
                                   )::text
                                   || ',"status":'
                                   || pg_catalog.to_jsonb(
                                       p_payload_jsonb->>'status'
                                   )::text
                                   || ',"work_order_no":'
                                   || pg_catalog.to_jsonb(
                                       p_payload_jsonb->>'work_order_no'
                                   )::text
                                   || '}',
                                   'UTF8'
                               )
                           ),
                           'hex'
                       )
                       AND (
                           (
                               p_source_version ~
                                   '^wo-v2:[0-9a-f]{64}:[0-9a-f]{64}$'
                               AND pg_catalog.split_part(
                                   p_source_version, ':', 2
                               ) = inbox.payload_sha256
                           )
                           OR (
                               p_source_version = 'wo-v1:' || (
                                   CASE
                                       WHEN pg_catalog.date_trunc(
                                           'second', p_source_updated_at
                                       ) = p_source_updated_at
                                       THEN pg_catalog.to_char(
                                           p_source_updated_at
                                               AT TIME ZONE 'UTC',
                                           'YYYY-MM-DD"T"HH24:MI:SS'
                                       )
                                       ELSE pg_catalog.to_char(
                                           p_source_updated_at
                                               AT TIME ZONE 'UTC',
                                           'YYYY-MM-DD"T"HH24:MI:SS.US'
                                       )
                                   END
                               ) || '+00:00:' || inbox.payload_sha256
                           )
                       )
               )
           )
           OR (
               p_operation_name = 'insert'
               AND EXISTS (
            SELECT 1
              FROM public.external_objects AS external
              JOIN public.sync_inbox_events AS inbox
                ON inbox.source_system_id = external.source_system_id
               AND inbox.entity_type = 'work_order'
               AND inbox.external_id = external.external_id
               AND inbox.source_updated_at = p_source_updated_at
              JOIN public.sync_batches AS batch ON batch.id = inbox.batch_id
              JOIN public.sync_runs AS run ON run.id = batch.run_id
              JOIN public.source_systems AS source
                ON source.id = run.source_system_id
               AND source.id = external.source_system_id
              JOIN public.oam_sync_scope_bindings AS binding
                ON binding.enabled
               AND binding.principal_name = session_user::text
               AND binding.capability = p_required_capability
               AND binding.source_system = source.code
               AND binding.formal_scope_key = run.scope_key
               AND binding.entity_type = 'work_order'
              JOIN public.external_objects AS employee
                ON employee.source_system_id = source.id
               AND employee.entity_type = 'employee'
               AND employee.external_id = inbox.payload_jsonb->>'executorId'
               AND employee.deleted_at IS NULL
              JOIN public.external_object_versions AS employee_version
                ON employee_version.id = employee.current_version_id
               AND employee_version.external_object_id = employee.id
               AND employee_version.is_current
               AND employee_version.valid_to IS NULL
               AND employee_version.payload_jsonb->>'accountId' =
                   employee.external_id
               AND employee_version.payload_jsonb->>'companyId' =
                   binding.company_id
               AND employee_version.payload_jsonb->>'orgCode' =
                   binding.org_code
               AND employee_version.payload_sha256 ~ '^[0-9a-f]{64}$'
              JOIN public.external_object_mappings AS mapping
                ON mapping.external_object_id = employee.id
               AND mapping.local_object_type = 'person'
               AND mapping.status = 'approved'
              JOIN public.people AS person
                ON person.id::text = mapping.local_object_id
               AND person.employment_status = 'active'
              JOIN public.organizations AS organization
                ON organization.id = person.organization_id
               AND organization.status = 'active'
              CROSS JOIN LATERAL (
                  SELECT CASE
                      WHEN inbox.payload_jsonb->>'statusCode' IN (
                          'to_be_create', 'create', 'wait_receive'
                      ) THEN 'pending'
                      WHEN inbox.payload_jsonb->>'statusCode' IN (
                          'wait_connect', 'wait_process', 'processing',
                          'transferring', 'wait_client_accept',
                          'wait_platform_accept', 'wait_source_accept',
                          'wait_install_command'
                      ) THEN 'active'
                      WHEN inbox.payload_jsonb->>'statusCode' IN (
                          'process_finish', 'client_accept_pass',
                          'platform_accept_pass', 'source_accept_pass', 'end'
                      ) THEN 'completed'
                      WHEN inbox.payload_jsonb->>'statusCode' = 'closed'
                          THEN 'closed'
                      WHEN inbox.payload_jsonb->>'statusCode' IN (
                          'stopping', 'stopped', 'rejected', 'transfer_reject'
                      ) THEN 'cancelled'
                      WHEN inbox.payload_jsonb->>'statusCode' = 'hang'
                          THEN 'inactive'
                      ELSE NULL
                  END AS projected_status
              ) AS projection
             WHERE external.id = p_external_object_id
               AND external.entity_type = 'work_order'
               AND external.deleted_at IS NULL
               AND run.status = 'projecting'
               AND batch.status = 'validated'
               AND inbox.status = 'validated'
               AND projection.projected_status IS NOT NULL
               AND employee_version.source_updated_at IS NOT NULL
               AND employee_version.source_updated_at <=
                   run.watermark_to::timestamptz
               AND ARRAY(
                   SELECT payload_key
                     FROM pg_catalog.jsonb_object_keys(
                         employee_version.payload_jsonb
                     ) AS payload(payload_key)
                    ORDER BY payload_key
               ) = ARRAY[
                   'accountId', 'companyId', 'orgCode', 'status'
               ]::text[]
               AND employee_version.payload_sha256 = pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(
                           '{"accountId":'
                           || pg_catalog.to_jsonb(
                               employee_version.payload_jsonb->>'accountId'
                           )::text
                           || ',"companyId":'
                           || pg_catalog.to_jsonb(
                               employee_version.payload_jsonb->>'companyId'
                           )::text
                           || ',"orgCode":'
                           || pg_catalog.to_jsonb(
                               employee_version.payload_jsonb->>'orgCode'
                           )::text
                           || ',"status":'
                           || pg_catalog.to_jsonb(
                               employee_version.payload_jsonb->>'status'
                           )::text
                           || '}',
                           'UTF8'
                       )
                   ),
                   'hex'
               )
               AND public.rsc_oam_inbox_allowed_0044(
                   'select',
                   p_required_capability,
                   inbox.batch_id,
                   inbox.source_system_id,
                   inbox.external_event_id,
                   inbox.entity_type,
                   inbox.external_id,
                   inbox.source_version,
                   inbox.source_updated_at,
                   inbox.payload_jsonb,
                   inbox.payload_sha256,
                   inbox.status
               )
               AND p_payload_jsonb = pg_catalog.jsonb_build_object(
                   'work_order_no', inbox.payload_jsonb->>'code',
                   'organization_id', person.organization_id::text,
                   'engineer_person_id', person.id::text,
                   'status', projection.projected_status
               )
               AND p_payload_sha256 = pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(
                           '{"engineer_person_id":'
                           || pg_catalog.to_jsonb(person.id::text)::text
                           || ',"organization_id":'
                           || pg_catalog.to_jsonb(
                               person.organization_id::text
                           )::text
                           || ',"status":'
                           || pg_catalog.to_jsonb(
                               projection.projected_status
                           )::text
                           || ',"work_order_no":'
                           || pg_catalog.to_jsonb(
                               inbox.payload_jsonb->>'code'
                           )::text
                           || '}',
                           'UTF8'
                       )
                   ),
                   'hex'
               )
               AND (
                   (
                       p_source_version ~
                           '^wo-v2:[0-9a-f]{64}:[0-9a-f]{64}$'
                       AND pg_catalog.split_part(
                           p_source_version, ':', 2
                       ) = inbox.payload_sha256
                       AND pg_catalog.split_part(
                           p_source_version, ':', 3
                       ) = pg_catalog.encode(
                           pg_catalog.sha256(
                               pg_catalog.convert_to(
                                   '{"approved_at":'
                                   || pg_catalog.to_jsonb(
                                       (
                                           CASE
                                               WHEN pg_catalog.date_trunc(
                                                   'second',
                                                   mapping.approved_at
                                               ) = mapping.approved_at
                                               THEN pg_catalog.to_char(
                                                   mapping.approved_at
                                                       AT TIME ZONE 'UTC',
                                                   'YYYY-MM-DD"T"HH24:MI:SS'
                                               )
                                               ELSE pg_catalog.to_char(
                                                   mapping.approved_at
                                                       AT TIME ZONE 'UTC',
                                                   'YYYY-MM-DD"T"HH24:MI:SS.US'
                                               )
                                           END
                                       ) || '+00:00'
                                   )::text
                                   || ',"approved_by_id":'
                                   || pg_catalog.to_jsonb(
                                       mapping.approved_by
                                   )::text
                                   || ',"external_object_id":'
                                   || pg_catalog.to_jsonb(
                                       employee.id::text
                                   )::text
                                   || ',"local_object_id":'
                                   || pg_catalog.to_jsonb(
                                       person.id::text
                                   )::text
                                   || ',"local_object_type":"person"'
                                   || ',"mapping_id":'
                                   || pg_catalog.to_jsonb(
                                       mapping.id::text
                                   )::text
                                   || ',"mapping_updated_at":'
                                   || pg_catalog.to_jsonb(
                                       (
                                           CASE
                                               WHEN pg_catalog.date_trunc(
                                                   'second',
                                                   mapping.updated_at
                                               ) = mapping.updated_at
                                               THEN pg_catalog.to_char(
                                                   mapping.updated_at
                                                       AT TIME ZONE 'UTC',
                                                   'YYYY-MM-DD"T"HH24:MI:SS'
                                               )
                                               ELSE pg_catalog.to_char(
                                                   mapping.updated_at
                                                       AT TIME ZONE 'UTC',
                                                   'YYYY-MM-DD"T"HH24:MI:SS.US'
                                               )
                                           END
                                       ) || '+00:00'
                                   )::text
                                   || ',"organization_id":'
                                   || pg_catalog.to_jsonb(
                                       organization.id::text
                                   )::text
                                   || ',"person_id":'
                                   || pg_catalog.to_jsonb(
                                       person.id::text
                                   )::text
                                   || '}',
                                   'UTF8'
                               )
                           ),
                           'hex'
                       )
                   )
               )
               AND mapping.approved_by IS NOT NULL
               AND mapping.approved_by = pg_catalog.btrim(mapping.approved_by)
               AND pg_catalog.length(mapping.approved_by) BETWEEN 1 AND 36
               AND mapping.approved_by !~ '[[:cntrl:]]'
               AND mapping.approved_at IS NOT NULL
               AND mapping.updated_at IS NOT NULL
               AND mapping.updated_at >= mapping.approved_at
               AND mapping.approved_at <= p_valid_from
               AND mapping.updated_at <= p_valid_from
               AND (
                   SELECT pg_catalog.count(*) = 1
                     FROM public.external_object_mappings AS approved_mapping
                    WHERE approved_mapping.external_object_id = employee.id
                      AND approved_mapping.local_object_type = 'person'
                      AND approved_mapping.status = 'approved'
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.people AS direct_person
                    WHERE direct_person.external_object_id = employee.id
                      AND direct_person.id <> person.id
               )
               AND public.rsc_oam_organization_allowed_0044(
                   person.organization_id
               )
               )
           )
       )
           )
       )
$$
"""


_CONFLICT_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_conflict_allowed_0044(
    p_required_capability text,
    p_run_id uuid,
    p_inbox_event_id uuid,
    p_external_object_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT session_user::text = 'star_oam_projector'
       AND p_required_capability IN ('projector_read', 'projector_write')
       AND (
           (p_inbox_event_id IS NULL)
           <> (p_external_object_id IS NULL)
       )
       AND EXISTS (
            SELECT 1
              FROM public.sync_runs AS run
              JOIN public.source_systems AS source ON source.id = run.source_system_id
              JOIN public.oam_sync_scope_bindings AS binding
                ON binding.enabled
               AND binding.principal_name = session_user::text
               AND binding.capability = p_required_capability
               AND binding.source_system = source.code
               AND binding.formal_scope_key = run.scope_key
               AND binding.entity_type = 'work_order'
             WHERE run.id = p_run_id
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', binding.source_instance,
                   'work_order_company_id', binding.company_id,
                   'work_order_org_code', binding.org_code,
                   'work_order_scope_key', binding.scope_key
               )
               AND (
                   p_inbox_event_id IS NULL
                   OR EXISTS (
                       SELECT 1
                         FROM public.sync_inbox_events AS inbox
                         JOIN public.sync_batches AS batch
                           ON batch.id = inbox.batch_id
                         JOIN public.sync_runs AS inbox_run
                           ON inbox_run.id = batch.run_id
                        WHERE inbox.id = p_inbox_event_id
                          AND inbox.source_system_id = source.id
                          AND inbox_run.source_system_id = source.id
                          AND inbox_run.scope_key = run.scope_key
                          AND inbox.entity_type = binding.entity_type
                          AND batch.entity_type = binding.entity_type
                          AND public.rsc_oam_inbox_allowed_0044(
                              'select',
                              p_required_capability,
                              inbox.batch_id,
                              inbox.source_system_id,
                              inbox.external_event_id,
                              inbox.entity_type,
                              inbox.external_id,
                              inbox.source_version,
                              inbox.source_updated_at,
                              inbox.payload_jsonb,
                              inbox.payload_sha256,
                              inbox.status
                          )
                   )
               )
               AND (
                   p_external_object_id IS NULL
                   OR EXISTS (
                       SELECT 1
                         FROM public.external_objects AS external
                        WHERE external.id = p_external_object_id
                          AND external.source_system_id = source.id
                          AND external.entity_type = binding.entity_type
                          AND public.rsc_oam_external_object_allowed_0044(
                              'select',
                              p_required_capability,
                              external.id,
                              external.source_system_id,
                              external.entity_type,
                              external.external_id,
                              external.current_version_id
                          )
                   )
               )
       )
$$
"""


_ORGANIZATION_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_organization_allowed_0044(
    p_organization_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    WITH RECURSIVE ancestry(id, parent_id, code, path) AS (
        SELECT organization.id,
               organization.parent_id,
               organization.code,
               ARRAY[organization.id]::uuid[]
          FROM public.organizations AS organization
         WHERE organization.id = p_organization_id
           AND organization.status = 'active'
        UNION ALL
        SELECT parent.id,
               parent.parent_id,
               parent.code,
               ancestry.path || parent.id
          FROM ancestry
          JOIN public.organizations AS parent ON parent.id = ancestry.parent_id
         WHERE parent.status = 'active'
           AND NOT parent.id = ANY(ancestry.path)
    )
    SELECT session_user::text = 'star_oam_projector'
       AND EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS binding
              JOIN public.source_systems AS source
                ON source.code = binding.source_system
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', binding.source_instance,
                   'work_order_company_id', binding.company_id,
                   'work_order_org_code', binding.org_code,
                   'work_order_scope_key', binding.scope_key
               )
              JOIN ancestry ON ancestry.code = binding.org_code
             WHERE binding.enabled
               AND binding.principal_name = session_user::text
               AND binding.capability = 'projector_read'
               AND binding.entity_type IN ('employee', 'work_order')
       )
$$
"""


_PERSON_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_person_allowed_0044(
    p_person_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT session_user::text = 'star_oam_projector'
       AND EXISTS (
            SELECT 1
              FROM public.people AS person
              JOIN public.external_object_mappings AS mapping
                ON mapping.local_object_type = 'person'
               AND mapping.local_object_id = person.id::text
               AND mapping.status = 'approved'
              JOIN public.external_objects AS employee
                ON employee.id = mapping.external_object_id
               AND employee.entity_type = 'employee'
               AND employee.deleted_at IS NULL
              JOIN public.external_object_versions AS employee_version
                ON employee_version.id = employee.current_version_id
               AND employee_version.external_object_id = employee.id
               AND employee_version.is_current
               AND employee_version.valid_to IS NULL
               AND employee_version.payload_jsonb->>'accountId' =
                   employee.external_id
               AND employee_version.payload_sha256 ~ '^[0-9a-f]{64}$'
             WHERE person.id = p_person_id
               AND person.employment_status = 'active'
               AND mapping.approved_by IS NOT NULL
               AND mapping.approved_by = pg_catalog.btrim(mapping.approved_by)
               AND pg_catalog.length(mapping.approved_by) BETWEEN 1 AND 36
               AND mapping.approved_by !~ '[[:cntrl:]]'
               AND mapping.approved_at IS NOT NULL
               AND mapping.updated_at IS NOT NULL
               AND mapping.updated_at >= mapping.approved_at
               AND mapping.approved_at <= pg_catalog.statement_timestamp()
               AND mapping.updated_at <= pg_catalog.statement_timestamp()
               AND (
                   SELECT pg_catalog.count(*) = 1
                     FROM public.external_object_mappings AS approved_mapping
                    WHERE approved_mapping.external_object_id = employee.id
                      AND approved_mapping.local_object_type = 'person'
                      AND approved_mapping.status = 'approved'
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.people AS direct_person
                    WHERE direct_person.external_object_id = employee.id
                      AND direct_person.id <> person.id
               )
               AND public.rsc_oam_organization_allowed_0044(
                   person.organization_id
               )
               AND public.rsc_oam_external_object_allowed_0044(
                   'select',
                   'projector_read',
                   employee.id,
                   employee.source_system_id,
                   employee.entity_type,
                   employee.external_id,
                   employee.current_version_id
               )
       )
$$
"""


_WORK_ORDER_HELPER_SQL = r"""
CREATE FUNCTION public.rsc_oam_work_order_allowed_0044(
    p_operation_name text,
    p_required_capability text,
    p_external_object_id uuid,
    p_work_order_no text,
    p_organization_id uuid,
    p_engineer_person_id uuid,
    p_status text,
    p_source_updated_at timestamptz,
    p_created_at timestamptz,
    p_updated_at timestamptz
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT session_user::text = 'star_oam_projector'
       AND p_operation_name IN (
           'select', 'insert', 'update_old', 'update_new'
       )
       AND p_required_capability IN ('projector_read', 'projector_write')
       AND p_source_updated_at <= p_updated_at
       AND p_created_at <= p_updated_at
       AND p_updated_at <= pg_catalog.statement_timestamp()
       AND EXISTS (
            SELECT 1
              FROM public.external_objects AS external
              JOIN public.external_object_versions AS version
                ON version.external_object_id = external.id
             WHERE external.id = p_external_object_id
               AND external.entity_type = 'work_order'
               AND external.deleted_at IS NULL
               AND (
                   (
                       p_operation_name IN ('select', 'update_old')
                       AND (
                           (version.is_current AND version.valid_to IS NULL)
                           OR (
                               NOT version.is_current
                               AND version.valid_to IS NOT NULL
                           )
                       )
                   )
                   OR (
                       p_operation_name IN ('insert', 'update_new')
                       AND version.id = external.current_version_id
                       AND version.is_current
                       AND version.valid_to IS NULL
                   )
               )
               AND version.source_updated_at = p_source_updated_at
               AND version.valid_from = version.created_at
               AND version.valid_from <= p_updated_at
               AND version.payload_jsonb = pg_catalog.jsonb_build_object(
                   'work_order_no', p_work_order_no,
                   'organization_id', p_organization_id::text,
                   'engineer_person_id', p_engineer_person_id::text,
                   'status', p_status
               )
               AND public.rsc_oam_version_allowed_0044(
                   'select',
                   p_required_capability,
                   version.id,
                   version.external_object_id,
                   version.source_version,
                   version.source_updated_at,
                   version.valid_from,
                   version.valid_to,
                   version.payload_jsonb,
                   version.payload_sha256,
                   version.is_current,
                   version.created_at
               )
       )
$$
"""


_SNAPSHOT_TRANSITION_GUARD_SQL = r"""
CREATE FUNCTION public.rsc_oam_snapshot_transition_guard_0044()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF session_user::text = 'star_oam_migrator' THEN
        RETURN NEW;
    END IF;
    IF session_user::text <> 'edge_inbox' THEN
        RAISE EXCEPTION '0044 snapshot transition caller is not authorized'
            USING ERRCODE = '42501';
    END IF;
    IF OLD.status <> 'receiving' THEN
        RAISE EXCEPTION '0044 terminal snapshot is immutable'
            USING ERRCODE = '42501';
    END IF;

    IF NEW.status = 'complete' THEN
        IF NEW.manifest_json = ''
           OR NEW.manifest_sha256 !~ '^[0-9a-f]{64}$'
           OR NEW.manifest_sha256 <> pg_catalog.encode(
               pg_catalog.sha256(
                   pg_catalog.convert_to(NEW.manifest_json, 'UTF8')
               ),
               'hex'
           )
           OR NEW.completed_at IS NULL
           OR NEW.completed_at < NEW.received_at
           OR NEW.completed_at < NEW.snapshot_at
           OR NEW.completed_at > pg_catalog.statement_timestamp() THEN
            RAISE EXCEPTION '0044 completed snapshot seal is invalid'
                USING ERRCODE = '42501';
        END IF;
    ELSIF NEW.status = 'rejected_stale' THEN
        IF NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
           OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256
           OR NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
            RAISE EXCEPTION '0044 rejected snapshot cannot be resealed'
                USING ERRCODE = '42501';
        END IF;
    ELSIF NEW.status = 'receiving' THEN
        IF NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
           OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256
           OR NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
            RAISE EXCEPTION '0044 receiving snapshot seal is immutable'
                USING ERRCODE = '42501';
        END IF;
    ELSE
        RAISE EXCEPTION '0044 snapshot transition is not allowed'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$
"""


_PROJECTION_CHAIN_GUARD_SQL = r"""
CREATE FUNCTION public.rsc_oam_projection_chain_guard_0044()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    checked_object_id uuid;
    checked_pointer_id uuid;
    checked_entity_type text;
    checked_deleted_at timestamptz;
    current_version_count bigint;
    current_version_id uuid;
    row_json jsonb;
BEGIN
    IF session_user::text = 'star_oam_migrator' THEN
        RETURN NULL;
    END IF;
    IF session_user::text <> 'star_oam_projector' THEN
        RAISE EXCEPTION '0044 projection chain caller is not authorized'
            USING ERRCODE = '42501';
    END IF;

    row_json := pg_catalog.to_jsonb(NEW);
    checked_object_id := CASE
        WHEN TG_TABLE_NAME = 'external_objects'
            THEN (row_json->>'id')::uuid
        ELSE (row_json->>'external_object_id')::uuid
    END;
    SELECT external.current_version_id,
           external.entity_type,
           external.deleted_at
      INTO checked_pointer_id, checked_entity_type, checked_deleted_at
      FROM public.external_objects AS external
     WHERE external.id = checked_object_id;
    IF NOT FOUND OR checked_entity_type <> 'work_order' THEN
        RETURN NULL;
    END IF;
    IF checked_deleted_at IS NOT NULL OR checked_pointer_id IS NULL THEN
        RAISE EXCEPTION '0044 work-order external object chain is incomplete'
            USING ERRCODE = '42501';
    END IF;

    SELECT pg_catalog.count(*),
           pg_catalog.min(version.id::text)::uuid
      INTO current_version_count, current_version_id
      FROM public.external_object_versions AS version
     WHERE version.external_object_id = checked_object_id
       AND version.is_current
       AND version.valid_to IS NULL;
    IF current_version_count <> 1
       OR current_version_id IS DISTINCT FROM checked_pointer_id THEN
        RAISE EXCEPTION '0044 work-order current-version pointer is invalid'
            USING ERRCODE = '42501';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.external_object_versions AS version
         WHERE version.external_object_id = checked_object_id
           AND (
               version.created_at IS DISTINCT FROM version.valid_from
               OR version.source_updated_at IS NULL
               OR (
                   version.is_current
                   AND (
                       version.id IS DISTINCT FROM checked_pointer_id
                       OR version.valid_to IS NOT NULL
                   )
               )
               OR (
                   NOT version.is_current
                   AND (
                       version.valid_to IS NULL
                       OR version.valid_to <= version.valid_from
                       OR (
                           SELECT pg_catalog.count(*)
                             FROM public.external_object_versions AS successor
                            WHERE successor.external_object_id =
                                  checked_object_id
                              AND successor.valid_from = version.valid_to
                       ) <> 1
                       OR EXISTS (
                           SELECT 1
                             FROM public.external_object_versions AS successor
                            WHERE successor.external_object_id =
                                  checked_object_id
                              AND successor.valid_from = version.valid_to
                              AND successor.source_updated_at <
                                  version.source_updated_at
                       )
                   )
               )
               OR (
                   SELECT pg_catalog.count(*)
                     FROM public.external_object_versions AS predecessor
                    WHERE predecessor.external_object_id = checked_object_id
                      AND predecessor.valid_to = version.valid_from
               ) > 1
           )
    ) OR (
        SELECT pg_catalog.count(*) <>
               pg_catalog.count(DISTINCT version.valid_from)
          FROM public.external_object_versions AS version
         WHERE version.external_object_id = checked_object_id
    ) THEN
        RAISE EXCEPTION '0044 work-order version history is invalid'
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM public.external_object_versions AS version
          JOIN public.oam_work_orders AS work_order
            ON work_order.external_object_id = checked_object_id
           AND work_order.source_updated_at = version.source_updated_at
           AND version.payload_jsonb = pg_catalog.jsonb_build_object(
               'work_order_no', work_order.work_order_no,
               'organization_id', work_order.organization_id::text,
               'engineer_person_id', work_order.engineer_person_id::text,
               'status', work_order.status
           )
         WHERE version.id = checked_pointer_id
           AND version.external_object_id = checked_object_id
           AND version.is_current
           AND version.valid_to IS NULL
           AND version.valid_from = version.created_at
           AND version.valid_from <= work_order.updated_at
    ) THEN
        RAISE EXCEPTION '0044 work-order projection chain is incomplete'
            USING ERRCODE = '42501';
    END IF;
    RETURN NULL;
END
$$
"""


_RLS_CHECK_FUNCTION_SQL = r"""
CREATE FUNCTION public.rsc_oam_rls_check_0044(
    resource_name text,
    operation_name text,
    row_data jsonb
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    caller_role text := session_user::text;
    required_capability text;
BEGIN
    IF row_data IS NULL
       OR operation_name NOT IN (
           'select', 'insert', 'update', 'update_old', 'update_new', 'delete'
       )
       OR caller_role NOT IN ('edge_inbox', 'star_oam_projector') THEN
        RETURN false;
    END IF;

    IF caller_role = 'edge_inbox' THEN
        required_capability := 'edge_ingress';
        IF resource_name = 'external_sync_snapshots'
           AND operation_name IN ('select', 'insert', 'update') THEN
            RETURN (
                operation_name <> 'insert'
                OR (
                    row_data->>'status' = 'receiving'
                    AND row_data->>'manifest_json' = ''
                    AND row_data->>'manifest_sha256' = ''
                    AND row_data->>'completed_at' IS NULL
                )
            ) AND public.rsc_oam_binding_allowed_0044(
                    caller_role,
                    required_capability,
                    row_data->>'source_system',
                    row_data->>'source_instance',
                    row_data->>'scope_key',
                    row_data->>'company_id',
                    row_data->>'org_code',
                    NULL
                );
        ELSIF resource_name IN (
            'external_sync_snapshot_batches',
            'external_sync_snapshot_records'
        ) AND operation_name IN ('select', 'insert') THEN
            RETURN EXISTS (
                SELECT 1
                 FROM public.external_sync_snapshots AS snapshot
                 WHERE snapshot.id = row_data->>'snapshot_ref_id'
                   AND (
                       operation_name <> 'insert'
                       OR snapshot.status = 'receiving'
                   )
                   AND (
                       resource_name = 'external_sync_snapshot_records'
                       OR row_data->>'source_instance' = snapshot.source_instance
                   )
                   AND public.rsc_oam_binding_allowed_0044(
                       caller_role,
                       required_capability,
                       snapshot.source_system,
                       snapshot.source_instance,
                       snapshot.scope_key,
                       snapshot.company_id,
                       snapshot.org_code,
                       row_data->>'entity_type'
                   )
            );
        ELSIF resource_name = 'external_sync_current_records'
           AND operation_name IN ('select', 'insert', 'update', 'delete') THEN
            RETURN EXISTS (
                SELECT 1
                 FROM public.external_sync_snapshots AS snapshot
                 WHERE snapshot.id = row_data->>'last_snapshot_id'
                   AND row_data->>'source_system' = snapshot.source_system
                   AND row_data->>'source_instance' = snapshot.source_instance
                   AND row_data->>'scope_key' = snapshot.scope_key
                   AND (
                       operation_name = 'select'
                       OR (
                           operation_name IN ('insert', 'update')
                           AND snapshot.status = 'receiving'
                       )
                       OR (
                           operation_name = 'delete'
                           AND EXISTS (
                               SELECT 1
                                 FROM public.external_sync_snapshots
                                      AS receiving_snapshot
                                WHERE receiving_snapshot.source_system =
                                      snapshot.source_system
                                  AND receiving_snapshot.source_instance =
                                      snapshot.source_instance
                                  AND receiving_snapshot.scope_key =
                                      snapshot.scope_key
                                  AND receiving_snapshot.company_id =
                                      snapshot.company_id
                                  AND receiving_snapshot.org_code =
                                      snapshot.org_code
                                  AND receiving_snapshot.status = 'receiving'
                           )
                       )
                   )
                   AND public.rsc_oam_binding_allowed_0044(
                       caller_role,
                       required_capability,
                       snapshot.source_system,
                       snapshot.source_instance,
                       snapshot.scope_key,
                       snapshot.company_id,
                       snapshot.org_code,
                       row_data->>'entity_type'
                   )
            );
        ELSIF resource_name = 'audit_logs'
           AND operation_name = 'insert'
           AND row_data->>'actor_id' IS NULL
           AND row_data->>'entity_type' = 'external_sync_snapshot'
           AND row_data->>'action' IN (
               'edge_sync.snapshot_batch.receive',
               'edge_sync.snapshot.reject_stale',
               'edge_sync.snapshot.complete'
           ) THEN
            RETURN EXISTS (
                SELECT 1
                 FROM public.external_sync_snapshots AS snapshot
                 WHERE snapshot.id = row_data->>'entity_id'
                   AND (
                       (row_data->>'action' =
                            'edge_sync.snapshot_batch.receive'
                        AND snapshot.status = 'receiving')
                       OR (row_data->>'action' =
                               'edge_sync.snapshot.reject_stale'
                           AND snapshot.status = 'rejected_stale')
                       OR (row_data->>'action' =
                               'edge_sync.snapshot.complete'
                           AND snapshot.status = 'complete')
                   )
                   AND public.rsc_oam_binding_allowed_0044(
                       caller_role,
                       required_capability,
                       snapshot.source_system,
                       snapshot.source_instance,
                       snapshot.scope_key,
                       snapshot.company_id,
                       snapshot.org_code,
                       NULL
                   )
            );
        END IF;
        RETURN false;
    END IF;

    required_capability := CASE
        WHEN operation_name = 'select' THEN 'projector_read'
        ELSE 'projector_write'
    END;

    IF resource_name = 'external_sync_snapshots'
       AND operation_name = 'select' THEN
        RETURN public.rsc_oam_binding_allowed_0044(
            caller_role,
            required_capability,
            row_data->>'source_system',
            row_data->>'source_instance',
            row_data->>'scope_key',
            row_data->>'company_id',
            row_data->>'org_code',
            NULL
        );
    ELSIF resource_name IN (
        'external_sync_snapshot_batches',
        'external_sync_snapshot_records'
    ) AND operation_name = 'select' THEN
        RETURN EXISTS (
            SELECT 1
              FROM public.external_sync_snapshots AS snapshot
             WHERE snapshot.id = row_data->>'snapshot_ref_id'
               AND (
                   resource_name = 'external_sync_snapshot_records'
                   OR row_data->>'source_instance' = snapshot.source_instance
               )
               AND public.rsc_oam_binding_allowed_0044(
                   caller_role,
                   required_capability,
                   snapshot.source_system,
                   snapshot.source_instance,
                   snapshot.scope_key,
                   snapshot.company_id,
                   snapshot.org_code,
                   row_data->>'entity_type'
               )
        );
    ELSIF resource_name = 'external_sync_current_records'
       AND operation_name = 'select' THEN
        RETURN EXISTS (
            SELECT 1
              FROM public.external_sync_snapshots AS snapshot
             WHERE snapshot.id = row_data->>'last_snapshot_id'
               AND row_data->>'source_system' = snapshot.source_system
               AND row_data->>'source_instance' = snapshot.source_instance
               AND row_data->>'scope_key' = snapshot.scope_key
               AND public.rsc_oam_binding_allowed_0044(
                   caller_role,
                   required_capability,
                   snapshot.source_system,
                   snapshot.source_instance,
                   snapshot.scope_key,
                   snapshot.company_id,
                   snapshot.org_code,
                   row_data->>'entity_type'
               )
        );
    ELSIF resource_name = 'source_systems'
       AND operation_name = 'select' THEN
        RETURN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS binding
             WHERE binding.enabled
               AND binding.principal_name = caller_role
               AND binding.capability = required_capability
               AND binding.source_system = row_data->>'code'
               AND row_data->>'mode' = 'read_only'
               AND (row_data->>'enabled')::boolean
               AND row_data->'configuration_jsonb' = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', binding.source_instance,
                   'work_order_company_id', binding.company_id,
                   'work_order_org_code', binding.org_code,
                   'work_order_scope_key', binding.scope_key
               )
        );
    ELSIF resource_name = 'sync_runs'
       AND operation_name IN ('select', 'insert', 'update') THEN
        RETURN public.rsc_oam_run_snapshot_allowed_0044(
            required_capability,
            (row_data->>'source_system_id')::uuid,
            row_data->>'run_key',
            row_data->>'scope_key',
            row_data->>'mode',
            row_data->>'watermark_to',
            row_data->>'manifest_sha256'
        );
    ELSIF resource_name = 'sync_batches'
       AND operation_name IN ('select', 'insert', 'update') THEN
        RETURN EXISTS (
            SELECT 1
              FROM public.sync_runs AS run
              JOIN public.source_systems AS source ON source.id = run.source_system_id
              JOIN public.oam_sync_scope_bindings AS binding
                ON binding.enabled
               AND binding.principal_name = caller_role
               AND binding.capability = required_capability
               AND binding.source_system = source.code
               AND binding.formal_scope_key = run.scope_key
               AND binding.entity_type = row_data->>'entity_type'
             WHERE run.id = (row_data->>'run_id')::uuid
               AND public.rsc_oam_run_snapshot_allowed_0044(
                   required_capability,
                   run.source_system_id,
                   run.run_key,
                   run.scope_key,
                   run.mode,
                   run.watermark_to,
                   run.manifest_sha256
               )
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', binding.source_instance,
                   'work_order_company_id', binding.company_id,
                   'work_order_org_code', binding.org_code,
                   'work_order_scope_key', binding.scope_key
               )
        );
    ELSIF resource_name = 'sync_inbox_events'
       AND operation_name IN ('select', 'insert', 'update') THEN
        RETURN public.rsc_oam_inbox_allowed_0044(
            operation_name,
            required_capability,
            (row_data->>'batch_id')::uuid,
            (row_data->>'source_system_id')::uuid,
            row_data->>'external_event_id',
            row_data->>'entity_type',
            row_data->>'external_id',
            row_data->>'source_version',
            (row_data->>'source_updated_at')::timestamptz,
            row_data->'payload_jsonb',
            row_data->>'payload_sha256',
            row_data->>'status'
        );
    ELSIF resource_name = 'external_objects'
       AND operation_name IN (
           'select', 'insert', 'update_old', 'update_new'
       ) THEN
        RETURN public.rsc_oam_external_object_allowed_0044(
            operation_name,
            required_capability,
            (row_data->>'id')::uuid,
            (row_data->>'source_system_id')::uuid,
            row_data->>'entity_type',
            row_data->>'external_id',
            NULLIF(row_data->>'current_version_id', '')::uuid
        );
    ELSIF resource_name = 'external_object_versions'
       AND operation_name IN (
           'select', 'insert', 'update_old', 'update_new'
       ) THEN
        RETURN public.rsc_oam_version_allowed_0044(
            operation_name,
            required_capability,
            (row_data->>'id')::uuid,
            (row_data->>'external_object_id')::uuid,
            row_data->>'source_version',
            (row_data->>'source_updated_at')::timestamptz,
            (row_data->>'valid_from')::timestamptz,
            NULLIF(row_data->>'valid_to', '')::timestamptz,
            row_data->'payload_jsonb',
            row_data->>'payload_sha256',
            (row_data->>'is_current')::boolean,
            (row_data->>'created_at')::timestamptz
        );
    ELSIF resource_name = 'external_object_mappings'
       AND operation_name = 'select'
       AND row_data->>'local_object_type' = 'person'
       AND row_data->>'status' = 'approved' THEN
        RETURN EXISTS (
            SELECT 1
              FROM public.external_objects AS external
             WHERE external.id = (row_data->>'external_object_id')::uuid
               AND external.entity_type = 'employee'
               AND public.rsc_oam_external_object_allowed_0044(
                   'select',
                   required_capability,
                   external.id,
                   external.source_system_id,
                   external.entity_type,
                   external.external_id,
                   external.current_version_id
               )
        );
    ELSIF resource_name = 'sync_conflicts'
       AND operation_name IN ('select', 'insert', 'update') THEN
        RETURN public.rsc_oam_conflict_allowed_0044(
            required_capability,
            (row_data->>'run_id')::uuid,
            NULLIF(row_data->>'inbox_event_id', '')::uuid,
            NULLIF(row_data->>'external_object_id', '')::uuid
        );
    ELSIF resource_name = 'organizations'
       AND operation_name = 'select' THEN
        RETURN public.rsc_oam_organization_allowed_0044(
            (row_data->>'id')::uuid
        );
    ELSIF resource_name = 'people'
       AND operation_name = 'select' THEN
        RETURN public.rsc_oam_person_allowed_0044(
            (row_data->>'id')::uuid
        );
    ELSIF resource_name = 'oam_work_orders'
       AND operation_name IN (
           'select', 'insert', 'update_old', 'update_new'
       ) THEN
        RETURN public.rsc_oam_work_order_allowed_0044(
            operation_name,
            required_capability,
            (row_data->>'external_object_id')::uuid,
            row_data->>'work_order_no',
            (row_data->>'organization_id')::uuid,
            (row_data->>'engineer_person_id')::uuid,
            row_data->>'status',
            (row_data->>'source_updated_at')::timestamptz,
            (row_data->>'created_at')::timestamptz,
            (row_data->>'updated_at')::timestamptz
        );
    END IF;
    RETURN false;
EXCEPTION
    WHEN data_exception THEN
        RETURN false;
END
$$
"""


_RUNTIME_READY_FUNCTION_SQL = r"""
CREATE FUNCTION public.rsc_oam_runtime_binding_ready_0044()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT (
        SELECT pg_catalog.count(*) = 1
           AND pg_catalog.min(version_num) = '20260902_0044'
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
