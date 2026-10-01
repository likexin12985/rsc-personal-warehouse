"""Actual approvals/postings recovered by full commands without replay."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission, OutboxEvent
from app.models import User
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from test_stock_loss_correction_execution import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, recoverable, inverse_ready, ready, snapshot, command, approval_request
from app.formal_services.stock_loss_corrections import correction_approval
from app.formal_services.stock_loss_corrections import correction_execution
from app.formal_services.stock_loss_corrections import correction_recovery as correction_recovery
pytestmark = [pytest.mark.parametrize('execution', ['restore_available'], indirect=True), pytest.mark.parametrize('ready', ['convert_used'], indirect=True)]

@pytest.fixture(params=['approval', 'execution'])
def pending(db, ready, request):
    r = ready
    cmd = approval_request(r.w, 'convert_damaged') if request.param == 'approval' else command(db, r)
    writer = correction_approval.approve if request.param == 'approval' else correction_execution.execute
    return SimpleNamespace(r=r, cmd=cmd, writer=writer, kind=request.param)

def read(db, p, request=None):
    return correction_recovery.lookup(db, actor=p.r.w.actor, request=request or p.cmd)

def post(db, p):
    result = p.writer(db, actor=p.r.w.actor, request=p.cmd)
    db.commit()
    return result

def test_exact_query_only_recovery_separates_approval_from_posting_and_current_rights(db, pending):
    p = pending
    expected = post(db, p)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    recovered = read(db, p)
    assert recovered['request_state'] == 'found' and recovered['retry_allowed'] is False
    assert recovered['result_scope'] == ('historical_approval' if p.kind == 'approval' else 'historical_correction_posting')
    assert recovered['result'] == expected and read(db, p) == recovered
    if p.kind == 'approval':
        assert expected['stock_effect'] == 'none' and 'posting_transaction_id' not in expected
    else:
        assert UUID(expected['posting_transaction_id']) and expected['status'] == 'posted'
    assert snapshot(db) == before and (not db.new) and (not db.dirty) and (not db.deleted)
    db.execute(text('PRAGMA query_only=OFF'))

    def rule(action):
        return db.scalar(select(RolePermission).join(Permission, Permission.id == RolePermission.permission_id).where(RolePermission.role_id == p.r.w.admin_role.id, Permission.resource == 'stock_operation', Permission.action == action))
    rule('approve_loss_correction' if p.kind == 'approval' else 'correct_loss').effect = 'deny'
    db.commit()
    p.r.w.actor = load_formal_principal(db, p.r.w.actor.user_id)
    assert read(db, p) == recovered
    rule('read').effect = 'deny'
    db.commit()
    p.r.w.actor = load_formal_principal(db, p.r.w.actor.user_id)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError) as caught:
        read(db, p)
    assert caught.value.code == 'loss_inverse_read_forbidden' and snapshot(db) == before

def test_original_command_and_cross_action_coordinates_cannot_be_substituted(db, pending):
    p = pending
    post(db, p)
    before = snapshot(db)
    changes = [('reason', '不同的原请求'), ('expected_reversal_hash', 'f' * 64), ('expected_submission_plan_hash', 'f' * 64), ('request_id', uuid4().hex), ('idempotency_key', uuid4().hex)]
    changes.append(('disposition', 'restore_available') if p.kind == 'approval' else ('expected_plan_hash', 'f' * 64))
    db.execute(text('PRAGMA query_only=ON'))
    for (field, value) in changes:
        with pytest.raises(InventoryReadError):
            read(db, p, p.cmd.model_copy(update={field: value}))
    for field in ('request_id', 'idempotency_key'):
        with pytest.raises(InventoryReadError) as caught:
            read(db, p, p.cmd.model_copy(update={field: getattr(p.r.w.command, field)}))
        assert caught.value.code == 'loss_correction_request_conflict'
    assert snapshot(db) == before

def test_clean_miss_disallows_retry_and_orphan_domain_event_is_unknown(db, pending):
    p = pending
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    answer = read(db, p)
    assert answer['request_state'] == 'not_found' and answer['retry_allowed'] is False
    assert answer['result'] is None and answer['result_scope'] == 'unconfirmed_request'
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    aggregate = 'stock_loss_correction_decision' if p.kind == 'approval' else 'stock_loss_correction_execution'
    db.add(OutboxEvent(event_type='stock_loss.correction_approved' if p.kind == 'approval' else 'stock_loss.correction_posted', aggregate_type=aggregate, aggregate_id=str(uuid4()), payload_jsonb={'actor_user_id': p.r.w.actor.user_id, 'request_id': p.cmd.request_id}, idempotency_key=uuid4().hex, available_at=datetime.now(timezone.utc)))
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError) as caught:
        read(db, p)
    assert caught.value.code == 'loss_correction_request_outcome_unknown' and snapshot(db) == before

def test_missing_or_extra_historical_evidence_cannot_claim_success(db, pending):
    p = pending
    result = post(db, p)
    aggregate = 'stock_loss_correction_decision' if p.kind == 'approval' else 'stock_loss_correction_execution'
    identifier = result['correction_decision_id' if p.kind == 'approval' else 'correction_execution_id']
    before = snapshot(db)
    for mode in ('missing', 'extra'):
        savepoint = db.begin_nested()
        try:
            event = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == aggregate, OutboxEvent.aggregate_id == identifier))
            if mode == 'missing':
                db.delete(event)
            else:
                db.add(OutboxEvent(event_type=event.event_type, aggregate_type=aggregate, aggregate_id=identifier, payload_jsonb=dict(event.payload_jsonb), idempotency_key=uuid4().hex, available_at=event.available_at))
            db.flush()
            altered = snapshot(db)
            with pytest.raises((InventoryReadError, InvalidChain)):
                read(db, p)
            assert snapshot(db) == altered
        finally:
            savepoint.rollback()
            db.expire_all()
        assert snapshot(db) == before

def test_authority_version_changed_during_proof_never_returns_success(db, pending, monkeypatch):
    p = pending
    post(db, p)
    before = snapshot(db)
    original = correction_recovery.verify_chain

    def changed(session, **kwargs):
        result = original(session, **kwargs)
        session.get(User, p.r.w.actor.user_id).authorization_version += 1
        session.flush()
        return result
    monkeypatch.setattr(correction_recovery, 'verify_chain', changed)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(InventoryPostingError) as caught:
            read(db, p)
        assert caught.value.code == 'actor_principal_stale'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before
