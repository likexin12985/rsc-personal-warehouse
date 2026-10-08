"""Real transaction and Alembic checks for installed read-only capture roles."""
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import sys

from sqlalchemy import text

from app.daily_reconciliation.capture_role_contract import ROLES
from app.daily_reconciliation.capture_security import validate_capture_roles, CaptureRoleSecurityError
from app.database_security import validate_production_database_security
from migration_capture_roles import maintain_capture_permissions, capture_acl
from pg16_daily_review_runtime import facts
from pg16_material_request_remaining_cancel_gate import _wait_on_blocker


def run(engines, administrator, *, command, head):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    before = facts(owner)
    with owner.connect() as db:
        grants = capture_acl(db, ROLES)
        assert validate_capture_roles(db)
    try:
        with administrator.begin() as db:
            with maintain_capture_permissions(db):
                raise AssertionError('superuser was accepted as the migration identity')
    except RuntimeError as error:
        assert str(error) == 'capture migration requires direct unprivileged PG16 migrator'
    # A failure after local permission maintenance must roll back both DDL
    # and ACLs, including while the PostgreSQL transaction is aborted.
    from sqlalchemy.exc import DBAPIError
    try:
        with owner.begin() as db:
            with maintain_capture_permissions(db):
                assert not capture_acl(db, ROLES)
                db.execute(text('CREATE TABLE public.capture_migration_rollback_probe (id integer)'))
                db.execute(text('SELECT 1/0'))
    except DBAPIError as error:
        assert error.orig.sqlstate == '22012'
    else:
        raise AssertionError('injected migration SQL failure was not observed')
    assert facts(owner) == before
    with owner.connect() as db:
        assert capture_acl(db, ROLES) == grants and validate_capture_roles(db)
        assert db.scalar(text("SELECT to_regclass('public.capture_migration_rollback_probe')")) is None

    # Extra SELECT is unsafe too. Refuse it before withdrawing any original
    # grant, then undo only this owned-fixture fault and revalidate the role.
    with administrator.begin() as db:
        db.execute(text('GRANT SELECT ON public.files TO rsc_control_capture'))
    try:
        with owner.connect() as db:
            drifted = capture_acl(db, ROLES)
        try:
            with owner.begin() as db:
                with maintain_capture_permissions(db):
                    raise AssertionError('unsafe capture catalog was accepted')
        except CaptureRoleSecurityError:
            pass
        with owner.connect() as db:
            assert capture_acl(db, ROLES) == drifted
    finally:
        with administrator.begin() as db:
            db.execute(text('REVOKE SELECT ON public.files FROM rsc_control_capture'))
    assert facts(owner) == before

    # An existing reader retains its transaction before maintenance obtains
    # the exclusive table locks. Observe the actual PostgreSQL blocker.
    waiting_pid = Queue()
    def maintain():
        with owner.begin() as db:
            waiting_pid.put(db.scalar(text('SELECT pg_backend_pid()')))
            with maintain_capture_permissions(db):
                assert not capture_acl(db, ROLES)
        return True
    with administrator.connect() as reader, ThreadPoolExecutor(max_workers=1) as pool:
        tx = reader.begin()
        reader.execute(text('SET LOCAL ROLE rsc_control_capture'))
        reader.execute(text('SELECT id FROM public.audit_events LIMIT 1')).all()
        blocker = reader.scalar(text('SELECT pg_backend_pid()'))
        future = pool.submit(maintain)
        try:
            _wait_on_blocker(owner,waiting_pid.get(timeout=5),blocker,future)
        finally:
            tx.rollback()
        assert future.result(timeout=30)

    # Exercise the real environment wrapper around immutable revisions.
    command('capture-configured-empty-downgrade', [sys.executable,'-m','alembic','-c','alembic.ini',
        'downgrade','20261223_0174'])
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM public.alembic_version')) == '20261223_0174'
        assert capture_acl(db, ROLES) == grants and validate_capture_roles(db)
    command('capture-configured-empty-reupgrade', [sys.executable,'-m','alembic','-c','alembic.ini',
        'upgrade',head])
    assert facts(owner) == before
    with owner.connect() as db:
        assert capture_acl(db, ROLES) == grants and validate_capture_roles(db)
    validate_production_database_security(api, expected_runtime_role='star_oam_api',
                                         expected_migration_role='star_oam_migrator')
    result = dict(passed=True, migrationHead=head, transactionalAclRestoration=True,
        privilegedMigrationIdentityRefused=True,
        sqlFailureRollback=True, unsafeSelectRefusedWithoutAclChange=True,
        exactReaderBlockerObserved=True, configuredRoleEmptyRoundtrip=True,
        originalFactsUnchanged=True, originalGrantsAndPoliciesRestored=True)
    print('Capture roles: exact ACL rollback, unsafe grant refusal, reader lock and formal migration roundtrip PASS',flush=True)
    return result
