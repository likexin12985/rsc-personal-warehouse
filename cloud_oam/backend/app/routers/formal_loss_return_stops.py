"""Dedicated return stop, independent from generic inverses and fulfillment."""
from uuid import UUID
from fastapi import APIRouter,Depends,Header,Response
from sqlalchemy.orm import Session
from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.stock_loss_corrections import bound_commands,bound_recovery,return_stop_api
from ..formal_services.stock_loss_corrections.request_contracts import ReversalPreview,ReversalExecute
from ..stock_loss_correction_http_schemas import InverseFact,InverseFound,Sealed,InverseRecovery
from ..stock_loss_return_stop_schemas import ReturnStopSources,ReturnStopPreview
from .formal_loss_corrections import _call,_write,_coordinates

router=APIRouter(prefix='/corrections/return-stops')

@router.get('/sources/{root_disposition_id}',response_model=ReturnStopSources)
def sources(root_disposition_id:UUID,response:Response,db:Session=Depends(get_db),
    principal:FormalPrincipal=Depends(require_permission('stock_operation','read'))):
    return _call(db,response,lambda:return_stop_api.read(db,actor=principal,root_disposition_id=root_disposition_id),ReturnStopSources,lookup=True)

@router.post('/preview',response_model=ReturnStopPreview)
def preview(payload:ReversalPreview,response:Response,db:Session=Depends(get_db),
    principal:FormalPrincipal=Depends(require_permission('stock_operation','reverse_loss'))):
    return _call(db,response,lambda:return_stop_api.preview(db,actor=principal,request=payload),ReturnStopPreview)

@router.post('',response_model=InverseFact)
def execute(payload:ReversalExecute,response:Response,db:Session=Depends(get_db),
    principal:FormalPrincipal=Depends(require_permission('stock_operation','reverse_loss')),
    trace:str|None=Header(None,alias='X-Request-ID'),key:str|None=Header(None,alias='Idempotency-Key')):
    return _write(payload,response,db,principal,trace,key,bound_commands.return_inverse,InverseFact)

@router.post('/request-seal',response_model=InverseFound|Sealed)
def seal(payload:ReversalExecute,response:Response,db:Session=Depends(get_db),
    principal:FormalPrincipal=Depends(require_permission('stock_operation','reverse_loss')),
    trace:str|None=Header(None,alias='X-Request-ID'),key:str|None=Header(None,alias='Idempotency-Key')):
    return _write(payload,response,db,principal,trace,key,bound_commands.seal_return_inverse,InverseFound|Sealed)

@router.post('/request-lookup',response_model=InverseRecovery)
def lookup(payload:ReversalExecute,response:Response,db:Session=Depends(get_db),
    principal:FormalPrincipal=Depends(require_permission('stock_operation','read')),
    trace:str|None=Header(None,alias='X-Request-ID'),key:str|None=Header(None,alias='Idempotency-Key')):
    _coordinates(payload,trace,key)
    return _call(db,response,lambda:bound_recovery.lookup_unshipped_return(db,actor=principal,request=payload),InverseRecovery,lookup=True)
