"""Read-only formal opening-stocktake list and detail endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import (
    opening_count_command_status as count_status_service,
    opening_recount_assignee_options as recount_assignee_option_service,
    opening_stocktake_query,
)
from ..opening_count_command_status_schemas import OpeningCountCommandStatusOut
from ..opening_recount_assignee_option_schemas import (
    OpeningRecountAssigneeOptionPageOut,
)
from ..opening_stocktake_read_schemas import (
    OpeningStocktakeTaskDetailOut,
    OpeningStocktakeTaskPageOut,
)


router = APIRouter(
    prefix="/v1/stocktakes/opening",
    tags=["formal-opening-stocktake-read"],
)


@router.get("", response_model=OpeningStocktakeTaskPageOut)
def list_opening_stocktakes(
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "read")),
    db: Session = Depends(get_db),
):
    return _call(
        opening_stocktake_query.list_opening_stocktakes,
        db,
        actor=principal,
        limit=limit,
        after_id=after_id,
    )


@router.get(
    "/{task_id}/rounds/{source_round_id}/scopes/{scope_id}/assignees",
    response_model=OpeningRecountAssigneeOptionPageOut,
)
def list_opening_recount_assignee_options(
    task_id: UUID,
    source_round_id: UUID,
    scope_id: UUID,
    response: Response,
    expected_task_version: Annotated[int, Query(ge=0)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    after_person_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    try:
        return recount_assignee_option_service.list_opening_recount_assignee_options(
            db,
            actor=principal,
            task_id=task_id,
            source_round_id=source_round_id,
            scope_id=scope_id,
            expected_task_version=expected_task_version,
            limit=limit,
            after_person_id=after_person_id,
        )
    except recount_assignee_option_service.OpeningRecountAssigneeOptionError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail=exc.as_detail(),
            headers={"Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff"},
        ) from exc


@router.get(
    "/{task_id}/rounds/{round_id}/scopes/{scope_id}/count-command-status",
    response_model=OpeningCountCommandStatusOut,
)
def opening_count_command_status(
    task_id: UUID,
    round_id: UUID,
    scope_id: UUID,
    request: Request,
    response: Response,
    actor_person_id: Annotated[UUID, Query()],
    actor_authorization_version: Annotated[int, Query(ge=1)],
    trace_request_id: Annotated[str, Query(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")],
    principal: FormalPrincipal = Depends(require_permission("stocktake", "read")),
    db: Session = Depends(get_db),
):
    headers = {"Cache-Control": "private, no-store, max-age=0", "X-Content-Type-Options": "nosniff"}
    response.headers.update(headers)
    try:
        allowed = {"actor_person_id", "actor_authorization_version", "trace_request_id"}
        if (
            set(request.query_params) != allowed
            or any(len(request.query_params.getlist(key)) != 1 for key in allowed)
            or "idempotency-key" in request.headers
            or request.headers.get("content-length", "0") != "0"
            or "transfer-encoding" in request.headers
        ):
            count_status_service._invalid_input()
        return count_status_service.opening_count_command_status(
            db, actor=principal, task_id=task_id, round_id=round_id, scope_id=scope_id,
            actor_person_id=actor_person_id,
            actor_authorization_version=actor_authorization_version,
            trace_request_id=trace_request_id,
        )
    except count_status_service.OpeningCountCommandStatusError as exc:
        raise HTTPException(
            status_code=exc.http_status_code, detail=exc.as_detail(), headers=headers,
        ) from None


@router.get("/{task_id}", response_model=OpeningStocktakeTaskDetailOut)
def opening_stocktake_detail(
    task_id: UUID,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "read")),
    db: Session = Depends(get_db),
):
    return _call(
        opening_stocktake_query.opening_stocktake_detail,
        db,
        actor=principal,
        task_id=task_id,
    )


def _call(function, db: Session, **kwargs):
    try:
        return function(db, **kwargs)
    except opening_stocktake_query.OpeningStocktakeReadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc


__all__ = ["router"]
