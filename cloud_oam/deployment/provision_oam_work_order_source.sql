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
\quit 3
\endif
\if :{?company_id}
\else
\echo 'company_id is required; refusing to guess'
\quit 3
\endif
\if :{?org_code}
\else
\echo 'org_code is required; refusing to guess'
\quit 3
\endif
\if :{?scope_key}
\else
\echo 'scope_key is required; refusing to guess'
\quit 3
\endif

BEGIN;

DO $$
BEGIN
    IF current_user <> 'star_oam_migrator' THEN
        RAISE EXCEPTION
            'OAM source provisioning requires star_oam_migrator';
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
  AND coordinates.scope_key !~ '[[:cntrl:]]';

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

COMMIT;
