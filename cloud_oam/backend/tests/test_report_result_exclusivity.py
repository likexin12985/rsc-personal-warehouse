"""A completed private report result cannot be assigned to two jobs."""

from pathlib import Path
import runpy
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0138


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / 'backend/alembic/versions/20261117_0138_report_result_exclusivity.py'


def _insert_import(connection, identifier: str, result_file: str):
    connection.execute(text('''
        INSERT INTO file_jobs (id,job_type,requested_by,parameters_jsonb,
          parameters_hash,idempotency_key,status,result_file_id,created_at,updated_at)
        VALUES (:id,'import','fixture-user','{}',:hash,:key,'succeeded',:file,:at,:at)
    '''), {'id':identifier, 'hash':'0'*64, 'key':identifier, 'file':result_file,
           'at':'2026-09-23 00:00:00'})


def test_0138_rejects_existing_duplicate_and_new_result_reuse(tmp_path, monkeypatch):
    migration = runpy.run_path(str(MIGRATION))
    assert migration['revision'] == '20261117_0138'
    assert migration['down_revision'] == '20261116_0137'
    assert migration['NEW_READY_HASH'] == OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0138[
        'rsc_oam_runtime_binding_ready_0044()'][6]
    url = f"sqlite+pysqlite:///{tmp_path / 'report-result.db'}"
    monkeypatch.setenv('OAM_DATABASE_URL', url)
    monkeypatch.setenv('OAM_ENVIRONMENT', 'development')
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, migration['down_revision'])
    engine = create_engine(url)
    try:
        shared = uuid.uuid4().hex
        first, second = uuid.uuid4().hex, uuid.uuid4().hex
        with engine.begin() as connection:
            _insert_import(connection, first, shared)
            _insert_import(connection, second, shared)
        with pytest.raises(RuntimeError, match='0138 result file is referenced by multiple jobs'):
            command.upgrade(config, migration['revision'])
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == migration['down_revision']
        with engine.begin() as connection:
            connection.execute(text('DELETE FROM file_jobs WHERE id=:id'), {'id':second})
        command.upgrade(config, migration['revision'])
        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                _insert_import(connection, second, shared)
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM file_jobs WHERE result_file_id=:file'), {'file':shared}) == 1
        command.downgrade(config, migration['down_revision'])
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == migration['down_revision']
    finally:
        engine.dispose()
