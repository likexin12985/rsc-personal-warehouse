\set ON_ERROR_STOP on
\pset format unaligned
\pset fieldsep '|'

-- Usage:
--   psql ... -v edge_role=edge_inbox -f deployment/verify_oam_edge_staging.sql
\if :{?edge_role}
\else
\echo 'edge_role is required; refusing to guess a database principal'
\quit 3
\endif

SELECT
    c.relname AS table_name,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
WHERE c.relname IN (
    'external_sync_snapshots',
    'external_sync_snapshot_batches',
    'external_sync_snapshot_records',
    'external_sync_current_records',
    'audit_logs'
)
ORDER BY c.relname;

SELECT
    has_table_privilege(
        :'edge_role',
        'public.external_sync_snapshots',
        'SELECT,INSERT,UPDATE'
    ) AS snapshot_header_access,
    has_table_privilege(
        :'edge_role',
        'public.external_sync_snapshot_batches',
        'SELECT,INSERT,UPDATE'
    ) AS snapshot_batch_access,
    has_table_privilege(
        :'edge_role',
        'public.external_sync_snapshot_records',
        'SELECT,INSERT,UPDATE'
    ) AS snapshot_record_access,
    has_table_privilege(
        :'edge_role',
        'public.external_sync_current_records',
        'SELECT,INSERT,UPDATE,DELETE'
    ) AS current_projection_access,
    has_table_privilege(
        :'edge_role',
        'public.audit_logs',
        'INSERT'
    ) AS audit_append_access,
    has_table_privilege(
        :'edge_role',
        'public.users',
        'SELECT,INSERT,UPDATE,DELETE'
    ) AS forbidden_user_access,
    has_table_privilege(
        :'edge_role',
        'public.inventory_balances',
        'SELECT,INSERT,UPDATE,DELETE'
    ) AS forbidden_inventory_access;
