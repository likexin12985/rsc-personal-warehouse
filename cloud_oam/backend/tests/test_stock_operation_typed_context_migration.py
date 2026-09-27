"""Typed stock documents preserve returns while loss admission remains closed."""
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import hashlib
import runpy
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import database_security as security, oam_sync_scope_security as scope
from app.stock_operation_models import StockOperationOrder, StockOperationLine

VERSIONS = Path(__file__).parents[1] / 'alembic/versions'


@pytest.fixture(scope='module')
def migration():
    return runpy.run_path(str(VERSIONS / '20261121_0142_stock_operation_typed_context.py'))


@pytest.fixture
def legacy(migration):
    engine = sa.create_engine('sqlite+pysqlite:///:memory:')
    with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql('CREATE TABLE permissions(id CHAR(32),resource TEXT,action TEXT,field_code TEXT,description TEXT,created_at DATETIME,updated_at DATETIME)')
        db.exec_driver_sql('CREATE TABLE role_permissions(id CHAR(32),role_id CHAR(32),permission_id CHAR(32),effect TEXT,created_at DATETIME)')
        old = runpy.run_path(str(VERSIONS / '20261010_0100_stock_return_orders.py'))
        old['upgrade']()
        inspector = sa.inspect(db)
        present = set(inspector.get_table_names())
        references = {fk['referred_table'] for name in old['TABLES'] for fk in inspector.get_foreign_keys(name)}
        for name in sorted(references - present):
            db.exec_driver_sql(f'CREATE TABLE {name}(id CHAR(32) PRIMARY KEY)')
        yield db
    engine.dispose()


def order(**changes):
    value = dict(id=uuid4(), operation_no='RET-'+uuid4().hex, operation_type='return', status='submitted',
        oam_work_order_id=uuid4(), source_location_id=uuid4(), target_location_id=uuid4(),
        transit_location_id=uuid4(), target_custody_assignment_id=uuid4(), requester_id=uuid4(),
        actor_user_id='synthetic-user', authorization_version=1, reason='Synthetic immutable return',
        request_id='request-'+uuid4().hex, idempotency_key_hash=uuid4().hex*2, request_hash='a'*64,
        plan_hash='b'*64, command_jsonb={}, plan_jsonb={}, posting_transaction_id=uuid4(),
        created_at=datetime.now(timezone.utc))
    return dict(value, **changes)


def line(parent, **changes):
    value = dict(id=uuid4(), operation_id=parent, line_no=1, source_recovery_line_id=uuid4(),
        stock_account_id=uuid4(), reserved_account_id=uuid4(), material_id=uuid4(), quantity=1,
        target_condition='used', reason='Synthetic immutable return', created_at=datetime.now(timezone.utc))
    return dict(value, **changes)


def facts(db):
    return {name:tuple(db.exec_driver_sql(f'SELECT * FROM {name} ORDER BY id').all())
        for name in ('stock_operation_orders','stock_operation_lines','permissions','role_permissions')}


def test_existing_return_rows_permissions_and_triggers_survive_roundtrip(legacy, migration):
    db = legacy
    parent = order()
    db.execute(StockOperationOrder.__table__.insert().values(**parent))
    db.execute(StockOperationLine.__table__.insert().values(**line(parent['id'])))
    before = facts(db)
    triggers = tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").all())
    migration['upgrade']()
    assert db.scalar(sa.text('SELECT operation_type FROM stock_operation_lines')) == 'return'
    assert facts(db)['stock_operation_orders'] == before['stock_operation_orders']
    assert facts(db)['permissions'] == before['permissions']
    assert facts(db)['role_permissions'] == before['role_permissions']
    for table in ('stock_operation_orders', 'stock_operation_lines'):
        for statement in (f'UPDATE {table} SET reason=reason', f'DELETE FROM {table}'):
            with pytest.raises(sa.exc.IntegrityError, match='append-only'):
                with db.begin_nested(): db.exec_driver_sql(statement)
    migration['downgrade']()
    assert facts(db) == before
    assert tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").all()) == triggers
    migration['upgrade']()
    assert db.scalar(sa.text('SELECT count(*) FROM stock_operation_lines')) == 1


@pytest.mark.parametrize('column', ('oam_work_order_id','target_location_id','transit_location_id','target_custody_assignment_id'))
def test_return_null_context_never_passes_sql_three_valued_check(legacy, migration, column):
    migration['upgrade']()
    with pytest.raises(sa.exc.IntegrityError, match='ck_stock_operation_orders_locations'):
        legacy.execute(StockOperationOrder.__table__.insert().values(**order(**{column:None})))


@pytest.mark.parametrize('changes', [dict(source_recovery_line_id=None), dict(target_condition='new'), dict(target_condition='scrapped')])
def test_return_origin_and_condition_still_required(legacy, migration, changes):
    migration['upgrade']()
    parent=order();legacy.execute(StockOperationOrder.__table__.insert().values(**parent))
    with pytest.raises(sa.exc.IntegrityError, match='ck_stock_operation_lines_dimensions'):
        legacy.execute(StockOperationLine.__table__.insert().values(**line(parent['id'],**changes)))


def test_loss_admission_is_closed_on_both_shared_tables(legacy, migration):
    migration['upgrade']()
    loss=order(operation_type='loss_report', **{name:None for name in migration['CONTEXT_COLUMNS']})
    with pytest.raises(sa.exc.IntegrityError, match='complete loss posting boundary'):
        with legacy.begin_nested(): legacy.execute(StockOperationOrder.__table__.insert().values(**loss))
    with pytest.raises(sa.exc.IntegrityError, match='complete loss posting boundary'):
        with legacy.begin_nested(): legacy.execute(StockOperationLine.__table__.insert().values(**line(loss['id'],operation_type='loss_report',source_recovery_line_id=None,target_condition='new')))
    assert legacy.scalar(sa.text('SELECT count(*) FROM stock_operation_orders')) == 0
    assert legacy.scalar(sa.text('SELECT count(*) FROM stock_operation_lines')) == 0


def test_unrecognized_document_type_remains_invalid(legacy,migration):
    migration['upgrade']()
    with pytest.raises(sa.exc.IntegrityError):
        legacy.execute(StockOperationOrder.__table__.insert().values(**order(operation_type='unreviewed')))


def test_downgrade_refuses_typed_history_before_changing_schema(legacy,migration):
    migration['upgrade']()
    # Only the synthetic SQLite fixture owner can remove admission to create
    # a future typed row. This does not exercise production stock posting.
    legacy.exec_driver_sql('DROP TRIGGER trg_stock_operation_orders_loss_admission_0142')
    loss=order(operation_type='loss_report', **{name:None for name in migration['CONTEXT_COLUMNS']})
    legacy.execute(StockOperationOrder.__table__.insert().values(**loss))
    before=facts(legacy)
    with pytest.raises(RuntimeError,match='typed operation history must be retained'):
        migration['downgrade']()
    assert facts(legacy)==before
    assert 'operation_type' in {c['name'] for c in sa.inspect(legacy).get_columns('stock_operation_lines')}


@pytest.mark.parametrize('action', ('upgrade','downgrade'))
def test_postgresql_ddl_and_guard_parse(migration,action):
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        migration[action]()
    sql=output.getvalue();parser=pytest.importorskip('pglast.parser');parser.parse_sql(sql)
    assert sql.index('LOCK TABLE public.alembic_version') < sql.index('ALTER TABLE')
    assert 'CREATE OR REPLACE' not in sql and 'DELETE FROM' not in sql
    parser.parse_plpgsql_json(f"CREATE FUNCTION {migration['FUNCTION']}() RETURNS trigger LANGUAGE plpgsql AS $body${migration['BODY']}$body$")
    if action=='downgrade':assert sql.index('typed operation history must be retained') < sql.index('DROP TRIGGER')


def test_historical_runtime_manifest_pins_closed_admission(migration):
    key=(migration['FUNCTION'],'')
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key]==hashlib.sha256(migration['BODY'].encode()).hexdigest()
    assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
    for name,table in migration['TRIGGERS'].items():
        assert security.EXPECTED_FORMAL_FILE_TRIGGERS_THROUGH_0144[name]==(table,migration['FUNCTION'],'A',7)
    assert scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0142['rsc_oam_runtime_binding_ready_0044()'][6]==migration['NEW_READY_HASH']
    assert scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0141['rsc_oam_runtime_binding_ready_0044()'][6]==migration['OLD_READY_HASH']


@pytest.mark.parametrize('parent_type', ('return', 'loss_report'))
def test_typed_parent_fk_rejects_cross_type_lines(legacy, migration, parent_type):
    migration['upgrade']()
    # Owner-only schema probe: remove closed admission in this disposable
    # SQLite fixture to isolate the composite FK from the earlier trigger.
    for trigger in migration['TRIGGERS']:
        legacy.exec_driver_sql('DROP TRIGGER '+trigger)
    parent = order(operation_type=parent_type,
        **({name:None for name in migration['CONTEXT_COLUMNS']} if parent_type=='loss_report' else {}))
    valid_line = line(parent['id'], operation_type=parent_type,
        **dict(source_recovery_line_id=None, target_condition='new') if parent_type=='loss_report' else {})
    wrong_type = 'loss_report' if parent_type=='return' else 'return'
    wrong_line = line(parent['id'], operation_type=wrong_type,
        **dict(source_recovery_line_id=None, target_condition='new') if wrong_type=='loss_report' else {})
    inspector = sa.inspect(legacy)
    for table, values in (('stock_operation_orders',parent),('stock_operation_lines',valid_line),('stock_operation_lines',wrong_line)):
        for fk in inspector.get_foreign_keys(table):
            if fk['referred_table']=='stock_operation_orders': continue
            value=values[fk['constrained_columns'][0]]
            if value is not None:
                legacy.execute(sa.text(f"INSERT OR IGNORE INTO {fk['referred_table']}(id) VALUES (:id)"),
                    {'id':value.hex if hasattr(value,'hex') else value})
    legacy.commit()
    legacy.exec_driver_sql('PRAGMA foreign_keys=ON')
    assert legacy.exec_driver_sql('PRAGMA foreign_keys').scalar()==1
    legacy.execute(StockOperationOrder.__table__.insert().values(**parent))
    with pytest.raises(sa.exc.IntegrityError, match='FOREIGN KEY'):
        with legacy.begin_nested():
            legacy.execute(StockOperationLine.__table__.insert().values(**wrong_line))
    legacy.execute(StockOperationLine.__table__.insert().values(**valid_line))
    assert legacy.scalar(sa.text('SELECT count(*) FROM stock_operation_lines'))==1


def test_dependent_trigger_on_other_table_survives_roundtrip(legacy, migration):
    legacy.exec_driver_sql('CREATE TABLE synthetic_observer(id INTEGER)')
    statement = "CREATE TRIGGER synthetic_dependency BEFORE INSERT ON synthetic_observer BEGIN SELECT count(*) FROM stock_operation_orders; SELECT count(*) FROM stock_operation_lines; END"
    legacy.exec_driver_sql(statement)
    migration['upgrade']()
    migration['downgrade']()
    assert legacy.scalar(sa.text("SELECT sql FROM sqlite_master WHERE name='synthetic_dependency'")) == statement
    legacy.exec_driver_sql('INSERT INTO synthetic_observer VALUES(1)')
