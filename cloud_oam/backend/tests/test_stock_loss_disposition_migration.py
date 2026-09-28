"""0150 exact source transition, immutable schema and minimum role catalog."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser
import pytest
import sqlalchemy as sa

from app import database_security as security, oam_sync_scope_security as scope
from app.stock_operation_models import StockLossDisposition
from migration_script_cache import cache_migration_compilation


@pytest.fixture(scope='module')
def migration():
    folder=Path(__file__).parents[1]/'alembic/versions'
    with cache_migration_compilation(folder):
        return runpy.run_path(str(folder/'20261129_0150_stock_loss_disposition.py'))


def test_exact_private_catalog_and_no_public_activation(migration):
    m=migration
    assert m['OLD_READY_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0149['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_READY_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0150['rsc_oam_runtime_binding_ready_0044()'][6]
    for coordinate,(args,result,body) in m['FUNCTIONS'].items():
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0150[coordinate]==hashlib.sha256(body.encode()).hexdigest()
        assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({args}) RETURNS {result} LANGUAGE plpgsql AS $b${body}$b$')
        assert not sa.text(body)._bindparams
    for signature,(old,new) in m['_sources']().items():
        name,args=signature.removeprefix('public.').rstrip(')').split('(',1)
        coordinate=name,args
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0149[coordinate]==hashlib.sha256(old.encode()).hexdigest()
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0150[coordinate]==hashlib.sha256(new.encode()).hexdigest()
        parameters={'uuid':'checked_account uuid' if 'hold' in name else 'checked_tx uuid','':''}[args]
        result='void' if args else 'trigger'
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({parameters}) RETURNS {result} LANGUAGE plpgsql AS $b${new}$b$')
        assert not sa.text(new)._bindparams
    for name,(table,_,function,kind,deferred) in m['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',kind,deferred,deferred,deferred)
    assert m['TABLE'] in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
    assert m['TABLE'] not in security.RUNTIME_UPDATE_TABLES
    assert 'rsc_assert_loss_submit_authority_0145' not in m['CHECK_BODY']
    assert 'rsc_assert_loss_headquarters_authority_0148' not in m['CHECK_BODY']
    assert 'rsc_check_loss_disposition_0150(identifier,false)' in m['HOLD_BODY']
    assert 'reversed_transaction_id' in m['TRANSACTION_BRANCH']


@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_locked_atomic_sql_and_history_retention(migration,direction):
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        migration[direction]()
    sql=output.getvalue();parser.parse_sql(sql)
    assert sql.index('direct schema owner required')<sql.index('LOCK TABLE public.alembic_version')<sql.index('LOCK TABLE public.inventory_ledger_heads')
    assert 'INSERT INTO permissions' not in sql and 'DELETE FROM' not in sql
    if direction=='downgrade':assert sql.index('immutable disposition history requires retention')<sql.index('DROP FUNCTION')
    else:
        assert 'GRANT SELECT,INSERT ON public.stock_loss_dispositions TO star_oam_api' in sql
        assert 'ENABLE ALWAYS TRIGGER trg_loss_disposition_immutable_0150' in sql


def test_empty_sqlite_schema_roundtrip_matches_model_and_cannot_execute(migration):
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as db,Operations.context(MigrationContext.configure(db)):
            migration['upgrade']()
            assert {c['name'] for c in sa.inspect(db).get_columns(migration['TABLE'])}==set(StockLossDisposition.__table__.columns.keys())-{'return_operation_id'}
            db.commit()
            with pytest.raises(sa.exc.DBAPIError,match='0150 PostgreSQL disposition proof required'):
                db.execute(sa.text('INSERT INTO stock_loss_dispositions DEFAULT VALUES'))
            db.rollback()
            migration['downgrade']();migration['upgrade']()
            # Preserve even malformed local history, never discard it to downgrade.
            db.execute(sa.text('DROP TRIGGER trg_loss_disposition_insert_0150'))
            db.execute(sa.text('PRAGMA ignore_check_constraints=ON'))
            columns=sa.inspect(db).get_columns(migration['TABLE'])
            values={c['name']:1 if isinstance(c['type'],(sa.Numeric,sa.Integer)) else '{}' if isinstance(c['type'],sa.JSON) else 'synthetic' for c in columns}
            db.execute(sa.text('INSERT INTO stock_loss_dispositions ('+','.join(values)+') VALUES ('+','.join(':'+k for k in values)+')'),values)
            with pytest.raises(RuntimeError,match='0150 immutable disposition history requires retention'):migration['downgrade']()
    finally:engine.dispose()
