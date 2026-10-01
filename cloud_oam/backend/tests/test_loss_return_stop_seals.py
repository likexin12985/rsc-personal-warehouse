"""Service-level permanent closure; native SQL exclusion remains separate."""
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from app.foundation_models import AuditEvent, OutboxEvent, Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.stock_loss_return_stop_models import StockLossReturnStop
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_stock_loss_correction_inverse_recovery import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, recoverable, orphan
from test_stock_loss_correction_inverse_posting import snapshot as prior_snapshot
from app.formal_services.stock_loss_corrections.seal_model import StockLossInverseRequestSeal as Seal
from app.formal_services.stock_loss_corrections import sealed_inverse as service

def snapshot(db):
    return (prior_snapshot(db), tuple(db.execute(text('SELECT * FROM stock_loss_inverse_request_seals ORDER BY id'))))

def stock_facts(db):
    return tuple(((name, tuple(db.execute(text('SELECT * FROM ' + name + ' ORDER BY ' + key)))) for (name, key) in (('inventory_transactions', 'id'), ('inventory_movements', 'id'), ('stock_balances', 'stock_account_id'), ('serial_current_positions', 'serial_id'), ('stock_loss_dispositions', 'id'), ('stock_loss_disposition_reversals', 'id'))))

def seal(db, w, request=None):
    return service.seal_unshipped_return(db, actor=w.actor, request=request or w.command)

def lookup(db, w, request=None):
    return service.lookup_unshipped_return(db, actor=w.actor, request=request or w.command)

def execute(db, w, request=None):
    return service.execute_unshipped_return(db, actor=w.actor, request=request or w.command)

@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_permanent_seal_repeats_without_stock_and_rejects_late_coordinate_aliases(db, recoverable):
    w = recoverable
    before_stock = stock_facts(db)
    answer = seal(db, w)
    db.commit()
    assert answer['request_state'] == 'sealed' and answer['retry_allowed'] is False
    assert answer['seal']['stock_effect'] == 'none' and stock_facts(db) == before_stock
    assert w.command.idempotency_key not in str(answer)
    before = snapshot(db)
    assert seal(db, w) == answer
    db.commit()
    assert snapshot(db) == before
    for change in ({}, {'request_id': uuid4().hex}, {'idempotency_key': uuid4().hex}):
        with pytest.raises(InventoryReadError) as caught:
            execute(db, w, w.command.model_copy(update=change))
        assert caught.value.code == 'loss_inverse_request_sealed'
        db.rollback()
        assert snapshot(db) == before
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation', Permission.action == 'reverse_loss'))
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    db.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id)).effect = 'deny'
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    before = snapshot(db)
    with pytest.raises(InventoryReadError):
        seal(db, w)
    db.rollback()
    db.execute(text('PRAGMA query_only=ON'))
    assert lookup(db, w) == answer and snapshot(db) == before
    assert not db.new and (not db.dirty) and (not db.deleted)

@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_existing_real_posting_is_recovered_instead_of_sealed(db, recoverable):
    w = recoverable
    posted = execute(db, w)
    db.commit()
    before = snapshot(db)
    result = seal(db, w)
    db.commit()
    assert result['request_state'] == 'found' and result['result'] == posted
    assert snapshot(db) == before and (not tuple(db.scalars(select(Seal.id))))

@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_new_explicit_coordinates_preserve_the_old_permanent_seal(db, recoverable):
    w = recoverable
    old = seal(db, w)
    db.commit()
    new = w.command.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex})
    posted = execute(db, w, new)
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert lookup(db, w) == old
    assert lookup(db, w, new)['result'] == posted and snapshot(db) == before

@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_orphan_posting_evidence_cannot_be_sealed_as_unexecuted(db, recoverable):
    w = recoverable
    orphan(db, w)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        seal(db, w)
    assert caught.value.code == 'loss_inverse_request_outcome_unknown'
    db.rollback()
    assert snapshot(db) == before

@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_incomplete_seal_proof_and_changed_original_command_are_rejected(db, recoverable):
    w = recoverable
    answer = seal(db, w)
    db.commit()
    for variant in ('audit_payload', 'orphan_audit', 'spurious_outbox', 'reason', 'plan_hash'):
        before = snapshot(db)
        savepoint = db.begin_nested()
        try:
            row = db.scalars(select(Seal)).one()
            event = db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type == service.AGGREGATE)).one()
            request = w.command
            if variant == 'audit_payload':
                event.after_jsonb = dict(event.after_jsonb, stock_effect='release')
            elif variant == 'orphan_audit':
                db.delete(row)
            elif variant == 'spurious_outbox':
                db.add(OutboxEvent(event_type=service.KIND, aggregate_type=service.AGGREGATE, aggregate_id=str(row.id), payload_jsonb={}, idempotency_key=uuid4().hex, available_at=datetime.now(timezone.utc)))
            elif variant == 'reason':
                request = request.model_copy(update={'reason': 'changed reason'})
            else:
                request = request.model_copy(update={'expected_plan_hash': 'f' * 64})
            db.flush()
            altered = snapshot(db)
            with pytest.raises(InventoryReadError):
                lookup(db, w, request)
            assert snapshot(db) == altered
        finally:
            savepoint.rollback()
            db.expire_all()
        assert snapshot(db) == before
    assert lookup(db, w) == answer

@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_authority_change_after_audit_rolls_back_seal_and_audit_head(db, recoverable, monkeypatch):
    from app.models import User
    w = recoverable
    append = service.append_audit_event
    before = snapshot(db)

    def revoked(session, **kwargs):
        result = append(session, **kwargs)
        session.get(User, w.actor.user_id).authorization_version += 1
        session.flush()
        return result
    monkeypatch.setattr(service, 'append_audit_event', revoked)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(InventoryPostingError) as caught:
            seal(db, w)
        assert caught.value.code == 'actor_principal_stale'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before


@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
def test_current_authority_is_rechecked_after_seal_evidence(db, recoverable, monkeypatch):
    import inspect
    from app.models import User
    w = recoverable
    seal(db, w)
    db.commit()
    before = snapshot(db)
    original_version = db.get(User, w.actor.user_id).authorization_version
    actual = service._verify_seal_evidence
    def revoked(session, **kwargs):
        answer = actual(session, **kwargs)
        session.get(User, w.actor.user_id).authorization_version += 1
        session.flush()
        return answer
    monkeypatch.setattr(service, '_verify_seal_evidence', revoked)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(InventoryPostingError) as caught:
            inspect.unwrap(service.lookup_unshipped_return)(db, actor=w.actor, request=w.command)
        assert caught.value.code == 'actor_principal_stale'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before
    assert db.get(User, w.actor.user_id).authorization_version == original_version
