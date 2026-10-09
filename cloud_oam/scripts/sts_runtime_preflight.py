#!/usr/bin/env python3
"""One bounded GetCallerIdentity read with the application's OIDC identity.

No arguments only prints help. --inspect accepts one fixed purpose, never an
arbitrary action/endpoint or credentials. No SMS/object write is possible.
This proves one identity read, not renewal, object permissions, SMS or UAT.
"""
from __future__ import annotations

import argparse
import contextlib
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

CHECKS = ('configuration', 'sdk_versions', 'dynamic_provider', 'sts_response',
          'expected_account', 'expected_role_session', 'assumed_role_identity')
STATES = frozenset(('passed', 'failed', 'unknown'))
SDK_VERSIONS = {'alibabacloud-credentials': '1.0.12', 'alibabacloud-tea-openapi': '0.4.6',
                'alibabacloud-oss-v2': '1.3.2'}
REGION = 'cn-hangzhou'
ENDPOINT = 'sts.cn-hangzhou.aliyuncs.com'


@contextlib.contextmanager
def bounded_sts_transport():
    """One exchange and one identity RPC; redirects cannot forward credentials."""
    import requests
    original = requests.Session.send
    counts = {'AssumeRoleWithOIDC': 0, 'GetCallerIdentity': 0}
    def send(session, request, **kwargs):
        url = urlsplit(request.url)
        query = parse_qs(url.query, keep_blank_values=True)
        action = request.headers.get('x-acs-action') or (query.get('Action') or [''])[0]
        api_version = request.headers.get('x-acs-version') or (query.get('Version') or [''])[0]
        if (url.scheme != 'https' or url.hostname != ENDPOINT or url.port not in (None, 443)
                or url.username or url.password or url.fragment or url.path not in ('', '/')
                or action not in counts or api_version != '2015-04-01'
                or request.method != ('GET' if action == 'AssumeRoleWithOIDC' else 'POST')
                or any(len(query.get(k, [])) > 1 for k in ('Action', 'Version'))
                or query.get('Action', [action])[0] != action
                or query.get('Version', [api_version])[0] != api_version
                or counts[action] != 0 or kwargs.get('verify') is not True):
            raise ValueError('sts_transport_boundary')
        counts[action] += 1
        kwargs['allow_redirects'] = False
        return original(session, request, **kwargs)
    requests.Session.send = send
    try:
        yield
    finally:
        requests.Session.send = original


def coordinates(environment, purpose):
    from pilot_preflight import PNVS_STATIC_FIELDS, pnvs_oidc_environment_configured
    if purpose not in ('oss', 'pnvs') or any(environment.get(key) for key in PNVS_STATIC_FIELDS):
        raise ValueError('identity_configuration')
    account = environment.get('RSC_OIDC_EXPECTED_ACCOUNT_ID', '')
    prefix = 'OAM_FILE_STORAGE' if purpose == 'oss' else 'RSC_PNVS'
    role = environment.get(prefix + '_OIDC_ROLE_ARN', '')
    provider = environment.get(prefix + '_OIDC_PROVIDER_ARN', '')
    session = environment.get(prefix + '_OIDC_SESSION_NAME', 'rsc-pilot-files' if purpose == 'oss' else '')
    token = environment.get(prefix + '_OIDC_TOKEN_FILE', '')
    region = environment.get('OAM_FILE_STORAGE_REGION' if purpose == 'oss' else 'ALIBABA_CLOUD_STS_REGION', '')
    if (re.fullmatch(r'[0-9]{16}', account) is None
            or re.fullmatch(r'acs:ram::' + account + r':role/[A-Za-z0-9.@_-]{1,64}', role) is None
            or re.fullmatch(r'acs:ram::' + account + r':oidc-provider/[A-Za-z0-9._-]{1,128}', provider) is None
            or re.fullmatch(r'[A-Za-z0-9.@_-]{2,64}', session) is None or region != REGION
            or not token.startswith('/run/') or '..' in Path(token).parts or str(Path(token)) != token
            or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise ValueError('identity_configuration')
    if purpose == 'oss':
        if environment.get('OAM_FILE_STORAGE_CREDENTIAL_MODE') != 'oidc_role_arn':
            raise ValueError('identity_configuration')
    elif not pnvs_oidc_environment_configured(environment):
        raise ValueError('identity_configuration')
    return dict(account=account, role=role, provider=provider, session=session, token=token)


def resolve_credentials(purpose, identity):
    """Use the actual app providers; values remain only in this child process."""
    if purpose == 'oss':
        backend = str(Path(__file__).resolve().parents[1] / 'backend')
        if backend not in sys.path:
            sys.path.insert(0, backend)
        from app.oss_runtime_credentials import OssOidcCredentialsProvider, OssOidcIdentity
        value = OssOidcCredentialsProvider(OssOidcIdentity(role_arn=identity['role'],
            provider_arn=identity['provider'], token_file=identity['token'], region=REGION,
            session_name=identity['session'])).get_credentials()
    else:
        from alibabacloud_credentials.client import Client
        value = Client().get_credential()
        if value.provider_name != 'default/oidc_role_arn':
            raise ValueError('identity_provider')
    values = {key: getattr(value, key, None) for key in ('access_key_id', 'access_key_secret', 'security_token')}
    if not all(type(v) is str and 1 <= len(v) <= 16384 and not any(ord(c) < 33 or ord(c) == 127 for c in v)
               for v in values.values()):
        raise ValueError('identity_values')
    return values


def caller_identity(credentials):
    """Pinned generic SDK, exact one RPC, no alternate endpoint or action."""
    from alibabacloud_tea_openapi.client import Client
    from alibabacloud_tea_openapi import models
    from darabonba.runtime import RuntimeOptions
    from darabonba.policy.retry import RetryOptions
    client = Client(models.Config(**credentials, endpoint=ENDPOINT, protocol='HTTPS',
        region_id=REGION, connect_timeout=1000, read_timeout=2500,
        retry_options=RetryOptions(retryable=False)))
    return client.call_api(models.Params(action='GetCallerIdentity', version='2015-04-01',
        protocol='HTTPS', pathname='/', method='POST', auth_type='AK', style='RPC',
        req_body_type='formData', body_type='json'), models.OpenApiRequest(),
        RuntimeOptions(autoretry=False, max_attempts=1, ignore_ssl=False,
                       connect_timeout=1000, read_timeout=2500))


def inspect_identity(environment, purpose, *, resolver=resolve_credentials,
                     caller=caller_identity, version_reader=version):
    checks = dict.fromkeys(CHECKS, 'unknown')
    # Suppress SDK output from construction through identity parsing. Exceptions
    # are discarded inside this child; neither their text nor context leaves it.
    logging.disable(logging.CRITICAL)
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            identity = coordinates(environment, purpose)
            checks['configuration'] = 'passed'
        except Exception:
            checks['configuration'] = 'failed'
            return checks
        try:
            if any(version_reader(name) != expected for name, expected in SDK_VERSIONS.items()):
                checks['sdk_versions'] = 'failed'
                return checks
            checks['sdk_versions'] = 'passed'
            with bounded_sts_transport():
                credential = resolver(purpose, identity)
                checks['dynamic_provider'] = 'passed'
                response = caller(credential)
            # Non-2xx, missing fields and transport failures stay unknown. An
            # actual complete identity which differs from expectation fails.
            if type(response) is not dict or type(response.get('statusCode')) is not int or response['statusCode'] != 200:
                return checks
            body = response.get('body')
            if type(body) is not dict or not all(type(body.get(k)) is str and body[k] for k in ('AccountId', 'Arn', 'IdentityType')):
                return checks
            checks['sts_response'] = 'passed'
            checks['expected_account'] = 'passed' if body['AccountId'] == identity['account'] else 'failed'
            checks['expected_role_session'] = 'passed' if body['Arn'] == identity['role'].replace(':role/', ':assumed-role/', 1) + '/' + identity['session'] else 'failed'
            checks['assumed_role_identity'] = 'passed' if body['IdentityType'] == 'AssumedRoleUser' else 'failed'
        except Exception:
            pass
    return checks


def inspect_runtime(environment, purpose, *, runner=subprocess.run):
    checks = dict.fromkeys(CHECKS, 'unknown')
    state = 'configuration_rejected'
    try:
        coordinates(environment, purpose)
        checks['configuration'] = 'passed'
        state = 'unknown'
        result = runner([sys.executable, str(Path(__file__).resolve()), '--inspect', '--purpose', purpose, '--worker'],
            env=dict(environment), capture_output=True, text=True, timeout=20, check=False)
        if result.returncode == 0 and type(result.stdout) is str and len(result.stdout) <= 4096:
            value = json.loads(result.stdout)
            if (type(value) is dict and set(value) == set(CHECKS)
                    and all(type(v) is str and v in STATES for v in value.values())
                    and value['configuration'] == 'passed'):
                checks = {key: value[key] for key in CHECKS}
                state = 'completed'
    except Exception:
        pass
    return dict(schema='rsc.sts-runtime-preflight.v1', purpose=purpose, probeState=state,
        checks=checks, identityVerified=state == 'completed' and all(v == 'passed' for v in checks.values()),
        credentialRefreshVerified=False, objectPermissionsVerified=False,
        issuerClaimsVerified=False, projectionVerified=False,
        smsSent=False, pnvsRoundTripVerified=False, releaseReady=False,
        autoRetry=False, unknownRequiresExactReadback=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--purpose', choices=('oss', 'pnvs'))
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.inspect:
        parser.print_help(); return 0
    if args.purpose is None:
        parser.error('--inspect requires --purpose')
    if args.worker:
        print(json.dumps(inspect_identity(os.environ, args.purpose), sort_keys=True)); return 0
    report = inspect_runtime(os.environ, args.purpose)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['identityVerified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
