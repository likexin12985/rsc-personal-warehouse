"""Notification expansion against the caller's verified disposable PG16.

No database is provisioned here and no notification provider is invoked.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from queue import Queue
import time
from uuid import uuid4

from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import NotificationEvent, NotificationRecipient, NotificationDelivery
from app.formal_services.notification_expansion import expand_notification_event, expand_pending_notification_events


def _event(api, *, recipient=True):
    now=datetime.now(timezone.utc)
    with Session(api) as db:
        item=NotificationEvent(id=uuid4(),event_type='pg16_expansion_proof',business_type='synthetic_notification',
            business_id=str(uuid4()),dedup_key='pg16-expansion:'+uuid4().hex,payload_jsonb={'synthetic':True},
            status='pending',occurred_at=now,created_at=now)
        db.add(item);db.flush()
        if recipient:
            db.add(NotificationRecipient(id=uuid4(),event_id=item.id,user_id=None,channel='wechat',
                recipient_key='synthetic:'+uuid4().hex,status='active',created_at=now))
        db.commit();return item.id


def _denied(engine, sql, params, code):
    with engine.connect() as db:
        try:
            db.execute(text(sql),params);db.commit()
        except DBAPIError as error:
            assert error.orig.sqlstate==code
            db.rollback();return
        raise AssertionError('notification forbidden operation succeeded')


def _blocked(observer,pid,owner):
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        blockers=observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
        if owner in blockers:
            assert observer.scalar(text('SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)'),{'pid':pid})
            return
        time.sleep(.025)
    raise AssertionError('actual notification event lock wait was not observed')


def _expand_competitor(api,key,ready,preload):
    with Session(api) as db:
        db.execute(text("SET LOCAL statement_timeout='12s'"))
        if preload:
            # Retain the instance: this exercises SQLAlchemy's identity map,
            # including after a different transaction commits cancellation.
            retained=db.get(NotificationEvent,key)
            assert retained.status=='pending'
        ready.put(db.scalar(text('SELECT pg_backend_pid()')))
        result=expand_notification_event(db,event_id=key);db.commit()
        return result.event_status,result.created_delivery_count


def assert_notification_expansion_gate(api,owner):
    for engine,role in ((api,'star_oam_api'),(owner,'star_oam_migrator')):
        with engine.connect() as db:
            assert db.scalar(text('SELECT current_user'))==role
            assert int(db.scalar(text('SHOW server_version_num')))//10000==16
    with owner.connect() as db:
        assert tuple(db.execute(text("SELECT has_table_privilege('star_oam_api','notification_events','UPDATE'),has_column_privilege('star_oam_api','notification_events','status','UPDATE'),has_any_column_privilege('star_oam_api','notification_recipients','UPDATE')")).one())==(False,True,False)
    key=_event(api)
    with Session(api) as db:
        assert expand_notification_event(db,event_id=key).created_delivery_count==1;db.commit()
        assert expand_notification_event(db,event_id=key).created_delivery_count==0;db.commit()
    for engine,sql,code in (
        (api,"UPDATE notification_events SET payload_jsonb='{}' WHERE id=:id",'42501'),
        (api,'DELETE FROM notification_events WHERE id=:id','42501'),
        (api,"UPDATE notification_recipients SET status='suppressed' WHERE event_id=:id",'42501'),
        (api,"UPDATE notification_events SET status='pending' WHERE id=:id",'23514'),
        (owner,"UPDATE notification_events SET payload_jsonb='{}' WHERE id=:id",'23514'),
        (owner,'DELETE FROM notification_events WHERE id=:id','23514'),
    ):
        _denied(engine,sql,{'id':key},code)
    with api.begin() as db:
        db.execute(text("UPDATE notification_events SET status='cancelled' WHERE id=:id"),{'id':key})
    for status in ('pending','expanded'):
        _denied(api,'UPDATE notification_events SET status=:status WHERE id=:id',{'id':key,'status':status},'23514')

    for cancelled in (False,True):
        key=_event(api);ready=Queue()
        with owner.connect() as observer, Session(api) as db, ThreadPoolExecutor(max_workers=1) as pool:
            db.execute(text("SET LOCAL statement_timeout='12s'"))
            pid=db.scalar(text('SELECT pg_backend_pid()'))
            if cancelled:
                db.execute(text("UPDATE notification_events SET status='cancelled' WHERE id=:id"),{'id':key})
            else:
                assert expand_notification_event(db,event_id=key).created_delivery_count==1
            contender=pool.submit(_expand_competitor,api,key,ready,cancelled)
            try:
                _blocked(observer,ready.get(timeout=5),pid)
                db.commit()
                assert contender.result(timeout=15)==('cancelled' if cancelled else 'expanded',0)
            finally:
                db.rollback()
        with Session(api) as db:
            assert db.scalar(select(func.count()).select_from(NotificationDelivery).join(NotificationRecipient).where(NotificationRecipient.event_id==key))==(0 if cancelled else 1)

    key=_event(api,recipient=False)
    with Session(api) as holder:
        holder.execute(select(NotificationEvent).where(NotificationEvent.id==key).with_for_update()).one()
        with Session(api) as worker:
            results=expand_pending_notification_events(worker,limit=500)
            assert all(result.event_id!=key for result in results)
            worker.rollback()
        holder.rollback()
    print('PG16 notification expansion: column ACL, immutable content, safe state transitions, real concurrent workers, cached cancellation and SKIP LOCKED PASS; provider calls 0',flush=True)
