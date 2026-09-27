"""Real role revocation, malformed first postings and distinct-receipt races."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event
import time
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import RoleAssignment
from app.inventory_models import CustodyAssignment, StockAccount, StockBalance
from app.formal_services import inventory_posting as posting
from app.formal_services.inventory_query import InventoryReadError
from pg16_stock_return_account_admission_gate import (
    commands, recovery, document, load_formal_principal, _checkpoint,
    prepare_departure_worlds, _prepare_return, departures, parcels, receipts, inbound, snapshot,
)


def distinct_race(engine, requests):
    held, started, release = Event(), Event(), Event()
    pids = {}
    def worker(index):
        with Session(engine) as db:
            pids[index] = db.scalar(text('SELECT pg_backend_pid()'))
            if index == 0:
                posting._lock_inventory_ledger_head_for_atomic_batch(db); held.set()
                assert release.wait(45)
            else:
                started.set()
            try:
                result = commands.execute_return_inbound(db, **requests[index])
                _checkpoint(db); db.commit(); return result
            except InventoryReadError as error:
                db.rollback(); return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker, 0)
        assert held.wait(20)
        second = pool.submit(worker, 1)
        try:
            assert started.wait(20)
            deadline = time.monotonic() + 30
            with engine.connect() as observer:
                while time.monotonic() < deadline:
                    blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid':pids[1]})
                    if pids[0] in blockers:
                        break
                    assert not second.done(), 'second command skipped the held ledger lock'
                    time.sleep(.05)
                else:
                    raise AssertionError('exact ledger contention not observed')
        finally:
            release.set()
        results = first.result(timeout=180), second.result(timeout=180)
    assert pids[0] != pids[1]
    return results


def assert_first_account_boundaries(api, owner):
    worlds = prepare_departure_worlds(api, owner, precreate_receiving_accounts=False, with_lots=True)
    for world in worlds.values():
        pending = _prepare_return(api, owner, world)
        with Session(api) as db:
            departures.execute_outbound(db, actor=load_formal_principal(db, pending['user_id']),
                work_order_id=pending['work_order_id'], operation_id=pending['operation_id'], request=pending['value'])
            _checkpoint(db); db.commit()
    origins = parcels.parcel_candidates(api, worlds)
    for candidate in origins.values():
        with Session(api) as db:
            command, _ = parcels._command(db, candidate, '1')
            parcels.commands.execute_shipment(db, **parcels._coordinates(db, candidate), request=command)
            _checkpoint(db); db.commit()
    selected = receipts.receipt_candidates(api, tuple(origins.values()))
    accepted = []
    for quantity in ('.375', '.625'):
        with Session(api) as db:
            context = receipts._context(db, selected['quantity'])
            request = context.request.model_copy(update={'lines': (context.request.lines[0].model_copy(
                update={'accepted_qty': Decimal(quantity)}),)})
            result = receipts._execute(db, context, receipts._command(db, context, request=request))
            db.commit(); accepted.append(result.receipt_id)
    with Session(api) as db:
        context = receipts._context(db, selected['serial'])
        result = receipts._execute(db, context, receipts._command(db, context))
        db.commit(); serial_receipt = result.receipt_id

    denied = []
    for receipt_id in (accepted[0], serial_receipt):
        with Session(api) as db:
            args, plan = inbound._args(db, receipt_id)
        for change in ('role_expired', 'custody_expired'):
            # Role restoration advances authorization version. Refresh the
            # plan first so custody expiry cannot pass via a stale actor.
            with Session(api) as db:
                args, plan = inbound._args(db, receipt_id)
            with Session(owner) as db:
                if change == 'role_expired':
                    rows = tuple(db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == args['actor'].user_id)))
                else:
                    rows = (db.get(CustodyAssignment, plan['target_custody_assignment_id']),)
                assert rows
                old = {row.id: row.valid_to for row in rows}
                for row in rows:
                    row.valid_to = datetime.now(timezone.utc)
                db.commit()
            before = snapshot(api)
            try:
                with Session(api) as db:
                    with pytest.raises((InventoryReadError, posting.InventoryPostingError)):
                        commands.execute_return_inbound(db, **args)
                    db.rollback()
                assert snapshot(api) == before
            finally:
                with Session(owner) as db:
                    model = RoleAssignment if change == 'role_expired' else CustodyAssignment
                    for identifier, value in old.items():
                        db.get(model, identifier).valid_to = value
                    db.commit()
            denied.append(change)
        for change in ('hash', 'command', 'plan', 'quantity', 'posting_link', 'outbox', 'audit',
                       *(('serial',) if receipt_id == serial_receipt else ())):
            before = snapshot(api)
            with Session(api) as db:
                args, plan = inbound._args(db, receipt_id)
                listener = inbound._corrupt(db, change)
                try:
                    with patch.object(commands, 'inbound_result', lambda _db, *, actor, fact, **_: document(fact)), \
                            (patch.object(commands, 'append_audit_event', return_value=None) if change == 'audit' else nullcontext()):
                        with pytest.raises(DBAPIError) as failure:
                            commands.execute_return_inbound(db, **args); _checkpoint(db)
                        assert failure.value.orig.sqlstate == '23514', str(failure.value.orig)
                        assert any(message in str(failure.value.orig) for message in (
                            '0106', '0111', 'API stock account insert must terminate'))
                finally:
                    event.remove(db, 'before_flush', listener); db.rollback()
            assert snapshot(api) == before
            denied.append(change)
    requests, plans = [], []
    with Session(api) as db:
        for identifier in accepted:
            args, plan = inbound._args(db, identifier)
            requests.append(args); plans.append(plan)
        target = UUID(plans[0]['lines'][0]['target_account_id'])
        assert plans[1]['lines'][0]['target_account_id'] == str(target)
        assert plans[0]['ledger_cursor'] == plans[1]['ledger_cursor']
        assert db.get(StockAccount, target) is None
        account_count = db.scalar(select(text('count(*)')).select_from(StockAccount))
    first, rejected = distinct_race(api, requests)
    assert isinstance(first, dict) and rejected == 'stock_return_inbound_plan_changed'
    with Session(api) as db:
        assert db.scalar(select(text('count(*)')).select_from(StockAccount)) == account_count + 1
        assert db.get(StockBalance, target).quantity == Decimal('.375')
        assert recovery.lookup_return_inbound_request(db, actor=requests[1]['actor'], receipt_id=accepted[1],
            request_id=requests[1]['request_id']) is None
        fresh_args, fresh_plan = inbound._args(db, accepted[1])
        assert fresh_plan['plan_hash'] != plans[1]['plan_hash']
        assert fresh_plan['lines'][0]['target_account_id'] == str(target)
        commands.execute_return_inbound(db, **fresh_args); _checkpoint(db); db.commit()
        assert db.scalar(select(text('count(*)')).select_from(StockAccount)) == account_count + 1
        balance = db.get(StockBalance, target)
        assert balance.quantity == Decimal('1') and balance.version == 2
    print('PG16 first account: revoked authority, 15 malformed graphs, distinct receipt race and explicit fresh preview PASS', flush=True)
    return dict(currentAuthorityDenials=4, malformedGraphDenials=15, fullRollback=True,
        distinctReceiptRace=True, staleSecondPlanRejected=True, explicitFreshPreviewReusesOneAccount=True)
