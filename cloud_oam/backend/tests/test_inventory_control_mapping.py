"""Real schema, formal JWT/session, immutable audit and HMAC normalization."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import os
import runpy
from uuid import UUID,uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import inventory_control_mapping as service
from app import inventory_control_configuration as configuration
from app import inventory_control_normalization as normalization
from app import inventory_control_authority as authority
from app.inventory_control_mapping_models import InventoryControlMappingDecision as Decision
from app.inventory_control_models import InventoryControlCatalogVersion as Catalog
from app.foundation_models import FileObject,RolePermission,AuditEvent,SourceSystem
from app.models import AuthSession,User
from test_inventory_control_admission import db as admission_db,authority_db,capture_world,world,fresh,grants,facts,login,cli
from test_inventory_control_normalization import formal_material,capture_rows,RULES

PATH=Path(__file__).parents[1]/'alembic/versions/20261025_0115_inventory_control_mappings.py'


@pytest.fixture
def db(admission_db):
    with Operations.context(MigrationContext.configure(admission_db.connection())):
        Decision.__table__.drop(admission_db.connection())
        runpy.run_path(str(PATH))['upgrade']()
    admission_db.commit()
    return admission_db


def command(db,world,**changes):
    value=dict(action='grant',binding_id=world.binding,catalog_id=world.catalog,rules=RULES,
        expected_subject_sha256=db.get(Catalog,world.catalog).catalog_sha256,evidence_file_id=world.file,
        evidence_sha256='a'*64,reason='Synthetic exact rule review',idempotency_key=uuid4().hex,request_id=uuid4().hex)
    value.update(changes)
    return service.MappingCommand.model_validate(value)


def credentials(login):
    return {key:login[key] for key in ('access_token','expected_authorization_version')}


def apply(db,login,cmd,review=None):
    args=dict(**credentials(login),command=cmd)
    review=review or service.preview_inventory_control_mapping(db,**args)
    result=service.execute_inventory_control_mapping(db,**args,review_sha256=review['review_sha256'])
    db.commit()
    return result


def revoke(db,world,login,grant):
    return apply(db,login,command(db,world,action='revoke',rules=None,revoked_grant_id=grant['decision_id'],
        expected_subject_sha256=grant['payload_sha256']))


def resolve(db,world,grant):
    return service.resolve_inventory_control_mapping(db,binding_id=world.binding,catalog_id=world.catalog,decision_id=UUID(grant['decision_id']))


def test_preview_apply_recovery_and_repeat_change_only_mapping_and_authorization_audit(db,world,login):
    cmd=command(db,world);args=dict(**credentials(login),command=cmd)
    before=facts(db);review=service.preview_inventory_control_mapping(db,**args)
    assert facts(db)==before and 'storage_key' not in json.dumps(review)
    assert not service.read_inventory_control_mapping(db,**args,review_sha256=review['review_sha256'])['recorded']
    grant=apply(db,login,cmd,review);after=facts(db)
    assert {name for name in after if before[name]!=after[name]}=={'audit_events','audit_chain_heads','inventory_control_mapping_decisions'}
    assert apply(db,login,cmd,review)==grant
    assert service.read_inventory_control_mapping(db,**args,review_sha256=review['review_sha256'])==grant
    assert facts(db)==after and resolve(db,world,grant)['rules']==RULES
    assert login['access_token'] not in json.dumps(db.get(Decision,UUID(grant['decision_id'])).payload_jsonb)


def test_revocation_retains_history_blocks_old_version_and_new_version_must_be_explicit(db,world,login):
    grant=apply(db,login,command(db,world));revoke(db,world,login,grant)
    with pytest.raises(service.ControlMappingError,match='mapping_unavailable'):resolve(db,world,grant)
    with pytest.raises(service.ControlMappingError,match='revision_reused'):apply(db,login,command(db,world))
    replacement=apply(db,login,command(db,world,rules=dict(RULES,revision='synthetic-v2')))
    assert resolve(db,world,replacement)['rules']['revision']=='synthetic-v2'
    assert db.scalar(sa.select(sa.func.count()).select_from(Decision))==3
    with pytest.raises(service.ControlMappingError,match='mapping_unavailable'):resolve(db,world,grant)


@pytest.mark.parametrize('change',['request','key','reason','rules','review'])
def test_idempotency_and_review_binding_refuse_ambiguous_replay(db,world,login,change):
    cmd=command(db,world);review=service.preview_inventory_control_mapping(db,**credentials(login),command=cmd)
    apply(db,login,cmd,review);before=facts(db)
    if change=='review':review=dict(review,review_sha256='b'*64)
    else:
        values=cmd.model_dump()
        if change=='request':values['request_id']=uuid4().hex
        elif change=='key':values['idempotency_key']=uuid4().hex
        elif change=='reason':values['reason']='Different explicit request'
        else:values['rules']=dict(RULES,revision='synthetic-v2')
        cmd=service.MappingCommand.model_validate(values)
    with pytest.raises(service.ControlMappingError,match='request_conflict'):apply(db,login,cmd,review)
    assert facts(db)==before


@pytest.mark.parametrize('change',['file','history','authorization','session','subject','source'])
def test_changed_review_or_current_operator_cannot_apply(db,world,login,change):
    cmd=command(db,world);review=service.preview_inventory_control_mapping(db,**credentials(login),command=cmd)
    if change=='file':db.get(FileObject,world.file).mime_type='text/plain'
    elif change=='history':apply(db,login,command(db,world,rules=dict(RULES,revision='other-review')))
    elif change=='authorization':db.get(User,world.actor.user_id).authorization_version+=1
    elif change=='session':db.scalar(sa.select(AuthSession)).revoked_at=datetime.now(timezone.utc)
    elif change=='source':db.get(SourceSystem,world.source).enabled=False
    else:cmd=service.MappingCommand.model_validate(dict(cmd.model_dump(),expected_subject_sha256='b'*64))
    db.commit();before=facts(db)
    with pytest.raises((service.ControlMappingError,configuration.ControlConfigurationError,authority.ControlAuthorityError)):
        apply(db,login,cmd,review)
    assert facts(db)==before


@pytest.mark.parametrize('case',['future','expired','overlap','backdated','end_boundary'])
def test_explicit_validity_boundaries_and_overlap(db,world,login,monkeypatch,case):
    now=authority._now(db)
    if case=='backdated':
        with pytest.raises(service.ControlMappingError,match='invalid_validity'):
            apply(db,login,command(db,world,valid_from=now-timedelta(seconds=1)))
        return
    start=now+timedelta(minutes=1);end=now+timedelta(minutes=2)
    grant=apply(db,login,command(db,world,valid_from=start,valid_to=end))
    if case=='overlap':
        with pytest.raises(service.ControlMappingError,match='overlapping_grant'):
            apply(db,login,command(db,world,rules=dict(RULES,revision='synthetic-v2')))
        return
    monkeypatch.setattr(authority,'_now',lambda db: start if case=='end_boundary' else end if case=='expired' else now)
    if case=='end_boundary':
        assert resolve(db,world,grant)['rules']==RULES
        monkeypatch.setattr(authority,'_now',lambda db:end)
    with pytest.raises(service.ControlMappingError,match='mapping_unavailable'):resolve(db,world,grant)


@pytest.mark.parametrize('change',['quarantined','sha','metadata','catalog','unknown'])
def test_resolution_rechecks_exact_binding_and_file(db,world,login,change):
    grant=apply(db,login,command(db,world))
    if change=='quarantined':db.get(FileObject,world.file).status='quarantined'
    elif change=='sha':db.get(FileObject,world.file).sha256='b'*64
    elif change=='metadata':db.get(FileObject,world.file).size_bytes+=1
    db.commit();before=facts(db)
    with pytest.raises((service.ControlMappingError,authority.ControlAuthorityError)):
        service.resolve_inventory_control_mapping(db,binding_id=world.binding,
            catalog_id=uuid4() if change=='catalog' else world.catalog,
            decision_id=uuid4() if change=='unknown' else UUID(grant['decision_id']))
    assert facts(db)==before


def test_revoke_allowed_after_source_disabled_but_not_grant(db,world,login):
    grant=apply(db,login,command(db,world));db.get(SourceSystem,world.source).enabled=False;db.commit()
    revoke(db,world,login,grant)
    with pytest.raises(service.ControlMappingError,match='binding_unavailable'):
        apply(db,login,command(db,world,rules=dict(RULES,revision='synthetic-v2')))


def test_failed_audit_or_expiry_rolls_back_mapping_and_audit_together(db,world,login,monkeypatch):
    cmd=command(db,world);review=service.preview_inventory_control_mapping(db,**credentials(login),command=cmd)
    before=facts(db);original=configuration._finish;count=0
    def finish(*args):
        nonlocal count
        count+=1
        if count==2:raise configuration.ControlConfigurationError('synthetic expiration')
        return original(*args)
    monkeypatch.setattr(configuration,'_finish',finish)
    with pytest.raises(configuration.ControlConfigurationError,match='synthetic expiration'):apply(db,login,cmd,review)
    assert facts(db)==before


@pytest.mark.parametrize('mode',['full','delta','zero'])
def test_actual_hmac_normalization_uses_approved_rules_then_refuses_revoked_version(db,world,fresh,formal_material,login,mode):
    grants(db,world);fresh(records=capture_rows())
    grant=apply(db,login,command(db,world))
    if mode!='full':fresh(records=[] if mode=='zero' else capture_rows()[:1])
    args=dict(preparation_id=world.root,mapping_decision_id=UUID(grant['decision_id']),**credentials(login))
    before=facts(db);result=configuration.inspect_inventory_control_capture(db,**args)
    assert facts(db)==before and result['normalization_rules_authorized']
    assert result['normalization_review']['normalization_complete'] is (mode == 'zero')
    assert result['normalization_review']['candidate_groups'] == []
    assert result['normalization_review']['target_record_count']=={'full':2,'delta':1,'zero':0}[mode]
    assert not result['master_source_evidence_verified'] and not result['projection_published'] and not result['start_ready']
    with pytest.raises(normalization.ControlNormalizationError,match='conflicting_rule_inputs'):
        configuration.inspect_inventory_control_capture(db,normalization_rules=RULES,**args)
    revoke(db,world,login,grant)
    with pytest.raises(service.ControlMappingError,match='mapping_unavailable'):
        configuration.inspect_inventory_control_capture(db,**args)


def test_mapping_expiry_during_normalization_is_rechecked(db,world,fresh,formal_material,login,monkeypatch):
    grants(db,world);fresh(records=capture_rows());end=authority._now(db)+timedelta(minutes=1)
    grant=apply(db,login,command(db,world,valid_to=end));original=normalization.normalize_records
    def expire(**kwargs):
        result=original(**kwargs);monkeypatch.setattr(authority,'_now',lambda db:end);return result
    monkeypatch.setattr(normalization,'normalize_records',expire)
    with pytest.raises(service.ControlMappingError,match='mapping_unavailable'):
        configuration.inspect_inventory_control_capture(db,preparation_id=world.root,mapping_decision_id=UUID(grant['decision_id']),**credentials(login))


def test_cli_preview_apply_status_and_approved_mapping_inspection(db,world,fresh,formal_material,login,cli,tmp_path,monkeypatch,capsys):
    grants(db,world);fresh(records=capture_rows());engine=db.get_bind()
    monkeypatch.setattr(sa,'create_engine',lambda *a,**kw:engine);monkeypatch.setattr(engine,'dispose',lambda:None)
    monkeypatch.setattr(cli,'_connection_config',lambda:('synthetic-dsn','synthetic-db'))
    monkeypatch.setattr(cli,'_database_preflight',lambda db,target:None)
    path=tmp_path/'command.json';doc=dict(command=authority._json(command(db,world).model_dump()),expected_authorization_version=login['expected_authorization_version'])
    def run(args):
        reader,writer=os.pipe();os.write(writer,login['access_token'].encode());os.close(writer)
        try:status=cli.main([*args,'--access-token-fd',str(reader)])
        finally:os.close(reader)
        output=capsys.readouterr();assert status==0 and not output.err and login['access_token'] not in output.out
        return json.loads(output.out)
    path.write_text(json.dumps(doc));review=run(['--mapping-file',str(path)])
    doc['review_sha256']=review['review_sha256'];path.write_text(json.dumps(doc))
    grant=run(['--mapping-file',str(path),'--mode','apply']);saved=facts(db)
    assert run(['--mapping-file',str(path),'--mode','status'])['decision_id']==grant['decision_id']
    path.write_text(json.dumps(dict(preparation_id=str(world.root),mapping_decision_id=grant['decision_id'],expected_authorization_version=login['expected_authorization_version'])))
    assert run(['--inspection-file',str(path)])['normalization_rules_authorized'] and facts(db)==saved
