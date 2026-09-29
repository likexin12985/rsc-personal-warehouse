"""Actual PostgreSQL read-only HTTP recovery after scoped write revocation."""
from contextlib import closing
from unittest.mock import patch

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.main import app
from app.stock_loss_schemas import StockLossReviewRequestLookupIn
from app.formal_services import stock_loss_sources as sources
from pg16_stock_loss_submit_gate import snapshot


def run(engines, *, stage, reviewer_id, write_grant_id, command, missing_command, result, service):
    owner,api=(engines[key] for key in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        permission=db.scalar(select(Permission).where(Permission.resource=='stock_operation',
            Permission.action=='read',Permission.field_code==''))
        if permission is None:
            permission=Permission(resource='stock_operation',action='read',field_code='',description='Synthetic review recovery')
            db.add(permission);db.flush()
        role_code='provincial_manager' if stage=='regional' else 'admin'
        role=db.scalar(select(Role.id).where(Role.code==role_code))
        grant=db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==permission.id))
        if grant is None:
            grant=RolePermission(role_id=role,permission_id=permission.id,effect='allow');db.add(grant);db.flush()
        assert grant.effect=='allow'
        read_grant_id=grant.id
        prior_write=db.get(RolePermission,write_grant_id).effect
        db.get(RolePermission,write_grant_id).effect='deny';db.commit()
    with Session(api) as db:
        actor=load_formal_principal(db,reviewer_id)
        def coordinates(original):
            return StockLossReviewRequestLookupIn(operation_id=original.operation_id,operator_person_id=actor.person_id,
                expected_submission_plan_hash=original.expected_submission_plan_hash,request_id=original.request_id,
                idempotency_key=original.idempotency_key,request_hash=sources._hash(service.intent(original)))
        value=coordinates(command)
        missing=coordinates(missing_command)

    def database():
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            assert db.scalar(text('SHOW transaction_read_only'))=='on'
            yield db

    def principal(db=Depends(get_db)):
        return load_formal_principal(db,reviewer_id)

    before=snapshot(owner)
    try:
        with patch.object(app,'dependency_overrides',{get_db:database,get_formal_principal:principal}):
            with closing(TestClient(app,raise_server_exceptions=False)) as client:
                path='/api/v1/stock-operations/loss-reports/'+stage+'-reviews/request-lookup'
                body=value.model_dump(mode='json')
                def post(payload,status):
                    response=client.post(path,json=payload)
                    assert response.status_code==status,(stage,response.status_code,response.text)
                    assert 'no-store' in response.headers['cache-control']
                    assert response.headers['referrer-policy']=='no-referrer'
                    assert command.idempotency_key not in response.text
                    return response.json()
                assert post(missing.model_dump(mode='json'),200)=={'lookup_status':'not_found','retry_permitted':False}
                recovered=post(body,200)
                assert recovered=={'lookup_status':'found','retry_permitted':False,'review':result.model_dump(mode='json')}
                for field in ('request_hash','expected_submission_plan_hash'):
                    assert post(body|{field:'f'*64},409)['detail']['code']=='stock_loss_review_request_conflict'
                with Session(owner) as db:db.get(RolePermission,read_grant_id).effect='deny';db.commit()
                post(body,403)
                with Session(owner) as db:db.get(RolePermission,read_grant_id).effect='allow';db.commit()
                assert post(body,200)==recovered
        assert snapshot(owner)==before
    finally:
        with Session(owner) as db:
            db.get(RolePermission,write_grant_id).effect=prior_write
            db.get(RolePermission,read_grant_id).effect='allow';db.commit()
    print('PG16 '+stage+' review recovery: API-role read-only HTTP, revoked write, exact coordinates, revoked read and unchanged stock/history PASS',flush=True)
    return dict(passed=True,apiRoleReadOnlyHttp=True,revokedWriteReadable=True,revokedReadDenied=True,
        exactRequestCoordinates=True,missingNeverPermitsRetry=True,stockAndHistoryUnchanged=True)
