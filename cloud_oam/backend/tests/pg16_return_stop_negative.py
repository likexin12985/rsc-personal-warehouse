"""Candidate native rollback probes; called only with the owned synthetic DB."""
from unittest.mock import patch
from sqlalchemy import event,select,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.foundation_models import OutboxEvent,StateTransitionEvent
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.formal_services.stock_loss_corrections import inverse_posting,return_stop


def snapshot(owner):
    from pg16_stock_loss_submit_gate import snapshot as stock
    result=stock(owner)
    with owner.connect() as db:
        for table in ('stock_loss_return_stops','stock_loss_disposition_reversals','stock_loss_request_key_bindings',
                      'audit_events','outbox_events','state_transition_events','audit_chain_heads'):
            result[table]=tuple(db.scalars(text('SELECT to_jsonb(t)::text FROM public.'+table+' t ORDER BY to_jsonb(t)::text')))
    return result


def exercise(context,request):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    passed=[]
    for damage in ('missing_stop','fingerprint','missing_outbox','wrong_state'):
        before=snapshot(owner)
        with Session(api) as db:
            def corrupt(session,*_):
                for row in tuple(session.new):
                    if isinstance(row,Stop) and damage=='fingerprint': row.evidence_fingerprint='f'*64
                    elif isinstance(row,OutboxEvent) and row.aggregate_type==return_stop.AGGREGATE and damage=='missing_outbox':
                        session.expunge(row)
                    elif isinstance(row,StateTransitionEvent) and row.aggregate_type==return_stop.AGGREGATE and damage=='wrong_state':
                        row.to_status='fulfilled'
            event.listen(db,'before_flush',corrupt)
            try:
                # Remove only Python's final duplicate proof in this negative
                # fixture so the DB must reject the malformed real transaction.
                with patch.object(inverse_posting,'verify_original_inverse',return_value=None):
                    if damage=='missing_stop':
                        with patch.object(return_stop,'record',return_value=None):
                            inverse_posting.execute_unshipped_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=request)
                    else:
                        inverse_posting.execute_unshipped_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=request)
                    try: db.commit()
                    except DBAPIError as error:
                        assert error.orig.sqlstate=='23514' and '0163' in str(error.orig),(damage,str(error.orig))
                        db.rollback()
                    else: raise AssertionError('native malformed stop committed: '+damage)
            finally:
                event.remove(db,'before_flush',corrupt)
        assert snapshot(owner)==before,damage
        passed.append(damage)
        print('PG16 malformed stop '+damage+' rejected and entire transaction unchanged',flush=True)
    return passed
