#!/usr/bin/env python3
"""Inspect PNVS login configuration and optionally resolve a runtime identity.

No phone number, PNVS send/check operation or application write is accepted.
The optional SDK lookup runs in a bounded child process. Only fixed status
codes leave it; credential values and SDK diagnostics are never returned.
An identity result does not prove provider permissions, KMS, SMS or release.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

from pilot_preflight import (
    PILOT_SMS_SCHEME_NAME, PILOT_SMS_SIGN_NAME, PILOT_SMS_TEMPLATE_CODE,
    bool_value, sms_default_credential_chain_configured,
)

INJECTED_KEYS = ('ALIBABA_CLOUD_ACCESS_KEY_ID', 'ALIBABA_CLOUD_ACCESS_KEY_SECRET',
                 'ALIBABA_CLOUD_SECURITY_TOKEN')
DYNAMIC_PROVIDERS = frozenset(
    prefix + name for prefix in ('default/', 'default/profile/')
    for name in ('ecs_ram_role', 'oidc_role_arn', 'credential_uri'))
RESOLUTION_STATES = frozenset(('dynamic_identity_resolved', 'incomplete_identity',
    'unreviewed_identity_source', 'identity_unavailable'))


def configuration(environment):
    checks = {
        'sms_enabled': bool_value(environment.get('OAM_SMS_LOGIN_ENABLED')) is True,
        'pnvs_provider': environment.get('OAM_SMS_PROVIDER') in ('aliyun_dypns', 'aliyun_pnvs'),
        'default_chain': sms_default_credential_chain_configured(environment),
        'no_injected_cloud_keys': not any(environment.get(key, '').strip() for key in INJECTED_KEYS),
        'signature': environment.get('OAM_SMS_SIGN_NAME') == PILOT_SMS_SIGN_NAME,
        'template': environment.get('OAM_SMS_TEMPLATE_CODE') == PILOT_SMS_TEMPLATE_CODE,
        'scheme': environment.get('OAM_SMS_SCHEME_NAME') == PILOT_SMS_SCHEME_NAME,
        'sms_only': all(bool_value(environment.get(key, 'false')) is False for key in (
            'OAM_WECHAT_LOGIN_ENABLED', 'OAM_PASSWORD_LOGIN_ENABLED')),
    }
    return checks


def resolve_identity(factory=None):
    # Isolated child only: do not restore SDK loggers or return exceptions whose
    # text may contain Authorization headers, URI credentials or SDK payloads.
    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            if factory is None:
                from alibabacloud_credentials.client import Client
                factory = Client
            credential = factory().get_credential()
            if credential.provider_name not in DYNAMIC_PROVIDERS:
                return 'unreviewed_identity_source'
            if not all((credential.access_key_id, credential.access_key_secret, credential.security_token)):
                return 'incomplete_identity'
            return 'dynamic_identity_resolved'
        except Exception:
            return 'identity_unavailable'


def inspect_runtime(environment, *, resolve=False, runner=subprocess.run):
    checks = configuration(environment)
    state = 'not_requested' if not resolve else 'configuration_blocked'
    if resolve and all(checks.values()):
        try:
            completed = runner([sys.executable, str(Path(__file__).resolve()), '--identity-worker'],
                env=dict(environment), capture_output=True, text=True, timeout=12, check=False)
            state = completed.stdout.strip()
            if completed.returncode != 0 or state not in RESOLUTION_STATES:
                state = 'identity_probe_failed'
        except subprocess.TimeoutExpired:
            state = 'identity_probe_timeout'
        except Exception:
            state = 'identity_probe_failed'
    return dict(configurationChecks=checks, identityResolution=state,
        configurationAndIdentityResolved=all(checks.values()) and state == 'dynamic_identity_resolved',
        providerPermissionsVerified=False, credentialRefreshVerified=False,
        smsSent=False, pnvsRoundTripVerified=False, releaseReady=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolve-default-chain', action='store_true')
    parser.add_argument('--identity-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.identity_worker:
        # The parent and worker independently refuse static/incomplete config.
        if not all(configuration(os.environ).values()):
            return 1
        print(resolve_identity())
        return 0
    report = inspect_runtime(os.environ, resolve=args.resolve_default_chain)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['configurationAndIdentityResolved'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
