from __future__ import annotations

import os
from pathlib import Path
import subprocess


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
