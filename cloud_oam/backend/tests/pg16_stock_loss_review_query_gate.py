"""Real API-role approval queries and bound-file download with synthetic storage."""
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import AuditEvent, RolePermission, RoleAssignment
from app.main import app
from app.routers.formal_files import get_formal_file_storage_adapter
from app.stock_operation_models import StockLossFile
from pg16_stock_loss_submit_gate import snapshot
from test_formal_files_service import FakeStorage, SECRET


def run(engines, *, stage, reviewer_id, read_grant_id, command, missing_command):
    # Caller has already denied the corresponding approval write permission.
    owner,api=(engines[k] for k in ('star_oam_migrator','star_oam_api'))
    readonly=True
    def database():
        with Session(api) as db:
            if readonly:db.execute(text('SET TRANSACTION READ ONLY'))
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            yield db
    def principal(db=Depends(get_db)):return load_formal_principal(db,reviewer_id)
    storage=FakeStorage()
    settings=Settings(_env_file=None,environment='test',database_url='sqlite+pysqlite://',
        database_schema_mode='alembic',file_storage_enabled=True,file_storage_provider='aliyun_oss_v2',
        file_storage_region='cn-shanghai',file_storage_bucket='rsc-private-files',
        file_download_intent_ttl_seconds=60,file_idempotency_hmac_secret=SECRET)
    overrides={get_db:database,get_formal_principal:principal,get_settings:lambda:settings,
        get_formal_file_storage_adapter:lambda:storage}
    before=snapshot(owner)
    with patch.object(app,'dependency_overrides',overrides),closing(TestClient(app,raise_server_exceptions=False)) as client:
        prefix='/api/v1/stock-operations/loss-reports/reviews/'+stage
        def get(path,status=200,**kwargs):
            response=client.get(path,**kwargs)
            assert response.status_code==status,(stage,path,response.status_code,response.text)
            assert 'no-store' in response.headers['cache-control']
            assert response.headers['referrer-policy']=='no-referrer'
            assert command.idempotency_key not in response.text
            assert all(k not in response.text for k in ('storage_key','stock_account_id','qr_code'))
            return response.json()
        expected={str(command.operation_id),str(missing_command.operation_id)}
        page=get(prefix,params={'view':'all','limit':1})
        assert len(page['items'])==1 and page['next_after_id']==page['items'][0]['operation_id']
        following=get(prefix,params={'view':'all','limit':1,'after_id':page['next_after_id']})
        assert len(following['items'])==1 and following['next_after_id'] is None
        assert {page['items'][0]['operation_id'],following['items'][0]['operation_id']}==expected
        pending=get(prefix)
        assert [r['operation_id'] for r in pending['items']]==[str(missing_command.operation_id)]
        detail=get(prefix+'/'+str(command.operation_id))['report']
        assert detail['approval_stage']==('awaiting_headquarters' if stage=='regional' else 'approved')
        assert detail['approval_stock_effect']=='none'
        assert detail['submission_plan_hash']==command.expected_submission_plan_hash
        with Session(owner) as db:
            file_id=db.scalar(select(StockLossFile.file_id).where(StockLossFile.operation_id==command.operation_id))
        assert str(file_id) in {e['file_id'] for e in detail['evidence']}
        get(prefix+'/'+str(uuid4()),404)
        if stage=='regional':
            with Session(owner) as db:
                assignment=db.scalar(select(RoleAssignment).where(RoleAssignment.user_id==reviewer_id,
                    RoleAssignment.scope_type=='organization'))
                assignment_id=assignment.id;original_scope=assignment.scope_id
                from test_formal_access import make_organization
                other_org=make_organization(db,name='Synthetic unrelated review region',org_type='region_company')
                other_org_id=other_org.id
                assignment.scope_id=str(other_org_id);db.commit()
            with Session(api) as db:
                changed=load_formal_principal(db,reviewer_id)
                assert any(g.scope_id==str(other_org_id) for g in changed.assignments)
            try:
                assert get(prefix)['items']==[]
                get(prefix+'/'+str(command.operation_id),404)
            finally:
                with Session(owner) as db:db.get(RoleAssignment,assignment_id).scope_id=original_scope;db.commit()
        assert snapshot(owner)==before
        readonly=False
        trace=uuid4().hex;file_path='/api/v1/files/'+str(file_id)+'/download-intent'
        download=get(file_path,headers={'X-Request-ID':trace})
        assert download['file_id']==str(file_id) and download['purpose']=='stock_loss_evidence'
        assert len(storage.download_calls)==1
        with Session(owner) as db:
            audit=db.scalar(select(AuditEvent).where(AuditEvent.request_id==trace,AuditEvent.action=='file.download_intent.created'))
            assert audit is not None and audit.actor_user_id==reviewer_id
            assert audit.after_jsonb['operation_id']==str(command.operation_id)
            assert audit.after_jsonb['access']==stage+'_current_read'
        after=snapshot(owner)
        for name in before:
            if name not in ('audit_events','audit_chain_heads'):assert before[name]==after[name],name
        assert len(after['audit_events'])==len(before['audit_events'])+1
        with Session(owner) as db:db.get(RolePermission,read_grant_id).effect='deny';db.commit()
        try:
            get(prefix,403)
            get(prefix+'/'+str(command.operation_id),403)
            get(file_path,403,headers={'X-Request-ID':uuid4().hex})
            assert len(storage.download_calls)==1 and snapshot(owner)==after
        finally:
            with Session(owner) as db:db.get(RolePermission,read_grant_id).effect='allow';db.commit()
    print('PG16 '+stage+' inbox: read-only HTTP, pagination, current read without write, bound-file audit and revoked-read denial PASS',flush=True)
    return dict(passed=True,apiRoleReadOnlyHttp=True,scopedPagination=True,immutableApprovalProjection=True,
        boundEvidenceDownloadHttp=True,downloadAuditOnly=True,revokedReadStopsSigning=True,syntheticStorage=True)
