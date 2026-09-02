from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re
import runpy

from app import database_security as api_security
from app import oam_projection_security as security
from app import oam_sync_scope_security as scope_security


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


def test_0044_scope_security_has_exact_force_rls_policy_closure():
    policies = scope_security.EXPECTED_POLICIES
    identities = {(row[0], row[1]) for row in policies}
    policy_by_identity = {(row[0], row[1]): row for row in policies}

    assert len(identities) == len(policies)
    assert len(policies) == 84
    assert set(scope_security.RLS_TABLES) == {
        "oam_sync_scope_bindings",
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
        "audit_logs",
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
    }
    assert (
        "oam_sync_scope_bindings",
        "oam_sync_scope_bindings_projector_select_0044",
    ) not in identities
    assert (
        "oam_sync_scope_bindings",
        "oam_sync_scope_bindings_edge_select_0044",
    ) not in identities
    assert policy_by_identity[
        (
            "external_sync_current_records",
            "external_sync_current_records_edge_update_0044",
        )
    ][4:] == (
        scope_security._runtime_policy_expression(
            "external_sync_current_records", "select"
        ),
        scope_security._runtime_policy_expression(
            "external_sync_current_records", "insert"
        ),
    )
    for table_name in (
        "external_objects",
        "external_object_versions",
        "oam_work_orders",
    ):
        assert policy_by_identity[
            (table_name, f"{table_name}_projector_update_0044")
        ][4:] == (
            scope_security._runtime_policy_expression(
                table_name, "update_old"
            ),
            scope_security._runtime_policy_expression(
                table_name, "update_new"
            ),
        )

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260902_0044_oam_sync_scope_force_rls.py"
    )
    migration_namespace = runpy.run_path(str(migration_path))
    assert migration_namespace["EXPECTED_POLICY_ROSTER"] == policies

    sql = str(scope_security._RLS_BOUNDARY_SQL)
    for required in (
        "relrowsecurity",
        "relforcerowsecurity",
        "pg_catalog.pg_policy",
        "policy_closure",
        "rsc_oam_rls_check_0044",
        "rsc_oam_runtime_binding_ready_0044",
        "session_user = :runtime_role",
        "current_setting('row_security') = 'on'",
        "oam_sync_scope_bindings",
        "has_any_column_privilege",
        "search_path=pg_catalog",
        "function_acl.grantee = 0",
        "CASE WHEN expected.is_private THEN 1 ELSE 3 END",
        "IS DISTINCT FROM expected.using_expression",
        "IS DISTINCT FROM expected.check_expression",
        "source_sha256",
        "function_roster_boundary",
        "runtime_execute_boundary",
        "revision_and_binding_boundary",
        "trigger_boundary",
        "tgdeferrable",
        "tginitdeferred",
        "tgisinternal",
        "tgfoid",
    ):
        assert required in sql
    normalized_sql = " ".join(sql.split())
    assert " NOT LIKE " not in sql
    assert " OR true" not in sql
    assert (
        "IS DISTINCT FROM (function_acl.grantee = function_row.proowner)"
        not in normalized_sql
    )
    assert "function_acl.grantee <> function_row.proowner" in sql

    for policy in policies:
        for expression in policy[4:]:
            if expression is not None and expression != "true":
                assert expression.startswith("rsc_oam_rls_check_0044(")
                assert " OR " not in expression


def test_0044_scope_function_manifest_matches_migration_bodies_exactly():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260902_0044_oam_sync_scope_force_rls.py"
    )
    tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    actual_manifest: dict[str, tuple[object, ...]] = {}
    actual_sources: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            function_sql = ast.literal_eval(node.value)
        except (TypeError, ValueError):
            continue
        if not isinstance(function_sql, str):
            continue
        match = re.search(
            r"CREATE FUNCTION public\.(rsc_oam_[a-z0-9_]+_0044)\s*"
            r"\((.*?)\)\s*RETURNS\s+([a-z_ ]+?)\s*\n(.*?)\nAS \$\$",
            function_sql,
            re.DOTALL,
        )
        if match is None:
            continue
        body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        function_name = match.group(1)
        argument_types = [
            declaration.strip().split(maxsplit=1)[1]
            for declaration in match.group(2).split(",")
            if declaration.strip()
        ]
        signature = f"{function_name}({','.join(argument_types)})"
        result_type = match.group(3).strip()
        attributes = " ".join(match.group(4).split())
        language_match = re.search(r"LANGUAGE ([a-z]+)", attributes)
        assert language_match is not None
        volatility = next(
            code
            for keyword, code in (
                ("IMMUTABLE", "i"),
                ("STABLE", "s"),
                ("VOLATILE", "v"),
            )
            if keyword in attributes
        )
        parallel_safety = (
            "s"
            if "PARALLEL SAFE" in attributes
            else "r"
            if "PARALLEL RESTRICTED" in attributes
            else "u"
        )
        source_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        actual_sources[function_name] = body
        actual_manifest[signature] = (
            function_name
            not in {
                "rsc_oam_rls_check_0044",
                "rsc_oam_runtime_binding_ready_0044",
            },
            volatility,
            language_match.group(1),
            result_type,
            "STRICT" in attributes,
            parallel_safety,
            source_hash,
        )
        assert "SECURITY DEFINER" in attributes
        assert "SET search_path = pg_catalog" in attributes

    assert len(scope_security.OAM_SYNC_FUNCTION_MANIFEST) == 14
    assert actual_manifest == scope_security.OAM_SYNC_FUNCTION_MANIFEST
    ready_body = actual_sources["rsc_oam_runtime_binding_ready_0044"]
    assert "FROM public.alembic_version" in ready_body
    assert "pg_catalog.count(*) = 1" in ready_body
    assert "pg_catalog.min(version_num) = '20260902_0044'" in ready_body

    external_object_body = " ".join(
        actual_sources["rsc_oam_external_object_allowed_0044"].split()
    )
    for required in (
        "p_operation_name IN ('select', 'update_old')",
        "p_current_version_id IS NULL OR EXISTS",
        "version.id = p_current_version_id",
        "version.external_object_id = p_object_id",
        "NOT version.is_current AND version.valid_to IS NOT NULL",
        "p_operation_name = 'update_new'",
        "p_current_version_id IS NOT NULL",
        "version.is_current AND version.valid_to IS NULL",
    ):
        assert required in external_object_body

    work_order_body = " ".join(
        actual_sources["rsc_oam_work_order_allowed_0044"].split()
    )
    for required in (
        "p_operation_name IN ('select', 'update_old')",
        "NOT version.is_current AND version.valid_to IS NOT NULL",
        "p_operation_name IN ('insert', 'update_new')",
        "version.id = external.current_version_id",
        "version.is_current AND version.valid_to IS NULL",
    ):
        assert required in work_order_body

    version_body = " ".join(
        actual_sources["rsc_oam_version_allowed_0044"].split()
    )
    for required in (
        "p_created_at = p_valid_from",
        "WHEN 'update_old' THEN p_is_current AND p_valid_to IS NULL",
        "WHEN 'update_new' THEN NOT p_is_current AND p_valid_to IS NOT NULL",
        "p_valid_to > p_valid_from",
    ):
        assert required in version_body

    chain_body = " ".join(
        actual_sources["rsc_oam_projection_chain_guard_0044"].split()
    )
    for required in (
        "current_version_count <> 1",
        "current_version_id IS DISTINCT FROM checked_pointer_id",
        "version.created_at IS DISTINCT FROM version.valid_from",
        "successor.valid_from = version.valid_to",
        "successor.source_updated_at < version.source_updated_at",
        "predecessor.valid_to = version.valid_from",
        "count(DISTINCT version.valid_from)",
        "work-order version history is invalid",
    ):
        assert required in chain_body


def test_0044_scope_trigger_manifest_is_exact():
    assert scope_security.EXPECTED_TRIGGERS == (
        (
            "external_sync_snapshots",
            "trg_external_sync_snapshot_transition_0044",
            "rsc_oam_snapshot_transition_guard_0044()",
            19,
            False,
            False,
            False,
        ),
        (
            "external_objects",
            "trg_external_objects_chain_0044",
            "rsc_oam_projection_chain_guard_0044()",
            21,
            True,
            True,
            True,
        ),
        (
            "external_object_versions",
            "trg_external_object_versions_chain_0044",
            "rsc_oam_projection_chain_guard_0044()",
            21,
            True,
            True,
            True,
        ),
    )
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260902_0044_oam_sync_scope_force_rls.py"
    )
    migration_tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    expected_ddl = {
        (
            "CREATE TRIGGER trg_external_sync_snapshot_transition_0044 "
            "BEFORE UPDATE ON public.external_sync_snapshots FOR EACH ROW "
            "EXECUTE FUNCTION public.rsc_oam_snapshot_transition_guard_0044()"
        ),
        (
            "CREATE CONSTRAINT TRIGGER trg_external_objects_chain_0044 "
            "AFTER INSERT OR UPDATE ON public.external_objects DEFERRABLE "
            "INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            "public.rsc_oam_projection_chain_guard_0044()"
        ),
        (
            "CREATE CONSTRAINT TRIGGER trg_external_object_versions_chain_0044 "
            "AFTER INSERT OR UPDATE ON public.external_object_versions "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            "public.rsc_oam_projection_chain_guard_0044()"
        ),
    }
    actual_ddl = {
        " ".join(node.value.split())
        for node in ast.walk(migration_tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.match(r"CREATE (?:CONSTRAINT )?TRIGGER trg_.*?_0044", node.value)
    }
    assert actual_ddl == expected_ddl


def test_0044_scope_security_requires_strict_positive_evidence():
    assert scope_security.oam_sync_scope_boundary_passed(
        {"boundary_ok": True, "boundary_failures": ""}
    ) is True
    for row in (
        None,
        {"boundary_ok": False, "boundary_failures": "policy_closure"},
        {"boundary_ok": True, "boundary_failures": "session_binding"},
        {"boundary_ok": 1, "boundary_failures": ""},
    ):
        assert scope_security.oam_sync_scope_boundary_passed(row) is False


def test_main_api_column_acl_query_excludes_other_isolated_principals():
    sql = " ".join(str(api_security._COLUMN_ACL_SQL).split())

    assert "FROM pg_catalog.pg_class AS class_row" in sql
    assert "JOIN pg_catalog.pg_namespace AS namespace_row" in sql
    assert "JOIN pg_catalog.pg_attribute AS attribute_row" in sql
    assert "JOIN pg_catalog.pg_roles AS role_row" in sql
    assert "pg_catalog.aclexplode(attribute_row.attacl)" in sql
    assert "role_row.rolname = current_user" in sql
    assert "column_acl.grantee IN (0, role_row.oid)" in sql
