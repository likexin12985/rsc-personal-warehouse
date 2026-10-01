"""Current HQ execution references, separate from historical request recovery."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import stock_loss_execution_sources as sources
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.inventory_posting import InventoryPostingError
from ..stock_loss_execution_source_schemas import ExecutionSourcesOut

router = APIRouter()
PRIVATE = {'Cache-Control': 'private, no-store'}


@router.get('/execution-sources/{operation_id}', response_model=ExecutionSourcesOut)
def read_execution_sources(operation_id: UUID, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    response.headers.update(PRIVATE)
    try:
        return sources.execution_sources(db, actor=principal, operation_id=operation_id)
    except (InventoryReadError, InventoryPostingError) as error:
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status, headers=PRIVATE, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError):
        raise HTTPException(503, headers=PRIVATE, detail={
            'code': 'stock_loss_execution_sources_unavailable',
            'message': '暂时无法核验处置来源，请稍后刷新；已有原请求请继续回查',
        }) from None
