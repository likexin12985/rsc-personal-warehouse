"""Real closure service admission followed by natural expiry at API COMMIT.

Only the runner-owned fixture's assignment deadline is configured by its
owner, before the API transaction starts. No late identity/version injection,
private authority probe, clock mocking, or automatic request replay is used.
"""
from contextlib import contextmanager
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from pg16_scrap_seal_authority import ACTIONS
from pg16_scrap_seal_persistence import snapshot
from pg16_scrap_seal_races import close, lookup, setup
from pg16_scrap_seal_sources import arguments


@contextmanager
def deadline(owner, actor_id, kind):
    role = 'technician' if kind == 'apply' else 'provincial_manager' if kind == 'regional' else 'admin'
    with owner.begin() as db:
        assignment = db.execute(text('''SELECT a.id,a.valid_to,a.updated_at
            FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
            WHERE a.user_id=:actor AND r.code=:role AND r.status='active'
              AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
              AND a.valid_from<=clock_timestamp()
              AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp())
            FOR UPDATE OF a'''), dict(actor=actor_id, role=role)).one()
        expires = db.scalar(text("SELECT clock_timestamp()+interval '30 seconds'"))
        assert assignment.valid_to is None or expires < assignment.valid_to
        # Raw SQL deliberately preserves fixture metadata. An ORM update's
        # onupdate timestamp would obscure the exact full-database rollback.
        changed = db.execute(text('UPDATE public.role_assignments SET valid_to=:expires WHERE id=:id'),
            dict(expires=expires, id=assignment.id))
        assert changed.rowcount == 1
    try:
        yield assignment.id, expires
    finally:
        with owner.begin() as db:
            changed = db.execute(text('UPDATE public.role_assignments SET valid_to=:old '
                'WHERE id=:id AND valid_to=:expires AND updated_at=:updated'),
                dict(old=assignment.valid_to, id=assignment.id, expires=expires, updated=assignment.updated_at))
            assert changed.rowcount == 1, 'fixture assignment unexpectedly changed during expiry test'


def exercise(context):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    cases = {}
    for actor_id, command, _ in context['scrap_lookup_cases'] + context['scrap_recovery_lookup_cases']:
        cases.setdefault(arguments(command)['kind'], (actor_id, command))
    assert set(cases) == set(ACTIONS)
    proofs = {}
    for kind, (actor_id, original) in cases.items():
        command = original.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
        before = snapshot(owner)
        with deadline(owner, actor_id, kind) as (assignment_id, expires):
            configured = snapshot(owner)
            assert {name for name in before if before[name] != configured[name]} == {'role_assignments'}
            with Session(api) as db:
                pid = setup(db)
                actor = load_formal_principal(db, actor_id)
                admitted_at = db.scalar(text('SELECT clock_timestamp()'))
                assert admitted_at < expires, 'fixture expired before the real service was called'
                answer = close(db, actor, command)
                created_at = db.scalar(text('SELECT clock_timestamp()'))
                assert created_at < expires, 'service did not return while its assignment was live'
                assert answer['request_state'] == 'sealed' and answer['retry_allowed'] is False
                seal_id = UUID(answer['seal']['seal_id'])
                assert db.scalar(text('SELECT count(*) FROM public.stock_scrap_request_seals WHERE id=:id'),
                    dict(id=seal_id)) == 1
                assert db.scalar(text("SELECT count(*) FROM public.audit_events "
                    "WHERE aggregate_type='stock_scrap_request_seal' AND aggregate_id=:id "
                    "AND action='seal_scrap_request'"), dict(id=str(seal_id))) == 1
                assert snapshot(owner) == configured, 'service committed before its caller'
                print(kind + ': live authority admitted real seal/audit; waiting for natural expiry', flush=True)
                # Use the same clock as the COMMIT guard. Bounded server sleeps
                # do not change the assignment, actor, or business transaction.
                while db.scalar(text('SELECT clock_timestamp()')) < expires:
                    db.execute(text('SELECT pg_sleep(LEAST(1,GREATEST(0,extract(epoch FROM '
                        '(CAST(:expires AS timestamptz)-clock_timestamp()))))+0.01)'), dict(expires=expires))
                commit_at = db.scalar(text('SELECT clock_timestamp()'))
                assert commit_at >= expires
                try:
                    db.commit()
                except DBAPIError as error:
                    assert error.orig.sqlstate == '23514', error.orig
                    assert 'current seal read and action permission required' in error.orig.diag.message_primary, error.orig
                    assert 'rsc_scrap_seal_authority_0165' in (error.orig.diag.context or ''), error.orig
                    message = error.orig.diag.message_primary
                    db.rollback()
                else:
                    raise AssertionError('naturally expired closure authority committed: ' + kind)
            assert snapshot(owner) == configured, 'rejected COMMIT retained business or audit facts'
        assert snapshot(owner) == before, 'expiry fixture did not restore the exact database'
        # Restored fixture grants allow a real READ ONLY query of the original
        # request. It must remain absent; this never permits blind resubmission.
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            recovered = lookup(db, load_formal_principal(db, actor_id), command)
            assert recovered['request_state'] == 'not_found' and recovered['retry_allowed'] is False
        assert snapshot(owner) == before
        proofs[kind] = dict(apiBackendPid=pid, assignmentId=str(assignment_id),
            serviceCalls=1, serviceReturnedWhileLive=True, pendingSealAndAuditVerified=True,
            admittedAt=admitted_at.isoformat(), serviceReturnedAt=created_at.isoformat(),
            expiresAt=expires.isoformat(), commitAttemptAt=commit_at.isoformat(),
            phase='commit', sqlstate='23514', message=message,
            fullRollback=True, fixtureRestored=True, readOnlyOutcome='not_found', retryAllowed=False)
        print(kind + ': natural expiry rejects actual API COMMIT; full rollback and READ ONLY absence PASS', flush=True)
    return dict(passed=True, kinds=proofs, databaseClock=True, actorVersionInjection=False,
        apiOwnsBusinessTransaction=True, productionAcceptance=False)
