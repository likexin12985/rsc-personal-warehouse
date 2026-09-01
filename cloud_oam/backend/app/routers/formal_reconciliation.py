"""Formal V1 opening-control reconciliation HTTP boundary."""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import opening_control_reconciliation as service
from ..reconciliation_schemas import (
    OpeningControlReconciliationApproveIn,
    OpeningControlReconciliationApproveOut,
    OpeningControlReconciliationDetailOut,
    OpeningControlReconciliationExplainIn,
    OpeningControlReconciliationExplainOut,
    OpeningControlReconciliationPageOut,
    OpeningControlReconciliationStartIn,
    OpeningControlReconciliationStartOut,
)


router = APIRouter(
    prefix="/v1/reconciliations/opening",
    tags=["formal-opening-control-reconciliation"],
)
_SAFE_HEADER_VALUE = re.compile(r"^[A-Za-z0-9._:-]+$")


@router.get("", response_model=OpeningControlReconciliationPageOut)
def list_opening_control_reconciliations(
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(
        require_permission("reconciliation", "read")
    ),
    db: Session = Depends(get_db),
):
    return _read_call(
        service.list_opening_control_reconciliations,
        db,
        actor=principal,
        limit=limit,
        after_id=after_id,
    )


@router.post(
    "/tasks/{task_id}",
    response_model=OpeningControlReconciliationStartOut,
)
def start_opening_control_reconciliation(
    task_id: UUID,
    payload: OpeningControlReconciliationStartIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("reconciliation", "create_opening")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = service.start_opening_control_reconciliation(
            db,
            actor=principal,
            command=service.StartOpeningControlReconciliationCommand(
                task_id=task_id,
                expected_task_version=payload.expected_task_version,
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningControlReconciliationStartOut(
            reconciliation_run_id=result.reconciliation_run_id,
            task_id=result.task_id,
            status=result.status,
            version=result.version,
            item_count=result.item_count,
            created_at=result.created_at,
            replayed=result.replayed,
        )
        db.commit()
    except service.OpeningControlReconciliationError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_replay_header(response, output.replayed)
    return output


@router.get(
    "/{reconciliation_run_id}",
    response_model=OpeningControlReconciliationDetailOut,
)
def opening_control_reconciliation_detail(
    reconciliation_run_id: UUID,
    principal: FormalPrincipal = Depends(
        require_permission("reconciliation", "read")
    ),
    db: Session = Depends(get_db),
):
    return _read_call(
        service.opening_control_reconciliation_detail,
        db,
        actor=principal,
        reconciliation_run_id=reconciliation_run_id,
    )


@router.post(
    "/{reconciliation_run_id}/explanations",
    response_model=OpeningControlReconciliationExplainOut,
)
def explain_opening_control_reconciliation(
    reconciliation_run_id: UUID,
    payload: OpeningControlReconciliationExplainIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("reconciliation", "explain_opening")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = service.explain_opening_control_reconciliation(
            db,
            actor=principal,
            command=service.ExplainOpeningControlReconciliationCommand(
                reconciliation_run_id=reconciliation_run_id,
                expected_version=payload.expected_version,
                items=tuple(
                    service.OpeningControlExplanationInput(
                        reconciliation_item_id=row.reconciliation_item_id,
                        expected_version=row.expected_version,
                        explanation=row.explanation,
                        evidence_reference=row.evidence_reference,
                        evidence_file_id=row.evidence_file_id,
                    )
                    for row in payload.items
                ),
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningControlReconciliationExplainOut(
            reconciliation_run_id=result.reconciliation_run_id,
            task_id=result.task_id,
            status=result.status,
            version=result.version,
            explained_item_count=result.explained_item_count,
            explained_at=result.explained_at,
            replayed=result.replayed,
        )
        db.commit()
    except service.OpeningControlReconciliationError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_replay_header(response, output.replayed)
    return output


@router.post(
    "/{reconciliation_run_id}/approve",
    response_model=OpeningControlReconciliationApproveOut,
)
def approve_opening_control_reconciliation(
    reconciliation_run_id: UUID,
    payload: OpeningControlReconciliationApproveIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("reconciliation", "approve_opening")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        result = service.approve_opening_control_reconciliation(
            db,
            actor=principal,
            command=service.ApproveOpeningControlReconciliationCommand(
                reconciliation_run_id=reconciliation_run_id,
                expected_version=payload.expected_version,
                comment=payload.comment,
            ),
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        output = OpeningControlReconciliationApproveOut(
            reconciliation_run_id=result.reconciliation_run_id,
            task_id=result.task_id,
            status=result.status,
            version=result.version,
            resolved_item_count=result.resolved_item_count,
            approved_at=result.approved_at,
            replayed=result.replayed,
        )
        db.commit()
    except service.OpeningControlReconciliationError as exc:
        db.rollback()
        _raise_service_error(exc)
    except Exception:
        db.rollback()
        raise
    _set_replay_header(response, output.replayed)
    return output


def _read_call(function, db: Session, **kwargs):
    try:
        return function(db, **kwargs)
    except service.OpeningControlReconciliationError as exc:
        _raise_service_error(exc)


def _raise_service_error(exc: service.OpeningControlReconciliationError) -> None:
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


def _set_replay_header(response: Response, replayed: bool) -> None:
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"


__all__ = ["router"]
