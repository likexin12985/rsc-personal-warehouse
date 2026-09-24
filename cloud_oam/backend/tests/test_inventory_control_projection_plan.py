"""Authenticated publication input plans bind every row to preserved evidence."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from uuid import UUID

import pytest
from sqlalchemy import select

from app import inventory_control_projection_plan as service
from app.inventory_control_models import InventoryControlPreparation as Preparation
from app.models import ExternalSyncSnapshotRecord, ExternalSyncCurrentRecord, User
from test_inventory_control_material_proof import (
    db, mapping_db, admission_db, authority_db, capture_world, world, login, fresh,
    material_world, grants, facts, apply, command, credentials, capture_rows, authority,
)
from test_inventory_control_admission import cli


def request(db, world, grant):
    return service.ControlProjectionPlanRequest(preparation_id=world.root,
        preparation_sha256=db.get(Preparation, world.root).control_manifest_sha256,
        mapping_decision_id=UUID(grant['decision_id']))


def inspect(db, world, login, grant):
    return service.inspect_inventory_control_projection_plan(db, **credentials(login), request=request(db, world, grant))


def setup(db, world, login, fresh, **mapping_changes):
    grants(db, world)
    grant = apply(db, login, command(db, world, **mapping_changes))
    fresh(records=capture_rows())
    return grant


def test_live_plan_exact_origins_stable_review_and_no_database_writes(db, world, login, fresh, material_world):
    grant = setup(db, world, login, fresh)
    before = facts(db)
    first = inspect(db, world, login, grant)
    second = inspect(db, world, login, grant)
    assert first['review'] == second['review'] and first['review_sha256'] == second['review_sha256']
    assert first['normalization_observation_sha256'] != second['normalization_observation_sha256']
    assert facts(db) == before
    plan = first['review']['plan']
    assert plan['scope_key'] == 'oam_inventory_control:region:' + str(world.region)
    assert plan['target_record_count'] == 2 and plan['line_count'] == 1
    assert plan['lines'][0]['payload']['control_qty'] == '4'
    assert len(plan['lines'][0]['payload']) == 7
    for origin in plan['lines'][0]['origins']:
        staged = db.get(ExternalSyncSnapshotRecord, origin['staging_record_id'])
        assert staged.business_key == origin['external_business_key']
        assert staged.snapshot_ref_id == origin['snapshot_ref_id']
        assert staged.payload_sha256 == origin['staging_payload_sha256']
        assert origin['material_publication_id'] == material_world.publication['publication_id']
    for key in ('recorded', 'write_authorized', 'projection_published', 'start_ready'):
        assert first[key] is False
    output = json.dumps(first)
    assert login['access_token'] not in output and 'storage_key' not in output and 'snNo' not in output


def test_incremental_plan_retains_original_pointer_for_unchanged_row_and_removes_deleted(db, world, login, fresh, material_world):
    grant = setup(db, world, login, fresh)
    old = {o['external_business_key']: o for o in inspect(db, world, login, grant)['review']['plan']['lines'][0]['origins']}
    changed = deepcopy(capture_rows()); changed[0]['qtyStock'] = '3'
    fresh(records=changed)
    plan = inspect(db, world, login, grant)['review']['plan']
    origins = plan['lines'][0]['origins']
    assert sorted(o['capture_sequence'] for o in origins) == [1, 2]
    for origin in origins:
        same = origin['staging_record_id'] == old[origin['external_business_key']]['staging_record_id']
        assert same is (origin['capture_sequence'] == 1)
    fresh(records=changed[:1])
    plan = inspect(db, world, login, grant)['review']['plan']
    assert plan['target_record_count'] == 1 and plan['lines'][0]['payload']['control_qty'] == '3'
    assert plan['lines'][0]['origins'][0]['capture_sequence'] == 2
    assert len(plan['transport']) == 3


@pytest.mark.parametrize('full', [False, True])
def test_proven_zero_has_no_fabricated_origin_or_material_claim(db, world, login, fresh, material_world, full):
    grant = setup(db, world, login, fresh)
    fresh(records=[], full=full)
    plan = inspect(db, world, login, grant)['review']['plan']
    assert plan['target_record_count'] == plan['line_count'] == 0 and plan['lines'] == []
    assert len(plan['transport']) == (1 if full else 2)
    assert plan['basis']['normalization_evidence']['master_source_evidence']['status'] == 'not_required'


def test_missing_material_refuses_whole_plan(db, world, login, fresh, material_world):
    grant = setup(db, world, login, fresh)
    changed = deepcopy(capture_rows()); changed[1]['materialCode'] = 'SKU-MISSING'
    fresh(records=changed); before = facts(db)
    with pytest.raises(service.ControlProjectionPlanError, match='normalization_blocked'):
        inspect(db, world, login, grant)
    assert facts(db) == before


def test_approved_mapping_expiry_bounds_normalization_and_plan(db, world, login, fresh, material_world):
    end = authority._now(db) + timedelta(minutes=2)
    grant = setup(db, world, login, fresh, valid_to=end)
    result = inspect(db, world, login, grant)
    plan = result['review']['plan']
    assert datetime.fromisoformat(plan['valid_until']) == end
    assert datetime.fromisoformat(plan['basis']['normalization_evidence']['valid_until']) == end


def test_final_operator_wait_cannot_outlive_mapping(db, world, login, fresh, material_world, monkeypatch):
    end = authority._now(db) + timedelta(minutes=2)
    grant = setup(db, world, login, fresh, valid_to=end)
    original = service.configuration._finish
    def finish(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr(authority, '_now', lambda db: end)
    monkeypatch.setattr(service.configuration, '_finish', finish)
    with pytest.raises(service.ControlProjectionPlanError, match='expired'):
        inspect(db, world, login, grant)


def test_existing_normalization_wrapper_rechecks_mapping_deadline(db, world, login, fresh, material_world, monkeypatch):
    end = authority._now(db) + timedelta(minutes=2)
    grant = setup(db, world, login, fresh, valid_to=end)
    original = service.configuration._finish
    def finish(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr(authority, '_now', lambda db: end)
    monkeypatch.setattr(service.configuration, '_finish', finish)
    with pytest.raises(service.configuration.ControlConfigurationError, match='capture_observation_expired'):
        service.configuration.inspect_inventory_control_capture(db, **credentials(login),
            preparation_id=world.root, mapping_decision_id=UUID(grant['decision_id']))


def test_same_capture_with_new_material_version_requires_new_review(db, world, login, fresh, material_world):
    grant = setup(db, world, login, fresh)
    old = inspect(db, world, login, grant)
    db.rollback()
    material_world.publish()
    new = inspect(db, world, login, grant)
    assert new['review_sha256'] != old['review_sha256']
    before = old['review']['plan']['lines'][0]['origins'][0]
    after = new['review']['plan']['lines'][0]['origins'][0]
    assert before['staging_record_id'] == after['staging_record_id']
    assert before['material_line_id'] != after['material_line_id']


@pytest.mark.parametrize('change', ['preparation', 'pointer', 'aggregate', 'duplicate'])
def test_input_or_internal_relation_drift_refuses_plan(db, world, login, fresh, material_world, monkeypatch, change):
    grant = setup(db, world, login, fresh); req = request(db, world, grant); before = facts(db)
    if change == 'preparation':
        req = req.model_copy(update={'preparation_sha256': 'f' * 64})
    elif change == 'pointer':
        original = service._latest_origins
        def wrong(*args):
            latest, transport = original(*args)
            latest.pop(next(iter(latest)))
            return latest, transport
        monkeypatch.setattr(service, '_latest_origins', wrong)
    else:
        original = service.normalization.inspect_inventory_control_normalization
        def wrong(*args, **kwargs):
            value = original(*args, **kwargs)
            group = value['normalization_review']['candidate_groups'][0]
            if change == 'aggregate':
                group['payload']['control_qty'] = '99'
                group['payload_sha256'] = service._sha(group['payload'])
            else:
                group['origins'].append(deepcopy(group['origins'][0]))
            return value
        monkeypatch.setattr(service.normalization, 'inspect_inventory_control_normalization', wrong)
    with pytest.raises(service.ControlProjectionPlanError):
        service.inspect_inventory_control_projection_plan(db, **credentials(login), request=req)
    assert facts(db) == before


def test_derived_current_cache_cannot_replace_preserved_origins(db, world, login, fresh, material_world):
    grant = setup(db, world, login, fresh)
    expected = inspect(db, world, login, grant)['review']
    db.rollback()
    for row in db.scalars(select(ExternalSyncCurrentRecord)):
        row.payload_json = '{}'; row.payload_sha256 = 'f' * 64
    db.commit()
    assert inspect(db, world, login, grant)['review'] == expected


def test_current_authorization_required(db, world, login, fresh, material_world):
    grant = setup(db, world, login, fresh)
    db.get(User, world.actor.user_id).authorization_version += 1; db.commit()
    with pytest.raises(service.configuration.ControlConfigurationError, match='authorization_changed'):
        inspect(db, world, login, grant)


def test_cli_plan_uses_current_credentials_and_rolls_back(db, world, login, fresh, material_world, cli, tmp_path, monkeypatch, capsys):
    grant = setup(db, world, login, fresh)
    req = request(db, world, grant)
    document = tmp_path/'plan.json'
    document.write_text(json.dumps(dict(command=req.model_dump(mode='json'), expected_authorization_version=world.actor.authorization_version)))
    monkeypatch.setattr(cli, '_connection_config', lambda: ('sqlite://', 'synthetic'))
    monkeypatch.setattr(cli, '_database_preflight', lambda *args: None)
    import sqlalchemy
    engine = db.get_bind()
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda *args, **kwargs: engine)
    monkeypatch.setattr(engine, 'dispose', lambda: None)
    before = facts(db); db.rollback()
    monkeypatch.setattr(sqlalchemy.orm.Session, 'commit', lambda db: pytest.fail('plan inspection must not commit'))
    read, write = os.pipe()
    try:
        os.write(write, login['access_token'].encode()); os.close(write)
        assert cli.main(['--control-projection-plan-file', str(document), '--access-token-fd', str(read)]) == 0
    finally:
        os.close(read)
    output = json.loads(capsys.readouterr().out)
    assert output['mode'] == 'inspect' and not output['recorded'] and not output['write_authorized']
    # The fixture owns its engine; the CLI uses a separate connection but never commits.
    assert output['review']['plan']['line_count'] == 1
    assert facts(db) == before


@pytest.mark.parametrize('mode', ['apply', 'status', 'preview'])
def test_cli_cannot_execute_a_projection_plan(cli, mode):
    with pytest.raises(SystemExit) as error:
        cli.main(['--control-projection-plan-file', 'unused', '--mode', mode])
    assert error.value.code == 2
