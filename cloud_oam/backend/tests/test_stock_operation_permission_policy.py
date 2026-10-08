"""Additive policy safety; SQLite tooling is not PostgreSQL role acceptance."""
from datetime import datetime, timezone
from uuid import uuid4, UUID

import pytest
import sqlalchemy as sa
from pathlib import Path
from types import SimpleNamespace
import runpy

policy = SimpleNamespace(**runpy.run_path(str(Path(__file__).parents[1] / 'alembic/stock_scrap_0165/permission_policy.py')))

EXTERNAL = UUID('10000000-0000-4000-8000-000000000004')
NOW = datetime.now(timezone.utc)


@pytest.fixture
def store():
    engine = sa.create_engine('sqlite://')
    metadata = sa.MetaData()
    roles = sa.Table('roles', metadata, sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('code', sa.String(), unique=True), sa.Column('is_external', sa.Boolean()), sa.Column('status', sa.String()))
    users = sa.Table('users', metadata, sa.Column('id', sa.String(), primary_key=True),
        sa.Column('authorization_version', sa.BigInteger()))
    definitions = sa.Table('permissions', metadata, sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('resource', sa.String()), sa.Column('action', sa.String()), sa.Column('field_code', sa.String()),
        sa.Column('description', sa.String()), sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)), sa.UniqueConstraint('resource', 'action', 'field_code'))
    grants = sa.Table('role_permissions', metadata, sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('role_id', sa.Uuid(), sa.ForeignKey('roles.id')), sa.Column('permission_id', sa.Uuid(), sa.ForeignKey('permissions.id')),
        sa.Column('effect', sa.String()), sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('role_id', 'permission_id'))
    assignments = sa.Table('role_assignments', metadata, sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('role_id', sa.Uuid(), sa.ForeignKey('roles.id'), primary_key=True))
    with engine.begin() as db:
        db.execute(sa.text('PRAGMA foreign_keys=ON'))
        metadata.create_all(db)
        db.execute(roles.insert(), [dict(id=identifier, code=role, is_external=False, status='active')
            for role, identifier in policy.ROLES.items()] + [dict(id=EXTERNAL, code='star_headquarters_approver', is_external=True, status='active')])
        db.execute(users.insert(), [dict(id=role, authorization_version=7) for role in (*policy.ROLES, 'external', 'unassigned', 'multiple')])
        db.execute(assignments.insert(), [dict(user_id=role, role_id=identifier) for role, identifier in policy.ROLES.items()]
            + [dict(user_id='external', role_id=EXTERNAL), dict(user_id='multiple', role_id=policy.ROLES['admin']),
               dict(user_id='multiple', role_id=policy.ROLES['technician'])])
    yield engine, metadata.tables
    engine.dispose()


def snapshot(db, tables):
    return {name: sorted([tuple(row) for row in db.execute(sa.select(table))], key=repr) for name, table in tables.items()}


def custom(db, tables, action, role, effect='deny', identifier=None, grant_identifier=None):
    identifier = identifier or uuid4(); grant_identifier = grant_identifier or uuid4()
    db.execute(tables['permissions'].insert(), dict(id=identifier, resource='stock_operation', action=action,
        field_code='', description='Existing reviewed custom definition', created_at=NOW, updated_at=NOW))
    db.execute(tables['role_permissions'].insert(), dict(id=grant_identifier, role_id=policy.ROLES[role],
        permission_id=identifier, effect=effect, created_at=NOW))
    return identifier, grant_identifier


def test_exact_eleven_defaults_no_external_grant_one_version_bump_per_person(store):
    engine, tables = store
    with engine.begin() as db:
        before = snapshot(db, tables)
        proof = policy.install(db)
        assert proof == dict(createdPermissions=11, createdGrants=11, preservedExplicitDeny=[], advancedUsers=4)
        p, g, r = (tables[name] for name in ('permissions', 'role_permissions', 'roles'))
        actual = set(db.execute(sa.select(r.c.code, p.c.resource, p.c.action, p.c.field_code, g.c.effect)
            .select_from(g.join(r, g.c.role_id==r.c.id).join(p, g.c.permission_id==p.c.id))).all())
        assert actual == {(role, 'stock_operation', action, '', 'allow') for role, action in policy.DEFAULTS}
        assert len(policy.DEFAULTS) == len({action for _, action in policy.DEFAULTS}) == 11
        versions = dict(db.execute(sa.select(tables['users'])).all())
        assert versions == dict(admin=8, provincial_manager=8, technician=8, multiple=8, external=7, unassigned=7)
        after = snapshot(db, tables)
        assert after['roles'] == before['roles'] and after['role_assignments'] == before['role_assignments']
        assert policy.install(db) == dict(createdPermissions=0, createdGrants=0, preservedExplicitDeny=[], advancedUsers=0)
        assert snapshot(db, tables) == after


@pytest.mark.parametrize('effect', ['allow', 'deny'])
def test_existing_custom_identity_description_and_effect_are_preserved(store, effect):
    engine, tables = store
    with engine.begin() as db:
        identifier, grant = custom(db, tables, 'submit_loss', 'technician', effect)
        before_permission = db.execute(sa.select(tables['permissions'])).one()
        before_grant = db.execute(sa.select(tables['role_permissions'])).one()
        proof = policy.install(db)
        assert proof['createdPermissions'] == proof['createdGrants'] == 10
        assert proof['preservedExplicitDeny'] == (['submit_loss'] if effect == 'deny' else [])
        assert db.execute(sa.select(tables['permissions']).where(tables['permissions'].c.id==identifier)).one() == before_permission
        assert db.execute(sa.select(tables['role_permissions']).where(tables['role_permissions'].c.id==grant)).one() == before_grant


def test_custom_definition_without_target_grant_is_reused_and_unrelated_grant_preserved(store):
    engine, tables = store
    with engine.begin() as db:
        identifier, grant = custom(db, tables, 'submit_loss', 'admin')
        policy.install(db)
        p, g = tables['permissions'], tables['role_permissions']
        assert db.scalar(sa.select(sa.func.count()).select_from(p).where(p.c.action=='submit_loss')) == 1
        assert db.scalar(sa.select(g.c.permission_id).where(g.c.id==policy.grant_id('technician','submit_loss'))) == identifier
        assert db.scalar(sa.select(g.c.effect).where(g.c.id==grant)) == 'deny'


@pytest.mark.parametrize('case', ['permission_collision', 'grant_collision', 'role_identity', 'external_flag', 'overflow'])
def test_all_preflight_failures_leave_every_row_unchanged(store, case):
    engine, tables = store
    with engine.begin() as db:
        if case == 'permission_collision':
            custom(db, tables, 'unrelated', 'admin', identifier=policy.permission_id('execute_scrap_recovery'))
        elif case == 'grant_collision':
            custom(db, tables, 'unrelated', 'admin', grant_identifier=policy.grant_id('admin', 'execute_scrap_recovery'))
        elif case == 'role_identity':
            db.execute(tables['roles'].update().where(tables['roles'].c.code=='admin').values(code='other_admin'))
        elif case == 'external_flag':
            db.execute(tables['roles'].update().where(tables['roles'].c.code=='admin').values(is_external=True))
        else:
            db.execute(tables['users'].update().where(tables['users'].c.id=='technician').values(authorization_version=2**63-1))
        before = snapshot(db, tables)
        with pytest.raises(ValueError):
            policy.install(db)
        assert snapshot(db, tables) == before


def test_inactive_role_is_not_reactivated(store):
    engine, tables = store
    with engine.begin() as db:
        db.execute(tables['roles'].update().where(tables['roles'].c.code=='admin').values(status='inactive'))
        policy.install(db)
        assert db.scalar(sa.select(tables['roles'].c.status).where(tables['roles'].c.code=='admin')) == 'inactive'


def test_caller_rollback_removes_all_new_grants_and_version_changes(store):
    engine, tables = store
    with engine.connect() as db:
        before = snapshot(db, tables); db.rollback()
        with pytest.raises(RuntimeError):
            with db.begin():
                policy.install(db)
                raise RuntimeError('simulated later migration failure')
        assert snapshot(db, tables) == before


def test_application_downgrade_preserves_authorization_data_including_later_deny(store):
    engine, tables = store
    with engine.begin() as db:
        policy.install(db)
        db.execute(tables['role_permissions'].update().where(tables['role_permissions'].c.id==
            policy.grant_id('admin','execute_scrap_recovery')).values(effect='deny'))
        before = snapshot(db, tables)
        assert policy.preserve_on_downgrade(db) == dict(authorizationDataRetained=True, grantsRevoked=0, definitionsDeleted=0)
        assert snapshot(db, tables) == before
        assert policy.install(db) == dict(createdPermissions=0, createdGrants=0,
            preservedExplicitDeny=['execute_scrap_recovery'], advancedUsers=0)
        assert snapshot(db, tables) == before


def test_no_implicit_transaction_or_commit(store):
    engine, tables = store
    with engine.connect() as db:
        with pytest.raises(ValueError, match='caller-owned transaction'):
            policy.install(db)
