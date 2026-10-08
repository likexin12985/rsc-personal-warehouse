"""Read-side closure proof; SQLite fixtures do not prove DB seal persistence."""
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from app.foundation_models import AuditEvent, AuditChainHead, StateTransitionEvent
from app.inventory_models import CustodyAssignment
from app.formal_access import load_formal_principal
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.authentication_audit import append_authentication_event, add_authentication_state_transition
from app.formal_services.stock_loss_corrections import return_condition_seal_admission as admission
from app.formal_services.stock_loss_corrections import return_condition_seal_reads as seals
from app.formal_services.stock_loss_corrections import return_condition_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_request_seals as writer
from test_return_condition_seal_admission import permission
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived,
    ready, parcel, acceptance, prepared, regional_opening, context, regional_source, ERRORS,
)

pytestmark = pytest.mark.parametrize('stock,command_name',
    [('quantity', 'quantity_command'), ('serial', 'serial_command')], indirect=['stock'])


def retained_fixture(db, actor, command):
    prepared = admission.authorize_absence_seal(db, actor=actor, request=command)
    basis = prepared.basis; at = datetime.now(timezone.utc)
    document = json.loads(prepared.original_input_json)
    row = dict(id=uuid4(), created_at=at, actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version, request_id=command.request_id,
        reason=command.reason, request_hash=prepared.original_input_hash,
        idempotency_key_hash=document['idempotency_key_hash'], command_jsonb=document,
        kind='submit', inbound_id=prepared.inbound_id, inbound_line_id=basis.inbound_line_id,
        root_disposition_id=basis.root_disposition_id, source_account_id=basis.source.id,
        original_transaction_id=basis.original_transaction_id, original_movement_id=basis.original_movement_id,
        original_ledger_cursor=basis.original_ledger_cursor, **dict(prepared.key_aliases))
    db.execute(seals.table().insert(), row)
    event = append_audit_event(db, stream_key='inventory', actor_user_id=actor.user_id,
        action='seal_condition_request', aggregate_type='stock_condition_request_seal', aggregate_id=str(row['id']),
        before_jsonb={}, after_jsonb=seals.payload(row), request_id='condition-seal:' + str(row['id']),
        occurred_at=at, created_at=at)
    db.commit()
    return row, event.id


def test_closed_original_read_survives_action_revocation_and_custody_end(
        db, regional_source, request, command_name):
    c = regional_source
    command = request.getfixturevalue(command_name).model_copy(update={'expected_source_hash': 'a' * 64})
    before = snapshot(db)
    with pytest.raises(RuntimeError, match='requires PostgreSQL'):
        writer.seal(db, actor=c.actor, request=command)
    assert snapshot(db) == before
    row, audit_id = retained_fixture(db, c.actor, command)
    after = snapshot(db)
    assert {name for name in before if before[name] != after[name]} == {
        'stock_condition_request_seals', 'audit_events', 'audit_chain_heads'}
    # Separate authentication evidence may use the same UUID in its own domain.
    # It must neither count as stock execution nor hide a reserved seal marker.
    if db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key == 'authentication')) is None:
        db.add(AuditChainHead(stream_key='authentication', version=0)); db.flush()
    at = datetime.now(timezone.utc); correlation = uuid4().hex
    auth = append_authentication_event(db, actor_user_id=c.actor.user_id, action='owned_fixture',
        aggregate_type='authentication_attempt', aggregate_id=str(row['id']), request_id=correlation,
        client_type='web', outcome='rejected', reason_code='owned_fixture', occurred_at=at)
    state = add_authentication_state_transition(db, actor_user_id=c.actor.user_id,
        aggregate_type='authentication_attempt', aggregate_id=str(row['id']), request_id=correlation,
        from_status=None, to_status='rejected', reason_code='owned_fixture', occurred_at=at)
    auth_id, state_id = auth.id, state.id
    permission(db, c.regional_role, 'stock_operation', 'submit_return_condition').effect = 'deny'
    db.get(CustodyAssignment, c.custody.id).valid_to = datetime.now(timezone.utc)
    db.commit()
    actor = load_formal_principal(db, c.actor.user_id)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        result = recovery.lookup(db, actor=actor, request=command)
        assert result['request_state'] == 'sealed' and result['absence_sealed'] is True
        assert result['result'] is None and result['stock_effect'] == 'none'
        assert result['result_scope'] == 'closed_original_request'
        assert result['retry_allowed'] is False and result['current_stock_verified'] is False
        assert result['seal']['command_jsonb']['expected_source_hash'] == 'a' * 64
        assert result['seal'] == seals.payload(row)
        assert snapshot(db) == before
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
    for change in ({'reason': 'different original reason'}, {'idempotency_key': uuid4().hex}):
        with pytest.raises(ERRORS) as error:
            recovery.lookup(db, actor=actor, request=command.model_copy(update=change))
        assert error.value.code == 'return_condition_original_input_conflict'
        db.rollback()
    # SQLite corruption probes are read-side failures, never native guard proof.
    for change in ('hash', 'source', 'detached_audit', 'audit_time', 'reserved_auth_marker', 'malformed_auth_state'):
        if change == 'hash':
            db.execute(seals.table().update().where(seals.table().c.id == row['id']).values(request_hash='f' * 64))
        elif change == 'source':
            db.execute(seals.table().update().where(seals.table().c.id == row['id']).values(original_ledger_cursor=1))
        elif change == 'detached_audit':
            event = db.get(AuditEvent, audit_id)
            event.aggregate_id = str(uuid4())
            event.request_id = 'detached-' + uuid4().hex
            db.flush()
        elif change == 'reserved_auth_marker':
            db.get(AuditEvent, auth_id).action = 'seal_condition_request'
            db.flush()
        elif change == 'malformed_auth_state':
            auth_state = db.get(StateTransitionEvent, state_id)
            auth_state.metadata_jsonb = {**auth_state.metadata_jsonb, 'operation': 'unrecognized'}
            db.flush()
        else:
            db.get(AuditEvent, audit_id).occurred_at = datetime.now(timezone.utc)
            db.flush()
        with pytest.raises(ERRORS):
            recovery.lookup(db, actor=actor, request=command)
        db.rollback()
        assert snapshot(db) == before
    permission(db, c.regional_role, 'inventory', 'read').effect = 'deny'
    db.flush()
    with pytest.raises(ERRORS):
        recovery.lookup(db, actor=actor, request=command)
    db.rollback()
    assert snapshot(db) == before
