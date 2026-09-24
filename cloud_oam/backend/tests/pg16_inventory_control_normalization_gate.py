"""Signed nonzero inventory and material proof under real PG16 roles.

Invoked only with protected CI engines or newly owned local-cluster engines.
All OAM and transport inputs are synthetic; no guards or privileges are bypassed.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import inventory_control_admission as admission
from app import inventory_control_attestation as attestation
from app import inventory_control_authority as authority
from app import inventory_control_configuration as configuration
from app import inventory_control_mapping as mapping
from app import inventory_control_normalization as normalization
from app import inventory_control_preparation as preparation
from app import inventory_control_projection_plan as projection_plan
from app import material_source_authority as material_authority
from app.foundation_models import ExternalObject, Organization
from app.formal_access import load_formal_principal
from app.inventory_control_models import InventoryControlPreparation
from app.material_capture_models import MaterialCaptureBinding
from app.material_projection_models import MaterialProjectionLine, MaterialProjectionPublication
from app.material_source_authority_models import MaterialSourceAuthorityDecision
from app.routers import integrations
from app.schemas import EdgeSyncSnapshotBatchIn, EdgeSyncSnapshotCompleteIn
from app.security import create_access_token
from app.edge_database_security import verify_edge_database_boundary
from app.oam_sync_scope_security import read_oam_sync_scope_boundary
from source_configuration_file_fixtures import source_evidence
from test_inventory_control_authority import command as authority_command
from test_inventory_control_mapping import command as mapping_command
from test_inventory_control_capture_ingress import Source, expected, edge, capture
from cloud_oam.edge_sync.test_inventory_control_capture import row as source_row
from test_material_capture_ingress import SECRET
from test_material_source_authority import command as material_command
from test_edge_sync_safety import request
from pg16_inventory_control_preparation_gate import _formal_stock
from pg16_material_projection_gate import snapshot as material_snapshot

RULES=dict(revision='pg16-normalization-v1',conditions=[
    dict(material_status='usable',material_stock_type='stock',condition_code='new'),
    dict(material_status='broken',material_stock_type='stock',condition_code='damaged')])


def _configuration(db, login, cmd):
    review=configuration.preview_inventory_control_configuration(db,**login,command=cmd)
    return configuration.execute_inventory_control_configuration(db,**login,command=cmd,review_sha256=review['review_sha256'])['decision']


def _mapping(db, login, cmd):
    review=mapping.preview_inventory_control_mapping(db,**login,command=cmd)
    return mapping.execute_inventory_control_mapping(db,**login,command=cmd,review_sha256=review['review_sha256'])


def _prepare(owner):
    catalog=expected()
    with Session(owner) as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        line=db.scalars(select(MaterialProjectionLine).join(ExternalObject,
            ExternalObject.current_version_id==MaterialProjectionLine.version_id)).one()
        publication=db.get(MaterialProjectionPublication,line.publication_id)
        binding=db.get(MaterialCaptureBinding,publication.binding_id)
        actor=load_formal_principal(db,publication.actor_user_id)
        catalog['binding']['source_instance']=binding.source_instance
        region=Organization(code='pg16-normalization-'+uuid4().hex,name='Synthetic normalization region',org_type='region_company',status='active')
        db.add(region);db.flush()
        db.execute(text('''INSERT INTO public.oam_sync_scope_bindings
            (id,principal_name,capability,source_system,source_instance,scope_key,company_id,org_code,entity_type)
            VALUES (:id,'edge_inbox','edge_ingress',:source_system,:source_instance,:scope_key,:company_id,:org_code,'inventory')'''),
            {'id':uuid4(),**catalog['binding']})
        db.commit()
        authority_file,_=source_evidence(db,actor.user_id)
        mapping_file,_=source_evidence(db,actor.user_id)
        db.commit()
        return SimpleNamespace(actor=actor,file=authority_file.id,mapping_file=mapping_file.id,region=region.id,
            source=binding.source_system_id,material_binding=binding.id,material_file=publication.evidence_file_id,
            material_grant=UUID(publication.payload_jsonb['source']['current_decision_id']),
            code=line.sku_code,material_id=line.material_id,policy_id=line.policy_id,catalog_document=catalog,
            login=dict(access_token=create_access_token(actor.user_id,publication.auth_session_id),
                       expected_authorization_version=actor.authorization_version))


def _capture_pipeline(owner, edge_engine, world, folder, patch):
    catalog=world.catalog_document;instance=catalog['binding']['source_instance']
    settings=integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret=SECRET,
        edge_sync_allowed_sources=instance,edge_control_capture_enabled=True,edge_control_capture_key_id='pg16-normalization-key-v1',
        edge_sync_legacy_batches_enabled=False,edge_sync_legacy_personnel_projection_enabled=False))
    patch.setattr(integrations,'settings',settings)
    endpoints={
        'https://synthetic.invalid/api/integrations/oam/edge/snapshots/batches':'batch',
        'https://synthetic.invalid/api/integrations/oam/edge/snapshots/complete':'complete',
        'https://synthetic.invalid/api/integrations/oam/edge/inventory-control/captures':'capture',
    }
    def transport(method,url,*,headers,body,timeout):
        assert method=='POST' and url in endpoints and timeout==60
        req=request();req._body=body
        verified=asyncio.run(integrations.verify_edge_request(req,source_instance=headers['X-RSC-Edge-Source'],
            timestamp_value=headers['X-RSC-Edge-Timestamp'],batch_id=headers['X-RSC-Edge-Batch'],signature=headers['X-RSC-Edge-Signature']))
        with Session(edge_engine) as db:
            assert db.execute(text('SELECT current_user,session_user')).one()==('edge_inbox','edge_inbox')
            kind=endpoints[url]
            if kind=='batch':result=integrations.receive_snapshot_batch(EdgeSyncSnapshotBatchIn.model_validate_json(body),req,verified,db)
            elif kind=='complete':result=integrations.complete_snapshot(EdgeSyncSnapshotCompleteIn.model_validate_json(body),req,verified,db)
            else:result=integrations.receive_inventory_control_capture(attestation.CaptureAttestationIn.model_validate_json(body),req,verified,db)
        return 200,json.dumps(result).encode(),{}
    patch.setattr(edge,'shared_edge_request',transport)
    state=dict(version=2,sourceInstance=instance,scopes={});base=None
    def run(records,*,full=False):
        nonlocal base
        now=datetime.now(timezone.utc)
        collected=capture.collect(catalog,read_page=Source(records),normalize_records=edge.inventory_records,
            bind_scope=edge.bind_inventory_scope,page_size=2,clock=lambda:now)
        box=edge.build_outbox(source_instance=instance,scope_key='all',warehouse_filter=None,
            company_id=catalog['binding']['company_id'],org_code=catalog['binding']['org_code'],snapshot_at=now.isoformat(),
            snapshots={'inventory':collected['records']},state=state,force_full=full,batch_size=2)
        bundle=capture.build_bundle(catalog,collected,box,manifest=edge.snapshot_manifest(box),
            batches=list(edge.snapshot_batches(box)),base=None if full else base)
        box['controlEvidence']=capture.archive(folder/'evidence',bundle)
        box['controlAttestation']=capture.attestation_payload(bundle,key_id=settings.edge_control_capture_key_id)
        edge.upload_outbox(outbox=box,api_base='https://synthetic.invalid/api',secret=SECRET,state_file=folder/'state.json',state=state)
        base=capture.load_base(folder/'evidence',state,catalog,force_full=False)
        with Session(owner) as db:
            result=preparation.record_inventory_control_preparation(db,source_system_id=world.source,region_org_id=world.region,
                expected_json=capture.canonical(catalog).decode(),evidence_json=capture.canonical(base).decode(),checked_at=authority._now(db))
            db.commit();world.root=result['preparation_id']
            root=db.get(InventoryControlPreparation,world.root);world.binding=root.binding_id;world.catalog=root.catalog_id
        return box,bundle
    return run


def inspect(db, world):
    return configuration.inspect_inventory_control_capture(db,**world.login,preparation_id=world.root,mapping_decision_id=world.mapping)


def _edge_guard_catalog_probes(owner,edge_engine):
    """Exact checker failures in rolled-back DDL probes, with no fact writes."""
    for statement,expected in (
        ('CREATE TRIGGER pg16_unexpected_material_graph BEFORE INSERT ON public.external_objects '
         'FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_material_projection_graph_0119()', 'trigger_closure'),
        ('ALTER FUNCTION public.rsc_guard_material_projection_graph_0119() '
         'SET search_path=pg_catalog,public,pg_catalog', 'function_closure'),
        ('GRANT EXECUTE ON FUNCTION public.rsc_guard_material_projection_graph_0119() TO edge_inbox', 'function_closure'),
    ):
        with owner.connect() as db:
            before=read_oam_sync_scope_boundary(db,expected_role='edge_inbox')
            assert expected not in before['boundary_failures'].split(',')
            db.execute(text(statement))
            after=read_oam_sync_scope_boundary(db,expected_role='edge_inbox')
            assert expected in after['boundary_failures'].split(',')
            db.rollback()
        verify_edge_database_boundary(edge_engine)


def assert_inventory_control_normalization_gate(owner,edge_engine,api,projector,backup,*,publication_check=False):
    world=_prepare(owner);before_stock=_formal_stock(owner);before_material=material_snapshot(owner)
    good=source_row('1',materialStatus='usable',materialStockType='stock');good['materialCode']=world.code
    damaged=source_row('2',materialStatus='broken',materialStockType='stock');damaged.update(materialCode=world.code,qtyStock='3.000')
    other=source_row('3',materialStatus='unresolved',materialStockType='unknown')
    other.update(warehouseCode='W-B',warehouseType='serviceWarehouse',warehouseAttribute='old',positionCode='P-B',materialCode='OTHER-REGION-UNKNOWN')
    observations=[]
    with pytest.MonkeyPatch.context() as patch,TemporaryDirectory(prefix='pg16-control-normalization-') as folder:
        capture_rows=_capture_pipeline(owner,edge_engine,world,Path(folder),patch)
        capture_rows([good,damaged,other],full=True)
        with Session(owner) as db:
            source=_configuration(db,world.login,authority_command(db,world));db.commit()
            _configuration(db,world.login,authority_command(db,world,action='catalog_grant',grant=source));db.commit()
            grant=_mapping(db,world.login,mapping_command(db,world,rules=RULES,evidence_file_id=world.mapping_file));db.commit()
            world.mapping=UUID(grant['decision_id'])
            with pytest.raises(admission.ControlAdmissionError,match='source_authority_missing'):inspect(db,world)
        # A fresh full capture discards the pre-grant seed from the new chain.
        modified=dict(good,qtyStock='4.000')
        missing=dict(damaged,materialCode='MISSING-TARGET-SKU')
        for label,records,full,quantities,verified,blocked in (
            ('full',[good,damaged,other],True,{'new':'2','damaged':'3'},True,0),
            ('incremental-preserve',[modified,damaged,other],False,{'new':'4','damaged':'3'},True,0),
            ('incremental-delete',[modified,other],False,{'new':'4'},True,0),
            ('missing-sku',[modified,missing,other],False,{},False,1),
            ('target-zero',[other],False,{},False,0),
            ('all-zero',[],True,{},False,0),
            ('fresh-full',[good,damaged,other],True,{'new':'2','damaged':'3'},True,0),
        ):
            box,bundle=capture_rows(records,full=full)
            with Session(owner) as db:
                result=inspect(db,world);review=result['normalization_review']
                assert review['schema_version']=='rsc.inventory_control_normalization_review.v2'
                assert result['capture_authorized'] and result['normalization_rules_authorized']
                assert result['master_source_evidence_verified'] is verified
                assert review['blocked_record_count']==blocked and review['normalization_complete'] is (blocked==0)
                assert {g['payload']['condition_code']:g['payload']['control_qty'] for g in review['candidate_groups']}==quantities
                assert review['non_target_record_count']==(0 if label=='all-zero' else 1)
                assert not result['projection_published'] and not result['start_ready']
                if label in ('target-zero','all-zero'):
                    assert not result['master_source_evidence_required'] and review['master_source_evidence']['status']=='not_required'
                assert len(result['observation']['captures'])==len(bundle['evidence']['snapshots'])
                plan_request=projection_plan.ControlProjectionPlanRequest(preparation_id=world.root,
                    preparation_sha256=db.get(InventoryControlPreparation,world.root).control_manifest_sha256,
                    mapping_decision_id=world.mapping)
                plan_args=dict(**world.login,request=plan_request)
                if blocked:
                    with pytest.raises(projection_plan.ControlProjectionPlanError,match='normalization_blocked'):
                        projection_plan.inspect_inventory_control_projection_plan(db,**plan_args)
                    origin_count=None
                else:
                    planned=projection_plan.inspect_inventory_control_projection_plan(db,**plan_args)
                    again=projection_plan.inspect_inventory_control_projection_plan(db,**plan_args)
                    assert planned['review']==again['review'] and planned['review_sha256']==again['review_sha256']
                    plan=planned['review']['plan']
                    assert not planned['recorded'] and not planned['write_authorized'] and not planned['projection_published']
                    origin_count=sum(len(line['origins']) for line in plan['lines'])
                    assert origin_count==review['target_record_count']
                    if label=='incremental-preserve':
                        assert {origin['capture_sequence'] for line in plan['lines'] for origin in line['origins']}=={1,2}
                    assert len(plan['transport'])==len(bundle['evidence']['snapshots'])
                observations.append(dict(case=label,mode=box['syncMode'],targetRecords=review['target_record_count'],
                    blockedRecords=blocked,quantities=quantities,materialVerified=verified,projectionPlanOriginCount=origin_count))
            assert _formal_stock(owner)==before_stock and material_snapshot(owner)==before_material
        if publication_check:
            from pg16_control_publication_gate import assert_control_publication_gate
            observations.append(assert_control_publication_gate(owner,edge_engine,api,projector,backup,world,capture_rows))
        # An entire combined review holds inventory, material and operator facts.
        with Session(owner) as holder:
            inspect(holder,world)
            probes=[('UPDATE public.source_systems SET enabled=false WHERE id=:id',world.source),
                ('UPDATE public.inventory_control_source_bindings SET binding_sha256=binding_sha256 WHERE id=:id',world.binding),
                ('UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=:id',world.material_binding),
                ('UPDATE public.materials SET name=name WHERE id=:id',world.material_id),
                ('UPDATE public.material_inventory_policies SET tracking_mode=tracking_mode WHERE id=:id',world.policy_id),
                ('UPDATE public.auth_sessions SET revoked_at=clock_timestamp() WHERE user_id=:id',world.actor.user_id)]
            probes += [('UPDATE public.files SET status=status WHERE id=:id',identifier)
                       for identifier in (world.file,world.mapping_file,world.material_file)]
            for sql,identifier in probes:
                with owner.connect() as contender:
                    contender.execute(text("SET LOCAL lock_timeout='200ms'"))
                    with pytest.raises(DBAPIError) as error:contender.execute(text(sql),{'id':identifier})
                    assert error.value.orig.sqlstate=='55P03';contender.rollback()
            with owner.connect() as contender:
                contender.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError) as error:contender.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                    {'key':'rsc.material-publication.sku:'+world.code})
                assert error.value.orig.sqlstate=='55P03';contender.rollback()
        # Changes are tested in real owner transactions and explicitly rolled
        # back. This preserves prior reviewed facts and all original history.
        for kind,file_id in (('inventory',world.file),('mapping',world.mapping_file),('material',world.material_file)):
            with Session(owner) as db:
                # Completed formal files are immutable in production. Prove
                # that rejection instead of disabling the file guard to make
                # a SQLite-only corruption fixture possible on PostgreSQL.
                with pytest.raises(DBAPIError) as error:
                    db.execute(text("UPDATE public.files SET status='quarantined' WHERE id=:id"),{'id':file_id})
                assert error.value.orig.sqlstate=='P0001'
                db.rollback()
                assert inspect(db,world)['master_source_evidence_verified']
        with Session(owner) as db:
            # Publication/start expiry probes may have naturally renewed the
            # mapping. Revoke the actual current grant, not its old fixture ID.
            from app.inventory_control_mapping_models import InventoryControlMappingDecision
            current_mapping=db.get(InventoryControlMappingDecision,world.mapping)
            cmd=mapping_command(db,world,action='revoke',rules=None,revoked_grant_id=current_mapping.id,expected_subject_sha256=current_mapping.payload_sha256,evidence_file_id=world.mapping_file)
            _mapping(db,world.login,cmd)
            with pytest.raises(mapping.ControlMappingError,match='mapping_unavailable'):inspect(db,world)
            db.rollback()
        with Session(owner) as db:
            material_grant=db.get(MaterialSourceAuthorityDecision,world.material_grant)
            material_world=SimpleNamespace(binding=world.material_binding,file=world.material_file)
            cmd=material_command(db,material_world,action='revoke',valid_to=None,revoked_grant_id=material_grant.id,expected_subject_sha256=material_grant.payload_sha256)
            review=material_authority.preview_material_source_authority(db,**world.login,command=cmd)
            material_authority.execute_material_source_authority(db,**world.login,command=cmd,review_sha256=review['review_sha256'])
            result=inspect(db,world)
            assert not result['master_source_evidence_verified'] and result['normalization_review']['candidate_groups']==[]
            db.rollback()
        for engine in (api,edge_engine,projector,backup):
            with Session(engine) as db:
                with pytest.raises(preparation.InventoryControlEvidenceError,match='requires_schema_owner'):
                    inspect(db,world)
            with Session(engine) as db:
                with pytest.raises(admission.ControlAdmissionError,match='requires_direct_owner'):
                    projection_plan.inspect_inventory_control_projection_plan(db,**plan_args)
        with Session(owner) as db:
            db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
            with pytest.raises(admission.ControlAdmissionError,match='requires_read_committed'):inspect(db,world)
        with Session(owner) as db:
            assert inspect(db,world)['master_source_evidence_verified']
    after_stock=_formal_stock(owner)
    if publication_check:
        assert after_stock[1:3]==before_stock[1:3]
        assert len(after_stock[3])==len(before_stock[3])+1  # committed proven-zero opening
        assert len(after_stock[4])==len(before_stock[4])+1  # separate task-start outbox fact
    else:
        assert after_stock==before_stock
    assert material_snapshot(owner)==before_material==material_snapshot(backup)
    _edge_guard_catalog_probes(owner,edge_engine)
    print('PG16 nonzero control normalization: signed full/incremental/zero, approved mapping, current material source, condition totals, no partial province, combined locks and revocation/file refusal PASS',flush=True)
    return observations
