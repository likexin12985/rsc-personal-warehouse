"""Current signed-publication recount lifecycle on explicitly supplied PG16 engines.

Synthetic master data and source transport only; no clock, authorization,
business-service, SQL-guard or commit overrides. This is a bounded local check,
not an entry point to the GitHub-only destructive release gate.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
from threading import Event
import time
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Role
from app.inventory_models import StockBalance
from app.stocktake_models import FormalStocktakeTask, FormalStocktakeScope, StocktakeDifference, StocktakeRound
from app.formal_services import opening_stocktake_count as count
from app.formal_services import opening_stocktake_review as review
from app.formal_services import opening_stocktake_recount as recount
from app.formal_services import opening_stocktake_finalize as final
from app.formal_services import opening_control_reconciliation as reconciliation
from pg16_opening_publication_fixture import prepare_stocktake_inventory
from pg16_release_gate_diagnostics import run_with_sanitized_database_diagnostics
from test_formal_access import assign, make_organization, make_user


def snapshot(owner):
    """Every row in the opening/count/recount/review/post/close and ledger graph."""
    from pg16_opening_fixture_gate import all_reconciliation_facts
    result = all_reconciliation_facts(owner)
    with owner.connect() as db:
        names = db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                                "AND (tablename LIKE 'stocktake\\_%' ESCAPE '\\' "
                                "OR tablename IN ('stock_accounts','inventory_serials')) ORDER BY tablename"))
        for name in names:
            assert name.replace('_', '').isalnum()
            result[name] = db.scalar(text("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),"
                                         "'[]'::jsonb) FROM public." + name + " t"))
    return result


def _round_facts(owner, round_id):
    rows = snapshot(owner)
    return {name: [row for row in values if row.get('round_id') == str(round_id)
                  or name == 'stocktake_rounds' and row['id'] == str(round_id)]
            for name, values in rows.items() if name.startswith('stocktake_')}


def _write(api, service, actor_id, command, key):
    with Session(api, expire_on_commit=False) as db:
        result = _invoke(db, service, load_formal_principal(db, actor_id), command, key)
        db.commit()
        return result


def _invoke(db, service, actor, command, key):
    return run_with_sanitized_database_diagnostics(db.get_bind(), lambda: service(
        db, actor=actor, command=command, idempotency_key=key, request_id=key),
        replace_unlinked_database_failure_when=None)


def _wait_blocked(owner, waiter_pid, blocker_pid, future):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if future.done():
            future.result()
            raise AssertionError('contender finished without observed database lock wait')
        with owner.connect() as db:
            blockers = db.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiter_pid})
        if blocker_pid in blockers:
            return
        time.sleep(.025)
    raise AssertionError('expected PostgreSQL lock wait not observed')


def _concurrent_replay(owner, api, service, actor_id, command, key):
    ready = Event()
    allow_commit = Event()
    pid = []
    def contender():
        with Session(api, expire_on_commit=False) as db:
            db.execute(text("SET LOCAL statement_timeout='25s'"))
            actor = load_formal_principal(db, actor_id)
            pid.append(db.scalar(text('SELECT pg_backend_pid()')))
            ready.set()
            result = _invoke(db, service, actor, command, key)
            # Freeze the committed baseline before the contender can commit;
            # its possible extra rows must not become part of that baseline.
            assert allow_commit.wait(15)
            db.commit()
            return result
    # Dispose/rollback winner before waiting for worker shutdown on failure.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(api, expire_on_commit=False) as db:
            blocker = db.scalar(text('SELECT pg_backend_pid()'))
            first = _invoke(db, service, load_formal_principal(db, actor_id), command, key)
            future = pool.submit(contender)
            assert ready.wait(5)
            _wait_blocked(owner, pid[0], blocker, future)
            db.commit()
            expected = snapshot(owner)
            allow_commit.set()
        second = future.result(timeout=30)
    assert not first.replayed and second.replayed
    assert replace(second, replayed=False) == first
    assert snapshot(owner) == expected
    return first


def _start_http(owner, api, fixture, actor_id, location):
    from app.config import get_settings
    from app.database import get_db
    from app.models import AuthSession
    from app.routers.formal_opening_stocktake import router
    from app.security import create_access_token
    with Session(owner) as db:
        now = db.scalar(text('SELECT clock_timestamp()'))
        session = AuthSession(user_id=actor_id, refresh_token_hash=uuid4().hex+uuid4().hex,
            client_type='web', device_id=uuid4().hex, ip_address='hmac:1:'+'a'*64,
            created_at=now, expires_at=now+timedelta(hours=1))
        db.add(session); db.flush()
        token = create_access_token(actor_id, session.id)
        db.commit()
    app = FastAPI(); app.include_router(router, prefix='/api')
    def database():
        with Session(api) as db:
            yield db
    app.dependency_overrides[get_db] = database
    key = uuid4().hex
    payload = dict(publication_id=str(fixture['control_publication_id']),
        region_org_id=str(fixture['region_org_id']), task_no='PG16-RECOUNT-'+key,
        scopes=[dict(owner_org_id=str(fixture['region_org_id']), location_id=str(location),
                     assignee_user_id=actor_id, freeze_mode='hard')])
    # Settings selects the real production JWT dependency. No auth override.
    with patch('app.dependencies.get_settings', return_value=get_settings().model_copy(
            update={'environment': 'production'})), TestClient(app) as client:
        headers = {'Authorization': 'Bearer '+token, 'Idempotency-Key': key, 'X-Request-ID': key}
        response = client.post('/api/v1/stocktakes/opening/from-publication', json=payload, headers=headers)
        assert response.status_code == 200, response.text
        first = response.json()
        before = snapshot(owner)
        again = client.post('/api/v1/stocktakes/opening/from-publication', json=payload, headers=headers)
        assert again.status_code == 200 and again.json() == dict(first, replayed=True)
        assert snapshot(owner) == before
    with Session(api) as db:
        scope = db.scalars(select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == UUID(first['task_id']))).one()
        assert first['control_line_count'] == 1 and first['snapshot_line_count'] == 1
        return UUID(first['task_id']), UUID(first['initial_round_id']), scope.id


def _count_command(fixture, task, round_id, scope, quantity):
    return count.SubmitOpeningStocktakeScopeCountCommand(task_id=task, round_id=round_id,
        scope_id=scope, physical_observations=(count.OpeningPhysicalObservationInput(
            material_id=fixture['material_id'], material_identifier_raw=fixture['material_sku_code'],
            material_identifier_type='sku_code', condition_code='new', availability_bucket='available',
            counted_qty=Decimal(quantity), count_method='manual'),), zero_confirmed=False)


def _review_command(api, task, round_id, decision):
    with Session(api) as db:
        differences = list(db.scalars(select(StocktakeDifference).where(
            StocktakeDifference.task_id == task, StocktakeDifference.round_id == round_id)))
        assert len(differences) == 2
        return review.SubmitOpeningStocktakeReviewCommand(task_id=task, round_id=round_id,
            decision=decision, items=tuple(review.OpeningStocktakeReviewItemInput(difference_id=row.id,
                decision='pending_verification' if row.difference_type == 'control_unassigned' else
                         'recount' if decision == 'recount' else 'accept_for_posting',
                comment='Synthetic physical evidence; external control stays independent') for row in differences),
            comment='Synthetic recount acceptance')


def run(engines):
    owner, api, edge = (engines[key] for key in ('star_oam_migrator', 'star_oam_api', 'edge_inbox'))
    with Session(owner) as db:
        hq = make_organization(db, name='Synthetic recount HQ')
        region = make_organization(db, name='Synthetic recount region', parent=hq)
        other_region = make_organization(db, name='Synthetic unrelated region', parent=hq)
        roles = {row.code: row for row in db.scalars(select(Role))}
        users = {}
        for key, org, role in (('admin', hq, 'admin'), ('approver', hq, 'admin'),
                ('manager', region, 'provincial_manager'), ('counter', region, 'provincial_manager'),
                ('revoked', region, 'provincial_manager'), ('outsider', other_region, 'provincial_manager')):
            user, _ = make_user(db, org, name='Synthetic recount '+key)
            assign(db, user, roles[role], scope_type='national' if role == 'admin' else 'organization',
                   scope_id='*' if role == 'admin' else str(org.id))
            users[key] = user.id
        db.commit()
    fixture = run_with_sanitized_database_diagnostics(owner, lambda: prepare_stocktake_inventory(
        owner, edge, actor_user_id=users['admin'], assignee_user_id=users['manager']),
        replace_unlinked_database_failure_when=None)
    task, initial, scope = _start_http(owner, api, fixture, users['manager'], fixture['recount_location_id'])
    first_count = _count_command(fixture, task, initial, scope, '2')
    initial_result = _concurrent_replay(owner, api, count.submit_opening_stocktake_scope_count,
        users['manager'], first_count, uuid4().hex)
    assert initial_result.round_sealed and initial_result.task_status == 'submitted'
    regional = _write(api, review.submit_opening_region_review, users['manager'],
        _review_command(api, task, initial, 'recount'), uuid4().hex)
    assert regional.resulting_task_status == 'recount_required'
    predecessor = _round_facts(owner, initial)
    command = recount.OpenOpeningStocktakeRecountCommand(task_id=task, source_round_id=initial,
        assignments=(recount.OpeningStocktakeRecountScopeAssignmentInput(scope_id=scope,
                     assignee_user_id=users['counter']),), reason='Synthetic independent physical recount')
    before = snapshot(owner)
    with pytest.raises(recount.OpeningStocktakeRecountError) as cross_region:
        _write(api, recount.open_opening_stocktake_recount, users['manager'], replace(command,
            assignments=(recount.OpeningStocktakeRecountScopeAssignmentInput(scope_id=scope,
                assignee_user_id=users['outsider']),)), uuid4().hex)
    assert cross_region.value.code == 'opening_recount_assignee_scope_forbidden'
    assert snapshot(owner) == before
    key = uuid4().hex
    opened = _write(api, recount.open_opening_stocktake_recount, users['manager'], command, key)
    assert opened.next_round_no == 2 and opened.resulting_task_status == 'counting'
    after = snapshot(owner)
    replay = _write(api, recount.open_opening_stocktake_recount, users['manager'], command, key)
    assert replay.replayed and replace(replay, replayed=False) == opened
    assert snapshot(owner) == after and _round_facts(owner, initial) == predecessor
    second_count = _count_command(fixture, task, opened.next_round_id, scope, '3')
    for wrong in ('manager', 'outsider'):
        before = snapshot(owner)
        with pytest.raises(count.OpeningStocktakeCountError) as caught:
            _write(api, count.submit_opening_stocktake_scope_count, users[wrong], second_count, uuid4().hex)
        assert caught.value.category == 'forbidden'
        assert snapshot(owner) == before
    second_key = uuid4().hex
    second_result = _concurrent_replay(owner, api, count.submit_opening_stocktake_scope_count,
        users['counter'], second_count, second_key)
    assert second_result.round_sealed and second_result.task_status == 'submitted'
    with api.connect() as db:
        for round_id in (initial, opened.next_round_id):
            assert db.scalar(text('SELECT COUNT(*) FROM stocktake_scope_count_completions '
                'WHERE task_id=:task AND round_id=:round'), {'task': task, 'round': round_id}) == 1
            assert db.scalar(text('SELECT COUNT(*) FROM stocktake_round_submissions '
                'WHERE task_id=:task AND round_id=:round'), {'task': task, 'round': round_id}) == 1
    before = snapshot(owner)
    with pytest.raises(count.OpeningStocktakeCountError) as conflict:
        _write(api, count.submit_opening_stocktake_scope_count, users['counter'],
            _count_command(fixture, task, opened.next_round_id, scope, '4'), second_key)
    assert conflict.value.code == 'opening_count_idempotency_conflict'
    assert snapshot(owner) == before and _round_facts(owner, initial) == predecessor
    print('PG16 actual successor recount: selected-publication JWT start/retry, both count lock waits, reassignment and forbidden/conflicting writes PASS', flush=True)
    approval = _review_command(api, task, opened.next_round_id, 'approve')
    assert _write(api, review.submit_opening_region_review, users['manager'], approval,
                  uuid4().hex).resulting_task_status == 'hq_review'
    assert _write(api, review.submit_opening_headquarters_review, users['admin'], approval,
                  uuid4().hex).resulting_task_status == 'approved'
    with Session(api) as db:
        version = db.get(FormalStocktakeTask, task).version
    posted = _concurrent_replay(owner, api, final.post_approved_opening_stocktake, users['admin'],
        final.PostOpeningStocktakeCommand(task_id=task, expected_version=version), uuid4().hex)
    assert posted.total_quantity == Decimal('3') and posted.pending_control_difference_count == 1
    with Session(api) as db:
        assert db.get(StockBalance, fixture['recount_account_id']).quantity == Decimal('3')
        assert db.scalar(text('SELECT COUNT(*) FROM stocktake_postings WHERE task_id=:task'),
                         {'task': task}) == 1
        assert db.scalar(text('SELECT COUNT(*) FROM inventory_transactions WHERE id=:id'),
                         {'id': posted.inventory_transaction_id}) == 1
    close = final.CloseOpeningStocktakeCommand(task_id=task, expected_version=posted.task_version)
    before = snapshot(owner)
    with pytest.raises(final.OpeningStocktakeFinalizeError) as blocked:
        _write(api, final.close_posted_opening_stocktake, users['admin'], close, uuid4().hex)
    assert blocked.value.code == 'opening_close_reconciliation_pending' and snapshot(owner) == before
    run = _write(api, reconciliation.start_opening_control_reconciliation, users['admin'],
        reconciliation.StartOpeningControlReconciliationCommand(task_id=task,
            expected_task_version=posted.task_version), uuid4().hex)
    with Session(api) as db:
        detail = reconciliation.opening_control_reconciliation_detail(db,
            actor=load_formal_principal(db, users['manager']), reconciliation_run_id=run.reconciliation_run_id)
    explained = _write(api, reconciliation.explain_opening_control_reconciliation, users['manager'],
        reconciliation.ExplainOpeningControlReconciliationCommand(reconciliation_run_id=run.reconciliation_run_id,
            expected_version=run.version, items=tuple(reconciliation.OpeningControlExplanationInput(
                reconciliation_item_id=item.reconciliation_item_id, expected_version=item.version,
                explanation='Second physical round counts three; external zero is retained',
                evidence_reference='synthetic-recount:'+str(opened.next_round_id)) for item in detail.items)), uuid4().hex)
    approved = _write(api, reconciliation.approve_opening_control_reconciliation, users['approver'],
        reconciliation.ApproveOpeningControlReconciliationCommand(reconciliation_run_id=run.reconciliation_run_id,
            expected_version=explained.version, comment='Independent synthetic recount evidence review'), uuid4().hex)
    assert approved.status == 'approved' and approved.resolved_item_count == 1
    after = snapshot(owner)
    for name in ('inventory_transactions', 'inventory_movements', 'inventory_movement_serials',
                 'inventory_ledger_heads', 'stock_balances', 'serial_current_positions'):
        assert before[name] == after[name]
    closed = _write(api, final.close_posted_opening_stocktake, users['admin'], close, uuid4().hex)
    assert closed.resulting_task_status == 'closed'
    assert _round_facts(owner, initial) == predecessor
    with Session(api) as db:
        assert db.get(StocktakeRound, initial).status == 'submitted'
        assert db.get(FormalStocktakeTask, task).status == 'closed'
        assert not db.scalar(text("SELECT EXISTS(SELECT 1 FROM inventory_freezes WHERE task_id=:id AND status='active')"), {'id': task})
    print('PG16 actual successor recount: latest quantity +3 posted once under contention; independent reconciliation leaves stock intact; close retains predecessor PASS', flush=True)
    _revocation_wait(owner, api, fixture, users['revoked'])
    from app.database_security import validate_production_database_security
    from app.edge_database_security import verify_edge_database_boundary
    validate_production_database_security(api, expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
    verify_edge_database_boundary(edge)
    with owner.connect() as revision_connection:
        migration_head=revision_connection.scalar(text('SELECT version_num FROM alembic_version'))
    return dict(status='passed', scope='local-native-pg16-actual-recount', migrationHead=migration_head,
        actualPostgreSQL16=True, selectedPublicationJwt=True, startReplayReadOnly=True,
        initialQuantity='2', recountQuantity='3', postedQuantity='3', actualRounds=2,
        predecessorSha256=hashlib.sha256(json.dumps(predecessor, sort_keys=True).encode()).hexdigest(),
        initialFactsPreserved=True, reassignsCounter=True, wrongAssigneeAndCrossRegionRefused=True,
        crossRegionRecountAssignmentRefused=True,
        countConflictingRetryRefused=True, observedSameKeyLockWaits=3, postReplayNoDuplicates=True,
        independentReconciliationPreservesStock=True, closed=True, authorizationRevocationLockWait=True,
        apiAndEdgeBoundaries=True, graphTables=len(snapshot(owner)), fullReleaseGate=False,
        githubReleaseGate=False, productionAcceptance=False)


def _revocation_wait(owner, api, fixture, user_id):
    task, round_id, scope = _start_http(owner, api, fixture, user_id, fixture['dynamic_peer_location_id'])
    command = _count_command(fixture, task, round_id, scope, '1')
    # Capture a real currently valid principal before the competing owner
    # transaction revokes it. Only authorization facts change in this probe.
    with Session(api) as db:
        actor = load_formal_principal(db, user_id)
    before = snapshot(owner)
    ready = Event(); pid = []
    def counter():
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout='25s'"))
            pid.append(db.scalar(text('SELECT pg_backend_pid()'))); ready.set()
            _invoke(db, count.submit_opening_stocktake_scope_count, actor, command, uuid4().hex)
            db.commit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with owner.begin() as db:
            blocker = db.scalar(text('SELECT pg_backend_pid()'))
            db.execute(text('UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id'), {'id': user_id})
            db.execute(text("UPDATE role_assignments SET status='revoked',revoked_at=clock_timestamp(),"
                "revoked_by=:id,updated_at=clock_timestamp() WHERE user_id=:id"), {'id': user_id})
            future = pool.submit(counter)
            assert ready.wait(5)
            _wait_blocked(owner, pid[0], blocker, future)
        with pytest.raises(count.OpeningStocktakeCountError) as rejected:
            future.result(timeout=30)
        assert rejected.value.category == 'forbidden'
    assert snapshot(owner) == before
    print('PG16 count waits on authorization writer: committed revocation rejects stale actor; full business graph unchanged PASS', flush=True)
