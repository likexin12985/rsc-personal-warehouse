"""Runtime proof for the PostgreSQL boundary used by receipt projection."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .oam_projection_security import (
    MIGRATION_ROLE,
    PROJECTOR_ROLE,
    verify_oam_projection_database_boundary,
)


class OamReceiptProjectionDatabaseBoundaryError(RuntimeError):
    """Raised when the receipt worker's exact ACL/RLS graph is not installed."""


_TABLE_SQL = text(
    """
    SELECT class_row.relname AS table_name,
           class_row.relforcerowsecurity AS force_rls,
           pg_catalog.has_table_privilege(:role_name, class_row.oid, 'SELECT') AS can_select,
           pg_catalog.has_table_privilege(:role_name, class_row.oid, 'INSERT') AS can_insert,
           pg_catalog.has_table_privilege(:role_name, class_row.oid, 'UPDATE') AS can_update,
           pg_catalog.has_table_privilege(:role_name, class_row.oid, 'DELETE') AS can_delete
      FROM pg_catalog.pg_class class_row
      JOIN pg_catalog.pg_namespace namespace_row
        ON namespace_row.oid = class_row.relnamespace
     WHERE namespace_row.nspname = 'public'
       AND class_row.relname IN ('shipments', 'oam_receipt_evidence')
     ORDER BY class_row.relname
    """
)

_COLUMN_SQL = text(
    """
    SELECT table_row.relname AS table_name,
           column_row.attname AS column_name,
           pg_catalog.has_column_privilege(
               :role_name, table_row.oid, column_row.attname, 'INSERT'
           ) AS can_insert
      FROM pg_catalog.pg_class table_row
      JOIN pg_catalog.pg_namespace namespace_row
        ON namespace_row.oid = table_row.relnamespace
      JOIN pg_catalog.pg_attribute column_row
        ON column_row.attrelid = table_row.oid
       AND column_row.attnum > 0
       AND NOT column_row.attisdropped
     WHERE namespace_row.nspname = 'public'
       AND table_row.relname = 'oam_receipt_evidence'
     ORDER BY column_row.attnum
    """
)

_POLICY_SQL = text(
    """
    SELECT policy.polname AS policy_name,
           table_row.relname AS table_name,
           policy.polcmd AS command_code,
           pg_catalog.pg_get_expr(policy.polqual, policy.polrelid) AS using_expression,
           pg_catalog.pg_get_expr(policy.polwithcheck, policy.polrelid) AS check_expression
      FROM pg_catalog.pg_policy policy
      JOIN pg_catalog.pg_class table_row ON table_row.oid = policy.polrelid
      JOIN pg_catalog.pg_namespace namespace_row
        ON namespace_row.oid = table_row.relnamespace
      JOIN pg_catalog.pg_roles role_row
        ON role_row.oid = ANY(policy.polroles)
     WHERE namespace_row.nspname = 'public'
       AND role_row.rolname = :role_name
       AND policy.polname IN (
           'external_sync_snapshots_projector_select_receipt_0082',
           'external_sync_current_records_projector_select_receipt_0082',
           'source_systems_projector_select_receipt_0082',
           'sync_runs_projector_select_receipt_0082',
           'sync_runs_projector_insert_receipt_0082',
           'external_objects_projector_select_receipt_0082',
           'external_object_mappings_projector_select_receipt_0082',
           'shipments_projector_select_receipt_0082',
           'oam_receipt_evidence_projector_select_receipt_0082',
           'oam_receipt_evidence_projector_insert_receipt_0082'
       )
    """
)

_FUNCTION_SQL = text(
    """
    SELECT function_row.proowner = migration_role.oid AS owned_by_migration,
           function_row.prosecdef AS security_definer,
           pg_catalog.has_function_privilege(
               :role_name, function_row.oid, 'EXECUTE'
           ) AS projector_can_execute,
           EXISTS (
               SELECT 1
                 FROM pg_catalog.aclexplode(
                     COALESCE(function_row.proacl, pg_catalog.acldefault('f', function_row.proowner))
                 ) acl
                WHERE acl.grantee = 0
                   OR acl.grantee = projector_role.oid
           ) AS public_or_projector_acl
      FROM pg_catalog.pg_proc function_row
      JOIN pg_catalog.pg_namespace namespace_row
        ON namespace_row.oid = function_row.pronamespace
      JOIN pg_catalog.pg_roles migration_role
        ON migration_role.rolname = :migration_role
      JOIN pg_catalog.pg_roles projector_role
        ON projector_role.rolname = :role_name
     WHERE namespace_row.nspname = 'public'
       AND function_row.proname = 'rsc_oam_receipt_rls_check_0082'
       AND pg_catalog.pg_get_function_identity_arguments(function_row.oid)
           = 'p_operation text, p_required_capability text, p_table_name text, p_row jsonb'
    """
)

_BINDING_SQL = text(
    """
    SELECT table_row.relname = 'oam_receipt_sync_scope_bindings' AS table_exists,
           pg_catalog.has_table_privilege(
               :migration_role, table_row.oid, 'SELECT'
           ) AS migration_can_select,
           EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_constraint constraint_row
                WHERE constraint_row.conrelid = table_row.oid
                  AND constraint_row.conname = 'ck_oam_receipt_binding_scope_0082'
           ) AS scope_constraint,
           EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_constraint constraint_row
                WHERE constraint_row.conrelid = table_row.oid
                  AND constraint_row.conname = 'uq_oam_receipt_binding_coordinate_0082'
           ) AS coordinate_unique
      FROM pg_catalog.pg_class table_row
      JOIN pg_catalog.pg_namespace namespace_row
        ON namespace_row.oid = table_row.relnamespace
     WHERE namespace_row.nspname = 'public'
       AND table_row.relname = 'oam_receipt_sync_scope_bindings'
    """
)


def _bool(row: Mapping[str, object], field: str) -> bool:
    return row.get(field) is True


def verify_oam_receipt_projection_database_boundary(
    engine: Engine,
    *,
    expected_role: str = PROJECTOR_ROLE,
    expected_migration_role: str = MIGRATION_ROLE,
) -> None:
    """Prove the receipt-specific ACL, FORCE RLS, binding and policy graph."""

    if engine.dialect.name != "postgresql":
        return
    verify_oam_projection_database_boundary(
        engine,
        expected_role=expected_role,
        expected_migration_role=expected_migration_role,
    )
    with engine.connect() as connection:
        tables = connection.execute(_TABLE_SQL, {"role_name": expected_role}).mappings().all()
        columns = connection.execute(_COLUMN_SQL, {"role_name": expected_role}).mappings().all()
        policies = connection.execute(
            _POLICY_SQL, {"role_name": expected_role}
        ).mappings().all()
        function = connection.execute(
            _FUNCTION_SQL,
            {"role_name": expected_role, "migration_role": expected_migration_role},
        ).mappings().one_or_none()
        binding = connection.execute(
            _BINDING_SQL, {"migration_role": expected_migration_role}
        ).mappings().one_or_none()

    failures: list[str] = []
    table_map = {row.get("table_name"): row for row in tables}
    for table_name in ("shipments", "oam_receipt_evidence"):
        row = table_map.get(table_name)
        if row is None:
            failures.append(f"tables.{table_name}.missing")
            continue
        if not _bool(row, "force_rls"):
            failures.append(f"tables.{table_name}.force_rls")
        if not _bool(row, "can_select"):
            failures.append(f"tables.{table_name}.select")
        if table_name == "shipments" and _bool(row, "can_insert"):
            failures.append("tables.shipments.insert_excess")
        if table_name == "oam_receipt_evidence" and _bool(row, "can_update"):
            failures.append("tables.oam_receipt_evidence.update_excess")
        if _bool(row, "can_delete"):
            failures.append(f"tables.{table_name}.delete_excess")

    expected_insert_columns = {
        "id",
        "external_object_id",
        "shipment_id",
        "status",
        "source_time",
        "source_version",
        "payload_sha256",
        "created_at",
    }
    for row in columns:
        name = row.get("column_name")
        actual = _bool(row, "can_insert")
        if name in expected_insert_columns and not actual:
            failures.append(f"columns.{name}.insert_missing")
        if name not in expected_insert_columns and actual:
            failures.append(f"columns.{name}.insert_excess")

    expected_policies = {
        "external_sync_snapshots_projector_select_receipt_0082",
        "external_sync_current_records_projector_select_receipt_0082",
        "source_systems_projector_select_receipt_0082",
        "sync_runs_projector_select_receipt_0082",
        "sync_runs_projector_insert_receipt_0082",
        "external_objects_projector_select_receipt_0082",
        "external_object_mappings_projector_select_receipt_0082",
        "shipments_projector_select_receipt_0082",
        "oam_receipt_evidence_projector_select_receipt_0082",
        "oam_receipt_evidence_projector_insert_receipt_0082",
    }
    actual_policies = {row.get("policy_name") for row in policies}
    for policy in sorted(expected_policies - actual_policies):
        failures.append(f"policies.{policy}.missing")
    if function is None:
        failures.append("function.rsc_oam_receipt_rls_check_0082.missing")
    else:
        for field in ("owned_by_migration", "security_definer"):
            if not _bool(function, field):
                failures.append(f"function.rsc_oam_receipt_rls_check_0082.{field}")
        if _bool(function, "projector_can_execute") or _bool(
            function, "public_or_projector_acl"
        ):
            failures.append("function.rsc_oam_receipt_rls_check_0082.execute_excess")
    if binding is None:
        failures.append("bindings.table.missing")
    else:
        for field in ("table_exists", "migration_can_select", "scope_constraint", "coordinate_unique"):
            if not _bool(binding, field):
                failures.append(f"bindings.{field}")
    if failures:
        raise OamReceiptProjectionDatabaseBoundaryError(
            "OAM receipt projector database boundary failed: "
            + ", ".join(sorted(set(failures)))
        )


__all__ = [
    "OamReceiptProjectionDatabaseBoundaryError",
    "verify_oam_receipt_projection_database_boundary",
]
