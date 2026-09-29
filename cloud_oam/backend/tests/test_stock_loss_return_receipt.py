"""Reported-loss acceptance, independent inbound and exact request recovery."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.formal_services import stock_return_receiving as receiving
from app.formal_services import stock_return_receipt_recovery as recovery
from app.formal_services import stock_return_inbound_commands as inbound
from app.formal_services import stock_return_inbound_recovery as inbound_recovery
from app.stock_return_receipt_schemas import StockReturnReceiptPreviewIn
from test_stock_loss_return_shipment import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, execute as ship, snapshot,
)
from test_stock_return_receiving import receiver
from test_stock_return_receipt import submit, execute
from test_work_order_removed_registration import inventory


@pytest.fixture
def acceptance(db, stock, parcel):
    shipment, _ = ship(db, parcel)
    actor = receiver(stock)
    actor = replace(actor, entitlements=actor.entitlements + (replace(actor.entitlements[0], action='receive_return'),))
    stock.world.current_principal = actor
    package = receiving.my_return_receiving_detail(db, actor=actor, shipment_id=shipment.shipment_id).package
    request = StockReturnReceiptPreviewIn(operator_person_id=actor.person_id, received_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        reason='核验报损退回实物，验收独立于入库', lines=[dict(shipment_line_id=package.lines[0].shipment_line_id,
        accepted_qty='1.000', rejected_qty='0.000', damaged_qty='0.000', shortage_qty='0.000', accepted_serial_verifications=[dict(serial_id=sn.id, serial_no=sn.serial_no,
            sku_code=stock.world.material.sku_code, qr_code=sn.qr_code) for sn in stock.serials if sn.id in {item.serial_id for item in package.lines[0].serials}])])
    return SimpleNamespace(actor=actor, package=package, request=request)


def test_loss_receipt_is_stock_neutral_and_exactly_recoverable(db, acceptance):
    before = inventory(db)
    command = submit(db, acceptance)
    result = execute(db, acceptance, command); db.commit()
    assert result.origin == acceptance.package.origin
    assert 'work_order_id' not in result.model_dump(mode='json')
    assert result.lines[0].condition_code == 'new'
    assert inventory(db) == before
    assert execute(db, acceptance, command) == result
    assert recovery.lookup_receipt_request(db, actor=acceptance.actor, shipment_id=result.shipment_id, request_id=command.request_id) == result


def test_loss_receipt_seal_prevents_late_write(db, acceptance):
    command = submit(db, acceptance)
    from app.formal_services.stock_return_receipt_plan import intent
    from app.formal_services.work_order_return_sources import _hash
    from app.formal_services.inventory_query import InventoryReadError
    result = recovery.seal_receipt_request(db, actor=acceptance.actor, shipment_id=acceptance.package.shipment_id,
        request_id=command.request_id, request_hash=_hash(intent(acceptance.package.shipment_id, command)))
    db.commit()
    assert result.seal.origin == acceptance.package.origin
    assert 'work_order_id' not in result.model_dump(mode='json')['seal']
    assert recovery.lookup_receipt_request(db, actor=acceptance.actor, shipment_id=acceptance.package.shipment_id, request_id=command.request_id) == result
    with pytest.raises(InventoryReadError): execute(db, acceptance, command)


def test_loss_receipt_inbound_posts_once_and_recovers(db, stock, acceptance, parcel):
    from app.inventory_models import StockAccount
    from test_inventory_posting import establish_account_for_posting, NOW
    from datetime import timedelta
    source = db.get(StockAccount, parcel.line.transit_stock_account_id)
    target = StockAccount(id=uuid4(), owner_org_id=source.owner_org_id,
        custodian_person_id=acceptance.actor.person_id, location_id=acceptance.package.target_location_id,
        material_id=source.material_id, condition_code=source.condition_code, availability_bucket='available',
        lot_id=source.lot_id, created_at=NOW-timedelta(days=1), updated_at=NOW-timedelta(days=1))
    db.add(target); db.flush()
    establish_account_for_posting(db, stock.world, target)
    stock.world.current_principal = acceptance.actor
    db.commit()
    result = execute(db, acceptance, submit(db, acceptance)); db.commit()
    preview = inbound.preview_return_inbound(db, actor=acceptance.actor, receipt_id=result.receipt_id)
    command = dict(actor=acceptance.actor, receipt_id=result.receipt_id, expected_plan_hash=preview['plan_hash'],
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    posted = inbound.execute_return_inbound(db, **command); db.commit()
    after = inventory(db)
    replay = inbound.execute_return_inbound(db, **command)
    assert replay['replayed'] is True
    assert replay['inbound_id'] == posted['inbound_id']
    assert inventory(db) == after


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_loss_partial_receipts_keep_independent_budgets(db, acceptance):
    from decimal import Decimal
    first = acceptance.request.model_copy(update={'lines': (acceptance.request.lines[0].model_copy(update={'accepted_qty': Decimal('.375')}),)})
    a = execute(db, acceptance, submit(db, acceptance, first)); db.commit()
    rest = first.model_copy(update={'lines': (first.lines[0].model_copy(update={'accepted_qty': Decimal('.625')}),)})
    b = execute(db, acceptance, submit(db, acceptance, rest)); db.commit()
    assert a.receipt_id != b.receipt_id
    assert b.lines[0].previously_accepted_qty == '0.375'
    from app.formal_services.inventory_query import InventoryReadError
    with pytest.raises(InventoryReadError, match='尚未确认'): submit(db, acceptance)


def test_loss_shortage_observation_does_not_consume_acceptance_budget(db, stock, acceptance, monkeypatch):
    from test_stock_return_receipt import evidence as receipt_evidence, abnormal
    file, _ = receipt_evidence(db, stock, acceptance, monkeypatch)
    value = abnormal(acceptance, file.id, 'shortage')
    before = inventory(db)
    result = execute(db, acceptance, submit(db, acceptance, value)); db.commit()
    assert result.status == 'exception' and result.lines[0].accepted_qty == '0.000'
    from app.formal_services.inventory_query import InventoryReadError
    with pytest.raises(InventoryReadError):inbound.preview_return_inbound(db, actor=acceptance.actor, receipt_id=result.receipt_id)
    accepted = execute(db, acceptance, submit(db, acceptance)); db.commit()
    assert accepted.lines[0].previously_accepted_qty == '0.000' and accepted.lines[0].accepted_qty == '1.000'
    assert inventory(db) == before


def test_loss_receipt_http_contract_matches_actual_mini_parser(db, stock, acceptance):
    import json, subprocess
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import formal_stock_return_receipts, formal_stock_return_receiving
    app = FastAPI()
    app.dependency_overrides[get_db] = lambda: db
    for module in (formal_stock_return_receiving, formal_stock_return_receipts):
        app.include_router(module.router, prefix='/api')
        for route in module.router.routes:
            for dependency in route.dependant.dependencies:
                if dependency.name == 'principal':app.dependency_overrides[dependency.call] = lambda: stock.world.current_principal
    prefix = '/api/v1/stock-returns/my-receiving/' + str(acceptance.package.shipment_id) + '/receipts'
    with TestClient(app) as client:
        history = client.get(prefix); assert history.status_code == 200, history.text
        preview = client.post(prefix + '/preview', json=acceptance.request.model_dump(mode='json'))
        assert preview.status_code == 200, preview.text
        command = submit(db, acceptance)
        posted = client.post(prefix, json=command.model_dump(mode='json'), headers={'X-Request-ID':command.request_id,'Idempotency-Key':command.idempotency_key})
        assert posted.status_code == 200, posted.text
        lookup = client.get(prefix + '/by-request/' + command.request_id)
        assert lookup.status_code == 200 and lookup.json() == posted.json()
        for response in (history, preview, posted, lookup):
            assert 'no-store' in response.headers['cache-control']
            assert 'work_order_id' not in response.text and 'qr_code' not in response.text
    marker = dict(origin=preview.json()['origin'], shipment_id=str(acceptance.package.shipment_id),
        person_id=str(acceptance.actor.person_id), operation_id=str(acceptance.package.operation_id),
        trace_request_id=command.request_id, request_hash=posted.json()['request_hash'], plan_hash=command.expected_plan_hash)
    script = """const fs=require('node:fs');const x=JSON.parse(fs.readFileSync(0,'utf8'));
const receiving=require('./utils/stock-return-receiving-contract');const receipt=require('./utils/stock-return-receipt-contract');
receiving.validateHistory(x.history,x.expected);receipt.validatePreview(x.preview,x.input,x.history,x.expected.shipmentId);
receipt.validateLookup(x.result,x.marker);"""
    import shutil
    node=shutil.which('node') or 'node'
    result=subprocess.run([node,'-e',script],text=True,capture_output=True,timeout=30,
        cwd=Path(__file__).resolve().parents[2]/'miniprogram',input=json.dumps(dict(
            history=history.json(),preview=preview.json(),input=acceptance.request.model_dump(mode='json'),result=posted.json(),marker=marker,
            expected=dict(personId=str(acceptance.actor.person_id),shipmentId=str(acceptance.package.shipment_id),authorizationVersion=acceptance.actor.authorization_version))))
    assert result.returncode == 0, result.stderr
