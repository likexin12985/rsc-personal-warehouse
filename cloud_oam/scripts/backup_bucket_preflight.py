#!/usr/bin/env python3
"""Four read-only settings for the fixed VERSIONED backup bucket; no writes."""
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

import oss_bucket_preflight as base

CHECKS = tuple('versioning_enabled' if key == 'versioning_never_enabled' else key for key in base.CHECKS)


def modules():
    root = Path(__file__).resolve().parents[1]
    for relative in ('deployment/backup-worker', 'backend'):
        path = str(root/relative)
        if path not in sys.path: sys.path.insert(0, path)
    import archive_joint
    import backup_recovery_identity
    return archive_joint, backup_recovery_identity


class EnabledTransport:
    def __init__(self, inner):
        self.inner = inner
        self.remaining = {'acl', 'versioning', 'encryption', 'publicAccessBlock'}
        self.enabled = None

    def open(self): return self.inner.open()
    def close(self): return self.inner.close()

    def send(self, request, **kwargs):
        shared, _ = modules()
        url = urlsplit(request.url); query = parse_qs(url.query, keep_blank_values=True)
        shared.require(request.method == 'GET' and url.scheme == 'https'
            and url.hostname == shared.BUCKET+'.oss-'+shared.REGION+'.aliyuncs.com'
            and url.port in (None, 443) and not url.username and not url.password and not url.fragment
            and url.path == '/' and len(query) == 1 and next(iter(query)) in self.remaining
            and next(iter(query.values())) == [''])
        action = next(iter(query)); self.remaining.remove(action)
        response = self.inner.send(request, **kwargs)
        if action == 'versioning' and response.status_code == 200:
            try:
                raw = response.content
                shared.require(type(raw) is bytes and 0 < len(raw) <= 16384 and b'<!DOCTYPE' not in raw.upper())
                root = ET.fromstring(raw)
                shared.require(root.tag.split('}')[-1] == 'VersioningConfiguration')
                children = list(root)
                self.enabled = (len(children) == 1 and children[0].tag.split('}')[-1] == 'Status'
                    and children[0].text == 'Enabled' and not list(children[0]))
            except Exception:
                response.close(); raise ValueError('backup version response unavailable') from None
        return response


def inspect(*, client_factory=None):
    shared, identity = modules(); tracker = []
    def factory(sdk, region, guarded):
        if client_factory is not None:
            def wrap(inner):
                transport = EnabledTransport(inner); tracker.append(transport); return guarded(transport)
            return client_factory(sdk, region, wrap)
        transport = EnabledTransport(sdk.transport.RequestsHttpClient(connect_timeout=3, readwrite_timeout=5,
            enabled_redirect=False, insecure_skip_verify=False)); tracker.append(transport)
        return sdk.Client(sdk.Config(region=region, credentials_provider=identity.credentials(), signature_version='v4',
            http_client=guarded(transport), retry_max_attempts=1, enabled_redirect=False,
            disable_ssl=False, insecure_skip_verify=False, connect_timeout=3, readwrite_timeout=5))
    # Reuse the reviewed owner/private/AES256/BPA checks without changing the
    # attachment-only never_enabled contract or pretending it passed.
    checks = base.inspect_bucket(shared.REGION, shared.BUCKET, shared.ACCOUNT, client_factory=factory)
    original = checks.pop('versioning_never_enabled')
    observed = tracker[0].enabled if len(tracker) == 1 else None
    checks['versioning_enabled'] = ('unknown' if original == 'unknown' or observed is None
                                    else 'passed' if observed is True else 'failed')
    return checks


def runtime(*, runner=subprocess.run):
    checks = dict.fromkeys(CHECKS, 'unknown'); status = 'worker_unavailable'
    try:
        child = runner([sys.executable, str(Path(__file__).resolve()), '--inspect', '--worker'],
            capture_output=True, text=True, timeout=45, check=False)
        value = json.loads(child.stdout) if child.returncode == 0 else None
        if (type(value) is dict and set(value) == set(CHECKS)
                and all(type(value[key]) is str and value[key] in base.STATES for key in CHECKS)):
            checks = value; status = 'completed'
    except subprocess.TimeoutExpired: status = 'timeout_unknown'
    except Exception: pass
    return dict(schema='rsc.backup-bucket-preflight.v1', probeStatus=status, checks=checks,
        bucketConfigurationVerified=all(v == 'passed' for v in checks.values()),
        objectRoundTripVerified=False, httpsPolicyVerified=False, restoreVerified=False, releaseReady=False)


def main():
    parser = base.SafeParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--worker', action='store_true', help=base.argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.inspect: parser.print_help(); return 0
    if args.worker:
        with base.quiet_sdk(): checks = inspect()
        print(json.dumps(checks, sort_keys=True)); return 0
    value = runtime(); print(json.dumps(value, sort_keys=True)); return 0 if value['bucketConfigurationVerified'] else 1


if __name__ == '__main__': raise SystemExit(main())
