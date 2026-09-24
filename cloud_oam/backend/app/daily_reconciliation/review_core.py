"""Deterministic daily explanation/review rules and generic projection plans.

No I/O and no inventory writes. Snapshots, event histories, live principals and
file observations MUST be read by a trusted database adapter, never from an
HTTP body. Hashes bind content; they do not authenticate a caller or replace
transaction/commit-time authorization and SQL guards. The formal review_service
adapter and migrations 0132/0133 supply that boundary; HTTP commands use the
fixed supervised review_entry worker.
"""
from __future__ import annotations

from datetime import date,datetime,timezone
from decimal import Decimal,localcontext
from typing import Annotated,Literal
from uuid import UUID,uuid5

from pydantic import BaseModel,ConfigDict,Field,TypeAdapter,model_validator,AwareDatetime
from ..formal_access import FormalPrincipal
from .kernel import digest,CONDITIONS,MAX_QTY

Digest=Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
Quantity=Annotated[str,Field(pattern=r'^(0|[1-9][0-9]{0,14})\.[0-9]{3}$')]
SignedQuantity=Annotated[str,Field(pattern=r'^-?(0|[1-9][0-9]{0,14})\.[0-9]{3}$')]
Identifier=Annotated[UUID,Field()]
Version=Annotated[int,Field(strict=True,ge=0,le=9223372036854775807)]

class ReviewError(ValueError):
    pass

def require(ok,code):
    if not ok:raise ReviewError('daily_review_'+code)

def at(value):
    require(isinstance(value,datetime) and value.tzinfo is not None and value.utcoffset() is not None,'aware_time_required')
    return value.astimezone(timezone.utc)

def clean(value,minimum=1,maximum=2000):
    require(type(value) is str and minimum<=len(value)<=maximum and value==value.strip()
            and all(ord(c)>=32 and ord(c)!=127 for c in value),'text_invalid')
    return value

class Frozen(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)

class Comparison(Frozen):
    warehouse_code:str
    material_id:UUID
    condition:Literal['new','used','damaged','scrapped']
    external_qty:Quantity
    local_qty:Quantity
    difference:SignedQuantity
    status:Literal['matched','difference']

    @model_validator(mode='after')
    def check(self):
        clean(self.warehouse_code,maximum=160);require(self.material_id.int!=0,'material_invalid')
        with localcontext() as c:
            c.prec=40
            external,local,difference=map(Decimal,(self.external_qty,self.local_qty,self.difference))
            require(difference==external-local and abs(difference)<=MAX_QTY,'quantity_mismatch')
            require(self.status==('matched' if difference==0 else 'difference'),'comparison_status_mismatch')
            require(self.difference!='-0.000','quantity_noncanonical')
        return self

class CutoffSnapshot(Frozen):
    cutoff_id:UUID
    cutoff_sha256:Digest
    comparison_sha256:Digest
    source_system_id:UUID
    region_org_id:UUID
    business_date:date
    external_snapshot_at:AwareDatetime
    local_ledger_cursor:Version
    comparison_status:Literal['matched','differences']
    items:tuple[Comparison,...]

    @model_validator(mode='after')
    def check(self):
        require(all(v.int for v in (self.cutoff_id,self.source_system_id,self.region_org_id)),'source_identity_invalid')
        at(self.external_snapshot_at)
        keys=[business_key(i) for i in self.items]
        require(len(keys)==len(set(keys)),'duplicate_comparison_key')
        require(self.comparison_status==('differences' if any(i.status=='difference' for i in self.items) else 'matched'),'comparison_status_mismatch')
        return self

class Evidence(Frozen):
    file_id:UUID
    sha256:Digest
    size_bytes:Annotated[int,Field(strict=True,gt=0)]
    mime_type:str
    status:Literal['available']
    accessible:Literal[True]

    @model_validator(mode='after')
    def check(self):
        require(self.file_id.int!=0,'evidence_invalid');clean(self.mime_type,maximum=160)
        return self

class Command(Frozen):
    cutoff_id:UUID
    expected_cutoff_sha256:Digest
    expected_comparison_sha256:Digest
    expected_version:Version
    idempotency_key:Annotated[str,Field(min_length=16,max_length=128,pattern=r'^[A-Za-z0-9._:-]+$')]
    request_id:Annotated[str,Field(min_length=8,max_length=160,pattern=r'^[A-Za-z0-9._:-]+$')]

class Open(Command):
    operation:Literal['open']='open'

class Explanation(Frozen):
    ordinal:Annotated[int,Field(strict=True,ge=1,le=2147483647)]
    expected_item_version:Version
    explanation:Annotated[str,Field(min_length=4,max_length=2000)]
    evidence_file_id:UUID
    evidence_sha256:Digest

    @model_validator(mode='after')
    def check(self):
        clean(self.explanation,4);require(self.evidence_file_id.int!=0,'evidence_invalid')
        return self

class Explain(Command):
    operation:Literal['explain']='explain'
    items:Annotated[tuple[Explanation,...],Field(min_length=1,max_length=100)]

    @model_validator(mode='after')
    def check(self):
        require(len({i.ordinal for i in self.items})==len(self.items),'duplicate_explanation')
        return self

class Review(Command):
    operation:Literal['approve','request_changes']
    comment:Annotated[str,Field(min_length=4,max_length=2000)]
    ordinals:tuple[Annotated[int,Field(strict=True,ge=1,le=2147483647)],...]=()

    @model_validator(mode='after')
    def check(self):
        clean(self.comment,4)
        require(not self.ordinals if self.operation=='approve' else 1<=len(self.ordinals)<=100,'review_selection_invalid')
        require(len(set(self.ordinals))==len(self.ordinals),'duplicate_review_selection')
        return self

Request=Annotated[Open|Explain|Review,Field(discriminator='operation')]
REQUEST=TypeAdapter(Request)

class Actor(Frozen):
    user_id:str
    person_id:UUID
    authorization_version:Annotated[int,Field(strict=True,ge=1)]
    assignment_id:UUID
    role_code:Literal['admin','provincial_manager']
    scope_type:Literal['national','organization']
    scope_id:str
    valid_from:AwareDatetime
    valid_to:AwareDatetime|None

    @model_validator(mode='after')
    def check(self):
        require(str(UUID(self.user_id))==self.user_id and UUID(self.user_id).int!=0
                and self.person_id.int!=0 and self.assignment_id.int!=0,'actor_identity_invalid')
        require(self.valid_to is None or at(self.valid_to)>at(self.valid_from),'grant_interval_invalid')
        return self

class Event(Frozen):
    event_id:UUID
    version:Annotated[int,Field(strict=True,ge=1)]
    previous_sha256:Digest
    snapshot_sha256:Digest
    request:Request
    actor:Actor
    occurred_at:AwareDatetime
    evidence:tuple[Evidence,...]
    sha256:Digest

class ItemState(Frozen):
    ordinal:int
    version:int=0
    explanation:str=''
    evidence:Evidence|None=None
    explained_by_person_id:UUID|None=None
    revision_requested:bool=False
    review_comment:str=''

class State(Frozen):
    version:int
    review_status:Literal['not_recorded','awaiting_explanations','pending_review','changes_requested','approved']
    items:tuple[ItemState,...]
    explanation_authors:tuple[UUID,...]=()
    approved_by_person_id:UUID|None=None
    approval_comment:str=''

class Receipt(Frozen):
    recorded:Literal[True]=True
    event_id:UUID
    event_sha256:Digest
    version:int
    review_status:str
    comparison_status:Literal['matched','differences']
    stock_written:Literal[False]=False

class Outcome(Frozen):
    event:Event
    receipt:Receipt
    replayed:bool

PERMISSIONS={'open':'create_daily','explain':'explain_daily','approve':'approve_daily','request_changes':'approve_daily'}

def business_key(item):
    # Stable, non-ambiguous key fits the baseline generic VARCHAR(300).
    import json
    return json.dumps([item.warehouse_code,str(item.material_id),item.condition],ensure_ascii=False,separators=(',',':'))

def run_id(snapshot):return uuid5(snapshot.cutoff_id,'rsc.daily.review.v1')
def item_id(snapshot,ordinal):return uuid5(snapshot.cutoff_id,'rsc.daily.review.item.v1:'+str(ordinal))

def authorize(principal,snapshot,operation,now):
    return authorize_region(principal,snapshot.region_org_id,operation,now)

def authorize_region(principal,region_org_id,operation,now):
    """Shared live grant rules for commands and bounded read-side action hints."""
    now=at(now)
    require(isinstance(principal,FormalPrincipal) and principal.access_mode=='active'
            and principal.account_status=='active' and principal.employment_status=='active'
            and type(principal.authorization_version) is int and principal.authorization_version>0,'forbidden')
    action=PERMISSIONS[operation] if operation!='read' else 'read'
    require(not any(e.resource=='reconciliation' and e.action in {action,'read'} and e.effect=='deny' for e in principal.entitlements),'forbidden')
    required_roles={'admin','provincial_manager'} if operation in {'open','read'} else {'provincial_manager'} if operation=='explain' else {'admin'}
    grants=[]
    for g in principal.assignments:
        if g.role_code not in required_roles or not (at(g.valid_from)<=now and (g.valid_to is None or now<at(g.valid_to))):continue
        target=('national','*') if g.role_code=='admin' else ('organization',str(region_org_id))
        if (g.scope_type,g.scope_id)!=target:continue
        if all(any(e.assignment_id==g.assignment_id and (e.role_code,e.scope_type,e.scope_id)==(g.role_code,g.scope_type,g.scope_id)
            and e.resource=='reconciliation' and e.action==needed and e.field_code=='' and e.effect=='allow'
            for e in principal.entitlements) for needed in {action,'read'}):grants.append(g)
    # HQ is the deterministic opening/read grant if both internal roles exist.
    if operation in {'open','read'} and any(g.role_code=='admin' for g in grants):grants=[g for g in grants if g.role_code=='admin']
    require(len(grants)==1,'forbidden')
    g=grants[0]
    return Actor(user_id=principal.user_id,person_id=principal.person_id,authorization_version=principal.authorization_version,
        assignment_id=g.assignment_id,role_code=g.role_code,scope_type=g.scope_type,scope_id=g.scope_id,valid_from=g.valid_from,valid_to=g.valid_to)

def request_binding(snapshot,request):
    require(request.cutoff_id==snapshot.cutoff_id and request.expected_cutoff_sha256==snapshot.cutoff_sha256
        and request.expected_comparison_sha256==snapshot.comparison_sha256,'cutoff_binding_changed')

def evidence_by_id(values):
    require(type(values) is tuple and all(isinstance(v,Evidence) for v in values),'evidence_observations_required')
    require(len({v.file_id for v in values})==len(values),'duplicate_evidence')
    return {v.file_id:Evidence.model_validate(v.model_dump()) for v in values}

def step(snapshot,state,request,actor,evidence):
    request_binding(snapshot,request)
    require(request.expected_version==state.version,'version_conflict')
    require(state.review_status!='approved','already_approved')
    files=evidence_by_id(evidence);items=list(state.items);authors=set(state.explanation_authors)
    if request.operation=='open':
        require(state.version==0,'already_open')
        require(not files,'unexpected_evidence')
    else:
        require(state.version>0,'not_open')
        if request.operation=='explain':
            expected_files={entry.evidence_file_id for entry in request.items}
            require(set(files)==expected_files,'evidence_set_mismatch')
            for entry in request.items:
                require(entry.ordinal<=len(items),'item_not_found')
                original=snapshot.items[entry.ordinal-1];current=items[entry.ordinal-1]
                require(original.status=='difference','matched_item_cannot_be_explained')
                require(entry.expected_item_version==current.version,'item_version_conflict')
                file=files[entry.evidence_file_id]
                require(file.sha256==entry.evidence_sha256,'evidence_changed')
                items[entry.ordinal-1]=ItemState(ordinal=entry.ordinal,version=current.version+1,explanation=entry.explanation,
                    evidence=file,explained_by_person_id=actor.person_id)
            authors.add(actor.person_id)
        else:
            require(actor.person_id not in authors,'self_review_forbidden')
            if request.operation=='request_changes':
                require(not files,'unexpected_evidence')
                for ordinal in request.ordinals:
                    require(ordinal<=len(items),'item_not_found');current=items[ordinal-1]
                    require(snapshot.items[ordinal-1].status=='difference' and bool(current.explanation)
                        and not current.revision_requested,'review_item_not_explained')
                    items[ordinal-1]=current.model_copy(update={'revision_requested':True,'review_comment':request.comment,'version':current.version+1})
            else:
                require(all(i.explanation and not i.revision_requested for i,o in zip(items,snapshot.items) if o.status=='difference'),'unexplained_differences')
                expected_files={}
                for item in items:
                    if item.evidence:
                        prior=expected_files.get(item.evidence.file_id)
                        require(prior is None or prior==item.evidence,'evidence_changed')
                        expected_files[item.evidence.file_id]=item.evidence
                require(files==expected_files,'evidence_changed')
                return State(version=state.version+1,review_status='approved',items=tuple(items),explanation_authors=tuple(sorted(authors,key=str)),
                    approved_by_person_id=actor.person_id,approval_comment=request.comment)
    pending=any(not i.explanation for i,o in zip(items,snapshot.items) if o.status=='difference')
    status='changes_requested' if any(i.revision_requested for i in items) else 'awaiting_explanations' if pending else 'pending_review'
    return State(version=state.version+1,review_status=status,items=tuple(items),explanation_authors=tuple(sorted(authors,key=str)))

def snapshot_digest(snapshot):
    value=snapshot.model_dump(mode='json')
    value['external_snapshot_at']=at(snapshot.external_snapshot_at).isoformat()
    return digest(value)

def event_digest(event):return digest(event.model_dump(mode='json',exclude={'sha256'}))
def initial(snapshot):return State(version=0,review_status='not_recorded',items=tuple(ItemState(ordinal=i+1) for i in range(len(snapshot.items))))

def prove_history(snapshot,events):
    require(isinstance(snapshot,CutoffSnapshot) and type(events) is tuple,'trusted_history_required')
    snapshot=CutoffSnapshot.model_validate(snapshot.model_dump())
    source_digest=snapshot_digest(snapshot)
    state=initial(snapshot);previous='0'*64;keys=set();requests=set();last_at=None
    for e in events:
        require(isinstance(e,Event),'event_invalid')
        e=Event.model_validate(e.model_dump())
        now=at(e.occurred_at);a=e.actor
        require(e.snapshot_sha256==source_digest,'source_snapshot_changed')
        require(e.version==state.version+1 and e.previous_sha256==previous and event_digest(e)==e.sha256,'event_chain_invalid')
        require(e.event_id==uuid5(snapshot.cutoff_id,a.user_id+':'+e.request.idempotency_key),'event_identity_invalid')
        require(last_at is None or now>=last_at,'event_time_regressed')
        require(at(a.valid_from)<=now and (a.valid_to is None or now<at(a.valid_to)),'historical_grant_expired')
        role='provincial_manager' if e.request.operation=='explain' else 'admin' if e.request.operation in {'approve','request_changes'} else a.role_code
        target=('national','*') if role=='admin' else ('organization',str(snapshot.region_org_id))
        require(a.role_code==role and (a.scope_type,a.scope_id)==target,'historical_scope_mismatch')
        key=(a.user_id,e.request.idempotency_key);request=(a.user_id,e.request.request_id)
        require(key not in keys and request not in requests,'duplicate_command')
        state=step(snapshot,state,e.request,a,e.evidence)
        keys.add(key);requests.add(request);previous=e.sha256;last_at=now
    return state

def receipt(snapshot,event,state):
    return Receipt(event_id=event.event_id,event_sha256=event.sha256,version=event.version,review_status=state.review_status,comparison_status=snapshot.comparison_status)

def recover(snapshot,events,*,principal,request,now):
    require(isinstance(request,(Open,Explain,Review)),'command_invalid');authorize(principal,snapshot,'read',now)
    request=REQUEST.validate_python(request.model_dump());request_binding(snapshot,request);prove_history(snapshot,events)
    matches=[(i,e) for i,e in enumerate(events) if e.actor.user_id==principal.user_id
        and (e.request.idempotency_key==request.idempotency_key or e.request.request_id==request.request_id)]
    if not matches:return None
    require(len(matches)==1,'request_conflict');i,e=matches[0]
    require(e.request==request and e.actor.person_id==principal.person_id,'request_conflict')
    return receipt(snapshot,e,prove_history(snapshot,events[:i+1]))

def apply(snapshot,events,*,principal,request,now,evidence=()):
    require(isinstance(request,(Open,Explain,Review)),'command_invalid')
    request=REQUEST.validate_python(request.model_dump())
    actor=authorize(principal,snapshot,request.operation,now)
    prior=recover(snapshot,events,principal=principal,request=request,now=now)
    if prior:
        event=next(e for e in events if e.event_id==prior.event_id)
        return Outcome(event=event,receipt=prior,replayed=True)
    state=prove_history(snapshot,events)
    require(not events or at(now)>=at(events[-1].occurred_at),'event_time_regressed')
    updated=step(snapshot,state,request,actor,evidence)
    event=Event(event_id=uuid5(snapshot.cutoff_id,actor.user_id+':'+request.idempotency_key),version=updated.version,
        previous_sha256=events[-1].sha256 if events else '0'*64,snapshot_sha256=snapshot_digest(snapshot),request=request,actor=actor,occurred_at=at(now),evidence=evidence,sha256='0'*64)
    event=event.model_copy(update={'sha256':event_digest(event)})
    return Outcome(event=event,receipt=receipt(snapshot,event,updated),replayed=False)

def projection_plan(snapshot,events):
    """Values for existing generic tables; DB adapter must write atomically.

    Comparison facts remain unchanged even when a difference becomes resolved.
    This plan is not permission to bypass opening-only database guards.
    """
    state=prove_history(snapshot,events);require(state.version>0,'not_open')
    created=events[0].occurred_at;updated=events[-1].occurred_at;rid=run_id(snapshot)
    run=dict(id=rid,run_key='daily-control:'+str(snapshot.cutoff_id),source_system_id=snapshot.source_system_id,
        scope='daily:'+str(snapshot.region_org_id)+':'+snapshot.business_date.isoformat(),external_snapshot_at=snapshot.external_snapshot_at,
        local_ledger_cursor=str(snapshot.local_ledger_cursor),status='approved' if state.review_status=='approved' else snapshot.comparison_status,
        summary_jsonb=dict(schema='rsc.daily_reconciliation.summary.v1',cutoff_id=str(snapshot.cutoff_id),cutoff_sha256=snapshot.cutoff_sha256,
            comparison_sha256=snapshot.comparison_sha256,item_count=len(snapshot.items)),started_at=created,completed_at=created,created_at=created,updated_at=updated)
    touched={i+1:created for i in range(len(snapshot.items))}
    for event in events:
        if event.request.operation=='explain':ordinals=[i.ordinal for i in event.request.items]
        elif event.request.operation=='request_changes':ordinals=event.request.ordinals
        elif event.request.operation=='approve':ordinals=[i+1 for i,r in enumerate(snapshot.items) if r.status=='difference']
        else:continue
        for ordinal in ordinals:touched[ordinal]=event.occurred_at
    items=[]
    for original,current in zip(snapshot.items,state.items):
        status='matched' if original.status=='matched' else 'resolved' if state.review_status=='approved' else 'explained' if current.explanation and not current.revision_requested else 'difference'
        items.append(dict(id=item_id(snapshot,current.ordinal),run_id=rid,business_key=business_key(original),external_qty=Decimal(original.external_qty),
            local_qty=Decimal(original.local_qty),difference=Decimal(original.difference),status=status,explanation=current.explanation,
            evidence_file_id=current.evidence.file_id if current.evidence else None,created_at=created,updated_at=touched[current.ordinal]))
    return run,tuple(items)
