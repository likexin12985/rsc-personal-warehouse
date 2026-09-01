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
    SELECT string_agg(required_table, ', ' ORDER BY required_table)
    INTO missing_tables
    FROM unnest(ARRAY[
        'external_sync_snapshots',
        'external_sync_snapshot_batches',
        'external_sync_snapshot_records',
        'external_sync_current_records',
        'audit_logs'
    ]) AS required(required_table)
    WHERE to_regclass(format('public.%I', required_table)) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'Alembic schema is incomplete; missing tables: %', missing_tables;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO :"edge_role";

GRANT SELECT, INSERT, UPDATE
ON TABLE
    external_sync_snapshots,
    external_sync_snapshot_batches,
    external_sync_snapshot_records
TO :"edge_role";

GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLE external_sync_current_records
TO :"edge_role";

-- The receiver appends transport evidence but cannot read other audit rows.
GRANT INSERT ON TABLE audit_logs TO :"edge_role";

COMMIT;
