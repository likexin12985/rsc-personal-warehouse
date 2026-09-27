"""Import migration preserves exports and closes unproved execution paths."""

import hashlib
from pathlib import Path
import runpy
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pglast import parser
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from migration_script_cache import cache_migration_compilation


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / 'backend/alembic/versions'


def _migration():
    with cache_migration_compilation(VERSIONS):
        return runpy.run_path(str(VERSIONS / '20261120_0141_opening_count_import_jobs.py'))


def test_import_job_guard_catalog_and_plpgsql_are_exact():
    from app.database_security import (
        FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0141, RUNTIME_UPDATE_COLUMNS,
    )
    from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0141

    migration = _migration()
    for function, body in ((migration['FUNCTION'], migration['BODY']),
                           (migration['TERMINAL_FUNCTION'], migration['TERMINAL_BODY']),
                           ('rsc_guard_report_export_job_0136', migration['REPORT_NEW_BODY']),
                           ('rsc_guard_formal_file_object_0036', migration['FILE_NEW_BODY'])):
        assert hashlib.sha256(body.encode()).hexdigest() == FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0141[(function, '')]
        assert not text(body)._bindparams, 'SQL body must not introduce bind parameters'
        parser.parse_plpgsql_json(f'CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $x${body}$x$')
    assert migration['NEW_READY_HASH'] == OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0141['rsc_oam_runtime_binding_ready_0044()'][6]
    assert 'import_binding_jsonb' not in RUNTIME_UPDATE_COLUMNS['file_jobs']
    assert {'import_preview_jsonb', 'import_completion_id', 'confirmed_by'} <= RUNTIME_UPDATE_COLUMNS['file_jobs']


def test_import_job_empty_sqlite_roundtrip_preserves_an_existing_export(tmp_path, monkeypatch):
    migration = _migration()
    url = f"sqlite+pysqlite:///{tmp_path / 'import-job.db'}"
    monkeypatch.setenv('OAM_DATABASE_URL', url)
    monkeypatch.setenv('OAM_ENVIRONMENT', 'development')
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, migration['down_revision'])
    engine = create_engine(url)
    key = uuid4().hex
    try:
        with engine.begin() as db:
            db.execute(text("""
                INSERT INTO file_jobs (
                    id,job_type,requested_by,parameters_jsonb,parameters_hash,idempotency_key,status,
                    created_at,updated_at,export_authorization_version,export_scope_jsonb,
                    export_ledger_cursor,download_count
                ) VALUES (
                    :id,'export','synthetic-migration-user',:parameters,:digest,:key,'queued',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,:scope,0,0
                )
            """), {'id': key, 'key': key, 'digest': 'a' * 64,
                   'parameters': '{"report":"inventory_balances","filters":{}}',
                   'scope': '{"version":1,"account_ids":[],"assignment_ids":[]}'})
            original = dict(db.execute(text('SELECT * FROM file_jobs WHERE id=:id'), {'id':key}).mappings().one())
        command.upgrade(config, migration['revision'])
        with engine.connect() as db:
            stored = dict(db.execute(text('SELECT * FROM file_jobs WHERE id=:id'), {'id':key}).mappings().one())
            assert {k:stored[k] for k in original} == original
            assert all(stored[k] is None for k in migration['COLUMNS'])
            assert set(migration['COLUMNS']) <= {column['name'] for column in inspect(db).get_columns('file_jobs')}
        with engine.begin() as db:
            with pytest.raises(DBAPIError, match='import requires PostgreSQL 16'):
                db.execute(text("""
                    INSERT INTO file_jobs (id,job_type,requested_by,parameters_jsonb,parameters_hash,idempotency_key,status,created_at,updated_at)
                    VALUES (:id,'import','synthetic-migration-user','{}',:digest,:id,'queued',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                """), {'id':uuid4().hex,'digest':'b'*64})
        command.downgrade(config, migration['down_revision'])
        with engine.connect() as db:
            assert dict(db.execute(text('SELECT * FROM file_jobs WHERE id=:id'), {'id':key}).mappings().one()) == original
            assert not set(migration['COLUMNS']) & {column['name'] for column in inspect(db).get_columns('file_jobs')}
    finally:
        engine.dispose()


def test_import_seal_catalog_is_private_exact_and_non_mutable():
    from app.database_security import (FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256, FORMAL_FILE_INTERNAL_FUNCTION_SHAPES,
        RUNTIME_READ_TABLES,RUNTIME_INSERT_TABLES,RUNTIME_UPDATE_TABLES,RUNTIME_DELETE_TABLES,RUNTIME_UPDATE_COLUMNS)
    migration=_migration()['seals']
    for name,(args,params,returns,body) in migration['FUNCTIONS'].items():
        assert hashlib.sha256(body.encode()).hexdigest()==FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[(name,args)]
        assert FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[(name,args)]==('f',returns,False)
        assert not text(body)._bindparams
        parser.parse_plpgsql_json(f'CREATE FUNCTION {name}({params}) RETURNS {returns} LANGUAGE plpgsql AS $x${body}$x$')
    table=migration['TABLE']
    assert table in RUNTIME_READ_TABLES & RUNTIME_INSERT_TABLES
    assert table not in RUNTIME_UPDATE_TABLES | RUNTIME_DELETE_TABLES
    assert table not in RUNTIME_UPDATE_COLUMNS


def test_import_seal_function_trigger_catalog_includes_both_before_and_deferred():
    from app.database_security import EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS
    seals=_migration()['seals']
    for name,(table,events,function,kind,deferred) in seals['TRIGGERS'].items():
        if function==seals['GUARD']:
            assert EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',kind,deferred,deferred,deferred)
