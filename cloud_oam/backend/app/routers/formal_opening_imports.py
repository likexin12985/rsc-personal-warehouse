"""Durable private opening imports; browser input never supplies preview proof."""

from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import SessionLocal
from ..dependencies import get_formal_principal
from ..formal_access import FormalPrincipal
from ..formal_file_schemas import PresignedDownloadOut, PresignedUploadOut
from ..foundation_models import FileJob, FileObject
from ..opening_stocktake_schemas import OpeningCountImportBusinessCheckIn
from ..formal_services import formal_files
from ..formal_services.file_storage import FileStorageAdapter
from ..formal_services.opening_count_import_document import load_import_binding
from ..formal_services.opening_count_import_download import create_opening_count_error_download
from ..formal_services.opening_count_import_review import read_opening_count_import_review
from ..formal_services.opening_count_import_management import read_opening_import_management_recovery
from ..formal_services.opening_count_import_intake import (
    create_opening_count_import_job, recover_opening_count_import_job, _request_id,
)
from ..formal_services.opening_count_import_jobs import (
    OpeningCountImportJobError, confirm_persisted_opening_count_import, read_opening_count_import_job,
)
from ..formal_services.opening_count_import_source import (
    OpeningCountImportSourceError, read_authorized_opening_count_source,
)
from ..formal_services.opening_stocktake_count import OpeningStocktakeCountError
from ..formal_services.opening_count_import_termination import FAILURE_CODES, cancel_opening_count_import
from .formal_files import get_formal_file_storage_adapter


router = APIRouter(prefix="/v1/stocktakes/opening/imports/opening-count", tags=["formal-opening-import"])
SAFETY_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache",
                  "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


def get_import_session_factory():
    return SessionLocal


class SourceUploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    original_filename: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=1, le=8 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceUploadOut(BaseModel):
    file_id: UUID
    status: Literal["pending", "available"]
    upload: PresignedUploadOut | None
    replayed: bool


class ImportSealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    actor_person_id: UUID
    authorization_version: int = Field(strict=True, ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(strict=True, ge=1, le=8388608)
    source_file_id: UUID | None = None


class ImportSealOut(BaseModel):
    schema_version: Literal["rsc.opening_import_seal.v1"]
    seal_id: UUID
    terminal_audit_id: UUID
    actor_person_id: UUID
    authorization_version: int
    reviewer_person_id: UUID
    reviewer_authorization_version: int
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    source_file_id: UUID
    source_sha256: str
    size_bytes: int
    permanent_nonexecution: Literal[True]
    automatic_retry_allowed: Literal[False]
    source_object_may_exist: Literal[True]


class ImportStatusOut(BaseModel):
    job_id: UUID
    status: Literal["queued", "prevalidating", "awaiting_confirmation", "running", "succeeded", "failed", "cancelled"]
    completion_id: UUID | None
    row_count: int | None = None
    error_count: int = 0
    error_file_available: bool = False
    failure_code: str | None = None
    replayed: bool = False


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImportReviewOut(BaseModel):
    job_id: UUID
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    actor_person_id: UUID
    authorization_version: int
    source_file_id: UUID
    source_sha256: str
    size_bytes: int
    original_filename: str
    can_confirm: bool
    result: ImportStatusOut


class ImportErrorDownloadOut(BaseModel):
    job_id: UUID
    file_id: UUID
    filename: str
    sha256: str
    size_bytes: int
    download: PresignedDownloadOut


class ImportManagementRecoveryOut(BaseModel):
    schema_version: Literal["rsc.opening_import_management_recovery.v1"]
    reviewer_person_id: UUID
    reviewer_authorization_version: int
    actor_person_id: UUID
    authorization_version: int
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    source_file_id: UUID
    source_sha256: str
    size_bytes: int
    job_id: UUID
    status: Literal["queued", "prevalidating", "awaiting_confirmation", "succeeded", "failed", "cancelled"]
    completion_id: UUID | None
    terminal_audit_id: UUID | None
    terminal_verified: bool
    automatic_retry_allowed: Literal[False]


def _enabled(settings, storage):
    return bool(settings.opening_count_import_enabled and settings.file_storage_configuration_ready()
                and settings.database_url.startswith("postgresql+psycopg://")
                and storage is not None and getattr(storage, "provider_code", None) == "aliyun_oss_v2")


def _require_enabled(settings, storage):
    if not _enabled(settings, storage):
        raise HTTPException(503, detail={"code": "opening_import_disabled",
            "message": "期初盘点导入尚未安全启用"}, headers=SAFETY_HEADERS)


def _raise(exc):
    if isinstance(exc, (OpeningCountImportJobError, OpeningCountImportSourceError, OpeningStocktakeCountError)):
        code, status, message = exc.code, exc.http_status_code, str(exc)
    elif isinstance(exc, formal_files.FormalFileError):
        code, status, message = exc.code, exc.http_status_code, str(exc)
    else:
        code, status, message = "opening_import_result_unknown", 503, "请求未获确定结果，请使用原请求键查询任务"
    raise HTTPException(status, detail={"code": code, "message": message}, headers=SAFETY_HEADERS) from None


def _status(db, *, actor, job_id, replayed=False):
    result = read_opening_count_import_job(db, actor=actor, job_id=job_id)
    row = db.get(FileJob, job_id)
    preview = row.import_preview_jsonb
    error_file = db.get(FileObject, row.error_file_id) if row.error_file_id is not None else None
    return ImportStatusOut(**asdict(result), replayed=replayed,
        row_count=preview.get("row_count") if preview else None,
        error_count=len(preview.get("errors", ())) if preview else 0,
        failure_code=row.error_detail if row.error_detail in FAILURE_CODES else None,
        error_file_available=bool(row.status == "failed" and error_file is not None
                                  and error_file.status == "available"))


@router.get("/capabilities")
def import_capabilities(response: Response, principal: FormalPrincipal = Depends(get_formal_principal),
    settings: Settings = Depends(get_settings), storage=Depends(get_formal_file_storage_adapter)):
    response.headers.update(SAFETY_HEADERS)
    return {"available": _enabled(settings, storage)}


@router.post("/source-upload-intents", response_model=SourceUploadOut, status_code=201)
def request_source_upload(payload: SourceUploadIn, response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            result = formal_files.create_file_upload_intent(db, actor=principal,
                command=formal_files.FileUploadIntentInput(purpose="opening_count_import",
                    original_filename=payload.original_filename, size_bytes=payload.size_bytes,
                    sha256=payload.sha256, mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                idempotency_key=idempotency_key or "", trace_request_id=request_id or "",
                idempotency_hmac_secret=settings.file_idempotency_hmac_secret,
                storage=storage, upload_ttl_seconds=settings.file_upload_intent_ttl_seconds,
                maximum_size_bytes=8 * 1024 * 1024)
            output = SourceUploadOut(file_id=result.file_id, status=result.status, replayed=result.replayed,
                upload=PresignedUploadOut(url=result.upload.url, expires_at=result.upload.expires_at,
                    headers=dict(result.upload.headers)) if result.upload else None)
            db.commit()
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.post("/sources/{file_id}/complete")
def complete_source(file_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            row = db.get(FileObject, file_id)
            if row is None or row.metadata_jsonb.get("purpose") != "opening_count_import":
                raise OpeningCountImportJobError("opening_import_source_unavailable", 404, "源文件不存在")
            result = formal_files.complete_file_upload(db, actor=principal, file_id=file_id,
                trace_request_id=request_id or "", storage=storage)
            output = {"file_id": result.file_id, "status": "available", "replayed": result.already_available}
            db.commit()
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.post("/jobs", response_model=ImportStatusOut, status_code=202)
def request_import(payload: OpeningCountImportBusinessCheckIn, response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            job_id, replayed = create_opening_count_import_job(db, actor=principal,
                **payload.model_dump(), idempotency_key=idempotency_key or "", request_id=request_id or "")
            db.commit()
        with factory() as db:
            output = _status(db, actor=principal, job_id=job_id, replayed=replayed)
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.get("/jobs/recovery", response_model=ImportStatusOut)
def recover_import(response: Response, principal: FormalPrincipal = Depends(get_formal_principal),
    settings: Settings = Depends(get_settings), storage=Depends(get_formal_file_storage_adapter),
    factory=Depends(get_import_session_factory),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            original = recover_opening_count_import_job(db, actor=principal, idempotency_key=idempotency_key or "")
            output = _status(db, actor=principal, job_id=original.job_id, replayed=True)
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.post("/command-seals", response_model=ImportSealOut)
def seal_import_command(payload: ImportSealIn, response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    import_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    upload_key: Annotated[str | None, Header(alias="X-Original-Upload-Key")] = None):
    _require_enabled(settings, storage)
    from ..formal_services.opening_count_import_seals import ImportSealCommand, seal_opening_import
    try:
        with factory() as db:
            output = ImportSealOut(**seal_opening_import(db, actor=principal,
                command=ImportSealCommand(**payload.model_dump(), import_key=import_key or "", upload_key=upload_key or ""),
                idempotency_hmac_secret=settings.file_idempotency_hmac_secret))
            db.commit()
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.get("/command-seals/recovery", response_model=ImportSealOut)
def recover_import_seal(response: Response, task_id: UUID, round_id: UUID, scope_id: UUID,
    actor_person_id: UUID, authorization_version: int, source_sha256: str, size_bytes: int,
    source_file_id: UUID | None = None,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    import_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    upload_key: Annotated[str | None, Header(alias="X-Original-Upload-Key")] = None):
    _require_enabled(settings, storage)
    from ..formal_services.opening_count_import_seals import ImportSealCommand, read_opening_import_seal
    try:
        with factory() as db:
            output = ImportSealOut(**read_opening_import_seal(db, actor=principal,
                command=ImportSealCommand(task_id=task_id,round_id=round_id,scope_id=scope_id,
                    actor_person_id=actor_person_id,authorization_version=authorization_version,source_sha256=source_sha256,
                    size_bytes=size_bytes,source_file_id=source_file_id,import_key=import_key or "",upload_key=upload_key or ""),
                idempotency_hmac_secret=settings.file_idempotency_hmac_secret))
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.get("/jobs/management-recovery", response_model=ImportManagementRecoveryOut)
def management_recovery(response: Response, task_id: UUID, round_id: UUID, scope_id: UUID, source_file_id: UUID,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            output = ImportManagementRecoveryOut(**read_opening_import_management_recovery(db,
                actor=principal, task_id=task_id, round_id=round_id, scope_id=scope_id,
                source_file_id=source_file_id, idempotency_key=idempotency_key or ""))
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.get("/jobs/{job_id}", response_model=ImportStatusOut)
def import_status(job_id: UUID, response: Response, principal: FormalPrincipal = Depends(get_formal_principal),
    settings: Settings = Depends(get_settings), storage=Depends(get_formal_file_storage_adapter),
    factory=Depends(get_import_session_factory)):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            output = _status(db, actor=principal, job_id=job_id)
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.post("/jobs/{job_id}/error-download-intents", response_model=ImportErrorDownloadOut)
def error_download(job_id: UUID, payload: ConfirmIn, response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            result = create_opening_count_error_download(db, actor=principal, job_id=job_id,
                request_id=request_id or "", storage=storage,
                ttl_seconds=settings.file_download_intent_ttl_seconds)
            output = ImportErrorDownloadOut(job_id=result.job_id, file_id=result.file_id,
                filename=result.filename, sha256=result.sha256, size_bytes=result.size_bytes,
                download=PresignedDownloadOut(url=result.download.url, expires_at=result.download.expires_at))
            db.commit()
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.get("/jobs/{job_id}/review", response_model=ImportReviewOut)
def review_import(job_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory)):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            review = read_opening_count_import_review(db, actor=principal, job_id=job_id)
            output = ImportReviewOut(**asdict(review), result=_status(db, actor=principal, job_id=job_id))
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.post("/jobs/{job_id}/cancel", response_model=ImportStatusOut)
def cancel_import(job_id: UUID, payload: ConfirmIn, response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None):
    _require_enabled(settings, storage)
    try:
        with factory() as db:
            _, replayed = cancel_opening_count_import(db, actor=principal, job_id=job_id,
                idempotency_key=idempotency_key or "", request_id=request_id or "")
            db.commit()
        with factory() as db:
            output = _status(db, actor=principal, job_id=job_id, replayed=replayed)
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output


@router.post("/jobs/{job_id}/confirm", response_model=ImportStatusOut)
def confirm_import(job_id: UUID, payload: ConfirmIn, response: Response,
    principal: FormalPrincipal = Depends(get_formal_principal), settings: Settings = Depends(get_settings),
    storage=Depends(get_formal_file_storage_adapter), factory=Depends(get_import_session_factory),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None):
    _require_enabled(settings, storage)
    try:
        _request_id(request_id or "")
        with factory() as db:
            original = recover_opening_count_import_job(db, actor=principal, idempotency_key=idempotency_key or "")
            if original.job_id != job_id:
                raise OpeningCountImportJobError("opening_import_idempotency_conflict", 409, "请求键与任务不一致")
            if original.status != "awaiting_confirmation":
                raise OpeningCountImportJobError("opening_import_not_awaiting_confirmation", 409, "请读取原任务结果，不要重复确认")
            binding = load_import_binding(db.get(FileJob, job_id).import_binding_jsonb)
        with factory() as db:
            source = read_authorized_opening_count_source(db, actor=principal,
                file_id=binding.source_file_id, storage=storage)
        with factory() as db:
            confirm_persisted_opening_count_import(db, actor=principal, job_id=job_id,
                source=source, request_id=request_id)
            db.commit()
        with factory() as db:
            output = _status(db, actor=principal, job_id=job_id)
    except Exception as exc:
        _raise(exc)
    response.headers.update(SAFETY_HEADERS)
    return output
