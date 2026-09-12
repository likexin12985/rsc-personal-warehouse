"""Real SQL fact guards; full role/ledger execution is exercised in the PG16 gate."""
from datetime import datetime, timezone
from pathlib import Path
import runpy
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, event, text
from sqlalchemy.exc import IntegrityError

from app.demand_models import MaterialRequestCommand
from app.inventory_models import InboundPosting, InventoryMovement, InventoryMovementSerial, ReceiptSerial
from app.formal_services import material_request_inbound as inbound
from test_material_request_inbound_posting import inbound_world, outbound_world, pick_world, release_world, approval_db, post

MIGRATION = Path(__file__).resolve().parents[1] / 'alembic/versions/20260927_0087_inbound_fulfillment_boundary.py'


@pytest.fixture
def guarded(inbound_world):
    w = inbound_world
    original = w.db.scalar(select(InboundPosting).where(InboundPosting.inbound_order_id == w.order.id))
    posting_id, transaction_id = original.id, original.inventory_transaction_id
    w.db.delete(original); w.db.flush()
    migration = runpy.run_path(str(MIGRATION))
    with Operations.context(MigrationContext.configure(w.db.connection())):
        migration['_guards'](True)
    return w, posting_id, transaction_id


def bind(value):
    w, posting_id, transaction_id = value
    w.db.add(InboundPosting(id=posting_id, inbound_order_id=w.order.id,
        inventory_transaction_id=transaction_id, created_at=datetime.now(timezone.utc)))
    w.db.flush()


def test_exact_accepted_stock_binding_succeeds_and_fact_is_immutable(guarded):
    bind(guarded)
    w, posting_id, _ = guarded
    assert inbound._posting_projection(w.db, w.order)['status'] == 'posted'
    assert w.order.status == 'pending' and w.order.posting_transaction_id is None
    for sql in ['DELETE FROM inbound_postings WHERE id=:id', 'UPDATE inbound_postings SET created_at=created_at WHERE id=:id']:
        with pytest.raises(IntegrityError, match='append-only'):
            with w.db.begin_nested():
                w.db.execute(text(sql), {'id': posting_id.hex})


@pytest.mark.parametrize('tamper', ['quantity', 'wrong_target', 'wrong_source', 'empty', 'receipt_person', 'rejected_condition', 'missing_target_binding'])
def test_unrelated_or_inexact_inventory_transaction_cannot_be_bound(guarded, tamper):
    w, _, transaction_id = guarded
    movement = w.db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == transaction_id))
    if tamper == 'quantity': movement.quantity += 1
    elif tamper == 'wrong_target': movement.to_account_id = movement.from_account_id
    elif tamper == 'wrong_source': movement.from_account_id = movement.to_account_id
    elif tamper == 'receipt_person': w.receipt.receiver_person_id = uuid4()
    elif tamper == 'rejected_condition':
        from app.inventory_models import ReceiptLine
        w.db.query(ReceiptLine).filter(ReceiptLine.receipt_id == w.receipt.id).update({'condition': 'rejected'})
    elif tamper == 'missing_target_binding':
        from app.inventory_models import StockAccount
        w.db.get(StockAccount, movement.to_account_id).availability_bucket = 'arrived_pending'
    else:
        w.db.query(InventoryMovementSerial).filter(InventoryMovementSerial.transaction_id == transaction_id).delete()
        w.db.delete(movement)
    # Some malformed movement dimensions are rejected by the existing ledger
    # CHECK before the new binding guard, which is equally fail-closed.
    with pytest.raises(IntegrityError):
        with w.db.begin_nested():
            w.db.flush()
            bind(guarded)


def test_rejected_sn_cannot_be_bound_as_personal_inventory(guarded):
    w, _, transaction_id = guarded
    serial = w.db.scalar(select(InventoryMovementSerial).where(InventoryMovementSerial.transaction_id == transaction_id))
    if serial is None:
        pytest.skip('SN-specific case uses the serial fixture')
    w.db.query(ReceiptSerial).filter(ReceiptSerial.serial_id == serial.serial_id).update({'accepted': False})
    w.db.flush()
    with pytest.raises(IntegrityError, match='accepted receipt stock'):
        with w.db.begin_nested(): bind(guarded)


def test_posting_appends_one_version_and_replay_never_updates_immutable_order(inbound_world):
    w = inbound_world
    command = w.db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == 'personal_inbound'))
    assert command.target_version == w.request.version
    assert command.result_jsonb['fact_id'] == str(w.first['inventory_transaction_id'])
    version = w.request.version
    statements = []
    def record(_conn, _cursor, sql, *_args): statements.append(sql)
    event.listen(w.db.bind, 'before_cursor_execute', record)
    try:
        post(w)
    finally:
        event.remove(w.db.bind, 'before_cursor_execute', record)
    assert w.request.version == version
    assert not any('UPDATE inbound_orders' in sql for sql in statements)
    command.result_jsonb = {**command.result_jsonb, 'fact_id': str(uuid4())}
    w.db.flush()
    with pytest.raises(Exception, match='命令证据'):
        post(w)


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_migration_late_failure_rolls_back_every_schema_change(tmp_path, monkeypatch, direction):
    from alembic import command
    from sqlalchemy import create_engine
    from test_alembic_migrations import _config
    monkeypatch.delenv('OAM_DATABASE_URL', raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'atomic.db'}"
    command.upgrade(_config(url), '20260926_0086' if direction == 'upgrade' else 'head')
    engine = create_engine(url)
    def catalog(connection):
        return tuple(connection.exec_driver_sql('SELECT type, name, sql FROM sqlite_master ORDER BY type, name'))
    with engine.connect() as connection:
        before = catalog(connection)
    migration = runpy.run_path(str(MIGRATION))
    namespace = migration[direction].__globals__
    if direction == 'upgrade':
        def fail(_): raise RuntimeError('injected late migration failure')
        namespace['_operations'] = fail
    else:
        real_guards = namespace['_guards']
        def fail(value):
            real_guards(value)
            raise RuntimeError('injected late migration failure')
        namespace['_guards'] = fail
    with pytest.raises(RuntimeError, match='injected late'):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration[direction]()
    with engine.connect() as connection:
        assert catalog(connection) == before
    engine.dispose()


def test_runtime_guard_manifest_matches_migration():
    from app.database_security import MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256
    assert MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[('rsc_guard_inbound_posting_0087', '')] == runpy.run_path(str(MIGRATION))['GUARD_HASH']


@pytest.mark.parametrize('legacy', ['posting', 'duplicate_orders', 'command'])
def test_migration_refuses_to_bless_or_erase_legacy_facts(legacy):
    from sqlalchemy import create_engine
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE inbound_orders (receipt_id TEXT)')
        connection.exec_driver_sql('CREATE TABLE inbound_postings (id TEXT)')
        connection.exec_driver_sql('CREATE TABLE material_request_commands (operation TEXT)')
        if legacy == 'posting': connection.exec_driver_sql("INSERT INTO inbound_postings VALUES ('old-posting')")
        elif legacy == 'duplicate_orders': connection.exec_driver_sql("INSERT INTO inbound_orders VALUES ('receipt'), ('receipt')")
        else: connection.exec_driver_sql("INSERT INTO material_request_commands VALUES ('personal_inbound')")
    migration = runpy.run_path(str(MIGRATION))
    direction = 'downgrade' if legacy == 'command' else 'upgrade'
    with pytest.raises(RuntimeError, match=f'0087 {direction} blocked'):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration[direction]()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE type IN ('index', 'trigger')").scalar() == 0
        assert connection.exec_driver_sql('SELECT (SELECT count(*) FROM inbound_orders) + (SELECT count(*) FROM inbound_postings) + (SELECT count(*) FROM material_request_commands)').scalar() == (2 if legacy == 'duplicate_orders' else 1)
    engine.dispose()


@pytest.mark.parametrize('drift', ['missing', 'duplicate', 'is_unique', 'is_valid', 'is_ready', 'is_live',
    'table_name', 'key_count', 'column_count', 'key_column', 'predicate', 'access_method'])
def test_runtime_rejects_missing_or_weakened_receipt_uniqueness(drift):
    from app.database_security import _assert_inbound_receipt_index, DatabaseSecurityBoundaryError
    row = dict(table_name='inbound_orders', is_unique=True, is_valid=True, is_ready=True,
        is_live=True, key_count=1, column_count=1, access_method='btree', key_column='receipt_id', predicate=None)
    _assert_inbound_receipt_index([row])
    rows = [row]
    if drift == 'missing': rows = []
    elif drift == 'duplicate': rows = [row, row]
    else: row[drift] = False if drift.startswith('is_') else 'altered'
    with pytest.raises(DatabaseSecurityBoundaryError, match='receipt uniqueness'):
        _assert_inbound_receipt_index(rows)
