"""Actual protected PG16 material authorization, concurrent grants and receipt locks."""
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

from app import material_source_authority as service
from app import inventory_control_authority as authority
from app.formal_access import FormalAccessError,load_formal_principal
from app.foundation_models import FileObject,Organization,Person
from app.models import AuthSession,User
from app.material_capture_models import MaterialCaptureBinding as Binding
from app.routers import integrations
from app.security import create_access_token
from test_material_source_authority import command
from test_material_capture_ingress import SOURCE,KEY,SECRET,bundle,packet,proof
from pg16_inventory_control_preparation_gate import _formal_stock,_rejected


def snapshot(engine):
    with engine.connect() as c:
        return tuple(c.execute(text('SELECT id,payload_sha256,audit_event_id FROM public.material_source_authority_decisions ORDER BY created_at,id')))


def assert_material_source_authority_gate(owner_engine,api_engine,edge_engine,projector_engine,backup_engine):
    stock_before=_formal_stock(owner_engine)
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        actor=None
        for identifier in db.scalars(select(User.id).join(Person,Person.id==User.person_id).join(Organization,Organization.id==Person.organization_id)
                .where(Organization.org_type=='headquarters',Organization.status=='active',User.account_status=='active',User.is_active.is_(True)).order_by(User.id)):
            try:
                current=load_formal_principal(db,identifier)
                authority._operator(db,current,authority._now(db),permission_resource='material_source')
            except (FormalAccessError,authority.ControlAuthorityError):continue
            actor=current;break
        assert actor is not None
        binding=db.scalars(select(Binding).where(Binding.source_instance==SOURCE,Binding.key_id==KEY+'-rotated')).one()
        file, _ = source_evidence(db, actor.user_id)
        now=authority._now(db)
        session=AuthSession(user_id=actor.user_id,refresh_token_hash=uuid4().hex+uuid4().hex,client_type='web',
            device_id='synthetic-material-authority-'+uuid4().hex,ip_address='hmac:1:'+'a'*64,
            created_at=now-timedelta(seconds=1),expires_at=now+timedelta(hours=1))
        db.add_all([file,session]);db.commit()
        world=SimpleNamespace(actor=actor,binding=binding.id,file=file.id,source=binding.source_system_id)
        args=dict(access_token=create_access_token(actor.user_id,session.id),expected_authorization_version=actor.authorization_version,
            command=command(db,world))
        args['review_sha256']=service.preview_material_source_authority(db,**args)['review_sha256']
    barrier=Barrier(2)
    def execute():
        with Session(owner_engine) as db:
            barrier.wait(timeout=20);result=service.execute_material_source_authority(db,**args);db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:first,second=list(pool.map(lambda _:execute(),range(2)))
    assert first==second and first['recorded'] and len(snapshot(owner_engine))==1
    settings=integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret=SECRET,
        edge_sync_allowed_sources=SOURCE,edge_material_capture_enabled=True,edge_material_capture_key_id=KEY+'-rotated',
        edge_sync_legacy_batches_enabled=False,edge_sync_legacy_personnel_projection_enabled=False))
    with pytest.MonkeyPatch.context() as patch,Session(edge_engine) as db:
        patch.setattr(integrations,'settings',settings)
        # The authorization already exists before this new capture starts.
        value=bundle(now=authority._now(db))
        received=integrations.receive_material_master_capture(proof(packet(value,key=KEY+'-rotated')),db)
    receipt_id=UUID(received['receipt_id'])
    with Session(owner_engine) as db:
        assert service.read_material_source_authority(db,**args)==first
        revoke_args={**args,'command':command(db,world,action='revoke',valid_to=None,revoked_grant_id=first['decision_id'],expected_subject_sha256=first['payload_sha256'])}
        revoke_args.pop('review_sha256');revoke_args['review_sha256']=service.preview_material_source_authority(db,**revoke_args)['review_sha256']
    with Session(owner_engine) as holder:
        admitted=service.inspect_authorized_material_capture(holder,receipt_id=receipt_id)
        assert admitted['source_authorized'] and not admitted['projection_published']
        with Session(owner_engine) as contender:
            contender.execute(text("SET LOCAL lock_timeout='200ms'"))
            with pytest.raises(DBAPIError) as blocked:service.execute_material_source_authority(contender,**revoke_args)
            assert blocked.value.orig.sqlstate=='55P03';contender.rollback()
        for statement,identifier in [
            ('UPDATE public.files SET status=\'quarantined\' WHERE id=:id',world.file),
            ('UPDATE public.source_systems SET enabled=false WHERE id=:id',world.source),
            ('UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=:id',world.binding),
        ]:
            with owner_engine.connect() as contender:
                contender.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError) as blocked:contender.execute(text(statement),{'id':identifier})
                assert blocked.value.orig.sqlstate=='55P03';contender.rollback()
        holder.rollback()
    for engine in (api_engine,edge_engine,projector_engine):
        _rejected(engine,'SELECT * FROM public.material_source_authority_decisions',state='42501')
        _rejected(engine,'INSERT INTO public.material_source_authority_decisions DEFAULT VALUES',state='42501')
    for sql in ('UPDATE public.material_source_authority_decisions SET subject_sha256=subject_sha256',
        'DELETE FROM public.material_source_authority_decisions','TRUNCATE public.material_source_authority_decisions'):
        _rejected(owner_engine,sql,state='23514',message='append-only')
    with Session(owner_engine) as db:
        service.execute_material_source_authority(db,**revoke_args);db.commit()
        with pytest.raises(service.MaterialSourceAuthorityError,match='source_authority_unavailable'):
            service.inspect_authorized_material_capture(db,receipt_id=receipt_id)
    assert len(snapshot(owner_engine))==2 and snapshot(owner_engine)==snapshot(backup_engine)
    assert _formal_stock(owner_engine)==stock_before
    print('PG16 material authority: current HQ session, concurrent grants, receipt/source/file/transport locks, revocation, immutable audit and ACL PASS',flush=True)
