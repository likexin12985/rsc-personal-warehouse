"""Fail-closed database identity check for the OAM projection worker."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .oam_sync_scope_security import (
    oam_sync_scope_boundary_passed,
    read_oam_sync_scope_boundary,
)


PROJECTOR_ROLE = "star_oam_projector"
MIGRATION_ROLE = "star_oam_migrator"

PROJECTOR_READ_TABLES = frozenset(
    {
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
        "source_systems",
        "sync_runs",
        "sync_batches",
        "sync_inbox_events",
        "external_objects",
        "external_object_versions",
        "external_object_mappings",
        "sync_conflicts",
        "organizations",
        "people",
        "oam_work_orders",
        "shipments",
        "oam_receipt_evidence",
    }
)
PROJECTOR_INSERT_COLUMNS = {
    "sync_runs": frozenset(
        {
            "id",
            "source_system_id",
            "run_key",
            "scope_key",
            "mode",
            "watermark_from",
            "watermark_to",
            "status",
            "manifest_sha256",
            "started_at",
            "completed_at",
            "failure_code",
            "failure_detail",
            "created_at",
            "updated_at",
        }
    ),
    "sync_batches": frozenset(
        {
            "id",
            "run_id",
            "entity_type",
            "sequence",
            "record_count",
            "body_sha256",
            "status",
            "received_at",
            "validated_at",
            "created_at",
        }
    ),
    "sync_inbox_events": frozenset(
        {
            "id",
            "batch_id",
            "source_system_id",
            "external_event_id",
            "entity_type",
            "external_id",
            "source_version",
            "source_updated_at",
            "payload_jsonb",
            "payload_sha256",
            "status",
            "error_code",
            "error_detail",
            "processed_at",
            "created_at",
        }
    ),
    "external_objects": frozenset(
        {
            "id",
            "source_system_id",
            "entity_type",
            "external_id",
            "current_version_id",
            "deleted_at",
            "created_at",
            "updated_at",
        }
    ),
    "external_object_versions": frozenset(
        {
            "id",
            "external_object_id",
            "source_version",
            "source_updated_at",
            "valid_from",
            "valid_to",
            "payload_jsonb",
            "payload_sha256",
            "is_current",
            "created_at",
        }
    ),
    "sync_conflicts": frozenset(
        {
            "id",
            "run_id",
            "inbox_event_id",
            "external_object_id",
            "dedup_key",
            "conflict_type",
            "external_value_jsonb",
            "local_value_jsonb",
            "status",
            "resolution_jsonb",
            "resolved_by",
            "resolved_at",
            "created_at",
            "updated_at",
        }
    ),
    "oam_work_orders": frozenset(
        {
            "id",
            "external_object_id",
            "work_order_no",
            "organization_id",
            "engineer_person_id",
            "status",
            "source_updated_at",
            "created_at",
            "updated_at",
        }
    ),
    "oam_receipt_evidence": frozenset(
        {
            "id",
            "external_object_id",
            "shipment_id",
            "status",
            "source_time",
            "source_version",
            "payload_sha256",
            "created_at",
        }
    ),
}
PROJECTOR_INSERT_TABLES = frozenset(PROJECTOR_INSERT_COLUMNS)
PROJECTOR_UPDATE_COLUMNS = {
    "sync_runs": frozenset(
        {
            "status",
            "manifest_sha256",
            "completed_at",
            "failure_code",
            "failure_detail",
            "updated_at",
        }
    ),
    "sync_batches": frozenset({"status", "validated_at"}),
    "sync_inbox_events": frozenset(
        {"status", "error_code", "error_detail", "processed_at"}
    ),
    "external_objects": frozenset({"current_version_id", "updated_at"}),
    "external_object_versions": frozenset({"is_current", "valid_to"}),
    "sync_conflicts": frozenset(
        {
            "status",
            "resolution_jsonb",
            "resolved_by",
            "resolved_at",
            "updated_at",
        }
    ),
    "oam_work_orders": frozenset(
        {
            "work_order_no",
            "organization_id",
            "engineer_person_id",
            "status",
            "source_updated_at",
            "updated_at",
        }
    ),
}
PROJECTOR_WRITE_TABLES = PROJECTOR_INSERT_TABLES | set(PROJECTOR_UPDATE_COLUMNS)
_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


class OamProjectionDatabaseBoundaryError(RuntimeError):
    pass


_ROLE_SQL = text(
    """
SELECT
    current_user AS current_role,
    role_row.rolsuper AS is_superuser,
    role_row.rolcreatedb AS can_create_database,
    role_row.rolcreaterole AS can_create_role,
    role_row.rolreplication AS can_replicate,
    role_row.rolbypassrls AS can_bypass_rls,
    role_row.rolcanlogin AS can_login,
    pg_catalog.pg_get_userbyid(database_row.datdba) AS database_owner,
    pg_catalog.has_database_privilege(
        current_user, pg_catalog.current_database(), 'CREATE'
    )
        AS can_create_in_database,
    pg_catalog.has_database_privilege(
        current_user, pg_catalog.current_database(), 'TEMPORARY'
    )
        AS can_create_temporary,
    EXISTS (
        SELECT 1
          FROM pg_catalog.aclexplode(
              COALESCE(
                  database_row.datacl,
                  pg_catalog.acldefault('d', database_row.datdba)
              )
          ) AS database_acl
         WHERE database_acl.grantee IN (0, role_row.oid)
           AND database_acl.is_grantable
    ) AS has_database_grant_option,
    EXISTS (
        SELECT 1
          FROM pg_catalog.aclexplode(
              COALESCE(
                  database_row.datacl,
                  pg_catalog.acldefault('d', database_row.datdba)
              )
          ) AS database_acl
         WHERE database_acl.grantee = 0
    ) AS has_public_database_acl,
    EXISTS (
        SELECT 1
          FROM pg_catalog.pg_database AS candidate_database
         WHERE candidate_database.datallowconn
           AND candidate_database.datname <> pg_catalog.current_database()
           AND pg_catalog.has_database_privilege(
               current_user, candidate_database.oid, 'CONNECT'
           )
    ) AS has_cross_database_connect,
    pg_catalog.pg_get_userbyid(schema_row.nspowner) AS schema_owner,
    pg_catalog.has_schema_privilege(current_user, 'public', 'USAGE')
        AS can_use_schema,
    pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE')
        AS can_create_in_schema,
    EXISTS (
        SELECT 1
          FROM pg_catalog.aclexplode(
              COALESCE(
                  schema_row.nspacl,
                  pg_catalog.acldefault('n', schema_row.nspowner)
              )
          ) AS schema_acl
         WHERE schema_acl.grantee IN (0, role_row.oid)
           AND schema_acl.is_grantable
    ) AS has_schema_grant_option,
    EXISTS (
        SELECT 1
          FROM pg_catalog.aclexplode(
              COALESCE(
                  schema_row.nspacl,
                  pg_catalog.acldefault('n', schema_row.nspowner)
              )
          ) AS schema_acl
         WHERE schema_acl.grantee = 0
    ) AS has_public_schema_acl,
    pg_catalog.current_schema() AS current_schema_name,
    pg_catalog.current_schemas(FALSE) AS current_schema_path,
    EXISTS (
        SELECT 1
          FROM pg_catalog.pg_namespace AS candidate_schema
         WHERE candidate_schema.nspname NOT LIKE 'pg_%'
           AND candidate_schema.nspname <> 'information_schema'
           AND candidate_schema.nspname <> 'public'
           AND (
                pg_catalog.pg_get_userbyid(candidate_schema.nspowner)
                    = current_user
                OR pg_catalog.has_schema_privilege(
                    current_user, candidate_schema.oid, 'USAGE'
                )
                OR pg_catalog.has_schema_privilege(
                    current_user, candidate_schema.oid, 'CREATE'
                )
           )
    ) AS has_non_system_schema_access,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_auth_members AS membership
         WHERE membership.member = role_row.oid
    ) AS is_member_of_other_role,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_auth_members AS membership
         WHERE membership.roleid = role_row.oid
    ) AS has_nonsuper_member,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ) AS migration_role_exists,
    COALESCE((
        SELECT migration_role.rolsuper
          FROM pg_catalog.pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_is_superuser,
    COALESCE((
        SELECT migration_role.rolcreatedb
          FROM pg_catalog.pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_can_create_database,
    COALESCE((
        SELECT migration_role.rolcreaterole
          FROM pg_catalog.pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_can_create_role,
    COALESCE((
        SELECT migration_role.rolreplication
          FROM pg_catalog.pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_can_replicate,
    COALESCE((
        SELECT migration_role.rolbypassrls
          FROM pg_catalog.pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_can_bypass_rls,
    COALESCE((
        SELECT EXISTS (
            SELECT 1 FROM pg_catalog.pg_auth_members AS membership
             WHERE membership.member = migration_role.oid
        )
          FROM pg_catalog.pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_has_membership,
    COALESCE((
        SELECT EXISTS (
            SELECT 1 FROM pg_catalog.pg_auth_members AS membership
             WHERE membership.roleid = migration_role.oid
        )
          FROM pg_catalog.pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_has_members,
    pg_catalog.has_parameter_privilege(
        current_user, 'session_replication_role', 'SET'
    ) AS can_disable_replication_guards,
    pg_catalog.current_setting('session_replication_role')
        AS session_replication_role
FROM pg_catalog.pg_roles AS role_row
JOIN pg_catalog.pg_database AS database_row
  ON database_row.datname = pg_catalog.current_database()
JOIN pg_catalog.pg_namespace AS schema_row ON schema_row.nspname = 'public'
WHERE role_row.rolname = current_user
"""
)

_TABLE_SQL = text(
    """
SELECT
    class_row.relname AS table_name,
    pg_catalog.pg_get_userbyid(class_row.relowner) AS owner_name,
    pg_catalog.has_table_privilege(current_user, class_row.oid, 'SELECT')
        AS can_select,
    pg_catalog.has_table_privilege(current_user, class_row.oid, 'INSERT')
        AS can_insert,
    pg_catalog.has_table_privilege(current_user, class_row.oid, 'UPDATE')
        AS can_update,
    pg_catalog.has_table_privilege(current_user, class_row.oid, 'DELETE')
        AS can_delete,
    pg_catalog.has_table_privilege(current_user, class_row.oid, 'TRUNCATE')
        AS can_truncate,
    pg_catalog.has_table_privilege(current_user, class_row.oid, 'REFERENCES')
        AS can_reference,
    pg_catalog.has_table_privilege(current_user, class_row.oid, 'TRIGGER')
        AS can_trigger,
    pg_catalog.has_any_column_privilege(current_user, class_row.oid, 'SELECT')
        AS can_select_any_column,
    pg_catalog.has_any_column_privilege(current_user, class_row.oid, 'INSERT')
        AS can_insert_any_column,
    pg_catalog.has_any_column_privilege(current_user, class_row.oid, 'UPDATE')
        AS can_update_any_column,
    pg_catalog.has_any_column_privilege(
        current_user, class_row.oid, 'REFERENCES'
    )
        AS can_reference_any_column,
    EXISTS (
        SELECT 1
          FROM pg_catalog.aclexplode(class_row.relacl) AS acl
         WHERE acl.grantee IN (0, role_row.oid)
           AND acl.is_grantable
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_attribute AS attribute_row
         CROSS JOIN LATERAL
              pg_catalog.aclexplode(attribute_row.attacl) AS acl
         WHERE attribute_row.attrelid = class_row.oid
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
           AND acl.grantee IN (0, role_row.oid)
           AND acl.is_grantable
    ) AS has_grant_option,
    EXISTS (
        SELECT 1
          FROM pg_catalog.aclexplode(class_row.relacl) AS acl
         WHERE acl.grantee = 0
    ) AS has_public_table_acl
FROM pg_catalog.pg_class AS class_row
JOIN pg_catalog.pg_namespace AS schema_row
  ON schema_row.oid = class_row.relnamespace
JOIN pg_catalog.pg_roles AS role_row ON role_row.rolname = current_user
WHERE schema_row.nspname = 'public'
  AND class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY class_row.relname
"""
)

_COLUMN_ACL_SQL = text(
    """
SELECT
    class_row.relname AS table_name,
    attribute_row.attname AS column_name,
    CASE
        WHEN column_acl.grantee = 0 THEN 'PUBLIC'
        ELSE pg_catalog.pg_get_userbyid(column_acl.grantee)
    END AS grantee_name,
    pg_catalog.upper(column_acl.privilege_type) AS privilege_type,
    column_acl.is_grantable AS is_grantable
FROM pg_catalog.pg_class AS class_row
JOIN pg_catalog.pg_namespace AS schema_row
  ON schema_row.oid = class_row.relnamespace
JOIN pg_catalog.pg_attribute AS attribute_row
  ON attribute_row.attrelid = class_row.oid
CROSS JOIN LATERAL
    pg_catalog.aclexplode(attribute_row.attacl) AS column_acl
JOIN pg_catalog.pg_roles AS role_row ON role_row.rolname = current_user
WHERE schema_row.nspname = 'public'
  AND class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND attribute_row.attnum > 0
  AND NOT attribute_row.attisdropped
  AND column_acl.grantee IN (0, role_row.oid)
ORDER BY class_row.relname, attribute_row.attnum, column_acl.grantee,
         column_acl.privilege_type
"""
)

_OBJECT_CLOSURE_SQL = text(
    """
SELECT
    (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
         WHERE schema_row.nspname = 'public'
           AND pg_catalog.has_function_privilege(
               current_user, function_row.oid, 'EXECUTE'
           )
           AND function_row.oid NOT IN (
               'public.rsc_oam_rls_check_0044(text,text,jsonb)'::regprocedure,
               'public.rsc_oam_runtime_binding_ready_0044()'::regprocedure
           )
    ) AS executable_function_count,
    (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_class AS sequence_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = sequence_row.relnamespace
         WHERE schema_row.nspname = 'public'
           AND sequence_row.relkind = 'S'
           AND (
                pg_catalog.has_sequence_privilege(
                    current_user, sequence_row.oid, 'USAGE'
                )
                OR pg_catalog.has_sequence_privilege(
                    current_user, sequence_row.oid, 'SELECT'
                )
                OR pg_catalog.has_sequence_privilege(
                    current_user, sequence_row.oid, 'UPDATE'
                )
           )
    ) AS accessible_sequence_count,
    (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_class AS class_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = class_row.relnamespace
         WHERE schema_row.nspname NOT LIKE 'pg_%'
           AND schema_row.nspname <> 'information_schema'
           AND schema_row.nspname <> 'public'
           AND (
                (
                    class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
                    AND (
                        pg_catalog.has_table_privilege(
                            current_user, class_row.oid, 'SELECT'
                        )
                        OR pg_catalog.has_table_privilege(
                            current_user, class_row.oid, 'INSERT'
                        )
                        OR pg_catalog.has_table_privilege(
                            current_user, class_row.oid, 'UPDATE'
                        )
                        OR pg_catalog.has_table_privilege(
                            current_user, class_row.oid, 'DELETE'
                        )
                        OR pg_catalog.has_table_privilege(
                            current_user, class_row.oid, 'TRUNCATE'
                        )
                        OR pg_catalog.has_table_privilege(
                            current_user, class_row.oid, 'REFERENCES'
                        )
                        OR pg_catalog.has_table_privilege(
                            current_user, class_row.oid, 'TRIGGER'
                        )
                        OR pg_catalog.has_any_column_privilege(
                            current_user, class_row.oid,
                            'SELECT,INSERT,UPDATE,REFERENCES'
                        )
                    )
                )
                OR (
                    class_row.relkind = 'S'
                    AND (
                        pg_catalog.has_sequence_privilege(
                            current_user, class_row.oid, 'USAGE'
                        )
                        OR pg_catalog.has_sequence_privilege(
                            current_user, class_row.oid, 'SELECT'
                        )
                        OR pg_catalog.has_sequence_privilege(
                            current_user, class_row.oid, 'UPDATE'
                        )
                    )
                )
           )
    ) AS non_public_relation_access_count,
    (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
         WHERE schema_row.nspname NOT LIKE 'pg_%'
           AND schema_row.nspname <> 'information_schema'
           AND schema_row.nspname <> 'public'
           AND pg_catalog.has_function_privilege(
                current_user, function_row.oid, 'EXECUTE'
           )
    ) AS non_public_function_access_count,
    (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_largeobject_metadata AS large_object
          JOIN pg_catalog.pg_roles AS role_row
            ON role_row.rolname = current_user
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
                   AND acl.privilege_type IN ('SELECT', 'UPDATE')
            )
    ) AS accessible_large_object_count,
    (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_parameter_acl AS parameter_acl
          JOIN pg_catalog.pg_roles AS role_row
            ON role_row.rolname = current_user
         CROSS JOIN LATERAL
              pg_catalog.aclexplode(parameter_acl.paracl) AS acl
         WHERE acl.grantee IN (0, role_row.oid)
    ) AS accessible_parameter_acl_count
"""
)


def verify_oam_projection_database_boundary(
    engine: Engine,
    *,
    expected_role: str = PROJECTOR_ROLE,
    expected_migration_role: str = MIGRATION_ROLE,
) -> None:
    """Prove the worker has exactly the reviewed effective PostgreSQL ACL."""

    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as connection:
        role = connection.execute(
            _ROLE_SQL, {"migration_role": expected_migration_role}
        ).mappings().one_or_none()
        tables = connection.execute(_TABLE_SQL).mappings().all()
        columns = connection.execute(_COLUMN_ACL_SQL).mappings().all()
        closure = connection.execute(_OBJECT_CLOSURE_SQL).mappings().one()
        rls_boundary = read_oam_sync_scope_boundary(
            connection,
            expected_role=expected_role,
            expected_migration_role=expected_migration_role,
        )
    failures: list[str] = []
    _assert_role(
        role,
        expected_role=expected_role,
        expected_migration_role=expected_migration_role,
        failures=failures,
    )
    _assert_tables(
        tables,
        expected_migration_role=expected_migration_role,
        failures=failures,
    )
    _assert_columns(columns, expected_role=expected_role, failures=failures)
    if closure.get("executable_function_count") != 0:
        failures.append("functions.execute")
    if closure.get("accessible_sequence_count") != 0:
        failures.append("sequences.access")
    if closure.get("non_public_relation_access_count") != 0:
        failures.append("non_public.relations.access")
    if closure.get("non_public_function_access_count") != 0:
        failures.append("non_public.functions.execute")
    if closure.get("accessible_large_object_count") != 0:
        failures.append("large_objects.access")
    if closure.get("accessible_parameter_acl_count") != 0:
        failures.append("parameters.access")
    if not oam_sync_scope_boundary_passed(rls_boundary):
        failures.append("rls.force_scope")
    if failures:
        raise OamProjectionDatabaseBoundaryError(
            "OAM projector database boundary failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_role(
    row: Mapping[str, object] | None,
    *,
    expected_role: str,
    expected_migration_role: str,
    failures: list[str],
) -> None:
    if row is None:
        failures.append("role.missing")
        return
    expected = {
        "current_role": expected_role,
        "is_superuser": False,
        "can_create_database": False,
        "can_create_role": False,
        "can_replicate": False,
        "can_bypass_rls": False,
        "can_login": True,
        "database_owner": expected_migration_role,
        "can_create_in_database": False,
        "can_create_temporary": False,
        "has_database_grant_option": False,
        "has_public_database_acl": False,
        "has_cross_database_connect": False,
        "schema_owner": expected_migration_role,
        "can_use_schema": True,
        "can_create_in_schema": False,
        "has_schema_grant_option": False,
        "has_public_schema_acl": False,
        "current_schema_name": "public",
        "current_schema_path": ["public"],
        "has_non_system_schema_access": False,
        "is_member_of_other_role": False,
        "has_nonsuper_member": False,
        "migration_role_exists": True,
        "migration_role_is_superuser": False,
        "migration_role_can_create_database": False,
        "migration_role_can_create_role": False,
        "migration_role_can_replicate": False,
        "migration_role_can_bypass_rls": False,
        "migration_role_has_membership": False,
        "migration_role_has_members": False,
        "can_disable_replication_guards": False,
        "session_replication_role": "origin",
    }
    for field, value in expected.items():
        if row.get(field) != value:
            failures.append(f"role.{field}")


def _assert_tables(
    rows: list[Mapping[str, object]],
    *,
    expected_migration_role: str,
    failures: list[str],
) -> None:
    seen: set[str] = set()
    field = {
        "SELECT": "can_select",
        "INSERT": "can_insert",
        "UPDATE": "can_update",
        "DELETE": "can_delete",
        "TRUNCATE": "can_truncate",
        "REFERENCES": "can_reference",
        "TRIGGER": "can_trigger",
    }
    column_field = {
        "SELECT": "can_select_any_column",
        "INSERT": "can_insert_any_column",
        "UPDATE": "can_update_any_column",
        "REFERENCES": "can_reference_any_column",
    }
    for row in rows:
        name = row.get("table_name")
        if not isinstance(name, str) or name in seen:
            failures.append("tables.identity")
            continue
        seen.add(name)
        if row.get("owner_name") != expected_migration_role:
            failures.append(f"tables.{name}.owner")
        expected_table_privileges = set()
        if name in PROJECTOR_READ_TABLES:
            expected_table_privileges.add("SELECT")
        for privilege in _PRIVILEGES:
            expected_table_access = privilege in expected_table_privileges
            if row.get(field[privilege]) is not expected_table_access:
                failures.append(f"tables.{name}.{privilege.lower()}")
            if (
                privilege in column_field
                and row.get(column_field[privilege])
                is not (
                    expected_table_access
                    or (
                        privilege == "INSERT"
                        and name in PROJECTOR_INSERT_COLUMNS
                    )
                    or (
                        privilege == "UPDATE"
                        and name in PROJECTOR_UPDATE_COLUMNS
                    )
                )
            ):
                failures.append(
                    f"tables.{name}.{privilege.lower()}_any_column"
                )
        if row.get("has_grant_option") is not False:
            failures.append(f"tables.{name}.grant_option")
        if row.get("has_public_table_acl") is not False:
            failures.append(f"tables.{name}.public_acl")
    required = (
        PROJECTOR_READ_TABLES
        | PROJECTOR_INSERT_TABLES
        | set(PROJECTOR_UPDATE_COLUMNS)
    )
    for missing in sorted(required - seen):
        failures.append(f"tables.{missing}.missing")


def _assert_columns(
    rows: list[Mapping[str, object]],
    *,
    expected_role: str,
    failures: list[str],
) -> None:
    expected = {
        (table_name, column_name, expected_role, "INSERT", False)
        for table_name, column_names in PROJECTOR_INSERT_COLUMNS.items()
        for column_name in column_names
    } | {
        (table_name, column_name, expected_role, "UPDATE", False)
        for table_name, column_names in PROJECTOR_UPDATE_COLUMNS.items()
        for column_name in column_names
    }
    actual: set[tuple[object, object, object, object, object]] = set()
    for row in rows:
        key = (
            row.get("table_name"),
            row.get("column_name"),
            row.get("grantee_name"),
            row.get("privilege_type"),
            row.get("is_grantable"),
        )
        if key in actual:
            failures.append("columns.identity")
        actual.add(key)
    for key in sorted(expected - actual, key=str):
        failures.append(f"columns.{key[0]}.{key[1]}.missing")
    for key in sorted(actual - expected, key=str):
        failures.append(f"columns.{key[0]}.{key[1]}.excess")


__all__ = [
    "MIGRATION_ROLE",
    "OamProjectionDatabaseBoundaryError",
    "PROJECTOR_INSERT_COLUMNS",
    "PROJECTOR_INSERT_TABLES",
    "PROJECTOR_READ_TABLES",
    "PROJECTOR_ROLE",
    "PROJECTOR_UPDATE_COLUMNS",
    "PROJECTOR_WRITE_TABLES",
    "verify_oam_projection_database_boundary",
]
