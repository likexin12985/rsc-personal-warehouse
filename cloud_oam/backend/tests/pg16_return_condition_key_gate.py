"""Native key ownership/reciprocal constraints, using the actual private writer.

Synthetic legacy shipment headers test reverse key fences, not physical shipping.
No trigger is disabled; all rejected writes roll back the entire transaction.
"""
from uuid import uuid4, UUID
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from time import monotonic, sleep
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.inventory_models import Shipment
from app.return_condition_key_schema import NAME
from app.formal_services.stock_loss_corrections import return_condition_keys as keys
from app.formal_services.stock_loss_corrections import return_condition_submission as writer


def refused(db, operation, message, *, code='23514'):
    try:
        operation()
    except DBAPIError as error:
        assert error.orig.sqlstate==code, str(error.orig)
        assert message in str(error.orig), str(error.orig)
        db.rollback()
    else:
        raise AssertionError('native key protection accepted: '+message)


def header(db, command):
    table=Shipment.__table__
    row=dict(db.execute(select(table).limit(1)).mappings().one())
    row.update(id=uuid4(),shipment_no='KEY-PROBE-'+uuid4().hex,
        idempotency_key_hash=keys.aliases(command.idempotency_key)['raw_key_hash'])
    db.execute(table.insert(),row)


def before_commit(api, *, command, original):
    report=[]
    for sql in ('INSERT INTO public.'+NAME+' DEFAULT VALUES',
                'UPDATE public.'+NAME+" SET key_token=repeat('0',64) WHERE false",
                'DELETE FROM public.'+NAME+' WHERE false'):
        with Session(api) as db:
            refused(db,lambda:db.execute(text(sql)),'permission denied',code='42501')
        report.append(sql.split()[0].lower()+'_denied')
    with Session(api) as db, patch.object(keys,'record',return_value=None):
        actor=load_formal_principal(db,original['receiverUserId'])
        writer.submit(db,actor=actor,request=command)
        refused(db,db.commit,'condition exact durable request binding required')
    report.append('missing_binding_actual_commit_refused')
    real_record=keys.record
    def wrong(db, *, event, request):
        return real_record(db,event=event,request=request.model_copy(update={'idempotency_key':uuid4().hex}))
    with Session(api) as db, patch.object(keys,'record',wrong):
        actor=load_formal_principal(db,original['receiverUserId'])
        refused(db,lambda:writer.submit(db,actor=actor,request=command),'condition raw key does not prove event')
    report.append('wrong_raw_key_refused')
    with Session(api) as db, patch.object(writer.coordinates,'require_unused',return_value=None):
        actor=load_formal_principal(db,original['receiverUserId'])
        old_request=command.model_copy(update={'request_id':original['inboundRequestId']})
        refused(db,lambda:writer.submit(db,actor=actor,request=old_request),
            'condition durable request collides with another action')
    report.append('actual_old_inbound_request_refused_by_registrar')
    # Bypass only the new Python precheck to prove the independent SQL fence.
    # The real source, identity, files, inventory writer and all SQL guards run.
    with Session(api) as db, patch.object(writer.coordinates,'require_unused',return_value=None):
        actor=load_formal_principal(db,original['receiverUserId'])
        header(db,command)
        refused(db,lambda:writer.submit(db,actor=actor,request=command),
            'condition durable request collides with another action')
    report.append('legacy_header_first_refused_by_registrar')
    with Session(api) as db:
        actor=load_formal_principal(db,original['receiverUserId'])
        writer.submit(db,actor=actor,request=command)
        header(db,command)
        refused(db,db.commit,'condition durable request collides with another action')
    report.append('condition_first_legacy_header_actual_commit_refused')
    return report


def commit_with_late_header(api, *, command, original):
    """Commit the real new writer while an independent API header waits on it."""
    ready=Queue()
    def late():
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout='15000ms'"))
            ready.put(db.scalar(text('SELECT pg_backend_pid()')))
            try:
                header(db,command)
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514', str(error.orig)
                assert 'condition durable request collides with another action' in str(error.orig)
                db.rollback()
                return dict(refused=True,sqlstate=error.orig.sqlstate)
            raise AssertionError('late legacy header committed after a condition binding')
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as executor:
        try:
            pid=db.scalar(text('SELECT pg_backend_pid()'))
            result=writer.submit(db,actor=load_formal_principal(db,original['receiverUserId']),request=command)
            pending=executor.submit(late)
            other=ready.get(timeout=5)
            observed=False;deadline=monotonic()+5
            while monotonic()<deadline:
                with api.connect() as observer:
                    observed=observer.scalar(text('SELECT :owner=ANY(pg_blocking_pids(:waiting))'),
                        {'owner':pid,'waiting':other})
                if observed: break
                if pending.done(): pending.result()
                sleep(.05)
            assert observed,'the independent legacy insert did not wait on the new transaction'
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            db.commit()
            verdict=pending.result(timeout=10)
        finally:
            # Release every held lock before joining the worker, even if an
            # assertion fails; its own SQL timeout also bounds failure cleanup.
            db.rollback()
    return result,dict(independentApiConnections=True,liveBlockingPidObserved=True,
        newWriterActualCommit=True,lateLegacyHeaderActualCommitRefused=verdict['refused'])


def after_commit(owner, api, *, command, result):
    event_id=UUID(result['event_id'])
    with Session(api) as db:
        table=writer.tables()['stock_condition_events']
        event=db.execute(select(table).where(table.c.id==event_id)).mappings().one()
        binding=keys.match_original(db,event=event,request=command)
        assert command.idempotency_key not in tuple(str(v) for v in binding.values())
        keys.record(db,event=event,request=command)
        db.commit()
    with Session(api) as db:
        assert keys.verify(db,event=event)==binding
        header(db,command)
        refused(db,db.commit,'condition durable request collides with another action')
    with Session(owner) as db:
        refused(db,lambda:db.execute(keys.table().update().where(keys.table().c.event_id==event_id)
            .values(key_token='0'*64)),'condition history is append only')
    with Session(api) as db:
        assert keys.verify(db,event=event)==binding
        for role in ('star_oam_api','star_oam_backup','star_oam_projector','star_oam_edge','edge_inbox'):
            for action in ('INSERT','UPDATE','DELETE','TRUNCATE'):
                assert not db.scalar(text('SELECT has_table_privilege(:role,:table,:action)'),
                    dict(role=role,table='public.'+NAME,action=action))
            allowed=db.scalar(text('SELECT has_function_privilege(:role,:signature,\'EXECUTE\')'),
                dict(role=role,signature='public.rsc_register_condition_request_key(uuid,text)'))
            assert allowed==(role=='star_oam_api')
    return dict(exactBindingRead=True,rawKeyNotStored=True,duplicateRegistrationNoChange=True,
        lateLegacyHeaderActualCommitRefused=True,ownerMutationRefused=True,minimumPrivileges=True,
        absenceSeals=False)
