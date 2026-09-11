\set ON_ERROR_STOP on
\pset format unaligned
\pset fieldsep '|'

-- Usage (run as star_oam_migrator after Alembic and edge ACL provisioning):
--   psql ... \
--     -v edge_role=edge_inbox \
--     -v projector_role=star_oam_projector \
--     -f deployment/verify_oam_edge_staging.sql
\if :{?edge_role}
\else
\echo 'edge_role is required; refusing to guess a database principal'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif
\if :{?projector_role}
\else
\echo 'projector_role is required; refusing to guess a database principal'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

SELECT
    pg_catalog.count(*) = 2
    AND pg_catalog.count(DISTINCT role_row.rolname) = 2
    AND :'edge_role' <> :'projector_role'
    AS deployment_roles_present
FROM pg_catalog.pg_roles AS role_row
WHERE role_row.rolname IN (:'edge_role', :'projector_role')
\gset

\if :deployment_roles_present
\else
\echo 'edge_role and projector_role must name two existing, distinct principals'
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

-- Verify effective privileges over every public relation/column, rather than
-- checking only the intended tables.  This catches PUBLIC grants, inherited
-- grants, stale column ACLs, and accidental access to a new formal table.
WITH
role_names(role_kind, role_name) AS (
    VALUES
        ('edge'::text, :'edge_role'::text),
        ('projector'::text, :'projector_role'::text)
),
runtime_functions(function_signature) AS (
    VALUES
        ('public.rsc_oam_rls_check_0044(text,text,jsonb)'::text),
        ('public.rsc_oam_runtime_binding_ready_0044()'::text)
),
expected_function_acl(role_kind, function_signature) AS (
    SELECT role_name.role_kind, runtime_function.function_signature
    FROM role_names AS role_name
    CROSS JOIN runtime_functions AS runtime_function
),
edge_table_acl(
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
projector_read_tables(table_name) AS (
    SELECT pg_catalog.unnest(ARRAY[
        'external_sync_snapshots',
        'external_sync_snapshot_batches',
        'external_sync_snapshot_records',
        'external_sync_current_records',
        'source_systems',
        'sync_runs',
        'sync_batches',
        'sync_inbox_events',
        'external_objects',
        'external_object_versions',
        'external_object_mappings',
        'sync_conflicts',
        'organizations',
        'people',
        'oam_work_orders',
        'shipments',
        'oam_receipt_evidence'
    ]::text[])
),
projector_write_tables(table_name) AS (
    SELECT pg_catalog.unnest(ARRAY[
        'sync_runs',
        'sync_batches',
        'sync_inbox_events',
        'external_objects',
        'external_object_versions',
        'sync_conflicts',
        'oam_work_orders'
    ]::text[])
),
expected_table_acl AS (
    SELECT
        'edge'::text AS role_kind,
        edge_acl.table_name,
        edge_acl.can_select,
        edge_acl.can_insert,
        edge_acl.can_update,
        edge_acl.can_delete,
        edge_acl.can_truncate,
        edge_acl.can_references,
        edge_acl.can_trigger
    FROM edge_table_acl AS edge_acl
    UNION ALL
    SELECT
        'projector'::text,
        read_table.table_name,
        TRUE,
        FALSE,
        FALSE,
        FALSE,
        FALSE,
        FALSE,
        FALSE
    FROM projector_read_tables AS read_table
    LEFT JOIN projector_write_tables AS write_table
      ON write_table.table_name = read_table.table_name
),
expected_edge_update_columns(table_name, column_name) AS (
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
expected_projector_insert_columns(table_name, column_name) AS (
    VALUES
        ('sync_runs', 'id'),
        ('sync_runs', 'source_system_id'),
        ('sync_runs', 'run_key'),
        ('sync_runs', 'scope_key'),
        ('sync_runs', 'mode'),
        ('sync_runs', 'watermark_from'),
        ('sync_runs', 'watermark_to'),
        ('sync_runs', 'status'),
        ('sync_runs', 'manifest_sha256'),
        ('sync_runs', 'started_at'),
        ('sync_runs', 'completed_at'),
        ('sync_runs', 'failure_code'),
        ('sync_runs', 'failure_detail'),
        ('sync_runs', 'created_at'),
        ('sync_runs', 'updated_at'),
        ('sync_batches', 'id'),
        ('sync_batches', 'run_id'),
        ('sync_batches', 'entity_type'),
        ('sync_batches', 'sequence'),
        ('sync_batches', 'record_count'),
        ('sync_batches', 'body_sha256'),
        ('sync_batches', 'status'),
        ('sync_batches', 'received_at'),
        ('sync_batches', 'validated_at'),
        ('sync_batches', 'created_at'),
        ('sync_inbox_events', 'id'),
        ('sync_inbox_events', 'batch_id'),
        ('sync_inbox_events', 'source_system_id'),
        ('sync_inbox_events', 'external_event_id'),
        ('sync_inbox_events', 'entity_type'),
        ('sync_inbox_events', 'external_id'),
        ('sync_inbox_events', 'source_version'),
        ('sync_inbox_events', 'source_updated_at'),
        ('sync_inbox_events', 'payload_jsonb'),
        ('sync_inbox_events', 'payload_sha256'),
        ('sync_inbox_events', 'status'),
        ('sync_inbox_events', 'error_code'),
        ('sync_inbox_events', 'error_detail'),
        ('sync_inbox_events', 'processed_at'),
        ('sync_inbox_events', 'created_at'),
        ('external_objects', 'id'),
        ('external_objects', 'source_system_id'),
        ('external_objects', 'entity_type'),
        ('external_objects', 'external_id'),
        ('external_objects', 'current_version_id'),
        ('external_objects', 'deleted_at'),
        ('external_objects', 'created_at'),
        ('external_objects', 'updated_at'),
        ('external_object_versions', 'id'),
        ('external_object_versions', 'external_object_id'),
        ('external_object_versions', 'source_version'),
        ('external_object_versions', 'source_updated_at'),
        ('external_object_versions', 'valid_from'),
        ('external_object_versions', 'valid_to'),
        ('external_object_versions', 'payload_jsonb'),
        ('external_object_versions', 'payload_sha256'),
        ('external_object_versions', 'is_current'),
        ('external_object_versions', 'created_at'),
        ('sync_conflicts', 'id'),
        ('sync_conflicts', 'run_id'),
        ('sync_conflicts', 'inbox_event_id'),
        ('sync_conflicts', 'external_object_id'),
        ('sync_conflicts', 'dedup_key'),
        ('sync_conflicts', 'conflict_type'),
        ('sync_conflicts', 'external_value_jsonb'),
        ('sync_conflicts', 'local_value_jsonb'),
        ('sync_conflicts', 'status'),
        ('sync_conflicts', 'resolution_jsonb'),
        ('sync_conflicts', 'resolved_by'),
        ('sync_conflicts', 'resolved_at'),
        ('sync_conflicts', 'created_at'),
        ('sync_conflicts', 'updated_at'),
        ('oam_work_orders', 'id'),
        ('oam_work_orders', 'external_object_id'),
        ('oam_work_orders', 'work_order_no'),
        ('oam_work_orders', 'organization_id'),
        ('oam_work_orders', 'engineer_person_id'),
        ('oam_work_orders', 'status'),
        ('oam_work_orders', 'source_updated_at'),
        ('oam_work_orders', 'created_at'),
        ('oam_work_orders', 'updated_at'),
        ('oam_receipt_evidence', 'id'),
        ('oam_receipt_evidence', 'external_object_id'),
        ('oam_receipt_evidence', 'shipment_id'),
        ('oam_receipt_evidence', 'status'),
        ('oam_receipt_evidence', 'source_time'),
        ('oam_receipt_evidence', 'source_version'),
        ('oam_receipt_evidence', 'payload_sha256'),
        ('oam_receipt_evidence', 'created_at')
),
expected_projector_update_columns(table_name, column_name) AS (
    VALUES
        ('sync_runs', 'status'),
        ('sync_runs', 'manifest_sha256'),
        ('sync_runs', 'completed_at'),
        ('sync_runs', 'failure_code'),
        ('sync_runs', 'failure_detail'),
        ('sync_runs', 'updated_at'),
        ('sync_batches', 'status'),
        ('sync_batches', 'validated_at'),
        ('sync_inbox_events', 'status'),
        ('sync_inbox_events', 'error_code'),
        ('sync_inbox_events', 'error_detail'),
        ('sync_inbox_events', 'processed_at'),
        ('external_objects', 'current_version_id'),
        ('external_objects', 'updated_at'),
        ('external_object_versions', 'is_current'),
        ('external_object_versions', 'valid_to'),
        ('sync_conflicts', 'status'),
        ('sync_conflicts', 'resolution_jsonb'),
        ('sync_conflicts', 'resolved_by'),
        ('sync_conflicts', 'resolved_at'),
        ('sync_conflicts', 'updated_at'),
        ('oam_work_orders', 'work_order_no'),
        ('oam_work_orders', 'organization_id'),
        ('oam_work_orders', 'engineer_person_id'),
        ('oam_work_orders', 'status'),
        ('oam_work_orders', 'source_updated_at'),
        ('oam_work_orders', 'updated_at')
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
    SELECT relation.oid, relation.relacl
    FROM pg_catalog.pg_class AS relation
    JOIN pg_catalog.pg_namespace AS schema_row
      ON schema_row.oid = relation.relnamespace
    WHERE schema_row.nspname = 'public'
      AND relation.relkind = 'S'
),
non_system_schemas AS (
    SELECT schema_row.oid, schema_row.nspname, schema_row.nspowner
    FROM pg_catalog.pg_namespace AS schema_row
    WHERE schema_row.nspname <> 'public'
      AND schema_row.nspname <> 'information_schema'
      AND schema_row.nspname !~ '^pg_'
),
roles_ok AS (
    SELECT
        (SELECT pg_catalog.count(*) FROM role_names) = 2
        AND (SELECT pg_catalog.count(DISTINCT role_name) FROM role_names) = 2
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            LEFT JOIN pg_catalog.pg_roles AS actual_role
              ON actual_role.rolname = expected_role.role_name
            WHERE actual_role.oid IS NULL
               OR (
                    expected_role.role_kind = 'projector'
                    AND expected_role.role_name <> 'star_oam_projector'
               )
               OR (
                    expected_role.role_kind = 'edge'
                    AND expected_role.role_name IN (
                        'star_oam_migrator',
                        'star_oam_api',
                        'star_oam_backup',
                        'star_oam_projector',
                        'star_oam_edge'
                    )
               )
               OR NOT actual_role.rolcanlogin
               OR actual_role.rolsuper
               OR actual_role.rolcreatedb
               OR actual_role.rolcreaterole
               OR actual_role.rolreplication
               OR actual_role.rolbypassrls
               OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_auth_members AS membership
                    WHERE membership.member = actual_role.oid
               )
               OR EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_auth_members AS membership
                    WHERE membership.roleid = actual_role.oid
               )
        ) AS passed
),
ddl_boundary_ok AS (
    SELECT
        pg_catalog.pg_get_userbyid(database_row.datdba) = 'star_oam_migrator'
        AND pg_catalog.pg_get_userbyid(schema_row.nspowner) = 'star_oam_migrator'
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            WHERE NOT pg_catalog.has_database_privilege(
                    expected_role.role_name,
                    database_row.oid,
                    'CONNECT'
                  )
               OR pg_catalog.has_database_privilege(
                    expected_role.role_name,
                    database_row.oid,
                    'CREATE'
                  )
               OR pg_catalog.has_database_privilege(
                    expected_role.role_name,
                    database_row.oid,
                    'TEMPORARY'
                  )
               OR NOT pg_catalog.has_schema_privilege(
                    expected_role.role_name,
                    schema_row.oid,
                    'USAGE'
                  )
               OR pg_catalog.has_schema_privilege(
                    expected_role.role_name,
                    schema_row.oid,
                    'CREATE'
                  )
        ) AS passed
    FROM pg_catalog.pg_database AS database_row
    CROSS JOIN pg_catalog.pg_namespace AS schema_row
    WHERE database_row.datname = pg_catalog.current_database()
      AND schema_row.nspname = 'public'
),
relations_ok AS (
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM expected_table_acl AS expected_acl
            WHERE NOT EXISTS (
                SELECT 1
                FROM public_tables AS relation
                WHERE relation.relname = expected_acl.table_name
            )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM public_tables AS relation
            WHERE pg_catalog.pg_get_userbyid(relation.relowner)
                  <> 'star_oam_migrator'
        ) AS passed
),
table_acl_ok AS (
    SELECT NOT EXISTS (
        SELECT 1
        FROM role_names AS expected_role
        CROSS JOIN public_tables AS relation
        CROSS JOIN table_privileges AS checked_privilege
        LEFT JOIN expected_table_acl AS expected_acl
          ON expected_acl.role_kind = expected_role.role_kind
         AND expected_acl.table_name = relation.relname
        WHERE pg_catalog.has_table_privilege(
                  expected_role.role_name,
                  relation.oid,
                  checked_privilege.privilege_type
              ) IS DISTINCT FROM CASE checked_privilege.privilege_type
                    WHEN 'SELECT' THEN COALESCE(expected_acl.can_select, FALSE)
                    WHEN 'INSERT' THEN COALESCE(expected_acl.can_insert, FALSE)
                    WHEN 'UPDATE' THEN COALESCE(expected_acl.can_update, FALSE)
                    WHEN 'DELETE' THEN COALESCE(expected_acl.can_delete, FALSE)
                    WHEN 'TRUNCATE' THEN COALESCE(expected_acl.can_truncate, FALSE)
                    WHEN 'REFERENCES' THEN COALESCE(expected_acl.can_references, FALSE)
                    WHEN 'TRIGGER' THEN COALESCE(expected_acl.can_trigger, FALSE)
                    ELSE FALSE
              END
    ) AS passed
),
column_acl_ok AS (
    SELECT NOT EXISTS (
        SELECT 1
        FROM role_names AS expected_role
        CROSS JOIN public_tables AS relation
        JOIN pg_catalog.pg_attribute AS column_row
          ON column_row.attrelid = relation.oid
         AND column_row.attnum > 0
         AND NOT column_row.attisdropped
        CROSS JOIN column_privileges AS checked_privilege
        LEFT JOIN expected_table_acl AS expected_acl
          ON expected_acl.role_kind = expected_role.role_kind
         AND expected_acl.table_name = relation.relname
        WHERE pg_catalog.has_column_privilege(
                  expected_role.role_name,
                  relation.oid,
                  column_row.attnum,
                  checked_privilege.privilege_type
              ) IS DISTINCT FROM (
                  CASE checked_privilege.privilege_type
                      WHEN 'SELECT' THEN COALESCE(expected_acl.can_select, FALSE)
                      WHEN 'INSERT' THEN COALESCE(expected_acl.can_insert, FALSE)
                      WHEN 'UPDATE' THEN COALESCE(expected_acl.can_update, FALSE)
                      WHEN 'REFERENCES' THEN COALESCE(
                          expected_acl.can_references,
                          FALSE
                      )
                      ELSE FALSE
                  END
                  OR (
                      expected_role.role_kind = 'edge'
                      AND checked_privilege.privilege_type = 'UPDATE'
                      AND EXISTS (
                          SELECT 1
                          FROM expected_edge_update_columns AS update_column
                          WHERE update_column.table_name = relation.relname
                            AND update_column.column_name = column_row.attname
                      )
                  )
                  OR (
                      expected_role.role_kind = 'projector'
                      AND checked_privilege.privilege_type = 'INSERT'
                      AND EXISTS (
                          SELECT 1
                          FROM expected_projector_insert_columns AS insert_column
                          WHERE insert_column.table_name = relation.relname
                            AND insert_column.column_name = column_row.attname
                      )
                  )
                  OR (
                      expected_role.role_kind = 'projector'
                      AND checked_privilege.privilege_type = 'UPDATE'
                      AND EXISTS (
                          SELECT 1
                          FROM expected_projector_update_columns AS update_column
                          WHERE update_column.table_name = relation.relname
                            AND update_column.column_name = column_row.attname
                      )
                  )
              )
    ) AS passed
),
function_sequence_ok AS (
    SELECT
        -- Both runtime entrypoints are migration-owned SECURITY DEFINER
        -- functions with a fixed catalog-only search path.  Private 0044
        -- helpers are deliberately absent from this allowlist.
        NOT EXISTS (
            SELECT 1
            FROM runtime_functions AS runtime_function
            LEFT JOIN pg_catalog.pg_proc AS function_row
              ON function_row.oid = pg_catalog.to_regprocedure(
                  runtime_function.function_signature
              )
            WHERE function_row.oid IS NULL
               OR pg_catalog.pg_get_userbyid(function_row.proowner)
                    <> 'star_oam_migrator'
               OR NOT function_row.prosecdef
               OR function_row.provolatile <> 's'
               OR function_row.proleakproof
               OR function_row.proconfig IS DISTINCT FROM
                    ARRAY['search_path=pg_catalog']::text[]
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            CROSS JOIN pg_catalog.pg_proc AS function_row
            JOIN pg_catalog.pg_namespace AS schema_row
              ON schema_row.oid = function_row.pronamespace
            LEFT JOIN expected_function_acl AS expected_acl
              ON expected_acl.role_kind = expected_role.role_kind
             AND function_row.oid = pg_catalog.to_regprocedure(
                 expected_acl.function_signature
             )
            WHERE schema_row.nspname = 'public'
              AND pg_catalog.has_function_privilege(
                      expected_role.role_name,
                      function_row.oid,
                      'EXECUTE'
                  ) IS DISTINCT FROM
                  (expected_acl.function_signature IS NOT NULL)
        )
        -- The two public entrypoints may be executable only by their owner and
        -- the exact edge/projector roles.  This rejects a grant to PUBLIC,
        -- API, backup, a stale role, or an unexpected deployment principal.
        AND NOT EXISTS (
            SELECT 1
            FROM runtime_functions AS runtime_function
            JOIN pg_catalog.pg_proc AS function_row
              ON function_row.oid = pg_catalog.to_regprocedure(
                  runtime_function.function_signature
              )
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS acl
            LEFT JOIN pg_catalog.pg_roles AS acl_grantee
              ON acl_grantee.oid = acl.grantee
            WHERE acl.privilege_type = 'EXECUTE'
              AND acl.grantee <> function_row.proowner
              AND (
                  acl.grantee = 0
                  OR acl_grantee.rolname IS NULL
                  OR acl_grantee.rolname NOT IN (
                      :'edge_role',
                      :'projector_role'
                  )
              )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            CROSS JOIN public_sequences AS sequence_row
            WHERE pg_catalog.has_sequence_privilege(
                      expected_role.role_name,
                      sequence_row.oid,
                      'USAGE'
                  )
               OR pg_catalog.has_sequence_privilege(
                      expected_role.role_name,
                      sequence_row.oid,
                      'SELECT'
                  )
               OR pg_catalog.has_sequence_privilege(
                      expected_role.role_name,
                      sequence_row.oid,
                      'UPDATE'
                  )
        ) AS passed
),
cross_schema_ok AS (
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN non_system_schemas AS schema_row
            WHERE schema_row.nspowner = role_row.oid
               OR pg_catalog.has_schema_privilege(
                    expected_role.role_name,
                    schema_row.oid,
                    'USAGE'
                  )
               OR pg_catalog.has_schema_privilege(
                    expected_role.role_name,
                    schema_row.oid,
                    'CREATE'
                  )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN pg_catalog.pg_class AS relation
            JOIN non_system_schemas AS schema_row
              ON schema_row.oid = relation.relnamespace
            WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND (
                  relation.relowner = role_row.oid
                  OR pg_catalog.has_table_privilege(
                      expected_role.role_name, relation.oid, 'SELECT'
                  )
                  OR pg_catalog.has_table_privilege(
                      expected_role.role_name, relation.oid, 'INSERT'
                  )
                  OR pg_catalog.has_table_privilege(
                      expected_role.role_name, relation.oid, 'UPDATE'
                  )
                  OR pg_catalog.has_table_privilege(
                      expected_role.role_name, relation.oid, 'DELETE'
                  )
                  OR pg_catalog.has_table_privilege(
                      expected_role.role_name, relation.oid, 'TRUNCATE'
                  )
                  OR pg_catalog.has_table_privilege(
                      expected_role.role_name, relation.oid, 'REFERENCES'
                  )
                  OR pg_catalog.has_table_privilege(
                      expected_role.role_name, relation.oid, 'TRIGGER'
                  )
                  OR pg_catalog.has_any_column_privilege(
                      expected_role.role_name, relation.oid, 'SELECT'
                  )
                  OR pg_catalog.has_any_column_privilege(
                      expected_role.role_name, relation.oid, 'INSERT'
                  )
                  OR pg_catalog.has_any_column_privilege(
                      expected_role.role_name, relation.oid, 'UPDATE'
                  )
                  OR pg_catalog.has_any_column_privilege(
                      expected_role.role_name, relation.oid, 'REFERENCES'
                  )
              )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN pg_catalog.pg_class AS sequence_row
            JOIN non_system_schemas AS schema_row
              ON schema_row.oid = sequence_row.relnamespace
            WHERE sequence_row.relkind = 'S'
              AND (
                  sequence_row.relowner = role_row.oid
                  OR pg_catalog.has_sequence_privilege(
                      expected_role.role_name, sequence_row.oid, 'USAGE'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      expected_role.role_name, sequence_row.oid, 'SELECT'
                  )
                  OR pg_catalog.has_sequence_privilege(
                      expected_role.role_name, sequence_row.oid, 'UPDATE'
                  )
              )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN pg_catalog.pg_proc AS function_row
            JOIN non_system_schemas AS schema_row
              ON schema_row.oid = function_row.pronamespace
            WHERE function_row.proowner = role_row.oid
               OR pg_catalog.has_function_privilege(
                    expected_role.role_name,
                    function_row.oid,
                    'EXECUTE'
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
              FROM public_sequences AS sequence_row
             CROSS JOIN LATERAL
                  pg_catalog.aclexplode(sequence_row.relacl) AS acl
             WHERE acl.grantee = 0
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
    SELECT
        NOT EXISTS (
            SELECT 1
              FROM role_names AS expected_role
              JOIN pg_catalog.pg_roles AS role_row
                ON role_row.rolname = expected_role.role_name
             CROSS JOIN pg_catalog.pg_largeobject_metadata AS large_object
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
              FROM role_names AS expected_role
              JOIN pg_catalog.pg_roles AS role_row
                ON role_row.rolname = expected_role.role_name
             CROSS JOIN pg_catalog.pg_parameter_acl AS parameter_row
             CROSS JOIN LATERAL
                  pg_catalog.aclexplode(parameter_row.paracl) AS acl
             WHERE acl.grantee IN (0, role_row.oid)
        ) AS passed
),
cross_database_ok AS (
    SELECT NOT EXISTS (
        SELECT 1
          FROM role_names AS expected_role
         CROSS JOIN pg_catalog.pg_database AS database_row
         WHERE database_row.datallowconn
           AND database_row.datname <> pg_catalog.current_database()
           AND pg_catalog.has_database_privilege(
               expected_role.role_name,
               database_row.oid,
               'CONNECT'
           )
    ) AS passed
),
grant_options_ok AS (
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN public_tables AS relation
            CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) AS acl
            WHERE acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN public_tables AS relation
            JOIN pg_catalog.pg_attribute AS column_row
              ON column_row.attrelid = relation.oid
             AND column_row.attnum > 0
             AND NOT column_row.attisdropped
            CROSS JOIN LATERAL pg_catalog.aclexplode(column_row.attacl) AS acl
            WHERE acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN pg_catalog.pg_proc AS function_row
            JOIN pg_catalog.pg_namespace AS schema_row
              ON schema_row.oid = function_row.pronamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(function_row.proacl) AS acl
            WHERE schema_row.nspname = 'public'
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN public_sequences AS sequence_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(sequence_row.relacl) AS acl
            WHERE acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN pg_catalog.pg_namespace AS schema_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(schema_row.nspacl) AS acl
            WHERE schema_row.nspname = 'public'
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        )
        AND NOT EXISTS (
            SELECT 1
            FROM role_names AS expected_role
            JOIN pg_catalog.pg_roles AS role_row
              ON role_row.rolname = expected_role.role_name
            CROSS JOIN pg_catalog.pg_database AS database_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(database_row.datacl) AS acl
            WHERE database_row.datname = pg_catalog.current_database()
              AND acl.grantee IN (0, role_row.oid)
              AND acl.is_grantable
        ) AS passed
),
check_results(check_name, passed) AS (
    SELECT 'roles', passed FROM roles_ok
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
    COALESCE(pg_catalog.bool_and(passed), FALSE) AS deployment_acl_ok,
    COALESCE(
        pg_catalog.string_agg(check_name, ',' ORDER BY check_name)
            FILTER (WHERE NOT COALESCE(passed, FALSE)),
        ''
    ) AS deployment_acl_failures
FROM check_results
\gset

\if :deployment_acl_ok
\echo 'edge/projector deployment ACL verified'
\else
\echo 'edge/projector deployment ACL verification failed:' :deployment_acl_failures
SELECT 1 / 0 AS intentional_psql_fail_closed;
\endif

SELECT
    relation.relname AS table_name,
    pg_catalog.pg_size_pretty(
        pg_catalog.pg_total_relation_size(relation.oid)
    ) AS total_size
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS schema_row
  ON schema_row.oid = relation.relnamespace
WHERE schema_row.nspname = 'public'
  AND relation.relname IN (
      'external_sync_snapshots',
      'external_sync_snapshot_batches',
      'external_sync_snapshot_records',
      'external_sync_current_records',
      'audit_logs',
      'oam_work_orders'
  )
ORDER BY relation.relname;
