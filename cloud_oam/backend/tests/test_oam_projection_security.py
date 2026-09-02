from __future__ import annotations

from app import database_security as api_security
from app import oam_projection_security as security


def _role_row(**overrides):
    row = {
        "current_role": "star_oam_projector",
        "is_superuser": False,
        "can_create_database": False,
        "can_create_role": False,
        "can_replicate": False,
        "can_bypass_rls": False,
        "can_login": True,
        "database_owner": "star_oam_migrator",
        "can_create_in_database": False,
        "can_create_temporary": False,
        "has_database_grant_option": False,
        "has_public_database_acl": False,
        "has_cross_database_connect": False,
        "schema_owner": "star_oam_migrator",
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
    row.update(overrides)
    return row


def _table_row(name: str, **overrides):
    readable = name in security.PROJECTOR_READ_TABLES
    insertable = name in security.PROJECTOR_INSERT_COLUMNS
    updatable = name in security.PROJECTOR_UPDATE_COLUMNS
    row = {
        "table_name": name,
        "owner_name": "star_oam_migrator",
        "can_select": readable,
        "can_insert": False,
        "can_update": False,
        "can_delete": False,
        "can_truncate": False,
        "can_reference": False,
        "can_trigger": False,
        "can_select_any_column": readable,
        "can_insert_any_column": insertable,
        "can_update_any_column": updatable,
        "can_reference_any_column": False,
        "has_grant_option": False,
        "has_public_table_acl": False,
    }
    row.update(overrides)
    return row


def _complete_table_rows():
    return [
        _table_row(name)
        for name in sorted(
            security.PROJECTOR_READ_TABLES | security.PROJECTOR_WRITE_TABLES
        )
    ]


def _exact_column_rows():
    return [
        {
            "table_name": table_name,
            "column_name": column_name,
            "grantee_name": "star_oam_projector",
            "privilege_type": "INSERT",
            "is_grantable": False,
        }
        for table_name, column_names in security.PROJECTOR_INSERT_COLUMNS.items()
        for column_name in column_names
    ] + [
        {
            "table_name": table_name,
            "column_name": column_name,
            "grantee_name": "star_oam_projector",
            "privilege_type": "UPDATE",
            "is_grantable": False,
        }
        for table_name, column_names in security.PROJECTOR_UPDATE_COLUMNS.items()
        for column_name in column_names
    ]


def test_exact_projector_role_and_table_closure_is_accepted():
    failures: list[str] = []

    security._assert_role(
        _role_row(),
        expected_role="star_oam_projector",
        expected_migration_role="star_oam_migrator",
        failures=failures,
    )
    security._assert_tables(
        _complete_table_rows(),
        expected_migration_role="star_oam_migrator",
        failures=failures,
    )
    security._assert_columns(
        _exact_column_rows(),
        expected_role="star_oam_projector",
        failures=failures,
    )

    assert failures == []


def test_role_rejects_temporary_objects_and_role_inheritance():
    failures: list[str] = []

    security._assert_role(
        _role_row(
            can_create_temporary=True,
            is_member_of_other_role=True,
        ),
        expected_role="star_oam_projector",
        expected_migration_role="star_oam_migrator",
        failures=failures,
    )

    assert "role.can_create_temporary" in failures
    assert "role.is_member_of_other_role" in failures


def test_role_rejects_search_path_grant_options_and_non_public_schema():
    failures: list[str] = []

    security._assert_role(
        _role_row(
            current_schema_path=["rogue", "public"],
            has_database_grant_option=True,
            has_public_database_acl=True,
            has_cross_database_connect=True,
            has_schema_grant_option=True,
            has_public_schema_acl=True,
            has_non_system_schema_access=True,
            can_disable_replication_guards=True,
        ),
        expected_role="star_oam_projector",
        expected_migration_role="star_oam_migrator",
        failures=failures,
    )

    assert "role.current_schema_path" in failures
    assert "role.has_database_grant_option" in failures
    assert "role.has_public_database_acl" in failures
    assert "role.has_cross_database_connect" in failures
    assert "role.has_schema_grant_option" in failures
    assert "role.has_public_schema_acl" in failures
    assert "role.has_non_system_schema_access" in failures
    assert "role.can_disable_replication_guards" in failures


def test_column_level_privilege_drift_is_rejected_even_without_table_grant():
    rows = _complete_table_rows()
    rows.append(
        _table_row(
            "material_requests",
            can_select_any_column=True,
            can_update_any_column=True,
        )
    )
    failures: list[str] = []

    security._assert_tables(
        rows,
        expected_migration_role="star_oam_migrator",
        failures=failures,
    )

    assert "tables.material_requests.select_any_column" in failures
    assert "tables.material_requests.update_any_column" in failures


def test_missing_required_table_and_grant_option_are_rejected():
    rows = _complete_table_rows()
    missing = rows.pop()
    rows[0]["has_grant_option"] = True
    failures: list[str] = []

    security._assert_tables(
        rows,
        expected_migration_role="star_oam_migrator",
        failures=failures,
    )

    assert f"tables.{missing['table_name']}.missing" in failures
    assert any(value.endswith(".grant_option") for value in failures)


def test_public_table_acl_is_rejected_even_when_effective_matrix_matches():
    rows = _complete_table_rows()
    rows[0]["has_public_table_acl"] = True
    failures: list[str] = []

    security._assert_tables(
        rows,
        expected_migration_role="star_oam_migrator",
        failures=failures,
    )

    assert any(value.endswith(".public_acl") for value in failures)


def test_column_acl_must_match_exact_mutable_field_matrix():
    rows = _exact_column_rows()
    rows.pop()
    rows.append(
        {
            "table_name": "external_object_versions",
            "column_name": "payload_jsonb",
            "grantee_name": "star_oam_projector",
            "privilege_type": "UPDATE",
            "is_grantable": False,
        }
    )
    failures: list[str] = []

    security._assert_columns(
        rows,
        expected_role="star_oam_projector",
        failures=failures,
    )

    assert "columns.external_object_versions.payload_jsonb.excess" in failures
    assert any(value.endswith(".missing") for value in failures)


def test_runtime_security_sql_uses_explicit_catalog_and_closes_cluster_objects():
    sql = "\n".join(
        str(statement)
        for statement in (
            security._ROLE_SQL,
            security._TABLE_SQL,
            security._COLUMN_ACL_SQL,
            security._OBJECT_CLOSURE_SQL,
        )
    )

    for required in (
        "pg_catalog.pg_roles",
        "pg_catalog.pg_database",
        "pg_catalog.pg_namespace",
        "pg_catalog.pg_largeobject_metadata",
        "pg_catalog.pg_parameter_acl",
        "pg_catalog.aclexplode",
        "pg_catalog.current_schemas",
        "has_cross_database_connect",
        "accessible_large_object_count",
        "accessible_parameter_acl_count",
    ):
        assert required in sql


def test_main_api_column_acl_query_excludes_other_isolated_principals():
    sql = " ".join(str(api_security._COLUMN_ACL_SQL).split())

    assert "JOIN pg_roles AS role_row" in sql
    assert "role_row.rolname = current_user" in sql
    assert "column_acl.grantee IN (0, role_row.oid)" in sql
