"""Private object-storage boundary for formal file intents.

The implementation imports Alibaba Cloud OSS SDK V2 lazily. An explicitly
composed OIDC provider preserves temporary credential expiration; the existing
environment path remains available for separately reviewed legacy deployments.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Mapping, Protocol
import uuid

from formal_file_integrity import OPENING_IMPORT_ERROR_MAX_BYTES, StoredObjectHead
from ..oss_runtime_credentials import OssOidcCredentialsProvider


class FileStorageError(RuntimeError):
    """Stable adapter failure whose original SDK detail must not reach HTTP."""


_PUBLIC_FAILURES = frozenset({
    "OSS storage coordinates are incomplete", "OSS credential provider is invalid",
    "OSS storage adapter is unavailable", "OSS signature lifetime exceeds credential lifetime",
    "OSS signature lifetime is unavailable", "OSS upload signature omitted a required bound header",
    "OSS upload intent is unavailable", "OSS object verification is unavailable",
    "opening count source identity is invalid", "opening count source binding is invalid",
    "opening count source HEAD verification failed", "opening count source GET verification failed",
    "opening count source stream is invalid", "opening count source exceeds declared size",
    "opening count source content verification failed", "OSS opening count source is unavailable",
    "OSS opening count source close failed", "report object identity is invalid",
    "report object content or key is invalid", "OSS report object verification failed",
    "OSS download intent is unavailable",
})


def _public_storage_boundary(fallback: str):
    """Drop SDK exception graphs, including implicit context, at public exits.

    Existing validation messages remain fixed. Arbitrary SDK text (even an
    exception presented as FileStorageError) is never copied into the result.
    The original operation and its exact-HEAD recovery run once, unchanged.
    """
    def decorate(action):
        @wraps(action)
        def protected(*args, **kwargs):
            message = fallback
            try:
                return action(*args, **kwargs)
            except Exception as error:
                if (type(error) is FileStorageError and len(error.args) == 1
                        and type(error.args[0]) is str and error.args[0] in _PUBLIC_FAILURES):
                    message = error.args[0]
            # Raising outside the handler is necessary: ``from None`` alone
            # still retains the credential-bearing exception in __context__.
            raise FileStorageError(message)
        return protected
    return decorate


@dataclass(frozen=True, slots=True)
class UploadIntent:
    storage_key: str
    url: str
    expires_at: datetime
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DownloadIntent:
    storage_key: str
    url: str
    expires_at: datetime


class FileStorageAdapter(Protocol):
    provider_code: str

    def create_upload_intent(
        self,
        *,
        storage_key: str,
        file_id: str,
        sha256: str,
        size_bytes: int,
        mime_type: str,
        ttl_seconds: int,
    ) -> UploadIntent: ...

    def head_object(self, *, storage_key: str) -> StoredObjectHead: ...

    def read_opening_count_source(
        self, *, storage_key: str, file_id: str, sha256: str, size_bytes: int
    ) -> bytes: ...

    def put_report_object(
        self, *, storage_key: str, file_id: str, sha256: str, payload: bytes
    ) -> StoredObjectHead: ...

    def put_opening_count_error(
        self, *, storage_key: str, file_id: str, sha256: str, payload: bytes
    ) -> StoredObjectHead: ...

    def create_download_intent(
        self,
        *,
        storage_key: str,
        ttl_seconds: int,
    ) -> DownloadIntent: ...


class AliyunOssV2StorageAdapter:
    """Alibaba Cloud OSS SDK V2 adapter using V4 presigned requests."""

    provider_code = "aliyun_oss_v2"
    _REPORT_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    _REPORT_MAX_BYTES = 20 * 1024 * 1024
    _OPENING_COUNT_MAX_BYTES = 8 * 1024 * 1024

    @_public_storage_boundary("OSS storage adapter is unavailable")
    def __init__(self, *, region: str, bucket: str,
                 runtime_credentials: OssOidcCredentialsProvider | None = None) -> None:
        checked_region = region.strip()
        checked_bucket = bucket.strip()
        if not checked_region or not checked_bucket:
            raise FileStorageError("OSS storage coordinates are incomplete")
        if runtime_credentials is not None and type(runtime_credentials) is not OssOidcCredentialsProvider:
            raise FileStorageError("OSS credential provider is invalid")
        try:
            import alibabacloud_oss_v2 as oss

            credentials_provider = runtime_credentials
            if credentials_provider is None:
                credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
            config = oss.config.load_default()
            config.credentials_provider = credentials_provider
            config.region = checked_region
            # OSS SDK V2 signs requests with signature version V4.  Keep this
            # explicit so a future SDK default cannot silently weaken it.
            config.signature_version = "v4"
            client = oss.Client(config)
        except Exception as exc:  # pragma: no cover - deployment-only adapter
            raise FileStorageError("OSS storage adapter is unavailable") from exc
        self._oss = oss
        self._client = client
        self._bucket = checked_bucket
        self._region = checked_region
        self._runtime_credentials = runtime_credentials

    def _presign(self, request, ttl_seconds):
        if self._runtime_credentials is None:
            result = self._client.presign(request, expires=timedelta(seconds=ttl_seconds))
        else:
            provider, expiry = self._runtime_credentials.signing_snapshot(ttl_seconds)
            # SDK 1.3.2 presign ignores operation-level credentials_provider.
            # A dedicated client pins this signature's checked identity without
            # mutating the shared HEAD/PUT client during concurrent requests.
            config = self._oss.config.load_default()
            config.region = self._region
            config.signature_version = "v4"
            config.credentials_provider = provider
            result = self._oss.Client(config).presign(request, expiration=expiry)
            if result.expiration is None or result.expiration > expiry:
                raise FileStorageError("OSS signature lifetime exceeds credential lifetime")
        if (not isinstance(result.expiration, datetime) or result.expiration.tzinfo is None
                or result.expiration <= datetime.now(timezone.utc)):
            raise FileStorageError("OSS signature lifetime is unavailable")
        return result

    @_public_storage_boundary("OSS upload intent is unavailable")
    def create_upload_intent(
        self,
        *,
        storage_key: str,
        file_id: str,
        sha256: str,
        size_bytes: int,
        mime_type: str,
        ttl_seconds: int,
    ) -> UploadIntent:
        del size_bytes  # exact length is verified by HEAD before availability
        try:
            request = self._oss.PutObjectRequest(
                bucket=self._bucket,
                key=storage_key,
                content_type=mime_type,
                metadata={"sha256": sha256, "file-id": file_id},
                forbid_overwrite=True,
            )
            result = self._presign(request, ttl_seconds)
            signed_headers = {
                str(key): str(value)
                for key, value in dict(result.signed_headers or {}).items()
            }
            # Return the exact values the client must send.  These are also
            # required to be part of the V4 signed-header set by the service.
            required = {
                "Content-Type": mime_type,
                "x-oss-meta-sha256": sha256,
                "x-oss-meta-file-id": file_id,
                "x-oss-forbid-overwrite": "true",
            }
            lower_signed = {key.lower(): value for key, value in signed_headers.items()}
            for key, value in required.items():
                if lower_signed.get(key.lower()) != value:
                    raise FileStorageError(
                        "OSS upload signature omitted a required bound header"
                    )
            return UploadIntent(
                storage_key=storage_key,
                url=str(result.url),
                expires_at=result.expiration,
                headers=required,
            )
        except FileStorageError:
            raise
        except Exception as exc:  # pragma: no cover - deployment-only adapter
            raise FileStorageError("OSS upload intent is unavailable") from exc

    @_public_storage_boundary("OSS object verification is unavailable")
    def head_object(self, *, storage_key: str) -> StoredObjectHead:
        try:
            result = self._client.head_object(
                self._oss.HeadObjectRequest(
                    bucket=self._bucket,
                    key=storage_key,
                )
            )
            return StoredObjectHead(
                storage_key=storage_key,
                size_bytes=int(result.content_length),
                mime_type=str(result.content_type or ""),
                metadata={
                    str(key): str(value)
                    for key, value in dict(result.metadata or {}).items()
                },
                etag=str(result.etag or ""),
            )
        except Exception as exc:  # pragma: no cover - deployment-only adapter
            raise FileStorageError("OSS object verification is unavailable") from exc

    @_public_storage_boundary("OSS opening count source is unavailable")
    def read_opening_count_source(
        self, *, storage_key: str, file_id: str, sha256: str, size_bytes: int
    ) -> bytes:
        """Read one private XLSX source with an exact identity and byte cap.

        The caller must first authorize the file row and its stocktake scope.
        ETag pins GET to the HEAD version, and the content digest remains the
        final authority even if object metadata or the ETag is misleading.
        """

        try:
            identifier = uuid.UUID(file_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise FileStorageError("opening count source identity is invalid") from exc
        expected_key = (
            f"formal-files/v1/opening_count_import/{identifier.hex[:2]}/"
            f"{identifier.hex}"
        )
        if (
            storage_key != expected_key
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or not 1 <= size_bytes <= self._OPENING_COUNT_MAX_BYTES
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise FileStorageError("opening count source binding is invalid")
        head = self.head_object(storage_key=storage_key)
        metadata = {
            str(key).lower().removeprefix("x-oss-meta-"): str(value)
            for key, value in head.metadata.items()
        }
        if (
            head.storage_key != storage_key
            or head.size_bytes != size_bytes
            or head.mime_type != self._REPORT_MIME
            or metadata.get("sha256") != sha256
            or metadata.get("file-id") != str(identifier)
            or not head.etag
        ):
            raise FileStorageError("opening count source HEAD verification failed")
        body = None
        try:
            result = self._client.get_object(
                self._oss.GetObjectRequest(
                    bucket=self._bucket,
                    key=storage_key,
                    if_match=head.etag,
                    accept_encoding="identity",
                )
            )
            body = result.body
            if (
                body is None
                or result.content_length != size_bytes
                or result.content_type != self._REPORT_MIME
                or result.etag != head.etag
                or result.content_encoding not in (None, "", "identity")
            ):
                raise FileStorageError("opening count source GET verification failed")
            chunks: list[bytes] = []
            received = 0
            digest = hashlib.sha256()
            for chunk in body.iter_bytes(block_size=64 * 1024):
                if not isinstance(chunk, bytes):
                    raise FileStorageError("opening count source stream is invalid")
                received += len(chunk)
                if received > size_bytes:
                    raise FileStorageError("opening count source exceeds declared size")
                digest.update(chunk)
                chunks.append(chunk)
            if received != size_bytes or digest.hexdigest() != sha256:
                raise FileStorageError("opening count source content verification failed")
            return b"".join(chunks)
        except FileStorageError:
            raise
        except Exception as exc:
            raise FileStorageError("OSS opening count source is unavailable") from exc
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception as exc:
                    raise FileStorageError("OSS opening count source close failed") from exc

    @_public_storage_boundary("OSS report object verification failed")
    def put_report_object(
        self, *, storage_key: str, file_id: str, sha256: str, payload: bytes
    ) -> StoredObjectHead:
        return self._put_generated_workbook(storage_key=storage_key, file_id=file_id,
            sha256=sha256, payload=payload, purpose="inventory_report_export",
            maximum_bytes=self._REPORT_MAX_BYTES)

    @_public_storage_boundary("OSS report object verification failed")
    def put_opening_count_error(
        self, *, storage_key: str, file_id: str, sha256: str, payload: bytes
    ) -> StoredObjectHead:
        return self._put_generated_workbook(storage_key=storage_key, file_id=file_id,
            sha256=sha256, payload=payload, purpose="opening_count_import_error",
            maximum_bytes=OPENING_IMPORT_ERROR_MAX_BYTES)

    def _put_generated_workbook(
        self, *, storage_key: str, file_id: str, sha256: str, payload: bytes,
        purpose: str, maximum_bytes: int,
    ) -> StoredObjectHead:
        """Write once, or recover only an exactly matching prior single PUT.

        A timeout or lost acknowledgement never authorizes a second overwrite.
        The deterministic key and OSS forbid-overwrite flag make a HEAD reread
        sufficient only when size, metadata and single-PUT MD5 all match.
        """

        try:
            identifier = uuid.UUID(file_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise FileStorageError("report object identity is invalid") from exc
        expected_key = (
            f"formal-files/v1/{purpose}/{identifier.hex[:2]}/"
            f"{identifier.hex}"
        )
        if (
            storage_key != expected_key
            or not isinstance(payload, bytes)
            or not 1 <= len(payload) <= maximum_bytes
            or not isinstance(sha256, str)
            or hashlib.sha256(payload).hexdigest() != sha256
        ):
            raise FileStorageError("report object content or key is invalid")
        digest_md5 = hashlib.md5(payload).digest()
        digest_md5_hex = digest_md5.hex()
        try:
            self._client.put_object(
                self._oss.PutObjectRequest(
                    bucket=self._bucket,
                    key=storage_key,
                    body=payload,
                    content_type=self._REPORT_MIME,
                    content_length=len(payload),
                    content_md5=base64.b64encode(digest_md5).decode("ascii"),
                    metadata={"sha256": sha256, "file-id": str(identifier)},
                    forbid_overwrite=True,
                ),
                # SDK retries are writes too: a lost response permits HEAD
                # recovery only, never a second underlying PUT attempt.
                retry_max_attempts=1,
            )
        except Exception:
            # The PUT may have succeeded before the acknowledgement was lost.
            # Never replay it automatically; inspect the exact object instead.
            pass
        head = self.head_object(storage_key=storage_key)
        metadata = {
            str(key).lower().removeprefix("x-oss-meta-"): str(value)
            for key, value in head.metadata.items()
        }
        if (
            head.storage_key != storage_key
            or head.size_bytes != len(payload)
            or head.mime_type != self._REPORT_MIME
            or metadata.get("sha256") != sha256
            or metadata.get("file-id") != str(identifier)
            or head.etag.strip('"').lower() != digest_md5_hex
        ):
            raise FileStorageError("OSS report object verification failed")
        return head

    @_public_storage_boundary("OSS download intent is unavailable")
    def create_download_intent(
        self,
        *,
        storage_key: str,
        ttl_seconds: int,
    ) -> DownloadIntent:
        try:
            result = self._presign(
                self._oss.GetObjectRequest(
                    bucket=self._bucket,
                    key=storage_key,
                ),
                ttl_seconds,
            )
            return DownloadIntent(
                storage_key=storage_key,
                url=str(result.url),
                expires_at=result.expiration,
            )
        except Exception as exc:  # pragma: no cover - deployment-only adapter
            raise FileStorageError("OSS download intent is unavailable") from exc


__all__ = [
    "AliyunOssV2StorageAdapter",
    "DownloadIntent",
    "FileStorageAdapter",
    "FileStorageError",
    "StoredObjectHead",
    "UploadIntent",
]
