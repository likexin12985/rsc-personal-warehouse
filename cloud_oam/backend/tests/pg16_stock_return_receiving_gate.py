"""Actual regional recipients read frozen return parcels in READ ONLY mode."""
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.formal_access import load_formal_principal
from app.inventory_models import Shipment
from app.models import User
from app.stock_operation_models import StockOperationShipment
from app.routers import formal_stock_return_receiving as router
from pg16_stock_return_shipment_gate import parcel_snapshot


def _app(db, user_id):
    app = FastAPI(); app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal":
                app.dependency_overrides[dependency.call] = lambda: load_formal_principal(db, user_id)
    return app


def assert_return_receiving_gate(api_engine, candidates):
    before = parcel_snapshot(api_engine)
    operations = tuple(UUID(str(row['operation_id'])) for row in candidates)
    with Session(api_engine) as db:
        shipments = tuple(db.execute(select(Shipment.id, Shipment.target_person_id, User.id)
            .join(StockOperationShipment, StockOperationShipment.id == Shipment.id)
            .join(User, User.person_id == Shipment.target_person_id)
            .where(StockOperationShipment.operation_id.in_(operations)).order_by(Shipment.id)))
    assert shipments, 'durable parcels are required before the receiver read gate'
    serial_kinds = set()
    for shipment_id, person_id, user_id in shipments:
        with Session(api_engine) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            statements = []
            def capture(_c, _cu, sql, _p, _ctx, _many): statements.append(sql)
            connection = db.connection(); event.listen(connection, 'before_cursor_execute', capture)
            try:
                with TestClient(_app(db, user_id)) as client:
                    prefix = '/api/v1/stock-returns/my-receiving'
                    response = client.get(prefix + '/' + str(shipment_id))
                    assert response.status_code == 200, response.text
                    package = response.json()['package']
                    assert package['verification_status'] == 'verified'
                    assert package['shipment_id'] == str(shipment_id) and package['receiver_person_id'] == str(person_id)
                    assert package['lines'] and all(line['shipment_line_id'] for line in package['lines'])
                    serial_kinds.update(bool(line['serials']) for line in package['lines'])
                    assert 'no-store' in response.headers['cache-control']
                    for private in ('stock_account_id', 'request_hash', 'plan_hash', 'qr_code', 'posting_transaction_id'):
                        assert private not in response.text
                    listing = client.get(prefix + '?limit=20')
                    assert listing.status_code == 200, listing.text
                    matching = [row for row in listing.json()['items'] if row['shipment_id'] == str(shipment_id)]
                    assert matching == [package]
                    missing = client.get(prefix + '/' + str(uuid4()))
                    assert missing.status_code == 404 and 'no-store' in missing.headers['cache-control']
            finally:
                event.remove(connection, 'before_cursor_execute', capture)
            assert statements and all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
            db.rollback()
    assert serial_kinds == {False, True}
    assert parcel_snapshot(api_engine) == before
    print('PG16 return receiving: actual recipient HTTP, quantity/SN exact parcel fields, forced READ ONLY and original stock unchanged PASS', flush=True)
