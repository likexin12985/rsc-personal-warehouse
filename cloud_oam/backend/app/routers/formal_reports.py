"""Gated formal inventory-report application, status and private download."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import get_formal_principal
from ..formal_access import FormalPrincipal
from ..formal_services import inventory_report_jobs, inventory_report_download
from ..formal_services.file_storage import FileStorageAdapter, FileStorageError
from ..formal_services.formal_files import FormalFileError
from ..formal_services.inventory_query import InventoryReadError
from .formal_files import get_formal_file_storage_adapter


router = APIRouter(prefix="/v1/reports/inventory-balances", tags=["formal-reports"])
_SAFETY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class InventoryReportJobOut(BaseModel):
    job_id: UUID
    status: str
    replayed: bool = False


class InventoryReportStatusOut(BaseModel):
    job_id: UUID
    status: str
    created_at: datetime
    completed_at: datetime | None
    download_count: int
    file_available: bool


class InventoryReportDownloadOut(BaseModel):
    job_id: UUID
    file_id: UUID
    url: str
    expires_at: datetime
    download_count: int


class InventoryReportCapabilityOut(BaseModel):
    available: bool


@router.get("/exports/capabilities", response_model=InventoryReportCapabilityOut)
def inventory_report_capabilities(
    response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal),
    settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
):
    # A capability hint is not authorization; every report operation rechecks
    # the current principal, report grant, stock scope, and storage state.
    _ = principal
    _no_store(response)
    return InventoryReportCapabilityOut(available=bool(
        settings.inventory_report_export_enabled
        and settings.file_storage_configuration_ready()
        and storage is not None
        and storage.provider_code == "aliyun_oss_v2"
    ))


@router.post("/exports", response_model=InventoryReportJobOut, status_code=status.HTTP_202_ACCEPTED)
def request_inventory_report(
    response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    _require_enabled(settings, storage)
    try:
        job, replayed = inventory_report_jobs.create_inventory_report_job(
            db,
            actor=principal,
            idempotency_key=idempotency_key or "",
            request_id=request_id or "",
        )
        output = InventoryReportJobOut(job_id=job.id, status=job.status, replayed=replayed)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _no_store(response)
    return output


@router.get("/exports/recovery", response_model=InventoryReportStatusOut)
def recover_inventory_report(
    response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    _require_enabled(settings, storage)
    try:
        result = inventory_report_download.recover_inventory_report_job_status(
            db, actor=principal, idempotency_key=idempotency_key or "",
        )
        output = InventoryReportStatusOut(
            job_id=result.job_id,
            status=result.status,
            created_at=result.created_at,
            completed_at=result.completed_at,
            download_count=result.download_count,
            file_available=result.file_available,
        )
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _no_store(response)
    return output


@router.get("/exports/{job_id}", response_model=InventoryReportStatusOut)
def inventory_report_status(
    job_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
):
    _require_enabled(settings, storage)
    try:
        result = inventory_report_download.get_inventory_report_job_status(
            db, actor=principal, job_id=job_id,
        )
        output = InventoryReportStatusOut(
            job_id=result.job_id,
            status=result.status,
            created_at=result.created_at,
            completed_at=result.completed_at,
            download_count=result.download_count,
            file_available=result.file_available,
        )
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _no_store(response)
    return output


@router.post("/exports/{job_id}/download-intents", response_model=InventoryReportDownloadOut)
def inventory_report_download_intent(
    job_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_storage = _require_enabled(settings, storage)
    try:
        result = inventory_report_download.create_inventory_report_download_intent(
            db,
            actor=principal,
            job_id=job_id,
            storage=checked_storage,
            request_id=request_id or "",
            ttl_seconds=settings.file_download_intent_ttl_seconds,
        )
        output = InventoryReportDownloadOut(
            job_id=result.job_id,
            file_id=result.file_id,
            url=result.url,
            expires_at=result.expires_at,
            download_count=result.download_count,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _no_store(response)
    return output


def _require_enabled(settings: Settings, storage: FileStorageAdapter | None) -> FileStorageAdapter:
    if (
        not settings.inventory_report_export_enabled
        or not settings.file_storage_configuration_ready()
        or storage is None
        or storage.provider_code != "aliyun_oss_v2"
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "inventory_report_export_disabled", "message": "库存报表导出尚未安全启用"},
            headers=_SAFETY_HEADERS,
        )
    return storage


def _rollback_and_raise(db: Session, exc: Exception) -> None:
    db.rollback()
    if isinstance(exc, InventoryReadError):
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail(), headers=_SAFETY_HEADERS) from None
    if isinstance(exc, FormalFileError):
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail(), headers=_SAFETY_HEADERS) from None
    code = "inventory_report_database_unavailable" if isinstance(exc, SQLAlchemyError) else "inventory_report_operation_unavailable"
    if isinstance(exc, FileStorageError):
        code = "inventory_report_storage_unavailable"
    raise HTTPException(
        status_code=503,
        detail={"code": code, "message": "库存报表服务暂不可用"},
        headers=_SAFETY_HEADERS,
    ) from None


def _no_store(response: Response) -> None:
    for name, value in _SAFETY_HEADERS.items():
        response.headers[name] = value
