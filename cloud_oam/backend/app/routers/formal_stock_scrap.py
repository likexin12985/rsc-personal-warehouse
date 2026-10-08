"""Scrap, independent found-stock approval, posting and exact request recovery."""
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services import stock_scrap_plan
from ..formal_services.stock_scrap import bound_commands, request_lookup, recovery_lookup, request_seals, recovery_plan
from ..stock_scrap_schemas import ScrapPreview, ScrapExecute, ScrapRequestLookup, ScrapRequestSeal
from ..stock_scrap_recovery_schemas import (
    ScrapRecoveryApply, ScrapRecoveryRegionalReview, ScrapRecoveryHeadquartersReview,
    ScrapRecoveryPreview, ScrapRecoveryExecute, ScrapRecoveryRequestLookup, ScrapRecoveryRequestSeal,
)
from ..stock_scrap_http_schemas import (
    ScrapFact, RecoveryApplyFact, RecoveryRegionalFact, RecoveryHeadquartersFact, RecoveryPostingFact,
    Found, Sealed, ScrapLookup, ApplyLookup, RegionalLookup, HeadquartersLookup, RecoveryLookup,
    ScrapPreviewOut, RecoveryPreviewOut, public_preview,
)

PRIVATE = {'Cache-Control': 'private, no-store'}


class PrivateCommandRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def checked(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Framework validation may otherwise echo the entire original
                # command, including its private idempotency key and evidence.
                return JSONResponse(status_code=422, headers=PRIVATE, content={'detail': {
                    'code': 'stock_scrap_request_invalid', 'message': '请求字段无效，请核验所选阶段并保留原请求'}})
        return checked


router = APIRouter(prefix='/scraps', route_class=PrivateCommandRoute)
from .formal_scrap_recovery_sources import router as recovery_sources_router
router.include_router(recovery_sources_router)
KINDS = {ScrapRecoveryApply: 'apply', ScrapRecoveryRegionalReview: 'regional',
         ScrapRecoveryHeadquartersReview: 'headquarters', ScrapRecoveryExecute: 'execute',
         ScrapRecoveryPreview: 'execute'}


def _kind(command):
    if type(command) in (ScrapPreview, ScrapExecute):
        return command.source.kind
    return KINDS.get(type(command))


def _request(payload, principal, kind, trace=None, key=None):
    command = getattr(payload, 'original', payload)
    if _kind(command) != kind:
        raise HTTPException(400, headers=PRIVATE, detail={
            'code': 'stock_scrap_stage_mismatch', 'message': '原请求与所选业务阶段不一致'})
    if hasattr(payload, 'operator_person_id') and payload.operator_person_id != principal.person_id:
        raise HTTPException(403, headers=PRIVATE, detail={
            'code': 'operator_mismatch', 'message': '回查或封存必须使用当前登录的原操作人'})
    for actual, expected, code in ((trace, getattr(command, 'request_id', None), 'request_id_mismatch'),
                                  (key, getattr(command, 'idempotency_key', None), 'idempotency_key_mismatch')):
        if actual is not None and actual != expected:
            raise HTTPException(400, headers=PRIVATE, detail={
                'code': code, 'message': '请求头与完整原请求不一致，请保留原请求回查'})
    return command


def _rollback(db):
    try:
        db.rollback()
    except SQLAlchemyError:
        pass


def _call(db, response, callback, schema, *, command=None, write=False, lookup=False):
    response.headers.update(PRIVATE)
    try:
        result = TypeAdapter(schema).validate_python(callback())
        if command is not None:
            if result.request_id != command.request_id:
                raise ValueError('response belongs to another request')
            fact = getattr(result, 'result', result)
            if fact is not None:
                if fact.request_id != result.request_id or fact.request_hash != result.request_hash:
                    raise ValueError('response and outcome disagree')
                if isinstance(fact, ScrapFact) and fact.source_kind != _kind(command):
                    raise ValueError('response belongs to another scrap source')
            if isinstance(result, Sealed) and result.seal.kind != _kind(command):
                raise ValueError('seal belongs to another stage')
        # Validate and materialize while rollback is still possible. One COMMIT,
        # no retry; any uncertainty leaves the client holding its exact request.
        answer = result.model_dump(mode='json')
        if write:
            db.commit()
        return answer
    except (InventoryReadError, InventoryPostingError) as error:
        if write:
            _rollback(db)
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status, headers=PRIVATE, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError, ValidationError, ValueError):
        if write:
            _rollback(db)
        raise HTTPException(503, headers=PRIVATE, detail={
            'code': ('stock_scrap_outcome_unconfirmed' if write else
                     'stock_scrap_lookup_unavailable' if lookup else 'stock_scrap_preview_unavailable'),
            'message': ('结果暂时无法确认，请保留完整原请求回查，勿自动重发' if write or lookup
                        else '暂时无法核验方案，请稍后重新预览'),
        }) from None
    except BaseException:
        if write:
            _rollback(db)
        raise


def _operate(payload, response, db, principal, kind, trace, key, handler, schema, *, write=False):
    command = _request(payload, principal, kind, trace, key)
    return _call(db, response, lambda: handler(db, actor=principal, request=payload), schema,
                 command=command, write=write, lookup=not write)


@router.post('/originals', response_model=ScrapFact)
def write_original(payload: ScrapExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'dispose_loss')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'original', trace, key, bound_commands.original,
                    ScrapFact, write=True)


@router.post('/originals/request-lookup', response_model=ScrapLookup)
def lookup_original(payload: ScrapRequestLookup, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'read')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'original', trace, key, request_lookup.lookup,
                    ScrapLookup, write=False)


@router.post('/originals/request-seal', response_model=Found[ScrapFact] | Sealed)
def seal_original(payload: ScrapRequestSeal, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'dispose_loss')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'original', trace, key, request_seals.seal,
                    Found[ScrapFact] | Sealed, write=True)


@router.post('/originals/preview', response_model=ScrapPreviewOut)
def preview_original(payload: ScrapPreview, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'dispose_loss'))):
    _request(payload, principal, 'original')
    return _call(db, response, lambda: public_preview(
        stock_scrap_plan.prepare(db, actor=principal, request=payload), ScrapPreviewOut), ScrapPreviewOut)


@router.post('/corrections', response_model=ScrapFact)
def write_correction(payload: ScrapExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'correct_loss')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'correction', trace, key, bound_commands.correct,
                    ScrapFact, write=True)


@router.post('/corrections/request-lookup', response_model=ScrapLookup)
def lookup_correction(payload: ScrapRequestLookup, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'read')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'correction', trace, key, request_lookup.lookup,
                    ScrapLookup, write=False)


@router.post('/corrections/request-seal', response_model=Found[ScrapFact] | Sealed)
def seal_correction(payload: ScrapRequestSeal, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'correct_loss')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'correction', trace, key, request_seals.seal,
                    Found[ScrapFact] | Sealed, write=True)


@router.post('/corrections/preview', response_model=ScrapPreviewOut)
def preview_correction(payload: ScrapPreview, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'correct_loss'))):
    _request(payload, principal, 'correction')
    return _call(db, response, lambda: public_preview(
        stock_scrap_plan.prepare(db, actor=principal, request=payload), ScrapPreviewOut), ScrapPreviewOut)


@router.post('/recovery/applications', response_model=RecoveryApplyFact)
def write_apply(payload: ScrapRecoveryApply, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'apply_scrap_recovery')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'apply', trace, key, bound_commands.review,
                    RecoveryApplyFact, write=True)


@router.post('/recovery/applications/request-lookup', response_model=ApplyLookup)
def lookup_apply(payload: ScrapRecoveryRequestLookup, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'read')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'apply', trace, key, recovery_lookup.lookup,
                    ApplyLookup, write=False)


@router.post('/recovery/applications/request-seal', response_model=Found[RecoveryApplyFact] | Sealed)
def seal_apply(payload: ScrapRecoveryRequestSeal, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'apply_scrap_recovery')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'apply', trace, key, request_seals.seal,
                    Found[RecoveryApplyFact] | Sealed, write=True)


@router.post('/recovery/regional-reviews', response_model=RecoveryRegionalFact)
def write_regional(payload: ScrapRecoveryRegionalReview, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'review_scrap_recovery_regional')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'regional', trace, key, bound_commands.review,
                    RecoveryRegionalFact, write=True)


@router.post('/recovery/regional-reviews/request-lookup', response_model=RegionalLookup)
def lookup_regional(payload: ScrapRecoveryRequestLookup, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'read')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'regional', trace, key, recovery_lookup.lookup,
                    RegionalLookup, write=False)


@router.post('/recovery/regional-reviews/request-seal', response_model=Found[RecoveryRegionalFact] | Sealed)
def seal_regional(payload: ScrapRecoveryRequestSeal, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'review_scrap_recovery_regional')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'regional', trace, key, request_seals.seal,
                    Found[RecoveryRegionalFact] | Sealed, write=True)


@router.post('/recovery/headquarters-reviews', response_model=RecoveryHeadquartersFact)
def write_headquarters(payload: ScrapRecoveryHeadquartersReview, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'review_scrap_recovery_headquarters')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'headquarters', trace, key, bound_commands.review,
                    RecoveryHeadquartersFact, write=True)


@router.post('/recovery/headquarters-reviews/request-lookup', response_model=HeadquartersLookup)
def lookup_headquarters(payload: ScrapRecoveryRequestLookup, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'read')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'headquarters', trace, key, recovery_lookup.lookup,
                    HeadquartersLookup, write=False)


@router.post('/recovery/headquarters-reviews/request-seal', response_model=Found[RecoveryHeadquartersFact] | Sealed)
def seal_headquarters(payload: ScrapRecoveryRequestSeal, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'review_scrap_recovery_headquarters')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'headquarters', trace, key, request_seals.seal,
                    Found[RecoveryHeadquartersFact] | Sealed, write=True)


@router.post('/recovery/executions', response_model=RecoveryPostingFact)
def write_execute(payload: ScrapRecoveryExecute, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'execute_scrap_recovery')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'execute', trace, key, bound_commands.recover,
                    RecoveryPostingFact, write=True)


@router.post('/recovery/executions/request-lookup', response_model=RecoveryLookup)
def lookup_execute(payload: ScrapRecoveryRequestLookup, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'read')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'execute', trace, key, recovery_lookup.lookup,
                    RecoveryLookup, write=False)


@router.post('/recovery/executions/request-seal', response_model=Found[RecoveryPostingFact] | Sealed)
def seal_execute(payload: ScrapRecoveryRequestSeal, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'execute_scrap_recovery')),
    trace: str|None=Header(None, alias='X-Request-ID'), key: str|None=Header(None, alias='Idempotency-Key')):
    return _operate(payload, response, db, principal, 'execute', trace, key, request_seals.seal,
                    Found[RecoveryPostingFact] | Sealed, write=True)


@router.post('/recovery/executions/preview', response_model=RecoveryPreviewOut)
def preview_execute(payload: ScrapRecoveryPreview, response: Response, db: Session=Depends(get_db),
    principal: FormalPrincipal=Depends(require_permission('stock_operation', 'execute_scrap_recovery'))):
    _request(payload, principal, 'execute')
    return _call(db, response, lambda: public_preview(
        recovery_plan.prepare(db, actor=principal, request=payload), RecoveryPreviewOut), RecoveryPreviewOut)
