"""Read-only picker APIs for formal managed stocktake creation."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import stocktake_options
from ..stocktake_option_schemas import (
    StocktakeAssigneeOptionPageOut,
    StocktakeLocationOptionPageOut,
    StocktakeRegionOptionPageOut,
)


router = APIRouter(prefix="/v1/stocktake-options", tags=["formal-stocktake-options"])


@router.get("/regions", response_model=StocktakeRegionOptionPageOut)
def list_formal_stocktake_region_options(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    try:
        output = stocktake_options.list_region_options(
            db,
            actor=principal,
            limit=limit,
            after_id=after_id,
        )
    except stocktake_options.StocktakeOptionError as exc:
        _raise_service_error(exc)
    _set_no_store(response)
    return output


@router.get("/locations", response_model=StocktakeLocationOptionPageOut)
def list_formal_stocktake_location_options(
    response: Response,
    region_org_id: Annotated[UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    try:
        output = stocktake_options.list_location_options(
            db,
            actor=principal,
            region_org_id=region_org_id,
            limit=limit,
            after_id=after_id,
        )
    except stocktake_options.StocktakeOptionError as exc:
        _raise_service_error(exc)
    _set_no_store(response)
    return output


@router.get("/assignees", response_model=StocktakeAssigneeOptionPageOut)
def list_formal_stocktake_assignee_options(
    response: Response,
    region_org_id: Annotated[UUID, Query()],
    location_id: Annotated[UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_person_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    try:
        output = stocktake_options.list_assignee_options(
            db,
            actor=principal,
            region_org_id=region_org_id,
            location_id=location_id,
            limit=limit,
            after_person_id=after_person_id,
        )
    except stocktake_options.StocktakeOptionError as exc:
        _raise_service_error(exc)
    _set_no_store(response)
    return output


def _raise_service_error(exc: stocktake_options.StocktakeOptionError) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from None


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"


__all__ = ["router"]
