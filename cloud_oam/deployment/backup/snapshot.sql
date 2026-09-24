\set ON_ERROR_STOP on
\set QUIET on
\o /dev/null
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
\ir inventory.sql
SELECT :'inventory_hash'=:'locked_inventory' AS inventory_unchanged
\gset
\if :inventory_unchanged
\else
DO $$BEGIN RAISE EXCEPTION 'RSC_BACKUP_RELATION_INVENTORY_CHANGED'; END$$;
\endif
\ir role.sql
\ir policies.sql
SELECT pg_export_snapshot() AS exported_snapshot
\gset
\setenv RSC_BACKUP_SNAPSHOT :exported_snapshot
\! sh "$RSC_BACKUP_LIB/dump.sh"
\if :SHELL_ERROR
DO $$BEGIN RAISE EXCEPTION 'RSC_BACKUP_DUMP_CHILD_FAILED'; END$$;
\endif
ROLLBACK;
