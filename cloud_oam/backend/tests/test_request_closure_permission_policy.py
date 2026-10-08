"""Shared close permission, additive grants and preserved explicit denial."""
from pathlib import Path
import runpy
from uuid import uuid4

import pytest
import sqlalchemy as sa

from test_return_condition_permission_policy import store, snapshot, NOW

policy = runpy.run_path(str(Path(__file__).parents[1]/'alembic/request_closure_0169/permission_policy.py'))


def test_one_shared_permission_two_grants_idempotent_and_no_technician_or_external_grant(store):
    engine,tables = store
    with engine.begin() as db:
        before=snapshot(db,tables)
        result=policy['install'](db)
        assert result==dict(createdPermissions=1,createdGrants=2,preservedExplicitDeny=[],advancedUsers=3)
        p,g,r=(tables[n] for n in ('permissions','role_permissions','roles'))
        assert set(db.execute(sa.select(r.c.code,p.c.resource,p.c.action,g.c.effect).select_from(
            g.join(r,g.c.role_id==r.c.id).join(p,g.c.permission_id==p.c.id)))) == {
            ('admin','material_request','close','allow'),('provincial_manager','material_request','close','allow')}
        assert dict(db.execute(sa.select(tables['users'])).all())==dict(admin=8,provincial_manager=8,
            technician=7,multiple=8,external=7,unassigned=7)
        after=snapshot(db,tables)
        assert after['roles']==before['roles'] and after['role_assignments']==before['role_assignments']
        assert policy['install'](db)==dict(createdPermissions=0,createdGrants=0,preservedExplicitDeny=[],advancedUsers=0)
        assert snapshot(db,tables)==after


def test_custom_permission_and_explicit_deny_are_kept(store):
    engine,tables=store
    with engine.begin() as db:
        identifier,grant=uuid4(),uuid4()
        db.execute(tables['permissions'].insert(),dict(id=identifier,resource='material_request',action='close',
            field_code='',description='Existing custom control',created_at=NOW,updated_at=NOW))
        db.execute(tables['role_permissions'].insert(),dict(id=grant,role_id=policy['ROLES']['admin'],
            permission_id=identifier,effect='deny',created_at=NOW))
        before=snapshot(db,tables)
        assert policy['install'](db)==dict(createdPermissions=0,createdGrants=1,preservedExplicitDeny=['close'],advancedUsers=1)
        assert snapshot(db,tables)['permissions']==before['permissions']
        assert db.scalar(sa.select(tables['role_permissions'].c.effect).where(tables['role_permissions'].c.id==grant))=='deny'
        assert policy['preserve_on_downgrade'](db)['grantsRevoked']==0


@pytest.mark.parametrize('invalid',['reserved_permission','role','version'])
def test_preflight_is_atomic(store,invalid):
    engine,tables=store
    with engine.begin() as db:
        if invalid=='reserved_permission':
            db.execute(tables['permissions'].insert(),dict(id=policy['permission_id']('close'),resource='other',
                action='other',field_code='',description='',created_at=NOW,updated_at=NOW))
        elif invalid=='role':
            db.execute(tables['roles'].update().where(tables['roles'].c.code=='admin').values(is_external=True))
        else:
            db.execute(tables['users'].update().where(tables['users'].c.id=='admin').values(authorization_version=2**63-1))
        before=snapshot(db,tables)
        with pytest.raises(ValueError):
            policy['install'](db)
        assert snapshot(db,tables)==before
