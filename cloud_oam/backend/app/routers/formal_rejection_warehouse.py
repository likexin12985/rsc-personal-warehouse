"""Public source-warehouse receipt and independent inventory posting boundary."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import material_request_rejection_warehouse as warehouse
from ..formal_services import material_request_rejection_receipt as acceptance
from ..formal_services import material_request_rejection_inbound as inbound
from ..formal_services.material_request_query import MaterialRequestReadError
from ..formal_services.material_request_lifecycle import MaterialRequestLifecycleError
from ..formal_services.inventory_posting import InventoryPostingError
from ..material_request_rejection_receipt_schemas import RejectionReceiptIn, RejectionReceiptOut
from ..material_request_rejection_inbound_schemas import RejectionInboundIn, RejectionInboundOut
from ..material_request_rejection_warehouse_schemas import (RejectionWarehouseInboxOut, RejectionWarehouseDetailOut,
    RejectionWarehousePreviewOut, RejectionWarehouseReceiptStatusOut, RejectionWarehouseInboundStatusOut)
from .formal_material_requests import (_required_write_headers, _rejection_lookup_headers,
    _require_lifecycle_write_runtime, _require_lifecycle_idempotency_secret, _MaterialRequestAdapterError)

router = APIRouter(prefix='/v1/rejection-returns', tags=['formal-rejection-warehouse'])
PRIVATE = {'Cache-Control': 'private, no-store, max-age=0', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer'}


def _run(db, response, callback, model, *, write=False):
    response.headers.update(PRIVATE)
    try:
        result = model.model_validate(callback())
        if write:
            db.commit()
            response.headers['Idempotency-Replayed'] = 'true' if result.replayed else 'false'
        return result
    except (MaterialRequestReadError, MaterialRequestLifecycleError, InventoryPostingError, _MaterialRequestAdapterError) as exc:
        if write: db.rollback()
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail(), headers=PRIVATE) from None
    except (SQLAlchemyError, ValidationError):
        db.rollback()
        raise HTTPException(status_code=503, headers=PRIVATE, detail={'code': 'rejection_warehouse_unavailable',
            'message': '仓库退回结果暂时无法核验，请保留原请求，不要重复提交'}) from None
    except Exception:
        if write: db.rollback()
        raise


@router.get('/my-warehouse', response_model=RejectionWarehouseInboxOut)
def read_my_warehouse(response: Response, limit: int = Query(default=5, ge=1, le=20), after_id: UUID | None = None,
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')), db: Session = Depends(get_db)):
    return _run(db, response, lambda: warehouse.inbox(db, actor=principal, limit=limit, after_id=after_id), RejectionWarehouseInboxOut)


@router.get('/{return_id}/warehouse', response_model=RejectionWarehouseDetailOut)
def read_warehouse_return(return_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')), db: Session = Depends(get_db)):
    return _run(db, response, lambda: warehouse.detail(db, actor=principal, return_id=return_id), RejectionWarehouseDetailOut)


@router.get('/{return_id}/warehouse-receipts/{receipt_id}/inbounds/preview', response_model=RejectionWarehousePreviewOut)
def preview_warehouse_inbound(return_id: UUID, receipt_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')), db: Session = Depends(get_db)):
    return _run(db, response, lambda: warehouse.preview(db, actor=principal, return_id=return_id, receipt_id=receipt_id), RejectionWarehousePreviewOut)


@router.post('/{return_id}/warehouse-receipts', response_model=RejectionReceiptOut, status_code=201)
def accept_warehouse_return(return_id: UUID, payload: RejectionReceiptIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias='Idempotency-Key')] = None,
    request_id: Annotated[str | None, Header(alias='X-Request-ID')] = None):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    return _run(db, response, lambda: acceptance.record_rejection_receipt(db, actor=principal, return_id=return_id,
        payload=payload, idempotency_key=key, secret=_require_lifecycle_write_runtime(settings), trace_request_id=trace),
        RejectionReceiptOut, write=True)


@router.post('/{return_id}/warehouse-receipts/{receipt_id}/inbounds', response_model=RejectionInboundOut, status_code=201)
def post_warehouse_inbound(return_id: UUID, receipt_id: UUID, payload: RejectionInboundIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias='Idempotency-Key')] = None,
    request_id: Annotated[str | None, Header(alias='X-Request-ID')] = None):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    return _run(db, response, lambda: inbound.post(db, actor=principal, return_id=return_id, receipt_id=receipt_id,
        payload=payload, idempotency_key=key, secret=_require_lifecycle_write_runtime(settings), trace_request_id=trace),
        RejectionInboundOut, write=True)


@router.get('/{return_id}/warehouse-receipts/command-status', response_model=RejectionWarehouseReceiptStatusOut)
def warehouse_receipt_status(return_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias='Idempotency-Key')] = None,
    original_request_id: Annotated[str | None, Header(alias='X-Original-Request-ID')] = None,
    request_fingerprint: Annotated[str | None, Header(alias='X-Request-Fingerprint')] = None):
    key, trace, fingerprint = _rejection_lookup_headers(idempotency_key, original_request_id, request_fingerprint)
    def read():
        result = acceptance.rejection_receipt_command_status(db, actor=principal, return_id=return_id,
            idempotency_key=key, secret=_require_lifecycle_idempotency_secret(settings) if key else None,
            trace_request_id=trace, request_fingerprint=fingerprint)
        return dict(lookup_status='confirmed' if result else 'not_observed', command=result)
    return _run(db, response, read, RejectionWarehouseReceiptStatusOut)


@router.get('/{return_id}/warehouse-receipts/{receipt_id}/inbounds/command-status', response_model=RejectionWarehouseInboundStatusOut)
def warehouse_inbound_status(return_id: UUID, receipt_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias='Idempotency-Key')] = None,
    original_request_id: Annotated[str | None, Header(alias='X-Original-Request-ID')] = None,
    request_fingerprint: Annotated[str | None, Header(alias='X-Request-Fingerprint')] = None):
    key, trace, fingerprint = _rejection_lookup_headers(idempotency_key, original_request_id, request_fingerprint)
    def read():
        result = inbound.command_status(db, actor=principal, return_id=return_id, receipt_id=receipt_id,
            idempotency_key=key, secret=_require_lifecycle_idempotency_secret(settings) if key else None,
            trace_request_id=trace, request_fingerprint=fingerprint)
        return dict(lookup_status='confirmed' if result else 'not_observed', command=result)
    return _run(db, response, read, RejectionWarehouseInboundStatusOut)
