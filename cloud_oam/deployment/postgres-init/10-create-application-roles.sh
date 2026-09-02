#!/usr/bin/env sh
set -eu

# The official PostgreSQL image sources this file only while initializing a
# brand-new data directory.  Existing volumes must follow the reviewed manual
# role-migration runbook; restarting a container never rewrites live ACLs.
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${OAM_DB_MIGRATOR_PASSWORD:?OAM_DB_MIGRATOR_PASSWORD is required}"
: "${OAM_DB_API_PASSWORD:?OAM_DB_API_PASSWORD is required}"
: "${OAM_DB_BACKUP_PASSWORD:?OAM_DB_BACKUP_PASSWORD is required}"
: "${OAM_DB_PROJECTOR_PASSWORD:?OAM_DB_PROJECTOR_PASSWORD is required}"

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=migrator_password="$OAM_DB_MIGRATOR_PASSWORD" \
  --set=api_password="$OAM_DB_API_PASSWORD" \
  --set=backup_password="$OAM_DB_BACKUP_PASSWORD" \
  --set=projector_password="$OAM_DB_PROJECTOR_PASSWORD" <<'SQL'
SELECT format(
    'CREATE ROLE star_oam_migrator LOGIN PASSWORD %L',
    :'migrator_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_migrator'
) \gexec
SELECT format(
    'CREATE ROLE star_oam_api LOGIN PASSWORD %L',
    :'api_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_api'
) \gexec
SELECT format(
    'CREATE ROLE star_oam_backup LOGIN PASSWORD %L',
    :'backup_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_backup'
) \gexec
SELECT format(
    'CREATE ROLE star_oam_projector LOGIN PASSWORD %L',
    :'projector_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_projector'
) \gexec

SELECT format(
    'ALTER ROLE star_oam_migrator WITH LOGIN PASSWORD %L '
    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'migrator_password'
) \gexec
SELECT format(
    'ALTER ROLE star_oam_api WITH LOGIN PASSWORD %L '
    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'api_password'
) \gexec
SELECT format(
    'ALTER ROLE star_oam_backup WITH LOGIN PASSWORD %L '
    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'backup_password'
) \gexec
SELECT format(
    'ALTER ROLE star_oam_projector WITH LOGIN PASSWORD %L '
    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'projector_password'
) \gexec

SELECT format(
    'REVOKE ALL ON DATABASE %I FROM PUBLIC',
    current_database()
) \gexec
-- The application principals are single-database identities.  A PostgreSQL
-- cluster grants CONNECT/TEMPORARY to PUBLIC by default on every database;
-- close that cluster-wide default before any application credential exists.
SELECT format(
    'REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC',
    database_row.datname
)
FROM pg_catalog.pg_database AS database_row
WHERE database_row.datallowconn
  AND database_row.datname <> pg_catalog.current_database()
ORDER BY database_row.datname
\gexec
SELECT format(
    'GRANT CONNECT ON DATABASE %I TO star_oam_migrator, star_oam_api, '
    'star_oam_backup, star_oam_projector',
    current_database()
) \gexec
SELECT format(
    'ALTER DATABASE %I OWNER TO star_oam_migrator',
    current_database()
) \gexec

ALTER SCHEMA public OWNER TO star_oam_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC, star_oam_api, star_oam_backup,
    star_oam_projector;
GRANT USAGE, CREATE ON SCHEMA public TO star_oam_migrator;
GRANT USAGE ON SCHEMA public TO star_oam_api, star_oam_backup,
    star_oam_projector;

ALTER ROLE star_oam_migrator SET search_path = public;
ALTER ROLE star_oam_api SET search_path = public;
ALTER ROLE star_oam_backup SET search_path = public;
ALTER ROLE star_oam_projector SET search_path = public;

ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO star_oam_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO star_oam_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
SQL
