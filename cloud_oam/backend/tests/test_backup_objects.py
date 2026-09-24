"""Real installed OSS V2 serialization/signing; synthetic in-memory transport only."""
from backup_test_support import CLOUD, WORKER, OPS
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import socket
import tarfile
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.types import HttpClient, HttpResponse
from requests.structures import CaseInsensitiveDict
import pytest

from app.foundation_models import FileObject
from app.formal_services.file_storage import StoredObjectHead
from app.formal_services.formal_files import (
    _prepare_upload, _upload_request_hash, _storage_key, _head_manifest_sha256,
    FILE_METADATA_SCHEMA, FileUploadIntentInput,
)
from formal_object_backup import OssBackupReader, ObjectBackupError, capture_objects, sdk_client


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network forbidden in SDK transport fixture")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def fixture_row(body=b"candidate bytes", *, pending=False, purpose="request_attachment"):
    file_id = uuid4()
    prepared = _prepare_upload(FileUploadIntentInput(purpose=purpose, original_filename="test.png",
        size_bytes=len(body), mime_type="image/png", sha256=sha256(body).hexdigest()), maximum_size_bytes=120*1024*1024)
    row = FileObject(id=file_id, storage_key=_storage_key(purpose, file_id), sha256=sha256(body).hexdigest(),
        size_bytes=len(body), mime_type="image/png", original_filename="test.png", uploaded_by="synthetic-backup-user",
        status="pending" if pending else "available", created_at=datetime.now(timezone.utc))
    row.metadata_jsonb = dict(schema=FILE_METADATA_SCHEMA, authorization_version=1,
        file_id=str(file_id), storage_key=row.storage_key, uploader_user_id=row.uploaded_by,
        uploader_person_id=str(uuid4()), purpose=purpose, provider="aliyun_oss_v2",
        request_sha256=_upload_request_hash(prepared), idempotency_key_hash="a"*64)
    # Deliberately not MD5: the backup treats the quoted ETag as an opaque token.
    etag = '"synthetic-multipart-etag-3"'
    head = StoredObjectHead(row.storage_key, row.size_bytes, row.mime_type,
        {"file-id": str(file_id), "sha256": row.sha256}, etag)
    if not pending:
        row.metadata_jsonb["completion"] = dict(etag_sha256=sha256(etag.encode()).hexdigest(),
            head_manifest_sha256=_head_manifest_sha256(head), verified_at=row.created_at.isoformat())
    headers = {"Content-Length": str(len(body)), "Content-Type": "image/png", "ETag": etag,
        "x-oss-meta-file-id": str(file_id), "x-oss-meta-sha256": row.sha256,
        "x-oss-version-id": "synthetic-version+/=", "x-oss-server-side-encryption": "KMS",
        "x-oss-server-side-encryption-key-id": "synthetic-kms-reference"}
    return row, body, headers


class Response(HttpResponse):
    def __init__(self, request, spec):
        self._request = request
        self.spec = spec
        self._headers = CaseInsensitiveDict(spec["headers"])
        self.closed = False
        self.consumed = False
        self.bytes_yielded = 0

    request = property(lambda self: self._request)
    status_code = property(lambda self: self.spec.get("status", 200))
    headers = property(lambda self: self._headers)
    reason = property(lambda self: "Synthetic response")
    is_closed = property(lambda self: self.closed)
    is_stream_consumed = property(lambda self: self.consumed)
    content = property(lambda self: self.spec.get("body", b""))
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self): self.closed = True
    def read(self):
        self.consumed = True
        self.close()
        return self.content
    def iter_bytes(self, **kwargs):
        size = kwargs["block_size"]
        assert size == 64 * 1024
        for pos in range(0, len(self.content), size):
            data = self.content[pos:pos+size]
            self.bytes_yielded += len(data)
            yield data
            if self.spec.get("stream_error"):
                raise IOError("synthetic secret must not escape")
        self.consumed = True


class Transport(HttpClient):
    def __init__(self, specs):
        self.specs = specs
        self.calls = []
        self.responses = []
    def open(self): pass
    def close(self): pass
    def send(self, request, **kwargs):
        assert request.method in {"HEAD", "GET"}
        url = urlsplit(request.url)
        assert url.scheme == "https" and url.hostname == "synthetic-backup.oss-cn-hangzhou.aliyuncs.com"
        assert request.headers["authorization"].startswith("OSS4-HMAC-SHA256 ")
        # Never retain the Authorization or security-token headers in evidence.
        self.calls.append(dict(method=request.method, path=url.path, query=parse_qs(url.query),
            if_match=request.headers.get("if-match"), encoding=request.headers.get("accept-encoding"),
            signed_condition="if-match" in request.headers["authorization"]))
        response = Response(request, self.specs[len(self.responses)])
        self.responses.append(response)
        return response


def reader_for(row, body, headers, *, head_change=None, get_change=None, status=200, stream_error=False):
    head = {"headers": deepcopy(headers)}
    get = {"headers": deepcopy(headers), "body": body, "status": status, "stream_error": stream_error}
    if head_change: head_change(head)
    if get_change: get_change(get)
    transport = Transport([head, get])
    config = oss.Config(region="cn-hangzhou", signature_version="v4", http_client=transport,
        credentials_provider=oss.credentials.StaticCredentialsProvider("SYNTHETIC_TEST_ID", "synthetic-not-a-live-secret"),
        retry_max_attempts=1, additional_headers=["if-match", "accept-encoding"])
    return OssBackupReader(sdk_client(config), region="cn-hangzhou", bucket="synthetic-backup"), transport


def capture(tmp_path, row, reader):
    return capture_objects([row], reader, tmp_path/"objects.tar", max_total_bytes=120*1024*1024)


def assert_no_bundle(tmp_path):
    assert list(tmp_path.iterdir()) == []


def test_real_sdk_v4_condition_version_stream_and_completion_archive(tmp_path):
    row, body, headers = fixture_row(b"0123456789" * 70_000)
    reader, transport = reader_for(row, body, headers)
    before = deepcopy(row.metadata_jsonb)
    result = capture(tmp_path, row, reader)
    assert row.metadata_jsonb == before
    assert [item["method"] for item in transport.calls] == ["HEAD", "GET"]
    get = transport.calls[1]
    assert get["query"] == {"versionId": [headers["x-oss-version-id"]]}
    assert get["if_match"] == headers["ETag"] and get["encoding"] == "identity" and get["signed_condition"]
    assert get["path"] == "/" + row.storage_key
    assert all(item.closed for item in transport.responses)
    assert result["database_snapshot_bound"] is False
    assert result["catalog"][0]["metadata"]["completion"] == before["completion"]
    assert result["objects"][0]["encryption"]["server_side_encryption_key_id"] == "synthetic-kms-reference"
    assert (tmp_path/"objects.tar").stat().st_mode & 0o777 == 0o600
    with tarfile.open(tmp_path/"objects.tar") as archive:
        assert set(archive.getnames()) == {"manifest.json", row.id.hex}
        assert archive.extractfile(row.id.hex).read() == body
        assert json.load(archive.extractfile("manifest.json")) == result
        assert all(item.isfile() and item.mode == 0o600 for item in archive)


@pytest.mark.parametrize("version", [None, "null"])
def test_unversioned_or_suspended_bucket_still_uses_if_match(tmp_path, version):
    row, body, headers = fixture_row()
    if version is None: headers.pop("x-oss-version-id")
    else: headers["x-oss-version-id"] = version
    reader, transport = reader_for(row, body, headers)
    capture(tmp_path, row, reader)
    assert transport.calls[1]["if_match"] == headers["ETag"]
    assert transport.calls[1]["query"] == ({} if version is None else {"versionId": ["null"]})


@pytest.mark.parametrize("change", [
    lambda s: s["headers"].update({"ETag": '"different"'}),
    lambda s: s["headers"].update({"x-oss-meta-sha256": "0"*64}),
    lambda s: s["headers"].update({"x-oss-meta-file-id": str(uuid4())}),
    lambda s: s["headers"].update({"x-oss-meta-extra": "not-completed"}),
    lambda s: s["headers"].update({"Content-Length": "999"}),
    lambda s: s["headers"].update({"Content-Type": "application/pdf"}),
    lambda s: s["headers"].update({"Content-Encoding": "gzip"}),
    lambda s: s["headers"].update({"Content-Range": "bytes 0-13/100"}),
    lambda s: s["headers"].update({"x-oss-delete-marker": "true"}),
    lambda s: s["headers"].update({"ETag": "*"}),
    lambda s: s["headers"].update({"ETag": '"a", "b"'}),
    lambda s: s["headers"].pop("ETag"),
])
def test_head_refusal_never_downloads_or_publishes(tmp_path, change):
    row, body, headers = fixture_row()
    reader, transport = reader_for(row, body, headers, head_change=change)
    with pytest.raises(ObjectBackupError): capture(tmp_path, row, reader)
    assert len(transport.calls) == 1
    assert_no_bundle(tmp_path)


@pytest.mark.parametrize("change", [
    lambda s: s["headers"].update({"ETag": '"replaced"'}),
    lambda s: s["headers"].update({"x-oss-version-id": "replaced"}),
    lambda s: s["headers"].pop("x-oss-version-id"),
    lambda s: s["headers"].update({"x-oss-server-side-encryption-key-id": "another-key"}),
    lambda s: s["headers"].pop("x-oss-server-side-encryption"),
    lambda s: s["headers"].update({"Content-Encoding": "gzip"}),
    lambda s: s.update(status=206),
    lambda s: s.update(body=b"changed content"),
    lambda s: s.update(body=b"short"),
    lambda s: s.update(body=b"too much content"*100),
    lambda s: s.update(stream_error=True),
])
def test_download_refusal_closes_stream_and_leaves_no_package(tmp_path, change):
    row, body, headers = fixture_row()
    reader, transport = reader_for(row, body, headers, get_change=change)
    with pytest.raises(ObjectBackupError) as caught: capture(tmp_path, row, reader)
    assert "secret" not in str(caught.value)
    assert all(item.closed for item in transport.responses)
    assert_no_bundle(tmp_path)


@pytest.mark.parametrize("status", [404, 412, 429, 500])
def test_transport_error_has_no_blind_retry_or_sdk_details(tmp_path, status):
    row, body, headers = fixture_row()
    reader, transport = reader_for(row, body, headers, status=status,
        get_change=lambda s: s.update(body=b"<Error><Code>SyntheticFailure</Code><Message>synthetic-secret</Message></Error>"))
    with pytest.raises(ObjectBackupError, match="^object_download_unavailable$"):
        capture(tmp_path, row, reader)
    assert len(transport.calls) == 2
    assert_no_bundle(tmp_path)


@pytest.mark.parametrize("change", [
    lambda r: setattr(r, "status", "deleted"),
    lambda r: setattr(r, "status", "quarantined"),
    lambda r: setattr(r, "storage_key", "../../sensitive"),
    lambda r: setattr(r, "created_at", datetime.now()),
    lambda r: r.metadata_jsonb.update(provider="unknown"),
    lambda r: r.metadata_jsonb.pop("completion"),
    lambda r: r.metadata_jsonb["completion"].update(etag_sha256="malformed"),
    lambda r: r.metadata_jsonb["completion"].update(verified_at="2026-09-21T10:00:00"),
])
def test_invalid_catalog_blocks_before_any_object_request(tmp_path, change):
    row, body, headers = fixture_row()
    reader, transport = reader_for(row, body, headers)
    change(row)
    with pytest.raises(ObjectBackupError): capture(tmp_path, row, reader)
    assert transport.calls == []
    assert_no_bundle(tmp_path)


def test_pending_intent_retained_but_never_downloaded(tmp_path):
    row, body, headers = fixture_row(pending=True)
    reader, transport = reader_for(row, body, headers)
    manifest = capture(tmp_path, row, reader)
    assert transport.calls == [] and manifest["objects"] == []
    assert manifest["catalog"][0]["object_archived"] is False
    assert "completion" not in manifest["catalog"][0]["metadata"]


def test_duplicate_and_budget_fail_before_network(tmp_path):
    row, body, headers = fixture_row()
    reader, transport = reader_for(row, body, headers)
    with pytest.raises(ObjectBackupError, match="duplicate_file_catalog"):
        capture_objects([row, row], reader, tmp_path/"objects.tar", max_total_bytes=999)
    with pytest.raises(ObjectBackupError, match="object_budget_exceeded"):
        capture_objects([row], reader, tmp_path/"objects.tar", max_total_bytes=1)
    assert transport.calls == []
    assert_no_bundle(tmp_path)


def test_existing_backup_and_symlink_are_never_overwritten(tmp_path):
    row, body, headers = fixture_row()
    reader, transport = reader_for(row, body, headers)
    existing = tmp_path/"objects.tar"
    existing.write_bytes(b"old complete backup")
    with pytest.raises(ObjectBackupError, match="already_exists"): capture(tmp_path, row, reader)
    assert existing.read_bytes() == b"old complete backup" and transport.calls == []
    alias = tmp_path/"alias.tar"
    alias.symlink_to(existing)
    with pytest.raises(ObjectBackupError, match="already_exists"):
        capture_objects([row], reader, alias, max_total_bytes=999)
    assert existing.read_bytes() == b"old complete backup"


def test_competing_publication_is_retained(tmp_path, monkeypatch):
    import formal_object_backup as module
    row, body, headers = fixture_row()
    reader, _ = reader_for(row, body, headers)
    original = module.os.link
    def compete(source, destination):
        destination.write_bytes(b"competing completed bundle")
        return original(source, destination)
    monkeypatch.setattr(module.os, "link", compete)
    with pytest.raises(ObjectBackupError, match="already_exists"): capture(tmp_path, row, reader)
    assert (tmp_path/"objects.tar").read_bytes() == b"competing completed bundle"
    assert len(list(tmp_path.iterdir())) == 1


def test_two_objects_second_failure_cleans_first_private_staging(tmp_path):
    # first request is valid; second HEAD fails after first object's bytes staged.
    (first, body, headers), (second, _, _) = sorted(
        [fixture_row(), fixture_row()], key=lambda fixture: fixture[0].id.hex)
    reader, transport = reader_for(first, body, headers)
    transport.specs.append(dict(headers={}, status=404, body=b"<Error><Code>NoSuchKey</Code></Error>"))
    with pytest.raises(ObjectBackupError, match="object_head_unavailable"):
        capture_objects([second, first], reader, tmp_path/"objects.tar", max_total_bytes=999)
    assert len(transport.calls) == 3
    assert_no_bundle(tmp_path)


def test_installed_sdk_132_original_header_config_does_not_bind_condition():
    from importlib.metadata import version
    assert version("alibabacloud-oss-v2") == "1.3.2"
    row, body, headers = fixture_row()
    transport = Transport([dict(headers=headers, body=body)])
    config = oss.Config(region="cn-hangzhou", signature_version="v4", http_client=transport,
        credentials_provider=oss.credentials.StaticCredentialsProvider("SYNTHETIC_TEST_ID", "synthetic-not-a-live-secret"),
        retry_max_attempts=1, additional_headers=["if-match", "accept-encoding"])
    result = oss.Client(config).get_object(oss.GetObjectRequest(bucket="synthetic-backup", key=row.storage_key,
        if_match=headers["ETag"], accept_encoding="identity"))
    result.body.close()
    assert transport.calls[0]["signed_condition"] is False


def test_backup_signer_rejects_write_before_transport():
    row, body, headers = fixture_row()
    reader, transport = reader_for(row, body, headers)
    with pytest.raises(Exception):
        reader.client.put_object(oss.PutObjectRequest(bucket=reader.bucket, key=row.storage_key, body=body))
    assert transport.calls == []


def test_environment_factory_uses_same_verified_signer_and_tls(tmp_path, monkeypatch):
    import formal_object_backup as module
    row, body, headers = fixture_row()
    transport = Transport([dict(headers=headers), dict(headers=headers, body=body)])
    provider = oss.credentials.StaticCredentialsProvider("SYNTHETIC_TEST_ID", "synthetic-not-a-live-secret")
    monkeypatch.setattr(oss.credentials, "EnvironmentVariableCredentialsProvider", lambda: provider)
    real_client = module.sdk_client
    def inspect(config):
        assert config.signature_version == "v4" and config.region == "cn-hangzhou"
        assert config.retry_max_attempts == 1
        assert config.disable_ssl is False and config.insecure_skip_verify is False and config.enabled_redirect is False
        assert config.connect_timeout == 5 and config.readwrite_timeout == 30
        assert config.endpoint is None and config.credentials_provider is provider
        config.http_client = transport
        return real_client(config)
    monkeypatch.setattr(module, "sdk_client", inspect)
    reader = module.OssBackupReader.from_environment(region="cn-hangzhou", bucket="synthetic-backup")
    capture(tmp_path, row, reader)
    assert transport.calls[1]["signed_condition"]


def test_source_orm_mutation_after_freezing_cannot_change_bundle_catalog(tmp_path):
    row, body, headers = fixture_row()
    reader, transport = reader_for(row, body, headers)
    original = transport.send
    def change_source(request, **kwargs):
        row.metadata_jsonb["completion"]["head_manifest_sha256"] = "0"*64
        row.status = "pending"
        return original(request, **kwargs)
    transport.send = change_source
    result = capture(tmp_path, row, reader)
    assert result["catalog"][0]["status"] == "available"
    assert result["catalog"][0]["metadata"]["completion"]["head_manifest_sha256"] != "0"*64


def test_pending_with_completion_is_invalid_and_empty_catalog_is_explicit(tmp_path):
    row, body, headers = fixture_row()
    row.status = "pending"
    reader, transport = reader_for(row, body, headers)
    with pytest.raises(ObjectBackupError, match="invalid_file_catalog"): capture(tmp_path, row, reader)
    result = capture_objects([], reader, tmp_path/"objects.tar", max_total_bytes=0)
    assert result["objects"] == [] and result["catalog"] == [] and transport.calls == []
    assert result["database_snapshot_bound"] is False
