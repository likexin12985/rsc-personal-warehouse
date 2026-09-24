"""Reviewed material-source authority and locked capture admission, never publication."""
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select

from . import inventory_control_admission as admission
from . import inventory_control_authority as authority
from . import inventory_control_configuration as configuration
from . import inventory_control_preparation as preparation
from . import material_capture_ingress as ingress
from .foundation_models import FileObject, SourceSystem
from .formal_services.audit_chain import append_audit_event, verify_audit_event_in_read_snapshot
from .material_capture_models import MaterialCaptureBinding as Binding, MaterialCaptureReceipt as Receipt
from .material_source_authority_models import MaterialSourceAuthorityDecision as Decision
from .material_master_capture_evidence import FRESHNESS, source_binding


class MaterialSourceAuthorityError(RuntimeError):
    pass


def _fail(code):
    raise MaterialSourceAuthorityError('material_source_authority_' + code)


class MaterialSourceCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    action: Literal['grant','revoke']
    binding_id: UUID
    revoked_grant_id: UUID | None = None
    expected_subject_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence_file_id: UUID
    evidence_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=1,max_length=1000)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    idempotency_key: str = Field(min_length=16,max_length=128,pattern=r'^[A-Za-z0-9._:-]+$')
    request_id: str = Field(min_length=8,max_length=160,pattern=r'^[A-Za-z0-9._:-]+$')

    @model_validator(mode='after')
    def shape(self):
        if not self.reason.strip() or any(getattr(self,n) is not None and getattr(self,n).int==0
                for n in ('binding_id','revoked_grant_id','evidence_file_id')):
            raise ValueError('explicit material source coordinates and reason required')
        if self.action=='grant':
            if self.revoked_grant_id is not None or self.valid_to is None:
                raise ValueError('grant requires an explicit expiry')
        elif self.revoked_grant_id is None or self.valid_from is not None or self.valid_to is not None:
            raise ValueError('revocation is immediate and names one original grant')
        return self


def _micros(value):
    delta=preparation._aware(value)-datetime(1970,1,1,tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def _subject(db, binding_id):
    row=db.get(Binding,binding_id,populate_existing=True)
    if row is None: _fail('binding_not_found')
    # Exclude the mutable revocation timestamp so historical decisions remain
    # provable after revocation. Current availability is checked separately.
    subject=dict(binding_id=str(row.id),source_system_id=str(row.source_system_id),
        wire_binding=source_binding(row.source_instance),key_id=row.key_id,key_fingerprint=row.key_fingerprint,
        registered_at_us=_micros(row.created_at),valid_from_us=_micros(row.valid_from),valid_to_us=_micros(row.valid_to))
    return row,subject


def _available(db,binding,now,*,current=False):
    source=db.get(SourceSystem,binding.source_system_id,populate_existing=True)
    if source is None or source.code!='oam' or source.mode!='read_only' or not source.enabled \
            or binding.revoked_at is not None or now>=preparation._aware(binding.valid_to) \
            or current and now<preparation._aware(binding.valid_from):
        _fail('binding_unavailable')
    return source


def _finish(db,principal,session,claims,settings):
    configuration._finish(db,principal,session,claims,settings,permission_resource='material_source')


def _context(db,access_token,expected_authorization_version,command):
    admission._owner(db)
    if not isinstance(command,MaterialSourceCommand): _fail('invalid_command')
    command=MaterialSourceCommand.model_validate(command.model_dump())
    preparation._begin_outer(db)
    claims=configuration._claims(access_token,configuration.get_settings())
    principal,session,claims,settings=configuration._operator_context(db,claims,expected_authorization_version,
        permission_resource='material_source')
    db.scalar(select(Binding.id).where(Binding.id==command.binding_id).with_for_update())
    _finish(db,principal,session,claims,settings)
    return command,principal,session,claims,settings


def _audit_payload(row):
    return dict(decision_id=str(row.id),action=row.action,binding_id=str(row.binding_id),
        subject_sha256=row.subject_sha256,payload_sha256=row.payload_sha256)


def _prove(db,row,seen=None):
    seen=set() if seen is None else seen
    if row is None or row.id in seen: _fail('decision_graph_invalid')
    seen.add(row.id)
    binding,subject=_subject(db,row.binding_id)
    try:
        payload=row.payload_jsonb
        command=MaterialSourceCommand.model_validate(payload['request'])
        expected=dict(schema_version='rsc.material_source_authority.v1',decision_id=str(row.id),action=row.action,
            subject=subject,subject_sha256=row.subject_sha256,revoked_grant_id=str(row.revoked_grant_id) if row.revoked_grant_id else None,
            actor_user_id=row.actor_user_id,actor_person_id=str(row.actor_person_id),actor_authorization_version=row.actor_authorization_version,
            auth_session_id=row.auth_session_id,request_sha256=row.request_sha256,review_sha256=row.review_sha256,
            created_at=preparation._aware(row.created_at).isoformat(),valid_from=preparation._aware(row.valid_from).isoformat(),
            valid_to=preparation._aware(row.valid_to).isoformat() if row.valid_to else None)
        extras={'request','actor_snapshot','evidence_file','access_issued_at','access_expires_at'}
        if set(payload)!=set(expected)|extras or any(payload.get(k)!=v for k,v in expected.items()) \
                or preparation._sha(payload)!=row.payload_sha256 or preparation._sha(subject)!=row.subject_sha256 \
                or preparation._sha(dict(actor_user_id=row.actor_user_id,request=authority._json(command.model_dump())))!=row.request_sha256 \
                or command.action!=row.action or command.binding_id!=row.binding_id or command.revoked_grant_id!=row.revoked_grant_id \
                or command.evidence_file_id!=row.evidence_file_id or command.evidence_sha256!=row.evidence_sha256 \
                or command.idempotency_key!=row.idempotency_key or command.request_id!=row.request_id \
                or payload['evidence_file']['file_id']!=str(row.evidence_file_id) or payload['evidence_file']['sha256']!=row.evidence_sha256 \
                or payload['actor_snapshot']['user_id']!=row.actor_user_id or payload['actor_snapshot']['person_id']!=str(row.actor_person_id) \
                or payload['actor_snapshot']['authorization_version']!=row.actor_authorization_version:
            _fail('decision_proof_invalid')
        created=preparation._aware(row.created_at);starts=preparation._aware(row.valid_from)
        ends=preparation._aware(row.valid_to) if row.valid_to else None
        if starts<created or ends is not None and ends<=starts \
                or starts!=(preparation._aware(command.valid_from) if command.valid_from else created) \
                or ends!=(preparation._aware(command.valid_to) if command.valid_to else None) \
                or type(payload['access_issued_at']) is not int or type(payload['access_expires_at']) is not int \
                or not payload['access_issued_at']<=created.timestamp()<payload['access_expires_at']:
            _fail('decision_proof_invalid')
        if row.action=='grant':
            if command.expected_subject_sha256!=row.subject_sha256 or starts<preparation._aware(binding.valid_from) \
                    or ends is None or ends>preparation._aware(binding.valid_to): _fail('decision_proof_invalid')
        else:
            parent=_prove(db,db.get(Decision,row.revoked_grant_id,populate_existing=True),seen)
            if parent.action!='grant' or parent.binding_id!=row.binding_id or parent.subject_sha256!=row.subject_sha256 \
                    or command.expected_subject_sha256!=parent.payload_sha256 or preparation._aware(parent.created_at)>created:
                _fail('decision_graph_invalid')
        event=verify_audit_event_in_read_snapshot(db,stream_key='authorization',event_id=row.audit_event_id)
        if event.action!='material_source.authority.'+row.action or event.aggregate_type!='material_source_authority_decision' \
                or event.aggregate_id!=str(row.id) or event.actor_user_id!=row.actor_user_id or event.before_jsonb!={} \
                or event.after_jsonb!=_audit_payload(row) or event.request_id!='material-authority:'+str(row.id) \
                or preparation._aware(event.occurred_at)!=created: _fail('audit_mismatch')
    except (KeyError,TypeError,ValueError,AttributeError):
        _fail('decision_proof_invalid')
    return row


def _history(db,binding_id):
    return tuple(_prove(db,row) for row in db.scalars(select(Decision).where(Decision.binding_id==binding_id)
        .order_by(Decision.created_at,Decision.id).execution_options(populate_existing=True)))


def _review(db,principal,command):
    binding,subject=_subject(db,command.binding_id)
    for model,identifier in ((SourceSystem,binding.source_system_id),(FileObject,command.evidence_file_id)):
        db.scalar(select(model.id).where(model.id==identifier).with_for_update(read=True))
    source=db.get(SourceSystem,binding.source_system_id,populate_existing=True)
    if command.action=='grant': _available(db,binding,authority._now(db))
    file=authority._file(db,command.evidence_file_id,command.evidence_sha256);file.pop('storage_key')
    document=dict(schema_version='rsc.material_source_authority_review.v1',actor_user_id=principal.user_id,
        actor_person_id=str(principal.person_id),actor_authorization_version=principal.authorization_version,
        subject=subject,subject_sha256=preparation._sha(subject),command=authority._json(command.model_dump()),
        source=dict(code=source.code,mode=source.mode,enabled=source.enabled) if source else None,
        transport_revoked_at=authority._json(binding.revoked_at),evidence_file=file,
        history=[[str(row.id),row.payload_sha256] for row in _history(db,binding.id)])
    return dict(review=document,review_sha256=preparation._sha(document),projection_published=False,start_ready=False)


def preview_material_source_authority(db,*,access_token,expected_authorization_version,command):
    command,principal,session,claims,settings=_context(db,access_token,expected_authorization_version,command)
    result=_review(db,principal,command)
    _finish(db,principal,session,claims,settings)
    return result


def _existing(db,principal,command,review_sha256):
    rows=tuple(db.scalars(select(Decision).where(Decision.actor_user_id==principal.user_id,
        or_(Decision.idempotency_key==command.idempotency_key,Decision.request_id==command.request_id)).execution_options(populate_existing=True)))
    if not rows:return None
    digest=preparation._sha(dict(actor_user_id=principal.user_id,request=authority._json(command.model_dump())))
    if len(rows)!=1 or rows[0].request_sha256!=digest or rows[0].review_sha256!=review_sha256: _fail('request_conflict')
    return _prove(db,rows[0])


def _result(row):
    return dict(recorded=True,decision_id=str(row.id),action=row.action,binding_id=str(row.binding_id),
        subject_sha256=row.subject_sha256,payload_sha256=row.payload_sha256,audit_event_id=str(row.audit_event_id),
        valid_from=preparation._aware(row.valid_from).isoformat(),valid_to=preparation._aware(row.valid_to).isoformat() if row.valid_to else None,
        projection_published=False,start_ready=False)


def read_material_source_authority(db,*,access_token,expected_authorization_version,command,review_sha256):
    configuration._require_digest(review_sha256)
    command,principal,session,claims,settings=_context(db,access_token,expected_authorization_version,command)
    row=_existing(db,principal,command,review_sha256)
    _finish(db,principal,session,claims,settings)
    return _result(row) if row else dict(recorded=False,retry_allowed=False,projection_published=False,start_ready=False)


def execute_material_source_authority(db,*,access_token,expected_authorization_version,command,review_sha256):
    admission._owner(db);configuration._require_digest(review_sha256)
    preparation._begin_outer(db)
    with db.begin_nested():
        command,principal,session,claims,settings=_context(db,access_token,expected_authorization_version,command)
        prior=_existing(db,principal,command,review_sha256)
        if prior is not None:
            _finish(db,principal,session,claims,settings)
            return _result(prior)
        if _review(db,principal,command)['review_sha256']!=review_sha256: _fail('review_changed')
        binding,subject=_subject(db,command.binding_id);subject_hash=preparation._sha(subject)
        history=_history(db,binding.id);now=authority._now(db)
        starts=preparation._aware(command.valid_from) if command.valid_from else now
        ends=preparation._aware(command.valid_to) if command.valid_to else None
        if starts<now or ends is not None and ends<=starts: _fail('invalid_validity')
        revoked={row.revoked_grant_id:preparation._aware(row.created_at) for row in history if row.action=='revoke'}
        if command.action=='grant':
            _available(db,binding,now)
            if command.expected_subject_sha256!=subject_hash: _fail('subject_changed')
            if starts<preparation._aware(binding.valid_from) or ends is None or ends>preparation._aware(binding.valid_to):
                _fail('outside_transport_window')
            for previous in history:
                if previous.action!='grant':continue
                end=min(preparation._aware(previous.valid_to),revoked.get(previous.id,preparation._aware(previous.valid_to)))
                if end>starts and ends>preparation._aware(previous.valid_from): _fail('overlapping_grant')
        else:
            parent=next((row for row in history if row.id==command.revoked_grant_id),None)
            if parent is None or parent.action!='grant' or parent.id in revoked or parent.payload_sha256!=command.expected_subject_sha256:
                _fail('revocation_conflict')
        request=authority._json(command.model_dump());request_hash=preparation._sha(dict(actor_user_id=principal.user_id,request=request))
        row=Decision(id=uuid4(),created_at=now,binding_id=binding.id,action=command.action,revoked_grant_id=command.revoked_grant_id,
            subject_sha256=subject_hash,valid_from=starts,valid_to=ends,actor_user_id=principal.user_id,actor_person_id=principal.person_id,
            actor_authorization_version=principal.authorization_version,auth_session_id=session.id,
            evidence_file_id=command.evidence_file_id,evidence_sha256=command.evidence_sha256,
            idempotency_key=command.idempotency_key,request_id=command.request_id,request_sha256=request_hash,review_sha256=review_sha256)
        row.payload_jsonb=dict(schema_version='rsc.material_source_authority.v1',decision_id=str(row.id),action=row.action,
            subject=subject,subject_sha256=subject_hash,revoked_grant_id=str(row.revoked_grant_id) if row.revoked_grant_id else None,
            created_at=now.isoformat(),valid_from=starts.isoformat(),valid_to=ends.isoformat() if ends else None,
            actor_user_id=principal.user_id,actor_person_id=str(principal.person_id),actor_authorization_version=principal.authorization_version,
            actor_snapshot=authority._json(asdict(principal)),auth_session_id=session.id,access_issued_at=claims['iat'],access_expires_at=claims['exp'],
            evidence_file=authority._file(db,command.evidence_file_id,command.evidence_sha256),request=request,request_sha256=request_hash,review_sha256=review_sha256)
        row.payload_sha256=preparation._sha(row.payload_jsonb)
        audit=append_audit_event(db,stream_key='authorization',actor_user_id=principal.user_id,action='material_source.authority.'+row.action,
            aggregate_type='material_source_authority_decision',aggregate_id=str(row.id),request_id='material-authority:'+str(row.id),
            before_jsonb={},after_jsonb=_audit_payload(row),occurred_at=now,created_at=now)
        row.audit_event_id=audit.id;db.add(row);db.flush()
        _finish(db,principal,session,claims,settings)
        if command.action=='grant':
            _available(db,binding,authority._now(db))
            if authority._now(db)>=ends: _fail('expired_during_execution')
        return _result(_prove(db,row))


def inspect_material_source_configuration(db,*,access_token,expected_authorization_version,receipt_id):
    admission._owner(db)
    preparation._begin_outer(db)
    claims=configuration._claims(access_token,configuration.get_settings())
    principal,session,claims,settings=configuration._operator_context(db,claims,expected_authorization_version,
        permission_resource='material_source')
    result=inspect_authorized_material_capture(db,receipt_id=receipt_id)
    _finish(db,principal,session,claims,settings)
    if authority._now(db)>=datetime.fromisoformat(result['valid_until']): _fail('observation_expired')
    return result


def inspect_authorized_material_capture(db,*,receipt_id):
    """Lock and reprove source/grants/files for this transaction, not a reusable permit."""
    admission._owner(db)
    if not isinstance(receipt_id,UUID) or receipt_id.int==0: _fail('invalid_receipt_id')
    row=db.get(Receipt,receipt_id,populate_existing=True)
    if row is None: _fail('receipt_not_found')
    db.scalar(select(Binding.id).where(Binding.id==row.binding_id).with_for_update())
    binding,subject=_subject(db,row.binding_id)
    db.scalar(select(SourceSystem.id).where(SourceSystem.id==binding.source_system_id).with_for_update(read=True))
    _available(db,binding,authority._now(db),current=True)
    report=ingress._prove(row)
    if row.source_instance!=binding.source_instance or row.key_id!=binding.key_id or row.key_fingerprint!=binding.key_fingerprint \
            or preparation._aware(row.capture_started_at)<preparation._aware(binding.valid_from) \
            or preparation._aware(row.created_at)>=preparation._aware(binding.valid_to): _fail('receipt_binding_mismatch')
    rows=_history(db,binding.id);revoked={item.revoked_grant_id for item in rows if item.action=='revoke'}
    def at(instant):
        choices=[item for item in rows if item.action=='grant' and item.id not in revoked
            and preparation._aware(item.valid_from)<=instant<preparation._aware(item.valid_to)]
        if len(choices)!=1: _fail('source_authority_unavailable')
        return choices[0]
    start=preparation._aware(row.capture_started_at);end=preparation._aware(row.created_at)
    points={start,end}
    for grant in rows:
        if grant.action=='grant':
            points.update(preparation._aware(stamp) for stamp in (grant.valid_from,grant.valid_to) if start<preparation._aware(stamp)<end)
    used={};spans=[];ordered=sorted(points)
    for i,instant in enumerate(ordered):
        grant=at(instant);used[grant.id]=grant
        spans.append(dict(started_at=instant.isoformat(),completed_at=ordered[min(i+1,len(ordered)-1)].isoformat(),
            end_inclusive=i==len(ordered)-1,decision_id=str(grant.id),decision_sha256=grant.payload_sha256))
    current=at(authority._now(db));used[current.id]=current
    for grant in sorted(used.values(),key=lambda value:str(value.evidence_file_id)):
        db.scalar(select(FileObject.id).where(FileObject.id==grant.evidence_file_id).with_for_update(read=True))
        if authority._file(db,grant.evidence_file_id,grant.evidence_sha256)!=grant.payload_jsonb['evidence_file']:
            _fail('evidence_file_changed')
    finished=authority._now(db)
    _available(db,binding,finished,current=True)
    if at(finished).id!=current.id: _fail('authority_changed_while_waiting')
    if not start<=finished or finished-start>FRESHNESS: _fail('capture_stale')
    return dict(schema_version='rsc.material_source_admission.v1',receipt_id=str(row.id),capture_id=report.capture_id,
        capture_sha256=report.capture_sha256,observed_count=report.observed_count,subject=subject,
        subject_sha256=preparation._sha(subject),authority_spans=spans,current_decision_id=str(current.id),
        current_decision_sha256=current.payload_sha256,checked_at=finished.isoformat(),
        valid_until=min(start+FRESHNESS,preparation._aware(current.valid_to),preparation._aware(binding.valid_to)).isoformat(),
        channel_attested=True,source_authorized=True,full_catalog_verified=False,
        master_source_evidence_verified=False,projection_published=False,start_ready=False)
