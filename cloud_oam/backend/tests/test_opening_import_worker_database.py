"""No external IO: exercise real Session transaction dispatch and isolation.

SQLite substitutes SET LOCAL with SELECT 1; the real PG16 gate must separately
prove lock cancellation, API-role permission, and commit/rollback restoration.
"""
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import opening_import_worker_database as module


@pytest.fixture
def sql_runtime():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    engine.dialect.name = 'postgresql'  # Exercise the listener without a PG server.
    statements = []
    @event.listens_for(engine, 'before_cursor_execute', retval=True)
    def local_statement(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
        if statement.startswith('SET LOCAL '):
            return 'SELECT 1', ()
        return statement, parameters
    yield engine, statements
    engine.dispose()


def test_each_transaction_gets_budget_and_pooled_api_sessions_do_not(sql_runtime):
    engine, statements = sql_runtime
    worker = module.opening_import_worker_session_factory(engine)
    api = sessionmaker(bind=engine)
    assert worker.class_ is not api.class_
    with worker() as db:
        assert db.scalar(text('SELECT 3')) == 3
        db.commit()
        assert db.scalar(text('SELECT 4')) == 4
        db.rollback()
        assert db.scalar(text('SELECT 5')) == 5
    with api() as db:
        assert db.scalar(text('SELECT 6')) == 6
    assert statements == [
        "SET LOCAL lock_timeout = '5000ms'", "SET LOCAL statement_timeout = '30000ms'", 'SELECT 3',
        "SET LOCAL lock_timeout = '5000ms'", "SET LOCAL statement_timeout = '30000ms'", 'SELECT 4',
        "SET LOCAL lock_timeout = '5000ms'", "SET LOCAL statement_timeout = '30000ms'", 'SELECT 5',
        'SELECT 6',
    ]


def test_independent_factories_do_not_stack_or_override_each_others_limits(sql_runtime):
    engine, statements = sql_runtime
    first = module.opening_import_worker_session_factory(engine, lock_timeout_ms=100, statement_timeout_ms=1000)
    second = module.opening_import_worker_session_factory(engine, lock_timeout_ms=200, statement_timeout_ms=2000)
    with first() as db: assert db.scalar(text('SELECT 1')) == 1
    with second() as db: assert db.scalar(text('SELECT 2')) == 2
    assert statements == [
        "SET LOCAL lock_timeout = '100ms'", "SET LOCAL statement_timeout = '1000ms'", 'SELECT 1',
        "SET LOCAL lock_timeout = '200ms'", "SET LOCAL statement_timeout = '2000ms'", 'SELECT 2',
    ]


@pytest.mark.parametrize('lock,statement', [(True, 1000), (100, True), (0, 1000), (100, 99), (100, 120001), ('5s', 1000)])
def test_invalid_budgets_fail_before_creating_sessions(sql_runtime, lock, statement):
    engine, statements = sql_runtime
    with pytest.raises(ValueError, match='database_budget_invalid'):
        module.opening_import_worker_session_factory(engine, lock_timeout_ms=lock, statement_timeout_ms=statement)
    assert statements == []


def test_non_postgresql_worker_factory_is_rejected():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    try:
        with pytest.raises(ValueError, match='database_budget_invalid'):
            module.opening_import_worker_session_factory(engine)
    finally:
        engine.dispose()
