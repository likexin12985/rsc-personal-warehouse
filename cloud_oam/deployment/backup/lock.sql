\set ON_ERROR_STOP on
\set QUIET on
\o /dev/null
BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY;
\ir role.sql
\ir inventory.sql
\setenv RSC_BACKUP_LOCK_HASH :inventory_hash
SELECT format('LOCK TABLE ONLY %I.%I IN ACCESS SHARE MODE NOWAIT',n.nspname,c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p')
ORDER BY n.nspname,c.relname,c.oid
\gexec
\! sh "$RSC_BACKUP_LIB/snapshot.sh"
\if :SHELL_ERROR
DO $$BEGIN RAISE EXCEPTION 'RSC_BACKUP_SNAPSHOT_CHILD_FAILED'; END$$;
\endif
-- Recheck current privileges after the child exits, while relation locks remain.
-- This READ COMMITTED statement catches persistent role/ACL changes that need
-- no conflicting relation lock. It is not a lock on cluster-wide role DDL.
\ir role.sql
ROLLBACK;
