"""State/ACL regression tests for the one-column notification expansion grant."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from app import database_security as security, oam_sync_scope_security as scope

PATH = Path(__file__).parents[1] / 'alembic/versions/20261108_0129_notification_expansion_status.py'


@pytest.fixture(scope='module')
def migration():
    return runpy.run_path(str(PATH))


def test_exact_one_column_acl_and_private_guard_manifest(migration):
    key = (migration['FUNCTION'], '')
    assert security.RUNTIME_UPDATE_COLUMNS['notification_events'] == frozenset({'status'})
    assert 'notification_events' not in security.RUNTIME_UPDATE_TABLES
    assert 'notification_recipients' not in security.RUNTIME_UPDATE_COLUMNS
    assert 'notification_recipients' not in security.RUNTIME_UPDATE_TABLES
    assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
    assert security.FORMAL_FILE_INTERNAL_FUNCTIONS[key] == ('v',False,'plpgsql',('search_path=pg_catalog, public',))
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[key] == ('f','trigger',False)
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key] == hashlib.sha256(migration['BODY'].encode()).hexdigest()
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key] == migration['BODY_HASH']
    assert key not in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    for name, (_, kind) in migration['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == ('notification_events',key[0],'A',kind,False,False,False)
    assert migration['OLD_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0128['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0129['rsc_oam_runtime_binding_ready_0044()'][6]


@pytest.mark.parametrize('action', ['upgrade','downgrade'])
def test_migration_locks_verifies_and_preserves_facts(migration,action):
    parser=pytest.importorskip('pglast.parser')
    parser.parse_plpgsql_json('CREATE FUNCTION proof() RETURNS trigger LANGUAGE plpgsql AS $x$'+migration['BODY']+'$x$')
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        migration[action]()
    sql=output.getvalue();parser.parse_sql(sql)
    assert 'direct schema owner required' in sql and 'ACCESS EXCLUSIVE MODE' in sql
    assert 'DELETE FROM' not in sql and 'DROP TABLE' not in sql and 'CREATE OR REPLACE' not in sql
    assert sql.index('notification runtime ACL drift') < sql.index('CREATE FUNCTION' if action=='upgrade' else 'REVOKE UPDATE')
    if action=='upgrade':
        assert sql.index('ENABLE ALWAYS TRIGGER') < sql.index('GRANT UPDATE(status)')
        assert 'SECURITY INVOKER' in sql and 'GRANT UPDATE ON' not in sql
    else:
        assert sql.index('notification event trigger drift') < sql.index('DROP TRIGGER')
        assert sql.index('REVOKE UPDATE(status)') < sql.index('DROP TRIGGER')


@pytest.fixture
def db(migration):
    engine=create_engine('sqlite+pysqlite:///:memory:')
    with engine.connect() as connection:
        connection.execute(text('CREATE TABLE notification_events (id TEXT PRIMARY KEY,event_type TEXT,business_type TEXT,business_id TEXT,dedup_key TEXT,payload_jsonb TEXT,occurred_at TEXT,created_at TEXT,target_manifest_sha256 TEXT,status TEXT NOT NULL)'))
        connection.execute(text('CREATE TABLE notification_recipients (id TEXT PRIMARY KEY,event_id TEXT,status TEXT)'))
        connection.execute(text('CREATE TABLE notification_target_bindings (recipient_id TEXT,target_id TEXT)'))
        connection.execute(text('CREATE TABLE notification_person_targets (id TEXT PRIMARY KEY,event_id TEXT)'))
        connection.execute(text('CREATE TABLE notification_deliveries (recipient_id TEXT)'))
        connection.execute(text("INSERT INTO notification_events VALUES ('event','type','business','object','key','{}','2026-09-21','2026-09-21',NULL,'pending')"));connection.commit()
        with Operations.context(MigrationContext.configure(connection)):migration['upgrade']()
        connection.commit();yield connection
    engine.dispose()


@pytest.mark.parametrize('column', ['id','event_type','business_type','business_id','dedup_key','payload_jsonb','occurred_at','created_at','target_manifest_sha256'])
def test_content_columns_cannot_change(db,column):
    with pytest.raises(IntegrityError):db.execute(text(f"UPDATE notification_events SET {column}='changed'"))
    db.rollback();assert db.scalar(text('SELECT status FROM notification_events'))=='pending'


def test_noop_forward_cancel_and_populated_downgrade_preserve_facts(db,migration):
    for state in ('pending','expanded','expanded','cancelled','cancelled'):
        db.execute(text('UPDATE notification_events SET status=:state'),{'state':state});db.commit()
    for state in ('pending','expanded'):
        with pytest.raises(IntegrityError):db.execute(text('UPDATE notification_events SET status=:state'),{'state':state})
        db.rollback()
    with pytest.raises(IntegrityError):db.execute(text('DELETE FROM notification_events'))
    db.rollback();before=db.execute(text('SELECT * FROM notification_events')).all();db.commit()
    with Operations.context(MigrationContext.configure(db)):migration['downgrade']()
    db.commit();assert db.execute(text('SELECT * FROM notification_events')).all()==before;db.commit()
    with Operations.context(MigrationContext.configure(db)):migration['upgrade']()
    db.commit();assert db.execute(text('SELECT * FROM notification_events')).all()==before


@pytest.mark.parametrize('binding,status,delivered,target_event,allowed', [
    (False,'active',False,'event',False),
    (True,'suppressed',False,'event',False),
    (True,'active',True,'event',False),
    (True,'active',False,'other-event',False),
    (True,'active',False,'event',True),
])
def test_only_outstanding_retained_binding_can_reopen_expansion(db,binding,status,delivered,target_event,allowed):
    db.execute(text("UPDATE notification_events SET status='expanded'"))
    db.execute(text("INSERT INTO notification_recipients VALUES ('recipient','event',:status)"),{'status':status})
    if binding:
        db.execute(text("INSERT INTO notification_person_targets VALUES ('target',:event)"),{'event':target_event})
        db.execute(text("INSERT INTO notification_target_bindings VALUES ('recipient','target')"))
    if delivered:db.execute(text("INSERT INTO notification_deliveries VALUES ('recipient')"))
    db.commit()
    if allowed:
        db.execute(text("UPDATE notification_events SET status='pending'"));db.commit()
        assert db.scalar(text('SELECT status FROM notification_events'))=='pending'
    else:
        with pytest.raises(IntegrityError):db.execute(text("UPDATE notification_events SET status='pending'"))
        db.rollback();assert db.scalar(text('SELECT status FROM notification_events'))=='expanded'
