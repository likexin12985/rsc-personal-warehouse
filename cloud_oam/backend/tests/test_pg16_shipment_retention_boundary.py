"""Focused regression for the observed 041cb inventory CI refusal mismatch.

These tests validate gate decisions and failure handling, not a real PG16 run.
"""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import test_postgresql16_release_gate as gate


MESSAGE = '0168 downgrade blocked: shipment projection facts exist'
TERMINAL = 'ValueError: ' + MESSAGE
SHIPMENT_QUERY = "SELECT EXISTS (SELECT 1 FROM public.material_requests WHERE shipment_status <> 'not_started')"


@pytest.mark.parametrize('required,retained,expected', [
    ('20260929_0089', dict(shipment_projections=True, opening_actors=True), MESSAGE),
    ('20261130_0151', dict(shipment_projections=True, loss_dispositions=True), MESSAGE),
    ('20261219_0170', dict(shipment_projections=True), 'required'),
    ('20260929_0089', dict(shipment_projections=False), 'required'),
])
def test_shipment_facts_select_only_a_newer_crossed_guard(required, retained, expected):
    assert gate._retention_chain_blocker('20260924_0084',
        blocking_revision=required, blocker='required', retained=retained) == expected


@pytest.mark.parametrize('returncode,stdout,stderr,accepted', [
    (1, '', 'Traceback (most recent call last):\n' + TERMINAL + '\n', True),
    (1, '', TERMINAL + '\n\n', True),
    (0, '', TERMINAL, False),
    (1, TERMINAL, '', False),
    (1, '', "    raise ValueError('" + MESSAGE + "')\nPermissionError: denied", False),
    (1, '', TERMINAL + '\nRuntimeError: cleanup failed', False),
    (1, '', 'RuntimeError: ' + MESSAGE, False),
    (1, '', TERMINAL + ' unexpectedly', False),
])
def test_only_exact_terminal_value_error_proves_the_0168_guard(returncode, stdout, stderr, accepted):
    result = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    if accepted:
        gate._assert_shipment_retention_failure(result)
    else:
        with pytest.raises(AssertionError, match='exact terminal 0168'):
            gate._assert_shipment_retention_failure(result)


def prepare_boundary(monkeypatch, snapshots, *, terminal=TERMINAL):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    queries = []

    def execute(query):
        queries.append(query)
        assert query.startswith('SELECT EXISTS ('), 'retention fact reads must not mutate'
        return SimpleNamespace(fetchone=lambda: (query == SHIPMENT_QUERY,))

    connection.execute.side_effect = execute
    monkeypatch.setattr(gate.psycopg, 'connect', lambda **_: connection)
    monkeypatch.setattr(gate, '_connection_parameters', lambda **_: {})
    monkeypatch.setattr(gate, '_role_password', lambda _: None)
    monkeypatch.setattr(gate, '_sqlalchemy_url', lambda **_: None)
    monkeypatch.setattr(gate, '_current_revision', lambda: gate.HEAD_REVISION)
    capture = MagicMock(side_effect=snapshots)
    monkeypatch.setattr(gate, '_shipment_retention_snapshot', capture)
    command = MagicMock(return_value=SimpleNamespace(
        returncode=1, stdout='', stderr=terminal))
    monkeypatch.setattr(gate, '_run_alembic', command)
    historical = MagicMock()
    historical.__enter__.return_value = historical
    historical.scalar.return_value = 'star_oam_migrator'
    engine = MagicMock()
    engine.connect.return_value = historical
    monkeypatch.setattr(gate, 'create_engine', lambda *args, **kwargs: engine)
    environment = MagicMock()
    monkeypatch.setattr(gate, 'EnvironmentContext', lambda *args: nullcontext(environment))
    monkeypatch.setattr(gate.Operations, 'context', lambda *args: nullcontext())
    old_guard = MagicMock(side_effect=RuntimeError('0089 transition blocked'))
    return capture, command, historical, old_guard, queries


def invoke(old_guard):
    gate._assert_retention_downgrade('20260924_0084', blocking_revision='20260929_0089',
        blocker='0089 transition blocked', retention_guard=old_guard)


def test_new_guard_keeps_older_independent_guard_and_three_exact_readbacks(monkeypatch):
    state = {'facts': {'shipments': (1, 'a' * 64)}, 'catalog': 'b' * 64}
    capture, command, historical, old_guard, queries = prepare_boundary(
        monkeypatch, [state, deepcopy(state), deepcopy(state)])
    invoke(old_guard)
    assert capture.call_count == 3
    old_guard.assert_called_once_with()
    historical.begin.return_value.rollback.assert_called_once_with()
    command.assert_called_once_with('downgrade', '20260924_0084', expect_success=False)
    assert queries.count(SHIPMENT_QUERY) == 1


@pytest.mark.parametrize('field', ['facts', 'catalog'])
@pytest.mark.parametrize('stage', ['chain', 'independent'])
def test_refusal_that_changes_facts_or_catalog_cannot_pass(monkeypatch, field, stage):
    state = {'facts': {'shipments': (1, 'a' * 64)}, 'catalog': 'b' * 64}
    changed = deepcopy(state)
    changed[field] = {'shipments': (1, 'c' * 64)} if field == 'facts' else 'c' * 64
    snapshots = [state, changed] if stage == 'chain' else [state, deepcopy(state), changed]
    _, _, historical, old_guard, _ = prepare_boundary(monkeypatch, snapshots)
    with pytest.raises(AssertionError, match='changed facts or catalog'):
        invoke(old_guard)
    assert old_guard.call_count == int(stage == 'independent')
    assert historical.begin.return_value.rollback.call_count == int(stage == 'independent')


def test_source_excerpt_followed_by_different_failure_never_runs_old_guard(monkeypatch):
    state = {'facts': {}, 'catalog': 'b' * 64}
    capture, _, _, old_guard, _ = prepare_boundary(monkeypatch, [state],
        terminal=TERMINAL + '\nPermissionError: unrelated failure')
    with pytest.raises(AssertionError, match='exact terminal 0168'):
        invoke(old_guard)
    assert capture.call_count == 1
    old_guard.assert_not_called()
