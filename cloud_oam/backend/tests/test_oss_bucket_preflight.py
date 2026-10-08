"""Pinned real OSS SDK with synthetic transport; cloud/network access is forbidden."""
import importlib.util
import json
import logging
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.types import HttpClient, HttpResponse
import pytest
from requests.structures import CaseInsensitiveDict

REGION = "cn-hangzhou"
BUCKET = "synthetic-oss-preflight"
OWNER = "1234567890123456"
MARKER = "synthetic-sensitive-marker-must-not-escape"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network forbidden in OSS bucket tests")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[2] / "scripts" / "oss_bucket_preflight.py"
    spec = importlib.util.spec_from_file_location("oss_bucket_preflight_candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response(HttpResponse):
    def __init__(self, request, body, status=200):
        self._request, self._body, self._status = request, body, status
        self.closed = False
        self._headers = CaseInsensitiveDict({"Content-Type": "application/xml", "Content-Length": str(len(body))})
    request = property(lambda self: self._request)
    status_code = property(lambda self: self._status)
    headers = property(lambda self: self._headers)
    content = property(lambda self: self._body)
    reason = property(lambda self: "Synthetic response")
    is_closed = property(lambda self: self.closed)
    is_stream_consumed = property(lambda self: self.closed)
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self): self.closed = True
    def read(self): self.close(); return self.content
    def iter_bytes(self, **kwargs): yield self.content


class Transport(HttpClient):
    def __init__(self, *, acl="private", owner=OWNER, version_body=b"<VersioningConfiguration/>",
                 encryption="AES256", kms_id="", block="true", failure=None):
        self.calls = []
        self.failure = failure
        self.bodies = {
            "acl": f"<AccessControlPolicy><Owner><ID>{owner}</ID></Owner><AccessControlList><Grant>{acl}</Grant></AccessControlList></AccessControlPolicy>".encode(),
            "versioning": version_body,
            "encryption": f"<ServerSideEncryptionRule><ApplyServerSideEncryptionByDefault><SSEAlgorithm>{encryption}</SSEAlgorithm><KMSMasterKeyID>{kms_id}</KMSMasterKeyID></ApplyServerSideEncryptionByDefault></ServerSideEncryptionRule>".encode(),
            "publicAccessBlock": f"<PublicAccessBlockConfiguration><BlockPublicAccess>{block}</BlockPublicAccess></PublicAccessBlockConfiguration>".encode(),
        }
    def open(self): pass
    def close(self): pass
    def send(self, request, **kwargs):
        url = urlsplit(request.url)
        assert request.method == "GET"
        assert url.scheme == "https" and url.hostname == f"{BUCKET}.oss-{REGION}.aliyuncs.com"
        assert url.path == "/"
        assert request.headers["authorization"].startswith("OSS4-HMAC-SHA256 ")
        operation = next(iter(parse_qs(url.query, keep_blank_values=True)))
        assert operation in self.bodies
        self.calls.append(operation)  # no credential or signed URL retained
        if self.failure == operation:
            print(MARKER)
            logging.critical(MARKER)
            return Response(request, f"<Error><Code>AccessDenied</Code><Message>{MARKER}</Message></Error>".encode(), 403)
        return Response(request, self.bodies[operation])


def inspect(probe, **changes):
    transport = Transport(**changes)
    def factory(sdk, region, guarded):
        return sdk.Client(sdk.Config(region=region, signature_version="v4", http_client=guarded(transport),
            retry_max_attempts=1, enabled_redirect=False, disable_ssl=False,
            credentials_provider=sdk.credentials.StaticCredentialsProvider("synthetic-id", "synthetic-secret", "synthetic-token")))
    return probe.inspect_bucket(REGION, BUCKET, OWNER, sdk=oss, client_factory=factory), transport


def test_real_sdk_serializes_exact_four_read_only_calls(probe):
    checks, transport = inspect(probe)
    assert all(value == "passed" for value in checks.values())
    assert transport.calls == ["acl", "versioning", "encryption", "publicAccessBlock"]


def test_default_client_uses_bounded_verified_transport_with_synthetic_identity(probe, monkeypatch):
    transport = Transport()
    def transport_factory(**kwargs):
        assert kwargs == dict(connect_timeout=3, readwrite_timeout=5,
            enabled_redirect=False, insecure_skip_verify=False)
        return transport
    monkeypatch.setenv('OSS_ACCESS_KEY_ID', 'synthetic-id')
    monkeypatch.setenv('OSS_ACCESS_KEY_SECRET', 'synthetic-secret')
    monkeypatch.setenv('OSS_SESSION_TOKEN', 'synthetic-token')
    monkeypatch.setattr(oss.transport, 'RequestsHttpClient', transport_factory)
    checks = probe.inspect_bucket(REGION, BUCKET, OWNER)
    assert all(value == 'passed' for value in checks.values())
    assert transport.calls == ['acl', 'versioning', 'encryption', 'publicAccessBlock']


def test_pinned_sdk_namespace_limitation_remains_unknown(probe):
    # Official examples include this namespace; SDK 1.3.2 compares its root
    # literally. Preserve this incompatibility rather than accepting exceptions
    # or changing third-party code to make a deployment check appear green.
    body = b'<VersioningConfiguration xmlns="http://doc.oss-cn-hangzhou.aliyuncs.com"/>'
    checks, _ = inspect(probe, version_body=body)
    assert checks["versioning_never_enabled"] == "unknown"
    assert checks["private_acl"] == "passed"


@pytest.mark.parametrize("state", ["Enabled", "Suspended", "Disabled", "", "unexpected"])
def test_any_version_status_cannot_bypass_forbid_overwrite_guard(probe, state):
    checks, _ = inspect(probe, version_body=f"<VersioningConfiguration><Status>{state}</Status></VersioningConfiguration>".encode())
    assert checks["versioning_never_enabled"] == "failed"


@pytest.mark.parametrize("body", [b"", b"<unexpected/>", b"not xml", b"<VersioningConfiguration><Unknown/></VersioningConfiguration>", b"<VersioningConfiguration>unexpected</VersioningConfiguration>"])
def test_missing_or_unparseable_version_document_is_unknown(probe, body):
    checks, _ = inspect(probe, version_body=body)
    assert checks["versioning_never_enabled"] == "unknown"


@pytest.mark.parametrize("change,key", [
    ({"acl": "public-read"}, "private_acl"),
    ({"acl": "public-read-write"}, "private_acl"),
    ({"owner": "9999999999999999"}, "bucket_owner"),
    ({"block": "false"}, "bucket_public_access_block"),
    ({"block": ""}, "bucket_public_access_block"),
    ({"encryption": "KMS"}, "sse_oss_aes256"),
    ({"encryption": "SM4"}, "sse_oss_aes256"),
    ({"encryption": ""}, "sse_oss_aes256"),
    ({"kms_id": MARKER}, "sse_oss_aes256"),
])
def test_unsafe_configuration_is_rejected_without_echoing_values(probe, change, key):
    checks, _ = inspect(probe, **change)
    assert checks[key] == "failed"
    assert MARKER not in json.dumps(checks)


@pytest.mark.parametrize("operation,keys", [
    ("acl", ("private_acl", "bucket_owner")),
    ("versioning", ("versioning_never_enabled",)),
    ("encryption", ("sse_oss_aes256",)),
    ("publicAccessBlock", ("bucket_public_access_block",)),
])
def test_access_or_transport_failure_stays_unknown_and_sdk_output_private(probe, operation, keys, capsys):
    checks, transport = inspect(probe, failure=operation)
    assert all(checks[key] == "unknown" for key in keys)
    assert transport.calls.count(operation) == 1
    assert capsys.readouterr() == ("", "")
    assert MARKER not in json.dumps(checks)


def test_sdk_version_drift_blocks_all_client_creation(probe):
    def factory(*args): raise AssertionError("must not create client")
    checks = probe.inspect_bucket(REGION, BUCKET, OWNER, client_factory=factory, version_reader=lambda _: "999.0")
    assert checks["sdk_version"] == "failed"
    assert checks["private_acl"] == "unknown"


def test_invalid_coordinates_do_not_start_worker_or_echo_input(probe):
    def runner(*args, **kwargs): raise AssertionError("must not start worker")
    result = probe.inspect_runtime(REGION, "https://secret:" + MARKER, OWNER, runner=runner)
    assert result["checks"]["coordinates_valid"] == "failed"
    assert result["targetFingerprint"] is None
    assert MARKER not in json.dumps(result)


def test_worker_timeout_is_unknown_and_not_login_failure(probe):
    def runner(*args, **kwargs):
        assert kwargs["timeout"] == 45
        raise subprocess.TimeoutExpired("synthetic-worker", 45, output=MARKER)
    result = probe.inspect_runtime(REGION, BUCKET, OWNER, runner=runner)
    assert result["probeStatus"] == "timeout_unknown"
    assert result["checks"]["private_acl"] == "unknown"
    assert not result["releaseReady"] and not result["bucketConfigurationVerified"]
    assert MARKER not in json.dumps(result)


@pytest.mark.parametrize("output", [MARKER, '{}', '{"releaseReady":true}', json.dumps(dict.fromkeys(["private_acl"], "passed"))])
def test_worker_output_is_allowlisted_not_forwarded(probe, output):
    result = probe.inspect_runtime(REGION, BUCKET, OWNER,
        runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=output, stderr=MARKER))
    assert result["probeStatus"] == "worker_unavailable"
    assert not result["bucketConfigurationVerified"]
    assert MARKER not in json.dumps(result)


def test_all_bucket_checks_do_not_claim_identity_object_backup_or_release(probe):
    checks = dict.fromkeys(probe.CHECKS, "passed")
    result = probe.inspect_runtime(REGION, BUCKET, OWNER,
        runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(checks), stderr=MARKER))
    assert result["bucketConfigurationVerified"]
    for key in ("credentialRefreshVerified", "objectRoundTripVerified", "httpsPolicyVerified", "corsVerified", "backupRestoreVerified", "releaseReady"):
        assert result[key] is False
    assert BUCKET not in json.dumps(result) and OWNER not in json.dumps(result)


def test_default_help_does_not_touch_sdk_credentials_or_environment(probe, monkeypatch, capsys):
    def forbidden(*args, **kwargs): raise AssertionError("inspection not requested")
    monkeypatch.setattr(probe, "inspect_runtime", forbidden)
    monkeypatch.setattr(probe, "inspect_bucket", forbidden)
    assert probe.main([]) == 0
    assert "--inspect" in capsys.readouterr().out
    assert probe.main(["--worker"]) == 0


def test_invalid_cli_does_not_echo_a_secret(probe, capsys):
    with pytest.raises(SystemExit) as error:
        probe.main(["--unknown-secret=" + MARKER])
    assert error.value.code == 2
    assert MARKER not in capsys.readouterr().err


@pytest.mark.parametrize("method,url", [
    ("PUT", f"https://{BUCKET}.oss-{REGION}.aliyuncs.com/?acl"),
    ("GET", "https://other.invalid/?acl"),
    ("GET", f"http://{BUCKET}.oss-{REGION}.aliyuncs.com/?acl"),
    ("GET", f"https://{BUCKET}.oss-{REGION}.aliyuncs.com/?delete"),
    ("GET", f"https://{BUCKET}.oss-{REGION}.aliyuncs.com/private-object?acl"),
])
def test_transport_cannot_be_reused_for_writes_objects_or_other_endpoints(probe, method, url):
    inner = SimpleNamespace(send=lambda *a, **k: pytest.fail("transport must reject before sending"))
    transport = probe.BucketReadTransport(inner, REGION, BUCKET)
    with pytest.raises(ValueError, match="read boundary rejected"):
        transport.send(SimpleNamespace(method=method, url=url))
