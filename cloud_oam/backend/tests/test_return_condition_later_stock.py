"""Actual ledger movements cannot be hidden by restoring the original balance.

The later moves use the generic posting service with synthetic permissions;
they exercise attribution, not a finished condition-correction business route.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.inventory_models import StockAccount, StockBalance
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_loss_corrections import return_condition_source as subject
from test_return_condition_source import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, acceptance, regional_opening, prepared,
)
from test_stock_return_inbound import snapshot


@pytest.mark.parametrize('stock', ['quantity', 'serial'], indirect=True)
@pytest.mark.parametrize('roundtrip', [False, True])
def test_reserve_and_release_cannot_prove_original_damaged_stock_is_untouched(db, stock, prepared, roundtrip):
    actor, selection, issue, _ = prepared
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = db.scalar(select(Permission).where(Permission.resource == 'inventory_transaction',
        Permission.action == 'post', Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='inventory_transaction', action='post', field_code='',
            description='Synthetic later ledger event')
        db.add(permission); db.flush()
    if db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id)) is None:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    source = db.get(StockAccount, issue.original_target_account_id)
    target = StockAccount(id=uuid4(), **{key: getattr(source, key) for key in
        ('owner_org_id', 'location_id', 'custodian_person_id', 'material_id', 'condition_code', 'lot_id')},
        availability_bucket='reserved')
    db.add(target); db.commit()
    actor = load_formal_principal(db, actor.user_id)
    stock.world.current_principal = actor
    amount = Decimal(1) if stock.tracked else Decimal('.125')
    initial = db.get(StockBalance, source.id).quantity
    for kind, from_id, to_id in ([('reserve', source.id, target.id),
            ('release', target.id, source.id)] if roundtrip else [('reserve', source.id, target.id)]):
        token = uuid4().hex
        command = posting.InventoryPostingCommand(transaction_no='SYNTHETIC-LATER-'+token,
            movement_type=kind, source_document_type='test_case', source_document_id=token,
            posting_key='synthetic-later:'+token, effective_at=datetime.now(timezone.utc),
            movements=(posting.InventoryMovementCommand(from_id, to_id, amount, issue.affected_serial_ids),))
        posting.post_inventory_transaction(db, actor=actor, command=command,
            idempotency_key=token, request_id=token)
        db.commit()
    if roundtrip:
        assert db.get(StockBalance, source.id).quantity == initial
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        value = subject.inspect_source(db, actor=actor, selection=selection).document
        assert value['source_status'] == 'later_activity_requires_reconciliation'
        assert value['later_outgoing_count'] == 1
        assert Decimal(value['later_outgoing_quantity']) == amount
        assert value['historical_damaged_quantity'] == ('1.000' if stock.tracked else '0.375')
        assert value['physical_verification_required'] and not value['posting_allowed']
        assert all(not row['retained_at_original_inbound'] for row in value['serials'])
        assert snapshot(db) == before and not db.new and not db.dirty
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
