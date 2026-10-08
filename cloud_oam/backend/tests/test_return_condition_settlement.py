"""Actual candidate unified posting and exact recovery on quantity/SN fixtures.

SQLite, fake object storage and the existing explicit SQLite key registrar do
not prove native COMMIT/target-account admission, HTTP or production readiness.
"""
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from sqlalchemy import func, select, text
from app.foundation_models import NotificationEvent
from app.inventory_models import InventoryTransaction, StockAccount, StockBalance, SerialCurrentPosition
from app.return_condition_settlement_requests import ConditionSettlement
from app.formal_services.stock_loss_corrections import return_condition_settlement as writer
from app.formal_services.stock_loss_corrections import return_condition_settlement_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_settlement_inputs as inputs
from app.formal_services.stock_loss_corrections import return_condition_history as graph
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_business_events as business
from app.formal_services.stock_loss_corrections import return_condition_posting_authority as permits
from test_return_condition_submission import db as initial_db, snapshot
from test_return_condition_ledger_facts import quantity_command, serial_command, authority_template
from test_return_condition_decisions import start, make_request
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived,
    ready, parcel, acceptance, prepared, regional_opening, reader_tables, context, regional_source, ERRORS, allow,
)


@pytest.fixture
def db(initial_db):
    # The shared initial fixture now installs the exact ten-table model.
    yield initial_db


@pytest.mark.parametrize('stock,command_name', [('quantity', 'quantity_command'), ('serial', 'serial_command')], indirect=['stock'])
@pytest.mark.parametrize('action', ['execute', 'release'])
def test_full_settlement_rollback_posting_and_original_lookup(db, regional_source, request, command_name, action, monkeypatch):
    c = regional_source; original = request.getfixturevalue(command_name)
    result = start(db, c, original); db.commit()
    for kind, reviewer in [('verify_region', c.reviewer), ('approve_hq', c.hq)]:
        actor, command = make_request(db, reviewer, result, kind)
        result = decisions.decide(db, actor=actor, request=command); db.commit()
    if action == 'release':
        actor, cancel = make_request(db, c.hq, result, 'cancel_approved')
        result = decisions.decide(db, actor=actor, request=cancel); db.commit()
    request_input = ConditionSettlement(action=action, case_id=UUID(result['case_id']),
        expected_event_id=UUID(result['event_id']), expected_event_hash=result['request_hash'],
        reason='按准确审批结算全部原冻结份额', request_id=uuid4().hex, idempotency_key=uuid4().hex,
        serial_verifications=original.serial_verifications)
    before = snapshot(db)
    unknown = recovery.lookup(db, actor=c.actor, request=request_input)
    assert unknown['request_state'] == 'unknown' and not unknown['retry_allowed'] and not unknown['absence_sealed']
    assert snapshot(db) == before
    tx_count = db.scalar(select(func.count()).select_from(InventoryTransaction))
    real_record = business.record
    def failed_effect(db, **kwargs):
        real_record(db, **kwargs)
        assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == tx_count + 1
        raise RuntimeError('synthetic failure after actual settlement posting and business effects')
    monkeypatch.setattr(business, 'record', failed_effect)
    with pytest.raises(RuntimeError, match='synthetic failure after actual settlement'):
        writer.settle(db, actor=c.actor, request=request_input)
    db.rollback()
    assert snapshot(db) == before and permits.KEY not in db.info
    monkeypatch.setattr(business, 'record', real_record)
    print(f'{command_name} {action} EFFECT_FAILURE_ROLLBACK', flush=True)
    # Synthetic failed transaction is exactly proved absent by the fixture's
    # full database rollback. This is a test, not a product automatic retry.
    total_before = db.scalar(select(func.sum(StockBalance.quantity)))
    source_before = db.get(StockBalance, c.source.id, populate_existing=True).quantity
    result = writer.settle(db, actor=c.actor, request=request_input); db.commit()
    assert result['status'] == ('executed' if action == 'execute' else 'released_cancelled')
    assert result['stock_effect'] == ('status_change' if action == 'execute' else 'unfreeze')
    assert db.scalar(select(func.sum(StockBalance.quantity))) == total_before
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == tx_count + 1
    event = db.execute(select(graph.tables()['stock_condition_events']).where(
        graph.tables()['stock_condition_events'].c.id == UUID(result['event_id']))).mappings().one()
    held = db.get(StockBalance, event['from_account_id'], populate_existing=True)
    target = db.get(StockAccount, event['to_account_id'], populate_existing=True)
    assert held.quantity == Decimal('0')
    assert target.condition_code == ('damaged' if action == 'execute' else c.source.condition_code)
    assert target.availability_bucket == 'available'
    assert db.get(StockBalance, c.source.id, populate_existing=True).quantity == source_before + (original.quantity if action == 'release' else 0)
    for scan in request_input.serial_verifications:
        assert db.get(SerialCurrentPosition, scan.serial_id, populate_existing=True).stock_account_id == target.id
    notice = db.scalar(select(NotificationEvent).where(NotificationEvent.business_id == str(event['id'])))
    assert notice.status == 'pending'
    stable = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    found = recovery.lookup(db, actor=c.actor, request=request_input)
    db.execute(text('PRAGMA query_only=OFF'))
    assert found['request_state'] == 'found' and found['result'] == result
    assert not found['retry_allowed'] and not found['current_stock_verified']
    assert snapshot(db) == stable
    changed = request_input.model_copy(update={'reason': '不同原始意图'})
    with pytest.raises(ERRORS): recovery.lookup(db, actor=c.actor, request=changed)
    db.rollback(); assert snapshot(db) == stable
    with pytest.raises(ERRORS): writer.settle(db, actor=c.actor, request=request_input)
    db.rollback(); assert snapshot(db) == stable
    # Old outcome remains readable after removing only the new action grant.
    grant = allow(db, c.regional_role, 'stock_operation', action + '_return_condition')
    grant.effect = 'deny'; db.commit()
    stable = snapshot(db)
    assert recovery.lookup(db, actor=c.actor, request=request_input)['result'] == result
    assert snapshot(db) == stable
    print(f'{command_name} {action} POSTED_AND_RECOVERED', flush=True)
