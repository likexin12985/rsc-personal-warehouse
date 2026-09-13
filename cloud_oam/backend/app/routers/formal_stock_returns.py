"""Own formal return commands with exact recovery and private responses."""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.stock_return_commands import submit_return, cancel_return
from ..formal_services.stock_return_plan import preview_return
from ..formal_services.stock_return_options import return_options
from ..formal_services.stock_return_history import return_history
from ..formal_services.stock_return_recovery import lookup_return_request, seal_return_request
from ..stock_return_outbound_schemas import StockReturnOutboundPreviewIn, StockReturnOutboundPreviewOut, StockReturnOutboundSubmitIn, StockReturnOutboundOut
from ..formal_services.stock_return_outbound_plan import preview_outbound
from ..formal_services.stock_return_outbound_commands import execute_outbound
from ..stock_return_schemas import (StockReturnPreviewIn, StockReturnPreviewOut, StockReturnSubmitIn, StockReturnOut,
    StockReturnCancelIn, StockReturnCancellationOut, StockReturnSealIn, StockReturnSealedOut, StockReturnOptionsOut, StockReturnHistoryOut)

from ..stock_return_outbound_schemas import StockReturnOutboundOptionsOut, StockReturnOutboundHistoryOut
from ..formal_services.stock_return_outbound_queries import outbound_options, outbound_history
from ..stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentPreviewOut, StockReturnShipmentSubmitIn, StockReturnShipmentOut
from ..formal_services.stock_return_shipment_plan import preview_shipment
from ..formal_services.stock_return_shipment_commands import execute_shipment
from ..stock_return_shipment_schemas import StockReturnShipmentOptionsOut, StockReturnShipmentHistoryOut
from ..formal_services.stock_return_shipment_queries import shipment_options, shipment_history

router = APIRouter(prefix="/v1/work-orders", tags=["formal-stock-returns"])
PRIVATE = {"Cache-Control": "private, no-store"}


def is_return_path(path: str) -> bool:
    parts = path.split("/")
    return len(parts) >= 6 and parts[:4] == ["", "api", "v1", "work-orders"] and parts[5] == "returns"


def _error(status, code, message):
    raise HTTPException(status_code=status, headers=PRIVATE, detail={"code": code, "message": message})


def _input(payload, principal, trace=None, key=None, *, request_id=None, seal=False):
    if payload.operator_person_id != principal.person_id:
        _error(403, "operator_mismatch", "操作人必须是当前登录人员")
    expected = request_id if seal else getattr(payload, "request_id", None)
    if trace is not None and trace != expected:
        _error(400, "request_id_mismatch", "请求头与原请求标识不一致")
    if key is not None and (seal or key != payload.idempotency_key):
        _error(400, "idempotency_key_mismatch", "请求幂等坐标不一致；封存只接受原请求标识")


def _run(db, response, callback, *, write=False):
    response.headers.update(PRIVATE)
    try:
        result = callback()
        if write: db.commit()
        return result
    except InventoryPostingError as exc:
        if write: db.rollback()
        raise HTTPException(status_code=exc.http_status_code, headers=PRIVATE, detail=exc.as_detail()) from None
    except InventoryReadError as exc:
        if write: db.rollback()
        raise HTTPException(status_code=exc.status_code, headers=PRIVATE, detail=exc.as_detail()) from None
    except SQLAlchemyError:
        if write: db.rollback()
        _error(503, "stock_return_unavailable", "退回结果暂时无法确认，请保留原请求并回查，勿自动重发")


def _lookup(db, **coordinates):
    result = lookup_return_request(db, **coordinates)
    if result is None:
        _error(404, "stock_return_not_observed", "暂未观察到原请求结果，不代表未执行；请保留原请求回查或封存")
    return result


@router.get("/{work_order_id}/returns/options", response_model=StockReturnOptionsOut)
def read_return_options(work_order_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "submit_return")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: return_options(db, actor=principal, work_order_id=work_order_id))


@router.get("/{work_order_id}/returns", response_model=StockReturnHistoryOut)
def read_return_history(work_order_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: return_history(db, actor=principal, work_order_id=work_order_id))


@router.post("/{work_order_id}/returns/preview", response_model=StockReturnPreviewOut)
def prepare_return(work_order_id: UUID, payload: StockReturnPreviewIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "submit_return")), db: Session = Depends(get_db)):
    _input(payload, principal)
    return _run(db, response, lambda: preview_return(db, actor=principal, work_order_id=work_order_id, request=payload)[0])


@router.post("/{work_order_id}/returns", response_model=StockReturnOut)
def execute_return(work_order_id: UUID, payload: StockReturnSubmitIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "submit_return")), db: Session = Depends(get_db),
    trace: str | None = Header(None, alias="X-Request-ID"), key: str | None = Header(None, alias="Idempotency-Key")):
    _input(payload, principal, trace, key)
    return _run(db, response, lambda: submit_return(db, actor=principal, work_order_id=work_order_id, request=payload), write=True)


@router.post("/{work_order_id}/returns/{operation_id}/cancellations", response_model=StockReturnCancellationOut)
def execute_cancellation(work_order_id: UUID, operation_id: UUID, payload: StockReturnCancelIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "cancel_return")), db: Session = Depends(get_db),
    trace: str | None = Header(None, alias="X-Request-ID"), key: str | None = Header(None, alias="Idempotency-Key")):
    _input(payload, principal, trace, key)
    return _run(db, response, lambda: cancel_return(db, actor=principal, work_order_id=work_order_id,
        operation_id=operation_id, request=payload), write=True)


@router.get("/{work_order_id}/returns/by-request/{request_id}", response_model=StockReturnOut | StockReturnSealedOut)
def read_return(work_order_id: UUID, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: _lookup(db, actor=principal, work_order_id=work_order_id,
        operation_type="submit_return", request_id=request_id))


@router.get("/{work_order_id}/returns/{operation_id}/cancellations/by-request/{request_id}", response_model=StockReturnCancellationOut | StockReturnSealedOut)
def read_cancellation(work_order_id: UUID, operation_id: UUID, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: _lookup(db, actor=principal, work_order_id=work_order_id,
        operation_type="cancel_return", operation_id=operation_id, request_id=request_id))


@router.post("/{work_order_id}/returns/by-request/{request_id}/seal", response_model=StockReturnOut | StockReturnSealedOut)
def seal_return(work_order_id: UUID, payload: StockReturnSealIn, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "submit_return")), db: Session = Depends(get_db),
    trace: str | None = Header(None, alias="X-Request-ID"), key: str | None = Header(None, alias="Idempotency-Key")):
    _input(payload, principal, trace, key, request_id=request_id, seal=True)
    return _run(db, response, lambda: seal_return_request(db, actor=principal, work_order_id=work_order_id,
        operation_type="submit_return", request_id=request_id, request_hash=payload.request_hash), write=True)


@router.post("/{work_order_id}/returns/{operation_id}/cancellations/by-request/{request_id}/seal", response_model=StockReturnCancellationOut | StockReturnSealedOut)
def seal_cancellation(work_order_id: UUID, operation_id: UUID, payload: StockReturnSealIn, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "cancel_return")), db: Session = Depends(get_db),
    trace: str | None = Header(None, alias="X-Request-ID"), key: str | None = Header(None, alias="Idempotency-Key")):
    _input(payload, principal, trace, key, request_id=request_id, seal=True)
    return _run(db, response, lambda: seal_return_request(db, actor=principal, work_order_id=work_order_id,
        operation_type="cancel_return", operation_id=operation_id, request_id=request_id, request_hash=payload.request_hash), write=True)


@router.post("/{work_order_id}/returns/{operation_id}/outbounds/preview", response_model=StockReturnOutboundPreviewOut)
def prepare_return_outbound(work_order_id: UUID, operation_id: UUID, payload: StockReturnOutboundPreviewIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "outbound_return")), db: Session = Depends(get_db)):
    _input(payload, principal)
    return _run(db, response, lambda: preview_outbound(db, actor=principal, work_order_id=work_order_id,
        operation_id=operation_id, request=payload)[0])


@router.post("/{work_order_id}/returns/{operation_id}/outbounds", response_model=StockReturnOutboundOut)
def submit_return_outbound(work_order_id: UUID, operation_id: UUID, payload: StockReturnOutboundSubmitIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "outbound_return")), db: Session = Depends(get_db),
    trace: str | None = Header(None, alias="X-Request-ID"), key: str | None = Header(None, alias="Idempotency-Key")):
    _input(payload, principal, trace, key)
    return _run(db, response, lambda: execute_outbound(db, actor=principal, work_order_id=work_order_id,
        operation_id=operation_id, request=payload), write=True)


@router.get("/{work_order_id}/returns/{operation_id}/outbounds/by-request/{request_id}", response_model=StockReturnOutboundOut | StockReturnSealedOut)
def read_return_outbound(work_order_id: UUID, operation_id: UUID, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: _lookup(db, actor=principal, work_order_id=work_order_id,
        operation_type="outbound_return", operation_id=operation_id, request_id=request_id))


@router.post("/{work_order_id}/returns/{operation_id}/outbounds/by-request/{request_id}/seal", response_model=StockReturnOutboundOut | StockReturnSealedOut)
def seal_return_outbound(work_order_id: UUID, operation_id: UUID, payload: StockReturnSealIn, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "outbound_return")), db: Session = Depends(get_db),
    trace: str | None = Header(None, alias="X-Request-ID"), key: str | None = Header(None, alias="Idempotency-Key")):
    _input(payload, principal, trace, key, request_id=request_id, seal=True)
    return _run(db, response, lambda: seal_return_request(db, actor=principal, work_order_id=work_order_id,
        operation_type="outbound_return", operation_id=operation_id, request_id=request_id, request_hash=payload.request_hash), write=True)


@router.get("/{work_order_id}/returns/{operation_id}/outbounds/options", response_model=StockReturnOutboundOptionsOut)
def read_outbound_options(work_order_id: UUID, operation_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "outbound_return")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: outbound_options(db, actor=principal, work_order_id=work_order_id, operation_id=operation_id))


@router.get("/{work_order_id}/returns/{operation_id}/outbounds", response_model=StockReturnOutboundHistoryOut)
def read_outbound_history(work_order_id: UUID, operation_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: outbound_history(db, actor=principal, work_order_id=work_order_id, operation_id=operation_id))


@router.post("/{work_order_id}/returns/{operation_id}/shipments/preview", response_model=StockReturnShipmentPreviewOut)
def prepare_return_shipment(work_order_id: UUID, operation_id: UUID, payload: StockReturnShipmentPreviewIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "ship_return")), db: Session = Depends(get_db)):
    _input(payload, principal)
    return _run(db, response, lambda: preview_shipment(db, actor=principal, work_order_id=work_order_id,
        operation_id=operation_id, request=payload)[0])


@router.post("/{work_order_id}/returns/{operation_id}/shipments", response_model=StockReturnShipmentOut)
def submit_return_shipment(work_order_id: UUID, operation_id: UUID, payload: StockReturnShipmentSubmitIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "ship_return")), db: Session = Depends(get_db),
    trace: str | None = Header(None, alias="X-Request-ID"), key: str | None = Header(None, alias="Idempotency-Key")):
    _input(payload, principal, trace, key)
    return _run(db, response, lambda: execute_shipment(db, actor=principal, work_order_id=work_order_id,
        operation_id=operation_id, request=payload), write=True)


@router.get("/{work_order_id}/returns/{operation_id}/shipments/by-request/{request_id}", response_model=StockReturnShipmentOut | StockReturnSealedOut)
def read_return_shipment(work_order_id: UUID, operation_id: UUID, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: _lookup(db, actor=principal, work_order_id=work_order_id,
        operation_type="ship_return", operation_id=operation_id, request_id=request_id))


@router.post("/{work_order_id}/returns/{operation_id}/shipments/by-request/{request_id}/seal", response_model=StockReturnShipmentOut | StockReturnSealedOut)
def seal_return_shipment(work_order_id: UUID, operation_id: UUID, payload: StockReturnSealIn, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "ship_return")), db: Session = Depends(get_db),
    trace: str | None = Header(None, alias="X-Request-ID"), key: str | None = Header(None, alias="Idempotency-Key")):
    _input(payload, principal, trace, key, request_id=request_id, seal=True)
    return _run(db, response, lambda: seal_return_request(db, actor=principal, work_order_id=work_order_id,
        operation_type="ship_return", operation_id=operation_id, request_id=request_id, request_hash=payload.request_hash), write=True)


@router.get("/{work_order_id}/returns/{operation_id}/shipments/options", response_model=StockReturnShipmentOptionsOut)
def read_shipment_options(work_order_id: UUID, operation_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "ship_return")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: shipment_options(db, actor=principal, work_order_id=work_order_id, operation_id=operation_id))


@router.get("/{work_order_id}/returns/{operation_id}/shipments", response_model=StockReturnShipmentHistoryOut)
def read_shipment_history(work_order_id: UUID, operation_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("stock_operation", "read")), db: Session = Depends(get_db)):
    return _run(db, response, lambda: shipment_history(db, actor=principal, work_order_id=work_order_id, operation_id=operation_id))
