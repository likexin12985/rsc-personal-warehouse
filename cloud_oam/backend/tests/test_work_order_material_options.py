"""Formal own-stock choices over real opening, occupancy and consumption facts."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.demand_models import OamWorkOrder
from app.foundation_models import AuditChainHead, ExternalObject, ExternalObjectVersion, SourceSystem
from app.inventory_models import InventoryLedgerHead, InventorySerial, StockAccount, StockBalance
from app.formal_services import oam_work_order_projection as projection
from app.formal_services import work_order_material as material
from app.formal_services import work_order_material_options as options
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.inventory_query import InventoryReadError
from test_inventory_posting import world, make_material, make_positive_opening_facts, make_principal
from test_work_order_material_evidence import db
from test_formal_inventory_established_read import _make_personal_account


def add_order(db, world, source, *, person=None):
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    person = person or world.person
    payload = {"work_order_no": "OPTIONS-" + uuid4().hex,
        "organization_id": str(world.organization.id), "engineer_person_id": str(person.id), "status": "active"}
    external = ExternalObject(id=uuid4(), source_system_id=source.id, entity_type="work_order", external_id=uuid4().hex)
    db.add(external); db.flush()
    version = ExternalObjectVersion(id=uuid4(), external_object_id=external.id,
        source_version=projection._projection_source_version("c" * 64, "d" * 64), source_updated_at=now,
        valid_from=now, valid_to=None, payload_jsonb=payload, payload_sha256=projection._sha256(payload),
        is_current=True, created_at=now)
    db.add(version); db.flush(); external.current_version_id=version.id
    order = OamWorkOrder(id=uuid4(), external_object_id=external.id, work_order_no=payload["work_order_no"],
        organization_id=world.organization.id, engineer_person_id=person.id, status="active",
        source_updated_at=now, created_at=now, updated_at=now)
    db.add(order); db.flush()
    return order


@pytest.fixture(params=["quantity", "serial"])
def stock(db, world, request):
    tracked = request.param == "serial"
    if tracked:
        world.material = make_material(db, world.source, tracking_mode="serial", quantity_scale=0, allow_fraction=False)
    principal = make_principal(world.user, world.person, scope_type="person", scope_id=str(world.person.id))
    world.current_principal = replace(principal, entitlements=principal.entitlements + tuple(
        replace(principal.entitlements[0], resource=resource, action=action)
        for resource, action in (("inventory", "read"), ("work_order_material", "read"), ("work_order_material", "operate"))))
    db.add(AuditChainHead(id=uuid4(), stream_key="material_request", last_event_id=None, last_hash=None, version=0))
    account, location, facts = _make_personal_account(db, world)
    serials = tuple(InventorySerial(id=uuid4(), material_id=world.material.id,
        serial_no=f"OPTIONS-SN-{number}", qr_code=f"OPTIONS-QR-{number}", lifecycle_status="active",
        created_at=facts.count_line.counted_at, updated_at=facts.count_line.counted_at) for number in range(5)) if tracked else ()
    db.add_all(serials); db.flush()
    make_positive_opening_facts(db, facts, quantity=Decimal("5" if tracked else "5.125"), serials=serials)
    source = SourceSystem(id=uuid4(), code=projection.SOURCE_SYSTEM_CODE, name="OAM options fixture",
        mode="read_only", enabled=True, configuration_jsonb={})
    db.add(source); db.flush()
    orders = (add_order(db, world, source), add_order(db, world, source),
              add_order(db, world, source, person=world.headquarters_reviewer_person))
    db.commit()
    def line(quantity, selected, *, identifier=account.id, target=None):
        return material.WorkOrderMaterialLineInput(world.material.id, identifier, Decimal(quantity),
            tuple(row.id for row in selected), "new",
            tuple(material.SerialVerificationInput(row.id, world.material.sku_code, row.serial_no, row.qr_code) for row in selected), target)
    for order, quantity, selected in ((orders[0], "2", serials[:2]), (orders[1], "1", serials[2:3])):
        material.execute_occupy_operation(db, actor=world.current_principal, work_order_id=order.id,
            lines=(line(quantity, selected),), idempotency_key=uuid4().hex, request_id=uuid4().hex)
        db.commit()
    reserved = db.scalar(select(StockAccount).where(StockAccount.location_id==location.id,
        StockAccount.material_id==world.material.id, StockAccount.availability_bucket=="reserved"))
    material.execute_consume_operation(db, actor=world.current_principal, work_order_id=orders[0].id,
        lines=(line("1", serials[:1], identifier=reserved.id),), idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    return SimpleNamespace(world=world, actor=world.current_principal, account=account, reserved=reserved,
        orders=orders, serials=serials, tracked=tracked, facts=facts, line=line)


def test_read_only_options_keep_pooled_balance_separate_from_exact_work_order_quantity(db, stock):
    db.execute(text("PRAGMA query_only=ON"))
    response = options.material_options(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    items = {row.availability_bucket: row for row in response.items}
    assert items["available"].selectable_quantity == ("2.000" if stock.tracked else "2.125")
    assert items["reserved"].quantity == "2.000"
    assert items["reserved"].selectable_quantity == "1.000"
    assert items["available"].allowed_actions == ("occupy",)
    assert items["reserved"].allowed_actions == ("consume", "release", "replace")
    expected = {stock.serials[1].id} if stock.tracked else set()
    assert {row.serial_id for row in items["reserved"].serials} == expected
    assert "qr_code" not in response.model_dump_json()
    assert response.person_id==stock.actor.person_id and response.authorization_version==stock.actor.authorization_version
    assert not db.new and not db.dirty and not db.deleted


def test_released_stock_leaves_this_orders_options_without_taking_other_orders_reservations(db, stock):
    material.execute_release_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        lines=(stock.line("1", stock.serials[1:2], identifier=stock.reserved.id, target=stock.account.id),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    first = options.material_options(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    second = options.material_options(db, actor=stock.actor, work_order_id=stock.orders[1].id)
    assert all(row.availability_bucket != "reserved" for row in first.items)
    remaining = next(row for row in second.items if row.availability_bucket == "reserved")
    assert remaining.selectable_quantity == "1.000"
    assert {row.serial_id for row in remaining.serials} == ({stock.serials[2].id} if stock.tracked else set())


def test_foreign_order_returns_no_stock_even_for_national_principal(db, stock):
    stock.world.current_principal = replace(stock.actor, entitlements=tuple(
        replace(row, scope_type="national", scope_id="*") for row in stock.actor.entitlements))
    with pytest.raises(InventoryPostingError) as exc:
        options.material_options(db, actor=stock.world.current_principal, work_order_id=stock.orders[2].id)
    assert exc.value.code == "work_order_not_found"


def test_corrupt_opening_or_balance_cannot_become_selectable_stock(db, stock):
    db.get(StockBalance, stock.reserved.id).quantity += Decimal("7")
    db.commit()
    with pytest.raises(InventoryReadError) as exc:
        options.material_options(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    assert exc.value.code == "inventory_projection_integrity_invalid"


@pytest.mark.parametrize("change", ["ledger", "authority", "source"])
def test_changes_during_read_discard_all_choices(db, stock, monkeypatch, change):
    original = options._material_items
    def changed(*args, **kwargs):
        items = original(*args, **kwargs)
        if change == "ledger":
            db.scalar(select(InventoryLedgerHead)).next_cursor += 1
            db.flush()
        elif change == "source":
            stock.orders[0].updated_at += timedelta(seconds=1)
            db.flush()
        else:
            stock.world.current_principal = replace(stock.actor, authorization_version=stock.actor.authorization_version+1)
        return items
    monkeypatch.setattr(options, "_material_items", changed)
    with pytest.raises((InventoryReadError, InventoryPostingError)) as exc:
        options.material_options(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    assert exc.value.code == {"ledger": "inventory_projection_changed", "authority": "actor_principal_stale",
                              "source": "work_order_projection_changed"}[change]


def test_http_options_use_formal_ids_and_do_not_cache_stock(db, stock):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import formal_work_order_query as api
    app = FastAPI(); app.include_router(api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal":
                app.dependency_overrides[dependency.call] = lambda: stock.actor
    with TestClient(app) as client:
        response = client.get(f"/api/v1/work-orders/{stock.orders[0].id}/material-options")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["work_order"]["work_order_id"] == str(stock.orders[0].id)
        assert client.get(f"/api/v1/work-orders/{stock.orders[2].id}/material-options").status_code == 404
