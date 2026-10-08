"""Formal migrated closure/barrier on the real completed HTTP fixture.

Exercises insert authorization, coverage, audit, permission defaults and the
write barrier installed by Alembic. The caller verifies runtime and retention.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
from uuid import UUID, uuid4
from threading import Event
from unittest.mock import patch

from app.material_request_closure_schemas import MaterialRequestClosureOut

from sqlalchemy import select, text, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.demand_models import MaterialRequest
from app.formal_access import load_formal_principal
from app.formal_services.material_request_closure import close_material_request, closure_command_status
from app.formal_services.material_request_closure import _audit_document
from app.formal_services.audit_chain import append_audit_event
from app.formal_services import material_request_lifecycle as lifecycle
from app.foundation_models import Permission, Role
from app.material_request_closure_schema import closures
from test_material_request_draft_service import SECRET
from app.stock_scrap_security_probe import snapshot as catalog_snapshot, difference
from pg16_stock_scrap_structure_gate import original_columns, facts


def run(engines, *, request_id, actor_id, directory):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    folder = Path(__file__).parents[1] / 'alembic/request_closure_0169'
    barrier = runpy.run_path(str(folder / 'write_barrier.py'))
    policy = runpy.run_path(str(folder / 'permission_policy.py'))
    identifier = UUID(request_id)
    with owner.begin() as db:
        assert db.scalar(text('SELECT version_num FROM public.alembic_version')) == '20261229_0180'
        policy_result=policy['install'](db)
        assert policy_result['createdPermissions']==0 and policy_result['createdGrants']==0
        assert policy_result['advancedUsers']==0
        permission_id=db.scalar(select(Permission.id).where(Permission.resource=='material_request',Permission.action=='close',Permission.field_code==''))
        admin_role=db.scalar(select(Role.id).where(Role.code=='admin'))
    with owner.connect() as db:
        columns={k:v for k,v in original_columns(db).items() if not k.startswith('audit_') and k!='material_request_closures'}
        business_before=facts(db,columns)

    close_key, close_trace = 'native-close-candidate-0001', 'native-close-trace-0001'
    def close(db):
        request = db.get(MaterialRequest, identifier)
        return close_material_request(db, actor=load_formal_principal(db, actor_id), request_id=identifier,
            payload={'expected_request_version': request.version, 'reason': '逐项实际入账核验完成'},
            idempotency_key=close_key, secret=SECRET, trace_request_id=close_trace)

    insert_rejections = []
    # Capture a legitimate row using the real service, then roll the entire
    # transaction back before exercising raw INSERTs. No trigger is disabled.
    with Session(api) as db:
        candidate = close(db)
        template = dict(db.execute(select(closures).where(closures.c.id == candidate.closure_id)).mappings().one())
        db.rollback()
    def reject_insert(label, mutate, expected, *, missing_audit=False, wrong_audit=False):
        try:
            with Session(api) as db, db.begin():
                row = {**template, 'id':uuid4()}
                row['occurred_at'] = row['created_at'] = lifecycle._database_now(db)
                mutate(row)
                db.execute(closures.insert().values(**row))
                if wrong_audit:
                    append_audit_event(db,stream_key='material_request',actor_user_id=actor_id,
                        action='material_request.close',aggregate_type='material_request_closure',aggregate_id=str(row['id']),
                        before_jsonb={'business_status':'open'},after_jsonb={**_audit_document(row),'request_version':row['request_version']+1},
                        request_id=row['trace_request_id'],occurred_at=row['occurred_at'],created_at=row['created_at'])
                if missing_audit:
                    db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        except DBAPIError as exc:
            assert expected in str(exc.orig), (label,str(exc.orig))
            insert_rejections.append(label)
        else:
            raise AssertionError(label + ' raw closure unexpectedly committed')
    for name,changes,expected in (
        ('stale-version',{'request_version':template['request_version']+1},'identity version or timestamp'),
        ('wrong-revision',{'revision_id':uuid4()},'identity version or timestamp'),
        ('stale-auth',{'authorization_version':template['authorization_version']+1},'current close identity'),
        ('wrong-person',{'actor_person_id':uuid4()},'current close identity'),
        ('wrong-assignment',{'actor_role_assignment_id':uuid4()},'current scoped close grant'),
        ('request-hash',{'request_hash':'f'*64},'proof or request hash'),
        ('evidence-hash',{'evidence_sha256':'f'*64},'proof or request hash'),
        ('missing-request',{'request_id':uuid4()},'fulfillment request missing'),
    ):
        reject_insert(name,lambda row,changes=changes:row.update(changes),expected)
    def forged(row):
        evidence=json.loads(json.dumps(row['evidence_jsonb']))
        evidence['coverage']['lines'][0]['posted_qty']='0.000'
        row.update(evidence_jsonb=evidence,evidence_sha256=lifecycle._canonical_hash(evidence))
    reject_insert('forged-quantity-with-valid-hash',forged,'proof or request hash')
    reject_insert('missing-audit',lambda row:None,'one exact committed audit',missing_audit=True)
    reject_insert('wrong-audit-content-with-valid-chain',lambda row:None,'one exact committed audit',missing_audit=True,wrong_audit=True)
    with owner.begin() as db:
        db.execute(text("UPDATE role_permissions SET effect='deny' WHERE permission_id=:id AND role_id=:role"),
            {'id':permission_id,'role':admin_role})
    try:
        reject_insert('current-explicit-deny',lambda row:None,'explicit close or read deny')
    finally:
        with owner.begin() as db:
            db.execute(text("UPDATE role_permissions SET effect='allow' WHERE permission_id=:id AND role_id=:role"),
                {'id':permission_id,'role':admin_role})

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
    def sessions():
        with Session(api) as db:
            if read_only:
                db.execute(text('SET TRANSACTION READ ONLY'))
            if pause_commit:
                def wait_at_commit(session):
                    ready.set()
                    assert permit.wait(12), 'HTTP close commit was not released'
                event.listen(db, 'before_commit', wait_at_commit)
            yield db
    def principal():
        with Session(api) as db:
            return load_formal_principal(db, actor_id)
    body={'expected_request_version':template['request_version'],'reason':template['reason']}
    fingerprint=lifecycle._canonical_hash(body)
    path='/api/v1/material-requests/'+request_id
    headers={'Idempotency-Key':close_key,'X-Request-ID':close_trace,'X-Request-Fingerprint':fingerprint}
    http_proof=[]
    with patch.object(app,'dependency_overrides',{**app.dependency_overrides,get_db:sessions,
            get_formal_principal:principal,get_settings:lambda:current_settings}), TestClient(app) as client:
        observed=client.get(path+'/closure')
        assert observed.status_code==200 and observed.json()['business_status']=='open'
        assert observed.json()['close_permitted'] is True
        with api.connect() as connection, ThreadPoolExecutor(max_workers=2) as pool:
            pid=connection.scalar(text('SELECT pg_backend_pid()'));connection.rollback()
            closing=pool.submit(client.post,path+'/close',json=body,headers=headers)
            try:
                assert ready.wait(10), 'HTTP close did not reach commit'
                def competing_write():
                    try:
                        with connection.begin():
                            connection.execute(text("SET LOCAL statement_timeout='10s'"))
                            connection.execute(text('INSERT INTO public.material_request_lines (id,request_id) VALUES (:id,:request)'),
                                {'id':uuid4(),'request':identifier})
                    except DBAPIError as exc:
                        return str(exc.orig)
                    return 'unexpected success'
                future=pool.submit(competing_write)
                import time
                deadline=time.monotonic()+8
                waiting=False
                with api.connect() as observer:
                    while time.monotonic()<deadline:
                        observer.rollback()
                        waiting=bool(observer.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),{'pid':pid}))
                        if waiting or future.done():break
                        time.sleep(0.05)
                assert waiting, 'competing fulfillment did not wait on HTTP closure parent'
            finally:
                permit.set()
            response=closing.result(timeout=15)
            assert response.status_code==201,response.text
            assert 'no-store' in response.headers['cache-control']
            original=MaterialRequestClosureOut.model_validate(response.json())
            assert original.replayed is False
            assert '0169 business request is closed' in future.result(timeout=12)
        pause_commit=False
        http_proof.append(dict(route='close',status=201,newFact=True))
        response=client.post(path+'/close',json=body,headers=headers)
        assert response.status_code==201 and response.json()['replayed'] is True
        assert response.json()['closure_id']==str(original.closure_id)
        response=client.post(path+'/close',json={**body,'reason':'different content'},headers=headers)
        assert response.status_code==409,response.text
        response=client.post(path+'/close',json=body,headers={**headers,'Idempotency-Key':close_key+'-new'})
        assert response.status_code==409,response.text
        current_settings.material_request_writes_enabled=False
        read_only=True
        with owner.connect() as db:
            all_columns=original_columns(db);all_before=facts(db,all_columns)
        for route in ('closure','close-command-status'):
            response=client.get(path+'/'+route,headers=headers)
            assert response.status_code==200,response.text
            assert 'no-store' in response.headers['cache-control']
            fact=response.json()['closure' if route=='closure' else 'command']
            assert fact['closure_id']==str(original.closure_id)
            http_proof.append(dict(route=route,status=200,readOnly=True,writesDisabled=True))
        mismatch=client.get(path+'/close-command-status',headers={**headers,'X-Request-Fingerprint':'b'*64})
        assert mismatch.status_code==409,mismatch.text
        missing=client.get(path+'/close-command-status',headers={**headers,'Idempotency-Key':close_key+'-missing'})
        assert missing.status_code==200 and missing.json()=={'lookup_status':'not_observed','command':None}
        with owner.connect() as db:
            assert facts(db,all_columns)==all_before, 'HTTP reads changed committed facts'
    assert original.business_status == 'closed'
    assert all(line.remaining_qty == '0.000' for line in original.lines)

    rejected = []
    def refuse(label, statement, parameters=None, *, expected='0169 business request is closed', engine=api,
               isolation=None):
        try:
            with engine.begin() as db:
                if isolation:
                    db.execute(text('SET TRANSACTION ISOLATION LEVEL ' + isolation))
                db.execute(text(statement), parameters or {})
        except DBAPIError as exc:
            assert expected in str(exc.orig), (label, str(exc.orig))
            rejected.append(label)
        else:
            raise AssertionError(label + ' unexpectedly succeeded')
    for table in barrier['DIRECT']:
        refuse('new-' + table, f'INSERT INTO public.{table} (id,request_id) VALUES (:id,:request)',
               {'id': uuid4(), 'request': identifier})
    with api.connect() as db:
        line = db.scalar(text('SELECT id FROM material_request_lines WHERE request_id=:id LIMIT 1'), {'id': identifier})
        outbound = db.scalar(text('SELECT id FROM outbound_postings WHERE request_id=:id LIMIT 1'), {'id': identifier})
        shipment_line = db.execute(text('SELECT id,shipment_id FROM shipment_lines WHERE outbound_posting_id=:id'), {'id': outbound}).one()
        receipt = db.scalar(text('SELECT id FROM receipts WHERE shipment_id=:id'), {'id': shipment_line.shipment_id})
        inbound = db.scalar(text('SELECT id FROM inbound_orders WHERE receipt_id=:id'), {'id': receipt})
    for table, column, value in (
        ('supply_tasks','request_line_id',line), ('substitution_decisions','request_line_id',line),
        ('shipment_lines','outbound_posting_id',outbound), ('receipts','shipment_id',shipment_line.shipment_id),
        ('receipt_lines','shipment_line_id',shipment_line.id), ('inbound_orders','receipt_id',receipt),
        ('inbound_postings','inbound_order_id',inbound),
    ):
        # Owner-level counterexamples test the trigger itself even where the
        # API already lacks a raw INSERT grant (such as substitution decisions).
        refuse('new-' + table, f'INSERT INTO public.{table} (id,{column}) VALUES (:id,:parent)',
            {'id':uuid4(),'parent':value}, engine=owner)
    refuse('api-substitution-insert-acl',
        'INSERT INTO public.substitution_decisions (id,request_line_id) VALUES (:id,:parent)',
        {'id':uuid4(),'parent':line}, expected='permission denied')
    refuse('parent-version', 'UPDATE public.material_requests SET version=version+1 WHERE id=:id', {'id':identifier})
    for isolation in ('REPEATABLE READ', 'SERIALIZABLE'):
        refuse('snapshot-isolation-' + isolation,
            'INSERT INTO public.material_request_lines (id,request_id) VALUES (:id,:request)',
            {'id':uuid4(),'request':identifier}, expected='0169 fulfillment requires read committed isolation', isolation=isolation)
    for operation in ('UPDATE public.material_request_closures SET reason=reason',
                      'DELETE FROM public.material_request_closures', 'TRUNCATE public.material_request_closures'):
        refuse(operation.split()[0].lower() + '-closure', operation,
               expected='0169 business closure facts are append-only', engine=owner)
    # Runtime principals may not invoke SECURITY DEFINER internals or mutate
    # the immutable close row, even before the formal runtime catalog lands.
    refuse('api-helper-acl', 'SELECT public.rsc_require_request_open_0169(:id)', {'id':identifier}, expected='permission denied')
    refuse('api-closure-update-acl', 'UPDATE public.material_request_closures SET reason=reason', expected='permission denied')
    with Session(api) as db, db.begin():
        replay = close(db)
        assert replay.closure_id == original.closure_id and replay.replayed
    with Session(api) as db, db.begin():
        db.execute(text('SET TRANSACTION READ ONLY'))
        recovered = closure_command_status(db, actor=load_formal_principal(db, actor_id), request_id=identifier,
            idempotency_key=close_key, secret=SECRET)
        assert recovered.closure_id == original.closure_id and recovered.replayed
    with owner.connect() as db:
        assert db.scalar(select(text('count(*)')).select_from(closures)) == 1
        assert db.scalar(text('SELECT version_num FROM public.alembic_version')) == '20261229_0180'
        assert facts(db,columns)==business_before, 'closure changed pre-existing business facts'
    result = dict(candidate=False, requestId=request_id, closed=original.model_dump(mode='json'),
        concurrentWriterWaited=True, concurrentWriterRejected=True, rejected=rejected,
        readOnlyRecovery=True, unchangedMigrationHead='20261229_0180',
        insertRejections=insert_rejections, databaseInsertAuthorityCoverageAuditGuard=True,
        businessFactsUnchanged=True,permissionPolicy=policy_result,http=http_proof,
        httpSameKeyReplayed=True,httpChangedContentRejected=True,httpReadOnlyFactsUnchanged=True,
        formalAlembicMigration=True, productionAcceptance=False,
        checkedAt=datetime.now(timezone.utc).isoformat())
    (directory/'closure-database-gate.json').write_text(json.dumps(result, indent=2)+'\n')
    return result
