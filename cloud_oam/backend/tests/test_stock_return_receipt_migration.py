"""Acceptance schema pins, lossless transitions and durable history retention."""
from datetime import datetime, timezone
import hashlib
from migration_source_expectations import current_source_hash
from io import StringIO
from pathlib import Path
import runpy
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0104, OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0105
from app.stock_operation_models import StockOperationReceipt, StockOperationCommandSeal

PATH=Path(__file__).parents[1]/'alembic/versions/20261015_0105_stock_return_receipts.py'


def test_receipt_runtime_manifest_matches_exact_sources_and_minimum_acl():
    m=runpy.run_path(str(PATH))
    assert m['down_revision']=='20261014_0104'
    assert m['OLD_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0104['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0105['rsc_oam_runtime_binding_ready_0044()'][6]
    for table in m['TABLES']:
        assert table in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
        assert table not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES
    for key,digest in m['FUNCTION_HASHES'].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==digest
        assert key in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
        assert (key in security.MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS)==(m['FUNCTIONS'][key][1]=='void')
    for signature,(old,new) in m['_sources']().items():
        name,args=signature.removeprefix('public.').split('(');key=(name,args[:-1])
        manifest=security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256 if name=='rsc_guard_formal_file_binding_0036' else security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256
        assert old!=new and manifest[key]==current_source_hash(m['revision'],signature,new)
    for name,(table,_,function,bits,deferred) in m['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',bits,deferred,deferred,deferred)
        if table=='audit_events':assert security.EXPECTED_AUDIT_TRIGGERS[name]==(table,function,bits,deferred,deferred,deferred)


@pytest.mark.parametrize('action',['upgrade','downgrade'])
def test_receipt_transition_sql_and_private_functions_parse(action):
    m=runpy.run_path(str(PATH));output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):m[action]()
    parser=pytest.importorskip('pglast.parser');parser.parse_sql(output.getvalue())
    for key,(args,result,body) in m['FUNCTIONS'].items():
        sql=f'CREATE FUNCTION {key[0]}({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$'
        assert not sa.text(sql)._bindparams;parser.parse_plpgsql_json(sql)


@pytest.mark.parametrize('retained_kind',['receipt','seal'])
def test_receipt_migration_preserves_old_seals_and_refuses_new_history_loss(retained_kind):
    m=runpy.run_path(str(PATH));engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    with engine.connect() as db,Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql('CREATE TABLE permissions(id CHAR(32),resource TEXT,action TEXT,field_code TEXT,description TEXT,created_at DATETIME,updated_at DATETIME)')
        db.exec_driver_sql('CREATE TABLE role_permissions(id CHAR(32),role_id CHAR(32),permission_id CHAR(32),effect TEXT,created_at DATETIME)')
        for table in ('users','people','oam_work_orders','stock_operation_orders','shipments','custody_assignments','receipts','files'):
            db.exec_driver_sql(f'CREATE TABLE {table}(id CHAR(36) PRIMARY KEY)')
        for filename in ('20261011_0101_stock_return_request_seals.py','20261013_0103_stock_return_outbounds.py','20261014_0104_stock_return_shipments.py'):
            runpy.run_path(str(PATH.with_name(filename)))['upgrade']()
        old=dict(id=uuid4(),actor_user_id='synthetic-user',operator_person_id=uuid4(),oam_work_order_id=uuid4(),operation_id=uuid4(),
            operation_type='ship_return',authorization_version=1,request_id='synthetic-old-parcel-request',request_reference='synthetic-reference',
            request_hash='a'*64,created_at=datetime.now(timezone.utc))
        db.execute(StockOperationCommandSeal.__table__.insert().values(**old))
        before=tuple(db.exec_driver_sql('SELECT * FROM stock_operation_command_seals'))
        m['upgrade']();m['downgrade']()
        assert tuple(db.exec_driver_sql('SELECT * FROM stock_operation_command_seals'))==before
        m['upgrade']()
        assert db.exec_driver_sql('SELECT shipment_id FROM stock_operation_command_seals').scalar_one() is None
        from app.database import Base
        for table in m['TABLES']:
            reflected=sa.Table(table,sa.MetaData(),autoload_with=db,resolve_fks=False)
            assert set(reflected.columns.keys())==set(Base.metadata.tables[table].columns.keys())
        allowed=tuple(db.exec_driver_sql("SELECT role_id FROM role_permissions WHERE permission_id='20000000000040008000000000000068' ORDER BY role_id"))
        assert allowed==(('10000000000040008000000000000001',),('10000000000040008000000000000002',))
        if retained_kind=='seal':
            db.execute(StockOperationCommandSeal.__table__.insert().values(**{**old,'id':uuid4(),'operation_type':'receive_return',
                'shipment_id':uuid4(),'request_id':'synthetic-receipt-seal'}))
            table='stock_operation_command_seals';column='request_hash'
        else:
            db.execute(StockOperationReceipt.__table__.insert().values(id=uuid4(),shipment_id=uuid4(),actor_user_id=old['actor_user_id'],
                operator_person_id=old['operator_person_id'],authorization_version=1,target_custody_assignment_id=uuid4(),
                request_id='synthetic-receipt-request',reason='Synthetic receipt retention',plan_hash='b'*64,audit_version=1,
                command_jsonb={},plan_jsonb={},created_at=old['created_at']))
            table='stock_operation_receipts';column='plan_hash'
        preserved=tuple(db.exec_driver_sql(f'SELECT * FROM {table} ORDER BY id'))
        for sql in (f'DELETE FROM {table}',f'UPDATE {table} SET {column}={column}'):
            with pytest.raises(sa.exc.IntegrityError,match='append-only'):db.exec_driver_sql(sql)
        with pytest.raises(RuntimeError,match='immutable return acceptance or request seals must be retained'):m['downgrade']()
        assert tuple(db.exec_driver_sql(f'SELECT * FROM {table} ORDER BY id'))==preserved
