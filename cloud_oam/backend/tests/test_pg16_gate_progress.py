"""Phase reporting must not alter business checks or disclose their inputs."""
import json
import pytest
from pg16_gate_progress import run_gate_phase


def events(capsys):
    output = capsys.readouterr()
    assert output.err == ''
    return [json.loads(line) for line in output.out.splitlines()]


def test_result_and_arguments_are_forwarded_exactly_once_without_logging(capsys):
    secret = object()
    result = object()
    calls = []
    def operation(*args, **kwargs):
        calls.append((args, kwargs))
        return result
    assert run_gate_phase('runtime_authority', operation, secret, password='synthetic-private-value') is result
    assert calls == [((secret,), {'password':'synthetic-private-value'})]
    rows = events(capsys)
    assert rows[0] == {'pg16_phase':'runtime_authority','state':'started'}
    assert rows[1]['state'] == 'passed' and rows[1]['elapsed_seconds'] >= 0
    assert set(rows[1]) == {'pg16_phase','state','elapsed_seconds'}
    assert 'synthetic-private-value' not in json.dumps(rows)


@pytest.mark.parametrize('exception', [AssertionError('synthetic-private-value'), RuntimeError('synthetic-private-value'), KeyboardInterrupt('synthetic-private-value')])
def test_original_failure_is_preserved_and_only_failure_state_is_logged(capsys, exception):
    def operation():
        raise exception
    with pytest.raises(type(exception)) as observed:
        run_gate_phase('migration_check', operation)
    assert observed.value is exception
    rows = events(capsys)
    assert [row['state'] for row in rows] == ['started','failed']
    assert 'synthetic-private-value' not in json.dumps(rows)


@pytest.mark.parametrize('label', ['bad\nlabel', 'https://example.test', '', 'x'*129, None])
def test_invalid_labels_never_run_or_print(label,capsys):
    called=[]
    with pytest.raises(ValueError):
        run_gate_phase(label,lambda:called.append(True))
    assert called==[] and events(capsys)==[]


def test_nested_phases_keep_result_and_completion_order(capsys):
    assert run_gate_phase('outer',lambda:run_gate_phase('inner',lambda:42))==42
    assert [(r['pg16_phase'],r['state']) for r in events(capsys)]==[
        ('outer','started'),('inner','started'),('inner','passed'),('outer','passed')]
