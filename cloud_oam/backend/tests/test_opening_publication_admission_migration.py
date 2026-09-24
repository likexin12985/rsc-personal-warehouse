from io import StringIO
from pathlib import Path
import runpy,hashlib
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import database_security as security,oam_sync_scope_security as scope
PATH=Path(__file__).parents[1]/'alembic/versions/20261104_0125_opening_publication_admission.py'

def test_exact_capabilities_and_hashes():
    m=runpy.run_path(str(PATH))
    for name,(args,params,returns,body,api) in m['FUNCTIONS'].items():
        key=(name,args)
        metadata=security.RUNTIME_EXECUTE_FUNCTIONS if api else security.FORMAL_FILE_INTERNAL_FUNCTIONS
        shapes=security.RUNTIME_FUNCTION_SHAPES if api else security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES
        hashes=security.RUNTIME_FUNCTION_BODY_SHA256 if api else security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256
        assert metadata[key]==('v',True,'plpgsql',('search_path=pg_catalog, public',))
        assert shapes[key]==('f',returns,False) and hashes[key]==hashlib.sha256(body.encode()).hexdigest()
        pytest.importorskip('pglast.parser').parse_plpgsql_json(f'CREATE FUNCTION {name}({params}) RETURNS {returns} LANGUAGE plpgsql AS $body$'+body+'$body$')
    assert m['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0124['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0125['rsc_oam_runtime_binding_ready_0044()'][6]
    assert 'auth_sessions' not in m['ASSERT_BODY'] and 'actor_user_id' not in m['ASSERT_BODY']

@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_transactional_ddl_and_insert_only_guards(direction):
    m=runpy.run_path(str(PATH));output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):m[direction]()
    sql=output.getvalue();pytest.importorskip('pglast.parser').parse_sql(sql)
    assert 'direct schema owner required' in sql and 'a.is_grantable' in sql and '0125 admission trigger drift' in sql
    for forbidden in ('GRANT SELECT','GRANT UPDATE','DROP TABLE','DELETE FROM public','CREATE OR REPLACE'):assert forbidden not in sql
    if direction=='upgrade':assert 'BEFORE INSERT ON public.stocktake_tasks' in sql and 'AFTER INSERT ON public.stocktake_tasks DEFERRABLE INITIALLY DEFERRED' in sql
    else:assert sql.index('0125 function source or ACL drift')<sql.index('DROP TRIGGER')
