from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
ROLE_INIT = (
    ROOT / "deployment" / "postgres-init" / "10-create-application-roles.sh"
)
ENV_EXAMPLE = ROOT / ".env.example"
BACKUP = ROOT / "scripts" / "backup.sh"
PG16_WORKFLOW = ROOT.parent / ".github" / "workflows" / (
    "postgresql16-release-gate.yml"
)
PG16_RELEASE_GATE_TEST = ROOT / "backend" / "tests" / (
    "test_postgresql16_release_gate.py"
)
DEPLOYMENT = ROOT / "deployment"
EDGE_SCOPE_PROVISION = DEPLOYMENT / "provision_oam_edge_scope.sql"


def test_compose_never_injects_bootstrap_or_migrator_secret_into_api() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    migrate = compose.split("  migrate:\n", 1)[1].split(
        "  kms-pin-plan:\n", 1
    )[0]
    kms_pin_gate = compose.split("  kms-pin-gate:\n", 1)[1].split(
        "  api:\n", 1
    )[0]
    api = compose.split("  api:\n", 1)[1].split("  web:\n", 1)[0]
    assert "star_oam_migrator:${OAM_DB_MIGRATOR_PASSWORD}" in migrate
    assert "star_oam_api:${OAM_DB_API_PASSWORD}" in kms_pin_gate
    assert "star_oam_api:${OAM_DB_API_PASSWORD}" in api
    assert "POSTGRES_PASSWORD" not in migrate
    assert "POSTGRES_PASSWORD" not in kms_pin_gate
    assert "POSTGRES_PASSWORD" not in api
    assert "OAM_DB_MIGRATOR_PASSWORD" not in kms_pin_gate
    assert "OAM_DB_MIGRATOR_PASSWORD" not in api
    assert "OAM_DB_BACKUP_PASSWORD" not in kms_pin_gate
    assert "OAM_DB_BACKUP_PASSWORD" not in api
    assert "OAM_DATABASE_EXPECTED_RUNTIME_ROLE: star_oam_api" in kms_pin_gate
    assert "OAM_DATABASE_EXPECTED_RUNTIME_ROLE: star_oam_api" in api
    assert "OAM_DATABASE_EXPECTED_MIGRATION_ROLE: star_oam_migrator" in migrate


def test_compose_blocks_api_on_read_only_kms_pin_gate() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    kms_pin_gate = compose.split("  kms-pin-gate:\n", 1)[1].split(
        "  api:\n", 1
    )[0]
    api = compose.split("  api:\n", 1)[1].split("  web:\n", 1)[0]

    assert 'command: ["python", "-m", "app.kms_pin_gate"]' in kms_pin_gate
    assert "migrate:\n        condition: service_completed_successfully" in kms_pin_gate
    assert "kms-pin-gate:\n        condition: service_completed_successfully" in api
    assert (
        "${OAM_KMS_ENCRYPTED_DATA_KEY_REGISTRY_FILE}:"
        "/run/secrets/rsc-kms-data-keys.json:ro"
    ) in kms_pin_gate


def test_compose_kms_pin_plan_has_no_database_secret_or_network() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    plan = compose.split("  kms-pin-plan:\n", 1)[1].split(
        "  kms-pin-gate:\n", 1
    )[0]

    assert 'profiles: [ops]' in plan
    assert (
        'command: ["python", "-m", "app.kms_pin_gate", "--plan"]'
        in plan
    )
    assert "network_mode: none" in plan
    assert "networks:" not in plan
    assert "depends_on:" not in plan
    assert "OAM_DB_API_PASSWORD" not in plan
    assert "OAM_DB_MIGRATOR_PASSWORD" not in plan
    assert "OAM_DB_BACKUP_PASSWORD" not in plan
    assert (
        "postgresql+psycopg://star_oam_api@127.0.0.1/"
        "unused_kms_pin_plan"
    ) in plan


def test_fresh_database_initializer_creates_distinct_non_superuser_roles() -> None:
    subprocess.run(["sh", "-n", str(ROLE_INIT)], check=True)
    script = ROLE_INIT.read_text(encoding="utf-8")
    normalized = " ".join(script.split())
    for role in (
        "star_oam_migrator",
        "star_oam_api",
        "star_oam_backup",
        "star_oam_projector",
    ):
        assert f"CREATE ROLE {role} LOGIN" in normalized
        assert f"ALTER ROLE {role} WITH LOGIN" in normalized
    assert normalized.count(
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    ) == 4
    assert "ALTER SCHEMA public OWNER TO star_oam_migrator" in normalized
    assert (
        "GRANT USAGE ON SCHEMA public TO star_oam_api, star_oam_backup, "
        "star_oam_projector"
    ) in normalized
    assert "ON TABLES TO star_oam_api" not in normalized
    assert "ON SEQUENCES TO star_oam_api" not in normalized
    assert "GRANT SELECT ON TABLES TO star_oam_backup" in normalized
    assert "GRANT star_oam_migrator TO star_oam_api" not in normalized
    assert (
        "REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC"
        in normalized
    )
    assert "database_row.datname <> pg_catalog.current_database()" in normalized


def test_postgresql16_gate_covers_main_prs_and_edge_role_provisioning() -> None:
    workflow = PG16_WORKFLOW.read_text(encoding="utf-8")

    assert "  pull_request:\n" in workflow
    assert "  push:\n" in workflow
    assert workflow.count("      - main\n") >= 2
    assert "cloud_oam/deployment/provision_edge_receiver_role.sql" in workflow
    assert workflow.count(
        '      - "cloud_oam/deployment/provision_oam_edge_scope.sql"\n'
    ) == 2
    for required_gate in (
        "backend/tests/test_database_security.py",
        "backend/tests/test_oam_projection_security.py",
        "backend/tests/test_material_request_draft_service.py",
        "backend/tests/test_material_request_approval_service.py",
        "backend/tests/test_material_request_lifecycle_service.py",
        "backend/tests/test_material_request_query_service.py",
        "backend/tests/test_audit_chain.py",
        "backend/tests/test_inventory_posting.py",
        "backend/tests/test_opening_stocktake_service.py",
        "backend/tests/test_opening_stocktake_review_service.py",
        "backend/tests/test_opening_recount_assignee_options.py",
        "backend/tests/test_opening_count_command_status.py",
        "backend/tests/test_formal_opening_stocktake_read.py",
        "backend/tests/test_stocktake_options.py",
    ):
        assert required_gate in workflow
    assert "pytest==9.1.1 pglast==7.18 httpx==0.28.1" in workflow


def test_postgresql16_scratch_cleanup_always_restores_projector_connect() -> None:
    source = PG16_RELEASE_GATE_TEST.read_text(encoding="utf-8")
    restore_source = source.split(
        "def _restore_main_projector_connect() -> None:\n", 1
    )[1].split("\ndef ", 1)[0]
    create_source = source.split(
        "def _create_opening_backfill_database() -> str:\n", 1
    )[1].split("\ndef ", 1)[0]
    cleanup_source = source.split(
        "def _drop_opening_backfill_database(database_name: str) -> None:\n",
        1,
    )[1].split("\ndef ", 1)[0]

    assert (
        "GRANT CONNECT ON DATABASE {} TO star_oam_projector"
        in restore_source
    )
    assert "except BaseException as exc:" in cleanup_source
    assert "cleanup_error = exc" in cleanup_source
    assert "finally:" in cleanup_source
    finally_source = cleanup_source.split("finally:", 1)[1]
    assert "_restore_main_projector_connect()" in finally_source
    assert "raise restoration_error from cleanup_error" in finally_source
    assert "except BaseException as creation_error:" in create_source
    assert "finally:" in create_source
    assert "_restore_main_projector_connect()" in create_source
    assert "raise cleanup_error from creation_error" in create_source
    assert "raise cleanup_error from setup_error" in create_source


def test_postgresql16_0049_catalog_uses_head_guard_hashes_by_revision() -> None:
    spec = importlib.util.spec_from_file_location(
        "rsc_pg16_gate_catalog_hash_test",
        PG16_RELEASE_GATE_TEST,
    )
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    migration_0049 = gate._load_stocktake_recount_guard_security_migration_0049()
    migration_0052 = gate._load_opening_terminal_guard_execution_migration_0052()
    migration_0055 = gate._load_nonopening_start_audit_order_migration_0055()
    migration_0056 = (
        gate._load_nonopening_count_guard_compatibility_migration_0056()
    )
    migration_0057 = (
        gate._load_nonopening_difference_replay_lock_migration_0057()
    )
    migration_0058 = (
        gate._load_nonopening_review_terminal_status_migration_0058()
    )
    signature = migration_0049.SCOPE_COMPLETION_CALLER_0021_SIGNATURE
    assert signature == migration_0052.SCOPE_COMPLETION_GUARD_SIGNATURE_0021

    for legacy_revision in (
        gate.STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION,
        gate.STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION,
        gate.STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION,
        gate.STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION,
    ):
        assert gate._expected_0049_function_body_sha256(
            migration_0049,
            signature=signature,
            expected_revision=legacy_revision,
            observation_scope_mode_fixed=True,
        ) == migration_0052.LEGACY_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021

    assert (
        gate.HEAD_REVISION
        == gate.SUPPLY_TASK_EVENT_KEY_REVISION
    )
    migration_0061 = gate._load_supply_event_key_migration_0061()
    assert migration_0061.revision == gate.HEAD_REVISION
    assert migration_0061.down_revision == gate.SUPPLY_TASK_SECURITY_REVISION
    assert migration_0058.revision == gate.NONOPENING_REVIEW_TERMINAL_STATUS_REVISION
    assert migration_0058.down_revision == migration_0057.revision
    assert migration_0057.down_revision == migration_0056.revision
    assert migration_0056.down_revision == gate.NONOPENING_START_AUDIT_ORDER_REVISION
    assert migration_0055.revision == migration_0056.down_revision
    assert migration_0055.down_revision == gate.OPENING_RECOUNT_SOURCE_HISTORY_REVISION
    assert gate._expected_0049_function_body_sha256(
        migration_0049,
        signature=signature,
        expected_revision=gate.HEAD_REVISION,
        observation_scope_mode_fixed=True,
    ) == migration_0052.FIXED_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021

    helper_signature = migration_0049.ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE
    assert gate._expected_0049_function_body_sha256(
        migration_0049,
        signature=helper_signature,
        expected_revision=gate.NONOPENING_START_AUDIT_ORDER_REVISION,
        observation_scope_mode_fixed=True,
    ) == migration_0056.LEGACY_BODY_SHA256
    assert gate._expected_0049_function_body_sha256(
        migration_0049,
        signature=helper_signature,
        expected_revision=gate.HEAD_REVISION,
        observation_scope_mode_fixed=True,
    ) == migration_0056.FIXED_BODY_SHA256

    review_signature = migration_0049.REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE
    assert gate._expected_0049_function_body_sha256(
        migration_0049,
        signature=review_signature,
        expected_revision=gate.NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION,
        observation_scope_mode_fixed=True,
    ) == migration_0058.LEGACY_BODY_SHA256
    assert gate._expected_0049_function_body_sha256(
        migration_0049,
        signature=review_signature,
        expected_revision=gate.HEAD_REVISION,
        observation_scope_mode_fixed=True,
    ) == migration_0058.FIXED_BODY_SHA256

    with pytest.raises(AssertionError, match="unsupported 0049 catalog revision"):
        gate._expected_0049_function_body_sha256(
            migration_0049,
            signature=signature,
            expected_revision="20260904_9999",
            observation_scope_mode_fixed=True,
        )

    unrelated_signature = migration_0049.COUNT_LINE_CALLER_0021_SIGNATURE
    assert gate._expected_0049_function_body_sha256(
        migration_0049,
        signature=unrelated_signature,
        expected_revision=gate.OPENING_TERMINAL_GUARD_EXECUTION_REVISION,
        observation_scope_mode_fixed=True,
    ) == migration_0049.EXPECTED_FUNCTION_BODY_SHA256[unrelated_signature]


def test_deployment_verifier_allows_only_0044_runtime_entrypoints() -> None:
    source = (DEPLOYMENT / "verify_oam_edge_staging.sql").read_text(
        encoding="utf-8"
    )

    assert "runtime_functions(function_signature) AS" in source
    assert "expected_function_acl(role_kind, function_signature) AS" in source
    for signature in (
        "public.rsc_oam_rls_check_0044(text,text,jsonb)",
        "public.rsc_oam_runtime_binding_ready_0044()",
    ):
        assert signature in source
    for security_attribute in (
        "pg_catalog.pg_get_userbyid(function_row.proowner)",
        "NOT function_row.prosecdef",
        "function_row.provolatile <> 's'",
        "function_row.proleakproof",
        "ARRAY['search_path=pg_catalog']::text[]",
    ):
        assert security_attribute in source
    assert "expected_acl.function_signature IS NOT NULL" in source
    assert "acl.grantee <> function_row.proowner" in source
    assert "acl_grantee.rolname NOT IN (" in source
    assert ":'edge_role'" in source
    assert ":'projector_role'" in source


def test_postgresql16_psql_scripts_use_real_fail_closed_exit_paths() -> None:
    for script in DEPLOYMENT.glob("*.sql"):
        source = script.read_text(encoding="utf-8")
        assert not any(
            line.startswith("\\quit ") and line.removeprefix("\\quit ").isdigit()
            for line in (row.strip() for row in source.splitlines())
        ), f"{script.name} uses a numeric \\quit argument that PostgreSQL 16 ignores"
        if "intentional_psql_fail_closed" in source:
            assert "\\set ON_ERROR_STOP on" in source


def test_edge_scope_provision_is_migrator_only_single_row_and_fail_closed() -> None:
    source = EDGE_SCOPE_PROVISION.read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    for parameter in (
        "source_instance",
        "company_id",
        "org_code",
        "scope_key",
        "entity_type",
    ):
        assert f"\\if :{{?{parameter}}}" in source
        assert f":'{parameter}'::text" in source

    assert "\\set ON_ERROR_STOP on" in source
    assert "current_user <> 'star_oam_migrator'" in source
    assert "session_user <> 'star_oam_migrator'" in source
    assert source.count("INSERT INTO public.oam_sync_scope_bindings") == 1
    assert "'edge_inbox'" in source
    assert "'edge_ingress'" in source
    assert "'starcharge_oam'" in source
    assert "ON CONFLICT (" in source
    assert ") DO NOTHING;" in source
    assert "DO UPDATE" not in source
    assert "UPDATE public.oam_sync_scope_bindings" not in source
    assert "DELETE FROM public.oam_sync_scope_bindings" not in source
    assert (
        "existing OAM edge scope binding differs; "
        "no automatic overwrite performed"
    ) in normalized

    insert_at = source.index("INSERT INTO public.oam_sync_scope_bindings")
    reread_at = source.index(
        "FROM public.oam_sync_scope_bindings AS binding", insert_at
    )
    assert (
        source.index("BEGIN;")
        < insert_at
        < reread_at
        < source.rindex("COMMIT;")
    )
    for exact_coordinate in (
        "binding.source_instance = scope_input.source_instance",
        "binding.scope_key = scope_input.scope_key",
        "binding.company_id = scope_input.company_id",
        "binding.org_code = scope_input.org_code",
        "binding.entity_type = scope_input.entity_type",
        "binding.enabled",
        "binding.formal_scope_key =",
    ):
        assert exact_coordinate in source


def test_edge_scope_provision_accepts_only_reviewed_entity_scope_pairs() -> None:
    source = EDGE_SCOPE_PROVISION.read_text(encoding="utf-8")

    assert (
        "^work-orders:recent-([1-9]|[1-9][0-9]|[12][0-9][0-9]|"
        "3[0-5][0-9]|36[0-5])d$"
    ) in source
    for entity_type in (
        "employee",
        "material_application",
        "material_application_line",
        "warehouse",
        "inventory",
        "work_order",
    ):
        assert f"'{entity_type}'" in source
    assert "scope_input.scope_key = 'all'" in source
    assert "^warehouse:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$" in source
    assert "~ '[[:cntrl:]]'" in source
    assert "~ '[*%?]'" in source
    assert "LIKE" not in source.upper()
    assert (
        "enabled read-only starcharge_oam source registration is required"
        in source
    )


def test_environment_and_backup_use_dedicated_database_credentials() -> None:
    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "POSTGRES_USER=star_oam_bootstrap" in example
    for key in (
        "OAM_DB_MIGRATOR_PASSWORD",
        "OAM_DB_API_PASSWORD",
        "OAM_DB_BACKUP_PASSWORD",
        "OAM_DB_PROJECTOR_PASSWORD",
    ):
        assert key in example
    backup = BACKUP.read_text(encoding="utf-8")
    assert "-U star_oam_backup" in backup
    assert 'PGPASSWORD="$OAM_DB_BACKUP_PASSWORD"' in backup
    assert 'pg_dump -U "$POSTGRES_USER"' not in backup
    assert "umask 077" in backup
    assert "| gzip" not in backup
    assert 'gzip -t "$DATABASE_TMP"' in backup
    assert 'tar -tzf "$UPLOADS_TMP"' in backup
    assert "current_user = 'star_oam_backup'" in backup
    assert "membership.roleid = role_row.oid" in backup
    assert "has_table_privilege(" in backup
    assert "has_any_column_privilege(" in backup
    assert "attribute_row.attacl" in backup
    assert "database_acl.is_grantable" in backup
    assert "schema_acl.is_grantable" in backup
    assert "table_acl.is_grantable" in backup
    assert "sequence_acl.is_grantable" in backup
    assert "sha256sum -c manifest.sha256" in backup
    assert 'mv "$BUNDLE_TMP" "$BUNDLE_FINAL"' in backup
    assert 'mv "$DATABASE_TMP" "$DATABASE_FINAL"' not in backup


def test_nonopening_stocktake_deployment_gate_is_explicit_and_disabled_by_default() -> None:
    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "OAM_STOCKTAKE_WRITES_ENABLED=false" in example
    assert "OAM_STOCKTAKE_IDEMPOTENCY_HMAC_SECRET=" in example
    assert (
        "OAM_STOCKTAKE_WRITES_ENABLED: "
        "${OAM_STOCKTAKE_WRITES_ENABLED:-false}"
    ) in compose
    assert (
        "OAM_STOCKTAKE_IDEMPOTENCY_HMAC_SECRET: "
        "${OAM_STOCKTAKE_IDEMPOTENCY_HMAC_SECRET:-}"
    ) in compose


def test_backup_never_publishes_a_partial_set_when_atomic_rename_fails(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "proof.txt").write_text("verified upload\n", encoding="utf-8")

    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
case " $* " in
  *" psql "*)
    printf 'ok\\n'
    ;;
  *" pg_dump "*)
    printf '%s\\n' '-- deterministic local pg_dump evidence'
    ;;
  *" api tar "*)
    exec tar -czf - -C "$FAKE_UPLOADS_DIR" .
    ;;
  *)
    exit 64
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    fake_mv = fake_bin / "mv"
    fake_mv.write_text("#!/bin/sh\nexit 73\n", encoding="utf-8")
    fake_mv.chmod(0o755)

    backup_dir = tmp_path / "backups"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "BACKUP_DIR": str(backup_dir),
            "FAKE_UPLOADS_DIR": str(uploads),
            "OAM_DB_BACKUP_PASSWORD": "local-test-only",
            "POSTGRES_DB": "star_oam_test",
        }
    )
    completed = subprocess.run(
        ["sh", str(BACKUP)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 73
    assert list(backup_dir.glob("backup_*.tar")) == []
    assert list(backup_dir.glob(".star-oam-backup.*")) == []
