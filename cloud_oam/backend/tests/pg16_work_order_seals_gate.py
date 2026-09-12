"""Real PostgreSQL request-seal races, late-write exclusion and retention."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import work_order_command_seal as seals
from app.formal_services import work_order_material as material
from app.formal_services.work_order_operation_read import client_request_hash
from app.work_order_material_schemas import WorkOrderMaterialLookupOut
from pg16_work_order_material_gate import _checkpoint, _snapshot


def _args(actor, order, line, trace):
    return dict(actor=actor, work_order_id=order, operation_type='occupy', request_id=trace,
        request_hash=client_request_hash(operation_type='occupy', work_order_id=order,
            operator_person_id=actor.person_id, lines=(line,)))


def assert_seal_atomic_gate(api_engine, worlds):
    for kind, (_, user_id, orders, line) in worlds.items():
        baseline = _snapshot(api_engine)
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id); trace = uuid4().hex
            operation, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
                lines=(line,), idempotency_key=uuid4().hex, request_id=trace)
            _checkpoint(db)
            resolved = seals.seal_command(db, **_args(actor, orders[0], line, trace))
            assert resolved.lookup_status == 'confirmed' and resolved.command.operation_id == operation.id
            db.rollback()
        # Bypass each application guard deliberately. PostgreSQL must reject
        # both insertion orders even when both facts appear in one transaction.
        for first in ('seal', 'operation'):
            try:
                with Session(api_engine) as db:
                    actor = load_formal_principal(db, user_id); trace = uuid4().hex
                    def post():
                        with patch.object(seals, 'require_unsealed_request', lambda *a, **k: None):
                            material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
                                lines=(line,), idempotency_key=uuid4().hex, request_id=trace)
                    def seal():
                        with patch.object(seals, 'lookup_command_result', lambda *a, **k: WorkOrderMaterialLookupOut(lookup_status='not_observed', command=None)):
                            seals.seal_command(db, **_args(actor, orders[0], line, trace))
                    (seal if first == 'seal' else post)()
                    (post if first == 'seal' else seal)()
                    _checkpoint(db)
            except DBAPIError as exc:
                assert getattr(exc.orig, 'sqlstate', None) == '23514'
                assert '0094' in str(exc.orig)
            else:
                raise AssertionError('seal and posting coexisted')
        assert _snapshot(api_engine) == baseline
        print(f'PG16 {kind} original proof and both seal/post insertion orders PASS', flush=True)


def assert_seal_commit_gate(api_engine, fixture_engine, worlds):
    for kind, (_, user_id, orders, line) in worlds.items():
        baseline = _snapshot(api_engine)
        started = Event(); identity = {}
        trace = uuid4().hex
        def late_post():
            with Session(api_engine) as db:
                db.execute(text("SET LOCAL statement_timeout = '10s'"))
                actor = load_formal_principal(db, user_id)
                identity['pid'] = db.scalar(text('SELECT pg_backend_pid()'))
                started.set()
                try:
                    material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
                        lines=(line,), idempotency_key=uuid4().hex, request_id=trace)
                except material.InventoryPostingError as exc:
                    return exc.code
                raise AssertionError('late sealed command executed')
        with Session(api_engine) as db, ThreadPoolExecutor(max_workers=1) as worker:
            actor = load_formal_principal(db, user_id)
            original = seals.seal_command(db, **_args(actor, orders[0], line, trace))
            _checkpoint(db)
            future = worker.submit(late_post)
            assert started.wait(timeout=5)
            blocked = False; until = monotonic()+3
            with fixture_engine.connect() as probe:
                while monotonic() < until:
                    blocked = bool(probe.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), identity))
                    if blocked: break
                    sleep(.02)
            assert blocked, 'late request did not reach the held ledger lock'
            db.commit()
            assert future.result(timeout=12) == 'work_order_request_sealed'
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            result = seals.lookup_command_result(db, actor=actor, work_order_id=orders[0], operation_type='occupy', request_id=trace)
            assert result.lookup_status == 'sealed_not_executed' and result.seal.seal_id == original.seal.seal_id
            assert seals.seal_command(db, **_args(actor, orders[0], line, trace)).seal.seal_id == original.seal.seal_id
            db.rollback()
        assert_sealed_http_result(api_engine, actor, original)
        for sql in ('UPDATE public.work_order_command_seals SET request_hash = request_hash WHERE id = :id',
                    'DELETE FROM public.work_order_command_seals WHERE id = :id', 'TRUNCATE public.work_order_command_seals'):
            for engine in (api_engine, fixture_engine):
                try:
                    with engine.begin() as connection:
                        connection.execute(text(sql), {'id':original.seal.seal_id})
                except DBAPIError as exc:
                    assert getattr(exc.orig, 'sqlstate', None) in {'42501', '55000'}
                else: raise AssertionError('immutable seal was mutable')
        after = _snapshot(api_engine)
        # The seal contributes exactly one audit event, but no inventory fact,
        # balance, serial position, lifecycle or stock ledger cursor changes.
        assert after[0][:8] == baseline[0][:8] and after[0][9:] == baseline[0][9:]
        assert after[0][8] == baseline[0][8] + 1 and after[1:5] == baseline[1:5]
        before_heads = {row[0]: tuple(row[1:]) for row in baseline[5]}
        after_heads = {row[0]: tuple(row[1:]) for row in after[5]}
        assert after_heads['material_request'][0] == before_heads['material_request'][0]+1
        assert {k:v for k,v in after_heads.items() if k!='material_request'} == {k:v for k,v in before_heads.items() if k!='material_request'}
        print(f'PG16 {kind} durable seal, blocked late POST, exact read and append-only retention PASS', flush=True)


def assert_sealed_http_result(api_engine, actor, original):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import formal_work_order_material as router

    app = FastAPI(); app.include_router(router.router, prefix='/api')
    def connection():
        with Session(api_engine) as db:
            yield db
    app.dependency_overrides[get_db] = connection
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == 'principal':
                app.dependency_overrides[dependency.call] = lambda: actor
    seal = original.seal
    path = f'/api/v1/work-orders/{seal.work_order_id}/material-operations/{seal.operation_type}/by-request/{seal.request_id}'
    with TestClient(app) as client:
        result = client.get(path)
        assert result.status_code == 200 and result.json() == original.model_dump(mode='json')
        assert result.headers['cache-control'] == 'private, no-store'
        replay = client.post(path+'/seal', json={'operator_person_id':str(actor.person_id), 'request_hash':seal.request_hash},
            headers={'X-Request-ID':seal.request_id})
        assert replay.status_code == 200 and replay.json() == result.json()


def assert_seal_migration_roundtrip(gate):
    gate._run_alembic('downgrade', '20261003_0093')
    assert gate._current_revision() == '20261003_0093'
    gate._run_alembic('upgrade', 'head')
    assert gate._current_revision() == gate.HEAD_REVISION
