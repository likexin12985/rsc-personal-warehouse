"""The export migration must keep permission and retained-job boundaries explicit."""

from pathlib import Path
import runpy
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20261114_0135_inventory_report_export_jobs.py"


def _config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_0135_permission_columns_and_populated_downgrade(tmp_path, monkeypatch):
    migration = runpy.run_path(str(MIGRATION))
    assert migration["revision"] == "20261114_0135"
    assert migration["down_revision"] == "20261113_0134"
    from app import oam_sync_scope_security as scope

    assert migration["NEW_HASH"] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0135[
        "rsc_oam_runtime_binding_ready_0044()"
    ][6]

    url = f"sqlite+pysqlite:///{tmp_path / 'report-export.db'}"
    monkeypatch.setenv("OAM_DATABASE_URL", url)
    monkeypatch.setenv("OAM_ENVIRONMENT", "development")
    config = _config(url)
    command.upgrade(config, migration["revision"])
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == migration["revision"]
            assert {
                "export_authorization_version", "export_scope_jsonb",
                "export_ledger_cursor", "result_sha256", "result_size_bytes",
                "download_count",
            } <= {column["name"] for column in inspect(connection).get_columns("file_jobs")}
            grants = connection.execute(text("""
                SELECT r.code, rp.effect FROM permissions p
                JOIN role_permissions rp ON rp.permission_id = p.id
                JOIN roles r ON r.id = rp.role_id
                WHERE p.resource = 'report' AND p.action = 'export' AND p.field_code = ''
                ORDER BY r.code
            """)).all()
            assert grants == [("admin", "allow"), ("provincial_manager", "allow")]
        job_id = uuid.uuid4().hex
        with engine.begin() as connection:
            parameters = {
                "id": job_id, "requested_by": "migration-test-user",
                "created_at": "2026-09-23 00:00:00", "updated_at": "2026-09-23 00:00:00",
            }
            connection.execute(text("""
                INSERT INTO file_jobs
                (id, job_type, requested_by, parameters_jsonb, parameters_hash,
                 idempotency_key, status, created_at, updated_at,
                 export_authorization_version, export_ledger_cursor, download_count)
                VALUES (:id, 'export', :requested_by, '{}', :hash,
                        :idempotency, 'queued', :created_at, :updated_at, 1, 0, 0)
            """), {**parameters, "hash": "0" * 64, "idempotency": f"report-{job_id}"})
        with pytest.raises(RuntimeError, match="0135 populated report jobs or downloads must be retained"):
            command.downgrade(config, migration["down_revision"])
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == migration["revision"]
            assert connection.scalar(text("SELECT count(*) FROM file_jobs WHERE id=:id"), {"id": job_id}) == 1
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM file_jobs WHERE id=:id"), {"id": job_id})
        with engine.begin() as connection:
            grant = connection.execute(text("""
                SELECT id, role_id, permission_id, effect, created_at
                FROM role_permissions WHERE id=:id
            """), {"id": migration["REGION_GRANT_ID"].hex}).mappings().one()
            connection.execute(text("DELETE FROM role_permissions WHERE id=:id"), {"id": grant["id"]})
        with pytest.raises(RuntimeError, match="0135 report permission catalog drift"):
            command.downgrade(config, migration["down_revision"])
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO role_permissions (id, role_id, permission_id, effect, created_at)
                VALUES (:id, :role_id, :permission_id, :effect, :created_at)
            """), dict(grant))
        command.downgrade(config, migration["down_revision"])
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == migration["down_revision"]
            assert "download_count" not in {column["name"] for column in inspect(connection).get_columns("file_jobs")}
            assert connection.scalar(text("SELECT count(*) FROM permissions WHERE resource='report' AND action='export'")) == 0
    finally:
        engine.dispose()
