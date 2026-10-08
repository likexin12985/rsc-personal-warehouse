"""Exercise the exact scrap fence, independently of older deferred guards."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app.foundation_models import StateTransitionEvent
from app.formal_services.audit_chain import append_audit_event


def verify(engines, *, user_id):
    # Import only after the original authentication gate module is initialized.
    from pg16_authentication_fence_gate import _audit_state, _values
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    audits = [('inventory','synthetic_stock_fact',None,{}),
              ('material_request','synthetic_request',None,{})]
    for stream in ('authentication','authorization'):
        audits.extend([(stream,'stock_scrap_request_seal',None,{}),
                       (stream,'auth_session','seal_scrap_request',{}),
                       (stream,'auth_session',None,{'scrap_key_hash':'a'*64})])
    states = [dict(operation='wrong',request_id='authreq-'+'a'*64),
              dict(operation='formal_authentication_state_transition'),
              dict(operation='formal_authentication_state_transition',request_id='invalid'),
              dict(operation='formal_authentication_state_transition',request_id='authreq-'+'a'*64,
                   request_reference='inventory-request-'+'b'*64),
              dict(operation='formal_authentication_state_transition',request_id='authreq-'+'a'*64,
                   scrap_key_hash='b'*64)]
    cases = [('audit',value) for value in audits]+[('state',value) for value in states]
    for kind,value in cases:
        before = _audit_state(owner)
        with owner.connect() as held:
            held.execute(text("SELECT id FROM inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE")).one()
            with Session(api) as db:
                db.execute(text("SET LOCAL lock_timeout='500ms'"))
                db.execute(text("SET LOCAL statement_timeout='10s'"))
                if kind == 'audit':
                    stream,aggregate,action,body = value
                    values = _values(user_id,stream,aggregate)
                    values['after_jsonb'] = body
                    if action is not None:
                        values['action'] = action
                    append_audit_event(db,**values)
                else:
                    db.add(StateTransitionEvent(aggregate_type='auth_session',aggregate_id=str(uuid4()),
                        from_status=None,to_status='active',reason='synthetic fence rejection',actor_id=user_id,
                        idempotency_key=uuid4().hex,metadata_jsonb=value,occurred_at=datetime.now(timezone.utc)))
                db.flush()
                try:
                    db.execute(text('SET CONSTRAINTS trg_scrap_seal_fence_0165 IMMEDIATE'))
                except DBAPIError as error:
                    assert error.orig.sqlstate == '55P03', (kind,value)
                    assert 'rsc_fence_scrap_seals_0165' in str(error.orig)
                    assert 'inventory_ledger_heads' in str(error.orig)
                    db.rollback()
                else:
                    raise AssertionError('stock or ambiguous authentication evidence bypassed scrap fence')
            held.rollback()
        assert _audit_state(owner) == before
    # With the lock released, explicit detached seal aggregates and action
    # spoofing still fail the seal proof, regardless of their audit stream.
    for stream,aggregate,action in [(s,a,c) for s in ('authentication','authorization')
            for a,c in [('stock_scrap_request_seal',None),('auth_session','seal_scrap_request')]]:
        before = _audit_state(owner)
        with Session(api) as db:
            values = _values(user_id,stream,aggregate)
            if action: values['action'] = action
            append_audit_event(db,**values)
            try:
                db.execute(text('SET CONSTRAINTS trg_scrap_seal_fence_0165 IMMEDIATE'))
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate == '23514'
                assert 'seal' in str(error.orig)
                db.rollback()
            else:
                raise AssertionError('detached scrap seal audit committed')
        assert _audit_state(owner) == before
    with Session(api) as db:
        try:
            db.execute(text('SELECT public.rsc_fence_scrap_seals_0165()'))
        except DBAPIError as error:
            assert error.orig.sqlstate == '42501'
            db.rollback()
        else:
            raise AssertionError('scrap trigger became API executable')
    return dict(passed=True,exactConstraintForced=True,inventoryFenceCases=len(cases),
        detachedSealRejections=4,failedRowsRolledBack=True,privateTriggerExecutionDenied=True)
