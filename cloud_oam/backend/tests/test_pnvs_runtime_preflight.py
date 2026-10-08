"""The live readiness probe never sends SMS or exposes SDK credentials."""
import importlib.util
import json
import logging
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('pnvs_runtime_probe', scripts/'pnvs_runtime_preflight.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The real function runs in a disposable subprocess, while these unit
    # tests restore the parent process logging threshold after direct calls.
    disabled = logging.root.manager.disable
    yield module
    logging.disable(disabled)


def configured(probe):
    return dict(OAM_SMS_LOGIN_ENABLED='true', OAM_SMS_PROVIDER='aliyun_pnvs',
        OAM_SMS_CREDENTIAL_MODE='default_chain', OAM_SMS_SIGN_NAME=probe.PILOT_SMS_SIGN_NAME,
        OAM_SMS_TEMPLATE_CODE=probe.PILOT_SMS_TEMPLATE_CODE,
        OAM_SMS_SCHEME_NAME=probe.PILOT_SMS_SCHEME_NAME)


@pytest.mark.parametrize('key', ['OAM_SMS_ACCESS_KEY_SECRET', 'ALIBABA_CLOUD_ACCESS_KEY_SECRET'])
def test_injected_key_blocks_lookup_without_disclosure(probe, key):
    environment = configured(probe) | {key: 'synthetic-never-print-this-secret'}
    def runner(*args, **kwargs):
        raise AssertionError('credential lookup must not be reached')
    result = probe.inspect_runtime(environment, resolve=True, runner=runner)
    assert result['identityResolution'] == 'configuration_blocked'
    assert 'synthetic-never-print' not in json.dumps(result)
    assert not result['releaseReady'] and not result['smsSent']


@pytest.mark.parametrize('provider,expected', [
    ('default/ecs_ram_role', 'dynamic_identity_resolved'),
    ('default/oidc_role_arn', 'dynamic_identity_resolved'),
    ('default/credential_uri', 'dynamic_identity_resolved'),
    ('default/env', 'unreviewed_identity_source'),
    ('default/profile/static_sts', 'unreviewed_identity_source')])
def test_only_reviewed_dynamic_source_is_reported(probe, provider, expected, capsys):
    def factory():
        print('synthetic-secret-from-sdk')
        return SimpleNamespace(get_credential=lambda: SimpleNamespace(provider_name=provider,
            access_key_id='synthetic-id', access_key_secret='synthetic-secret', security_token='synthetic-token'))
    assert probe.resolve_identity(factory) == expected
    assert capsys.readouterr().out == ''


def test_dynamic_provider_without_token_is_not_ready(probe):
    factory = lambda: SimpleNamespace(get_credential=lambda: SimpleNamespace(
        provider_name='default/ecs_ram_role', access_key_id='synthetic-id',
        access_key_secret='synthetic-secret', security_token=None))
    assert probe.resolve_identity(factory) == 'incomplete_identity'


def test_exception_and_worker_output_cannot_leak(probe, capsys):
    def factory():
        raise RuntimeError('synthetic-secret')
    assert probe.resolve_identity(factory) == 'identity_unavailable'
    assert capsys.readouterr().out == ''
    result = probe.inspect_runtime(configured(probe), resolve=True,
        runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout='synthetic-secret', stderr=''))
    assert result['identityResolution'] == 'identity_probe_failed'
    assert 'synthetic-secret' not in json.dumps(result)


def test_probe_timeout_is_unknown_not_success(probe):
    def runner(*a, **k):
        assert k['timeout'] == 12
        raise subprocess.TimeoutExpired('probe', 12, output='synthetic-secret')
    result = probe.inspect_runtime(configured(probe), resolve=True, runner=runner)
    assert result['identityResolution'] == 'identity_probe_timeout'
    assert not result['configurationAndIdentityResolved']


def test_resolved_identity_does_not_claim_sms_or_release(probe):
    result = probe.inspect_runtime(configured(probe), resolve=True,
        runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout='dynamic_identity_resolved\n'))
    assert result['configurationAndIdentityResolved']
    for key in ('providerPermissionsVerified', 'credentialRefreshVerified', 'smsSent',
                'pnvsRoundTripVerified', 'releaseReady'):
        assert result[key] is False
