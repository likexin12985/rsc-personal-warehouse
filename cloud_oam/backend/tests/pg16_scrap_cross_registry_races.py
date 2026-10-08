"""A legal old correction approval competes with a new original-request seal.

Both services use one real raw key but different request IDs. This isolates
the shared token/alias boundary from actor/request uniqueness. No inverse is
invented for an ineligible scrap root; coverage here is explicitly approval.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import bound_commands as legacy
from app.formal_services.stock_scrap import legacy_request_lookup
from pg16_scrap_seal_persistence import snapshot, only_closure_changed, register
from pg16_scrap_seal_races import close, lookup, setup


def require_unknown(call, *, legacy_reader=False):
    try:
        call()
    except InventoryReadError as error:
        expected = ('loss_request_binding_outcome_unknown' if legacy_reader
            else 'stock_scrap_recovery_request_outcome_unknown')
        assert error.code == expected and error.status_code == 503, (error.code, error.status_code)
        return error.code
    raise AssertionError('a different command sharing the raw key returned a recoverable result')


def race(context, approval, *, seal_first):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    actor_id = context['admin_id']
    original_actor, original, _ = context['scrap_lookup_cases'][0]
    assert original_actor == actor_id and original.source.kind == 'original'
    command = original.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=approval.idempotency_key))
    assert command.request_id != approval.request_id and command.idempotency_key == approval.idempotency_key
    direction = 'new_seal_first' if seal_first else 'legacy_approval_first'
    before = snapshot(owner)
    # Each independently specified request has a valid retained source, and
    # neither key has any prior fact. A miss is not used as a replay permit.
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, actor_id)
        for value in (lookup(db, actor, command), legacy_request_lookup.lookup(db, actor=actor, request=approval)):
            assert value['request_state'] == 'not_found' and value['retry_allowed'] is False
    assert snapshot(owner) == before
    started, settled, release = Event(), Event(), Event()
    shared = {}

    def opponent():
        with Session(api) as db:
            shared['pid'] = setup(db)
            started.set()
            try:
                actor = load_formal_principal(db, actor_id)
                if seal_first:
                    # Exercise the unchanged old service AND real key
                    # registrar. Only its actual COMMIT may reject this case.
                    legacy.approve(db, actor=actor, request=approval)
                    try:
                        db.commit()
                    except DBAPIError as error:
                        assert error.orig.sqlstate == '23514', error.orig
                        assert 'sealed key or request has another registry fact' in error.orig.diag.message_primary, error.orig
                        assert 'rsc_assert_scrap_seal_absence_0165' in (error.orig.diag.context or ''), error.orig
                        result = dict(phase='commit', sqlstate=error.orig.sqlstate,
                            message=error.orig.diag.message_primary)
                        db.rollback()
                    else:
                        raise AssertionError('old approval committed using a new sealed raw key')
                else:
                    code = require_unknown(lambda: close(db, actor, command))
                    db.rollback()
                    result = dict(phase='service', code=code, statusCode=503)
                settled.set()
                assert release.wait(timeout=600), 'winner snapshot not collected'
                return result
            finally:
                settled.set()

    pool = ThreadPoolExecutor(max_workers=1)
    stored = None
    try:
        with Session(api) as winner:
            winner_pid = setup(winner)
            actor = load_formal_principal(winner, actor_id)
            if seal_first:
                answer = close(winner, actor, command)
                assert answer['request_state'] == 'sealed' and answer['retry_allowed'] is False
                stored = register(winner, actor_id, command)
                assert not stored['created'] and stored['seal']['id'] == answer['seal']['seal_id']
            else:
                answer = legacy.approve(winner, actor=actor, request=approval)
            future = pool.submit(opponent)
            try:
                assert started.wait(timeout=30), 'opponent did not open its API connection'
                assert winner_pid != shared['pid']
                deadline = time.monotonic() + 30
                with owner.connect() as observer:
                    while True:
                        if future.done():
                            future.result()
                            raise AssertionError('opponent finished before winner released the ledger')
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), dict(pid=shared['pid']))
                        relation = observer.scalar(text('SELECT EXISTS(SELECT 1 FROM pg_locks '
                            "WHERE pid=:pid AND relation='public.inventory_ledger_heads'::regclass "
                            "AND mode='RowShareLock' AND granted)"), dict(pid=shared['pid']))
                        if winner_pid in blockers and relation:
                            break
                        if time.monotonic() >= deadline:
                            raise AssertionError('cross-registry API ledger contention not observed')
                        time.sleep(0.02)
                print(direction + ': old approval/new seal share raw key; actual API ledger wait PASS', flush=True)
                winner.commit()
            finally:
                winner.rollback()
        assert settled.wait(timeout=600), 'opponent did not observe committed winner'
        if future.done():
            future.result()
        expected = snapshot(owner)
        release.set()
        loser = future.result(timeout=600)
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)
    after = snapshot(owner)
    assert after == expected, 'losing cross-registry transaction changed retained winner facts'
    assert after['stock_scrap_request_key_bindings'] == before['stock_scrap_request_key_bindings']
    if seal_first:
        only_closure_changed(before, after)
        for name in ('stock_scrap_request_seals', 'audit_events'):
            assert len(after[name]) == len(before[name]) + 1
    else:
        assert after['stock_scrap_request_seals'] == before['stock_scrap_request_seals']
        for name in ('stock_loss_correction_decisions', 'stock_loss_request_key_bindings'):
            assert len(after[name]) == len(before[name]) + 1
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, actor_id)
        if seal_first:
            assert lookup(db, actor, command) == answer
            require_unknown(lambda: legacy_request_lookup.lookup(db, actor=actor, request=approval), legacy_reader=True)
        else:
            found = legacy_request_lookup.lookup(db, actor=actor, request=approval)
            assert found['request_state'] == 'found' and found['result'] == answer and found['retry_allowed'] is False
            require_unknown(lambda: lookup(db, actor, command))
    assert snapshot(owner) == after
    proof = dict(legacyAction='approve_loss_correction', newAction='seal_original_scrap',
        sameRawKey=True, differentRequestIds=True, sameActor=True, apiConnections=2,
        winnerBackendPid=winner_pid, waitingBackendPid=shared['pid'], ledgerLockWaitObserved=True,
        attemptsPerParticipant=1, loser=loser, databaseEqualsWinnerSnapshot=True,
        winnerReadOnlyVerified=True, differentCommandReadOnlyUnknown=True,
        newSealsCommitted=1 if seal_first else 0, legacyApprovalsCommitted=0 if seal_first else 1)
    context.setdefault('cross_registry_seal_races', {})[direction] = proof
    if seal_first:
        context.setdefault('cross_registry_seal_cases', []).append((actor_id, command, stored))
    print(direction + ': unique durable result, loser rejected, both READ ONLY outcomes verified PASS', flush=True)
    return answer


def approve_with_cross_registry_races(context, approval):
    # The first race's old approval must roll back. The second is the actual
    # independent approval consumed by the subsequent corrected scrap flow.
    other = approval.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
    race(context, other, seal_first=True)
    return race(context, approval, seal_first=False)
