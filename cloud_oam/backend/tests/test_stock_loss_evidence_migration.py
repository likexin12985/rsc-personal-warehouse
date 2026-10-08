"""Immutable loss-file migration, catalog fingerprints and retained history."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser
import pytest
from sqlalchemy import create_engine, text

from app import database_security as security, oam_sync_scope_security as scope
from migration_script_cache import cache_migration_compilation
from migration_source_expectations import current_source_hash

VERSIONS = Path(__file__).parents[1] / 'alembic/versions'


@pytest.fixture(scope='module')
def migration():
    with cache_migration_compilation(VERSIONS):
        return runpy.run_path(str(VERSIONS / '20261122_0143_stock_loss_evidence_purpose.py'))


def test_private_file_functions_and_current_readiness_are_exact(migration):
    m = migration
    for name, args, body, result in (
        ('rsc_guard_formal_file_object_0036', '', m['NEW_BODY'], 'trigger'),
        (m['AUTHORITY_FUNCTION'], m['AUTHORITY_SIGNATURE'], m['AUTHORITY_BODY'], 'void'),
        (m['COMMIT_FUNCTION'], '', m['COMMIT_BODY'], 'trigger'),
    ):
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[(name,args)] == current_source_hash(m['revision'], 'public.'+name+'('+args+')', body)
        assert (name,args) not in security.RUNTIME_EXECUTE_FUNCTIONS
        declaration = 'actor_id text, actor_version bigint' if args else ''
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({declaration}) RETURNS {result} LANGUAGE plpgsql AS $b${body}$b$')
        assert not text(body)._bindparams
    assert m['OLD_FILE_HASH'] == security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0141[('rsc_guard_formal_file_object_0036','')]
    assert m['OLD_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0142['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0143['rsc_oam_runtime_binding_ready_0044()'][6]
    assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[m['COMMIT_TRIGGER']] == (
        'files',m['COMMIT_FUNCTION'],'A',21,True,True,True)


@pytest.mark.parametrize('direction', ['upgrade','downgrade'])
def test_sql_is_owner_only_locked_and_retains_every_file(migration, direction):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql', opts={'as_sql':True,'output_buffer':output})):
        migration[direction]()
    sql = output.getvalue()
    parser.parse_sql(sql)
    assert sql.index('LOCK TABLE public.alembic_version') < sql.index('LOCK TABLE public.files')
    assert sql.index('requires retention') < sql.index('stock_loss_evidence_purpose_0143')
    assert 'DELETE FROM' not in sql and 'INSERT INTO permissions' not in sql
    assert 'CREATE OR REPLACE' not in sql
    if direction == 'upgrade':
        assert 'DEFERRABLE INITIALLY DEFERRED' in sql and 'ENABLE ALWAYS TRIGGER' in sql


@pytest.mark.parametrize('direction', ['upgrade','downgrade'])
def test_even_pending_evidence_blocks_schema_transition_without_mutation(migration, direction):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.begin() as db:
            db.exec_driver_sql('CREATE TABLE files(id text primary key,status text,metadata_jsonb json)')
            db.execute(text('INSERT INTO files VALUES (:id,:status,:metadata)'),
                       dict(id='synthetic-file',status='pending',metadata='{"purpose":"stock_loss_evidence"}'))
            before = tuple(db.execute(text('SELECT * FROM files')))
            with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match='retention'):
                migration[direction]()
            assert tuple(db.execute(text('SELECT * FROM files'))) == before
    finally:
        engine.dispose()
