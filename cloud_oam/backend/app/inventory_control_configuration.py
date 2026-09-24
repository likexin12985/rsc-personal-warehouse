"""Authenticated owner-side control configuration; caller owns the transaction.

The API never receives the owner DSN. This administrative entry resolves the
reviewer from a signed, live RSC web session, binds a preview to exact evidence,
and records session evidence atomically with the domain decision. It is not an
OAM collector, a publisher, or a replacement for the planned PC configuration UI.
"""
import re
from datetime import datetime
from uuid import UUID

import jwt
from sqlalchemy import or_, select, text

from .auth_sessions import SessionError, require_current_session_ip_evidence
from .config import get_settings, _is_configured_secret
from .formal_access import load_formal_principal, lock_formal_principal_graph
from .formal_services.audit_chain import append_audit_event, verify_audit_event_in_read_snapshot
from .foundation_models import AuditEvent, FileObject, Organization, SourceSystem
from .inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from .inventory_control_models import InventoryControlSourceBinding as Binding
from .models import AuthSession
from . import inventory_control_authority as authority
from . import inventory_control_preparation as preparation


class ControlConfigurationError(RuntimeError):
    pass


def _fail(code):
    raise ControlConfigurationError('control_configuration_' + code)


def _claims(token, settings):
    if not _is_configured_secret(settings.jwt_secret, min_length=32):
        _fail('authentication_unconfigured')
    if not isinstance(token, str) or not 1 <= len(token) <= 8192 or not re.fullmatch(r'[A-Za-z0-9_.-]+', token):
        _fail('authentication_required')
    try:
        # Time is validated against a fresh DB clock after all business locks.
        value = jwt.decode(token, settings.jwt_secret, algorithms=['HS256'], audience='star-oam-cloud',
            options={'require': ['sub', 'sid', 'aud', 'iat', 'exp'],
                     'verify_exp': False, 'verify_iat': False, 'verify_nbf': False})
        if set(value) != {'sub', 'sid', 'aud', 'iat', 'exp'} or value['aud'] != 'star-oam-cloud':
            raise ValueError()
        for name in ('sub', 'sid'):
            if not isinstance(value[name], str) or str(UUID(value[name])) != value[name] or UUID(value[name]).int == 0:
                raise ValueError()
        if any(type(value[name]) is not int for name in ('iat', 'exp')) \
                or not 0 < value['iat'] < value['exp'] <= 253402300799 \
                or value['exp'] - value['iat'] > settings.jwt_ttl_minutes * 60:
            raise ValueError()
    except Exception:
        _fail('authentication_required')
    return value


def _operator_context(db, claims, expected_version, *, permission_resource='inventory_control'):
    if db.new or db.dirty or db.deleted:
        _fail('requires_clean_session')
    if type(expected_version) is not int or expected_version < 1:
        _fail('invalid_authorization_version')
    settings = get_settings()
    # Session revocation/refresh writers lock this family first. No audit head
    # is acquired until session, principal and exact binding locks are held.
    session = db.scalar(select(AuthSession).where(AuthSession.id == claims['sid'])
        .with_for_update().execution_options(populate_existing=True))
    lock_formal_principal_graph(db, [claims['sub']])
    now = authority._now(db)
    _session_valid(session, claims, settings, now)
    principal = load_formal_principal(db, claims['sub'], now=now)
    authority._operator(db, principal, now, permission_resource=permission_resource)
    if principal.authorization_version != expected_version:
        _fail('authorization_changed')
    return principal, session, claims, settings


def _context(db, token, expected_version, command, *, purpose, review_sha256=None):
    if db.new or db.dirty or db.deleted:
        _fail('requires_clean_session')
    if not isinstance(command, authority.AuthorityCommand):
        _fail('invalid_command')
    command = authority.AuthorityCommand.model_validate(command.model_dump())
    preparation._owner(db)
    if db.get_bind().dialect.name == 'postgresql' and db.scalar(text('SELECT session_user')) != 'star_oam_migrator':
        _fail('requires_direct_schema_owner')
    from .inventory_control_handoff import SignedControlRequest, verify_owner_request
    claims = verify_owner_request(db, token, purpose=purpose, command=command,
        expected_version=expected_version, review_sha256=review_sha256) if isinstance(token, SignedControlRequest) \
        else _claims(token, get_settings())
    principal, session, claims, settings = _operator_context(db, claims, expected_version)
    if 'handoff_actor_person_id' in claims and claims['handoff_actor_person_id'] != str(principal.person_id):
        _fail('authorization_changed')
    db.scalar(select(Binding.id).where(Binding.id == command.binding_id).with_for_update())
    _finish(db, principal, session, claims, settings)
    return command, principal, session, claims, settings


def _session_valid(session, claims, settings, now):
    if not claims['iat'] <= now.timestamp() < claims['exp'] or session is None \
            or session.user_id != claims['sub'] or session.revoked_at is not None \
            or preparation._aware(session.expires_at) <= now \
            or int(preparation._aware(session.created_at).timestamp()) > claims['iat'] \
            or session.client_type != 'web' or not session.device_id.strip():
        _fail('authentication_required')
    if 'handoff' in claims and not claims['handoff']['issued_at'] <= now.timestamp() < claims['handoff']['expires_at']:
        _fail('handoff_expired')
    try:
        require_current_session_ip_evidence(session, hash_version=settings.identity_hash_version)
    except SessionError:
        _fail('authentication_required')


def _finish(db, principal, session, claims, settings, *, permission_resource='inventory_control'):
    now = authority._now(db)
    _session_valid(session, claims, settings, now)
    authority._operator(db, principal, now, permission_resource=permission_resource)


def inspect_inventory_control_capture(db, *, access_token, expected_authorization_version, preparation_id,
                                      normalization_rules=None, mapping_decision_id=None):
    """Current HQ web-session inspection; caller rolls back the read/lock scope."""
    from .inventory_control_admission import inspect_inventory_control_admission
    if db.new or db.dirty or db.deleted:
        _fail('requires_clean_session')
    preparation._owner(db)
    if db.get_bind().dialect.name == 'postgresql' and db.scalar(text('SELECT session_user')) != 'star_oam_migrator':
        _fail('requires_direct_schema_owner')
    claims = _claims(access_token, get_settings())
    principal, session, claims, settings = _operator_context(db, claims, expected_authorization_version)
    if normalization_rules is None and mapping_decision_id is None:
        result = inspect_inventory_control_admission(db, preparation_id=preparation_id)
    else:
        from .inventory_control_normalization import inspect_inventory_control_normalization
        result = inspect_inventory_control_normalization(db, preparation_id=preparation_id, rules=normalization_rules,
                                                        mapping_decision_id=mapping_decision_id)
    _finish(db, principal, session, claims, settings)
    if authority._now(db) >= preparation._aware(datetime.fromisoformat(result.get('normalization_valid_until', result['observation']['valid_until']))):
        _fail('capture_observation_expired')
    return result


def _review(db, principal, command):
    binding, catalog, subject = authority._subject(db, command.binding_id, command.catalog_id)
    for model, identifier in ((SourceSystem, binding.source_system_id),
                              (Organization, binding.region_org_id), (FileObject, command.evidence_file_id)):
        db.scalar(select(model.id).where(model.id == identifier).with_for_update(read=True))
    source = db.get(SourceSystem, binding.source_system_id, populate_existing=True)
    region = db.get(Organization, binding.region_org_id, populate_existing=True)
    file = authority._file(db, command.evidence_file_id, command.evidence_sha256)
    # The signed content is operator-visible; do not expose private storage keys.
    file.pop('storage_key')
    history = tuple(db.scalars(select(Decision).where(Decision.binding_id == binding.id)
        .order_by(Decision.created_at, Decision.id).execution_options(populate_existing=True)))
    decisions = []
    for row in history:
        authority._prove(db, row)
        decisions.append(authority._json(authority._result(row)))
    document = dict(schema_version='rsc.inventory_control_configuration_review.v1',
        actor_user_id=principal.user_id, actor_person_id=str(principal.person_id),
        actor_authorization_version=principal.authorization_version,
        command=authority._json(command.model_dump()), subject=subject,
        source_binding=binding.binding_jsonb, catalogue=catalog.catalog_jsonb if catalog else None,
        source=dict(code=source.code, mode=source.mode, enabled=source.enabled) if source else None,
        region=dict(code=region.code, name=region.name, org_type=region.org_type, status=region.status) if region else None,
        evidence_file=file, decision_history=decisions)
    return dict(review=document, review_sha256=preparation._sha(document),
                projection_published=False, start_ready=False)


def preview_inventory_control_configuration(db, *, access_token, expected_authorization_version, command):
    """Read-only review; it does not claim the command can currently succeed."""
    command, principal, session, claims, settings = _context(db, access_token, expected_authorization_version, command, purpose='preview')
    result = _review(db, principal, command)
    _finish(db, principal, session, claims, settings)
    return result


def _existing(db, principal, command):
    rows = tuple(db.scalars(select(Decision).where(Decision.actor_user_id == principal.user_id,
        or_(Decision.idempotency_key == command.idempotency_key, Decision.request_id == command.request_id))
        .execution_options(populate_existing=True)))
    if not rows:
        return None
    digest = preparation._sha(dict(actor_user_id=principal.user_id, request=authority._json(command.model_dump())))
    if len(rows) != 1 or rows[0].request_sha256 != digest:
        _fail('request_conflict')
    return authority._prove(db, rows[0])


def _receipt(db, row, review_sha256):
    events = tuple(db.scalars(select(AuditEvent).where(AuditEvent.stream_key == 'authorization',
        AuditEvent.request_id == 'control-configuration:' + str(row.id)).execution_options(populate_existing=True)))
    if not events:
        _fail('execution_evidence_missing')
    if len(events) != 1:
        _fail('execution_evidence_invalid')
    event = verify_audit_event_in_read_snapshot(db, stream_key='authorization', event_id=events[0].id)
    payload = event.after_jsonb
    expected = dict(schema_version='rsc.inventory_control_configuration_execution.v1',
        decision_id=str(row.id), decision_sha256=row.payload_sha256, decision_audit_event_id=str(row.audit_event_id),
        request_sha256=row.request_sha256, review_sha256=review_sha256,
        actor_authorization_version=row.actor_authorization_version)
    if event.action != 'inventory_control.configuration.execute' or event.aggregate_type != 'inventory_control_authority_decision' \
            or event.aggregate_id != str(row.id) or event.actor_user_id != row.actor_user_id or event.before_jsonb != {} \
            or any(payload.get(key) != value for key, value in expected.items()):
        _fail('execution_evidence_invalid')
    try:
        if set(payload) != {*expected, 'auth_session_id', 'access_issued_at', 'access_expires_at', *({'handoff'} if 'handoff' in payload else set())} \
                or str(UUID(payload['auth_session_id'])) != payload['auth_session_id'] \
                or UUID(payload['auth_session_id']).int == 0 \
                or type(payload['access_issued_at']) is not int or type(payload['access_expires_at']) is not int \
                or payload['access_issued_at'] > preparation._aware(row.created_at).timestamp() \
                or preparation._aware(row.created_at) > preparation._aware(event.occurred_at) \
                or not payload['access_issued_at'] <= preparation._aware(event.occurred_at).timestamp() < payload['access_expires_at']:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        _fail('execution_evidence_invalid')
    if 'handoff' in payload:
        from .inventory_control_handoff import prove_execution_handoff
        prove_execution_handoff(db, row=row, event=event)
    return dict(decision=authority._json(authority._result(row)), execution_audit_event_id=str(event.id),
        recorded=True, projection_published=False, start_ready=False)


def read_inventory_control_configuration(db, *, access_token, expected_authorization_version, command, review_sha256):
    """Recover exactly this request; missing is not permission to blind-retry."""
    command, principal, session, claims, settings = _context(db, access_token, expected_authorization_version, command,
        purpose='status', review_sha256=review_sha256)
    _require_digest(review_sha256)
    row = _existing(db, principal, command)
    result = _receipt(db, row, review_sha256) if row else dict(recorded=False, projection_published=False, start_ready=False)
    _finish(db, principal, session, claims, settings)
    return result


def _require_digest(value):
    if not isinstance(value, str) or re.fullmatch('[a-f0-9]{64}', value) is None:
        _fail('invalid_review_digest')


def execute_inventory_control_configuration(db, *, access_token, expected_authorization_version, command, review_sha256):
    """Exact reviewed command plus live session evidence, one rollback boundary."""
    if db.new or db.dirty or db.deleted:
        _fail('requires_clean_session')
    _require_digest(review_sha256)
    preparation._begin_outer(db)
    with db.begin_nested():
        command, principal, session, claims, settings = _context(db, access_token, expected_authorization_version, command,
            purpose='execute', review_sha256=review_sha256)
        prior = _existing(db, principal, command)
        if prior is not None:
            result = _receipt(db, prior, review_sha256)
        else:
            reviewed = _review(db, principal, command)
            if reviewed['review_sha256'] != review_sha256:
                _fail('review_changed')
            result = authority.record_inventory_control_authority(db, actor=principal, command=command)
            row = db.get(Decision, result['decision_id'])
            now = authority._now(db)
            _session_valid(session, claims, settings, now)
            append_audit_event(db, stream_key='authorization', actor_user_id=principal.user_id,
                action='inventory_control.configuration.execute', aggregate_type='inventory_control_authority_decision',
                aggregate_id=str(row.id), request_id='control-configuration:' + str(row.id), before_jsonb={},
                after_jsonb=dict(schema_version='rsc.inventory_control_configuration_execution.v1',
                    decision_id=str(row.id), decision_sha256=row.payload_sha256, decision_audit_event_id=str(row.audit_event_id),
                    request_sha256=row.request_sha256, review_sha256=review_sha256,
                    actor_authorization_version=principal.authorization_version, auth_session_id=session.id,
                    access_issued_at=claims['iat'], access_expires_at=claims['exp'],
                    **({'handoff': claims['handoff']} if 'handoff' in claims else {})), occurred_at=now, created_at=now)
            db.flush()
            result = _receipt(db, row, review_sha256)
        # Audit-head contention must not let an expired access token finish.
        _finish(db, principal, session, claims, settings)
        return result
