"""Deterministic publisher/new-opening interleaving on owned PG16 engines."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import runpy
from threading import Event
import time
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app import inventory_control_configuration as configuration
from app import inventory_control_projection as publication_service
from app.foundation_models import SyncRun
from app.formal_access import load_formal_principal
from app.formal_services import opening_stocktake as opening
from app.inventory_control_projection_models import ControlProjectionPublication, ControlProjectionLine
from app.inventory_models import StockLocation
from pg16_inventory_control_preparation_gate import _formal_stock
from app.formal_services.audit_chain import append_audit_event


def assert_return_migration_drift_refused(owner):
    """A bad source, widened ACL or disabled guard must prevent migration.

    Only the parent's disposable database is used; every synthetic catalog
    change, including the failed migration transaction, is rolled back.
    """
    migration = runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/20261102_0123_seal_audit_lock_scope.py'))
    def catalog():
        with owner.connect() as db:
            return tuple(db.execute(text("""SELECT p.oid,p.prosrc,p.proacl::text,p.proconfig,
                    t.tgname,t.tgenabled FROM pg_proc p JOIN pg_trigger t ON t.tgfoid=p.oid
                    WHERE p.proname IN ('rsc_guard_stock_operation_seal_0101',
                        'rsc_dispatch_stock_return_0100','rsc_dispatch_stock_return_outbound_0103')
                    ORDER BY p.oid,t.tgname""")))
    before = catalog()
    for signature, trigger in migration['AUDIT_TRIGGERS'].items():
        for drift in ('source','acl','trigger'):
            with owner.connect() as db:
                assert db.execute(text('SELECT current_user,session_user')).one()==('star_oam_migrator','star_oam_migrator')
                if drift=='source':
                    definition = db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
                    db.exec_driver_sql(definition.replace('BEGIN\n','BEGIN\n    -- synthetic catalog drift\n',1),
                        execution_options={'no_parameters':True})
                elif drift=='acl':
                    db.exec_driver_sql('GRANT EXECUTE ON FUNCTION '+signature+' TO star_oam_api')
                else:
                    db.exec_driver_sql('ALTER TABLE public.audit_events DISABLE TRIGGER '+trigger)
                try:
                    with pytest.raises(DBAPIError) as error, Operations.context(MigrationContext.configure(db)):
                        migration['downgrade']()
                    assert error.value.orig.sqlstate=='P0001'
                    assert '0123 return function source, ownership, ACL or trigger drift' in str(error.value.orig)
                finally: db.rollback()
            assert catalog()==before
    print('PG16 0123 migration preflight: 3 functions x source/ACL/trigger drift refused, catalog restored PASS',flush=True)


def _command(owner, world, published):
    with Session(owner) as db:
        pub = db.get(ControlProjectionPublication,UUID(published['publication_id']))
        run = db.get(SyncRun,pub.sync_run_id)
        lines = tuple(publication_service._line_input(row) for row in db.scalars(select(ControlProjectionLine)
            .where(ControlProjectionLine.publication_id==pub.id).order_by(ControlProjectionLine.sequence)))
        location = StockLocation(code='RACE-'+uuid4().hex,name='Synthetic publication/start race',
            location_type='region',owner_org_id=world.region,status='active')
        db.add(location); db.commit()
        return opening.StartOpeningStocktakeCommand(task_no='RACE-'+uuid4().hex,
            region_org_id=world.region,control_source_system_id=world.source,
            control_sync_run_id=run.id,control_sync_scope_key=run.scope_key,
            scopes=(opening.OpeningStocktakeScopeInput(world.region,location.id,world.actor.user_id),),
            control_lines=lines)


def publish_while_start_waits(owner, api, world, previous, arguments):
    """Publisher holds principal first; starter then reaches that same lock.

    Before the repair, starter already owns SourceSystem, and publication must
    wait for it: PostgreSQL reports 40P01. After repair publisher can complete;
    startup rechecks the now-replaced version and refuses without a partial task.
    The ledger must also remain outside unrelated deferred return-event checks.
    Hooks only schedule real service calls; no lock or database guard is mocked.
    """
    command = _command(owner,world,previous)
    before = _formal_stock(owner)[1:]
    publisher_ready, starter_at_principal = Event(), Event()
    pids = {}
    original_operator = configuration._operator_context
    original_principal = opening.lock_formal_principal_graph

    def operator(db,*args,**kwargs):
        result = original_operator(db,*args,**kwargs)
        if db.info.pop('pause_publisher',False):
            pids['publisher'] = db.scalar(text('SELECT pg_backend_pid()'))
            publisher_ready.set()
            assert starter_at_principal.wait(15), 'starter never reached principal lock'
            deadline = time.monotonic()+5
            with owner.connect() as observer:
                while time.monotonic()<deadline:
                    if pids['publisher'] in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pids['starter']}):
                        break
                    time.sleep(.02)
                else: raise AssertionError('opening never waited for the publisher principal')
        return result

    def principal(db,*args,**kwargs):
        if db.info.get('race_starter'):
            starter_at_principal.set()
        return original_principal(db,*args,**kwargs)

    def publish():
        with Session(owner) as db:
            db.execute(text("SET LOCAL statement_timeout='20s'"))
            db.info['pause_publisher'] = True
            result = publication_service.execute_control_publication(db,**arguments)
            db.commit()
            return result

    def start():
        assert publisher_ready.wait(15), 'publisher never acquired principal'
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout='20s'"))
            assert db.execute(text('SELECT current_user,session_user')).one()==('star_oam_api','star_oam_api')
            pids['starter'] = db.scalar(text('SELECT pg_backend_pid()'))
            actor = load_formal_principal(db,world.actor.user_id)
            db.info['race_starter'] = True
            try:
                opening.start_opening_stocktake(db,actor=actor,command=command,
                    idempotency_key=uuid4().hex,request_id=uuid4().hex)
            except opening.OpeningStocktakeError as exc:
                db.rollback()
                assert exc.code=='control_publication_not_admissible', exc.code
                return exc.code
            raise AssertionError('stale control batch unexpectedly started')

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(configuration,'_operator_context',operator)
        patch.setattr(opening,'lock_formal_principal_graph',principal)
        with ThreadPoolExecutor(max_workers=2) as pool:
            publisher, starter = pool.submit(publish), pool.submit(start)
            result = publisher.result(timeout=30)
            assert starter.result(timeout=30)=='control_publication_not_admissible'
    assert _formal_stock(owner)[1:]==before
    print('PG16 publisher-first/new-opening contention: publication commits; replaced startup refused without deadlock or partial task PASS',flush=True)
    return result


def assert_return_event_locks_retained(owner, api, world):
    """Every matching aggregate must still wait for the ledger and fail closed.

    Detached proof events are rolled back. No fake business fact is retained;
    observed blocking and SQLSTATE prove both the lock and the proof still run.
    """
    before = _formal_stock(owner)
    cases = (
        ('stock_operation_command_seal','0101 seal audit is detached'),
        ('stock_operation_order','0100 return order missing'),
        ('stock_operation_outbound','0103 detached physical departure'),
    )
    for aggregate, expected in cases:
        ready = Event(); pids = {}
        def commit_detached():
            with Session(api) as db:
                assert db.scalar(text('SELECT current_user'))=='star_oam_api'
                db.execute(text("SET LOCAL statement_timeout='15s'"))
                pids['worker'] = db.scalar(text('SELECT pg_backend_pid()'))
                append_audit_event(db,stream_key='material_request',actor_user_id=world.actor.user_id,
                    action='synthetic.detached_proof',aggregate_type=aggregate,aggregate_id=str(uuid4()),
                    request_id=uuid4().hex,before_jsonb={},after_jsonb={},occurred_at=datetime.now(timezone.utc))
                ready.set()
                try:
                    with pytest.raises(DBAPIError) as error: db.commit()
                    assert error.value.orig.sqlstate=='23514'
                    assert expected in str(error.value.orig)
                finally: db.rollback()
        with Session(owner) as holder, ThreadPoolExecutor(max_workers=1) as pool:
            holder_pid = holder.scalar(text('SELECT pg_backend_pid()'))
            holder.execute(text("SELECT id FROM inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE"))
            worker = pool.submit(commit_detached)
            try:
                assert ready.wait(10), 'detached event did not reach commit'
                deadline = time.monotonic()+5
                with owner.connect() as observer:
                    while time.monotonic()<deadline:
                        if holder_pid in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pids['worker']}): break
                        if worker.done(): break
                        time.sleep(.02)
                    else: raise AssertionError('return event never waited for ledger')
                    assert not worker.done(), 'return event skipped the ledger'
            finally: holder.rollback()
            worker.result(timeout=20)
    assert _formal_stock(owner)==before
    print('PG16 matching return/seal/outbound audits: ledger waits observed, detached evidence refused, stock unchanged PASS',flush=True)
