"""Disambiguate the stocktake observation scope-mode source column.

Revision ID: 20260903_0050
Revises: 20260903_0049
Create Date: 2026-09-03

The round-aware observation trigger introduced in revision 0021 selects a
``scope_mode`` column into a PL/pgSQL variable with the same name.  PostgreSQL
therefore rejects the trigger path as an ambiguous reference before the frozen
scope can be validated.  This narrow forward repair qualifies that one source
column as ``stocktake_scopes.scope_mode`` and changes nothing else.

PostgreSQL locks the sole trigger table and fail-closed verifies the exact
function identity, shape, owner, ACL, security mode, search path, body hash and
single live trigger binding before and after replacement.  The function text
is obtained from PostgreSQL only after the legacy hash and a unique source
fragment have matched; ``CREATE OR REPLACE`` consequently preserves its owner,
closed ACL and SECURITY DEFINER boundary.  Downgrade applies the exact inverse
replacement under the same checks.  No business row or privilege is changed.

SQLite is an explicit schema no-op for local migration-chain compatibility;
it is not PostgreSQL security evidence.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0050"
down_revision: Union[str, Sequence[str], None] = "20260903_0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
OAM_RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
PREVIOUS_SCHEMA_REVISION = "20260903_0049"

FUNCTION_NAME = "rsc_validate_stocktake_observation_insert_0021"
FUNCTION_SIGNATURE = f"public.{FUNCTION_NAME}()"
TRIGGER_TABLE = "stocktake_count_observations"
TRIGGER_NAME = "trg_stocktake_count_observations_assignment_0021"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"

LEGACY_BODY_SHA256 = (
    "082b8afe54b38790b15d72b3f946fbf7c943b2780cd5ba3f43a9dee34db337f3"
)
QUALIFIED_BODY_SHA256 = (
    "06cf2fafa1d90f120fe4bba21cc1dc55dba70bd63f649671b4333a6159af06bb"
)
LEGACY_SOURCE_FRAGMENT = (
    "           scope_mode, material_id, condition_code,"
)
QUALIFIED_SOURCE_FRAGMENT = (
    "           stocktake_scopes.scope_mode, material_id, condition_code,"
)

CATALOG_ERROR = (
    "0050 stocktake observation scope-mode catalog verification failed"
)
REPLACEMENT_ERROR = (
    "0050 stocktake observation scope-mode replacement failed"
)


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0050 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_trigger_table()
    _verify_catalog(
        expected_body_sha256=LEGACY_BODY_SHA256,
        expected_source_fragment=LEGACY_SOURCE_FRAGMENT,
        forbidden_source_fragment=QUALIFIED_SOURCE_FRAGMENT,
        phase="legacy upgrade preflight",
    )
    _replace_body_fragment(
        expected_body_sha256=LEGACY_BODY_SHA256,
        source_fragment=LEGACY_SOURCE_FRAGMENT,
        replacement_fragment=QUALIFIED_SOURCE_FRAGMENT,
        phase="upgrade",
    )
    _verify_catalog(
        expected_body_sha256=QUALIFIED_BODY_SHA256,
        expected_source_fragment=QUALIFIED_SOURCE_FRAGMENT,
        forbidden_source_fragment=LEGACY_SOURCE_FRAGMENT,
        phase="qualified upgrade postflight",
    )
    _replace_oam_runtime_ready_function(revision)


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_trigger_table()
    _verify_catalog(
        expected_body_sha256=QUALIFIED_BODY_SHA256,
        expected_source_fragment=QUALIFIED_SOURCE_FRAGMENT,
        forbidden_source_fragment=LEGACY_SOURCE_FRAGMENT,
        phase="qualified downgrade preflight",
    )
    _replace_body_fragment(
        expected_body_sha256=QUALIFIED_BODY_SHA256,
        source_fragment=QUALIFIED_SOURCE_FRAGMENT,
        replacement_fragment=LEGACY_SOURCE_FRAGMENT,
        phase="downgrade",
    )
    _verify_catalog(
        expected_body_sha256=LEGACY_BODY_SHA256,
        expected_source_fragment=LEGACY_SOURCE_FRAGMENT,
        forbidden_source_fragment=QUALIFIED_SOURCE_FRAGMENT,
        phase="legacy downgrade postflight",
    )
    _replace_oam_runtime_ready_function(PREVIOUS_SCHEMA_REVISION)


def _lock_trigger_table() -> None:
    op.execute(
        f"LOCK TABLE public.{TRIGGER_TABLE} IN ACCESS EXCLUSIVE MODE"
    )


def _verify_catalog(
    *,
    expected_body_sha256: str,
    expected_source_fragment: str,
    forbidden_source_fragment: str,
    phase: str,
) -> None:
    if expected_body_sha256 not in {
        LEGACY_BODY_SHA256,
        QUALIFIED_BODY_SHA256,
    }:
        raise ValueError("unsupported stocktake observation body hash")
    fragment_pairs = {
        (LEGACY_SOURCE_FRAGMENT, QUALIFIED_SOURCE_FRAGMENT),
        (QUALIFIED_SOURCE_FRAGMENT, LEGACY_SOURCE_FRAGMENT),
    }
    if (expected_source_fragment, forbidden_source_fragment) not in fragment_pairs:
        raise ValueError("unsupported stocktake observation source fragments")

    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0050_catalog$
DECLARE
    api_oid oid;
    migrator_oid oid;
    function_oid oid;
    function_source text;
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

    function_oid := pg_catalog.to_regprocedure('{FUNCTION_SIGNATURE}');
    IF function_oid IS NULL OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
         WHERE schema_row.nspname = 'public'
           AND function_row.proname = '{FUNCTION_NAME}'
    ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function identity mismatch';
    END IF;

    SELECT function_row.prosrc
      INTO function_source
      FROM pg_catalog.pg_proc AS function_row
      JOIN pg_catalog.pg_language AS language_row
        ON language_row.oid = function_row.prolang
     WHERE function_row.oid = function_oid
       AND function_row.proowner = migrator_oid
       AND function_row.prokind = 'f'
       AND function_row.prorettype = pg_catalog.to_regtype('trigger')
       AND NOT function_row.proretset
       AND function_row.pronargs = 0
       AND function_row.proargtypes = ''::pg_catalog.oidvector
       AND function_row.proallargtypes IS NULL
       AND function_row.proargnames IS NULL
       AND function_row.proargmodes IS NULL
       AND function_row.pronargdefaults = 0
       AND function_row.proargdefaults IS NULL
       AND function_row.provariadic = 0
       AND language_row.lanname = 'plpgsql'
       AND function_row.provolatile = 'v'
       AND NOT function_row.proisstrict
       AND NOT function_row.proleakproof
       AND function_row.proparallel = 'u'
       AND function_row.prosecdef
       AND function_row.proconfig = ARRAY['{FIXED_SEARCH_PATH}']::text[]
       AND pg_catalog.encode(
               pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')
               ),
               'hex'
           ) = '{expected_body_sha256}';
    IF function_source IS NULL THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function definition mismatch';
    END IF;

    IF (
        pg_catalog.length(function_source)
        - pg_catalog.length(
            pg_catalog.replace(
                function_source,
                $rsc_0050_expected${expected_source_fragment}$rsc_0050_expected$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0050_expected${expected_source_fragment}$rsc_0050_expected$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0050_forbidden${forbidden_source_fragment}$rsc_0050_forbidden$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: source fragment mismatch';
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
         WHERE function_row.oid = function_oid
           AND (
               function_acl.privilege_type <> 'EXECUTE'
               OR function_acl.grantee <> migrator_oid
               OR function_acl.grantor <> migrator_oid
               OR function_acl.is_grantable
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
         WHERE function_row.oid = function_oid
           AND function_acl.privilege_type = 'EXECUTE'
           AND function_acl.grantee = migrator_oid
           AND function_acl.grantor = migrator_oid
           AND NOT function_acl.is_grantable
    ) <> 1 OR pg_catalog.has_function_privilege(
        api_oid,
        function_oid,
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
         WHERE function_row.oid = function_oid
           AND function_acl.grantee = 0
    ) THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function ACL mismatch';
    END IF;

    IF (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgname = '{TRIGGER_NAME}'
           AND trigger_row.tgrelid =
               pg_catalog.to_regclass('public.{TRIGGER_TABLE}')
           AND trigger_row.tgfoid = function_oid
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
           AND trigger_row.tgname = '{TRIGGER_NAME}'
    ) <> 1 OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = function_oid
    ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: trigger binding mismatch';
    END IF;
END
$rsc_0050_catalog$
"""
    )


def _replace_body_fragment(
    *,
    expected_body_sha256: str,
    source_fragment: str,
    replacement_fragment: str,
    phase: str,
) -> None:
    expected_pairs = {
        (
            LEGACY_BODY_SHA256,
            LEGACY_SOURCE_FRAGMENT,
            QUALIFIED_SOURCE_FRAGMENT,
        ),
        (
            QUALIFIED_BODY_SHA256,
            QUALIFIED_SOURCE_FRAGMENT,
            LEGACY_SOURCE_FRAGMENT,
        ),
    }
    if (
        expected_body_sha256,
        source_fragment,
        replacement_fragment,
    ) not in expected_pairs:
        raise ValueError("unsupported stocktake observation body replacement")

    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0050_replace$
DECLARE
    function_oid oid;
    function_source text;
    function_definition text;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;

    function_oid := pg_catalog.to_regprocedure('{FUNCTION_SIGNATURE}');
    SELECT function_row.prosrc
      INTO function_source
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(
               pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')
               ),
               'hex'
           ) = '{expected_body_sha256}';
    IF function_source IS NULL OR (
        pg_catalog.length(function_source)
        - pg_catalog.length(
            pg_catalog.replace(
                function_source,
                $rsc_0050_source${source_fragment}$rsc_0050_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0050_source${source_fragment}$rsc_0050_source$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0050_replacement${replacement_fragment}$rsc_0050_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: source mismatch';
    END IF;

    function_definition := pg_catalog.pg_get_functiondef(function_oid);
    IF (
        pg_catalog.length(function_definition)
        - pg_catalog.length(
            pg_catalog.replace(
                function_definition,
                $rsc_0050_source${source_fragment}$rsc_0050_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0050_source${source_fragment}$rsc_0050_source$
    ) <> 1 OR pg_catalog.strpos(
        function_definition,
        $rsc_0050_replacement${replacement_fragment}$rsc_0050_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: definition mismatch';
    END IF;

    EXECUTE pg_catalog.replace(
        function_definition,
        $rsc_0050_source${source_fragment}$rsc_0050_source$,
        $rsc_0050_replacement${replacement_fragment}$rsc_0050_replacement$
    );
END
$rsc_0050_replace$
"""
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
