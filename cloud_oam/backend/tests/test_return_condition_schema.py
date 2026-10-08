"""Relational constraints with minimal external-parent stubs, not business proof."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pglast import parse_sql
from sqlalchemy import Column, MetaData, Table, UniqueConstraint, create_engine, event, select, func
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.return_condition_schema import CASES, EVENTS, SERIALS, TABLE_NAMES, build_schema, define
from pg16_return_condition_schema import compile_structure


def test_candidate_compiles_without_changing_live_models_or_existing_foreign_keys():
    before = {n: (tuple(t.columns.keys()), frozenset(t.constraints)) for n, t in Base.metadata.tables.items()}
    metadata, tables, parents, statements = compile_structure()
    assert len(tables) == 4 and len(parents) == 4
    for statement in statements:
        parse_sql(statement)
    # SQLite and syntax-only parsers allow duplicate constraint names. PG16
    # rejects them at CREATE TABLE; include implicit shared-context checks.
    for table in (*tables, *parents):
        names = [c.name for c in table.constraints if c.name is not None]
        assert len(names) == len(set(names)), table.name
    assert sum(' ADD COLUMN ' in s for s in statements) == 2
    assert sum('CREATE INDEX' in s for s in statements) == 2
    assert not any('GRANT ' in s or 'DISABLE TRIGGER' in s for s in statements)
    first_fk = next(i for i, s in enumerate(statements) if ' FOREIGN KEY' in s)
    assert all(' FOREIGN KEY' in s for s in statements[first_fk:])
    assert before == {n: (tuple(t.columns.keys()), frozenset(t.constraints)) for n, t in Base.metadata.tables.items()}
    for table in parents:
        for fk in Base.metadata.tables[table.name].foreign_key_constraints:
            assert any(tuple(c.name for c in candidate.columns) == tuple(c.name for c in fk.columns)
                and tuple(e.target_fullname for e in candidate.elements) == tuple(e.target_fullname for e in fk.elements)
                for candidate in table.foreign_key_constraints)
    with pytest.raises(ValueError, match='live application metadata'):
        define(Base.metadata)
    with pytest.raises(ValueError, match='installed once'):
        define(metadata)
    assert set(TABLE_NAMES) <= Base.metadata.tables.keys()


@pytest.fixture
def relational():
    metadata, tables, _ = build_schema()
    dependencies, keys = {}, {}
    for table in tables:
        for fk in table.foreign_key_constraints:
            target = fk.elements[0].column.table
            if target.name in TABLE_NAMES:
                continue
            dependencies.setdefault(target.name, {}).update({e.column.name: e.column for e in fk.elements})
            keys.setdefault(target.name, set()).add(tuple(e.column.name for e in fk.elements))
    stubs = MetaData()
    parents = {}
    for name, columns in dependencies.items():
        parents[name] = Table(name, stubs,
            *(Column(key, col.type, primary_key=key == 'id' or 'id' not in columns,
                     nullable=col.nullable) for key, col in columns.items()),
            *(UniqueConstraint(*key) for key in sorted(keys[name]) if key != ('id',)))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def enforce(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
    stubs.create_all(engine)
    metadata.create_all(engine, tables=tables)
    ids = {key: uuid4() for key in ('case', 'line', 'root', 'inbound', 'inbound_line',
        'original_tx', 'original_move', 'freeze_tx', 'freeze_move', 'source', 'frozen',
        'damaged', 'custody', 'person', 'serial', 'other_serial', 'event', 'file')}
    user = str(uuid4())
    context = dict(id=ids['event'], created_at=datetime.now(timezone.utc), actor_user_id=user,
        actor_person_id=ids['person'], authorization_version=1, request_id='request-initial',
        idempotency_key_hash='a'*64, request_hash='b'*64, reason='Synthetic condition evidence', command_jsonb={},
        case_id=ids['case'], submit_event_id=ids['event'], inbound_line_id=ids['inbound_line'],
        source_account_id=ids['source'], frozen_account_id=ids['frozen'], quantity=Decimal(1),
        event_sequence=1, kind='submit', from_state='draft', to_state='awaiting_regional',
        previous_event_id=None, previous_sequence=None, decision_event_id=None, decision_kind=None,
        posting_transaction_id=ids['freeze_tx'], posting_movement_id=ids['freeze_move'],
        movement_type='freeze', from_account_id=ids['source'], to_account_id=ids['frozen'],
        plan_hash='c'*64, plan_jsonb={})
    case = dict(id=ids['case'], operation_type='condition_correction', line_id=ids['line'],
        root_disposition_id=ids['root'], inbound_id=ids['inbound'], inbound_line_id=ids['inbound_line'],
        original_transaction_id=ids['original_tx'], original_movement_id=ids['original_move'], original_ledger_cursor=1,
        source_account_id=ids['source'], frozen_account_id=ids['frozen'], custody_assignment_id=ids['custody'],
        recorded_condition='new', target_condition='damaged', quantity=Decimal(1), affected_quantity=Decimal(2),
        tracking_mode='serial', quantity_scale=0, allow_fraction=False, history_hash='d'*64,
        source_hash='e'*64, source_jsonb={}, submit_event_id=ids['event'], submit_kind='submit',
        freeze_transaction_id=ids['freeze_tx'], freeze_movement_id=ids['freeze_move'])
    rows = {
        'stock_operation_orders': [dict(id=ids['case'], operation_type='condition_correction', posting_transaction_id=ids['freeze_tx'])],
        'stock_operation_lines': [dict(id=ids['line'], operation_id=ids['case'], operation_type='condition_correction',
            stock_account_id=ids['source'], reserved_account_id=ids['frozen'], quantity=Decimal(1), target_condition='damaged')],
        'stock_loss_dispositions': [dict(id=ids['root'])],
        'stock_operation_return_inbounds': [dict(id=ids['inbound'])],
        'stock_operation_return_inbound_lines': [dict(id=ids['inbound_line'], inbound_id=ids['inbound'],
            target_account_id=ids['source'], condition_code='new')],
        'inventory_transactions': [dict(id=ids[k]) for k in ('original_tx', 'freeze_tx')],
        'inventory_movements': [dict(id=ids['original_move'], transaction_id=ids['original_tx'],
            from_account_id=ids['damaged'], to_account_id=ids['source'], quantity=Decimal(2)),
            dict(id=ids['freeze_move'], transaction_id=ids['freeze_tx'], from_account_id=ids['source'],
                 to_account_id=ids['frozen'], quantity=Decimal(1))],
        'stock_accounts': [dict(id=ids[k]) for k in ('source', 'frozen', 'damaged')],
        'custody_assignments': [dict(id=ids['custody'])],
        'people': [dict(id=ids['person'])], 'users': [dict(id=user)], 'files': [dict(id=ids['file'])],
        'inventory_serials': [dict(id=ids[k]) for k in ('serial', 'other_serial')],
        'stock_operation_return_inbound_serials': [dict(line_id=ids['inbound_line'], serial_id=ids['serial'])],
    }
    with engine.begin() as db:
        for name, parent in parents.items():
            db.execute(parent.insert(), rows[name])
        db.execute(metadata.tables[CASES].insert(), case)
        db.execute(metadata.tables[EVENTS].insert(), context)
    with engine.connect() as db:
        yield db, metadata, parents, ids, context, case
    engine.dispose()


def successor(previous, kind='verify_region', to_state='awaiting_headquarters'):
    return previous | dict(id=uuid4(), event_sequence=previous['event_sequence'] + 1,
        kind=kind, from_state=previous['to_state'], to_state=to_state,
        previous_event_id=previous['id'], previous_sequence=previous['event_sequence'],
        request_id='request-' + uuid4().hex, request_hash=uuid4().hex * 2,
        idempotency_key_hash=uuid4().hex * 2, posting_transaction_id=None, posting_movement_id=None,
        movement_type=None, from_account_id=None, to_account_id=None,
        decision_event_id=None, decision_kind=None)


def refused(db, table, row):
    with pytest.raises(IntegrityError):
        db.execute(table.insert(), row)
        db.commit()
    # SQLite retains a deferred-FK failure after SQLAlchemy ends its wrapper.
    db.connection.rollback()
    db.rollback()


def test_initial_claim_event_and_serial_bind_to_the_exact_source(relational):
    db, meta, _, ids, initial, _ = relational
    db.execute(meta.tables[SERIALS].insert(), dict(case_id=ids['case'], serial_id=ids['serial'],
                                                inbound_line_id=ids['inbound_line']))
    db.commit()
    assert db.scalar(select(func.count()).select_from(meta.tables[EVENTS])) == 1
    assert db.execute(select(meta.tables[EVENTS].c.kind)).scalar_one() == 'submit'


def test_serial_from_another_inbound_cannot_be_substituted(relational):
    db, meta, _, ids, _, _ = relational
    refused(db, meta.tables[SERIALS], dict(case_id=ids['case'], serial_id=ids['other_serial'],
                                         inbound_line_id=ids['inbound_line']))


@pytest.mark.parametrize('change', [
    {'kind': 'approve_hq', 'to_state': 'approved'},
    {'from_state': 'awaiting_headquarters'}, {'previous_sequence': None},
    {'previous_event_id': None}, {'previous_sequence': 8}, {'event_sequence': 1},
    {'quantity': Decimal(2)}, {'case_id': uuid4()}, {'inbound_line_id': uuid4()},
    {'source_account_id': uuid4()}, {'submit_event_id': uuid4()},
    {'posting_transaction_id': uuid4()}, {'movement_type': 'freeze'},
    {'decision_kind': 'approve_hq'}, {'decision_event_id': uuid4()},
])
def test_review_cannot_skip_predecessors_change_share_or_smuggle_posting(relational, change):
    db, meta, _, _, initial, _ = relational
    refused(db, meta.tables[EVENTS], successor(initial) | change)
    assert db.scalar(select(func.count()).select_from(meta.tables[EVENTS])) == 1


def test_one_predecessor_cannot_fork_to_approval_and_rejection(relational):
    db, meta, _, _, initial, _ = relational
    approved = successor(initial)
    db.execute(meta.tables[EVENTS].insert(), approved)
    db.commit()
    refused(db, meta.tables[EVENTS], successor(initial, 'reject_region', 'rejected_pending_release') |
            {'event_sequence': 3})


def test_second_submission_cannot_start_an_unbound_parallel_root(relational):
    db, meta, _, _, initial, _ = relational
    second = initial | dict(id=uuid4(), event_sequence=2, request_id='another-submit', idempotency_key_hash='f'*64)
    refused(db, meta.tables[EVENTS], second)


def posting_event(relational, previous, *, kind='execute', to_state='executed'):
    db, meta, parents, ids, _, _ = relational
    row = successor(previous, kind, to_state) | dict(decision_event_id=previous['id'],
        decision_kind=previous['kind'], posting_transaction_id=uuid4(), posting_movement_id=uuid4(),
        from_account_id=ids['frozen'], to_account_id=ids['damaged'] if kind == 'execute' else ids['source'],
        movement_type='status_change' if kind == 'execute' else 'unfreeze')
    db.execute(parents['inventory_transactions'].insert(), dict(id=row['posting_transaction_id']))
    db.execute(parents['inventory_movements'].insert(), dict(id=row['posting_movement_id'],
        transaction_id=row['posting_transaction_id'], from_account_id=row['from_account_id'],
        to_account_id=row['to_account_id'], quantity=row['quantity']))
    db.commit()
    return row


def test_approval_and_posting_are_independent_relational_facts(relational):
    db, meta, _, ids, initial, _ = relational
    regional = successor(initial)
    hq = successor(regional, 'approve_hq', 'approved')
    db.execute(meta.tables[EVENTS].insert(), [regional, hq])
    db.commit()
    assert hq['posting_transaction_id'] is None
    execute = posting_event(relational, hq)
    db.execute(meta.tables[EVENTS].insert(), execute)
    db.commit()
    assert db.scalar(select(func.count()).select_from(meta.tables[EVENTS])) == 4


@pytest.mark.parametrize('change', [
    {'decision_event_id': None}, {'decision_kind': None}, {'decision_kind': 'approve_hq'},
    {'posting_transaction_id': None}, {'posting_movement_id': None},
    {'from_account_id': None}, {'to_account_id': None}, {'quantity': Decimal(2)},
])
def test_release_has_no_nullable_fk_escape_and_requires_exact_decision_and_movement(relational, change):
    db, meta, _, ids, initial, _ = relational
    rejected = successor(initial, 'reject_region', 'rejected_pending_release')
    db.execute(meta.tables[EVENTS].insert(), rejected)
    db.commit()
    release = posting_event(relational, rejected, kind='release', to_state='released_rejected')
    refused(db, meta.tables[EVENTS], release | change)


def test_exact_rejection_release_can_commit_but_remains_separate_from_decision(relational):
    db, meta, _, _, initial, _ = relational
    rejected = successor(initial, 'reject_region', 'rejected_pending_release')
    db.execute(meta.tables[EVENTS].insert(), rejected)
    db.commit()
    assert rejected['posting_transaction_id'] is None
    release = posting_event(relational, rejected, kind='release', to_state='released_rejected')
    db.execute(meta.tables[EVENTS].insert(), release)
    db.commit()
    refused(db, meta.tables[EVENTS], successor(release, 'approve_hq', 'approved'))
