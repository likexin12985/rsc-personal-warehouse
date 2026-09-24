"""Real actor/time admission and retention using factory-owned PG16 engines."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import runpy,time
from uuid import UUID,uuid4
import pytest
from sqlalchemy import event,select,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.foundation_models import Organization,Person,Role,RoleAssignment,RolePermission,Permission
from app.formal_access import load_formal_principal
from app.formal_services import opening_stocktake as opening
from app.inventory_control_projection_models import ControlProjectionPublication as Publication
from app.models import User
from app.stocktake_models import FormalStocktakeTask
from pg16_control_start_concurrency_gate import _command
from test_formal_access import make_user,assign

SQL='SELECT public.rsc_assert_opening_actor_0126(:user,:version,:region)'


def facts(owner):
    """Full rows, not counts: failures must leave every startup fact unchanged."""
    from app.foundation_models import AuditEvent,StateTransitionEvent,OutboxEvent,AuditChainHead
    from app.inventory_models import InventoryTransaction,StockBalance,InventoryLedgerHead
    from app.stocktake_models import (InventoryFreeze,FormalStocktakeScope,StocktakeRound,
        StocktakeSnapshotLine,StocktakeControlSnapshotLine)
    result={}
    with owner.connect() as db:
        for model in (FormalStocktakeTask,InventoryFreeze,FormalStocktakeScope,StocktakeRound,
                      StocktakeSnapshotLine,StocktakeControlSnapshotLine,AuditEvent,StateTransitionEvent,
                      OutboxEvent,AuditChainHead,InventoryTransaction,StockBalance,InventoryLedgerHead):
            result[model.__tablename__]=db.scalar(text('SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb) FROM public.'+model.__tablename__+' t'))
    return result


def assert_actor_commit(owner,api,edge,projector,backup,world,published):
    command=_command(owner,world,published)
    # No new runtime capability. All invocation is through sealed task triggers.
    args=dict(user=world.actor.user_id,version=world.actor.authorization_version,region=world.region)
    for engine in (api,edge,projector,backup):
        with engine.connect() as db:
            with pytest.raises(DBAPIError) as error:db.execute(text(SQL),args)
            assert error.value.orig.sqlstate=='42501'
    for changes in ({'version':args['version']+1},{'region':uuid4()},{'user':'missing'}):
        with owner.connect() as db:
            with pytest.raises(DBAPIError) as error:db.execute(text(SQL),args|changes)
            assert error.value.orig.sqlstate=='42501'
    before=facts(owner)
    for value in (None,0,args['version']+1):
        with Session(api) as db:
            def corrupt(session,context,instances):
                for row in session.new:
                    if isinstance(row,FormalStocktakeTask):row.opening_authorization_version=value
            event.listen(db,'before_flush',corrupt)
            actor=load_formal_principal(db,world.actor.user_id)
            # Fault injection changes the application-produced row; actual
            # PostgreSQL ACLs, admission functions and transaction stay real.
            with pytest.raises((DBAPIError,opening.OpeningStocktakeError)):
                opening.start_opening_stocktake(db,actor=actor,command=command,idempotency_key=uuid4().hex,request_id=uuid4().hex)
            db.rollback()
        assert facts(owner)==before
    with Session(api) as db:
        actor=load_formal_principal(db,world.actor.user_id)
        result=opening.start_opening_stocktake(db,actor=actor,command=command,idempotency_key=uuid4().hex,request_id=uuid4().hex)
        task=db.get(FormalStocktakeTask,result.task_id)
        assert task.opening_authorization_version==actor.authorization_version
        # Identity graph writes contend with the real current-actor lock set.
        for sql in ("UPDATE public.users SET authorization_version=authorization_version+1 WHERE id=:id",
                    "UPDATE public.role_assignments SET status='revoked',revoked_at=clock_timestamp(),revoked_by=:id,updated_at=clock_timestamp() WHERE user_id=:id"):
            with owner.connect() as writer:
                writer.execute(text("SET LOCAL lock_timeout='150ms'"))
                with pytest.raises(DBAPIError) as error:writer.execute(text(sql),{'id':actor.user_id})
                assert error.value.orig.sqlstate=='55P03';writer.rollback()
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'));db.rollback()
    assert facts(owner)==before

    # The publication remains valid well after these authorization deadlines.
    with Session(owner) as db:
        end=db.get(Publication,UUID(published['publication_id'])).valid_until
        assert end>datetime.now(timezone.utc)+timedelta(minutes=1)
        manager_role=db.scalars(select(Role).where(Role.code=='provincial_manager')).one()
        manager_role_id=manager_role.id
        admin_role=db.scalars(select(Role).where(Role.code=='admin')).one()
        original=db.get(User,world.actor.user_id);person=db.get(Person,original.person_id)
        hq=db.get(Organization,person.organization_id)
        # Independently authorized regional actor, not the publication reviewer.
        user,_=make_user(db,db.get(Organization,world.region),name='Synthetic expiring opening manager')
        deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=10)
        assign(db,user,manager_role,scope_type='organization',scope_id=str(world.region),valid_to=deadline)
        expiring_user=user.id;db.commit()
    _expires_at_commit(owner,api,world,command,expiring_user,deadline,'role expiry')

    with Session(owner) as db:
        permission=db.scalars(select(Permission).where(Permission.resource=='stocktake',Permission.action=='manage',Permission.field_code=='')).one()
        deny_permission=db.scalars(select(RolePermission).where(RolePermission.role_id==manager_role_id,RolePermission.permission_id==permission.id)).one()
        restore_effect=deny_permission.effect;permission_row_id=deny_permission.id
        deny_permission.effect='deny'
        original=db.get(User,world.actor.user_id);hq=db.get(Organization,db.get(Person,original.person_id).organization_id)
        user,_=make_user(db,hq,name='Synthetic scheduled deny opening manager')
        assign(db,user,db.scalars(select(Role).where(Role.code=='admin')).one(),scope_type='national',scope_id='*')
        deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=10)
        assign(db,user,db.get(Role,manager_role_id),scope_type='organization',scope_id=str(world.region),status='scheduled',valid_from=deadline)
        denied_user=user.id;db.commit()
    try:_expires_at_commit(owner,api,world,command,denied_user,deadline,'scheduled global deny')
    finally:
        with Session(owner) as db:
            db.get(RolePermission,permission_row_id).effect=restore_effect;db.commit()
    assert facts(owner)==before
    assert_http_expiry(owner,api,world,published,command)
    print('PG16 0126 actor admission: server version, missing/stale evidence refusal, exact principal locks, role expiry and future global deny at real COMMIT; full task/freeze/audit/outbox rollback PASS',flush=True)


def _expires_at_commit(owner,api,world,command,user,deadline,label):
    before=facts(owner)
    with Session(api) as db:
        actor=load_formal_principal(db,user)
        result=opening.start_opening_stocktake(db,actor=actor,command=command,idempotency_key=uuid4().hex,request_id=uuid4().hex)
        assert db.get(FormalStocktakeTask,result.task_id).opening_authorization_version==actor.authorization_version
        remaining=(deadline-datetime.now(timezone.utc)).total_seconds()
        assert 0<remaining<15,label+' fixture must expire after facts and before commit'
        time.sleep(remaining+.06)
        with pytest.raises(DBAPIError) as error:db.commit()
        assert error.value.orig.sqlstate=='42501',label
        assert '0126 opening actor' in str(error.value.orig),label
        db.rollback()
    assert facts(owner)==before,label


def assert_actor_migration(owner):
    m=runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/20261105_0126_opening_actor_commit.py'))
    before=facts(owner)
    for name,(args,*_) in m['FUNCTIONS'].items():
        for mutation in ('body','acl'):
            with owner.connect() as db:
                signature=f'public.{name}({args})'
                if mutation=='acl':db.exec_driver_sql('GRANT EXECUTE ON FUNCTION '+signature+' TO star_oam_api')
                else:
                    definition=db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
                    db.exec_driver_sql(definition.replace('BEGIN\n','BEGIN\n    -- synthetic drift\n',1),execution_options={'no_parameters':True})
                with Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError) as error:m['downgrade']()
                assert '0126 actor function source or ACL drift' in str(error.value.orig);db.rollback()
    for name in m['TRIGGERS']:
        with owner.connect() as db:
            db.exec_driver_sql('ALTER TABLE public.stocktake_tasks DISABLE TRIGGER '+name)
            with Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError) as error:m['downgrade']()
            assert '0126 actor trigger drift' in str(error.value.orig);db.rollback()
    with owner.connect() as db:
        db.exec_driver_sql('ALTER TABLE public.stocktake_tasks ALTER COLUMN opening_authorization_version SET DEFAULT 1')
        with Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError) as error:m['downgrade']()
        assert '0126 authorization column drift' in str(error.value.orig);db.rollback()
    with owner.connect() as db:
        db.exec_driver_sql('ALTER TABLE public.stocktake_tasks DROP CONSTRAINT ck_opening_authorization_version_0126')
        db.exec_driver_sql('ALTER TABLE public.stocktake_tasks ADD CONSTRAINT ck_opening_authorization_version_0126 CHECK (opening_authorization_version IS NULL OR opening_authorization_version>0)')
        with Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError) as error:m['downgrade']()
        assert '0126 authorization column drift' in str(error.value.orig);db.rollback()
    with owner.connect() as db:
        task=db.execute(text('SELECT id,opening_authorization_version FROM public.stocktake_tasks WHERE task_type=\'opening\' LIMIT 1')).one()
        assert task.opening_authorization_version>0
        with pytest.raises(DBAPIError) as error:
            db.execute(text('UPDATE public.stocktake_tasks SET opening_authorization_version=NULL WHERE id=:id'),{'id':task.id})
        assert '0126 opening authorization evidence is immutable' in str(error.value.orig);db.rollback()
    with owner.connect() as db,Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError) as error:m['downgrade']()
    assert '0126 downgrade blocked: opening authorization evidence must be retained' in str(error.value.orig)
    assert facts(owner)==before
    print('PG16 0126 actor evidence: private function/ACL/trigger/column/constraint drift refused, immutable version and populated downgrade retention PASS',flush=True)


def assert_http_expiry(owner,api,world,published,command):
    """Real JWT and API transaction; delay only after persisted task facts exist."""
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.config import get_settings
    from app.models import AuthSession
    from app.security import create_access_token
    from app.routers.formal_opening_stocktake import router
    with Session(owner) as db:
        user,_=make_user(db,db.get(Organization,world.region),name='Synthetic HTTP expiring manager')
        now=db.scalar(text('SELECT clock_timestamp()'));deadline=now+timedelta(seconds=10)
        role=db.scalars(select(Role).where(Role.code=='provincial_manager')).one()
        assign(db,user,role,scope_type='organization',scope_id=str(world.region),valid_to=deadline)
        session=AuthSession(user_id=user.id,refresh_token_hash=uuid4().hex+uuid4().hex,client_type='web',
            device_id=uuid4().hex,ip_address='hmac:1:'+'a'*64,created_at=now,expires_at=now+timedelta(hours=1))
        db.add(session);db.flush();token=create_access_token(user.id,session.id);db.commit()
    before=facts(owner);observed=[]
    class ExpiringSession(Session):
        def commit(self):
            assert self.scalar(select(FormalStocktakeTask.id).where(FormalStocktakeTask.task_no==command.task_no)) is not None
            remaining=(deadline-datetime.now(timezone.utc)).total_seconds()
            assert 0<remaining<15
            observed.append('facts_present_before_wait')
            time.sleep(remaining+.06)
            return super().commit()
    app=FastAPI();app.include_router(router,prefix='/api')
    def session():
        with ExpiringSession(api) as db:yield db
    app.dependency_overrides[get_db]=session
    payload=dict(publication_id=published['publication_id'],region_org_id=str(world.region),task_no=command.task_no,
        scopes=[dict(owner_org_id=str(row.owner_org_id),location_id=str(row.location_id),
                     assignee_user_id=row.assignee_user_id,freeze_mode=row.freeze_mode) for row in command.scopes])
    with patch('app.dependencies.get_settings',return_value=get_settings().model_copy(update={'environment':'production'})),TestClient(app) as client:
        response=client.post('/api/v1/stocktakes/opening/from-publication',json=payload,
            headers={'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex,'X-Request-ID':uuid4().hex})
    assert observed==['facts_present_before_wait']
    assert response.status_code==403,response.text
    assert response.json()['detail']['code']=='opening_authorization_changed'
    assert response.headers['cache-control']=='no-store'
    assert facts(owner)==before
    print('PG16 0126 production JWT selected-start: facts created, actor expires, real COMMIT refused, sanitized HTTP 403 and full rollback PASS',flush=True)
