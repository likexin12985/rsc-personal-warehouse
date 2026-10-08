"""Private real unified posting; production COMMIT/lifecycle guards separate."""
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import select
from app.inventory_models import StockBalance, InventorySerial, SerialCurrentPosition, InventoryTransaction
from app.foundation_models import NotificationEvent
from app.formal_services import stock_scrap_plan, inventory_posting as posting
from app.formal_services.stock_scrap import execution as service, posting_authority, events
from app.formal_services.serial_ledger import rebuild_serial_states, SerialLedgerError
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap.tables import tables
from test_stock_scrap_plan import world, stock, allowed, evidence, regional, headquarters, approved, request, snapshot
from test_stock_scrap_execution_bundle import execution
from stock_scrap_writer_fixture import db


def execute_command(db, actor, preview):
    prepared = stock_scrap_plan.prepare(db, actor=actor, request=preview)
    return execution(preview, prepared), prepared


def verify_posted(db, actor, command, prepared):
    source = UUID(prepared.document['source_account_id'])
    quantity = db.get(StockBalance, source).quantity
    result = service.execute(db, actor=actor, request=command)
    db.commit()
    assert result['status'] == 'posted' and result['target_account_id'] is None
    assert db.get(StockBalance, source).quantity == quantity - 1
    tx = db.get(InventoryTransaction, UUID(result['posting_transaction_id']))
    assert tx.movement_type == 'scrap' and tx.status == 'posted'
    assert result['source_kind'] == command.source.kind
    ids = tuple(UUID(s) for s in prepared.document['serial_ids'])
    states = rebuild_serial_states(db, ids)
    for identifier in ids:
        assert db.get(InventorySerial, identifier).lifecycle_status == states[identifier].lifecycle_status == 'scrapped'
        assert db.get(SerialCurrentPosition, identifier).stock_account_id is None
        assert states[identifier].last_movement_id == UUID(result['posting_movement_id'])
        assert states[identifier].admission_movement_id == UUID(prepared.document['serials'][0]['admission_movement_id'])
    notifications = db.scalars(select(NotificationEvent).where(
        NotificationEvent.business_type == tx.source_document_type, NotificationEvent.business_id == tx.source_document_id)).all()
    assert len(notifications) == 1 and notifications[0].payload_jsonb['posting_transaction_id'] == str(tx.id)
    assert db.scalar(select(NotificationEvent.id).where(NotificationEvent.business_type == 'stock_operation_scrap')) is None
    before = snapshot(db)
    with pytest.raises(InventoryReadError, match='原请求已有事实'):
        service.execute(db, actor=actor, request=command)
    db.rollback()
    assert snapshot(db) == before
    return result


def test_original_scrap_really_posts_and_reconstructs_sn(db, approved, monkeypatch):
    preview = request(db, approved, monkeypatch)
    command, prepared = execute_command(db, approved.actor, preview)
    verify_posted(db, approved.actor, command, prepared)


def test_notification_failure_rolls_back_every_posting_fact(db, approved, monkeypatch):
    command, prepared = execute_command(db, approved.actor, request(db, approved, monkeypatch))
    before = snapshot(db)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic scrap notification failure')
    monkeypatch.setattr(events, 'record_business_notification', fail)
    with pytest.raises(RuntimeError, match='synthetic scrap notification'):
        service.execute(db, actor=approved.actor, request=command)
    db.rollback()
    assert snapshot(db) == before
    assert db.execute(select(tables()['stock_scrap_lines'])).all() == []


def test_late_authority_revocation_requires_whole_transaction_rollback(db, allowed, approved, monkeypatch):
    command, _ = execute_command(db, approved.actor, request(db, approved, monkeypatch))
    before = snapshot(db)
    record = events.record
    def revoke(*args, **kwargs):
        result = record(*args, **kwargs)
        allowed.world.current_principal = replace(approved.actor,
            authorization_version=approved.actor.authorization_version + 1)
        return result
    monkeypatch.setattr(events, 'record', revoke)
    with pytest.raises((InventoryReadError, posting.InventoryPostingError)) as error:
        service.execute(db, actor=approved.actor, request=command)
    assert error.value.code == 'actor_principal_stale'
    db.rollback()
    assert snapshot(db) == before


def test_generic_posting_cannot_borrow_a_valid_scrap_command(db, approved, monkeypatch):
    command, prepared = execute_command(db, approved.actor, request(db, approved, monkeypatch))
    permit = posting_authority.prepare(db, actor=approved.actor, request=command)
    before = snapshot(db)
    with pytest.raises(posting.InventoryPostingError) as error:
        posting.post_inventory_transaction(db, actor=approved.actor, command=permit.bundle.posting_command,
            idempotency_key=permit.bundle.posting_key, request_id=command.request_id,
            permission_resource='stock_operation', permission_action='dispose_loss')
    assert error.value.code == 'stock_scrap_posting_authority_invalid'
    db.rollback()
    assert snapshot(db) == before


@pytest.mark.parametrize('change', ['clone', 'transaction', 'savepoint'])
def test_private_permit_is_exact_object_and_transaction_bound(db, approved, monkeypatch, change):
    command, _ = execute_command(db, approved.actor, request(db, approved, monkeypatch))
    permit = posting_authority.prepare(db, actor=approved.actor, request=command)
    if change == 'clone':
        permit = replace(permit)
    elif change == 'savepoint':
        db.begin_nested()
    else:
        db.rollback()
    with pytest.raises(posting.InventoryPostingError) as error:
        posting._post_new_transaction(db, actor=approved.actor, command=permit.bundle.posting_command,
            idempotency_key_hash='a'*64, request_hash='b'*64, request_reference='test-invalid-permit',
            permission_resource='stock_operation', permission_action=permit.action,
            reversed_transaction_id=None, event_suffix='posted', scrap_authority=permit)
    assert error.value.code == 'stock_scrap_posting_authority_invalid'


def test_rebuild_rejects_a_forged_previous_serial_movement(db, allowed, approved, monkeypatch):
    if not allowed.tracked:
        pytest.skip('quantity mode has no serial position')
    command, prepared = execute_command(db, approved.actor, request(db, approved, monkeypatch))
    result = verify_posted(db, approved.actor, command, prepared)
    table = tables()['stock_scrap_serials']
    # Deliberate corruption in a disposable SQLite fixture. Production must
    # separately reject this UPDATE through its append-only SQL guard.
    db.execute(table.update().values(previous_movement_id=UUID(result['posting_movement_id'])))
    db.commit()
    with pytest.raises(SerialLedgerError):
        rebuild_serial_states(db, tuple(UUID(s) for s in prepared.document['serial_ids']))
