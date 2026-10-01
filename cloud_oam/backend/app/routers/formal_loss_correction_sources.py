"""Headquarters query-only verified references for explicit correction actions."""
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException,Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.audit_chain import AuditChainError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.stock_loss_corrections import read_sources
from ..stock_loss_correction_source_schemas import CorrectionSources

router=APIRouter()
PRIVATE={'Cache-Control':'private, no-store'}

@router.get('/corrections/sources/{root_disposition_id}',response_model=CorrectionSources)
def correction_sources(root_disposition_id:UUID,response:Response,db:Session=Depends(get_db),
    principal:FormalPrincipal=Depends(require_permission('stock_operation','read'))):
    response.headers.update(PRIVATE)
    try:
        return read_sources.read(db,actor=principal,root_disposition_id=root_disposition_id)
    except (InventoryReadError,InventoryPostingError) as error:
        status=error.status_code if isinstance(error,InventoryReadError) else error.http_status_code
        raise HTTPException(status,headers=PRIVATE,detail=error.as_detail()) from None
    except (SQLAlchemyError,AuditChainError,ValidationError):
        raise HTTPException(503,headers=PRIVATE,detail={
            'code':'loss_correction_sources_unavailable','message':'暂时无法核验纠正来源，请保留已有原请求并稍后刷新'}) from None
