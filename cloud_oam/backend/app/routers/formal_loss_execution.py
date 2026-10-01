"""HQ-approved disposition and return commands with commit-before-acknowledge."""
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import stock_loss_disposition_commands as dispositions
from ..formal_services import stock_loss_disposition_plan as disposition_plan
from ..formal_services import stock_loss_disposition_seals as seals
from ..formal_services import stock_loss_return_commands as returns
from ..formal_services import stock_loss_return_plan as return_plan
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..stock_loss_schemas import StockLossDispositionPreviewIn, StockLossDispositionExecuteIn
from ..stock_loss_return_schemas import StockLossReturnPreviewIn, StockLossReturnExecuteIn
from ..stock_loss_disposition_seal_schemas import StockLossDispositionSealIn, StockLossDerivedReturnSealIn
from ..stock_loss_execution_recovery_schemas import (
    AccountDispositionFact, DerivedReturnFact, DispositionFound, ReturnFound, ExecutionSealed,
)
from ..stock_loss_execution_schemas import DispositionPreviewOut, DerivedReturnPreviewOut, public_preview

router = APIRouter()
PRIVATE = {'Cache-Control': 'private, no-store'}


def _coordinates(command, trace, key):
    for supplied, expected, code in (
        (trace, command.request_id, 'request_id_mismatch'),
        (key, command.idempotency_key, 'idempotency_key_mismatch'),
    ):
        if supplied is not None and supplied != expected:
            raise HTTPException(400, headers=PRIVATE, detail={
                'code': code, 'message': '请求头与完整原请求不一致，请保留原请求回查'})


def _rollback(db):
    try:
        db.rollback()
    except SQLAlchemyError:
        # A broken connection cannot establish whether COMMIT succeeded.
        # Do not turn it into a clean failure or invite a new write.
        pass


def _call(db, response, callback, schema, *, write=False):
    response.headers.update(PRIVATE)
    try:
        result = TypeAdapter(schema).validate_python(callback())
        # Materialize and validate the public JSON while rollback is still
        # possible, then acknowledge only after the transaction commits.
        answer = result.model_dump(mode='json')
        if write:
            db.commit()
        return answer
    except (InventoryReadError, InventoryPostingError) as error:
        if write:
            _rollback(db)
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status, headers=PRIVATE, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError, ValidationError):
        if write:
            _rollback(db)
        raise HTTPException(503, headers=PRIVATE, detail={
            'code': 'stock_loss_execution_unconfirmed' if write else 'stock_loss_execution_preview_unavailable',
            'message': ('处置结果暂时无法确认，请保留完整原请求回查，勿自动重发'
                if write else '暂时无法核验处置方案，请稍后重新预览'),
        }) from None
    except BaseException:
        if write:
            _rollback(db)
        raise


@router.post('/dispositions/preview', response_model=DispositionPreviewOut)
def preview_disposition(payload: StockLossDispositionPreviewIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'dispose_loss'))):
    return _call(db, response, lambda: public_preview(DispositionPreviewOut,
        disposition_plan.preview_disposition(db, actor=principal, request=payload)), DispositionPreviewOut)


@router.post('/derived-returns/preview', response_model=DerivedReturnPreviewOut)
def preview_return(payload: StockLossReturnPreviewIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'dispose_loss'))):
    return _call(db, response, lambda: public_preview(DerivedReturnPreviewOut,
        return_plan.preview_loss_return(db, actor=principal, request=payload)), DerivedReturnPreviewOut)


@router.post('/dispositions', response_model=AccountDispositionFact)
def execute_disposition(payload: StockLossDispositionExecuteIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'dispose_loss')),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    _coordinates(payload, trace, key)
    return _call(db, response, lambda: dispositions.execute_disposition(
        db, actor=principal, request=payload), AccountDispositionFact, write=True)


@router.post('/derived-returns', response_model=DerivedReturnFact)
def execute_return(payload: StockLossReturnExecuteIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'dispose_loss')),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    _coordinates(payload, trace, key)
    return _call(db, response, lambda: returns.execute_loss_return(
        db, actor=principal, request=payload), DerivedReturnFact, write=True)


def _seal(payload, response, db, principal, trace, key, flow, schema):
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(403, headers=PRIVATE, detail={
            'code': 'operator_mismatch', 'message': '操作人必须是当前登录人员'})
    _coordinates(payload.original, trace, key)
    return _call(db, response, lambda: seals.seal_execution_request(
        db, actor=principal, request=payload, flow=flow), schema, write=True)


@router.post('/dispositions/request-seal', response_model=DispositionFound | ExecutionSealed)
def seal_disposition(payload: StockLossDispositionSealIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'dispose_loss')),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    return _seal(payload, response, db, principal, trace, key, 'disposition', DispositionFound | ExecutionSealed)


@router.post('/derived-returns/request-seal', response_model=ReturnFound | ExecutionSealed)
def seal_return(payload: StockLossDerivedReturnSealIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'dispose_loss')),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    return _seal(payload, response, db, principal, trace, key, 'return', ReturnFound | ExecutionSealed)
