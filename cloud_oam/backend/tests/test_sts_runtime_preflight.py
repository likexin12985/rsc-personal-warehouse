"""Pinned SDK composition and fixed read boundaries; every transport is mocked."""
import copy
import json
import logging
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import requests

from test_pnvs_oidc_deployment import candidate
import sts_runtime_preflight as probe


@pytest.fixture(autouse=True)
def restore_logging():
    previous = logging.root.manager.disable
    yield
    logging.disable(previous)


def environment():
    env = candidate()['services']['api']['environment']
    env['OAM_FILE_STORAGE_REGION'] = 'cn-hangzhou'
    return env


def body(purpose='oss'):
    # Official GetCallerIdentity RAM-role response uses assumed-role, not role.
    # Literal fixtures are independent of the implementation's ARN derivation.
    arns = {'oss': 'acs:ram::1234567890123456:assumed-role/synthetic-files/rsc-pilot-files',
            'pnvs': 'acs:ram::1234567890123456:assumed-role/synthetic-pnvs/synthetic-pnvs'}
    return {'statusCode': 200, 'body': {'AccountId': '1234567890123456',
        'Arn': arns[purpose], 'IdentityType': 'AssumedRoleUser'}}


def inspect(response=None, purpose='oss', **kwargs):
    return probe.inspect_identity(environment(), purpose, resolver=lambda *_: {'synthetic': 'DO_NOT_OUTPUT'},
        caller=lambda _: body(purpose) if response is None else response,
        version_reader=lambda name: probe.SDK_VERSIONS[name], **kwargs)


@pytest.mark.parametrize('purpose', ['oss', 'pnvs'])
def test_exact_identity_is_verified_without_provider_values(purpose):
    result = inspect(purpose=purpose)
    assert set(result.values()) == {'passed'}
    assert 'DO_NOT_OUTPUT' not in json.dumps(result)


@pytest.mark.parametrize('field,value,check', [('AccountId', '0000000000000000', 'expected_account'),
    ('Arn', 'acs:ram::1234567890123456:role/other/session', 'expected_role_session'),
    ('IdentityType', 'RAMUser', 'assumed_role_identity')])
def test_complete_wrong_identity_fails(field, value, check):
    response = body(); response['body'][field] = value
    assert inspect(response)[check] == 'failed'


@pytest.mark.parametrize('arn', [
    'acs:ram::1234567890123456:role/synthetic-files/rsc-pilot-files',
    'acs:ram::0000000000000000:assumed-role/synthetic-files/rsc-pilot-files',
    'acs:ram::1234567890123456:assumed-role/other/rsc-pilot-files',
    'acs:ram::1234567890123456:assumed-role/synthetic-files/other'])
def test_official_role_response_rejects_wrong_namespace_account_role_or_session(arn):
    response = body(); response['body']['Arn'] = arn
    result = inspect(response)
    assert result['sts_response'] == result['expected_account'] == 'passed'
    assert result['expected_role_session'] == 'failed'


@pytest.mark.parametrize('response', [{'statusCode': 403, 'body': 'DO_NOT_OUTPUT'},
    {'statusCode': True, 'body': {}}, {'statusCode': 200, 'body': []},
    {'statusCode': 200, 'body': {'AccountId': 'DO_NOT_OUTPUT'}}, []])
def test_non_success_or_incomplete_identity_remains_unknown(response):
    result = inspect(response)
    assert result['sts_response'] == result['expected_account'] == 'unknown'
    assert 'DO_NOT_OUTPUT' not in json.dumps(result)


@pytest.mark.parametrize('key,value', [('OAM_FILE_STORAGE_CREDENTIAL_MODE', 'environment'),
    ('OAM_FILE_STORAGE_REGION', 'cn-shanghai'), ('RSC_OIDC_EXPECTED_ACCOUNT_ID', 'root'),
    ('OAM_FILE_STORAGE_OIDC_ROLE_ARN', 'acs:ram::0000000000000000:role/files'),
    ('OAM_FILE_STORAGE_OIDC_TOKEN_FILE', '/run/../tmp/token'),
    ('ALIBABA_CLOUD_ACCESS_KEY_SECRET', 'DO_NOT_OUTPUT')])
def test_bad_config_does_not_construct_credentials(key, value):
    env = environment(); env[key] = value
    result = probe.inspect_identity(env, 'oss', resolver=lambda *_: pytest.fail('credential lookup'))
    assert result['configuration'] == 'failed' and result['dynamic_provider'] == 'unknown'
    assert 'DO_NOT_OUTPUT' not in json.dumps(result)


def test_sdk_mismatch_does_not_construct_credentials():
    result = probe.inspect_identity(environment(), 'oss', version_reader=lambda _: 'wrong',
        resolver=lambda *_: pytest.fail('credential lookup'))
    assert result['sdk_versions'] == 'failed'


def test_sdk_output_and_exception_never_escape(capsys):
    def fail(*_):
        print('DO_NOT_OUTPUT'); print('DO_NOT_OUTPUT', file=sys.stderr)
        raise RuntimeError('DO_NOT_OUTPUT')
    result = probe.inspect_identity(environment(), 'oss', resolver=fail,
        version_reader=lambda name: probe.SDK_VERSIONS[name])
    assert result['dynamic_provider'] == 'unknown'
    assert 'DO_NOT_OUTPUT' not in str(result) + str(capsys.readouterr())


def test_parent_rebuilds_only_allowlisted_states_and_never_retries():
    calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict.fromkeys(probe.CHECKS, 'passed')))
    result = probe.inspect_runtime(environment(), 'oss', runner=runner)
    assert result['identityVerified'] and len(calls) == 1
    assert calls[0][1]['timeout'] == 20
    assert all(result[k] is False for k in ('credentialRefreshVerified', 'objectPermissionsVerified',
        'issuerClaimsVerified', 'projectionVerified', 'smsSent', 'pnvsRoundTripVerified', 'releaseReady', 'autoRetry'))


@pytest.mark.parametrize('output,code', [('DO_NOT_OUTPUT', 0), ('{}', 0),
    (json.dumps({**dict.fromkeys(probe.CHECKS, 'passed'), 'secret': 'DO_NOT_OUTPUT'}), 0),
    (json.dumps(dict.fromkeys(probe.CHECKS, 'DO_NOT_OUTPUT')), 0), ('', 1)])
def test_parent_rejects_worker_output_without_forwarding(output, code):
    result = probe.inspect_runtime(environment(), 'oss', runner=lambda *a, **k: SimpleNamespace(returncode=code, stdout=output))
    assert result['probeState'] == 'unknown' and not result['identityVerified']
    assert 'DO_NOT_OUTPUT' not in json.dumps(result)


def test_parent_timeout_is_unknown_without_replay():
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1); raise subprocess.TimeoutExpired('DO_NOT_OUTPUT', 20)
    result = probe.inspect_runtime(environment(), 'oss', runner=timeout)
    assert result['probeState'] == 'unknown' and calls == [1]
    assert result['unknownRequiresExactReadback'] and 'DO_NOT_OUTPUT' not in json.dumps(result)


def prepared(action='GetCallerIdentity', endpoint=probe.ENDPOINT, method='POST'):
    return requests.Request(method, 'https://' + endpoint + '/',
        headers={'x-acs-action': action, 'x-acs-version': '2015-04-01'}).prepare()


def test_guard_disables_redirects_and_rejects_a_second_rpc(monkeypatch):
    sent = []
    monkeypatch.setattr(requests.Session, 'send', lambda self, request, **kw: sent.append(kw) or 'response')
    with probe.bounded_sts_transport():
        session = requests.Session()
        assert session.send(prepared(), verify=True, allow_redirects=True) == 'response'
        with pytest.raises(ValueError): session.send(prepared(), verify=True)
    assert len(sent) == 1 and sent[0]['allow_redirects'] is False


@pytest.mark.parametrize('action,endpoint,method,verify', [
    ('SendSmsVerifyCode', probe.ENDPOINT, 'POST', True),
    ('GetCallerIdentity', 'unreviewed.invalid', 'POST', True),
    ('GetCallerIdentity', probe.ENDPOINT, 'GET', True),
    ('GetCallerIdentity', probe.ENDPOINT, 'POST', False)])
def test_transport_cannot_leave_the_fixed_read_contract(monkeypatch, action, endpoint, method, verify):
    monkeypatch.setattr(requests.Session, 'send', lambda *a, **k: pytest.fail('unapproved transport'))
    with probe.bounded_sts_transport(), pytest.raises(ValueError):
        requests.Session().send(prepared(action, endpoint, method), verify=verify)


def test_real_pinned_tea_sdk_uses_one_signed_fixed_request_without_retry(monkeypatch):
    calls = []
    def response(session, request, **kwargs):
        calls.append((request.method, request.url, dict(request.headers), kwargs))
        output = requests.Response(); output.status_code = 200
        output._content = json.dumps(body()['body']).encode(); output.headers = {'content-type': 'application/json'}
        return output
    monkeypatch.setattr(requests.Session, 'send', response)
    with probe.bounded_sts_transport():
        result = probe.caller_identity(dict(access_key_id='synthetic-ak', access_key_secret='synthetic-sk',
                                            security_token='synthetic-sts'))
    assert result['statusCode'] == 200 and len(calls) == 1
    method, url, headers, options = calls[0]
    assert method == 'POST' and url == 'https://' + probe.ENDPOINT + '/'
    assert headers['x-acs-action'] == 'GetCallerIdentity' and headers['x-acs-version'] == '2015-04-01'
    assert options['allow_redirects'] is False and options['verify'] is True
    assert 'authorization' in {key.lower() for key in headers}


def test_real_pinned_tea_sdk_failure_is_attempted_once(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1); raise IOError('synthetic transport failure')
    monkeypatch.setattr(requests.Session, 'send', fail)
    with probe.bounded_sts_transport(), pytest.raises(Exception):
        probe.caller_identity(dict(access_key_id='synthetic-ak', access_key_secret='synthetic-sk', security_token='synthetic-sts'))
    assert calls == [1]


@pytest.mark.parametrize('query', ['?Action=SendSmsVerifyCode', '?Version=unreviewed'])
def test_conflicting_header_query_actions_are_rejected(monkeypatch, query):
    monkeypatch.setattr(requests.Session, 'send', lambda *a, **k: pytest.fail('unapproved transport'))
    request = prepared(); request.url += query
    with probe.bounded_sts_transport(), pytest.raises(ValueError):
        requests.Session().send(request, verify=True)


def test_oss_resolution_uses_exact_production_identity_composition(monkeypatch):
    from app import oss_runtime_credentials
    identity = probe.coordinates(environment(), 'oss'); received = []
    def factory(value):
        received.append(value)
        return SimpleNamespace(get_credentials=lambda: SimpleNamespace(access_key_id='synthetic-ak',
            access_key_secret='synthetic-sk', security_token='synthetic-token'))
    monkeypatch.setattr(oss_runtime_credentials, 'OssOidcCredentialsProvider', factory)
    result = probe.resolve_credentials('oss', identity)
    assert result['security_token'] == 'synthetic-token' and len(received) == 1
    assert (received[0].role_arn, received[0].provider_arn, received[0].token_file,
            received[0].region, received[0].session_name) == (
        identity['role'], identity['provider'], identity['token'], probe.REGION, identity['session'])


@pytest.mark.parametrize('provider,token,accepted', [('default/oidc_role_arn', 'synthetic-token', True),
    ('default/env', 'synthetic-token', False), ('default/oidc_role_arn', '', False)])
def test_pnvs_resolution_uses_actual_default_provider_name(monkeypatch, provider, token, accepted):
    from alibabacloud_credentials import client
    monkeypatch.setattr(client, 'Client', lambda: SimpleNamespace(get_credential=lambda:
        SimpleNamespace(provider_name=provider, access_key_id='synthetic-ak',
                        access_key_secret='synthetic-sk', security_token=token)))
    identity = probe.coordinates(environment(), 'pnvs')
    if accepted:
        assert probe.resolve_credentials('pnvs', identity)['security_token'] == token
    else:
        with pytest.raises(ValueError): probe.resolve_credentials('pnvs', identity)


@pytest.mark.parametrize('arguments', [[], ['--help']])
def test_cold_help_does_not_read_identity_or_start_worker(monkeypatch, capsys, arguments):
    monkeypatch.setattr(sys, 'argv', ['sts_runtime_preflight.py', *arguments])
    monkeypatch.setattr(probe, 'coordinates', lambda *_: pytest.fail('identity read'))
    monkeypatch.setattr(probe.subprocess, 'run', lambda *_: pytest.fail('worker launch'))
    if arguments:
        with pytest.raises(SystemExit) as error: probe.main()
        assert error.value.code == 0
    else: assert probe.main() == 0
    assert 'GetCallerIdentity' in capsys.readouterr().out
