"""Atomic approved-loss return service, rollback and historical proof acceptance."""
from uuid import UUID, uuid4
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.inventory_models import InventoryTransaction, InventoryMovement, StockAccount, StockBalance, Shipment, Receipt, SerialCurrentPosition
from app.foundation_models import NotificationEvent, NotificationPersonTarget, OutboxEvent, StateTransitionEvent
from app.stock_operation_models import StockOperationOrder, StockOperationLine, StockLossDisposition
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.formal_services import stock_loss_return_plan as plan, stock_loss_return_commands as commands
from app.formal_services import stock_loss_disposition_facts as facts, stock_loss_disposition_plan as approved_plan
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_return_plan import db, world, stock, allowed, evidence, regional, headquarters, approved, route, request
from test_stock_loss_dispositions import snapshot, second_report


def execute_request(db, approved, route):
    value = request(db, approved, route)
    preview = plan.preview_loss_return(db, actor=approved.actor, request=value)
    return StockLossReturnExecuteIn(**value.model_dump(), expected_plan_hash=preview['plan_hash'],
        request_id=uuid4().hex, idempotency_key=uuid4().hex), preview


def test_derived_return_posts_once_and_replays_without_forging_fulfillment(db, allowed, approved, route):
    value, preview = execute_request(db, approved, route)
    before_tx = len(tuple(db.scalars(select(InventoryTransaction.id))))
    result = commands.execute_loss_return(db, actor=approved.actor, request=value)
    db.commit()
    child = db.get(StockOperationOrder, UUID(result['return_operation_id']))
    assert child.requester_id == allowed.actor.person_id
    assert child.actor_user_id == approved.actor.user_id
    assert child.oam_work_order_id is None
    assert child.loss_headquarters_decision_id == value.headquarters_decision_id
    lines = tuple(db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id == child.id)))
    assert len(lines) == 1 and lines[0].source_loss_line_id == UUID(preview['loss_line_id'])
    assert lines[0].source_recovery_line_id is None and lines[0].target_condition == 'new'
    pending = db.get(StockAccount, lines[0].reserved_account_id)
    assert pending.availability_bucket == 'return_pending'
    assert pending.custodian_person_id == allowed.actor.person_id
    assert db.get(StockBalance, pending.id).quantity == lines[0].quantity
    assert len(tuple(db.scalars(select(InventoryTransaction.id)))) == before_tx + 1
    assert len(tuple(db.scalars(select(InventoryMovement.id).where(InventoryMovement.transaction_id == child.posting_transaction_id)))) == 1
    assert not tuple(db.scalars(select(Shipment.id))) and not tuple(db.scalars(select(Receipt.id)))
    row = db.get(StockLossDisposition, UUID(result['disposition_id']))
    assert facts.verified(db, row=row) == result
    frozen = db.get(StockAccount, row.source_account_id)
    remaining, _, balance = approved_plan._remaining(db, frozen)
    assert len(remaining) == 1 and remaining[0].line_id == UUID(preview['loss_line_id'])
    assert remaining[0].quantity == 0 and remaining[0].serial_ids == frozenset() and balance.quantity == 0
    before = snapshot(db)
    assert commands.execute_loss_return(db, actor=approved.actor, request=value) == result
    assert snapshot(db) == before


@pytest.mark.parametrize('stage', ['child', 'root', 'final_proof'])
def test_late_failure_rolls_back_pending_account_child_and_entire_stock_effect(db, approved, route, monkeypatch, stage):
    value, preview = execute_request(db, approved, route)
    before = snapshot(db)
    def failure(*args, **kwargs):
        raise RuntimeError('synthetic late return failure')
    if stage == 'child': monkeypatch.setattr(commands, '_record_child', failure)
    elif stage == 'root': monkeypatch.setattr(commands.disposition_commands, '_record', failure)
    else: monkeypatch.setattr(facts, 'verified', failure)
    with pytest.raises(RuntimeError, match='synthetic late return failure'):
        commands.execute_loss_return(db, actor=approved.actor, request=value)
    db.rollback()
    assert snapshot(db) == before
    assert db.get(StockAccount, UUID(preview['pending_account_id'])) is None
    assert db.get(StockOperationOrder, UUID(preview['derived_return_operation_id'])) is None


def test_new_key_cannot_derive_again_from_same_headquarters_decision(db, approved, route):
    value, _ = execute_request(db, approved, route)
    commands.execute_loss_return(db, actor=approved.actor, request=value)
    db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        commands.execute_loss_return(db, actor=approved.actor, request=value.model_copy(update={
            'request_id':uuid4().hex, 'idempotency_key':uuid4().hex}))
    assert error.value.code == 'stock_loss_return_request_conflict'
    assert snapshot(db) == before


def test_current_authority_revocation_refuses_even_an_exact_replay(db, allowed, approved, route):
    value, _ = execute_request(db, approved, route)
    commands.execute_loss_return(db, actor=approved.actor, request=value)
    db.commit()
    allowed.world.current_principal = replace(approved.actor, entitlements=tuple(
        item for item in approved.actor.entitlements if item.action != 'dispose_loss'))
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        commands.execute_loss_return(db, actor=approved.actor, request=value)
    assert error.value.code == 'stock_loss_disposition_forbidden'
    assert snapshot(db) == before


def test_shared_freeze_releases_only_exact_line_and_rejects_stale_plan(db, allowed, approved, regional, route, monkeypatch):
    value, original_preview = execute_request(db, approved, route)
    second = second_report(db, allowed, regional, monkeypatch)
    allowed.world.current_principal = approved.actor
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        commands.execute_loss_return(db, actor=approved.actor, request=value)
    assert error.value.code == 'stock_loss_return_plan_changed'
    assert snapshot(db) == before
    assert db.get(StockAccount, UUID(original_preview['pending_account_id'])) is None
    preview = plan.preview_loss_return(db, actor=approved.actor, request=value)
    value = value.model_copy(update={'expected_plan_hash':preview['plan_hash']})
    result = commands.execute_loss_return(db, actor=approved.actor, request=value)
    db.commit()
    source = db.get(StockAccount, UUID(preview['source_account_id']))
    remaining, _, balance = approved_plan._remaining(db, source)
    assert balance.quantity == 1 and sorted(item.quantity for item in remaining) == [0, 1]
    held = next(item for item in remaining if item.quantity == 1)
    other_line = db.get(StockOperationLine, held.line_id)
    assert other_line.operation_id == second.operation_id
    if allowed.tracked:
        assert held.serial_ids == frozenset({allowed.serials[4].id})
        assert db.get(SerialCurrentPosition, allowed.serials[4].id).stock_account_id == source.id
        assert db.get(SerialCurrentPosition, allowed.serials[3].id).stock_account_id == UUID(preview['pending_account_id'])
    assert facts.verified(db, row=db.get(StockLossDisposition, UUID(result['disposition_id']))) == result


@pytest.mark.parametrize('damage', ['requester','reason','child_plan','line_quantity','root_quantity','child_state','notification_target','duplicate_event','state_time','outbox_time'])
def test_changed_lineage_or_domain_evidence_never_verifies_as_posted(db, approved, route, damage):
    value, _ = execute_request(db, approved, route)
    result = commands.execute_loss_return(db, actor=approved.actor, request=value)
    db.commit()
    row = db.get(StockLossDisposition, UUID(result['disposition_id']))
    child = db.get(StockOperationOrder, row.return_operation_id)
    line = db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id == child.id))
    # Deliberately corrupt only this SQLite fixture's persisted evidence. Actual
    # raw-role PostgreSQL COMMIT refusal needs the future additive migration.
    if damage == 'requester': child.requester_id = approved.actor.person_id
    elif damage == 'reason': child.reason = 'Unapproved reason'
    elif damage == 'child_plan': child.plan_jsonb = {}
    elif damage == 'line_quantity': line.quantity += 1
    elif damage == 'root_quantity': row.quantity += 1
    elif damage == 'child_state':
        state = db.scalar(select(StateTransitionEvent).where(StateTransitionEvent.aggregate_type == 'stock_operation_order',
            StateTransitionEvent.aggregate_id == str(child.id)))
        state.metadata_jsonb = {**state.metadata_jsonb, 'unexpected':True}
    elif damage == 'notification_target':
        notification = db.scalar(select(NotificationEvent).where(NotificationEvent.business_type == 'stock_loss_disposition',
            NotificationEvent.business_id == str(row.id)))
        target = db.scalar(select(NotificationPersonTarget).where(NotificationPersonTarget.event_id == notification.id))
        target.person_id = approved.actor.person_id
    elif damage in {'state_time','outbox_time'}:
        model = StateTransitionEvent if damage == 'state_time' else OutboxEvent
        event = db.scalar(select(model).where(model.aggregate_type == 'stock_operation_order', model.aggregate_id == str(child.id)))
        if damage == 'state_time': event.occurred_at += timedelta(seconds=1)
        else: event.created_at += timedelta(seconds=1)
    else:
        db.add(OutboxEvent(event_type='stock_loss.return_derived', aggregate_type='stock_operation_order',
            aggregate_id=str(child.id), idempotency_key='duplicate-'+uuid4().hex, payload_jsonb={},
            available_at=row.created_at, created_at=row.created_at, updated_at=row.created_at))
    db.flush()
    with pytest.raises(InventoryReadError):
        facts.verified(db, row=row)


def test_queue_retry_does_not_change_original_stock_or_break_historical_proof(db, approved, route):
    value, _ = execute_request(db, approved, route)
    result = commands.execute_loss_return(db, actor=approved.actor, request=value)
    db.commit()
    row = db.get(StockLossDisposition, UUID(result['disposition_id']))
    frozen_quantity = db.get(StockBalance, row.source_account_id).quantity
    pending_quantity = db.get(StockBalance, row.target_account_id).quantity
    for event in db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id.in_(
            (str(row.return_operation_id), str(row.id), str(row.posting_transaction_id))))):
        event.status = 'failed'
        event.attempts += 1
        event.available_at += timedelta(minutes=5)
        event.updated_at += timedelta(minutes=5)
    db.commit()
    assert facts.verified(db, row=row) == result
    assert db.get(StockBalance, row.source_account_id).quantity == frozen_quantity
    assert db.get(StockBalance, row.target_account_id).quantity == pending_quantity


def test_historical_proof_binds_original_ids_without_reexecuting_allocators(db, approved, route, monkeypatch):
    from app.formal_services import stock_loss_return_facts as returned
    value, _ = execute_request(db, approved, route)
    result = commands.execute_loss_return(db, actor=approved.actor, request=value)
    db.commit()
    row = db.get(StockLossDisposition, UUID(result['disposition_id']))
    before = snapshot(db)
    def retired_allocator(*args, **kwargs):
        raise AssertionError('historical proof must bind persisted provenance, not rerun allocation')
    monkeypatch.setattr(returned, 'uuid5', retired_allocator)
    monkeypatch.setattr(returned, 'child_line_id', retired_allocator)
    assert facts.verified(db, row=row) == result
    assert snapshot(db) == before
