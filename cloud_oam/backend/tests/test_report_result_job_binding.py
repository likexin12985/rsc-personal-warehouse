"""The result guard must seal the file against the exact report job."""

import hashlib
from pathlib import Path
import runpy

from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from pglast import parser
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0139


MIGRATION = Path(__file__).resolve().parents[1] / 'alembic/versions/20261118_0139_report_result_job_binding.py'
ROOT = MIGRATION.parents[3]


def test_0139_function_and_runtime_ready_manifest():
    migration = runpy.run_path(str(MIGRATION))
    assert migration['revision'] == '20261118_0139'
    assert migration['down_revision'] == '20261117_0138'
    assert migration['FUNCTION_HASH'] == hashlib.sha256(migration['BODY'].encode()).hexdigest()
    assert migration['NEW_READY_HASH'] == OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0139[
        'rsc_oam_runtime_binding_ready_0044()'
    ][6]
    parser.parse_plpgsql_json(
        'CREATE FUNCTION public.rsc_guard_report_result_job_binding_0139() '
        'RETURNS trigger LANGUAGE plpgsql AS $job$'
        + migration['BODY'] + '$job$'
    )


def test_0139_empty_sqlite_upgrade_and_downgrade(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'report-binding.db'}"
    monkeypatch.setenv('OAM_DATABASE_URL', url)
    monkeypatch.setenv('OAM_ENVIRONMENT', 'development')
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, '20261118_0139')
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '20261118_0139'
            assert connection.scalar(text(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_file_jobs_result_binding_0139'"
            )) == 1
        command.downgrade(config, '20261117_0138')
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '20261117_0138'
            assert connection.scalar(text(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_file_jobs_result_binding_0139'"
            )) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ('job_key', 'authorization_version'),
    [('job-key', None), ('other-job-key', 1)],
)
def test_0139_preflight_rejects_unbound_completed_job(job_key, authorization_version):
    migration = runpy.run_path(str(MIGRATION))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('CREATE TABLE file_jobs (job_type text,status text,result_file_id text,idempotency_key text,export_authorization_version integer,requested_by text,result_sha256 text,result_size_bytes integer)')
            connection.exec_driver_sql('CREATE TABLE files (id text,metadata_jsonb text,status text,uploaded_by text,sha256 text,size_bytes integer,mime_type text)')
            connection.execute(text('''INSERT INTO files VALUES
                ('file-1',:metadata,'available','u',:digest,1,
                 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')'''), {
                     'metadata': '{"purpose":"inventory_report_export","idempotency_key_hash":"job-key","authorization_version":"1"}',
                     'digest': 'a' * 64,
                 })
            connection.execute(text(
                "INSERT INTO file_jobs VALUES ('export','succeeded','file-1',:job_key,:version,'u',:digest,1)"
            ), {'job_key': job_key, 'version': authorization_version, 'digest': 'a' * 64})
            connection.commit()
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                with pytest.raises(RuntimeError, match='0139 existing report result is not bound to its job'):
                    migration['_transition'](True)
    finally:
        engine.dispose()


def test_0139_sqlite_guard_transition_on_existing_schema():
    migration = runpy.run_path(str(MIGRATION))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('CREATE TABLE file_jobs (job_type text,status text,result_file_id text,idempotency_key text,export_authorization_version integer,requested_by text,result_sha256 text,result_size_bytes integer)')
            connection.exec_driver_sql('CREATE TABLE files (id text,metadata_jsonb text,status text,uploaded_by text,sha256 text,size_bytes integer,mime_type text)')
            connection.commit()
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration['_transition'](True)
                assert connection.scalar(text(
                    "SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                    "AND name='trg_file_jobs_result_binding_0139'"
                )) == 1
                connection.execute(text('''INSERT INTO files VALUES
                    ('file-1',:metadata,'available','u',:digest,1,
                     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')'''), {
                         'metadata': '{"purpose":"inventory_report_export","idempotency_key_hash":"job-key","authorization_version":"1"}',
                         'digest': 'a' * 64,
                     })
                connection.execute(text(
                    "INSERT INTO file_jobs VALUES ('export','running','file-1','wrong-key',1,'u',:digest,1)"
                ), {'digest': 'a' * 64})
                with pytest.raises(IntegrityError, match='0139 report result is not bound to its job'):
                    connection.exec_driver_sql("UPDATE file_jobs SET status='succeeded'")
                connection.exec_driver_sql("UPDATE file_jobs SET idempotency_key='job-key'")
                connection.exec_driver_sql("UPDATE file_jobs SET status='succeeded'")
                with pytest.raises(RuntimeError, match='0139 completed report bindings must be retained'):
                    migration['_transition'](False)
                connection.exec_driver_sql('DELETE FROM file_jobs')
                migration['_transition'](False)
                assert connection.scalar(text(
                    "SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                    "AND name='trg_file_jobs_result_binding_0139'"
                )) == 0
    finally:
        engine.dispose()
