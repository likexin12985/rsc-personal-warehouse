"""0110 SQL-created seals: retention, namespace rejection and catalog pins.

The fixture applies 0106 and 0110 to isolated metadata prerequisites. PG16
commit/concurrency proofs must run in the protected disposable release gate.
"""
import hashlib
from io import StringIO
from pathlib import Path
import re
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.database import Base
from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0110, OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0109
from app.stock_operation_models import StockOperationReturnInboundSeal
from test_stock_return_inbound_seals import (world, stock, recovered, destination, prepared, parcel,
    incoming, acceptance, inbound_accounts, accepted, seal_arguments, recovery, snapshot)

FOLDER=Path(__file__).parents[1]/'alembic/versions'
PATH=FOLDER/'20261020_0110_stock_return_inbound_seals.py'


@pytest.fixture
def db():
    before=runpy.run_path(str(FOLDER/'20261016_0106_stock_return_inbounds.py'))
    migration=runpy.run_path(str(PATH))
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    @sa.event.listens_for(engine,'connect')
    def fk(connection,_):connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine,tables=[table for table in Base.metadata.tables.values()
        if table.name not in (*before['TABLES'],migration['TABLE'])])
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        before['upgrade']();migration['upgrade']()
    with Session(engine) as session:yield session
    engine.dispose()


def test_migrated_seal_is_immutable_retained_and_exactly_replayed(db,accepted):
    migration=runpy.run_path(str(PATH));table=migration['TABLE']
    reflected=sa.Table(table,sa.MetaData(),autoload_with=db.connection(),resolve_fks=False)
    assert set(reflected.columns.keys())==set(Base.metadata.tables[table].columns.keys())
    _,args=seal_arguments(db,accepted)
    original=recovery.seal_return_inbound_request(db,**args);db.commit();before=snapshot(db)
    for sql in (f'DELETE FROM {table}',f'UPDATE {table} SET id=id'):
        with pytest.raises(sa.exc.IntegrityError,match='append-only'),db.begin_nested():db.execute(sa.text(sql))
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='seals must be retained'):migration['downgrade']()
    assert snapshot(db)==before
    assert recovery.seal_return_inbound_request(db,**args)==original


def test_empty_0110_roundtrip_preserves_prerequisites(db):
    migration=runpy.run_path(str(PATH))
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']()
        assert migration['TABLE'] not in sa.inspect(db.connection()).get_table_names()
        assert 'stock_operation_command_seals' in sa.inspect(db.connection()).get_table_names()
        migration['upgrade']()
    db.commit()
    assert migration['TABLE'] in sa.inspect(db.connection()).get_table_names()


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_raw_insert_cannot_reuse_acceptance_request(db,accepted):
    migration=runpy.run_path(str(PATH));_,args=seal_arguments(db,accepted)
    recovery.seal_return_inbound_request(db,**args);db.commit()
    row=db.scalar(sa.select(StockOperationReturnInboundSeal))
    from app.stock_operation_models import StockOperationReceipt
    receipt=db.get(StockOperationReceipt,accepted.receipt.receipt_id)
    copy={col.name:getattr(row,col.name) for col in row.__table__.columns}
    from uuid import uuid4
    copy.update(id=uuid4(),request_id=receipt.request_id)
    with pytest.raises(sa.exc.IntegrityError,match='namespace conflict'),db.begin_nested():
        db.execute(sa.insert(StockOperationReturnInboundSeal).values(**copy))


def test_0110_hash_chain_acl_trigger_inventory_and_postgresql_syntax():
    migration=runpy.run_path(str(PATH));key=(migration['FUNCTION_NAME'],'')
    assert migration['OLD_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0109['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0110['rsc_oam_runtime_binding_ready_0044()'][6]
    origin=runpy.run_path(str(FOLDER/'20260903_0047_nonopening_stocktake_start_causality.py'))
    sql=origin['_oam_runtime_ready_function_sql'](origin['revision'])
    body=re.search(r'AS \$\$([\s\S]*?)\$\$',sql).group(1)
    for revision,digest in ((migration['down_revision'],migration['OLD_HASH']),(migration['revision'],migration['NEW_HASH'])):
        assert hashlib.sha256(body.replace(origin['revision'],revision).encode()).hexdigest()==digest
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==migration['FUNCTION_HASH']
    assert key in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
    table=migration['TABLE']
    assert table in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
    assert table not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES
    for name,(table,_,function,bits,deferred) in migration['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',bits,deferred,deferred,deferred)
    parser=pytest.importorskip('pglast.parser')
    parser.parse_plpgsql_json(f"CREATE FUNCTION {key[0]}() RETURNS trigger LANGUAGE plpgsql AS $body${migration['CHECK_BODY']}$body$")
    for direction in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):migration[direction]()
        parser.parse_sql(output.getvalue())


@pytest.mark.parametrize('field,value',[('source_body','BEGIN RETURN NULL; END;'),('owner_name','star_oam_api'),('can_execute',True),('configuration',['search_path=public'])])
def test_0110_function_catalog_drift_is_rejected(monkeypatch,field,value):
    from test_database_security import _valid_material_request_approval_function_rows,_assert_valid_material_request_approval_catalog
    rows=_valid_material_request_approval_function_rows(monkeypatch)
    next(row for row in rows if row['function_name']=='rsc_guard_return_inbound_seal_0110')[field]=value
    with pytest.raises(security.DatabaseSecurityBoundaryError):_assert_valid_material_request_approval_catalog(monkeypatch,functions=rows)


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_upgrade_preserves_legacy_acceptance_seals_and_refuses_existing_request_collision(db,accepted):
    from uuid import uuid4
    from app.stock_operation_models import StockOperationCommandSeal
    from app.formal_services.stock_return_receipt_recovery import seal_receipt_request
    from app.formal_services import inventory_posting as posting
    from test_stock_return_inbound import command,commands
    migration=runpy.run_path(str(PATH))
    original=seal_receipt_request(db,actor=accepted.actor,shipment_id=accepted.receipt.shipment_id,
        request_id=uuid4().hex,request_hash='a'*64);db.commit()
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']();migration['upgrade']()
    db.commit()
    legacy=db.get(StockOperationCommandSeal,original.seal.seal_id)
    assert legacy.request_hash=='a'*64 and legacy.operation_type=='receive_return'
    value=command(db,accepted);commands.execute_return_inbound(db,**value);db.commit()
    with Operations.context(MigrationContext.configure(db.connection())):migration['downgrade']()
    # Model an ambiguous 0109-era database without disabling any constraints.
    # The old acceptance seal model has no inbound receipt association.
    copy={col.name:getattr(legacy,col.name) for col in legacy.__table__.columns}
    copy.update(id=uuid4(),request_id=value['request_id'],request_reference=posting._request_reference(value['request_id']))
    db.execute(sa.insert(StockOperationCommandSeal).values(**copy));db.commit()
    before=tuple(db.execute(sa.text('SELECT id,request_id,request_hash FROM stock_operation_command_seals ORDER BY id')))
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='pre-existing inbound request conflict'):migration['upgrade']()
    assert migration['TABLE'] not in sa.inspect(db.connection()).get_table_names()
    assert tuple(db.execute(sa.text('SELECT id,request_id,request_hash FROM stock_operation_command_seals ORDER BY id')))==before
