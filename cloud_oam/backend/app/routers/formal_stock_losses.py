"""Own-stock loss submission and exact original-request recovery."""
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import stock_loss_sources, stock_loss_plan, stock_loss_recovery
from ..formal_services import stock_loss_commands, stock_loss_seals
from ..formal_services import stock_loss_review_recovery
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..stock_loss_schemas import StockLossSelectionIn, StockLossSelectionOut, StockLossSourcesOut, StockLossPreviewIn, StockLossPreviewOut
from ..stock_loss_schemas import StockLossRequestLookupIn, StockLossRequestLookupOut
from ..stock_loss_schemas import StockLossSubmitIn, StockLossSubmittedOut, StockLossSealIn
from ..stock_loss_schemas import StockLossRequestFoundOut, StockLossRequestSealedOut
from ..stock_loss_schemas import StockLossReviewRequestLookupIn, StockLossRegionalReviewLookupOut, StockLossHeadquartersReviewLookupOut

router = APIRouter(prefix='/v1/stock-operations/loss-reports', tags=['formal-stock-losses'])
PRIVATE = {'Cache-Control': 'private, no-store'}


def _read(response, callback, *, unavailable=None):
    response.headers.update(PRIVATE)
    try:
        return callback()
    except InventoryReadError as error:
        raise HTTPException(status_code=error.status_code, detail=error.as_detail(), headers=PRIVATE) from None
    except InventoryPostingError as error:
        raise HTTPException(status_code=error.http_status_code, detail=error.as_detail(), headers=PRIVATE) from None
    except SQLAlchemyError:
        code, message = unavailable or ('stock_loss_sources_unavailable', '暂时无法核验报损来源，请稍后重新预检')
        raise HTTPException(status_code=503, headers=PRIVATE, detail={
            'code': code, 'message': message,
        }) from None


@router.get('/sources', response_model=StockLossSourcesOut)
def read_sources(response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'submit_loss'))):
    return _read(response, lambda: stock_loss_sources.loss_sources(db, actor=principal))


@router.post('/source-preview', response_model=StockLossSelectionOut)
def preview_sources(payload: StockLossSelectionIn, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'submit_loss'))):
    return _read(response, lambda: stock_loss_sources.preview_selection(db, actor=principal, request=payload))


@router.post('/preview', response_model=StockLossPreviewOut)
def preview_command(payload: StockLossPreviewIn, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'submit_loss'))):
    return _read(response, lambda: stock_loss_plan.preview_loss(db, actor=principal, request=payload)[0])


@router.post('/request-lookup', response_model=StockLossRequestLookupOut)
def lookup_request(payload: StockLossRequestLookupIn, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _read(response, lambda: stock_loss_recovery.lookup_loss_request(db, actor=principal, request=payload),
        unavailable=('stock_loss_lookup_unavailable', '暂时无法核验原报损请求，请保留原请求并稍后回查'))


@router.post('/regional-reviews/request-lookup', response_model=StockLossRegionalReviewLookupOut)
def lookup_regional_review(payload: StockLossReviewRequestLookupIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _read(response, lambda: stock_loss_review_recovery.lookup_review_request(
        db, actor=principal, request=payload, stage='regional'),
        unavailable=('stock_loss_review_lookup_unavailable', '暂时无法核验原审批请求，请保留原请求并稍后回查'))


@router.post('/headquarters-reviews/request-lookup', response_model=StockLossHeadquartersReviewLookupOut)
def lookup_headquarters_review(payload: StockLossReviewRequestLookupIn, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _read(response, lambda: stock_loss_review_recovery.lookup_review_request(
        db, actor=principal, request=payload, stage='headquarters'),
        unavailable=('stock_loss_review_lookup_unavailable', '暂时无法核验原审批请求，请保留原请求并稍后回查'))


def _coordinates(payload, principal, trace, key):
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, headers=PRIVATE, detail={
            'code': 'operator_mismatch', 'message': '操作人必须是当前登录人员'})
    for supplied, expected, code in (
        (trace, payload.request_id, 'request_id_mismatch'),
        (key, payload.idempotency_key, 'idempotency_key_mismatch'),
    ):
        if supplied is not None and supplied != expected:
            raise HTTPException(status_code=400, headers=PRIVATE, detail={
                'code': code, 'message': '请求头与原请求坐标不一致，请保留原请求回查'})


def _write(db, response, callback):
    # Service output is already validated; do not acknowledge before COMMIT.
    # A lost COMMIT response remains unknown even after a local rollback.
    response.headers.update(PRIVATE)
    try:
        result = callback()
        db.commit()
        return result
    except (InventoryReadError, InventoryPostingError) as error:
        db.rollback()
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status_code=status, detail=error.as_detail(), headers=PRIVATE) from None
    except (SQLAlchemyError, AuditChainError):
        db.rollback()
        raise HTTPException(status_code=503, headers=PRIVATE, detail={
            'code': 'stock_loss_write_unconfirmed',
            'message': '报损操作结果暂时无法确认，请保留原请求并回查，勿自动重发',
        }) from None
    except BaseException:
        db.rollback()
        raise


@router.post('', response_model=StockLossSubmittedOut)
def submit_report(payload: StockLossSubmitIn, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'submit_loss')),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    _coordinates(payload, principal, trace, key)
    return _write(db, response, lambda: stock_loss_commands.submit_loss(db, actor=principal, request=payload))


@router.post('/request-seal', response_model=StockLossRequestFoundOut | StockLossRequestSealedOut)
def seal_request(payload: StockLossSealIn, response: Response, db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'submit_loss')),
    trace: str | None = Header(None, alias='X-Request-ID'),
    key: str | None = Header(None, alias='Idempotency-Key')):
    # These are the original report coordinates, never a new submission key.
    _coordinates(payload, principal, trace, key)
    return _write(db, response, lambda: stock_loss_seals.seal_loss_request(db, actor=principal, request=payload))
