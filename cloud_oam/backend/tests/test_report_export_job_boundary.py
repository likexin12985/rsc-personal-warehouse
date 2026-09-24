"""Export jobs cannot escape the queued/running/terminal state boundary."""

from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


ROOT = Path(__file__).resolve().parents[2]
HEAD = '20261115_0136'


def test_sqlite_export_job_state_and_nonempty_downgrade(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'report-job-boundary.db'}"
    monkeypatch.setenv('OAM_DATABASE_URL', url)
    monkeypatch.setenv('OAM_ENVIRONMENT', 'development')
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, HEAD)
    engine = create_engine(url)
    try:
        job_id = uuid.uuid4().hex
        row = dict(id=job_id, requested_by='report-job-test',
                   parameters='{"report":"inventory_balances","filters":{}}',
                   scope='{"version":1,"account_ids":[],"assignment_ids":[]}',
                   digest='0'*64, created_at='2026-09-23 00:00:00',
                   updated_at='2026-09-23 00:00:00')
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO file_jobs (id,job_type,requested_by,parameters_jsonb,
                  parameters_hash,idempotency_key,status,export_authorization_version,
                  export_scope_jsonb,export_ledger_cursor,created_at,updated_at)
                VALUES (:id,'export',:requested_by,:parameters,:digest,:digest,
                  'queued',1,:scope,0,:created_at,:updated_at)
            """), row)
        with engine.begin() as connection:
            with pytest.raises(IntegrityError, match='0136 export transition invalid'):
                connection.execute(text("UPDATE file_jobs SET parameters_hash=:digest WHERE id=:id"),
                    {'id':job_id,'digest':'1'*64})
        with engine.begin() as connection:
            connection.execute(text("UPDATE file_jobs SET status='running',started_at=:at WHERE id=:id"),
                {'id':job_id,'at':'2026-09-23 00:01:00'})
        with engine.begin() as connection:
            connection.execute(text("UPDATE file_jobs SET status='failed',completed_at=:at,error_detail='render_failed' WHERE id=:id"),
                {'id':job_id,'at':'2026-09-23 00:02:00'})
        with engine.begin() as connection:
            with pytest.raises(IntegrityError, match='0136 export transition invalid'):
                connection.execute(text("UPDATE file_jobs SET status='queued' WHERE id=:id"), {'id':job_id})
        with pytest.raises(RuntimeError, match='0136 export jobs must be retained'):
            command.downgrade(config, '20261114_0135')
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
            assert connection.scalar(text('SELECT status FROM file_jobs WHERE id=:id'), {'id':job_id}) == 'failed'
    finally:
        engine.dispose()
