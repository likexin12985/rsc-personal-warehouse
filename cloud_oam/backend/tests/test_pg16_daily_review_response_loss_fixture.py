"""Local fixture checks only; these do not establish a real PG16 COMMIT."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from app.daily_reconciliation import review_entry
from app.daily_reconciliation.process_entry import ProcessEntryError, ProcessOutcomeUnknown
import pg16_daily_review_gate as gate


def test_spawn_import_does_not_load_parent_http_or_database_fixtures():
    backend=Path(__file__).parents[1]
    environment=dict(os.environ,PYTHONPATH=os.pathsep.join((str(backend),str(backend/'tests'))))
    result=subprocess.run([sys.executable,'-c',
        "import sys; import pg16_daily_review_gate; "
        "assert not ({'pg16_daily_review_fixture', 'test_formal_files_service', "
        "'test_formal_access', 'app.main', 'sqlalchemy', 'fastapi', 'pytest'} & sys.modules.keys())"],
        env=environment,capture_output=True,text=True,timeout=8)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('result',[
    {'outcome':'rejected','code':'daily_review_authentication_required','status':401},
    {'outcome':'rejected','code':'daily_review_forbidden','status':403},
    {'outcome':'rejected','code':'daily_review_request_sealed','status':409},
    {'outcome':'rejected','code':'daily_review_invalid_command','status':422},
    {'outcome':'rejected','code':'daily_review_database_rejected','status':503},
    {'outcome':'observed','receipt':{'recorded':False,'stock_written':False}},
    {'outcome':'observed','receipt':{'recorded':'true'}},
    {'outcome':'observed','receipt':None},
])
def test_uncommitted_result_returns_unchanged_instead_of_becoming_unknown(monkeypatch,result):
    payload={'synthetic':'not-a-database-request'}
    worker=Mock(return_value=result);sleep=Mock()
    monkeypatch.setattr(review_entry,'_review_worker',worker)
    monkeypatch.setattr(gate.time,'sleep',sleep)
    assert gate._lose_committed_response(payload,123.5) is result
    worker.assert_called_once_with(payload,123.5)
    sleep.assert_not_called()


def test_loss_is_injected_only_after_committed_recorded_result(monkeypatch):
    class ResponseWithheld(Exception):pass
    payload={'synthetic':'not-a-database-request'}
    worker=Mock(return_value={'outcome':'observed','receipt':{'recorded':True,'version':1}})
    sleep=Mock(side_effect=ResponseWithheld)
    monkeypatch.setattr(review_entry,'_review_worker',worker)
    monkeypatch.setattr(gate.time,'sleep',sleep)
    with pytest.raises(ResponseWithheld):gate._lose_committed_response(payload,123.5)
    worker.assert_called_once_with(payload,123.5)
    sleep.assert_called_once_with(60)


@pytest.mark.parametrize('result',[
    {'outcome':'rejected','code':'daily_review_request_sealed','status':409},
    {'outcome':'observed','receipt':{'recorded':False,'stock_written':False}},
    {'outcome':'observed','receipt':{'recorded':True,'version':1}},
])
def test_any_delivered_response_fails_with_actual_result_and_never_retries(monkeypatch,result):
    payload={'synthetic':'not-a-database-request'}
    job=Mock(return_value=result)
    monkeypatch.setattr(gate,'run_owned_job',job)
    with pytest.raises(AssertionError) as error:gate._require_response_loss(payload)
    assert error.value.args==(('daily_review_response_loss_not_injected',result),)
    job.assert_called_once_with(gate._lose_committed_response,payload,maximum_seconds=8)


def test_unknown_proceeds_to_caller_exact_recovery_without_repeating_command(monkeypatch):
    payload={'synthetic':'not-a-database-request'}
    job=Mock(side_effect=ProcessOutcomeUnknown('use_exact_recovery'))
    monkeypatch.setattr(gate,'run_owned_job',job)
    assert gate._require_response_loss(payload) is None
    job.assert_called_once_with(gate._lose_committed_response,payload,maximum_seconds=8)


def test_cleanup_failure_is_not_downgraded_to_an_expected_unknown(monkeypatch):
    job=Mock(side_effect=ProcessEntryError('worker_cleanup_unconfirmed'))
    monkeypatch.setattr(gate,'run_owned_job',job)
    with pytest.raises(ProcessEntryError,match='worker_cleanup_unconfirmed'):
        gate._require_response_loss({'synthetic':'not-a-database-request'})
    assert job.call_count==1
