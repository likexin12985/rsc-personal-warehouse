"""Fail-closed PostgreSQL identity and ACL proof for the edge receiver."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


EDGE_ROLE = "edge_inbox"
MIGRATION_ROLE = "star_oam_migrator"


class EdgeDatabaseBoundaryError(RuntimeError):
    """The receiver is not running with the exact staging-only privilege set."""


_BOUNDARY_SQL = text(
    """
WITH
expected_table_acl(
    table_name,
    can_select,
    can_insert,
    can_update,
    can_delete,
    can_truncate,
    can_references,
    can_trigger
) AS (
    VALUES
        ('external_sync_snapshots', TRUE, TRUE, FALSE, FALSE, FALSE, FALSE, FALSE),
        ('external_sync_snapshot_batches', TRUE, TRUE, FALSE, FALSE, FALSE, FALSE, FALSE),
        ('external_sync_snapshot_records', TRUE, TRUE, FALSE, FALSE, FALSE, FALSE, FALSE),
        ('external_sync_current_records', TRUE, TRUE, FALSE, TRUE, FALSE, FALSE, FALSE),
        ('audit_logs', FALSE, TRUE, FALSE, FALSE, FALSE, FALSE, FALSE)
),
expected_update_columns(table_name, column_name) AS (
    VALUES
        ('external_sync_snapshots', 'status'),
        ('external_sync_snapshots', 'manifest_json'),
        ('external_sync_snapshots', 'manifest_sha256'),
        ('external_sync_snapshots', 'completed_at'),
        ('external_sync_current_records', 'source_updated_at'),
        ('external_sync_current_records', 'payload_json'),
        ('external_sync_current_records', 'payload_sha256'),
        ('external_sync_current_records', 'last_snapshot_id'),
        ('external_sync_current_records', 'updated_at')
),
table_privileges(privilege_type) AS (
    VALUES
        ('SELECT'::text),
        ('INSERT'::text),
        ('UPDATE'::text),
        ('DELETE'::text),
        ('TRUNCATE'::text),
        ('REFERENCES'::text),
        ('TRIGGER'::text)
),
column_privileges(privilege_type) AS (
    VALUES
        ('SELECT'::text),
        ('INSERT'::text),
        ('UPDATE'::text),
        ('REFERENCES'::text)
),
public_tables AS (
    SELECT relation.oid, relation.relname, relation.relowner, relation.relacl
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS schema_row
      ON schema_row.oid = relation.relnamespace
    WHERE schema_row.nspname = 'public'
      AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
),
public_sequences AS (
    SELECT relation.oid, relation.relowner, relation.relacl
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS schema_row
      ON schema_row.oid = relation.relnamespace
    WHERE schema_row.nspname = 'public'
      AND relation.relkind = 'S'
),
non_system_schemas AS (
    SELECT schema_row.oid, schema_row.nspowner, schema_row.nspacl
    FROM pg_catalog.pg_namespace AS schema_row
    WHERE schema_row.nspname <> 'public'
      AND schema_row.nspname <> 'information_schema'
      AND schema_row.nspname !~ '^pg_'
),
role_ok AS (
    SELECT COALESCE((
        SELECT
            current_user = :edge_role
            AND session_user = :edge_role
            AND role_row.rolname = :edge_role
            AND role_row.rolname NOT IN (
                'star_oam_migrator',
                'star_oam_api',
                'star_oam_backup',
                'star_oam_projector',
                'star_oam_edge'
            )
            AND role_row.rolcanlogin
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
            AND NOT pg_catalog.has_parameter_privilege(
                current_user,
                'session_replication_role',
                'SET'
            )
            AND pg_catalog.current_setting('session_replication_role') = 'origin'
        FROM pg_catalog.pg_roles AS role_row
        WHERE role_row.rolname = current_user
    ), FALSE) AS passed
),
ddl_boundary_ok AS (
    SELECT COALESCE((
        SELECT
            pg_catalog.pg_get_userbyid(database_row.datdba) = :migration_role
            AND pg_catalog.pg_get_userbyid(schema_row.nspowner) = :migration_role
            AND pg_catalog.has_database_privilege(
                current_user, database_row.oid, 'CONNECT'
            )
            AND NOT pg_catalog.has_database_privilege(
                current_user, database_row.oid, 'CREATE'
            )
            AND NOT pg_catalog.has_database_privilege(
                current_user, database_row.oid, 'TEMPORARY'
            )
            AND pg_catalog.has_schema_privilege(
                current_user, schema_row.oid, 'USAGE'
            )
            AND NOT pg_catalog.has_schema_privilege(
                current_user, schema_row.oid, 'CREATE'
            )
            AND pg_catalog.current_schema() = 'public'
            AND pg_catalog.current_schemas(FALSE) = ARRAY['public']::name[]
        FROM pg_catalog.pg_database AS database_row
        CROSS JOIN pg_catalog.pg_namespace AS schema_row
        WHERE database_row.datname = pg_catalog.current_database()
          AND schema_row.nspname = 'public'
    ), FALSE) AS passed
),
relations_ok AS (
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM expected_table_acl AS expected
            WHERE NOT EXISTS (
                SELECT 1
                FROM public_tables AS relation
                WHERE relation.relname = expected.table_name
            )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM public_tables AS relation
            WHERE pg_catalog.pg_get_userbyid(relation.relowner) <> :migration_role
        )
        AND NOT EXISTS (
            SELECT 1
            FROM public_sequences AS sequence_row
            WHERE pg_catalog.pg_get_userbyid(sequence_row.relowner) <> :migration_role
        ) AS passed
),
table_acl_ok AS (
    SELECT NOT EXISTS (
        SELECT 1
        FROM public_tables AS relation
        CROSS JOIN table_privileges AS checked_privilege
        LEFT JOIN expected_table_acl AS expected
          ON expected.table_name = relation.relname
        WHERE pg_catalog.has_table_privilege(
                  current_user,
                  relation.oid,
                  checked_privilege.privilege_type
              ) IS DISTINCT FROM CASE checked_privilege.privilege_type
                    WHEN 'SELECT' THEN COALESCE(expected.can_select, FALSE)
                    WHEN 'INSERT' THEN COALESCE(expected.can_insert, FALSE)
                    WHEN 'UPDATE' THEN COALESCE(expected.can_update, FALSE)
                    WHEN 'DELETE' THEN COALESCE(expected.can_delete, FALSE)
                    WHEN 'TRUNCATE' THEN COALESCE(expected.can_truncate, FALSE)
                    WHEN 'REFERENCES' THEN COALESCE(expected.can_references, FALSE)
                    WHEN 'TRIGGER' THEN COALESCE(expected.can_trigger, FALSE)
                    ELSE FALSE
              END
    ) AS passed
),
column_acl_ok AS (
    SELECT NOT EXISTS (
        SELECT 1
        FROM public_tables AS relation
        JOIN pg_catalog.pg_attribute AS column_row
          ON column_row.attrelid = relation.oid
         AND column_row.attnum > 0
         AND NOT column_row.attisdropped
        CROSS JOIN column_privileges AS checked_privilege
        LEFT JOIN expected_table_acl AS expected
          ON expected.table_name = relation.relname
        WHERE pg_catalog.has_column_privilege(
                  current_user,
                  relation.oid,
                  column_row.attnum,
                  checked_privilege.privilege_type
              ) IS DISTINCT FROM (
                  CASE checked_privilege.privilege_type
                      WHEN 'SELECT' THEN COALESCE(expected.can_select, FALSE)
                      WHEN 'INSERT' THEN COALESCE(expected.can_insert, FALSE)
                      WHEN 'UPDATE' THEN COALESCE(expected.can_update, FALSE)
                      WHEN 'REFERENCES' THEN COALESCE(
                          expected.can_references, FALSE
                      )
                      ELSE FALSE
                  END
                  OR (
                      checked_privilege.privilege_type = 'UPDATE'
                      AND EXISTS (
                          SELECT 1
                          FROM expected_update_columns AS update_column
                          WHERE update_column.table_name = relation.relname
                            AND update_column.column_name = column_row.attname
                      )
                  )
              )
    ) AS passed
),
function_sequence_ok AS (
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS function_row
            JOIN pg_catalog.pg_namespace AS schema_row
              ON schema_row.oid = function_row.pronamespace
            WHERE schema_row.nspname = 'public'
              AND pg_catalog.has_function_privilege(
                  current_user, function_row.oid, 'EXECUTE'
              )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM public_sequences AS sequence_row
            WHERE pg_catalog.has_sequence_privilege(
                      current_user, sequence_row.oid, 'USAGE'
                  )
               OR pg_catalog.has_sequence_privilege(
                      current_user, sequence_row.oid, 'SELECT'
                  )
               OR pg_catalog.has_sequence_privilege(
                      current_user, sequence_row.oid, 'UPDATE'
                  )
        ) AS passed
),
cross_schema_ok AS (
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM non_system_schemas AS schema_row
            WHERE schema_row.nspowner = (
                      SELECT oid FROM pg_catalog.pg_roles
                      WHERE rolname = current_user
                  )
               OR pg_catalog.has_schema_privilege(
                      current_user, schema_row.oid, 'USAGE'
                  )
               OR pg_catalog.has_schema_privilege(
                      current_user, schema_row.oid, 'CREATE'
                  )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class AS relation
            JOIN non_system_schemas AS schema_row
              ON schema_row.oid = relation.relnamespace
            WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND (
                  relation.relowner = (
                      SELECT oid FROM pg_catalog.pg_roles
                      WHERE rolname = current_user
                  )
                  OR pg_catalog.has_table_privilege(
                      current_user, relation.oid, 'SELECT'
                  )
                  OR pg_catalog.has_any_column_privilege(
                      current_user, relation.oid, 'SELECT'
                  )
                  OR pg_catalog.has_table_privilege(
                      current_user, relation.oid, 'INSERT'
                  )
                  OR pg_catalog.has_any_column_privilege(
                      current_user, relation.oid, 'INSERT'
                  )
                  OR pg_catalog.has_table_privilege(
                      current_user, relation.oid, 'UPDATE'
                  )
                  OR pg_catalog.has_any_column_privilege(
                      current_user, relation.oid, 'UPDATE'
                  )
                  OR pg_catalog.has_table_privilege(
                      current_user, relation.oid, 'DELETE'
                  )
                  OR pg_catalog.has_table_privilege(
                      current_user, relation.oid, 'TRUNCATE'
                  )
                  OR pg_catalog.has_table_privilege(
                      current_user, relation.oid, 'REFERENCES'
                  )
                  OR pg_catalog.has_any_column_privilege(
                      current_user, relation.oid, 'REFERENCES'
                  )
                  OR pg_catalog.has_table_privilege(
                      current_user, relation.oid, 'TRIGGER'
                  )
              )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class AS sequence_row
            JOIN non_system_schemas AS schema_row
              ON schema_row.oid = sequence_row.relnamespace
            WHERE sequence_row.relkind = 'S'
              AND (
                  sequence_row.relowner = (
                      SELECT oid FROM pg_catalog.pg_roles
                      WHERE rolname = current_user
                  )
                  OR pg_catalog.has_sequence_privilege(
                      current_user, sequence_row.oid, 'USAGE'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      current_user, sequence_row.oid, 'SELECT'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      current_user, sequence_row.oid, 'UPDATE'
                  )
              )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS function_row
            JOIN non_system_schemas AS schema_row
              ON schema_row.oid = function_row.pronamespace
            WHERE function_row.proowner = (
                      SELECT oid FROM pg_catalog.pg_roles
                      WHERE rolname = current_user
                  )
               OR pg_catalog.has_function_privilege(
                      current_user, function_row.oid, 'EXECUTE'
                  )
        ) AS passed
),
public_acl_ok AS (
    SELECT
        NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_database AS database_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     database_row.datacl,
                     pg_catalog.acldefault('d', database_row.datdba)
                 )
             ) AS acl
             WHERE database_row.datname = pg_catalog.current_database()
               AND acl.grantee = 0
        )
        AND NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_namespace AS schema_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     schema_row.nspacl,
                     pg_catalog.acldefault('n', schema_row.nspowner)
                 )
             ) AS acl
             WHERE schema_row.nspname = 'public'
               AND acl.grantee = 0
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public_tables AS relation
             CROSS JOIN LATERAL
                  pg_catalog.aclexplode(relation.relacl) AS acl
             WHERE acl.grantee = 0
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public_tables AS relation
              JOIN pg_catalog.pg_attribute AS column_row
                ON column_row.attrelid = relation.oid
               AND column_row.attnum > 0
               AND NOT column_row.attisdropped
             CROSS JOIN LATERAL
                  pg_catalog.aclexplode(column_row.attacl) AS acl
             WHERE acl.grantee = 0
        )
        AND NOT EXISTS (
            SELECT 1
              FROM public_sequences AS sequence_row
             CROSS JOIN LATERAL
                  pg_catalog.aclexplode(sequence_row.relacl) AS acl
             WHERE acl.grantee = 0
        )
        AND NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
              JOIN pg_catalog.pg_namespace AS schema_row
                ON schema_row.oid = function_row.pronamespace
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner)
                 )
             ) AS acl
             WHERE schema_row.nspname = 'public'
               AND acl.grantee = 0
        )
        AND NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_largeobject_metadata AS large_object
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     large_object.lomacl,
                     pg_catalog.acldefault('L', large_object.lomowner)
                 )
             ) AS acl
             WHERE acl.grantee = 0
        )
        AND NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_parameter_acl AS parameter_row
             CROSS JOIN LATERAL
                  pg_catalog.aclexplode(parameter_row.paracl) AS acl
             WHERE acl.grantee = 0
        ) AS passed
),
cluster_object_ok AS (
    SELECT COALESCE((
        SELECT
            NOT EXISTS (
                SELECT 1
                  FROM pg_catalog.pg_largeobject_metadata AS large_object
                 WHERE large_object.lomowner = role_row.oid
                    OR EXISTS (
                        SELECT 1
                          FROM pg_catalog.aclexplode(
                              COALESCE(
                                  large_object.lomacl,
                                  pg_catalog.acldefault(
                                      'L', large_object.lomowner
                                  )
                              )
                          ) AS acl
                         WHERE acl.grantee IN (0, role_row.oid)
                    )
            )
            AND NOT EXISTS (
                SELECT 1
                  FROM pg_catalog.pg_parameter_acl AS parameter_row
                 CROSS JOIN LATERAL
                      pg_catalog.aclexplode(parameter_row.paracl) AS acl
                 WHERE acl.grantee IN (0, role_row.oid)
            )
          FROM pg_catalog.pg_roles AS role_row
         WHERE role_row.rolname = current_user
    ), FALSE) AS passed
),
cross_database_ok AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_database AS database_row
         WHERE database_row.datallowconn
           AND database_row.datname <> pg_catalog.current_database()
           AND pg_catalog.has_database_privilege(
               current_user, database_row.oid, 'CONNECT'
           )
    ) AS passed
),
grant_options_ok AS (
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles AS role_row
            CROSS JOIN pg_catalog.pg_database AS database_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(database_row.datacl) AS acl
            WHERE role_row.rolname = current_user
              AND database_row.datname = pg_catalog.current_database()
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles AS role_row
            CROSS JOIN pg_catalog.pg_namespace AS schema_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(schema_row.nspacl) AS acl
            WHERE role_row.rolname = current_user
              AND schema_row.nspname = 'public'
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles AS role_row
            CROSS JOIN public_tables AS relation
            CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) AS acl
            WHERE role_row.rolname = current_user
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles AS role_row
            CROSS JOIN public_tables AS relation
            JOIN pg_catalog.pg_attribute AS column_row
              ON column_row.attrelid = relation.oid
             AND column_row.attnum > 0
             AND NOT column_row.attisdropped
            CROSS JOIN LATERAL pg_catalog.aclexplode(column_row.attacl) AS acl
            WHERE role_row.rolname = current_user
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles AS role_row
            CROSS JOIN public_sequences AS sequence_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(sequence_row.relacl) AS acl
            WHERE role_row.rolname = current_user
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_roles AS role_row
            CROSS JOIN pg_catalog.pg_proc AS function_row
            JOIN pg_catalog.pg_namespace AS schema_row
              ON schema_row.oid = function_row.pronamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(function_row.proacl) AS acl
            WHERE role_row.rolname = current_user
              AND schema_row.nspname = 'public'
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        ) AS passed
),
check_results(check_name, passed) AS (
    SELECT 'role', passed FROM role_ok
    UNION ALL SELECT 'ddl_boundary', passed FROM ddl_boundary_ok
    UNION ALL SELECT 'relations', passed FROM relations_ok
    UNION ALL SELECT 'table_acl', passed FROM table_acl_ok
    UNION ALL SELECT 'column_acl', passed FROM column_acl_ok
    UNION ALL SELECT 'functions_sequences', passed FROM function_sequence_ok
    UNION ALL SELECT 'cross_schema', passed FROM cross_schema_ok
    UNION ALL SELECT 'public_acl', passed FROM public_acl_ok
    UNION ALL SELECT 'cluster_objects', passed FROM cluster_object_ok
    UNION ALL SELECT 'cross_database', passed FROM cross_database_ok
    UNION ALL SELECT 'grant_options', passed FROM grant_options_ok
)
SELECT
    COALESCE(pg_catalog.bool_and(passed), FALSE) AS boundary_ok,
    COALESCE(
        pg_catalog.string_agg(check_name, ',' ORDER BY check_name)
            FILTER (WHERE NOT COALESCE(passed, FALSE)),
        ''
    ) AS boundary_failures
FROM check_results
"""
)


def verify_edge_database_boundary(
    engine: Engine,
    *,
    expected_role: str = EDGE_ROLE,
    expected_migration_role: str = MIGRATION_ROLE,
) -> None:
    """Prove the receiver has exactly the reviewed effective PostgreSQL ACL."""

    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as connection:
        result = connection.execute(
            _BOUNDARY_SQL,
            {
                "edge_role": expected_role,
                "migration_role": expected_migration_role,
            },
        ).mappings().one_or_none()
    if (
        result is None
        or result.get("boundary_ok") is not True
        or result.get("boundary_failures") != ""
    ):
        failures = (
            str(result.get("boundary_failures") or "unknown")
            if result is not None
            else "missing_evidence"
        )
        raise EdgeDatabaseBoundaryError(
            f"edge receiver database boundary failed: {failures}"
        )


__all__ = [
    "EDGE_ROLE",
    "MIGRATION_ROLE",
    "EdgeDatabaseBoundaryError",
    "verify_edge_database_boundary",
]
