"""Exact recipient acceptance and recovery; stock posting remains independent."""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.stock_return_receipt_plan import preview_receipt
from ..formal_services.stock_return_receipt_commands import execute_receipt
from ..formal_services.stock_return_receipt_queries import receipt_history
from ..formal_services.stock_return_receipt_recovery import lookup_receipt_request, seal_receipt_request
from ..stock_return_schemas import StockReturnSealIn
from ..stock_return_receipt_schemas import (StockReturnReceiptPreviewIn, StockReturnReceiptPreviewOut,
    StockReturnReceiptSubmitIn, StockReturnReceiptOut, StockReturnReceiptHistoryOut, StockReturnReceiptSealedOut)
from .formal_stock_returns import _input, _run, _error

router = APIRouter(prefix='/v1/stock-returns/my-receiving/{shipment_id}/receipts', tags=['formal-stock-return-receipts'])


@router.get('', response_model=StockReturnReceiptHistoryOut)
def read_receipt_history(shipment_id: UUID, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _run(db, response, lambda: receipt_history(db, actor=principal, shipment_id=shipment_id))


@router.post('/preview', response_model=StockReturnReceiptPreviewOut)
def prepare_receipt(shipment_id: UUID, payload: StockReturnReceiptPreviewIn, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'receive_return'))):
    _input(payload, principal)
    return _run(db, response, lambda: preview_receipt(db, actor=principal, shipment_id=shipment_id, request=payload)[0])


@router.post('', response_model=StockReturnReceiptOut)
def record_receipt(shipment_id: UUID, payload: StockReturnReceiptSubmitIn, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'receive_return')),
    trace: str | None = Header(None, alias='X-Request-ID'), key: str | None = Header(None, alias='Idempotency-Key')):
    _input(payload, principal, trace, key)
    return _run(db, response, lambda: execute_receipt(db, actor=principal, shipment_id=shipment_id, request=payload), write=True)


def _lookup(db, **coordinates):
    result = lookup_receipt_request(db, **coordinates)
    if result is None:
        _error(404, 'stock_return_receipt_not_observed', '暂未观察到原验收结果，不代表未执行；请保留原请求回查或封存')
    return result


@router.get('/by-request/{request_id}', response_model=StockReturnReceiptOut | StockReturnReceiptSealedOut)
def read_receipt_request(shipment_id: UUID, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$'),
    db: Session = Depends(get_db), principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _run(db, response, lambda: _lookup(db, actor=principal, shipment_id=shipment_id, request_id=request_id))


@router.post('/by-request/{request_id}/seal', response_model=StockReturnReceiptOut | StockReturnReceiptSealedOut)
def seal_receipt(shipment_id: UUID, payload: StockReturnSealIn, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$'),
    db: Session = Depends(get_db), principal: FormalPrincipal = Depends(require_permission('stock_operation', 'receive_return')),
    trace: str | None = Header(None, alias='X-Request-ID'), key: str | None = Header(None, alias='Idempotency-Key')):
    _input(payload, principal, trace, key, request_id=request_id, seal=True)
    return _run(db, response, lambda: seal_receipt_request(db, actor=principal, shipment_id=shipment_id,
        request_id=request_id, request_hash=payload.request_hash), write=True)
