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
from ..formal_services.stock_return_inbound_recovery import (
    lookup_return_inbound_request,
    seal_return_inbound_request,
)
from ..formal_services.stock_return_inbound_queries import read_return_inbound_state
from ..stock_return_inbound_schemas import (
    StockReturnInboundOut,
    StockReturnInboundPreviewOut,
    StockReturnInboundSealIn,
    StockReturnInboundSealOut,
    StockReturnInboundSubmitIn,
    StockReturnInboundStateOut,
)
from .formal_stock_returns import _error, _input, _run

router = APIRouter(
    prefix="/v1/stock-returns/my-receiving/{receipt_id}/inbound",
    tags=["formal-stock-return-inbounds"],
)


@router.get('', response_model=StockReturnInboundStateOut)
def read_inbound_state(
    receipt_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation','read')),
):
    return _run(db,response,lambda: read_return_inbound_state(db,actor=principal,receipt_id=receipt_id))


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


def _lookup(db, *, actor, receipt_id, request_id):
    result = lookup_return_inbound_request(
        db, actor=actor, receipt_id=receipt_id, request_id=request_id,
    )
    if result is None:
        _error(404, "stock_return_inbound_not_observed", "暂未观察到原入账结果，请保留原请求回查")
    return result


@router.get("/by-request/{request_id}", response_model=StockReturnInboundOut | StockReturnInboundSealOut)
def read_inbound_request(
    receipt_id: UUID,
    request_id: str,
    response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read")),
):
    return _run(db, response, lambda: _lookup(
        db, actor=principal, receipt_id=receipt_id, request_id=request_id,
    ))


@router.post("/by-request/{request_id}/seal", response_model=StockReturnInboundOut | StockReturnInboundSealOut)
def seal_inbound_request(
    receipt_id: UUID,
    request_id: str,
    payload: StockReturnInboundSealIn,
    response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "receive_return")),
    trace: str | None = Header(None, alias="X-Request-ID"),
    key: str | None = Header(None, alias="Idempotency-Key"),
):
    if trace is not None and trace != request_id:
        _error(400, "request_id_mismatch", "请求头与原请求标识不一致")
    if key is not None:
        _error(400, "idempotency_key_mismatch", "封存不接受新的幂等键")
    return _run(db, response, lambda: seal_return_inbound_request(
        db, actor=principal, receipt_id=receipt_id, request_id=request_id,
        request_hash=payload.request_hash,
    ), write=True)
