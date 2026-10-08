"""Real service decisions and historical recovery; SQLite is not PG COMMIT proof.

The full old source, source freeze, identities, audit, effects and history reader
are real. Object storage and the SQLite key registrar are explicit test doubles
in the shared fixture. Native roles/late revocation require separate PG gates.
"""
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import FileObject
from app.return_condition_decision_requests import ConditionDecision
from app.formal_services.stock_loss_corrections import return_condition_submission as initial
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_decision_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_history_read as history
from app.formal_services.stock_loss_corrections import return_condition_recovery as initial_recovery
from app.formal_services.stock_loss_corrections import return_condition_business_events as business
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived,
    ready, parcel, acceptance, prepared, regional_opening, context, regional_source, ERRORS, allow,
)
from test_return_condition_evidence import create, finish
from test_formal_files_service import FakeStorage


def start(db, c, command):
    for role in (c.regional_role, c.hq_role):
        for resource in ('inventory', 'stock_operation'):
            allow(db, role, resource, 'read')
    db.commit()
    # No grants are added by the product; the owned fixture supplies them.
    c.actor = load_formal_principal(db, c.actor.user_id)
    return initial.submit(db, actor=c.actor, request=command)


def stock_facts(db):
    values = snapshot(db)
    return {name: values[name] for name in ('stock_accounts', 'stock_balances',
        'inventory_transactions', 'inventory_movements', 'inventory_movement_serials',
        'serial_current_positions', 'inventory_ledger_heads', 'stock_condition_cases',
        'stock_condition_serials', 'stock_operation_orders', 'stock_operation_lines')}


def make_request(db, actor, previous, kind):
    actor = load_formal_principal(db, actor.user_id)
    files = ()
    if kind in ('supplement', 'verify_region'):
        upload = SimpleNamespace(actor=actor, storage=FakeStorage())
        row = db.get(FileObject, create(db, upload).file_id)
        finish(db, upload, row)
        db.commit()
        files = (row.id,)
    request = ConditionDecision(action=kind, case_id=UUID(previous['case_id']),
        expected_event_id=UUID(previous['event_id']), expected_event_hash=previous['request_hash'],
        evidence_file_ids=files, reason='核对准确原事件后处理本次成色纠正',
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    return actor, request


def step(db, actor, previous, kind, *, unchanged):
    actor, command = make_request(db, actor, previous, kind)
    missing = recovery.lookup(db, actor=actor, request=command)
    assert missing['request_state'] == 'unknown' and not missing['retry_allowed']
    result = decisions.decide(db, actor=actor, request=command)
    db.commit()
    assert result['action'] == kind and result['stock_effect'] == 'none'
    assert result['posting_transaction_id'] is None and result['posting_movement_id'] is None
    assert stock_facts(db) == unchanged
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    found = recovery.lookup(db, actor=actor, request=command)
    db.execute(text('PRAGMA query_only=OFF'))
    assert found['request_state'] == 'found' and found['result'] == result
    assert found['current_case_status'] == result['status']
    assert not found['retry_allowed'] and not found['current_stock_verified']
    assert snapshot(db) == before
    db.rollback()
    return result, actor, command


@pytest.mark.parametrize('stock,command_name', [('quantity','quantity_command'),('serial','serial_command')], indirect=['stock'])
def test_full_non_posting_review_and_supplement_path(db, regional_source, request, command_name):
    c = regional_source
    command = request.getfixturevalue(command_name)
    result = start(db, c, command); db.commit()
    unchanged = stock_facts(db)
    retained = []
    for kind, actor in (
        ('return_evidence', c.reviewer), ('supplement', c.actor),
        ('verify_region', c.reviewer), ('return_region', c.hq),
        ('verify_region', c.reviewer), ('approve_hq', c.hq), ('cancel_approved', c.hq),
    ):
        result, actor, action = step(db, actor, result, kind, unchanged=unchanged)
        retained.append((actor, action, result))
    assert result['status'] == 'cancelled_pending_release'
    before = snapshot(db)
    for actor, action, original in retained:
        found = recovery.lookup(db, actor=actor, request=action)
        assert found['result'] == original and found['current_case_status'] == 'cancelled_pending_release'
        for changes in ({'reason':'不同于原请求的理由'}, {'idempotency_key':uuid4().hex},
                {'expected_event_hash':'a'*64}):
            with pytest.raises(ERRORS) as error:
                recovery.lookup(db, actor=actor, request=action.model_copy(update=changes))
            assert error.value.code == 'return_condition_original_input_conflict'
            db.rollback()
            assert snapshot(db) == before
    initial_found = initial_recovery.lookup(db, actor=c.actor, request=command)
    assert initial_found['current_case_status'] == 'cancelled_pending_release'
    assert initial_found['result']['status'] == 'awaiting_regional'
    assert snapshot(db) == before and stock_facts(db) == unchanged


@pytest.mark.parametrize('stock,command_name,prefix,kind,actor_name', [
    ('quantity','quantity_command',(), 'reject_region','reviewer'),
    ('serial','serial_command',('verify_region',), 'reject_hq','hq'),
    ('quantity','quantity_command',(), 'withdraw','actor'),
    ('serial','serial_command',('verify_region',), 'withdraw','actor'),
    ('quantity','quantity_command',('return_evidence',), 'withdraw','actor'),
], indirect=['stock'])
def test_rejection_and_withdrawal_keep_the_frozen_share(db, regional_source, request,
        command_name, prefix, kind, actor_name):
    c = regional_source; command = request.getfixturevalue(command_name)
    result = start(db, c, command); db.commit(); unchanged = stock_facts(db)
    for action in prefix:
        result, _, _ = step(db, c.reviewer, result, action, unchanged=unchanged)
    result, actor, action = step(db, getattr(c, actor_name), result, kind, unchanged=unchanged)
    assert result['status'] == ('cancelled_pending_release' if kind == 'withdraw' else 'rejected_pending_release')
    proved = history.read(db, actor=actor, inbound_line_id=c.line)
    assert proved.graph.projection.held_quantity == command.quantity
    assert proved.graph.projection.corrected_quantity == 0
    before = snapshot(db)
    with pytest.raises(ERRORS):
        decisions.decide(db, actor=actor, request=action)
    db.rollback(); assert snapshot(db) == before


@pytest.mark.parametrize('stock,command_name', [('quantity','quantity_command')], indirect=['stock'])
def test_bad_authority_stale_input_and_effect_failure_never_leave_partial_decision(
        db, regional_source, request, command_name, monkeypatch):
    c = regional_source; command = request.getfixturevalue(command_name)
    result = start(db, c, command); db.commit()
    reviewer, action = make_request(db, c.reviewer, result, 'return_evidence')
    before = snapshot(db)
    for actor, changed in ((c.actor, action),
            (reviewer, action.model_copy(update={'expected_event_hash':'a'*64})),
            (reviewer, action.model_copy(update={'expected_event_id':uuid4()}))):
        with pytest.raises(ERRORS):
            decisions.decide(db, actor=actor, request=changed)
        db.rollback(); assert snapshot(db) == before
    real = business.record
    def failed(db, **kwargs):
        real(db, **kwargs)
        raise RuntimeError('synthetic decision transaction failure')
    with monkeypatch.context() as scope:
        scope.setattr(business, 'record', failed)
        with pytest.raises(RuntimeError, match='synthetic decision transaction failure'):
            decisions.decide(db, actor=reviewer, request=action)
        db.rollback()
    assert snapshot(db) == before
    result = decisions.decide(db, actor=reviewer, request=action); db.commit()
    assert result['status'] == 'needs_evidence'
