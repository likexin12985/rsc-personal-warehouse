"""Focused failure-handling checks for the 2a49cbc hosted loss-gate failure.

These mocked checks are not evidence that the real PG16 loss leg passed.
"""
import ast
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

import pg16_stock_loss_release_checks as checks


TERMINAL = 'ValueError: 0168 downgrade blocked: shipment projection facts exist'


def rejection(message, *, sqlstate='P0001'):
    original = RuntimeError('original database rejection')
    original.sqlstate = sqlstate
    original.diag = SimpleNamespace(message_primary=message)
    return DBAPIError('DO original guard', None, original)


def prepare(monkeypatch, *, revision='20261125_0146', snapshots=None, error=None):
    table, message = checks._RETAINED_GUARDS[revision]
    before = dict(revision=[checks.HEAD_REVISION], facts={table: (1, 'a' * 64)},
        shipment_facts=True, catalog='b' * 64)
    capture = MagicMock(side_effect=snapshots or [deepcopy(before) for _ in range(3)])
    monkeypatch.setattr(checks, '_retained_snapshot', capture)
    db = MagicMock()
    db.execute.return_value.one.return_value = ('star_oam_migrator', 'star_oam_migrator')
    owner = MagicMock()
    owner.connect.return_value = nullcontext(db)
    migration = SimpleNamespace(revision=revision,
        downgrade=MagicMock(side_effect=error or rejection(message)))
    monkeypatch.setattr(checks, '_module', lambda *_: migration)
    environment = MagicMock()
    monkeypatch.setattr(checks, 'EnvironmentContext', lambda *_: nullcontext(environment))
    monkeypatch.setattr(checks.Operations, 'context', lambda *_: nullcontext())
    migrate = MagicMock(return_value=SimpleNamespace(returncode=1, stdout='', stderr=TERMINAL))
    return SimpleNamespace(before=before, capture=capture, db=db, owner=owner,
        migration=migration, migrate=migrate, revision=revision)


def invoke(fixture):
    checks._assert_retained_loss(fixture.owner, migrate=fixture.migrate,
        label='retained-loss-downgrade', destination='20261124_0145',
        blocking_revision=fixture.revision)


@pytest.mark.parametrize('revision', tuple(checks._RETAINED_GUARDS))
def test_each_original_guard_runs_once_then_rolls_back_and_rereads(monkeypatch, revision):
    fixture = prepare(monkeypatch, revision=revision)
    invoke(fixture)
    fixture.migration.downgrade.assert_called_once_with()
    fixture.db.begin.return_value.rollback.assert_called_once_with()
    assert fixture.capture.call_count == 3
    fixture.migrate.assert_called_once_with('retained-loss-downgrade', 'downgrade',
        '20261124_0145', '0168 downgrade blocked: shipment projection facts exist')


@pytest.mark.parametrize('returncode,stdout,stderr', [
    (0, '', TERMINAL),
    (1, TERMINAL, ''),
    (1, '', TERMINAL + '\nPermissionError: different failure'),
    (1, '', "raise ValueError('0168 downgrade blocked: shipment projection facts exist')"),
])
def test_invalid_chain_failure_never_invokes_independent_guard(monkeypatch, returncode, stdout, stderr):
    fixture = prepare(monkeypatch)
    fixture.migrate.return_value = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    with pytest.raises(AssertionError, match='exact terminal 0168'):
        invoke(fixture)
    fixture.migration.downgrade.assert_not_called()
    fixture.owner.connect.assert_not_called()
    assert fixture.capture.call_count == 1


@pytest.mark.parametrize('error,match', [
    (rejection(checks._RETAINED_GUARDS['20261125_0146'][1], sqlstate='42501'), 'SQLSTATE'),
    (rejection('0146 immutable loss seal history requires retention extra'), 'guard'),
    (RuntimeError(checks._RETAINED_GUARDS['20261125_0146'][1]), 'immutable loss seal'),
])
def test_independent_failure_requires_database_sqlstate_and_exact_primary_message(monkeypatch, error, match):
    fixture = prepare(monkeypatch, error=error)
    expected_type = RuntimeError if isinstance(error, RuntimeError) else AssertionError
    with pytest.raises(expected_type, match=match):
        invoke(fixture)
    fixture.db.begin.return_value.rollback.assert_called_once_with()
    assert fixture.capture.call_count == 2


def test_independent_unexpected_success_is_failure_and_still_rolls_back(monkeypatch):
    fixture = prepare(monkeypatch)
    fixture.migration.downgrade.side_effect = None
    with pytest.raises(pytest.fail.Exception, match='DID NOT RAISE'):
        invoke(fixture)
    fixture.db.begin.return_value.rollback.assert_called_once_with()


@pytest.mark.parametrize('stage', ['chain', 'independent'])
@pytest.mark.parametrize('field', ['facts', 'catalog', 'revision'])
def test_any_post_refusal_fact_catalog_or_head_change_fails(monkeypatch, stage, field):
    fixture = prepare(monkeypatch)
    changed = deepcopy(fixture.before)
    changed[field] = 'changed'
    fixture.capture.side_effect = ([fixture.before, changed] if stage == 'chain' else
        [fixture.before, deepcopy(fixture.before), changed])
    with pytest.raises(AssertionError, match='changed facts or catalog'):
        invoke(fixture)
    assert fixture.migration.downgrade.call_count == int(stage == 'independent')
    assert fixture.db.begin.return_value.rollback.call_count == int(stage == 'independent')


@pytest.mark.parametrize('missing', ['shipment', 'original'])
def test_guard_selection_requires_retained_facts_before_any_downgrade(monkeypatch, missing):
    fixture = prepare(monkeypatch)
    before = deepcopy(fixture.before)
    if missing == 'shipment':
        before['shipment_facts'] = False
    else:
        before['facts']['stock_loss_request_seals'] = (0, 'a' * 64)
    fixture.capture.side_effect = [before]
    with pytest.raises(AssertionError, match='facts'):
        invoke(fixture)
    fixture.migrate.assert_not_called()


def test_destination_must_cross_the_original_guard(monkeypatch):
    fixture = prepare(monkeypatch)
    with pytest.raises(AssertionError, match='both retention boundaries'):
        checks._assert_retained_loss(fixture.owner, migrate=fixture.migrate,
            label='invalid', destination=fixture.revision, blocking_revision=fixture.revision)
    fixture.capture.assert_not_called()


def test_snapshot_enumerates_new_tables_and_never_returns_plaintext(monkeypatch):
    db = MagicMock()
    owner = SimpleNamespace(connect=lambda: nullcontext(db))
    tables = ('alembic_version', 'future_retained_facts', 'material_requests')
    reads = []

    def execute(statement):
        sql = str(statement)
        reads.append(sql)
        if sql.startswith('SET TRANSACTION'):
            return None
        if sql.startswith('SELECT current_user'):
            return SimpleNamespace(one=lambda: ('star_oam_migrator', 'star_oam_migrator',
                'rsc_pg16_release_gate', 16))
        assert sql.startswith('SELECT to_jsonb(fact)::text FROM public.')
        return [('private synthetic row',)]

    db.execute.side_effect = execute
    db.scalars.side_effect = [SimpleNamespace(all=lambda: [checks.HEAD_REVISION]), tables]
    db.scalar.return_value = True
    probe = SimpleNamespace(snapshot=MagicMock(return_value=dict(
        tables={table: {'acl': []} for table in tables}, functions={})))
    monkeypatch.setattr(checks, '_module', lambda *_: probe)
    result = checks._retained_snapshot(owner)
    assert set(result['facts']) == set(tables)
    assert all(count == 1 and len(digest) == 64 for count, digest in result['facts'].values())
    assert 'private synthetic row' not in repr(result)
    assert reads[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    probe.snapshot.assert_called_once_with(db)


@pytest.mark.parametrize('script', ['run_local_pg16_stock_loss_submit_checks.py',
    'run_local_pg16_stock_loss_review_seal_checks.py'])
def test_native_callback_preserves_real_stderr_separate_from_stdout(script, tmp_path):
    # Compile only the actual nested callback; no cluster or original suite runs.
    path = checks.CLOUD_ROOT / 'scripts' / script
    tree = ast.parse(path.read_text())
    callbacks = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        and node.name == 'migrate']
    assert len(callbacks) == 1
    result = SimpleNamespace(returncode=1, stdout=TERMINAL, stderr='PermissionError: denied')
    subprocess = SimpleNamespace(run=MagicMock(return_value=result))
    scope = dict(directory=tmp_path, CLOUD=checks.CLOUD_ROOT, environment={},
        subprocess=subprocess, sys=SimpleNamespace(executable='synthetic-python'))
    exec(compile(ast.Module(body=callbacks, type_ignores=[]), str(path), 'exec'), scope)
    completed = scope['migrate']('retained', 'downgrade', '20261124_0145', '0168 downgrade blocked')
    assert completed is result
    assert subprocess.run.call_args.kwargs['capture_output'] is True
    assert subprocess.run.call_args.kwargs['text'] is True
    assert (tmp_path / 'retained.log').read_text() == result.stdout + result.stderr
    with pytest.raises(AssertionError, match='exact terminal 0168'):
        checks._assert_shipment_retention_failure(completed)
