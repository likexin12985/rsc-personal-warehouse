"""Real approval/allocation history through the public read-only capacity GET."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_formal_principal
from app.demand_models import MaterialRequestCommand, MaterialRequestLine
from app.formal_services import material_request_supply as supply
from app.routers.formal_material_requests import router
from test_material_request_supply_allocation_recovery import _allocated_world
from test_material_request_draft_service import SECRET


@pytest.fixture
def database():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _): connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.mark.parametrize('serial', [False, True])
def test_real_http_capacity_and_detail_create_permission_follow_remaining_quantity(database, monkeypatch, serial):
    db, actor, demand, _, _, _, balance, _ = _allocated_world(database, monkeypatch,
        serial=serial, planned=False, allocation_count=1)
    monkeypatch.setattr(supply, '_database_now', lambda db: datetime.now(timezone.utc))
    request_id = demand.id
    api = FastAPI(); api.include_router(router, prefix='/api')
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: actor
    path = '/api/v1/material-requests/'+str(request_id)
    statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    with TestClient(api) as client:
        event.listen(db.bind, 'before_cursor_execute', capture)
        try:
            response = client.get(path+'/supply-planning-capacity')
            detail = client.get(path)
        finally:
            event.remove(db.bind, 'before_cursor_execute', capture)
        assert response.status_code == detail.status_code == 200, (response.text, detail.text)
        assert statements and all(sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper() for sql in statements)
        assert 'no-store' in response.headers['cache-control']
        assert 'create_supply_task' in detail.json()['allowed_actions']
        body = response.json(); assert body['request_version'] == demand.version
        line = body['lines'][0]; assert line['new_plan_qty'] == ('1.000' if serial else '1.500')
        stock_before = (balance.quantity, balance.version, balance.ledger_cursor)
        supply.create_supply_task(db, actor=actor, material_request_id=request_id,
            expected_request_version=demand.version,
            plan=supply.SupplyTaskCreateInput(request_line_id=db.scalar(select(MaterialRequestLine.id).where(
                MaterialRequestLine.request_id == request_id)), supply_type='headquarters_replenishment',
                expected_qty=Decimal(line['new_plan_qty']), reference_no=None, expected_date=None),
            idempotency_key='http-capacity-plan', idempotency_hmac_secret=SECRET, trace_request_id='http-capacity-plan-trace')
        db.commit()
        response = client.get(path+'/supply-planning-capacity'); detail = client.get(path)
        assert response.status_code == detail.status_code == 200, (response.text, detail.text)
        assert response.json()['lines'][0]['new_plan_qty'] == '0.000'
        assert 'create_supply_task' not in detail.json()['allowed_actions']
        assert (balance.quantity, balance.version, balance.ledger_cursor) == stock_before
        # An incomplete command must not become a plausible zero capacity.
        command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.request_id == request_id,
            MaterialRequestCommand.operation == 'allocate'))
        command.result_hash = '0' * 64; db.commit()
        response = client.get(path+'/supply-planning-capacity')
        assert response.status_code == 503 and 'lines' not in response.json()
        assert 'no-store' in response.headers['cache-control']


def test_unproved_later_fulfillment_fails_closed(database, monkeypatch):
    db, actor, demand, *_ = _allocated_world(database, monkeypatch, serial=False, planned=False, allocation_count=1)
    demand.reservation_status = 'released'; db.commit(); request_id = demand.id
    api = FastAPI(); api.include_router(router, prefix='/api')
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: actor
    with TestClient(api) as client:
        response = client.get(f'/api/v1/material-requests/{request_id}/supply-planning-capacity')
    # A projection-only status change has no immutable reservation/release
    # history. The post-fulfillment reader must fail closed instead of
    # treating it as a valid zero-capacity plan.
    assert response.status_code == 503 and 'lines' not in response.json()
    assert 'no-store' in response.headers['cache-control']
