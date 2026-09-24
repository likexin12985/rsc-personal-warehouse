"""Actual disposable PG16 publication, concurrency, graph, ACL and recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import material_projection as service,material_source_authority as source
from app import inventory_control_authority as authority
from app.foundation_models import ExternalObjectVersion
from app.formal_access import load_formal_principal
from app.formal_services.material_catalog import list_active_materials
from app.material_source_authority_models import MaterialSourceAuthorityDecision as Decision
from app.material_projection_models import MaterialProjectionPublication as Publication,MaterialProjectionLine as Line
from app.material_capture_models import MaterialCaptureBinding as Binding
from app.security import create_access_token
from app.routers import integrations
from test_material_projection import command
from test_material_source_authority import command as grant_command
from test_material_capture_ingress import SOURCE,KEY,SECRET,bundle,packet,proof
from pg16_inventory_control_preparation_gate import _formal_stock,_rejected


def snapshot(engine):
    with engine.connect() as db:
        return tuple(tuple(db.scalars(text('SELECT to_jsonb(t) FROM public.'+table+' t ORDER BY id')))
                     for table in ('material_projection_publications','material_projection_lines'))


def assert_material_projection_gate(owner_engine,api_engine,edge_engine,projector_engine,backup_engine):
    before=_formal_stock(owner_engine)
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        previous=db.scalars(select(Decision).order_by(Decision.created_at.desc())).first()
        assert previous is not None
        actor=load_formal_principal(db,previous.actor_user_id)
        binding=db.get(Binding,previous.binding_id)
        world=SimpleNamespace(actor=actor,binding=binding.id,file=previous.evidence_file_id,source=binding.source_system_id)
        login=dict(access_token=create_access_token(actor.user_id,previous.auth_session_id),expected_authorization_version=actor.authorization_version)
        grant=grant_command(db,world,valid_to=min(authority._now(db)+timedelta(minutes=35),service.preparation._aware(binding.valid_to)))
        review=source.preview_material_source_authority(db,**login,command=grant)
        source.execute_material_source_authority(db,**login,command=grant,review_sha256=review['review_sha256']);db.commit()
    code='GATE-PUBLICATION-'+uuid4().hex.upper()
    settings=integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret=SECRET,
        edge_sync_allowed_sources=SOURCE,edge_material_capture_enabled=True,edge_material_capture_key_id=KEY+'-rotated',
        edge_sync_legacy_batches_enabled=False,edge_sync_legacy_personnel_projection_enabled=False))
    def capture(name):
        with pytest.MonkeyPatch.context() as patch,Session(edge_engine) as db:
            patch.setattr(integrations,'settings',settings)
            raw=[dict(materialCode=code,materialName=name,unitCode='EA',unitName='piece',materialStatus=0)]
            value=bundle(raw,now=authority._now(db))
            received=integrations.receive_material_master_capture(proof(packet(value,key=KEY+'-rotated')),db)
            return UUID(received['receipt_id'])
    receipt_id=capture('Synthetic A')
    with Session(owner_engine) as db:
        cmd=command(db,world,receipt_id)
        review=service.preview_material_publication(db,**login,command=cmd)
        args=dict(**login,command=cmd,review_sha256=review['review_sha256'])
    barrier=Barrier(2)
    def publish():
        with Session(owner_engine) as db:
            barrier.wait(timeout=20)
            result=service.execute_material_publication(db,**args);db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:first,second=list(pool.map(lambda _:publish(),range(2)))
    assert first==second and first['projection_published'] and not first['start_ready']
    with Session(owner_engine) as db:
        line=db.scalars(select(Line).where(Line.publication_id==UUID(first['publication_id']))).one()
        material_id=line.material_id;version_id=line.version_id;policy_id=line.policy_id;object_id=line.external_object_id
        original=db.get(ExternalObjectVersion,version_id).payload_jsonb
        assert db.scalar(text('SELECT count(*) FROM public.material_projection_publications WHERE receipt_id=:id'),{'id':receipt_id})==1
    # Exact graph writes fail at commit even for the schema owner.
    for sql,identifier in (
        ("UPDATE public.materials SET name='Tampered' WHERE id=:id",material_id),
        ("UPDATE public.material_inventory_policies SET tracking_mode='serial' WHERE id=:id",policy_id),
        ("UPDATE public.external_objects SET current_version_id=NULL WHERE id=:id",object_id),
        ("UPDATE public.external_object_versions SET payload_sha256=repeat('b',64) WHERE id=:id",version_id),
        ("UPDATE public.external_object_versions SET is_current=false,valid_to=clock_timestamp() WHERE id=:id",version_id),
        ("UPDATE public.material_projection_publications SET payload_sha256=payload_sha256 WHERE id=:id",UUID(first['publication_id'])),
    ):
        _rejected(owner_engine,sql,{'id':identifier})
    for engine in (api_engine,edge_engine,projector_engine):
        for table in ('material_projection_publications','material_projection_lines'):
            _rejected(engine,'SELECT * FROM public.'+table,state='42501')
            _rejected(engine,'INSERT INTO public.'+table+' DEFAULT VALUES',state='42501')
    _rejected(api_engine,"UPDATE public.materials SET name='Tampered' WHERE id=:id",{'id':material_id},state='42501')
    with Session(api_engine) as db:
        page=list_active_materials(db,actor=actor,query=code,limit=10)
        assert page.schema_version=='2.0' and len(page.items)==1 and page.items[0].source_updated_at is None
        assert page.items[0].material_id==material_id
    for name in ('Synthetic B','Synthetic A'):
        receipt=capture(name)
        with Session(owner_engine) as db:
            new=command(db,world,receipt)
            reviewed=service.preview_material_publication(db,**login,command=new)
            service.execute_material_publication(db,**login,command=new,review_sha256=reviewed['review_sha256']);db.commit()
    with Session(owner_engine) as db:
        assert service.read_material_publication(db,**args)==first
        assert db.get(ExternalObjectVersion,version_id).payload_jsonb==original
        versions=list(db.scalars(select(ExternalObjectVersion).where(ExternalObjectVersion.external_object_id==object_id).order_by(ExternalObjectVersion.valid_from)))
        assert [v.is_current for v in versions]==[False,False,True]
        assert versions[0].valid_to==versions[1].valid_from and versions[1].valid_to==versions[2].valid_from
    # Re-review locks the exact SKU and current policy while source files remain
    # pinned; contenders cannot change them between review and publication.
    next_receipt=capture('Synthetic C')
    with Session(owner_engine) as holder:
        next_command=command(holder,world,next_receipt)
        service.preview_material_publication(holder,**login,command=next_command)
        for sql,identifier in (
            ('UPDATE public.materials SET name=name WHERE id=:id',material_id),
            ('UPDATE public.material_inventory_policies SET tracking_mode=tracking_mode WHERE id=:id',policy_id),
            ('UPDATE public.files SET status=status WHERE id=:id',world.file),
            ('UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=:id',world.binding),
        ):
            with owner_engine.connect() as contender:
                contender.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError) as caught:contender.execute(text(sql),{'id':identifier})
                assert caught.value.orig.sqlstate=='55P03';contender.rollback()
        holder.rollback()
    assert snapshot(owner_engine)==snapshot(backup_engine)
    assert _formal_stock(owner_engine)==before
    print('PG16 material publication: signed receipt, concurrent exact replay, A/B/A immutable versions, unknown source time, graph/ACL denial and precise locks PASS',flush=True)
