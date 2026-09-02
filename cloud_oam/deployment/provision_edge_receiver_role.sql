\set ON_ERROR_STOP on

-- Bootstrap-only role provisioning.  The password is read from the process
-- environment and is never accepted as a psql command-line variable.
-- Usage:
--   OAM_DB_EDGE_RECEIVER_PASSWORD='...' psql ... \
--     -v edge_role=edge_inbox \
--     -f deployment/provision_edge_receiver_role.sql
\if :{?edge_role}
\else
\echo 'edge_role is required; refusing to guess a database principal'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

\getenv edge_password OAM_DB_EDGE_RECEIVER_PASSWORD
\if :{?edge_password}
\else
\echo 'OAM_DB_EDGE_RECEIVER_PASSWORD is required in the process environment'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

SELECT :'edge_role' = 'edge_inbox' AS edge_role_is_exact
\gset
\if :edge_role_is_exact
\else
\echo 'edge_role must be the independently revocable edge_inbox principal'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

SELECT
    pg_catalog.length(:'edge_password') >= 32
    AND :'edge_password' !~ '[[:cntrl:]]'
    AND pg_catalog.lower(:'edge_password') NOT IN (
        'password',
        'changeme',
        'replace-me',
        'replace-with-strong-password'
    ) AS edge_password_safe
\gset
\if :edge_password_safe
\else
\echo 'edge receiver password must be at least 32 non-control characters and not a placeholder'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

SELECT COALESCE(
    (
        SELECT role_row.rolsuper
          FROM pg_catalog.pg_roles AS role_row
         WHERE role_row.rolname = current_user
    ),
    FALSE
) AS bootstrap_is_superuser
\gset
\if :bootstrap_is_superuser
\else
\echo 'edge receiver role provisioning requires a bootstrap superuser'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

BEGIN;

SELECT pg_catalog.format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE '
    'NOREPLICATION NOBYPASSRLS INHERIT',
    :'edge_role',
    :'edge_password'
)
WHERE NOT EXISTS (
    SELECT 1
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = :'edge_role'
)
\gexec

SELECT COALESCE(
    (
        SELECT
            role_row.rolcanlogin
            AND NOT role_row.rolsuper
            AND NOT role_row.rolcreatedb
            AND NOT role_row.rolcreaterole
            AND NOT role_row.rolreplication
            AND NOT role_row.rolbypassrls
            AND NOT EXISTS (
                SELECT 1
                  FROM pg_catalog.pg_auth_members AS membership
                 WHERE membership.member = role_row.oid
                    OR membership.roleid = role_row.oid
            )
          FROM pg_catalog.pg_roles AS role_row
         WHERE role_row.rolname = :'edge_role'
    ),
    FALSE
) AS edge_role_safe
\gset
\if :edge_role_safe
\else
ROLLBACK;
\echo 'existing edge_inbox role is privileged or participates in membership; no password was changed'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

SELECT pg_catalog.format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB '
    'NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT',
    :'edge_role',
    :'edge_password'
)
\gexec
SELECT pg_catalog.format(
    'ALTER ROLE %I SET search_path = public',
    :'edge_role'
)
\gexec

-- Direct database grants are also reset.  PUBLIC and pg_hba boundaries are
-- separate deployment prerequisites verified by the release gate.
SELECT pg_catalog.format(
    'REVOKE ALL ON DATABASE %I FROM %I',
    database_row.datname,
    :'edge_role'
)
FROM pg_catalog.pg_database AS database_row
WHERE database_row.datallowconn
ORDER BY database_row.datname
\gexec

-- Keep the cluster boundary check in the same transaction as CREATE/ALTER.
-- At this bootstrap stage edge_inbox intentionally has no direct CONNECT on
-- the current database; create_oam_edge_staging.sql grants that only after it
-- has established the complete table/schema ACL boundary.  Effective CONNECT
-- to any *other* connectable database is nevertheless a hard failure.  Role
-- DDL and password changes are transactional, so ROLLBACK leaves neither a
-- newly login-capable role nor a changed password behind.
SELECT NOT EXISTS (
    SELECT 1
      FROM pg_catalog.pg_database AS database_row
     WHERE database_row.datallowconn
       AND database_row.datname <> pg_catalog.current_database()
       AND pg_catalog.has_database_privilege(
           :'edge_role', database_row.oid, 'CONNECT'
       )
) AS edge_cross_database_boundary_closed
\gset
\if :edge_cross_database_boundary_closed
\else
ROLLBACK;
\unset edge_password
\echo 'PUBLIC or inherited CONNECT still exposes another database; use a dedicated cluster or close pg_hba/database ACLs'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

COMMIT;

\unset edge_password
\echo 'edge receiver role provisioned without command-line password transport'
