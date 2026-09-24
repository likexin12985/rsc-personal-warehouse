"""0127 renames one ambiguous variable and preserves all reconciliation guards."""
from io import StringIO
from pathlib import Path
import hashlib, runpy
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from app import database_security as security,oam_sync_scope_security as scope

PATH=Path(__file__).parents[1]/'alembic/versions/20261106_0127_reconciliation_event_binding.py'

@pytest.fixture(scope='module')
def migration():return runpy.run_path(str(PATH))


def test_exact_private_invoker_source_only_variable_is_renamed(migration):
    m=migration;key=(m['FUNCTION'],'')
    assert m['FIXED_BODY'].replace('expected_reconciliation_event','event_type')==m['LEGACY_BODY']
    assert m['replacement_count']==12 and m['FIXED_BODY'].count('outbox.event_type = expected_reconciliation_event')==2
    assert hashlib.sha256(m['FIXED_BODY'].encode()).hexdigest()==m['FIXED_HASH']
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==m['FIXED_HASH']
    assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
    assert key not in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    assert m['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0126['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0127['rsc_oam_runtime_binding_ready_0044()'][6]
    pytest.importorskip('pglast.parser').parse_plpgsql_json('CREATE FUNCTION checked() RETURNS trigger LANGUAGE plpgsql AS $$'+m['FIXED_BODY']+'$$')


@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_transition_pins_source_owner_acl_and_both_existing_triggers_before_replace(migration,direction):
    out=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':out})):
        migration[direction]()
    sql=out.getvalue();pytest.importorskip('pglast.parser').parse_sql(sql)
    assert 'ACCESS EXCLUSIVE MODE' in sql and 'NOT p.prosecdef' in sql and 'a.grantee<>p.proowner' in sql
    assert migration['LEGACY_HASH'] in sql and migration['FIXED_HASH'] in sql
    assert sql.index('0127 reconciliation source, owner or private ACL drift')<sql.index('EXECUTE function_definition')
    assert 'function_oid' in sql and 'original_acl' in sql and 'original_security' in sql
    for forbidden in ('GRANT ','CREATE TABLE ','DROP TABLE ','DROP TRIGGER ','DELETE FROM '):assert forbidden not in sql


def test_sqlite_roundtrip_keeps_existing_facts_and_schema(migration):
    engine=create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql('CREATE TABLE retained(id INTEGER PRIMARY KEY,value TEXT)')
        db.exec_driver_sql("INSERT INTO retained VALUES(1,'historical reconciliation')")
        schema=tuple(db.exec_driver_sql('SELECT * FROM sqlite_master'))
        migration['upgrade']();migration['downgrade']();migration['upgrade']()
        assert tuple(db.exec_driver_sql('SELECT * FROM sqlite_master'))==schema
        assert db.exec_driver_sql('SELECT * FROM retained').one()==(1,'historical reconciliation')
    engine.dispose()
