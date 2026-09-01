import importlib.util
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deployment" / "build_edge_runtime_env.py"
EDGE_GRANTS = ROOT / "deployment" / "create_oam_edge_staging.sql"
MAIN_COMPOSE = ROOT / "docker-compose.yml"
SPEC = importlib.util.spec_from_file_location("build_edge_runtime_env", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_runtime_values_use_dedicated_edge_credentials_only(tmp_path):
    source = tmp_path / "edge.env"
    values = module.build_runtime_values(
        {
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
        "OAM_EDGE_SYNC_SECRET": "s" * 32,
        "OAM_EDGE_SYNC_ALLOWED_SOURCES": "admin-mac,backup-mac",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"RSC_EDGE_DATABASE_URL": "sqlite:///unsafe.db"},
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
        "RSC_EDGE_DATABASE_URL=postgresql+psycopg://edge:secret@db:5432/oam\n"
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
    assert "GRANT INSERT, UPDATE ON TABLE users" not in sql
    assert "GRANT SELECT ON TABLE users" not in sql
    assert "GRANT INSERT, UPDATE ON TABLE inventory_balances" not in sql


def test_main_api_compose_has_no_edge_receiver_secret():
    compose = MAIN_COMPOSE.read_text(encoding="utf-8")
    api_section = compose.split("  api:\n", 1)[1].split("  web:\n", 1)[0]
    assert "OAM_EDGE_SYNC_SECRET" not in api_section
    assert "OAM_EDGE_SYNC_ALLOWED_SOURCES" not in api_section
    assert 'OAM_EDGE_SYNC_ENABLED: "false"' in api_section
