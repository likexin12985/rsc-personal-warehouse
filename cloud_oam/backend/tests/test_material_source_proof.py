"""Current source proof from real signed receipts, reviews, versions and audit."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import material_source_proof as proof
from app.foundation_models import AuditEvent, ExternalObject, ExternalObjectVersion, FileObject
from app.inventory_models import FormalMaterial, MaterialInventoryPolicy
from app.material_capture_models import MaterialCaptureReceipt as Receipt
from source_configuration_file_fixtures import source_evidence
from test_material_projection import (
    db, source_db, ingress_db, transport_world, world, login, prepare, apply,
    capture_receipt, command, source, authority, Publication, Line, facts,
)
from test_material_source_authority import revoke, command as grant_command, apply as grant_apply
from test_material_capture_ingress import SOURCE


def inspect(db, world, codes=('SKU-1',), **changes):
    return proof.inspect_current_material_sources(db, **dict(
        source_system_id=world.source, source_instance=SOURCE, sku_codes=list(codes)) | changes)


def test_signed_reviewed_current_material_is_proved_without_writing(db, world, login):
    _, cmd = prepare(db, world, login)
    publication = apply(db, login, cmd)
    before = facts(db)
    result = inspect(db, world)
    assert result['evidence']['status'] == 'verified'
    assert result['evidence_sha256'] == authority.preparation._sha(result['evidence'])
    material = result['evidence']['materials']['SKU-1']
    assert material['publication_id'] == publication['publication_id']
    assert material['version_id'] == str(db.scalar(select(ExternalObjectVersion.id)))
    assert not result['evidence']['full_catalog_verified'] and facts(db) == before
    assert 'storage_key' not in str(result) and login['access_token'] not in str(result)


def test_empty_missing_and_legacy_rows_never_become_verified_catalogue(db, world):
    assert inspect(db, world, ())['evidence']['status'] == 'not_required'
    result = inspect(db, world)['evidence']
    assert result['status'] == 'blocked' and result['issues'] == {'SKU-1':'material_missing'}
    obj = ExternalObject(source_system_id=world.source, entity_type='material', external_id='SKU-1')
    db.add(obj); db.flush()
    db.add(FormalMaterial(external_object_id=obj.id, sku_code='SKU-1', name='Legacy', base_unit='piece',
        status='active', source_updated_at=None))
    db.commit()
    result = inspect(db, world)['evidence']
    assert result['status'] == 'blocked' and result['issues'] == {'SKU-1':'publication_missing'}


def test_exact_source_system_and_collector_instance_are_both_required(db, world, login):
    _, cmd = prepare(db, world, login); apply(db, login, cmd)
    for change in ({'source_system_id':uuid4()}, {'source_instance':'another-collector'}):
        value = inspect(db, world, **change)['evidence']
        assert value['status'] == 'blocked' and value['issues'] == {'SKU-1':'source_binding_mismatch'}


@pytest.mark.parametrize('change', ['file', 'receipt', 'audit', 'policy', 'material', 'version', 'pointer'])
def test_drift_is_not_hidden_by_existing_master_rows(db, world, login, change):
    _, cmd = prepare(db, world, login); apply(db, login, cmd)
    if change == 'file': db.get(FileObject, world.file).status = 'quarantined'
    elif change == 'receipt':
        # Use a proof hook to simulate corrupted storage without disabling any
        # immutable database guard or rewriting retained publication history.
        row = db.scalar(select(Receipt))
        row.records_sha256 = 'b'*64
    elif change == 'audit': db.scalar(select(AuditEvent).where(AuditEvent.action=='material_source.publish')).action = 'changed'
    elif change == 'policy': db.scalar(select(MaterialInventoryPolicy)).quantity_scale = 3
    elif change == 'material': db.scalar(select(FormalMaterial)).name = 'Changed'
    elif change == 'version': db.scalar(select(ExternalObjectVersion)).payload_sha256 = 'b'*64
    else: db.scalar(select(ExternalObject)).current_version_id = None
    # Receipt tables themselves are immutable; use a read hook for that case.
    if change == 'receipt':
        db.rollback()
        from unittest.mock import patch
        with patch.object(proof.publication.ingress, '_prove', side_effect=proof.MaterialMasterCaptureError('synthetic damaged capture')):
            value = inspect(db, world)['evidence']
    else:
        db.commit(); value = inspect(db, world)['evidence']
    assert value['status'] == 'blocked' and 'SKU-1' in value['issues']


def test_revocation_and_expiry_block_current_use_but_keep_original_publication(db, world, login, monkeypatch):
    grant, cmd = prepare(db, world, login)
    review = proof.publication.preview_material_publication(db, **login, command=cmd)
    result = apply(db, login, cmd, review)
    revoke(db, world, login, grant)
    before = facts(db)
    assert inspect(db, world)['evidence']['status'] == 'blocked'
    assert apply(db, login, cmd, review)['publication_id'] == result['publication_id'] and facts(db) == before
    now = authority._now(db)
    monkeypatch.setattr(authority, '_now', lambda db: now+timedelta(minutes=46))
    assert inspect(db, world)['evidence']['status'] == 'blocked'


def test_publication_file_is_checked_separately_from_source_grant_file(db, world, login):
    _, cmd = prepare(db, world, login)
    file, _ = source_evidence(db, world.actor.user_id); db.commit()
    cmd = cmd.model_copy(update={'evidence_file_id':file.id})
    apply(db, login, cmd)
    assert inspect(db, world)['evidence']['status'] == 'verified'
    file.status = 'quarantined'; db.commit()
    assert inspect(db, world)['evidence']['status'] == 'blocked'


def test_uninterrupted_authority_renewal_keeps_original_capture_proof(db, world, login, monkeypatch):
    end = authority._now(db) + timedelta(minutes=1)
    original = grant_apply(db, login, grant_command(db,world,valid_to=end))
    receipt = capture_receipt(db)
    apply(db,login,command(db,world,receipt))
    before = inspect(db,world)['evidence']
    monkeypatch.setattr(authority,'_now',lambda db:end)
    assert inspect(db,world)['evidence']['status']=='blocked'
    renewed = grant_apply(db,login,grant_command(db,world,valid_to=end+timedelta(minutes=10)))
    result = inspect(db,world)['evidence']
    assert result['status']=='verified' and result['materials']==before['materials']
    assert next(iter(result['publications'].values()))['current_authority_decision_id']==renewed['decision_id']
    assert renewed['decision_id']!=original['decision_id']


def test_freshness_expires_even_when_source_authority_remains_current(db, world, login, monkeypatch):
    now=authority._now(db)
    grant_apply(db,login,grant_command(db,world,valid_to=now+timedelta(minutes=55)))
    receipt=capture_receipt(db);apply(db,login,command(db,world,receipt))
    assert inspect(db,world)['evidence']['status']=='verified'
    monkeypatch.setattr(authority,'_now',lambda db:now+timedelta(minutes=46))
    assert inspect(db,world)['evidence']['status']=='blocked'


def test_current_version_follows_a_b_a_while_old_proofs_remain_historical(db, world, login):
    _, cmd = prepare(db, world, login); apply(db, login, cmd)
    first = inspect(db, world)['evidence']
    old_id = db.scalar(select(ExternalObjectVersion.id))
    old_payload = deepcopy(db.get(ExternalObjectVersion, old_id).payload_jsonb)
    for name in ('B', 'Synthetic'):
        receipt = capture_receipt(db, [dict(materialCode='SKU-1', materialName=name, unitCode='EA', unitName='piece', materialStatus=0)])
        apply(db, login, command(db, world, receipt))
        current = inspect(db, world)['evidence']
        assert current['status'] == 'verified'
        assert current['materials']['SKU-1']['version_id'] != first['materials']['SKU-1']['version_id']
    assert db.get(ExternalObjectVersion, old_id).payload_jsonb == old_payload


def test_multi_sku_publication_is_proved_once_and_missing_row_blocks_set(db, world, login, monkeypatch):
    rows = [dict(materialCode=code, materialName=code, unitCode='EA', unitName='piece', materialStatus=0)
            for code in ('SKU-1','SKU-2')]
    _, cmd = prepare(db, world, login, rows); apply(db, login, cmd)
    original = proof.publication._prove
    calls = []
    def observed(db, row): calls.append(row.id); return original(db, row)
    monkeypatch.setattr(proof.publication, '_prove', observed)
    value = inspect(db, world, ('SKU-1','SKU-2'))['evidence']
    assert value['status'] == 'verified' and len(calls) == 1
    value = inspect(db, world, ('SKU-1','SKU-2','SKU-3'))['evidence']
    assert value['status'] == 'blocked' and value['issues'] == {'SKU-3':'material_missing'}


@pytest.mark.parametrize('codes', [['SKU-1','SKU-1'], ['SKU-2','SKU-1'], ['sku-1'], [True], ['../secret']])
def test_sku_coordinates_are_exact_and_bounded(db, world, codes):
    with pytest.raises(proof.MaterialSourceProofError, match='invalid_sku_set'):
        inspect(db, world, codes)
