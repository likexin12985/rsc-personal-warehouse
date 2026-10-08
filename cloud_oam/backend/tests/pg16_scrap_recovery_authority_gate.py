"""Current recovery authority over the real migrated 0164 identity catalog.

The probe schema is test-only. Its BEFORE and deferred triggers call the
candidate helper; it does not pretend to bind or post a real scrap document.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
import time
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import AuthIdentity, Organization, Person, Role, RoleAssignment, RolePermission
from app.inventory_models import CustodyAssignment, StockLocation
from app.models import User
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap.recovery_authority import ACTIONS, authorize
from test_formal_access import make_organization, make_user, make_permission, assign, grant


FUNCTION = 'rsc_assert_scrap_recovery_authority_0165'
SIGNATURE = 'public.' + FUNCTION + '(text,bigint,uuid,uuid,uuid,uuid,text)'
INSERT = text('''INSERT INTO recovery_authority_probe.commands
    (id, actor_id, actor_version, actor_person, owner_id, location_id, requester_id, stage)
    VALUES (:id, :actor_id, :actor_version, :actor_person, :owner_id, :location_id, :requester_id, :stage)''')
SCHEMA = '''
CREATE SCHEMA recovery_authority_probe AUTHORIZATION star_oam_migrator;
CREATE TABLE recovery_authority_probe.commands (
    id uuid PRIMARY KEY, actor_id text NOT NULL, actor_version bigint NOT NULL,
    actor_person uuid NOT NULL, owner_id uuid NOT NULL, location_id uuid NOT NULL,
    requester_id uuid NOT NULL, stage text NOT NULL);
CREATE FUNCTION recovery_authority_probe.guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public AS $$
BEGIN
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    PERFORM public.rsc_assert_scrap_recovery_authority_0165(
        NEW.actor_id, NEW.actor_version, NEW.actor_person, NEW.owner_id,
        NEW.location_id, NEW.requester_id, NEW.stage);
    RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION recovery_authority_probe.guard() FROM PUBLIC;
CREATE TRIGGER authority_before BEFORE INSERT ON recovery_authority_probe.commands
FOR EACH ROW EXECUTE FUNCTION recovery_authority_probe.guard();
CREATE CONSTRAINT TRIGGER authority_commit AFTER INSERT ON recovery_authority_probe.commands
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION recovery_authority_probe.guard();
ALTER TABLE recovery_authority_probe.commands ENABLE ALWAYS TRIGGER authority_before;
ALTER TABLE recovery_authority_probe.commands ENABLE ALWAYS TRIGGER authority_commit;
GRANT USAGE ON SCHEMA recovery_authority_probe TO star_oam_api;
GRANT SELECT, INSERT ON recovery_authority_probe.commands TO star_oam_api;
'''


def seed(owner):
    now = datetime.now(timezone.utc)
    with Session(owner) as db:
        hq = make_organization(db, name='Synthetic recovery HQ')
        region = make_organization(db, name='Synthetic recovery region', parent=hq)
        other = make_organization(db, name='Synthetic other region', parent=hq)
        roles = {r.code: r for r in db.scalars(select(Role))}
        users = {}; people = {}; assignments = {}; identities = {}; grants = {}; deny_grants = {}
        for name, org, role, scope, scope_id in (
            ('apply', region, 'technician', 'person', None),
            ('regional', region, 'provincial_manager', 'organization', str(region.id)),
            ('headquarters', hq, 'admin', 'national', '*'),
        ):
            user, person = make_user(db, org, name='Synthetic recovery '+name)
            users[name], people[name] = user.id, person.id
            assignments[name] = assign(db, user, roles[role], scope_type=scope,
                scope_id=scope_id or str(person.id)).id
            identities[name] = db.scalar(select(AuthIdentity.id).where(AuthIdentity.user_id==user.id))
        users['execute'] = users['headquarters']; people['execute'] = people['headquarters']
        assignments['execute'] = assignments['headquarters']; identities['execute'] = identities['headquarters']
        for stage, action in ACTIONS.items():
            role = 'technician' if stage=='apply' else 'provincial_manager' if stage=='regional' else 'admin'
            permission = make_permission(db, 'stock_operation', action)
            grant(db, roles[role], permission)
            grants[stage] = db.scalar(select(RolePermission.id).where(
                RolePermission.role_id==roles[role].id, RolePermission.permission_id==permission.id))
            # Dormant extra assignments below exercise matching-deny precedence
            # and an allow borrowed from an unrelated role, using real grants.
            if stage in ('apply', 'headquarters', 'execute'):
                grant(db, roles['provincial_manager'], permission, effect='deny')
                deny_grants[stage] = db.scalar(select(RolePermission.id).where(
                    RolePermission.role_id==roles['provincial_manager'].id,
                    RolePermission.permission_id==permission.id))
            if stage=='execute':
                grant(db, roles['technician'], permission)
        dummy = make_permission(db, 'stock_operation', 'synthetic_unused_recovery_action')
        extras = {}
        for key, who, role, scope, scope_id in (
            ('apply_deny', 'apply', 'provincial_manager', 'organization', str(region.id)),
            ('hq_deny', 'headquarters', 'provincial_manager', 'organization', str(region.id)),
            ('hq_borrow', 'headquarters', 'technician', 'person', str(people['headquarters'])),
        ):
            extras[key] = assign(db, db.get(User, users[who]), roles[role], scope_type=scope,
                scope_id=scope_id, status='scheduled', valid_from=now+timedelta(days=1)).id
        parent = StockLocation(id=uuid4(), code='RECOVERY-REG-'+uuid4().hex, name='Synthetic region',
            location_type='region', owner_org_id=region.id, status='active')
        db.add(parent); db.flush()
        location = StockLocation(id=uuid4(), code='RECOVERY-PERSON-'+uuid4().hex, name='Synthetic personal',
            location_type='personal', owner_org_id=region.id, parent_id=parent.id,
            custodian_person_id=people['apply'], status='active')
        db.add(location); db.flush()
        custody = CustodyAssignment(location_id=location.id, custodian_person_id=people['apply'],
            valid_from=now-timedelta(days=2))
        second = CustodyAssignment(location_id=location.id, custodian_person_id=people['regional'],
            valid_from=now+timedelta(days=2), valid_to=now+timedelta(days=3))
        db.add_all([custody, second]); db.flush()
        result = dict(users=users, people=people, assignments=assignments, identities=identities,
            grants=grants, deny_grants=deny_grants, roles={k:r.id for k,r in roles.items()}, extras=extras, dummy=dummy.id,
            owner=region.id, hq=hq.id, other=other.id, location=location.id, custody=custody.id,
            second_custody=second.id, now=now)
        db.commit()
        return result


def params(f, stage):
    return dict(id=uuid4(), actor_id=f['users'][stage], actor_version=1, actor_person=f['people'][stage],
        owner_id=f['owner'], location_id=f['location'], requester_id=f['people']['apply'], stage=stage)


def apply_edits(db, edits):
    saved = []
    for model, identifier, changes in edits:
        table = model.__table__
        row = db.execute(select(table).where(table.c.id==identifier)).mappings().one()
        saved.append((model, identifier, {key:row[key] for key in changes}))
        db.execute(update(table).where(table.c.id==identifier).values(**changes))
    return list(reversed(saved))


@contextmanager
def patched(owner, edits):
    with owner.begin() as db:
        undo = apply_edits(db, edits)
    try:
        yield
    finally:
        with owner.begin() as db:
            apply_edits(db, undo)


def row_count(owner):
    with owner.connect() as db:
        return db.scalar(text('SELECT count(*) FROM recovery_authority_probe.commands'))


def python_check(api, f, stage, actor, *, accepted):
    source = dict(order=SimpleNamespace(requester_id=f['people']['apply']), account=SimpleNamespace(
        owner_org_id=f['owner'], location_id=f['location'], custodian_person_id=f['people']['apply']))
    with Session(api) as db:
        try:
            authorize(db, actor=actor, source=source, stage=stage)
        except (InventoryReadError, InventoryPostingError):
            assert not accepted, ('Python rejected valid current authority', stage)
        else:
            assert accepted, ('Python accepted invalid current authority', stage)


def attempt(engine, values, *, rejected=False, late_edits=(), phase='statement', isolation=None,
            connected=None, before_commit=None):
    with engine.connect().execution_options(isolation_level=isolation or 'READ COMMITTED') as db:
        db.execute(text("SET LOCAL lock_timeout='5s'"))
        db.execute(text("SET LOCAL statement_timeout='10s'"))
        if connected:
            connected(db)
        reached = 'statement'
        try:
            db.execute(INSERT, values)
            reached = 'commit'
            if late_edits:
                apply_edits(db, late_edits)
            if before_commit:
                before_commit(db)
            db.commit()
        except DBAPIError as error:
            assert rejected, str(error.orig)
            assert error.orig.sqlstate=='23514', str(error.orig)
            assert FUNCTION in (error.orig.diag.context or ''), str(error.orig)
            assert reached==phase, (reached, phase)
            db.rollback()
        else:
            assert not rejected, 'invalid current authority committed'


def expiry_checks(owner, api, f):
    outcomes = []
    for label, model, identifier in (
        ('role_assignment', RoleAssignment, f['assignments']['execute']),
        ('custody', CustodyAssignment, f['custody']),
    ):
        before = row_count(owner)
        expiry = datetime.now(timezone.utc)+timedelta(seconds=2)
        def wait_for_expiry(db):
            # The BEFORE trigger has already accepted the live assignment.
            # Wait using the same server clock as the candidate guard, then
            # require a real API COMMIT to reject the elapsed authority.
            db.execute(text('SELECT pg_sleep(GREATEST(0,extract(epoch FROM '
                '(CAST(:expiry AS timestamptz)-clock_timestamp())))+0.02)'),dict(expiry=expiry))
        with patched(owner,[(model,identifier,dict(valid_to=expiry))]):
            attempt(api,params(f,'execute'),rejected=True,phase='commit',before_commit=wait_for_expiry)
        assert row_count(owner)==before
        outcomes.append(label)
    return outcomes


def concurrency_checks(owner, api, f):
    before = row_count(owner)
    ready = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with owner.connect() as held:
                held.execute(update(User).where(User.id==f['users']['execute']).values(authorization_version=2))
                future = pool.submit(attempt,api,params(f,'execute'),rejected=True,
                    connected=lambda db: ready.put(db.scalar(text('SELECT pg_backend_pid()'))))
                try:
                    pid = ready.get(timeout=5)
                    deadline = time.monotonic()+2
                    while not held.scalar(text('SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)'),dict(pid=pid)):
                        assert time.monotonic()<deadline and not future.done(), 'API did not wait on the held principal'
                        time.sleep(0.02)
                    held.commit()
                    future.result(timeout=10)
                finally:
                    held.rollback()
        finally:
            with owner.begin() as db:
                db.execute(update(User).where(User.id==f['users']['execute']).values(authorization_version=1))
    assert row_count(owner)==before
    blocked = []
    def contenders(_api_db):
        for label in ('principal_revocation','new_custody_assignment'):
            with owner.connect() as rival:
                rival.execute(text("SET LOCAL lock_timeout='200ms'"))
                try:
                    if label=='principal_revocation':
                        rival.execute(update(User).where(User.id==f['users']['execute']).values(authorization_version=2))
                    else:
                        rival.execute(CustodyAssignment.__table__.insert().values(id=uuid4(),
                            location_id=f['location'],custodian_person_id=f['people']['apply'],
                            valid_from=f['now']+timedelta(days=4),valid_to=f['now']+timedelta(days=5)))
                except DBAPIError as error:
                    assert error.orig.sqlstate=='55P03', str(error.orig)
                    if label=='new_custody_assignment':
                        assert 'stock_locations' in (error.orig.diag.context or ''), str(error.orig)
                    rival.rollback(); blocked.append(label)
                else:
                    rival.rollback()
                    raise AssertionError('authority dependencies changed while API held admission locks')
    attempt(api,params(f,'execute'),before_commit=contenders)
    assert row_count(owner)==before+1
    assert blocked==['principal_revocation','new_custody_assignment']
    return dict(revocationBeforeAdmissionObserved=True,admissionWaitVerifiedInPgLocks=True,
        dependenciesBlockedUntilCommit=blocked)


def run(engines):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    from app.database_security import validate_production_database_security
    validate_production_database_security(api, expected_runtime_role='star_oam_api',
        expected_migration_role='star_oam_migrator')
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261213_0164'
        old_functions = dict(db.execute(text("SELECT oid,pg_get_functiondef(oid) FROM pg_proc "
            "WHERE pronamespace='public'::regnamespace AND prokind='f'")).all())
    f = seed(owner)
    with owner.begin() as db:
        statement = text((Path(__file__).parents[1]/'alembic/stock_scrap_0165/recovery_authority.sql').read_text())
        assert not statement._bindparams
        db.execute(statement)
        db.exec_driver_sql(SCHEMA)
        info = db.execute(text('''SELECT pg_get_userbyid(proowner),prosecdef,provolatile,proconfig
            FROM pg_proc WHERE oid=to_regprocedure(:signature)'''), dict(signature=SIGNATURE)).one()
        assert tuple(info)==('star_oam_migrator', True, 'v', ['search_path=pg_catalog, public'])
        for role in ('star_oam_api','star_oam_projector','star_oam_edge','edge_inbox','star_oam_backup'):
            assert not db.scalar(text('SELECT has_function_privilege(:role,:signature,\'EXECUTE\')'),
                dict(role=role,signature=SIGNATURE))
    actors = {}
    with Session(api) as db:
        for stage in ACTIONS:
            actors[stage] = load_formal_principal(db, f['users'][stage])
    accepted = []; rejected = []; late = []
    for stage in ACTIONS:
        python_check(api, f, stage, actors[stage], accepted=True)
        attempt(api, params(f, stage)); accepted.append(stage)
        role = 'technician' if stage=='apply' else 'provincial_manager' if stage=='regional' else 'admin'
        cases = {
            'version_changed': [(User,f['users'][stage],dict(authorization_version=2))],
            'account_disabled': [(User,f['users'][stage],dict(account_status='disabled'))],
            'legacy_inactive': [(User,f['users'][stage],dict(is_active=False))],
            'person_departed': [(Person,f['people'][stage],dict(employment_status='left'))],
            'identity_pending': [(AuthIdentity,f['identities'][stage],dict(status='pending',verified_at=None))],
            'identity_revoked': [(AuthIdentity,f['identities'][stage],dict(status='revoked',revoked_at=f['now']))],
            'assignment_expired': [(RoleAssignment,f['assignments'][stage],dict(valid_to=f['now']-timedelta(seconds=1)))],
            'assignment_future': [(RoleAssignment,f['assignments'][stage],dict(valid_from=f['now']+timedelta(days=1)))],
            'assignment_revoked': [(RoleAssignment,f['assignments'][stage],dict(status='revoked',
                revoked_at=f['now'],revoked_by=f['users'][stage]))],
            'role_inactive': [(Role,f['roles'][role],dict(status='inactive'))],
            'missing_exact_allow': [(RolePermission,f['grants'][stage],dict(permission_id=f['dummy']))],
            'location_inactive': [(StockLocation,f['location'],dict(status='inactive'))],
            'location_other_owner': [(StockLocation,f['location'],dict(owner_org_id=f['other']))],
            'location_other_custodian': [(StockLocation,f['location'],dict(custodian_person_id=f['people']['regional']))],
            'custody_expired': [(CustodyAssignment,f['custody'],dict(valid_to=f['now']-timedelta(seconds=1)))],
            'custody_other_person': [(CustodyAssignment,f['custody'],dict(custodian_person_id=f['people']['regional']))],
            'custody_overlaps': [(CustodyAssignment,f['second_custody'],dict(valid_from=f['now']-timedelta(days=1)))],
        }
        if stage=='regional':
            cases['other_region_scope'] = [(RoleAssignment,f['assignments'][stage],dict(scope_id=str(f['other'])))]
        if stage in ('headquarters','execute'):
            cases['admin_not_hq'] = [(Person,f['people'][stage],dict(organization_id=f['owner']))]
        for case, edits in cases.items():
            before = row_count(owner)
            with patched(owner, edits):
                python_check(api, f, stage, actors[stage], accepted=False)
                attempt(api, params(f, stage), rejected=True)
            assert row_count(owner)==before
            rejected.append(stage+':'+case)
        # The direct owner probe proves the deferred trigger independently of
        # BEFORE admission. Catalog edits and probe row roll back together.
        for case in ('version_changed', 'identity_pending', 'missing_exact_allow', 'custody_expired'):
            before = row_count(owner)
            attempt(owner, params(f, stage), rejected=True, late_edits=cases[case], phase='commit')
            assert row_count(owner)==before
            python_check(api, f, stage, actors[stage], accepted=True)
            late.append(stage+':'+case)
        before = row_count(owner)
        attempt(api, params(f, stage), rejected=True, isolation='REPEATABLE READ')
        assert row_count(owner)==before
        rejected.append(stage+':repeatable_read')
    # An effective allow from a different assignment cannot supply the admin
    # permission; a matching explicit deny beats the valid required allow.
    special = [
        ('execute','borrowed_allow', [
            (RolePermission,f['grants']['execute'],dict(permission_id=f['dummy'])),
            (RolePermission,f['deny_grants']['execute'],dict(effect='allow')),
            (RoleAssignment,f['extras']['hq_deny'],dict(valid_from=f['now']-timedelta(days=1)))]),
        ('apply','organization_deny', [(RoleAssignment,f['extras']['apply_deny'],dict(valid_from=f['now']-timedelta(days=1)))]),
        ('headquarters','organization_deny', [(RoleAssignment,f['extras']['hq_deny'],dict(valid_from=f['now']-timedelta(days=1)))]),
        ('execute','organization_deny', [(RoleAssignment,f['extras']['hq_deny'],dict(valid_from=f['now']-timedelta(days=1)))]),
    ]
    for stage, case, edits in special:
        before=row_count(owner)
        with patched(owner, edits):
            if case=='borrowed_allow':
                with Session(api) as db:
                    current=load_formal_principal(db,f['users'][stage])
                    assert current.allows(db,'stock_operation',ACTIONS[stage],
                        target_scope_type='organization',target_scope_id=str(f['owner']))
            python_check(api,f,stage,actors[stage],accepted=False)
            attempt(api,params(f,stage),rejected=True)
        assert row_count(owner)==before
        rejected.append(stage+':'+case)
    for stage in ('headquarters','execute'):
        edits=[(RoleAssignment,f['extras']['hq_deny'],dict(valid_from=f['now']-timedelta(days=1),scope_id=str(f['other'])))]
        with patched(owner,edits):
            python_check(api,f,stage,actors[stage],accepted=True)
            attempt(api,params(f,stage))
        accepted.append(stage+':unrelated_region_deny')
    for stage in ACTIONS:
        for key, value in (
            ('actor_version',0),('actor_person',uuid4()),('owner_id',f['other']),
            ('location_id',uuid4()),('requester_id',uuid4()),('stage','unknown'),
        ):
            before=row_count(owner); values=params(f,stage); values[key]=value
            attempt(api,values,rejected=True)
            assert row_count(owner)==before
            rejected.append(stage+':forged_'+key)
    expired = expiry_checks(owner,api,f)
    races = concurrency_checks(owner,api,f)
    with api.connect() as db:
        try:
            values=params(f,'execute')
            db.execute(text('SELECT public.'+FUNCTION+'(:actor_id,:actor_version,:actor_person,'
                ':owner_id,:location_id,:requester_id,:stage)'),values)
        except DBAPIError as error:
            assert error.orig.sqlstate=='42501'
            db.rollback()
        else:
            raise AssertionError('API directly executed private authority helper')
    with owner.connect() as db:
        current = dict(db.execute(text("SELECT oid,pg_get_functiondef(oid) FROM pg_proc "
            "WHERE pronamespace='public'::regnamespace AND prokind='f'")).all())
        assert {oid:current[oid] for oid in old_functions}==old_functions
        assert db.scalar(text('SELECT count(*) FROM inventory_transactions'))==0
    return dict(passed=True, positive=accepted, rejected=rejected, deferredRejected=late,
        apiExpiredAtCommit=expired,concurrency=races,apiDirectExecutionDenied=True,
        directApiAuthorityChecks=True, pythonAuthorityParity=True, oldFunctionsUnchanged=True,
        originalSchemaRevision='20261213_0164', probe='test-only BEFORE and deferred commands',
        fullBusinessPosting=False, formalMigration=False, productionAcceptance=False)
