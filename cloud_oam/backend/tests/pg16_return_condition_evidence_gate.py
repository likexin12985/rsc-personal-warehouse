"""Actual PG16 COMMIT attachment proof, with explicitly synthetic parents."""
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
import re
import runpy
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.return_condition_guards import statements
from formal_file_integrity import _canonical_hash, _storage_key
from pg16_return_condition_invariants_gate import snapshot
from return_condition_evidence_fixture import EvidenceSource, evidence_schema


def run(owner,api):
    folder=Path(__file__).resolve().parents[1]/'alembic'
    candidate=runpy.run_path(str(folder/'return_condition_candidate/evidence.py'))
    refs=candidate['references'](); meta=evidence_schema(refs); meta.create_all(owner)
    migration=folder/'versions/20260831_0026_opening_control_reconciliation.py'
    canonical=re.search(r'CREATE FUNCTION public\.\{PG_CANONICAL_JSON_FUNCTION\}.*?\n\$\$',migration.read_text(),re.S).group()
    canonical=canonical.replace('{PG_CANONICAL_JSON_FUNCTION}','rsc_canonical_reconciliation_json_0026').replace('{{','{').replace('}}','}')
    ddl=statements(identity=True)+candidate['statements']()
    with owner.begin() as db:
        db.execute(text(canonical))
        for sql in ddl: db.execute(text(sql))
        for name in meta.tables:
            db.exec_driver_sql('GRANT SELECT,INSERT,UPDATE,DELETE ON public.'+name+' TO star_oam_api')
    rejected=[]; positive=[]

    def source(mode='none'):
        s=EvidenceSource(meta,mode)
        with owner.begin() as db: s.seed(db)
        return s

    def refuse(name,action,*,at_commit=True,engine=None):
        before=snapshot(owner,meta); completed=False
        try:
            with (engine or api).begin() as db:
                action(db); completed=True
        except DBAPIError as error:
            assert error.orig.sqlstate=='23514',(name,str(error.orig))
            assert not at_commit or completed,(name,'failed before commit',str(error.orig))
            assert 'condition' in str(error.orig),(name,str(error.orig))
            assert snapshot(owner,meta)==before,name+': refusal changed facts'
            rejected.append(dict(name=name,atCommit=completed,sqlstate='23514',allRowsPreserved=True))
        else: raise AssertionError(name+': evidence forgery committed')

    for mode in ('none','lot','serial','lot_and_serial'):
        s=source(mode)
        with api.begin() as db:
            initial=s.claim(db,quantity='1' if s.tracked else '0.375')
            back=s.action(db,initial,'return_evidence','needs_evidence',actor='region')
            supplement=s.action(db,back,'supplement','awaiting_regional')
            region=s.action(db,supplement,'verify_region','awaiting_headquarters',actor='region')
            hq=s.action(db,region,'approve_hq','approved',actor='hq')
            s.action(db,hq,'execute','executed')
        positive.append(mode+':completed_files_rework_review_execute')
        s=source(mode)
        for change in ('pending','purpose','provider','user','person','version','sha','future','after_event','naive_time','completion'):
            def forge(row, change=change):
                m=row['metadata_jsonb']
                if change=='pending': row['status']='pending'; m.pop('completion')
                elif change=='purpose':
                    m['purpose']='stock_loss_evidence'
                    row['storage_key']=m['storage_key']=_storage_key(m['purpose'],row['id'])
                    m['request_sha256']=_canonical_hash({k:row[k] for k in ('original_filename','size_bytes','mime_type','sha256')}|dict(purpose=m['purpose']))
                elif change=='provider': m['provider']='another_provider'
                elif change=='user': row['uploaded_by']=m['uploader_user_id']=s.actors['other'][0]
                elif change=='person': m['uploader_person_id']=str(s.actors['other'][1])
                elif change=='version': m['authorization_version']+=1
                elif change=='sha': row['sha256']='f'*64
                elif change=='future': m['completion']['verified_at']=(row['created_at']+timedelta(days=1)).isoformat()
                elif change=='after_event': m['completion']['verified_at']=(row['created_at']+timedelta(minutes=1,seconds=1)).isoformat()
                elif change=='naive_time': m['completion']['verified_at']=row['created_at'].replace(tzinfo=None).isoformat()
                else: m.pop('completion')
                return row
            s.file_transform=forge
            refuse(mode+':rehash_'+change,lambda db:s.claim(db))
        s.file_transform=lambda row:row
        s.file_count=21
        refuse(mode+':too_many_files',lambda db:s.claim(db))
        s.file_count=1
        with api.begin() as db: initial=s.claim(db)
        identifier=s.files_by_event[initial['id']][0]
        with owner.connect() as db:
            metadata=db.scalar(select(meta.tables['files'].c.metadata_jsonb).where(meta.tables['files'].c.id==identifier))
        before=snapshot(owner,meta)
        s.evidence_files=lambda db,event:[dict(file_id=identifier,metadata_sha256=_canonical_hash(metadata))]
        try:
            with api.begin() as db: s.claim(db,seq=2)
        except DBAPIError as error:
            assert error.orig.sqlstate=='23505' and 'uq_stock_condition_files_file' in str(error.orig)
            assert snapshot(owner,meta)==before
            rejected.append(dict(name=mode+':same_file_second_event',atCommit=False,sqlstate='23505',allRowsPreserved=True))
        else: raise AssertionError('same completed file bound twice')
        for change in ('metadata','sha','status'):
            def late(db,change=change):
                files=meta.tables['files']
                row=dict(db.execute(select(files).where(files.c.id==identifier)).mappings().one())
                if change=='metadata':
                    m=deepcopy(row['metadata_jsonb']); m['completion']['etag_sha256']='e'*64
                    values=dict(metadata_jsonb=m)
                elif change=='sha': values=dict(sha256='e'*64)
                else: values=dict(status='quarantined')
                db.execute(files.update().where(files.c.id==identifier).values(**values))
            refuse(mode+':late_'+change,late)
        def prove_then_change(db):
            db.execute(text('SELECT public.rsc_condition_check_file(:id)'),dict(id=identifier))
            db.execute(meta.tables['files'].update().where(meta.tables['files'].c.id==identifier).values(sha256='e'*64))
        refuse(mode+':proof_then_change',prove_then_change,engine=owner)
        for table,columns in refs.items():
            for column in columns:
                refuse(mode+':foreign_'+table+'.'+column,
                    lambda db,t=table,c=column:db.execute(meta.tables[t].insert(),dict(id=uuid4(),**{c:identifier})),at_commit=False)
        refuse(mode+':daily_manifest_reuse',lambda db:db.execute(meta.tables['daily_review_events'].insert(),
            dict(id=uuid4(),payload_jsonb=dict(evidence=[dict(file_id=str(identifier))]))),at_commit=False)
    s=source(); s.file_count=20
    with api.begin() as db: s.claim(db)
    positive.append('quantity:twenty_completed_files')
    # Existing purposes still pass this new exclusivity layer. Their original
    # domain guards are intentionally not part of the component schema.
    with api.begin() as db:
        for table,columns in refs.items():
            for column in columns:
                db.execute(meta.tables[table].insert(),dict(id=uuid4(),**{column:s.ids['file']}))
        db.execute(meta.tables['daily_review_events'].insert(),dict(id=uuid4(),
            payload_jsonb=dict(evidence=[dict(file_id=str(s.ids['file']))])))
    positive.append('legacy_references_not_reclassified')
    return dict(passed=True,rejected=rejected,positive=positive,ddlSha256=hashlib.sha256('\n'.join(ddl).encode()).hexdigest(),
        foreignReferences=refs,legacyJsonReference='daily_review_events.payload_jsonb.evidence',
        realApiRoleSql=True,actualCommitTested=True,externalParents='minimal synthetic inventory and completed file metadata',
        uploadServiceTested=False,currentActionAuthority=False,physicalEvidenceVerified=False,
        fullHistoricalLedger=False,auditOutboxRequestRecovery=False,formalMigration=False,productionAcceptance=False)
