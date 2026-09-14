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
    PersonalWarehouseTransactionPageOut,
    PersonalWarehouseSerialPageOut,
    PersonalQrResolutionOut,
    PersonalWarehouseOut,
)


router = APIRouter(prefix="/v1/inventory", tags=["formal-inventory"])


qr_router = APIRouter(prefix="/v1/scan", tags=["formal-scan"])


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


@router.get("/personal/me/transactions", response_model=PersonalWarehouseTransactionPageOut)
def personal_transactions(
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    after_cursor: Annotated[int | None, Query(ge=1)] = None,
    principal: FormalPrincipal = Depends(require_permission("inventory", "read")),
    db: Session = Depends(get_db),
):
    return _call(
        inventory_query.personal_warehouse_transactions,
        db,
        actor=principal,
        limit=limit,
        after_cursor=after_cursor,
    )


@router.get(
    "/personal/me/accounts/{stock_account_id}/serials",
    response_model=PersonalWarehouseSerialPageOut,
)
def personal_account_serials(
    stock_account_id: UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    serial_no: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    principal: FormalPrincipal = Depends(require_permission("inventory", "read")),
    db: Session = Depends(get_db),
):
    return _call(
        inventory_query.personal_warehouse_serials,
        db,
        actor=principal,
        stock_account_id=stock_account_id,
        limit=limit,
        after_id=after_id,
        serial_no=serial_no,
    )


@qr_router.get("/qr", response_model=PersonalQrResolutionOut)
def resolve_qr(
    code: Annotated[str, Query(min_length=1, max_length=250)],
    principal: FormalPrincipal = Depends(require_permission("inventory", "read")),
    db: Session = Depends(get_db),
):
    return _call(
        inventory_query.resolve_personal_qr,
        db,
        actor=principal,
        code=code,
    )


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
