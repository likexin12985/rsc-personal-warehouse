"""0152 exact transition, preserved local history, private catalog and source pins."""
from pathlib import Path
import runpy
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from migration_script_cache import cache_migration_compilation
from test_stock_operation_typed_context_migration import legacy,order,line,migration
from app.stock_operation_models import StockOperationOrder,StockOperationLine

R=Path(__file__).parents[1]/'alembic/versions'

@pytest.fixture(scope='module')
def candidate():
    with cache_migration_compilation(R):
        filename='20261201_0152_stock_loss_derived_returns.py'
        return runpy.run_path(str(R/filename))

@pytest.fixture
def current(legacy,migration):
    db=legacy
    migration['upgrade']()
    with cache_migration_compilation(R):
        disposition=runpy.run_path(str(R/'20261129_0150_stock_loss_disposition.py'))
    disposition['upgrade']()
    inspector=sa.inspect(db);present=set(inspector.get_table_names())
    refs={fk['referred_table'] for table in ('stock_loss_dispositions',) for fk in inspector.get_foreign_keys(table)}
    for table in refs-present:db.exec_driver_sql('CREATE TABLE '+table+'(id CHAR(32) PRIMARY KEY)')
    db.exec_driver_sql('CREATE TABLE synthetic_dependent(id INTEGER)')
    db.exec_driver_sql('CREATE TRIGGER dependent_return_graph BEFORE INSERT ON synthetic_dependent BEGIN SELECT count(*) FROM stock_operation_orders; SELECT count(*) FROM stock_operation_lines; SELECT count(*) FROM stock_loss_dispositions; END')
    parent=order();db.execute(StockOperationOrder.__table__.insert().values(**parent))
    db.execute(StockOperationLine.__table__.insert().values(**line(parent['id'],operation_type='return')))
    yield db


def snapshot(db):
    tables=('stock_operation_orders','stock_operation_lines','stock_loss_dispositions','permissions','role_permissions')
    return {t:tuple(db.exec_driver_sql('SELECT * FROM '+t+' ORDER BY id')) for t in tables}


def triggers(db):
    return tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"))


def test_retains_old_facts_and_dependent_guards_roundtrip(current,candidate):
    db=current;before=snapshot(db);old=triggers(db)
    candidate['upgrade']()
    for table,column in zip(candidate['TABLES'],candidate['COLUMNS']):
        assert column in {c['name'] for c in sa.inspect(db).get_columns(table)}
    db.exec_driver_sql('INSERT INTO synthetic_dependent VALUES(1)')
    for table in ('stock_operation_orders','stock_operation_lines'):
        with pytest.raises(sa.exc.IntegrityError,match='append-only'):
            with db.begin_nested():db.exec_driver_sql('UPDATE '+table+' SET reason=reason')
    candidate['downgrade']()
    assert snapshot(db)==before and triggers(db)==old
    candidate['upgrade']();candidate['downgrade']()
    assert snapshot(db)==before and triggers(db)==old


@pytest.mark.parametrize('index',range(3))
def test_local_origin_admission_closed_and_downgrade_retains_history(current,candidate,index):
    db=current;candidate['upgrade']()
    table=candidate['TABLES'][index];column=candidate['COLUMNS'][index]
    with pytest.raises(sa.exc.IntegrityError,match='PostgreSQL derived-return proof required'):
        with db.begin_nested():
            db.execute(sa.text('INSERT INTO '+table+' ('+column+') VALUES (:id)'),{'id':uuid4().hex})
    # Owner-only shape fixture to assert downgrade never discards even malformed
    # provenance. Ignore CHECK locally; no business posting or PG proof claimed.
    db.exec_driver_sql('PRAGMA ignore_check_constraints=ON')
    # Existing rows are append-only. Replace only the synthetic schema trigger,
    # retaining its exact SQL for restoration; this is not an application path.
    if index<2:
        for name,statement in triggers(db):
            if table in statement and 'BEFORE UPDATE' in statement.upper():
                db.exec_driver_sql('DROP TRIGGER '+db.dialect.identifier_preparer.quote(name))
        db.execute(sa.text('UPDATE '+table+' SET '+column+'=:id'),{'id':uuid4().hex})
    else:
        db.exec_driver_sql('DROP TRIGGER trg_loss_disposition_insert_0150')
        db.exec_driver_sql('DROP TRIGGER '+candidate['GUARDS'][index])
        cols=sa.inspect(db).get_columns(table)
        values={c['name']:1 if isinstance(c['type'],(sa.Numeric,sa.Integer)) else '{}' if isinstance(c['type'],sa.JSON) else uuid4().hex for c in cols}
        db.execute(sa.text('INSERT INTO '+table+' ('+','.join(values)+') VALUES ('+','.join(':'+k for k in values)+')'),values)
    before=snapshot(db);old=triggers(db)
    with pytest.raises(RuntimeError,match='provenance history requires retention'):candidate['downgrade']()
    assert snapshot(db)==before and triggers(db)==old
    assert column in {c['name'] for c in sa.inspect(db).get_columns(table)}


@pytest.mark.parametrize('kind',('restore_available','convert_used','convert_damaged'))
def test_existing_disposition_history_survives_structure_roundtrip(current,candidate,kind):
    from datetime import datetime, timezone
    db=current;table='stock_loss_dispositions'
    original=db.scalar(sa.text("SELECT sql FROM sqlite_master WHERE name='trg_loss_disposition_insert_0150'"))
    db.exec_driver_sql('DROP TRIGGER trg_loss_disposition_insert_0150')
    values={c['name']:1 if isinstance(c['type'],(sa.Numeric,sa.Integer)) else '{}' if isinstance(c['type'],sa.JSON)
        else datetime.now(timezone.utc) if isinstance(c['type'],sa.DateTime)
        else uuid4().hex for c in sa.inspect(db).get_columns(table)}
    for field in ('request_hash','plan_hash','idempotency_key_hash'):values[field]=uuid4().hex*2
    values['disposition']=kind
    db.execute(sa.text('INSERT INTO '+table+' ('+','.join(values)+') VALUES ('+','.join(':'+k for k in values)+')'),values)
    db.exec_driver_sql(original)
    before=snapshot(db);saved=triggers(db)
    candidate['upgrade']();candidate['downgrade']()
    assert snapshot(db)==before and triggers(db)==saved


@pytest.mark.parametrize('direction',('upgrade','downgrade'))
def test_postgresql_structure_sql_parses_with_no_history_deletion(candidate,direction):
    from io import StringIO
    from pglast import parser
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        candidate[direction]()
    sql=output.getvalue();parser.parse_sql(sql)
    assert 'DELETE FROM' not in sql and 'TRUNCATE' not in sql
    if direction=='downgrade':
        assert sql.index('provenance history requires retention') < sql.index('ALTER TABLE')


def test_sqlite_foreign_keys_on_stops_before_table_rebuild(current,candidate):
    db=current;db.commit();before=snapshot(db);saved=triggers(db);db.commit()
    db.exec_driver_sql('PRAGMA foreign_keys=ON')
    assert db.exec_driver_sql('PRAGMA foreign_keys').scalar()==1
    with pytest.raises(RuntimeError,match='foreign_keys OFF'):candidate['upgrade']()
    assert snapshot(db)==before and triggers(db)==saved


def test_current_catalog_exactly_matches_private_proof_and_patches(candidate):
    import hashlib
    from migration_source_expectations import current_source_hash
    from pglast import parser
    from app import database_security as security, oam_sync_scope_security as scope
    m=candidate;key=(m['FUNCTION'],'uuid, boolean')
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key]==m['CHECK_HASH']
    assert key in security.FORMAL_FILE_INTERNAL_FUNCTIONS and key not in security.RUNTIME_EXECUTE_FUNCTIONS
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[key]==('f','void',False)
    parser.parse_plpgsql_json('CREATE FUNCTION guard(checked_fact uuid,require_current boolean) RETURNS void LANGUAGE plpgsql AS $b$'+m['CHECK_BODY']+'$b$')
    assert not sa.text(m['CHECK_BODY'])._bindparams
    assert scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0151['rsc_oam_runtime_binding_ready_0044()'][6]==m['OLD_READY_HASH']
    assert scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0152['rsc_oam_runtime_binding_ready_0044()'][6]==m['NEW_READY_HASH']
    before={**security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0151,**security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256_THROUGH_0151}
    after={**security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256,**security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256}
    for signature,(old,new) in m['_sources']().items():
        name,args=signature.removeprefix('public.').rstrip(')').split('(',1)
        assert before[name,args]==hashlib.sha256(old.encode()).hexdigest()
        assert after[name,args]==current_source_hash(m['revision'],signature,new)
        assert not sa.text(new)._bindparams
    for table in ('stock_locations','custody_assignments'):
        assert table not in security.RUNTIME_UPDATE_TABLES
