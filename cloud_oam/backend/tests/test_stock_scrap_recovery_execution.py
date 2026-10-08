"""Actual approved scrap inverse, exact frozen balance and immutable SN proof."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID
import pytest
from sqlalchemy import select
from app.foundation_models import Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.inventory_models import StockBalance, InventorySerial, SerialCurrentPosition, InventoryTransaction
from app.stock_scrap_recovery_schemas import ScrapRecoveryPreview, ScrapRecoveryExecute
from app.formal_services import inventory_posting as posting
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.stock_scrap import recovery_execution as service, recovery_plan as plan, recovery_posting_authority as permits
from app.formal_services.stock_scrap import recovery_authority as authority
from app.formal_services.stock_scrap.tables import tables
from test_stock_scrap_recovery_approval import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found,
    submit, region_request, hq_request, coordinates, stock_state, all_state,
)


@pytest.fixture
def ready_to_restore(db, found):
    applied = submit(db, found, 'apply', found.application)
    region = submit(db, found, 'regional', region_request(found, applied))
    final = submit(db, found, 'headquarters', hq_request(found, applied, region))
    permission = Permission(resource='stock_operation', action=authority.ACTIONS['execute'], field_code='', description='Synthetic physical recovery')
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    db.add(permission); db.flush(); db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow')); db.commit()
    actor = load_formal_principal(db, found.actors['headquarters'].user_id)
    found.world.current_principal = actor
    preview = ScrapRecoveryPreview(source=found.application.source, recovery_request_id=applied['fact_id'],
        expected_request_hash=applied['request_hash'], headquarters_review_id=final['fact_id'],
        expected_headquarters_hash=final['request_hash'], reason='按批准找回准确原报废，恢复后仍冻结')
    checked = plan.prepare(db, actor=actor, request=preview)
    command = ScrapRecoveryExecute(**preview.model_dump(), action='execute_scrap_recovery',
        expected_plan_hash=checked.plan_hash, **coordinates())
    return SimpleNamespace(found=found, actor=actor, command=command, checked=checked)


def snapshot(db):
    return all_state(db), stock_state(db), tuple(db.execute(select(tables()['stock_scrap_recovery_executions']))), tuple(db.execute(select(tables()['stock_loss_disposition_reversals'])))


def test_recovery_posts_exact_inverse_to_frozen_and_restores_original_sn(db, ready_to_restore):
    w = ready_to_restore
    doc = w.checked.document
    target = UUID(doc['target_account_id'])
    was = db.get(StockBalance, target).quantity
    result = service.execute(db, actor=w.actor, request=w.command)
    db.commit()
    assert result['stock_effect'] == 'restores_original_frozen_share' and result['source_account_id'] is None
    assert db.get(StockBalance, target).quantity == was + 1
    tx = db.get(InventoryTransaction, UUID(result['posting_transaction_id']))
    assert tx.reversed_transaction_id == UUID(doc['original_transaction_id'])
    ids = tuple(UUID(s) for s in doc['serial_ids'])
    states = rebuild_serial_states(db, ids)
    for identifier in ids:
        assert states[identifier].lifecycle_status == db.get(InventorySerial, identifier).lifecycle_status == 'active'
        assert states[identifier].stock_account_id == db.get(SerialCurrentPosition, identifier).stock_account_id == target
        assert states[identifier].admission_movement_id == UUID(doc['serials'][0]['admission_movement_id'])
    proof = verify_chain(db, root_disposition_id=UUID(doc['root_disposition_id']))
    assert any(p.reversal_id == UUID(result['reversal_id']) for p in proof.inverse_proofs)
    before = snapshot(db)
    with pytest.raises(InventoryReadError):
        service.execute(db, actor=w.actor, request=w.command)
    db.rollback()
    assert snapshot(db) == before


def test_generic_inventory_cannot_borrow_recovery_command_even_without_sn(db, ready_to_restore):
    w = ready_to_restore
    permit = permits.prepare(db, actor=w.actor, request=w.command)
    before = snapshot(db)
    with pytest.raises(posting.InventoryPostingError) as error:
        posting._post_new_transaction(db, actor=w.actor, command=permit.command,
            idempotency_key_hash=permit.key_hash, request_hash=posting._reversal_request_hash(w.actor, permit.reversal),
            request_reference=posting._request_reference(w.command.request_id), permission_resource='stock_operation',
            permission_action=authority.ACTIONS['execute'], reversed_transaction_id=permit.reversal.original_transaction_id,
            event_suffix='reversed', occurred_at=permit.preparation.checked_at)
    assert error.value.code == 'stock_scrap_recovery_posting_authority_invalid'
    db.rollback()
    assert snapshot(db) == before


@pytest.mark.parametrize('attack', ['clone', 'savepoint', 'transaction'])
def test_recovery_permit_is_bound_to_exact_object_and_transaction(db, ready_to_restore, attack):
    w = ready_to_restore
    permit = permits.prepare(db, actor=w.actor, request=w.command)
    if attack == 'clone':
        permit = replace(permit)
    elif attack == 'savepoint':
        db.begin_nested()
    else:
        db.rollback()
    with pytest.raises(posting.InventoryPostingError) as error:
        posting._post_new_transaction(db, actor=w.actor, command=permit.command,
            idempotency_key_hash=permit.key_hash, request_hash=posting._reversal_request_hash(w.actor, permit.reversal),
            request_reference=posting._request_reference(w.command.request_id), permission_resource='stock_operation',
            permission_action=authority.ACTIONS['execute'], reversed_transaction_id=permit.reversal.original_transaction_id,
            event_suffix='reversed', occurred_at=permit.preparation.checked_at, scrap_recovery_authority=permit)
    assert error.value.code == 'stock_scrap_recovery_posting_authority_invalid'


def test_late_revocation_rolls_back_inverse_and_sn_recovery(db, ready_to_restore, monkeypatch):
    w = ready_to_restore
    original = service.business_events.record
    def revoke(*args, **kwargs):
        original(*args, **kwargs)
        w.found.world.current_principal = replace(w.actor, authorization_version=w.actor.authorization_version+1)
    monkeypatch.setattr(service.business_events, 'record', revoke)
    before = snapshot(db)
    with pytest.raises(posting.InventoryPostingError) as error:
        service.execute(db, actor=w.actor, request=w.command)
    assert error.value.code == 'actor_principal_stale'
    db.rollback()
    assert snapshot(db) == before
