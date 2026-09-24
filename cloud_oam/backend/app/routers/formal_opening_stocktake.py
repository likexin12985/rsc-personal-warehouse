"""Formal V1.0 command API for the complete opening-stocktake workflow.

Each route represents exactly one domain transition. The HTTP layer supplies
no actor, state or hash input; start resolves the persisted payload hash from
two evidence anchors and the domain service revalidates that evidence under
its transaction locks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, Request, Query, status
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..config import Settings, get_settings
from ..dependencies import require_permission
from ..foundation_models import (
    ExternalObject,
    ExternalObjectVersion,
    SyncBatch,
    SyncInboxEvent,
)
from ..formal_access import FormalPrincipal
from ..formal_services import opening_observation_disposition as disposition_service
from ..formal_services import opening_stocktake as start_service
from ..formal_services import opening_stocktake_count as count_service
from ..formal_services import opening_count_import_workbook as import_workbook
from ..formal_services import opening_count_import_prevalidation as import_prevalidation
from ..formal_services.opening_count_import_source import OpeningCountImportSourceError
from ..formal_services.file_storage import FileStorageAdapter
from .formal_files import get_formal_file_storage_adapter
from ..formal_services import opening_stocktake_finalize as terminal_service
from ..formal_services import opening_stocktake_recount as recount_service
from ..formal_services import opening_stocktake_review as review_service
from ..opening_stocktake_schemas import (
    OpeningObservationDispositionIn,
    OpeningObservationDispositionOut,
    OpeningPhysicalObservationIn,
    OpeningCountImportErrorOut,
    OpeningCountImportFormatOut,
    OpeningCountImportBusinessCheckIn,
    OpeningCountImportBusinessCheckOut,
    OpeningStocktakeCloseOut,
    OpeningStocktakeCountIn,
    OpeningStocktakeCountOut,
    OpeningStocktakeHeadquartersReviewIn,
    OpeningStocktakePostOut,
    OpeningStocktakeRecountIn,
    OpeningStocktakeRecountOut,
    OpeningStocktakeReviewIn,
    OpeningStocktakeReviewOut,
    OpeningStocktakeStartIn,
    OpeningStocktakeFromPublicationIn,
    OpeningStocktakeStartRecoveryOut,
    OpeningStartSealIn,
    OpeningStartCommandResultOut,
    OpeningStocktakeStartOut,
    OpeningStocktakeTerminalIn,
)


router = APIRouter(
    prefix="/v1/stocktakes/opening",
    tags=["formal-opening-stocktake"],
)
_SAFE_HEADER_VALUE = re.compile(r"^[A-Za-z0-9._:-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUANTITY_QUANTUM = Decimal("0.001")
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def get_opening_import_session_factory():
    return SessionLocal


class _OpeningStocktakeAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        category: str,
        message: str,
        http_status_code: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.http_status_code = http_status_code

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


@router.get("/imports/opening-count/template")
def get_opening_count_import_template(
    _principal: FormalPrincipal = Depends(require_permission("stocktake", "count")),
):
    """Return the exact format template; no business facts are read or written."""

    return Response(
        content=import_workbook.render_opening_count_import_template(),
        media_type=_XLSX_MIME,
        headers={"Cache-Control": "no-store", "Content-Disposition":
                 'attachment; filename="rsc-opening-count-v1.xlsx"'},
    )


@router.post("/imports/opening-count/format-check", response_model=OpeningCountImportFormatOut)
async def check_opening_count_import_format(
    request: Request,
    response: Response,
    _principal: FormalPrincipal = Depends(require_permission("stocktake", "count")),
):
    """Check workbook syntax only; current scope and identity remain unconfirmed."""

    result = await _opening_count_import_preview(request)
    response.headers["Cache-Control"] = "no-store"
    return OpeningCountImportFormatOut(
        source_sha256=result.source_sha256,
        row_count=result.row_count,
        format_valid=result.ready,
        payload_sha256=result.payload_sha256,
        errors=tuple(OpeningCountImportErrorOut(
            row=item.row, field=item.field, code=item.code, message=item.message,
        ) for item in result.errors),
    )


@router.post("/imports/opening-count/error-report")
async def get_opening_count_import_error_report(
    request: Request,
    _principal: FormalPrincipal = Depends(require_permission("stocktake", "count")),
):
    result = await _opening_count_import_preview(request)
    if result.ready:
        raise HTTPException(status_code=409, detail={
            "code": "opening_import_no_format_errors", "category": "conflict",
            "message": "文件没有格式错误报告",
        }, headers={"Cache-Control": "no-store"})
    return Response(
        content=import_workbook.render_opening_count_error_report(result.errors),
        media_type=_XLSX_MIME,
        headers={"Cache-Control": "no-store", "Content-Disposition":
                 'attachment; filename="rsc-opening-count-errors.xlsx"'},
    )


async def _opening_count_import_preview(request: Request) -> import_workbook.OpeningCountImportPreview:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != _XLSX_MIME:
        raise HTTPException(status_code=415, detail={
            "code": "opening_import_content_type_invalid", "category": "invalid_request",
            "message": "仅接受 XLSX 文件",
        }, headers={"Cache-Control": "no-store"})
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > import_workbook.MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail={
                "code": "opening_import_file_too_large", "category": "invalid_request",
                "message": "文件超过大小限制",
            }, headers={"Cache-Control": "no-store"})
    try:
        return import_workbook.prevalidate_opening_count_workbook(bytes(data))
    except import_workbook.OpeningCountImportFormatError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "opening_import_format_invalid", "category": "invalid_request",
            "message": str(exc),
        }, headers={"Cache-Control": "no-store"}) from None


@router.post(
    "/imports/opening-count/business-check",
    response_model=OpeningCountImportBusinessCheckOut,
)
def check_opening_count_import_business(
    payload: OpeningCountImportBusinessCheckIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "count")),
    settings: Settings = Depends(get_settings),
    storage: FileStorageAdapter | None = Depends(get_formal_file_storage_adapter),
    session_factory=Depends(get_opening_import_session_factory),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    """Read-only business preview of one completed private XLSX source."""

    key, trace = _required_write_headers(
        idempotency_key=idempotency_key, request_id=request_id,
    )
    if (
        not settings.file_storage_configuration_ready()
        or storage is None
        or getattr(storage, "provider_code", None) != "aliyun_oss_v2"
    ):
        raise HTTPException(status_code=503, detail={
            "code": "opening_import_storage_disabled", "category": "service_unavailable",
            "message": "期初盘点私有文件服务尚未安全启用",
        }, headers={"Cache-Control": "no-store"})
    try:
        preview = import_prevalidation.prevalidate_authorized_opening_count_import(
            session_factory,
            actor=principal,
            storage=storage,
            file_id=payload.source_file_id,
            task_id=payload.task_id,
            round_id=payload.round_id,
            scope_id=payload.scope_id,
            idempotency_key=key,
            request_id=trace,
        )
    except OpeningCountImportSourceError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail={
            "code": exc.code, "category": "invalid_request" if exc.http_status_code == 422 else "forbidden" if exc.http_status_code == 403 else "precondition_failed" if exc.http_status_code == 412 else "not_found",
            "message": str(exc),
        }, headers={"Cache-Control": "no-store"}) from None
    except count_service.OpeningStocktakeCountError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail(),
                            headers={"Cache-Control": "no-store"}) from None
    except import_workbook.OpeningCountImportFormatError as exc:
        raise HTTPException(status_code=422, detail={
            "code": "opening_import_format_invalid", "category": "invalid_request",
            "message": str(exc),
        }, headers={"Cache-Control": "no-store"}) from None
    except Exception:
        raise HTTPException(status_code=503, detail={
            "code": "opening_import_prevalidation_unavailable",
            "category": "service_unavailable",
            "message": "期初盘点业务预校验暂不可用",
        }, headers={"Cache-Control": "no-store"}) from None
    response.headers["Cache-Control"] = "no-store"
    return OpeningCountImportBusinessCheckOut(
        source_sha256=preview.source_sha256,
        payload_sha256=preview.payload_sha256,
        row_count=preview.row_count,
        ready=preview.ready,
        task_version=preview.count.task_version if preview.count else None,
        actor_authorization_version=(
            preview.count.actor_authorization_version if preview.count else None
        ),
        request_sha256=preview.count.request_sha256 if preview.count else None,
        errors=tuple(OpeningCountImportErrorOut(
            row=item.row, field=item.field, code=item.code, message=item.message,
        ) for item in preview.errors),
    )


@router.post("", response_model=OpeningStocktakeStartOut)
def start_formal_opening_stocktake(
    payload: OpeningStocktakeStartIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "manage")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        control_lines = tuple(
            start_service.OpeningControlLineInput(
                sync_inbox_event_id=row.sync_inbox_event_id,
                external_object_version_id=row.external_object_version_id,
                external_business_key=row.external_business_key,
                material_id=row.material_id,
                condition_code=row.condition_code,
                control_qty=row.control_qty,
                mapping_status=row.mapping_status,
                source_updated_at=row.source_updated_at,
                payload_sha256=_load_control_payload_sha256(
                    db,
                    sync_inbox_event_id=row.sync_inbox_event_id,
                    external_object_version_id=(
                        row.external_object_version_id
                    ),
                    expected_source_system_id=(
                        payload.control_source_system_id
                    ),
                    expected_sync_run_id=payload.control_sync_run_id,
                    expected_business_key=row.external_business_key,
                    expected_source_updated_at=row.source_updated_at,
                ),
                mapping_note=row.mapping_note,
            )
            for row in payload.control_lines
        )
        result = start_service.start_opening_stocktake(
            db,
            actor=principal,
            command=start_service.StartOpeningStocktakeCommand(
                task_no=payload.task_no,
                region_org_id=payload.region_org_id,
                control_source_system_id=payload.control_source_system_id,
                control_sync_run_id=payload.control_sync_run_id,
                control_sync_scope_key=payload.control_sync_scope_key,
                scopes=tuple(
                    start_service.OpeningStocktakeScopeInput(
                        owner_org_id=row.owner_org_id,
                        location_id=row.location_id,
                        assignee_user_id=row.assignee_user_id,
                        freeze_mode=row.freeze_mode,
                    )
                    for row in payload.scopes
                ),
                control_lines=control_lines,
                blind_count=payload.blind_count,
                deadline=payload.deadline,
                note=payload.note,
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningStocktakeStartOut(
            task_id=result.task_id,
            task_no=result.task_no,
            status=result.status,
            cutoff_ledger_cursor=result.cutoff_ledger_cursor,
            initial_round_id=result.initial_round_id,
            scope_count=result.scope_count,
            snapshot_line_count=result.snapshot_line_count,
            control_line_count=result.control_line_count,
            replayed=result.replayed,
        )
        db.commit()
    except (
        start_service.OpeningStocktakeError,
        _OpeningStocktakeAdapterError,
    ) as exc:
        db.rollback()
        _raise_write_error(exc)
    except DBAPIError as exc:
        db.rollback()
        # Only a named database authorization refusal proves this outcome.
        # Do not classify unrelated DB errors or driver text as a known denial.
        if start_service.is_actor_admission_rejection(exc):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "opening_authorization_changed",
                    "category": "forbidden",
                    "message": "提交时盘点管理权限已失效，请重新核对当前权限",
                },
                headers={"Cache-Control": "no-store"},
            ) from None
        raise
    except Exception:
        db.rollback()
        raise
    _set_replay_header(response, output.replayed)
    return output


@router.get("/start-result",response_model=OpeningStocktakeStartRecoveryOut)
def recover_formal_opening_start(
    request: Request,
    response: Response,
    region_org_id: UUID,
    publication_id: UUID,
    trace_request_id: Annotated[str,Query(min_length=8,max_length=160,pattern=r"^[A-Za-z0-9._:-]+$")],
    principal: FormalPrincipal = Depends(require_permission("stocktake","manage")),
    db: Session = Depends(get_db),
):
    from ..formal_services.opening_start_recovery import recover_start
    allowed={"region_org_id","publication_id","trace_request_id"}
    if set(request.query_params)!=allowed or any(len(request.query_params.getlist(key))!=1 for key in allowed):
        raise HTTPException(status_code=422,detail="需要唯一的区域、发布批次及原请求编号",headers={"Cache-Control":"no-store"})
    response.headers["Cache-Control"]="no-store"
    try:
        return recover_start(db,actor=principal,region_org_id=region_org_id,publication_id=publication_id,trace_request_id=trace_request_id)
    except start_service.OpeningStocktakeError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.http_status_code,detail=exc.as_detail(),headers={"Cache-Control":"no-store"}) from None


@router.get("/start-command-result", response_model=OpeningStartCommandResultOut)
def recover_formal_opening_command(
    request: Request, response: Response, region_org_id: UUID, publication_id: UUID,
    trace_request_id: Annotated[str, Query(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")],
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    from ..formal_services.opening_start_seals import recover_start_command
    allowed = {"region_org_id", "publication_id", "trace_request_id"}
    if set(request.query_params) != allowed or any(len(request.query_params.getlist(key)) != 1 for key in allowed):
        raise HTTPException(status_code=422, detail="需要唯一的区域、发布批次及原请求编号", headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    try:
        return recover_start_command(db, actor=principal, region_org_id=region_org_id,
            publication_id=publication_id, trace_request_id=trace_request_id)
    except start_service.OpeningStocktakeError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail(), headers={"Cache-Control": "no-store"}) from None


@router.post("/seal-start-command", response_model=OpeningStartCommandResultOut)
def seal_formal_opening_command(
    payload: OpeningStartSealIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")), db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    from ..formal_services.opening_start_seals import seal_start_command
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    if trace != payload.trace_request_id or key != "opening-start-seal:" + payload.trace_request_id:
        raise HTTPException(status_code=400, detail="终结只接受准确的原启动请求坐标", headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    try:
        result = OpeningStartCommandResultOut.model_validate(seal_start_command(db, actor=principal, **payload.model_dump()))
        db.commit()
        return result
    except start_service.OpeningStocktakeError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail(), headers={"Cache-Control": "no-store"}) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail={"code": "opening_seal_unavailable", "category": "service_unavailable",
            "message": "原请求终结结果暂不可确认，请保留记录并查询原结果"}, headers={"Cache-Control": "no-store"}) from None
    except Exception:
        db.rollback()
        raise


@router.post("/from-publication", response_model=OpeningStocktakeStartOut)
def start_formal_opening_from_publication(
    payload: OpeningStocktakeFromPublicationIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    _required_write_headers(idempotency_key=idempotency_key,request_id=request_id)
    from ..formal_services.opening_publication_admission import selection
    try:
        # Historical evidence is needed for an exact retry after expiry or
        # replacement. The shared new-task service alone decides admission.
        document,_ = selection(db,actor=principal,region=payload.region_org_id,publication=payload.publication_id)
        raw=payload.model_dump(mode="json",exclude={"publication_id"})
        raw.update(control_source_system_id=document["source_system_id"],control_sync_run_id=document["sync_run_id"],
            control_sync_scope_key=document["sync_scope_key"],control_lines=[
                {key:value for key,value in row.items() if key!="payload_sha256"} for row in document["control_lines"]])
        command=OpeningStocktakeStartIn.model_validate(raw)
    except start_service.OpeningStocktakeError as exc:
        db.rollback()
        _raise_write_error(exc)
    except Exception:
        db.rollback()
        raise
    return start_formal_opening_stocktake(command,response,principal,db,idempotency_key,request_id)


@router.post(
    "/{task_id}/rounds/{round_id}/scopes/{scope_id}/count",
    response_model=OpeningStocktakeCountOut,
)
def submit_formal_opening_stocktake_scope_count(
    task_id: UUID,
    round_id: UUID,
    scope_id: UUID,
    payload: OpeningStocktakeCountIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "count")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = count_service.submit_opening_stocktake_scope_count(
            db,
            actor=principal,
            command=count_service.SubmitOpeningStocktakeScopeCountCommand(
                task_id=task_id,
                round_id=round_id,
                scope_id=scope_id,
                physical_observations=tuple(
                    _physical_observation_command(row)
                    for row in payload.physical_observations
                ),
                zero_confirmed=payload.zero_confirmed,
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningStocktakeCountOut(
            task_id=result.task_id,
            round_id=result.round_id,
            scope_id=result.scope_id,
            task_status=result.task_status,
            round_status=result.round_status,
            scope_completed=result.scope_completed,
            round_sealed=result.round_sealed,
            has_pending_verification=result.has_pending_verification,
            replayed=result.replayed,
        )
        db.commit()
    except count_service.OpeningStocktakeCountError as exc:
        db.rollback()
        _raise_write_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_replay_header(response, output.replayed)
    return output


@router.post(
    "/{task_id}/rounds/{round_id}/reviews/region",
    response_model=OpeningStocktakeReviewOut,
)
def submit_formal_opening_stocktake_region_review(
    task_id: UUID,
    round_id: UUID,
    payload: OpeningStocktakeReviewIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "review_region")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    return _submit_review(
        db=db,
        principal=principal,
        task_id=task_id,
        round_id=round_id,
        payload=payload,
        response=response,
        idempotency_key=idempotency_key,
        request_id=request_id,
        submit=review_service.submit_opening_region_review,
    )


@router.post(
    "/{task_id}/rounds/{round_id}/reviews/headquarters",
    response_model=OpeningStocktakeReviewOut,
)
def submit_formal_opening_stocktake_headquarters_review(
    task_id: UUID,
    round_id: UUID,
    payload: OpeningStocktakeHeadquartersReviewIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "review_headquarters")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    return _submit_review(
        db=db,
        principal=principal,
        task_id=task_id,
        round_id=round_id,
        payload=payload,
        response=response,
        idempotency_key=idempotency_key,
        request_id=request_id,
        submit=review_service.submit_opening_headquarters_review,
    )


@router.post(
    "/{task_id}/rounds/{round_id}/recount",
    response_model=OpeningStocktakeRecountOut,
)
def open_formal_opening_stocktake_recount(
    task_id: UUID,
    round_id: UUID,
    payload: OpeningStocktakeRecountIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "manage")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = recount_service.open_opening_stocktake_recount(
            db,
            actor=principal,
            command=recount_service.OpenOpeningStocktakeRecountCommand(
                task_id=task_id,
                source_round_id=round_id,
                assignments=tuple(
                    recount_service.OpeningStocktakeRecountScopeAssignmentInput(
                        scope_id=row.scope_id,
                        assignee_user_id=row.assignee_user_id,
                    )
                    for row in payload.assignments
                ),
                reason=payload.reason,
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningStocktakeRecountOut(
            recount_case_id=result.recount_case_id,
            task_id=result.task_id,
            source_round_id=result.source_round_id,
            next_round_id=result.next_round_id,
            next_round_no=result.next_round_no,
            scope_count=result.scope_count,
            resulting_task_status=result.resulting_task_status,
            replayed=result.replayed,
        )
        db.commit()
    except recount_service.OpeningStocktakeRecountError as exc:
        db.rollback()
        _raise_write_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_replay_header(response, output.replayed)
    return output


@router.post(
    "/{task_id}/rounds/{round_id}/observations/{observation_id}/disposition",
    response_model=OpeningObservationDispositionOut,
)
def record_formal_opening_observation_disposition(
    task_id: UUID,
    round_id: UUID,
    observation_id: UUID,
    payload: OpeningObservationDispositionIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "manage")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = disposition_service.record_opening_observation_disposition(
            db,
            actor=principal,
            command=(
                disposition_service.RecordOpeningObservationDispositionCommand(
                    task_id=task_id,
                    round_id=round_id,
                    observation_id=observation_id,
                    disposition=payload.disposition,
                    reason_code=payload.reason_code,
                    comment=payload.comment,
                    resolved_material_id=payload.resolved_material_id,
                    resolved_lot_id=payload.resolved_lot_id,
                    resolved_serial_id=payload.resolved_serial_id,
                )
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningObservationDispositionOut(
            disposition_id=result.disposition_id,
            task_id=result.task_id,
            round_id=result.round_id,
            scope_id=result.scope_id,
            observation_id=result.observation_id,
            disposition=result.disposition,
            resolved_material_id=result.resolved_material_id,
            resolved_lot_id=result.resolved_lot_id,
            resolved_serial_id=result.resolved_serial_id,
            disposition_manifest_sha256=(
                result.disposition_manifest_sha256
            ),
            replayed=result.replayed,
        )
        db.commit()
    except disposition_service.OpeningObservationDispositionError as exc:
        db.rollback()
        _raise_write_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_replay_header(response, output.replayed)
    return output


@router.post(
    "/{task_id}/post",
    response_model=OpeningStocktakePostOut,
)
def post_approved_opening_stocktake(
    task_id: UUID,
    payload: OpeningStocktakeTerminalIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "post_opening")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = terminal_service.post_approved_opening_stocktake(
            db,
            actor=principal,
            command=terminal_service.PostOpeningStocktakeCommand(
                task_id=task_id,
                expected_version=payload.expected_version,
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningStocktakePostOut(
            task_id=result.task_id,
            round_id=result.round_id,
            posting_id=result.posting_id,
            inventory_transaction_id=result.inventory_transaction_id,
            resulting_task_status=result.resulting_task_status,
            task_version=result.task_version,
            total_quantity=_quantity_text(result.total_quantity),
            established_scope_count=result.established_scope_count,
            pending_control_difference_count=(
                result.pending_control_difference_count
            ),
            ledger_cursor=result.ledger_cursor,
            replayed=result.replayed,
        )
        db.commit()
    except terminal_service.OpeningStocktakeFinalizeError as exc:
        db.rollback()
        _raise_terminal_error(exc)
    except Exception:
        db.rollback()
        raise
    response.headers["Idempotency-Replayed"] = (
        "true" if output.replayed else "false"
    )
    return output


@router.post(
    "/{task_id}/close",
    response_model=OpeningStocktakeCloseOut,
)
def close_posted_opening_stocktake(
    task_id: UUID,
    payload: OpeningStocktakeTerminalIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "post_opening")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = terminal_service.close_posted_opening_stocktake(
            db,
            actor=principal,
            command=terminal_service.CloseOpeningStocktakeCommand(
                task_id=task_id,
                expected_version=payload.expected_version,
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningStocktakeCloseOut(
            task_id=result.task_id,
            posting_id=result.posting_id,
            inventory_transaction_id=result.inventory_transaction_id,
            resulting_task_status=result.resulting_task_status,
            task_version=result.task_version,
            closed_at=_aware_timestamp(result.closed_at),
            replayed=result.replayed,
        )
        db.commit()
    except terminal_service.OpeningStocktakeFinalizeError as exc:
        db.rollback()
        _raise_terminal_error(exc)
    except Exception:
        db.rollback()
        raise
    response.headers["Idempotency-Replayed"] = (
        "true" if output.replayed else "false"
    )
    return output


def _physical_observation_command(
    row: OpeningPhysicalObservationIn,
) -> count_service.OpeningPhysicalObservationInput:
    return count_service.OpeningPhysicalObservationInput(
        material_identifier_raw=row.material_identifier_raw,
        material_identifier_type=row.material_identifier_type,
        condition_code=row.condition_code,
        availability_bucket=row.availability_bucket,
        counted_qty=row.counted_qty,
        material_id=row.material_id,
        lot_id=row.lot_id,
        lot_no_raw=row.lot_no_raw,
        serial_id=row.serial_id,
        serial_no_raw=row.serial_no_raw,
        serial_identifier_type=row.serial_identifier_type,
        count_method=row.count_method,
        reason_code=row.reason_code,
        remark=row.remark,
    )


def _submit_review(
    *,
    db: Session,
    principal: FormalPrincipal,
    task_id: UUID,
    round_id: UUID,
    payload: OpeningStocktakeReviewIn | OpeningStocktakeHeadquartersReviewIn,
    response: Response,
    idempotency_key: str | None,
    request_id: str | None,
    submit,
) -> OpeningStocktakeReviewOut:
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = submit(
            db,
            actor=principal,
            command=review_service.SubmitOpeningStocktakeReviewCommand(
                task_id=task_id,
                round_id=round_id,
                decision=payload.decision,
                items=tuple(
                    review_service.OpeningStocktakeReviewItemInput(
                        difference_id=row.difference_id,
                        decision=row.decision,
                        comment=row.comment,
                    )
                    for row in payload.items
                ),
                comment=payload.comment,
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningStocktakeReviewOut(
            review_id=result.review_id,
            task_id=result.task_id,
            round_id=result.round_id,
            review_stage=result.review_stage,
            decision=result.decision,
            resulting_task_status=result.resulting_task_status,
            item_count=result.item_count,
            pending_control_count=result.pending_control_count,
            replayed=result.replayed,
        )
        db.commit()
    except review_service.OpeningStocktakeReviewError as exc:
        db.rollback()
        _raise_write_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_replay_header(response, output.replayed)
    return output


def _load_control_payload_sha256(
    db: Session,
    *,
    sync_inbox_event_id: UUID,
    external_object_version_id: UUID,
    expected_source_system_id: UUID,
    expected_sync_run_id: UUID,
    expected_business_key: str,
    expected_source_updated_at: datetime | None,
) -> str:
    # Deliberately do not lock here. This is only HTTP input adaptation; the
    # domain service locks and revalidates the complete sync graph. A mutation
    # between these reads and the service call must therefore fail closed in
    # the service rather than be accepted on this snapshot.
    event_rows = db.execute(
        select(SyncInboxEvent, SyncBatch)
        .select_from(SyncInboxEvent)
        .join(SyncBatch, SyncBatch.id == SyncInboxEvent.batch_id)
        .where(SyncInboxEvent.id == sync_inbox_event_id)
    ).all()
    version_rows = db.execute(
        select(ExternalObjectVersion, ExternalObject)
        .select_from(ExternalObjectVersion)
        .join(
            ExternalObject,
            ExternalObject.id == ExternalObjectVersion.external_object_id,
        )
        .where(ExternalObjectVersion.id == external_object_version_id)
    ).all()
    if not event_rows or not version_rows:
        raise _OpeningStocktakeAdapterError(
            "opening_control_evidence_not_found",
            "precondition_failed",
            "期初控制行的同步事件或外部版本证据不存在",
            status.HTTP_412_PRECONDITION_FAILED,
        )
    if len(event_rows) != 1 or len(version_rows) != 1:
        raise _OpeningStocktakeAdapterError(
            "opening_control_evidence_ambiguous",
            "service_unavailable",
            "期初控制行证据不唯一，禁止启动盘点",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    event, batch = event_rows[0]
    version, external_object = version_rows[0]
    payload_hash = event.payload_sha256
    if (
        batch.run_id != expected_sync_run_id
        or event.source_system_id != expected_source_system_id
        or external_object.source_system_id != expected_source_system_id
        or event.entity_type != external_object.entity_type
        or event.external_id != expected_business_key
        or external_object.external_id != expected_business_key
        or version.external_object_id != external_object.id
        or event.source_version != version.source_version
        or not _same_evidence_timestamp(
            event.source_updated_at,
            version.source_updated_at,
        )
        or not _same_evidence_timestamp(
            event.source_updated_at,
            expected_source_updated_at,
        )
        or event.payload_jsonb != version.payload_jsonb
        or event.payload_sha256 != version.payload_sha256
        or not isinstance(payload_hash, str)
        or _SHA256.fullmatch(payload_hash) is None
    ):
        raise _OpeningStocktakeAdapterError(
            "opening_control_evidence_mismatch",
            "precondition_failed",
            "期初控制行的两个证据锚点关系不一致",
            status.HTTP_412_PRECONDITION_FAILED,
        )
    return payload_hash


def _same_evidence_timestamp(
    left: datetime | None,
    right: datetime | None,
) -> bool:
    if left is None or right is None:
        return left is right
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return False

    def normalized(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return normalized(left) == normalized(right)


def _set_replay_header(response: Response, replayed: bool) -> None:
    response.headers["Idempotency-Replayed"] = (
        "true" if replayed else "false"
    )


def _raise_write_error(exc) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from None


def _required_write_headers(
    *, idempotency_key: str | None, request_id: str | None
) -> tuple[str, str]:
    return (
        _required_safe_header(
            "Idempotency-Key",
            idempotency_key,
            minimum=16,
            maximum=128,
        ),
        _required_safe_header(
            "X-Request-ID",
            request_id,
            minimum=8,
            maximum=160,
        ),
    )


def _required_safe_header(
    name: str,
    value: str | None,
    *,
    minimum: int,
    maximum: int,
) -> str:
    if (
        value is None
        or not minimum <= len(value) <= maximum
        or _SAFE_HEADER_VALUE.fullmatch(value) is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": f"{name.lower().replace('-', '_')}_invalid",
                "category": "invalid_request",
                "message": f"{name} 必须是 {minimum}-{maximum} 位安全字符",
            },
        )
    return value


def _quantity_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise RuntimeError("opening stocktake returned an invalid quantity")
    quantized = value.quantize(_QUANTITY_QUANTUM)
    if quantized != value or quantized < 0:
        raise RuntimeError("opening stocktake returned an invalid quantity")
    return format(quantized, "f")


def _aware_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimeError("opening stocktake returned an invalid timestamp")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise RuntimeError(
            "opening stocktake returned an invalid timestamp"
        ) from exc
    if offset is None:
        raise RuntimeError("opening stocktake returned an invalid timestamp")
    return value.astimezone(timezone.utc)


def _raise_terminal_error(
    exc: terminal_service.OpeningStocktakeFinalizeError,
) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from None
