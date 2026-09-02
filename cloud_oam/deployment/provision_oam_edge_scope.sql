\set ON_ERROR_STOP on

-- Add exactly one reviewed edge-ingress scope after Alembic 0044 and the
-- read-only `starcharge_oam` source registration are present.  This script
-- never creates, updates, disables, or broadens an existing binding.
--
-- Example:
--   psql ... \
--     -v source_instance=authorized-admin-mac \
--     -v company_id=exact-nio-company-id \
--     -v org_code=exact-nio-org-code \
--     -v scope_key=warehouse:WH-001 \
--     -v entity_type=inventory \
--     -f deployment/provision_oam_edge_scope.sql

\if :{?source_instance}
\else
\echo 'source_instance is required; refusing to guess'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif
\if :{?company_id}
\else
\echo 'company_id is required; refusing to guess'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif
\if :{?org_code}
\else
\echo 'org_code is required; refusing to guess'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif
\if :{?scope_key}
\else
\echo 'scope_key is required; refusing to guess'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif
\if :{?entity_type}
\else
\echo 'entity_type is required; refusing to guess'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

BEGIN;

DO $$
BEGIN
    IF current_user <> 'star_oam_migrator'
       OR session_user <> 'star_oam_migrator' THEN
        RAISE EXCEPTION
            'OAM edge scope provisioning requires a direct star_oam_migrator session';
    END IF;

    IF pg_catalog.to_regclass(
        'public.oam_sync_scope_bindings'
    ) IS NULL OR pg_catalog.to_regprocedure(
        'public.rsc_oam_formal_scope_key_0044(text,text)'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Alembic 0044 OAM scope boundary is not installed';
    END IF;
END
$$;

-- psql variables are not interpolated inside dollar-quoted PL/pgSQL.  Keep
-- the reviewed coordinates in a private transaction-local relation so every
-- validation, INSERT, and reread uses the same exact values.
CREATE TEMPORARY TABLE oam_edge_scope_input (
    source_instance text NOT NULL,
    company_id text NOT NULL,
    org_code text NOT NULL,
    scope_key text NOT NULL,
    entity_type text NOT NULL
) ON COMMIT DROP;

INSERT INTO pg_temp.oam_edge_scope_input (
    source_instance,
    company_id,
    org_code,
    scope_key,
    entity_type
)
VALUES (
    :'source_instance'::text,
    :'company_id'::text,
    :'org_code'::text,
    :'scope_key'::text,
    :'entity_type'::text
);

DO $$
DECLARE
    scope_input pg_temp.oam_edge_scope_input%ROWTYPE;
BEGIN
    SELECT *
      INTO STRICT scope_input
      FROM pg_temp.oam_edge_scope_input;

    IF scope_input.source_instance <> pg_catalog.btrim(scope_input.source_instance)
       OR scope_input.company_id <> pg_catalog.btrim(scope_input.company_id)
       OR scope_input.org_code <> pg_catalog.btrim(scope_input.org_code)
       OR scope_input.scope_key <> pg_catalog.btrim(scope_input.scope_key)
       OR scope_input.entity_type <> pg_catalog.btrim(scope_input.entity_type)
       OR pg_catalog.length(scope_input.source_instance) NOT BETWEEN 1 AND 128
       OR pg_catalog.length(scope_input.company_id) NOT BETWEEN 1 AND 80
       OR pg_catalog.length(scope_input.org_code) NOT BETWEEN 1 AND 80
       OR pg_catalog.length(scope_input.scope_key) NOT BETWEEN 1 AND 160
       OR pg_catalog.length(scope_input.entity_type) NOT BETWEEN 1 AND 64 THEN
        RAISE EXCEPTION
            'OAM edge scope coordinates are empty, oversized, or non-canonical';
    END IF;

    IF scope_input.source_instance ~ '[[:cntrl:]]'
       OR scope_input.company_id ~ '[[:cntrl:]]'
       OR scope_input.org_code ~ '[[:cntrl:]]'
       OR scope_input.scope_key ~ '[[:cntrl:]]'
       OR scope_input.entity_type ~ '[[:cntrl:]]'
       OR scope_input.source_instance ~ '[*%?]'
       OR scope_input.company_id ~ '[*%?]'
       OR scope_input.org_code ~ '[*%?]'
       OR scope_input.scope_key ~ '[*%?]'
       OR scope_input.entity_type ~ '[*%?]' THEN
        RAISE EXCEPTION
            'OAM edge scope coordinates contain a control or wildcard character';
    END IF;

    IF NOT (
        (
            scope_input.entity_type = 'work_order'
            AND scope_input.scope_key ~
                '^work-orders:recent-([1-9]|[1-9][0-9]|[12][0-9][0-9]|3[0-5][0-9]|36[0-5])d$'
        )
        OR (
            scope_input.entity_type IN (
                'employee',
                'material_application',
                'material_application_line'
            )
            AND scope_input.scope_key = 'all'
        )
        OR (
            scope_input.entity_type IN ('warehouse', 'inventory')
            AND (
                scope_input.scope_key = 'all'
                OR scope_input.scope_key ~
                    '^warehouse:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
            )
        )
    ) THEN
        RAISE EXCEPTION
            'entity_type and scope_key are not an approved exact OAM edge scope';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM public.source_systems AS source
         WHERE source.code = 'starcharge_oam'
           AND source.mode = 'read_only'
           AND source.enabled
    ) THEN
        RAISE EXCEPTION
            'enabled read-only starcharge_oam source registration is required';
    END IF;
END
$$;

-- A length-delimited JSON array makes the deterministic UUID unambiguous even
-- when reviewed identifiers contain punctuation.  The exact-key conflict path
-- is deliberately DO NOTHING; the following reread rejects every other drift.
INSERT INTO public.oam_sync_scope_bindings (
    id,
    principal_name,
    capability,
    source_system,
    source_instance,
    scope_key,
    company_id,
    org_code,
    entity_type,
    enabled,
    created_at,
    updated_at
)
SELECT
    pg_catalog.md5(
        pg_catalog.jsonb_build_array(
            'rsc:oam-edge-scope-binding:v1',
            'edge_inbox',
            'edge_ingress',
            'starcharge_oam',
            scope_input.source_instance,
            scope_input.scope_key,
            scope_input.company_id,
            scope_input.org_code,
            scope_input.entity_type
        )::text
    )::uuid,
    'edge_inbox',
    'edge_ingress',
    'starcharge_oam',
    scope_input.source_instance,
    scope_input.scope_key,
    scope_input.company_id,
    scope_input.org_code,
    scope_input.entity_type,
    TRUE,
    pg_catalog.clock_timestamp(),
    pg_catalog.clock_timestamp()
FROM pg_temp.oam_edge_scope_input AS scope_input
ON CONFLICT (
    principal_name,
    capability,
    source_system,
    source_instance,
    scope_key,
    entity_type
) DO NOTHING;

DO $$
DECLARE
    scope_input pg_temp.oam_edge_scope_input%ROWTYPE;
    expected_id uuid;
    key_count bigint;
    exact_count bigint;
BEGIN
    SELECT *
      INTO STRICT scope_input
      FROM pg_temp.oam_edge_scope_input;

    expected_id := pg_catalog.md5(
        pg_catalog.jsonb_build_array(
            'rsc:oam-edge-scope-binding:v1',
            'edge_inbox',
            'edge_ingress',
            'starcharge_oam',
            scope_input.source_instance,
            scope_input.scope_key,
            scope_input.company_id,
            scope_input.org_code,
            scope_input.entity_type
        )::text
    )::uuid;

    SELECT
        pg_catalog.count(*),
        pg_catalog.count(*) FILTER (
            WHERE binding.id = expected_id
              AND binding.company_id = scope_input.company_id
              AND binding.org_code = scope_input.org_code
              AND binding.enabled
              AND binding.formal_scope_key =
                  public.rsc_oam_formal_scope_key_0044(
                      scope_input.source_instance,
                      scope_input.scope_key
                  )
        )
      INTO key_count, exact_count
      FROM public.oam_sync_scope_bindings AS binding
     WHERE binding.principal_name = 'edge_inbox'
       AND binding.capability = 'edge_ingress'
       AND binding.source_system = 'starcharge_oam'
       AND binding.source_instance = scope_input.source_instance
       AND binding.scope_key = scope_input.scope_key
       AND binding.entity_type = scope_input.entity_type;

    IF key_count <> 1 OR exact_count <> 1 THEN
        RAISE EXCEPTION
            'existing OAM edge scope binding differs; no automatic overwrite performed';
    END IF;
END
$$;

-- Transaction-local reread evidence.  No runtime secret is stored or emitted.
SELECT
    binding.principal_name,
    binding.capability,
    binding.source_system,
    binding.source_instance,
    binding.scope_key,
    binding.company_id,
    binding.org_code,
    binding.entity_type,
    binding.enabled,
    binding.formal_scope_key
FROM public.oam_sync_scope_bindings AS binding
JOIN pg_temp.oam_edge_scope_input AS scope_input
  ON binding.principal_name = 'edge_inbox'
 AND binding.capability = 'edge_ingress'
 AND binding.source_system = 'starcharge_oam'
 AND binding.source_instance = scope_input.source_instance
 AND binding.scope_key = scope_input.scope_key
 AND binding.company_id = scope_input.company_id
 AND binding.org_code = scope_input.org_code
 AND binding.entity_type = scope_input.entity_type
 AND binding.enabled;

COMMIT;
