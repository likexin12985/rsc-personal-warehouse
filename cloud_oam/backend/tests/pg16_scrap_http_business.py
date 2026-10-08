"""Actual mounted HTTP, synthetic sessions, real API-role PostgreSQL commits."""
import json
from decimal import Decimal
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID,uuid4

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text,select,event
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from app import dependencies
from app.database import get_db
from app.database_security import validate_production_database_security
from app.main import app
from app.models import User
from app.formal_access import load_formal_principal
from app.foundation_models import Role,Permission,RolePermission,Organization,Person
from test_formal_access import authenticated_request,make_organization,make_user,assign
from test_stock_scrap_plan import _upload
from pg16_stock_loss_sources_gate import run as opening
from pg16_loss_multigeneration_fixture import exercise as approved_loss
from pg16_stock_operation_permission_policy import assert_fresh_defaults
from pg16_scrap_business_recovery import inventory
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from pg16_scrap_business_generations import retained

BASE='/api/v1/stock-operations/loss-reports/scraps/'
FLOWS=dict(original=('originals','dispose_loss','admin'),
    correction=('corrections','correct_loss','admin'),apply=('recovery/applications','apply_scrap_recovery','technician'),
    regional=('recovery/regional-reviews','review_scrap_recovery_regional','provincial_manager'),
    headquarters=('recovery/headquarters-reviews','review_scrap_recovery_headquarters','admin'),
    execute=('recovery/executions','execute_scrap_recovery','admin'))


def function_stats(owner):
    with owner.connect() as db:
        if db.scalar(text('SHOW track_functions'))=='none':return None
        return {r['function']:dict(r) for r in db.execute(text("""SELECT schemaname||'.'||funcname||'('||pg_catalog.pg_get_function_identity_arguments(funcid)||')' AS function,
            calls,total_time,self_time FROM pg_catalog.pg_stat_user_functions WHERE schemaname='public'""")).mappings()}


def function_delta(before,after):
    if before is None or after is None:return dict(enabled=False)
    rows=[]
    for name,row in after.items():
        old=before.get(name,{})
        delta={key:round(row[key]-old.get(key,0),6) for key in ('calls','total_time','self_time')}
        if delta['calls']>0:rows.append(dict(function=name,**delta))
    return dict(enabled=True,scope='fixture-approved to final-readback; milliseconds; inclusive and self timings',
        functions=sorted(rows,key=lambda row:row['self_time'],reverse=True),productionPerformanceAcceptance=False)


def exercise(context):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    source=approved_loss(context,approved_disposition='scrap',after_approval=lambda value:value)
    function_before=function_stats(owner)
    with Session(owner) as db:
        regional=db.execute(text('''SELECT regional.actor_user_id FROM stock_loss_headquarters_decisions decision
            JOIN stock_loss_headquarters_reviews headquarters ON headquarters.id=decision.review_id
            JOIN stock_loss_regional_reviews regional ON regional.id=headquarters.regional_review_id
            WHERE decision.id=:id'''),dict(id=source['headquarters_decision_id'])).scalar_one()
        operation_id=db.scalar(text('SELECT operation_id FROM stock_loss_headquarters_reviews WHERE id=(SELECT review_id FROM stock_loss_headquarters_decisions WHERE id=:id)'),dict(id=source['headquarters_decision_id']))
        read_grant=db.scalar(select(RolePermission.id).join(Permission).join(Role,Role.id==RolePermission.role_id)
            .where(Role.code=='admin',Permission.resource=='stock_operation',Permission.action=='read',Permission.field_code==''))
        assert read_grant is not None
        actors=dict(original=context['admin_id'],correction=context['admin_id'],apply=context['engineer_id'],regional=regional,
                    headquarters=context['admin_id'],execute=context['admin_id'])
        applicant=db.get(Person,db.get(User,context['engineer_id']).person_id)
        home=db.get(Organization,applicant.organization_id)
        other=make_organization(db,name='Synthetic separate recovery region',parent=db.get(Organization,home.parent_id) if home.parent_id else None,org_type='region_company')
        for key,code,org in [('foreign_regional','provincial_manager',other),('foreign_apply','technician',other),('other_apply','technician',home)]:
            user,person=make_user(db,org,name='Synthetic '+key)
            db_role=db.scalar(select(Role).where(Role.code==code))
            assign(db,user,db_role,scope_type='organization' if code=='provincial_manager' else 'person',
                scope_id=str(org.id if code=='provincial_manager' else person.id))
            actors[key]=user.id
        sessions={}
        for actor_id in set(actors.values()):
            user=db.get(User,actor_id)
            _,token=authenticated_request(db,user)
            sessions[actor_id]=(token,str(user.person_id))
        db.commit()
        grants={kind:db.scalar(select(RolePermission.id).join(Permission).join(Role,Role.id==RolePermission.role_id)
            .where(Role.code==role,Permission.resource=='stock_operation',Permission.action==action,Permission.field_code==''))
            for kind,(_,action,role) in FLOWS.items()}
        assert all(grants.values())
    def evidence(actor_id):
        # Each business event owns a distinct evidence file. Reusing an earlier
        # scrap/recovery attachment violates the formal one-event binding.
        with Session(api) as db:
            file=_upload(db,load_formal_principal(db,actor_id));db.commit()
            return str(file.id)
    fault={'commit':None};commits=[];attempts=[];readonly=[];cases=[];lookups=[];timings=[];source_reads=[];correction_reads=[];recovery_source_reads=[]
    def snapshot():
        names=('inventory_transactions','inventory_movements','inventory_movement_serials','stock_balances',
            'inventory_serials','serial_current_positions','stock_operation_orders','stock_loss_dispositions',
            'stock_loss_disposition_reversals','stock_loss_correction_decisions',
            'stock_loss_correction_executions','stock_scrap_lines','stock_scrap_serials','stock_scrap_files',
            'stock_scrap_recovery_requests','stock_scrap_recovery_files','stock_scrap_recovery_regional_reviews',
            'stock_scrap_recovery_headquarters_reviews','stock_scrap_recovery_executions',
            'stock_scrap_request_key_bindings','stock_scrap_request_seals','stock_loss_request_key_bindings',
            'stock_loss_inverse_request_seals','stock_loss_correction_approval_seals','stock_loss_correction_execution_seals',
            'audit_events','audit_chain_heads','outbox_events','notification_events','state_transition_events')
        with owner.connect() as db:
            return {name:db.scalars(text('SELECT to_jsonb(t)::text FROM public.'+name+' t ORDER BY to_jsonb(t)::text')).all() for name in names}
    class HttpSession(Session):
        def commit(self):
            assert not self.info.get('query_only')
            attempts.append(fault['commit'])
            if fault['commit']!='before':
                started=perf_counter()
                try:
                    super().commit();commits.append('committed')
                finally:
                    timings.append(dict(phase='commit',path=self.info['path'],injection=fault['commit'],seconds=round(perf_counter()-started,6)))
                    print('HTTP commit timing '+str(timings[-1]),flush=True)
            if fault['commit']:
                raise OperationalError('SYNTHETIC-PRIVATE-COMMIT',{},Exception('response unavailable'))
    def request_db(request:Request):
        with HttpSession(api) as db:
            lookup=request.url.path.endswith('/request-lookup') or request.method=='GET'
            db.info['path']=request.url.path
            preview=request.url.path.endswith('/preview')
            db.info['query_only']=lookup or preview
            if lookup:
                db.execute(text('SET TRANSACTION READ ONLY'))
                assert db.scalar(text('SHOW transaction_read_only'))=='on';readonly.append(request.url.path)
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            connection=db.connection()
            def selects(conn,cursor,statement,parameters,ctx,many):
                assert statement.lstrip().upper().startswith('SELECT '),'preview issued a non-query'
            if preview:event.listen(connection,'before_cursor_execute',selects)
            try:yield db
            finally:
                if preview:event.remove(connection,'before_cursor_execute',selects)
    with patch.object(app,'dependency_overrides',{get_db:request_db}),patch.object(dependencies,'get_settings',
            lambda:SimpleNamespace(environment='production',identity_hash_version=1)):
        client=TestClient(app,raise_server_exceptions=False)
        try:
            def call(kind,suffix,body,status=200,*,path=None):
                token,person=sessions[actors[kind]]
                original=body.get('original',body)
                headers={'Authorization':'Bearer '+token}
                if 'request_id' in original:
                    headers.update({'X-Request-ID':original['request_id'],'Idempotency-Key':original['idempotency_key']})
                started=perf_counter()
                reply=client.post((path or BASE+FLOWS[kind][0])+suffix,json=body,headers=headers)
                timings.append(dict(phase='http',path=(path or BASE+FLOWS[kind][0])+suffix,status=reply.status_code,
                    injection=fault['commit'],seconds=round(perf_counter()-started,6)))
                assert reply.status_code==status,(kind,suffix,reply.status_code,reply.text)
                assert 'no-store' in reply.headers['cache-control'] and reply.headers['referrer-policy']=='no-referrer'
                assert token not in reply.text and original.get('idempotency_key','PRIVATE-absent') not in reply.text
                assert 'command_jsonb' not in reply.text and 'SYNTHETIC-PRIVATE' not in reply.text
                return reply.json()
            original_posting=None
            def read_source(label,*,role='original',status=200,anonymous=False):
                before=snapshot();n=len(commits)
                token,person=sessions[actors[role]]
                path='/api/v1/stock-operations/loss-reports/execution-sources/'+str(operation_id)
                started=perf_counter()
                response=client.get(path,headers={} if anonymous else {'Authorization':'Bearer '+token})
                timings.append(dict(phase='source_get',label=label,status=response.status_code,seconds=round(perf_counter()-started,6)))
                assert response.status_code==status,(label,response.status_code,response.text)
                assert 'no-store' in response.headers['cache-control'] and response.headers['referrer-policy']=='no-referrer'
                assert token not in response.text
                assert not any(key in response.text for key in ('command_jsonb','idempotency_key','key_hash'))
                assert snapshot()==before and len(commits)==n
                result=response.json()
                if status==200:
                    assert result['person_id']==person and result['report']['operation_id']==str(operation_id)
                    assert result['report']['approval_stage']=='approved' and result['report']['approval_stock_effect']=='none'
                    rows=[row for row in result['decisions'] if row['headquarters_decision_id']==str(source['headquarters_decision_id'])]
                    assert len(rows)==1 and rows[0]['disposition']=='scrap'
                    assert rows[0]['preview_reference']=={k:str(v) for k,v in source.items()}
                    posting=rows[0]['original_posting']
                    if original_posting is None:assert posting is None
                    else:
                        assert posting['result_scope']=='original_posting' and posting['disposition_id']==original_posting['root_disposition_id']
                        assert posting['posting_transaction_id']==original_posting['posting_transaction_id'] and posting['quantity']==original_posting['quantity']
                        assert posting['return_operation_id'] is None
                else:
                    assert 'report' not in result and 'decisions' not in result
                source_reads.append(dict(label=label,status=status,readOnly=True,allFactsUnchanged=True))
                print('source GET '+label+' PASS',flush=True)
                return result
            read_source('before-original')
            read_source('unauthenticated',anonymous=True,status=401)
            read_source('engineer-denied',role='apply',status=403)
            read_source('regional-denied',role='regional',status=403)
            with owner.begin() as db:
                db.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),dict(id=read_grant))
            try:read_source('read-revoked',status=403)
            finally:
                with owner.begin() as db:
                    db.execute(text("UPDATE role_permissions SET effect='allow' WHERE id=:id"),dict(id=read_grant))
            def run(kind,body):
                before_stock=inventory(owner)
                if kind in ('original','correction','execute'):
                    before_preview=snapshot()
                    preview=call(kind,'/preview',body)
                    assert preview['planning_status']=='preview_only' and preview['stock_effect']=='none'
                    assert snapshot()==before_preview and inventory(owner)==before_stock
                    body=dict(body,expected_plan_hash=preview['plan_hash'])
                    if kind=='execute':body=dict(body,action='execute_scrap_recovery')
                command=dict(body,request_id=uuid4().hex,idempotency_key=uuid4().hex)
                person=sessions[actors[kind]][1]
                wrapped=dict(operator_person_id=person,original=command)
                before=snapshot();n=len(commits)
                missing=call(kind,'/request-lookup',wrapped)
                assert missing['request_state']=='not_found' and missing['retry_allowed'] is False
                assert snapshot()==before and len(commits)==n
                prior_attempts=len(attempts)
                fault['commit']='before';call(kind,'',command,503);fault['commit']=None
                assert len(attempts)==prior_attempts+1 and attempts[-1]=='before',(kind,'write did not reach injected commit failure')
                assert snapshot()==before and len(commits)==n
                abandoned=dict(command,request_id=uuid4().hex,idempotency_key=uuid4().hex)
                closure=dict(operator_person_id=person,original=abandoned)
                sealed=call(kind,'/request-seal',closure)
                assert sealed['request_state']=='sealed' and sealed['retry_allowed'] is False
                closed=snapshot();call(kind,'',abandoned,409)
                assert snapshot()==closed and inventory(owner)==before_stock
                fault['commit']='after';unknown=call(kind,'',command,503);fault['commit']=None
                assert unknown['detail']['code']=='stock_scrap_outcome_unconfirmed'
                assert len(commits)==n+2,(kind,'lost reply did not follow actual COMMIT',len(commits)-n)
                settled=snapshot();found=call(kind,'/request-lookup',wrapped)
                assert found['request_state']=='found' and found['retry_allowed'] is False
                assert snapshot()==settled
                assert (inventory(owner)==before_stock)==(kind in ('apply','regional','headquarters'))
                with owner.begin() as db:
                    db.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),dict(id=grants[kind]))
                try:
                    assert call(kind,'/request-lookup',wrapped)==found
                    call(kind,'',command,403);call(kind,'/request-seal',wrapped,403)
                    assert snapshot()==settled
                finally:
                    with owner.begin() as db:
                        db.execute(text("UPDATE role_permissions SET effect='allow' WHERE id=:id"),dict(id=grants[kind]))
                assert call(kind,'/request-lookup',closure)==sealed and snapshot()==settled
                cases.append(dict(kind=kind,authenticatedSyntheticSession=True,beforeCommitRollback=True,
                    actualCommitLostReply=True,readOnlyRecovery=True,writeRevocationReadback=True,sealRejectsLateWrite=True))
                print(kind+' actual authenticated HTTP COMMIT, lost reply, readonly recovery and ACL PASS',flush=True)
                lookups.append((kind,wrapped,found))
                return found['result']
            def read_recovery(stage, scrap, *, queue=False, role=None, status=200, anonymous=False, absent=False):
                before=snapshot();n=len(commits);token,person=sessions[actors[role or stage]]
                path=BASE+'recovery/sources'+('' if queue else '/'+scrap['scrap_line_id'])
                reply=client.get(path,params={'stage':stage},headers={} if anonymous else {'Authorization':'Bearer '+token})
                assert reply.status_code==status,(stage,queue,reply.status_code,reply.text)
                assert 'no-store' in reply.headers['cache-control'] and reply.headers['referrer-policy']=='no-referrer'
                assert token not in reply.text and not any(k in reply.text for k in ('command_jsonb','idempotency_key','key_hash'))
                assert snapshot()==before and len(commits)==n
                body=reply.json()
                if status==200:
                    assert body['person_id']==person and body['requested_stage']==stage
                    selected=body
                    if queue and absent:
                        assert body['items']==[] and body['next_after_id'] is None
                    elif queue:
                        matches=[v for v in body['items'] if v.get('scrap_reference',{}).get('scrap_line_id')==scrap['scrap_line_id']]
                        assert len(matches)==1 and body['next_after_id'] is None
                        selected=matches[0]
                    if not absent:
                        assert selected['availability']=='verified' and selected['stock_effect']=='none'
                        assert selected['write_authorization_provided'] is False
                        assert selected['scrap_reference']==dict(scrap_line_id=scrap['scrap_line_id'],expected_scrap_request_hash=scrap['request_hash'])
                        with owner.connect() as db:
                            serial_rows=db.execute(text('SELECT s.id,s.serial_no,s.qr_code FROM inventory_serials s JOIN stock_scrap_serials b ON b.serial_id=s.id WHERE b.scrap_line_id=:id ORDER BY s.id'),dict(id=scrap['scrap_line_id'])).mappings().all()
                        assert selected['serial_ids']==[str(r['id']) for r in serial_rows]
                        assert selected['serials']==[dict(serial_id=str(r['id']),serial_no=r['serial_no'],qr_code=r['qr_code']) for r in serial_rows]
                else:
                    assert 'applications' not in body and 'items' not in body
                recovery_source_reads.append(dict(stage=stage,queue=queue,status=status,role=role or stage,absent=absent,readOnly=True,allFactsUnchanged=True))
                print('recovery source GET '+stage+(' queue' if queue else ' detail')+' '+str(status)+' PASS',flush=True)
                return body
            scrap=run('original',dict(source=dict(kind='original',**{k:str(v) for k,v in source.items()}),
                execution_reason='Synthetic mounted HTTP scrap',evidence_file_ids=[evidence(context['admin_id'])]))
            for stage,role in [('regional','foreign_regional'),('apply','foreign_apply'),('apply','other_apply')]:
                read_recovery(stage,scrap,role=role,status=404)
                read_recovery(stage,scrap,role=role,queue=True,absent=True)
            read_recovery('apply',scrap,status=401,anonymous=True)
            read_recovery('headquarters',scrap,role='apply',status=403)
            read_recovery('apply',scrap,role='headquarters',status=403)
            with owner.begin() as db:
                db.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),dict(id=read_grant))
            try:read_recovery('execute',scrap,status=403)
            finally:
                with owner.begin() as db:
                    db.execute(text("UPDATE role_permissions SET effect='allow' WHERE id=:id"),dict(id=read_grant))
            def recover(scrap):
                for stage in ('apply','regional','headquarters','execute'):
                    read_recovery(stage,scrap,queue=True)
                initial=read_recovery('apply',scrap)
                assert initial['state']=='awaiting_application' and initial['is_current_scrap']
                ref=initial['next_reference']['source']; assert initial['next_reference']['stage']=='apply'
                application=run('apply',dict(action='apply_scrap_recovery',source=ref,
                    reason='Synthetic found stock evidence',evidence_file_ids=[evidence(context['engineer_id'])]))
                regional_source=read_recovery('regional',scrap);binding=dict(regional_source['next_reference']);assert binding.pop('stage')=='regional'
                assert binding==dict(source=ref,recovery_request_id=application['recovery_request_id'],expected_request_hash=application['request_hash'])
                checked=run('regional',dict(action='review_scrap_recovery_region',**binding,decision='verified',reason='Independent regional confirmation'))
                hq_source=read_recovery('headquarters',scrap);hq_binding=dict(hq_source['next_reference']);assert hq_binding.pop('stage')=='headquarters'
                assert hq_binding==dict(**binding,regional_review_id=checked['fact_id'],expected_regional_hash=checked['request_hash'])
                approved=run('headquarters',dict(action='review_scrap_recovery_headquarters',**hq_binding,
                    decision='approve',reason='Independent headquarters approval'))
                with owner.begin() as db:
                    db.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),dict(id=grants['execute']))
                try:execution_source=read_recovery('execute',scrap)
                finally:
                    with owner.begin() as db:
                        db.execute(text("UPDATE role_permissions SET effect='allow' WHERE id=:id"),dict(id=grants['execute']))
                execution_binding=dict(execution_source['next_reference']);assert execution_binding.pop('stage')=='execute'
                assert execution_binding==dict(**binding,headquarters_review_id=approved['fact_id'],expected_headquarters_hash=approved['request_hash'])
                recovered=run('execute',dict(**execution_binding,reason='Physical restoration to original frozen share'))
                assert recovered['stock_effect']=='restores_original_frozen_share'
                for stage in ('apply','regional','headquarters','execute'):
                    result=read_recovery(stage,scrap)
                    assert result['state']=='recovered' and result['is_current_scrap'] is False and result['next_reference'] is None
                    assert result['recovery_posting']==recovered
                return recovered
            original_posting=scrap
            read_source('after-original')
            first_recovery=recover(scrap)
            read_source('after-first-recovery')
            historical=retained(owner)
            before_approval=inventory(owner)
            with Session(api) as db:
                root=db.get(StockLossDisposition,UUID(scrap['root_disposition_id']))
                order=db.get(StockOperationOrder,root.operation_id)
                corrected_source=dict(root_disposition_id=str(root.id),expected_root_request_hash=root.request_hash,
                    expected_submission_plan_hash=order.plan_hash,reversal_id=first_recovery['reversal_id'],
                    expected_reversal_hash=first_recovery['request_hash'])
            approval_command=dict(**corrected_source,disposition='scrap',reason='Independent second scrap approval',
                request_id=uuid4().hex,idempotency_key=uuid4().hex)
            approval_path='/api/v1/stock-operations/loss-reports/corrections/approvals'
            sealed_approval_command=dict(approval_command)
            sealed_approval=call('correction','/request-seal',sealed_approval_command,path=approval_path)
            assert sealed_approval['request_state']=='sealed' and sealed_approval['retry_allowed'] is False
            assert inventory(owner)==before_approval,'approval seal changed stock'
            read_source('after-correction-approval-seal')
            approval_command=dict(approval_command,request_id=uuid4().hex,idempotency_key=uuid4().hex)
            approved=call('correction','',approval_command,path=approval_path)
            assert inventory(owner)==before_approval,'correction approval changed stock'
            read_source('after-correction-approval')
            def read_correction(label,*,role='correction',status=200,anonymous=False):
                before=snapshot();n=len(commits)
                token,person=sessions[actors[role]]
                path='/api/v1/stock-operations/loss-reports/corrections/sources/'+scrap['root_disposition_id']
                reply=client.get(path,headers={} if anonymous else {'Authorization':'Bearer '+token})
                assert reply.status_code==status,(label,reply.status_code,reply.text)
                assert 'no-store' in reply.headers['cache-control'] and reply.headers['referrer-policy']=='no-referrer'
                assert token not in reply.text and not any(k in reply.text for k in ('command_jsonb','idempotency_key','key_hash'))
                assert snapshot()==before and len(commits)==n
                result=reply.json()
                if status==200:
                    assert result['person_id']==person and result['root_disposition_id']==scrap['root_disposition_id']
                    assert result['operation_id']==str(operation_id) and result['line_id']==first_recovery['line_id']
                    assert result['stock_effect']=='none' and result['write_authorization_provided'] is False
                else:
                    assert 'history' not in result and 'approval_choices' not in result
                correction_reads.append(dict(label=label,status=status,readOnly=True,allFactsUnchanged=True))
                print('correction source GET '+label+' PASS',flush=True)
                return result
            read_correction('unauthenticated',status=401,anonymous=True)
            read_correction('engineer-denied',role='apply',status=403)
            read_correction('regional-denied',role='regional',status=403)
            selected=read_correction('awaiting-corrected-scrap')
            assert selected['chain_state']=='awaiting_execution' and selected['approval_reference']==corrected_source
            choices=[v for v in selected['approval_choices'] if v['correction_decision_id']==approved['correction_decision_id']]
            assert len(choices)==1 and choices[0]['disposition']=='scrap' and choices[0]['execution_mode']=='dedicated_flow_required'
            facts=[v for v in selected['history'] if v['kind']=='approval' and v['fact_id']==approved['correction_decision_id']]
            assert len(facts)==1 and facts[0]['request_hash']==approved['request_hash'] and facts[0]['disposition']=='scrap'
            # The browser builds this reference only from the same verified GET.
            # Execute it through the real preview/writer rather than substituting fixture IDs.
            browser_source=dict(kind='correction',**selected['approval_reference'],
                correction_decision_id=choices[0]['correction_decision_id'],expected_correction_decision_hash=facts[0]['request_hash'])
            corrected=run('correction',dict(source=browser_source,
                execution_reason='Synthetic second approved scrap generation',evidence_file_ids=[evidence(context['admin_id'])]))
            assert corrected['source_kind']=='correction' and corrected['root_disposition_id']==scrap['root_disposition_id']
            read_source('after-corrected-scrap')
            consumed=read_correction('after-corrected-scrap')
            assert consumed['chain_state']=='dedicated_compensation_required' and not consumed['approval_choices']
            assert consumed['approval_reference'] is None
            second_recovery=recover(corrected)
            read_source('after-second-recovery')
            reopened=read_correction('after-second-recovery')
            assert reopened['chain_state']=='awaiting_approval' and not reopened['approval_choices']
            assert reopened['approval_reference']['reversal_id']==second_recovery['reversal_id']
            assert second_recovery['reversal_id']!=first_recovery['reversal_id']
            after=retained(owner)
            assert all(rows <= after[name] for name,rows in historical.items()),'successors changed immutable historical facts'
            settled=snapshot();n=len(commits)
            for kind,wrapped,expected in lookups:
                assert call(kind,'/request-lookup',wrapped)==expected
            approval_found=call('correction','/request-lookup',approval_command,path=approval_path)
            assert approval_found['request_state']=='found' and approval_found['result']==approved
            assert approval_found['retry_allowed'] is False
            assert call('correction','/request-lookup',sealed_approval_command,path=approval_path)==sealed_approval
            assert snapshot()==settled and len(commits)==n
            # Authoritative graph/lifecycle checks, and exact restored frozen share.
            # Ledger cursors advance; history must never be made equal by deleting it.
            with owner.begin() as db:
                proof=db.scalar(text('SELECT public.rsc_check_loss_history_graph_0159(:id)'),dict(id=UUID(scrap['root_disposition_id'])))
                assert len(proof['verified_inverse_ids'])==2 and len(proof['verified_correction_ids'])==1
                rows=db.execute(text('SELECT stock_account_id,quantity FROM stock_balances')).all()
                assert {str(k):Decimal(str(v)) for k,v in rows}=={
                    row['stock_account_id']:Decimal(str(row['quantity'])) for row in before_approval['stock_balances']}
                positions=db.execute(text('SELECT serial_id,stock_account_id FROM serial_current_positions')).all()
                assert {str(k):str(v) for k,v in positions}=={
                    row['serial_id']:row['stock_account_id'] for row in before_approval['serial_current_positions']}
                for serial_id,_ in positions:
                    db.execute(text('SELECT public.rsc_check_serial_lifecycle_0092(:id)'),dict(id=serial_id))
            print('two HTTP scrap/recovery generations, immutable history and old request rereads PASS',flush=True)
            # Capture profiling before isolated proof instrumentation; its
            # additional owner queries are not part of the HTTP comparison.
            statistics=function_delta(function_before,function_stats(owner))
            from pg16_scrap_history_proof_gate import run as history_proof_gate
            history_proof=history_proof_gate(context['engines'],UUID(scrap['root_disposition_id']))
            assert snapshot()==settled
            print('private history proof: exactly one complete check per fact per graph call PASS',flush=True)
            return dict(passed=True,tracking=context['tracking'],cases=cases,readOnlyRequests=len(readonly),
                actualCommits=len(commits),correctedScrapHttpVerified=True,completedScrapRecoveryGenerations=2,
                historicalRequestsReadBack=len(lookups)+2,priorImmutableFactsRetained=True,
                frozenShareRestored=True,realProviderLoginVerified=False,sourceReads=source_reads,
                timings=timings,functionStatistics=statistics,historyProofReuse=history_proof,correctionSourceReads=correction_reads,
                recoverySourceReads=recovery_source_reads)
        except BaseException:
            # Preserve timings when a later assertion fails. No SQL bodies or
            # credentials: only function names, counters and route timings.
            try:
                print('HTTP failure diagnostics '+json.dumps(dict(tracking=context['tracking'],
                    timings=timings,sourceReads=source_reads,
                    functionStatistics=function_delta(function_before,function_stats(owner)))),flush=True)
            except Exception as diagnostic_error:
                print('HTTP failure diagnostics unavailable: '+type(diagnostic_error).__name__,flush=True)
            raise
        finally:client.close()


def release(engines,*,tracking,migrate,provision):
    if tracking not in ('quantity','serial'):
        raise ValueError('tracking must be quantity or serial')
    migrate('http-complete-0170','upgrade','20261229_0180');provision()
    validate_production_database_security(engines['star_oam_api'],expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    with engines['star_oam_migrator'].connect() as db:assert_fresh_defaults(db)
    proof=opening(engines,tracking=tracking,after_preview=exercise)['submission']
    return dict(passed=True,scope='mounted-authenticated-scrap-http',**{k:v for k,v in proof.items() if k!='passed'},productionAcceptance=False)
