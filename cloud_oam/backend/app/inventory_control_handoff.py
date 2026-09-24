"""Audited, purpose-bound Ed25519 control commands and owner acknowledgements.

The API signs only a current reviewer's exact command. Owner tools verify that
signature, the stored issuance audit, deployment/database identity and the live
session before touching private control facts. Neither direction carries a JWT.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select

from .config import get_settings
from .foundation_models import AuditChainHead
from .formal_services.audit_chain import append_audit_event, verify_audit_event_in_read_snapshot
from . import inventory_control_configuration as configuration
from . import inventory_control_authority as authority
from . import inventory_control_preparation as preparation

TTL = 300
MAX_ENVELOPE_BYTES = 2 * 1024 * 1024


class ControlHandoffError(RuntimeError):
    pass


@dataclass(frozen=True)
class SignedControlRequest:
    envelope: dict = field(repr=False)


def _fail(code):
    raise ControlHandoffError('control_handoff_' + code)


def _bytes(value):
    try:
        data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        _fail('invalid_document')
    if len(data) > MAX_ENVELOPE_BYTES:
        _fail('document_too_large')
    return data


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        _fail('invalid_document')
    return value


def _hash(value):
    if not isinstance(value, str) or re.fullmatch('[a-f0-9]{64}', value) is None:
        _fail('invalid_document')
    return value


def _keys(side, settings=None):
    settings = settings if settings is not None else get_settings()
    if not settings.control_configuration_handoff_enabled:
        _fail('disabled')
    try:
        deployment = _uuid(settings.control_configuration_deployment_id)
        private_text = getattr(settings, f'control_configuration_{side}_private_key')
        public_text = getattr(settings, f'control_configuration_{"owner" if side == "api" else "api"}_public_key')
        # A deployed process must never hold both signing authorities.
        if settings.environment == 'production' and getattr(settings,
                f'control_configuration_{"owner" if side == "api" else "api"}_private_key'):
            raise ValueError()
        if any(re.fullmatch('[a-f0-9]{64}', value) is None for value in (private_text, public_text)):
            raise ValueError()
        private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_text))
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_text))
        if private.public_key().public_bytes_raw() == public.public_bytes_raw():
            raise ValueError()
    except Exception:
        _fail('unconfigured')
    return private, public, deployment


def _sign(payload, private, direction):
    return dict(payload=payload, key_id=hashlib.sha256(private.public_key().public_bytes_raw()).hexdigest()[:16],
                signature=private.sign(('rsc-control-' + direction + '-v1\n').encode() + _bytes(payload)).hex())


def _verify(envelope, public, direction):
    try:
        if not isinstance(envelope, dict) or set(envelope) != {'payload', 'key_id', 'signature'} \
                or envelope['key_id'] != hashlib.sha256(public.public_bytes_raw()).hexdigest()[:16] \
                or not isinstance(envelope['payload'], dict) \
                or not isinstance(envelope['signature'], str) or re.fullmatch('[a-f0-9]{128}', envelope['signature']) is None:
            raise ValueError()
        public.verify(bytes.fromhex(envelope['signature']),
                      ('rsc-control-' + direction + '-v1\n').encode() + _bytes(envelope['payload']))
    except Exception:
        _fail('signature_invalid')
    # Detach the signed object from caller-owned mutable JSON containers.
    return json.loads(_bytes(envelope['payload']))


def _database_id(db):
    value = db.scalar(select(AuditChainHead.id).where(AuditChainHead.stream_key == 'authorization'))
    if value is None:
        _fail('database_identity_missing')
    return str(value)


def _request_shape(payload):
    keys = {'schema_version', 'handoff_id', 'purpose', 'deployment_id', 'database_id', 'actor_user_id',
            'actor_person_id', 'authorization_version', 'auth_session_id', 'access_issued_at', 'access_expires_at',
            'issued_at', 'expires_at', 'command', 'review_sha256', 'audit_event_id'}
    if set(payload) != keys or payload['schema_version'] != 'rsc.control_configuration_request.v1' \
            or payload['purpose'] not in ('preview', 'execute', 'status'):
        _fail('invalid_document')
    for key in ('handoff_id', 'deployment_id', 'database_id', 'actor_user_id', 'actor_person_id', 'auth_session_id', 'audit_event_id'):
        _uuid(payload[key])
    for key in ('authorization_version', 'access_issued_at', 'access_expires_at', 'issued_at', 'expires_at'):
        if type(payload[key]) is not int or not 0 < payload[key] < 253402300799:
            _fail('invalid_document')
    if not payload['access_issued_at'] <= payload['issued_at'] < payload['expires_at'] <= payload['access_expires_at'] \
            or payload['expires_at'] - payload['issued_at'] > TTL:
        _fail('invalid_document')
    try:
        command = authority.AuthorityCommand.model_validate(payload['command'])
        if authority._json(command.model_dump()) != payload['command']:
            raise ValueError()
    except (ValueError, TypeError):
        _fail('invalid_document')
    if payload['purpose'] == 'preview':
        if payload['review_sha256'] is not None:
            _fail('invalid_document')
    else:
        _hash(payload['review_sha256'])
    return command


def _prove_issuance(db, payload):
    _request_shape(payload)
    document = {key: value for key, value in payload.items() if key != 'audit_event_id'}
    event = verify_audit_event_in_read_snapshot(db, stream_key='authorization', event_id=UUID(payload['audit_event_id']))
    if event.action != 'inventory_control.authority.handoff_issued' or event.actor_user_id != payload['actor_user_id'] \
            or event.aggregate_type != 'inventory_control_handoff' or event.aggregate_id != payload['handoff_id'] \
            or event.request_id != 'control-handoff:' + payload['handoff_id'] or event.before_jsonb != {} \
            or event.after_jsonb != dict(request=document, request_sha256=preparation._sha(document)) \
            or int(preparation._aware(event.occurred_at).timestamp()) != payload['issued_at']:
        _fail('issuance_evidence_invalid')


def _current_api_actor(db, token, actor, expected_version):
    principal, session, claims, settings = configuration._operator_context(db,
        configuration._claims(token, get_settings()), expected_version)
    if principal != actor:
        _fail('actor_changed')
    return principal, session, claims, settings


def issue_control_handoff(db, *, access_token, actor, expected_version, command, purpose, owner_response=None):
    """The API issues an audited bounded capability, never a control decision."""
    if db.new or db.dirty or db.deleted:
        _fail('requires_clean_session')
    signer, owner_key, deployment = _keys('api')
    if purpose not in ('preview', 'execute', 'status'):
        _fail('invalid_purpose')
    command = authority.AuthorityCommand.model_validate(command.model_dump())
    preparation._begin_outer(db)
    with db.begin_nested():
        principal, session, claims, settings = _current_api_actor(db, access_token, actor, expected_version)
        review_sha = None
        if purpose != 'preview':
            response = _accepted_response(db, owner_response, signer.public_key(), owner_key, deployment,
                principal, command, allow_history=purpose == 'status')
            original = response['request']['payload']
            if purpose == 'execute' and original['purpose'] != 'preview':
                _fail('preview_required')
            review_sha = response['result']['review_sha256'] if original['purpose'] == 'preview' else original['review_sha256']
        elif owner_response is not None:
            _fail('invalid_purpose')
        now = authority._now(db)
        issued = int(now.timestamp())
        expires = min(issued + TTL, claims['exp'], int(preparation._aware(session.expires_at).timestamp()))
        if expires <= issued:
            _fail('expired')
        document = dict(schema_version='rsc.control_configuration_request.v1', handoff_id=str(uuid4()), purpose=purpose,
            deployment_id=deployment, database_id=_database_id(db), actor_user_id=principal.user_id,
            actor_person_id=str(principal.person_id), authorization_version=principal.authorization_version,
            auth_session_id=session.id, access_issued_at=claims['iat'], access_expires_at=claims['exp'],
            issued_at=issued, expires_at=expires, command=authority._json(command.model_dump()), review_sha256=review_sha)
        audit = append_audit_event(db, stream_key='authorization', actor_user_id=principal.user_id,
            action='inventory_control.authority.handoff_issued', aggregate_type='inventory_control_handoff',
            aggregate_id=document['handoff_id'], request_id='control-handoff:' + document['handoff_id'], before_jsonb={},
            after_jsonb=dict(request=document, request_sha256=preparation._sha(document)), occurred_at=now, created_at=now)
        payload = dict(document, audit_event_id=str(audit.id))
        _prove_issuance(db, payload)
        configuration._finish(db, principal, session, claims, settings)
        if authority._now(db).timestamp() >= expires:
            _fail('expired')
        return _sign(payload, signer, 'request')


def verify_owner_request(db, request, *, purpose, command, expected_version, review_sha256):
    _, api_key, deployment = _keys('owner')
    payload = _verify(request.envelope, api_key, 'request')
    _request_shape(payload)
    if payload['deployment_id'] != deployment or payload['database_id'] != _database_id(db):
        _fail('target_mismatch')
    if payload['purpose'] != purpose or payload['command'] != authority._json(command.model_dump()) \
            or payload['authorization_version'] != expected_version or payload['review_sha256'] != review_sha256:
        _fail('command_mismatch')
    _prove_issuance(db, payload)
    if not payload['issued_at'] <= authority._now(db).timestamp() < payload['expires_at']:
        _fail('expired')
    return dict(sub=payload['actor_user_id'], sid=payload['auth_session_id'],
        iat=payload['access_issued_at'], exp=payload['access_expires_at'],
        handoff_actor_person_id=payload['actor_person_id'],
        handoff=dict(handoff_id=payload['handoff_id'], purpose=purpose, audit_event_id=payload['audit_event_id'],
                     payload_sha256=preparation._sha(payload), issued_at=payload['issued_at'], expires_at=payload['expires_at']))


def prove_execution_handoff(db, *, row, event):
    evidence = event.after_jsonb['handoff']
    if not isinstance(evidence, dict) or set(evidence) != {'handoff_id', 'purpose', 'audit_event_id', 'payload_sha256', 'issued_at', 'expires_at'}:
        _fail('execution_evidence_invalid')
    audit = verify_audit_event_in_read_snapshot(db, stream_key='authorization', event_id=UUID(_uuid(evidence['audit_event_id'])))
    payload = dict(audit.after_jsonb.get('request', {}), audit_event_id=str(audit.id))
    _prove_issuance(db, payload)
    if payload['purpose'] != 'execute' or evidence != dict(handoff_id=payload['handoff_id'], purpose=payload['purpose'],
            audit_event_id=str(audit.id), payload_sha256=preparation._sha(payload), issued_at=payload['issued_at'], expires_at=payload['expires_at']) \
            or payload['actor_user_id'] != row.actor_user_id or payload['actor_person_id'] != str(row.actor_person_id) \
            or payload['authorization_version'] != row.actor_authorization_version \
            or payload['command'] != row.payload_jsonb['request'] \
            or payload['review_sha256'] != event.after_jsonb['review_sha256'] \
            or payload['auth_session_id'] != event.after_jsonb['auth_session_id'] \
            or payload['access_issued_at'] != event.after_jsonb['access_issued_at'] \
            or payload['access_expires_at'] != event.after_jsonb['access_expires_at'] \
            or not payload['issued_at'] <= preparation._aware(row.created_at).timestamp() \
                <= preparation._aware(event.occurred_at).timestamp() < payload['expires_at']:
        _fail('execution_evidence_invalid')


def run_owner_handoff(db, envelope):
    """Process one signed request; the caller must commit before releasing its response."""
    if db.new or db.dirty or db.deleted:
        _fail('requires_clean_session')
    preparation._begin_outer(db)
    with db.begin_nested():
        return _run_owner_handoff(db, envelope)


def _run_owner_handoff(db, envelope):
    signer, api_key, deployment = _keys('owner')
    envelope = json.loads(_bytes(envelope))
    payload = _verify(envelope, api_key, 'request')
    command = _request_shape(payload)
    functions = dict(preview=configuration.preview_inventory_control_configuration,
        execute=configuration.execute_inventory_control_configuration, status=configuration.read_inventory_control_configuration)
    kwargs = dict(access_token=SignedControlRequest(envelope), expected_authorization_version=payload['authorization_version'], command=command)
    if payload['purpose'] != 'preview': kwargs['review_sha256'] = payload['review_sha256']
    result = functions[payload['purpose']](db, **kwargs)
    now = authority._now(db)
    if now.timestamp() >= payload['expires_at']:
        _fail('expired')
    response = dict(schema_version='rsc.control_configuration_response.v1', deployment_id=deployment,
        database_id=_database_id(db), issued_at=int(now.timestamp()), expires_at=int(now.timestamp()) + TTL,
        request=json.loads(_bytes(envelope)), result=result)
    # Caller keeps this private until COMMIT is acknowledged. Rollback, signing
    # failure and unknown COMMIT outcomes never produce a releasable receipt.
    signed = _sign(response, signer, 'response')
    if authority._now(db).timestamp() >= payload['expires_at']:
        _fail('expired')
    return signed


def _accepted_response(db, envelope, api_key, owner_key, deployment, actor, command, *, allow_history):
    payload = _verify(envelope, owner_key, 'response')
    if set(payload) != {'schema_version', 'deployment_id', 'database_id', 'issued_at', 'expires_at', 'request', 'result'} \
            or payload['schema_version'] != 'rsc.control_configuration_response.v1' \
            or payload['deployment_id'] != deployment or payload['database_id'] != _database_id(db):
        _fail('target_mismatch')
    original = _verify(payload['request'], api_key, 'request')
    _prove_issuance(db, original)
    now = authority._now(db).timestamp()
    if any(type(payload[key]) is not int for key in ('issued_at', 'expires_at')) \
            or not original['issued_at'] <= payload['issued_at'] < original['expires_at'] \
            or payload['issued_at'] > now or payload['expires_at'] - payload['issued_at'] != TTL:
        _fail('invalid_response')
    if not allow_history and (now >= payload['expires_at'] or original['authorization_version'] != actor.authorization_version):
        _fail('review_expired')
    if original['deployment_id'] != deployment or original['database_id'] != payload['database_id'] \
            or original['actor_user_id'] != actor.user_id or original['actor_person_id'] != str(actor.person_id) \
            or original['command'] != authority._json(command.model_dump()):
        _fail('command_mismatch')
    result = payload['result']
    if not isinstance(result, dict) or result.get('projection_published') is not False or result.get('start_ready') is not False:
        _fail('invalid_response')
    if original['purpose'] == 'preview':
        review = result.get('review')
        if set(result) != {'review', 'review_sha256', 'projection_published', 'start_ready'} or not isinstance(review, dict) \
                or result['review_sha256'] != preparation._sha(review) or review.get('command') != original['command'] \
                or review.get('actor_user_id') != original['actor_user_id'] or review.get('actor_person_id') != original['actor_person_id'] \
                or review.get('actor_authorization_version') != original['authorization_version']:
            _fail('invalid_response')
    else:
        if type(result.get('recorded')) is not bool or (original['purpose'] == 'execute' and result['recorded'] is not True):
            _fail('invalid_response')
        if result['recorded']:
            if set(result) != {'decision', 'execution_audit_event_id', 'recorded', 'projection_published', 'start_ready'}:
                _fail('invalid_response')
            _uuid(result['execution_audit_event_id'])
            decision = result['decision']
            if not isinstance(decision, dict) or set(decision) != {'decision_id', 'action', 'binding_id', 'catalog_id',
                    'payload_sha256', 'audit_event_id', 'created_at', 'valid_from', 'valid_to'} or decision.get('action') != command.action \
                    or decision.get('binding_id') != str(command.binding_id) \
                    or decision.get('catalog_id') != (str(command.catalog_id) if command.catalog_id else None):
                _fail('invalid_response')
            _uuid(decision.get('decision_id')); _uuid(decision.get('audit_event_id')); _hash(decision.get('payload_sha256'))
            from pydantic import AwareDatetime, TypeAdapter
            try:
                for key in ('created_at', 'valid_from', 'valid_to'):
                    if key == 'valid_to' and decision[key] is None:
                        continue
                    if not isinstance(decision[key], str):
                        raise ValueError()
                    TypeAdapter(AwareDatetime).validate_python(decision[key])
            except (ValueError, TypeError):
                _fail('invalid_response')
        elif set(result) != {'recorded', 'projection_published', 'start_ready'}:
            _fail('invalid_response')
    return payload


def inspect_owner_response(db, *, access_token, actor, expected_version, command, envelope):
    signer, owner_key, deployment = _keys('api')
    principal, session, claims, settings = _current_api_actor(db, access_token, actor, expected_version)
    payload = _accepted_response(db, envelope, signer.public_key(), owner_key, deployment, principal, command, allow_history=True)
    configuration._finish(db, principal, session, claims, settings)
    return dict(result=payload['result'], purpose=payload['request']['payload']['purpose'],
        can_execute=payload['request']['payload']['purpose'] == 'preview'
            and authority._now(db).timestamp() < payload['expires_at']
            and payload['request']['payload']['authorization_version'] == principal.authorization_version,
        issued_at=payload['issued_at'], expires_at=payload['expires_at'])
