"""Candidate: verified OSS object subset, not a complete database backup.

The caller must supply files from the same exported snapshot as its database dump.
This module neither queries a database nor writes to OSS. The outer coordinator,
restore policy, dedicated cloud identity and real transport acceptance are pending.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tarfile
from tempfile import TemporaryDirectory

from formal_file_integrity import FileObject, StoredObjectHead
from formal_file_integrity import (
    _head_manifest_sha256, _validate_file_row, _validate_intent_metadata,
    _validate_object_head,
)


class ObjectBackupError(RuntimeError):
    """Only a stable code is exposed; SDK URLs, credentials and bodies are private."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _refuse(code):
    raise ObjectBackupError(code)


def _text(value, *, maximum=1024):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _refuse("invalid_object_header")
    return value


def sdk_client(config):
    import alibabacloud_oss_v2 as oss
    from alibabacloud_oss_v2.signer import SignerV4

    class BoundReadSigner(SignerV4):
        def sign(self, context):
            if context.request.method not in {"HEAD", "GET"}:
                _refuse("backup_reader_write_forbidden")
            # SDK 1.3.2 does not transfer Config.additional_headers into the
            # ordinary request SigningContext. Use its signer injection point;
            # retain the original V4 algorithm and do not patch installed SDK.
            context.additional_headers = set(context.additional_headers or ()) | {"if-match", "accept-encoding"}
            super().sign(context)

    return oss.Client(config, signer=BoundReadSigner())


class OssBackupReader:
    """Use SDK dependency injection for offline transport tests, never presigned URLs."""

    def __init__(self, client, *, region, bucket):
        if re.fullmatch(r"[a-z][a-z0-9-]{1,62}", region or "") is None or re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket or "") is None:
            _refuse("invalid_storage_coordinates")
        self.client, self.region, self.bucket = client, region, bucket

    @classmethod
    def from_environment(cls, *, region, bucket):
        import alibabacloud_oss_v2 as oss
        # Caller supplies the dedicated read-only backup role environment.
        cls(None, region=region, bucket=bucket)
        try:
            config = oss.Config(region=region, signature_version="v4",
                credentials_provider=oss.credentials.EnvironmentVariableCredentialsProvider(),
                disable_ssl=False, insecure_skip_verify=False, enabled_redirect=False,
                retry_max_attempts=1, connect_timeout=5, readwrite_timeout=30,
                additional_headers=["if-match", "accept-encoding"])
            return cls(sdk_client(config), region=region, bucket=bucket)
        except Exception:
            raise ObjectBackupError("object_reader_unavailable") from None

    def head(self, key):
        import alibabacloud_oss_v2 as oss
        try:
            return self.client.head_object(oss.HeadObjectRequest(bucket=self.bucket, key=key))
        except Exception:
            raise ObjectBackupError("object_head_unavailable") from None

    @contextmanager
    def download(self, key, *, etag, version_id):
        import alibabacloud_oss_v2 as oss
        result = None
        try:
            result = self.client.get_object(oss.GetObjectRequest(bucket=self.bucket, key=key,
                if_match=etag, version_id=version_id, accept_encoding="identity"))
            yield result
        except ObjectBackupError:
            raise
        except Exception:
            raise ObjectBackupError("object_download_unavailable") from None
        finally:
            if result is not None and result.body is not None:
                try:
                    result.body.close()
                except Exception:
                    raise ObjectBackupError("object_stream_close_failed") from None


def _verified_head(row, result):
    headers = {str(key).lower(): str(value) for key, value in result.headers.items()}
    # HEAD's typed result omits Content-Range; also inspect raw response headers.
    if result.status_code != 200 or "content-range" in headers or headers.get("x-oss-delete-marker", "false") != "false" or headers.get("content-encoding", "identity") != "identity":
        _refuse("object_response_not_complete")
    etag = _text(result.etag, maximum=300)
    # Reject unquoted/multiple ETags before placing the value in If-Match.
    if re.fullmatch(r'"[^"\x00-\x20\x7f]+"', etag) is None:
        _refuse("invalid_object_etag")
    head = StoredObjectHead(storage_key=row.storage_key, size_bytes=result.content_length,
        mime_type=result.content_type, metadata=result.metadata, etag=etag)
    try:
        _validate_object_head(row, head)
        completion = row.metadata_jsonb["completion"]
        if completion["etag_sha256"] != sha256(etag.encode()).hexdigest() or completion["head_manifest_sha256"] != _head_manifest_sha256(head):
            _refuse("object_changed_since_completion")
    except ObjectBackupError:
        raise
    except Exception:
        raise ObjectBackupError("object_metadata_mismatch") from None
    version = getattr(result, "version_id", None)
    if version is not None:
        _text(version)
    encryption = {}
    for key in ("server_side_encryption", "server_side_data_encryption", "server_side_encryption_key_id"):
        value = getattr(result, key, None)
        if value is not None:
            encryption[key] = _text(value)
    metadata = {}
    for key, value in head.metadata.items():
        key = str(key).lower()
        if key.startswith("x-oss-meta-"): key = key[len("x-oss-meta-"):]
        metadata[key] = str(value)
    return {"etag": etag, "version_id": version, "encryption": encryption, "metadata": metadata}


def _catalog(rows):
    frozen, manifest, ids, keys = [], [], set(), set()
    for source in rows:
        row = FileObject(id=source.id, storage_key=source.storage_key, sha256=source.sha256,
            size_bytes=source.size_bytes, mime_type=source.mime_type,
            original_filename=source.original_filename, uploaded_by=source.uploaded_by,
            status=source.status, metadata_jsonb=deepcopy(source.metadata_jsonb), created_at=source.created_at)
        try:
            _validate_file_row(row)
            _validate_intent_metadata(row, allow_completed=True)
        except Exception:
            raise ObjectBackupError("invalid_file_catalog") from None
        if row.metadata_jsonb["provider"] != "aliyun_oss_v2":
            _refuse("unsupported_object_provider")
        # A retention/restore rule is required before these states can be archived.
        if row.status not in {"pending", "available"}:
            _refuse("unsupported_file_retention_state")
        if row.id in ids or row.storage_key in keys:
            _refuse("duplicate_file_catalog")
        ids.add(row.id); keys.add(row.storage_key)
        if row.created_at is None or row.created_at.tzinfo is None:
            _refuse("invalid_file_catalog")
        frozen.append(row)
        manifest.append(dict(file_id=str(row.id), storage_key=row.storage_key, sha256=row.sha256,
            size_bytes=row.size_bytes, mime_type=row.mime_type, original_filename=row.original_filename,
            uploaded_by=row.uploaded_by, status=row.status, metadata=deepcopy(row.metadata_jsonb),
            created_at=row.created_at.isoformat(), object_archived=row.status == "available"))
    return sorted(frozen, key=lambda row: row.id.hex), sorted(manifest, key=lambda row: row["file_id"])


def _write(path, data):
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        output.write(data); output.flush(); os.fsync(output.fileno())


def capture_objects(rows, reader, destination: Path, *, max_total_bytes):
    """Atomically publish one exclusive private tar; no final output on validation failure.

    This is only an object bundle. It does not assert database-snapshot completeness,
    cloud restore success, key availability or a completed joint backup.
    """
    if type(max_total_bytes) is not int or max_total_bytes < 0:
        _refuse("invalid_object_budget")
    frozen, catalog = _catalog(rows)
    if sum(row.size_bytes for row in frozen if row.status == "available") > max_total_bytes:
        _refuse("object_budget_exceeded")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        _refuse("object_bundle_already_exists")
    receipts = []
    with TemporaryDirectory(prefix=".formal-object-backup-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        for row in frozen:
            if row.status == "pending":
                continue
            observed = _verified_head(row, reader.head(row.storage_key))
            with reader.download(row.storage_key, etag=observed["etag"], version_id=observed["version_id"]) as result:
                if _verified_head(row, result) != observed:
                    _refuse("object_changed_during_download")
                if result.body is None:
                    _refuse("object_body_missing")
                length, digest = 0, sha256()
                path = staging / row.id.hex
                with path.open("xb") as output:
                    os.chmod(path, 0o600)
                    for chunk in result.body.iter_bytes(block_size=64 * 1024):
                        if not isinstance(chunk, bytes):
                            _refuse("invalid_object_stream")
                        length += len(chunk)
                        if length > row.size_bytes:
                            _refuse("object_length_mismatch")
                        digest.update(chunk); output.write(chunk)
                    if length != row.size_bytes or digest.hexdigest() != row.sha256:
                        _refuse("object_content_mismatch")
                    output.flush(); os.fsync(output.fileno())
            receipts.append(dict(file_id=str(row.id), archive_member=row.id.hex,
                sha256=digest.hexdigest(), size_bytes=length, **observed))
        manifest = dict(schema="cloud_oam.formal_object_backup_candidate.v1",
            provider="aliyun_oss_v2", region=reader.region, bucket=reader.bucket,
            catalog=catalog, catalog_sha256=sha256(canonical(catalog)).hexdigest(),
            objects=receipts, database_snapshot_bound=False)
        _write(staging / "manifest.json", canonical(manifest) + b"\n")
        bundle = staging / "bundle.tar"
        with bundle.open("xb") as output:
            os.chmod(bundle, 0o600)
            with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name in ["manifest.json", *[entry["archive_member"] for entry in receipts]]:
                    archive.add(staging / name, arcname=name, recursive=False)
            output.flush(); os.fsync(output.fileno())
        # link creates a final pathname exclusively, even if a competing job arrived.
        try:
            os.link(bundle, destination)
        except FileExistsError:
            raise ObjectBackupError("object_bundle_already_exists") from None
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return manifest
