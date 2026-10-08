"""Actual closure API versus a separate fixture-owner permission transaction.

The revoker changes an existing action grant to deny, keeping read access.
This proves catalog-revocation ordering, not an administration HTTP endpoint.
No API identity-table privileges or late same-transaction edits are added.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from pg16_scrap_seal_authority import ACTIONS
from pg16_scrap_seal_persistence import snapshot, only_closure_changed
from pg16_scrap_seal_races import close, lookup, setup
from pg16_scrap_seal_sources import arguments


def owner_setup(db):
    db.execute(text("SET LOCAL lock_timeout='600s'"))
    db.execute(text("SET LOCAL statement_timeout='600s'"))
    assert db.scalar(text('SELECT current_user')) == 'star_oam_migrator'
    return db.scalar(text('SELECT pg_backend_pid()'))


def revoke(db, identifier):
    assert db.execute(text("UPDATE public.role_permissions SET effect='deny' "
        "WHERE id=:id AND effect='allow'"), dict(id=identifier)).rowcount == 1


def race(context, *, actor_id, command, revoke_first):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    kind = arguments(command)['kind']
    role = 'technician' if kind == 'apply' else 'provincial_manager' if kind == 'regional' else 'admin'
    direction = 'revoke_first' if revoke_first else 'seal_first'
    with owner.connect() as db:
        grant = db.execute(text('''SELECT rp.id,rp.effect FROM public.role_permissions rp
            JOIN public.roles r ON r.id=rp.role_id JOIN public.permissions p ON p.id=rp.permission_id
            WHERE r.code=:role AND p.resource='stock_operation' AND p.action=:action AND p.field_code='' '''),
            dict(role=role, action=ACTIONS[kind])).one()
        assert grant.effect == 'allow'
    before = snapshot(owner)
    started, settled, release = Event(), Event(), Event()
    shared = {}

    def opponent():
        with Session(api if revoke_first else owner) as db:
            shared['pid'] = setup(db) if revoke_first else owner_setup(db)
            started.set()
            try:
                if revoke_first:
                    try:
                        close(db, load_formal_principal(db, actor_id), command)
                    except DBAPIError as error:
                        assert error.orig.sqlstate == '23514', error.orig
                        assert 'current seal read and action permission required' in error.orig.diag.message_primary, error.orig
                        assert 'rsc_scrap_seal_authority_0165' in (error.orig.diag.context or ''), error.orig
                        outcome = dict(phase='service', sqlstate='23514', message=error.orig.diag.message_primary)
                        db.rollback()
                    else:
                        raise AssertionError('closure accepted after competing permission deny committed')
                else:
                    revoke(db, grant.id)
                    outcome = dict(permissionChanged=True)
                settled.set()
                assert release.wait(timeout=600), 'winner snapshot was not collected'
                if not revoke_first:
                    db.commit()
                return outcome
            finally:
                settled.set()

    pool = ThreadPoolExecutor(max_workers=1)
    answer = None
    try:
        with Session(owner if revoke_first else api) as winner:
            winner_pid = owner_setup(winner) if revoke_first else setup(winner)
            if revoke_first:
                revoke(winner, grant.id)
            else:
                answer = close(winner, load_formal_principal(winner, actor_id), command)
                assert answer['request_state'] == 'sealed' and answer['retry_allowed'] is False
            future = pool.submit(opponent)
            try:
                assert started.wait(timeout=30), 'opponent connection did not start'
                assert winner_pid != shared['pid']
                deadline = time.monotonic() + 30
                with owner.connect() as observer:
                    while True:
                        if future.done():
                            future.result()
                            raise AssertionError('opponent finished before permission lock was released')
                        blockers = observer.scalar(text('SELECT pg_blocking_pids(:pid)'), dict(pid=shared['pid']))
                        lock_mode = 'RowShareLock' if revoke_first else 'RowExclusiveLock'
                        relation = observer.scalar(text('SELECT EXISTS(SELECT 1 FROM pg_locks '
                            "WHERE pid=:pid AND relation='public.role_permissions'::regclass "
                            'AND mode=:mode AND granted)'), dict(pid=shared['pid'], mode=lock_mode))
                        if winner_pid in blockers and relation:
                            break
                        if time.monotonic() >= deadline:
                            raise AssertionError('permission-row lock contention not observed')
                        time.sleep(0.02)
                print(kind + ' ' + direction + ': API/owner real permission lock wait PASS', flush=True)
                winner.commit()
            finally:
                winner.rollback()
        assert settled.wait(timeout=600), 'opponent did not observe committed winner'
        if future.done():
            future.result()
        expected = snapshot(owner)
        release.set()
        outcome = future.result(timeout=600)
        denied = snapshot(owner)
        if revoke_first:
            assert denied == expected
            assert {name for name in before if before[name] != denied[name]} == {'role_permissions'}
        else:
            only_closure_changed(before, expected)
            assert {name for name in expected if expected[name] != denied[name]} == {'role_permissions'}
        # Today's read permission still exists. With write currently denied,
        # history survives a winning seal; a losing seal leaves no result.
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            observed = lookup(db, load_formal_principal(db, actor_id), command)
            if revoke_first:
                assert observed['request_state'] == 'not_found' and observed['retry_allowed'] is False
            else:
                assert observed == answer
        assert snapshot(owner) == denied
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)
        with owner.begin() as db:
            current = db.scalar(text('SELECT effect FROM public.role_permissions WHERE id=:id'), dict(id=grant.id))
            assert current in ('allow', 'deny')
            if current == 'deny':
                assert db.execute(text("UPDATE public.role_permissions SET effect='allow' WHERE id=:id AND effect='deny'"),
                    dict(id=grant.id)).rowcount == 1
    after = snapshot(owner)
    assert after == (before if revoke_first else expected), 'revocation fixture failed exact restoration'
    context.setdefault('seal_revocation_races', {})[kind + ':' + direction] = dict(
        action=ACTIONS[kind], direction=direction, apiConnections=1, ownerConnections=1,
        winnerBackendPid=winner_pid, waitingBackendPid=shared['pid'], permissionLockWaitObserved=True,
        closureCalls=1, revocationWrites=1, permissionChangedSeparately=True,
        outcome=outcome, readOnlyWithWriteDenied=observed['request_state'],
        permissionFixtureRestored=True, exactDatabaseVerified=True,
        newSealsCommitted=0 if revoke_first else 1, administrationHttpVerified=False)
    print(kind + ' ' + direction + ': ordered revoke/seal outcome and exact restored database PASS', flush=True)
    return answer


def commit_with_revocation_races(context, *, actor_id, command):
    other = command.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
    race(context, actor_id=actor_id, command=other, revoke_first=True)
    return race(context, actor_id=actor_id, command=command, revoke_first=False)
