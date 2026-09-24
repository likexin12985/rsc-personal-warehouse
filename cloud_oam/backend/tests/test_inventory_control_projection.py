"""Actual control publications preserve origins without creating inventory facts."""
from copy import deepcopy
from uuid import UUID, uuid4
from types import SimpleNamespace
from pathlib import Path
import runpy
import json
import os
from alembic.migration import MigrationContext
from alembic.operations import Operations

import pytest
from sqlalchemy import select

from app import inventory_control_projection as service
from app.inventory_control_projection_models import ControlProjectionPublication as Publication, ControlProjectionLine as Line
from app.foundation_models import ExternalObjectVersion, ExternalObject
from test_inventory_control_projection_plan import (
    db as material_db, mapping_db, admission_db, authority_db, capture_world, world, login, fresh,
    material_world, grants, facts, apply, command, credentials, capture_rows, authority, setup, request,
)
from test_inventory_control_admission import cli
from test_inventory_control_mapping import revoke as revoke_mapping
from app.inventory_control_mapping import ControlMappingError

PATH=Path(__file__).parents[1]/'alembic/versions/20261031_0121_control_publications.py'


@pytest.fixture
def db(material_db):
    migration=runpy.run_path(str(PATH))
    from app.database import Base
    with Operations.context(MigrationContext.configure(material_db.connection())):
        for table in reversed(migration['TABLES']): Base.metadata.tables[table].drop(material_db.connection())
        migration['upgrade']()
    material_db.commit()
    return material_db


def publication_command(db, world, grant, **changes):
    value = request(db, world, grant).model_dump()
    value.update(evidence_file_id=world.file, evidence_sha256='a'*64, reason='Synthetic reviewed control publication',
                 idempotency_key='control-publish-'+str(uuid4()), request_id='control-request-'+str(uuid4()))
    value.update(changes)
    return service.ControlPublicationCommand(**value)


def publish(db, world, login, grant):
    cmd = publication_command(db, world, grant)
    args = dict(**credentials(login), command=cmd)
    review = service.preview_control_publication(db, **args)
    result = service.execute_control_publication(db, **args, review_sha256=review['review_sha256'])
    db.commit()
    return result, dict(**args, review_sha256=review['review_sha256'])


def test_actual_publish_idempotent_recovery_and_no_inventory_posting(db, world, login, fresh, material_world):
    grant = setup(db, world, login, fresh); before = facts(db)
    result, args = publish(db, world, login, grant)
    assert result['record_count']==1 and result['origin_count']==2
    assert result['projection_published'] and not result['start_ready']
    after = facts(db)
    assert {name for name in after if before[name]!=after[name]} == {
        'control_projection_publications', 'control_projection_lines', 'control_projection_origins',
        'external_objects', 'external_object_versions', 'sync_runs', 'sync_batches', 'sync_inbox_events',
        'audit_events', 'audit_chain_heads'}
    assert service.read_control_publication(db, **args)==result
    assert service.execute_control_publication(db, **args)==result
    db.commit(); assert facts(db)==after
    consume(db, result)


def consume(db, result, historical=False):
    from app.formal_services.opening_stocktake import _prepare_control_evidence
    row=db.get(Publication,UUID(result['publication_id']))
    lines=tuple(db.scalars(select(Line).where(Line.publication_id==row.id).order_by(Line.sequence)))
    cmd=SimpleNamespace(control_source_system_id=row.source_system_id, control_sync_run_id=row.sync_run_id,
        control_sync_scope_key='oam_inventory_control:region:'+str(row.region_org_id),region_org_id=row.region_org_id,
        control_lines=tuple(service._line_input(line) for line in lines))
    actual,digest=_prepare_control_evidence(db,cmd,now=authority._now(db),lock_rows=False,
        historical_at=service.preparation._aware(row.created_at) if historical else None)
    assert len(actual)==result['record_count'] and digest==row.payload_jsonb['control_manifest_sha256']


def test_delta_zero_reappearance_and_historical_recovery(db, world, login, fresh, material_world):
    grant=setup(db,world,login,fresh)
    first, old_args=publish(db,world,login,grant)
    changed=deepcopy(capture_rows()); changed[0]['qtyStock']='3'
    fresh(records=changed); second,_=publish(db,world,login,grant)
    fresh(records=[]); zero,_=publish(db,world,login,grant)
    assert zero['record_count']==zero['origin_count']==0
    assert not tuple(db.scalars(select(ExternalObjectVersion).join(Line,Line.version_id==ExternalObjectVersion.id)
                               .where(ExternalObjectVersion.is_current.is_(True))))
    fresh(records=capture_rows()); again,_=publish(db,world,login,grant)
    assert again['record_count']==1
    for result in (first,second,zero,again):
        assert service.prove_control_publication(db,db.get(Publication,UUID(result['publication_id'])))
        consume(db,result,historical=True)
    assert service.read_control_publication(db,**old_args)==first
    assert len(tuple(db.scalars(select(ExternalObject).where(ExternalObject.entity_type=='oam_inventory_control'))))==1


@pytest.mark.parametrize('point', ['audit','final_plan','proof'])
def test_failure_rolls_back_entire_graph_and_leaves_caller_usable(db,world,login,fresh,material_world,monkeypatch,point):
    grant=setup(db,world,login,fresh); cmd=publication_command(db,world,grant)
    args=dict(**credentials(login),command=cmd)
    review=service.preview_control_publication(db,**args); before=facts(db)
    def fail(*a,**kw): raise RuntimeError('synthetic publication failure')
    if point=='audit': monkeypatch.setattr(service,'append_audit_event',fail)
    elif point=='proof': monkeypatch.setattr(service,'prove_control_publication',fail)
    else:
        original=service.planner.inspect_inventory_control_projection_plan
        calls=0
        def inspect(*a,**kw):
            nonlocal calls
            calls+=1
            if calls==2: fail()
            return original(*a,**kw)
        monkeypatch.setattr(service.planner,'inspect_inventory_control_projection_plan',inspect)
    with pytest.raises(RuntimeError,match='synthetic publication failure'):
        service.execute_control_publication(db,**args,review_sha256=review['review_sha256'])
    assert facts(db)==before
    db.commit(); assert facts(db)==before


def test_review_change_refuses_before_writes(db,world,login,fresh,material_world):
    grant=setup(db,world,login,fresh); cmd=publication_command(db,world,grant)
    args=dict(**credentials(login),command=cmd)
    service.preview_control_publication(db,**args); before=facts(db)
    with pytest.raises(service.ControlProjectionError,match='review_changed'):
        service.execute_control_publication(db,**args,review_sha256='f'*64)
    assert facts(db)==before


def test_initial_proven_zero_is_a_complete_empty_batch(db,world,login,fresh,material_world):
    grant=setup(db,world,login,fresh);fresh(records=[],full=True)
    result,args=publish(db,world,login,grant)
    assert result['record_count']==result['origin_count']==0
    consume(db,result)
    assert service.read_control_publication(db,**args)==result


def test_unmanaged_control_identity_requires_reviewed_migration(db,world,login,fresh,material_world):
    grant=setup(db,world,login,fresh)
    inspected=service.planner.inspect_inventory_control_projection_plan(db,**credentials(login),request=request(db,world,grant))
    value=inspected['review']['plan']['lines'][0]['payload'];now=authority._now(db)
    obj=ExternalObject(id=uuid4(),source_system_id=world.source,entity_type='oam_inventory_control',
        external_id=value['external_business_key'],created_at=now,updated_at=now)
    db.add(obj);db.flush()
    version=ExternalObjectVersion(id=uuid4(),external_object_id=obj.id,source_version='legacy-unreviewed',valid_from=now,
        is_current=True,payload_jsonb=value,payload_sha256=service.planner._sha(value),created_at=now)
    db.add(version);db.flush();obj.current_version_id=version.id;db.commit()
    before=facts(db)
    with pytest.raises(service.ControlProjectionError,match='unmanaged_control_migration_required'):
        service.preview_control_publication(db,**credentials(login),command=publication_command(db,world,grant))
    assert facts(db)==before


def test_mapping_revocation_preserves_exact_history_but_blocks_new_publication(db,world,login,fresh,material_world):
    grant=setup(db,world,login,fresh);result,args=publish(db,world,login,grant)
    revoke_mapping(db,world,login,grant)
    assert service.read_control_publication(db,**args)==result
    assert service.execute_control_publication(db,**args)==result
    fresh(records=capture_rows());before=facts(db)
    with pytest.raises(ControlMappingError,match='mapping_unavailable'):
        service.preview_control_publication(db,**credentials(login),command=publication_command(db,world,grant))
    assert facts(db)==before


def test_cli_unknown_commit_is_recovered_without_second_apply(db,world,login,fresh,material_world,cli,tmp_path,monkeypatch,capsys):
    grant=setup(db,world,login,fresh);cmd=publication_command(db,world,grant)
    review=service.preview_control_publication(db,**credentials(login),command=cmd)
    document=tmp_path/'publication.json'
    document.write_text(json.dumps(dict(command=cmd.model_dump(mode='json'),
        expected_authorization_version=world.actor.authorization_version,review_sha256=review['review_sha256'])))
    engine=db.get_bind();db.rollback()
    import sqlalchemy
    monkeypatch.setattr(cli,'_connection_config',lambda:('sqlite://','synthetic'))
    monkeypatch.setattr(cli,'_database_preflight',lambda *a:None)
    monkeypatch.setattr(sqlalchemy,'create_engine',lambda *a,**kw:engine)
    monkeypatch.setattr(engine,'dispose',lambda:None)
    original=sqlalchemy.orm.Session.commit
    commits=0
    def lost_ack(session):
        nonlocal commits
        commits+=1;original(session);raise RuntimeError('synthetic lost commit acknowledgement')
    monkeypatch.setattr(sqlalchemy.orm.Session,'commit',lost_ack)
    def run(mode):
        read,write=os.pipe()
        try:
            os.write(write,login['access_token'].encode());os.close(write)
            return cli.main(['--control-publication-file',str(document),'--mode',mode,'--access-token-fd',str(read)])
        finally:os.close(read)
    assert run('apply')==3 and commits==1
    output=capsys.readouterr()
    assert output.out=='' and json.loads(output.err)['next_action']=='read_exact_status'
    assert run('status')==0 and commits==1
    recovered=json.loads(capsys.readouterr().out)
    assert recovered['recorded'] and recovered['projection_published'] and not recovered['start_ready']
    assert login['access_token'] not in json.dumps(recovered)
