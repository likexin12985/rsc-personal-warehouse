"""Private object-storage boundary for formal file intents.

The production implementation imports Alibaba Cloud OSS SDK V2 lazily and
uses only its environment credential provider.  No access-key setting or
plaintext credential fallback exists in the application configuration.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol
import uuid

from formal_file_integrity import StoredObjectHead


class FileStorageError(RuntimeError):
    """Stable adapter failure whose original SDK detail must not reach HTTP."""


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

    def __init__(self, *, region: str, bucket: str) -> None:
        checked_region = region.strip()
        checked_bucket = bucket.strip()
        if not checked_region or not checked_bucket:
            raise FileStorageError("OSS storage coordinates are incomplete")
        try:
            import alibabacloud_oss_v2 as oss

            credentials_provider = (
                oss.credentials.EnvironmentVariableCredentialsProvider()
            )
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
            result = self._client.presign(
                request,
                self._oss.PresignOptions(
                    expires=timedelta(seconds=ttl_seconds),
                ),
            )
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
                expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=ttl_seconds),
                headers=required,
            )
        except FileStorageError:
            raise
        except Exception as exc:  # pragma: no cover - deployment-only adapter
            raise FileStorageError("OSS upload intent is unavailable") from exc

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

    def put_report_object(
        self, *, storage_key: str, file_id: str, sha256: str, payload: bytes
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
            f"formal-files/v1/inventory_report_export/{identifier.hex[:2]}/"
            f"{identifier.hex}"
        )
        if (
            storage_key != expected_key
            or not isinstance(payload, bytes)
            or not 1 <= len(payload) <= self._REPORT_MAX_BYTES
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
                )
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

    def create_download_intent(
        self,
        *,
        storage_key: str,
        ttl_seconds: int,
    ) -> DownloadIntent:
        try:
            result = self._client.presign(
                self._oss.GetObjectRequest(
                    bucket=self._bucket,
                    key=storage_key,
                ),
                self._oss.PresignOptions(
                    expires=timedelta(seconds=ttl_seconds),
                ),
            )
            return DownloadIntent(
                storage_key=storage_key,
                url=str(result.url),
                expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=ttl_seconds),
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
