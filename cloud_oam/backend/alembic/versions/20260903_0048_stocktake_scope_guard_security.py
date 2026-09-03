"""Repair the stocktake scope guard execution boundary without widening ACLs.

Revision ID: 20260903_0048
Revises: 20260903_0047
Create Date: 2026-09-03

Revision 0025 installed the correct region-owner validation body and bound it
to an ENABLE ALWAYS trigger, but left the trigger function as SECURITY
INVOKER.  The runtime API role can read the OAM-owned organization and location
projections but deliberately lacks the UPDATE authority PostgreSQL also
requires for the guard's ``FOR SHARE`` row locks, so a valid formal scope insert
fails with PostgreSQL 42501 before the guard can return its decision.

This narrow forward repair locks the complete scope graph and fail-closed
verifies the legacy function catalog, immutable body hash, trigger binding,
owner, search path and ACL before changing only its execution context.  The
function becomes migration-owned SECURITY DEFINER with its existing fixed
``pg_catalog, public`` search path; its body is not replaced, and PUBLIC/API
EXECUTE remains revoked.
No business row is rewritten and no API master-data privilege is added.

Downgrade performs the symmetric hardened-catalog verification before
restoring the exact SECURITY INVOKER execution context from revision 0025.
SQLite is an explicit schema no-op used only for local migration-chain
compatibility; it is not evidence equivalent to the PostgreSQL 16 boundary.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0048"
down_revision: Union[str, Sequence[str], None] = "20260903_0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
OAM_RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
PREVIOUS_SCHEMA_REVISION = "20260903_0047"

SCOPE_GUARD_FUNCTION = "rsc_validate_stocktake_scope_region_owner_0025"
SCOPE_GUARD_TRIGGER = "trg_stocktake_scopes_region_owner_0025"
EXPECTED_FUNCTION_BODY_SHA256 = (
    "904a443c2c5930356af0f15f444f29ec6b6ce61f32294e4b2e3b40dd3a0e4e8e"
)

SCOPE_GRAPH_TABLES = (
    "organizations",
    "stock_locations",
    "stocktake_tasks",
    "stocktake_scopes",
)
LEGACY_SEARCH_PATH = "search_path=pg_catalog, public"
HARDENED_SEARCH_PATH = "search_path=pg_catalog, public"

CATALOG_ERROR = "0048 stocktake scope guard catalog verification failed"


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0048 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_scope_graph()
    _verify_scope_guard_catalog(
        security_definer=False,
        expected_search_path=LEGACY_SEARCH_PATH,
        phase="legacy upgrade preflight",
    )
    _set_scope_guard_security(
        security_definer=True,
        search_path="pg_catalog, public",
    )
    _verify_scope_guard_catalog(
        security_definer=True,
        expected_search_path=HARDENED_SEARCH_PATH,
        phase="hardened upgrade postflight",
    )
    _replace_oam_runtime_ready_function(revision)


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_scope_graph()
    _verify_scope_guard_catalog(
        security_definer=True,
        expected_search_path=HARDENED_SEARCH_PATH,
        phase="hardened downgrade preflight",
    )
    _set_scope_guard_security(
        security_definer=False,
        search_path="pg_catalog, public",
    )
    _verify_scope_guard_catalog(
        security_definer=False,
        expected_search_path=LEGACY_SEARCH_PATH,
        phase="legacy downgrade postflight",
    )
    _replace_oam_runtime_ready_function(PREVIOUS_SCHEMA_REVISION)


def _lock_scope_graph() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in SCOPE_GRAPH_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _verify_scope_guard_catalog(
    *,
    security_definer: bool,
    expected_search_path: str,
    phase: str,
) -> None:
    expected_security = "TRUE" if security_definer else "FALSE"
    escaped_phase = phase.replace("'", "''")
    escaped_search_path = expected_search_path.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0048$
DECLARE
    guard_oid oid;
    api_oid oid;
    migrator_oid oid;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;

    SELECT role_row.oid
      INTO migrator_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{MIGRATION_ROLE}';
    SELECT role_row.oid
      INTO api_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{PRODUCTION_API_ROLE}';
    IF migrator_oid IS NULL OR api_oid IS NULL THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: required role is missing';
    END IF;

    guard_oid := pg_catalog.to_regprocedure(
        'public.{SCOPE_GUARD_FUNCTION}()'
    );
    IF guard_oid IS NULL OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
         WHERE schema_row.nspname = 'public'
           AND function_row.proname = '{SCOPE_GUARD_FUNCTION}'
    ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function identity mismatch';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid = guard_oid
           AND function_row.proowner = migrator_oid
           AND function_row.prokind = 'f'
           AND function_row.pronargs = 0
           AND function_row.prorettype = 'trigger'::pg_catalog.regtype
           AND language_row.lanname = 'plpgsql'
           AND function_row.provolatile = 'v'
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.prosecdef IS {expected_security}
           AND function_row.proconfig IS NOT DISTINCT FROM
               ARRAY['{escaped_search_path}']::text[]
           AND pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               ) = '{EXPECTED_FUNCTION_BODY_SHA256}'
    ) THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function definition mismatch';
    END IF;

    IF (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgname = '{SCOPE_GUARD_TRIGGER}'
           AND trigger_row.tgrelid =
               'public.stocktake_scopes'::pg_catalog.regclass
           AND trigger_row.tgfoid = guard_oid
           AND trigger_row.tgenabled = 'A'
           AND trigger_row.tgtype = 7
           AND trigger_row.tgconstraint = 0
           AND NOT trigger_row.tgdeferrable
           AND NOT trigger_row.tginitdeferred
           AND trigger_row.tgqual IS NULL
           AND trigger_row.tgnargs = 0
           AND trigger_row.tgattr = ''::pg_catalog.int2vector
    ) <> 1 OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgname = '{SCOPE_GUARD_TRIGGER}'
    ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: trigger binding mismatch';
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
         WHERE function_row.oid = guard_oid
           AND (
               function_acl.privilege_type <> 'EXECUTE'
               OR function_acl.grantee <> migrator_oid
           )
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
             COALESCE(
                 function_row.proacl,
                 pg_catalog.acldefault('f', function_row.proowner)
             )
         ) AS function_acl
         WHERE function_row.oid = guard_oid
           AND function_acl.privilege_type = 'EXECUTE'
           AND function_acl.grantee = migrator_oid
    ) <> 1 OR pg_catalog.has_function_privilege(
        api_oid,
        guard_oid,
        'EXECUTE'
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
             COALESCE(
                 function_row.proacl,
                 pg_catalog.acldefault('f', function_row.proowner)
             )
         ) AS function_acl
         WHERE function_row.oid = guard_oid
           AND function_acl.grantee = 0
    ) THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function ACL mismatch';
    END IF;
END
$rsc_0048$
"""
    )


def _set_scope_guard_security(
    *,
    security_definer: bool,
    search_path: str,
) -> None:
    security = "DEFINER" if security_definer else "INVOKER"
    signature = f"public.{SCOPE_GUARD_FUNCTION}()"
    op.execute(f"ALTER FUNCTION {signature} SECURITY {security}")
    op.execute(f"ALTER FUNCTION {signature} SET search_path = {search_path}")
    op.execute(f"ALTER FUNCTION {signature} OWNER TO {MIGRATION_ROLE}")
    op.execute(
        f"REVOKE ALL ON FUNCTION {signature} "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )


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
