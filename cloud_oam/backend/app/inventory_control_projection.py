"""Reviewed control publication, atomic mirror graph and exact result recovery.

Caller owns the transaction. No inventory posting, opening start or notification
is performed. New publications require fresh evidence; recovery proves the
original immutable result and current operator without reauthorizing old input.
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import exists, or_, select, text
from sqlalchemy.orm.attributes import flag_modified

from . import inventory_control_admission as admission
from . import inventory_control_authority as authority
from . import inventory_control_configuration as configuration
from . import inventory_control_preparation as preparation
from . import inventory_control_projection_plan as planner
from .foundation_models import ExternalObject, ExternalObjectVersion, FileObject, SyncRun, SyncBatch, SyncInboxEvent
from .formal_services.audit_chain import append_audit_event, verify_audit_event_in_read_snapshot
from .formal_services.opening_stocktake import (
    OPENING_CONTROL_ENTITY_TYPE, OpeningControlLineInput, opening_control_batch_body_sha256,
    opening_control_manifest_sha256, opening_control_projection_payload,
    _canonical_timestamp,
)
from .inventory_control_models import InventoryControlPreparation as Preparation, InventoryControlSourceBinding as Binding, InventoryControlCaptureChain as Chain
from .inventory_control_projection_models import (
    ControlProjectionPublication as Publication, ControlProjectionLine as Line,
    ControlProjectionOrigin as Origin, ControlProjectionClosure as Closure,
)
from .material_projection_models import MaterialProjectionLine
from .models import ExternalSyncSnapshotRecord


class ControlProjectionError(RuntimeError):
    pass


def _fail(code):
    raise ControlProjectionError('control_projection_' + code)


def _stamp(value):
    return preparation._aware(value).isoformat() if value is not None else None


def _time(value):
    return preparation._aware(datetime.fromisoformat(value.replace('Z', '+00:00'))) if value is not None else None


class ControlPublicationCommand(planner.ControlProjectionPlanRequest):
    evidence_file_id: UUID
    evidence_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')
    request_id: str = Field(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$')

    @model_validator(mode='after')
    def evidence_required(self):
        if not self.evidence_file_id.int or not self.reason.strip():
            raise ValueError('explicit evidence and reason required')
        return self


def _context(db, access_token, expected_authorization_version, command):
    admission._owner(db)
    if not isinstance(command, ControlPublicationCommand):
        _fail('invalid_command')
    command = ControlPublicationCommand.model_validate(command.model_dump())
    preparation._begin_outer(db)
    claims = configuration._claims(access_token, configuration.get_settings())
    principal, session, claims, settings = configuration._operator_context(db, claims, expected_authorization_version)
    root = db.get(Preparation, command.preparation_id, populate_existing=True)
    if root is None or root.control_manifest_sha256 != command.preparation_sha256:
        _fail('preparation_mismatch')
    binding = db.get(Binding, root.binding_id, populate_existing=True)
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
            {'key': 'rsc.control-publication.scope:' + str(binding.source_system_id) + ':' + str(binding.region_org_id)})
    configuration._finish(db, principal, session, claims, settings)
    return command, principal, session, claims, settings, binding


def _line_input(row):
    return OpeningControlLineInput(sync_inbox_event_id=row.sync_inbox_event_id,
        external_object_version_id=row.version_id, external_business_key=row.external_business_key,
        material_id=row.material_id, condition_code=row.condition_code, control_qty=row.control_qty,
        mapping_status='resolved', mapping_note='', source_updated_at=row.source_updated_at,
        payload_sha256=row.payload_jsonb['control_payload_sha256'])


def _batch_hash(lines):
    return opening_control_batch_body_sha256(sequence=1, events=[dict(event_sort_key=str(row.sync_inbox_event_id),
        external_event_id='control-publication:' + str(row.id), external_id=row.external_business_key,
        payload_sha256=row.payload_jsonb['control_payload_sha256'], source_updated_at=_canonical_timestamp(row.source_updated_at) if row.source_updated_at else None,
        source_version='control-publication:' + str(row.publication_id)) for row in lines])


def _core_record(row):
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = _stamp(value) if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value
    return result


def _audit_payload(row):
    return dict(publication_id=str(row.id), sync_run_id=str(row.sync_run_id), preparation_id=str(row.preparation_id),
        record_count=row.record_count, origin_count=row.origin_count, payload_sha256=row.payload_sha256,
        review_sha256=row.review_sha256)


def _prove_line(db, line):
    pub = db.get(Publication, line.publication_id, populate_existing=True)
    obj = db.get(ExternalObject, line.external_object_id, populate_existing=True)
    version = db.get(ExternalObjectVersion, line.version_id, populate_existing=True)
    event = db.get(SyncInboxEvent, line.sync_inbox_event_id, populate_existing=True)
    expected = opening_control_projection_payload(external_business_key=line.external_business_key,
        region_org_id=pub.region_org_id, material_id=line.material_id, condition_code=line.condition_code,
        control_qty=line.control_qty, mapping_status='resolved', mapping_note='')
    planned = pub.payload_jsonb['review']['plan']['lines'][line.sequence-1]
    origins = tuple(db.scalars(select(Origin).where(Origin.line_id == line.id).order_by(Origin.sequence)
        .execution_options(populate_existing=True)))
    metadata = dict(line_id=str(line.id), publication_id=str(pub.id), sequence=line.sequence,
        external_object_id=str(line.external_object_id), version_id=str(line.version_id),
        sync_inbox_event_id=str(line.sync_inbox_event_id), previous_line_id=str(line.previous_line_id) if line.previous_line_id else None,
        control_payload=expected, control_payload_sha256=planner._sha(expected), source_updated_at=_stamp(line.source_updated_at),
        origins=[[str(row.id), row.payload_sha256] for row in origins])
    if line.payload_jsonb != metadata or line.payload_sha256 != planner._sha(metadata) \
            or planned['payload'] != expected or planned['source_updated_at'] != _stamp(line.source_updated_at) \
            or len(origins) != len(planned['origins']) or not origins \
            or obj is None or obj.source_system_id != pub.source_system_id or obj.entity_type != OPENING_CONTROL_ENTITY_TYPE \
            or obj.external_id != line.external_business_key or version is None or event is None \
            or version.external_object_id != obj.id or version.payload_jsonb != expected or event.payload_jsonb != expected \
            or version.payload_sha256 != planner._sha(expected) or event.payload_sha256 != planner._sha(expected) \
            or event.source_system_id != pub.source_system_id or event.batch_id != pub.sync_batch_id \
            or event.entity_type != OPENING_CONTROL_ENTITY_TYPE or event.external_id != obj.external_id \
            or event.external_event_id != 'control-publication:' + str(line.id) \
            or version.source_version != 'control-publication:' + str(pub.id) or event.source_version != version.source_version \
            or event.status != 'applied' or event.error_code is not None or event.error_detail is not None \
            or any(_stamp(value) != _stamp(pub.created_at) for value in (version.valid_from, version.created_at, event.created_at, event.processed_at)) \
            or any(_stamp(value) != _stamp(line.source_updated_at) for value in (version.source_updated_at, event.source_updated_at)):
        _fail('line_graph_invalid')
    for sequence, (origin, plan) in enumerate(zip(origins, planned['origins'], strict=True), 1):
        stage = db.get(ExternalSyncSnapshotRecord, origin.staging_record_id, populate_existing=True)
        material_line = db.get(MaterialProjectionLine, origin.material_line_id, populate_existing=True)
        value = dict(origin_id=str(origin.id), publication_id=str(pub.id), line_id=str(line.id), sequence=sequence, origin=plan)
        if origin.publication_id != pub.id or origin.sequence != sequence or origin.payload_jsonb != value \
                or origin.payload_sha256 != planner._sha(value) or origin.external_business_key != plan['external_business_key'] \
                or str(origin.capture_snapshot_id) != plan['capture_snapshot_id'] or origin.staging_record_id != plan['staging_record_id'] \
                or str(origin.material_line_id) != plan['material_line_id'] or stage is None or material_line is None \
                or stage.snapshot_ref_id != plan['snapshot_ref_id'] or stage.business_key != origin.external_business_key \
                or stage.operation != 'upsert' or stage.payload_sha256 != plan['staging_payload_sha256'] \
                or planner._sha(preparation._json(stage.payload_json)) != stage.payload_sha256 \
                or material_line.material_id != line.material_id or material_line.payload_sha256 != plan['material_line_sha256']:
            _fail('origin_graph_invalid')
    closure = db.scalar(select(Closure).where(Closure.closed_line_id == line.id).execution_options(populate_existing=True))
    child = db.scalar(select(Line).where(Line.previous_line_id == line.id).execution_options(populate_existing=True))
    if closure:
        closing = db.get(Publication, closure.publication_id, populate_existing=True)
        value = dict(closure_id=str(closure.id), publication_id=str(closing.id), closed_line_id=str(line.id),
            reason=closure.reason, closed_at=_stamp(closing.created_at))
        if closure.payload_jsonb != value or closure.payload_sha256 != planner._sha(value) \
                or closing.source_system_id != pub.source_system_id or closing.region_org_id != pub.region_org_id \
                or preparation._aware(closing.created_at) <= preparation._aware(pub.created_at) \
                or version.is_current or _stamp(version.valid_to) != _stamp(closing.created_at) \
                or closure.reason == 'replaced' and (child is None or child.publication_id != closing.id):
            _fail('closure_graph_invalid')
        if child is None and (obj.current_version_id != version.id or _stamp(obj.deleted_at) != _stamp(closing.created_at)):
            _fail('absent_projection_drift')
    elif child is not None or not version.is_current or version.valid_to is not None \
            or obj.current_version_id != version.id or obj.deleted_at is not None:
        _fail('current_projection_drift')
    if child:
        child_pub = db.get(Publication, child.publication_id, populate_existing=True)
        if child.external_object_id != line.external_object_id or child.material_id != line.material_id \
                or preparation._aware(child_pub.created_at) < preparation._aware(version.valid_to):
            _fail('lineage_invalid')
    return line


def prove_control_publication(db, row):
    """Verify preserved result; current source revocation cannot erase history."""
    try:
        payload = row.payload_jsonb
        command = ControlPublicationCommand.model_validate(payload['review']['command'])
        run = db.get(SyncRun, row.sync_run_id, populate_existing=True)
        batch = db.get(SyncBatch, row.sync_batch_id, populate_existing=True)
        lines = tuple(db.scalars(select(Line).where(Line.publication_id == row.id).order_by(Line.sequence)
            .execution_options(populate_existing=True)))
        closures = tuple(db.scalars(select(Closure).where(Closure.publication_id == row.id).order_by(Closure.closed_line_id)
            .execution_options(populate_existing=True)))
        plan = payload['review']['plan']
        if planner._sha(payload) != row.payload_sha256 or planner._sha(payload['review']) != row.review_sha256 \
                or payload['schema_version'] != 'rsc.control_projection_publication.v1' or payload['publication_id'] != str(row.id) \
                or payload['created_at'] != _stamp(row.created_at) or payload['captured_at'] != _stamp(row.captured_at) \
                or payload['valid_until'] != _stamp(row.valid_until) \
                or payload['review']['actor_user_id'] != row.actor_user_id or payload['review']['actor_person_id'] != str(row.actor_person_id) \
                or payload['review']['actor_authorization_version'] != row.actor_authorization_version \
                or payload['auth_session_id'] != row.auth_session_id \
                or command.preparation_id != row.preparation_id or command.mapping_decision_id != row.mapping_decision_id \
                or command.evidence_file_id != row.evidence_file_id or command.evidence_sha256 != row.evidence_sha256 \
                or command.idempotency_key != row.idempotency_key or command.request_id != row.request_id \
                or planner._sha(dict(actor_user_id=row.actor_user_id, command=command.model_dump(mode='json'))) != row.request_sha256 \
                or plan['source_system_id'] != str(row.source_system_id) or plan['region_org_id'] != str(row.region_org_id) \
                or len(lines) != row.record_count or len(plan['lines']) != len(lines) or plan['target_record_count'] != row.origin_count \
                or tuple(line.sequence for line in lines) != tuple(range(1, row.record_count+1)) \
                or payload['lines'] != [[str(line.id), line.payload_sha256] for line in lines] \
                or payload['closures'] != [[str(c.id), c.payload_sha256] for c in closures] \
                or _core_record(run) != payload['run'] or _core_record(batch) != payload['batch'] \
                or run.source_system_id != row.source_system_id or run.status != 'completed' or run.scope_key != plan['scope_key'] \
                or run.run_key != 'control-publication:' + str(row.id) or batch.run_id != run.id \
                or batch.entity_type != OPENING_CONTROL_ENTITY_TYPE or batch.status != 'applied' or batch.sequence != 1 \
                or batch.record_count != row.record_count or batch.body_sha256 != _batch_hash(lines) \
                or len(tuple(db.scalars(select(SyncBatch.id).where(SyncBatch.run_id == run.id)))) != 1 \
                or set(db.scalars(select(SyncInboxEvent.id).where(SyncInboxEvent.batch_id == batch.id))) != {line.sync_inbox_event_id for line in lines}:
            _fail('publication_graph_invalid')
        previous = db.get(Publication, row.previous_publication_id, populate_existing=True) if row.previous_publication_id else None
        expected_previous = dict(publication_id=str(previous.id), payload_sha256=previous.payload_sha256) if previous else None
        if payload['review']['previous_publication'] != expected_previous \
                or previous and (previous.source_system_id != row.source_system_id or previous.region_org_id != row.region_org_id
                    or preparation._aware(previous.captured_at) >= preparation._aware(row.captured_at)
                    or preparation._aware(previous.created_at) >= preparation._aware(row.created_at)):
            _fail('publication_lineage_invalid')
        previous_lines = set(db.scalars(select(Line.id).where(Line.publication_id == previous.id))) if previous else set()
        if {c.closed_line_id for c in closures} != previous_lines:
            _fail('closure_set_invalid')
        for line in lines:
            _prove_line(db, line)
        for closure in closures:
            _prove_line(db, db.get(Line, closure.closed_line_id, populate_existing=True))
        manifest = opening_control_manifest_sha256(source_system_id=row.source_system_id, sync_run_id=run.id,
            sync_scope_key=run.scope_key, region_org_id=row.region_org_id, lines=tuple(_line_input(line) for line in lines))
        if run.manifest_sha256 != manifest or payload['control_manifest_sha256'] != manifest:
            _fail('control_manifest_invalid')
        event = verify_audit_event_in_read_snapshot(db, stream_key='authorization', event_id=row.audit_event_id)
        if event.action != 'inventory_control.publish' or event.aggregate_type != 'control_projection_publication' \
                or event.aggregate_id != str(row.id) or event.actor_user_id != row.actor_user_id or event.before_jsonb != {} \
                or event.after_jsonb != _audit_payload(row) or event.request_id != 'control-publication:' + str(row.id) \
                or _stamp(event.occurred_at) != _stamp(row.created_at):
            _fail('audit_invalid')
    except (KeyError, IndexError, AttributeError, TypeError, ValueError):
        _fail('publication_proof_invalid')
    return row


def _identities(db, binding, keys):
    region_version = exists(select(ExternalObjectVersion.id).where(ExternalObjectVersion.external_object_id == ExternalObject.id,
        ExternalObjectVersion.payload_jsonb['region_org_id'].as_string() == str(binding.region_org_id)))
    objects = tuple(db.scalars(select(ExternalObject).where(ExternalObject.source_system_id == binding.source_system_id,
        ExternalObject.entity_type == OPENING_CONTROL_ENTITY_TYPE, or_(ExternalObject.external_id.in_(keys), region_version))
        .order_by(ExternalObject.external_id).with_for_update().execution_options(populate_existing=True)))
    result = []
    for obj in objects:
        line = db.scalar(select(Line).where(Line.version_id == obj.current_version_id).execution_options(populate_existing=True))
        if line is None:
            _fail('unmanaged_control_migration_required')
        _prove_line(db, line)
        version = db.get(ExternalObjectVersion, line.version_id)
        result.append(dict(external_business_key=obj.external_id, external_object_id=str(obj.id),
            previous_line_id=str(line.id), previous_line_sha256=line.payload_sha256,
            version_id=str(version.id), valid_to=_stamp(version.valid_to), deleted_at=_stamp(obj.deleted_at)))
    return result


def _review(db, principal, command, login, binding):
    inspected = planner.inspect_inventory_control_projection_plan(db, **login,
        request=planner.ControlProjectionPlanRequest.model_validate({name:getattr(command, name)
            for name in planner.ControlProjectionPlanRequest.model_fields}))
    plan = inspected['review']['plan']
    previous = db.scalars(select(Publication).where(Publication.source_system_id == binding.source_system_id,
        Publication.region_org_id == binding.region_org_id).order_by(Publication.captured_at.desc()).limit(1)
        .execution_options(populate_existing=True)).first()
    if previous:
        prove_control_publication(db, previous)
    chain = db.get(Chain, UUID(plan['capture_chain_id']), populate_existing=True)
    captured = _time(chain.evidence_jsonb['snapshots'][-1]['manifest']['snapshot_at'])
    if previous and preparation._aware(previous.captured_at) >= captured:
        _fail('fresh_capture_required')
    db.scalar(select(FileObject.id).where(FileObject.id == command.evidence_file_id).with_for_update(read=True))
    file = authority._file(db, command.evidence_file_id, command.evidence_sha256); file.pop('storage_key')
    identities = _identities(db, binding, [line['payload']['external_business_key'] for line in plan['lines']])
    document = dict(schema_version='rsc.control_projection_publication_review.v1',
        actor_user_id=principal.user_id, actor_person_id=str(principal.person_id), actor_authorization_version=principal.authorization_version,
        command=command.model_dump(mode='json'), plan=plan, evidence_file=file, identities=identities,
        captured_at=_stamp(captured), previous_publication=dict(publication_id=str(previous.id), payload_sha256=previous.payload_sha256) if previous else None)
    return dict(review=document, review_sha256=planner._sha(document), projection_published=False, start_ready=False)


def preview_control_publication(db, *, access_token, expected_authorization_version, command):
    command, principal, session, claims, settings, binding = _context(db, access_token, expected_authorization_version, command)
    result = _review(db, principal, command, dict(access_token=access_token, expected_authorization_version=expected_authorization_version), binding)
    configuration._finish(db, principal, session, claims, settings)
    if authority._now(db) >= _time(result['review']['plan']['valid_until']):
        _fail('expired')
    return result


def _existing(db, principal, command, review_sha256):
    rows = tuple(db.scalars(select(Publication).where(or_(Publication.preparation_id == command.preparation_id,
        (Publication.actor_user_id == principal.user_id) & or_(Publication.idempotency_key == command.idempotency_key,
            Publication.request_id == command.request_id))).execution_options(populate_existing=True)))
    if not rows:
        return None
    digest = planner._sha(dict(actor_user_id=principal.user_id, command=command.model_dump(mode='json')))
    if len(rows) != 1 or rows[0].request_sha256 != digest or rows[0].review_sha256 != review_sha256:
        _fail('request_conflict')
    return prove_control_publication(db, rows[0])


def _result(row):
    return dict(recorded=True, publication_id=str(row.id), sync_run_id=str(row.sync_run_id),
        preparation_id=str(row.preparation_id), record_count=row.record_count, origin_count=row.origin_count,
        payload_sha256=row.payload_sha256, audit_event_id=str(row.audit_event_id), published_at=_stamp(row.created_at),
        projection_published=True, start_ready=False)


def read_control_publication(db, *, access_token, expected_authorization_version, command, review_sha256):
    configuration._require_digest(review_sha256)
    command, principal, session, claims, settings, _ = _context(db, access_token, expected_authorization_version, command)
    row = _existing(db, principal, command, review_sha256)
    configuration._finish(db, principal, session, claims, settings)
    return _result(row) if row else dict(recorded=False, retry_allowed=False, projection_published=False, start_ready=False)


def execute_control_publication(db, *, access_token, expected_authorization_version, command, review_sha256):
    admission._owner(db); configuration._require_digest(review_sha256); preparation._begin_outer(db)
    with db.begin_nested():
        command, principal, session, claims, settings, binding = _context(db, access_token, expected_authorization_version, command)
        existing = _existing(db, principal, command, review_sha256)
        if existing:
            configuration._finish(db, principal, session, claims, settings)
            return _result(existing)
        login = dict(access_token=access_token, expected_authorization_version=expected_authorization_version)
        reviewed = _review(db, principal, command, login, binding)
        if reviewed['review_sha256'] != review_sha256:
            _fail('review_changed')
        review = reviewed['review']; plan = review['plan']; now = authority._now(db)
        pub_id, run_id, batch_id = uuid4(), uuid4(), uuid4()
        identities = {row['external_business_key']:row for row in review['identities']}
        keys = {row['payload']['external_business_key'] for row in plan['lines']}
        previous_id = UUID(review['previous_publication']['publication_id']) if review['previous_publication'] else None
        previous_lines = tuple(db.scalars(select(Line).where(Line.publication_id == previous_id))) if previous_id else ()
        closures = []
        for old in previous_lines:
            version = db.get(ExternalObjectVersion, old.version_id); obj = db.get(ExternalObject, old.external_object_id)
            if not version.is_current or version.valid_to is not None or preparation._aware(version.valid_from) >= now:
                _fail('previous_version_drift')
            version.is_current = False; version.valid_to = now
            reason = 'replaced' if old.external_business_key in keys else 'absent'
            if reason == 'absent':
                obj.deleted_at = now; obj.updated_at = now
            closure = Closure(id=uuid4(), publication_id=pub_id, closed_line_id=old.id, reason=reason)
            closure.payload_jsonb = dict(closure_id=str(closure.id), publication_id=str(pub_id), closed_line_id=str(old.id),
                reason=reason, closed_at=_stamp(now)); closure.payload_sha256 = planner._sha(closure.payload_jsonb)
            closures.append(closure)
        db.flush()
        lines, origins, events = [], [], []
        for planned in plan['lines']:
            value = planned['payload']; identity = identities.get(value['external_business_key'])
            obj = db.get(ExternalObject, UUID(identity['external_object_id'])) if identity else ExternalObject(id=uuid4(),
                source_system_id=binding.source_system_id, entity_type=OPENING_CONTROL_ENTITY_TYPE,
                external_id=value['external_business_key'], created_at=now, updated_at=now)
            if not identity:
                db.add(obj); db.flush()
            version = ExternalObjectVersion(id=uuid4(), external_object_id=obj.id, source_version='control-publication:' + str(pub_id),
                source_updated_at=_time(planned['source_updated_at']), valid_from=now, valid_to=None, payload_jsonb=value,
                payload_sha256=planned['payload_sha256'], is_current=True, created_at=now)
            db.add(version); db.flush()
            obj.current_version_id = version.id; obj.deleted_at = None; obj.updated_at = now; flag_modified(obj, 'updated_at')
            line = Line(id=uuid4(), publication_id=pub_id, sequence=planned['sequence'],
                external_business_key=value['external_business_key'], external_object_id=obj.id, version_id=version.id,
                sync_inbox_event_id=uuid4(), previous_line_id=UUID(identity['previous_line_id']) if identity else None,
                material_id=UUID(value['material_id']), condition_code=value['condition_code'], control_qty=Decimal(value['control_qty']),
                source_updated_at=version.source_updated_at)
            local_origins = []
            for sequence, origin in enumerate(planned['origins'], 1):
                row = Origin(id=uuid4(), publication_id=pub_id, line_id=line.id, sequence=sequence,
                    external_business_key=origin['external_business_key'], capture_snapshot_id=UUID(origin['capture_snapshot_id']),
                    staging_record_id=origin['staging_record_id'], material_line_id=UUID(origin['material_line_id']))
                row.payload_jsonb = dict(origin_id=str(row.id), publication_id=str(pub_id), line_id=str(line.id), sequence=sequence, origin=origin)
                row.payload_sha256 = planner._sha(row.payload_jsonb); local_origins.append(row)
            line.payload_jsonb = dict(line_id=str(line.id), publication_id=str(pub_id), sequence=line.sequence,
                external_object_id=str(obj.id), version_id=str(version.id), sync_inbox_event_id=str(line.sync_inbox_event_id),
                previous_line_id=str(line.previous_line_id) if line.previous_line_id else None, control_payload=value,
                control_payload_sha256=planned['payload_sha256'], source_updated_at=planned['source_updated_at'],
                origins=[[str(row.id), row.payload_sha256] for row in local_origins])
            line.payload_sha256 = planner._sha(line.payload_jsonb); lines.append(line); origins.extend(local_origins)
            events.append(SyncInboxEvent(id=line.sync_inbox_event_id, batch_id=batch_id, source_system_id=binding.source_system_id,
                external_event_id='control-publication:' + str(line.id), entity_type=OPENING_CONTROL_ENTITY_TYPE,
                external_id=obj.external_id, source_version=version.source_version, source_updated_at=version.source_updated_at,
                payload_jsonb=value, payload_sha256=planned['payload_sha256'], status='applied', processed_at=now, created_at=now))
        manifest = opening_control_manifest_sha256(source_system_id=binding.source_system_id, sync_run_id=run_id,
            sync_scope_key=plan['scope_key'], region_org_id=binding.region_org_id, lines=tuple(_line_input(line) for line in lines))
        chain = db.get(Chain, UUID(plan['capture_chain_id'])); last = chain.evidence_jsonb['snapshots'][-1]['manifest']
        run = SyncRun(id=run_id, source_system_id=binding.source_system_id, run_key='control-publication:' + str(pub_id),
            scope_key=plan['scope_key'], mode=last['sync_mode'], watermark_from=chain.evidence_jsonb['snapshots'][0]['manifest']['snapshot_id'],
            watermark_to=last['snapshot_id'], status='completed', manifest_sha256=manifest, started_at=now, completed_at=now, created_at=now, updated_at=now)
        batch = SyncBatch(id=batch_id, run_id=run_id, entity_type=OPENING_CONTROL_ENTITY_TYPE, sequence=1,
            record_count=len(lines), body_sha256=_batch_hash(lines), status='applied', received_at=now, validated_at=now, created_at=now)
        db.add(run); db.flush(); db.add(batch); db.flush(); db.add_all(events); db.flush()
        row = Publication(id=pub_id, preparation_id=command.preparation_id, source_system_id=binding.source_system_id,
            region_org_id=binding.region_org_id, mapping_decision_id=command.mapping_decision_id, previous_publication_id=previous_id,
            sync_run_id=run_id, sync_batch_id=batch_id, actor_user_id=principal.user_id, actor_person_id=principal.person_id,
            actor_authorization_version=principal.authorization_version, auth_session_id=session.id, evidence_file_id=command.evidence_file_id,
            evidence_sha256=command.evidence_sha256, captured_at=_time(review['captured_at']), valid_until=_time(plan['valid_until']),
            record_count=len(lines), origin_count=len(origins), idempotency_key=command.idempotency_key, request_id=command.request_id,
            request_sha256=planner._sha(dict(actor_user_id=principal.user_id, command=command.model_dump(mode='json'))),
            review_sha256=review_sha256, created_at=now)
        closures.sort(key=lambda item: str(item.closed_line_id))
        row.payload_jsonb = dict(schema_version='rsc.control_projection_publication.v1', publication_id=str(pub_id),
            created_at=_stamp(now), captured_at=_stamp(row.captured_at), valid_until=_stamp(row.valid_until), review=review,
            auth_session_id=session.id, access_issued_at=claims['iat'], access_expires_at=claims['exp'],
            run=_core_record(run), batch=_core_record(batch), control_manifest_sha256=manifest,
            lines=[[str(line.id), line.payload_sha256] for line in lines], closures=[[str(c.id), c.payload_sha256] for c in closures])
        row.payload_sha256 = planner._sha(row.payload_jsonb)
        event = append_audit_event(db, stream_key='authorization', actor_user_id=principal.user_id, action='inventory_control.publish',
            aggregate_type='control_projection_publication', aggregate_id=str(pub_id), request_id='control-publication:' + str(pub_id),
            before_jsonb={}, after_jsonb=_audit_payload(row), occurred_at=now, created_at=now)
        row.audit_event_id = event.id; db.add(row); db.flush(); db.add_all(lines); db.flush()
        db.add_all(origins); db.add_all(closures); db.flush()
        # Source evidence remains independently current after graph and audit writes.
        checked = planner.inspect_inventory_control_projection_plan(db, **login,
            request=planner.ControlProjectionPlanRequest.model_validate({name:getattr(command, name) for name in planner.ControlProjectionPlanRequest.model_fields}))
        if checked['review']['plan'] != plan:
            _fail('evidence_changed_during_execution')
        configuration._finish(db, principal, session, claims, settings)
        if authority._now(db) >= preparation._aware(row.valid_until):
            _fail('expired_during_execution')
        return _result(prove_control_publication(db, row))
