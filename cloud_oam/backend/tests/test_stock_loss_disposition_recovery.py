"""Real loss approvals/postings; SQLite query-only recovery, PG16 gates separate."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.foundation_models import NotificationEvent, OutboxEvent
from app.stock_operation_models import StockLossDisposition
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.formal_services import stock_loss_disposition_recovery as recovery
from app.formal_services import stock_loss_disposition_commands as commands
from app.formal_services import stock_loss_return_commands as returns
from app.formal_services import stock_loss_disposition_plan as plan
from app.formal_services import stock_loss_return_plan as return_plan
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_stock_loss_dispositions import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    prepare, execute_request, snapshot,
)
from test_stock_loss_return_plan import route, request as return_request


@pytest.fixture(params=['restore_available', 'convert_used', 'convert_damaged', 'return_to_region'])
def execution(request, db, allowed, approved, route):
    grant = next(g for g in approved.actor.entitlements if g.action == 'dispose_loss')
    actor = replace(approved.actor, entitlements=approved.actor.entitlements + (replace(grant, action='read'),))
    approved.actor = actor
    allowed.world.current_principal = actor
    if request.param == 'return_to_region':
        value = return_request(db, approved, route)
        preview = return_plan.preview_loss_return(db, actor=actor, request=value)
        command = StockLossReturnExecuteIn(**value.model_dump(), expected_plan_hash=preview['plan_hash'],
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        flow, execute = 'return', returns.execute_loss_return
    else:
        value = prepare(db, approved, request.param)
        preview = plan.preview_disposition(db, actor=actor, request=value)
        command = execute_request(preview, value)
        flow, execute = 'disposition', commands.execute_disposition
    def commit():
        result = execute(db, actor=actor, request=command)
        db.commit()
        return result
    return SimpleNamespace(actor=actor, command=command, flow=flow, commit=commit)


def read(db, w, command=None, actor=None):
    return recovery.lookup_disposition_request(db, actor=actor or w.actor,
        request=command or w.command, flow=w.flow)


def test_lost_response_recovery_uses_full_command_without_write_access_or_replay(db, allowed, execution, monkeypatch):
    w = execution
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db, w) == {'lookup_status':'not_found', 'retry_permitted':False}
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    # An orphan notification intent carrying this exact request remains an
    # unknown outcome even when no disposition row or posting is visible.
    now = datetime.now(timezone.utc)
    db.add(OutboxEvent(event_type=recovery.facts.KIND, aggregate_type=recovery.facts.AGGREGATE,
        aggregate_id=str(uuid4()), idempotency_key=uuid4().hex, available_at=now,
        payload_jsonb={'executor_person_id':str(w.actor.person_id), 'request_id':w.command.request_id}))
    db.flush()
    with pytest.raises(InventoryReadError) as caught:
        read(db, w)
    assert caught.value.code == 'stock_loss_disposition_evidence_invalid'
    db.rollback()
    assert snapshot(db) == before
    posted = w.commit()
    allowed.world.current_principal = replace(w.actor,
        entitlements=tuple(g for g in w.actor.entitlements if g.action != 'dispose_loss'))
    def forbidden(*args, **kwargs):
        raise AssertionError('Recovery must never preview or replay a physical command')
    monkeypatch.setattr(commands, 'execute_disposition', forbidden)
    monkeypatch.setattr(returns, 'execute_loss_return', forbidden)
    monkeypatch.setattr(plan, 'preview_disposition', forbidden)
    monkeypatch.setattr(return_plan, 'preview_loss_return', forbidden)
    db.expire_all()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    found = read(db, w)
    assert found == {'lookup_status':'found', 'retry_permitted':False, 'disposition':posted}
    assert read(db, w) == found and snapshot(db) == before
    assert w.command.idempotency_key not in str(found)
    assert not db.new and not db.dirty and not db.deleted
    if w.flow == 'return':
        assert posted['return_fulfillment_required'] is True
        assert posted['stock_effect'] == 'frozen_to_return_pending'
    for field in type(w.command).model_fields:
        value = uuid4() if field.endswith('_id') and field != 'request_id' else (
            uuid4().hex if field in ('request_id', 'idempotency_key') else 'f' * 64)
        with pytest.raises(InventoryReadError):
            read(db, w, w.command.model_copy(update={field:value}))
        assert snapshot(db) == before


def test_missing_notification_and_orphan_posting_never_become_found_or_clean_miss(db, execution):
    w = execution
    posted = w.commit()
    row = db.get(StockLossDisposition, UUID(posted['disposition_id']))
    notification = db.scalar(select(NotificationEvent).where(NotificationEvent.business_id == str(row.id),
        NotificationEvent.business_type == 'stock_loss_disposition'))
    notification.payload_jsonb = {**notification.payload_jsonb, 'quantity':'999.000'}
    db.flush()
    with pytest.raises(InventoryReadError) as caught:
        read(db, w)
    # Both fact verifiers use stock_return_facts.single for exact event proof.
    assert caught.value.code == 'stock_return_evidence_invalid'
    assert caught.value.status_code == 503
    db.rollback()
    assert read(db, w)['disposition'] == posted
    # Intentionally corrupt only the disposable SQLite fixture; ledger and
    # events remain. A missing root must not invite another inventory movement.
    row = db.get(StockLossDisposition, UUID(posted['disposition_id']))
    db.delete(row)
    db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError):
        read(db, w)
    assert snapshot(db) == before


def test_current_read_authority_and_cursor_are_rechecked(db, allowed, execution, monkeypatch):
    w = execution
    w.commit()
    for fault in ('read', 'scope', 'borrowed', 'inactive', 'version', 'operator', 'other_user'):
        actor = w.actor
        if fault == 'read':
            actor = replace(actor, entitlements=tuple(g for g in actor.entitlements if g.action != 'read'))
        elif fault == 'scope':
            actor = replace(actor, assignments=())
        elif fault == 'borrowed':
            actor = replace(actor, entitlements=tuple(replace(g, role_code='technician')
                if g.action == 'read' else g for g in actor.entitlements))
        elif fault == 'inactive':
            actor = replace(actor, account_status='suspended', access_mode='restricted_handover')
        elif fault == 'version':
            actor = replace(actor, authorization_version=actor.authorization_version + 1)
        elif fault == 'operator':
            actor = replace(actor, person_id=allowed.actor.person_id)
        else:
            actor = replace(actor, user_id=allowed.actor.user_id, person_id=allowed.actor.person_id,
                authorization_version=allowed.actor.authorization_version)
        allowed.world.current_principal = actor
        before = snapshot(db)
        with pytest.raises((InventoryReadError, InventoryPostingError)):
            read(db, w, actor=w.actor if fault == 'version' else actor)
        assert snapshot(db) == before
    allowed.world.current_principal = w.actor
    cursor = recovery._cursor
    calls = 0
    def changed(session):
        nonlocal calls
        calls += 1
        value = cursor(session)
        return value if calls == 1 else (value[0] + 1, value[1])
    monkeypatch.setattr(recovery, '_cursor', changed)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        read(db, w)
    assert caught.value.code == 'stock_loss_disposition_lookup_changed'
    assert snapshot(db) == before
    monkeypatch.setattr(recovery, '_cursor', cursor)
    verify = recovery.facts.verified
    def revoke_after_proof(*args, **kwargs):
        result = verify(*args, **kwargs)
        allowed.world.current_principal = replace(w.actor,
            entitlements=tuple(g for g in w.actor.entitlements if g.action != 'read'))
        return result
    monkeypatch.setattr(recovery.facts, 'verified', revoke_after_proof)
    with pytest.raises(InventoryReadError) as caught:
        read(db, w)
    assert caught.value.code == 'stock_loss_disposition_read_forbidden'
    assert snapshot(db) == before
