"""Read-only recovery with immutable database-owned request-key provenance.

The existing recovery first proves current read permission and complete
business evidence. No registration, stock write, lock, retry or repair occurs
here. Missing/corrupt bindings turn an otherwise found result into unknown.
"""
import hashlib
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.formal_services import stock_loss_sources as sources
from app.stock_operation_models import StockLossDisposition,StockOperationOrder
from .request_contracts import ReversalExecute, CorrectionApprove, CorrectionExecute, validate, original_request
from .historical_original import _bound
from app.formal_services.work_order_query import _aware
from . import binding_contracts as binding_sql
from . import sealed_inverse as sealed_inverse
from . import correction_recovery as correction_recovery
from . import inverse_recovery as inverse_recovery


def _unknown():
    sources._fail('loss_request_binding_outcome_unknown','原请求键绑定不完整或读取期间变化，不能判定未执行或重发',503)


def _identity(request,result):
    state=result.get('request_state')
    if state=='not_found':
        if result.get('retry_allowed') is not False or result.get('result') is not None:_unknown()
        return ('inverse' if type(request) is ReversalExecute else 'approval' if type(request) is CorrectionApprove else 'correction'),None
    if state=='sealed':
        kind={ReversalExecute:'inverse_seal', CorrectionApprove:'approval_seal',
              CorrectionExecute:'correction_seal'}[type(request)]
        value=result.get('seal',{}).get('seal_id')
    elif state=='found':
        kind,field={ReversalExecute:('inverse','reversal_id'),CorrectionApprove:('approval','correction_decision_id'),
            CorrectionExecute:('correction','correction_execution_id')}[type(request)]
        value=(result.get('result') or {}).get(field)
    else:_unknown()
    try:return kind,UUID(str(value))
    except (ValueError,TypeError):_unknown()


def _read(db,actor,request,kind,identifier):
    table,_,_=binding_sql.KINDS[kind]
    keys=sealed_inverse._keys(request)
    token=hashlib.sha256(('cloud_oam.loss.correction.key.v1\0'+request.idempotency_key).encode()).hexdigest()
    try:
        rows=db.execute(text('SELECT b.*,f.id AS fact_record_id,f.root_disposition_id AS fact_root_id,'
            'f.actor_user_id AS fact_actor,f.actor_person_id AS fact_person,f.request_id AS fact_request,'
            'f.request_hash AS fact_request_hash,f.idempotency_key_hash AS fact_key,f.created_at AS fact_created_at '
            'FROM public.'+binding_sql.TABLE+' b LEFT JOIN public.'+table+' f ON f.id=b.fact_id '
            'WHERE b.fact_id=:identifier OR b.key_token=:token OR b.reversal_key_hash=ANY(:keys) '
            'OR b.approval_key_hash=ANY(:keys) OR b.correction_key_hash=ANY(:keys) '
            'OR (b.actor_user_id=:actor AND b.request_id=:request) ORDER BY b.fact_id LIMIT 3'),
            dict(identifier=identifier,token=token,keys=list(keys),actor=actor.user_id,request=request.request_id)).mappings().all()
    except DBAPIError:_unknown()
    return [dict(r) for r in rows],keys,token


def _verify(rows,keys,token,actor,request,kind,identifier):
    if identifier is None:
        if rows:_unknown()
        return
    if len(rows)!=1:_unknown()
    row=rows[0];binding=original_request(actor=actor,request=request)
    expected=dict(fact_id=identifier,binding_kind=kind,root_disposition_id=request.root_disposition_id,
        actor_user_id=actor.user_id,request_id=request.request_id,request_hash=binding.request_hash,
        key_token=token,reversal_key_hash=keys[0],approval_key_hash=keys[1],correction_key_hash=keys[2],
        fact_record_id=identifier,fact_root_id=request.root_disposition_id,fact_actor=actor.user_id,
        fact_person=actor.person_id,fact_request=request.request_id,fact_request_hash=binding.request_hash,
        fact_key=binding.key_hash)
    expected.update({column:identifier if entry==kind else None for entry,(_,_,column) in binding_sql.KINDS.items()})
    if any(row.get(key)!=value for key,value in expected.items()):_unknown()
    if row['created_at'] is None or row['fact_created_at'] is None or _aware(row['created_at'])!=_aware(row['fact_created_at']):_unknown()


def lookup(db, *, actor, request):
    return _lookup(db,actor=actor,request=request,stopped_return=False)


def lookup_unshipped_return(db, *, actor, request):
    return _lookup(db,actor=actor,request=request,stopped_return=True)


def _lookup(db, *, actor, request, stopped_return):
    request=validate(request)
    if stopped_return and type(request) is not ReversalExecute:
        raise ValueError('exact original return inverse request required')
    if type(request) not in (ReversalExecute,CorrectionApprove,CorrectionExecute):
        raise ValueError('exact original write request required')
    with db.no_autoflush:
        before=_bound(db)
        # Complete existing read authorization happens before querying bindings.
        handler=(sealed_inverse.lookup_unshipped_return if stopped_return else sealed_inverse.lookup) if type(request) is ReversalExecute else correction_recovery.lookup
        result=handler(db,actor=actor,request=request)
        kind,identifier=_identity(request,result)
        root=db.get(StockLossDisposition,request.root_disposition_id,populate_existing=True)
        order=db.get(StockOperationOrder,root.operation_id,populate_existing=True) if root else None
        if order is None:_unknown()
        current=inverse_recovery._authorize(db,actor,order)
        first,keys,token=_read(db,current,request,kind,identifier)
        _verify(first,keys,token,current,request,kind,identifier)
        current=inverse_recovery._authorize(db,current,order)
        second,keys,token=_read(db,current,request,kind,identifier)
        _verify(second,keys,token,current,request,kind,identifier)
        if first!=second or _bound(db)!=before:_unknown()
        return result
