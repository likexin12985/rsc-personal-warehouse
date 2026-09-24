"""Owner-side reviewed OAM material projection; caller owns the transaction.

Only exact authenticated receipts are inputs. Each receipt row needs an explicit
typed semantic decision. This publishes no inventory, control totals or outbox.
Existing unmanaged SKUs and changes to established units/policies require a
separate migration. Absence from a visible capture never deletes a material.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from sqlalchemy import or_, select, text
from sqlalchemy.orm.attributes import flag_modified

from . import inventory_control_admission as admission
from . import inventory_control_authority as authority
from . import inventory_control_configuration as configuration
from . import inventory_control_preparation as preparation
from . import material_capture_ingress as ingress
from . import material_source_authority as source
from .foundation_models import ExternalObject, ExternalObjectVersion, FileObject
from .formal_services.audit_chain import append_audit_event, verify_audit_event_in_read_snapshot
from .inventory_models import FormalMaterial, MaterialInventoryPolicy
from .material_capture_models import MaterialCaptureReceipt as Receipt
from .material_master_capture_evidence import digest
from .material_projection_models import MaterialProjectionPublication as Publication, MaterialProjectionLine as Line


class MaterialProjectionError(RuntimeError):
    pass


def _fail(code):
    raise MaterialProjectionError('material_projection_' + code)


class MaterialSemanticDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    sku_code: str = Field(pattern=r'^[A-Z0-9][A-Z0-9._/-]{0,79}$')
    raw_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    status: Literal['active', 'inactive']
    base_unit: str = Field(min_length=1, max_length=32)
    tracking_mode: Literal['none', 'lot', 'serial', 'lot_and_serial']
    quantity_scale: StrictInt = Field(ge=0, le=3)
    allow_fraction: StrictBool

    @model_validator(mode='after')
    def valid_semantics(self):
        if self.base_unit != self.base_unit.strip() or any(ord(c)<32 or ord(c)==127 for c in self.base_unit):
            raise ValueError('explicit clean base unit required')
        if self.allow_fraction != (self.quantity_scale > 0) or self.tracking_mode in ('serial', 'lot_and_serial') and self.allow_fraction:
            raise ValueError('fraction and serial tracking rules conflict')
        return self


class MaterialPublicationCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    receipt_id: UUID
    capture_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    decisions: tuple[MaterialSemanticDecision, ...] = Field(min_length=1, max_length=1000)
    evidence_file_id: UUID
    evidence_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')
    request_id: str = Field(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$')

    @model_validator(mode='after')
    def exact_set(self):
        codes = [row.sku_code for row in self.decisions]
        if codes != sorted(set(codes)) or self.receipt_id.int==0 or self.evidence_file_id.int==0 or not self.reason.strip():
            raise ValueError('exact ordered unique decisions, evidence and reason required')
        return self


def _context(db, access_token, expected_authorization_version, command):
    admission._owner(db)
    if not isinstance(command, MaterialPublicationCommand): _fail('invalid_command')
    command = MaterialPublicationCommand.model_validate(command.model_dump())
    preparation._begin_outer(db)
    claims = configuration._claims(access_token, configuration.get_settings())
    principal, session, claims, settings = configuration._operator_context(db, claims, expected_authorization_version,
        permission_resource='material_source')
    # All material publications from any binding use deterministic SKU locks.
    # Read-only catalogue queries do not take these locks.
    if db.get_bind().dialect.name == 'postgresql':
        for row in command.decisions:
            db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                       {'key':'rsc.material-publication.sku:' + row.sku_code})
    source._finish(db, principal, session, claims, settings)
    return command, principal, session, claims, settings


def _observation(db, receipt_id):
    result = source.inspect_authorized_material_capture(db, receipt_id=receipt_id)
    # A review binds a finite authorization interval, not one CPU clock reading.
    result.pop('checked_at')
    return result


def _normalized(raw, decision):
    if raw['materialCode'] != decision.sku_code or digest(raw) != decision.raw_sha256:
        _fail('semantic_row_mismatch')
    return dict(sku_code=decision.sku_code, name=raw['materialName'], specification=raw.get('regularModel') or '',
                base_unit=decision.base_unit, status=decision.status, source_updated_at=None)


def _policy(decision):
    return {key:getattr(decision, key) for key in ('tracking_mode', 'quantity_scale', 'allow_fraction')}


def _proof_line(db, line, *, current=False):
    """Reconstruct immutable facts and their current/closed version coordinates."""
    if line is None: _fail('line_missing')
    payload = line.payload_jsonb
    pub = db.get(Publication, line.publication_id, populate_existing=True)
    version = db.get(ExternalObjectVersion, line.version_id, populate_existing=True)
    obj = db.get(ExternalObject, line.external_object_id, populate_existing=True)
    material = db.get(FormalMaterial, line.material_id, populate_existing=True)
    policy = db.get(MaterialInventoryPolicy, line.policy_id, populate_existing=True)
    try:
        receipt = db.get(Receipt, pub.receipt_id, populate_existing=True)
        command = MaterialPublicationCommand.model_validate(pub.payload_jsonb['request'])
        raw = receipt.payload_jsonb['capture']['records'][line.sequence-1]['data']
        decision = command.decisions[line.sequence-1]
        normalized = _normalized(raw, decision)
        expected_version = dict(schema_version='rsc.reviewed_material_observation.v1', receipt_id=str(receipt.id),
            capture_id=str(receipt.capture_id), raw=raw, semantics=decision.model_dump(), source_updated_at=None)
        expected = dict(line_id=str(line.id), publication_id=str(pub.id), sequence=line.sequence,
            material_id=str(line.material_id), external_object_id=str(line.external_object_id), version_id=str(line.version_id),
            policy_id=str(line.policy_id), previous_line_id=str(line.previous_line_id) if line.previous_line_id else None,
            normalized=normalized, policy=_policy(decision), version_payload=expected_version)
        if payload != expected or digest(payload)!=line.payload_sha256 or line.sku_code!=decision.sku_code \
                or obj.source_system_id!=UUID(pub.payload_jsonb['source']['subject']['source_system_id']) \
                or obj.entity_type!='material' or obj.external_id!=line.sku_code or obj.deleted_at is not None \
                or material.external_object_id!=obj.id or material.sku_code!=line.sku_code \
                or version.external_object_id!=obj.id or version.source_version!='capture-v1:'+str(receipt.capture_id) \
                or version.source_updated_at is not None or version.payload_jsonb!=expected_version \
                or version.payload_sha256!=digest(expected_version) or preparation._aware(version.valid_from)!=preparation._aware(pub.created_at) \
                or preparation._aware(version.created_at)!=preparation._aware(pub.created_at) \
                or policy.material_id!=material.id or policy.effective_to is not None \
                or len(tuple(db.scalars(select(MaterialInventoryPolicy.id).where(MaterialInventoryPolicy.material_id==material.id))))!=1 \
                or {key:getattr(policy,key) for key in _policy(decision)}!=_policy(decision):
            _fail('line_proof_invalid')
        previous = db.get(Line, line.previous_line_id, populate_existing=True) if line.previous_line_id else None
        if previous:
            prior = db.get(Publication, previous.publication_id, populate_existing=True)
            prior_receipt = db.get(Receipt, prior.receipt_id, populate_existing=True)
            if (previous.material_id,previous.external_object_id,previous.policy_id)!=(line.material_id,line.external_object_id,line.policy_id) \
                    or previous.payload_jsonb['normalized']['base_unit']!=normalized['base_unit'] \
                    or prior_receipt.source_instance!=receipt.source_instance \
                    or preparation._aware(prior_receipt.capture_completed_at)>=preparation._aware(receipt.capture_started_at) \
                    or preparation._aware(prior.created_at)>=preparation._aware(pub.created_at):
                _fail('lineage_invalid')
        elif any(preparation._aware(value)!=preparation._aware(pub.created_at)
                 for value in (policy.effective_from,material.created_at,obj.created_at)):
            _fail('initial_policy_invalid')
        child = db.scalar(select(Line).where(Line.previous_line_id==line.id).execution_options(populate_existing=True))
        if child:
            child_pub = db.get(Publication, child.publication_id, populate_existing=True)
            if current or version.is_current or version.valid_to is None \
                    or preparation._aware(version.valid_to)!=preparation._aware(child_pub.created_at): _fail('version_closed_invalid')
        elif not version.is_current or version.valid_to is not None or obj.current_version_id!=version.id \
                or any(getattr(material,k)!=v for k,v in normalized.items()) \
                or preparation._aware(material.updated_at)!=preparation._aware(pub.created_at) \
                or preparation._aware(obj.updated_at)!=preparation._aware(pub.created_at):
            _fail('current_projection_drift')
    except (KeyError, IndexError, AttributeError, TypeError, ValueError):
        _fail('line_proof_invalid')
    return line


def _audit_payload(row):
    return dict(publication_id=str(row.id), receipt_id=str(row.receipt_id), record_count=row.record_count,
                payload_sha256=row.payload_sha256, review_sha256=row.review_sha256)


def _prove(db, row):
    try:
        payload = row.payload_jsonb
        command = MaterialPublicationCommand.model_validate(payload['request'])
        receipt = db.get(Receipt,row.receipt_id,populate_existing=True)
        ingress._prove(receipt)
        expected = dict(schema_version='rsc.material_publication.v1', publication_id=str(row.id),
            receipt_id=str(row.receipt_id), binding_id=str(row.binding_id), created_at=preparation._aware(row.created_at).isoformat(),
            actor_user_id=row.actor_user_id, actor_person_id=str(row.actor_person_id),
            actor_authorization_version=row.actor_authorization_version, auth_session_id=row.auth_session_id,
            request_sha256=row.request_sha256, review_sha256=row.review_sha256, record_count=row.record_count)
        extras = {'request','source','evidence_file','access_issued_at','access_expires_at','lines'}
        if set(payload)!=set(expected)|extras or any(payload[k]!=v for k,v in expected.items()) or digest(payload)!=row.payload_sha256 \
                or digest(dict(actor_user_id=row.actor_user_id,request=payload['request']))!=row.request_sha256 \
                or command.receipt_id!=row.receipt_id or receipt.binding_id!=row.binding_id \
                or command.capture_sha256!=receipt.capture_sha256 or row.record_count!=receipt.observed_count \
                or command.evidence_file_id!=row.evidence_file_id or command.evidence_sha256!=row.evidence_sha256 \
                or command.request_id!=row.request_id or command.idempotency_key!=row.idempotency_key \
                or payload['source']['receipt_id']!=str(receipt.id) or payload['source']['capture_sha256']!=receipt.capture_sha256 \
                or payload['evidence_file']['file_id']!=str(row.evidence_file_id) or payload['evidence_file']['sha256']!=row.evidence_sha256 \
                or type(payload['access_issued_at']) is not int or type(payload['access_expires_at']) is not int \
                or not payload['access_issued_at']<=preparation._aware(row.created_at).timestamp()<payload['access_expires_at']:
            _fail('publication_proof_invalid')
        lines = tuple(db.scalars(select(Line).where(Line.publication_id==row.id).order_by(Line.sequence)
                                .execution_options(populate_existing=True)))
        if len(lines)!=row.record_count or [line.sequence for line in lines]!=list(range(1,row.record_count+1)) \
                or payload['lines']!=[[str(line.id),line.payload_sha256] for line in lines]: _fail('incomplete_publication')
        for line in lines: _proof_line(db,line)
        event = verify_audit_event_in_read_snapshot(db,stream_key='authorization',event_id=row.audit_event_id)
        if event.action!='material_source.publish' or event.aggregate_type!='material_projection_publication' \
                or event.aggregate_id!=str(row.id) or event.actor_user_id!=row.actor_user_id or event.before_jsonb!={} \
                or event.after_jsonb!=_audit_payload(row) or event.request_id!='material-publication:'+str(row.id) \
                or preparation._aware(event.occurred_at)!=preparation._aware(row.created_at): _fail('audit_mismatch')
    except (KeyError,AttributeError,TypeError,ValueError):
        _fail('publication_proof_invalid')
    return row


def _plan(db, principal, command):
    observation = _observation(db,command.receipt_id)
    receipt = db.get(Receipt,command.receipt_id,populate_existing=True)
    rows = receipt.payload_jsonb['capture']['records']
    if receipt.capture_sha256!=command.capture_sha256 or [r['external_id'] for r in rows]!=[d.sku_code for d in command.decisions]:
        _fail('capture_decisions_mismatch')
    db.scalar(select(FileObject.id).where(FileObject.id==command.evidence_file_id).with_for_update(read=True))
    file = authority._file(db,command.evidence_file_id,command.evidence_sha256)
    file.pop('storage_key')
    targets = []; proven_publications = set()
    for raw, decision in zip(rows,command.decisions,strict=True):
        normalized = _normalized(raw['data'],decision)
        material = db.scalar(select(FormalMaterial).where(FormalMaterial.sku_code==decision.sku_code).with_for_update()
                             .execution_options(populate_existing=True))
        obj = db.scalar(select(ExternalObject).where(ExternalObject.source_system_id==UUID(observation['subject']['source_system_id']),
            ExternalObject.entity_type=='material',ExternalObject.external_id==decision.sku_code).with_for_update()
            .execution_options(populate_existing=True))
        previous = None
        if material is not None or obj is not None:
            if material is None or obj is None or material.external_object_id!=obj.id: _fail('unmanaged_identity_conflict')
            previous = db.scalar(select(Line).where(Line.material_id==material.id,Line.version_id==obj.current_version_id)
                                 .execution_options(populate_existing=True))
            if previous is None: _fail('unmanaged_identity_conflict')
            for model, identifier in ((ExternalObjectVersion,previous.version_id),(MaterialInventoryPolicy,previous.policy_id)):
                db.scalar(select(model.id).where(model.id==identifier).with_for_update(read=True))
            if previous.publication_id not in proven_publications:
                _prove(db,db.get(Publication,previous.publication_id,populate_existing=True))
                proven_publications.add(previous.publication_id)
            _proof_line(db,previous,current=True)
            prior = db.get(Publication,previous.publication_id)
            prior_receipt = db.get(Receipt,prior.receipt_id)
            if prior_receipt.source_instance!=receipt.source_instance: _fail('source_identity_conflict')
            if preparation._aware(receipt.capture_started_at)<=preparation._aware(prior_receipt.capture_completed_at): _fail('stale_or_overlapping_capture')
            if previous.payload_jsonb['policy']!=_policy(decision) or previous.payload_jsonb['normalized']['base_unit']!=decision.base_unit:
                _fail('unit_or_policy_migration_required')
        targets.append(dict(normalized=normalized,policy=_policy(decision),previous_line_id=str(previous.id) if previous else None,
                            previous_line_sha256=previous.payload_sha256 if previous else None))
    review = dict(schema_version='rsc.material_publication_review.v1',actor_user_id=principal.user_id,
        actor_person_id=str(principal.person_id),actor_authorization_version=principal.authorization_version,
        command=authority._json(command.model_dump()),source=observation,evidence_file=file,targets=targets)
    return dict(review=review,review_sha256=digest(review),projection_published=False,start_ready=False)


def preview_material_publication(db, *, access_token, expected_authorization_version, command):
    command,principal,session,claims,settings = _context(db,access_token,expected_authorization_version,command)
    result = _plan(db,principal,command)
    source._finish(db,principal,session,claims,settings)
    return result


def _existing(db, principal, command, review_sha256):
    rows = tuple(db.scalars(select(Publication).where(or_(Publication.receipt_id==command.receipt_id,
        (Publication.actor_user_id==principal.user_id)&or_(Publication.idempotency_key==command.idempotency_key,Publication.request_id==command.request_id)))
        .execution_options(populate_existing=True)))
    if not rows: return None
    request_hash = digest(dict(actor_user_id=principal.user_id,request=authority._json(command.model_dump())))
    if len(rows)!=1 or rows[0].request_sha256!=request_hash or rows[0].review_sha256!=review_sha256: _fail('request_conflict')
    return _prove(db,rows[0])


def _result(row):
    return dict(recorded=True,publication_id=str(row.id),receipt_id=str(row.receipt_id),record_count=row.record_count,
        payload_sha256=row.payload_sha256,audit_event_id=str(row.audit_event_id),projection_published=True,
        published_at=preparation._aware(row.created_at).isoformat(),full_catalog_verified=False,start_ready=False)


def read_material_publication(db, *, access_token, expected_authorization_version, command, review_sha256):
    configuration._require_digest(review_sha256)
    command,principal,session,claims,settings = _context(db,access_token,expected_authorization_version,command)
    row = _existing(db,principal,command,review_sha256)
    source._finish(db,principal,session,claims,settings)
    return _result(row) if row else dict(recorded=False,retry_allowed=False,projection_published=False,start_ready=False)


def execute_material_publication(db, *, access_token, expected_authorization_version, command, review_sha256):
    admission._owner(db); configuration._require_digest(review_sha256); preparation._begin_outer(db)
    with db.begin_nested():
        command,principal,session,claims,settings = _context(db,access_token,expected_authorization_version,command)
        existing = _existing(db,principal,command,review_sha256)
        if existing:
            source._finish(db,principal,session,claims,settings)
            return _result(existing)
        review = _plan(db,principal,command)
        if review['review_sha256']!=review_sha256: _fail('review_changed')
        observation = review['review']['source']
        receipt = db.get(Receipt,command.receipt_id)
        now = authority._now(db)
        pub_id = uuid4(); lines = []
        for sequence,(record,decision,target) in enumerate(zip(receipt.payload_jsonb['capture']['records'],command.decisions,review['review']['targets'],strict=True),1):
            previous = db.get(Line,UUID(target['previous_line_id'])) if target['previous_line_id'] else None
            if previous:
                material = db.get(FormalMaterial,previous.material_id)
                obj = db.get(ExternalObject,previous.external_object_id)
                policy = db.get(MaterialInventoryPolicy,previous.policy_id)
                old_version = db.get(ExternalObjectVersion,previous.version_id)
                if preparation._aware(old_version.valid_from)>=now: _fail('publication_time_conflict')
                old_version.is_current=False; old_version.valid_to=now; db.flush()
            else:
                obj = ExternalObject(id=uuid4(),source_system_id=UUID(observation['subject']['source_system_id']),
                    entity_type='material',external_id=decision.sku_code,created_at=now,updated_at=now)
                db.add(obj); db.flush()
                material = FormalMaterial(id=uuid4(),external_object_id=obj.id,created_at=now,updated_at=now,**target['normalized'])
                db.add(material); db.flush()
                policy = MaterialInventoryPolicy(id=uuid4(),material_id=material.id,effective_from=now,effective_to=None,**target['policy'])
                db.add(policy); db.flush()
            version_payload = dict(schema_version='rsc.reviewed_material_observation.v1',receipt_id=str(receipt.id),
                capture_id=str(receipt.capture_id),raw=record['data'],semantics=decision.model_dump(),source_updated_at=None)
            version = ExternalObjectVersion(id=uuid4(),external_object_id=obj.id,source_version='capture-v1:'+str(receipt.capture_id),
                source_updated_at=None,valid_from=now,valid_to=None,payload_jsonb=version_payload,payload_sha256=digest(version_payload),
                is_current=True,created_at=now)
            db.add(version); db.flush()
            obj.current_version_id=version.id; obj.updated_at=now
            # The initial pointer update must keep the sealed publication time;
            # assigning the same timestamp alone lets the ORM onupdate replace it.
            flag_modified(obj,'updated_at')
            for key,value in target['normalized'].items(): setattr(material,key,value)
            material.updated_at=now
            line = Line(id=uuid4(),publication_id=pub_id,sequence=sequence,sku_code=decision.sku_code,material_id=material.id,
                external_object_id=obj.id,version_id=version.id,policy_id=policy.id,previous_line_id=previous.id if previous else None)
            line.payload_jsonb = dict(line_id=str(line.id),publication_id=str(pub_id),sequence=sequence,material_id=str(material.id),
                external_object_id=str(obj.id),version_id=str(version.id),policy_id=str(policy.id),previous_line_id=str(previous.id) if previous else None,
                normalized=target['normalized'],policy=target['policy'],version_payload=version_payload)
            line.payload_sha256=digest(line.payload_jsonb); lines.append(line)
        db.flush()
        request = authority._json(command.model_dump())
        pub = Publication(id=pub_id,created_at=now,receipt_id=receipt.id,binding_id=receipt.binding_id,actor_user_id=principal.user_id,
            actor_person_id=principal.person_id,actor_authorization_version=principal.authorization_version,auth_session_id=session.id,
            evidence_file_id=command.evidence_file_id,evidence_sha256=command.evidence_sha256,record_count=len(lines),
            idempotency_key=command.idempotency_key,request_id=command.request_id,
            request_sha256=digest(dict(actor_user_id=principal.user_id,request=request)),review_sha256=review_sha256)
        pub.payload_jsonb = dict(schema_version='rsc.material_publication.v1',publication_id=str(pub.id),receipt_id=str(receipt.id),
            binding_id=str(receipt.binding_id),created_at=now.isoformat(),actor_user_id=principal.user_id,actor_person_id=str(principal.person_id),
            actor_authorization_version=principal.authorization_version,auth_session_id=session.id,request_sha256=pub.request_sha256,
            review_sha256=review_sha256,record_count=len(lines),request=request,source=observation,
            evidence_file=authority._file(db,command.evidence_file_id,command.evidence_sha256),access_issued_at=claims['iat'],access_expires_at=claims['exp'],
            lines=[[str(line.id),line.payload_sha256] for line in lines])
        pub.payload_sha256=digest(pub.payload_jsonb)
        event = append_audit_event(db,stream_key='authorization',actor_user_id=principal.user_id,action='material_source.publish',
            aggregate_type='material_projection_publication',aggregate_id=str(pub.id),request_id='material-publication:'+str(pub.id),
            before_jsonb={},after_jsonb=_audit_payload(pub),occurred_at=now,created_at=now)
        pub.audit_event_id=event.id; db.add(pub); db.flush(); db.add_all(lines); db.flush()
        source._finish(db,principal,session,claims,settings)
        if _observation(db,receipt.id)!=observation or authority._file(db,command.evidence_file_id,command.evidence_sha256)!=pub.payload_jsonb['evidence_file']:
            _fail('evidence_changed_during_execution')
        result = _result(_prove(db,pub))
        source._finish(db,principal,session,claims,settings)
        if authority._now(db)>=datetime.fromisoformat(observation['valid_until']): _fail('expired_during_execution')
        return result
