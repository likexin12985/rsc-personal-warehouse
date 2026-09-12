"""Replacement preview must validate both halves without creating target stock."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.database import get_db
from app.inventory_models import StockAccount, InventoryLedgerHead, MaterialInventoryPolicy
from app.formal_services import inventory_posting as posting
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import work_order_material as material
from app.formal_services import work_order_replacements as replacements
from app.formal_services import work_order_replacement_preview as preview
from app.routers import formal_work_order_query as router
from app.work_order_material_schemas import WorkOrderRemovedScanIn
from test_work_order_material_options import db, world, stock


def inputs(stock):
    consumed = stock.line("1", stock.serials[1:2], identifier=stock.reserved.id)
    removed = stock.serials[:1]
    recovered = replacements.RecoveryLineInput(stock.reserved.id, stock.world.material.id, Decimal(1), "used",
        serial_ids=tuple(row.id for row in removed), serial_verifications=tuple(material.SerialVerificationInput(
            row.id, stock.world.material.sku_code, row.serial_no, row.qr_code) for row in removed))
    return dict(consume_lines=(consumed,), recover_lines=(recovered,),
        pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0], removed[0].id),) if removed else ())


def run(db, stock, **changes):
    return preview.preview_replacement(db, actor=stock.world.current_principal,
        work_order_id=stock.orders[0].id, **{**inputs(stock), **changes})


def scan(stock, **changes):
    return WorkOrderRemovedScanIn(**{**dict(operator_person_id=stock.actor.person_id,
        basis_stock_account_id=stock.reserved.id, sku_code=stock.world.material.sku_code, condition_before="used",
        serial_no=stock.serials[0].serial_no if stock.tracked else None,
        qr_code=stock.serials[0].qr_code if stock.tracked else None), **changes})


def resolve(db, stock, value=None):
    return preview.lookup_removed_part(db, actor=stock.world.current_principal,
        work_order_id=stock.orders[0].id, scan=value or scan(stock))


def test_preview_missing_old_account_checks_both_halves_in_read_only_transaction(db,stock):
    count=db.scalar(select(func.count()).select_from(StockAccount))
    db.execute(text("PRAGMA query_only=ON"))
    result=run(db,stock)
    assert result.consume_line_count==result.recover_line_count==1
    assert result.pair_count==(1 if stock.tracked else 0)
    assert result.request_hash==replacements._hash(replacements.replacement_request_payload(
        work_order_id=stock.orders[0].id,operator_person_id=stock.actor.person_id,**inputs(stock)))
    assert db.scalar(select(func.count()).select_from(StockAccount))==count
    assert not db.new and not db.dirty and not db.deleted
    assert "qr_code" not in result.model_dump_json()


@pytest.mark.parametrize("damage",["consume_excess","missing_recovery","wrong_basis","new_part","wrong_lot","wrong_target","duplicate_target"])
def test_any_invalid_consume_or_recovery_line_stops_entire_read_only_batch(db,stock,damage):
    args=inputs(stock);line=args["recover_lines"][0]
    if damage=="consume_excess":args["consume_lines"]=(replace(args["consume_lines"][0],quantity=Decimal(99),serial_ids=(),serial_verifications=()),)
    elif damage=="missing_recovery":args["recover_lines"]=()
    elif damage=="wrong_basis":args["recover_lines"]=(replace(line,basis_stock_account_id=stock.account.id),)
    elif damage=="new_part":args["recover_lines"]=(replace(line,condition_before="new"),)
    elif damage=="wrong_lot":args["recover_lines"]=(replace(line,lot_id=uuid4()),)
    elif damage=="wrong_target":args["recover_lines"]=(replace(line,target_stock_account_id=stock.account.id),)
    else:args["recover_lines"]*=2
    db.execute(text("PRAGMA query_only=ON"))
    with pytest.raises(posting.InventoryPostingError):run(db,stock,**args)
    assert not db.new and not db.dirty and not db.deleted


def test_exact_scan_resolves_registered_material_without_returning_private_qr_or_writing(db,stock):
    db.execute(text("PRAGMA query_only=ON"))
    result=resolve(db,stock)
    assert result.material_id==stock.world.material.id and result.basis_stock_account_id==stock.reserved.id
    assert result.serial_id==(stock.serials[0].id if stock.tracked else None)
    assert result.operator_person_id==stock.actor.person_id
    assert "qr_code" not in result.model_dump_json()
    assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize("stock",["serial"],indirect=True)
@pytest.mark.parametrize("damage",["unknown","qr","sku","managed","missing"])
def test_unknown_in_stock_or_incomplete_removed_serial_never_registers_or_returns_candidate(db,stock,damage):
    changes={"unknown":{"serial_no":"UNKNOWN-SN"},"qr":{"qr_code":"WRONG-QR"},"sku":{"sku_code":"NO-SUCH-SKU"},
        "managed":{"serial_no":stock.serials[2].serial_no,"qr_code":stock.serials[2].qr_code},"missing":{"qr_code":None}}[damage]
    db.execute(text("PRAGMA query_only=ON"))
    with pytest.raises(posting.InventoryPostingError):resolve(db,stock,scan(stock,**changes))
    assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize("change",["ledger","permission","policy","source","metadata"])
def test_drift_during_scan_discards_the_candidate(db,stock,monkeypatch,change):
    original=preview._recover_check;changed=False
    def mutate(*args,**kwargs):
        nonlocal changed
        result=original(*args,**kwargs)
        if not changed:
            changed=True
            if change=="ledger":db.scalar(select(InventoryLedgerHead)).next_cursor+=1
            elif change=="permission":stock.world.current_principal=replace(stock.actor,authorization_version=stock.actor.authorization_version+1)
            elif change=="policy":db.scalar(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id==stock.world.material.id)).allow_fraction ^= True
            elif change=="source":stock.orders[0].status="closed"
            else:stock.world.material.name="changed material"
            db.flush()
        return result
    monkeypatch.setattr(preview,"_recover_check",mutate)
    with pytest.raises((posting.InventoryPostingError,InventoryReadError)):resolve(db,stock)


@pytest.mark.parametrize("target",["source","recovery"])
def test_freeze_blocks_even_a_recovery_account_that_does_not_exist(db,stock,target):
    from test_inventory_posting import freeze_account_scope
    freeze_account_scope(db,stock.world,stock.reserved,freeze_mode="hard",scope_mode="filtered")
    if target=="recovery":
        from app.stocktake_models import FormalStocktakeScope
        scope=db.scalar(select(FormalStocktakeScope).order_by(FormalStocktakeScope.created_at.desc()))
        scope.condition_code="used";scope.availability_bucket="available"
    db.commit();db.execute(text("PRAGMA query_only=ON"))
    for operation in (run,resolve):
        with pytest.raises(posting.InventoryPostingError) as exc:operation(db,stock)
        assert exc.value.code=="inventory_scope_hard_frozen"


def test_http_scan_and_preview_are_no_store_and_reject_other_operator(db,stock):
    app=FastAPI();app.include_router(router.router,prefix="/api")
    app.dependency_overrides[get_db]=lambda:db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=="principal":app.dependency_overrides[dependency.call]=lambda:stock.actor
    command=replacements.replacement_request_payload(work_order_id=stock.orders[0].id,
        operator_person_id=stock.actor.person_id,**inputs(stock))
    body={key:value for key,value in command.items() if key!="work_order_id"}
    body["consume_lines"]=[{key:value for key,value in line.items() if key!="target_stock_account_id"} for line in body["consume_lines"]]
    db.execute(text("PRAGMA query_only=ON"))
    with TestClient(app) as client:
        for endpoint,payload in (("preview",body),("removed-part",scan(stock).model_dump(mode="json"))):
            path=f"/api/v1/work-orders/{stock.orders[0].id}/material-replacements/{endpoint}"
            response=client.post(path,json=payload)
            assert response.status_code==200,response.text
            assert response.headers["cache-control"]=="private, no-store"
            assert "qr_code" not in response.text
            assert client.post(path,json={**payload,"operator_person_id":str(uuid4())}).status_code==403
            assert client.post(path,json={**payload,"idempotency_key":"not-a-command"}).status_code==422


@pytest.mark.parametrize("stock",["quantity"],indirect=True)
def test_lot_recovery_supports_explicit_other_sku_and_rejects_crossed_lot(db,stock):
    from test_inventory_posting import make_material
    from app.inventory_models import InventoryLot
    sku=make_material(db,stock.world.source,tracking_mode="lot",quantity_scale=3,allow_fraction=True)
    lot=InventoryLot(id=uuid4(),material_id=sku.id,lot_no="REMOVED-LOT")
    db.add(lot);db.commit()
    args=inputs(stock)
    args["recover_lines"]=(replace(args["recover_lines"][0],material_id=sku.id,lot_id=lot.id,quantity=Decimal("1.125")),)
    db.execute(text("PRAGMA query_only=ON"))
    result=run(db,stock,**args)
    assert result.recover_line_count==1
    candidate=resolve(db,stock,scan(stock,sku_code=sku.sku_code,lot_no=lot.lot_no))
    assert candidate.lot_id==lot.id and candidate.material_id==sku.id and candidate.tracking_mode=="lot"
    for value in (scan(stock,sku_code=sku.sku_code),scan(stock,sku_code=sku.sku_code,lot_no="WRONG")):
        with pytest.raises(posting.InventoryPostingError):resolve(db,stock,value)
    args["recover_lines"]=(replace(args["recover_lines"][0],material_id=stock.world.material.id),)
    with pytest.raises(posting.InventoryPostingError) as exc:run(db,stock,**args)
    assert exc.value.code=="recover_lot_invalid"
