"""Current receiver reads; no acceptance or inventory write endpoints."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.stock_return_receiving import list_my_return_receiving, my_return_receiving_detail
from ..stock_return_receiving_schemas import StockReturnReceivingOut, StockReturnReceivingDetailOut
from .formal_stock_returns import _run

router = APIRouter(prefix="/v1/stock-returns/my-receiving", tags=["formal-stock-return-receiving"])


@router.get("", response_model=StockReturnReceivingOut)
def read_my_return_receiving(response: Response, limit: int = Query(10, ge=1, le=20),
    after_id: UUID | None = Query(None), db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read"))):
    return _run(db, response, lambda: list_my_return_receiving(db, actor=principal, limit=limit, after_id=after_id))


@router.get("/{shipment_id}", response_model=StockReturnReceivingDetailOut)
def read_my_return_receiving_detail(shipment_id: UUID, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read"))):
    return _run(db, response, lambda: my_return_receiving_detail(db, actor=principal, shipment_id=shipment_id))
