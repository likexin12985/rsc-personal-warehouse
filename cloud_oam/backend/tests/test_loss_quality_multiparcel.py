"""Independent parcel budgets and original recovery after later damaged inbounds."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.inventory_models import StockAccount, StockBalance, SerialCurrentPosition
from app.stock_operation_models import StockOperationReturnInbound
from app.formal_services import stock_return_receiving as receiving
from app.formal_services import stock_return_inbound_commands as commands
from app.formal_services import stock_return_inbound_facts as facts
from app.formal_services import stock_return_inbound_recovery as recovery
from app.formal_services.inventory_query import InventoryReadError
from test_loss_return_quality_extended import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel,
)
from test_loss_return_damaged_inbound import (
    regional_opening, post, lines, receipt_evidence, abnormal,
)
from test_stock_loss_return_receipt import acceptance as original_acceptance
from test_stock_loss_return_shipment import execute as ship
from test_stock_return_inbound import snapshot
from test_stock_return_receipt import submit
from test_work_order_removed_registration import inventory


@pytest.fixture
def acceptance(db, stock, parcel):
    whole = parcel.request
    quantities = (Decimal(1), Decimal(1)) if stock.tracked else (Decimal('.375'), Decimal('.625'))
    proofs = whole.lines[0].serial_ids
    first = whole.model_copy(update={'lines': (whole.lines[0].model_copy(update={
        'quantity': quantities[0], 'serial_ids': proofs[:1] if stock.tracked else (),
    }),)})
    parcel.request = first
    result = original_acceptance.__wrapped__(db, stock, parcel)
    result.request = result.request.model_copy(update={'lines': (
        result.request.lines[0].model_copy(update={'accepted_qty': quantities[0]}),)})
    second = whole.model_copy(update={
        'tracking_no': 'SECOND-' + uuid4().hex,
        'lines': (whole.lines[0].model_copy(update={
            'quantity': quantities[1], 'serial_ids': proofs[1:] if stock.tracked else (),
        }),),
    })
    # Restore the sender's current authorization only for this independent handover.
    stock.world.current_principal = parcel.actor
    before = inventory(db)
    shipped, _ = ship(db, parcel, second)
    assert inventory(db) == before
    stock.world.current_principal = result.actor
    package = receiving.my_return_receiving_detail(db, actor=result.actor, shipment_id=shipped.shipment_id).package
    data = result.request.model_dump()
    data.update({
        'received_at': datetime.now(timezone.utc),
        'lines': ({**data['lines'][0],
            'shipment_line_id': package.lines[0].shipment_line_id,
            'accepted_qty': quantities[1],
            'accepted_serial_verifications': tuple(dict(serial_id=sn.id, serial_no=sn.serial_no,
                sku_code=stock.world.material.sku_code, qr_code=sn.qr_code)
                for sn in stock.serials if sn.id in set(proofs[1:])),
        },),
    })
    request = type(result.request).model_validate(data)
    result.other = SimpleNamespace(actor=result.actor, package=package, request=request)
    return result


@pytest.mark.parametrize('reverse', [False, True])
def test_parcel_local_damage_and_recovery_survive_other_parcel_posting(db, stock, acceptance, monkeypatch, reverse):
    original_execute = commands.execute_return_inbound
    original_commands = {}
    def capture(db, **args):
        original_commands[args['receipt_id']] = dict(args)
        return original_execute(db, **args)
    monkeypatch.setattr(commands, 'execute_return_inbound', capture)
    contexts = [acceptance, acceptance.other]
    for index, context in enumerate(contexts):
        if stock.tracked and index == 0:
            continue
        file, _ = receipt_evidence(db, stock, context, monkeypatch)
        amount = Decimal(1) if stock.tracked else (Decimal('.125'), Decimal('.250'))[index]
        data = context.request.model_dump()
        data['lines'][0].update(damaged_qty=amount,
            damaged_serial_ids=tuple(proof.serial_id for proof in context.request.lines[0].accepted_serial_verifications),
            exceptions=(dict(exception_type='damaged', description='独立包裹破损验收凭证', evidence_file_id=file.id),))
        context.request = type(context.request).model_validate(data)
    ordered = list(reversed(contexts)) if reverse else contexts
    results = []
    targets = {}
    for context in ordered:
        preview, result = post(db, context)
        results.append(result)
        for part in lines(db, result):
            targets[part.condition_code] = part.target_account_id
            assert db.get(StockAccount, part.target_account_id).condition_code == part.condition_code
        # A fully accepted parcel cannot spend another parcel's remaining budget.
        before = snapshot(db)
        with pytest.raises(InventoryReadError):
            submit(db, context)
        assert snapshot(db) == before
    assert len({item['inbound_id'] for item in results}) == 2
    assert db.get(StockBalance, targets['new']).quantity == (Decimal(1) if stock.tracked else Decimal('.625'))
    assert db.get(StockBalance, targets['damaged']).quantity == (Decimal(1) if stock.tracked else Decimal('.375'))
    for index, context in enumerate(contexts):
        for proof in context.request.lines[0].accepted_serial_verifications:
            expected = targets['new' if index == 0 else 'damaged']
            assert db.get(SerialCurrentPosition, proof.serial_id).stock_account_id == expected
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    rows = []
    for result in results:
        row = db.get(StockOperationReturnInbound, result['inbound_id'])
        assert facts._proof(db, row) == result
        assert recovery.lookup_return_inbound_request(db, actor=acceptance.actor,
            receipt_id=row.receipt_id, request_id=row.request_id) == result
        rows.append(row)
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    for row, result in zip(rows, results):
        assert original_execute(db, **original_commands[row.receipt_id]) == dict(result, replayed=True)
    db.commit()
    assert snapshot(db) == before
