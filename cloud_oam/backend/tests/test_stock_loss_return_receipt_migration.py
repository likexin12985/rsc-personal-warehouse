"""Loss receipt migration preserves ordinary proofs and private SQL authority."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser
from migration_script_cache import cache_migration_compilation
from app import database_security as security, oam_sync_scope_security as scope


@pytest.fixture(scope='module')
def migration():
    folder=Path(__file__).parents[1]/'alembic/versions'
    with cache_migration_compilation(folder):return runpy.run_path(str(folder/'20261204_0155_stock_loss_return_receipts.py'))


def test_private_proof_catalog_and_source_chain(migration):
    m=migration
    assert m['down_revision']=='20261203_0154'
    for key,(args,result,body) in m['FUNCTIONS'].items():
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key]==hashlib.sha256(body.encode()).hexdigest()
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[key]==('f',result,False)
        assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({args}) RETURNS {result} LANGUAGE plpgsql AS $b${body}$b$')
    for key,(args,result,old,new) in m['PATCHES'].items():
        catalog = 'FORMAL_FILE_INTERNAL' if key==m['ACCOUNT_KEY'] else 'MATERIAL_REQUEST_APPROVAL'
        assert getattr(security,catalog+'_FUNCTION_BODY_SHA256_THROUGH_0154')[key]==hashlib.sha256(old.encode()).hexdigest()
        assert getattr(security,catalog+'_FUNCTION_BODY_SHA256')[key]==hashlib.sha256(new.encode()).hexdigest()
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({args}) RETURNS {result} LANGUAGE plpgsql AS $b${new}$b$')
    prefix,remainder=m['DISPATCHED'].split('    END IF;\n',1)
    assert m['BASE']==m['DISPATCHED'].split('BEGIN\n',1)[0]+'BEGIN\n'+remainder
    for version,label in (('0154','OLD'),('0155','NEW')):
        assert getattr(scope,'OAM_SYNC_FUNCTION_MANIFEST_THROUGH_'+version)['rsc_oam_runtime_binding_ready_0044()'][6]==m[label+'_READY_HASH']


@pytest.mark.parametrize('direction',('upgrade','downgrade'))
def test_atomic_owner_transition_and_retention(migration,direction):
    out=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':out})):
        migration[direction]()
    sql=out.getvalue();parser.parse_sql(sql)
    assert sql.index('direct schema owner required')<sql.index('LOCK TABLE public.alembic_version')
    assert 'DELETE FROM' not in sql and 'TRUNCATE ' not in sql and 'GRANT ' not in sql
    if direction=='downgrade':assert sql.index('history requires retention')<sql.index('EXECUTE function_definition')<sql.index('DROP TRIGGER')
    else:
        assert 'source_loss_disposition_id' in sql
        for name in migration['TRIGGERS']:assert 'ENABLE ALWAYS TRIGGER '+name in sql


def test_sqlite_source_exclusivity_retention_and_fail_closed(migration):
    engine=sa.create_engine('sqlite://')
    with engine.connect() as db,Operations.context(MigrationContext.configure(db)):
        for statement in (
            'CREATE TABLE oam_work_orders(id UUID PRIMARY KEY)',
            'CREATE TABLE stock_loss_dispositions(id UUID PRIMARY KEY)',
            'CREATE TABLE stock_operation_orders(id UUID PRIMARY KEY,loss_headquarters_decision_id UUID)',
            'CREATE TABLE stock_operation_shipments(id UUID PRIMARY KEY,operation_id UUID)',
            'CREATE TABLE stock_operation_receipts(id UUID PRIMARY KEY,shipment_id UUID)',
            'CREATE TABLE stock_operation_command_seals(id UUID PRIMARY KEY,oam_work_order_id UUID NOT NULL,operation_type TEXT NOT NULL)',
            "CREATE TABLE stock_operation_return_inbound_lines(id UUID PRIMARY KEY,line_no INT,accepted_qty NUMERIC,condition_code TEXT,CONSTRAINT ck_stock_operation_return_inbound_lines_context CHECK(line_no>0 AND accepted_qty>0 AND condition_code IN ('used','damaged')))",
            "INSERT INTO stock_operation_orders VALUES('loss','approved')",
            "INSERT INTO stock_operation_shipments VALUES('parcel','loss')",
            "CREATE TRIGGER guard_existing_seals BEFORE DELETE ON stock_operation_command_seals BEGIN SELECT RAISE(ABORT,'historical seal retained'); END",
            "CREATE TRIGGER guard_cross_table BEFORE UPDATE ON stock_operation_orders WHEN EXISTS(SELECT 1 FROM stock_operation_command_seals) BEGIN SELECT RAISE(ABORT,'existing seal dependency'); END",
        ):db.exec_driver_sql(statement)
        saved=tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"))
        db.commit();migration['upgrade']()
        assert all(db.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).scalar()==sql for name,sql in saved)
        with pytest.raises(sa.exc.IntegrityError,match='PostgreSQL loss receipt proof required'):
            db.exec_driver_sql("INSERT INTO stock_operation_receipts VALUES('receipt','parcel')")
        with pytest.raises(sa.exc.IntegrityError,match='PostgreSQL loss receipt seal proof required'):
            db.exec_driver_sql("INSERT INTO stock_operation_command_seals VALUES('seal',NULL,'receive_return','disposition')")
        with pytest.raises(sa.exc.IntegrityError,match='PostgreSQL new-condition return inbound proof required'):
            db.exec_driver_sql("INSERT INTO stock_operation_return_inbound_lines VALUES('line',1,1,'new')")
        db.exec_driver_sql("INSERT INTO stock_operation_command_seals VALUES('ordinary','wo','receive_return',NULL)")
        with pytest.raises(sa.exc.IntegrityError):
            db.exec_driver_sql("INSERT INTO stock_operation_command_seals VALUES('missing',NULL,'receive_return',NULL)")
        db.commit();migration['downgrade']()
        assert tuple(db.exec_driver_sql('SELECT * FROM stock_operation_command_seals'))==(('ordinary','wo','receive_return'),)
        assert tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"))==saved
        with pytest.raises(sa.exc.IntegrityError,match='historical seal retained'):
            db.exec_driver_sql("DELETE FROM stock_operation_command_seals WHERE id='ordinary'")
        with pytest.raises(sa.exc.IntegrityError,match='existing seal dependency'):
            db.exec_driver_sql("UPDATE stock_operation_orders SET id=id")
        migration['upgrade']()
        db.exec_driver_sql('DROP TRIGGER trg_loss_receipt_block_0155')
        db.exec_driver_sql("INSERT INTO stock_operation_receipts VALUES('retained','parcel')");db.commit()
        with pytest.raises(RuntimeError,match='history requires retention'):migration['downgrade']()
    engine.dispose()
