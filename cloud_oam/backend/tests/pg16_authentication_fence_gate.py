"""Independent audit domains commit; inventory and seal evidence remain fenced."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app.foundation_models import AuditEvent
from app.formal_services.audit_chain import (
    append_audit_event, verify_audit_event_in_read_snapshot,
)
from pg16_loss_execution_auth_isolation import facts, verify as verify_session_isolation


SEALS = ('stock_loss_inverse_request_seal', 'stock_loss_correction_approval_seal',
         'stock_loss_correction_execution_seal')


def _audit_state(owner):
    with owner.connect() as db:
        return {name: db.scalar(text(
            "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) "
            'FROM public.' + name + ' t'))
            for name in ('audit_events', 'audit_chain_heads', 'auth_sessions',
                         'auth_refresh_tokens', 'state_transition_events')}


def _values(user_id, stream, aggregate):
    return dict(stream_key=stream, actor_user_id=user_id,
                action='synthetic.audit_domain_probe', aggregate_type=aggregate,
                aggregate_id=str(uuid4()), request_id='audit-probe-' + uuid4().hex,
                before_jsonb={}, after_jsonb={}, occurred_at=datetime.now(timezone.utc))


def verify(engines, *, user_id):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    positive = verify_session_isolation(engines, user_id=user_id)
    independent = []
    for isolation in ('READ COMMITTED', 'REPEATABLE READ'):
        protected = facts(owner)
        values = _values(user_id, 'authorization', 'control_projection_publication')
        with owner.connect() as held:
            held.execute(text("SELECT id FROM inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE")).one()
            with api.connect().execution_options(isolation_level=isolation) as connection:
                with Session(connection) as db:
                    db.execute(text("SET LOCAL lock_timeout='500ms'"))
                    db.execute(text("SET LOCAL statement_timeout='10s'"))
                    append_audit_event(db, **values)
                    db.commit()
            with Session(api) as db:
                event = db.scalars(select(AuditEvent).where(
                    AuditEvent.stream_key == 'authorization',
                    AuditEvent.aggregate_id == values['aggregate_id'])).one()
                verify_audit_event_in_read_snapshot(db, stream_key='authorization', event_id=event.id)
            held.rollback()
        assert facts(owner) == protected
        independent.append(dict(isolation=isolation, committedWhileLedgerLocked=True,
                                auditVerified=True, inventoryAndNotificationsUnchanged=True))

    # Force this specific deferred constraint, so another older stock fence
    # cannot accidentally make the changed 0161 fence appear to be working.
    cases = [('inventory', 'synthetic_stock_fact'), ('material_request', 'synthetic_request')]
    cases += [(stream, seal) for stream in ('authentication', 'authorization') for seal in SEALS]
    fenced = []
    for stream, aggregate in cases:
        before = _audit_state(owner)
        values = _values(user_id, stream, aggregate)
        with owner.connect() as held:
            held.execute(text("SELECT id FROM inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE")).one()
            with Session(api) as db:
                db.execute(text("SET LOCAL lock_timeout='500ms'"))
                db.execute(text("SET LOCAL statement_timeout='10s'"))
                append_audit_event(db, **values)
                db.flush()
                try:
                    db.execute(text('SET CONSTRAINTS trg_correction_closure_0161_0 IMMEDIATE'))
                except DBAPIError as error:
                    assert error.orig.sqlstate == '55P03', (stream, aggregate)
                    assert 'rsc_fence_loss_correction_seal_0161' in str(error.orig)
                    assert 'inventory_ledger_heads' in str(error.orig)
                    db.rollback()
                else:
                    raise AssertionError('stock/seal audit bypassed inventory fence')
            held.rollback()
        assert _audit_state(owner) == before
        fenced.append(dict(stream=stream, aggregate=aggregate))

    rejected = []
    for stream in ('authentication', 'authorization'):
        for aggregate in SEALS:
            before = _audit_state(owner)
            with Session(api) as db:
                append_audit_event(db, **_values(user_id, stream, aggregate))
                try:
                    db.commit()
                except DBAPIError as error:
                    assert error.orig.sqlstate == '23514', (stream, aggregate)
                    assert 'seal' in str(error.orig)
                    db.rollback()
                else:
                    raise AssertionError('detached seal audit committed in independent domain')
            assert _audit_state(owner) == before
            rejected.append(dict(stream=stream, aggregate=aggregate))
    with Session(api) as db:
        try:
            db.execute(text('SELECT public.rsc_fence_loss_correction_seal_0161()'))
        except DBAPIError as error:
            assert error.orig.sqlstate == '42501'
            db.rollback()
        else:
            raise AssertionError('private trigger became API executable')
    return dict(status='passed', authenticationIsolation=positive,
                authorizationIsolation=independent, stockAndSealAuditsFenced=fenced,
                detachedSealAuditsRejected=rejected, failedRowsRolledBack=True,
                privateTriggerExecutionDenied=True, productionAcceptance=False)
