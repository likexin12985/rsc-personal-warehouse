"""Formal daily review persistence using the API identity, never owner DSNs.

The supervised HTTP worker commits/rolls back its one outer transaction. Event
history and source are server reads; JWT and current principal are revalidated.
"""
import json
from uuid import UUID,uuid4
from sqlalchemy import select,text,insert,update
from app.models import AuthSession
from app.foundation_models import FileObject,ReconciliationRun,ReconciliationItem,StateTransitionEvent
from app.formal_access import lock_formal_principal_graph,load_formal_principal
from app import inventory_control_configuration as authentication
from app.daily_reconciliation import review_core as core
from app.daily_reconciliation.query_service import REPORTS
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.formal_files import is_available_formal_file_for_purpose

def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def now(db):return db.scalar(text('SELECT clock_timestamp()'))

def source(db,cutoff_id):
    r=db.execute(select(REPORTS).where(REPORTS.c.cutoff_id==cutoff_id)).mappings().one_or_none()
    core.require(r is not None,'not_found')
    return core.CutoffSnapshot(cutoff_id=r['cutoff_id'],cutoff_sha256=r['cutoff_sha256'],comparison_sha256=r['comparison_sha256'],
        source_system_id=r['source_system_id'],region_org_id=r['region_org_id'],business_date=r['business_date'],
        external_snapshot_at=r['source_captured_at'],local_ledger_cursor=r['local_ledger_cursor'],comparison_status=r['comparison_status'],items=r['comparison_items'])

def context(db,token,expected_version,request,operation):
    request=core.REQUEST.validate_python(request.model_dump())
    return (request,*authority_context(db,token,expected_version,request.cutoff_id,request.request_id,operation))

def authority_context(db,token,expected_version,cutoff_id,trace_request_id,operation):
    core.require(db.bind.dialect.name=='postgresql' and not db.new and not db.dirty and not db.deleted,'clean_postgresql_session_required')
    core.require(db.scalar(text('SHOW transaction_isolation'))=='read committed','current_snapshot_required')
    core.require(db.scalar(text('SELECT session_user'))=='star_oam_api','api_role_required')
    settings=authentication.get_settings();claims=authentication._claims(token,settings)
    session=db.scalar(select(AuthSession).where(AuthSession.id==claims['sid']).with_for_update().execution_options(populate_existing=True))
    lock_formal_principal_graph(db,[claims['sub']]);stamp=now(db)
    authentication._session_valid(session,claims,settings,stamp)
    principal=load_formal_principal(db,claims['sub'],now=stamp)
    core.require(type(expected_version) is int and principal.authorization_version==expected_version,'authorization_changed')
    snap=source(db,cutoff_id);core.authorize(principal,snap,operation,stamp)
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'daily-review:'+str(snap.cutoff_id)})
    db.execute(text('SELECT run_id FROM daily_review_bindings WHERE cutoff_id=:id FOR UPDATE'),{'id':snap.cutoff_id})
    # One trace namespace across cutoffs, shared with the SQL insertion guards.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
        {'key':'daily-review-request:'+principal.user_id+':'+trace_request_id})
    return snap,principal,session,claims,settings

def history(db,snap):
    events=tuple(core.Event.model_validate(v) for v in db.scalars(text('SELECT payload_jsonb FROM daily_review_events WHERE cutoff_id=:id ORDER BY version'),{'id':snap.cutoff_id}))
    core.prove_history(snap,events)
    return events

def prior(db,snap,events,principal,request):
    # Look up keys across every run before considering a new command.
    rows=db.execute(text('SELECT id,cutoff_id FROM daily_review_events WHERE actor_user_id=:actor AND (idempotency_key=:key OR request_id=:request)'),
        {'actor':principal.user_id,'key':request.idempotency_key,'request':request.request_id}).all()
    core.require(not rows or len(rows)==1 and rows[0].cutoff_id==snap.cutoff_id,'request_conflict')
    return core.recover(snap,events,principal=principal,request=request,now=now(db))

def finish(db,snap,principal,session,claims,settings,operation):
    stamp=now(db);authentication._session_valid(session,claims,settings,stamp)
    current=load_formal_principal(db,principal.user_id,now=stamp)
    core.require(current==principal,'authorization_changed');core.authorize(current,snap,operation,stamp)

def recover(db,*,access_token,expected_authorization_version,command):
    command,snap,p,s,claims,settings=context(db,access_token,expected_authorization_version,command,'read')
    result=prior(db,snap,history(db,snap),p,command)
    from .recovery_service import require_unsealed
    if result is None:require_unsealed(db,p,command.request_id)
    finish(db,snap,p,s,claims,settings,'read')
    return result.model_dump(mode='json') if result else {'recorded':False,'stock_written':False}

def prepare(db,*,access_token,expected_authorization_version,command):
    command,snap,p,s,claims,settings=context(db,access_token,expected_authorization_version,command,command.operation)
    events=history(db,snap);existing=prior(db,snap,events,p,command)
    if existing:
        finish(db,snap,p,s,claims,settings,command.operation)
        return {'replayed':True,'receipt':existing.model_dump(mode='json')}
    from .recovery_service import require_unsealed
    require_unsealed(db,p,command.request_id)
    state=core.prove_history(snap,events)
    ids={i.evidence_file_id for i in command.items} if command.operation=='explain' else {
        i.evidence.file_id for i in state.items if i.evidence} if command.operation=='approve' else set()
    files=tuple(db.scalars(select(FileObject).where(FileObject.id.in_(ids)).order_by(FileObject.id).with_for_update(read=True).execution_options(populate_existing=True))) if ids else ()
    core.require({f.id for f in files}==ids,'evidence_missing')
    core.require(command.operation!='explain' or all(f.uploaded_by==p.user_id for f in files),'evidence_not_owned')
    core.require(all(is_available_formal_file_for_purpose(f,purpose='daily_reconciliation_evidence',uploader_user_id=p.user_id if command.operation=='explain' else None) for f in files),'evidence_purpose_invalid')
    observations=tuple(core.Evidence(file_id=f.id,sha256=f.sha256,size_bytes=f.size_bytes,mime_type=f.mime_type,status=f.status,accessible=True) for f in files)
    outcome=core.apply(snap,events,principal=p,request=command,now=now(db),evidence=observations)
    after=events+(outcome.event,);run,items=core.projection_plan(snap,after)
    return dict(replayed=False,snapshot=snap,principal=p,session=s,claims=claims,settings=settings,events=after,
        outcome=outcome,run=run,items=items,state=core.prove_history(snap,after),prior_state=state)

def record(db,prepared):
    """Internal append stage; SQL independently validates the prepared command."""
    if prepared['replayed']:return prepared['receipt']
    v=prepared;e=v['outcome'].event;snap=v['snapshot'];run=v['run'];items=v['items'];rid=run['id']
    insert_event(db,event_values(v))
    binding=dict(run=rid,cutoff=snap.cutoff_id,version=e.version,last=e.event_id,item_ids=encode([str(i['id']) for i in items]),
        state=encode(v['state'].model_dump(mode='json')),created=run['created_at'],updated=e.occurred_at)
    if e.version==1:
        db.execute(text('INSERT INTO daily_review_bindings (run_id,cutoff_id,version,last_event_id,item_ids,state_jsonb,created_at,updated_at) VALUES (:run,:cutoff,:version,:last,CAST(:item_ids AS jsonb),CAST(:state AS jsonb),:created,:updated)'),binding)
        db.execute(insert(ReconciliationRun).values(**run))
        if items:db.execute(insert(ReconciliationItem),list(items))
    else:
        db.execute(text('UPDATE daily_review_bindings SET version=:version,last_event_id=:last,state_jsonb=CAST(:state AS jsonb),updated_at=:updated WHERE run_id=:run'),binding)
        db.execute(update(ReconciliationRun).where(ReconciliationRun.id==rid).values(status=run['status'],updated_at=run['updated_at']))
        for item in items:
            if item['updated_at']==e.occurred_at:
                db.execute(update(ReconciliationItem).where(ReconciliationItem.id==item['id']).values(**{k:item[k] for k in ('status','explanation','evidence_file_id','updated_at')}))
    old=v['prior_state'].review_status if e.version>1 else None
    transition_id=None
    if old!=v['state'].review_status:
        transition_id=uuid4()
        db.add(StateTransitionEvent(id=transition_id,aggregate_type='daily_reconciliation_run',aggregate_id=str(rid),from_status=old,
            to_status=v['state'].review_status,reason='daily_reconciliation.'+e.request.operation,actor_id=e.actor.user_id,
            idempotency_key='daily-review:'+str(e.event_id),occurred_at=e.occurred_at,created_at=e.occurred_at,
            metadata_jsonb=dict(event_id=str(e.event_id),event_sha256=e.sha256)));db.flush()
    # Audit is the final acquired lock; SQL deferred checks read held graphs.
    event=append_audit_event(db,stream_key='authorization',actor_user_id=e.actor.user_id,action='daily_reconciliation.'+e.request.operation,
        aggregate_type='daily_reconciliation_run',aggregate_id=str(rid),request_id='daily-review:'+str(e.event_id),before_jsonb=dict(version=e.version-1),
        after_jsonb=dict(event_id=str(e.event_id),event_sha256=e.sha256,receipt=v['outcome'].receipt.model_dump(mode='json')),
        occurred_at=e.occurred_at,created_at=e.occurred_at)
    db.execute(text('INSERT INTO daily_review_consumptions (event_id,audit_event_id,transition_event_id,transaction_id) VALUES (:event,:audit,:transition,txid_current())'),{'event':e.event_id,'audit':event.id,'transition':transition_id})
    finish(db,snap,v['principal'],v['session'],v['claims'],v['settings'],e.request.operation)
    return v['outcome'].receipt.model_dump(mode='json')

def execute(db,**kwargs):return record(db,prepare(db,**kwargs))

def event_values(v):
    e=v['outcome'].event;snap=v['snapshot'];rid=v['run']['id']
    values=dict(id=e.event_id,run_id=rid,cutoff_id=snap.cutoff_id,version=e.version,actor_user_id=e.actor.user_id,
        actor_person_id=e.actor.person_id,auth_session_id=v['session'].id,access_issued_at=v['claims']['iat'],access_expires_at=v['claims']['exp'],
        idempotency_key=e.request.idempotency_key,request_id=e.request.request_id,payload_jsonb=encode(e.model_dump(mode='json')),
        after_state_jsonb=encode(v['state'].model_dump(mode='json')),receipt_jsonb=encode(v['outcome'].receipt.model_dump(mode='json')),
        payload_sha256=e.sha256,created_at=e.occurred_at)
    return values

def insert_event(db,values):
    columns=list(values);placeholders=[f'CAST(:{x} AS jsonb)' if x.endswith('jsonb') else ':'+x for x in columns]
    db.execute(text('INSERT INTO daily_review_events ('+','.join(columns)+',transaction_id) VALUES ('+','.join(placeholders)+',txid_current())'),values)
