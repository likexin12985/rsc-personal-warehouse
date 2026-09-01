"""Formal, read-only V1.0 inventory API.

No endpoint in this router adjusts a balance or posts a transaction.  Business
documents will call the internal atomic posting service only after their own
state and authorization checks have passed.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import inventory_query
from ..inventory_schemas import (
    InventoryAccountPageOut,
    InventorySummaryOut,
    InventoryTransactionOut,
    PersonalWarehouseOut,
)


router = APIRouter(prefix="/v1/inventory", tags=["formal-inventory"])


@router.get("/summary", response_model=InventorySummaryOut)
def summary(
    principal: FormalPrincipal = Depends(require_permission("inventory", "read")),
    db: Session = Depends(get_db),
):
    return _call(inventory_query.inventory_summary, db, actor=principal)


@router.get("/personal/me", response_model=PersonalWarehouseOut)
def personal_me(
    principal: FormalPrincipal = Depends(require_permission("inventory", "read")),
    db: Session = Depends(get_db),
):
    return _call(inventory_query.personal_warehouse, db, actor=principal)


@router.get("/accounts", response_model=InventoryAccountPageOut)
def accounts(
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("inventory", "read")),
    db: Session = Depends(get_db),
):
    return _call(
        inventory_query.list_inventory_accounts,
        db,
        actor=principal,
        limit=limit,
        after_id=after_id,
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=InventoryTransactionOut,
)
def transaction_detail(
    transaction_id: UUID,
    principal: FormalPrincipal = Depends(require_permission("inventory", "read")),
    db: Session = Depends(get_db),
):
    return _call(
        inventory_query.inventory_transaction_detail,
        db,
        actor=principal,
        transaction_id=transaction_id,
    )


def _call(function, db: Session, **kwargs):
    try:
        return function(db, **kwargs)
    except inventory_query.InventoryReadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_detail(),
        ) from exc
