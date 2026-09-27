"""Read-only own-stock selection; this router cannot submit or freeze a loss."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import stock_loss_sources, stock_loss_plan, stock_loss_recovery
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..stock_loss_schemas import StockLossSelectionIn, StockLossSelectionOut, StockLossSourcesOut, StockLossPreviewIn, StockLossPreviewOut
from ..stock_loss_schemas import StockLossRequestLookupIn, StockLossRequestLookupOut

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
