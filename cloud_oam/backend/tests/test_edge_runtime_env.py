import importlib.util
import re
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deployment" / "build_edge_runtime_env.py"
EDGE_GRANTS = ROOT / "deployment" / "create_oam_edge_staging.sql"
EDGE_VERIFY = ROOT / "deployment" / "verify_oam_edge_staging.sql"
EDGE_ROLE_PROVISION = ROOT / "deployment" / "provision_edge_receiver_role.sql"
SOURCE_PROVISION = ROOT / "deployment" / "provision_oam_work_order_source.sql"
MAIN_COMPOSE = ROOT / "docker-compose.yml"
EDGE_COMPOSE = ROOT / "deployment" / "docker-compose.edge.yml"
EDGE_ENV_EXAMPLE = ROOT / "deployment" / "edge-receiver.env.example"
ENV_EXAMPLE = ROOT / ".env.example"
SPEC = importlib.util.spec_from_file_location("build_edge_runtime_env", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_runtime_values_use_dedicated_edge_credentials_only(tmp_path):
    source = tmp_path / "edge.env"
    values = module.build_runtime_values(
        {
            "RSC_EDGE_DATABASE_ROLE": "edge_inbox",
            "RSC_EDGE_DATABASE_URL": (
                "postgresql+psycopg://edge_inbox:secret@db:5432/star_oam"
            ),
            "RSC_EDGE_SYNC_SECRET": "s" * 32,
            "RSC_EDGE_ALLOWED_SOURCES": "admin-mac,admin-mac,backup-mac",
            "OAM_JWT_SECRET": "must-not-be-forwarded",
            "POSTGRES_PASSWORD": "must-not-be-forwarded",
        },
        source,
    )

    assert values == {
        "OAM_DATABASE_URL": (
            "postgresql+psycopg://edge_inbox:secret@db:5432/star_oam"
        ),
        "OAM_DATABASE_EXPECTED_EDGE_ROLE": "edge_inbox",
        "OAM_EDGE_SYNC_SECRET": "s" * 32,
        "OAM_EDGE_SYNC_ALLOWED_SOURCES": "admin-mac,backup-mac",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"RSC_EDGE_DATABASE_ROLE": ""},
        {"RSC_EDGE_DATABASE_URL": "sqlite:///unsafe.db"},
        {"RSC_EDGE_DATABASE_ROLE": "star_oam_api"},
        {"RSC_EDGE_DATABASE_ROLE": "bad-role"},
        {"RSC_EDGE_DATABASE_ROLE": "different_edge_login"},
        {"RSC_EDGE_SYNC_SECRET": "short"},
        {"RSC_EDGE_ALLOWED_SOURCES": ""},
        {"RSC_EDGE_ALLOWED_SOURCES": "valid,bad source"},
        {"RSC_EDGE_SYNC_SECRET": "replace-with-a-separate-32-character-secret"},
        {
            "RSC_EDGE_DATABASE_URL": (
                "postgresql+psycopg://edge_inbox:replace-me@db:5432/star_oam"
            )
        },
    ],
)
def test_runtime_values_fail_closed_on_unsafe_configuration(tmp_path, overrides):
    raw = {
        "RSC_EDGE_DATABASE_ROLE": "edge_inbox",
        "RSC_EDGE_DATABASE_URL": (
            "postgresql+psycopg://edge_inbox:secret@db:5432/star_oam"
        ),
        "RSC_EDGE_SYNC_SECRET": "s" * 32,
        "RSC_EDGE_ALLOWED_SOURCES": "admin-mac",
    }
    raw.update(overrides)
    with pytest.raises(RuntimeError):
        module.build_runtime_values(raw, tmp_path / "edge.env")


def test_main_writes_private_runtime_env_without_jwt_or_main_db_credentials(
    tmp_path, monkeypatch
):
    source = tmp_path / "edge.env"
    output = tmp_path / "runtime.env"
    source.write_text(
        "RSC_EDGE_DATABASE_ROLE=edge_inbox\n"
        "RSC_EDGE_DATABASE_URL=postgresql+psycopg://edge_inbox:secret@db:5432/oam\n"
        f"RSC_EDGE_SYNC_SECRET={'s' * 32}\n"
        "RSC_EDGE_ALLOWED_SOURCES=admin-mac\n"
        "OAM_JWT_SECRET=do-not-copy\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), str(source), str(output)])

    assert module.main() == 0
    text = output.read_text(encoding="utf-8")
    assert "OAM_JWT_SECRET" not in text
    assert "POSTGRES_PASSWORD" not in text
    assert "OAM_DATABASE_EXPECTED_EDGE_ROLE=edge_inbox" in text
    assert "OAM_EDGE_SYNC_ALLOWED_SOURCES=admin-mac" in text
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_edge_database_setup_is_grants_only_and_excludes_business_tables():
    sql = EDGE_GRANTS.read_text(encoding="utf-8")
    normalized = " ".join(sql.upper().split())
    assert "CREATE TABLE" not in normalized
    assert "ALTER TABLE" not in normalized
    assert "ALL DDL IS OWNED BY ALEMBIC" in normalized
    for table in (
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
        "audit_logs",
    ):
        assert table in sql
    for revoke in (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA PUBLIC",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA PUBLIC",
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA PUBLIC",
        "REVOKE ALL ON SCHEMA PUBLIC",
        "REVOKE ALL ON DATABASE",
    ):
        assert revoke in normalized
    assert "INFORMATION_SCHEMA.COLUMN_PRIVILEGES" in normalized
    assert "GRANT USAGE ON SCHEMA PUBLIC" in normalized
    assert re.search(
        r"GRANT\s+SELECT,\s*INSERT\s+ON\s+TABLE\s+"
        r"PUBLIC\.EXTERNAL_SYNC_SNAPSHOT_BATCHES,\s*"
        r"PUBLIC\.EXTERNAL_SYNC_SNAPSHOT_RECORDS\s+TO",
        normalized,
    )
    assert re.search(
        r"GRANT\s+SELECT,\s*INSERT,\s*DELETE\s+ON\s+TABLE\s+"
        r"PUBLIC\.EXTERNAL_SYNC_CURRENT_RECORDS\s+TO",
        normalized,
    )
    assert "GRANT INSERT ON TABLE PUBLIC.AUDIT_LOGS" in normalized
    for immutable_table in (
        "PUBLIC.EXTERNAL_SYNC_SNAPSHOT_BATCHES",
        "PUBLIC.EXTERNAL_SYNC_SNAPSHOT_RECORDS",
    ):
        assert not re.search(
            rf"GRANT\s+[^;]*(?:UPDATE|DELETE)[^;]*{immutable_table}",
            normalized,
        )
    for forbidden_table in (
        "users",
        "inventory_balances",
        "source_systems",
        "sync_runs",
        "oam_work_orders",
    ):
        assert forbidden_table not in sql
    assert "pg_auth_members" in sql
    assert "rolsuper" in sql
    assert "rolbypassrls" in sql
    for protected_role in (
        "star_oam_migrator",
        "star_oam_api",
        "star_oam_backup",
        "star_oam_projector",
        "star_oam_edge",
    ):
        assert protected_role in sql
    assert "pg_catalog.pg_largeobject_metadata" in sql
    assert "pg_catalog.pg_parameter_acl" in sql


def test_edge_receiver_role_provisioning_reads_secret_only_from_environment():
    sql = EDGE_ROLE_PROVISION.read_text(encoding="utf-8")
    normalized = " ".join(sql.upper().split())

    assert "\\GETENV EDGE_PASSWORD OAM_DB_EDGE_RECEIVER_PASSWORD" in normalized
    assert "EDGE_ROLE MUST BE THE INDEPENDENTLY REVOCABLE EDGE_INBOX" in normalized
    assert "PG_CATALOG.PG_AUTH_MEMBERS" in normalized
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in normalized
    assert "NOREPLICATION NOBYPASSRLS" in normalized
    assert "REVOKE ALL ON DATABASE" in normalized
    assert "HAS_DATABASE_PRIVILEGE" in normalized
    assert "\\UNSET EDGE_PASSWORD" in normalized
    assert "--SET=EDGE_PASSWORD" not in normalized

    dollar_parts = sql.split("$$")
    assert len(dollar_parts) % 2 == 1
    for block in dollar_parts[1::2]:
        assert ":'edge_password'" not in block


def test_edge_receiver_role_cross_database_failure_rolls_back_role_changes():
    sql = EDGE_ROLE_PROVISION.read_text(encoding="utf-8")
    normalized = " ".join(sql.upper().split())

    begin = normalized.index("BEGIN;")
    create_role = normalized.index("'CREATE ROLE %I LOGIN PASSWORD %L")
    alter_role = normalized.index("'ALTER ROLE %I WITH LOGIN PASSWORD %L")
    revoke_databases = normalized.index("'REVOKE ALL ON DATABASE %I FROM %I'")
    cross_database = normalized.index(
        ") AS EDGE_CROSS_DATABASE_BOUNDARY_CLOSED"
    )
    commit = normalized.index("COMMIT;")
    assert begin < create_role < alter_role < revoke_databases < cross_database < commit

    assert "DATABASE_ROW.DATNAME <> PG_CATALOG.CURRENT_DATABASE()" in normalized
    assert "GRANT CONNECT" not in normalized
    failure = normalized.split(
        "\\IF :EDGE_CROSS_DATABASE_BOUNDARY_CLOSED", 1
    )[1].split("\\ENDIF", 1)[0]
    assert failure.index("ROLLBACK;") < failure.index("\\QUIT 4")
    assert failure.index("\\UNSET EDGE_PASSWORD") < failure.index("\\QUIT 4")


def test_edge_database_setup_limits_updates_to_reviewed_mutable_columns():
    sql = " ".join(EDGE_GRANTS.read_text(encoding="utf-8").upper().split())
    assert (
        "GRANT UPDATE ( STATUS, MANIFEST_JSON, MANIFEST_SHA256, COMPLETED_AT ) "
        "ON TABLE PUBLIC.EXTERNAL_SYNC_SNAPSHOTS"
    ) in sql
    assert (
        "GRANT UPDATE ( SOURCE_UPDATED_AT, PAYLOAD_JSON, PAYLOAD_SHA256, "
        "LAST_SNAPSHOT_ID, UPDATED_AT ) ON TABLE "
        "PUBLIC.EXTERNAL_SYNC_CURRENT_RECORDS"
    ) in sql
    assert not re.search(
        r"GRANT\s+[^;]*\bUPDATE\b\s+ON\s+TABLE\s+"
        r"PUBLIC\.EXTERNAL_SYNC_(?:SNAPSHOTS|CURRENT_RECORDS)",
        sql,
    )


def test_oam_source_provisioning_never_interpolates_psql_inside_dollar_quote():
    sql = SOURCE_PROVISION.read_text(encoding="utf-8")
    dollar_parts = sql.split("$$")
    assert len(dollar_parts) % 2 == 1
    for block in dollar_parts[1::2]:
        assert ":'" not in block
        assert ':"' not in block
    assert "CREATE TEMPORARY TABLE oam_work_order_source_input" in sql
    assert "FROM pg_temp.oam_work_order_source_input" in sql
    for variable in (
        "edge_source_instance",
        "company_id",
        "org_code",
        "scope_key",
    ):
        assert f":'{variable}'::text" in sql


def test_deployment_acl_verifier_covers_edge_and_projector_full_closure():
    sql = EDGE_VERIFY.read_text(encoding="utf-8")
    normalized = " ".join(sql.upper().split())
    assert "PROJECTOR_ROLE IS REQUIRED" in normalized
    assert "DEPLOYMENT_ROLES_PRESENT" in normalized
    assert "PUBLIC_TABLES AS" in normalized
    assert "PG_CATALOG.PG_ATTRIBUTE" in normalized
    assert "COLUMN_ROW.ATTACL" in normalized
    assert "'TEMPORARY'" in normalized
    for checker in (
        "HAS_TABLE_PRIVILEGE",
        "HAS_COLUMN_PRIVILEGE",
        "HAS_FUNCTION_PRIVILEGE",
        "HAS_SEQUENCE_PRIVILEGE",
        "HAS_DATABASE_PRIVILEGE",
        "HAS_SCHEMA_PRIVILEGE",
        "PG_AUTH_MEMBERS",
        "ACLEXPLODE",
    ):
        assert checker in normalized
    for formal_table in (
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
    ):
        assert formal_table in sql
    for boundary in (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
        "star_oam_migrator",
        "grant_options_ok",
        "expected_edge_update_columns",
        "public_acl_ok",
        "cluster_object_ok",
        "cross_database_ok",
        "pg_catalog.pg_largeobject_metadata",
        "pg_catalog.pg_parameter_acl",
        "deployment_acl_failures",
    ):
        assert boundary in sql
    assert "\\quit 4" in sql


def test_main_api_compose_has_no_edge_receiver_secret():
    compose = MAIN_COMPOSE.read_text(encoding="utf-8")
    api_section = compose.split("  api:\n", 1)[1].split(
        "  oam-work-order-projector:\n", 1
    )[0]
    assert "OAM_EDGE_SYNC_SECRET" not in api_section
    assert "OAM_EDGE_SYNC_ALLOWED_SOURCES" not in api_section
    assert 'OAM_EDGE_SYNC_ENABLED: "false"' in api_section


def test_projector_compose_has_exact_database_only_environment():
    compose = MAIN_COMPOSE.read_text(encoding="utf-8")
    projector = compose.split("  oam-work-order-projector:\n", 1)[1].split(
        "  web:\n", 1
    )[0]
    environment = projector.split("    environment:\n", 1)[1].split(
        "    healthcheck:\n", 1
    )[0]
    environment_keys = set(
        re.findall(r"^      ([A-Z][A-Z0-9_]+):", environment, re.MULTILINE)
    )

    assert environment_keys == {
        "OAM_ENVIRONMENT",
        "OAM_DATABASE_URL",
        "OAM_DATABASE_EXPECTED_RUNTIME_ROLE",
        "OAM_DATABASE_EXPECTED_MIGRATION_ROLE",
        "OAM_DATABASE_SCHEMA_MODE",
        "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED",
        "OAM_EDGE_SYNC_ENABLED",
        "OAM_EDGE_SYNC_LEGACY_BATCHES_ENABLED",
        "OAM_EDGE_SYNC_LEGACY_PERSONNEL_PROJECTION_ENABLED",
    }
    assert (
        "postgresql+psycopg://star_oam_projector:"
        "${OAM_DB_PROJECTOR_PASSWORD}@db:5432/${POSTGRES_DB}"
    ) in projector
    for forbidden_secret in (
        "POSTGRES_PASSWORD",
        "OAM_DB_MIGRATOR_PASSWORD",
        "OAM_DB_API_PASSWORD",
        "OAM_DB_BACKUP_PASSWORD",
        "OAM_JWT_SECRET",
        "OAM_IDENTITY_HASH_SECRET",
        "OAM_AUTH_",
        "OAM_KMS_",
        "OAM_SMS_",
        "OAM_WECHAT_",
        "OAM_FILE_",
        "OAM_MATERIAL_REQUEST_",
        "OAM_STOCKTAKE_",
        "OAM_EDGE_SYNC_SECRET",
        "OAM_EDGE_SYNC_ALLOWED_SOURCES",
    ):
        assert forbidden_secret not in projector
    assert "env_file:" not in projector
    assert "secrets:" not in projector
    assert "volumes:" not in projector
    assert "ports:" not in projector


def test_projector_compose_is_nonroot_read_only_bounded_and_self_checks_acl():
    compose = MAIN_COMPOSE.read_text(encoding="utf-8")
    projector = compose.split("  oam-work-order-projector:\n", 1)[1].split(
        "  web:\n", 1
    )[0]

    for boundary in (
        "profiles: [sync]",
        "read_only: true",
        'user: "65532:65532"',
        "cap_drop: [ALL]",
        "no-new-privileges:true",
        "/tmp:size=32m,mode=1777,noexec,nosuid,nodev",
        "mem_limit: 512m",
        "cpus: 0.50",
        "pids_limit: 64",
        "stop_grace_period: 35s",
        "restart: unless-stopped",
    ):
        assert boundary in projector
    assert (
        "from app.oam_projection_security import "
        "verify_oam_projection_database_boundary; "
        "verify_oam_projection_database_boundary(engine)"
    ) in projector
    for health_setting in (
        "interval: 60s",
        "timeout: 10s",
        "retries: 3",
        "start_period: 20s",
    ):
        assert health_setting in projector
    assert "migrate:\n        condition: service_completed_successfully" in projector


def test_projector_compose_uses_only_internal_database_network():
    compose = MAIN_COMPOSE.read_text(encoding="utf-8")
    database = compose.split("  db:\n", 1)[1].split("  migrate:\n", 1)[0]
    projector = compose.split("  oam-work-order-projector:\n", 1)[1].split(
        "  web:\n", 1
    )[0]
    network_definitions = compose.split("\nnetworks:\n", 1)[1].split(
        "\nvolumes:\n", 1
    )[0]

    assert "      - backend\n      - projector_db" in database
    assert "networks: [projector_db]" in projector
    assert "networks: [backend]" not in projector
    assert "projector_db:\n    internal: true" in network_definitions
    assert compose.count("projector_db") == 3


def test_projector_password_is_documented_as_a_distinct_database_only_secret():
    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert example.count("OAM_DB_PROJECTOR_PASSWORD=") == 1
    assert "Database-only credential for the isolated sync-profile projector" in example
    assert "Never\n# reuse JWT, edge HMAC, OAM session, KMS, SMS" in example


def test_edge_receiver_compose_is_hardened_and_uses_only_internal_database_network():
    edge_compose = EDGE_COMPOSE.read_text(encoding="utf-8")
    main_compose = MAIN_COMPOSE.read_text(encoding="utf-8")
    database = main_compose.split("  db:\n", 1)[1].split("  migrate:\n", 1)[0]
    network_definitions = main_compose.split("\nnetworks:\n", 1)[1].split(
        "\nvolumes:\n", 1
    )[0]

    for boundary in (
        "read_only: true",
        'user: "65532:65532"',
        "cap_drop: [ALL]",
        "no-new-privileges:true",
        "/tmp:size=32m,mode=1777,noexec,nosuid,nodev",
        "mem_limit: 512m",
        "cpus: 0.50",
        "pids_limit: 64",
        "stop_grace_period: 20s",
        "start_period: 20s",
        "networks: [edge_db]",
    ):
        assert boundary in edge_compose
    assert "star-oam_backend" not in edge_compose
    assert "name: star-oam_edge_db" in edge_compose
    assert "      - edge_db" in database
    assert "edge_db:\n    internal: true\n    name: star-oam_edge_db" in network_definitions


def test_edge_receiver_runtime_boundary_excludes_external_system_credentials():
    edge_compose = EDGE_COMPOSE.read_text(encoding="utf-8")
    edge_example = EDGE_ENV_EXAMPLE.read_text(encoding="utf-8")
    builder = SCRIPT.read_text(encoding="utf-8")

    assert "127.0.0.1:18000:8000" in edge_compose
    assert 'OAM_EDGE_SYNC_ENABLED: "true"' in edge_compose
    assert "OAM_DATABASE_SCHEMA_MODE: alembic" in edge_compose
    assert "OAM_DATABASE_EXPECTED_EDGE_ROLE" not in edge_compose
    assert "RSC_EDGE_DATABASE_ROLE=edge_inbox" in edge_example
    assert "OAM_DATABASE_EXPECTED_EDGE_ROLE" in builder

    for forbidden in (
        "OAM_SESSION_FILE",
        "OAM_JWT_SECRET",
        "OAM_IDENTITY_HASH_SECRET",
        "OAM_AUTH_",
        "OAM_KMS_",
        "OAM_SMS_",
        "OAM_WECHAT_",
        "OAM_FILE_",
        "OAM_MATERIAL_REQUEST_",
        "OAM_STOCKTAKE_",
        "FEISHU",
        "WORKFLOW",
        "RSC_TOKEN",
    ):
        assert forbidden not in edge_compose
        assert forbidden not in edge_example
