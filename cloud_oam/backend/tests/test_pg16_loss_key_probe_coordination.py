"""Local coordination checks; these do not certify the real PostgreSQL races."""
from threading import Event

import pytest
from sqlalchemy.exc import DBAPIError

import pg16_stock_loss_seal_gate as gate


class ProbeSession:
    def __init__(self):
        self.prepared = False
        self.constraints = []
        self.rollbacks = 0
        self.on_constraint = lambda: None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, statement):
        sql = str(statement)
        if sql.startswith('SET CONSTRAINTS'):
            assert self.prepared
            self.constraints.append(sql)
            self.on_constraint()

    def scalar(self, statement):
        assert str(statement) == 'SELECT pg_backend_pid()'
        return 41

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def probe_harness(monkeypatch):
    db = ProbeSession()
    diagnostics = []
    monkeypatch.setattr(gate, 'Session', lambda _: db)
    monkeypatch.setattr(gate, 'generic_probe', lambda *_: setattr(db, 'prepared', True))
    monkeypatch.setattr(gate, 'generic_probe_diagnostics',
                        lambda *args: diagnostics.append(args))
    context = {'engines': {'star_oam_api': object(), 'star_oam_migrator': object()}}
    return db, context, diagnostics


@pytest.mark.parametrize('kind', ('shipments', 'receipts'))
def test_prepared_probe_waits_for_holder_and_finishes_before_holder_release(probe_harness, kind):
    db, context, _ = probe_harness
    holder = Event()

    def fence():
        assert holder.is_set(), 'target fence ran outside the held-lock interval'

    db.on_constraint = fence
    with gate.prepared_generic_key_probe(context, object(), kind) as (future, execute, phases, pids):
        # Readiness means the INSERT/flush finished, but the deferred fence has
        # not run. The holder is established only after this observed boundary.
        assert db.prepared and phases['worker'] == 'prepared'
        assert pids == {'waiter': 41}
        assert not db.constraints and not future.done()
        holder.set()
        execute.set()
        assert future.result(timeout=2) == 'allowed'
        assert holder.is_set()
        holder.clear()
    assert db.constraints == [f'SET CONSTRAINTS trg_{kind}_loss_seal_0146 IMMEDIATE']
    assert db.rollbacks == 1 and phases['worker'] == 'rolled_back'


def test_failed_preparation_preserves_original_error_and_never_runs_fence(probe_harness, monkeypatch):
    db, context, diagnostics = probe_harness
    failure = RuntimeError('synthetic INSERT rejected')

    def rejected(*_):
        raise failure

    monkeypatch.setattr(gate, 'generic_probe', rejected)
    with pytest.raises(RuntimeError) as result:
        with gate.prepared_generic_key_probe(context, object(), 'shipments'):
            pytest.fail('failed preparation must not admit the lock phase')
    assert result.value is failure
    assert db.rollbacks == 1 and not db.constraints
    assert len(diagnostics) == 1


def test_holder_failure_releases_abandoned_worker_without_firing_fence(probe_harness):
    db, context, _ = probe_harness
    with pytest.raises(RuntimeError, match='holder failed'):
        with gate.prepared_generic_key_probe(context, object(), 'receipts') as (future, _, phases, _):
            raise RuntimeError('holder failed')
    # The context has joined the worker; an abandoned preparation cannot hang
    # executor shutdown or accidentally run a deferred business constraint.
    assert future.done() and future.result() == 'abandoned'
    assert not db.constraints and db.rollbacks == 1
    assert phases['worker'] == 'rolled_back'


@pytest.mark.parametrize('kind', ('shipments', 'receipts'))
def test_insert_phase_publishes_pid_before_any_insert_and_waits_for_holder(
        probe_harness, monkeypatch, kind):
    db, context, _ = probe_harness
    holder = Event()

    def insert(*_):
        assert holder.is_set(), 'raw INSERT started before the seal holder'
        assert phases['worker'] == 'inserting'
        db.prepared = True

    monkeypatch.setattr(gate, 'generic_probe', insert)
    with gate.prepared_generic_key_probe(context, object(), kind, flush_before_ready=False) as (
            future, execute, phases, pids):
        assert pids == {'waiter': 41} and phases['worker'] == 'awaiting_insert'
        assert not db.prepared and not db.constraints and not future.done()
        holder.set()
        execute.set()
        assert future.result(timeout=2) == 'allowed'
    assert db.prepared and db.rollbacks == 1


def test_abandoned_insert_phase_never_inserts_or_fires_constraint(probe_harness):
    db, context, _ = probe_harness
    with gate.prepared_generic_key_probe(context, object(), 'receipts', flush_before_ready=False) as (
            future, _, phases, _):
        assert phases['worker'] == 'awaiting_insert'
    assert future.result() == 'abandoned'
    assert not db.prepared and not db.constraints and db.rollbacks == 1


def test_insert_failure_after_pid_publication_is_not_a_successful_fence(probe_harness, monkeypatch):
    db, context, _ = probe_harness
    failure = RuntimeError('synthetic immediate guard rejection')

    def rejected(*_):
        raise failure

    monkeypatch.setattr(gate, 'generic_probe', rejected)
    with gate.prepared_generic_key_probe(context, object(), 'shipments', flush_before_ready=False) as (
            future, execute, _, _):
        execute.set()
        with pytest.raises(RuntimeError) as result:
            future.result(timeout=2)
        assert result.value is failure
    assert not db.constraints and db.rollbacks == 1


class SyntheticPostgresError(Exception):
    def __init__(self, sqlstate, message):
        super().__init__(message)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(('sqlstate', 'message', 'expected'), (
    ('23514', '0146 sealed loss key cannot execute', 'sealed'),
    ('57014', '0146 sealed loss key cannot execute', None),
    ('23514', 'unrelated constraint rejected', None),
))
def test_only_exact_loss_fence_rejection_is_an_accepted_race_result(
        probe_harness, sqlstate, message, expected):
    db, context, _ = probe_harness

    def rejected():
        raise DBAPIError(None, None, SyntheticPostgresError(sqlstate, message))

    db.on_constraint = rejected
    with gate.prepared_generic_key_probe(context, object(), 'shipments') as (future, execute, _, _):
        execute.set()
        if expected is None:
            with pytest.raises(AssertionError):
                future.result(timeout=2)
        else:
            assert future.result(timeout=2) == expected
    assert db.rollbacks == 1


def test_lock_diagnostics_do_not_report_query_text_parameters_or_exception_details(monkeypatch, capsys):
    statements = []

    class Owner:
        def connect(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, statement, parameters=None):
            statements.append((str(statement), parameters))
            if parameters is not None:
                raise RuntimeError('must-not-appear: confidential query parameter')

    gate.generic_probe_diagnostics(Owner(), 'receipts', {'worker': 'constraint'}, {'waiter': 41})
    output = capsys.readouterr().out
    assert 'RuntimeError' in output and 'constraint' in output and '41' in output
    assert 'must-not-appear' not in output and 'confidential' not in output
    assert 'statement_timeout' in statements[0][0]
    assert 'pg_blocking_pids(pid)' in statements[1][0]
    assert statements[1][1] == {'pids': [41]}
