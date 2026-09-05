"""Formal V1.0 API for non-opening stocktake reads and commands.

Each write route commits exactly one local domain transition. Nothing in this
adapter calls OAM, RSC, Workflow, Feishu or another external system.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import stocktake_count as count_service
from ..formal_services import stocktake_count_command_status as count_status_service
from ..formal_services import stocktake_difference as difference_service
from ..formal_services import stocktake_query as query_service
from ..formal_services import stocktake_posting as posting_service
from ..formal_services import stocktake_close as close_service
from ..formal_services import stocktake_recount as recount_service
from ..formal_services import stocktake_recount_count as recount_count_service
from ..formal_services import (
    stocktake_recount_difference as recount_difference_service,
)
from ..formal_services import stocktake_review as review_service
from ..formal_services import stocktake_task as task_service
from ..stocktake_command_schemas import (
    StocktakeDifferenceGenerateIn,
    StocktakeDifferenceGenerateOut,
    StocktakeDifferencePostIn,
    StocktakeDifferencePostOut,
    StocktakeCloseOut,
    StocktakeCloseReconciliationOut,
    StocktakeInitialScopeCountIn,
    StocktakeInitialScopeCountOut,
    StocktakeRecountDifferenceOut,
    StocktakeRecountOpenIn,
    StocktakeRecountOpenOut,
    StocktakeRecountScopeCountIn,
    StocktakeRecountScopeCountOut,
    StocktakeReviewIn,
    StocktakeReviewOut,
    StocktakeTerminalIn,
)
from ..stocktake_read_schemas import StocktakeTaskDetailOut, StocktakeTaskPageOut
from ..stocktake_count_command_status_schemas import StocktakeCountCommandStatusOut
from ..stocktake_task_schemas import (
    PersonalStocktakeCreateIn,
    StocktakeTaskCreateIn,
    StocktakeTaskCreateOut,
    StocktakeTaskStartIn,
    StocktakeTaskStartOut,
)


router = APIRouter(prefix="/v1/stocktakes", tags=["formal-stocktake"])
_SAFE_HEADER_VALUE = re.compile(r"^[A-Za-z0-9._:-]+$")
_PLACEHOLDER_MARKERS = (
    "replace-with",
    "replace_me",
    "replace-me",
    "change-me",
    "changeme",
)


@router.get("", response_model=StocktakeTaskPageOut)
def list_formal_stocktakes(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "read")),
    db: Session = Depends(get_db),
):
    try:
        output = query_service.list_stocktake_tasks(
            db,
            actor=principal,
            limit=limit,
            after_id=after_id,
        )
    except query_service.StocktakeReadError as exc:
        _raise_service_error(exc)
    _set_no_store(response)
    return output


@router.get("/{task_id}", response_model=StocktakeTaskDetailOut)
def formal_stocktake_detail(
    task_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "read")),
    db: Session = Depends(get_db),
):
    try:
        output = query_service.stocktake_task_detail(
            db,
            actor=principal,
            task_id=task_id,
        )
    except query_service.StocktakeReadError as exc:
        _raise_service_error(exc)
    _set_no_store(response)
    return output


@router.get(
    "/{task_id}/rounds/{round_id}/scopes/{scope_id}/count-command-status",
    response_model=StocktakeCountCommandStatusOut,
)
def formal_stocktake_count_command_status(
    task_id: UUID, round_id: UUID, scope_id: UUID,
    request: Request, response: Response,
    operation: Annotated[Literal["initial_count", "recount_count"], Query()],
    actor_person_id: Annotated[UUID, Query()],
    actor_authorization_version: Annotated[int, Query(ge=1)],
    trace_request_id: Annotated[str, Query(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")],
    principal: FormalPrincipal = Depends(require_permission("stocktake", "read")),
    db: Session = Depends(get_db),
):
    headers = {"Cache-Control": "private, no-store, max-age=0", "Pragma": "no-cache",
               "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
    response.headers.update(headers)
    try:
        allowed = {"operation", "actor_person_id", "actor_authorization_version", "trace_request_id"}
        if (set(request.query_params) != allowed
            or any(len(request.query_params.getlist(key)) != 1 for key in allowed)
            or "idempotency-key" in request.headers
            or request.headers.get("content-length", "0") != "0"
            or "transfer-encoding" in request.headers):
            count_status_service._invalid_input()
        return count_status_service.stocktake_count_command_status(
            db, actor=principal, operation=operation, task_id=task_id,
            round_id=round_id, scope_id=scope_id, actor_person_id=actor_person_id,
            actor_authorization_version=actor_authorization_version, trace_request_id=trace_request_id,
        )
    except count_status_service.StocktakeCountCommandStatusError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail(), headers=headers) from None


@router.post("", response_model=StocktakeTaskCreateOut, status_code=201)
def create_formal_stocktake(
    payload: StocktakeTaskCreateIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    try:
        result = task_service.create_stocktake_task_draft(
            db,
            actor=principal,
            draft=payload,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeTaskCreateOut(
            task_id=result.task_id,
            task_no=result.task_no,
            task_type=result.task_type,
            status=result.status,
            task_version=result.version,
            scope_count=result.scope_count,
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except task_service.StocktakeTaskError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post("/personal", response_model=StocktakeTaskCreateOut, status_code=201)
def create_formal_personal_stocktake(
    payload: PersonalStocktakeCreateIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "count")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    try:
        result = task_service.create_personal_stocktake_draft(
            db,
            actor=principal,
            draft=payload,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeTaskCreateOut(
            task_id=result.task_id,
            task_no=result.task_no,
            task_type=result.task_type,
            status=result.status,
            task_version=result.version,
            scope_count=result.scope_count,
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except task_service.StocktakeTaskError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post("/{task_id}/start", response_model=StocktakeTaskStartOut)
def start_formal_stocktake(
    task_id: UUID,
    payload: StocktakeTaskStartIn,
    response: Response,
    # Technicians must be able to start their own personal stocktake. The
    # service separately proves manager/admin authority for managed tasks.
    principal: FormalPrincipal = Depends(require_permission("stocktake", "count")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    try:
        result = task_service.start_stocktake_task(
            db,
            actor=principal,
            task_id=task_id,
            command=payload,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeTaskStartOut(
            task_id=result.task_id,
            task_type=result.task_type,
            status=result.status,
            task_version=result.version,
            cutoff_ledger_cursor=result.cutoff_ledger_cursor,
            initial_round_id=result.initial_round_id,
            scope_count=result.scope_count,
            snapshot_line_count=result.snapshot_line_count,
            active_freeze_count=result.active_freeze_count,
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except task_service.StocktakeTaskError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/rounds/{round_id}/scopes/{scope_id}/initial-count",
    response_model=StocktakeInitialScopeCountOut,
)
def submit_formal_stocktake_initial_scope_count(
    task_id: UUID,
    round_id: UUID,
    scope_id: UUID,
    payload: StocktakeInitialScopeCountIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "count")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    command = count_service.SubmitStocktakeInitialScopeCountCommand(
        task_id=task_id,
        round_id=round_id,
        scope_id=scope_id,
        count_mode=payload.count_mode,
        account_counts=tuple(
            count_service.StocktakeSnapshotCountInput(
                stock_account_id=row.stock_account_id,
                counted_qty=row.counted_qty,
                count_method=row.count_method,
                serial_ids=row.serial_ids,
                book_qty_confirmation=row.book_qty_confirmation,
                reason_code=row.reason_code,
                remark=row.remark,
            )
            for row in payload.account_counts
        ),
        physical_observations=tuple(
            count_service.StocktakePhysicalObservationInput(
                material_id=row.material_id,
                material_identifier_raw=row.material_identifier_raw,
                material_identifier_type=row.material_identifier_type,
                condition_code=row.condition_code,
                availability_bucket=row.availability_bucket,
                counted_qty=row.counted_qty,
                lot_id=row.lot_id,
                lot_no_raw=row.lot_no_raw,
                serial_id=row.serial_id,
                serial_no_raw=row.serial_no_raw,
                serial_identifier_type=row.serial_identifier_type,
                count_method=row.count_method,
                reason_code=row.reason_code,
                remark=row.remark,
            )
            for row in payload.physical_observations
        ),
        evidence_file_ids=payload.evidence_file_ids,
        zero_confirmed=payload.zero_confirmed,
    )
    try:
        result = count_service.submit_stocktake_initial_scope_count(
            db,
            actor=principal,
            command=command,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeInitialScopeCountOut(
            task_id=result.task_id,
            round_id=result.round_id,
            scope_id=result.scope_id,
            task_status=result.task_status,
            round_status=result.round_status,
            task_version=result.task_version,
            scope_completed=result.scope_completed,
            round_submitted=result.round_submitted,
            evidence_file_count=result.evidence_file_count,
            replayed=result.replayed,
        )
        db.commit()
    except count_service.StocktakeCountError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/rounds/{round_id}/differences",
    response_model=StocktakeDifferenceGenerateOut,
)
def generate_formal_stocktake_initial_differences(
    task_id: UUID,
    round_id: UUID,
    payload: StocktakeDifferenceGenerateIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    try:
        result = difference_service.generate_stocktake_initial_differences(
            db,
            actor=principal,
            command=difference_service.GenerateStocktakeDifferenceCommand(
                task_id=task_id,
                round_id=round_id,
                expected_task_version=payload.expected_task_version,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeDifferenceGenerateOut(
            task_id=result.task_id,
            round_id=result.round_id,
            completion_id=result.completion_id,
            task_status=result.task_status,
            round_status=result.round_status,
            task_version=result.task_version,
            difference_status=result.difference_status,
            difference_count=result.difference_count,
            physical_difference_count=result.physical_difference_count,
            pending_observation_difference_count=(
                result.pending_observation_difference_count
            ),
            total_affected_qty=result.total_affected_qty,
            difference_manifest_sha256=result.difference_manifest_sha256,
            replayed=result.replayed,
        )
        db.commit()
    except difference_service.StocktakeDifferenceError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/rounds/{source_round_id}/recount",
    response_model=StocktakeRecountOpenOut,
)
def open_formal_stocktake_recount(
    task_id: UUID,
    source_round_id: UUID,
    payload: StocktakeRecountOpenIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    command = recount_service.OpenStocktakeRecountCommand(
        task_id=task_id,
        source_round_id=source_round_id,
        expected_task_version=payload.expected_task_version,
        assignments=tuple(
            recount_service.StocktakeRecountScopeAssignmentInput(
                scope_id=row.scope_id,
                assignee_user_id=row.assignee_user_id,
            )
            for row in payload.assignments
        ),
        reason=payload.reason,
    )
    try:
        result = recount_service.open_stocktake_recount(
            db,
            actor=principal,
            command=command,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeRecountOpenOut(
            recount_case_id=result.recount_case_id,
            task_id=result.task_id,
            source_round_id=result.source_round_id,
            next_round_id=result.next_round_id,
            next_round_no=result.next_round_no,
            scope_count=result.scope_count,
            assignment_count=result.assignment_count,
            resulting_task_status=result.resulting_task_status,
            task_version=result.task_version,
            replayed=result.replayed,
        )
        db.commit()
    except recount_service.StocktakeRecountError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/rounds/{round_id}/scopes/{scope_id}/recount-count",
    response_model=StocktakeRecountScopeCountOut,
)
def submit_formal_stocktake_recount_scope_count(
    task_id: UUID,
    round_id: UUID,
    scope_id: UUID,
    payload: StocktakeRecountScopeCountIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "count")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    command = recount_count_service.SubmitStocktakeRecountScopeCountCommand(
        task_id=task_id,
        round_id=round_id,
        scope_id=scope_id,
        count_mode=payload.count_mode,
        account_counts=tuple(
            recount_count_service.StocktakeSnapshotCountInput(
                stock_account_id=row.stock_account_id,
                counted_qty=row.counted_qty,
                count_method=row.count_method,
                serial_ids=row.serial_ids,
                book_qty_confirmation=row.book_qty_confirmation,
                reason_code=row.reason_code,
                remark=row.remark,
            )
            for row in payload.account_counts
        ),
        physical_observations=tuple(
            recount_count_service.StocktakePhysicalObservationInput(
                material_id=row.material_id,
                material_identifier_raw=row.material_identifier_raw,
                material_identifier_type=row.material_identifier_type,
                condition_code=row.condition_code,
                availability_bucket=row.availability_bucket,
                counted_qty=row.counted_qty,
                lot_id=row.lot_id,
                lot_no_raw=row.lot_no_raw,
                serial_id=row.serial_id,
                serial_no_raw=row.serial_no_raw,
                serial_identifier_type=row.serial_identifier_type,
                count_method=row.count_method,
                reason_code=row.reason_code,
                remark=row.remark,
            )
            for row in payload.physical_observations
        ),
        evidence_file_ids=payload.evidence_file_ids,
        zero_confirmed=payload.zero_confirmed,
    )
    try:
        result = recount_count_service.submit_stocktake_recount_scope_count(
            db,
            actor=principal,
            command=command,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeRecountScopeCountOut(
            task_id=result.task_id,
            round_id=result.round_id,
            scope_id=result.scope_id,
            recount_case_id=result.recount_case_id,
            task_status=result.task_status,
            round_status=result.round_status,
            task_version=result.task_version,
            scope_completed=result.scope_completed,
            round_submitted=result.round_submitted,
            count_ledger_cursor=result.count_ledger_cursor,
            evidence_file_count=result.evidence_file_count,
            replayed=result.replayed,
        )
        db.commit()
    except recount_count_service.StocktakeRecountCountError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/rounds/{round_id}/recount-differences",
    response_model=StocktakeRecountDifferenceOut,
)
def generate_formal_stocktake_recount_differences(
    task_id: UUID,
    round_id: UUID,
    payload: StocktakeDifferenceGenerateIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    try:
        result = recount_difference_service.generate_stocktake_recount_differences(
            db,
            actor=principal,
            command=(
                recount_difference_service.GenerateStocktakeRecountDifferenceCommand(
                    task_id=task_id,
                    round_id=round_id,
                    expected_task_version=payload.expected_task_version,
                )
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeRecountDifferenceOut(
            task_id=result.task_id,
            round_id=result.round_id,
            recount_case_id=result.recount_case_id,
            completion_id=result.completion_id,
            task_status=result.task_status,
            round_status=result.round_status,
            task_version=result.task_version,
            difference_status=result.difference_status,
            difference_count=result.difference_count,
            physical_difference_count=result.physical_difference_count,
            pending_observation_difference_count=(
                result.pending_observation_difference_count
            ),
            total_affected_qty=result.total_affected_qty,
            difference_manifest_sha256=result.difference_manifest_sha256,
            selected_scope_count=result.selected_scope_count,
            replayed=result.replayed,
        )
        db.commit()
    except recount_difference_service.StocktakeRecountDifferenceError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/rounds/{round_id}/reviews/region",
    response_model=StocktakeReviewOut,
)
def submit_formal_stocktake_region_review(
    task_id: UUID,
    round_id: UUID,
    payload: StocktakeReviewIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "review_region")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    return _submit_review(
        db=db,
        principal=principal,
        runtime_settings=runtime_settings,
        task_id=task_id,
        round_id=round_id,
        payload=payload,
        response=response,
        idempotency_key=idempotency_key,
        request_id=request_id,
        submit=review_service.submit_stocktake_region_review,
    )


@router.post(
    "/{task_id}/rounds/{round_id}/reviews/headquarters",
    response_model=StocktakeReviewOut,
)
def submit_formal_stocktake_headquarters_review(
    task_id: UUID,
    round_id: UUID,
    payload: StocktakeReviewIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "review_headquarters")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    return _submit_review(
        db=db,
        principal=principal,
        runtime_settings=runtime_settings,
        task_id=task_id,
        round_id=round_id,
        payload=payload,
        response=response,
        idempotency_key=idempotency_key,
        request_id=request_id,
        submit=review_service.submit_stocktake_headquarters_review,
    )


def _submit_review(
    *,
    db: Session,
    principal: FormalPrincipal,
    runtime_settings: Settings,
    task_id: UUID,
    round_id: UUID,
    payload: StocktakeReviewIn,
    response: Response,
    idempotency_key: str | None,
    request_id: str | None,
    submit,
) -> StocktakeReviewOut:
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    command = review_service.SubmitStocktakeReviewCommand(
        task_id=task_id,
        round_id=round_id,
        expected_task_version=payload.expected_task_version,
        decision=payload.decision,
        items=tuple(
            review_service.StocktakeReviewItemInput(
                difference_id=row.difference_id,
                decision=row.decision,
                comment=row.comment,
            )
            for row in payload.items
        ),
        comment=payload.comment,
    )
    try:
        result = submit(
            db,
            actor=principal,
            command=command,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeReviewOut(
            review_id=result.review_id,
            task_id=result.task_id,
            round_id=result.round_id,
            review_stage=result.review_stage,
            decision=result.decision,
            resulting_task_status=result.resulting_task_status,
            task_version=result.task_version,
            item_count=result.item_count,
            pending_verification_count=result.pending_verification_count,
            ready_for_posting=result.ready_for_posting,
            replayed=result.replayed,
        )
        db.commit()
    except review_service.StocktakeReviewError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/post-differences",
    response_model=StocktakeDifferencePostOut,
)
def post_formal_stocktake_differences(
    task_id: UUID,
    payload: StocktakeDifferencePostIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("stocktake", "post_difference")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> StocktakeDifferencePostOut:
    """Create only the immutable posting completion and ``posted`` state."""

    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    command = posting_service.PostApprovedStocktakeDifferencesCommand(
        task_id=task_id,
        expected_task_version=payload.expected_task_version,
    )
    try:
        result = posting_service.post_approved_stocktake_differences(
            db,
            actor=principal,
            command=command,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeDifferencePostOut(
            completion_id=result.completion_id,
            task_id=result.task_id,
            terminal_round_id=result.terminal_round_id,
            resulting_task_status=result.resulting_task_status,
            task_version=result.task_version,
            scope_count=result.scope_count,
            difference_count=result.difference_count,
            accepted_difference_count=result.accepted_difference_count,
            no_adjustment_count=result.no_adjustment_count,
            transaction_count=result.transaction_count,
            movement_count=result.movement_count,
            total_quantity=result.total_quantity,
            first_ledger_cursor=result.first_ledger_cursor,
            last_ledger_cursor=result.last_ledger_cursor,
            replayed=result.replayed,
        )
        db.commit()
    except posting_service.StocktakeDifferencePostingError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/reconcile",
    response_model=StocktakeCloseReconciliationOut,
)
def reconcile_formal_stocktake_for_close(
    task_id: UUID,
    payload: StocktakeTerminalIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "reconcile")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> StocktakeCloseReconciliationOut:
    """Append an internal reconciliation fact; keep the task ``posted``."""

    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    try:
        result = close_service.reconcile_posted_stocktake_for_close(
            db,
            actor=principal,
            command=close_service.ReconcileStocktakeForCloseCommand(
                task_id=task_id,
                expected_task_version=payload.expected_task_version,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeCloseReconciliationOut(
            completion_id=result.completion_id,
            task_id=result.task_id,
            posting_completion_id=result.posting_completion_id,
            reconciliation_no=result.reconciliation_no,
            reconciliation_ledger_cursor=result.reconciliation_ledger_cursor,
            resulting_task_status=result.resulting_task_status,
            task_version=result.task_version,
            scope_count=result.scope_count,
            account_count=result.account_count,
            scoped_account_count=result.scoped_account_count,
            serial_count=result.serial_count,
            transaction_count=result.transaction_count,
            movement_count=result.movement_count,
            book_total_qty=result.book_total_qty,
            physical_total_qty=result.physical_total_qty,
            reconciled_at=result.reconciled_at,
            replayed=result.replayed,
        )
        db.commit()
    except close_service.StocktakeCloseError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


@router.post(
    "/{task_id}/close",
    response_model=StocktakeCloseOut,
)
def close_formal_stocktake(
    task_id: UUID,
    payload: StocktakeTerminalIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "close")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> StocktakeCloseOut:
    """Close only against the exact latest, still-current reconciliation."""

    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    secret = _stocktake_write_secret(runtime_settings)
    try:
        result = close_service.close_reconciled_stocktake(
            db,
            actor=principal,
            command=close_service.CloseReconciledStocktakeCommand(
                task_id=task_id,
                expected_task_version=payload.expected_task_version,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = StocktakeCloseOut(
            completion_id=result.completion_id,
            task_id=result.task_id,
            reconciliation_completion_id=result.reconciliation_completion_id,
            reconciliation_no=result.reconciliation_no,
            reconciliation_ledger_cursor=result.reconciliation_ledger_cursor,
            resulting_task_status=result.resulting_task_status,
            task_version=result.task_version,
            closed_at=result.closed_at,
            replayed=result.replayed,
        )
        db.commit()
    except close_service.StocktakeCloseError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_write_headers(response, result.replayed)
    return output


def _stocktake_write_secret(settings: Settings) -> str:
    if not settings.stocktake_writes_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "stocktake_writes_disabled",
                "category": "service_unavailable",
                "message": "正式盘点写入尚未启用",
            },
        )
    secret = settings.stocktake_idempotency_hmac_secret.strip()
    lowered = secret.lower()
    if len(secret) < 32 or any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "stocktake_write_security_unavailable",
                "category": "service_unavailable",
                "message": "正式盘点写入安全配置不可用",
            },
        )
    other_secrets = {
        value.strip()
        for value in (
            settings.jwt_secret,
            settings.identity_hash_secret,
            settings.auth_idempotency_hmac_secret,
            settings.auth_login_rate_limit_hmac_secret,
            settings.material_request_idempotency_hmac_secret,
            settings.material_request_contact_mobile_hmac_secret,
        )
        if value.strip()
    }
    if secret in other_secrets:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "stocktake_write_secret_reuse_forbidden",
                "category": "service_unavailable",
                "message": "正式盘点写入密钥域不独立",
            },
        )
    return secret


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


def _set_write_headers(response: Response, replayed: bool) -> None:
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"
    _set_no_store(response)


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"


def _raise_service_error(exc) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from None


__all__ = ["router"]
