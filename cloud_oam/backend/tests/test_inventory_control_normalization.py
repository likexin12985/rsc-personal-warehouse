"""Exact decimal/identity review and actual authenticated capture integration."""
from datetime import datetime, timedelta, timezone
from decimal import localcontext
import json
import os
from uuid import UUID

import pytest
import sqlalchemy as sa

from app import inventory_control_normalization as service
from app import inventory_control_configuration as configuration
from app import inventory_control_authority as authority
from app.foundation_models import ExternalObject, SourceSystem
from app.inventory_models import FormalMaterial, MaterialInventoryPolicy
from test_inventory_control_admission import (
    db, authority_db, capture_world, world, fresh, grants, facts, login, cli,
)
from cloud_oam.edge_sync.test_inventory_control_capture import row as source_row


REGION = UUID('11111111-1111-4111-8111-111111111111')
MATERIAL = '22222222-2222-4222-8222-222222222222'
RULES = dict(revision='synthetic-v1', conditions=[dict(material_status='usable', material_stock_type='stock', condition_code='new')])


def master(**changes):
    return dict(material_id=MATERIAL, policy_id='33333333-3333-4333-8333-333333333333', issue=None,
                base_unit='piece', quantity_scale=3, allow_fraction=True, tracking_mode='none', **changes)


def record(key='stock:1', **changes):
    data = dict(materialCode='SKU-1', warehouseCode='W-A', positionCode='P-A', unitName='piece',
                qtyStock='2.000', qtyLock='0.000', materialStatus='usable', materialStockType='stock')
    data.update(changes)
    return dict(business_key=key, source_updated_at=None, data=data)


def normalize(rows=None, *, material=None, rules=None):
    return service.normalize_records(records=rows if rows is not None else [record()],
        target_positions={('W-A', 'P-A'), ('W-A', 'P-A2')}, region_org_id=REGION,
        masters={'SKU-1': material if material is not None else master()}, rules=RULES if rules is None else rules)


@pytest.mark.parametrize('value', ['-1', '-0', '01', '1e2', ' 1', '1 ', 'NaN', 'Infinity', '1.0000',
                                 '0.0001', '1000000000000000', '', None, True, False, 1.0, 1.25, -1, 10**15])
def test_quantity_rejects_lossy_noncanonical_or_out_of_range_source_values(value):
    result = normalize([record(qtyStock=value)])
    assert result['issues']['quantity_invalid'] == 1
    assert not result['normalization_complete'] and result['candidate_groups'] == []
    assert result['rows'][0]['control_qty'] is None


@pytest.mark.parametrize('value,wanted', [('999999999999999.999','999999999999999.999'), ('0.001','0.001'), (3,'3'), ('0','0')])
def test_exact_numeric_boundaries_survive_low_ambient_decimal_precision(value, wanted):
    with localcontext() as context:
        context.prec = 6
        result = normalize([record(qtyStock=value)])
    assert result['normalization_complete']
    assert result['candidate_groups'][0]['payload']['control_qty'] == wanted


def test_aggregation_is_deterministic_keeps_all_origins_and_does_not_subtract_locks():
    rows = [record('stock:2', qtyStock='0.002', positionCode='P-A2'), record('stock:1', qtyStock='1.001', qtyLock='0.500')]
    result = normalize(rows)
    assert result == normalize(list(reversed(rows)))
    group = result['candidate_groups'][0]
    assert group['payload']['control_qty'] == '1.003'
    assert [row['external_business_key'] for row in group['origins']] == ['stock:1','stock:2']
    assert result['rows'][0]['locked_qty'] == '0.500'
    assert all(row['source_updated_at'] is None for row in result['rows'])
    assert group['payload_sha256'] == service._sha(group['payload'])
    assert rows == [record('stock:2', qtyStock='0.002', positionCode='P-A2'), record('stock:1', qtyStock='1.001', qtyLock='0.500')]


@pytest.mark.parametrize('changes,issue', [({'unitName':'box'},'unit_mismatch'), ({'unitName':'piece '},'unit_mismatch'),
    ({'materialCode':'sku-1'},'material_unmapped'), ({'materialStatus':None},'condition_unmapped'),
    ({'qtyLock':None},'locked_quantity_invalid'), ({'qtyLock':'3'},'locked_quantity_exceeds_stock')])
def test_one_bad_row_blocks_the_entire_candidate_without_hiding_other_origins(changes, issue):
    result = normalize([record(), record('stock:2', **changes)])
    assert result['blocked_record_count'] == 1 and result['issues'][issue] == 1
    assert len(result['rows']) == 2 and result['candidate_groups'] == []


def test_group_overflow_marks_every_origin_and_suppresses_candidates():
    result = normalize([record(qtyStock='999999999999999.999'), record('stock:2', qtyStock='0.001')])
    assert result['issues'] == {'aggregate_quantity_overflow': 2}
    assert result['blocked_record_count'] == 2 and result['candidate_groups'] == []


@pytest.mark.parametrize('policy,changes,issue', [
    ({'quantity_scale':0}, {'qtyStock':'1.001'}, 'policy_quantity_mismatch'),
    ({'allow_fraction':False}, {'qtyLock':'0.001'}, 'policy_quantity_mismatch'),
    ({'tracking_mode':'serial'}, {}, 'serial_missing_or_invalid'),
    ({'tracking_mode':'serial'}, {'snNo':'synthetic-serial'}, 'serial_quantity_invalid'),
    ({'tracking_mode':'lot'}, {'materialStockTypeRefNumber':'not-a-proven-lot'}, 'lot_semantics_unavailable'),
])
def test_formal_tracking_and_quantity_policies_are_enforced(policy, changes, issue):
    material = master(); material.update(policy)
    result = normalize([record(**changes)], material=material)
    assert result['issues'][issue] == 1 and not result['candidate_groups']


def test_duplicate_positive_serials_across_positions_block_both_rows_without_exposing_sn():
    material = master(); material['tracking_mode'] = 'serial'
    result = normalize([record(qtyStock='1',snNo='synthetic-serial'),
                        record('stock:2',qtyStock='1',snNo='synthetic-serial',positionCode='P-A2')],material=material)
    assert result['issues'] == {'serial_duplicate':2} and not result['candidate_groups']
    assert 'synthetic-serial' not in json.dumps(result)


def test_explicit_zero_and_non_target_rows_do_not_create_a_fake_material_balance():
    assert normalize([])['candidate_groups'] == [] and normalize([])['normalization_complete']
    result = normalize([record(warehouseCode='W-B',positionCode='P-B',qtyStock='bad')])
    assert result['target_record_count'] == 0 and result['normalization_complete']


def test_rule_types_are_exact_and_conflicting_or_invalid_rules_fail_closed():
    rule = dict(material_status=1, material_stock_type='stock', condition_code='new')
    rules = dict(revision='synthetic-v2',conditions=[rule])
    assert normalize([record(materialStatus=1)],rules=rules)['normalization_complete']
    assert not normalize([record(materialStatus='1')],rules=rules)['normalization_complete']
    for bad in [dict(rules,conditions=[rule,rule]), dict(rules,conditions=[dict(rule,material_status=True)]),
                dict(rules,conditions=[dict(rule,condition_code='good')]), dict(rules,approved=True), dict(rules,revision='')]:
        with pytest.raises(service.ControlNormalizationError,match='invalid_rules'): normalize(rules=bad)


def test_conditions_remain_separate_and_rule_order_does_not_change_review():
    rules = dict(revision='synthetic-v2', conditions=RULES['conditions'] + [
        dict(material_status='broken',material_stock_type='stock',condition_code='damaged')])
    rows = [record(),record('stock:2',materialStatus='broken')]
    result = normalize(rows,rules=rules)
    assert result == normalize(rows,rules=dict(rules,conditions=list(reversed(rules['conditions']))))
    assert {group['payload']['condition_code'] for group in result['candidate_groups']} == {'new','damaged'}
    assert all(group['payload']['control_qty'] == '2' for group in result['candidate_groups'])


@pytest.fixture
def formal_material(db, world):
    obj = ExternalObject(source_system_id=world.source, entity_type='material', external_id='SKU-1')
    db.add(obj); db.flush()
    material = FormalMaterial(external_object_id=obj.id, sku_code='SKU-1', name='Synthetic part', base_unit='piece',
                              status='active', source_updated_at=datetime.now(timezone.utc))
    db.add(material); db.flush()
    policy = MaterialInventoryPolicy(material_id=material.id,tracking_mode='none',quantity_scale=3,allow_fraction=True,
                                     effective_from=datetime.now(timezone.utc)-timedelta(days=1))
    db.add(policy); db.commit()
    return material, policy, obj


def capture_rows():
    return [source_row('1',materialStatus='usable',materialStockType='stock'),
            source_row('2',materialStatus='usable',materialStockType='stock')]


@pytest.mark.parametrize('mode',['full','delta','zero'])
def test_actual_hmac_inspection_refuses_unreviewed_master_without_any_writes(db,world,fresh,formal_material,login,mode):
    grants(db,world); fresh(records=capture_rows())
    if mode != 'full': fresh(records=[] if mode == 'zero' else capture_rows()[:1])
    before = facts(db)
    result = configuration.inspect_inventory_control_capture(db,preparation_id=world.root,normalization_rules=RULES,
        access_token=login['access_token'],expected_authorization_version=login['expected_authorization_version'])
    assert facts(db) == before
    review = result['normalization_review']
    assert review['target_record_count'] == {'full':2,'delta':1,'zero':0}[mode]
    assert review['normalization_complete'] is (mode == 'zero')
    assert review['candidate_groups'] == []
    assert review['master_source_evidence']['issues'] == ({} if mode == 'zero' else {'SKU-1':'publication_missing'})
    assert result['normalization_review_sha256'] == service._sha(review)
    assert result['capture_authorized'] and not result['normalization_rules_authorized']
    assert not result['master_source_evidence_verified'] and not result['projection_published'] and not result['start_ready']


@pytest.mark.parametrize('change,issue', [('inactive','material_unavailable'),('wrong_source','material_source_mismatch'),
    ('wrong_identity','material_source_mismatch'),('deleted','material_source_mismatch'),
    ('missing_policy','policy_missing_or_ambiguous'),('overlap','policy_missing_or_ambiguous'),('expired','policy_missing_or_ambiguous')])
def test_database_identity_and_policy_cannot_be_supplied_by_review_rules(db,world,fresh,formal_material,change,issue):
    grants(db,world); fresh(records=capture_rows())
    material,policy,obj = formal_material
    if change == 'inactive': material.status = 'inactive'
    elif change == 'wrong_source':
        source = SourceSystem(code='synthetic-unrelated',name='Other',mode='read_only',enabled=True)
        db.add(source); db.flush(); obj.source_system_id = source.id
    elif change == 'wrong_identity': obj.external_id = 'SKU-2'
    elif change == 'deleted': obj.deleted_at = datetime.now(timezone.utc)
    elif change == 'missing_policy': db.delete(policy)
    elif change == 'expired': policy.effective_to = datetime.now(timezone.utc)-timedelta(seconds=1)
    else:
        db.add(MaterialInventoryPolicy(material_id=material.id,tracking_mode='none',quantity_scale=3,allow_fraction=True,
            effective_from=datetime.now(timezone.utc)-timedelta(hours=1),effective_to=datetime.now(timezone.utc)+timedelta(hours=1)))
    db.commit(); before = facts(db)
    result = service.inspect_inventory_control_normalization(db,preparation_id=world.root,rules=RULES)
    assert result['normalization_review']['issues'][issue] == 2 and facts(db) == before


def test_policy_expiry_while_reviewing_is_rechecked(db,world,fresh,formal_material,monkeypatch):
    grants(db,world); fresh(records=capture_rows())
    expiry = datetime.now(timezone.utc)+timedelta(minutes=1)
    formal_material[1].effective_to = expiry; db.commit()
    original = service.normalize_records
    def expire(**kwargs):
        result = original(**kwargs)
        monkeypatch.setattr(authority,'_now',lambda db:expiry)
        return result
    monkeypatch.setattr(service,'normalize_records',expire)
    with pytest.raises(service.ControlNormalizationError,match='master_changed_during_review'):
        service.inspect_inventory_control_normalization(db,preparation_id=world.root,rules=RULES)


def test_normalization_cannot_bypass_capture_authority(db,world,formal_material):
    from app.inventory_control_admission import ControlAdmissionError
    grants(db,world)
    with pytest.raises(ControlAdmissionError,match='source_authority_missing'):
        service.inspect_inventory_control_normalization(db,preparation_id=world.root,rules=RULES)


def test_cli_normalization_review_uses_current_jwt_and_rolls_back(db,world,fresh,formal_material,login,cli,tmp_path,monkeypatch,capsys):
    grants(db,world); fresh(records=capture_rows()); before = facts(db)
    engine = db.get_bind()
    monkeypatch.setattr(sa,'create_engine',lambda *a,**kw:engine)
    monkeypatch.setattr(engine,'dispose',lambda:None)
    monkeypatch.setattr(cli,'_connection_config',lambda:('synthetic-dsn','synthetic-db'))
    monkeypatch.setattr(cli,'_database_preflight',lambda db,target:None)
    monkeypatch.setattr(sa.orm.Session,'commit',lambda db:pytest.fail('review cannot commit'))
    path = tmp_path/'review.json'
    path.write_text(json.dumps(dict(preparation_id=str(world.root),normalization_rules=RULES,
                                   expected_authorization_version=login['expected_authorization_version'])))
    reader,writer = os.pipe(); os.write(writer,login['access_token'].encode()); os.close(writer)
    try: status = cli.main(['--inspection-file',str(path),'--access-token-fd',str(reader)])
    finally: os.close(reader)
    output = capsys.readouterr()
    assert status == 0 and not output.err
    result = json.loads(output.out)
    assert not result['normalization_review']['normalization_complete']
    assert result['normalization_review']['issues'] == {'material_source_evidence_unavailable':2}
    assert login['access_token'] not in output.out and facts(db) == before
