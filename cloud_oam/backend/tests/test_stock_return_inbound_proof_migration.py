"""0111 retention and catalog checks; these do not replace the PG16 gate."""
import hashlib
from migration_source_expectations import current_source_hash
from io import StringIO
from pathlib import Path
import re
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0111, OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0110
from test_stock_return_inbound_seal_migration import db
from test_stock_return_inbound import (world,stock,recovered,destination,prepared,parcel,incoming,acceptance,
    inbound_accounts,accepted,command,commands,snapshot)

FOLDER=Path(__file__).parents[1]/'alembic/versions'
PATH=FOLDER/'20261021_0111_stock_return_inbound_proof.py'


def test_empty_transition_preserves_schema_and_existing_acceptance(db,accepted):
    migration=runpy.run_path(str(PATH));before=snapshot(db)
    tables=sa.inspect(db.connection()).get_table_names()
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['upgrade']();migration['downgrade']();migration['upgrade']()
    assert tables==sa.inspect(db.connection()).get_table_names() and before==snapshot(db)


def test_both_transitions_refuse_populated_inbound_without_rewriting(db,accepted):
    migration=runpy.run_path(str(PATH))
    result=commands.execute_return_inbound(db,**command(db,accepted));db.commit();before=snapshot(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        for direction,reason in [('upgrade','require investigation'),('downgrade','proof must be retained')]:
            with pytest.raises(RuntimeError,match=reason):migration[direction]()
            assert snapshot(db)==before
    assert db.scalar(sa.text('SELECT count(*) FROM stock_operation_return_inbounds'))==1
    assert result['status']=='posted'


def test_0111_exact_forward_dispatch_readiness_acl_and_sql_syntax():
    migration=runpy.run_path(str(PATH));parser=pytest.importorskip('pglast.parser')
    assert migration['OLD_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0110['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0111['rsc_oam_runtime_binding_ready_0044()'][6]
    origin=runpy.run_path(str(FOLDER/'20260903_0047_nonopening_stocktake_start_causality.py'))
    body=re.search(r'AS \$\$([\s\S]*?)\$\$',origin['_oam_runtime_ready_function_sql'](origin['revision'])).group(1)
    assert hashlib.sha256(body.replace(origin['revision'],migration['revision']).encode()).hexdigest()==migration['NEW_HASH']
    old=runpy.run_path(str(FOLDER/'20261013_0103_stock_return_outbounds.py'))['DISPATCH_BODY']
    before=migration['OLD_BRANCH'];after=migration['NEW_BRANCH']
    assert old.count(before)==1 and after not in old
    updated=old.replace(before,after)
    assert updated.count(after)==1 and before not in updated
    assert updated.replace(after,before)==old
    assert current_source_hash(migration['revision'],'public.rsc_dispatch_stock_return_outbound_0103()',updated)==security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[('rsc_dispatch_stock_return_outbound_0103','')]
    parser.parse_plpgsql_json(f'CREATE FUNCTION departure() RETURNS trigger LANGUAGE plpgsql AS $body${updated}$body$')
    for key,(args,result,body) in migration['FUNCTIONS'].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==migration['FUNCTION_HASHES'][key]
        assert key in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
        assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
        parser.parse_plpgsql_json(f'CREATE FUNCTION {key[0]}({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$')
    for name,(table,function) in migration['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',5,True,True,True)
    for direction in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):migration[direction]()
        sql=output.getvalue();parser.parse_sql(sql)
        assert sql.index('LOCK TABLE')<sql.index('pre-existing inbound facts' if direction=='upgrade' else 'proof must be retained')


def test_pg16_inbound_gate_loads_with_test_configuration_without_database_calls():
    # The protected entry imports this helper only after provisioning. Catch
    # syntax and import failures locally without fabricating CI credentials.
    from pg16_stock_return_inbound_gate import assert_return_inbound_gate
    from pg16_stock_return_outbound_gate import prepare_departure_worlds
    assert callable(assert_return_inbound_gate) and callable(prepare_departure_worlds)


@pytest.mark.parametrize('name',['rsc_check_stock_return_inbound_0111','rsc_dispatch_stock_return_inbound_0111'])
@pytest.mark.parametrize('field,value',[('source_body','BEGIN RETURN NULL; END;'),('owner_name','star_oam_api'),('can_execute',True),('configuration',['search_path=public'])])
def test_0111_catalog_drift_fails_closed(monkeypatch,name,field,value):
    from test_database_security import _valid_material_request_approval_function_rows,_assert_valid_material_request_approval_catalog
    rows=_valid_material_request_approval_function_rows(monkeypatch)
    next(row for row in rows if row['function_name']==name)[field]=value
    with pytest.raises(security.DatabaseSecurityBoundaryError):_assert_valid_material_request_approval_catalog(monkeypatch,functions=rows)
