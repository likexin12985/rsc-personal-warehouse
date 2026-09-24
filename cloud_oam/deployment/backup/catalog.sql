\set ON_ERROR_STOP on
\set QUIET on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET TRANSACTION SNAPSHOT :'exported_snapshot';
SET LOCAL TIME ZONE 'UTC';
SELECT jsonb_build_object(
 'schema','cloud_oam.formal_file_snapshot_candidate.v1',
 'snapshot_id',:'exported_snapshot',
 'database',current_database(),
 'role',current_user,
 'isolation',current_setting('transaction_isolation'),
 'read_only',current_setting('transaction_read_only'),
 'file_count',(SELECT count(*) FROM public.files),
 'migration_head',(SELECT version_num FROM public.alembic_version)
)::text;
SELECT to_jsonb(f)::text FROM public.files f ORDER BY f.id;
ROLLBACK;
