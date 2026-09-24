"""Candidate HQ approval of daily warehouse/location/bucket mappings.

Derived from the current mapping authority service; independent decision table.
No API, inventory posting or daily reconciliation persistence is added here.
"""
from dataclasses import asdict
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select

from app import inventory_control_admission as admission
from app import inventory_control_authority as authority
from app import inventory_control_configuration as configuration
from app import inventory_control_preparation as preparation
from app.foundation_models import FileObject, Organization, SourceSystem
from app.formal_services.audit_chain import append_audit_event, verify_audit_event_in_read_snapshot
from app.inventory_control_models import InventoryControlSourceBinding as Binding
from .mapping_models import DailyMappingDecision as Decision


class ControlMappingError(RuntimeError):
    pass


def _fail(code):
    raise ControlMappingError('control_mapping_' + code)


def _rules(value):
    from .mapping_validation import validate_rules
    return validate_rules(value)


class MappingCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    action: Literal['grant','revoke']
    binding_id: UUID
    catalog_id: UUID
    revoked_grant_id: UUID | None = None
    rules: dict[str,Any] | None = None
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
                for n in ('binding_id','catalog_id','revoked_grant_id','evidence_file_id')):
            raise ValueError('explicit subject and reason required')
        if self.action=='grant':
            if self.rules is None or self.revoked_grant_id is not None:
                raise ValueError('grant requires explicit rules')
            object.__setattr__(self,'rules',_rules(self.rules))
        elif self.rules is not None or self.revoked_grant_id is None or self.valid_from is not None or self.valid_to is not None:
            raise ValueError('revocation names one original grant and is immediate')
        return self


def _context(db,access_token,expected_authorization_version,command):
    admission._owner(db)
    if not isinstance(command,MappingCommand): _fail('invalid_command')
    command=MappingCommand.model_validate(command.model_dump())
    preparation._begin_outer(db)
    claims=configuration._claims(access_token,configuration.get_settings())
    principal,session,claims,settings=configuration._operator_context(db,claims,expected_authorization_version)
    db.scalar(select(Binding.id).where(Binding.id==command.binding_id).with_for_update())
    configuration._finish(db,principal,session,claims,settings)
    return command,principal,session,claims,settings


def _audit_payload(row):
    return dict(decision_id=str(row.id),action=row.action,binding_id=str(row.binding_id),catalog_id=str(row.catalog_id),
                rules_sha256=row.rules_sha256,payload_sha256=row.payload_sha256)


def _prove(db,row,seen=None):
    seen=set() if seen is None else seen
    if row is None or row.id in seen: _fail('decision_graph_invalid')
    seen.add(row.id)
    _,_,subject=authority._subject(db,row.binding_id,row.catalog_id)
    try:
        payload=row.payload_jsonb
        command=MappingCommand.model_validate(payload['request'])
        rules=_rules(row.rules_jsonb)
        expected=dict(schema_version='rsc.daily_comparison_mapping_decision.v1',decision_id=str(row.id),action=row.action,
            subject=subject,rules=rules,rules_sha256=row.rules_sha256,revoked_grant_id=str(row.revoked_grant_id) if row.revoked_grant_id else None,
            actor_user_id=row.actor_user_id,actor_person_id=str(row.actor_person_id),actor_authorization_version=row.actor_authorization_version,
            auth_session_id=row.auth_session_id,request_sha256=row.request_sha256,review_sha256=row.review_sha256,
            created_at=preparation._aware(row.created_at).isoformat(),valid_from=preparation._aware(row.valid_from).isoformat(),
            valid_to=preparation._aware(row.valid_to).isoformat() if row.valid_to else None)
        if any(payload.get(k)!=v for k,v in expected.items()) or preparation._sha(payload)!=row.payload_sha256 \
            or rules!=row.rules_jsonb or rules['revision']!=row.rules_revision or preparation._sha(rules)!=row.rules_sha256 \
            or preparation._sha(dict(actor_user_id=row.actor_user_id,request=authority._json(command.model_dump())))!=row.request_sha256 \
            or command.action!=row.action or command.binding_id!=row.binding_id or command.catalog_id!=row.catalog_id \
            or command.revoked_grant_id!=row.revoked_grant_id or command.idempotency_key!=row.idempotency_key or command.request_id!=row.request_id \
            or command.evidence_file_id!=row.evidence_file_id or command.evidence_sha256!=row.evidence_sha256 \
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
            if command.rules!=rules or command.expected_subject_sha256!=subject['catalog_sha256']:
                _fail('decision_proof_invalid')
        else:
            parent=_prove(db,db.get(Decision,row.revoked_grant_id,populate_existing=True),seen)
            if parent.action!='grant' or parent.binding_id!=row.binding_id or parent.catalog_id!=row.catalog_id \
                or parent.rules_jsonb!=rules or command.expected_subject_sha256!=parent.payload_sha256 \
                or preparation._aware(parent.created_at)>created:
                _fail('decision_graph_invalid')
        event=verify_audit_event_in_read_snapshot(db,stream_key='authorization',event_id=row.audit_event_id)
        if event.action!='daily_reconciliation.mapping.'+row.action or event.aggregate_type!='daily_comparison_mapping_decision' \
            or event.aggregate_id!=str(row.id) or event.actor_user_id!=row.actor_user_id or event.before_jsonb!={} \
            or event.after_jsonb!=_audit_payload(row) or event.request_id!='daily-mapping:'+str(row.id) \
            or preparation._aware(event.occurred_at)!=created:
            _fail('audit_mismatch')
    except (KeyError,TypeError,ValueError):
        _fail('decision_proof_invalid')
    return row


def _history(db,binding_id,catalog_id):
    return tuple(_prove(db,row) for row in db.scalars(select(Decision).where(Decision.binding_id==binding_id,Decision.catalog_id==catalog_id)
        .order_by(Decision.created_at,Decision.id).execution_options(populate_existing=True)))


def _review(db,principal,command):
    binding,catalog,subject=authority._subject(db,command.binding_id,command.catalog_id)
    if command.action=='grant':
        from .mapping_validation import check_directory
        check_directory(db,command.rules,command.binding_id,command.catalog_id)
    for model,identifier in ((SourceSystem,binding.source_system_id),(Organization,binding.region_org_id),(FileObject,command.evidence_file_id)):
        db.scalar(select(model.id).where(model.id==identifier).with_for_update(read=True))
    if command.action=='grant' and not authority._binding_available(db,binding): _fail('binding_unavailable')
    file=authority._file(db,command.evidence_file_id,command.evidence_sha256);file.pop('storage_key')
    document=dict(schema_version='rsc.daily_comparison_mapping_review.v1',actor_user_id=principal.user_id,
        actor_person_id=str(principal.person_id),actor_authorization_version=principal.authorization_version,
        subject=subject,command=authority._json(command.model_dump()),evidence_file=file,
        history=[[str(row.id),row.payload_sha256] for row in _history(db,binding.id,catalog.id)])
    return dict(review=document,review_sha256=preparation._sha(document),projection_published=False,start_ready=False)


def preview_inventory_control_mapping(db,*,access_token,expected_authorization_version,command):
    command,principal,session,claims,settings=_context(db,access_token,expected_authorization_version,command)
    result=_review(db,principal,command)
    configuration._finish(db,principal,session,claims,settings)
    return result


def _existing(db,principal,command,review_sha256):
    rows=tuple(db.scalars(select(Decision).where(Decision.actor_user_id==principal.user_id,
        or_(Decision.idempotency_key==command.idempotency_key,Decision.request_id==command.request_id)).execution_options(populate_existing=True)))
    if not rows:return None
    digest=preparation._sha(dict(actor_user_id=principal.user_id,request=authority._json(command.model_dump())))
    if len(rows)!=1 or rows[0].request_sha256!=digest or rows[0].review_sha256!=review_sha256: _fail('request_conflict')
    return _prove(db,rows[0])


def _result(row):
    return dict(recorded=True,decision_id=str(row.id),action=row.action,rules_revision=row.rules_revision,
        rules_sha256=row.rules_sha256,payload_sha256=row.payload_sha256,audit_event_id=str(row.audit_event_id),
        valid_from=preparation._aware(row.valid_from).isoformat(),valid_to=preparation._aware(row.valid_to).isoformat() if row.valid_to else None,
        projection_published=False,start_ready=False)


def read_inventory_control_mapping(db,*,access_token,expected_authorization_version,command,review_sha256):
    configuration._require_digest(review_sha256)
    command,principal,session,claims,settings=_context(db,access_token,expected_authorization_version,command)
    row=_existing(db,principal,command,review_sha256)
    configuration._finish(db,principal,session,claims,settings)
    return _result(row) if row else dict(recorded=False,projection_published=False,start_ready=False)


def execute_inventory_control_mapping(db,*,access_token,expected_authorization_version,command,review_sha256):
    admission._owner(db);configuration._require_digest(review_sha256)
    preparation._begin_outer(db)
    with db.begin_nested():
        command,principal,session,claims,settings=_context(db,access_token,expected_authorization_version,command)
        prior=_existing(db,principal,command,review_sha256)
        if prior is not None:
            configuration._finish(db,principal,session,claims,settings)
            return _result(prior)
        if _review(db,principal,command)['review_sha256']!=review_sha256: _fail('review_changed')
        binding,catalog,subject=authority._subject(db,command.binding_id,command.catalog_id)
        history=_history(db,binding.id,catalog.id)
        now=authority._now(db);starts=preparation._aware(command.valid_from) if command.valid_from else now
        ends=preparation._aware(command.valid_to) if command.valid_to else None
        if starts<now or ends is not None and ends<=starts: _fail('invalid_validity')
        revoked={row.revoked_grant_id:preparation._aware(row.created_at) for row in history if row.action=='revoke'}
        if command.action=='grant':
            if command.expected_subject_sha256!=catalog.catalog_sha256: _fail('subject_changed')
            rules=command.rules
            for previous in history:
                if previous.action!='grant':continue
                if previous.rules_revision==rules['revision']: _fail('revision_reused')
                possible=[stamp for stamp in (revoked.get(previous.id),preparation._aware(previous.valid_to) if previous.valid_to else None) if stamp is not None]
                end=min(possible) if possible else None
                if (end is None or end>starts) and (ends is None or ends>preparation._aware(previous.valid_from)): _fail('overlapping_grant')
        else:
            parent=next((row for row in history if row.id==command.revoked_grant_id),None)
            if parent is None or parent.action!='grant' or parent.id in revoked or parent.payload_sha256!=command.expected_subject_sha256:
                _fail('revocation_conflict')
            rules=parent.rules_jsonb
        request=authority._json(command.model_dump());request_hash=preparation._sha(dict(actor_user_id=principal.user_id,request=request))
        row=Decision(id=uuid4(),created_at=now,binding_id=binding.id,catalog_id=catalog.id,action=command.action,
            revoked_grant_id=command.revoked_grant_id,rules_revision=rules['revision'],rules_jsonb=rules,rules_sha256=preparation._sha(rules),
            valid_from=starts,valid_to=ends,actor_user_id=principal.user_id,actor_person_id=principal.person_id,
            actor_authorization_version=principal.authorization_version,auth_session_id=session.id,
            evidence_file_id=command.evidence_file_id,evidence_sha256=command.evidence_sha256,
            idempotency_key=command.idempotency_key,request_id=command.request_id,request_sha256=request_hash,review_sha256=review_sha256)
        row.payload_jsonb=dict(schema_version='rsc.daily_comparison_mapping_decision.v1',decision_id=str(row.id),action=row.action,
            subject=subject,rules=rules,rules_sha256=row.rules_sha256,revoked_grant_id=str(row.revoked_grant_id) if row.revoked_grant_id else None,
            created_at=now.isoformat(),valid_from=starts.isoformat(),valid_to=ends.isoformat() if ends else None,
            actor_user_id=principal.user_id,actor_person_id=str(principal.person_id),actor_authorization_version=principal.authorization_version,
            actor_snapshot=authority._json(asdict(principal)),auth_session_id=session.id,access_issued_at=claims['iat'],access_expires_at=claims['exp'],
            evidence_file=authority._file(db,command.evidence_file_id,command.evidence_sha256),request=request,request_sha256=request_hash,review_sha256=review_sha256)
        row.payload_sha256=preparation._sha(row.payload_jsonb)
        audit=append_audit_event(db,stream_key='authorization',actor_user_id=principal.user_id,action='daily_reconciliation.mapping.'+row.action,
            aggregate_type='daily_comparison_mapping_decision',aggregate_id=str(row.id),request_id='daily-mapping:'+str(row.id),
            before_jsonb={},after_jsonb=_audit_payload(row),occurred_at=now,created_at=now)
        row.audit_event_id=audit.id;db.add(row);db.flush()
        configuration._finish(db,principal,session,claims,settings)
        if ends is not None and authority._now(db)>=ends: _fail('expired_during_execution')
        return _result(_prove(db,row))


def resolve_inventory_control_mapping(db,*,binding_id,catalog_id,decision_id):
    admission._owner(db)
    if not isinstance(decision_id,UUID) or decision_id.int==0: _fail('invalid_decision_id')
    db.scalar(select(Binding.id).where(Binding.id==binding_id).with_for_update())
    rows=_history(db,binding_id,catalog_id);now=authority._now(db)
    revoked={row.revoked_grant_id for row in rows if row.action=='revoke'}
    active=[row for row in rows if row.action=='grant' and row.id not in revoked and preparation._aware(row.valid_from)<=now
            and (row.valid_to is None or now<preparation._aware(row.valid_to))]
    if len(active)!=1 or active[0].id!=decision_id: _fail('mapping_unavailable')
    row=active[0]
    from .mapping_validation import check_directory
    check_directory(db,row.rules_jsonb,row.binding_id,row.catalog_id)
    db.scalar(select(FileObject.id).where(FileObject.id==row.evidence_file_id).with_for_update(read=True))
    if authority._file(db,row.evidence_file_id,row.evidence_sha256)!=row.payload_jsonb['evidence_file']: _fail('evidence_file_changed')
    finished=authority._now(db)
    if row.valid_to is not None and finished>=preparation._aware(row.valid_to): _fail('mapping_expired')
    return dict(decision_id=str(row.id),decision_sha256=row.payload_sha256,rules=row.rules_jsonb,rules_sha256=row.rules_sha256,
        audit_event_id=str(row.audit_event_id),valid_until=preparation._aware(row.valid_to).isoformat() if row.valid_to else None)
