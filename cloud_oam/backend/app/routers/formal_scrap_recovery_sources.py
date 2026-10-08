"""Read-only role/scoped source selection, separate from original-request lookup."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.stock_scrap import recovery_sources
from ..stock_scrap_recovery_source_schemas import Stage, RecoverySources, RecoveryQueue

router = APIRouter(prefix='/recovery/sources')
PRIVATE = {'Cache-Control': 'private, no-store'}


def _read(response, call, schema):
    response.headers.update(PRIVATE)
    try:
        return schema.model_validate(call())
    except (InventoryReadError, InventoryPostingError) as error:
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status, headers=PRIVATE, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError, ValidationError):
        raise HTTPException(503, headers=PRIVATE, detail={
            'code': 'stock_scrap_recovery_sources_unavailable', 'message': '暂时无法核验找回来源，请保留已有原请求并稍后刷新'}) from None


@router.get('', response_model=RecoveryQueue)
def queue(response: Response, stage: Stage, after_id: UUID | None = None, limit: int = Query(5, ge=1, le=10),
    db: Session = Depends(get_db), principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _read(response, lambda: recovery_sources.queue(db, actor=principal, stage=stage, after_id=after_id, limit=limit), RecoveryQueue)


@router.get('/{scrap_line_id}', response_model=RecoverySources)
def detail(scrap_line_id: UUID, response: Response, stage: Stage,
    db: Session = Depends(get_db), principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    return _read(response, lambda: recovery_sources.read(db, actor=principal, stage=stage, scrap_line_id=scrap_line_id), RecoverySources)
