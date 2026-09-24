"""Real disposable PG16 mapping decisions, direct-owner ACL, concurrency and retention."""
from source_configuration_file_fixtures import source_evidence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import inventory_control_mapping as mapping
from app import inventory_control_authority as authority
from app.formal_access import FormalAccessError,load_formal_principal
from app.foundation_models import FileObject,Organization,Person
from app.inventory_control_mapping_models import InventoryControlMappingDecision as Decision
from app.inventory_control_models import InventoryControlPreparation as Preparation
from app.models import AuthSession,User
from app.security import create_access_token
from test_inventory_control_mapping import command
from pg16_inventory_control_preparation_gate import _formal_stock


def snapshot(engine):
    with engine.connect() as connection:
        return tuple(connection.execute(text('SELECT id,payload_sha256,audit_event_id FROM public.inventory_control_mapping_decisions ORDER BY created_at,id')))


def assert_inventory_control_mapping_gate(owner_engine,api_engine,edge_engine,projector_engine,backup_engine):
    stock_before=_formal_stock(owner_engine)
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        actor=None
        candidates=db.scalars(select(User.id).join(Person,Person.id==User.person_id).join(Organization,Organization.id==Person.organization_id)
            .where(Organization.org_type=='headquarters',Organization.status=='active',User.account_status=='active',User.is_active.is_(True)).order_by(User.id))
        for identifier in candidates:
            try:
                current=load_formal_principal(db,identifier);authority._operator(db,current,authority._now(db))
            except (FormalAccessError,authority.ControlAuthorityError):continue
            actor=current;break
        assert actor is not None
        root=db.scalars(select(Preparation).order_by(Preparation.created_at.desc(),Preparation.id).limit(1)).one()
        file, _ = source_evidence(db, actor.user_id)
        now=authority._now(db)
        session=AuthSession(user_id=actor.user_id,refresh_token_hash=uuid4().hex+uuid4().hex,client_type='web',
            device_id='synthetic-mapping-'+uuid4().hex,ip_address='hmac:1:'+'a'*64,
            created_at=now-timedelta(seconds=1),expires_at=now+timedelta(hours=1))
        db.add_all([file,session]);db.commit()
        world=SimpleNamespace(actor=actor,binding=root.binding_id,catalog=root.catalog_id,file=file.id)
        args=dict(access_token=create_access_token(actor.user_id,session.id),expected_authorization_version=actor.authorization_version,
                  command=command(db,world))
        args['review_sha256']=mapping.preview_inventory_control_mapping(db,**args)['review_sha256']
    barrier=Barrier(2)
    def execute():
        with Session(owner_engine) as db:
            barrier.wait(timeout=20)
            result=mapping.execute_inventory_control_mapping(db,**args);db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=list(pool.map(lambda _:execute(),range(2)))
    assert first==second and first['recorded']
    saved=snapshot(owner_engine);assert len(saved)==1 and snapshot(backup_engine)==saved
    with Session(owner_engine) as db:
        assert mapping.read_inventory_control_mapping(db,**args)==first
        revoke_args={**args,'command':command(db,world,action='revoke',rules=None,revoked_grant_id=first['decision_id'],expected_subject_sha256=first['payload_sha256'])}
        revoke_args.pop('review_sha256')
        revoke_args['review_sha256']=mapping.preview_inventory_control_mapping(db,**revoke_args)['review_sha256']
    with Session(owner_engine) as holder:
        resolved=mapping.resolve_inventory_control_mapping(holder,binding_id=world.binding,catalog_id=world.catalog,decision_id=UUID(first['decision_id']))
        assert resolved['rules_sha256']==first['rules_sha256']
        with Session(owner_engine) as contender:
            contender.execute(text("SET LOCAL lock_timeout='200ms'"))
            with pytest.raises(DBAPIError) as error:mapping.execute_inventory_control_mapping(contender,**revoke_args)
            assert error.value.orig.sqlstate=='55P03';contender.rollback()
        with owner_engine.connect() as connection:
            connection.execute(text("SET LOCAL lock_timeout='200ms'"))
            with pytest.raises(DBAPIError) as error:connection.execute(text("UPDATE public.files SET status='quarantined' WHERE id=:id"),{'id':world.file})
            assert error.value.orig.sqlstate=='55P03';connection.rollback()
        holder.rollback()
    for sql in ['UPDATE public.inventory_control_mapping_decisions SET rules_revision=rules_revision',
                'DELETE FROM public.inventory_control_mapping_decisions','TRUNCATE public.inventory_control_mapping_decisions']:
        with owner_engine.connect() as connection:
            with pytest.raises(DBAPIError) as error:connection.execute(text(sql))
            assert error.value.orig.sqlstate=='23514';connection.rollback()
    for engine in (api_engine,edge_engine,projector_engine):
        for sql in ['SELECT * FROM public.inventory_control_mapping_decisions','INSERT INTO public.inventory_control_mapping_decisions DEFAULT VALUES']:
            with engine.connect() as connection:
                with pytest.raises(DBAPIError) as error:connection.execute(text(sql))
                assert error.value.orig.sqlstate=='42501';connection.rollback()
    with Session(owner_engine) as db:
        mapping.execute_inventory_control_mapping(db,**revoke_args);db.commit()
        with pytest.raises(mapping.ControlMappingError,match='mapping_unavailable'):
            mapping.resolve_inventory_control_mapping(db,binding_id=world.binding,catalog_id=world.catalog,decision_id=UUID(first['decision_id']))
    assert len(snapshot(owner_engine))==2 and snapshot(owner_engine)==snapshot(backup_engine)
    assert _formal_stock(owner_engine)==stock_before
    print('PG16 control mappings: actual grants/revocation, current web session, concurrent replay, binding/file locks, immutable facts and private ACL PASS',flush=True)
