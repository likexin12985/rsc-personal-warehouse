"""Owned spawned-child lifecycle tests; no business DB, network or real secrets."""
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import Mock

import pytest
from app import opening_count_import_worker as worker
from app import opening_import_worker_entry as entry
from app.daily_reconciliation.process_entry import ProcessEntryError, ProcessOutcomeUnknown, run_owned_job
from app.formal_services import opening_count_import_termination as termination

FIRST, SECOND = UUID(int=1), UUID(int=2)


from opening_import_process_fixture import hung_source as _hung_source, healthy as _healthy


class Stop:
    def __init__(self, pages=1): self.pages, self.waits = pages, 0
    def is_set(self): return self.waits >= self.pages
    def wait(self, seconds):
        assert seconds == 5
        self.waits += 1


def _poll(monkeypatch, processor, *, page, pages=1):
    monkeypatch.setattr(worker, '_opening_import_queue_page', lambda *a, **k: page())
    monkeypatch.setattr(termination, 'sweep_awaiting_opening_imports',
                        lambda *a, **k: SimpleNamespace(next_after_id=None, failed_ids=()))
    output = []
    worker._poll_opening_imports(lambda: pytest.fail('unexpected parent DB'), storage=None,
        poll_seconds=5, stop_event=Stop(pages), emit=output.append, process_job=processor)
    return output


def _assert_reaped(marker):
    assert marker.exists(), 'test did not reach source read before the deadline'
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    return pid


def test_hung_source_child_is_reaped_before_next_original_job(monkeypatch, tmp_path):
    marker = tmp_path/'hung.pid'
    calls = []
    def supervise(target, payload, *, maximum_seconds):
        assert target is entry._opening_import_job_worker
        assert maximum_seconds == 60 and set(payload) == {'job_id'}
        calls.append(payload['job_id'])
        if payload['job_id'] == str(FIRST):
            return run_owned_job(_hung_source, {**payload, 'marker': str(marker)}, maximum_seconds=2)
        _assert_reaped(marker)
        return run_owned_job(_healthy, payload, maximum_seconds=5)
    monkeypatch.setattr(entry, 'run_owned_job', supervise)
    output = _poll(monkeypatch, entry.process_owned_opening_import, page=lambda: (FIRST, SECOND))
    assert calls == [str(FIRST), str(SECOND)]
    assert output == [
        {'ok': False, 'job_id': str(FIRST), 'code': 'opening_import_worker_retry_or_review_required'},
        {'ok': True, 'job_id': str(SECOND), 'status': 'awaiting_confirmation', 'recovered': False},
    ]
    _assert_reaped(marker)


def test_unknown_result_does_not_reexecute_an_observed_waiting_job(monkeypatch, tmp_path):
    # The file is a synthetic authoritative-state stand-in. Real COMMIT and
    # deferred-guard behavior is explicitly reserved for the PG16 gate.
    marker, committed = tmp_path/'hung.pid', tmp_path/'observed-status'
    calls = []
    def supervise(target, payload, **kwargs):
        calls.append(payload['job_id'])
        return run_owned_job(_hung_source, {**payload, 'marker': str(marker),
                             'committed': str(committed)}, maximum_seconds=2)
    monkeypatch.setattr(entry, 'run_owned_job', supervise)
    output = _poll(monkeypatch, entry.process_owned_opening_import,
                   page=lambda: () if committed.exists() else (FIRST,), pages=2)
    assert calls == [str(FIRST)] and committed.read_text() == 'awaiting_confirmation'
    assert output[0] == {'ok': False, 'job_id': str(FIRST), 'code': 'opening_import_worker_retry_or_review_required'}
    assert output[1]['status'] == 'idle'
    _assert_reaped(marker)


def test_unconfirmed_child_cleanup_stops_scheduler_before_next_job(monkeypatch):
    calls = []
    def supervise(target, payload, **kwargs):
        calls.append(payload['job_id'])
        raise ProcessEntryError('worker_cleanup_unconfirmed_pid_812345')
    monkeypatch.setattr(entry, 'run_owned_job', supervise)
    with pytest.raises(entry.OpeningImportWorkerSupervisionError) as caught:
        _poll(monkeypatch, entry.process_owned_opening_import, page=lambda: (FIRST, SECOND))
    assert caught.value.worker_pid == 812345 and calls == [str(FIRST)]


@pytest.mark.parametrize('phase', ['kill', 'join', 'status', 'close'])
def test_real_executor_cleanup_errors_stop_next_job_and_retain_pid(monkeypatch, phase):
    from app.daily_reconciliation import process_entry

    # Actual executor and pipes, synthetic process: no live child is left
    # behind when injecting a failed cleanup syscall.
    original_context = process_entry.mp.get_context('spawn')
    trace, calls = [], []

    class Child:
        pid = 812345
        alive = True
        def start(self): trace.append('start')
        def is_alive(self):
            if phase == 'status':
                raise OSError('synthetic private process status failure')
            return self.alive
        def kill(self):
            trace.append('kill')
            if phase == 'kill':
                raise OSError('synthetic private kill failure')
            self.alive = False
        def join(self, **kwargs):
            trace.append('join')
            if phase == 'join':
                raise RuntimeError('synthetic private join failure')
        def close(self):
            trace.append('close')
            if phase == 'close':
                raise OSError('synthetic private close failure')

    context = SimpleNamespace(Pipe=original_context.Pipe, Process=lambda **kwargs: Child())
    monkeypatch.setattr(process_entry.mp, 'get_context', lambda method: context)

    def process(identifier):
        calls.append(identifier)
        return entry.process_owned_opening_import(identifier, maximum_seconds=1)

    with pytest.raises(entry.OpeningImportWorkerSupervisionError) as caught:
        _poll(monkeypatch, process, page=lambda: (FIRST, SECOND))
    assert caught.value.worker_pid == 812345
    assert calls == [FIRST] and trace[0] == 'start'
    assert str(caught.value) == 'opening_import_worker_supervision_failed'


@pytest.mark.parametrize('error', [OSError('private OS failure'), RuntimeError('private supervisor failure')])
def test_unclassified_executor_error_stops_scheduler(monkeypatch, error):
    calls = []
    def supervise(target, payload, **kwargs):
        calls.append(payload['job_id'])
        raise error
    monkeypatch.setattr(entry, 'run_owned_job', supervise)
    with pytest.raises(entry.OpeningImportWorkerSupervisionError) as caught:
        _poll(monkeypatch, entry.process_owned_opening_import, page=lambda: (FIRST, SECOND))
    assert calls == [str(FIRST)] and caught.value.worker_pid is None
    assert str(caught.value) == 'opening_import_worker_supervision_failed'


@pytest.mark.parametrize('result', [
    {'job_id': str(SECOND), 'status': 'succeeded', 'recovered': False},
    {'job_id': str(FIRST), 'status': 'invented', 'recovered': False},
    {'job_id': str(FIRST), 'status': 'succeeded', 'recovered': 1},
    {'job_id': str(FIRST), 'status': 'succeeded', 'recovered': False, 'source': 'private'},
])
def test_invalid_child_receipt_stays_unknown_without_replay(monkeypatch, result):
    runner = Mock(return_value=result)
    monkeypatch.setattr(entry, 'run_owned_job', runner)
    with pytest.raises(ProcessOutcomeUnknown): entry.process_owned_opening_import(FIRST)
    assert runner.call_count == 1


def _settings():
    return SimpleNamespace(environment='production', database_url='postgresql+psycopg://synthetic-never-connect',
        opening_count_import_enabled=True, file_storage_configuration_ready=lambda: True,
        database_expected_runtime_role='star_oam_api', database_expected_migration_role='star_oam_migrator',
        file_storage_region='cn-hangzhou', file_storage_bucket='synthetic-private')


def _configured_parent(monkeypatch):
    from app import database, database_security, opening_import_worker_database
    from app.formal_services import file_storage
    monkeypatch.setattr(worker, 'get_settings', _settings)
    monkeypatch.setattr(database, 'engine', object())
    monkeypatch.setattr(database_security, 'validate_production_database_security', lambda *a, **k: None)
    monkeypatch.setattr(opening_import_worker_database, 'opening_import_worker_session_factory', lambda _: object())
    monkeypatch.setattr(file_storage, 'AliyunOssV2StorageAdapter', lambda **k: pytest.fail('parent built SDK client'))
    monkeypatch.setattr(worker, 'process_one_opening_count_import', lambda *a, **k: pytest.fail('parent ran original job'))


def test_production_cli_poll_uses_supervised_processor(monkeypatch, capsys):
    _configured_parent(monkeypatch)
    calls = []
    def process(identifier):
        calls.append(identifier)
        return worker.OpeningImportWorkerResult(identifier, 'awaiting_confirmation')
    monkeypatch.setattr(entry, 'process_owned_opening_import', process)
    def poll(factory, **kwargs):
        assert kwargs['storage'] is None and kwargs['process_job'] is process
        kwargs['process_job'](FIRST)
    monkeypatch.setattr(worker, '_poll_opening_imports', poll)
    assert worker.main(['--poll-seconds', '5']) == 0 and calls == [FIRST]


def test_production_cli_single_job_uses_supervised_processor(monkeypatch, capsys):
    _configured_parent(monkeypatch)
    process = Mock(return_value=worker.OpeningImportWorkerResult(FIRST, 'awaiting_confirmation'))
    monkeypatch.setattr(entry, 'process_owned_opening_import', process)
    assert worker.main(['--job-id', str(FIRST)]) == 0
    assert process.call_args.args == (FIRST,)
    assert json.loads(capsys.readouterr().out)['status'] == 'awaiting_confirmation'


def test_production_cli_supervision_failure_is_sanitized_and_nonzero(monkeypatch, capsys):
    _configured_parent(monkeypatch)
    process = Mock(side_effect=entry.OpeningImportWorkerSupervisionError(812345))
    monkeypatch.setattr(entry, 'process_owned_opening_import', process)
    assert worker.main(['--job-id', str(FIRST)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        'ok': False, 'code': 'opening_import_worker_supervision_failed', 'worker_pid': 812345}


def test_cli_remains_disabled_when_import_flag_is_false(monkeypatch, capsys):
    settings = _settings(); settings.opening_count_import_enabled = False
    monkeypatch.setattr(worker, 'get_settings', lambda: settings)
    monkeypatch.setattr(entry, 'process_owned_opening_import', lambda *a, **k: pytest.fail('disabled worker started'))
    assert worker.main(['--job-id', str(FIRST)]) == 2
    assert json.loads(capsys.readouterr().out)['code'] == 'opening_import_worker_not_configured'


def test_child_owns_engine_storage_and_original_job_and_disposes(monkeypatch):
    import sqlalchemy
    from sqlalchemy.pool import NullPool
    from app import config, database_security, opening_import_worker_database
    from app import file_storage_composition
    engine = SimpleNamespace(dispose=Mock())
    create = Mock(return_value=engine)
    factory, storage = object(), object()
    monkeypatch.setattr(config, 'get_settings', _settings)
    monkeypatch.setattr(sqlalchemy, 'create_engine', create)
    validate = Mock()
    monkeypatch.setattr(database_security, 'validate_production_database_security', validate)
    monkeypatch.setattr(opening_import_worker_database, 'opening_import_worker_session_factory', lambda item: factory if item is engine else pytest.fail('wrong engine'))
    compose = Mock(return_value=storage)
    monkeypatch.setattr(file_storage_composition, 'create_file_storage_adapter', compose)
    process = Mock(return_value=worker.OpeningImportWorkerResult(FIRST, 'awaiting_confirmation'))
    monkeypatch.setattr(worker, 'process_one_opening_count_import', process)
    result = entry._opening_import_job_worker({'job_id': str(FIRST)}, time.monotonic()+60)
    assert create.call_args.kwargs['poolclass'] is NullPool and create.call_args.kwargs['hide_parameters'] is True
    assert process.call_args.args == (factory,) and process.call_args.kwargs == {'storage': storage, 'job_id': FIRST}
    assert result == {'job_id': str(FIRST), 'status': 'awaiting_confirmation', 'recovered': False}
    assert validate.call_count == engine.dispose.call_count == 1
    assert compose.call_count == 1
