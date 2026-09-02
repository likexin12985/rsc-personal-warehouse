"""Add the isolated formal OAM work-order projector database boundary.

Revision ID: 20260902_0043
Revises: 20260902_0042
Create Date: 2026-09-02

No business table is added here: the V1 source/run/inbox/version/work-order
schema already exists.  This migration grants an independently provisioned
worker role only the exact tables required to transform completed edge staging
snapshots.  The edge receiver receives no formal-table access and the main API
keeps ``oam_work_orders`` SELECT-only.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0043"
down_revision: Union[str, Sequence[str], None] = "20260902_0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
PROJECTOR_ROLE = "star_oam_projector"
READ_TABLES = (
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
)
INSERT_COLUMNS = {
    "sync_runs": (
        "id", "source_system_id", "run_key", "scope_key", "mode",
        "watermark_from", "watermark_to", "status", "manifest_sha256",
        "started_at", "completed_at", "failure_code", "failure_detail",
        "created_at", "updated_at",
    ),
    "sync_batches": (
        "id", "run_id", "entity_type", "sequence", "record_count",
        "body_sha256", "status", "received_at", "validated_at", "created_at",
    ),
    "sync_inbox_events": (
        "id", "batch_id", "source_system_id", "external_event_id",
        "entity_type", "external_id", "source_version", "source_updated_at",
        "payload_jsonb", "payload_sha256", "status", "error_code",
        "error_detail", "processed_at", "created_at",
    ),
    "external_objects": (
        "id", "source_system_id", "entity_type", "external_id",
        "current_version_id", "deleted_at", "created_at", "updated_at",
    ),
    "external_object_versions": (
        "id", "external_object_id", "source_version", "source_updated_at",
        "valid_from", "valid_to", "payload_jsonb", "payload_sha256",
        "is_current", "created_at",
    ),
    "sync_conflicts": (
        "id", "run_id", "inbox_event_id", "external_object_id", "dedup_key",
        "conflict_type", "external_value_jsonb", "local_value_jsonb", "status",
        "resolution_jsonb", "resolved_by", "resolved_at", "created_at",
        "updated_at",
    ),
    "oam_work_orders": (
        "id", "external_object_id", "work_order_no", "organization_id",
        "engineer_person_id", "status", "source_updated_at", "created_at",
        "updated_at",
    ),
}
UPDATE_COLUMNS = {
    "sync_runs": (
        "status",
        "manifest_sha256",
        "completed_at",
        "failure_code",
        "failure_detail",
        "updated_at",
    ),
    "sync_batches": ("status", "validated_at"),
    "sync_inbox_events": (
        "status",
        "error_code",
        "error_detail",
        "processed_at",
    ),
    "external_objects": ("current_version_id", "updated_at"),
    "external_object_versions": ("is_current", "valid_to"),
    "sync_conflicts": (
        "status",
        "resolution_jsonb",
        "resolved_by",
        "resolved_at",
        "updated_at",
    ),
    "oam_work_orders": (
        "work_order_no",
        "organization_id",
        "engineer_person_id",
        "status",
        "source_updated_at",
        "updated_at",
    ),
}


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0043 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    _require_roles_and_tables()
    _close_projector_database_acl()
    _require_cross_schema_boundary()
    _apply_projector_acl()
    _require_external_object_acl_boundary()


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    _revoke_projector_public_object_acl()
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {PROJECTOR_ROLE}")


def _require_roles_and_tables() -> None:
    table_literals = ", ".join(f"'{name}'" for name in READ_TABLES)
    op.execute(
        f"""
DO $$
DECLARE
    missing_tables text;
    projector_oid oid;
    migration_oid oid;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '0043 must run as the migration database role';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{MIGRATION_ROLE}'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{PROJECTOR_ROLE}'
    ) THEN
        RAISE EXCEPTION
            '0043 requires provisioned migration and projector database roles';
    END IF;

    SELECT role_row.oid
      INTO migration_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{MIGRATION_ROLE}'
       AND role_row.rolcanlogin
       AND NOT role_row.rolsuper
       AND NOT role_row.rolcreatedb
       AND NOT role_row.rolcreaterole
       AND NOT role_row.rolreplication
       AND NOT role_row.rolbypassrls;
    IF migration_oid IS NULL THEN
        RAISE EXCEPTION
            '0043 migration role attributes violate the owner boundary';
    END IF;

    SELECT role_row.oid
      INTO projector_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{PROJECTOR_ROLE}'
       AND role_row.rolcanlogin
       AND NOT role_row.rolsuper
       AND NOT role_row.rolcreatedb
       AND NOT role_row.rolcreaterole
       AND NOT role_row.rolreplication
       AND NOT role_row.rolbypassrls;
    IF projector_oid IS NULL THEN
        RAISE EXCEPTION
            '0043 projector role attributes violate the isolated worker boundary';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_auth_members AS membership
         WHERE membership.member IN (projector_oid, migration_oid)
            OR membership.roleid IN (projector_oid, migration_oid)
    ) THEN
        RAISE EXCEPTION
            '0043 database role membership violates the isolated worker boundary';
    END IF;

    IF pg_catalog.pg_get_userbyid((
        SELECT database_row.datdba
          FROM pg_catalog.pg_database AS database_row
         WHERE database_row.datname = pg_catalog.current_database()
    )) <> '{MIGRATION_ROLE}' OR pg_catalog.pg_get_userbyid((
        SELECT schema_row.nspowner
          FROM pg_catalog.pg_namespace AS schema_row
         WHERE schema_row.nspname = 'public'
    )) <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '0043 requires migration-owned database and public schema';
    END IF;

    SELECT pg_catalog.string_agg(
        required_table, ', ' ORDER BY required_table
    )
      INTO missing_tables
      FROM pg_catalog.unnest(ARRAY[{table_literals}])
        AS required(required_table)
     WHERE pg_catalog.to_regclass(
         pg_catalog.format('public.%I', required_table)
     ) IS NULL;
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            '0043 projector boundary missing tables: %', missing_tables;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = relation.relnamespace
         WHERE schema_row.nspname = 'public'
           AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND relation.relowner <> migration_oid
    ) THEN
        RAISE EXCEPTION
            '0043 requires migration-owned public tables and views';
    END IF;
END
$$
"""
    )


def _close_projector_database_acl() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    EXECUTE pg_catalog.format(
        'REVOKE ALL ON DATABASE %I FROM {PROJECTOR_ROLE}',
        pg_catalog.current_database()
    );
    EXECUTE pg_catalog.format(
        'REVOKE ALL ON DATABASE %I FROM PUBLIC',
        pg_catalog.current_database()
    );
    EXECUTE pg_catalog.format(
        'GRANT CONNECT ON DATABASE %I TO {PROJECTOR_ROLE}',
        pg_catalog.current_database()
    );
END
$$
"""
    )


def _require_cross_schema_boundary() -> None:
    op.execute(
        f"""
DO $$
DECLARE
    projector_oid oid;
    database_oid oid;
BEGIN
    SELECT role_row.oid
      INTO STRICT projector_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{PROJECTOR_ROLE}';
    SELECT database_row.oid
      INTO STRICT database_oid
      FROM pg_catalog.pg_database AS database_row
     WHERE database_row.datname = pg_catalog.current_database();

    IF NOT pg_catalog.has_database_privilege(
        projector_oid,
        database_oid,
        'CONNECT'
    ) OR pg_catalog.has_database_privilege(
        projector_oid,
        database_oid,
        'CREATE'
    ) OR pg_catalog.has_database_privilege(
        projector_oid,
        database_oid,
        'TEMPORARY'
    ) THEN
        RAISE EXCEPTION
            '0043 projector database ACL violates the isolated worker boundary';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_database AS candidate_database
         WHERE candidate_database.datallowconn
           AND candidate_database.datname <> pg_catalog.current_database()
           AND pg_catalog.has_database_privilege(
               projector_oid, candidate_database.oid, 'CONNECT'
           )
    ) THEN
        RAISE EXCEPTION
            '0043 projector cross-database CONNECT boundary is not closed';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_namespace AS schema_row
         WHERE schema_row.nspname <> 'public'
           AND schema_row.nspname <> 'information_schema'
           AND schema_row.nspname !~ '^pg_'
           AND (
               schema_row.nspowner = projector_oid
               OR pg_catalog.has_schema_privilege(
                   projector_oid,
                   schema_row.oid,
                   'USAGE'
               )
               OR pg_catalog.has_schema_privilege(
                   projector_oid,
                   schema_row.oid,
                   'CREATE'
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = relation.relnamespace
         WHERE schema_row.nspname <> 'public'
           AND schema_row.nspname <> 'information_schema'
           AND schema_row.nspname !~ '^pg_'
           AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND (
               relation.relowner = projector_oid
               OR pg_catalog.has_table_privilege(
                   projector_oid, relation.oid, 'SELECT'
               )
               OR pg_catalog.has_table_privilege(
                   projector_oid, relation.oid, 'INSERT'
               )
               OR pg_catalog.has_table_privilege(
                   projector_oid, relation.oid, 'UPDATE'
               )
               OR pg_catalog.has_table_privilege(
                   projector_oid, relation.oid, 'DELETE'
               )
               OR pg_catalog.has_table_privilege(
                   projector_oid, relation.oid, 'TRUNCATE'
               )
               OR pg_catalog.has_table_privilege(
                   projector_oid, relation.oid, 'REFERENCES'
               )
               OR pg_catalog.has_table_privilege(
                   projector_oid, relation.oid, 'TRIGGER'
               )
               OR pg_catalog.has_any_column_privilege(
                   projector_oid, relation.oid, 'SELECT'
               )
               OR pg_catalog.has_any_column_privilege(
                   projector_oid, relation.oid, 'INSERT'
               )
               OR pg_catalog.has_any_column_privilege(
                   projector_oid, relation.oid, 'UPDATE'
               )
               OR pg_catalog.has_any_column_privilege(
                   projector_oid, relation.oid, 'REFERENCES'
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS sequence_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = sequence_row.relnamespace
         WHERE schema_row.nspname <> 'public'
           AND schema_row.nspname <> 'information_schema'
           AND schema_row.nspname !~ '^pg_'
           AND sequence_row.relkind = 'S'
           AND (
               sequence_row.relowner = projector_oid
               OR pg_catalog.has_sequence_privilege(
                   projector_oid, sequence_row.oid, 'USAGE'
               )
               OR pg_catalog.has_sequence_privilege(
                   projector_oid, sequence_row.oid, 'SELECT'
               )
               OR pg_catalog.has_sequence_privilege(
                   projector_oid, sequence_row.oid, 'UPDATE'
               )
           )
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
         WHERE schema_row.nspname <> 'public'
           AND schema_row.nspname <> 'information_schema'
           AND schema_row.nspname !~ '^pg_'
           AND (
               function_row.proowner = projector_oid
               OR pg_catalog.has_function_privilege(
                   projector_oid, function_row.oid, 'EXECUTE'
               )
           )
    ) THEN
        RAISE EXCEPTION
            '0043 projector cross-schema boundary is not closed';
    END IF;
END
$$
"""
    )


def _apply_projector_acl() -> None:
    read_tables = ", ".join(f"public.{name}" for name in READ_TABLES)
    _revoke_projector_public_object_acl()
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {PROJECTOR_ROLE}")
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {PROJECTOR_ROLE}")
    op.execute(f"GRANT SELECT ON TABLE {read_tables} TO {PROJECTOR_ROLE}")
    for table_name, column_names in INSERT_COLUMNS.items():
        columns = ", ".join(column_names)
        op.execute(
            f"GRANT INSERT ({columns}) ON TABLE public.{table_name} "
            f"TO {PROJECTOR_ROLE}"
        )
    for table_name, column_names in UPDATE_COLUMNS.items():
        columns = ", ".join(column_names)
        op.execute(
            f"GRANT UPDATE ({columns}) ON TABLE public.{table_name} "
            f"TO {PROJECTOR_ROLE}"
        )


def _revoke_projector_public_object_acl() -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        f"FROM {PROJECTOR_ROLE}"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC"
    )
    # PostgreSQL table-level REVOKE does not remove historical column ACLs.
    # Enumerate the direct attacl residue before granting the reviewed matrix.
    op.execute(
        f"""
DO $$
DECLARE
    column_acl record;
BEGIN
    FOR column_acl IN
        SELECT
            schema_row.nspname AS schema_name,
            relation.relname AS relation_name,
            attribute_row.attname AS column_name,
            acl.privilege_type,
            acl.grantee
          FROM pg_catalog.pg_attribute AS attribute_row
          JOIN pg_catalog.pg_class AS relation
            ON relation.oid = attribute_row.attrelid
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = relation.relnamespace
         CROSS JOIN LATERAL pg_catalog.aclexplode(attribute_row.attacl) AS acl
         WHERE schema_row.nspname = 'public'
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
           AND acl.grantee IN (
               0,
               (SELECT oid
                  FROM pg_catalog.pg_roles
                 WHERE rolname = '{PROJECTOR_ROLE}')
           )
         ORDER BY relation.relname, attribute_row.attnum, acl.privilege_type
    LOOP
        IF column_acl.grantee = 0 THEN
            EXECUTE pg_catalog.format(
                'REVOKE %s (%I) ON TABLE %I.%I FROM PUBLIC',
                column_acl.privilege_type,
                column_acl.column_name,
                column_acl.schema_name,
                column_acl.relation_name
            );
        ELSE
            EXECUTE pg_catalog.format(
                'REVOKE %s (%I) ON TABLE %I.%I FROM %I',
                column_acl.privilege_type,
                column_acl.column_name,
                column_acl.schema_name,
                column_acl.relation_name,
                '{PROJECTOR_ROLE}'
            );
        END IF;
    END LOOP;
END
$$
"""
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        f"FROM {PROJECTOR_ROLE}"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public "
        f"FROM {PROJECTOR_ROLE}"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC"
    )
    op.execute(
        f"""
DO $$
DECLARE
    large_object_acl record;
    parameter_acl record;
BEGIN
    FOR large_object_acl IN
        SELECT DISTINCT large_object.oid, acl.grantee
          FROM pg_catalog.pg_largeobject_metadata AS large_object
         CROSS JOIN LATERAL
              pg_catalog.aclexplode(large_object.lomacl) AS acl
         WHERE acl.grantee IN (
             0,
             (SELECT oid
                FROM pg_catalog.pg_roles
               WHERE rolname = '{PROJECTOR_ROLE}')
         )
         ORDER BY large_object.oid, acl.grantee
    LOOP
        IF large_object_acl.grantee = 0 THEN
            EXECUTE pg_catalog.format(
                'REVOKE ALL PRIVILEGES ON LARGE OBJECT %s FROM PUBLIC',
                large_object_acl.oid
            );
        ELSE
            EXECUTE pg_catalog.format(
                'REVOKE ALL PRIVILEGES ON LARGE OBJECT %s FROM %I',
                large_object_acl.oid,
                '{PROJECTOR_ROLE}'
            );
        END IF;
    END LOOP;

    FOR parameter_acl IN
        SELECT DISTINCT parameter_row.parname, acl.grantee
          FROM pg_catalog.pg_parameter_acl AS parameter_row
         CROSS JOIN LATERAL
              pg_catalog.aclexplode(parameter_row.paracl) AS acl
         WHERE acl.grantee IN (
             0,
             (SELECT oid
                FROM pg_catalog.pg_roles
               WHERE rolname = '{PROJECTOR_ROLE}')
         )
         ORDER BY parameter_row.parname, acl.grantee
    LOOP
        IF parameter_acl.grantee = 0 THEN
            EXECUTE pg_catalog.format(
                'REVOKE ALL PRIVILEGES ON PARAMETER %I FROM PUBLIC',
                parameter_acl.parname
            );
        ELSE
            EXECUTE pg_catalog.format(
                'REVOKE ALL PRIVILEGES ON PARAMETER %I FROM %I',
                parameter_acl.parname,
                '{PROJECTOR_ROLE}'
            );
        END IF;
    END LOOP;
END
$$
"""
    )


def _require_external_object_acl_boundary() -> None:
    op.execute(
        f"""
DO $$
DECLARE
    projector_oid oid;
BEGIN
    SELECT oid INTO STRICT projector_oid
      FROM pg_catalog.pg_roles
     WHERE rolname = '{PROJECTOR_ROLE}';

    IF EXISTS (
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
    ) OR EXISTS (
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
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_class AS relation
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = relation.relnamespace
         CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) AS acl
         WHERE schema_row.nspname = 'public'
           AND acl.grantee = 0
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_attribute AS attribute_row
          JOIN pg_catalog.pg_class AS relation
            ON relation.oid = attribute_row.attrelid
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = relation.relnamespace
         CROSS JOIN LATERAL
              pg_catalog.aclexplode(attribute_row.attacl) AS acl
         WHERE schema_row.nspname = 'public'
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
           AND acl.grantee = 0
    ) OR EXISTS (
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
    ) THEN
        RAISE EXCEPTION
            '0043 PUBLIC ACL boundary is not closed';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_largeobject_metadata AS large_object
         WHERE large_object.lomowner = projector_oid
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
                 WHERE acl.grantee IN (0, projector_oid)
            )
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_parameter_acl AS parameter_row
         CROSS JOIN LATERAL
              pg_catalog.aclexplode(parameter_row.paracl) AS acl
         WHERE acl.grantee IN (0, projector_oid)
    ) THEN
        RAISE EXCEPTION
            '0043 large-object or parameter ACL boundary is not closed';
    END IF;
END
$$
"""
    )
