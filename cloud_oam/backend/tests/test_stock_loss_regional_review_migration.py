"""Schema, private proof catalog, immutable history and closed SQLite path."""
from io import StringIO
from pathlib import Path
import runpy
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser
import pytest
import sqlalchemy as sa

from app import database_security as security,oam_sync_scope_security as scope
from app.stock_operation_models import StockLossRegionalReview
from migration_script_cache import cache_migration_compilation


@pytest.fixture(scope='module')
def migration():
    folder=Path(__file__).parents[1]/'alembic/versions'
    with cache_migration_compilation(folder):
        return runpy.run_path(str(folder/'20261126_0147_stock_loss_regional_review.py'))


def test_regional_proof_catalog_is_private_and_not_stock_disposal(migration):
    m=migration
    assert m['NEW_READY_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0147['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['OLD_READY_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0146['rsc_oam_runtime_binding_ready_0044()'][6]
    for coordinate,(args,result,body) in m['FUNCTIONS'].items():
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]==m['FUNCTION_HASHES'][coordinate]
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate]==('f',result,False)
        assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({args}) RETURNS {result} LANGUAGE plpgsql AS $b${body}$b$')
        assert not sa.text(body)._bindparams
    for name,(table,_,function,kind,deferred) in m['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',kind,deferred,deferred,deferred)
        if table=='audit_events':
            assert security.EXPECTED_AUDIT_TRIGGERS[name]==(table,function,kind,deferred,deferred,deferred)
    assert 'rsc_assert_loss_submit_authority_0145' not in m['CHECK_BODY']
    assert 'rsc_check_loss_hold_0145' in m['CHECK_BODY']
    assert 'regional review cannot post stock' in m['GUARD_BODY']
    assert 'clock_timestamp()' in m['AUTHORITY_BODY']


@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_atomic_owner_only_migration_and_retention(migration,direction):
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        migration[direction]()
    sql=output.getvalue();parser.parse_sql(sql)
    assert sql.index('LOCK TABLE public.alembic_version')<sql.index('LOCK TABLE public.inventory_ledger_heads')
    assert 'INSERT INTO permissions' not in sql and 'DELETE FROM' not in sql
    if direction=='downgrade':assert sql.index('requires retention')<sql.index('DROP FUNCTION')


def test_sqlite_regional_review_stays_closed_and_empty_schema_roundtrip(migration):
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as db,Operations.context(MigrationContext.configure(db)):
            for table in ('users','people','organizations'):
                db.exec_driver_sql('CREATE TABLE '+table+'(id CHAR(36) PRIMARY KEY)')
            migration['upgrade']()
            assert {col['name'] for col in sa.inspect(db).get_columns(migration['TABLE'])}==set(StockLossRegionalReview.__table__.columns.keys())
            with pytest.raises(sa.exc.IntegrityError,match='PostgreSQL regional review proof required'):
                db.execute(StockLossRegionalReview.__table__.insert().values(id=uuid4(),actor_user_id=str(uuid4()),
                    reviewer_person_id=uuid4(),owner_org_id=uuid4(),operation_id=uuid4(),operation_type='loss_report',
                    authorization_version=1,decision='verified',comment='Synthetic review',request_id='synthetic-request',
                    idempotency_key_hash='a'*64,request_hash='b'*64,submission_plan_hash='c'*64))
            assert db.scalar(sa.text('SELECT count(*) FROM stock_loss_regional_reviews'))==0
            migration['downgrade']()
            assert migration['TABLE'] not in sa.inspect(db).get_table_names()
    finally:engine.dispose()


def test_retained_review_prevents_downgrade_before_ddl(migration):
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as db,Operations.context(MigrationContext.configure(db)):
            db.exec_driver_sql('CREATE TABLE stock_loss_regional_reviews(id TEXT PRIMARY KEY)')
            db.exec_driver_sql("INSERT INTO stock_loss_regional_reviews VALUES ('retained')")
            with pytest.raises(RuntimeError,match='history requires retention'):migration['downgrade']()
            assert db.scalar(sa.text('SELECT count(*) FROM stock_loss_regional_reviews'))==1
    finally:engine.dispose()
