"""Read-only opening eligibility directories; never a stocktake start permit."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import opening_start_options as service
from ..opening_start_option_schemas import (
    OpeningStartAssigneeOptionPageOut,
    OpeningStartAssetOwnerOptionPageOut,
    OpeningStartLocationOptionPageOut,
    OpeningStartRegionOptionPageOut,
)


_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
_QUERY_KEYS = {
    "regions": frozenset({"limit", "after_id"}),
    "asset-owners": frozenset({"region_org_id", "limit", "after_id"}),
    "locations": frozenset({"region_org_id", "owner_org_id", "limit", "after_id"}),
    "assignees": frozenset({
        "region_org_id", "owner_org_id", "location_id", "limit", "after_person_id",
    }),
}


def _require_exact_query(request: Request) -> None:
    allowed = _QUERY_KEYS.get(request.url.path.rsplit("/", 1)[-1], frozenset())
    keys = [key for key, _ in request.query_params.multi_items()]
    if len(keys) != len(set(keys)) or not set(keys).issubset(allowed):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "opening_start_options_query_invalid",
                "category": "invalid_request",
                "message": "期初盘点选项仅接受当前层级的唯一查询坐标",
            },
            headers=_PRIVATE_HEADERS,
        )


router = APIRouter(
    prefix="/v1/stocktakes/opening/start-options",
    tags=["formal-opening-start-options"],
    dependencies=[Depends(_require_exact_query)],
)


def _read(response: Response, query, db: Session, principal: FormalPrincipal, **coordinates):
    try:
        result = query(db, actor=principal, **coordinates)
    except service.OpeningStartOptionError as exc:
        raise HTTPException(
            status_code=exc.http_status_code, detail=exc.as_detail(),
            headers=_PRIVATE_HEADERS,
        ) from None
    response.headers.update(_PRIVATE_HEADERS)
    return result


@router.get("/regions", response_model=OpeningStartRegionOptionPageOut)
def list_opening_regions(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    return _read(response, service.list_region_options, db, principal,
                 limit=limit, after_id=after_id)


@router.get("/asset-owners", response_model=OpeningStartAssetOwnerOptionPageOut)
def list_opening_asset_owners(
    response: Response,
    region_org_id: Annotated[UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    return _read(response, service.list_asset_owner_options, db, principal,
                 region_org_id=region_org_id, limit=limit, after_id=after_id)


@router.get("/locations", response_model=OpeningStartLocationOptionPageOut)
def list_opening_locations(
    response: Response,
    region_org_id: Annotated[UUID, Query()],
    owner_org_id: Annotated[UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    return _read(response, service.list_location_options, db, principal,
                 region_org_id=region_org_id, owner_org_id=owner_org_id,
                 limit=limit, after_id=after_id)


@router.get("/assignees", response_model=OpeningStartAssigneeOptionPageOut)
def list_opening_assignees(
    response: Response,
    region_org_id: Annotated[UUID, Query()],
    owner_org_id: Annotated[UUID, Query()],
    location_id: Annotated[UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_person_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("stocktake", "manage")),
    db: Session = Depends(get_db),
):
    return _read(response, service.list_assignee_options, db, principal,
                 region_org_id=region_org_id, owner_org_id=owner_org_id,
                 location_id=location_id, limit=limit, after_person_id=after_person_id)


__all__ = ["router"]
