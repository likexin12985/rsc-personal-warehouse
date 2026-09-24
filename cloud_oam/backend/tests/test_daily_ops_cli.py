"""Boundary checks for the one-shot daily mapping command entry."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import date
from uuid import uuid4

import pytest

from app.daily_reconciliation import ops_cli, mapping_entry, process_entry


def command():
    return dict(action='revoke', binding_id=str(uuid4()), catalog_id=str(uuid4()),
        revoked_grant_id=str(uuid4()), expected_subject_sha256='a'*64,
        evidence_file_id=str(uuid4()), evidence_sha256='b'*64,
        reason='Synthetic exact revocation', idempotency_key='synthetic-key-12345',
        request_id='synthetic-request-12345')


def cutoff_command():
    return dict(business_date=date.today().isoformat(),source_publication_id=str(uuid4()),
        source_publication_sha256='a'*64,mapping_decision_id=str(uuid4()),
        mapping_decision_sha256='b'*64,idempotency_key='synthetic-cutoff-key-12345',
        request_id='synthetic-cutoff-request-12345')


def document(tmp_path, operation='mapping-preview'):
    value = dict(command=command(), expected_authorization_version=1)
    if operation != 'mapping-preview':
        value['review_sha256'] = 'c'*64
    path = tmp_path/'command.json'
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(extra='injected'),
    lambda v: v.update(expected_authorization_version=True),
    lambda v: v.update(expected_authorization_version=0),
    lambda v: v.update(access_token='private-token'),
    lambda v: v['command'].update(actor_user_id=str(uuid4())),
    lambda v: v['command'].update(binding_id=str(uuid4())+'; DROP TABLE audit_events'),
])
def test_invalid_envelopes_are_rejected_before_credentials_or_connection(tmp_path, mutation, monkeypatch, capsys):
    path, value = document(tmp_path)
    mutation(value)
    path.write_text(json.dumps(value))
    monkeypatch.setattr(ops_cli, '_credential', lambda fd: pytest.fail('credential read'))
    assert ops_cli.main(['mapping-preview', '--command-file', str(path)]) == 2
    output = capsys.readouterr()
    assert not output.out and 'private-token' not in output.err


@pytest.mark.parametrize('raw', [
    '{"command":{},"command":{},"expected_authorization_version":1}',
    '{"command":{},"expected_authorization_version":NaN}',
    '{"command":{},"expected_authorization_version":Infinity}',
    '{"command":{},"expected_authorization_version":1,"review_sha256":"c"}',
])
def test_json_duplicates_nonfinite_and_preview_digest_are_rejected(tmp_path, raw):
    path = tmp_path/'command.json'
    path.write_text(raw)
    with pytest.raises(Exception):
        ops_cli._document(path, 'mapping-preview')


def test_status_needs_exact_digest_and_command_file_is_bounded(tmp_path):
    path, value = document(tmp_path, 'mapping-status')
    assert ops_cli._document(path, 'mapping-status')[2] == 'c'*64
    value['review_sha256'] = 'bad'
    path.write_text(json.dumps(value))
    with pytest.raises(Exception):
        ops_cli._document(path, 'mapping-status')
    path.write_bytes(b' '* (ops_cli.MAX_COMMAND_BYTES + 1))
    with pytest.raises(ValueError, match='too large'):
        ops_cli._document(path, 'mapping-status')


def test_private_conninfo_file_and_target_config(tmp_path, monkeypatch):
    path = tmp_path/'owner.conninfo'
    path.write_text('host=127.0.0.1 port=5432 dbname=synthetic user=star_oam_migrator password=private')
    path.chmod(0o600)
    monkeypatch.setenv('OAM_DAILY_RECONCILIATION_OWNER_CONNINFO_FILE', str(path))
    monkeypatch.setenv('OAM_DAILY_RECONCILIATION_DATABASE_NAME', 'synthetic')
    assert ops_cli._configuration()[1:] == ('synthetic', 10)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        ops_cli._configuration()
    path.chmod(0o600)
    monkeypatch.setenv('OAM_DAILY_RECONCILIATION_MAXIMUM_SECONDS', '61')
    with pytest.raises(ValueError):
        ops_cli._configuration()


def test_token_descriptor_reads_once_without_waiting_for_pipe_close():
    reader,writer=os.pipe()
    try:
        os.write(writer,b'synthetic.jwt.token')
        assert ops_cli._credential(reader)=='synthetic.jwt.token'
    finally:
        os.close(reader);os.close(writer)


def test_help_and_invalid_arguments_work_without_application_settings():
    environment={key:value for key,value in os.environ.items() if not key.startswith(('OAM_','PG'))}
    environment['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
    base=[sys.executable,'-m','app.daily_reconciliation.ops_cli']
    help_result=subprocess.run([*base,'--help'],env=environment,capture_output=True,text=True,timeout=10)
    assert help_result.returncode==0 and 'cutoff-preview' in help_result.stdout
    bad=subprocess.run([*base,'--token','private-synthetic-token'],env=environment,
        capture_output=True,text=True,timeout=10)
    assert bad.returncode==2 and bad.stderr.strip()=='daily_ops_invalid_arguments'
    assert 'private-synthetic-token' not in bad.stdout+bad.stderr


def test_mapping_apply_unknown_is_redacted_and_never_retried(tmp_path, monkeypatch, capsys):
    path, _ = document(tmp_path, 'mapping-apply')
    token = 'private-synthetic-token'
    monkeypatch.setattr(ops_cli, '_credential', lambda fd: token)
    monkeypatch.setattr(ops_cli, '_configuration', lambda: ('private-dsn', 'synthetic', 10))
    calls = []
    def lost(**kwargs):
        calls.append(kwargs)
        raise RuntimeError(token + ' private-dsn unobserved COMMIT')
    monkeypatch.setattr(mapping_entry, 'execute', lost)
    assert ops_cli.main(['mapping-apply', '--command-file', str(path)]) == 3
    output = capsys.readouterr()
    assert len(calls) == 1 and not output.out
    assert token not in output.err and 'private-dsn' not in output.err
    assert json.loads(output.err)['next_action'] == 'mapping-status-with-original-command'


def test_mapping_status_missing_is_not_a_commit_or_retry_signal(tmp_path, monkeypatch, capsys):
    path, _ = document(tmp_path, 'mapping-status')
    monkeypatch.setattr(ops_cli, '_credential', lambda fd: 'synthetic-token')
    monkeypatch.setattr(ops_cli, '_configuration', lambda: ('private-dsn', 'synthetic', 10))
    monkeypatch.setattr(mapping_entry, 'execute', lambda **kwargs: dict(operation='status', recorded=False,
        projection_published=False, start_ready=False))
    assert ops_cli.main(['mapping-status', '--command-file', str(path)]) == 4
    assert json.loads(capsys.readouterr().out)['recorded'] is False


def test_worker_dispatch_is_fixed_and_retains_original_coordinates(monkeypatch):
    observed = {}
    def run(worker, payload, *, maximum_seconds):
        observed.update(worker=worker, payload=payload, duration=maximum_seconds)
        return dict(recorded=True)
    monkeypatch.setattr(mapping_entry, 'run_owned_job', run)
    result = mapping_entry.execute(conninfo='private-dsn', database_name='synthetic',
        command=command(), access_token='synthetic-token', expected_authorization_version=7,
        operation='status', review_sha256='c'*64, maximum_seconds=9)
    assert result['recorded'] and observed['worker'] is mapping_entry._worker
    assert observed['payload']['review_sha256'] == 'c'*64
    assert observed['payload']['expected_authorization_version'] == 7
    assert observed['duration'] == 9


def test_cutoff_file_rejects_client_supplied_fact_and_bad_date(tmp_path):
    path = tmp_path/'cutoff.json'
    value = dict(command=cutoff_command(),expected_authorization_version=1)
    path.write_text(json.dumps(value))
    assert ops_cli._document(path,'cutoff-preview')[1] == 1
    value['command']['local_ledger_cursor'] = 5
    path.write_text(json.dumps(value))
    with pytest.raises(Exception):
        ops_cli._document(path,'cutoff-preview')
    del value['command']['local_ledger_cursor']
    value['command']['business_date'] = 'not-a-date'
    path.write_text(json.dumps(value))
    with pytest.raises(Exception):
        ops_cli._document(path,'cutoff-preview')


def test_cutoff_connections_are_three_distinct_fixed_roles_and_one_target(tmp_path,monkeypatch):
    monkeypatch.setenv('OAM_DAILY_RECONCILIATION_DATABASE_NAME','synthetic')
    for name,role in (('OWNER','star_oam_migrator'),('SOURCE','rsc_control_capture'),
                      ('LEDGER','rsc_reconciliation_capture')):
        path=tmp_path/(name+'.conninfo')
        path.write_text(f'host=127.0.0.1 port=5432 dbname=synthetic user={role} password=private')
        path.chmod(0o600)
        monkeypatch.setenv('OAM_DAILY_RECONCILIATION_'+name+'_CONNINFO_FILE',str(path))
    config,target,seconds=ops_cli._cutoff_configuration()
    assert target=='synthetic' and seconds==10 and config.source_conninfo!=config.owner_conninfo
    path=tmp_path/'LEDGER.conninfo'
    path.write_text('host=127.0.0.2 port=5432 dbname=synthetic user=rsc_reconciliation_capture password=private')
    with pytest.raises(ValueError,match='targets mismatch'):
        ops_cli._cutoff_configuration()
    path.write_text('host=127.0.0.1 port=5432 dbname=synthetic user=star_oam_migrator password=private')
    with pytest.raises(ValueError,match='configuration invalid'):
        ops_cli._cutoff_configuration()


def test_cutoff_capture_unknown_is_redacted_and_never_retried(tmp_path,monkeypatch,capsys):
    path=tmp_path/'cutoff.json'
    path.write_text(json.dumps(dict(command=cutoff_command(),expected_authorization_version=1)))
    monkeypatch.setattr(ops_cli,'_credential',lambda fd:'private-synthetic-token')
    monkeypatch.setattr(ops_cli,'_cutoff_configuration',lambda:('private-database','synthetic',10))
    calls=[]
    def lost(**kwargs):
        calls.append(kwargs)
        raise RuntimeError('private-synthetic-token private-database lost COMMIT')
    monkeypatch.setattr(process_entry,'execute',lost)
    assert ops_cli.main(['cutoff-capture','--command-file',str(path)])==3
    output=capsys.readouterr()
    assert len(calls)==1 and not output.out and 'private-' not in output.err
    assert json.loads(output.err)['next_action']=='cutoff-recover-with-original-command'


def test_cutoff_recover_missing_is_not_capture_success(tmp_path,monkeypatch,capsys):
    path=tmp_path/'cutoff.json'
    path.write_text(json.dumps(dict(command=cutoff_command(),expected_authorization_version=1)))
    monkeypatch.setattr(ops_cli,'_credential',lambda fd:'synthetic-token')
    monkeypatch.setattr(ops_cli,'_cutoff_configuration',lambda:('private-database','synthetic',10))
    monkeypatch.setattr(process_entry,'execute',lambda **kwargs:
        dict(outcome='committed',receipt=dict(recorded=False,stock_written=False,
            daily_reconciliation_approved=False)))
    assert ops_cli.main(['cutoff-recover','--command-file',str(path)])==4
    assert json.loads(capsys.readouterr().out)['receipt']['recorded'] is False
