import hashlib
from io import StringIO
from pathlib import Path
import runpy

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import IntegrityError
from app import database_security as security, oam_sync_scope_security as scope

PATH = Path(__file__).parents[1] / 'alembic/versions/20261107_0128_opening_start_seals.py'


@pytest.fixture(scope='module')
def migration(): return runpy.run_path(str(PATH))


def test_private_sql_functions_and_exact_trigger_manifests(migration):
    parser = pytest.importorskip('pglast.parser')
    for name, body in [(migration['FUNCTION'], migration['BODY']), (migration['IMMUTABLE_FUNCTION'], migration['IMMUTABLE_BODY'])]:
        parser.parse_plpgsql_json(f'CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $body$' + body + '$body$')
        key = (name, '')
        assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key] == hashlib.sha256(body.encode()).hexdigest()
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[key] == ('f','trigger',False)
    for name, (table, _, function, kind, deferred) in migration['TRIGGERS'].items():
        assert security.EXPECTED_OPENING_TERMINAL_TRIGGERS[name] == (table, function, 'A', kind)
        assert (name in security.OPENING_COMMIT_TRIGGER_NAMES) == deferred
    assert migration['TABLE'] in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
    assert migration['OLD_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0127['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0128['rsc_oam_runtime_binding_ready_0044()'][6]


@pytest.mark.parametrize('action', ['upgrade','downgrade'])
def test_ddl_refuses_ambiguity_and_preserves_negative_facts(migration, action):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql', opts={'as_sql':True,'output_buffer':output})):
        migration[action]()
    sql = output.getvalue(); pytest.importorskip('pglast.parser').parse_sql(sql)
    assert 'direct schema owner required' in sql
    assert 'CREATE OR REPLACE' not in sql and 'DELETE FROM' not in sql
    if action == 'upgrade':
        assert sql.index('ambiguous historical opening') < sql.index('CREATE TABLE')
        assert 'GRANT SELECT, INSERT' in sql and 'DEFERRABLE INITIALLY DEFERRED' in sql
    else:
        assert sql.index('original request uniqueness drift') < sql.index('DROP TRIGGER')
        assert sql.index('original request seals must be retained') < sql.index('DROP TABLE')


def test_sqlite_roundtrip_and_populated_downgrade_refusal(migration):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.connect() as db:
        db.execute(text('CREATE TABLE audit_events (stream_key TEXT, aggregate_type TEXT, action TEXT, actor_user_id TEXT, request_id TEXT)')); db.commit()
        with Operations.context(MigrationContext.configure(db)): migration['upgrade']()
        db.commit()
        with Operations.context(MigrationContext.configure(db)): migration['downgrade']()
        db.commit()
        assert migration['TABLE'] not in inspect(db).get_table_names()
        with Operations.context(MigrationContext.configure(db)): migration['upgrade']()
        db.commit()
        db.execute(text("INSERT INTO opening_start_command_seals VALUES (:id, 'synthetic-user', :id, 1, :id, :id, 'original-01', :ref, '2026-09-20')"),
            {'id':'10000000000040008000000000000001','ref':'opening-request-'+'a'*64}); db.commit()
        with pytest.raises(IntegrityError): db.execute(text('DELETE FROM opening_start_command_seals'))
        db.rollback()
        with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match='seals must be retained'):
            migration['downgrade']()
        db.rollback()
        assert db.scalar(text('SELECT count(*) FROM opening_start_command_seals')) == 1
    engine.dispose()


def test_startup_uniqueness_refuses_weakened_or_missing_original_namespaces():
    rows = [dict(index_name=name,table_name='opening_start_command_seals',access_method='btree',
        is_unique=True,is_valid=True,is_ready=True,is_live=True,is_immediate=True,attribute_count=2,
        key_columns=['actor_user_id',column],predicate=None) for name,column in [
            ('uq_opening_start_seal_request','request_id'),('uq_opening_start_seal_reference','request_reference')]]
    security._assert_opening_seal_indexes(rows)
    with pytest.raises(security.DatabaseSecurityBoundaryError):security._assert_opening_seal_indexes(rows[:1])
    for field,value in [('is_unique',False),('is_immediate',False),('attribute_count',3),('key_columns',['actor_user_id']),('predicate','false')]:
        changed=[dict(row) for row in rows];changed[0][field]=value
        with pytest.raises(security.DatabaseSecurityBoundaryError):security._assert_opening_seal_indexes(changed)


@pytest.mark.parametrize('has_seals,revision,blocker', [
    (True,'20261107_0128','0128 downgrade blocked: original request seals must be retained'),
    (False,'20261105_0126','0126 downgrade blocked: opening authorization evidence must be retained'),
])
def test_release_retention_detects_newer_evidence_before_old_guards(monkeypatch,has_seals,revision,blocker):
    # Exercise only the chain-selection function with synthetic query results;
    # no GitHub markers, DSN, connection, migration or release test is executed.
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    import test_postgresql16_release_gate as gate
    connection=MagicMock();connection.__enter__.return_value=connection
    connection.execute.side_effect=[SimpleNamespace(fetchone=lambda value=value:(value,))
        for value in (has_seals,True,True,True,True,True,True)]
    monkeypatch.setattr(gate,'_current_revision',lambda:gate.HEAD_REVISION)
    monkeypatch.setattr(gate,'_role_password',lambda _:None)
    monkeypatch.setattr(gate,'_connection_parameters',lambda **_: {})
    connect=MagicMock(return_value=connection);monkeypatch.setattr(gate.psycopg,'connect',connect)
    command=MagicMock(return_value=SimpleNamespace(stdout=blocker,stderr=''))
    monkeypatch.setattr(gate,'_run_alembic',command)
    gate._assert_retention_downgrade('20261104_0125',blocking_revision=revision,blocker=blocker)
    command.assert_called_once_with('downgrade','20261104_0125',expect_success=False)
