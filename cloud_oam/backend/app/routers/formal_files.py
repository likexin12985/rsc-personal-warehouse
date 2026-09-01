"""HTTP composition root for the formal private-file intent API."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import get_formal_principal
from ..formal_access import FormalPrincipal
from ..formal_file_schemas import (
    FileCompleteOut,
    FileDownloadIntentOut,
    FileUploadIntentIn,
    FileUploadIntentOut,
    PresignedDownloadOut,
    PresignedUploadOut,
)
from ..formal_services import formal_files as file_service
from ..formal_services.file_storage import (
    AliyunOssV2StorageAdapter,
    FileStorageAdapter,
    FileStorageError,
)


router = APIRouter(prefix="/v1/files", tags=["formal-files"])
_SAFETY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@lru_cache(maxsize=8)
def _production_storage_adapter(
    provider: str,
    region: str,
    bucket: str,
) -> FileStorageAdapter | None:
    if provider != "aliyun_oss_v2":
        return None
    try:
        return AliyunOssV2StorageAdapter(region=region, bucket=bucket)
    except FileStorageError:
        return None


def get_formal_file_storage_adapter(
    runtime_settings: Settings = Depends(get_settings),
) -> FileStorageAdapter | None:
    """Deployment composition point; disabled/misconfigured means no adapter."""

    if not runtime_settings.file_storage_configuration_ready():
        return None
    return _production_storage_adapter(
        runtime_settings.file_storage_provider,
        runtime_settings.file_storage_region.strip(),
        runtime_settings.file_storage_bucket.strip(),
    )


@router.post(
    "/upload-intents",
    response_model=FileUploadIntentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_formal_file_upload_intent(
    payload: FileUploadIntentIn,
    response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_storage = _require_runtime(runtime_settings, storage)
    try:
        result = file_service.create_file_upload_intent(
            db,
            actor=principal,
            command=file_service.FileUploadIntentInput(
                purpose=payload.purpose,
                original_filename=payload.original_filename,
                size_bytes=payload.size_bytes,
                mime_type=payload.mime_type,
                sha256=payload.sha256,
            ),
            idempotency_key=idempotency_key or "",
            idempotency_hmac_secret=runtime_settings.file_idempotency_hmac_secret,
            trace_request_id=request_id or "",
            storage=checked_storage,
            upload_ttl_seconds=runtime_settings.file_upload_intent_ttl_seconds,
            maximum_size_bytes=runtime_settings.max_upload_bytes,
        )
        output = FileUploadIntentOut(
            file_id=result.file_id,
            purpose=result.purpose,
            status=result.status,
            upload=(
                PresignedUploadOut(
                    url=result.upload.url,
                    expires_at=result.upload.expires_at,
                    headers=dict(result.upload.headers),
                )
                if result.upload is not None
                else None
            ),
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_safety_headers(response)
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return output


@router.post("/{file_id}/complete", response_model=FileCompleteOut)
def complete_formal_file_upload(
    file_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_storage = _require_runtime(runtime_settings, storage)
    try:
        result = file_service.complete_file_upload(
            db,
            actor=principal,
            file_id=file_id,
            trace_request_id=request_id or "",
            storage=checked_storage,
        )
        output = FileCompleteOut(
            file_id=result.file_id,
            purpose=result.purpose,
            verified_at=result.verified_at,
            already_available=result.already_available,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_safety_headers(response)
    return output


@router.get("/{file_id}/download-intent", response_model=FileDownloadIntentOut)
def create_formal_file_download_intent(
    file_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_storage = _require_runtime(runtime_settings, storage)
    try:
        result = file_service.create_file_download_intent(
            db,
            actor=principal,
            file_id=file_id,
            trace_request_id=request_id or "",
            storage=checked_storage,
            download_ttl_seconds=runtime_settings.file_download_intent_ttl_seconds,
        )
        output = FileDownloadIntentOut(
            file_id=result.file_id,
            purpose=result.purpose,
            download=PresignedDownloadOut(
                url=result.download.url,
                expires_at=result.download.expires_at,
            ),
        )
        # Download grants are security facts, so this GET commits exactly the
        # immutable audit event written by the service and nothing else.
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_safety_headers(response)
    return output


def _require_runtime(
    settings: Settings,
    storage: FileStorageAdapter | None,
) -> FileStorageAdapter:
    if (
        not settings.file_storage_configuration_ready()
        or storage is None
        or getattr(storage, "provider_code", None) != settings.file_storage_provider
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "file_storage_disabled",
                "category": "service_unavailable",
                "message": "正式文件服务尚未安全启用",
            },
            headers=_SAFETY_HEADERS,
        )
    return storage


def _rollback_and_raise(db: Session, exc: Exception) -> None:
    db.rollback()
    if isinstance(exc, file_service.FormalFileError):
        raise HTTPException(
            status_code=exc.http_status_code,
            detail=exc.as_detail(),
            headers=_SAFETY_HEADERS,
        ) from None
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (DBAPIError, SQLAlchemyError)):
        code = "file_database_unavailable"
    else:
        code = "file_operation_unavailable"
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": code,
            "category": "service_unavailable",
            "message": "文件服务暂不可用",
        },
        headers=_SAFETY_HEADERS,
    ) from None


def _set_safety_headers(response: Response) -> None:
    for key, value in _SAFETY_HEADERS.items():
        response.headers[key] = value


__all__ = [
    "get_formal_file_storage_adapter",
    "router",
]
