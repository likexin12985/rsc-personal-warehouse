"""Private object-storage boundary for formal file intents.

The production implementation imports Alibaba Cloud OSS SDK V2 lazily and
uses only its environment credential provider.  No access-key setting or
plaintext credential fallback exists in the application configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol


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


@dataclass(frozen=True, slots=True)
class StoredObjectHead:
    storage_key: str
    size_bytes: int
    mime_type: str
    metadata: Mapping[str, str]
    etag: str


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

    def create_download_intent(
        self,
        *,
        storage_key: str,
        ttl_seconds: int,
    ) -> DownloadIntent: ...


class AliyunOssV2StorageAdapter:
    """Alibaba Cloud OSS SDK V2 adapter using V4 presigned requests."""

    provider_code = "aliyun_oss_v2"

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
