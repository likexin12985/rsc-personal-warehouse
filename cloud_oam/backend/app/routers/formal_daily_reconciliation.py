"""Scoped daily reads and supervised commands, independent of opening review."""
from datetime import date
from typing import Annotated
from uuid import UUID
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from pydantic import ValidationError
from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..daily_reconciliation import query_service as service
from ..daily_reconciliation.query_schemas import DailyPage, DailyDetail, ComparisonPage, ExcludedPage, ReviewHistoryPage

router=APIRouter(prefix='/v1/reconciliations/daily',tags=['formal-daily-reconciliation'])
Principal=Annotated[FormalPrincipal,Depends(require_permission('reconciliation','read'))]
Database=Annotated[Session,Depends(get_db)]

def read_call(request,response,db,actor,function,allowed,**kwargs):
    response.headers['Cache-Control']='no-store'
    keys=list(request.query_params.keys())
    if set(keys)-set(allowed) or any(len(request.query_params.getlist(k))!=1 for k in keys):
        raise HTTPException(422,detail={'code':'daily_query_parameters_invalid'},headers={'Cache-Control':'no-store'})
    try:
        return function(db,actor=actor,session_id=getattr(request.state,'auth_session_id',None),**kwargs)
    except service.DailyQueryError as exc:
        db.rollback()
        raise HTTPException(exc.status,detail={'code':exc.code},headers={'Cache-Control':'no-store'}) from None
    except (DBAPIError,ValidationError):
        db.rollback()
        raise HTTPException(503,detail={'code':'daily_query_unavailable'},headers={'Cache-Control':'no-store'}) from None

@router.get('',response_model=DailyPage)
def list_reports(request:Request,response:Response,actor:Principal,db:Database,
    limit:Annotated[int,Query(ge=1,le=20)]=20,after_id:UUID|None=None,
    business_date:date|None=None,region_org_id:UUID|None=None):
    return read_call(request,response,db,actor,service.list_reports,('limit','after_id','business_date','region_org_id'),
        limit=limit,after_id=after_id,business_date=business_date,region_org_id=region_org_id)

@router.get('/{cutoff_id}',response_model=DailyDetail)
def detail(cutoff_id:UUID,request:Request,response:Response,actor:Principal,db:Database):
    return read_call(request,response,db,actor,service.detail,(),cutoff_id=cutoff_id)

@router.get('/{cutoff_id}/items',response_model=ComparisonPage)
def items(cutoff_id:UUID,request:Request,response:Response,actor:Principal,db:Database,
    limit:Annotated[int,Query(ge=1,le=100)]=100,after_ordinal:Annotated[int,Query(ge=0,le=2147483647)]=0,
    expected_review_version:Annotated[int|None,Query(ge=0,le=9223372036854775807)]=None):
    return read_call(request,response,db,actor,service.item_page,('limit','after_ordinal','expected_review_version'),
        cutoff_id=cutoff_id,limit=limit,after_ordinal=after_ordinal,expected_review_version=expected_review_version)

@router.get('/{cutoff_id}/excluded-quantities',response_model=ExcludedPage)
def excluded(cutoff_id:UUID,request:Request,response:Response,actor:Principal,db:Database,
    limit:Annotated[int,Query(ge=1,le=100)]=100,after_ordinal:Annotated[int,Query(ge=0,le=2147483647)]=0):
    return read_call(request,response,db,actor,service.item_page,('limit','after_ordinal'),cutoff_id=cutoff_id,limit=limit,after_ordinal=after_ordinal,excluded=True)


@router.get('/{cutoff_id}/history',response_model=ReviewHistoryPage)
def history(cutoff_id:UUID,request:Request,response:Response,actor:Principal,db:Database,
    limit:Annotated[int,Query(ge=1,le=100)]=50,after_version:Annotated[int,Query(ge=0,le=9223372036854775807)]=0,
    expected_review_version:Annotated[int|None,Query(ge=0,le=9223372036854775807)]=None):
    return read_call(request,response,db,actor,service.history_page,('limit','after_version','expected_review_version'),
        cutoff_id=cutoff_id,limit=limit,after_version=after_version,expected_review_version=expected_review_version)

# Commands use a fixed supervised worker. Authentication, pool/connect work,
# Python validation, SQL and COMMIT are all inside its wall-clock deadline.
from ..config import Settings,get_settings
from ..daily_reconciliation import review_entry
from ..daily_reconciliation.review_http import CommandInput,CommandOutput
from ..daily_reconciliation.recovery_http import LookupInput,SealInput,RecoveryOutput


def get_review_command_executor():
    return review_entry.execute


def command_token(request,settings,reject):
    """Only extract credentials here; the supervised worker proves live authority."""
    authorization=request.headers.getlist('authorization')
    if authorization:
        if len(authorization)!=1 or not authorization[0].startswith('Bearer '):
            reject(401,'daily_review_authentication_required')
        token=authorization[0][7:]
    else:
        # Request.cookies collapses duplicate names. Refuse ambiguous cookie paths
        # and never fall back from an invalid explicit Authorization header.
        tokens=[part.partition('=')[2] for header in request.headers.getlist('cookie')
                for part in header.split(';') if part.partition('=')[0].strip()=='access_token']
        if len(tokens)!=1:reject(401,'daily_review_authentication_required')
        token=tokens[0]
        origin=settings.app_origin
        try:
            parsed=urlsplit(origin)
            valid=(bool(parsed.hostname) and parsed.scheme in ('https','http') and not parsed.username
                and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
                and origin==f'{parsed.scheme}://{parsed.netloc}' and not any(c.isspace() for c in origin)
                and (settings.environment!='production' or parsed.scheme=='https'))
            parsed.port  # Reject malformed ports, rather than trusting the Host header.
        except ValueError:valid=False
        if not valid:reject(503,'daily_review_cookie_origin_unconfigured')
        if request.headers.getlist('origin')!=[origin]:reject(403,'daily_review_origin_forbidden')
        if request.headers.getlist('sec-fetch-site') not in ([],['same-origin']):
            reject(403,'daily_review_origin_forbidden')
    if not 1<=len(token)<=8192 or any(ord(c)<33 or ord(c)>126 or c in '\";,\\' for c in token):
        reject(401,'daily_review_authentication_required')
    return token


def command_call(cutoff_id,request,response,payload,settings,executor,*,recover):
    headers={'Cache-Control':'no-store','Pragma':'no-cache','Referrer-Policy':'no-referrer'}
    response.headers.update(headers)
    def reject(status,code):raise HTTPException(status,detail={'code':code},headers=headers)
    if request.query_params:reject(422,'daily_review_parameters_invalid')
    token=command_token(request,settings,reject)
    if any(len(request.headers.getlist(k))!=1 for k in ('idempotency-key','x-request-id')):
        reject(422,'daily_review_headers_invalid')
    c=payload.command
    if c.cutoff_id!=cutoff_id or request.headers['idempotency-key']!=c.idempotency_key or request.headers['x-request-id']!=c.request_id:
        reject(422,'daily_review_request_binding_invalid')
    if not settings.database_url.startswith('postgresql+psycopg://'):reject(503,'daily_review_database_unavailable')
    try:
        result=executor(database_url=settings.database_url,command=c.model_dump(mode='json'),access_token=token,
            expected_authorization_version=payload.expected_authorization_version,recover=recover)
    except review_entry.ReviewEntryBusy:reject(503,'daily_review_capacity_busy')
    except (review_entry.ProcessEntryError,review_entry.ProcessOutcomeUnknown):
        reject(503,'daily_review_outcome_unknown_use_exact_recovery')
    if result.get('outcome')=='rejected':reject(result['status'],result['code'])
    if result.get('outcome')!='observed':reject(503,'daily_review_outcome_unknown_use_exact_recovery')
    try:return CommandOutput(receipt=result['receipt'])
    except (KeyError,ValidationError):reject(503,'daily_review_outcome_unknown_use_exact_recovery')


@router.post('/{cutoff_id}/commands',response_model=CommandOutput)
def execute_review(cutoff_id:UUID,payload:CommandInput,request:Request,response:Response,
    settings:Annotated[Settings,Depends(get_settings)],executor=Depends(get_review_command_executor)):
    return command_call(cutoff_id,request,response,payload,settings,executor,recover=False)


@router.post('/{cutoff_id}/command-status',response_model=CommandOutput)
def recover_review(cutoff_id:UUID,payload:CommandInput,request:Request,response:Response,
    settings:Annotated[Settings,Depends(get_settings)],executor=Depends(get_review_command_executor)):
    return command_call(cutoff_id,request,response,payload,settings,executor,recover=True)

def get_review_recovery_executor():
    return review_entry.execute_recovery

def reference_call(cutoff_id,request,response,payload,settings,executor,*,seal):
    headers={'Cache-Control':'no-store','Pragma':'no-cache','Referrer-Policy':'no-referrer'}
    response.headers.update(headers)
    def reject(status,code):raise HTTPException(status,detail={'code':code},headers=headers)
    if request.query_params:reject(422,'daily_review_parameters_invalid')
    token=command_token(request,settings,reject)
    if len(request.headers.getlist('x-request-id'))!=1:reject(422,'daily_review_headers_invalid')
    if payload.reference.cutoff_id!=cutoff_id:reject(422,'daily_review_request_binding_invalid')
    if not settings.database_url.startswith('postgresql+psycopg://'):reject(503,'daily_review_database_unavailable')
    try:
        result=executor(database_url=settings.database_url,reference=payload.reference.model_dump(mode='json'),access_token=token,
            expected_authorization_version=payload.expected_authorization_version,seal=seal)
    except review_entry.ReviewEntryBusy:reject(503,'daily_review_capacity_busy')
    except (review_entry.ProcessEntryError,review_entry.ProcessOutcomeUnknown):reject(503,'daily_review_outcome_unknown_use_exact_recovery')
    if result.get('outcome')=='rejected':reject(result['status'],result['code'])
    if result.get('outcome')!='observed':reject(503,'daily_review_outcome_unknown_use_exact_recovery')
    try:
        output=RecoveryOutput.model_validate(result['recovery'])
        if output.reference!=payload.reference or output.current_authorization_version!=payload.expected_authorization_version:
            raise ValueError('recovery response binding changed')
        return output
    except (KeyError,ValueError,ValidationError):reject(503,'daily_review_outcome_unknown_use_exact_recovery')

@router.post('/{cutoff_id}/request-recovery',response_model=RecoveryOutput)
def recover_reference(cutoff_id:UUID,payload:LookupInput,request:Request,response:Response,
    settings:Annotated[Settings,Depends(get_settings)],executor=Depends(get_review_recovery_executor)):
    return reference_call(cutoff_id,request,response,payload,settings,executor,seal=False)

@router.post('/{cutoff_id}/request-seal',response_model=RecoveryOutput)
def seal_reference(cutoff_id:UUID,payload:SealInput,request:Request,response:Response,
    settings:Annotated[Settings,Depends(get_settings)],executor=Depends(get_review_recovery_executor)):
    return reference_call(cutoff_id,request,response,payload,settings,executor,seal=True)
