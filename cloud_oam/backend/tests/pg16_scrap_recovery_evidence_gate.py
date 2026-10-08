"""Attacks on recovery file/event evidence, with terminal rollback readback."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, text
from app.formal_services.audit_chain import calculate_audit_event_hash
from app.formal_services.stock_scrap import recovery_events
from scrap_recovery_evidence_fixture import insert_events


def run(owner, api, metadata, reject):
    from pg16_scrap_recovery_approval_gate import base, row_for, insert, digest, NAMES
    files = metadata.tables['files']
    checked = []
    for attack in ('pending', 'deleted', 'provider', 'purpose', 'uploader', 'person', 'authorization', 'schema',
            'extra_metadata', 'request_hash', 'content_hash', 'head_hash', 'completion_missing', 'completion_extra',
            'verified_future', 'verified_naive', 'verified_invalid', 'size_zero', 'mime_forged', 'extension', 'path_name'):
        c = base(owner, metadata, 'original')
        app = row_for(c, 'apply')
        identifier = c['data']['ids']['files']
        with owner.begin() as db:
            row = dict(db.execute(select(files).where(files.c.id==identifier)).mappings().one())
            meta = deepcopy(row['metadata_jsonb'])
            values = {}
            if attack in ('pending','deleted'): values['status']=attack
            if attack == 'provider': meta['provider']='mock'
            if attack == 'purpose': meta['purpose']='request_attachment'
            if attack == 'uploader': values['uploaded_by']=c['actors'][1][0]
            if attack == 'person': meta['uploader_person_id']=str(c['actors'][1][1])
            if attack == 'authorization': meta['authorization_version']=2
            if attack == 'schema': meta['schema']='legacy'
            if attack == 'extra_metadata': meta['unverified']=True
            if attack == 'request_hash': meta['request_sha256']='f'*64
            if attack == 'content_hash': values['sha256']='f'*64
            if attack == 'head_hash': meta['completion']['head_manifest_sha256']='f'*64
            if attack == 'completion_missing': del meta['completion']
            if attack == 'completion_extra': meta['completion']['override']=True
            if attack == 'verified_future': meta['completion']['verified_at']=(app['created_at']+timedelta(seconds=1)).isoformat()
            if attack == 'verified_naive': meta['completion']['verified_at']=app['created_at'].replace(tzinfo=None).isoformat()
            if attack == 'verified_invalid': meta['completion']['verified_at']='not-a-timeZ'
            if attack == 'size_zero': values['size_bytes']=0
            if attack == 'mime_forged': values['mime_type']='text/plain'
            if attack == 'extension': values['original_filename']='伪造附件.pdf'
            if attack == 'path_name': values['original_filename']='../伪造附件.png'
            if values:
                # Recompute upload intent to avoid relying only on a stale
                # checksum when testing MIME, contents and original completion.
                changed=row|values
                meta['request_sha256']=digest({k:changed[k] for k in ('mime_type','original_filename','sha256','size_bytes')}|
                    {'purpose':'stock_loss_evidence'})
            db.execute(files.update().where(files.c.id==identifier).values(**values,metadata_jsonb=meta))
        # insert() recomputes the binding digest from this exact bad metadata.
        reject('file:'+attack,lambda db:insert(db,metadata,'apply',app))
        checked.append('file:'+attack)

    for stage in ('apply','regional','headquarters'):
        for attack in ('omit_audit','omit_state','omit_outbox','omit_notification','omit_target',
                'wrong_payload','wrong_actor','wrong_state','wrong_target','wrong_manifest','wrong_audit_hash','extra_outbox',
                'numeric_note','numeric_state','numeric_outbox','numeric_audit'):
            c=base(owner,metadata,'correction')
            app=row_for(c,'apply')
            region=row_for(c,'regional',application=app,offset=2)
            hq=row_for(c,'headquarters',application=app,regional=region,offset=3)
            if stage!='apply':
                with api.begin() as db:insert(db,metadata,'apply',app)
            if stage=='headquarters':
                with api.begin() as db:insert(db,metadata,'regional',region)
            fact={'apply':app,'regional':region,'headquarters':hq}[stage]
            omit={'omit_audit':'audit_events','omit_state':'state_transition_events','omit_outbox':'outbox_events',
                'omit_notification':('notification_events','notification_person_targets'),
                'omit_target':'notification_person_targets'}.get(attack)
            def mutate(rows):
                if attack.startswith('numeric_'):
                    name,field={'numeric_note':('notification_events','payload_jsonb'),
                        'numeric_outbox':('outbox_events','payload_jsonb'),'numeric_state':('state_transition_events','metadata_jsonb'),
                        'numeric_audit':('audit_events','after_jsonb')}[attack]
                    rows[name][field]=dict(rows[name][field],authorization_version=1.0)
                    if name=='audit_events':
                        audit=rows[name]
                        audit['event_hash']=calculate_audit_event_hash(event_id=audit['id'], **{
                            k:v for k,v in audit.items() if k not in ('id','created_at','stream_version','event_hash')})
                if attack=='wrong_payload':rows['outbox_events']['payload_jsonb']={'stock_effect':'restored'}
                if attack=='wrong_actor':rows['state_transition_events']['actor_id']=c['actors'][3][0]
                if attack=='wrong_state':rows['state_transition_events']['to_status']='inventory_restored'
                if attack=='wrong_target':rows['notification_person_targets']['person_id']=c['actors'][3][1]
                if attack=='wrong_manifest':rows['notification_events']['target_manifest_sha256']='f'*64
                if attack=='wrong_audit_hash':rows['audit_events']['event_hash']='f'*64
            def write(db):
                insert(db,metadata,stage,fact,omit=omit,mutate=mutate)
                if attack=='extra_outbox':
                    table=metadata.tables['outbox_events']
                    row=dict(db.execute(select(table).where(table.c.aggregate_id==str(fact['id']))).mappings().one())
                    row.update(id=uuid4(),idempotency_key=uuid4().hex)
                    db.execute(table.insert(),row)
            reject('event:'+stage+':'+attack,write)
            checked.append('event:'+stage+':'+attack)

    # A complete-looking event set without an application is rejected by the
    # reverse event -> request trigger even though no request trigger fires.
    c=base(owner,metadata,'original');app=row_for(c,'apply')
    reject('orphan:complete_event_bundle',lambda db:insert_events(db,metadata,'apply',app))
    checked.append('orphan:complete_event_bundle')
    # Changing an unrelated row INTO a recovery event must also validate NEW.
    with owner.begin() as db:
        table=metadata.tables['outbox_events'];identifier=uuid4();at=app['created_at']
        db.execute(table.insert(),dict(id=identifier,event_type='synthetic.unrelated',aggregate_type='synthetic',aggregate_id=str(uuid4()),
            payload_jsonb={},idempotency_key=uuid4().hex,available_at=at,created_at=at,updated_at=at,status='pending',attempts=0))
    aggregate,kind,_=recovery_events.coordinates(app,'apply')
    reject('orphan:update_into_recovery',lambda db:db.execute(table.update().where(table.c.id==identifier).values(
        event_type=kind,aggregate_type=aggregate,aggregate_id=str(app['id']))),role=owner)
    checked.append('orphan:update_into_recovery')
    reject('orphan:wrong_aggregate_for_event_kind',lambda db:db.execute(table.update().where(table.c.id==identifier).values(event_type=kind)),role=owner)
    checked.append('orphan:wrong_aggregate_for_event_kind')

    c=base(owner,metadata,'original');app=row_for(c,'apply')
    with api.begin() as db:insert(db,metadata,'apply',app)
    for name,column in [('outbox_events','aggregate_id'),('state_transition_events','aggregate_id'),('notification_events','business_id')]:
        table=metadata.tables[name];condition=table.c[column]==str(app['id'])
        def delete(db):
            if name=='notification_events':
                targets=metadata.tables['notification_person_targets']
                db.execute(targets.delete().where(targets.c.event_id.in_(select(table.c.id).where(condition))))
            db.execute(table.delete().where(condition))
        reject('immutable_event:'+name,delete,role=owner)
        checked.append('immutable_event:'+name)
    # Worker state changes are deliberately independent of the historical
    # approval facts, and must remain usable even after failed delivery.
    with owner.begin() as db:
        outbox=metadata.tables['outbox_events'];note=metadata.tables['notification_events']
        db.execute(outbox.update().where(outbox.c.aggregate_id==str(app['id'])).values(status='failed',attempts=1,
            available_at=app['created_at']+timedelta(minutes=1),last_error='synthetic provider timeout'))
        db.execute(note.update().where(note.c.business_id==str(app['id'])).values(status='expanded'))
    # New guards must not take the inventory lock for unrelated event work.
    with owner.begin() as locked:
        locked.execute(text('SELECT id FROM inventory_ledger_heads FOR UPDATE')).all()
        with owner.begin() as db:
            db.execute(text("SET LOCAL lock_timeout='200ms'"))
            table=metadata.tables['outbox_events'];at=app['created_at']
            db.execute(table.insert(),dict(id=uuid4(),event_type='synthetic.unrelated',aggregate_type='synthetic',aggregate_id=str(uuid4()),
                payload_jsonb={},idempotency_key=uuid4().hex,available_at=at,created_at=at,updated_at=at,status='pending',attempts=0))
    for attack in ('approval_key','approval_document'):
        c=base(owner,metadata,'original');application=row_for(c,'apply')
        aggregate,_,_=recovery_events.coordinates(application,'apply')
        def post(db):
            insert(db,metadata,'apply',application)
            db.execute(metadata.tables['inventory_transactions'].insert(),dict(id=uuid4(),
                idempotency_key_hash=application['idempotency_key_hash'] if attack=='approval_key' else 'e'*64,
                source_document_type=aggregate if attack=='approval_document' else 'synthetic',source_document_id=str(application['id'])))
        reject('approval_posts_stock:'+attack,post,role=owner,message='0165 recovery approval cannot post stock')
        checked.append('approval_posts_stock:'+attack)
    for signature in ('rsc_check_scrap_recovery_files_0165(uuid)',
            'rsc_check_scrap_recovery_events_0165(jsonb,text,uuid)','rsc_fence_scrap_recovery_events_0165()'):
        with owner.connect() as db:
            for role in ('star_oam_api','star_oam_projector','star_oam_edge','edge_inbox'):
                assert not db.scalar(text('SELECT has_function_privilege(:role,:signature,\'EXECUTE\')'),
                    dict(role=role,signature='public.'+signature))
    return dict(rejectedEvidenceCases=len(checked),cases=checked,deliveryStateChangesPreserveApproval=True,unrelatedEventCommitsWhileInventoryLocked=True,
        fileRowsAndEventSchemas='real columns and constraints; synthetic upload and audit data',
        fullStockAndCurrentAuthority=False)
