"""Correction HTTP over actual API-role transactions; synthetic login identity only."""
from uuid import uuid4
from unittest.mock import patch
from fastapi import Depends,Request
from fastapi.testclient import TestClient
from sqlalchemy import event,select,text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import Permission,Role,RolePermission
from app.main import app
from pg16_loss_multigeneration_fixture import exercise as prepare_original


def exercise(context):
    original=prepare_original(context)
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    statuses=[];commits=[];readonly=[];source_reads=[];lost_reply=False
    def stock():
        with owner.connect() as db:
            return {table:db.scalars(text('SELECT to_jsonb(t)::text FROM public.'+table+' t ORDER BY to_jsonb(t)::text')).all()
                for table in ('inventory_transactions','inventory_movements','inventory_movement_serials','stock_balances','serial_current_positions')}
    def facts():
        with owner.connect() as db:
            return {table:db.scalars(text('SELECT to_jsonb(t)::text FROM public.'+table+' t ORDER BY to_jsonb(t)::text')).all()
                for table in ('stock_loss_disposition_reversals','stock_loss_correction_decisions','stock_loss_correction_executions',
                    'stock_loss_inverse_request_seals','stock_loss_correction_approval_seals','stock_loss_correction_execution_seals',
                    'stock_loss_request_key_bindings','audit_events','outbox_events','notification_events','state_transition_events')}
    class HttpSession(Session):
        def commit(self):
            nonlocal lost_reply
            assert not self.info.get('query_only')
            super().commit();commits.append('committed')
            if lost_reply:
                lost_reply=False
                raise OperationalError('synthetic committed reply loss',{},Exception('unavailable'))
    def request_db(request:Request):
        with HttpSession(api) as db:
            is_source='/corrections/sources/' in request.url.path
            is_lookup=request.url.path.endswith('/request-lookup') or is_source
            is_preview=request.url.path.endswith('/preview')
            db.info['query_only']=is_lookup or is_preview
            if is_lookup:
                db.execute(text('SET TRANSACTION READ ONLY'))
                assert db.scalar(text('SHOW transaction_read_only'))=='on';readonly.append(request.url.path)
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            connection=db.connection()
            def select_only(conn,cursor,statement,parameters,execution_context,executemany):
                assert statement.lstrip().upper().startswith('SELECT '),'preview emitted a write'
            if is_preview:event.listen(connection,'before_cursor_execute',select_only)
            try:
                yield db
                if is_preview:assert not db.new and not db.dirty and not db.deleted
            finally:
                if is_preview:event.remove(connection,'before_cursor_execute',select_only)
    def actor(db:Session=Depends(get_db)):return load_formal_principal(db,context['admin_id'])
    with owner.connect() as db:
        grants={action:db.scalar(select(RolePermission.id).join(Permission,Permission.id==RolePermission.permission_id)
            .join(Role,Role.id==RolePermission.role_id).where(Role.code=='admin',Permission.resource=='stock_operation',
            Permission.action==action,Permission.field_code=='')) for action in ('read','reverse_loss','approve_loss_correction','correct_loss')}
        assert all(grants.values())
    def permission(action,effect):
        with owner.begin() as db:db.execute(text('UPDATE role_permissions SET effect=:effect WHERE id=:id'),dict(effect=effect,id=grants[action]))
    base='/api/v1/stock-operations/loss-reports/corrections/'
    with patch.object(app,'dependency_overrides',{get_db:request_db,get_formal_principal:actor}):
        client=TestClient(app,raise_server_exceptions=False)
        try:
            def call(flow,suffix,command,status):
                response=client.post(base+flow+suffix,json=command)
                assert response.status_code==status,(flow,suffix,response.status_code,response.text)
                assert 'private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control']
                assert response.headers['referrer-policy']=='no-referrer'
                assert command.get('idempotency_key','PRIVATE-absent-key') not in response.text
                assert 'command_jsonb' not in response.text and 'key_hash' not in response.text
                statuses.append(status);return response.json()
            def read_source(expected_state=None, status=200):
                before_stock=stock();before_facts=facts();before_commits=len(commits)
                response=client.get(base+'sources/'+str(original['binding']['root_disposition_id']))
                assert response.status_code==status,(response.status_code,response.text)
                assert 'no-store' in response.headers['cache-control'] and 'private' in response.headers['cache-control']
                assert response.headers['referrer-policy']=='no-referrer'
                assert all(secret not in response.text for secret in ('idempotency_key','command_jsonb','plan_jsonb','key_hash','intent_hash'))
                assert stock()==before_stock and facts()==before_facts and len(commits)==before_commits
                value=response.json()
                if status==200:
                    assert value['chain_state']==expected_state
                    assert value['stock_effect']=='none' and value['write_authorization_provided'] is False
                    assert value['root_disposition_id']==str(original['binding']['root_disposition_id'])
                else:
                    assert 'history' not in value and 'inverse_preview_reference' not in value
                source_reads.append(dict(status=status,state=expected_state));return value
            def run_command(flow,body,action):
                nonlocal lost_reply
                before_stock=stock()
                if flow!='approvals':
                    prepared=call(flow,'/preview',body,200)
                    assert prepared['planning_status']=='preview_only' and prepared['stock_effect']=='none'
                    assert stock()==before_stock
                    body=dict(body,expected_plan_hash=prepared['plan_hash'])
                sealed_command=dict(body,request_id=uuid4().hex,idempotency_key=uuid4().hex)
                sealed=call(flow,'/request-seal',sealed_command,200)
                assert sealed['request_state']=='sealed' and sealed['retry_allowed'] is False
                assert stock()==before_stock
                sealed_facts=facts()
                assert call(flow,'/request-lookup',sealed_command,200)==sealed
                call(flow,'',sealed_command,409)
                assert stock()==before_stock and facts()==sealed_facts
                command=dict(body,request_id=uuid4().hex,idempotency_key=uuid4().hex)
                missing_facts=facts();missing=call(flow,'/request-lookup',command,200)
                assert missing['request_state']=='not_found' and missing['retry_allowed'] is False and facts()==missing_facts
                lost_reply=True
                before_commits=len(commits)
                unknown=call(flow,'',command,503)
                assert unknown['detail']['code']=='loss_correction_outcome_unconfirmed'
                assert len(commits)==before_commits+1
                settled=facts()
                found=call(flow,'/request-lookup',command,200)
                assert found['request_state']=='found' and found['retry_allowed'] is False and facts()==settled
                assert (stock()==before_stock) is (flow=='approvals')
                permission(action,'deny')
                try:
                    read_source({'inverses':'awaiting_approval','approvals':'awaiting_execution','executions':'active_execution'}[flow])
                    assert call(flow,'/request-lookup',command,200)==found
                    call(flow,'',command,403)
                    call(flow,'/request-seal',command,403)
                    assert facts()==settled
                finally:permission(action,'allow')
                permission('read','deny')
                try:
                    call(flow,'/request-lookup',command,403)
                    read_source(status=403)
                finally:permission('read','allow')
                assert call(flow,'/request-lookup',sealed_command,200)==sealed
                assert facts()==settled
                print(flow+' HTTP preview/seal, real COMMIT reply loss, READ ONLY recovery and current ACL PASS',flush=True)
                return found['result']
            first=read_source('active_execution')
            assert first['inverse_preview_reference']['reversed_correction_id'] is None
            inverse=run_command('inverses',dict(first['inverse_preview_reference'],reason='Explicit inverse from proven sources'),'reverse_loss')
            pending=read_source('awaiting_approval')
            assert pending['approval_reference']['reversal_id']==inverse['reversal_id']
            assert pending['inverse_preview_reference'] is None and pending['approval_choices']==[]
            approved=run_command('approvals',dict(pending['approval_reference'],disposition='restore_available',reason='Independent approval from proven inverse'),'approve_loss_correction')
            approval_sources=read_source('awaiting_execution')
            choices=[row for row in approval_sources['approval_choices'] if row['correction_decision_id']==approved['correction_decision_id']]
            assert len(choices)==1 and choices[0]['execution_mode']=='preview_required'
            corrected=run_command('executions',dict(choices[0]['preview_reference'],reason='Explicit correction from proven selected approval'),'correct_loss')
            final=read_source('active_execution')
            assert final['inverse_preview_reference']['reversed_correction_id']==corrected['correction_execution_id']
            assert final['inverse_preview_reference']['expected_execution_request_hash']==corrected['request_hash']
            assert final['approval_choices']==[] and final['approval_reference'] is None
            assert {row['fact_id'] for row in final['history']}=={str(original['binding']['root_disposition_id']),inverse['reversal_id'],approved['correction_decision_id'],corrected['correction_execution_id']}
            assert approved['stock_effect']=='none' and corrected['status']=='posted'
        finally:
            client.close()
    assert statuses.count(503)==3 and len(readonly)>=15
    return dict(passed=True,tracking=context['tracking'],httpStatusCodes=statuses,committedTransactions=len(commits),
        realPostCommitReplyLossCases=3,readonlyLookups=len(readonly),exactDbRequestBindings=True,
        permanentSealsAndLateWriteDenied=True,currentWriteAndReadRightsIndependent=True,
        approvalStockNeutral=True,sourceReadChecks=source_reads,sourceGetReadOnly=True,clientCommandsFromProvenReferences=True,apiDatabaseRole='star_oam_api',realLoginIdentity=False,productionAcceptance=False)
