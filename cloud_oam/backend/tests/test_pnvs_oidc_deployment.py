"""Deployment-only PNVS wiring; locked SDK, synthetic identity, no SMS/cloud."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from scripts import pilot_preflight as preflight, pilot_release as release
import pnvs_runtime_preflight as probe


def candidate():
    env = dict(
        OAM_ENVIRONMENT='production', OAM_SMS_LOGIN_ENABLED='true',
        OAM_SMS_PROVIDER='aliyun_pnvs', OAM_SMS_CREDENTIAL_MODE='default_chain',
        OAM_SMS_SIGN_NAME=preflight.PILOT_SMS_SIGN_NAME,
        OAM_SMS_TEMPLATE_CODE=preflight.PILOT_SMS_TEMPLATE_CODE,
        OAM_SMS_SCHEME_NAME=preflight.PILOT_SMS_SCHEME_NAME,
        OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER='openbao_transit_v1',
        OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER='openbao_transit_v1',
        OAM_FILE_STORAGE_CREDENTIAL_MODE='oidc_role_arn',
        OAM_FILE_STORAGE_OIDC_ROLE_ARN='acs:ram::1234567890123456:role/synthetic-files',
        OAM_FILE_STORAGE_OIDC_PROVIDER_ARN='acs:ram::1234567890123456:oidc-provider/synthetic',
        OAM_FILE_STORAGE_OIDC_TOKEN_FILE='/run/synthetic/files/oidc.jwt',
        RSC_OSS_OIDC_PROJECTOR_UID='41003', RSC_OSS_OIDC_SHARED_GID='41004',
        RSC_OSS_OIDC_SUBJECT='00000000-0000-4000-8000-000000000001',
        RSC_OSS_OIDC_AUDIENCE='synthetic-files',
        RSC_OIDC_EXPECTED_ACCOUNT_ID='1234567890123456',
        RSC_OIDC_ISSUER_URL='https://identity.invalid/v1/identity/oidc',
        RSC_PNVS_OIDC_ROLE_ARN='acs:ram::1234567890123456:role/synthetic-pnvs',
        RSC_PNVS_OIDC_PROVIDER_ARN='acs:ram::1234567890123456:oidc-provider/synthetic',
        RSC_PNVS_OIDC_TOKEN_FILE='/run/synthetic/pnvs/oidc.jwt',
        RSC_PNVS_OIDC_SESSION_NAME='synthetic-pnvs',
        RSC_PNVS_OIDC_PROJECTOR_UID='41005', RSC_PNVS_OIDC_SHARED_GID='41006',
        RSC_PNVS_OIDC_SUBJECT='00000000-0000-4000-8000-000000000002',
        RSC_PNVS_OIDC_AUDIENCE='synthetic-pnvs',
        ALIBABA_CLOUD_CLI_PROFILE_DISABLED='true', ALIBABA_CLOUD_CREDENTIALS_FILE='/dev/null',
        ALIBABA_CLOUD_ECS_METADATA_DISABLED='true', ALIBABA_CLOUD_VPC_ENDPOINT_ENABLED='false',
        ALIBABA_CLOUD_STS_REGION='cn-hangzhou')
    for left, right in preflight.PNVS_OIDC_COORDINATES:
        env[right] = env[left]
    api = dict(environment=env, user='41001:41001', group_add=['41004', '41006'],
        cap_drop=['ALL'], cap_add=[], privileged=False, security_opt=['no-new-privileges:true'], volumes=[
        dict(type='bind', source='/run/synthetic/' + purpose, target='/run/synthetic/' + purpose,
             read_only=True, bind=dict(create_host_path=False)) for purpose in ('files', 'pnvs')])
    return dict(services=dict(api=api, **{'kms-pin-gate': dict(environment={})}))


def test_same_ram_provider_binds_separate_roles_entities_audiences_and_live_directories(monkeypatch):
    value = candidate()
    # No runtime access is needed to validate these public declarations.
    monkeypatch.setattr(Path, 'read_bytes', lambda *_: pytest.fail('unexpected file read'))
    monkeypatch.setattr(Path, 'read_text', lambda *_: pytest.fail('unexpected file read'))
    assert all(preflight.pnvs_oidc_configuration_checks(value).values())
    assert all(probe.configuration(value['services']['api']['environment']).values())
    specs = release.live_bind_specs(value)
    assert {row['kind'] for row in specs.values()} == {'oss_oidc', 'pnvs_oidc'}
    assert specs['api', '/run/synthetic/pnvs']['owner'] == 41005


@pytest.mark.parametrize('key', preflight.PNVS_STATIC_FIELDS)
def test_every_static_namespace_is_rejected_without_lookup_or_disclosure(key):
    env = candidate()['services']['api']['environment']
    env[key] = 'synthetic-never-output-this'
    result = probe.inspect_runtime(env, resolve=True,
        runner=lambda *a, **k: pytest.fail('credential worker must not start'))
    assert result['identityResolution'] == 'configuration_blocked'
    assert 'synthetic-never-output' not in json.dumps(result)


@pytest.mark.parametrize('mutation', [
    'sdk-coordinate', 'role-account', 'provider-account', 'expected-account',
    'shared-role', 'shared-subject', 'shared-audience', 'shared-target',
    'shared-source', 'metadata', 'uri', 'cli-profile', 'legacy-profile',
    'kms-provider', 'invalid-issuer', 'partial', 'api-owner', 'missing-group',
    'rw', 'create-source', 'single-file', 'dev-null', 'devices', 'second-service',
])
def test_unsafe_or_partial_declarations_fail_closed(mutation):
    value = candidate(); api = value['services']['api']; env = api['environment']
    if mutation == 'sdk-coordinate': env['ALIBABA_CLOUD_ROLE_ARN'] += '-drift'
    elif mutation == 'role-account':
        env['RSC_PNVS_OIDC_ROLE_ARN'] = env['ALIBABA_CLOUD_ROLE_ARN'] = 'acs:ram::0000000000000000:role/synthetic'
    elif mutation == 'provider-account':
        env['RSC_PNVS_OIDC_PROVIDER_ARN'] = env['ALIBABA_CLOUD_OIDC_PROVIDER_ARN'] = 'acs:ram::0000000000000000:oidc-provider/synthetic'
    elif mutation == 'expected-account': env['RSC_OIDC_EXPECTED_ACCOUNT_ID'] = '0000000000000000'
    elif mutation == 'shared-role': env['RSC_PNVS_OIDC_ROLE_ARN'] = env['ALIBABA_CLOUD_ROLE_ARN'] = env['OAM_FILE_STORAGE_OIDC_ROLE_ARN']
    elif mutation == 'shared-subject': env['RSC_PNVS_OIDC_SUBJECT'] = env['RSC_OSS_OIDC_SUBJECT']
    elif mutation == 'shared-audience': env['RSC_PNVS_OIDC_AUDIENCE'] = env['RSC_OSS_OIDC_AUDIENCE']
    elif mutation == 'shared-target': env['RSC_PNVS_OIDC_TOKEN_FILE'] = env['ALIBABA_CLOUD_OIDC_TOKEN_FILE'] = env['OAM_FILE_STORAGE_OIDC_TOKEN_FILE']
    elif mutation == 'shared-source': api['volumes'][1]['source'] = api['volumes'][0]['source']
    elif mutation == 'metadata': env['ALIBABA_CLOUD_ECS_METADATA_DISABLED'] = 'false'
    elif mutation == 'uri': env['ALIBABA_CLOUD_CREDENTIALS_URI'] = 'http://127.0.0.1/synthetic'
    elif mutation == 'cli-profile': env['ALIBABA_CLOUD_CLI_PROFILE_DISABLED'] = 'false'
    elif mutation == 'legacy-profile': env['ALIBABA_CLOUD_CREDENTIALS_FILE'] = '/home/api/.alibabacloud/credentials.ini'
    elif mutation == 'kms-provider': env['OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER'] = 'aliyun_kms'
    elif mutation == 'invalid-issuer': env['RSC_OIDC_ISSUER_URL'] = 'http://identity.invalid/v1/identity/oidc'
    elif mutation == 'partial': del env['RSC_PNVS_OIDC_AUDIENCE']
    elif mutation == 'api-owner': env['RSC_PNVS_OIDC_PROJECTOR_UID'] = '41001'
    elif mutation == 'missing-group': api['group_add'] = ['41004']
    elif mutation == 'rw': api['volumes'][1]['read_only'] = False
    elif mutation == 'create-source': api['volumes'][1]['bind']['create_host_path'] = True
    elif mutation == 'single-file': api['volumes'][1]['target'] += '/oidc.jwt'
    elif mutation == 'dev-null': api['volumes'].append(dict(type='bind', source='/synthetic/config', target='/dev/null', read_only=True))
    elif mutation == 'devices': api['devices'] = ['/dev/synthetic:/dev/null']
    else: value['services']['kms-pin-gate']['environment'].update(env)
    assert not all(preflight.pnvs_oidc_configuration_checks(value).values())
    with pytest.raises((release.Refused, ValueError)):
        release.live_bind_specs(value)


@pytest.mark.parametrize('provider,expected', [
    ('default/oidc_role_arn', 'dynamic_identity_resolved'),
    ('default/profile/oidc_role_arn', 'unreviewed_identity_source'),
    ('default/ecs_ram_role', 'unreviewed_identity_source'),
    ('default/credential_uri', 'unreviewed_identity_source'),
])
def test_selected_pnvs_oidc_requires_exact_sdk_provider(provider, expected):
    import logging
    disabled = logging.root.manager.disable
    try:
        factory = lambda: SimpleNamespace(get_credential=lambda: SimpleNamespace(
            provider_name=provider, access_key_id='synthetic', access_key_secret='synthetic', security_token='synthetic'))
        assert probe.resolve_identity(factory, expected_provider='default/oidc_role_arn') == expected
    finally:
        logging.disable(disabled)


@pytest.mark.parametrize('failure', [False, True])
def test_locked_sdk_uses_exact_pnvs_coordinates_and_cannot_fallback(tmp_path, failure):
    # A fresh process is required: this SDK captures its environment at import.
    env = candidate()['services']['api']['environment']
    program = r'''
import io,json,os,socket,sys
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch
from alibabacloud_credentials.client import Client
from alibabacloud_credentials.provider import oidc,profile,cli_profile
sys.path.insert(0,sys.argv[2])
import pnvs_runtime_preflight as runtime_probe
failure = sys.argv[1] == 'True'
seen=[]; profiles=[]
original=profile._load_ini
def empty_profile(path):
 assert path == '/dev/null'
 profiles.append(path)
 return original(path)
def exchange(request, options):
 assert request.query['Action']=='AssumeRoleWithOIDC'
 assert request.query['RoleArn']==os.environ['RSC_PNVS_OIDC_ROLE_ARN']
 assert request.query['OIDCProviderArn']==os.environ['RSC_PNVS_OIDC_PROVIDER_ARN']
 assert request.query['RoleSessionName']==os.environ['RSC_PNVS_OIDC_SESSION_NAME']
 assert request.query['OIDCToken']=='synthetic-jwt'
 assert request.headers['host']=='sts.cn-hangzhou.aliyuncs.com'
 seen.append('exchange')
 if failure: raise RuntimeError('synthetic-sensitive-sdk-error')
 payload={'Credentials':{'AccessKeyId':'synthetic-id','AccessKeySecret':'synthetic-secret',
  'SecurityToken':'synthetic-sts','Expiration':(datetime.now(timezone.utc)+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}}
 return SimpleNamespace(status_code=200,body=json.dumps(payload).encode())
def token(path):
 assert path==os.environ['RSC_PNVS_OIDC_TOKEN_FILE']
 return 'synthetic-jwt'
def forbidden(*a,**k):raise AssertionError('unexpected network or CLI profile')
with patch.object(oidc,'_get_token',token),patch.object(oidc.TeaCore,'do_action',exchange), \
 patch.object(profile,'_load_ini',empty_profile),patch.object(cli_profile,'_load_config',forbidden), \
 patch.object(socket.socket,'connect',forbidden),patch.object(socket,'create_connection',forbidden):
 class CheckedClient:
  def get_credential(self):
   result=Client().get_credential()
   assert result.provider_name=='default/oidc_role_arn'
   assert not hasattr(result,'expiration')
   return result
 state=runtime_probe.resolve_identity(CheckedClient,expected_provider='default/oidc_role_arn')
 if failure: assert state=='identity_unavailable' and seen==['exchange'] and profiles==['/dev/null']
 else: assert state=='dynamic_identity_resolved' and seen==['exchange'] and profiles==[]
print('synthetic_sdk_contract_pass')
'''
    result = subprocess.run([sys.executable, '-c', program, str(failure), str(ROOT/'scripts')], env=env,
        cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'synthetic_sdk_contract_pass\n' and not result.stderr


def test_oidc_worker_result_never_claims_role_issuer_refresh_or_sms():
    env = candidate()['services']['api']['environment']
    result = probe.inspect_runtime(env, resolve=True,
        runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout='dynamic_identity_resolved\n'))
    assert result['configurationAndIdentityResolved']
    for key in ('providerPermissionsVerified', 'credentialRefreshVerified', 'issuerClaimsVerified',
                'expectedRoleVerified', 'projectionVerified', 'smsSent', 'pnvsRoundTripVerified', 'releaseReady'):
        assert result[key] is False


def test_help_does_not_load_credential_or_identity_modules_or_runtime_files():
    program = '''import runpy,sys
script=sys.argv[1]
sys.path.insert(0,str(__import__('pathlib').Path(script).parent))
def guard(event,args):
 if event=='import' and str(args[0]).startswith(('alibabacloud','identity_contract','app.')):
  raise AssertionError('unexpected SDK or runtime import')
 if event=='open' and ('/run/' in str(args[0]) or '.aliyun' in str(args[0]) or str(args[0])=='/dev/null'):
  raise AssertionError('unexpected runtime file')
sys.addaudithook(guard)
sys.argv=[script,'--help'];runpy.run_path(script,run_name='__main__')
'''
    result = subprocess.run([sys.executable, '-c', program, str(ROOT/'scripts/pnvs_runtime_preflight.py')],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '--resolve-default-chain' in result.stdout
