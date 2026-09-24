"""Owner provisioning of reviewed control scope; never source authentication.

An active formal headquarters principal records an exact version decision with
file evidence. The owner connection is infrastructure authority, not a substitute
for the reviewer. Runtime API/edge/projector roles cannot invoke these writes.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import re
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select, text

from .formal_access import FormalAccessError, FormalPrincipal, load_formal_principal, lock_formal_principal_graph
from .foundation_models import AuditChainHead, AuditEvent, AuthIdentity, FileObject, Organization, SourceSystem
from .models import User
from .inventory_control_models import InventoryControlSourceBinding as Binding, InventoryControlCatalogVersion as Catalog, InventoryControlPreparation as Preparation
from .inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from . import inventory_control_preparation as preparation
from .formal_services.audit_chain import append_audit_event, verify_audit_event_in_read_snapshot


class ControlAuthorityError(RuntimeError):
    pass


class AuthorityCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    action: Literal['source_grant', 'catalog_grant', 'revoke']
    binding_id: UUID
    catalog_id: UUID | None = None
    source_grant_id: UUID | None = None
    revoked_grant_id: UUID | None = None
    expected_subject_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence_file_id: UUID
    evidence_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=1, max_length=1000)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')
    request_id: str = Field(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$')

    @model_validator(mode='after')
    def shape(self):
        if not self.reason.strip() or any(getattr(self, name) is not None and getattr(self, name).int == 0
                for name in ('binding_id', 'catalog_id', 'source_grant_id', 'revoked_grant_id', 'evidence_file_id')):
            raise ValueError('explicit authority coordinates and reason required')
        if self.action == 'source_grant' and any((self.catalog_id, self.source_grant_id, self.revoked_grant_id)):
            raise ValueError('source grant cannot include catalogue or grant references')
        if self.action == 'catalog_grant' and (self.catalog_id is None or self.source_grant_id is None or self.revoked_grant_id is not None):
            raise ValueError('catalogue grant requires its exact source grant')
        if self.action == 'revoke' and (self.revoked_grant_id is None or self.source_grant_id is not None or self.valid_from is not None or self.valid_to is not None):
            raise ValueError('revocation is immediate and names an original grant')
        return self


def _fail(code):
    raise ControlAuthorityError('control_authority_' + code)


def _now(db):
    return preparation._aware(db.scalar(text('SELECT clock_timestamp()'))) if db.get_bind().dialect.name == 'postgresql' else datetime.now(timezone.utc)


def _json(value):
    if isinstance(value, datetime):
        return preparation._aware(value).isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    return value


def _operator(db, actor, now, *, permission_resource='inventory_control'):
    if not isinstance(actor, FormalPrincipal):
        _fail('operator_forbidden')
    try:
        current = load_formal_principal(db, actor.user_id, now=now)
    except FormalAccessError:
        _fail('operator_forbidden')
    user = db.get(User, actor.user_id, populate_existing=True)
    if current != actor or not user.is_active or current.access_mode != 'active' or not any(
            row.role_code == 'admin' and row.scope_type == 'national' and row.scope_id == '*' for row in current.assignments
    ) or not current.allows(db, permission_resource, 'authorize', target_scope_type='national', target_scope_id='*'):
        _fail('operator_forbidden')
    # Formal context requires a verified identity; also refuse a future-dated
    # verification rather than relying on a non-null timestamp alone.
    if not db.scalar(select(AuthIdentity.id).where(AuthIdentity.user_id == actor.user_id,
            AuthIdentity.status == 'active', AuthIdentity.revoked_at.is_(None), AuthIdentity.verified_at <= now).limit(1)):
        _fail('operator_forbidden')
    return current


def _file(db, file_id, expected_sha256):
    from .formal_services.formal_files import is_available_formal_file_for_purpose
    row = db.get(FileObject, file_id, populate_existing=True)
    if not is_available_formal_file_for_purpose(row, purpose="source_configuration_evidence") or row.status != 'available' or row.size_bytes <= 0 or row.sha256 != expected_sha256 \
            or not re.fullmatch('[a-f0-9]{64}', row.sha256):
        _fail('evidence_file_unavailable')
    verified_at = datetime.fromisoformat(row.metadata_jsonb['completion']['verified_at'])
    if row.metadata_jsonb['provider'] != 'aliyun_oss_v2' or not preparation._aware(row.created_at) <= verified_at <= _now(db):
        _fail('evidence_file_unavailable')
    return dict(file_id=str(row.id), sha256=row.sha256, size_bytes=row.size_bytes,
                mime_type=row.mime_type, storage_key=row.storage_key)


def _subject(db, binding_id, catalog_id):
    binding = db.get(Binding, binding_id, populate_existing=True)
    catalog = db.get(Catalog, catalog_id, populate_existing=True) if catalog_id is not None else None
    if binding is None or (catalog_id is not None and (catalog is None or catalog.binding_id != binding_id)):
        _fail('subject_not_found')
    if preparation._sha(binding.binding_jsonb) != binding.binding_sha256 or (catalog is not None and
            (preparation._sha(catalog.catalog_jsonb) != catalog.catalog_sha256
             or catalog.catalog_revision != catalog.catalog_jsonb.get('catalog_revision')
             or binding.target_region_code != catalog.catalog_jsonb.get('target_region_code'))):
        _fail('subject_invalid')
    return binding, catalog, dict(binding_id=str(binding.id), binding_sha256=binding.binding_sha256,
        source_system_id=str(binding.source_system_id), region_org_id=str(binding.region_org_id),
        target_region_code=binding.target_region_code, catalog_id=str(catalog.id) if catalog else None,
        catalog_sha256=catalog.catalog_sha256 if catalog else None)


def _binding_available(db, binding):
    source = db.get(SourceSystem, binding.source_system_id, populate_existing=True)
    region = db.get(Organization, binding.region_org_id, populate_existing=True)
    return source is not None and source.code.strip().casefold() == 'oam' and source.enabled and source.mode == 'read_only' \
        and region is not None and region.status == 'active' and region.org_type == 'region_company'


def _audit_payload(row):
    return dict(decision_id=str(row.id), action=row.action, binding_id=str(row.binding_id),
                catalog_id=str(row.catalog_id) if row.catalog_id else None, payload_sha256=row.payload_sha256)


def _prove(db, row, seen=None):
    seen = set() if seen is None else seen
    if row is None or row.id in seen:
        _fail('decision_graph_invalid')
    seen.add(row.id)
    _, _, subject = _subject(db, row.binding_id, row.catalog_id)
    payload = row.payload_jsonb
    expected = dict(schema_version='rsc.inventory_control_authority.v1', decision_id=str(row.id),
        action=row.action, subject=subject, source_grant_id=str(row.source_grant_id) if row.source_grant_id else None,
        revoked_grant_id=str(row.revoked_grant_id) if row.revoked_grant_id else None,
        created_at=preparation._aware(row.created_at).isoformat(), valid_from=preparation._aware(row.valid_from).isoformat(),
        valid_to=preparation._aware(row.valid_to).isoformat() if row.valid_to else None,
        actor_user_id=row.actor_user_id, actor_person_id=str(row.actor_person_id),
        actor_authorization_version=row.actor_authorization_version, request_sha256=row.request_sha256)
    if any(payload.get(key) != value for key, value in expected.items()) or preparation._sha(payload) != row.payload_sha256:
        _fail('decision_proof_invalid')
    try:
        command = AuthorityCommand.model_validate(payload['request'])
        actor = payload['actor_snapshot']
        file = payload['evidence_file']
    except (ValueError, KeyError, TypeError):
        _fail('decision_proof_invalid')
    if preparation._sha(dict(actor_user_id=row.actor_user_id, request=_json(command.model_dump()))) != row.request_sha256 \
            or command.action != row.action or command.binding_id != row.binding_id or command.catalog_id != row.catalog_id \
            or command.source_grant_id != row.source_grant_id or command.revoked_grant_id != row.revoked_grant_id \
            or command.evidence_file_id != row.evidence_file_id or command.evidence_sha256 != row.evidence_sha256 \
            or command.request_id != row.request_id or command.idempotency_key != row.idempotency_key \
            or actor.get('user_id') != row.actor_user_id or actor.get('person_id') != str(row.actor_person_id) \
            or actor.get('authorization_version') != row.actor_authorization_version \
            or file.get('file_id') != str(row.evidence_file_id) or file.get('sha256') != row.evidence_sha256:
        _fail('decision_proof_invalid')
    created = preparation._aware(row.created_at)
    starts = preparation._aware(row.valid_from)
    ends = preparation._aware(row.valid_to) if row.valid_to else None
    if starts < created or (ends is not None and ends <= starts) \
            or starts != (preparation._aware(command.valid_from) if command.valid_from else created) \
            or ends != (preparation._aware(command.valid_to) if command.valid_to else None):
        _fail('decision_proof_invalid')
    if row.action != 'revoke' and command.expected_subject_sha256 != (subject['catalog_sha256'] if row.catalog_id else subject['binding_sha256']):
        _fail('decision_proof_invalid')
    event = verify_audit_event_in_read_snapshot(db, stream_key='authorization', event_id=row.audit_event_id)
    if event.action != 'inventory_control.authority.' + row.action or event.aggregate_type != 'inventory_control_authority_decision' \
            or event.aggregate_id != str(row.id) or event.actor_user_id != row.actor_user_id or event.before_jsonb != {} \
            or event.after_jsonb != _audit_payload(row) or event.request_id != 'control-authority:' + str(row.id) \
            or preparation._aware(event.occurred_at) != preparation._aware(row.created_at):
        _fail('audit_mismatch')
    reference = row.source_grant_id or row.revoked_grant_id
    if reference:
        parent = db.get(Decision, reference, populate_existing=True)
        _prove(db, parent, seen)
        if parent.binding_id != row.binding_id or parent.action not in ('source_grant', 'catalog_grant'):
            _fail('decision_graph_invalid')
        if row.action == 'catalog_grant' and (parent.action != 'source_grant'
                or preparation._aware(parent.valid_from) > preparation._aware(row.valid_from)
                or (parent.valid_to is not None and (row.valid_to is None or preparation._aware(parent.valid_to) < preparation._aware(row.valid_to)))):
            _fail('decision_graph_invalid')
        if row.action == 'revoke' and (parent.catalog_id != row.catalog_id or preparation._aware(parent.created_at) > preparation._aware(row.created_at)):
            _fail('decision_graph_invalid')
        if row.action == 'revoke' and command.expected_subject_sha256 != parent.payload_sha256:
            _fail('decision_proof_invalid')
    return row


def _revocation(db, row):
    result = db.scalar(select(Decision).where(Decision.revoked_grant_id == row.id).execution_options(populate_existing=True))
    return _prove(db, result) if result else None


def _result(row):
    return dict(decision_id=row.id, action=row.action, binding_id=row.binding_id, catalog_id=row.catalog_id,
                payload_sha256=row.payload_sha256, audit_event_id=row.audit_event_id,
                created_at=preparation._aware(row.created_at), valid_from=preparation._aware(row.valid_from),
                valid_to=preparation._aware(row.valid_to) if row.valid_to else None)


def record_inventory_control_authority(db, *, actor, command: AuthorityCommand):
    if not isinstance(command, AuthorityCommand):
        _fail('invalid_command')
    # Internal model_construct/model_copy callers do not bypass the wire shape.
    command = AuthorityCommand.model_validate(command.model_dump())
    if db.new or db.dirty or db.deleted:
        _fail('requires_clean_session')
    preparation._owner(db)
    if db.get_bind().dialect.name == 'postgresql' and db.scalar(text('SELECT session_user')) != 'star_oam_migrator':
        _fail('requires_direct_schema_owner')
    preparation._begin_outer(db)
    with db.begin_nested():
        lock_formal_principal_graph(db, [actor.user_id] if isinstance(actor, FormalPrincipal) else [])
        # Serialize decisions for exactly this reviewed source/region binding;
        # unrelated bindings remain independent until their audit append.
        db.scalar(select(Binding.id).where(Binding.id == command.binding_id).with_for_update())
        now = _now(db)
        current = _operator(db, actor, now)
        request = _json(command.model_dump())
        request_hash = preparation._sha(dict(actor_user_id=current.user_id, request=request))
        prior = tuple(db.scalars(select(Decision).where(Decision.actor_user_id == current.user_id,
            or_(Decision.idempotency_key == command.idempotency_key, Decision.request_id == command.request_id))))
        if prior:
            if len(prior) != 1 or prior[0].request_sha256 != request_hash:
                _fail('request_conflict')
            return _result(_prove(db, prior[0]))
        binding, catalog, subject = _subject(db, command.binding_id, command.catalog_id)
        file = _file(db, command.evidence_file_id, command.evidence_sha256)
        starts = preparation._aware(command.valid_from) if command.valid_from else now
        ends = preparation._aware(command.valid_to) if command.valid_to else None
        if starts < now or (ends is not None and ends <= starts):
            _fail('invalid_validity')
        if command.action == 'revoke':
            parent = _prove(db, db.get(Decision, command.revoked_grant_id, populate_existing=True))
            if parent.action == 'revoke' or parent.binding_id != binding.id or parent.catalog_id != command.catalog_id \
                    or parent.payload_sha256 != command.expected_subject_sha256 or _revocation(db, parent) is not None:
                _fail('revocation_conflict')
        else:
            if not _binding_available(db, binding):
                _fail('binding_unavailable')
            if command.expected_subject_sha256 != (catalog.catalog_sha256 if catalog else binding.binding_sha256):
                _fail('subject_changed')
            if command.action == 'catalog_grant':
                parent = _prove(db, db.get(Decision, command.source_grant_id, populate_existing=True))
                if parent.action != 'source_grant' or parent.binding_id != binding.id or _revocation(db, parent) is not None \
                        or preparation._aware(parent.valid_from) > starts or (parent.valid_to is not None and
                        (ends is None or preparation._aware(parent.valid_to) < ends)):
                    _fail('source_grant_unavailable')
            for previous in db.scalars(select(Decision).where(Decision.binding_id == binding.id,
                    Decision.catalog_id == command.catalog_id, Decision.action == command.action)):
                _prove(db, previous)
                revoked = _revocation(db, previous)
                end = preparation._aware(previous.valid_to) if previous.valid_to else None
                if revoked:
                    end = min(end, preparation._aware(revoked.created_at)) if end else preparation._aware(revoked.created_at)
                if previous.source_grant_id:
                    source_revoked = _revocation(db, db.get(Decision, previous.source_grant_id))
                    if source_revoked:
                        end = min(end, preparation._aware(source_revoked.created_at)) if end else preparation._aware(source_revoked.created_at)
                if (end is None or end > starts) and (ends is None or ends > preparation._aware(previous.valid_from)):
                    _fail('overlapping_grant')
        row = Decision(id=uuid4(), binding_id=binding.id, catalog_id=command.catalog_id, action=command.action,
            source_grant_id=command.source_grant_id, revoked_grant_id=command.revoked_grant_id, valid_from=starts, valid_to=ends,
            actor_user_id=current.user_id, actor_person_id=current.person_id, actor_authorization_version=current.authorization_version,
            evidence_file_id=command.evidence_file_id, evidence_sha256=command.evidence_sha256,
            idempotency_key=command.idempotency_key, request_id=command.request_id, request_sha256=request_hash, created_at=now)
        row.payload_jsonb = dict(schema_version='rsc.inventory_control_authority.v1', decision_id=str(row.id),
            action=row.action, subject=subject, source_grant_id=str(row.source_grant_id) if row.source_grant_id else None,
            revoked_grant_id=str(row.revoked_grant_id) if row.revoked_grant_id else None,
            created_at=now.isoformat(), valid_from=starts.isoformat(), valid_to=ends.isoformat() if ends else None,
            actor_user_id=current.user_id, actor_person_id=str(current.person_id), actor_authorization_version=current.authorization_version,
            actor_snapshot=_json(asdict(current)), evidence_file=file, request=request, request_sha256=request_hash)
        row.payload_sha256 = preparation._sha(row.payload_jsonb)
        audit = append_audit_event(db, stream_key='authorization', actor_user_id=current.user_id,
            action='inventory_control.authority.' + row.action, aggregate_type='inventory_control_authority_decision',
            aggregate_id=str(row.id), request_id='control-authority:' + str(row.id), before_jsonb={},
            after_jsonb=_audit_payload(row), occurred_at=now, created_at=now)
        row.audit_event_id = audit.id
        db.add(row)
        db.flush()
        return _result(_prove(db, row))


def resolve_inventory_control_authority(db, *, preparation_id):
    """Current observation only; not a transactional publication capability.

    A publisher must lock/revalidate decisions and mutable source/file evidence
    in its own transaction, including validity at the actual publication time.

    No caller-supplied historical clock can authorize a new publication. Explicit
    revocation preserves historical decisions and affects all dependent catalogues.
    """
    if any(isinstance(row, (Decision, Binding, Catalog, Preparation, FileObject, SourceSystem, Organization, AuditEvent, AuditChainHead))
           for row in (*db.new, *db.dirty, *db.deleted)):
        _fail('pending_evidence')
    with db.no_autoflush:
        preparation.read_inventory_control_preparation(db, preparation_id=preparation_id)
        root = db.get(Preparation, preparation_id)
        binding, _, _ = _subject(db, root.binding_id, root.catalog_id)
        rows = tuple(db.scalars(select(Decision).where(Decision.binding_id == root.binding_id)
                               .order_by(Decision.created_at, Decision.id).execution_options(populate_existing=True)))
        for row in rows:
            _prove(db, row)
        revoked = {row.revoked_grant_id for row in rows if row.action == 'revoke'}
        now = _now(db)
        def effective(row):
            if row.id in revoked or preparation._aware(row.valid_from) > now or (row.valid_to and preparation._aware(row.valid_to) <= now):
                return False
            try:
                return _file(db, row.evidence_file_id, row.evidence_sha256) == row.payload_jsonb['evidence_file']
            except ControlAuthorityError:
                return False
        sources = [row for row in rows if row.action == 'source_grant' and effective(row)]
        if len(sources) > 1:
            _fail('ambiguous_active_authority')
        source = sources[0] if sources and _binding_available(db, binding) else None
        catalogues = [row for row in rows if row.action == 'catalog_grant' and row.catalog_id == root.catalog_id
                      and source and row.source_grant_id == source.id and effective(row)]
        if len(catalogues) > 1:
            _fail('ambiguous_active_authority')
        catalog = catalogues[0] if catalogues and source and catalogues[0].source_grant_id == source.id else None
        final_ids = tuple(db.scalars(select(Decision.id).where(Decision.binding_id == root.binding_id).order_by(Decision.created_at, Decision.id)))
        if final_ids != tuple(row.id for row in rows):
            _fail('changed_during_observation')
        return dict(preparation_id=preparation_id, source_authorized=source is not None, catalog_authorized=catalog is not None,
            source_grant_id=source.id if source else None, catalog_grant_id=catalog.id if catalog else None,
            authority_cursor_sha256=preparation._sha([[str(row.id), row.payload_sha256] for row in rows]), checked_at=now,
            source_authenticated=False, capture_attested=False, projection_published=False, start_ready=False)
