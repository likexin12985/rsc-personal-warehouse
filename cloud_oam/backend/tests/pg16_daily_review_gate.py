"""Real daily HTTP/worker/COMMIT checks; CI or a newly owned native PG16 only."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import time
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.database_security import validate_production_database_security
from app.daily_reconciliation import review_core as core, review_entry, review_service
from app.daily_reconciliation.process_entry import run_owned_job, ProcessOutcomeUnknown
from app.foundation_models import FileObject,Person,Role,RoleAssignment
from app.models import User
from app.routers import formal_daily_reconciliation, formal_files
from pg16_daily_review_fixture import prepare
from test_formal_files_service import FakeStorage
from test_formal_access import assign


def snapshot(owner, tables):
    with owner.connect() as db:
        return {name:db.scalar(text('SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb) FROM public.'+name+' t')) for name in tables}


@contextmanager
def http_client(api):
    app=FastAPI()
    app.include_router(formal_daily_reconciliation.router,prefix='/api')
    app.include_router(formal_files.router,prefix='/api')
    def database():
        with Session(api) as db:yield db
    settings=get_settings().model_copy(update=dict(database_url=api.url.render_as_string(hide_password=False),
        app_origin='https://rscwz.cn',file_storage_enabled=True,file_storage_provider='aliyun_oss_v2',
        file_storage_region='cn-shanghai',file_storage_bucket='synthetic-private-bucket',
        file_idempotency_hmac_secret='synthetic-daily-gate-file-secret-more-than-32-characters'))
    storage=FakeStorage()
    app.dependency_overrides[get_db]=database
    app.dependency_overrides[get_settings]=lambda:settings
    app.dependency_overrides[formal_files.get_formal_file_storage_adapter]=lambda:storage
    with TestClient(app) as client:yield client,storage


def _lose_committed_response(payload, expires):
    result=review_entry._review_worker(payload,expires)
    assert result['outcome']=='observed' and result['receipt']['recorded']
    time.sleep(60)  # The real fixed supervisor must kill this owned worker.


def run(engines, readers, *, check_retention=None):
    owner,api,edge=(engines[name] for name in ('star_oam_migrator','star_oam_api','edge_inbox'))
    identities,cutoffs=prepare(owner,edge,readers)
    # A headquarters employee may hold the explicit regional-manager scope.
    # Prepare that valid synthetic identity before any review activity, so a
    # later additional HQ grant tests self-review rather than invalid org data.
    with Session(owner) as db:
        db.get(Person,identities['region']['person_id']).organization_id=\
            db.get(Person,identities['hq']['person_id']).organization_id
        db.commit()
    validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    immutable=('inventory_transactions','inventory_movements','stock_balances','stock_accounts','stock_locations',
        'inventory_ledger_heads','daily_reconciliation_cutoffs')
    before=snapshot(owner,immutable);cases=[];action_reads=[]
    def passed(name):cases.append(name);print('PG16 daily '+name+' PASS',flush=True)
    passed('signed-source-publication-reviewed-mapping-and-three-real-cutoffs')
    if check_retention:
        check_retention('20261108_0129','0130 downgrade blocked: daily facts and audit must be retained')
        passed('0130-populated-cutoffs-refuse-downgrade-before-review-facts')
    with http_client(api) as (client,storage):
        prefix='/api/v1/reconciliations/daily/'
        def headers(key):return {'Cookie':'access_token='+identities[key]['token'],'Origin':'https://rscwz.cn',
            'Sec-Fetch-Site':'same-origin','X-Request-Id':uuid4().hex}
        def query(key,path):return client.get(path,headers=headers(key))
        with Session(api) as db:source=review_service.source(db,cutoffs[0])
        def actions(stage,key,expected,*,status=200,version=None):
            response=query(key,prefix+str(source.cutoff_id))
            assert response.status_code==status,response.text
            context=None
            if status==200:
                detail=response.json();actual=detail['allowed_actions']
                assert len(actual)==len(set(actual)) and set(actual)==set(expected),(stage,key,actual,expected)
                with Session(owner) as db:
                    current_version=db.get(User,identities[key]['user_id']).authorization_version
                context=dict(person_id=str(identities[key]['person_id']),authorization_version=current_version)
                assert detail['action_context']==context
                assert detail['review_version']==version,(stage,key,detail['review_version'],version)
                assert 'no-store' in response.headers['cache-control']
            else:
                assert 'allowed_actions' not in response.json() and 'action_context' not in response.json()
            action_reads.append(dict(stage=stage,actor=key,status=status,actions=list(expected),version=version,context=context))
            print('PG16 daily action capabilities '+stage+' '+key+' PASS',flush=True)
        differences=[i+1 for i,row in enumerate(source.items) if row.status=='difference']
        assert len(differences)==2
        actions('not-open','region',['open'],version=0)
        actions('not-open','hq',['open'],version=0)
        actions('cross-region','other',[],status=404)
        def command(operation,version=0,source=source,**fields):
            return core.REQUEST.validate_python(dict(operation=operation,cutoff_id=source.cutoff_id,
                expected_cutoff_sha256=source.cutoff_sha256,expected_comparison_sha256=source.comparison_sha256,
                expected_version=version,idempotency_key=uuid4().hex,request_id=uuid4().hex,**fields))
        def post(command,key='region',expected=200):
            value=command.model_dump(mode='json');h=headers(key)
            h.update({'Idempotency-Key':value['idempotency_key'],'X-Request-Id':value['request_id']})
            response=client.post(prefix+value['cutoff_id']+'/commands',headers=h,
                json=dict(expected_authorization_version=1,command=value))
            assert response.status_code==expected,response.text
            assert 'no-store' in response.headers['cache-control']
            return response.json()
        def reference(command,key='region'):
            return dict(cutoff_id=str(command.cutoff_id),actor_person_id=str(identities[key]['person_id']),
                original_authorization_version=1,original_review_version=command.expected_version,
                operation=command.operation,trace_request_id=command.request_id)
        def recover(command,key='region',seal=False):
            body=dict(expected_authorization_version=1,reference=reference(command,key))
            if seal:body['confirmation']='permanently_prevent_original_daily_review_request'
            response=client.post(prefix+str(command.cutoff_id)+('/request-seal' if seal else '/request-recovery'),json=body,headers=headers(key))
            assert response.status_code==200,response.text
            result=response.json();assert result['reference']==body['reference'] and not result['automatic_retry_allowed']
            return result
        def upload():
            h=headers('region');h['Idempotency-Key']=uuid4().hex
            response=client.post('/api/v1/files/upload-intents',headers=h,json=dict(purpose='daily_reconciliation_evidence',
                original_filename='synthetic-daily.png',size_bytes=32,mime_type='image/png',sha256='b'*64))
            assert response.status_code==201,response.text
            file_id=UUID(response.json()['file_id'])
            assert query('region','/api/v1/files/'+str(file_id)+'/download-intent').status_code==404
            with Session(api) as db:storage.materialize(db.get(FileObject,file_id))
            response=client.post('/api/v1/files/'+str(file_id)+'/complete',headers=headers('region'))
            assert response.status_code==200,response.text
            assert query('hq','/api/v1/files/'+str(file_id)+'/download-intent').status_code==403
            return file_id
        opening=command('open');opened=post(opening)['receipt'];assert opened['version']==1
        assert post(opening)['receipt']==opened and recover(opening)['receipt']==opened
        actions('just-opened','region',['explain'],version=1)
        actions('just-opened','hq',[],version=1)
        passed('current-jwt-api-role-open-and-exact-idempotent-recovery')
        if check_retention:
            check_retention('20261110_0131','0132 populated downgrade refused: retain daily review history')
            passed('0132-populated-review-refuses-downgrade-before-dedicated-files')
        file_id=upload();passed('real-api-upload-intent-completion-and-unbound-file-isolation')
        if check_retention:
            check_retention('20261111_0132','0133 daily evidence must be retained')
            passed('0133-dedicated-evidence-refuses-downgrade-before-request-seals')
        def explain(version,ordinals,item_version=0):
            return command('explain',version,items=[dict(ordinal=n,expected_item_version=item_version,
                explanation='合成验收：逐项核对原始截止与证据',evidence_file_id=file_id,evidence_sha256='b'*64) for n in ordinals])
        partial=explain(1,differences[:1]);post(partial)
        actions('partly-explained','region',['explain'],version=2)
        actions('partly-explained','hq',['request_changes'],version=2)
        assert 'unexplained_differences' in post(command('approve',2,comment='总部逐项审核未解释差异'),'hq',409)['detail']['code']
        post(explain(2,differences[1:]))
        actions('fully-explained','region',['explain'],version=3)
        actions('fully-explained','hq',['approve','request_changes'],version=3)
        # A real national grant must not turn an explanation author into an
        # independent reviewer. This exercises PG jsonb author membership,
        # instead of passing only because the normal regional role cannot review.
        with Session(owner) as db:
            now=db.scalar(text('SELECT clock_timestamp()'))
            grant=assign(db,db.get(User,identities['region']['user_id']),
                db.scalar(select(Role).where(Role.code=='admin')),scope_type='national',scope_id='*',
                valid_from=now-timedelta(seconds=1),valid_to=now+timedelta(hours=1))
            extra_grant_id=grant.id;db.commit()
        try:
            actions('explanation-author-with-hq-grant','region',['explain'],version=3)
        finally:
            with Session(owner) as db:
                grant=db.get(RoleAssignment,extra_grant_id)
                grant.status='revoked';grant.revoked_at=db.scalar(text('SELECT clock_timestamp()'))
                grant.revoked_by=identities['region']['user_id'];db.commit()
        post(command('request_changes',3,comment='总部要求指定项补充证据',ordinals=differences[:1]),'hq')
        actions('changes-requested','region',['explain'],version=4)
        actions('changes-requested','hq',['request_changes'],version=4)
        post(explain(4,differences[:1],2))
        actions('re-explained','hq',['approve','request_changes'],version=5)
        approved=post(command('approve',5,comment='总部独立审核证据完整'),'hq')['receipt']
        assert approved['review_status']=='approved' and approved['version']==6
        actions('approved','region',[],version=6)
        actions('approved','hq',[],version=6)
        assert recover(opening)['receipt']==opened
        detail=query('region',prefix+str(source.cutoff_id));assert detail.status_code==200
        assert detail.json()['review_status']=='approved' and detail.json()['comparison_status']=='differences'
        assert query('other',prefix+str(source.cutoff_id)).status_code==404
        assert query('hq','/api/v1/files/'+str(file_id)+'/download-intent').status_code==200
        assert query('other','/api/v1/files/'+str(file_id)+'/download-intent').status_code==403
        history=query('hq',prefix+str(source.cutoff_id)+'/history').json()['items']
        assert [row['operation'] for row in history]==['open','explain','explain','request_changes','explain','approve']
        passed('partial-explanation-targeted-return-independent-approval-and-private-download')
        with Session(api) as db:second=review_service.source(db,cutoffs[1]);third=review_service.source(db,cutoffs[2])
        sealed=command('open',source=second)
        assert recover(sealed)['outcome']=='not_observed'
        seal=recover(sealed,seal=True);assert seal['outcome']=='sealed'
        assert recover(sealed)==seal and recover(sealed,seal=True)==seal
        assert 'request_sealed' in post(sealed,expected=409)['detail']['code']
        passed('not-observed-is-not-success-permanent-seal-blocks-late-command')
        if check_retention:
            check_retention('20261112_0133','0134 populated downgrade refused: original request seals must be retained')
            passed('0134-original-request-seal-refuses-downgrade')
        lost=command('open',source=third)
        payload=dict(database_url=api.url.render_as_string(hide_password=False),command=lost.model_dump(mode='json'),
            access_token=identities['region']['token'],expected_authorization_version=1,recover=False)
        with pytest.raises(ProcessOutcomeUnknown):run_owned_job(_lose_committed_response,payload,maximum_seconds=8)
        recovered=recover(lost);assert recovered['outcome']=='found' and recovered['receipt']['version']==1
        assert post(lost)['receipt']==recovered['receipt']
        passed('commit-response-loss-recovers-once-through-fixed-process-supervisor')
        with owner.begin() as db:
            db.execute(text("UPDATE role_assignments SET valid_to=clock_timestamp()-interval '1 second' WHERE id=:id"),{'id':identities['region']['grant_id']})
        try:
            actions('current-grant-revoked','region',[],status=403)
            response=client.post(prefix+str(opening.cutoff_id)+'/request-recovery',json=dict(expected_authorization_version=1,reference=reference(opening)),headers=headers('region'))
            assert response.status_code==403,response.text
            assert query('region','/api/v1/files/'+str(file_id)+'/download-intent').status_code==403
        finally:
            with owner.begin() as db:db.execute(text("UPDATE role_assignments SET valid_to=clock_timestamp()+interval '1 hour' WHERE id=:id"),{'id':identities['region']['grant_id']})
        passed('current-grant-revocation-blocks-recovery-and-download')
    assert snapshot(owner,immutable)==before
    with owner.connect() as db:
        versions=db.execute(text('SELECT cutoff_id,version FROM daily_review_events WHERE cutoff_id=ANY(:ids) ORDER BY cutoff_id,version'),{'ids':cutoffs}).all()
        assert sum(row[0]==cutoffs[0] for row in versions)==6 and sum(row[0]==cutoffs[1] for row in versions)==0 and sum(row[0]==cutoffs[2] for row in versions)==1
        assert db.scalar(text('SELECT count(*) FROM daily_review_request_seals WHERE cutoff_id=ANY(:ids)'),{'ids':cutoffs})==1
    passed('seven-events-one-seal-and-unchanged-stock-and-cutoff-facts')
    return dict(status='passed',cases=cases,actionCapabilityReads=action_reads,
        actionCapabilityReadCount=len(action_reads),cutoffIds=[str(value) for value in cutoffs],events=7,seals=1,realOss=False,
        productionStartup=False,stockWritten=False,unchangedFactTables=list(immutable),
        immutableFactsSha256={name:hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()
            for name,rows in before.items()})
