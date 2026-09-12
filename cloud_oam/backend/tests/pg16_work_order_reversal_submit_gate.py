"""Shipped mini SDK reads originals and proves an entire reversal or permanent seal.

Real API role, deferred guards and SELECT-only read/preview assertions; synthetic
stock, identities and compensations are contained in one rollback transaction.
"""
from contextlib import nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, event, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.demand_models import WorkOrderReversal, WorkOrderCommandSeal, WorkOrderMaterialOperation
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material, work_order_replacements as paired
from app.formal_services import work_order_removed_registration as registration
from app.inventory_models import InventoryMovement, InventoryTransaction, StockAccount
from app.routers import formal_work_order_query, formal_work_order_material
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_removed_registration_gate import case, recovered
from pg16_work_order_reversal_write_gate import snapshot


def _original(db, world, operation):
    _, user_id, orders, line = world
    actor = load_formal_principal(db, user_id)
    if operation == 'replace' and line.serial_ids:
        args, installed = case(db, world)
        removed = registration.register_removed_serial(db, **args); _checkpoint(db)
        parent = paired.execute_replacement(db, actor=actor, work_order_id=orders[0], consume_lines=(installed,),
            recover_lines=(recovered(db, args, removed),), pairs=(material.WorkOrderReplacementPairInput(installed.serial_ids[0], removed.serial_id),),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        return dict(original_operation_id=None, original_replacement_id=str(parent.id))
    original, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0], lines=(line,),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    _checkpoint(db)
    if operation != 'occupy':
        reserve = db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id == original.posting_transaction_id))
        command = replace(line, stock_account_id=reserve, target_stock_account_id=line.stock_account_id if operation == 'release' else None)
        if operation == 'replace':
            account = db.get(StockAccount, reserve)
            parent = paired.execute_replacement(db, actor=actor, work_order_id=orders[0], consume_lines=(command,),
                recover_lines=(paired.RecoveryLineInput(reserve, line.material_id, line.quantity, 'damaged', lot_id=account.lot_id),),
                pairs=(), idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db)
            return dict(original_operation_id=None, original_replacement_id=str(parent.id))
        original, _ = getattr(material, f'execute_{operation}_operation')(db, actor=actor, work_order_id=orders[0],
            lines=(command,), idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
    return dict(original_operation_id=str(original.id), original_replacement_id=None)


def assert_reversal_submit_gate(api_engine, fixture_engine):
    node = shutil.which('node'); assert node, 'Node is required for the reversal mini SDK gate'
    script = Path(__file__).resolve().parents[2] / 'miniprogram/tests/fixtures/work-order-reversal-submit-pg16.cjs'
    for kind, world in query_worlds(fixture_engine).items():
        _, user_id, orders, _ = world
        for operation, mode in [('occupy','post'), ('consume','post'), ('release','post'), ('replace','post'), ('replace','lost_after'), ('occupy','lost_before')]:
            baseline = snapshot(api_engine)
            with Session(api_engine) as db:
                selection = _original(db, world, operation)
                actor = load_formal_principal(db, user_id)
                fixture = dict(workOrderId=str(orders[0]), personId=str(actor.person_id), authorizationVersion=actor.authorization_version,
                    selection=selection, reason='核对原件后冲销🔧\n整组保留历史', paired=operation == 'replace', tracked=kind == 'serial', mode=mode)
                local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
                models = (WorkOrderReversal, WorkOrderMaterialOperation, InventoryTransaction, WorkOrderCommandSeal)
                counts = [db.scalar(select(func.count()).select_from(model)) for model in models]
                app = FastAPI(); app.dependency_overrides[get_db] = lambda: db
                for router in (formal_work_order_query.router, formal_work_order_material.router):
                    app.include_router(router, prefix='/api')
                    for route in router.routes:
                        for dependency in route.dependant.dependencies:
                            if dependency.name == 'principal': app.dependency_overrides[dependency.call] = lambda: load_formal_principal(db, user_id)
                process = subprocess.Popen([node, str(script)], text=True, bufsize=1, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env={'PATH': os.environ.get('PATH', '')})
                try:
                    process.stdin.write(json.dumps(fixture) + '\n'); process.stdin.flush()
                    with selectors.DefaultSelector() as ready, TestClient(app) as client, patch.object(db, 'commit', side_effect=lambda: _checkpoint(db)):
                        ready.register(process.stdout, selectors.EVENT_READ)
                        complete = False; original_request = None
                        for _ in range(20):
                            assert ready.select(timeout=30), 'reversal mini request timed out'
                            message = process.stdout.readline()
                            assert message, 'reversal mini exited before completion: ' + process.stderr.read()
                            request = json.loads(message)
                            if request.get('complete'):
                                assert request == dict(complete=True, posts=1, previews=1, reads=3 if mode=='lost_before' else 1,
                                    seals=1 if mode=='lost_before' else 0, confirmations=1, status='sealed' if mode=='lost_before' else 'confirmed')
                                complete = True; break
                            data = request['request']
                            assert data['path'].startswith(f'/api/v1/work-orders/{orders[0]}/')
                            posting = data['method'] == 'POST' and data['path'].endswith('/material-reversals')
                            sealing = data['method'] == 'POST' and data['path'].endswith('/seal')
                            if posting:
                                assert original_request is None
                                original_request = data
                            before = snapshot(local)
                            if posting and mode == 'lost_before':
                                response_data = {'transportLost': True}
                            else:
                                statements = []
                                def capture(_c, _cu, statement, _p, _ctx, _m): statements.append(statement)
                                event.listen(db.connection(), 'before_cursor_execute', capture)
                                try: response = client.request(data['method'], data['path'], json=data.get('data'), headers=data['headers'])
                                finally: event.remove(db.connection(), 'before_cursor_execute', capture)
                                assert response.status_code == 200 or (mode == 'lost_before' and response.status_code == 404 and response.json()['detail']['code'] == 'work_order_reversal_not_observed'), (data['path'], response.text)
                                if not posting and not sealing:
                                    assert response.headers['cache-control'] == 'private, no-store'
                                    assert all(row.lstrip().upper().startswith('SELECT') for row in statements), statements
                                    assert snapshot(local) == before
                                response_data = {'transportLost': True} if posting and mode == 'lost_after' else {'status': response.status_code, 'body': response.json()}
                            process.stdin.write(json.dumps(response_data) + '\n'); process.stdin.flush()
                        assert complete
                        if mode == 'lost_before':
                            before = snapshot(local)
                            # Preserve the outer synthetic history while the
                            # route rolls back its rejected late request.
                            with db.begin_nested() as late, patch.object(db, 'rollback', side_effect=late.rollback):
                                response = client.post(original_request['path'], json=original_request['data'], headers=original_request['headers'])
                            assert response.status_code == 412 and response.json()['detail']['code'] == 'work_order_request_sealed', response.text
                            assert snapshot(local) == before
                        else:
                            response = client.get(f'/api/v1/work-orders/{orders[0]}/material-reversals/originals')
                            assert response.status_code == 200, response.text
                            selected = next(row for row in response.json()['items'] if all(row[key] == value for key, value in selection.items()))
                            assert selected['reversal_id'] is not None
                    process.stdin.close(); assert process.wait(timeout=10) == 0, process.stderr.read()
                    amount = 2 if operation == 'replace' else 1
                    expected = [a+b for a,b in zip(counts, [0,0,0,1] if mode=='lost_before' else [1,amount,amount,0])]
                    assert [db.scalar(select(func.count()).select_from(model)) for model in models] == expected
                finally:
                    if process.poll() is None: process.kill(); process.wait(timeout=10)
                    for stream in (process.stdin, process.stdout, process.stderr): stream.close()
                    db.rollback()
            assert snapshot(api_engine) == baseline
            print(f'PG16 {kind} {operation} reversal mini SDK {mode}, exact originals, full review, one POST, verified recovery and complete rollback PASS', flush=True)
