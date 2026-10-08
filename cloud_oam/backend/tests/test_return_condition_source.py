"""Current stock proof is separate from old receipts and permission to correct."""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.inventory_models import StockAccount, StockBalance, StockLocation, SerialCurrentPosition, InventoryMovement
from app.stock_operation_models import StockOperationReceiptSerial
from app.formal_services import stock_return_inbound_plan as planning, stock_return_inbound_commands as commands
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_return_inbound_quality import AcceptedPart
from app.formal_services.stock_loss_corrections import return_history, return_condition_source as subject
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.work_order_return_sources import _hash
from test_loss_return_damaged_inbound import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, acceptance, regional_opening,
    receipt_evidence, abnormal, post,
)
from test_loss_return_history import headquarters_reader
from test_stock_return_inbound import snapshot


@pytest.fixture
def prepared(db, stock, approved, derived, acceptance, monkeypatch):
    file, _ = receipt_evidence(db, stock, acceptance, monkeypatch)
    request = abnormal(acceptance, file.id, 'damaged')
    damaged = Decimal(1) if stock.tracked else Decimal('.375')
    acceptance.request = request.model_copy(update={'lines': (
        request.lines[0].model_copy(update={'damaged_qty': damaged}),)})
    modern = planning.plan_return_inbound

    def old_parts(db, *, origin, source, at):
        ids = tuple(db.scalars(select(StockOperationReceiptSerial.serial_id).where(
            StockOperationReceiptSerial.line_id == origin.id,
            StockOperationReceiptSerial.result == 'accepted').order_by(StockOperationReceiptSerial.serial_id)))
        return (AcceptedPart(source.condition_code, origin.accepted_qty, ids),)

    def old_plan(*args, **kwargs):
        plan = modern(*args, **kwargs)
        plan['schema_version'] = '1.0'
        plan['plan_hash'] = _hash(planning.plan_document(plan))
        return plan

    with monkeypatch.context() as old:
        old.setattr(planning, 'receipt_parts', old_parts)
        old.setattr(commands, 'plan_return_inbound', old_plan)
        _, posted = post(db, acceptance, schema='1.0')
    actor = headquarters_reader(db, stock, approved)
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = db.scalar(select(Permission).where(Permission.resource == 'inventory',
        Permission.action == 'read', Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='inventory', action='read', field_code='', description='Synthetic stock read')
        db.add(permission); db.flush()
    grant = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
        RolePermission.permission_id == permission.id))
    if grant is None:
        grant = RolePermission(role_id=role.id, permission_id=permission.id, effect='allow')
        db.add(grant)
    db.commit()
    actor = load_formal_principal(db, actor.user_id)
    stock.world.current_principal = actor
    history = return_history.read(db, actor=actor, root_disposition_id=UUID(derived.result['disposition_id']))
    issue = history.classification_exceptions[0]
    selection = subject.ConditionSourceSelection(root_disposition_id=history.root_disposition_id,
        inbound_line_id=issue.inbound_line_id, expected_history_fingerprint=history.evidence_fingerprint)
    # The inherited inventory fixture replaces this loader with an in-memory
    # principal. Current-access tests must exercise the actual database loader.
    monkeypatch.setattr(posting, 'load_formal_principal', load_formal_principal)
    return actor, selection, issue, grant


@pytest.mark.parametrize('stock', ['quantity', 'serial'], indirect=True)
def test_exact_current_stock_evidence_is_readonly_and_never_a_posting_permit(db, stock, prepared):
    actor, selection, issue, _ = prepared
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        result = subject.inspect_source(db, actor=actor, selection=selection)
        value = result.document
        assert value['source_status'] == 'recorded_stock_retained'
        assert value['historical_damaged_quantity'] == ('1.000' if stock.tracked else '0.375')
        assert value['account_balance_quantity'] == '1.000'
        assert value['current_projection_verified'] is True
        assert value['posting_allowed'] is False and value['correction_authorized'] is False
        assert value['physical_verification_required'] is True
        assert value['source_account_id'] == str(issue.original_target_account_id)
        assert value['recorded_condition'] == 'new' and value['required_condition'] == 'damaged'
        assert {s['serial_id'] for s in value['serials']} == {str(i) for i in issue.affected_serial_ids}
        assert all(s['retained_at_original_inbound'] for s in value['serials'])
        assert result.evidence_hash == _hash(value)
        assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
        value['posting_allowed'] = True
        assert result.document['posting_allowed'] is False
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
@pytest.mark.parametrize('change', ['old_fingerprint', 'foreign_line', 'balance', 'custody', 'inventory_permission'])
def test_wrong_binding_or_unproved_current_source_is_not_reclassified(db, stock, prepared, change):
    actor, selection, issue, grant = prepared
    if change == 'old_fingerprint':
        selection = selection.model_copy(update={'expected_history_fingerprint': '0'*64})
    elif change == 'foreign_line':
        selection = selection.model_copy(update={'inbound_line_id': uuid4()})
    elif change == 'balance':
        db.get(StockBalance, issue.original_target_account_id).quantity = Decimal(999)
    elif change == 'custody':
        source = db.get(StockAccount, issue.original_target_account_id)
        db.get(StockLocation, source.location_id).custodian_person_id = actor.person_id
    else:
        grant.effect = 'deny'
    db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError):
        subject.inspect_source(db, actor=actor, selection=selection)
    assert snapshot(db) == before and not db.new and not db.dirty


@pytest.mark.parametrize('stock', ['serial'], indirect=True)
def test_sn_projection_cannot_be_substituted_with_another_inbound(db, stock, prepared):
    actor, selection, issue, _ = prepared
    position = db.get(SerialCurrentPosition, issue.affected_serial_ids[0])
    position.last_movement_id = db.scalar(select(InventoryMovement.id).where(
        InventoryMovement.id != position.last_movement_id).order_by(InventoryMovement.id).limit(1))
    db.commit()
    with pytest.raises(InventoryReadError):
        subject.inspect_source(db, actor=actor, selection=selection)


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_permission_revoked_between_reads_never_returns_source_proof(db, stock, prepared, monkeypatch):
    actor, selection, _, grant = prepared
    original = subject._current
    calls = 0

    def revoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 1:
            grant.effect = 'deny'
            db.flush()
        return result

    monkeypatch.setattr(subject, '_current', revoke)
    with pytest.raises(InventoryReadError):
        subject.inspect_source(db, actor=actor, selection=selection)


