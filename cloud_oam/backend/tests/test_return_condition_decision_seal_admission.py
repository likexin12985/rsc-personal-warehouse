"""Real historical/service fixtures; preparation is not durable PG closure."""
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission
from app.inventory_models import CustodyAssignment
from app.formal_services.stock_loss_corrections import return_condition_authority as authority
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_decision_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_decision_seal_admission as subject
from test_return_condition_decisions import start, make_request
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived,
    ready, parcel, acceptance, prepared, regional_opening, reader_tables, context, regional_source, ERRORS,
)

pytestmark = pytest.mark.parametrize('stock,command_name',
    [('quantity', 'quantity_command'), ('serial', 'serial_command')], indirect=['stock'])


def review_grant(db, c):
    return db.scalar(select(RolePermission).join(Permission).where(
        RolePermission.role_id == c.regional_role.id,
        Permission.resource == 'stock_operation',
        Permission.action == 'review_return_condition_regional', Permission.field_code == ''))


def test_stale_missing_decision_can_prepare_closure_without_new_stock_authority(
        db, regional_source, request, command_name):
    c = regional_source
    result = start(db, c, request.getfixturevalue(command_name)); db.commit()
    actor, missing = make_request(db, c.reviewer, result, 'return_evidence')
    # Exact old input is retained even when its claimed preflight cannot be proven.
    missing = missing.model_copy(update={'expected_event_hash': 'a' * 64})
    actor, executed = make_request(db, c.reviewer, result, 'return_evidence')
    moved = decisions.decide(db, actor=actor, request=executed); db.commit()
    assert moved['status'] != result['status']
    db.get(CustodyAssignment, c.custody.id).valid_to = datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(ERRORS):
        authority.authorize_action(db, actor=actor, case_id=missing.case_id,
            expected_event_id=missing.expected_event_id, kind=missing.action)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        admitted = subject.authorize_absence_seal(db, actor=actor, request=missing)
        assert json.loads(admitted.original_input_json) == subject.canonical(missing)
        assert missing.idempotency_key not in admitted.original_input_json
        assert admitted.basis.inbound_line_id == c.line
        assert admitted.action == 'review_return_condition_regional'
        assert not admitted.absence_sealed and not admitted.retry_allowed
        assert not admitted.current_stock_verified and not admitted.original_preflight_verified
        assert not db.new and not db.dirty and snapshot(db) == before
        # An actual committed decision never becomes an absent coordinate.
        with pytest.raises(ERRORS):
            subject.authorize_absence_seal(db, actor=actor, request=executed)
        found = recovery.lookup(db, actor=actor, request=executed)
        assert found['request_state'] == 'found' and found['result'] == moved
        assert snapshot(db) == before
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


def test_current_scope_independence_and_late_revocation_are_rechecked(
        db, regional_source, request, command_name, monkeypatch):
    c = regional_source
    result = start(db, c, request.getfixturevalue(command_name)); db.commit()
    actor, command = make_request(db, c.reviewer, result, 'return_evidence')
    before = snapshot(db)
    def unexpected(*args, **kwargs):
        raise AssertionError('unauthorized closure traversed full history')
    with monkeypatch.context() as patch:
        patch.setattr(subject.history, 'read', unexpected)
        with pytest.raises(ERRORS):
            subject.authorize_absence_seal(db, actor=load_formal_principal(db, c.actor.user_id), request=command)
        for changes in ({'case_id': uuid4()}, {'expected_event_id': uuid4()}):
            with pytest.raises(ERRORS):
                subject.authorize_absence_seal(db, actor=actor, request=command.model_copy(update=changes))
        review_grant(db, c).effect = 'deny'; db.flush()
        with pytest.raises(ERRORS):
            subject.authorize_absence_seal(db, actor=actor, request=command)
        db.rollback()
    assert snapshot(db) == before
    real_read = subject.history.read
    def revoke_after_history(*args, **kwargs):
        proved = real_read(*args, **kwargs)
        review_grant(db, c).effect = 'deny'; db.flush()
        return proved
    with monkeypatch.context() as patch:
        patch.setattr(subject.history, 'read', revoke_after_history)
        with pytest.raises(ERRORS):
            subject.authorize_absence_seal(db, actor=actor, request=command)
        db.rollback()
    assert snapshot(db) == before
