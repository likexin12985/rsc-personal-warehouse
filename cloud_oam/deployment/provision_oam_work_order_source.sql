\set ON_ERROR_STOP on

-- Run with star_oam_migrator only after the exact OAM scope has been reviewed:
-- psql ... \
--   -v edge_source_instance=authorized-admin-mac \
--   -v company_id=exact-nio-company-id \
--   -v org_code=exact-nio-org-code \
--   -v scope_key=work-orders:recent-30d \
--   -f deployment/provision_oam_work_order_source.sql

\if :{?edge_source_instance}
\else
\echo 'edge_source_instance is required; refusing to guess'
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

BEGIN;

DO $$
BEGIN
    IF current_user <> 'star_oam_migrator'
       OR session_user <> 'star_oam_migrator' THEN
        RAISE EXCEPTION
            'OAM source provisioning requires a direct star_oam_migrator session';
    END IF;
END
$$;

-- psql deliberately does not interpolate variables inside dollar-quoted
-- PL/pgSQL bodies.  Materialize the reviewed coordinates once in a private
-- transaction-local relation, then let the verification block read them as
-- ordinary SQL data instead of accidentally comparing literal :variables.
CREATE TEMPORARY TABLE oam_work_order_source_input (
    edge_source_instance text NOT NULL,
    company_id text NOT NULL,
    org_code text NOT NULL,
    scope_key text NOT NULL,
    configuration_jsonb jsonb NOT NULL
) ON COMMIT DROP;

INSERT INTO pg_temp.oam_work_order_source_input (
    edge_source_instance,
    company_id,
    org_code,
    scope_key,
    configuration_jsonb
)
SELECT
    coordinates.edge_source_instance,
    coordinates.company_id,
    coordinates.org_code,
    coordinates.scope_key,
    jsonb_build_object(
        'projection_schema', 'rsc.oam_work_order_projection.v1',
        'edge_source_instance', coordinates.edge_source_instance,
        'work_order_company_id', coordinates.company_id,
        'work_order_org_code', coordinates.org_code,
        'work_order_scope_key', coordinates.scope_key
    )
FROM (
    VALUES (
        :'edge_source_instance'::text,
        :'company_id'::text,
        :'org_code'::text,
        :'scope_key'::text
    )
) AS coordinates(
    edge_source_instance,
    company_id,
    org_code,
    scope_key
)
WHERE coordinates.edge_source_instance = btrim(coordinates.edge_source_instance)
  AND coordinates.company_id = btrim(coordinates.company_id)
  AND coordinates.org_code = btrim(coordinates.org_code)
  AND coordinates.scope_key = btrim(coordinates.scope_key)
  AND length(coordinates.edge_source_instance) BETWEEN 1 AND 128
  AND length(coordinates.company_id) BETWEEN 1 AND 80
  AND length(coordinates.org_code) BETWEEN 1 AND 80
  AND length(coordinates.scope_key) BETWEEN 1 AND 160
  AND coordinates.edge_source_instance !~ '[[:cntrl:]]'
  AND coordinates.company_id !~ '[[:cntrl:]]'
  AND coordinates.org_code !~ '[[:cntrl:]]'
  AND coordinates.scope_key !~ '[[:cntrl:]]'
  AND coordinates.edge_source_instance !~ '[*%?]'
  AND coordinates.company_id !~ '[*%?]'
  AND coordinates.org_code !~ '[*%?]'
  AND coordinates.scope_key !~ '[*%?]';

DO $$
BEGIN
    IF (SELECT count(*) FROM pg_temp.oam_work_order_source_input) <> 1 THEN
        RAISE EXCEPTION
            'OAM source provisioning coordinates are empty, malformed, or not canonical';
    END IF;
END
$$;

INSERT INTO public.source_systems (
    id,
    code,
    name,
    mode,
    enabled,
    configuration_jsonb,
    created_at,
    updated_at
)
SELECT
    md5('rsc:source:starcharge_oam')::uuid,
    'starcharge_oam',
    'StarCharge OAM',
    'read_only',
    TRUE,
    source_input.configuration_jsonb,
    clock_timestamp(),
    clock_timestamp()
FROM pg_temp.oam_work_order_source_input AS source_input
ON CONFLICT (code) DO NOTHING;

DO $$
DECLARE
    source_count bigint;
    expected_configuration jsonb;
BEGIN
    SELECT source_input.configuration_jsonb
      INTO STRICT expected_configuration
      FROM pg_temp.oam_work_order_source_input AS source_input;

    SELECT count(*)
      INTO source_count
      FROM public.source_systems
     WHERE code = 'starcharge_oam'
       AND mode = 'read_only'
       AND enabled IS TRUE
       AND configuration_jsonb = expected_configuration;
    IF source_count <> 1 THEN
        RAISE EXCEPTION
            'existing starcharge_oam source differs; no automatic overwrite performed';
    END IF;
END
$$;

-- 0044 keeps authorization coordinates in a migrator-owned table.  Runtime
-- roles can neither read nor mutate these rows; provisioning inserts only the
-- four reviewed capability/entity combinations and never overwrites drift.
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
        'rsc:oam-sync-scope-binding:'
        || binding.principal_name || ':'
        || binding.capability || ':'
        || binding.entity_type || ':'
        || source_input.edge_source_instance || ':'
        || source_input.scope_key
    )::uuid,
    binding.principal_name,
    binding.capability,
    'starcharge_oam',
    source_input.edge_source_instance,
    source_input.scope_key,
    source_input.company_id,
    source_input.org_code,
    binding.entity_type,
    TRUE,
    pg_catalog.clock_timestamp(),
    pg_catalog.clock_timestamp()
FROM pg_temp.oam_work_order_source_input AS source_input
CROSS JOIN (
    VALUES
        ('edge_inbox'::text, 'edge_ingress'::text, 'work_order'::text),
        ('star_oam_projector', 'projector_read', 'employee'),
        ('star_oam_projector', 'projector_read', 'work_order'),
        ('star_oam_projector', 'projector_write', 'work_order')
) AS binding(principal_name, capability, entity_type)
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
    source_input pg_temp.oam_work_order_source_input%ROWTYPE;
    exact_binding_count bigint;
    projector_binding_count bigint;
BEGIN
    SELECT *
      INTO STRICT source_input
      FROM pg_temp.oam_work_order_source_input;

    SELECT pg_catalog.count(*)
      INTO exact_binding_count
      FROM public.oam_sync_scope_bindings AS binding
     WHERE binding.enabled
       AND binding.source_system = 'starcharge_oam'
       AND binding.source_instance = source_input.edge_source_instance
       AND binding.scope_key = source_input.scope_key
       AND binding.company_id = source_input.company_id
       AND binding.org_code = source_input.org_code
       AND (
           (binding.principal_name = 'edge_inbox'
            AND binding.capability = 'edge_ingress'
            AND binding.entity_type = 'work_order')
           OR
           (binding.principal_name = 'star_oam_projector'
            AND binding.capability = 'projector_read'
            AND binding.entity_type IN ('employee', 'work_order'))
           OR
           (binding.principal_name = 'star_oam_projector'
            AND binding.capability = 'projector_write'
            AND binding.entity_type = 'work_order')
       );

    SELECT pg_catalog.count(*)
      INTO projector_binding_count
      FROM public.oam_sync_scope_bindings AS binding
     WHERE binding.enabled
       AND binding.principal_name = 'star_oam_projector';

    IF exact_binding_count <> 4 OR projector_binding_count <> 3 THEN
        RAISE EXCEPTION
            'existing OAM sync scope bindings differ; no automatic overwrite performed';
    END IF;
END
$$;

COMMIT;
