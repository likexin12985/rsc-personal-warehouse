"""Read-only recovery from a complete original execution command."""
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import stock_loss_disposition_recovery as recovery
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..stock_loss_schemas import StockLossDispositionExecuteIn
from ..stock_loss_return_schemas import StockLossReturnExecuteIn
from ..stock_loss_execution_recovery_schemas import DispositionRecoveryOut, ReturnRecoveryOut

router = APIRouter()
PRIVATE = {'Cache-Control': 'private, no-store'}


def _lookup(db, response, actor, request, flow, trace, key):
    response.headers.update(PRIVATE)
    for supplied, expected, code in (
        (trace, request.request_id, 'request_id_mismatch'),
        (key, request.idempotency_key, 'idempotency_key_mismatch'),
    ):
        if supplied is not None and supplied != expected:
            raise HTTPException(status_code=400, headers=PRIVATE, detail={
                'code': code, 'message': '请求头与完整原请求不一致，请保留原请求回查'})
    try:
        # No preview, execution, COMMIT, seal or retry fallback on this path.
        return recovery.lookup_disposition_request(db, actor=actor, request=request, flow=flow)
    except InventoryReadError as error:
        raise HTTPException(status_code=error.status_code, headers=PRIVATE, detail=error.as_detail()) from None
    except InventoryPostingError as error:
        raise HTTPException(status_code=error.http_status_code, headers=PRIVATE, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError):
        raise HTTPException(status_code=503, headers=PRIVATE, detail={
            'code': 'stock_loss_execution_lookup_unavailable',
            'message': '暂时无法核验原处置结果，请保留完整原请求稍后回查，勿自动重发',
        }) from None


@router.post('/dispositions/request-lookup', response_model=DispositionRecoveryOut)
def lookup_disposition(payload: StockLossDispositionExecuteIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    return _lookup(db, response, principal, payload, 'disposition', trace, key)


@router.post('/derived-returns/request-lookup', response_model=ReturnRecoveryOut)
def lookup_derived_return(payload: StockLossReturnExecuteIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read')),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    return _lookup(db, response, principal, payload, 'return', trace, key)
