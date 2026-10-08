"""Relational contract tests, not a registrar or a native migration gate.

SQLite uses minimal parent rows with the real seal constraints and foreign
keys. This isolates binding failures; it does not prove historical business
validity, concurrent PostgreSQL COMMIT fences, or authority/audit completeness.
"""
from datetime import datetime, timezone
import hashlib
from typing import get_args
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import (Column, MetaData, PrimaryKeyConstraint, Table,
    UniqueConstraint, create_engine, event)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from app.database import Base
from app.return_condition_decision_requests import ConditionDecision
from app.return_condition_settlement_requests import ConditionSettlement
from app.return_condition_schema import TRANSITIONS

from app import return_condition_decision_seal_schema as schema


@pytest.fixture(scope='module')
def candidate():
    # Load models before capturing Base: build_schema legitimately imports them.
    from app import models  # noqa: F401
    before = {name: (tuple(t.c.keys()), tuple(sorted(str(c) for c in t.constraints)))
        for name, t in Base.metadata.tables.items()}
    result = schema.build_schema()
    assert before == {name: (tuple(t.c.keys()), tuple(sorted(str(c) for c in t.constraints)))
        for name, t in Base.metadata.tables.items()}
    return result


def test_isolation_and_real_postgresql_ddl(candidate):
    metadata, tables, _ = candidate
    assert len(tables) == 8 and tables[-1].name == schema.NAME
    assert schema.NAME in Base.metadata.tables and metadata is not Base.metadata
    assert set(schema.ACTIONS) == (set(get_args(ConditionDecision.model_fields['action'].annotation))
        | set(get_args(ConditionSettlement.model_fields['action'].annotation)))
    table = tables[-1]
    sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert 'DEFERRABLE INITIALLY DEFERRED' in sql
    assert " ~ '^[a-f0-9]{64}$'" in sql and 'GLOB' not in sql
    assert all(len(c.name) <= 63 for c in table.constraints if c.name)
    for fk in table.foreign_key_constraints:
        remote = fk.referred_table
        columns = tuple(e.column.name for e in fk.elements)
        assert any(tuple(c.columns.keys()) == columns for c in remote.constraints
            if isinstance(c, (UniqueConstraint, PrimaryKeyConstraint)))
        assert fk.ondelete == 'RESTRICT'
    assert not any(c in table.c for c in ('posting_transaction_id', 'posting_movement_id',
        'retry_allowed', 'current_stock_verified', 'idempotency_key'))
    with pytest.raises(ValueError):
        schema.define(metadata)
    with pytest.raises(ValueError):
        schema.define(Base.metadata)
    with pytest.raises(ValueError):
        schema.define(MetaData())


@pytest.fixture
def harness(candidate):
    actual = candidate[1][-1]
    minimal = MetaData()
    # Preserve the exact referred column types and unique keys, using only
    # schema fixtures for parent data. All seal constraints remain untouched.
    for fk in actual.foreign_key_constraints:
        parent = fk.referred_table
        if parent.name not in minimal.tables:
            Table(parent.name, minimal)
        stub = minimal.tables[parent.name]
        for element in fk.elements:
            c = element.column
            if c.name not in stub.c:
                stub.append_column(Column(c.name, c.type, nullable=False))
        names = tuple(e.column.name for e in fk.elements)
        stub.append_constraint(UniqueConstraint(*names))
    table = actual.to_metadata(minimal)
    # SQLAlchemy copies constraints without their dialect-conditional rule.
    # Use the project's exact-rule copier; never remove digest validation.
    from app.stock_scrap_schema import copy_conditions
    copy_conditions(SimpleNamespace(tables={actual.name: actual}), minimal)
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    minimal.create_all(engine)
    source = {name: uuid4() for name in schema.SOURCE[:-1]}
    source['original_ledger_cursor'] = 1
    case_id, event_id, person_id = uuid4(), uuid4(), uuid4()
    aliases = {name: hashlib.sha256(name.encode()).hexdigest() for name in schema.ALIASES}
    row = dict(id=uuid4(), created_at=datetime.now(timezone.utc), actor_user_id='user-one',
        actor_person_id=person_id, authorization_version=1, request_id='seal-test-0001',
        request_hash='a'*64, idempotency_key_hash=aliases['condition_key_hash'],
        reason='Close the exact missing request', command_jsonb={'schema_version': 'fixture-only'},
        kind='verify_region', case_id=case_id, **source,
        expected_event_id=event_id, historical_state='awaiting_regional', historical_sequence=1,
        claimed_event_hash='b'*64, history_hash='c'*64, key_token='d'*64, **aliases)
    with engine.begin() as db:
        db.execute(minimal.tables['users'].insert(), {'id': 'user-one'})
        db.execute(minimal.tables['people'].insert(), {'id': person_id})
        db.execute(minimal.tables[schema.CASES].insert(), {'id': case_id, **source})
        db.execute(minimal.tables[schema.EVENTS].insert(), {'id': event_id, 'case_id': case_id,
            'to_state': 'awaiting_regional', 'event_sequence': 1})
    yield engine, minimal, table, row
    engine.dispose()


STATES = sorted({state for _, before, after in TRANSITIONS for state in (before, after)})
LEGAL = {(kind, before) for kind, before, _ in TRANSITIONS if kind in schema.ACTIONS}


@pytest.mark.parametrize('kind,state', sorted(LEGAL))
def test_all_legal_historical_actions(harness, kind, state):
    engine, metadata, table, row = harness
    row.update(kind=kind, historical_state=state)
    with engine.begin() as db:
        db.execute(metadata.tables[schema.EVENTS].update().values(to_state=state))
        db.execute(table.insert(), row)
    # Deliberately unrelated claimed_event_hash is preserved, not promoted to
    # a proof of original preflight, event identity, or executable stock.
    with engine.connect() as db:
        assert db.execute(table.select()).mappings().one()['claimed_event_hash'] == 'b'*64


@pytest.mark.parametrize('kind,state', [(kind, state)
    for kind in (*schema.ACTIONS, 'submit', 'unknown')
    for state in STATES if (kind, state) not in LEGAL])
def test_illegal_action_state_rejected(harness, kind, state):
    engine, metadata, table, row = harness
    row.update(kind=kind, historical_state=state)
    with engine.begin() as db:
        db.execute(metadata.tables[schema.EVENTS].update().values(to_state=state))
    with pytest.raises(IntegrityError):
        with engine.begin() as db:
            db.execute(table.insert(), row)


@pytest.mark.parametrize('field', ('case_id', *schema.SOURCE, 'expected_event_id',
    'historical_sequence'))
def test_exact_source_and_predecessor_binding(harness, field):
    engine, _, table, row = harness
    row[field] = 2 if field in ('historical_sequence', 'original_ledger_cursor') else uuid4()
    with pytest.raises(IntegrityError):
        with engine.begin() as db:
            db.execute(table.insert(), row)


@pytest.mark.parametrize('field', ('request_hash', 'idempotency_key_hash', 'claimed_event_hash',
    'history_hash', 'key_token', *schema.ALIASES))
def test_invalid_digest_rejected(harness, field):
    engine, _, table, row = harness
    row[field] = 'Z'*64
    with pytest.raises(IntegrityError):
        with engine.begin() as db:
            db.execute(table.insert(), row)


def test_alias_collision_and_wrong_condition_key_rejected(harness):
    engine, _, table, row = harness
    for changed in ({'raw_key_hash': row['scrap_key_hash']}, {'idempotency_key_hash': 'e'*64}):
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(table.insert(), {**row, **changed})


@pytest.mark.parametrize('collision', ('request', 'key_token', *schema.ALIASES))
def test_existing_request_or_any_alias_cannot_be_claimed_again(harness, collision):
    engine, _, table, row = harness
    with engine.begin() as db:
        db.execute(table.insert(), row)
    other = {**row, 'id': uuid4(), 'request_id': 'different-request'}
    for name in ('key_token', *schema.ALIASES):
        other[name] = hashlib.sha256(('other-'+name).encode()).hexdigest()
    if collision == 'request':
        other['request_id'] = row['request_id']
    else:
        other[collision] = row[collision]
    other['idempotency_key_hash'] = other['condition_key_hash']
    with pytest.raises(IntegrityError):
        with engine.begin() as db:
            db.execute(table.insert(), other)
