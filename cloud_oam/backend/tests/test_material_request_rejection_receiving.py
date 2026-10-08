"""Warehouse history remains readable without impersonating the old requester.

Composition tests use synthetic posting from the existing original chain;
PG16 and warehouse acceptance/posting remain separate gates.
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from app.formal_access import load_formal_principal
from app.foundation_models import AuditEvent, Permission, Role, RolePermission
from app.inventory_models import CustodyAssignment, OutboundPosting, Receipt, ReceiptLine, StockAccount, StockLocation
from app.models import User
from app.material_request_rejection_return_schema import returns
from app.material_request_rejection_progress_schema import progress
from app.formal_services import material_request_rejection_receiving as receiving
from app.formal_services import material_request_my_receipt as receipt_service
from app.formal_services.material_request_query import MaterialRequestReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_material_request_rejection_progress import (
    world as progress_world, registration_world, receipt_world, receiving_world, outbound_world,
    create as advance, payload, inventory_unchanged,
)
from test_material_request_my_inbound import facts

pytest_plugins = ('test_material_request_picking',)


@pytest.fixture
def world(progress_world):
    db, sender, request, original, _ = progress_world
    departed = advance(progress_world, payload(progress_world, 'depart'))
    handover = advance(progress_world, payload(progress_world, 'handover', departed), key='warehouse-receiving-handover-0001')
    parent = db.execute(select(returns).where(returns.c.id == original.return_id)).mappings().one()
    source = db.get(StockAccount, parent['return_source_account_id'])
    location = db.get(StockLocation, source.location_id)
    outbound = db.get(OutboundPosting, parent['outbound_posting_id'])
    actor = load_formal_principal(db, outbound.actor_user_id)
    location.custodian_person_id = actor.person_id
    db.add(CustodyAssignment(id=uuid4(), location_id=location.id, custodian_person_id=actor.person_id,
        valid_from=original.registered_at - timedelta(days=1)))
    role = db.scalar(select(Role.id).where(Role.code == 'admin'))
    perm = db.scalar(select(Permission).where(Permission.resource == 'stock_operation', Permission.action == 'read', Permission.field_code == ''))
    if perm is None:
        perm = Permission(id=uuid4(), resource='stock_operation', action='read', field_code='')
        db.add(perm); db.flush()
    link = db.scalar(select(RolePermission).where(RolePermission.role_id == role, RolePermission.permission_id == perm.id))
    if link is None:
        db.add(RolePermission(role_id=role, permission_id=perm.id, effect='allow'))
    else:
        link.effect = 'allow'
    db.flush()
    return db, load_formal_principal(db, actor.user_id), sender, request, original, location, handover, parent


def read(world, actor=None):
    db, warehouse, _, _, original, *_ = world
    return receiving.rejection_return_receiving_detail(db, actor=actor or warehouse, return_id=original.return_id)


def test_receiving_exact_source_warehouse_is_select_only(world):
    db, actor, _, request, original, location, handover, _ = world
    before = facts(db); statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        result = read(world)
        assert read(world) == result
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert result.target_location_id == location.id and result.receiver_person_id == actor.person_id
    assert result.handover_id == handover.event_id and result.registration_request_hash == original.request_hash
    assert result.quantity == original.quantity and result.status == 'awaiting_warehouse_acceptance'
    assert tuple(sn.serial_id for sn in result.serials) == original.serial_ids
    assert result.request_version == request.version and result.original_exception == 'rejected'
    assert facts(db) == before
    assert all(sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper() for sql in statements)


def test_disabled_sender_and_ended_personal_custody_do_not_block_warehouse(world, monkeypatch):
    db, _, sender, request, original, *_ = world
    expected = read(world)
    db.get(User, sender.user_id).account_status = 'disabled'
    custody = db.scalar(select(CustodyAssignment).where(CustodyAssignment.custodian_person_id == sender.person_id))
    custody.valid_to = original.registered_at + timedelta(seconds=1)
    personal = db.get(StockLocation, custody.location_id)
    personal.status = 'inactive'
    db.flush()
    original_loader = receiving.posting.load_formal_principal
    def guard(db, user_id, *args, **kwargs):
        assert user_id != sender.user_id, 'warehouse must never load sender login'
        return original_loader(db, user_id, *args, **kwargs)
    monkeypatch.setattr(receiving.posting, 'load_formal_principal', guard)
    assert read(world) == expected
    with pytest.raises(MaterialRequestReadError):
        receipt_service.my_receipt_command_status(db, actor=sender, request_id=request.id,
            idempotency_key='original-irrelevant-key-0001', secret=b'x' * 32)


@pytest.mark.parametrize('kind', ['sender', 'custodian', 'custody', 'inactive', 'denied', 'duplicate_custody'])
def test_current_warehouse_authority_is_required(world, kind):
    db, actor, sender, _, original, location, *_ = world
    if kind == 'sender':
        with pytest.raises(MaterialRequestReadError): read(world, sender)
        return
    if kind == 'custodian': location.custodian_person_id = sender.person_id
    if kind == 'custody':
        db.scalar(select(CustodyAssignment).where(CustodyAssignment.location_id == location.id)).valid_to = original.registered_at
    if kind == 'inactive': db.get(User, actor.user_id).account_status = 'disabled'
    if kind == 'denied':
        permission = db.scalar(select(Permission.id).where(Permission.resource == 'stock_operation', Permission.action == 'read'))
        db.query(RolePermission).filter(RolePermission.permission_id == permission).update({'effect': 'deny'})
    if kind == 'duplicate_custody':
        db.add(CustodyAssignment(id=uuid4(), location_id=location.id, custodian_person_id=sender.person_id,
            valid_from=original.registered_at - timedelta(hours=12),
            valid_to=original.registered_at + timedelta(days=1)))
    db.flush(); before = facts(db)
    with pytest.raises((MaterialRequestReadError, InventoryPostingError)): read(world)
    assert facts(db) == before


@pytest.mark.parametrize('kind', ['registration', 'handover', 'receipt', 'receipt_audit', 'old_custody', 'requester'])
def test_corrupt_origin_never_becomes_a_receivable_return(world, kind):
    db, _, _, request, original, _, handover, parent = world
    if kind == 'registration': db.execute(returns.update().where(returns.c.id == original.return_id).values(evidence_sha256='0' * 64))
    if kind == 'handover': db.execute(progress.update().where(progress.c.id == handover.event_id).values(request_hash='0' * 64))
    if kind == 'receipt': db.get(ReceiptLine, parent['receipt_line_id']).rejected_qty += 1
    if kind == 'receipt_audit':
        audit = db.scalar(select(AuditEvent).where(AuditEvent.aggregate_type == 'receipt', AuditEvent.action == 'my_receipt_registered'))
        audit.event_hash = '0' * 64
    if kind == 'old_custody':
        receipt = db.get(Receipt, parent['receipt_id'])
        db.scalar(select(CustodyAssignment).where(CustodyAssignment.custodian_person_id == receipt.receiver_person_id)).valid_to = receipt.created_at - timedelta(days=1)
    if kind == 'requester': request.requester_user_id = world[1].user_id
    db.flush()
    with pytest.raises(MaterialRequestReadError): read(world)


def test_not_handed_over_cannot_be_received(world):
    db, _, _, _, _, _, handover, _ = world
    # Corrupting a synthetic fixture must fail rather than presenting it as accepted.
    db.execute(progress.delete().where(progress.c.id == handover.event_id))
    db.flush()
    with pytest.raises(MaterialRequestReadError, match='承运交接'): read(world)
