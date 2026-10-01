"""Query-only exact recovery against real inverse postings and DB authority."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission, OutboxEvent
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from test_stock_loss_correction_inverse_posting import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, command, snapshot
from app.formal_services.stock_loss_corrections import inverse_posting
from app.formal_services.stock_loss_corrections import inverse_recovery

@pytest.fixture
def recoverable(db, prepared):
    w = prepared
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation', Permission.action == 'read', Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='stock_operation', action='read', field_code='', description='Synthetic recovery read')
        db.add(permission)
        db.flush()
    rule = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id))
    if rule is None:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    else:
        rule.effect = 'allow'
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    w.command = command(db, w)
    return w

def read(db, w, request=None):
    return inverse_recovery.lookup_original_inverse(db, actor=w.actor, request=request or w.command)

def orphan(db, w):
    db.add(OutboxEvent(event_type='stock_loss.disposition_reversed', aggregate_type='stock_loss_disposition_reversal', aggregate_id=str(uuid4()), payload_jsonb={'actor_user_id': w.actor.user_id, 'request_id': w.command.request_id}, idempotency_key=uuid4().hex, available_at=datetime.now(timezone.utc)))
    db.commit()

@pytest.mark.parametrize('execution', ['restore_available', 'convert_used', 'convert_damaged'], indirect=True)
def test_exact_original_recovery_is_query_only_and_not_current_stock_or_delivery(db, recoverable):
    w = recoverable
    posted = inverse_posting.execute_account_inverse(db, actor=w.actor, request=w.command)
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    result = read(db, w)
    assert result['request_state'] == 'found' and result['retry_allowed'] is False
    assert result['result_scope'] == 'historical_original_posting' and result['result'] == posted
    assert read(db, w) == result and snapshot(db) == before
    assert not db.new and (not db.dirty) and (not db.deleted)

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_miss_cannot_authorize_retry_and_orphan_evidence_is_not_a_miss(db, recoverable):
    w = recoverable
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    result = read(db, w)
    assert result['request_state'] == 'not_found' and result['retry_allowed'] is False and (result['result'] is None)
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    orphan(db, w)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError) as caught:
        read(db, w)
    assert caught.value.code == 'loss_inverse_request_outcome_unknown' and snapshot(db) == before

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_original_request_reason_hash_id_and_key_cannot_be_substituted(db, recoverable):
    w = recoverable
    inverse_posting.execute_account_inverse(db, actor=w.actor, request=w.command)
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    for (field, value) in (('reason', 'different reason'), ('expected_plan_hash', 'f' * 64), ('expected_submission_plan_hash', 'f' * 64), ('request_id', uuid4().hex), ('idempotency_key', uuid4().hex)):
        with pytest.raises(InventoryReadError) as caught:
            read(db, w, w.command.model_copy(update={field: value}))
        assert caught.value.code in {'stock_loss_correction_request_conflict', 'loss_inverse_request_conflict'}
    assert snapshot(db) == before

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_read_permission_is_current_and_independent_of_historical_write_permission(db, recoverable):
    w = recoverable
    inverse_posting.execute_account_inverse(db, actor=w.actor, request=w.command)
    db.commit()

    def rule(action):
        return db.scalar(select(RolePermission).join(Permission, Permission.id == RolePermission.permission_id).join(Role, Role.id == RolePermission.role_id).where(Role.code == 'admin', Permission.resource == 'stock_operation', Permission.action == action))
    rule('reverse_loss').effect = 'deny'
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    assert read(db, w)['request_state'] == 'found'
    rule('read').effect = 'deny'
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError) as caught:
        read(db, w)
    assert caught.value.code == 'loss_inverse_read_forbidden' and snapshot(db) == before

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_missing_or_extra_evidence_never_recovers_a_success(db, recoverable):
    w = recoverable
    posted = inverse_posting.execute_account_inverse(db, actor=w.actor, request=w.command)
    db.commit()
    savepoint = db.begin_nested()
    try:
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == 'stock_loss_disposition_reversal', OutboxEvent.aggregate_id == posted['reversal_id']))
        db.delete(event)
        db.flush()
        before = snapshot(db)
        with pytest.raises((InventoryReadError, InvalidChain)):
            read(db, w)
        assert snapshot(db) == before
    finally:
        savepoint.rollback()
        db.expire_all()
    orphan(db, w)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError) as caught:
        read(db, w)
    assert caught.value.code == 'loss_inverse_request_outcome_unknown' and snapshot(db) == before

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_current_authority_is_rechecked_after_historical_proof(db, recoverable, monkeypatch):
    from app.models import User
    w = recoverable
    inverse_posting.execute_account_inverse(db, actor=w.actor, request=w.command)
    db.commit()
    original = inverse_recovery.verify_original_inverse

    def changed(session, **kwargs):
        result = original(session, **kwargs)
        session.get(User, w.actor.user_id).authorization_version += 1
        session.flush()
        return result
    monkeypatch.setattr(inverse_recovery, 'verify_original_inverse', changed)
    before = snapshot(db)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(InventoryPostingError) as caught:
            read(db, w)
        assert caught.value.code == 'actor_principal_stale'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before
