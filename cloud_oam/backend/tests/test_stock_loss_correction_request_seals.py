"""Real correction history with stock-neutral closure; native fences separate."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.foundation_models import AuditEvent, OutboxEvent, Permission, RolePermission
from app.formal_access import load_formal_principal
from app.models import User
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_loss_corrections import (
    correction_recovery, correction_seal_facts as facts, sealed_corrections as service,
    correction_approval, correction_execution,
)
from test_stock_loss_correction_recovery import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready, ready, pending, command, approval_request,
    snapshot as previous_snapshot,
)
from test_stock_loss_correction_sealed_inverse import stock_facts


pytestmark = [pytest.mark.parametrize('execution', ['restore_available'], indirect=True),
              pytest.mark.parametrize('ready', ['convert_used'], indirect=True)]


def snapshot(db):
    return (previous_snapshot(db), *(tuple(db.execute(text('SELECT * FROM ' + model.__tablename__ + ' ORDER BY id')))
                                    for model in facts.KINDS))


def read(db, p, request=None):
    return correction_recovery.lookup(db, actor=p.r.w.actor, request=request or p.cmd)


def seal(db, p):
    return service.seal(db, actor=p.r.w.actor, request=p.cmd)


def test_closure_is_repeatable_without_stock_and_blocks_late_coordinate_aliases(db, pending):
    p = pending
    before_stock = stock_facts(db)
    result = seal(db, p)
    db.commit()
    assert result['request_state'] == 'sealed' and result['retry_allowed'] is False
    assert result['seal']['stock_effect'] == 'none' and result['result'] is None
    assert p.cmd.idempotency_key not in str(result)
    assert stock_facts(db) == before_stock
    before = snapshot(db)
    assert seal(db, p) == result
    db.commit()
    assert snapshot(db) == before
    for changes in ({}, {'request_id': uuid4().hex}, {'idempotency_key': uuid4().hex}):
        with pytest.raises(InventoryReadError):
            p.writer(db, actor=p.r.w.actor, request=p.cmd.model_copy(update=changes))
        db.rollback()
        assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db, p) == result and snapshot(db) == before
    assert not db.new and not db.dirty and not db.deleted


def test_existing_committed_approval_or_posting_is_found_without_sealing(db, pending):
    p = pending
    posted = p.writer(db, actor=p.r.w.actor, request=p.cmd)
    db.commit()
    before = snapshot(db)
    answer = seal(db, p)
    db.commit()
    assert answer['request_state'] == 'found' and answer['result'] == posted
    assert snapshot(db) == before
    assert all(not tuple(db.scalars(select(model.id))) for model in facts.KINDS)


def test_current_read_survives_write_revocation_but_missing_read_is_denied(db, pending):
    p = pending
    answer = seal(db, p)
    db.commit()
    def rule(action):
        return db.scalar(select(RolePermission).join(Permission).where(
            RolePermission.role_id == p.r.w.admin_role.id,
            Permission.resource == 'stock_operation', Permission.action == action))
    rule('approve_loss_correction' if p.kind == 'approval' else 'correct_loss').effect = 'deny'
    db.commit()
    p.r.w.actor = load_formal_principal(db, p.r.w.actor.user_id)
    before = snapshot(db)
    with pytest.raises(InventoryReadError):
        seal(db, p)
    db.rollback()
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db, p) == answer and snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    rule('read').effect = 'deny'
    db.commit()
    p.r.w.actor = load_formal_principal(db, p.r.w.actor.user_id)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError) as caught:
        read(db, p)
    assert caught.value.code == 'loss_inverse_read_forbidden'


def test_cross_action_aliases_cannot_reuse_a_permanently_closed_request(db, pending):
    p = pending
    seal(db, p)
    db.commit()
    other = command(db, p.r) if p.kind == 'approval' else approval_request(p.r.w, 'convert_damaged')
    writer = correction_execution.execute if p.kind == 'approval' else correction_approval.approve
    before = snapshot(db)
    for coordinate in ('request_id', 'idempotency_key'):
        request = other.model_copy(update={coordinate: getattr(p.cmd, coordinate)})
        with pytest.raises(InventoryReadError) as caught:
            writer(db, actor=p.r.w.actor, request=request)
        assert caught.value.code == 'loss_correction_seal_conflict'
        db.rollback()
        assert snapshot(db) == before


def test_corrupt_or_orphan_closure_evidence_never_becomes_found_or_clean_missing(db, pending):
    p = pending
    seal(db, p)
    db.commit()
    model = facts.MODELS[type(p.cmd)]
    aggregate, kind = facts.KINDS[model]
    before = snapshot(db)
    for variant in ('audit', 'orphan', 'unexpected_event', 'changed_command'):
        savepoint = db.begin_nested()
        try:
            row = db.scalars(select(model)).one()
            request = p.cmd
            if variant == 'audit':
                event = db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type == aggregate)).one()
                event.after_jsonb = dict(event.after_jsonb, stock_effect='release')
            elif variant == 'orphan':
                db.delete(row)
            elif variant == 'unexpected_event':
                db.add(OutboxEvent(event_type=kind, aggregate_type=aggregate, aggregate_id=str(row.id),
                    payload_jsonb={}, idempotency_key=uuid4().hex, available_at=datetime.now(timezone.utc)))
            else:
                request = request.model_copy(update={'reason': '不是已经封存的原始原因'})
            db.flush()
            altered = snapshot(db)
            with pytest.raises(InventoryReadError):
                read(db, p, request)
            assert snapshot(db) == altered
        finally:
            savepoint.rollback()
            db.expire_all()
        assert snapshot(db) == before


def test_late_authority_change_rolls_back_seal_and_complete_audit_chain(db, pending, monkeypatch):
    p = pending
    before = snapshot(db)
    append = service.append_audit_event
    def changed(session, **kwargs):
        result = append(session, **kwargs)
        session.get(User, p.r.w.actor.user_id).authorization_version += 1
        session.flush()
        return result
    monkeypatch.setattr(service, 'append_audit_event', changed)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(InventoryPostingError) as caught:
            seal(db, p)
        assert caught.value.code == 'actor_principal_stale'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before


def test_new_explicit_request_can_execute_while_original_remains_closed(db, pending):
    p = pending
    original = seal(db, p)
    db.commit()
    new = (command(db, p.r) if p.kind == 'execution' else
           p.cmd.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex}))
    assert new.request_id != p.cmd.request_id and new.idempotency_key != p.cmd.idempotency_key
    posted = p.writer(db, actor=p.r.w.actor, request=new)
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db, p) == original
    assert read(db, p, new)['result'] == posted
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted


def test_read_authority_is_rechecked_after_the_seal_proof(db, pending, monkeypatch):
    p = pending
    seal(db, p)
    db.commit()
    before = snapshot(db)
    verify = facts.verify
    def changed(session, **kwargs):
        result = verify(session, **kwargs)
        session.get(User, p.r.w.actor.user_id).authorization_version += 1
        session.flush()
        return result
    monkeypatch.setattr(facts, 'verify', changed)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(InventoryPostingError) as caught:
            read(db, p)
        assert caught.value.code == 'actor_principal_stale'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert snapshot(db) == before
