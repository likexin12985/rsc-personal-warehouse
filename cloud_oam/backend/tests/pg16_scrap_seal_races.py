"""Deterministic winner ordering with two real API transactions and lock proof."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import request_seals, request_lookup, recovery_lookup
from app.stock_scrap_schemas import ScrapExecute, ScrapRequestSeal, ScrapRequestLookup
from app.stock_scrap_recovery_schemas import ScrapRecoveryRequestSeal, ScrapRecoveryRequestLookup
from pg16_scrap_closed_execution import write
from pg16_scrap_seal_persistence import register, snapshot, only_closure_changed
from pg16_scrap_seal_sources import arguments


def close(db, actor, command):
    model = ScrapRequestSeal if type(command) is ScrapExecute else ScrapRecoveryRequestSeal
    return request_seals.seal(db, actor=actor, request=model(operator_person_id=actor.person_id, original=command))


def lookup(db, actor, command):
    original = type(command) is ScrapExecute
    model = ScrapRequestLookup if original else ScrapRecoveryRequestLookup
    handler = request_lookup.lookup if original else recovery_lookup.lookup
    return handler(db, actor=actor, request=model(operator_person_id=actor.person_id, original=command))


def setup(db):
    db.execute(text("SET LOCAL lock_timeout='600s'"))
    db.execute(text("SET LOCAL statement_timeout='600s'"))
    assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
    return db.scalar(text('SELECT pg_backend_pid()'))


def race(context, *, actor_id, command, seal_first):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    kind = arguments(command)['kind']
    direction = 'seal_first' if seal_first else 'execute_first'
    key = kind + ':' + direction
    proofs = context.setdefault('seal_race_proofs', {})
    assert key not in proofs, 'one exact controlled contention per stage/direction'
    before = snapshot(owner)
    started = Event()
    settled = Event()
    release = Event()
    shared = {}

    def opponent():
        with Session(api) as db:
            shared['pid'] = setup(db)
            started.set()
            actor = load_formal_principal(db, actor_id)
            try:
                try:
                    answer = write(db, actor=actor, command=command) if seal_first else close(db, actor, command)
                    assert not seal_first, 'a closed request executed after its seal committed'
                    outcome = dict(state='found', answer=answer)
                except InventoryReadError as error:
                    db.rollback()
                    prefix = 'stock_scrap' if kind in ('original', 'correction') else 'stock_scrap_recovery'
                    assert seal_first and error.code == prefix + '_request_sealed' and error.status_code == 409, error.code
                    outcome = dict(state='rejected', code=error.code)
                settled.set()
                assert release.wait(timeout=600), 'winner snapshot was not collected'
                if outcome['state'] == 'found':
                    db.commit()
                return outcome
            finally:
                settled.set()  # expose an early exception without hiding it behind a wait

    pool = ThreadPoolExecutor(max_workers=1)
    future = None
    stored = None
    try:
        with Session(api) as winner:
            winner_pid = setup(winner)
            actor = load_formal_principal(winner, actor_id)
            answer = close(winner, actor, command) if seal_first else write(winner, actor=actor, command=command)
            if seal_first:
                assert answer['request_state'] == 'sealed'
                # Preserve the real DB row/payload used by existing negative
                # audit tests. This is an actual exact repeat, not fabricated
                # created metadata or a second creation path.
                stored = register(winner, actor_id, command)
                assert not stored['created'] and stored['seal']['id'] == answer['seal']['seal_id']
            winner.flush()
            future = pool.submit(opponent)
            try:
                assert started.wait(timeout=30), 'opponent did not open its API connection'
                assert winner_pid != shared['pid']
                deadline = time.monotonic() + 30
                with owner.connect() as observer:
                    while True:
                        if future.done():
                            future.result()
                            raise AssertionError('opponent finished before the winner released the ledger')
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), dict(pid=shared['pid']))
                        ledger_relation = observer.scalar(text('SELECT EXISTS(SELECT 1 FROM pg_locks '
                            "WHERE pid=:pid AND relation='public.inventory_ledger_heads'::regclass "
                            "AND mode='RowShareLock' AND granted)"), dict(pid=shared['pid']))
                        if winner_pid in blockers and ledger_relation:
                            break
                        if time.monotonic() >= deadline:
                            raise AssertionError('no observed API ledger lock contention')
                        time.sleep(0.02)
                print(kind + ' ' + direction + ': two API connections, actual ledger lock wait PASS', flush=True)
                winner.commit()
            finally:
                # Also release on an assertion/timeout so the blocked worker
                # can finish and expose its own failure; never replay it.
                winner.rollback()
        assert settled.wait(timeout=600), 'opponent did not finish observing the committed winner'
        if future.done():
            future.result()
        # Read the whole committed database through its existing owner role;
        # do not grant the API extra table reads just to take a test snapshot.
        # The opponent cannot COMMIT until this baseline is captured.
        expected = snapshot(owner)
        release.set()
        loser = future.result(timeout=600)
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)
    after = snapshot(owner)
    assert after == expected, 'losing transaction changed committed winner facts'
    if seal_first:
        only_closure_changed(before, after)
        for name in ('stock_scrap_request_seals', 'audit_events'):
            assert len(after[name]) == len(before[name]) + 1
        assert loser['state'] == 'rejected'
    else:
        assert after['stock_scrap_request_seals'] == before['stock_scrap_request_seals']
        assert loser['answer']['request_state'] == 'found'
        assert loser['answer']['result'] == answer and loser['answer']['retry_allowed'] is False
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        observed = lookup(db, load_formal_principal(db, actor_id), command)
        if seal_first:
            assert observed == answer
        else:
            assert observed['request_state'] == 'found' and observed['result'] == answer
        assert observed['retry_allowed'] is False
    assert snapshot(owner) == after
    proofs[key] = dict(kind=kind, direction=direction, apiConnections=2,
        winnerBackendPid=winner_pid, waitingBackendPid=shared['pid'], ledgerLockWaitObserved=True,
        attemptsPerParticipant=1, committedBusinessFacts=0 if seal_first else 1,
        committedSealFacts=1 if seal_first else 0, databaseEqualsWinnerSnapshot=True,
        losingOutcome=loser['state'], exactReadOnlyRecovery=True)
    print(kind + ' ' + direction + ': one durable outcome, loser adds no facts PASS', flush=True)
    return stored if seal_first else answer


def commit_seal(context, *, actor_id, command):
    return race(context, actor_id=actor_id, command=command, seal_first=True)


def commit_write(context, *, actor_id, command):
    key = arguments(command)['kind'] + ':execute_first'
    if key not in context.get('seal_race_proofs', {}):
        return race(context, actor_id=actor_id, command=command, seal_first=False)
    # Later generations still use the real writer once, without inventing a
    # duplicate race result or skipping their downstream business assertions.
    with Session(context['engines']['star_oam_api']) as db:
        answer = write(db, actor=load_formal_principal(db, actor_id), command=command)
        db.commit()
        return answer
