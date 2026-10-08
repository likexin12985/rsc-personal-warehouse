"""Real historical fixtures; read-only seal preparation, not durable sealing."""
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.foundation_models import OutboxEvent, Permission, RolePermission
from app.inventory_models import CustodyAssignment
from app.models import User
from app.stock_operation_models import StockOperationReturnInbound
from app.formal_access import load_formal_principal
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_loss_corrections import return_condition_seal_admission as subject
from app.formal_services.stock_loss_corrections import return_condition_authority as authority
from app.formal_services.stock_loss_corrections import return_condition_request_inputs as inputs
from app.formal_services.stock_loss_corrections import return_condition_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived,
    ready, parcel, acceptance, prepared, regional_opening, reader_tables, context, regional_source, ERRORS,
)

pytestmark = pytest.mark.parametrize('stock,command_name',
    [('quantity', 'quantity_command'), ('serial', 'serial_command')], indirect=['stock'])


def permission(db, role, resource, action):
    return db.scalar(select(RolePermission).join(Permission).where(
        RolePermission.role_id == role.id, Permission.resource == resource,
        Permission.action == action, Permission.field_code == ''))


def test_expired_custody_can_close_old_coordinate_without_new_stock_authority(
        db, regional_source, request, command_name):
    c = regional_source
    command = request.getfixturevalue(command_name)
    # The unsaved preflight cannot be reconstructed from current stock. Preserve
    # its exact old hash; accepting closure must not label that hash as verified.
    command = command.model_copy(update={'expected_source_hash': 'a' * 64})
    db.get(CustodyAssignment, c.custody.id).valid_to = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(ERRORS):
        authority.authorize_submission(db, actor=c.actor, inbound_line_id=c.line)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        admitted = subject.authorize_absence_seal(db, actor=c.actor, request=command)
        assert admitted.actor == c.actor
        assert admitted.inbound_id == c.issue.inbound_id
        assert admitted.basis.source.id == c.source.id
        assert admitted.basis.inbound_line_id == c.line
        assert json.loads(admitted.original_input_json) == inputs.canonical(command)
        assert admitted.original_input_hash == posting._canonical_hash(inputs.canonical(command))
        assert command.idempotency_key not in admitted.original_input_json
        assert dict(admitted.key_aliases)['condition_key_hash'] == inputs.canonical(command)['idempotency_key_hash']
        assert not admitted.absence_sealed and not admitted.retry_allowed and not admitted.current_stock_verified
        assert not db.new and not db.dirty and snapshot(db) == before
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


def test_current_permission_identity_and_original_custodian_before_history(
        db, regional_source, request, command_name, monkeypatch):
    c = regional_source
    command = request.getfixturevalue(command_name)
    before = snapshot(db)
    def unexpected(*args, **kwargs):
        raise AssertionError('unauthorized seal traversed full historical evidence')
    monkeypatch.setattr(subject.history, 'read', unexpected)
    for resource, action in (('inventory', 'read'), ('stock_operation', 'read'),
                              ('stock_operation', 'submit_return_condition')):
        permission(db, c.regional_role, resource, action).effect = 'deny'
        db.flush()
        with pytest.raises(ERRORS):
            subject.authorize_absence_seal(db, actor=c.actor, request=command)
        db.rollback()
        assert snapshot(db) == before
    for change in ('inactive', 'version'):
        user = db.get(User, c.actor.user_id)
        if change == 'inactive':
            user.is_active = False
        else:
            user.authorization_version += 1
        db.flush()
        with pytest.raises(ERRORS):
            subject.authorize_absence_seal(db, actor=c.actor, request=command)
        db.rollback()
        assert snapshot(db) == before
    for other in (c.reviewer, c.hq):
        with pytest.raises(ERRORS):
            subject.authorize_absence_seal(db,
                actor=load_formal_principal(db, other.user_id), request=command)
        assert snapshot(db) == before


def test_conflicts_corrupt_history_and_late_revocation_never_admit_closure(
        db, regional_source, request, command_name, monkeypatch):
    c = regional_source
    command = request.getfixturevalue(command_name)
    before = snapshot(db)
    # Real legacy inbound coordinate conflicts, even with a fresh key.
    header = db.get(StockOperationReturnInbound, c.issue.inbound_id)
    conflict = command.model_copy(update={'request_id': header.request_id})
    with pytest.raises(ERRORS):
        subject.authorize_absence_seal(db, actor=c.actor, request=conflict)
    assert snapshot(db) == before
    header.request_hash = 'f' * 64
    db.flush()
    with pytest.raises(ERRORS):
        subject.authorize_absence_seal(db, actor=c.actor, request=command)
    db.rollback()
    assert snapshot(db) == before
    # Unknown ordinary messages are evidence too; no condition event is forged.
    at = datetime.now(timezone.utc)
    db.add(OutboxEvent(idempotency_key='synthetic-seal:' + uuid4().hex,
        event_type='unresolved.business_event', aggregate_type='unresolved_business',
        aggregate_id=str(uuid4()), payload_jsonb={'request_id': command.request_id},
        status='pending', attempts=0, available_at=at, created_at=at, updated_at=at))
    db.flush()
    with pytest.raises(ERRORS):
        subject.authorize_absence_seal(db, actor=c.actor, request=command)
    db.rollback()
    assert snapshot(db) == before
    original = subject.history.read
    for resource, action in (('stock_operation', 'submit_return_condition'), ('inventory', 'read')):
        calls = []
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(True)
            permission(db, c.regional_role, resource, action).effect = 'deny'
            db.flush()
            return result
        with monkeypatch.context() as patch:
            patch.setattr(subject.history, 'read', revoke)
            with pytest.raises(ERRORS):
                subject.authorize_absence_seal(db, actor=c.actor, request=command)
        assert calls == [True]
        db.rollback()
        assert snapshot(db) == before


def test_executed_original_stays_readable_after_write_revocation_but_is_not_absent(
        db, regional_source, request, command_name):
    c = regional_source
    command = request.getfixturevalue(command_name)
    result = writer.submit(db, actor=c.actor, request=command)
    db.commit()
    before = snapshot(db)
    with pytest.raises(ERRORS):
        subject.authorize_absence_seal(db, actor=c.actor, request=command)
    assert snapshot(db) == before
    permission(db, c.regional_role, 'stock_operation', 'submit_return_condition').effect = 'deny'
    db.commit()
    current = load_formal_principal(db, c.actor.user_id)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        found = recovery.lookup(db, actor=current, request=command)
        assert found['request_state'] == 'found' and found['result'] == result
        assert found['retry_allowed'] is False and found['absence_sealed'] is False
        with pytest.raises(ERRORS):
            subject.authorize_absence_seal(db, actor=current, request=command)
        assert snapshot(db) == before
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
