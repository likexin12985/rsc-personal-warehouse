"""Scrap/recovery execution evidence in complete synthetic model ledgers.

No external storage/provider calls, full migration, or PG service posting.
"""
from copy import deepcopy
from datetime import timedelta
import json
from uuid import uuid4
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from app.formal_services.audit_chain import calculate_audit_event_hash
from pg16_scrap_inventory_edges_gate import CLOUD, decoded, install
from pg16_stock_scrap_bindings_gate import snapshot

AUDIT_HELPERS = {
    'rsc_check_scrap_recovery_files_with_audit_0165': 'uuid,uuid[]',
    'rsc_check_scrap_recovery_events_with_audit_0165': 'jsonb,text,uuid,uuid[]',
    'rsc_check_scrap_recovery_approvals_with_audit_0165': 'uuid,uuid[]',
    'rsc_check_scrap_inventory_edges_with_audit_0165': 'uuid,uuid[]',
    'rsc_check_scrap_historical_plans_with_audit_0165': 'uuid,uuid[]',
    'rsc_check_scrap_execution_files_with_audit_0165': 'uuid,uuid[]',
    'rsc_check_scrap_posting_events_with_audit_0165': 'jsonb,boolean,uuid[]',
    'rsc_assert_scrap_domain_events_with_audit_0165': 'jsonb,text,text,jsonb,uuid,uuid[]',
    'rsc_check_scrap_execution_events_with_audit_0165': 'uuid,uuid[]',
}


def run(engines,fixture):
    data=json.loads(fixture.read_text(),object_hook=decoded)
    owner,api=engines['star_oam_migrator'],engines['star_oam_api']
    metadata=install(owner,data)
    catalog=json.loads((CLOUD/'backend/alembic/stock_loss_corrections_0159/frozen-catalog.json').read_text())
    with owner.begin() as db:
        for name in ('rsc_loss_chain_projection_0159','rsc_loss_hold_projection_0159'):
            db.execute(text(next(f['definition'] for f in catalog['newFunctions'] if f['proname']==name)))
            db.execute(text(f'REVOKE ALL ON FUNCTION public.{name}(uuid,bigint) FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox'))
        for name in ('historical_plans.sql','execution_evidence.sql'):
            db.execute(text((CLOUD/'backend/alembic/stock_scrap_0165'/name).read_text()))
        records=db.execute(text("SELECT c.relname,t.tgdeferrable,t.tginitdeferred,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgname='trg_scrap_execution_evidence_0165'")).all()
        expected={'stock_scrap_lines','stock_scrap_files','stock_operation_orders','stock_scrap_recovery_executions',
            'stock_loss_dispositions','stock_loss_correction_executions','stock_loss_disposition_reversals','inventory_transactions',
            'files','audit_events','audit_chain_heads','outbox_events','state_transition_events','notification_events','notification_person_targets'}
        assert {r.relname for r in records}==expected and all(r.tgdeferrable and r.tginitdeferred and r.tgenabled=='A' for r in records)
        signatures = ['rsc_check_scrap_execution_files_0165(uuid)','rsc_check_scrap_posting_events_0165(jsonb,boolean)',
                'rsc_assert_scrap_domain_events_0165(jsonb,text,text,jsonb,uuid)',
                'rsc_check_scrap_execution_events_0165(uuid)','rsc_fence_scrap_execution_evidence_0165()']
        signatures += [name+'('+types+')' for name,types in AUDIT_HELPERS.items()]
        for signature in signatures:
            f=db.execute(text('SELECT prosecdef,proconfig,pg_get_userbyid(proowner) owner FROM pg_proc WHERE oid=CAST(:sig AS regprocedure)'),dict(sig='public.'+signature)).one()
            assert f.prosecdef and f.proconfig==['search_path=pg_catalog, public'] and f.owner=='star_oam_migrator'
            for role in ('star_oam_api','star_oam_projector','star_oam_edge','edge_inbox'):
                assert not db.scalar(text("SELECT has_function_privilege(:role,:sig,'EXECUTE')"),dict(role=role,sig='public.'+signature))
    rows=data['rows'];lines=rows['stock_scrap_lines'];successful=[];rejected=[]
    byid=lambda name,id_:next(r for r in rows[name] if r['id']==id_)
    for line in lines:
        with owner.begin() as db:
            db.execute(text('SELECT public.rsc_check_scrap_execution_events_0165(:id)'),dict(id=line['id']))
        successful.append(line['source_kind']+':complete_execution_evidence')
    baseline=snapshot(owner,metadata)
    def update(db,name,row,**values):
        t=metadata.tables[name];db.execute(t.update().where(t.c.id==row['id']).values(**values))
    def delete(db,name,row):
        t=metadata.tables[name];db.execute(t.delete().where(t.c.id==row['id']))
    def rechain(db):
        # Deliberately forge a coherent chain in this owned adversarial fixture.
        # Domain/file proof must reject wrong evidence even with valid hashes.
        t=metadata.tables['audit_events'];previous=None;last=None
        audits=db.execute(select(t).where(t.c.stream_key=='inventory').order_by(t.c.stream_version)).mappings().all()
        for version,original in enumerate(audits,1):
            row=dict(original);row.update(previous_hash=previous)
            digest=calculate_audit_event_hash(event_id=row['id'],**{k:row[k] for k in (
                'stream_key','actor_user_id','action','aggregate_type','aggregate_id','before_jsonb','after_jsonb',
                'request_id','previous_hash','occurred_at')})
            update(db,'audit_events',row,previous_hash=previous,event_hash=digest,stream_version=version)
            previous=digest;last=row['id']
        head=metadata.tables['audit_chain_heads']
        db.execute(head.update().where(head.c.stream_key=='inventory').values(last_event_id=last,last_hash=previous,version=len(audits)))
    def reject(label,action,*,state='23514',phase='commit',engine=owner,message=None):
        reached='statement'
        try:
            with engine.begin() as db:
                action(db);reached='commit'
        except DBAPIError as error:
            assert error.orig.sqlstate==state,(label,str(error.orig))
            assert phase==reached,(label,reached,str(error.orig))
            if state=='23514':assert 'rsc_' in (error.orig.diag.context or ''),(label,str(error.orig))
            if message:assert message==error.orig.diag.message_primary,(label,str(error.orig))
            assert snapshot(owner,metadata)==baseline,label+': rollback altered ledger'
            rejected.append(dict(case=label,phase=reached,sqlstate=state,allRowsPreserved=True))
        else:raise AssertionError(label+': invalid evidence committed')
    def event(name,aggregate,identifier):
        a,b=('business_type','business_id') if name=='notification_events' else ('aggregate_type','aggregate_id')
        return next(r for r in rows[name] if r[a]==aggregate and r[b]==str(identifier))
    for line in lines:
        kind=line['source_kind'];parent_name='stock_loss_disposition' if kind=='original' else 'stock_loss_correction_execution'
        fact_id=line['root_disposition_id'] if kind=='original' else line['correction_execution_id']
        recovery=next(r for r in rows['stock_scrap_recovery_executions'] if r['scrap_line_id']==line['id'])
        inverse=byid('stock_loss_disposition_reversals',recovery['reversal_id'])
        bundles=[(parent_name,fact_id),('stock_operation_scrap',line['operation_id']),
            ('inventory_transaction',line['posting_transaction_id']),('stock_loss_disposition_reversal',inverse['id']),
            ('stock_scrap_recovery_execution',recovery['id']),('inventory_transaction',inverse['posting_transaction_id'])]
        for aggregate,identifier in bundles:
            out=event('outbox_events',aggregate,identifier);state=event('state_transition_events',aggregate,identifier)
            audit=event('audit_events',aggregate,identifier)
            label=kind+':'+aggregate+':'+str(identifier)[:8]
            reject(label+':missing_outbox',lambda db:delete(db,'outbox_events',out))
            reject(label+':retag_outbox',lambda db:update(db,'outbox_events',out,aggregate_type='synthetic.unrelated',event_type='synthetic.unrelated'))
            reject(label+':duplicate_outbox',lambda db:db.execute(metadata.tables['outbox_events'].insert(),out|dict(id=uuid4(),idempotency_key=uuid4().hex)))
            # Same numeric value but different canonical JSON bytes.
            field='ledger_cursor' if aggregate=='inventory_transaction' else 'authorization_version'
            if field not in out['payload_jsonb']:field='status'
            value=float(out['payload_jsonb'][field]) if field!='status' else 'not_posted'
            reject(label+':wrong_payload',lambda db:update(db,'outbox_events',out,payload_jsonb=out['payload_jsonb']|{field:value}))
            reject(label+':wrong_state',lambda db:update(db,'state_transition_events',state,to_status='forged_posted'))
            reject(label+':wrong_creation_time',lambda db:update(db,'outbox_events',out,created_at=out['created_at']+timedelta(seconds=1)))
            def forged_audit(db):
                update(db,'audit_events',audit,after_jsonb=audit['after_jsonb']|{'forged_extra':True})
                rechain(db)
            reject(label+':coherently_rehashed_audit',forged_audit)
        for aggregate,identifier in [(parent_name,fact_id),('stock_loss_disposition_reversal',inverse['id'])]:
            note=event('notification_events',aggregate,identifier)
            target=next(r for r in rows['notification_person_targets'] if r['event_id']==note['id'])
            reject(kind+':'+aggregate+':wrong_target_manifest',lambda db:update(db,'notification_events',note,target_manifest_sha256='f'*64))
            def remove_target(db):
                t=metadata.tables['notification_person_targets'];db.execute(t.delete().where(t.c.event_id==note['id'],t.c.person_id==target['person_id']))
            reject(kind+':'+aggregate+':missing_target',remove_target)
        for aggregate,identifier in [('stock_operation_scrap',line['operation_id']),('stock_scrap_recovery_execution',recovery['id'])]:
            note=event('notification_events',parent_name,fact_id)
            reject(kind+':'+aggregate+':duplicate_child_notification',lambda db,a=aggregate,i=identifier:db.execute(
                metadata.tables['notification_events'].insert(),note|dict(id=uuid4(),business_type=a,business_id=str(i),dedup_key=uuid4().hex)),
                message='0165 scrap child cannot duplicate parent notification')
        binding=next(r for r in rows['stock_scrap_files'] if r['scrap_line_id']==line['id'])
        file=byid('files',binding['file_id'])
        for action in ('file.upload_intent.created','file.upload_completed'):
            audit=next(r for r in rows['audit_events'] if r['aggregate_type']=='formal_file' and r['aggregate_id']==str(file['id']) and r['action']==action)
            def change_file_audit(db):
                update(db,'audit_events',audit,after_jsonb=audit['after_jsonb']|{'sha256':'f'*64})
                rechain(db)
            reject(kind+':'+action+':rehashed_wrong_content',change_file_audit,
                message='0165 original upload creation and completion audits required')
    # Reverse event admission must reject detached children even when no
    # business fact trigger runs. Test both INSERT and OLD/NEW reassignment.
    template=event('outbox_events','stock_operation_scrap',lines[0]['operation_id'])
    reject('orphan_child_insert',lambda db:db.execute(metadata.tables['outbox_events'].insert(),
        template|dict(id=uuid4(),aggregate_id=str(uuid4()),idempotency_key=uuid4().hex)),message='0165 orphan scrap execution event')
    reject('orphan_child_reassignment',lambda db:update(db,'outbox_events',template,aggregate_id=str(uuid4())))
    reject('private_api_call',lambda db:db.execute(text('SELECT public.rsc_check_scrap_execution_events_0165(:id)'),
        dict(id=lines[0]['id'])),state='42501',phase='statement',engine=api)
    # A client may not supply a forged audit-membership array, even when it
    # knows every real audit UUID. Exercise the call, not just catalog ACLs.
    for name,types in AUDIT_HELPERS.items():
        arguments = ','.join('NULL::'+kind for kind in types.split(',')[:-1])
        statement = 'SELECT public.'+name+'('+arguments+',ARRAY[CAST(:audit AS uuid)])'
        reject('forged_audit_api_call:'+name,lambda db,sql=statement:db.execute(
            text(sql),dict(audit=rows['audit_events'][0]['id'])),
            state='42501',phase='statement',engine=api)
    # Proving a chain earlier in this transaction must not authorize later
    # changed data. Both calls use the original complete entry point.
    def changed_after_proof(db):
        db.execute(text('SELECT public.rsc_check_scrap_execution_events_0165(:id)'),dict(id=lines[0]['id']))
        audit=next(row for row in rows['audit_events'] if row['stream_key']=='inventory')
        update(db,'audit_events',audit,event_hash='f'*64)
        db.execute(text('SELECT public.rsc_check_scrap_execution_events_0165(:id)'),dict(id=lines[0]['id']))
    reject('same_transaction_changed_chain_requires_fresh_proof',changed_after_proof,phase='statement')
    # Successful worker updates force all deferred guards but are rolled back
    # after proving the original immutable snapshot remains recoverable.
    with owner.connect() as db:
        transaction=db.begin()
        try:
            update(db,'outbox_events',template,status='failed',attempts=1,last_error='synthetic provider timeout',
                available_at=template['available_at']+timedelta(minutes=1))
            note=event('notification_events','stock_loss_disposition',lines[0]['root_disposition_id'])
            update(db,'notification_events',note,status='expanded')
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            for line in lines:
                db.execute(text('SELECT public.rsc_check_scrap_execution_events_0165(:id)'),dict(id=line['id']))
        finally:transaction.rollback()
    assert snapshot(owner,metadata)==baseline
    successful.append('worker_retry_state_preserves_history')
    with owner.begin() as locked:
        locked.execute(text('SELECT id FROM inventory_ledger_heads FOR UPDATE')).all()
        with owner.connect() as db:
            transaction=db.begin()
            try:
                db.execute(text("SET LOCAL lock_timeout='200ms'"))
                db.execute(metadata.tables['outbox_events'].insert(),template|dict(id=uuid4(),idempotency_key=uuid4().hex,
                    event_type='synthetic.unrelated',aggregate_type='synthetic',aggregate_id=str(uuid4())))
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            finally:transaction.rollback()
    assert snapshot(owner,metadata)==baseline
    successful.append('unrelated_events_do_not_lock_inventory')
    return dict(status='passed',tracking=data['tracking'],shared=data.get('shared',False),successful=successful,rejected=rejected,
        exactSnapshotPreserved=True,full0164Migration=False,postgresBusinessPosting=False,
        scope='native execution/upload/posting/domain evidence over complete synthetic service-exported model ledgers')
