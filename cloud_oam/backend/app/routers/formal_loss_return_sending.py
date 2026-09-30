"""Own loss-derived return reads; physical commands have separate recovery gates."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.loss_return_sender_directory import list_sender_returns
from ..formal_services.loss_return_sender_detail import read_sender_return
from ..loss_return_sender_detail_schemas import LossReturnSenderDetailOut
from ..formal_services.stock_return_outbound_queries import outbound_options, outbound_history
from ..formal_services.stock_return_shipment_queries import shipment_options, shipment_history
from ..loss_return_sender_schemas import LossReturnSenderDirectoryOut
from ..loss_return_outbound_schemas import LossReturnOutboundOptionsOut, LossReturnOutboundHistoryOut
from ..loss_return_shipment_schemas import LossReturnShipmentOptionsOut, LossReturnShipmentHistoryOut
from .formal_stock_returns import _run

router = APIRouter(prefix='/returns', tags=['formal-loss-return-sending'])


@router.get('/my-sending', response_model=LossReturnSenderDirectoryOut)
def read_directory(response: Response, limit: int = Query(25, ge=1, le=50),
    after_id: UUID | None = Query(None), snapshot_hash: str | None = Query(None, pattern='^[0-9a-f]{64}$'),
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _run(db, response, lambda: list_sender_returns(db, actor=principal,
        limit=limit, after_id=after_id, snapshot_hash=snapshot_hash))


@router.get('/{operation_id}/outbounds/options', response_model=LossReturnOutboundOptionsOut)
def read_outbound_options(operation_id: UUID, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'outbound_return'))):
    return _run(db, response, lambda: outbound_options(db, actor=principal, work_order_id=None, operation_id=operation_id))


@router.get('/{operation_id}/outbounds', response_model=LossReturnOutboundHistoryOut)
def read_outbounds(operation_id: UUID, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _run(db, response, lambda: outbound_history(db, actor=principal, work_order_id=None, operation_id=operation_id))


@router.get('/{operation_id}/shipments/options', response_model=LossReturnShipmentOptionsOut)
def read_shipment_options(operation_id: UUID, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'ship_return'))):
    return _run(db, response, lambda: shipment_options(db, actor=principal, work_order_id=None, operation_id=operation_id))


@router.get('/{operation_id}/shipments', response_model=LossReturnShipmentHistoryOut)
def read_shipments(operation_id: UUID, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _run(db, response, lambda: shipment_history(db, actor=principal, work_order_id=None, operation_id=operation_id))


@router.get('/{operation_id}', response_model=LossReturnSenderDetailOut)
def read_original_return(operation_id: UUID, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _run(db, response, lambda: read_sender_return(db, actor=principal, operation_id=operation_id))


from fastapi import Header
from ..stock_return_outbound_schemas import StockReturnOutboundPreviewIn, StockReturnOutboundSubmitIn
from ..stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from ..loss_return_outbound_schemas import LossReturnOutboundPreviewOut, LossReturnOutboundOut
from ..loss_return_shipment_schemas import LossReturnShipmentPreviewOut, LossReturnShipmentOut
from ..loss_return_sender_recovery_schemas import LossSenderOutboundLookupOut, LossSenderShipmentLookupOut
from ..formal_services import stock_return_outbound_plan, stock_return_outbound_commands
from ..formal_services import stock_return_shipment_plan, stock_return_shipment_commands
from ..formal_services.loss_return_sender_recovery import lookup_sender_request
from ..formal_services.loss_return_sender_seals import seal_sender_request
from .formal_stock_returns import _input


@router.post('/{operation_id}/outbounds/preview', response_model=LossReturnOutboundPreviewOut)
def preview_departure(operation_id: UUID, payload: StockReturnOutboundPreviewIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'outbound_return'))):
    _input(payload, principal)
    return _run(db, response, lambda: stock_return_outbound_plan.preview_outbound(
        db, actor=principal, work_order_id=None, operation_id=operation_id, request=payload)[0])


@router.post('/{operation_id}/outbounds', response_model=LossReturnOutboundOut)
def submit_departure(operation_id: UUID, payload: StockReturnOutboundSubmitIn, response: Response,
    trace: str | None = Header(None, alias='X-Request-ID'), key: str | None = Header(None, alias='Idempotency-Key'),
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'outbound_return'))):
    _input(payload, principal, trace, key)
    return _run(db, response, lambda: stock_return_outbound_commands.execute_outbound(
        db, actor=principal, work_order_id=None, operation_id=operation_id, request=payload), write=True)


@router.post('/{operation_id}/shipments/preview', response_model=LossReturnShipmentPreviewOut)
def preview_parcel(operation_id: UUID, payload: StockReturnShipmentPreviewIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'ship_return'))):
    _input(payload, principal)
    return _run(db, response, lambda: stock_return_shipment_plan.preview_shipment(
        db, actor=principal, work_order_id=None, operation_id=operation_id, request=payload)[0])


@router.post('/{operation_id}/shipments', response_model=LossReturnShipmentOut)
def submit_parcel(operation_id: UUID, payload: StockReturnShipmentSubmitIn, response: Response,
    trace: str | None = Header(None, alias='X-Request-ID'), key: str | None = Header(None, alias='Idempotency-Key'),
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'ship_return'))):
    _input(payload, principal, trace, key)
    return _run(db, response, lambda: stock_return_shipment_commands.execute_shipment(
        db, actor=principal, work_order_id=None, operation_id=operation_id, request=payload), write=True)


@router.post('/{operation_id}/outbounds/requests/lookup', response_model=LossSenderOutboundLookupOut)
def lookup_departure(operation_id: UUID, payload: StockReturnOutboundSubmitIn, response: Response,
    db: Session = Depends(get_db), principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    _input(payload, principal)
    return _run(db, response, lambda: lookup_sender_request(db, actor=principal,
        operation_type='outbound_return', operation_id=operation_id, request=payload))


@router.post('/{operation_id}/shipments/requests/lookup', response_model=LossSenderShipmentLookupOut)
def lookup_parcel(operation_id: UUID, payload: StockReturnShipmentSubmitIn, response: Response,
    db: Session = Depends(get_db), principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    _input(payload, principal)
    return _run(db, response, lambda: lookup_sender_request(db, actor=principal,
        operation_type='ship_return', operation_id=operation_id, request=payload))


@router.post('/{operation_id}/outbounds/requests/seal', response_model=LossSenderOutboundLookupOut)
def seal_departure(operation_id: UUID, payload: StockReturnOutboundSubmitIn, response: Response,
    trace: str | None = Header(None, alias='X-Request-ID'), key: str | None = Header(None, alias='Idempotency-Key'),
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'outbound_return'))):
    _input(payload, principal, trace, key, request_id=payload.request_id, seal=True)
    return _run(db, response, lambda: seal_sender_request(db, actor=principal,
        operation_type='outbound_return', operation_id=operation_id, request=payload), write=True)


@router.post('/{operation_id}/shipments/requests/seal', response_model=LossSenderShipmentLookupOut)
def seal_parcel(operation_id: UUID, payload: StockReturnShipmentSubmitIn, response: Response,
    trace: str | None = Header(None, alias='X-Request-ID'), key: str | None = Header(None, alias='Idempotency-Key'),
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'ship_return'))):
    _input(payload, principal, trace, key, request_id=payload.request_id, seal=True)
    return _run(db, response, lambda: seal_sender_request(db, actor=principal,
        operation_type='ship_return', operation_id=operation_id, request=payload), write=True)
