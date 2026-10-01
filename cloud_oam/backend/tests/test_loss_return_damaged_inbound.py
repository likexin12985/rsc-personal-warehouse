"""Actual loss-return acceptance, split posting, and exact request recovery."""
from decimal import Decimal
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.inventory_models import StockAccount, StockBalance, SerialCurrentPosition
from app.stock_operation_models import StockOperationReturnInboundLine, StockOperationReturnInbound
from app.formal_services import stock_return_inbound_commands as commands
from app.formal_services import stock_return_inbound_facts as facts
from app.formal_services import stock_return_inbound_recovery as recovery
from app.formal_services import stock_return_inbound_plan as planning
from app.formal_services.stock_return_inbound_quality import AcceptedPart
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.work_order_return_sources import _hash
from app.stock_operation_models import StockOperationReceiptSerial
from test_stock_loss_return_receipt import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, acceptance, submit, execute,
)
from test_stock_return_receipt import evidence as receipt_evidence, abnormal
from test_stock_return_inbound import snapshot
from test_inventory_posting import establish_account_for_posting, NOW


@pytest.fixture(autouse=True)
def regional_opening(db, stock, acceptance, parcel):
    # Establish the receiving location through its real reviewed opening.
    # The damaged dimension itself must be created atomically by the inbound.
    source = db.get(StockAccount, parcel.line.transit_stock_account_id)
    target = StockAccount(id=uuid4(), owner_org_id=source.owner_org_id,
        custodian_person_id=acceptance.actor.person_id, location_id=acceptance.package.target_location_id,
        material_id=source.material_id, condition_code=source.condition_code, availability_bucket='available',
        lot_id=source.lot_id, created_at=NOW-timedelta(days=1), updated_at=NOW-timedelta(days=1))
    db.add(target); db.flush()
    establish_account_for_posting(db, stock.world, target)
    stock.world.current_principal = acceptance.actor
    db.commit()


def post(db, acceptance, *, schema='2.0'):
    receipt = execute(db, acceptance, submit(db, acceptance)); db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    preview = commands.preview_return_inbound(db, actor=acceptance.actor, receipt_id=receipt.receipt_id)
    assert preview['schema_version'] == schema
    assert snapshot(db) == before and not db.new and not db.dirty
    db.execute(text('PRAGMA query_only=OFF'))
    args = dict(actor=acceptance.actor, receipt_id=receipt.receipt_id, expected_plan_hash=preview['plan_hash'],
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    result = commands.execute_return_inbound(db, **args); db.commit()
    after = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    recovered = recovery.lookup_return_inbound_request(db, actor=acceptance.actor,
        receipt_id=receipt.receipt_id, request_id=args['request_id'])
    assert recovered['inbound_id'] == result['inbound_id']
    assert facts._proof(db, db.get(StockOperationReturnInbound, result['inbound_id'])) == result
    assert snapshot(db) == after
    db.execute(text('PRAGMA query_only=OFF'))
    assert commands.execute_return_inbound(db, **args)['replayed'] is True
    assert snapshot(db) == after
    return preview, result


def lines(db, result):
    return tuple(db.scalars(select(StockOperationReturnInboundLine).where(
        StockOperationReturnInboundLine.inbound_id == result['inbound_id']).order_by(StockOperationReturnInboundLine.line_no)))


def test_whole_damaged_acceptance_enters_only_damaged_account(db, stock, acceptance, monkeypatch):
    file, _ = receipt_evidence(db, stock, acceptance, monkeypatch)
    acceptance.request = abnormal(acceptance, file.id, 'damaged')
    preview, result = post(db, acceptance)
    posted = lines(db, result)
    assert len(posted) == 1
    target = db.get(StockAccount, posted[0].target_account_id)
    assert target.condition_code == posted[0].condition_code == 'damaged'
    assert db.get(StockBalance, target.id).quantity == posted[0].accepted_qty == 1
    assert preview['lines'][0]['condition_code'] == 'damaged'
    assert db.get(StockBalance, posted[0].source_account_id).quantity == 0
    for identifier in acceptance.request.lines[0].damaged_serial_ids:
        assert db.get(SerialCurrentPosition, identifier).stock_account_id == target.id


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_mixed_acceptance_splits_same_receipt_and_preserves_total(db, stock, acceptance, monkeypatch):
    file, _ = receipt_evidence(db, stock, acceptance, monkeypatch)
    damaged = abnormal(acceptance, file.id, 'damaged')
    acceptance.request = damaged.model_copy(update={'lines': (
        damaged.lines[0].model_copy(update={'damaged_qty': Decimal('.375')}),)})
    preview, result = post(db, acceptance)
    posted = lines(db, result)
    assert len(posted) == 2 and len({row.receipt_line_id for row in posted}) == 1
    assert [(row.condition_code, row.accepted_qty) for row in posted] == [
        ('new', Decimal('.625')), ('damaged', Decimal('.375'))]
    assert sum(row.accepted_qty for row in posted) == 1
    assert db.get(StockBalance, posted[0].source_account_id).quantity == 0
    for row in posted:
        target = db.get(StockAccount, row.target_account_id)
        assert target.condition_code == row.condition_code
        assert db.get(StockBalance, row.target_account_id).quantity == row.accepted_qty
    assert [line['accepted_qty'] for line in preview['lines']] == ['0.625', '0.375']


def test_original_version_one_request_stays_readable_after_upgrade(db, stock, acceptance, monkeypatch):
    """Create the historical v1 contract, then restore v2 before recovering.

    This explicitly recreates the former faulty all-damaged classification.
    A migration must not silently rewrite that fact or its original digest.
    """
    file, _ = receipt_evidence(db, stock, acceptance, monkeypatch)
    acceptance.request = abnormal(acceptance, file.id, 'damaged')
    current = planning.plan_return_inbound

    def historical_parts(db, *, origin, source, at):
        ids = tuple(db.scalars(select(StockOperationReceiptSerial.serial_id).where(
            StockOperationReceiptSerial.line_id == origin.id,
            StockOperationReceiptSerial.result == 'accepted').order_by(StockOperationReceiptSerial.serial_id)))
        return (AcceptedPart(source.condition_code, origin.accepted_qty, ids),)

    def historical_plan(*args, **kwargs):
        plan = current(*args, **kwargs)
        plan['schema_version'] = '1.0'
        plan['plan_hash'] = _hash(planning.plan_document(plan))
        return plan

    with monkeypatch.context() as legacy:
        legacy.setattr(planning, 'receipt_parts', historical_parts)
        legacy.setattr(commands, 'plan_return_inbound', historical_plan)
        _, result = post(db, acceptance, schema='1.0')
    row = db.get(StockOperationReturnInbound, result['inbound_id'])
    before = snapshot(db)
    assert row.plan_jsonb['schema_version'] == '1.0'
    assert db.get(StockAccount, lines(db, result)[0].target_account_id).condition_code == 'new'
    db.execute(text('PRAGMA query_only=ON'))
    assert facts._proof(db, row) == result
    assert recovery.lookup_return_inbound_request(db, actor=acceptance.actor,
        receipt_id=row.receipt_id, request_id=row.request_id)['request_hash'] == row.request_hash
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
@pytest.mark.parametrize('damage', ['amount', 'condition', 'source', 'plan_version'])
def test_split_history_refuses_tampered_posting(db, stock, acceptance, monkeypatch, damage):
    file, _ = receipt_evidence(db, stock, acceptance, monkeypatch)
    damaged = abnormal(acceptance, file.id, 'damaged')
    acceptance.request = damaged.model_copy(update={'lines': (
        damaged.lines[0].model_copy(update={'damaged_qty': Decimal('.375')}),)})
    _, result = post(db, acceptance)
    row = db.get(StockOperationReturnInbound, result['inbound_id'])
    posted = lines(db, result)
    if damage == 'amount':
        posted[0].accepted_qty = Decimal('.750'); posted[1].accepted_qty = Decimal('.250')
    elif damage == 'condition':
        posted[1].condition_code = 'used'
    elif damage == 'source':
        posted[1].source_account_id = posted[0].target_account_id
    else:
        row.plan_jsonb = {**row.plan_jsonb, 'schema_version': '3.0'}
        row.plan_hash = _hash(row.plan_jsonb)
        row.request_hash = commands._request_hash(receipt_id=row.receipt_id, request_id=row.request_id, plan_hash=row.plan_hash)
    db.flush()
    with pytest.raises(InventoryReadError):
        facts._proof(db, row)
