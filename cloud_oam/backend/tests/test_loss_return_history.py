"""Real service facts and query-only planning; native PG coverage is separate."""
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission, OutboxEvent
from app.models import User
from app import stock_operation_models as models
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_history
from test_stock_loss_return_receipt import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, acceptance, submit, execute, ship, inbound,
)
from test_stock_loss_return_outbound import submit as depart, snapshot


def headquarters_reader(db, stock, approved):
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
        Permission.action == 'read', Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='stock_operation', action='read', field_code='', description='Synthetic read only')
        db.add(permission); db.flush()
    grant = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
        RolePermission.permission_id == permission.id))
    if grant is None:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    actor = load_formal_principal(db, approved.actor.user_id)
    stock.world.current_principal = actor
    return actor


def read_only(db, stock, approved, derived):
    actor = headquarters_reader(db, stock, approved)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        result = return_history.read(db, actor=actor, root_disposition_id=UUID(derived.result['disposition_id']))
        assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
        assert result.write_authorization_provided is False
        assert result.operation_id == derived.order.id
        assert len(result.lines) == 1
        assert sum((s.quantity for s in result.lines[0].shares), Decimal(0)) == Decimal(1)
        return result
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


def shares(result):
    return {share.stage: share.quantity for share in result.lines[0].shares}


def test_pending_return_is_not_outbound(db, stock, approved, derived):
    result = read_only(db, stock, approved, derived)
    assert shares(result)['not_outbound'] == 1
    assert all(not ids for _, ids in result.coordinates)


def test_departure_is_not_shipment_or_receipt(db, stock, approved, ready):
    result, _ = depart(db, ready)
    proof = read_only(db, stock, approved, ready.derived)
    assert shares(proof)['outbound_not_shipped'] == 1
    assert dict(proof.coordinates)['outbounds'] == (result.outbound_id,)
    assert dict(proof.coordinates)['shipments'] == ()


def test_shipment_is_not_receipt_or_inbound(db, stock, approved, derived, parcel):
    result, _ = ship(db, parcel)
    proof = read_only(db, stock, approved, derived)
    assert shares(proof)['shipped_unconfirmed'] == 1
    assert dict(proof.coordinates)['shipments'] == (result.shipment_id,)
    assert dict(proof.coordinates)['inbounds'] == ()


def test_acceptance_is_not_inbound(db, stock, approved, derived, acceptance):
    result = execute(db, acceptance, submit(db, acceptance)); db.commit()
    proof = read_only(db, stock, approved, derived)
    assert shares(proof)['accepted_not_inbound'] == 1
    assert shares(proof)['posted_inbound'] == 0
    assert dict(proof.coordinates)['receipts'] == (result.receipt_id,)
    expected = tuple(sorted((sn.serial_id for sn in acceptance.package.lines[0].serials), key=str))
    assert next(s for s in proof.lines[0].shares if s.stage == 'accepted_not_inbound').serial_ids == expected


def post_inbound(db, stock, acceptance, parcel):
    from app.inventory_models import StockAccount
    from test_inventory_posting import establish_account_for_posting, NOW
    from datetime import timedelta
    source = db.get(StockAccount, parcel.line.transit_stock_account_id)
    target = StockAccount(id=uuid4(), owner_org_id=source.owner_org_id,
        custodian_person_id=acceptance.actor.person_id, location_id=acceptance.package.target_location_id,
        material_id=source.material_id, condition_code=source.condition_code, availability_bucket='available',
        lot_id=source.lot_id, created_at=NOW-timedelta(days=1), updated_at=NOW-timedelta(days=1))
    db.add(target); db.flush(); establish_account_for_posting(db, stock.world, target)
    stock.world.current_principal = acceptance.actor; db.commit()
    accepted = execute(db, acceptance, submit(db, acceptance)); db.commit()
    preview = inbound.preview_return_inbound(db, actor=acceptance.actor, receipt_id=accepted.receipt_id)
    result = inbound.execute_return_inbound(db, actor=acceptance.actor, receipt_id=accepted.receipt_id,
        expected_plan_hash=preview['plan_hash'], request_id=uuid4().hex, idempotency_key=uuid4().hex)
    db.commit()
    return result


def test_inbound_has_its_own_proven_fact(db, stock, approved, derived, acceptance, parcel):
    result = post_inbound(db, stock, acceptance, parcel)
    proof = read_only(db, stock, approved, derived)
    assert shares(proof)['posted_inbound'] == 1
    assert shares(proof)['accepted_not_inbound'] == 0
    assert dict(proof.coordinates)['inbounds'] == (result['inbound_id'],)


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_partial_acceptance_leaves_unconfirmed_share(db, stock, approved, derived, acceptance):
    partial = acceptance.request.model_copy(update={'lines': (acceptance.request.lines[0].model_copy(
        update={'accepted_qty': Decimal('.375')}),)})
    execute(db, acceptance, submit(db, acceptance, partial)); db.commit()
    proof = read_only(db, stock, approved, derived)
    assert shares(proof)['accepted_not_inbound'] == Decimal('.375')
    assert shares(proof)['shipped_unconfirmed'] == Decimal('.625')


def test_shortage_then_arrival_does_not_double_consume(db, stock, approved, derived, acceptance, monkeypatch):
    from test_stock_return_receipt import evidence as receipt_evidence, abnormal
    file, _ = receipt_evidence(db, stock, acceptance, monkeypatch)
    shortage = abnormal(acceptance, file.id, 'shortage')
    execute(db, acceptance, submit(db, acceptance, shortage)); db.commit()
    execute(db, acceptance, submit(db, acceptance)); db.commit()
    proof = read_only(db, stock, approved, derived)
    assert shares(proof)['accepted_not_inbound'] == 1
    assert shares(proof)['shipped_unconfirmed'] == 0
    assert len(proof.lines[0].shortage_observations) == 1
    assert proof.lines[0].shortage_observations[0][1] == 1


@pytest.mark.parametrize('damage', ['outbound_quantity', 'shipment_plan', 'receipt_quantity', 'missing_outbox', 'inbound_plan'])
def test_corrupt_stage_cannot_become_compensation_basis(db, stock, approved, derived, acceptance, parcel, damage):
    posted = post_inbound(db, stock, acceptance, parcel)
    headquarters_reader(db, stock, approved)
    if damage == 'outbound_quantity':
        db.scalars(select(models.StockOperationOutboundLine)).one().quantity += Decimal(1)
    elif damage == 'shipment_plan':
        row = db.scalars(select(models.StockOperationShipment)).one(); row.plan_hash = 'f'*64
    elif damage == 'receipt_quantity':
        db.scalars(select(models.StockOperationReceiptLine)).one().accepted_qty += Decimal(1)
    elif damage == 'inbound_plan':
        db.get(models.StockOperationReturnInbound, posted['inbound_id']).plan_hash = 'f'*64
    else:
        db.delete(db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_type == 'stock_operation_return_inbound')).one())
    db.flush()
    with pytest.raises(InventoryReadError):
        return_history.read(db, actor=stock.world.current_principal,
            root_disposition_id=UUID(derived.result['disposition_id']))


def test_sender_without_headquarters_read_cannot_discover_root(db, derived):
    for identifier in (UUID(derived.result['disposition_id']), uuid4()):
        with pytest.raises(InventoryReadError) as error:
            return_history.read(db, actor=derived.actor, root_disposition_id=identifier)
        assert error.value.status_code == 403


def test_reader_revocation_during_proof_blocks_result(db, stock, approved, derived, monkeypatch):
    actor = headquarters_reader(db, stock, approved)
    original = return_history._prove
    def revoke(*args):
        original(*args)
        stock.world.current_principal = replace(actor, entitlements=tuple(e for e in actor.entitlements
            if not (e.resource == 'stock_operation' and e.action == 'read')))
    monkeypatch.setattr(return_history, '_prove', revoke)
    with pytest.raises(InventoryReadError):
        return_history.read(db, actor=actor, root_disposition_id=UUID(derived.result['disposition_id']))


def test_inactive_sender_does_not_erase_historical_fulfillment(db, stock, approved, derived, acceptance):
    execute(db, acceptance, submit(db, acceptance)); db.commit()
    db.get(User, derived.actor.user_id).is_active = False; db.commit()
    proof = read_only(db, stock, approved, derived)
    assert shares(proof)['accepted_not_inbound'] == 1
