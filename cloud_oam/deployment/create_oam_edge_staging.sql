\set ON_ERROR_STOP on

-- Compatibility filename retained for existing deployment automation.
-- This script deliberately creates no table: all DDL is owned by Alembic.
-- Run only after `alembic upgrade head`, for example:
--   psql ... -v edge_role=edge_inbox -f deployment/create_oam_edge_staging.sql

\if :{?edge_role}
\else
\echo 'edge_role is required; refusing to guess a database principal'
\quit 3
\endif

BEGIN;

DO $$
DECLARE
    missing_tables text;
BEGIN
    IF current_user <> 'star_oam_migrator' THEN
        RAISE EXCEPTION
            'edge staging ACL provisioning requires star_oam_migrator';
    END IF;

    SELECT pg_catalog.string_agg(
        required_table, ', ' ORDER BY required_table
    )
    INTO missing_tables
    FROM pg_catalog.unnest(ARRAY[
        'external_sync_snapshots',
        'external_sync_snapshot_batches',
        'external_sync_snapshot_records',
        'external_sync_current_records',
        'audit_logs'
    ]) AS required(required_table)
    WHERE pg_catalog.to_regclass(
        pg_catalog.format('public.%I', required_table)
    ) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'Alembic schema is incomplete; missing tables: %', missing_tables;
    END IF;
END
$$;

-- Keep the credential independently revocable.  A role membership could
-- silently reintroduce formal-table, function, sequence, or DDL privileges.
SELECT COALESCE(
    (
        SELECT
            edge_role.rolname NOT IN (
                'star_oam_migrator',
                'star_oam_api',
                'star_oam_backup',
                'star_oam_projector',
                'star_oam_edge'
            )
            AND edge_role.rolcanlogin
            AND NOT edge_role.rolsuper
            AND NOT edge_role.rolcreatedb
            AND NOT edge_role.rolcreaterole
            AND NOT edge_role.rolreplication
            AND NOT edge_role.rolbypassrls
            AND NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_auth_members AS membership
                WHERE membership.member = edge_role.oid
            )
            AND NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_auth_members AS membership
                WHERE membership.roleid = edge_role.oid
            )
        FROM pg_catalog.pg_roles AS edge_role
        WHERE edge_role.rolname = :'edge_role'
    ),
    FALSE
) AS edge_role_safe
\gset

\if :edge_role_safe
\else
\echo 'edge_role is missing, privileged, inherited, or shared; refusing ACL provisioning'
\quit 3
\endif

-- Start from a closed direct ACL.  Table-level REVOKE does not remove a
-- historical column grant, so emit exact revokes for any such residue first.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM :"edge_role";
SELECT pg_catalog.format(
    'REVOKE %s (%I) ON TABLE %I.%I FROM %I',
    column_acl.privilege_type,
    column_acl.column_name,
    column_acl.table_schema,
    column_acl.table_name,
    :'edge_role'
)
FROM information_schema.column_privileges AS column_acl
WHERE column_acl.grantee = :'edge_role'
  AND column_acl.table_schema = 'public'
ORDER BY
    column_acl.table_name,
    column_acl.column_name,
    column_acl.privilege_type
\gexec

REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM :"edge_role";
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM :"edge_role";
-- Large objects and parameter grants live outside the schema/table matrix.
-- Remove any direct historical residue before applying the reviewed grants.
SELECT pg_catalog.format(
    'REVOKE ALL PRIVILEGES ON LARGE OBJECT %s FROM %I',
    large_object.oid,
    :'edge_role'
)
FROM pg_catalog.pg_largeobject_metadata AS large_object
CROSS JOIN LATERAL pg_catalog.aclexplode(large_object.lomacl) AS acl
JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = acl.grantee
WHERE grantee.rolname = :'edge_role'
GROUP BY large_object.oid
ORDER BY large_object.oid
\gexec
SELECT pg_catalog.format(
    'REVOKE ALL PRIVILEGES ON PARAMETER %I FROM %I',
    parameter_row.parname,
    :'edge_role'
)
FROM pg_catalog.pg_parameter_acl AS parameter_row
CROSS JOIN LATERAL pg_catalog.aclexplode(parameter_row.paracl) AS acl
JOIN pg_catalog.pg_roles AS grantee ON grantee.oid = acl.grantee
WHERE grantee.rolname = :'edge_role'
GROUP BY parameter_row.parname
ORDER BY parameter_row.parname
\gexec
REVOKE ALL ON SCHEMA public FROM :"edge_role";
SELECT pg_catalog.format(
    'REVOKE ALL ON DATABASE %I FROM %I',
    pg_catalog.current_database(),
    :'edge_role'
)
\gexec
SELECT pg_catalog.format(
    'GRANT CONNECT ON DATABASE %I TO %I',
    pg_catalog.current_database(),
    :'edge_role'
)
\gexec

GRANT USAGE ON SCHEMA public TO :"edge_role";

-- Header rows are immutable except for the reviewed receive/finalize state.
GRANT SELECT, INSERT
ON TABLE public.external_sync_snapshots
TO :"edge_role";
GRANT UPDATE (
    status,
    manifest_json,
    manifest_sha256,
    completed_at
)
ON TABLE public.external_sync_snapshots
TO :"edge_role";

-- Received batches and records are evidence: append and reread only.
GRANT SELECT, INSERT
ON TABLE
    public.external_sync_snapshot_batches,
    public.external_sync_snapshot_records
TO :"edge_role";

-- The receiver may reconcile only the mutable current-state payload fields.
GRANT SELECT, INSERT, DELETE
ON TABLE public.external_sync_current_records
TO :"edge_role";
GRANT UPDATE (
    source_updated_at,
    payload_json,
    payload_sha256,
    last_snapshot_id,
    updated_at
)
ON TABLE public.external_sync_current_records
TO :"edge_role";

-- The receiver appends transport evidence but cannot read other audit rows.
GRANT INSERT ON TABLE public.audit_logs TO :"edge_role";

COMMIT;
