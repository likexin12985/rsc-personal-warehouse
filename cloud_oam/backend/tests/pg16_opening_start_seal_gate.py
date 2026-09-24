"""Bounded original-request races, using only factory-owned native PG16 engines."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import time
from unittest.mock import patch
from uuid import uuid4
from pathlib import Path
import runpy

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Role
from app.formal_services import opening_stocktake as opening, opening_start_seals as seals
from pg16_opening_publication_fixture import prepare_stocktake_inventory
from pg16_opening_recount_gate import snapshot as opening_snapshot, _wait_blocked
from pg16_release_gate_diagnostics import run_with_sanitized_database_diagnostics
from test_formal_access import assign, make_organization, make_user


def snapshot(owner):
    result = opening_snapshot(owner)
    with owner.connect() as db:
        result['opening_start_command_seals'] = db.scalar(text("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb) FROM opening_start_command_seals t"))
    return result


def run(engines):
    owner, api, edge = (engines[key] for key in ('star_oam_migrator','star_oam_api','edge_inbox'))
    with Session(owner) as db:
        hq = make_organization(db, name='Synthetic original seal HQ')
        region = make_organization(db, name='Synthetic original seal region', parent=hq)
        roles = {row.code:row for row in db.scalars(select(Role))}
        users = {}
        for key, org, role in [('admin',hq,'admin'),('manager',region,'provincial_manager'),('revoked',region,'provincial_manager'),('expiring',region,'provincial_manager')]:
            user,_ = make_user(db,org,name='Synthetic seal '+key)
            assign(db,user,roles[role],scope_type='national' if role=='admin' else 'organization',scope_id='*' if role=='admin' else str(org.id))
            users[key]=user.id
        db.commit()
    fixture = run_with_sanitized_database_diagnostics(owner, lambda:prepare_stocktake_inventory(
        owner,edge,actor_user_id=users['admin'],assignee_user_id=users['manager']), replace_unlinked_database_failure_when=None)
    _http_proof(owner,api,fixture,users)
    def args(request): return dict(region_org_id=fixture['region_org_id'],publication_id=fixture['control_publication_id'],trace_request_id=request)
    def command(request,location):
        return opening.StartOpeningStocktakeCommand(task_no='PG16-SEAL-'+request,region_org_id=fixture['region_org_id'],
            control_source_system_id=fixture['control_source_system_id'],control_sync_run_id=fixture['control_sync_run_id'],
            control_sync_scope_key=fixture['control_scope_key'],control_lines=fixture['control_lines'],
            scopes=(opening.OpeningStocktakeScopeInput(fixture['region_org_id'],location,users['manager'],'hard'),),
            blind_count=True,deadline=fixture['deadline'],note='Synthetic original request race')
    def invoke(db,action,request,location=None,actor=None):
        principal=actor or load_formal_principal(db,users['manager'])
        if action=='start': return opening.start_opening_stocktake(db,actor=principal,command=command(request,location),idempotency_key=uuid4().hex,request_id=request)
        return (seals.seal_start_command if action=='seal' else seals.recover_start_command)(db,actor=principal,**args(request))
    request=uuid4().hex
    with Session(api) as db:
        assert invoke(db,'read',request)['outcome']=='not_observed'
        first=invoke(db,'seal',request); db.commit()
    assert first['outcome']=='sealed' and first['seal']['permanent_nonexecution'] is True
    before=snapshot(owner)
    with Session(api) as db:
        assert invoke(db,'read',request)==first
        assert invoke(db,'seal',request)==first; db.commit()
    assert snapshot(owner)==before
    with Session(api) as db, pytest.raises(opening.OpeningStocktakeError) as denied:
        invoke(db,'start',request,fixture['location_id'])
    assert denied.value.code=='opening_start_request_sealed' and snapshot(owner)==before
    # Deliberately bypass only the Python precheck. Real valid start facts must
    # still lose at PostgreSQL COMMIT; no SQL trigger or authorization is mocked.
    with patch.object(seals,'require_unsealed',return_value=None), Session(api) as db:
        invoke(db,'start',request,fixture['location_id'])
        with pytest.raises(DBAPIError) as caught: db.commit()
        assert caught.value.orig.diag.constraint_name=='opening_start_sealed_0128'
        db.rollback()
    assert snapshot(owner)==before
    print('PG16 immutable negative proof: replay read-only, late application start and SQL bypass refused PASS',flush=True)
    for first_action, second_action, location in [('seal','start',fixture['serial_replay_location_id']),('start','seal',fixture['recount_location_id'])]:
        request=uuid4().hex; ready=Event(); allow_commit=Event(); pid=[]
        def contender():
            with Session(api) as db:
                db.execute(text("SET LOCAL statement_timeout='25s'"))
                actor=load_formal_principal(db,users['manager'])
                pid.append(db.scalar(text('SELECT pg_backend_pid()'))); ready.set()
                result=invoke(db,second_action,request,location,actor)
                assert allow_commit.wait(15); db.commit(); return result
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(api) as db:
                blocker=db.scalar(text('SELECT pg_backend_pid()'))
                winner=invoke(db,first_action,request,location)
                future=pool.submit(contender); assert ready.wait(5)
                _wait_blocked(owner,pid[0],blocker,future)
                db.commit(); before=snapshot(owner); allow_commit.set()
            if first_action=='start':
                loser=future.result(timeout=30)
                assert loser['outcome']=='found' and loser['result']['task_id']==winner.task_id and loser['seal'] is None
            else:
                with pytest.raises(opening.OpeningStocktakeError) as denied: future.result(timeout=30)
                assert denied.value.code=='opening_start_request_sealed'
        assert snapshot(owner)==before
    print('PG16 ledger lock races: start-first recovers positive facts; seal-first permanently refuses late start PASS',flush=True)
    # Current authorization is checked again after a real database lock wait.
    with Session(api) as db: stale=load_formal_principal(db,users['revoked'])
    ready=Event(); pid=[]; before=snapshot(owner)
    def revoke_contender():
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout='25s'")); pid.append(db.scalar(text('SELECT pg_backend_pid()'))); ready.set()
            invoke(db,'seal',uuid4().hex,actor=stale); db.commit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with owner.begin() as db:
            blocker=db.scalar(text('SELECT pg_backend_pid()'))
            db.execute(text("SELECT 1 FROM inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE"))
            future=pool.submit(revoke_contender); assert ready.wait(5); _wait_blocked(owner,pid[0],blocker,future)
            db.execute(text('UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id'),{'id':users['revoked']})
            db.execute(text("UPDATE role_assignments SET status='revoked',revoked_at=clock_timestamp(),revoked_by=:id,updated_at=clock_timestamp() WHERE user_id=:id"),{'id':users['revoked']})
        with pytest.raises(opening.OpeningStocktakeError) as denied: future.result(timeout=30)
        assert denied.value.category=='forbidden'
    assert snapshot(owner)==before
    with owner.begin() as db:
        expiry=db.scalar(text("UPDATE role_assignments SET valid_to=clock_timestamp()+interval '5 seconds',updated_at=clock_timestamp() WHERE user_id=:id RETURNING valid_to"),{'id':users['expiring']})
    before=snapshot(owner)
    with Session(api) as db:
        actor=load_formal_principal(db,users['expiring'])
        invoke(db,'seal',uuid4().hex,actor=actor)
        while db.scalar(text('SELECT clock_timestamp()'))<=expiry: time.sleep(.025)
        with pytest.raises(DBAPIError) as caught: db.commit()
        assert caught.value.orig.diag.constraint_name=='opening_actor_admission_0126'
        db.rollback()
    assert snapshot(owner)==before
    for sql in ('DELETE FROM opening_start_command_seals','UPDATE opening_start_command_seals SET authorization_version=authorization_version','TRUNCATE opening_start_command_seals'):
        with owner.connect() as db,pytest.raises(DBAPIError): db.execute(text(sql))
        with api.connect() as db,pytest.raises(DBAPIError): db.execute(text(sql))
        assert snapshot(owner)==before
    print('PG16 wait revocation, commit-time expiry and immutable row/ACL refusals preserve complete business graph PASS',flush=True)
    _catalog_proof(owner,api,fixture,users['manager'])
    from app.database_security import validate_production_database_security
    from app.edge_database_security import verify_edge_database_boundary
    validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    verify_edge_database_boundary(edge)
    with owner.connect() as revision_connection:
        migration_head=revision_connection.scalar(text('SELECT version_num FROM alembic_version'))
    return dict(status='passed',scope='local-native-pg16-opening-start-seals',migrationHead=migration_head,
        actualPostgreSQL16=True,realJwtSealAndRecovery=True,sealReplayReadOnly=True,lateStartRejected=True,databaseBypassRejected=True,
        observedLedgerWaits=3,positiveStartWins=True,negativeSealWins=True,revocationAfterWaitRefused=True,
        commitTimeExpiryRefused=True,immutableAndRuntimeAcl=True,apiAndEdgeBoundaries=True,
        missingAuditCommitRefused=True,catalogDriftRefused=True,
        graphTables=len(snapshot(owner)),fullReleaseGate=False,githubReleaseGate=False,productionAcceptance=False)


def _http_proof(owner,api,fixture,users):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.config import get_settings
    from app.database import get_db
    from app.models import AuthSession
    from app.routers.formal_opening_stocktake import router
    from app.security import create_access_token
    with Session(owner) as db:
        now=db.scalar(text('SELECT clock_timestamp()'))
        auth=AuthSession(user_id=users['manager'],refresh_token_hash=uuid4().hex+uuid4().hex,client_type='web',
            device_id=uuid4().hex,ip_address='hmac:1:'+'a'*64,created_at=now,expires_at=now+timedelta(hours=1))
        db.add(auth);db.flush();token=create_access_token(users['manager'],auth.id);db.commit()
    app=FastAPI();app.include_router(router,prefix='/api')
    def database():
        with Session(api) as db:yield db
    app.dependency_overrides[get_db]=database
    original=uuid4().hex
    payload=dict(region_org_id=str(fixture['region_org_id']),publication_id=str(fixture['control_publication_id']),trace_request_id=original)
    headers={'Authorization':'Bearer '+token,'X-Request-ID':original,'Idempotency-Key':'opening-start-seal:'+original}
    with patch('app.dependencies.get_settings',return_value=get_settings().model_copy(update={'environment':'production'})),TestClient(app) as client:
        path='/api/v1/stocktakes/opening'
        read=client.get(path+'/start-command-result',params=payload,headers=headers)
        assert read.status_code==200 and read.json()['outcome']=='not_observed'
        result=client.post(path+'/seal-start-command',json=payload,headers=headers)
        assert result.status_code==200,result.text
        assert result.json()['outcome']=='sealed' and result.headers['cache-control']=='no-store'
        before=snapshot(owner)
        assert client.get(path+'/start-command-result',params=payload,headers=headers).json()==result.json()
        assert client.post(path+'/seal-start-command',json=payload,headers=headers).json()==result.json()
        # A different publication cannot borrow this exact negative fact.
        denied=client.get(path+'/start-command-result',params=payload|{'publication_id':str(uuid4())},headers=headers)
        assert denied.status_code==412 and denied.json()['detail']['code']=='control_publication_not_admissible', (denied.status_code, denied.json())
        assert snapshot(owner)==before
    print('PG16 real JWT: independent v2 GET, explicit seal, exact replay and wrong-publication refusal PASS',flush=True)


def _catalog_proof(owner,api,fixture,user):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.stocktake_models import OpeningStartCommandSeal
    migration=runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/20261107_0128_opening_start_seals.py'))
    before=snapshot(owner)
    with Session(api) as db:
        actor=load_formal_principal(db,user);original=uuid4().hex
        db.add(OpeningStartCommandSeal(id=uuid4(),actor_user_id=user,actor_person_id=actor.person_id,
            authorization_version=actor.authorization_version,region_org_id=fixture['region_org_id'],
            publication_id=fixture['control_publication_id'],request_id=original,
            request_reference=opening._request_reference(original),created_at=db.scalar(text('SELECT clock_timestamp()'))))
        db.flush()
        with pytest.raises(DBAPIError):db.commit()
        db.rollback()
    assert snapshot(owner)==before
    mutations=[
        'ALTER FUNCTION rsc_guard_opening_start_seal_0128() SET search_path=public',
        'GRANT EXECUTE ON FUNCTION rsc_guard_opening_start_seal_0128() TO star_oam_api',
        'ALTER TABLE opening_start_command_seals DISABLE TRIGGER trg_opening_start_seal_commit_0128',
        'ALTER TABLE opening_start_command_seals DROP CONSTRAINT uq_opening_start_seal_request',
    ]
    for sql in mutations:
        with owner.connect() as db:
            db.execute(text(sql))
            with Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError):migration['_verify']()
            db.rollback()
            with Operations.context(MigrationContext.configure(db)):migration['_verify']()
        assert snapshot(owner)==before
