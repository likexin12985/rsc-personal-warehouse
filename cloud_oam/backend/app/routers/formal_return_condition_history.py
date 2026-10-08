"""Read-only condition case timeline; mutations stay in the command router."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from formal_file_integrity import FormalFileError

from ..database import get_db
from ..dependencies import get_formal_principal
from ..formal_access import FormalPrincipal
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.stock_loss_corrections import return_condition_case_read as reader
from ..formal_services.stock_loss_corrections.request_contracts import FactId
from ..return_condition_read_schemas import ConditionCaseHistory, ConditionReceiptHistory, ConditionInbox
from .formal_return_conditions import PrivateConditionRoute, _rollback

router = APIRouter(prefix='/return-condition-corrections', route_class=PrivateConditionRoute)


@router.get('/inbox', response_model=ConditionInbox)
def inbox(view: Literal['pending', 'all'] = 'pending', limit: int = Query(5, ge=1, le=10),
          after_id: FactId | None = None, db: Session = Depends(get_db),
          principal: FormalPrincipal = Depends(get_formal_principal)):
    return _read(db, principal, lambda: reader.inbox(db, actor=principal, view=view, limit=limit, after_id=after_id))


@router.get('/history/{inbound_line_id}', response_model=ConditionCaseHistory)
def read(inbound_line_id: FactId, db: Session = Depends(get_db),
         principal: FormalPrincipal = Depends(get_formal_principal)):
    return _read(db, principal, lambda: reader.read(db, actor=principal, inbound_line_id=inbound_line_id))


@router.get('/receipts/{receipt_id}', response_model=ConditionReceiptHistory)
def receipt_sources(receipt_id: FactId, db: Session = Depends(get_db),
                    principal: FormalPrincipal = Depends(get_formal_principal)):
    return _read(db, principal, lambda: reader.receipt_sources(db, actor=principal, receipt_id=receipt_id))


def _read(db, principal, operation):
    try:
        if not principal.allows(db, 'stock_operation', 'read'):
            raise HTTPException(403, detail={'code': 'forbidden', 'message': '没有成色纠正历史查看权限'})
        return operation().model_dump(mode='json')
    except (InventoryReadError, InventoryPostingError) as error:
        status = error.status_code if isinstance(error, InventoryReadError) else error.http_status_code
        raise HTTPException(status, detail=error.as_detail()) from None
    except (SQLAlchemyError, AuditChainError, FormalFileError, ValueError, KeyError, TypeError):
        raise HTTPException(503, detail={'code': 'return_condition_history_unavailable',
            'message': '暂时无法完整核验纠正历史，请稍后重新查询'}) from None
    finally:
        _rollback(db)
