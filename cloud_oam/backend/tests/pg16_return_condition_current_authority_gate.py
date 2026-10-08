"""Real identity catalog and real COMMIT checks, with a test-only command probe.

This does not claim a complete condition event, ledger or request write. The
event-binding trigger is separately compiled against the full candidate schema.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
import time
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import AuthIdentity, Organization, Person, Role, RoleAssignment, RolePermission
from app.inventory_models import CustodyAssignment, StockLocation
from app.models import User
from app.formal_services.stock_loss_corrections.return_condition_authority import ACTIONS, REQUESTER, REGIONAL
from pg16_scrap_recovery_authority_gate import patched, apply_edits
from test_formal_access import make_organization, make_user, make_permission, assign, grant


FUNCTION = 'rsc_condition_assert_current_authority'
SIGNATURE = 'public.' + FUNCTION + '(text,bigint,uuid,uuid,uuid,uuid,uuid,text)'
INSERT = text('''INSERT INTO condition_authority_probe.commands
    (id,actor_id,actor_version,actor_person,owner_id,location_id,requester_id,custody_id,kind)
    VALUES (:id,:actor_id,:actor_version,:actor_person,:owner_id,:location_id,:requester_id,:custody_id,:kind)''')
SCHEMA = '''
CREATE SCHEMA condition_authority_probe AUTHORIZATION star_oam_migrator;
CREATE TABLE condition_authority_probe.commands (
    id uuid PRIMARY KEY, actor_id text NOT NULL, actor_version bigint NOT NULL,
    actor_person uuid NOT NULL, owner_id uuid NOT NULL, location_id uuid NOT NULL,
    requester_id uuid NOT NULL, custody_id uuid NOT NULL, kind text NOT NULL);
CREATE FUNCTION condition_authority_probe.guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public AS $$
BEGIN
    PERFORM public.rsc_condition_assert_current_authority(NEW.actor_id,NEW.actor_version,
        NEW.actor_person,NEW.owner_id,NEW.location_id,NEW.requester_id,NEW.custody_id,NEW.kind);
    RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION condition_authority_probe.guard() FROM PUBLIC;
CREATE TRIGGER authority_before BEFORE INSERT ON condition_authority_probe.commands
FOR EACH ROW EXECUTE FUNCTION condition_authority_probe.guard();
CREATE CONSTRAINT TRIGGER authority_commit AFTER INSERT ON condition_authority_probe.commands
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION condition_authority_probe.guard();
ALTER TABLE condition_authority_probe.commands ENABLE ALWAYS TRIGGER authority_before;
ALTER TABLE condition_authority_probe.commands ENABLE ALWAYS TRIGGER authority_commit;
GRANT USAGE ON SCHEMA condition_authority_probe TO star_oam_api;
GRANT SELECT,INSERT ON condition_authority_probe.commands TO star_oam_api;
'''


def seed(owner):
    now = datetime.now(timezone.utc)
    with Session(owner) as db:
        hq=make_organization(db,name='Synthetic condition HQ')
        region=make_organization(db,name='Synthetic condition region',parent=hq)
        other=make_organization(db,name='Synthetic other condition region',parent=hq)
        roles={r.code:r for r in db.scalars(select(Role))}
        users={}; people={}; assignments={}; identities={}
        for name,org,role,scope,scope_id in (
            ('apply',region,'provincial_manager','organization',str(region.id)),
            ('regional',region,'provincial_manager','organization',str(region.id)),
            ('headquarters',hq,'admin','national','*'),
        ):
            user,person=make_user(db,org,name='Synthetic condition '+name)
            users[name],people[name]=user.id,person.id
            assignments[name]=assign(db,user,roles[role],scope_type=scope,scope_id=scope_id).id
            identities[name]=db.scalar(select(AuthIdentity.id).where(AuthIdentity.user_id==user.id))
        grants = {}
        for action in sorted(set(ACTIONS.values())):
            role = 'admin' if action in ('review_return_condition_headquarters','cancel_return_condition_approval') else 'provincial_manager'
            permission = make_permission(db, 'stock_operation', action)
            grant(db, roles[role], permission)
            grants[action] = db.scalar(select(RolePermission.id).where(
                RolePermission.role_id == roles[role].id, RolePermission.permission_id == permission.id))
        dummy=make_permission(db,'stock_operation','synthetic_unused_condition_action')
        location=StockLocation(id=uuid4(),code='COND-'+uuid4().hex,name='Synthetic regional stock',
            location_type='region',owner_org_id=region.id,custodian_person_id=people['apply'],status='active')
        db.add(location); db.flush()
        custody=CustodyAssignment(location_id=location.id,custodian_person_id=people['apply'],
            valid_from=now-timedelta(days=2))
        second=CustodyAssignment(location_id=location.id,custodian_person_id=people['regional'],
            valid_from=now+timedelta(days=2),valid_to=now+timedelta(days=3))
        db.add_all([custody,second]); db.flush()
        f=dict(users=users,people=people,assignments=assignments,identities=identities,
            roles={k:r.id for k,r in roles.items()},owner=region.id,hq=hq.id,other=other.id,
            location=location.id,custody=custody.id,second_custody=second.id,now=now,dummy=dummy.id)
        db.commit()
    f['condition_grants'] = grants
    return f


def who(kind):
    return 'apply' if kind in REQUESTER else 'regional' if kind in REGIONAL else 'headquarters'


def params(f, kind):
    actor = who(kind)
    return dict(id=uuid4(), actor_id=f['users'][actor], actor_version=1, actor_person=f['people'][actor],
        owner_id=f['owner'], location_id=f['location'], requester_id=f['people']['apply'],
        custody_id=f['custody'], kind=kind)


def count(owner):
    with owner.connect() as db:
        return db.scalar(text('SELECT count(*) FROM condition_authority_probe.commands'))


def attempt(engine, values, *, rejected=False, phase='statement', late=(), before_commit=None,
            connected=None, isolation='READ COMMITTED'):
    with engine.connect().execution_options(isolation_level=isolation) as db:
        db.execute(text("SET LOCAL lock_timeout='5s'"))
        db.execute(text("SET LOCAL statement_timeout='10s'"))
        if connected:
            connected(db)
        reached = 'statement'
        try:
            db.execute(INSERT, values)
            reached = 'commit'
            apply_edits(db, late)
            if before_commit:
                before_commit(db)
            db.commit()
        except DBAPIError as error:
            assert rejected, str(error.orig)
            assert error.orig.sqlstate == '23514', str(error.orig)
            assert FUNCTION in (error.orig.diag.context or ''), str(error.orig)
            assert reached == phase, (reached, phase)
            db.rollback()
        else:
            assert not rejected, 'invalid authority committed'


def expiry_checks(owner, api, f):
    result = []
    for label, model, identifier in (
        ('grant', RoleAssignment, f['assignments']['apply']),
        ('custody', CustodyAssignment, f['custody']),
    ):
        before = count(owner)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=2)
        def wait(db):
            db.execute(text('SELECT pg_sleep(GREATEST(0,extract(epoch FROM '
                '(CAST(:expiry AS timestamptz)-clock_timestamp())))+0.02)'), dict(expiry=expiry))
        with patched(owner, [(model, identifier, dict(valid_to=expiry))]):
            attempt(api, params(f, 'execute'), rejected=True, phase='commit', before_commit=wait)
        assert count(owner) == before
        result.append(label)
    return result


def races(owner, api, f):
    ready = Queue(); before = count(owner)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with owner.connect() as held:
                held.execute(update(User).where(User.id == f['users']['apply']).values(authorization_version=2))
                blocker = held.scalar(text('SELECT pg_backend_pid()'))
                future = pool.submit(attempt, api, params(f, 'execute'), rejected=True,
                    connected=lambda db: ready.put(db.scalar(text('SELECT pg_backend_pid()'))))
                pid = ready.get(timeout=5); deadline = time.monotonic()+3
                while not held.scalar(text('SELECT :blocker=ANY(pg_blocking_pids(:pid))'), dict(blocker=blocker,pid=pid)):
                    assert time.monotonic() < deadline and not future.done(), 'specific principal lock wait not observed'
                    time.sleep(0.02)
                held.commit(); future.result(timeout=10)
        finally:
            with owner.begin() as db:
                db.execute(update(User).where(User.id == f['users']['apply']).values(authorization_version=1))
    assert count(owner) == before
    blocked = []
    def contenders(_db):
        for label in ('principal','custody','new_custody','organization'):
            with owner.connect() as rival:
                rival.execute(text("SET LOCAL lock_timeout='200ms'"))
                try:
                    if label == 'principal':
                        rival.execute(update(User).where(User.id == f['users']['apply']).values(authorization_version=2))
                    elif label == 'custody':
                        rival.execute(update(CustodyAssignment).where(CustodyAssignment.id == f['custody']).values(valid_to=f['now']))
                    elif label == 'organization':
                        rival.execute(update(Organization).where(Organization.id == f['owner']).values(status='inactive'))
                    else:
                        rival.execute(CustodyAssignment.__table__.insert().values(id=uuid4(),location_id=f['location'],
                            custodian_person_id=f['people']['apply'],valid_from=f['now']+timedelta(days=4),
                            valid_to=f['now']+timedelta(days=5)))
                except DBAPIError as error:
                    assert error.orig.sqlstate == '55P03', str(error.orig)
                    rival.rollback(); blocked.append(label)
                else:
                    rival.rollback(); raise AssertionError('authority dependency changed during admission')
    attempt(api, params(f,'execute'), before_commit=contenders)
    assert count(owner) == before+1
    return dict(specificPrincipalWaitObserved=True, revocationAfterWaitRejected=True, blocked=blocked)


def run(engines):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    from app.database_security import validate_production_database_security
    validate_production_database_security(api, expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
    root = Path(__file__).parents[1] / 'alembic/return_condition_candidate'
    f = seed(owner)
    with owner.begin() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261215_0166'
        db.execute(text((root/'authority.sql').read_text()))
        db.exec_driver_sql(SCHEMA)
        for role in ('star_oam_api','star_oam_projector','star_oam_edge','edge_inbox','star_oam_backup'):
            assert not db.scalar(text("SELECT has_function_privilege(:r,:s,'EXECUTE')"),dict(r=role,s=SIGNATURE))
    positive=[]; rejected=[]; late=[]
    for kind in ACTIONS:
        actor=who(kind); values=params(f,kind)
        role='admin' if actor=='headquarters' else 'provincial_manager'
        attempt(api, values); positive.append(kind)
        cases = {
            'version': (User,f['users'][actor],dict(authorization_version=2)),
            'disabled': (User,f['users'][actor],dict(is_active=False)),
            'left': (Person,f['people'][actor],dict(employment_status='left')),
            'identity': (AuthIdentity,f['identities'][actor],dict(status='pending',verified_at=None)),
            'expired': (RoleAssignment,f['assignments'][actor],dict(valid_to=f['now']-timedelta(seconds=1))),
            'future': (RoleAssignment,f['assignments'][actor],dict(valid_from=f['now']+timedelta(days=1))),
            'role': (Role,f['roles'][role],dict(status='inactive')),
            'deny': (RolePermission,f['condition_grants'][ACTIONS[kind]],dict(effect='deny')),
            'missing_action': (RolePermission,f['condition_grants'][ACTIONS[kind]],dict(permission_id=f['dummy'])),
            'scope': (RoleAssignment,f['assignments'][actor],dict(scope_type='organization',scope_id=str(f['other']))),
            'location': (StockLocation,f['location'],dict(status='inactive')),
            'custodian': (StockLocation,f['location'],dict(custodian_person_id=f['people']['regional'])),
            'custody_expired': (CustodyAssignment,f['custody'],dict(valid_to=f['now']-timedelta(seconds=1))),
            'custody_person': (CustodyAssignment,f['custody'],dict(custodian_person_id=f['people']['regional'])),
            'custody_overlap': (CustodyAssignment,f['second_custody'],dict(valid_from=f['now']-timedelta(days=1))),
            'owner': (Organization,f['owner'],dict(status='inactive')),
            'ancestor': (Organization,f['hq'],dict(status='inactive')),
        }
        if actor == 'headquarters':
            cases['hq_affiliation']=(Person,f['people'][actor],dict(organization_id=f['owner']))
        for label, edit in cases.items():
            before=count(owner)
            with patched(owner,[edit]):
                attempt(api,params(f,kind),rejected=True)
            assert count(owner)==before
            rejected.append(kind+':'+label)
        # Privileged test-only catalog edits in the same transaction prove the
        # deferred trigger independently; API catalog writes remain prohibited.
        for label in ('version','deny','expired','custodian','custody_expired','owner'):
            before=count(owner)
            attempt(owner,params(f,kind),rejected=True,phase='commit',late=[cases[label]])
            assert count(owner)==before
            late.append(kind+':'+label)
        for key,value in (('custody_id',uuid4()),('actor_person',uuid4()),('owner_id',f['other']),('kind','unknown')):
            v=params(f,kind); v[key]=value
            attempt(api,v,rejected=True); rejected.append(kind+':forged_'+key)
        attempt(api,params(f,kind),rejected=True,isolation='REPEATABLE READ')
        rejected.append(kind+':snapshot_isolation')
    expired=expiry_checks(owner,api,f); concurrency=races(owner,api,f)
    # Compile the event wrapper against all real candidate tables. This check
    # deliberately does not claim a persisted full-event business transaction.
    from pg16_return_condition_schema import compile_structure
    with owner.connect() as db:
        for sql in compile_structure()[3]:
            db.execute(text(sql))
        db.execute(text((root/'authority_event.sql').read_text()))
        assert db.scalar(text("SELECT tgenabled FROM pg_trigger WHERE tgname='condition_current_authority'"))=='A'
        with db.begin_nested() as save:
            try:
                db.execute(text('SELECT public.rsc_condition_check_current_event(:id)'),dict(id=uuid4()))
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514' and 'exact current event' in str(error.orig)
                save.rollback()
            else:
                raise AssertionError('missing event accepted')
        db.rollback()
    with owner.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM inventory_transactions'))==0
        assert db.scalar(text("SELECT to_regclass('public.stock_condition_events')")) is None
    return dict(passed=True,positive=positive,rejected=rejected,deferredRejected=late,apiExpiredAtCommit=expired,
        concurrency=concurrency,realIdentityCatalog=True,realApiProbeCommits=True,eventWrapperCompiled=True,
        eventBusinessBindingVerified=False,fullBusinessPosting=False,delegatedReviewVerified=False,
        formalMigration=False,productionAcceptance=False)
