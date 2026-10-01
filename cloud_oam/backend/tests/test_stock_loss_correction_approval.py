"""Real inverse followed by an independent stock-neutral HQ decision."""
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.models import User
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_loss_corrections.correction_models import StockLossDispositionReversal as Inverse, StockLossCorrectionDecision as Decision
from app.formal_services.stock_loss_corrections.request_contracts import CorrectionApprove
from test_stock_loss_correction_sealed_inverse import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, recoverable, snapshot, stock_facts
from app.formal_services.stock_loss_corrections import sealed_inverse
from app.formal_services.stock_loss_corrections import correction_approval
pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)

@pytest.fixture
def inverse_ready(db, recoverable):
    w = recoverable
    posted = sealed_inverse.execute(db, actor=w.actor, request=w.command)
    db.commit()
    w.inverse = db.get(Inverse, UUID(posted['reversal_id']))
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = Permission(resource='stock_operation', action='approve_loss_correction', field_code='', description='Synthetic independent correction approval')
    db.add(permission)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    w.approval_permission = permission
    w.admin_role = role
    return w

def request(w, disposition='restore_available'):
    return CorrectionApprove(root_disposition_id=w.root.id, expected_root_request_hash=w.root.request_hash, expected_submission_plan_hash=w.order.plan_hash, reason='总部独立复核后的纠正决定', reversal_id=w.inverse.id, expected_reversal_hash=w.inverse.request_hash, disposition=disposition, request_id=uuid4().hex, idempotency_key=uuid4().hex)

def test_five_independent_approval_outcomes_never_release_frozen_stock(db, inverse_ready):
    w = inverse_ready
    before_stock = stock_facts(db)
    commands = []
    for kind in ('restore_available', 'convert_used', 'convert_damaged', 'return_to_region', 'scrap'):
        cmd = request(w, kind)
        commands.append(cmd)
        result = correction_approval.approve(db, actor=w.actor, request=cmd)
        db.commit()
        assert result['approval_stage'] == 'approved' and result['stock_effect'] == 'none'
        assert result['disposition'] == kind and result['reversal_id'] == str(w.inverse.id)
        assert result['original_headquarters_decision_id'] == str(w.root.headquarters_decision_id)
        assert result['correction_decision_id'] != str(w.root.headquarters_decision_id)
        assert stock_facts(db) == before_stock
    before = snapshot(db)
    with pytest.raises(InventoryReadError):
        correction_approval.approve(db, actor=w.actor, request=commands[-1])
    db.rollback()
    assert snapshot(db) == before
    assert len(tuple(db.scalars(select(Decision.id)))) == 5

def test_approval_cannot_borrow_inverse_permission_or_approve_own_loss(db, inverse_ready, allowed):
    from datetime import datetime, timedelta, timezone
    from app.foundation_models import AuthIdentity, Person
    from test_formal_access import assign
    w = inverse_ready
    cmd = request(w)
    before = snapshot(db)
    savepoint = db.begin_nested()
    try:
        rule = db.scalar(select(RolePermission).where(RolePermission.role_id == w.admin_role.id, RolePermission.permission_id == w.approval_permission.id))
        rule.effect = 'deny'
        db.flush()
        with pytest.raises(InventoryReadError) as caught:
            correction_approval.approve(db, actor=w.actor, request=cmd)
        assert caught.value.code == 'stock_loss_correction_forbidden'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before
    user = db.get(User, allowed.actor.user_id)
    now = datetime.now(timezone.utc)
    db.get(Person, user.person_id).organization_id = db.get(Person, w.actor.person_id).organization_id
    db.add(AuthIdentity(user_id=user.id, identity_type='mobile', provider_key='correction-self-review-test', identifier_hash=uuid4().hex + uuid4().hex, hash_version=1, verified_at=now, status='active'))
    assign(db, user, w.admin_role, scope_type='national', scope_id='*', valid_from=now - timedelta(seconds=1))
    db.commit()
    applicant = load_formal_principal(db, user.id)
    before = snapshot(db)
    assert applicant.person_id == w.order.requester_id
    with pytest.raises(InventoryReadError) as caught:
        correction_approval.approve(db, actor=applicant, request=cmd)
    assert caught.value.code == 'stock_loss_self_review_forbidden' and snapshot(db) == before

def test_wrong_inverse_hash_and_orphan_request_cannot_approve(db, inverse_ready):
    from app.foundation_models import OutboxEvent
    from datetime import datetime, timezone
    w = inverse_ready
    cmd = request(w)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        correction_approval.approve(db, actor=w.actor, request=cmd.model_copy(update={'expected_reversal_hash': 'f' * 64}))
    assert caught.value.code == 'stock_loss_correction_reference_changed' and snapshot(db) == before
    db.add(OutboxEvent(event_type='stock_loss.correction_approved', aggregate_type='stock_loss_correction_decision', aggregate_id=str(uuid4()), payload_jsonb={'actor_user_id': w.actor.user_id, 'request_id': cmd.request_id}, idempotency_key=uuid4().hex, available_at=datetime.now(timezone.utc)))
    db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        correction_approval.approve(db, actor=w.actor, request=cmd)
    assert caught.value.code == 'loss_inverse_request_outcome_unknown' and snapshot(db) == before

def test_after_event_authority_change_rolls_back_decision_and_all_events(db, inverse_ready, monkeypatch):
    w = inverse_ready
    cmd = request(w)
    record = correction_approval.business_events.record
    before = snapshot(db)
    savepoint = db.begin_nested()

    def revoke(session, **kwargs):
        record(session, **kwargs)
        session.get(User, w.actor.user_id).authorization_version += 1
        session.flush()
    monkeypatch.setattr(correction_approval.business_events, 'record', revoke)
    try:
        with pytest.raises(InventoryPostingError) as caught:
            correction_approval.approve(db, actor=w.actor, request=cmd)
        assert caught.value.code == 'actor_principal_stale'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before and (not tuple(db.scalars(select(Decision.id))))
