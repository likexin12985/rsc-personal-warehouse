"""Real API-role reads of own stock and exact work-order occupancy on PG16."""
from types import SimpleNamespace
from uuid import uuid4
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.foundation_models import SourceSystem
from app.formal_access import load_formal_principal
from app.formal_services import oam_work_order_projection as projection
from app.formal_services import work_order_material as material
from app.formal_services.work_order_material_options import material_options
from app.formal_services.work_order_query import get_my_work_order, list_my_work_orders
from app.inventory_models import (FormalMaterial, InventorySerial, SerialCurrentPosition,
    StockAccount, StockBalance, StockLocation)
from app.models import User
from pg16_work_order_material_gate import _checkpoint, _snapshot
from test_work_order_material_options import add_order


def assert_work_order_query_gate(api_engine, fixture_engine):
    worlds = {}
    with Session(fixture_engine, expire_on_commit=False) as db:
        source = db.scalar(select(SourceSystem).where(SourceSystem.code == projection.SOURCE_SYSTEM_CODE))
        assert source is not None
        rows = db.execute(select(StockAccount, User.id).join(StockBalance,
            StockBalance.stock_account_id==StockAccount.id).join(StockLocation,
            StockLocation.id==StockAccount.location_id).join(User, User.person_id==StockAccount.custodian_person_id)
            .where(StockLocation.location_type=="personal", StockAccount.availability_bucket=="available",
                   StockAccount.condition_code.in_(("new", "used", "damaged")), StockBalance.quantity>=1)
            .order_by(StockAccount.id)).all()
        for account, user_id in rows:
            serials = tuple(db.scalars(select(InventorySerial).join(SerialCurrentPosition,
                SerialCurrentPosition.serial_id==InventorySerial.id).where(SerialCurrentPosition.stock_account_id==account.id)
                .order_by(InventorySerial.id)))
            kind = "serial" if serials else "quantity"
            if kind in worlds:
                continue
            world = SimpleNamespace(person=SimpleNamespace(id=account.custodian_person_id),
                                    organization=SimpleNamespace(id=account.owner_org_id))
            orders = (add_order(db, world, source), add_order(db, world, source))
            sku = db.get(FormalMaterial, account.material_id)
            worlds[kind] = (account.id, user_id, tuple(row.id for row in orders),
                material.WorkOrderMaterialLineInput(account.material_id, account.id, Decimal(1),
                    tuple(row.id for row in serials[:1]), account.condition_code,
                    tuple(material.SerialVerificationInput(row.id, sku.sku_code, row.serial_no, row.qr_code)
                          for row in serials[:1])))
        db.commit()
    assert set(worlds)=={"quantity", "serial"}
    for kind, (account_id, user_id, orders, line) in worlds.items():
        baseline = _snapshot(api_engine)
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            number = get_my_work_order(db, actor=actor, work_order_id=orders[0]).work_order_no
            choices = list_my_work_orders(db, actor=actor, search=number)
            assert len(choices.items)==1 and choices.items[0].work_order_id==orders[0]
            material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
                lines=(line,), idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db)
            first = material_options(db, actor=actor, work_order_id=orders[0])
            second = material_options(db, actor=actor, work_order_id=orders[1])
            reserved = [row for row in first.items if row.availability_bucket=="reserved"]
            assert len(reserved)==1 and reserved[0].selectable_quantity=="1.000"
            assert {row.serial_id for row in reserved[0].serials}==set(line.serial_ids)
            assert all(row.availability_bucket!="reserved" for row in second.items)
            material.execute_consume_operation(db, actor=actor, work_order_id=orders[0],
                lines=(material.WorkOrderMaterialLineInput(line.material_id, reserved[0].stock_account_id,
                    line.quantity, line.serial_ids, line.condition_before, line.serial_verifications),),
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db)
            after = material_options(db, actor=actor, work_order_id=orders[0])
            assert all(row.availability_bucket!="reserved" for row in after.items)
            assert not ({row.serial_id for item in after.items for row in item.serials} & set(line.serial_ids))
            db.rollback()
        assert _snapshot(api_engine)==baseline
        print(f"PG16 {kind} formal own order, exact reservation/SN choices, consumption refresh and rollback PASS", flush=True)
