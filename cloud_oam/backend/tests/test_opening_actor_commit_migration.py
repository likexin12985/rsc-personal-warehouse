"""Forward migration contract; real commit/time behavior is in the PG16 gate."""
from io import StringIO
from pathlib import Path
import hashlib,runpy
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.exc import IntegrityError
from app import database_security as security,oam_sync_scope_security as scope

PATH=Path(__file__).parents[1]/'alembic/versions/20261105_0126_opening_actor_commit.py'

@pytest.fixture(scope='module')
def migration():return runpy.run_path(str(PATH))


def test_internal_capabilities_are_fixed_and_not_runtime_executable(migration):
    parser=pytest.importorskip('pglast.parser')
    for name,(args,params,returns,body) in migration['FUNCTIONS'].items():
        key=(name,args)
        assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
        assert security.FORMAL_FILE_INTERNAL_FUNCTIONS[key]==('v',True,'plpgsql',('search_path=pg_catalog, public',))
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[key]==('f',returns,False)
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key]==hashlib.sha256(body.encode()).hexdigest()
        parser.parse_plpgsql_json(f'CREATE FUNCTION {name}({params}) RETURNS {returns} LANGUAGE plpgsql AS $body$'+body+'$body$')
    assert migration['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0125['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0126['rsc_oam_runtime_binding_ready_0044()'][6]
    assert 'statement_timestamp' not in migration['ACTOR_BODY'] and 'transaction_timestamp' not in migration['ACTOR_BODY']
    assert 'clock_timestamp()' in migration['ACTOR_BODY'] and 'rejected' in migration['ACTOR_BODY']
    for name,(kind,deferred) in migration['TRIGGERS'].items():
        assert security.EXPECTED_OPENING_TERMINAL_TRIGGERS[name]==('stocktake_tasks','rsc_guard_opening_actor_0126','A',kind)
        assert (name in security.OPENING_COMMIT_TRIGGER_NAMES)==deferred


@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_transactional_ddl_and_no_backfill(migration,direction):
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        migration[direction]()
    sql=output.getvalue();pytest.importorskip('pglast.parser').parse_sql(sql)
    assert 'direct schema owner required' in sql
    for forbidden in ('GRANT ','UPDATE public.stocktake_tasks','UPDATE stocktake_tasks','CREATE OR REPLACE','DELETE FROM'):
        assert forbidden not in sql
    if direction=='upgrade':
        assert 'ADD COLUMN opening_authorization_version BIGINT' in sql
        assert 'DEFERRABLE INITIALLY DEFERRED' in sql
    else:
        assert sql.index('0126 actor function source or ACL drift')<sql.index('DROP TRIGGER')
        assert sql.index('0126 downgrade blocked')<sql.index('DROP COLUMN')


def legacy_connection():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    connection=engine.connect()
    connection.execute(text('CREATE TABLE stocktake_tasks (id INTEGER PRIMARY KEY, task_type TEXT NOT NULL)'))
    connection.execute(text("INSERT INTO stocktake_tasks (id,task_type) VALUES (1,'opening')"));connection.commit()
    return engine,connection


def test_historical_unknown_is_preserved_and_cannot_be_backfilled(migration):
    engine,db=legacy_connection()
    try:
        with Operations.context(MigrationContext.configure(db)):migration['upgrade']()
        db.commit()
        assert db.execute(text('SELECT id,opening_authorization_version FROM stocktake_tasks')).all()==[(1,None)]
        with pytest.raises(IntegrityError):db.execute(text('UPDATE stocktake_tasks SET opening_authorization_version=1 WHERE id=1'))
        db.rollback()
        db.execute(text("INSERT INTO stocktake_tasks (id,task_type,opening_authorization_version) VALUES (2,'opening',7)"));db.commit()
        with Operations.context(MigrationContext.configure(db)),pytest.raises(RuntimeError,match='authorization evidence must be retained'):
            migration['downgrade']()
        db.rollback()
        assert db.execute(text('SELECT id,opening_authorization_version FROM stocktake_tasks ORDER BY id')).all()==[(1,None),(2,7)]
    finally:db.close();engine.dispose()


def test_empty_evidence_roundtrip_keeps_legacy_row(migration):
    engine,db=legacy_connection()
    try:
        with Operations.context(MigrationContext.configure(db)):migration['upgrade']()
        db.commit()
        with Operations.context(MigrationContext.configure(db)):migration['downgrade']()
        db.commit()
        assert [c['name'] for c in inspect(db).get_columns('stocktake_tasks')]==['id','task_type']
        assert db.execute(text('SELECT * FROM stocktake_tasks')).all()==[(1,'opening')]
    finally:db.close();engine.dispose()
