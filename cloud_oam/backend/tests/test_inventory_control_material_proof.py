"""Full signed inventory + material captures, approved mapping and live proof.

Fixtures run the actual services on migrated SQLite, using synthetic sources.
The PostgreSQL companion separately proves production role and lock behavior.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import select

from app import material_source_proof as proof
from app import material_capture_ingress as ingress
from app.foundation_models import FileObject
from app.inventory_control_models import InventoryControlSourceBinding
from app.material_capture_models import MaterialCaptureBinding, MaterialCaptureReceipt
from app.material_source_authority_models import MaterialSourceAuthorityDecision
from app.material_projection_models import MaterialProjectionPublication, MaterialProjectionLine
from app.routers import integrations
from source_configuration_file_fixtures import source_evidence
from test_inventory_control_mapping import (
    db as mapping_db, admission_db, authority_db, capture_world, world, login, fresh,
    grants, facts, command, apply, credentials, RULES, configuration, normalization, authority, capture_rows,
)
from test_material_capture_ingress import capture, packet, proof as signed_proof, SECRET, KEY
from test_material_source_authority import command as source_command, apply as source_apply, revoke
from test_material_projection import command as publication_command, apply as publish


@pytest.fixture
def db(mapping_db):
    migrations = [
        ('20261026_0116_material_capture_ingress.py', (MaterialCaptureReceipt, MaterialCaptureBinding)),
        ('20261027_0117_material_source_authority.py', (MaterialSourceAuthorityDecision,)),
        ('20261029_0119_material_publications.py', (MaterialProjectionLine, MaterialProjectionPublication)),
    ]
    for filename, models in migrations:
        with Operations.context(MigrationContext.configure(mapping_db.connection())):
            for model in models: model.__table__.drop(mapping_db.connection())
            runpy.run_path(str(Path(__file__).parents[1]/'alembic/versions'/filename))['upgrade']()
        mapping_db.commit()
    return mapping_db


@pytest.fixture
def material_world(db, world, login, monkeypatch):
    instance = db.get(InventoryControlSourceBinding, world.binding).binding_jsonb['source_instance']
    now = authority._now(db)
    binding = MaterialCaptureBinding(source_system_id=world.source, source_instance=instance, key_id=KEY,
        key_fingerprint=ingress.key_fingerprint(SECRET,instance), created_at=now, valid_from=now,
        valid_to=now+timedelta(hours=1))
    db.add(binding); db.commit()
    # Separate documents distinguish a material-only loss from an inventory loss.
    file, _ = source_evidence(db,world.actor.user_id); db.commit()
    material = SimpleNamespace(actor=world.actor,binding=binding.id,source=world.source,file=file.id)
    material.grant = source_apply(db, credentials(login), source_command(db,material))
    def run(codes=('SKU-1',)):
        raw = [dict(materialCode=code,materialName=code,unitCode='EA',unitName='piece',materialStatus=0) for code in codes]
        value = capture.collect(source_instance=instance,
            read_page=lambda path,query:dict(success=True,model=dict(amount=len(raw),result=deepcopy(raw))),
            clock=lambda:authority._now(db))
        with monkeypatch.context() as patch:
            patch.setattr(integrations,'settings',integrations.settings.model_copy(update=dict(
                edge_sync_enabled=True,edge_sync_secret=SECRET,edge_sync_allowed_sources=instance,
                edge_material_capture_enabled=True,edge_material_capture_key_id=KEY)))
            received = integrations.receive_material_master_capture(signed_proof(packet(value),source=instance),db)
        cmd = publication_command(db,material,UUID(received['receipt_id']))
        return publish(db,credentials(login),cmd)
    material.publish = run
    material.publication = run()
    return material


def inspect(db,world,login,grant):
    return configuration.inspect_inventory_control_capture(db,preparation_id=world.root,
        mapping_decision_id=UUID(grant['decision_id']),**credentials(login))


@pytest.mark.parametrize('mode',['full','delta','zero'])
def test_signed_inventory_and_material_evidence_allow_exact_normalization_only(db,world,login,fresh,material_world,mode):
    grants(db,world); mapping = apply(db,login,command(db,world))
    fresh(records=capture_rows())
    if mode != 'full': fresh(records=[] if mode=='zero' else capture_rows()[:1])
    before = facts(db); result = inspect(db,world,login,mapping)
    assert facts(db)==before
    review = result['normalization_review']
    assert review['schema_version']=='rsc.inventory_control_normalization_review.v2'
    assert review['normalization_complete'] and result['normalization_rules_authorized']
    assert result['master_source_evidence_required'] is (mode!='zero')
    assert result['master_source_evidence_verified'] is (mode!='zero')
    assert review['master_source_evidence']['status']==('not_required' if mode=='zero' else 'verified')
    assert [g['payload']['control_qty'] for g in review['candidate_groups']]=={'full':['4'],'delta':['2'],'zero':[]}[mode]
    assert not result['projection_published'] and not result['start_ready']
    assert not review['master_source_evidence']['full_catalog_verified']


@pytest.mark.parametrize('change',['missing','file','revoked'])
def test_one_missing_or_invalid_material_suppresses_whole_province_candidates(db,world,login,fresh,material_world,change):
    grants(db,world); mapping = apply(db,login,command(db,world))
    rows = capture_rows()
    if change=='missing': rows[1]['materialCode']='SKU-2'
    elif change=='file': db.get(FileObject,material_world.file).status='quarantined';db.commit()
    else: revoke(db,material_world,credentials(login),material_world.grant)
    fresh(records=rows);before=facts(db)
    result=inspect(db,world,login,mapping);review=result['normalization_review']
    assert facts(db)==before and not result['master_source_evidence_verified']
    assert not review['normalization_complete'] and review['candidate_groups']==[]
    assert review['blocked_record_count']==(1 if change=='missing' else 2)


def test_material_proof_is_rechecked_after_normalization(db,world,login,fresh,material_world,monkeypatch):
    grants(db,world);mapping=apply(db,login,command(db,world));fresh(records=capture_rows())
    original=normalization.normalize_records
    # Module import binds at call entry; mutate the underlying evidence source
    # instead of replacing the public function to exercise its second real read.
    def expire_file(**kwargs):
        result=original(**kwargs)
        original_file=authority._file
        def read(db,file_id,sha):
            if file_id==material_world.file: raise authority.ControlAuthorityError('synthetic file loss')
            return original_file(db,file_id,sha)
        monkeypatch.setattr(authority,'_file',read)
        return result
    monkeypatch.setattr(normalization,'normalize_records',expire_file)
    with pytest.raises(normalization.ControlNormalizationError,match='master_source_changed_during_review'):
        inspect(db,world,login,mapping)


def test_material_source_expiry_bounds_whole_review(db,world,login,fresh,material_world,monkeypatch):
    grants(db,world);mapping=apply(db,login,command(db,world));fresh(records=capture_rows())
    result=inspect(db,world,login,mapping)
    material_end=result['normalization_review']['master_source_evidence']['valid_until']
    assert result['normalization_valid_until']==material_end
    original=normalization.normalize_records
    end=authority.preparation._aware(datetime.fromisoformat(material_end))
    def expire(**kwargs):
        result=original(**kwargs);monkeypatch.setattr(authority,'_now',lambda db:end);return result
    monkeypatch.setattr(normalization,'normalize_records',expire)
    with pytest.raises(normalization.ControlNormalizationError,match='master_source_changed_during_review'):
        inspect(db,world,login,mapping)


def test_material_deadline_is_checked_again_after_final_operator_validation(db,world,login,fresh,material_world,monkeypatch):
    grants(db,world);mapping=apply(db,login,command(db,world));fresh(records=capture_rows())
    result=inspect(db,world,login,mapping)
    end=datetime.fromisoformat(result['normalization_valid_until'])
    original=configuration._finish
    def finish(*args,**kwargs):
        original(*args,**kwargs)
        monkeypatch.setattr(authority,'_now',lambda db:end)
    monkeypatch.setattr(configuration,'_finish',finish)
    with pytest.raises(configuration.ControlConfigurationError,match='capture_observation_expired'):
        inspect(db,world,login,mapping)
