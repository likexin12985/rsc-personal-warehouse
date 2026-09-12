"""Committed ordinary commands are read in new API-role transactions on PG16."""
from dataclasses import replace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_db
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material
from app.formal_services.work_order_operation_read import lookup_operation, client_request_hash
from app.routers import formal_work_order_material as router
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_material_gate import _checkpoint


def assert_work_order_recovery_gate(api_engine, fixture_engine):
    worlds = query_worlds(fixture_engine)
    for kind, (account_id, user_id, orders, line) in worlds.items():
        commands = []
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            trace = 'pg16-recovery-' + uuid4().hex
            operation, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
                lines=(line,), idempotency_key=uuid4().hex, request_id=trace)
            _checkpoint(db)
            from app.inventory_models import InventoryMovement
            from sqlalchemy import select
            reserved_id = db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id==operation.posting_transaction_id))
            commands.append(('occupy', trace, operation.id, line))
            db.commit()
        def verify(db, operation_kind, trace, operation_id, input_line):
            current = load_formal_principal(db, user_id)
            result = lookup_operation(db, actor=current, work_order_id=orders[0], operation_type=operation_kind, request_id=trace)
            assert result.lookup_status=='confirmed' and result.command.operation_id==operation_id
            assert result.command.request_hash==client_request_hash(operation_type=operation_kind, work_order_id=orders[0],
                operator_person_id=current.person_id, lines=(input_line,))
            return result
        with Session(api_engine) as db:
            verify(db, *commands[0])
            released = replace(line, stock_account_id=reserved_id, target_stock_account_id=account_id)
            trace = 'pg16-recovery-' + uuid4().hex
            operation, _ = material.execute_release_operation(db, actor=load_formal_principal(db,user_id), work_order_id=orders[0],
                lines=(released,), idempotency_key=uuid4().hex, request_id=trace)
            _checkpoint(db); commands.append(('release', trace, operation.id, released)); db.commit()
        with Session(api_engine) as db:
            for command in commands: verify(db, *command)
            trace = 'pg16-recovery-' + uuid4().hex
            operation, _ = material.execute_occupy_operation(db, actor=load_formal_principal(db,user_id), work_order_id=orders[0],
                lines=(line,), idempotency_key=uuid4().hex, request_id=trace)
            _checkpoint(db); commands.append(('occupy', trace, operation.id, line)); db.commit()
        with Session(api_engine) as db:
            consumed = replace(line, stock_account_id=reserved_id)
            trace = 'pg16-recovery-' + uuid4().hex
            operation, _ = material.execute_consume_operation(db, actor=load_formal_principal(db,user_id), work_order_id=orders[0],
                lines=(consumed,), idempotency_key=uuid4().hex, request_id=trace)
            _checkpoint(db); commands.append(('consume', trace, operation.id, consumed)); db.commit()
        # A new session observes committed history after the SN was consumed.
        with Session(api_engine) as db:
            for command in commands: verify(db, *command)
            assert lookup_operation(db, actor=load_formal_principal(db,user_id), work_order_id=orders[0],
                operation_type='consume', request_id='not-observed').lookup_status=='not_observed'
        app=FastAPI(); app.include_router(router.router,prefix='/api')
        def request_db():
            with Session(api_engine) as db: yield db
        def principal():
            with Session(api_engine) as db: return load_formal_principal(db,user_id)
        app.dependency_overrides[get_db]=request_db
        for route in router.router.routes:
            for dependency in route.dependant.dependencies:
                if dependency.name=='principal':app.dependency_overrides[dependency.call]=principal
        with TestClient(app) as client:
            response=client.get(f'/api/v1/work-orders/{orders[0]}/material-operations/consume/by-request/{commands[-1][1]}')
            assert response.status_code==200,response.text
            assert response.json()['command']['operation_id']==str(commands[-1][2])
            assert response.headers['cache-control']=='private, no-store'
        print(f'PG16 {kind} committed occupy/release/consume, original-request proof after later movements and fresh-session HTTP recovery PASS',flush=True)
