"""Formal read-only material picker API."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import material_catalog
from ..material_catalog_schemas import MaterialCatalogPageOut


router = APIRouter(prefix="/v1/materials", tags=["formal-material-catalog"])


@router.get("", response_model=MaterialCatalogPageOut)
def list_formal_materials(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    query: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    principal: FormalPrincipal = Depends(require_permission("inventory", "read")),
    db: Session = Depends(get_db),
):
    try:
        output = material_catalog.list_active_materials(
            db,
            actor=principal,
            limit=limit,
            after_id=after_id,
            query=query,
        )
    except material_catalog.MaterialCatalogError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail=exc.as_detail(),
        ) from None
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return output


__all__ = ["router"]
