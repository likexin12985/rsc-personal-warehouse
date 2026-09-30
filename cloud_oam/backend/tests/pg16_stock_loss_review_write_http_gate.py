"""Actual API-role review/seal HTTP writes, unknown COMMIT and exact recovery."""
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import RolePermission
from app.main import app
from app.stock_loss_review_seal_schemas import StockLossRegionalReviewSealIn, StockLossHeadquartersReviewSealIn
from app.stock_loss_schemas import StockLossRegionalReviewOut, StockLossHeadquartersReviewOut
from app.formal_services import stock_loss_sources as sources
from pg16_stock_loss_submit_gate import snapshot


def run(engines, *, stage, reviewer_id, write_grant_id, command, service):
    owner,api=(engines[k] for k in ('star_oam_migrator','star_oam_api'))
    fault={'commit':None}
    class RequestSession(Session):
        def commit(self):
            if fault['commit']!='before':super().commit()
            if fault['commit'] is not None:
                raise OperationalError('PRIVATE-SYNTHETIC-COMMIT',{},Exception('PRIVATE-LOST-RESPONSE'))
    def database():
        with RequestSession(api) as db:
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            yield db
    def principal(db=Depends(get_db)):return load_formal_principal(db,reviewer_id)
    with Session(api) as db:actor=load_formal_principal(db,reviewer_id)
    schema=StockLossRegionalReviewSealIn if stage=='regional' else StockLossHeadquartersReviewSealIn
    output=StockLossRegionalReviewOut if stage=='regional' else StockLossHeadquartersReviewOut
    value=schema(operator_person_id=actor.person_id,original=command,
        request_hash=sources._hash(service.intent(command))).model_dump(mode='json')
    def coordinates(body):
        original=body['original']
        return {k:original[k] for k in ('operation_id','expected_submission_plan_hash','request_id','idempotency_key')}|{
            'operator_person_id':body['operator_person_id'],'request_hash':body['request_hash']}
    def all_facts():
        answer=snapshot(owner)
        with owner.connect() as db:
            for table in ('stock_loss_review_request_seals','stock_loss_regional_reviews','stock_loss_headquarters_reviews','stock_loss_headquarters_decisions'):
                answer[table]=tuple(db.execute(text('SELECT * FROM '+table+' ORDER BY id')))
        return answer
    def stock():
        answer=all_facts()
        return {k:answer[k] for k in ('stock_accounts','stock_balances','inventory_transactions','inventory_movements',
            'inventory_movement_serials','serial_current_positions','inventory_serials','inventory_ledger_heads')}
    path='/api/v1/stock-operations/loss-reports/'+stage+'-reviews'
    with patch.object(app,'dependency_overrides',{get_db:database,get_formal_principal:principal}):
        with closing(TestClient(app,raise_server_exceptions=False)) as client:
            def post(suffix,body,status=200):
                coords=body.get('original',body)
                response=client.post(path+suffix,json=body,headers={
                    'X-Request-ID':coords['request_id'],'Idempotency-Key':coords['idempotency_key']})
                assert response.status_code==status,(stage,suffix,response.status_code,response.text)
                assert 'no-store' in response.headers['cache-control']
                assert response.headers['referrer-policy']=='no-referrer'
                assert 'PRIVATE-' not in response.text and coords['idempotency_key'] not in response.text
                return response.json()
            before=all_facts();stock_before=stock()
            fault['commit']='before';post('',value,503);fault['commit']=None
            assert all_facts()==before
            assert post('/request-lookup',coordinates(value))=={'lookup_status':'not_found','retry_permitted':False}
            abandoned=value|{'original':value['original']|{'request_id':uuid4().hex,'idempotency_key':uuid4().hex}}
            fault['commit']='before';post('/request-seal',abandoned,503);fault['commit']=None
            assert all_facts()==before
            fault['commit']='after';post('/request-seal',abandoned,503);fault['commit']=None
            sealed=post('/request-lookup',coordinates(abandoned))
            assert sealed['lookup_status']=='sealed' and sealed['retry_permitted'] is False
            after_seal=all_facts()
            assert stock()==stock_before
            assert post('/request-seal',abandoned)==sealed and all_facts()==after_seal
            post('',abandoned,409)
            assert all_facts()==after_seal
            fault['commit']='after';post('',value,503);fault['commit']=None
            found=post('/request-lookup',coordinates(value))
            assert found['lookup_status']=='found' and found['retry_permitted'] is False
            result=output.model_validate(found['review'])
            assert result.stock_effect=='none' and stock()==stock_before
            if stage=='headquarters':assert result.disposition_stage=='pending'
            committed=all_facts()
            assert post('',value)==found['review']
            assert post('/request-seal',value)==found
            assert post('/request-lookup',coordinates(abandoned))==sealed
            assert all_facts()==committed
            with Session(owner) as db:
                prior=db.get(RolePermission,write_grant_id).effect
                db.get(RolePermission,write_grant_id).effect='deny';db.commit()
            try:
                post('',value,403);post('/request-seal',abandoned,403)
                assert post('/request-lookup',coordinates(value))==found
                assert post('/request-lookup',coordinates(abandoned))==sealed
                assert all_facts()==committed
            finally:
                with Session(owner) as db:db.get(RolePermission,write_grant_id).effect=prior;db.commit()
    print('PG16 '+stage+' HTTP: actual commit, unknown result recovered, pre-commit rollback, independent seal, late review and revoked writes PASS',flush=True)
    return result,dict(passed=True,apiRoleHttp=True,preCommitRollback=True,approvalCommitResponseLostRecovered=True,
        sealCommitResponseLostRecovered=True,oldSealReadableAfterNewReview=True,lateApprovalRejected=True,
        exactReplayNoNewFacts=True,revokedWriteStillReadable=True,stockUnchanged=True)
