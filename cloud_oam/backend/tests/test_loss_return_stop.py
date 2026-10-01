"""Actual unified candidate posting; native migration/concurrency separate."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.inventory_models import StockBalance, SerialCurrentPosition
from app.foundation_models import AuthIdentity, OutboxEvent, Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.stock_operation_models import StockOperationOrder
from app.formal_services import stock_return_origins as origins
from app.formal_services import stock_loss_disposition_recovery as original_recovery
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import inverse_posting, inverse_recovery, return_stop
from app.formal_services.stock_loss_corrections.history_chain import verify_inverse
from app.formal_services.stock_loss_corrections.correction_models import StockLossDispositionReversal as Inverse
from test_stock_loss_correction_inverse_posting import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, execution, prepared, command, snapshot as inverse_snapshot,
)
from test_stock_loss_original_history_recovery import bind_fixture


pytestmark = pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)


def snapshot(db):
    return inverse_snapshot(db), tuple(db.execute(text('SELECT * FROM stock_loss_return_stops ORDER BY id')))


def reader(db, actor):
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
        Permission.action == 'read', Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='stock_operation', action='read', field_code='', description='Synthetic read')
        db.add(permission); db.flush()
    if not db.scalar(select(RolePermission.id).where(RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id)):
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    return load_formal_principal(db, actor.user_id)


def test_stop_and_inverse_preserve_original_restore_only_frozen_and_recover(db, prepared, allowed, execution):
    w = prepared; root = w.root; request = command(db, w)
    original = tuple(db.execute(text('SELECT * FROM stock_operation_orders ORDER BY id')))
    before_source = db.get(StockBalance, root.target_account_id).quantity
    before_target = db.get(StockBalance, root.source_account_id).quantity
    result = inverse_posting.execute_unshipped_return_inverse(db, actor=w.actor, request=request)
    db.commit()
    inverse = db.get(Inverse, UUID(result['reversal_id']))
    stop = db.scalars(select(Stop)).one()
    assert stop.reversal_id == inverse.id and stop.return_operation_id == root.return_operation_id
    assert db.get(StockBalance, root.target_account_id).quantity == before_source - root.quantity
    assert db.get(StockBalance, root.source_account_id).quantity == before_target + root.quantity
    assert result['stock_effect'] == 'restores_original_frozen_share'
    assert tuple(db.execute(text('SELECT * FROM stock_operation_orders ORDER BY id'))) == original
    for identifier in root.plan_jsonb['serial_ids']:
        position = db.get(SerialCurrentPosition, UUID(identifier))
        assert position.stock_account_id == root.source_account_id
        assert position.last_movement_id == inverse.posting_movement_id
    actor = reader(db, w.actor)
    with pytest.raises(InventoryReadError) as missing_binding:
        original_recovery.lookup_disposition_request(db, actor=actor, request=execution.command, flow='return')
    assert missing_binding.value.code == 'stock_loss_disposition_history_unknown'
    # Explicit SQLite fixture, not proof of native request-key registration.
    bind_fixture(db, inverse, request, 'inverse')
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        assert verify_inverse(db, reversal_id=inverse.id).posting_transaction_id == inverse.posting_transaction_id
        recovered = inverse_recovery.lookup_unshipped_return_inverse(db, actor=actor, request=request)
        assert recovered['request_state'] == 'found' and recovered['result'] == result
        assert recovered['retry_allowed'] is False
        original_request = original_recovery.lookup_disposition_request(
            db, actor=actor, request=execution.command, flow='return')
        assert original_request['lookup_status'] == 'found'
        assert original_request['disposition']['disposition_id'] == str(root.id)
        assert original_request['disposition']['stock_effect'] == 'frozen_to_return_pending'
        # Original historical origin remains readable to its actual sender.
        order = db.get(StockOperationOrder, root.return_operation_id)
        assert origins.verify_return_origin(db, actor=allowed.actor, order=order).operation_id == order.id
        with pytest.raises(InventoryReadError) as error:
            return_stop.require_open(db, operation_id=order.id)
        assert error.value.code == 'loss_return_stopped'
        assert snapshot(db) == before and not db.new and not db.dirty
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
    with pytest.raises(InventoryReadError) as error:
        inverse_posting.execute_unshipped_return_inverse(db, actor=actor, request=request)
    assert error.value.code == 'loss_inverse_request_requires_recovery'
    db.rollback(); assert snapshot(db) == before


@pytest.mark.parametrize('failure', ['stop_record', 'final_proof'])
def test_stop_or_final_proof_failure_rolls_back_all_stock_and_evidence(db, prepared, monkeypatch, failure):
    request = command(db, prepared); before = snapshot(db)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic stop transaction failure')
    monkeypatch.setattr(return_stop if failure == 'stop_record' else inverse_posting,
        'record' if failure == 'stop_record' else 'verify_original_inverse', fail)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(RuntimeError, match='synthetic stop transaction failure'):
            inverse_posting.execute_unshipped_return_inverse(db, actor=prepared.actor, request=request)
    finally:
        savepoint.rollback(); db.expire_all()
    assert snapshot(db) == before


def test_missing_or_forged_stop_cannot_prove_an_inverse(db, prepared):
    request = command(db, prepared)
    result = inverse_posting.execute_unshipped_return_inverse(db, actor=prepared.actor, request=request)
    db.commit(); inverse_id = UUID(result['reversal_id'])
    for damage in ('missing_stop', 'fingerprint', 'missing_event'):
        before = snapshot(db); savepoint = db.begin_nested()
        try:
            stop = db.scalars(select(Stop)).one()
            if damage == 'missing_stop': db.delete(stop)
            elif damage == 'fingerprint': stop.evidence_fingerprint = 'f' * 64
            else:
                db.delete(db.scalars(select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == return_stop.AGGREGATE)).one())
            db.flush()
            with pytest.raises(InventoryReadError): verify_inverse(db, reversal_id=inverse_id)
        finally:
            savepoint.rollback(); db.expire_all()
        assert snapshot(db) == before


def test_existing_generic_inverse_entry_stays_closed_for_returns(db, prepared):
    request = command(db, prepared); before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        inverse_posting.execute_account_inverse(db, actor=prepared.actor, request=request)
    assert error.value.code == 'loss_inverse_dedicated_compensation_required'
    assert snapshot(db) == before


def physical_actor(db, actor, action):
    # Older stock fixtures use an in-memory sender principal. This test uses
    # the actual DB loader, so explicitly establish its synthetic identity.
    if not db.scalar(select(AuthIdentity.id).where(AuthIdentity.user_id == actor.user_id)):
        db.add(AuthIdentity(user_id=actor.user_id, identity_type='mobile', provider_key='test',
            identifier_hash=uuid4().hex + uuid4().hex, hash_version=1, status='active',
            verified_at=datetime.now(timezone.utc)-timedelta(days=1), revoked_at=None))
        db.commit()
    current = load_formal_principal(db, actor.user_id)
    codes = {assignment.role_code for assignment in current.assignments}
    role = db.scalars(select(Role).where(Role.code.in_(codes))).first()
    assert role is not None
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
        Permission.action == action, Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='stock_operation', action=action, field_code='', description='Synthetic physical return')
        db.add(permission); db.flush()
    if not db.scalar(select(RolePermission.id).where(RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id)):
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    return load_formal_principal(db, actor.user_id)


def test_current_fulfillment_stops_only_after_scope_check_and_recovery_survives_revoke(db, prepared, allowed):
    request = command(db, prepared)
    result = inverse_posting.execute_unshipped_return_inverse(db, actor=prepared.actor, request=request)
    db.commit()
    sender = physical_actor(db, allowed.actor, 'outbound_return')
    outsider = physical_actor(db, prepared.actor, 'outbound_return')
    for actor, code in ((sender, 'loss_return_stopped'), (outsider, 'stock_return_not_found')):
        before = snapshot(db)
        with pytest.raises(InventoryReadError) as error:
            origins.authorize_return_fulfillment(db, actor=actor,
                operation_id=prepared.root.return_operation_id, action='outbound_return')
        assert error.value.code == code and snapshot(db) == before
    reader(db, prepared.actor)
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
        Permission.action == 'reverse_loss'))
    db.scalars(select(RolePermission).where(RolePermission.permission_id == permission.id)).one().effect = 'deny'
    db.commit(); current = load_formal_principal(db, prepared.actor.user_id)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        found = inverse_recovery.lookup_unshipped_return_inverse(db, actor=current, request=request)
        assert found['result'] == result and found['retry_allowed'] is False
        assert snapshot(db) == before
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
    with pytest.raises(InventoryReadError) as error:
        inverse_posting.execute_unshipped_return_inverse(db, actor=current, request=request)
    assert error.value.code == 'stock_loss_correction_forbidden'
    assert snapshot(db) == before
