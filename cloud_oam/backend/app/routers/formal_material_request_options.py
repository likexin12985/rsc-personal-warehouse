"""Read-only picker APIs for formal material-request creation."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_formal_principal
from ..formal_access import FormalPrincipal
from ..formal_services import material_request_options
from ..material_request_option_schemas import (
    MaterialRequestWorkOrderOptionDetailOut,
    MaterialRequestWorkOrderOptionPageOut,
)


router = APIRouter(
    prefix="/v1/material-request-options",
    tags=["formal-material-request-options"],
)


def require_work_order_option_permission(
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
) -> FormalPrincipal:
    """Allow picker use only in a legitimate create or draft-edit workflow."""

    if not (
        principal.allows(db, "material_request", "create")
        or principal.allows(db, "material_request", "update_draft")
    ):
        raise HTTPException(status_code=403, detail="没有此操作权限")
    return principal


@router.get("/work-orders", response_model=MaterialRequestWorkOrderOptionPageOut)
def list_formal_material_request_work_order_options(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    query: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    principal: FormalPrincipal = Depends(require_work_order_option_permission),
    db: Session = Depends(get_db),
):
    try:
        output = material_request_options.list_work_order_options(
            db,
            actor=principal,
            limit=limit,
            after_id=after_id,
            query=query,
        )
    except material_request_options.MaterialRequestOptionError as exc:
        _raise_service_error(exc)
    _set_no_store(response)
    return output


@router.get(
    "/work-orders/{work_order_id}",
    response_model=MaterialRequestWorkOrderOptionDetailOut,
)
def formal_material_request_work_order_option_detail(
    work_order_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(require_work_order_option_permission),
    db: Session = Depends(get_db),
):
    try:
        output = material_request_options.work_order_option_detail(
            db,
            actor=principal,
            work_order_id=work_order_id,
        )
    except material_request_options.MaterialRequestOptionError as exc:
        _raise_service_error(exc)
    _set_no_store(response)
    return output


def _raise_service_error(exc: material_request_options.MaterialRequestOptionError) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from None


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"


__all__ = ["require_work_order_option_permission", "router"]
