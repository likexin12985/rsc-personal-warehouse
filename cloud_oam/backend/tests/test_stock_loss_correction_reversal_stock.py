"""Real original stock services and current DB permissions; no inverse write."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
import pytest
from sqlalchemy import select, text
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission, OutboxEvent
from app.inventory_models import CustodyAssignment, StockBalance, StockLocation, FormalMaterial, InventorySerial
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.formal_services import inventory_posting as posting
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_correction_history_inventory import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution
from test_stock_loss_correction_business_events import snapshot
from app.formal_services.stock_loss_corrections.request_contracts import ReversalPreview
from app.formal_services.stock_loss_corrections.reversal_stock import prepare
from app.formal_services.stock_loss_corrections import reversal_stock

def grant(db, execution, monkeypatch):
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = Permission(resource='stock_operation', action='reverse_loss', field_code='', description='Synthetic loss inverse preparation')
    db.add(permission)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    monkeypatch.setattr(posting, 'load_formal_principal', load_formal_principal)
    return load_formal_principal(db, execution.actor.user_id)

@pytest.fixture
def prepared(db, execution, monkeypatch):
    result = execution.commit()
    root = db.get(StockLossDisposition, UUID(result['disposition_id']))
    actor = grant(db, execution, monkeypatch)
    order = db.get(StockOperationOrder, root.operation_id)
    request = ReversalPreview(root_disposition_id=root.id, expected_root_request_hash=root.request_hash, expected_submission_plan_hash=order.plan_hash, reason='核验实物后恢复原冻结份额', reversed_correction_id=None, expected_execution_request_hash=root.request_hash)
    return SimpleNamespace(root=root, order=order, actor=actor, request=request)

def test_four_original_kinds_prepare_exact_inverse_query_only_and_stable_hash(db, prepared):
    w = prepared
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    first = prepare(db, actor=w.actor, request=w.request)
    second = prepare(db, actor=w.actor, request=w.request)
    assert first.document == second.document and first.plan_hash == second.plan_hash
    result = first.document
    assert result['source_account_id'] == str(w.root.target_account_id)
    assert result['target_account_id'] == str(w.root.source_account_id)
    assert result['quantity'] == format(w.root.quantity, '.3f')
    assert result['serial_ids'] == w.root.plan_jsonb['serial_ids']
    assert result['restored_line_id'] == str(w.root.line_id)
    assert result['stage'] == 'stock_preparation_only'
    if w.root.disposition == 'return_to_region':
        boundary = result['return_boundary']
        assert boundary['operation_id'] == str(w.root.return_operation_id)
        assert all((boundary[key] == [] for key in ('outbounds', 'shipments', 'receipts', 'inbounds', 'cancellations')))
        assert len(boundary['evidence_fingerprint']) == 64
        assert len(boundary['lines']) == 1
        assert boundary['lines'][0]['quantity'] == result['quantity']
        assert boundary['lines'][0]['serial_ids'] == result['serial_ids']
    else:
        assert result['return_boundary'] is None
    assert snapshot(db) == before and (not db.new) and (not db.dirty) and (not db.deleted)

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_permission_custody_projection_master_and_history_damage_are_rejected_without_write(db, prepared):
    w = prepared
    for variant in ('permission', 'custody', 'balance', 'location', 'material', 'original_outbox'):
        before = snapshot(db)
        savepoint = db.begin_nested()
        try:
            if variant == 'permission':
                permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation', Permission.action == 'reverse_loss'))
                rule = db.scalar(select(RolePermission).where(RolePermission.permission_id == permission.id))
                rule.effect = 'deny'
            elif variant == 'custody':
                custody = db.get(CustodyAssignment, w.root.custody_assignment_id)
                custody.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
            elif variant == 'balance':
                db.get(StockBalance, w.root.target_account_id).quantity += Decimal('100')
            elif variant == 'location':
                db.get(StockLocation, w.order.source_location_id).status = 'inactive'
            elif variant == 'material':
                from app.inventory_models import StockAccount
                account = db.get(StockAccount, w.root.source_account_id)
                db.get(FormalMaterial, account.material_id).status = 'inactive'
            else:
                outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == 'stock_loss_disposition', OutboxEvent.aggregate_id == str(w.root.id)))
                db.delete(outbox)
            db.flush()
            changed = snapshot(db)
            with pytest.raises((InventoryReadError, InventoryPostingError)):
                prepare(db, actor=w.actor, request=w.request)
            assert snapshot(db) == changed
        finally:
            savepoint.rollback()
            db.expire_all()
        assert snapshot(db) == before

@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_late_reference_authority_change_cannot_issue_a_plan(db, prepared, monkeypatch):
    w = prepared
    original = reversal_stock.request_authority.references
    calls = []

    def changed(db, **kwargs):
        result = original(db, **kwargs)
        calls.append(True)
        if len(calls) == 2:
            from app.models import User
            db.get(User, w.actor.user_id).authorization_version += 1
            db.flush()
            return original(db, **kwargs)
        return result
    monkeypatch.setattr(reversal_stock.request_authority, 'references', changed)
    before = snapshot(db)
    savepoint = db.begin_nested()
    try:
        with pytest.raises(InventoryPostingError) as caught:
            prepare(db, actor=w.actor, request=w.request)
        assert caught.value.code == 'actor_principal_stale'
    finally:
        savepoint.rollback()
        db.expire_all()
    assert len(calls) == 2 and snapshot(db) == before
