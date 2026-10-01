"""Real read-only API transactions and isolated corruption refusal controls."""
from uuid import UUID,uuid4
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.formal_services.inventory_query import InventoryReadError
from app.formal_access import load_formal_principal
from app.formal_services.stock_loss_corrections import binding_contracts as binding_sql
from app.formal_services.stock_loss_corrections import bound_recovery


def readonly(api,context,request):
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        result=bound_recovery.lookup(db,actor=load_formal_principal(db,context['admin_id']),request=request)
        assert db.scalar(text('SHOW transaction_read_only'))=='on'
        db.rollback()
        return result


def corrupted(owner,context,request,identifier):
    cases=[]
    for mode in ('missing','shared_token','other_action_alias'):
        with Session(owner) as db:
            db.execute(text('ALTER TABLE public.'+binding_sql.TABLE+' DISABLE TRIGGER trg_loss_request_binding_immutable'))
            params=dict(id=UUID(str(identifier)),digest=uuid4().hex+uuid4().hex)
            statement={
                'missing':'DELETE FROM public.'+binding_sql.TABLE+' WHERE fact_id=:id',
                'shared_token':'UPDATE public.'+binding_sql.TABLE+' SET key_token=:digest WHERE fact_id=:id',
                'other_action_alias':'UPDATE public.'+binding_sql.TABLE+' SET approval_key_hash=:digest WHERE fact_id=:id',
            }[mode]
            assert db.execute(text(statement),params).rowcount==1
            try:bound_recovery.lookup(db,actor=load_formal_principal(db,context['admin_id']),request=request)
            except InventoryReadError as error:
                assert error.status_code==503 and error.code=='loss_request_binding_outcome_unknown',str(error)
            else:raise AssertionError('corrupt binding accepted: '+mode)
            db.rollback()
        cases.append(mode+'_binding_rejected_without_replay')
    return cases


def readonly_original(api, context, request):
    """Recover an original disposition under the actual read-only API role."""
    from app.formal_services.stock_loss_disposition_recovery import lookup_disposition_request
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        result = lookup_disposition_request(db, actor=load_formal_principal(db, context['admin_id']),
            request=request, flow='disposition')
        assert db.scalar(text('SHOW transaction_read_only')) == 'on'
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        return result
