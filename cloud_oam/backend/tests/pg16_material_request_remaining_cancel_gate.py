"""Formal 0171 compensation after real partial fulfillment and release.

Synthetic identities; this is local database integration, not user UAT.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
import copy
import json
import time
from uuid import UUID, uuid4

from sqlalchemy import select, text, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.material_request_remaining_cancel_schemas import MaterialRequestRemainingCancellationOut
from app.demand_models import MaterialRequest
from app.formal_access import load_formal_principal
from app.formal_services import material_request_remaining_cancel as service
from app.formal_services.audit_chain import append_audit_event
from app.material_request_remaining_cancel_schema import cancellations, cancellation_lines
from pg16_stock_scrap_structure_gate import original_columns, facts
from test_material_request_draft_service import SECRET


def _wait_on_blocker(engine, pid, blocker, future):
    """Observe the actual competing backend and exact blocker, not elapsed time."""
    deadline = time.monotonic() + 8
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            observer.rollback()
            if observer.scalar(text('SELECT :blocker = ANY(pg_blocking_pids(:pid))'),
                               {'blocker': blocker, 'pid': pid}):
                return
            if future.done():
                break
            time.sleep(0.05)
    raise AssertionError('expected authority transaction did not block the competing backend')


def run(engines, *, request_id, actor_id_for_close, directory):
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    request_id = UUID(request_id)
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261227_0178'
        columns = {k:v for k,v in original_columns(db).items()
                   if not k.startswith('audit_') and not k.startswith('material_request_remaining_cancel')
                   and k != 'material_request_closures'}
        business_before = facts(db, columns)
    with Session(api) as db:
        request = db.get(MaterialRequest, request_id)
        actor_id = request.requester_user_id
        assessment = service.remaining_fulfillment(db, actor=load_formal_principal(db,actor_id),request_id=request_id)
        assert len(assessment.lines)==1
        assert assessment.lines[0].posted_qty=='1.000' and assessment.lines[0].unreserved_qty=='1.000'
        payload=dict(expected_request_version=request.version,reason='部分入账后取消全部剩余未履约数量',
            lines=[dict(request_line_id=str(assessment.lines[0].request_line_id),cancelled_qty='1.000')])
    def cancel(db):
        return service.cancel_remaining_demand(db,actor=load_formal_principal(db,actor_id),request_id=request_id,
            payload=payload,idempotency_key='native-remaining-cancel-0001',secret=SECRET,trace_request_id='native-remaining-cancel-trace-0001')
    with Session(api) as db:
        prepared=cancel(db)
        template=dict(db.execute(select(cancellations).where(cancellations.c.id==prepared.cancellation_id)).mappings().one())
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        db.rollback()
    with owner.connect() as db:
        cancel_grant = db.execute(text("""SELECT rp.id,rp.effect FROM role_permissions rp
            JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id
            WHERE r.code='technician' AND p.resource='material_request'
              AND p.action='cancel' AND p.field_code=''""")).one()
        assert cancel_grant.effect == 'allow'
    def restore_cancel_grant():
        with owner.begin() as db:
            assert db.execute(text("UPDATE role_permissions SET effect='allow' WHERE id=:id"),
                              {'id':cancel_grant.id}).rowcount == 1
    rejected=[]
    def bad_insert(label, mutate, *, add_lines=True, add_audit=True):
        try:
            with Session(api) as db, db.begin():
                row=copy.deepcopy(template);row['id']=uuid4()
                row['occurred_at']=row['created_at']=service.lifecycle._database_now(db)
                mutate(row)
                db.execute(cancellations.insert().values(**row))
                if add_lines:
                    db.execute(cancellation_lines.insert(),[dict(cancellation_id=row['id'],request_id=request_id,
                        revision_id=row['revision_id'],request_line_id=UUID(item['request_line_id']),cancelled_qty=item['cancelled_qty'])
                        for item in row['evidence_jsonb']['input']['lines']])
                if add_audit:
                    append_audit_event(db,stream_key='material_request',actor_user_id=actor_id,action=service.ACTION,
                        aggregate_type='material_request_remaining_cancellation',aggregate_id=str(row['id']),
                        before_jsonb={'remaining_cancellation':'not_recorded'},after_jsonb=service._audit_document(row),
                        request_id=row['trace_request_id'],occurred_at=row['occurred_at'],created_at=row['created_at'])
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        except DBAPIError as exc:
            assert '0171' in str(exc.orig), (label,str(exc.orig))
            rejected.append(label)
        else: raise AssertionError(label+' unexpectedly committed')
    for name,updates in [('version',{'request_version':template['request_version']+1}),
                         ('actor',{'actor_user_id':'not-the-requester'}),
                         ('authorization',{'authorization_version':template['authorization_version']+1}),
                         ('digest',{'evidence_sha256':'f'*64}),('request-hash',{'request_hash':'f'*64})]:
        bad_insert(name,lambda row,updates=updates:row.update(updates))
    bad_insert('wrong-line-quantity',lambda row:row['evidence_jsonb']['input']['lines'][0].update(cancelled_qty='2.000'))
    bad_insert('missing-input-version',lambda row:row['evidence_jsonb']['input'].pop('expected_request_version'))
    bad_insert('missing-lines',lambda row:None,add_lines=False)
    bad_insert('missing-audit',lambda row:None,add_audit=False)
    cancel_key, cancel_trace = 'native-remaining-cancel-0001', 'native-remaining-cancel-trace-0001'
    # The production HTTP route owns the committing transaction. Pause only
    # its before_commit event to observe a genuinely competing PG connection;
    # do not replace the service, route output or database guard.
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.config import get_settings
    ready, permit = Event(), Event()
    current_settings = get_settings().model_copy(update={'material_request_writes_enabled':True,
        'material_request_idempotency_hmac_secret':SECRET.decode()})
    pause_commit = True
    read_only = False
    capture_pid = None
    pid_ready = Event()
    def sessions():
        with Session(api) as db:
            if capture_pid is not None:
                capture_pid.append(db.scalar(text('SELECT pg_backend_pid()')))
                pid_ready.set()
            if read_only:
                db.execute(text('SET TRANSACTION READ ONLY'))
            if pause_commit:
                def wait_at_commit(session):
                    ready.set()
                    assert permit.wait(12), 'HTTP cancellation commit was not released'
                event.listen(db, 'before_commit', wait_at_commit)
            yield db
    def principal():
        with Session(api) as db:
            return load_formal_principal(db, actor_id)
    body=payload
    fingerprint=service.lifecycle._canonical_hash(body)
    path='/api/v1/material-requests/'+str(request_id)
    headers={'Idempotency-Key':cancel_key,'X-Request-ID':cancel_trace,'X-Request-Fingerprint':fingerprint}
    http_proof=[]
    with patch.object(app,'dependency_overrides',{**app.dependency_overrides,get_db:sessions,
            get_formal_principal:principal,get_settings:lambda:current_settings}), TestClient(app) as client:
        observed=client.get(path+'/remaining-cancellation')
        assert observed.status_code==200 and observed.json()['cancellation'] is None
        assert observed.json()['cancel_permitted'] is True
        # Global permission denial wins first: the already-started HTTP request
        # must wait for that row and re-read its committed deny, leaving no fact.
        authority_proof = []
        pause_commit = False
        with owner.connect() as held, ThreadPoolExecutor(max_workers=1) as pool:
            blocker = held.scalar(text('SELECT pg_backend_pid()'))
            held.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),
                         {'id':cancel_grant.id})
            capture_pid = []
            pid_ready.clear()
            future = pool.submit(client.post,path+'/cancel-remaining',json=body,headers=headers)
            try:
                assert pid_ready.wait(8), 'HTTP authority contender did not start'
                _wait_on_blocker(api,capture_pid[0],blocker,future)
                held.commit()
                denied = future.result(timeout=12)
                assert denied.status_code == 403, denied.text
                authority_proof.append(dict(order='revocation-first',channel='HTTP',
                    exactBlockerObserved=True,status=403))
            finally:
                held.rollback()
                capture_pid = None
                restore_cancel_grant()
        # The migration-owned INSERT guard must reject the same race even when
        # application admission is bypassed. No candidate schema is installed.
        with owner.connect() as held, api.connect() as writer, ThreadPoolExecutor(max_workers=1) as pool:
            blocker = held.scalar(text('SELECT pg_backend_pid()'))
            held.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),
                         {'id':cancel_grant.id})
            pid = writer.scalar(text('SELECT pg_backend_pid()')); writer.rollback()
            def direct_insert():
                try:
                    with writer.begin():
                        writer.execute(text("SET LOCAL statement_timeout='12s'"))
                        row=copy.deepcopy(template); row['id']=uuid4()
                        row['occurred_at']=row['created_at']=writer.scalar(text('SELECT CURRENT_TIMESTAMP'))
                        writer.execute(cancellations.insert().values(**row))
                except DBAPIError as exc:
                    return exc.orig.sqlstate, str(exc.orig)
                return None, 'unexpected success'
            future=pool.submit(direct_insert)
            try:
                _wait_on_blocker(api,pid,blocker,future)
                held.commit()
                sqlstate,message=future.result(timeout=15)
                assert sqlstate=='42501' and '0171 explicit requester deny' in message, (sqlstate,message)
                authority_proof.append(dict(order='revocation-first',channel='direct-api-role-insert',
                    exactBlockerObserved=True,sqlstate=sqlstate))
            finally:
                held.rollback()
                restore_cancel_grant()
        with owner.connect() as db:
            assert facts(db,columns)==business_before
            assert db.scalar(select(cancellations.c.id).where(cancellations.c.request_id==request_id)) is None
        pause_commit = True
        with api.connect() as connection, owner.connect() as revoker, ThreadPoolExecutor(max_workers=3) as pool:
            pid=connection.scalar(text('SELECT pg_backend_pid()'));connection.rollback()
            revoke_pid=revoker.scalar(text('SELECT pg_backend_pid()'));revoker.rollback()
            capture_pid=[];pid_ready.clear()
            closing=pool.submit(client.post,path+'/cancel-remaining',json=body,headers=headers)
            try:
                assert ready.wait(10), 'HTTP cancellation did not reach commit'
                def competing_write():
                    try:
                        with connection.begin():
                            connection.execute(text("SET LOCAL statement_timeout='10s'"))
                            connection.execute(text('INSERT INTO public.material_request_lines (id,request_id) VALUES (:id,:request)'),
                                {'id':uuid4(),'request':request_id})
                    except DBAPIError as exc:
                        return str(exc.orig)
                    return 'unexpected success'
                future=pool.submit(competing_write)
                deadline=time.monotonic()+8
                waiting=False
                with api.connect() as observer:
                    while time.monotonic()<deadline:
                        observer.rollback()
                        waiting=bool(observer.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),{'pid':pid}))
                        if waiting or future.done():break
                        time.sleep(0.05)
                assert waiting, 'competing fulfillment did not wait on HTTP cancellation parent'
                def revoke_after_cancel():
                    with revoker.begin():
                        revoker.execute(text("SET LOCAL statement_timeout='12s'"))
                        changed=revoker.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),
                                                {'id':cancel_grant.id})
                        assert changed.rowcount==1
                revocation=pool.submit(revoke_after_cancel)
                _wait_on_blocker(owner,revoke_pid,capture_pid[0],revocation)
            finally:
                permit.set()
            response=closing.result(timeout=15)
            assert response.status_code==201,response.text
            assert 'no-store' in response.headers['cache-control']
            original=MaterialRequestRemainingCancellationOut.model_validate(response.json())
            assert original.replayed is False
            assert '0171 remaining demand cancelled' in future.result(timeout=12)
            revocation.result(timeout=15)
            authority_proof.append(dict(order='cancellation-first',channel='HTTP',
                exactBlockerObserved=True,cancellationCommittedBeforeRevocation=True))
            capture_pid=None
        pause_commit=False
        http_proof.append(dict(route='cancel-remaining',status=201,newFact=True))
        response=client.post(path+'/cancel-remaining',json=body,headers=headers)
        assert response.status_code==201 and response.json()['replayed'] is True
        assert response.json()['cancellation_id']==str(original.cancellation_id)
        response=client.post(path+'/cancel-remaining',json={**body,'reason':'different content'},headers=headers)
        assert response.status_code==409,response.text
        response=client.post(path+'/cancel-remaining',json=body,headers={**headers,'Idempotency-Key':cancel_key+'-new'})
        assert response.status_code==403,response.text
        assert response.json()['detail']['code']=='material_request_self_scope_forbidden'
        current_settings.material_request_writes_enabled=False
        read_only=True
        with owner.connect() as db:
            all_columns=original_columns(db);all_before=facts(db,all_columns)
        for route in ('remaining-cancellation','cancel-remaining-command-status'):
            response=client.get(path+'/'+route,headers=headers)
            assert response.status_code==200,response.text
            assert 'no-store' in response.headers['cache-control']
            fact=response.json()['cancellation' if route=='remaining-cancellation' else 'command']
            assert fact['cancellation_id']==str(original.cancellation_id)
            http_proof.append(dict(route=route,status=200,readOnly=True,writesDisabled=True))
        mismatch=client.get(path+'/cancel-remaining-command-status',headers={**headers,'X-Request-Fingerprint':'b'*64})
        assert mismatch.status_code==409,mismatch.text
        missing=client.get(path+'/cancel-remaining-command-status',headers={**headers,'Idempotency-Key':cancel_key+'-missing'})
        assert missing.status_code==200 and missing.json()=={'lookup_status':'not_observed','command':None}
        detail=client.get(path)
        assert detail.status_code==200,detail.text
        assert detail.json()['lines'][0]['cancelled_qty']=='1.000' and not detail.json()['allowed_actions']
        with owner.connect() as db:
            assert facts(db,all_columns)==all_before, 'HTTP reads changed committed facts'
        with Session(api) as db:
            denied_actor=load_formal_principal(db,actor_id)
            request=db.get(MaterialRequest,request_id)
            try:
                service._authority(db,denied_actor,request)
            except service.lifecycle.MaterialRequestLifecycleError as exc:
                assert exc.http_status_code==403
            else:
                raise AssertionError('cancel authority remained usable after committed denial')
        restore_cancel_grant()
        # Preserve the original duplicate-command check separately from the
        # permission-first rejection above.
        read_only=False
        current_settings.material_request_writes_enabled=True
        response=client.post(path+'/cancel-remaining',json=body,headers={**headers,'Idempotency-Key':cancel_key+'-new'})
        assert response.status_code==409,response.text
    result=original
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        recovered=service.remaining_cancellation_command_status(db,actor=load_formal_principal(db,actor_id),
            request_id=request_id,idempotency_key='native-remaining-cancel-0001',secret=SECRET,
            request_fingerprint=service.lifecycle._canonical_hash(payload))
        assert recovered.cancellation_id==result.cancellation_id and recovered.replayed
    with Session(api) as db, db.begin():
        assert cancel(db).cancellation_id==result.cancellation_id
    for label,sql,params in (
        ('request-write', 'UPDATE public.material_requests SET version=version+1 WHERE id=:id',{'id':request_id}),
        ('line-write', 'UPDATE public.material_request_lines SET cancelled_qty=1 WHERE request_id=:id',{'id':request_id}),
        ('cancel-update','UPDATE public.material_request_remaining_cancellations SET reason=reason',{}),
        ('cancel-delete','DELETE FROM public.material_request_remaining_cancellations',{}),
        ('line-delete','DELETE FROM public.material_request_remaining_cancellation_lines',{}),
        ('cancel-truncate','TRUNCATE public.material_request_remaining_cancellation_lines',{}),
    ):
        try:
            with owner.begin() as db:db.execute(text(sql),params)
        except DBAPIError as exc:
            assert 'remaining demand cancelled' in str(exc.orig) or 'append-only' in str(exc.orig),(label,str(exc.orig))
            rejected.append(label)
        else:raise AssertionError(label+' unexpectedly permitted')
    with owner.connect() as db:
        assert facts(db,columns)==business_before
        coverage=db.scalar(text('SELECT public.rsc_closure_evidence_0169(:id)'),{'id':request_id})
        assert coverage['coverage']['quantity_coverage_complete']
        assert [(r['approved_qty'],r['cancelled_qty'],r['posted_qty'],r['remaining_qty'])
                for r in coverage['coverage']['lines']]==[('2.000','1.000','1.000','0.000')]
        for table in (cancellations.name,cancellation_lines.name):
            assert db.scalar(text("SELECT has_table_privilege('star_oam_api',:name,'SELECT,INSERT')"),{'name':table})
            for privilege in ('UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'):
                assert not db.scalar(text("SELECT has_table_privilege('star_oam_api',:name,:privilege)"),{'name':table,'privilege':privilege})
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261227_0178'
    from app.formal_services.material_request_closure import close_material_request, closure_command_status
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, actor_id)
        complete = service.completion_quantities(db, actor=actor, request_id=request_id)
        remainder = service.remaining_fulfillment(db, actor=actor, request_id=request_id)
        assert complete.quantity_coverage_complete
        assert complete.lines[0].cancelled_qty == '1.000' and complete.lines[0].posted_qty == '1.000'
        assert remainder.lines[0].unreserved_qty == '0.000'
    with Session(api) as db, db.begin():
        closed = close_material_request(db, actor=load_formal_principal(db, actor_id_for_close), request_id=request_id,
            payload=dict(expected_request_version=payload['expected_request_version'], reason='入账及剩余取消均已核对'),
            idempotency_key='native-cancel-then-close-0001',secret=SECRET,trace_request_id='native-cancel-close-trace-0001')
        assert closed.lines[0].cancelled_qty == '1.000' and closed.lines[0].posted_qty == '1.000'
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        recovered_close = closure_command_status(db, actor=load_formal_principal(db, actor_id_for_close), request_id=request_id,
            idempotency_key='native-cancel-then-close-0001',secret=SECRET)
        assert recovered_close.closure_id == closed.closure_id
    with owner.connect() as db:
        assert facts(db, columns) == business_before
    evidence=dict(passed=True,scope='formal 0171 cancellation and closure',alembicActivated=True,
        requestId=str(request_id),cancellation=result.model_dump(mode='json'),closure=closed.model_dump(mode='json'),
        rejections=rejected,http=http_proof,authorityConcurrency=authority_proof,
        recoveryAfterCommittedCancelRevocation=True,
        newKeyDeniedAfterRevocation=True,newKeyConflictAfterAuthorityRestored=True,
        concurrentWriterWaited=True,concurrentWriterRejected=True,originalBusinessUnchanged=True,readOnlyRecovery=True,productionAcceptance=False)
    (directory/'remaining-cancel-checks.json').write_text(json.dumps(evidence,indent=2)+'\n')
    return evidence
