"""Real JWT, migrated material receipts, immutable grants and precise recovery."""
from source_configuration_file_fixtures import source_evidence
from datetime import datetime,timedelta,timezone
import importlib.util
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID,uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import material_source_authority as service
from app import inventory_control_authority as authority
from app import inventory_control_configuration as configuration
from app import material_capture_ingress as ingress
from app.foundation_models import AuditChainHead,FileObject,Role,RolePermission,Permission,SourceSystem
from app.formal_access import FormalAccessError,load_formal_principal
from app.material_source_authority_models import MaterialSourceAuthorityDecision as Decision
from app.models import AuthSession,User
from app.security import create_access_token
from test_material_capture_ingress import db as ingress_db,world as transport_world,receive,bundle,Receipt,Binding
from test_inventory_control_admission import facts
from test_formal_access import make_organization,make_user,assign

PATH=Path(__file__).parents[1]/'alembic/versions/20261027_0117_material_source_authority.py'


@pytest.fixture
def db(ingress_db):
    migration=runpy.run_path(str(PATH))
    ingress_db.add(Role(id=migration['ADMIN_ROLE_ID'],code='admin',name='Synthetic HQ',is_external=False,status='active'))
    ingress_db.add(AuditChainHead(stream_key='authorization',version=0));ingress_db.commit()
    with Operations.context(MigrationContext.configure(ingress_db.connection())):
        Decision.__table__.drop(ingress_db.connection());migration['upgrade']()
    ingress_db.commit()
    return ingress_db


@pytest.fixture
def world(db,transport_world):
    organization=make_organization(db,name='Synthetic HQ')
    user,_=make_user(db,organization,name='Synthetic material reviewer')
    role=db.scalar(sa.select(Role).where(Role.code=='admin'));assignment=assign(db,user,role,scope_type='national',scope_id='*')
    db.commit()
    file, _ = source_evidence(db, user.id)
    db.add(file);db.commit()
    return SimpleNamespace(actor=load_formal_principal(db,user.id),file=file.id,binding=transport_world.binding.id,
        source=transport_world.source.id,assignment=assignment.id,transport=transport_world)


@pytest.fixture
def login(db,world):
    now=authority._now(db)
    session=AuthSession(user_id=world.actor.user_id,refresh_token_hash=uuid4().hex+uuid4().hex,client_type='web',
        device_id=uuid4().hex,ip_address='hmac:1:'+'a'*64,created_at=now-timedelta(seconds=1),expires_at=now+timedelta(hours=1))
    db.add(session);db.commit()
    return dict(access_token=create_access_token(world.actor.user_id,session.id),expected_authorization_version=world.actor.authorization_version)


def command(db,world,**changes):
    binding,subject=service._subject(db,world.binding)
    value=dict(action='grant',binding_id=binding.id,expected_subject_sha256=service.preparation._sha(subject),
        valid_to=authority._now(db)+timedelta(minutes=40),evidence_file_id=world.file,evidence_sha256='a'*64,
        reason='Synthetic material source scope review',idempotency_key=uuid4().hex,request_id=uuid4().hex)
    value.update(changes)
    return service.MaterialSourceCommand.model_validate(value)


def apply(db,login,cmd,review=None):
    args=dict(**login,command=cmd)
    review=review or service.preview_material_source_authority(db,**args)
    result=service.execute_material_source_authority(db,**args,review_sha256=review['review_sha256'])
    db.commit();return result


def revoke(db,world,login,grant):
    return apply(db,login,command(db,world,action='revoke',valid_to=None,revoked_grant_id=grant['decision_id'],
        expected_subject_sha256=grant['payload_sha256']))


def capture_receipt(db,rows=None,now=None):
    result=receive(db,bundle(rows,now=now or authority._now(db)))
    return UUID(result['receipt_id'])


def inspect(db,receipt_id):
    return service.inspect_authorized_material_capture(db,receipt_id=receipt_id)


def test_preview_apply_recovery_only_add_authority_and_audit(db,world,login):
    cmd=command(db,world);args=dict(**login,command=cmd);before=facts(db)
    review=service.preview_material_source_authority(db,**args)
    assert facts(db)==before and 'storage_key' not in json.dumps(review)
    missing=service.read_material_source_authority(db,**args,review_sha256=review['review_sha256'])
    assert not missing['recorded'] and not missing['retry_allowed']
    grant=apply(db,login,cmd,review);after=facts(db)
    assert {name for name in after if before[name]!=after[name]}=={'audit_events','audit_chain_heads',Decision.__tablename__}
    assert apply(db,login,cmd,review)==grant
    assert service.read_material_source_authority(db,**args,review_sha256=review['review_sha256'])==grant and facts(db)==after
    row=db.get(Decision,UUID(grant['decision_id']));assert service._prove(db,row)==row
    assert login['access_token'] not in json.dumps(row.payload_jsonb) and not grant['projection_published'] and not grant['start_ready']


@pytest.mark.parametrize('empty',[False,True])
def test_grant_then_authenticated_capture_is_admitted_without_any_projection(db,world,login,empty):
    grant=apply(db,login,command(db,world));receipt=capture_receipt(db,[] if empty else None);before=facts(db)
    result=service.inspect_material_source_configuration(db,receipt_id=receipt,**login)
    assert result['channel_attested'] and result['source_authorized'] and result['current_decision_id']==grant['decision_id']
    assert result['observed_count']==(0 if empty else 1) and facts(db)==before
    assert not any(result[k] for k in ('full_catalog_verified','master_source_evidence_verified','projection_published','start_ready'))


def test_retrospective_grant_never_authorizes_an_earlier_capture(db,world,login):
    receipt=capture_receipt(db);apply(db,login,command(db,world));before=facts(db)
    with pytest.raises(service.MaterialSourceAuthorityError,match='source_authority_unavailable'):inspect(db,receipt)
    assert facts(db)==before
    assert inspect(db,capture_receipt(db))['source_authorized']


def test_revoke_retains_original_result_and_requires_a_new_capture(db,world,login):
    cmd=command(db,world);review=service.preview_material_source_authority(db,command=cmd,**login)
    grant=apply(db,login,cmd,review);receipt=capture_receipt(db);revoke(db,world,login,grant)
    replacement=apply(db,login,command(db,world))
    with pytest.raises(service.MaterialSourceAuthorityError,match='source_authority_unavailable'):inspect(db,receipt)
    assert inspect(db,capture_receipt(db))['current_decision_id']==replacement['decision_id']
    assert service.read_material_source_authority(db,command=cmd,review_sha256=review['review_sha256'],**login)==grant
    assert db.scalar(sa.select(sa.func.count()).select_from(Decision))==3


@pytest.mark.parametrize('change',['reason','key','request','review','subject'])
def test_exact_request_and_review_cannot_be_replayed_with_changes(db,world,login,change):
    cmd=command(db,world);review=service.preview_material_source_authority(db,command=cmd,**login);apply(db,login,cmd,review)
    values=cmd.model_dump()
    if change=='review':review=dict(review,review_sha256='b'*64)
    elif change=='reason':values['reason']='Changed review'
    elif change=='key':values['idempotency_key']=uuid4().hex
    elif change=='request':values['request_id']=uuid4().hex
    else:values['expected_subject_sha256']='b'*64
    before=facts(db)
    with pytest.raises(service.MaterialSourceAuthorityError,match='request_conflict'):
        apply(db,login,service.MaterialSourceCommand.model_validate(values),review)
    assert facts(db)==before


@pytest.mark.parametrize('change',['session','role','identity','authorization','file','source','transport','history'])
def test_apply_rechecks_current_session_permission_review_and_source(db,world,login,change):
    cmd=command(db,world);review=service.preview_material_source_authority(db,command=cmd,**login)
    if change=='session':db.scalar(sa.select(AuthSession)).revoked_at=authority._now(db)
    elif change=='role':db.scalar(sa.select(RolePermission)).effect='deny'
    elif change=='identity':
        from app.foundation_models import AuthIdentity
        identity=db.scalar(sa.select(AuthIdentity));identity.status='revoked';identity.revoked_at=authority._now(db)
    elif change=='authorization':db.get(User,world.actor.user_id).authorization_version+=1
    elif change=='file':db.get(FileObject,world.file).size_bytes+=1
    elif change=='source':db.get(SourceSystem,world.source).enabled=False
    elif change=='transport':db.get(Binding,world.binding).revoked_at=authority._now(db)
    else:apply(db,login,command(db,world))
    db.commit();before=facts(db)
    with pytest.raises((service.MaterialSourceAuthorityError,configuration.ControlConfigurationError,authority.ControlAuthorityError,FormalAccessError)):
        apply(db,login,cmd,review)
    assert facts(db)==before


@pytest.mark.parametrize('change',['quarantine','file_hash','file_metadata','source','transport','binding','body','stale'])
def test_capture_inspection_reproves_evidence_and_never_changes_business_facts(db,world,login,monkeypatch,change):
    apply(db,login,command(db,world));receipt=capture_receipt(db)
    if change=='quarantine':db.get(FileObject,world.file).status='quarantined'
    elif change=='file_hash':db.get(FileObject,world.file).sha256='b'*64
    elif change=='file_metadata':db.get(FileObject,world.file).mime_type='text/plain'
    elif change=='source':db.get(SourceSystem,world.source).enabled=False
    elif change=='transport':db.get(Binding,world.binding).revoked_at=authority._now(db)
    elif change in ('binding','body'):
        original=ingress._prove
        def changed(row):
            result=original(row)
            if change=='binding':row.key_fingerprint='b'*64
            else:raise ingress.MaterialMasterCaptureError('synthetic changed body')
            return result
        monkeypatch.setattr(ingress,'_prove',changed)
    else:monkeypatch.setattr(authority,'_now',lambda db:datetime.now(timezone.utc)+timedelta(minutes=46))
    db.commit();before=facts(db)
    with pytest.raises((service.MaterialSourceAuthorityError,authority.ControlAuthorityError,ingress.MaterialMasterCaptureError)):
        inspect(db,receipt)
    db.rollback();assert facts(db)==before


@pytest.mark.parametrize('case',['overlap','backdated','beyond_key','future','end'])
def test_grant_and_transport_windows_are_explicit(db,world,login,monkeypatch,case):
    now=authority._now(db)
    if case=='backdated':changes=dict(valid_from=now-timedelta(seconds=1))
    elif case=='beyond_key':changes=dict(valid_to=now+timedelta(hours=2))
    else:changes=dict(valid_from=now+timedelta(seconds=10),valid_to=now+timedelta(seconds=20))
    if case in ('backdated','beyond_key'):
        with pytest.raises(service.MaterialSourceAuthorityError):apply(db,login,command(db,world,**changes))
        return
    apply(db,login,command(db,world,**changes))
    if case=='overlap':
        with pytest.raises(service.MaterialSourceAuthorityError,match='overlapping_grant'):apply(db,login,command(db,world))
    else:
        receipt=capture_receipt(db)
        if case=='end':monkeypatch.setattr(authority,'_now',lambda db:changes['valid_to'])
        with pytest.raises(service.MaterialSourceAuthorityError,match='source_authority_unavailable'):inspect(db,receipt)


@pytest.mark.parametrize('gap',[False,True])
def test_natural_renewal_covers_the_whole_capture_to_receipt_interval(db,world,login,monkeypatch,gap):
    now=authority._now(db);start=now+timedelta(seconds=5);boundary=now+timedelta(seconds=10);received=now+timedelta(seconds=15)
    first=apply(db,login,command(db,world,valid_from=start,valid_to=boundary))
    second=apply(db,login,command(db,world,valid_from=boundary+timedelta(microseconds=1) if gap else boundary,valid_to=now+timedelta(minutes=2)))
    monkeypatch.setattr(ingress,'clock',lambda db:received)
    receipt=capture_receipt(db,now=start);monkeypatch.setattr(authority,'_now',lambda db:received)
    if gap:
        with pytest.raises(service.MaterialSourceAuthorityError,match='source_authority_unavailable'):inspect(db,receipt)
    else:
        result=inspect(db,receipt)
        assert {span['decision_id'] for span in result['authority_spans']}=={first['decision_id'],second['decision_id']}
        assert result['current_decision_id']==second['decision_id']


def test_stopped_source_can_be_revoked_and_history_recovered(db,world,login):
    cmd=command(db,world);review=service.preview_material_source_authority(db,command=cmd,**login);grant=apply(db,login,cmd,review)
    db.get(SourceSystem,world.source).enabled=False;db.get(Binding,world.binding).revoked_at=authority._now(db);db.commit()
    revoke(db,world,login,grant)
    assert service.read_material_source_authority(db,command=cmd,review_sha256=review['review_sha256'],**login)==grant


def test_post_audit_expiry_and_caller_rollback_leave_no_partial_decision(db,world,login,monkeypatch):
    cmd=command(db,world);review=service.preview_material_source_authority(db,command=cmd,**login);before=facts(db)
    original=service.append_audit_event
    def expired(*args,**kwargs):
        result=original(*args,**kwargs)
        raise RuntimeError('synthetic failure after audit')
    with monkeypatch.context() as patch:
        patch.setattr(service,'append_audit_event',expired)
        with pytest.raises(RuntimeError):apply(db,login,cmd,review)
    assert facts(db)==before
    service.execute_material_source_authority(db,command=cmd,review_sha256=review['review_sha256'],**login)
    db.rollback();assert facts(db)==before


@pytest.fixture
def cli():
    path=PATH.parents[3]/'scripts/configure_inventory_control.py'
    spec=importlib.util.spec_from_file_location('material_authority_cli_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.mark.parametrize('lost_ack',[False,True])
def test_cli_preview_apply_original_status_and_capture_inspection(db,world,login,cli,tmp_path,monkeypatch,capsys,lost_ack):
    engine=db.get_bind();monkeypatch.setattr(sa,'create_engine',lambda *a,**kw:engine);monkeypatch.setattr(engine,'dispose',lambda:None)
    monkeypatch.setattr(cli,'_connection_config',lambda:('synthetic-dsn','synthetic-db'))
    monkeypatch.setattr(cli,'_database_preflight',lambda db,target:None)
    doc=dict(command=authority._json(command(db,world).model_dump()),expected_authorization_version=login['expected_authorization_version'])
    path=tmp_path/'command.json'
    def run(args,expected=0):
        reader,writer=os.pipe();os.write(writer,login['access_token'].encode());os.close(writer)
        try:code=cli.main([*args,'--access-token-fd',str(reader)])
        finally:os.close(reader)
        output=capsys.readouterr();assert code==expected and login['access_token'] not in output.out+output.err
        if expected:
            assert not output.out
            return json.loads(output.err)
        assert not output.err
        return json.loads(output.out)
    path.write_text(json.dumps(doc));review=run(['--material-source-file',str(path)])
    doc['review_sha256']=review['review_sha256'];path.write_text(json.dumps(doc))
    if lost_ack:
        original=sa.orm.Session.commit;attempts=[]
        def commit_and_lose(session):
            attempts.append(1);original(session);raise RuntimeError('synthetic lost acknowledgement')
        with monkeypatch.context() as patch:
            patch.setattr(sa.orm.Session,'commit',commit_and_lose)
            unknown=run(['--material-source-file',str(path),'--mode','apply'],expected=3)
        assert attempts==[1] and unknown['next_action']=='read_exact_status'
        grant=run(['--material-source-file',str(path),'--mode','status'])
        assert grant['recorded'] and db.scalar(sa.select(sa.func.count()).select_from(Decision))==1
    else:grant=run(['--material-source-file',str(path),'--mode','apply'])
    assert run(['--material-source-file',str(path),'--mode','status'])['decision_id']==grant['decision_id']
    receipt=capture_receipt(db);before=facts(db)
    path.write_text(json.dumps(dict(receipt_id=str(receipt),expected_authorization_version=login['expected_authorization_version'])))
    assert run(['--material-inspection-file',str(path)])['source_authorized'] and facts(db)==before


def test_authority_expiring_after_audit_rolls_back_new_facts(db,world,login,monkeypatch):
    end=authority._now(db)+timedelta(seconds=10)
    cmd=command(db,world,valid_to=end);review=service.preview_material_source_authority(db,command=cmd,**login)
    before=facts(db);original=service.append_audit_event
    def expire(*args,**kwargs):
        result=original(*args,**kwargs);monkeypatch.setattr(authority,'_now',lambda db:end);return result
    monkeypatch.setattr(service,'append_audit_event',expire)
    with pytest.raises(service.MaterialSourceAuthorityError,match='expired_during_execution'):apply(db,login,cmd,review)
    assert facts(db)==before


def test_capture_freshness_still_required_with_valid_longer_authority(db,world,login,monkeypatch):
    start=authority._now(db)
    apply(db,login,command(db,world,valid_to=start+timedelta(minutes=58)));receipt=capture_receipt(db)
    monkeypatch.setattr(authority,'_now',lambda db:start+timedelta(minutes=46))
    with pytest.raises(service.MaterialSourceAuthorityError,match='capture_stale'):inspect(db,receipt)


def test_authority_expiry_while_waiting_for_file_lock_is_rechecked(db,world,login,monkeypatch):
    end=authority._now(db)+timedelta(seconds=10)
    apply(db,login,command(db,world,valid_to=end));receipt=capture_receipt(db);original=authority._file
    def expire(*args,**kwargs):
        result=original(*args,**kwargs);monkeypatch.setattr(authority,'_now',lambda db:end);return result
    monkeypatch.setattr(authority,'_file',expire)
    with pytest.raises(service.MaterialSourceAuthorityError,match='source_authority_unavailable'):inspect(db,receipt)
