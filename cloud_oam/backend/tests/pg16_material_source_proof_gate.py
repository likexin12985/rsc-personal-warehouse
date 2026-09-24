"""Current source proofs under real PG16 roles and competing transactions.

Engines must come from the protected CI gate or the owned local cluster factory.
This checker only reads existing synthetic publications and rolls back probes.
"""
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import material_source_proof as proof, material_projection as publication
from app import inventory_control_admission as admission
from app.foundation_models import ExternalObject
from app.material_capture_models import MaterialCaptureBinding, MaterialCaptureReceipt
from app.material_projection_models import MaterialProjectionLine, MaterialProjectionPublication
from app.material_source_authority_models import MaterialSourceAuthorityDecision
from app.formal_access import load_formal_principal
from app.security import create_access_token
from test_material_projection import command
from pg16_material_projection_gate import snapshot
from pg16_inventory_control_preparation_gate import _formal_stock


def assert_material_source_proof_gate(owner_engine,api_engine,edge_engine,projector_engine,backup_engine):
    before=snapshot(owner_engine); stock=_formal_stock(owner_engine)
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        line=db.scalars(select(MaterialProjectionLine).join(ExternalObject,
            ExternalObject.current_version_id==MaterialProjectionLine.version_id)).one()
        row=db.get(MaterialProjectionPublication,line.publication_id)
        binding=db.get(MaterialCaptureBinding,row.binding_id)
        codes=[line.sku_code]
        args=dict(source_system_id=binding.source_system_id,source_instance=binding.source_instance,sku_codes=codes)
        grant=db.get(MaterialSourceAuthorityDecision,UUID(row.payload_jsonb['source']['current_decision_id']))
        actor=load_formal_principal(db,row.actor_user_id)
        world=SimpleNamespace(actor=actor,file=row.evidence_file_id)
        login=dict(access_token=create_access_token(actor.user_id,row.auth_session_id),expected_authorization_version=actor.authorization_version)
        latest=db.scalars(select(MaterialCaptureReceipt).where(MaterialCaptureReceipt.binding_id==binding.id)
                          .order_by(MaterialCaptureReceipt.created_at.desc()).limit(1)).one()
        cmd=command(db,world,latest.id)
        coordinates=dict(material_id=line.material_id,object_id=line.external_object_id,version_id=line.version_id,
            policy_id=line.policy_id,file_id=row.evidence_file_id,grant_file_id=grant.evidence_file_id,binding_id=binding.id,
            source_id=binding.source_system_id)
    with Session(owner_engine) as holder:
        result=proof.inspect_current_material_sources(holder,**args)
        assert result['evidence']['status']=='verified' and not result['evidence']['full_catalog_verified']
        assert result['evidence']['materials'][codes[0]]['version_id']==str(coordinates['version_id'])
        assert proof.inspect_current_material_sources(holder,**dict(args,source_instance='synthetic-unrelated'))['evidence']['status']=='blocked'
        assert proof.inspect_current_material_sources(holder,**dict(args,source_system_id=uuid4()))['evidence']['status']=='blocked'
        assert proof.inspect_current_material_sources(holder,**dict(args,sku_codes=sorted(codes+['ZZ-MISSING-SKU'])))['evidence']['status']=='blocked'
        for sql,key in (
            ('UPDATE public.materials SET name=name WHERE id=:id','material_id'),
            ('UPDATE public.external_objects SET current_version_id=current_version_id WHERE id=:id','object_id'),
            ('UPDATE public.external_object_versions SET is_current=is_current WHERE id=:id','version_id'),
            ('UPDATE public.material_inventory_policies SET tracking_mode=tracking_mode WHERE id=:id','policy_id'),
            ('UPDATE public.files SET status=status WHERE id=:id','file_id'),
            ('UPDATE public.files SET status=status WHERE id=:id','grant_file_id'),
            ('UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=:id','binding_id'),
            ('UPDATE public.source_systems SET enabled=false WHERE id=:id','source_id'),
        ):
            with owner_engine.connect() as contender:
                contender.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError) as error:contender.execute(text(sql),{'id':coordinates[key]})
                assert error.value.orig.sqlstate=='55P03';contender.rollback()
        # Actual publication preview takes the exclusive same-SKU advisory lock.
        with Session(owner_engine) as contender:
            contender.execute(text("SET LOCAL lock_timeout='200ms'"))
            with pytest.raises(DBAPIError) as error:publication.preview_material_publication(contender,**login,command=cmd)
            assert error.value.orig.sqlstate=='55P03';contender.rollback()
        # An unrelated SKU key is free; no whole-catalogue advisory lock exists.
        with owner_engine.connect() as contender:
            contender.execute(text("SET LOCAL lock_timeout='200ms'"))
            contender.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                {'key':'rsc.material-publication.sku:UNRELATED-'+uuid4().hex.upper()})
            contender.rollback()
        holder.rollback()
    with Session(owner_engine) as db:
        publication.preview_material_publication(db,**login,command=cmd)
        db.rollback()
    for engine in (api_engine,edge_engine,projector_engine,backup_engine):
        with Session(engine) as db:
            with pytest.raises(admission.ControlAdmissionError,match='requires_direct_owner'):
                proof.inspect_current_material_sources(db,**args)
    with Session(owner_engine) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        with pytest.raises(admission.ControlAdmissionError,match='requires_read_committed'):
            proof.inspect_current_material_sources(db,**args)
    assert snapshot(owner_engine)==before==snapshot(backup_engine) and _formal_stock(owner_engine)==stock
    print('PG16 current material proof: exact current publication/source/instance, missing set refusal, owner/isolation boundary, same-SKU publisher and mutable evidence locks, unchanged stock/history PASS',flush=True)
