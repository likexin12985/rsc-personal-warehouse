"""0103 is additive and pins every replacement to its historical source."""
from io import StringIO
from pathlib import Path
import hashlib
import runpy
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import database_security as security
from migration_source_expectations import current_source_hash
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0102, OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0103

PATH = Path(__file__).parents[1]/'alembic/versions/20261013_0103_stock_return_outbounds.py'


def test_departure_runtime_manifest_matches_installed_sources_and_minimum_acl():
    m = runpy.run_path(str(PATH))
    assert m['down_revision']=='20261012_0102'
    assert m['OLD_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0102['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0103['rsc_oam_runtime_binding_ready_0044()'][6]
    for table in m['TABLES']:
        assert table in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
        assert table not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES
    for coordinate,digest in m['FUNCTION_HASHES'].items():
        body=m['FUNCTIONS'][coordinate][2]
        assert hashlib.sha256(body.encode()).hexdigest()==digest
        signature=f"public.{coordinate[0]}({coordinate[1]})"
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate]==current_source_hash(m['revision'],signature,body)
        assert coordinate in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    for signature,(old,new) in m['_sources']().items():
        name,args=signature.removeprefix('public.').split('(');coordinate=(name,args[:-1])
        manifest = security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256 if name=='rsc_require_opening_observation_account_0023' else security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256
        assert old!=new and manifest[coordinate]==current_source_hash(m['revision'],signature,new)
    for name,(table,_events,fn,bits,deferred) in m['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,fn,'A',bits,deferred,deferred,deferred)
        if table=='audit_events': assert security.EXPECTED_AUDIT_TRIGGERS[name]==(table,fn,bits,deferred,deferred,deferred)


@pytest.mark.parametrize('action',['upgrade','downgrade'])
def test_departure_transition_sql_parses_with_no_accidental_bind_parameters(action):
    m=runpy.run_path(str(PATH));output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        m[action]()
    parser=pytest.importorskip('pglast.parser')
    parser.parse_sql(output.getvalue())
    for name_signature,(args,result,body) in m['FUNCTIONS'].items():
        sql=f'CREATE FUNCTION {name_signature[0]}({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$'
        assert not sa.text(sql)._bindparams
        parser.parse_plpgsql_json(sql)


@pytest.mark.parametrize('retained_kind',['outbound_return_seal','outbound_fact'])
def test_upgrade_preserves_old_seals_and_downgrade_retains_new_history(retained_kind):
    from datetime import datetime,timezone
    from uuid import uuid4
    from app.stock_operation_models import StockOperationCommandSeal,StockOperationOutbound
    previous=runpy.run_path(str(PATH.with_name('20261011_0101_stock_return_request_seals.py')))
    m=runpy.run_path(str(PATH));engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    with engine.connect() as db,Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql('CREATE TABLE permissions(id CHAR(32),resource TEXT,action TEXT,field_code TEXT,description TEXT,created_at DATETIME,updated_at DATETIME)')
        db.exec_driver_sql('CREATE TABLE role_permissions(id CHAR(32),role_id CHAR(32),permission_id CHAR(32),effect TEXT,created_at DATETIME)')
        for table in ('users','people','oam_work_orders','stock_operation_orders'):
            db.exec_driver_sql(f'CREATE TABLE {table}(id CHAR(36) PRIMARY KEY)')
        previous['upgrade']()
        old=dict(id=uuid4(),actor_user_id='synthetic-user',operator_person_id=uuid4(),oam_work_order_id=uuid4(),operation_id=None,
            operation_type='submit_return',authorization_version=1,request_id='synthetic-old-request',request_reference='synthetic-old-reference',request_hash='a'*64,created_at=datetime.now(timezone.utc))
        db.execute(StockOperationCommandSeal.__table__.insert().values(**old))
        preserved=tuple(db.exec_driver_sql('SELECT * FROM stock_operation_command_seals'))
        m['upgrade']();m['downgrade']();m['upgrade']()
        assert tuple(db.exec_driver_sql('SELECT * FROM stock_operation_command_seals'))==preserved
        for sql in ('DELETE FROM stock_operation_command_seals','UPDATE stock_operation_command_seals SET request_hash=request_hash'):
            with pytest.raises(sa.exc.IntegrityError,match='append-only'):db.exec_driver_sql(sql)
        if retained_kind=='outbound_return_seal':
            row={**old,'id':uuid4(),'operation_type':'outbound_return','operation_id':uuid4(),'request_id':'synthetic-departure-request'}
            db.execute(StockOperationCommandSeal.__table__.insert().values(**row))
            table='stock_operation_command_seals'
        else:
            row=dict(id=uuid4(),outbound_no='RET-OUT-SYNTHETIC',operation_id=uuid4(),status='outbound',actor_user_id=old['actor_user_id'],
                operator_person_id=old['operator_person_id'],target_custody_assignment_id=uuid4(),authorization_version=1,reason='Synthetic migration retention',
                outbound_at=old['created_at'],created_at=old['created_at'],request_id='synthetic-departure-request',idempotency_key_hash='b'*64,
                request_hash='c'*64,plan_hash='d'*64,command_jsonb={},plan_jsonb={},posting_transaction_id=uuid4())
            db.execute(StockOperationOutbound.__table__.insert().values(**row));table='stock_operation_outbounds'
        before=tuple(db.exec_driver_sql(f'SELECT * FROM {table} ORDER BY id'))
        for sql in (f'DELETE FROM {table}',f'UPDATE {table} SET request_hash=request_hash'):
            with pytest.raises(sa.exc.IntegrityError,match='append-only'):db.exec_driver_sql(sql)
        with pytest.raises(RuntimeError,match='immutable physical departure history or request seals must be retained'):m['downgrade']()
        assert tuple(db.exec_driver_sql(f'SELECT * FROM {table} ORDER BY id'))==before
