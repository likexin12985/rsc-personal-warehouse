#!/usr/bin/env python3
"""Read four OSS bucket safety settings only after explicit --inspect.

No arguments prints help and makes no SDK/credential/network calls. Inspection
runs in a bounded child. Only fixed check states leave it; SDK output, exception
text, account coordinates and credentials are never emitted. This is not a
release gate, a credential-refresh proof or an attachment/backup UAT.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

SDK_VERSION = "1.3.2"
CHECKS = ("coordinates_valid", "sdk_version", "bucket_owner", "private_acl",
          "versioning_never_enabled", "sse_oss_aes256", "bucket_public_access_block")
STATES = frozenset(("passed", "failed", "unknown"))
WORKER_SECONDS = 45


class BucketReadTransport:
    """Public SDK HttpClient wrapper: exact GETs and nonempty version XML only."""
    def __init__(self, inner, region, bucket):
        self.inner, self.region, self.bucket = inner, region, bucket
        self.version_document = None

    def open(self):
        return self.inner.open()

    def close(self):
        return self.inner.close()

    def send(self, request, **kwargs):
        url = urlsplit(request.url)
        query = parse_qs(url.query, keep_blank_values=True)
        if (request.method != "GET" or url.scheme != "https"
                or url.hostname != f"{self.bucket}.oss-{self.region}.aliyuncs.com"
                or url.port not in (None, 443) or url.username or url.password
                or url.path != "/" or len(query) != 1
                or next(iter(query)) not in {"acl", "versioning", "encryption", "publicAccessBlock"}):
            raise ValueError("read boundary rejected")
        response = self.inner.send(request, **kwargs)
        if "versioning" in query:
            self.version_document = None
            if response.status_code == 200:
                try:
                    body = response.content
                    if not isinstance(body, bytes) or not 0 < len(body) <= 16384 or b"<!DOCTYPE" in body.upper():
                        raise ValueError("version response unavailable")
                    root = ET.fromstring(body)
                    if root.tag.split("}")[-1] != "VersioningConfiguration":
                        raise ValueError("version response unavailable")
                    children = list(root)
                    if not children and not (root.text or "").strip():
                        self.version_document = "never_enabled"
                    elif len(children) == 1 and children[0].tag.split("}")[-1] == "Status":
                        self.version_document = "status_present"
                    else:
                        raise ValueError("version response unavailable")
                except Exception:
                    response.close()
                    raise ValueError("version response unavailable") from None
        return response


def valid_coordinates(region, bucket, expected_owner):
    return bool(isinstance(region, str) and re.fullmatch(r"cn-[a-z][a-z0-9-]{1,40}", region)
        and isinstance(bucket, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket)
        and isinstance(expected_owner, str) and re.fullmatch(r"[0-9]{6,32}", expected_owner))


@contextlib.contextmanager
def quiet_sdk():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                yield
    finally:
        logging.disable(previous)


def inspect_bucket(region, bucket, expected_owner, *, sdk=None, client_factory=None,
                   version_reader=version):
    """Worker entry; factories support offline tests with the real pinned SDK."""
    checks = dict.fromkeys(CHECKS, "unknown")
    checks["coordinates_valid"] = "passed" if valid_coordinates(region, bucket, expected_owner) else "failed"
    if checks["coordinates_valid"] != "passed":
        return checks
    with quiet_sdk():
        transport_guard = None
        def guarded(inner):
            nonlocal transport_guard
            transport_guard = BucketReadTransport(inner, region, bucket)
            return transport_guard
        try:
            if version_reader("alibabacloud-oss-v2") != SDK_VERSION:
                checks["sdk_version"] = "failed"
                return checks
            if sdk is None:
                import alibabacloud_oss_v2 as sdk
            checks["sdk_version"] = "passed"
            if client_factory is None:
                # An independently provisioned audit identity is supplied only
                # to this explicit inspection process. No profile/IMDS fallback.
                config = sdk.Config(region=region, signature_version="v4",
                    credentials_provider=sdk.credentials.EnvironmentVariableCredentialsProvider(),
                    http_client=guarded(sdk.transport.RequestsHttpClient(connect_timeout=3,
                        readwrite_timeout=5, enabled_redirect=False, insecure_skip_verify=False)),
                    disable_ssl=False, insecure_skip_verify=False, enabled_redirect=False,
                    retry_max_attempts=1, connect_timeout=3, readwrite_timeout=5)
                client = sdk.Client(config)
            else:
                client = client_factory(sdk, region, guarded)
        except Exception:
            return checks

        # These methods/models/fields were inspected in installed OSS V2 1.3.2.
        # Each independent read is attempted once; access/transport errors remain
        # unknown and never become an assertion that the user's login expired.
        calls = (
            ("get_bucket_acl", "GetBucketAclRequest", ("bucket_owner", "private_acl")),
            ("get_bucket_versioning", "GetBucketVersioningRequest", ("versioning_never_enabled",)),
            ("get_bucket_encryption", "GetBucketEncryptionRequest", ("sse_oss_aes256",)),
            ("get_bucket_public_access_block", "GetBucketPublicAccessBlockRequest", ("bucket_public_access_block",)),
        )
        for method, request_model, keys in calls:
            try:
                result = getattr(client, method)(getattr(sdk, request_model)(bucket=bucket))
                if type(result.status_code) is not int or result.status_code != 200:
                    continue
                if method == "get_bucket_acl":
                    checks["private_acl"] = "passed" if result.acl == "private" else "failed"
                    owner = getattr(result.owner, "id", None)
                    checks["bucket_owner"] = "passed" if owner == expected_owner else "failed"
                elif method == "get_bucket_versioning":
                    # Official GET response omits Status only for never-enabled
                    # buckets. Empty string/unknown enum cannot stand for None.
                    # SDK 1.3.2 maps empty body and <Status/> to None too;
                    # transport proof distinguishes them without mutating SDK.
                    if transport_guard is None or transport_guard.version_document is None:
                        continue
                    state = result.version_status
                    checks[keys[0]] = "passed" if state is None and transport_guard.version_document == "never_enabled" else "failed"
                elif method == "get_bucket_encryption":
                    rule = result.server_side_encryption_rule
                    default = getattr(rule, "apply_server_side_encryption_by_default", None)
                    ok = (getattr(default, "sse_algorithm", None) == "AES256"
                          and getattr(default, "kms_master_key_id", None) in (None, "")
                          and getattr(default, "kms_data_encryption", None) in (None, ""))
                    checks[keys[0]] = "passed" if ok else "failed"
                else:
                    configuration = result.public_access_block_configuration
                    ok = getattr(configuration, "block_public_access", None) is True
                    checks[keys[0]] = "passed" if ok else "failed"
            except Exception:
                # Deliberately discard status bodies/URLs/SDK exception repr.
                for key in keys:
                    checks[key] = "unknown"
    return checks


def inspect_runtime(region, bucket, expected_owner, *, runner=subprocess.run):
    checks = dict.fromkeys(CHECKS, "unknown")
    configured = valid_coordinates(region, bucket, expected_owner)
    checks["coordinates_valid"] = "passed" if configured else "failed"
    probe_status = "configuration_rejected"
    fingerprint = None
    if configured:
        fingerprint = hashlib.sha256((region + "\0" + bucket + "\0" + expected_owner).encode()).hexdigest()
        try:
            completed = runner([sys.executable, str(Path(__file__).resolve()), "--inspect", "--worker",
                "--region", region, "--bucket", bucket, "--expected-owner", expected_owner],
                capture_output=True, text=True, timeout=WORKER_SECONDS, check=False)
            # Reconstruct the final response from allowlisted fixed states;
            # never forward even a valid JSON SDK diagnostic or arbitrary field.
            value = json.loads(completed.stdout) if completed.returncode == 0 else None
            if (type(value) is dict and set(value) == set(CHECKS)
                    and all(type(value[key]) is str and value[key] in STATES for key in CHECKS)
                    and value["coordinates_valid"] == "passed"):
                checks = {key: value[key] for key in CHECKS}
                probe_status = "completed"
            else:
                probe_status = "worker_unavailable"
        except subprocess.TimeoutExpired:
            probe_status = "timeout_unknown"
        except Exception:
            probe_status = "worker_unavailable"
    return dict(schema="cloud_oam.oss_bucket_preflight.v1", targetFingerprint=fingerprint,
        sdkExpectedVersion=SDK_VERSION, probeStatus=probe_status, checks=checks,
        bucketConfigurationVerified=all(value == "passed" for value in checks.values()),
        credentialRefreshVerified=False, objectRoundTripVerified=False,
        httpsPolicyVerified=False, corsVerified=False, backupRestoreVerified=False,
        releaseReady=False)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Invalid input may accidentally include a credential URL. Never echo it.
        self.print_usage(sys.stderr)
        self.exit(2, "invalid arguments; inspect --help\n")


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Explicitly permit four read-only OSS API calls")
    parser.add_argument("--region", help="OSS region, or OAM_FILE_STORAGE_REGION")
    parser.add_argument("--bucket", help="Exact bucket, or OAM_FILE_STORAGE_BUCKET")
    parser.add_argument("--expected-owner", help="Expected account ID, or RSC_OSS_PREFLIGHT_OWNER_ID")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.inspect:
        parser.print_help()
        return 0
    region = args.region if args.region is not None else os.environ.get("OAM_FILE_STORAGE_REGION", "")
    bucket = args.bucket if args.bucket is not None else os.environ.get("OAM_FILE_STORAGE_BUCKET", "")
    owner = args.expected_owner if args.expected_owner is not None else os.environ.get("RSC_OSS_PREFLIGHT_OWNER_ID", "")
    if args.worker:
        # This mode still requires --inspect and emits only the fixed check map.
        print(json.dumps(inspect_bucket(region, bucket, owner), sort_keys=True))
        return 0
    result = inspect_runtime(region, bucket, owner)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["bucketConfigurationVerified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
