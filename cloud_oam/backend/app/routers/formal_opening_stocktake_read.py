"""Read-only formal opening-stocktake list and detail endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import opening_stocktake_query
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
