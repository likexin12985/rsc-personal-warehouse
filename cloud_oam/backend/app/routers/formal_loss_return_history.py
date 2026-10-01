"""Headquarters query of proved return facts and preserved classification issues."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.stock_loss_corrections import return_history
from ..stock_loss_return_history_schemas import LossReturnHistory, public_history

router = APIRouter()
PRIVATE = {'Cache-Control': 'private, no-store'}


@router.get('/corrections/return-history/{root_disposition_id}', response_model=LossReturnHistory)
def loss_return_history(root_disposition_id: UUID, response: Response,
    db: Session = Depends(get_db),
    principal: FormalPrincipal = Depends(require_permission('stock_operation', 'read'))):
    response.headers.update(PRIVATE)
    try:
        # Validate inside the boundary, before FastAPI serializes the answer.
        return public_history(return_history.read(db, actor=principal,
            root_disposition_id=root_disposition_id))
    except (InventoryReadError, InventoryPostingError) as error:
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status, headers=PRIVATE, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError, ValidationError):
        raise HTTPException(503, headers=PRIVATE, detail={
            'code': 'loss_return_history_unavailable',
            'message': '暂时无法核验退回历史，请保留原请求并稍后刷新',
        }) from None
