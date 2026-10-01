"""Real service paths for mixed SN and independently posted partial acceptance.

Uses existing synthetic identity and reviewed opening fixtures. Native database
concurrency/guards are proved separately, never inferred from these cases.
"""
from decimal import Decimal
import pytest
from app.inventory_models import StockBalance, StockAccount, SerialCurrentPosition
from test_loss_return_damaged_inbound import (
    db, world, stock, evidence, regional, headquarters, approved, route, derived,
    regional_opening, post, lines, receipt_evidence, abnormal,
)
from test_stock_loss_sources import allowed as original_allowed, request as select_request
from test_stock_loss_return_outbound import ready as original_ready
from test_stock_loss_return_shipment import parcel as original_parcel
from test_stock_loss_return_receipt import acceptance as original_acceptance
import test_stock_loss_plan as loss_fixture


@pytest.fixture
def allowed(stock, monkeypatch):
    result = original_allowed.__wrapped__(stock)
    if stock.tracked:
        def select_two(value):
            proofs = tuple(dict(serial_id=sn.id, sku_code=value.world.material.sku_code,
                serial_no=sn.serial_no, qr_code=sn.qr_code) for sn in value.serials[3:5])
            return select_request(value,quantity='2',serial_verifications=proofs)
        monkeypatch.setattr(loss_fixture,'request',select_two)
    return result


@pytest.fixture
def ready(db,allowed,derived,route):
    result = original_ready.__wrapped__(db,allowed,derived,route)
    if allowed.tracked:
        result.request = result.request.model_copy(update={'lines': (
            result.request.lines[0].model_copy(update={'quantity':Decimal(2)}),)})
    return result


@pytest.fixture
def parcel(db,ready,allowed):
    result = original_parcel.__wrapped__(db,ready,allowed)
    if allowed.tracked:
        result.request = result.request.model_copy(update={'lines': (
            result.request.lines[0].model_copy(update={'quantity':Decimal(2)}),)})
    return result


@pytest.fixture
def acceptance(db,stock,parcel):
    result = original_acceptance.__wrapped__(db,stock,parcel)
    if stock.tracked:
        result.request = result.request.model_copy(update={'lines': (
            result.request.lines[0].model_copy(update={'accepted_qty':Decimal(2)}),)})
    return result


@pytest.mark.parametrize('stock',['serial'],indirect=True)
def test_mixed_sn_routes_only_damaged_serial_to_damaged_account(db,stock,acceptance,monkeypatch):
    file,_ = receipt_evidence(db,stock,acceptance,monkeypatch)
    value = abnormal(acceptance,file.id,'damaged')
    selected = acceptance.request.lines[0].accepted_serial_verifications
    assert len(selected)==2
    damaged_id = selected[0].serial_id
    acceptance.request = value.model_copy(update={'lines': (
        value.lines[0].model_copy(update={'damaged_qty':Decimal(1),'damaged_serial_ids':(damaged_id,)}),)})
    preview,result = post(db,acceptance)
    posted = lines(db,result)
    assert [(row.condition_code,row.accepted_qty) for row in posted]==[('new',Decimal(1)),('damaged',Decimal(1))]
    assert len({row.receipt_line_id for row in posted})==1
    assert db.get(StockBalance,posted[0].source_account_id).quantity==0
    assert {sn for row in preview['lines'] for sn in row['serial_ids']}=={str(p.serial_id) for p in selected}
    for row in posted:
        assert db.get(StockBalance,row.target_account_id).quantity==1
    for proof in selected:
        position=db.get(SerialCurrentPosition,proof.serial_id)
        target=db.get(StockAccount,position.stock_account_id)
        assert target.condition_code==('damaged' if proof.serial_id==damaged_id else 'new')


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_partial_acceptance_inbounds_preserve_separate_damage_budgets(db,stock,acceptance,monkeypatch):
    original_request = acceptance.request
    results=[]
    for accepted,damaged in [('0.375','0.125'),('0.625','0.250')]:
        acceptance.request = original_request
        file,_ = receipt_evidence(db,stock,acceptance,monkeypatch)
        original = abnormal(acceptance,file.id,'damaged')
        acceptance.request = original.model_copy(update={'lines': (
            original.lines[0].model_copy(update={'accepted_qty':Decimal(accepted),'damaged_qty':Decimal(damaged)}),)})
        preview,result=post(db,acceptance)
        assert [(row['condition_code'],Decimal(row['accepted_qty'])) for row in preview['lines']]==[
            ('new',Decimal(accepted)-Decimal(damaged)),('damaged',Decimal(damaged))]
        results.append(result)
    assert results[0]['inbound_id']!=results[1]['inbound_id']
    posted=lines(db,results[1])
    assert db.get(StockBalance,posted[0].source_account_id).quantity==0
    assert {row.condition_code:db.get(StockBalance,row.target_account_id).quantity for row in posted}=={
        'new':Decimal('.625'),'damaged':Decimal('.375')}
