import hashlib
from io import StringIO
from pathlib import Path
import runpy

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from app import database_security as security,oam_sync_scope_security as scope

PATH=Path(__file__).parents[1]/'alembic/versions/20261103_0124_opening_control_directory.py'


def test_exact_api_capability_and_no_private_table_acl_or_lock():
    m=runpy.run_path(str(PATH));key=(m['FUNCTION'],m['ARGUMENTS'])
    assert security.RUNTIME_EXECUTE_FUNCTIONS[key]==('s',True,'plpgsql',('search_path=pg_catalog, public',))
    assert security.RUNTIME_FUNCTION_SHAPES[key]==('f','jsonb',False)
    assert security.RUNTIME_FUNCTION_BODY_SHA256[key]==hashlib.sha256(m['BODY'].encode()).hexdigest()
    assert m['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0123['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0124['rsc_oam_runtime_binding_ready_0044()'][6]
    for forbidden in ('FOR UPDATE','FOR SHARE','pg_advisory','auth_sessions','storage_key','payload_jsonb','control_qty'):
        assert forbidden not in m['BODY']
    pytest.importorskip('pglast.parser').parse_plpgsql_json('CREATE FUNCTION sample() RETURNS jsonb LANGUAGE plpgsql AS $body$'+m['BODY']+'$body$')


@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_transactional_ddl_preflight_and_bounded_grant(direction):
    m=runpy.run_path(str(PATH));output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        m[direction]()
    sql=output.getvalue();pytest.importorskip('pglast.parser').parse_sql(sql)
    assert m['BODY_HASH'] in sql and 'direct schema owner required' in sql
    assert 'a.is_grantable' in sql and 'LOCK TABLE public.alembic_version' in sql
    for forbidden in ('GRANT SELECT','GRANT UPDATE','DROP TABLE','DELETE FROM','CREATE OR REPLACE FUNCTION public.rsc_opening'):
        assert forbidden not in sql
    if direction=='downgrade': assert sql.index('directory function source or ACL drift')<sql.index('DROP FUNCTION')


def test_sqlite_downgrade_preserves_all_existing_facts():
    m=runpy.run_path(str(PATH));engine=create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql('CREATE TABLE retained(id INTEGER PRIMARY KEY, value TEXT)')
        db.exec_driver_sql("INSERT INTO retained VALUES(1,'history')")
        before=tuple(db.exec_driver_sql('SELECT * FROM sqlite_master'))
        m['upgrade']();m['downgrade']()
        assert tuple(db.exec_driver_sql('SELECT * FROM sqlite_master'))==before
        assert db.exec_driver_sql('SELECT value FROM retained').scalar()=='history'
