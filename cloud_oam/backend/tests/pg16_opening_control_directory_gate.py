"""Actual API role reads redacted summaries; private facts remain unreadable."""
from pathlib import Path
import runpy
from uuid import uuid4
from unittest.mock import patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import get_settings
from app.formal_access import load_formal_principal
from app.formal_services.opening_control_directory import SQL,list_control_batches
from app.foundation_models import Organization,Permission,Role,RolePermission
from app.routers.formal_opening_start_options import router
from test_formal_access import make_organization,make_user,assign


def assert_opening_control_directory_gate(owner,api,edge,projector,backup,world,history):
    from pg16_control_publication_gate import publication_snapshot
    from pg16_inventory_control_preparation_gate import _formal_stock
    before=(publication_snapshot(owner),_formal_stock(owner))
    args=dict(user=world.actor.user_id,version=world.actor.authorization_version,region=world.region,limit=1,after=None)
    seen=[];cursor=None
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert db.execute(text('SELECT current_user,session_user')).one()==('star_oam_api','star_oam_api')
        while True:
            page=list_control_batches(db,actor=load_formal_principal(db,world.actor.user_id),region_org_id=world.region,
                limit=1,after_id=cursor)
            assert page.start_ready is False and page.admission_status=='not_evaluated'
            seen.extend(page.items);cursor=page.next_after_id
            if cursor is None:break
    assert {str(row.publication_id) for row in seen}=={result['publication_id'] for result,_ in history}
    assert [str(row.publication_id) for row in seen if row.is_latest]==[history[-1][0]['publication_id']]
    assert sum(row.record_count==0 for row in seen)==1
    # Exercise actual FastAPI adapter with the restricted connection too.
    app=FastAPI();app.include_router(router,prefix='/api')
    def session():
        with Session(api) as db:yield db
    app.dependency_overrides[get_db]=session
    client=TestClient(app)
    url='/api/v1/stocktakes/opening/start-options/control-batches'
    params={'region_org_id':str(world.region)}
    # Use the production formal-authentication branch against this explicitly
    # owned synthetic DB. Test-mode legacy users.province must not substitute
    # for formal regional assignments. JWT/session/principal are not mocked.
    with patch('app.dependencies.get_settings',return_value=get_settings().model_copy(update={'environment':'production'})):
        assert client.get(url,params=params).status_code==401
        response=client.get(url,params=params,headers={'Authorization':'Bearer '+world.login['access_token']})
        print('PG16 directory production JWT HTTP status: '+str(response.status_code),flush=True)
        assert response.status_code==200,response.text
    assert 'no-store' in response.headers['cache-control']
    body=response.json()
    for row in body['items']:
        assert set(row)=={'publication_id','source_system_id','source_name','captured_at','published_at','valid_until','record_count','is_latest'}
    for changed in ({'version':args['version']+1},{'region':uuid4()},{'user':'missing-user'}):
        with api.connect() as db:
            with pytest.raises(DBAPIError) as error:db.execute(text(SQL),args|changed)
            assert error.value.orig.sqlstate=='42501'
    for engine in (edge,projector,backup):
        with engine.connect() as db:
            with pytest.raises(DBAPIError) as error:db.execute(text(SQL),args)
            assert error.value.orig.sqlstate=='42501'
    with api.connect().execution_options(isolation_level='REPEATABLE READ') as db:
        with pytest.raises(DBAPIError) as error:db.execute(text(SQL),args)
        assert error.value.orig.sqlstate=='25001'
    # Exact region manager can read their region only, even when the foreign
    # region exists and is active. SQL capability must enforce this itself.
    with Session(owner) as db:
        region=db.get(Organization,world.region)
        foreign=make_organization(db,name='Synthetic foreign directory region',org_type='region_company')
        user,_=make_user(db,region,name='Synthetic directory region manager')
        role=db.scalars(select(Role).where(Role.code=='provincial_manager')).one()
        assignment=assign(db,user,role,scope_type='organization',scope_id=str(region.id))
        db.commit();manager_id=user.id;foreign_id=foreign.id;manager_role_id=assignment.role_id
    manager=args|dict(user=manager_id,version=1)
    with api.connect() as db:
        assert db.scalar(text(SQL),manager)['items']
        with pytest.raises(DBAPIError) as error:db.execute(text(SQL),manager|dict(region=foreign_id))
        assert error.value.orig.sqlstate=='42501'
    # Temporary global deny and disabled user are tested in owner transactions,
    # then rolled back; no shared role policy or user state is left changed.
    with Session(owner) as db:
        permission=db.scalars(select(Permission).where(Permission.resource=='stocktake',Permission.action=='manage',Permission.field_code=='')).one()
        from app.models import User
        permission_id=permission.id
        for mutation in ('deny','inactive'):
            with pytest.raises(DBAPIError) as error:
                with db.begin_nested():
                    if mutation=='deny':
                        db.scalars(select(RolePermission).where(RolePermission.role_id==manager_role_id,RolePermission.permission_id==permission_id)).one().effect='deny'
                    else:db.get(User,manager_id).is_active=False
                    db.flush()
                    db.execute(text(SQL),manager)
            assert error.value.orig.sqlstate=='42501'
            db.rollback()
        # A selected national allow must not bypass a different assignment's
        # organization deny, including a descendant region.
        with pytest.raises(DBAPIError) as error:
            with db.begin_nested():
                child=make_organization(db,name='Synthetic descendant directory region',parent=db.get(Organization,world.region))
                assign(db,db.get(User,world.actor.user_id),db.get(Role,manager_role_id),
                    scope_type='organization',scope_id=str(world.region))
                db.scalars(select(RolePermission).where(RolePermission.role_id==manager_role_id,
                    RolePermission.permission_id==permission_id)).one().effect='deny'
                db.flush()
                db.execute(text(SQL),args|dict(region=child.id))
        assert error.value.orig.sqlstate=='42501'
        db.rollback()
    # Downgrade preflight rejects a drifted function/ACL before removing it.
    migration=runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/20261103_0124_opening_control_directory.py'))
    for drift in ('body','public','missing_api'):
        with owner.connect() as db:
            signature=migration['SIGNATURE']
            if drift=='body':
                definition=db.scalar(text('SELECT pg_get_functiondef(CAST(:signature AS regprocedure))'),{'signature':signature})
                definition=definition.replace(migration['BODY'],migration['BODY']+'\n-- drift\n')
                db.exec_driver_sql(definition,execution_options={'no_parameters':True})
            elif drift=='public':db.exec_driver_sql('GRANT EXECUTE ON FUNCTION '+signature+' TO PUBLIC')
            else:db.exec_driver_sql('REVOKE EXECUTE ON FUNCTION '+signature+' FROM star_oam_api')
            try:
                with Operations.context(MigrationContext.configure(db)),pytest.raises(DBAPIError) as error:migration['downgrade']()
                assert '0124 directory function source or ACL drift' in str(error.value.orig)
            finally:db.rollback()
    admission=runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions/20261104_0125_opening_publication_admission.py'))
    with owner.begin() as db,Operations.context(MigrationContext.configure(db)):
        migration['_verify']();admission['_verify']()
    assert (publication_snapshot(owner),_formal_stock(owner))==before
    print('PG16 0124 actual API directory: exact scope, historical/zero pagination, redaction, deny/stale identity, private roles and migration drift PASS',flush=True)
