"""Real PG16 reversal recovery, non-execution proof and competing requests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.database import get_db
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material, work_order_command_seal as ordinary
from app.formal_services import work_order_reversal_seal as recovery
from app.formal_services.work_order_reversal_plan import preview_reversal
from app.formal_services.work_order_reversal_write import execute_reversal
from app.inventory_models import InventoryMovement, StockBalance
from app.routers import formal_work_order_material as router
from app.work_order_reversal_schemas import WorkOrderReversalPreviewIn, WorkOrderReversalIn
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_reversal_write_gate import snapshot


def _case(db, user_id, order, line):
    actor = load_formal_principal(db, user_id)
    original, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=order, lines=(line,),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    _checkpoint(db)
    selection = WorkOrderReversalPreviewIn(operator_person_id=actor.person_id,
        original_operation_id=original.id, reason="合成数据冲销恢复验证")
    plan = preview_reversal(db, actor=actor, work_order_id=order, request=selection)
    request = WorkOrderReversalIn(**selection.model_dump(), expected_plan_hash=plan.plan_hash,
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    args = dict(actor=actor, work_order_id=order, request_id=request.request_id, request_hash=plan.request_hash)
    return request, args, original


def _lookup(db, args):
    return recovery.lookup_reversal(db, **{k:v for k,v in args.items() if k != "request_hash"})


def assert_reversal_seal_atomic_gate(api_engine, fixture_engine):
    for kind, (_, user_id, orders, line) in query_worlds(fixture_engine).items():
        baseline = snapshot(api_engine)
        with Session(api_engine) as db:
            request, args, _ = _case(db, user_id, orders[0], line)
            assert _lookup(db, args) is None
            sealed = recovery.seal_reversal(db, **args); _checkpoint(db)
            assert _lookup(db, args) == sealed
            try: execute_reversal(db, actor=args['actor'], work_order_id=orders[0], request=request)
            except material.InventoryPostingError as exc: assert exc.code == 'work_order_request_sealed'
            else: raise AssertionError('sealed reversal executed')
            db.rollback()
        for first in ('seal', 'post'):
            with Session(api_engine) as db:
                request, args, _ = _case(db, user_id, orders[0], line)
                def post():
                    with patch.object(ordinary, 'require_unsealed_request', lambda *a, **k: None), patch.object(recovery, 'lookup_reversal', lambda *a, **k: None):
                        execute_reversal(db, actor=args['actor'], work_order_id=orders[0], request=request)
                def seal():
                    with patch.object(recovery, 'lookup_reversal', lambda *a, **k: None):
                        recovery.seal_reversal(db, **args)
                (seal if first == 'seal' else post)()
                (post if first == 'seal' else seal)()
                try:
                    db.execute(text('SET CONSTRAINTS trg_work_order_seals_reversal_0099, trg_work_order_reversals_seal_0099 IMMEDIATE'))
                except DBAPIError as exc:
                    assert getattr(exc.orig, 'sqlstate', None) == '23514' and '0099' in str(exc.orig)
                else: raise AssertionError('SQL accepted a posted and sealed reversal')
                db.rollback()
        assert snapshot(api_engine) == baseline
        print(f'PG16 {kind} reversal seal, late-key exclusion and both SQL insertion orders; complete rollback PASS', flush=True)


def _app(db, user_id):
    app = FastAPI(); app.include_router(router.router, prefix='/api')
    def principal(): return load_formal_principal(db, user_id)
    def session(): yield db
    app.dependency_overrides[get_db] = session
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == 'principal': app.dependency_overrides[dependency.call] = principal
    return app


def assert_reversal_http_gate(api_engine, fixture_engine):
    for kind, (_, user_id, orders, line) in query_worlds(fixture_engine).items():
        baseline = snapshot(api_engine)
        for action in ('post', 'seal'):
            with Session(api_engine) as db:
                request, args, _ = _case(db, user_id, orders[0], line)
                path = f'/api/v1/work-orders/{orders[0]}/material-reversals'
                lookup_path = path + '/by-request/' + request.request_id
                with TestClient(_app(db, user_id)) as client, patch.object(db, 'commit', side_effect=lambda: _checkpoint(db)):
                    empty = client.get(lookup_path)
                    assert empty.status_code == 404 and empty.json()['detail']['code'] == 'work_order_reversal_not_observed'
                    response = client.post(path, json=request.model_dump(mode='json')) if action == 'post' else client.post(
                        lookup_path+'/seal', json={'operator_person_id': str(args['actor'].person_id), 'request_hash':args['request_hash']})
                    assert response.status_code == 200, response.text
                    before = snapshot(SimpleNamespace(connect=lambda: nullcontext(db.connection())))
                    statements = []
                    def capture(connection, cursor, statement, parameters, context, many): statements.append(statement)
                    event.listen(db.connection(), 'before_cursor_execute', capture)
                    try: recovered = client.get(lookup_path)
                    finally: event.remove(db.connection(), 'before_cursor_execute', capture)
                    assert recovered.status_code == 200 and recovered.json() == response.json(), recovered.text
                    assert recovered.headers['cache-control'] == 'private, no-store'
                    assert all(s.lstrip().upper().startswith('SELECT') for s in statements), statements
                    assert snapshot(SimpleNamespace(connect=lambda: nullcontext(db.connection()))) == before
                db.rollback()
        assert snapshot(api_engine) == baseline
        print(f'PG16 {kind} HTTP single submit/seal, exact readback, SELECT-only GET and full rollback PASS', flush=True)


def assert_reversal_seal_commit_gate(api_engine, fixture_engine):
    # Commit only quantity fixtures, then undo/release the matching reservation.
    # The immutable seals intentionally require keeping migration 0099 afterward.
    _, user_id, orders, line = query_worlds(fixture_engine)['quantity']
    def quantities():
        with Session(api_engine) as db:
            return dict(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity).where(StockBalance.quantity != 0)).all())
    for first in ('seal', 'post'):
        before = quantities()
        with Session(api_engine) as db:
            request, args, original = _case(db, user_id, orders[0], line)
            reserved = db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id == original.posting_transaction_id))
            db.commit()
        started = Event(); identity = {}
        def late():
            with Session(api_engine) as db:
                db.execute(text("SET LOCAL statement_timeout='15s'"))
                actor = load_formal_principal(db, user_id)
                identity['pid'] = db.scalar(text('SELECT pg_backend_pid()')); started.set()
                if first == 'post':
                    result = recovery.seal_reversal(db, **{**args, 'actor':actor}); _checkpoint(db); db.commit()
                    return result
                try:
                    execute_reversal(db, actor=actor, work_order_id=orders[0], request=request.model_copy(update={'idempotency_key':uuid4().hex}))
                except material.InventoryPostingError as exc:
                    return exc.code
                raise AssertionError('late sealed reversal executed')
        with Session(api_engine) as db, ThreadPoolExecutor(max_workers=1) as workers:
            actor = load_formal_principal(db, user_id)
            result = recovery.seal_reversal(db, **{**args,'actor':actor}) if first == 'seal' else execute_reversal(
                db, actor=actor, work_order_id=orders[0], request=request)
            _checkpoint(db)
            future = workers.submit(late); assert started.wait(timeout=5)
            try:
                blocked = False; until = monotonic()+4
                with fixture_engine.connect() as probe:
                    while monotonic() < until:
                        blocked = bool(probe.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), identity))
                        if blocked: break
                        sleep(.02)
                assert blocked, 'competing reversal request did not reach the held ledger lock'
                db.commit()
            except BaseException:
                db.rollback(); raise
            returned = future.result(timeout=20)
            assert returned == ('work_order_request_sealed' if first == 'seal' else result)
        with Session(api_engine) as db:
            args['actor'] = load_formal_principal(db, user_id)
            assert _lookup(db, args) == result
            assert recovery.seal_reversal(db, **args) == result
            db.rollback()
        if first == 'seal':
            with Session(api_engine) as db:
                material.execute_release_operation(db, actor=load_formal_principal(db, user_id), work_order_id=orders[0],
                    lines=(replace(line, stock_account_id=reserved, target_stock_account_id=line.stock_account_id),),
                    idempotency_key=uuid4().hex, request_id=uuid4().hex)
                _checkpoint(db); db.commit()
        assert quantities() == before
        print(f'PG16 real {first}-first concurrent reversal request, fresh-session proof and zero net stock/reservation PASS', flush=True)
