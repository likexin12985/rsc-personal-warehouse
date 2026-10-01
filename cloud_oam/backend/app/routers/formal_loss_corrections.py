"""Separate inverse, approval, execution and original-request recovery commands."""
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.stock_loss_corrections import bound_commands, bound_recovery, reversal_stock, correction_stock
from ..formal_services.stock_loss_corrections.request_contracts import (
    ReversalPreview, ReversalExecute, CorrectionApprove, CorrectionPreview, CorrectionExecute,
)
from ..stock_loss_correction_http_schemas import (
    InverseFact, ApprovalFact, ExecutionFact, InverseFound, ApprovalFound, ExecutionFound,
    Sealed, InverseRecovery, ApprovalRecovery, ExecutionRecovery,
    InversePreview, ExecutionPreview, public_preview,
)

router = APIRouter(prefix='/corrections')
PRIVATE = {'Cache-Control':'private, no-store'}


def _coordinates(command, trace, key):
    for actual, expected, code in ((trace, command.request_id, 'request_id_mismatch'),
                                  (key, command.idempotency_key, 'idempotency_key_mismatch')):
        if actual is not None and actual != expected:
            raise HTTPException(400, headers=PRIVATE, detail={
                'code':code, 'message':'请求头与完整原请求不一致，请保留原请求回查'})


def _rollback(db):
    try:
        db.rollback()
    except SQLAlchemyError:
        pass


def _call(db, response, callback, schema, *, write=False, lookup=False):
    response.headers.update(PRIVATE)
    try:
        # Validate and serialize before COMMIT; never acknowledge a failed or
        # unknown commit, nor repeat it to guess whether it took effect.
        result = TypeAdapter(schema).validate_python(callback())
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
            'code': ('loss_correction_outcome_unconfirmed' if write else
                     'loss_correction_lookup_unavailable' if lookup else 'loss_correction_preview_unavailable'),
            'message': ('纠正结果暂时无法确认，请保留完整原请求回查，勿自动重发' if write or lookup
                        else '暂时无法核验纠正方案，请稍后重新预览'),
        }) from None
    except BaseException:
        if write:
            _rollback(db)
        raise


def _write(payload, response, db, principal, trace, key, handler, schema):
    _coordinates(payload, trace, key)
    return _call(db, response, lambda:handler(db, actor=principal, request=payload), schema, write=True)


def _lookup(payload, response, db, principal, trace, key, schema):
    _coordinates(payload, trace, key)
    # The bound recovery service is query-only. No preview/execute/seal fallback.
    return _call(db, response, lambda:bound_recovery.lookup(db, actor=principal, request=payload),
                 schema, lookup=True)


@router.post('/inverses/preview', response_model=InversePreview)
def preview_inverse(payload: ReversalPreview, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','reverse_loss'))):
    return _call(db,response,lambda:public_preview(reversal_stock.prepare(db,actor=principal,request=payload),
                 InversePreview),InversePreview)


@router.post('/executions/preview', response_model=ExecutionPreview)
def preview_correction(payload: CorrectionPreview, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','correct_loss'))):
    return _call(db,response,lambda:public_preview(correction_stock.prepare(db,actor=principal,request=payload),
                 ExecutionPreview),ExecutionPreview)


@router.post('/inverses', response_model=InverseFact)
def execute_inverse(payload: ReversalExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','reverse_loss')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _write(payload,response,db,principal,trace,key,bound_commands.inverse,InverseFact)


@router.post('/approvals', response_model=ApprovalFact)
def approve_correction(payload: CorrectionApprove, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','approve_loss_correction')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _write(payload,response,db,principal,trace,key,bound_commands.approve,ApprovalFact)


@router.post('/executions', response_model=ExecutionFact)
def execute_correction(payload: CorrectionExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','correct_loss')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _write(payload,response,db,principal,trace,key,bound_commands.correct,ExecutionFact)


@router.post('/inverses/request-seal', response_model=InverseFound|Sealed)
def seal_inverse(payload: ReversalExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','reverse_loss')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _write(payload,response,db,principal,trace,key,bound_commands.seal,InverseFound|Sealed)


@router.post('/approvals/request-seal', response_model=ApprovalFound|Sealed)
def seal_approval(payload: CorrectionApprove, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','approve_loss_correction')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _write(payload,response,db,principal,trace,key,bound_commands.seal,ApprovalFound|Sealed)


@router.post('/executions/request-seal', response_model=ExecutionFound|Sealed)
def seal_correction(payload: CorrectionExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','correct_loss')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _write(payload,response,db,principal,trace,key,bound_commands.seal,ExecutionFound|Sealed)


@router.post('/inverses/request-lookup', response_model=InverseRecovery)
def lookup_inverse(payload: ReversalExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','read')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _lookup(payload,response,db,principal,trace,key,InverseRecovery)


@router.post('/approvals/request-lookup', response_model=ApprovalRecovery)
def lookup_approval(payload: CorrectionApprove, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','read')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _lookup(payload,response,db,principal,trace,key,ApprovalRecovery)


@router.post('/executions/request-lookup', response_model=ExecutionRecovery)
def lookup_correction(payload: CorrectionExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation','read')),
    trace: str|None=Header(None,alias='X-Request-ID'), key: str|None=Header(None,alias='Idempotency-Key')):
    return _lookup(payload,response,db,principal,trace,key,ExecutionRecovery)
