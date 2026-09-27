"""Exact loss proofs replace only admission and typed dispatch, never returns."""
import hashlib
from migration_source_expectations import current_source_hash
from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser
import pytest
import sqlalchemy as sa

from app import database_security as security, oam_sync_scope_security as scope
from migration_script_cache import cache_migration_compilation


@pytest.fixture(scope='module')
def migration():
    folder=Path(__file__).parents[1]/'alembic/versions'
    with cache_migration_compilation(folder):
        return runpy.run_path(str(folder/'20261124_0145_stock_loss_submission_proof.py'))


def test_private_complete_proof_catalog_and_inherited_returns(migration):
    m=migration
    for coordinate,(args,result,body) in m['FUNCTIONS'].items():
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0149[coordinate]==hashlib.sha256(body.encode()).hexdigest()
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate]==('f',result,False)
        assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({args}) RETURNS {result} LANGUAGE plpgsql AS $b${body}$b$')
        assert not sa.text(body)._bindparams
    for signature,(old,new) in m['_sources']().items():
        assert old!=new
        parser.parse_plpgsql_json('CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $b$'+new+'$b$')
        coordinate=(signature.removeprefix('public.').split('(')[0],'')
        catalog=security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256 if 'dispatch' in signature else security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256
        assert catalog[coordinate]==current_source_hash(m['revision'],signature,new)
    dispatcher=m['_sources']()['public.rsc_dispatch_stock_return_0100()'][1]
    assert 'PERFORM public.rsc_check_stock_return_0100(checked_order, checked_cancel);' in dispatcher
    assert dispatcher.index('-- Unrelated event aggregates')<dispatcher.index('FOR UPDATE')
    assert 'rsc_check_stock_return_0100(uuid, uuid)' not in m['_sources']()
    assert m['NEW_READY_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0145['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['OLD_READY_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0144['rsc_oam_runtime_binding_ready_0044()'][6]
    for name,(table,function) in m['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',5,True,True,True)
    for table in ('stock_operation_orders','stock_operation_lines'):
        assert f'trg_{table}_loss_admission_0142' not in security.EXPECTED_FORMAL_FILE_TRIGGERS
        assert f'trg_{table}_proof_0100' in security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS


@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_ddl_is_atomic_owner_only_and_retains_loss_history(migration,direction):
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        migration[direction]()
    sql=output.getvalue();parser.parse_sql(sql)
    assert sql.index('LOCK TABLE public.alembic_version')<sql.index('LOCK TABLE public.inventory_ledger_heads')
    assert 'INSERT INTO permissions' not in sql and 'DELETE FROM' not in sql
    if direction=='upgrade':
        assert sql.index('CREATE FUNCTION public.rsc_check_loss_submission_0145')<sql.index('DROP TRIGGER trg_stock_operation_orders_loss_admission_0142')
    else:
        assert sql.index('history requires retention')<sql.index('DROP FUNCTION')


@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_even_one_loss_prevents_historical_schema_transition(migration,direction):
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            db.exec_driver_sql('CREATE TABLE stock_operation_orders(id TEXT,operation_type TEXT)')
            db.exec_driver_sql("INSERT INTO stock_operation_orders VALUES ('synthetic','loss_report')")
            with pytest.raises(RuntimeError,match='history requires retention'):
                migration[direction]()
            assert db.scalar(sa.text('SELECT count(*) FROM stock_operation_orders'))==1
    finally:
        engine.dispose()
