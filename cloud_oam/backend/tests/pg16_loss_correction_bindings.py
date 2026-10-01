"""Native bypass attempts and exact registration against real retained facts."""
from datetime import datetime,timezone
from uuid import UUID,uuid4
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.stock_operation_models import StockLossDisposition,StockOperationOrder
from app.formal_services.stock_loss_corrections.correction_models import StockLossCorrectionDecision
from app.formal_services.stock_loss_corrections.request_contracts import CorrectionApprove,original_request
from app.formal_services.stock_loss_corrections.seal_model import StockLossInverseRequestSeal
from app.formal_services.stock_loss_corrections import business_events
from app.formal_services.stock_loss_corrections import sealed_inverse
from app.formal_services.stock_loss_corrections import bound_commands
from app.formal_services.stock_loss_corrections import binding_contracts as binding_sql
import pg16_loss_correction_recovery as recovery_checks


def count(owner):
    with owner.connect() as db:return db.scalar(text('SELECT count(*) FROM public.'+binding_sql.TABLE))


def before_inverse(owner,api,context,request,snapshot):
    before=snapshot(owner)
    for mode in ('missing_binding','wrong_raw_key'):
        with Session(api) as db:
            result=sealed_inverse.execute(db,actor=load_formal_principal(db,context['admin_id']),request=request)
            try:
                if mode=='wrong_raw_key':
                    bound_commands.register(db,kind='inverse',identifier=result['reversal_id'],client_key='wrong-'+uuid4().hex)
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514',str(error.orig)
                required='database-owned request binding required' if mode=='missing_binding' else 'client key does not prove stored action hashes'
                assert required in str(error.orig),str(error.orig)
                db.rollback()
            else:raise AssertionError(mode+' committed')
        assert snapshot(owner)==before and count(owner)==0
    return ['missing_binding_entire_inverse_rolled_back','wrong_raw_key_entire_inverse_rolled_back']


def global_token_probe(owner,inverse_id):
    # Isolate the global token's unique index with an owner-only negative row.
    # All other unique coordinates are fresh. No trigger or FK is disabled;
    # this deliberately invalid row must be rejected before it can persist.
    with Session(owner) as db:
        columns=list(db.execute(text('SELECT * FROM public.'+binding_sql.TABLE+' LIMIT 0')).keys())
        identifier=uuid4()
        replacements={
            'fact_id':identifier,'inverse_id':identifier,'request_id':uuid4().hex,
            'reversal_key_hash':uuid4().hex+uuid4().hex,
            'approval_key_hash':uuid4().hex+uuid4().hex,
            'correction_key_hash':uuid4().hex+uuid4().hex,
        }
        expressions=[(':'+name) if name in replacements else 'b.'+name for name in columns]
        try:
            db.execute(text('INSERT INTO public.'+binding_sql.TABLE+' ('+','.join(columns)+') SELECT '+
                ','.join(expressions)+' FROM public.'+binding_sql.TABLE+' b WHERE b.fact_id=:original'),
                dict(replacements,original=inverse_id))
        except DBAPIError as error:
            assert error.orig.sqlstate=='23505',str(error.orig)
            assert error.orig.diag.table_name==binding_sql.TABLE,str(error.orig)
            assert error.orig.diag.constraint_name=='stock_loss_request_key_bindings_key_token_key',str(error.orig)
            db.rollback()
        else:raise AssertionError('global raw-key token accepted an independent duplicate')


def reused_key_approval(owner,api,context,binding,inverse_id,inverse_hash,inverse_command,snapshot):
    before=snapshot(owner)
    request=CorrectionApprove(**binding,reversal_id=inverse_id,expected_reversal_hash=inverse_hash,
        disposition='restore_available',request_id=uuid4().hex,idempotency_key=inverse_command.idempotency_key)
    with Session(api) as db:
        actor=load_formal_principal(db,context['admin_id']);coordinates=original_request(actor=actor,request=request)
        root=db.get(StockLossDisposition,request.root_disposition_id);order=db.get(StockOperationOrder,root.operation_id)
        # Direct API-role fact construction deliberately bypasses Python's
        # coordinate precheck. The DB must reject the shared raw key itself.
        row=StockLossCorrectionDecision(id=uuid4(),root_disposition_id=root.id,actor_user_id=actor.user_id,
            actor_person_id=actor.person_id,authorization_version=actor.authorization_version,
            request_id=request.request_id,idempotency_key_hash=coordinates.key_hash,request_hash=coordinates.request_hash,
            reason=request.reason,command_jsonb=coordinates.document,reversal_id=inverse_id,
            expected_reversal_hash=inverse_hash,disposition=request.disposition,created_at=datetime.now(timezone.utc))
        db.add(row);db.flush();business_events.record(db,row=row,root=root,order=order)
        try:
            bound_commands.register(db,kind='approval',identifier=row.id,client_key=request.idempotency_key)
            db.commit()
        except DBAPIError as error:
            assert error.orig.sqlstate=='23505',str(error.orig)
            assert error.orig.diag.table_name==binding_sql.TABLE,str(error.orig)
            assert error.orig.diag.constraint_name in {
                'stock_loss_request_key_bindings_'+column+'_key'
                for column in ('key_token','reversal_key_hash','approval_key_hash','correction_key_hash')
            },str(error.orig)
            db.rollback()
        else:raise AssertionError('same raw key crossed inverse/approval actions')
    assert snapshot(owner)==before and count(owner)==1
    global_token_probe(owner,inverse_id)
    assert snapshot(owner)==before and count(owner)==1
    return 'cross_action_api_reuse_and_isolated_global_token_both_rejected'



def seal_provenance(owner,api,context,inverse_command,snapshot):
    before=snapshot(owner)
    bad=inverse_command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    with Session(api) as db:
        actor=load_formal_principal(db,context['admin_id']);coordinates=original_request(actor=actor,request=bad)
        keys=sealed_inverse._keys(bad);forged='a'*64
        assert forged not in keys
        row=StockLossInverseRequestSeal(id=uuid4(),root_disposition_id=bad.root_disposition_id,
            actor_user_id=actor.user_id,actor_person_id=actor.person_id,authorization_version=actor.authorization_version,
            request_id=bad.request_id,idempotency_key_hash=coordinates.key_hash,request_hash=coordinates.request_hash,
            reason=bad.reason,command_jsonb=coordinates.document,reversal_key_hash=keys[0],approval_key_hash=forged,
            correction_key_hash=keys[2],request_reference=sealed_inverse.posting._request_reference(bad.request_id),
            plan_hash=bad.expected_plan_hash,created_at=datetime.now(timezone.utc))
        db.add(row);db.flush()
        try:
            bound_commands.register(db,kind='inverse_seal',identifier=row.id,client_key=bad.idempotency_key)
            db.commit()
        except DBAPIError as error:
            assert error.orig.sqlstate=='23514' and 'client key does not prove stored action hashes' in str(error.orig),str(error.orig)
            db.rollback()
        else:raise AssertionError('forged non-selected action alias accepted')
    assert snapshot(owner)==before and count(owner)==3
    request=inverse_command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    with Session(api) as db:
        result=bound_commands.seal(db,actor=load_formal_principal(db,context['admin_id']),request=request)
        assert result['request_state']=='sealed'
        identifier=UUID(result['seal']['seal_id'])
        bound_commands.register(db,kind='inverse_seal',identifier=identifier,client_key=request.idempotency_key)
        db.commit()
        answer=recovery_checks.readonly(api,context,request)
        assert answer==result and answer['retry_allowed'] is False
    assert count(owner)==4
    # The seal changes only its own immutable row and audit chain, never stock.
    after=snapshot(owner)
    assert all(after[k]==before[k] for k in before if k!='audit_chain_heads')
    return ['forged_seal_action_alias_rejected','stock_neutral_seal_binding_commit_and_exact_recovery','exact_duplicate_registration_no_second_binding']
