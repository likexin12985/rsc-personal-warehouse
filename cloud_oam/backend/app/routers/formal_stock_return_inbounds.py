"""Independent local-inventory inbound after return parcel acceptance."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.stock_return_inbound_commands import (
    execute_return_inbound,
    preview_return_inbound,
)
from ..stock_return_inbound_schemas import (
    StockReturnInboundOut,
    StockReturnInboundPreviewOut,
    StockReturnInboundSubmitIn,
)
from .formal_stock_returns import _input, _run

router = APIRouter(
    prefix="/v1/stock-returns/my-receiving/{receipt_id}/inbound",
    tags=["formal-stock-return-inbounds"],
)


@router.post("/preview", response_model=StockReturnInboundPreviewOut)
def prepare_inbound(
    receipt_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "receive_return")),
):
    return _run(db, response, lambda: preview_return_inbound(db, actor=principal, receipt_id=receipt_id))


@router.post("", response_model=StockReturnInboundOut)
def post_inbound(
    receipt_id: UUID,
    payload: StockReturnInboundSubmitIn,
    response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "receive_return")),
    trace: str | None = Header(None, alias="X-Request-ID"),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    _input(payload, principal, trace, key)
    return _run(
        db,
        response,
        lambda: execute_return_inbound(
            db,
            actor=principal,
            receipt_id=receipt_id,
            expected_plan_hash=payload.expected_plan_hash,
            request_id=payload.request_id,
            idempotency_key=payload.idempotency_key,
        ),
        write=True,
    )

