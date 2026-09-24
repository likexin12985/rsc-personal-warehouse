"""Actual formal identities, audit chains and migrated synthetic authority facts."""
from source_configuration_file_fixtures import source_evidence
from datetime import timedelta
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.database import Base
from app import inventory_control_authority as service
from app.formal_access import load_formal_principal
from app.foundation_models import AuditChainHead, FileObject, Role, RolePermission, SourceSystem
from app.inventory_control_models import InventoryControlPreparation as Preparation, TABLES
from app.inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from test_inventory_control_preparation import prepared, record as prepare, PATH as PREPARATION_PATH
from test_formal_access import make_organization, make_user, assign

PATH = Path(__file__).parents[1]/'alembic/versions/20261023_0113_inventory_control_authority.py'


@pytest.fixture
def db():
    engine=sa.create_engine('sqlite+pysqlite:///:memory:',
        connect_args={'check_same_thread': False}, poolclass=sa.pool.StaticPool)
    @sa.event.listens_for(engine,'connect')
    def foreign_keys(connection,_):connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine,tables=[table for table in Base.metadata.tables.values()
                                         if table.name not in (*TABLES,Decision.__tablename__)])
    migration=runpy.run_path(str(PATH))
    with Session(engine) as db:
        db.add(Role(id=migration['ADMIN_ROLE_ID'],code='admin',name='Synthetic HQ',is_external=False,status='active'))
        db.add(AuditChainHead(stream_key='authorization',version=0));db.commit()
    with engine.begin() as connection,Operations.context(MigrationContext.configure(connection)):
        runpy.run_path(str(PREPARATION_PATH))['upgrade']();migration['upgrade']()
    with Session(engine) as db:yield db
    engine.dispose()


@pytest.fixture
def world(db,prepared):
    value=prepare(db,prepared);db.commit();root=db.get(Preparation,value['preparation_id'])
    organization=make_organization(db,name='Synthetic HQ')
    user,_=make_user(db,organization,name='Synthetic reviewer')
    role=db.scalar(sa.select(Role).where(Role.code=='admin'))
    assignment=assign(db,user,role,scope_type='national',scope_id='*')
    db.commit()
    file, _ = source_evidence(db, user.id)
    db.add(file);db.commit()
    return SimpleNamespace(actor=load_formal_principal(db,user.id),file=file.id,root=root.id,binding=root.binding_id,
        catalog=root.catalog_id,source=prepared.source,assignment=assignment.id)


def command(db,world,*,action='source_grant',grant=None,**changes):
    binding,catalog,_=service._subject(db,world.binding,world.catalog)
    data=dict(action=action,binding_id=world.binding,evidence_file_id=world.file,evidence_sha256='a'*64,
        reason='Synthetic exact version review',idempotency_key='authority-'+uuid4().hex,request_id='request-'+uuid4().hex,
        expected_subject_sha256=binding.binding_sha256)
    if action=='catalog_grant':
        data.update(catalog_id=world.catalog,source_grant_id=grant['decision_id'],expected_subject_sha256=catalog.catalog_sha256)
    elif action=='revoke':
        data.update(catalog_id=grant['catalog_id'],revoked_grant_id=grant['decision_id'],expected_subject_sha256=grant['payload_sha256'])
    data.update(changes)
    return service.AuthorityCommand(**data)


def decide(db,world,cmd=None,**kwargs):
    result=service.record_inventory_control_authority(db,actor=world.actor,command=cmd or command(db,world,**kwargs))
    db.commit();return result


def state(db,world):
    return service.resolve_inventory_control_authority(db,preparation_id=world.root)


def history(db):
    return tuple(tuple(db.execute(sa.text(f'SELECT * FROM {table} ORDER BY id')))
                 for table in (Decision.__tablename__,'audit_events','audit_chain_heads'))


def test_exact_source_then_catalogue_and_revocation_preserve_history_and_never_publish(db,world):
    assert not state(db,world)['source_authorized']
    source_command=command(db,world);source=decide(db,world,source_command)
    assert state(db,world)['source_authorized'] and not state(db,world)['catalog_authorized']
    catalogue=decide(db,world,action='catalog_grant',grant=source)
    current=state(db,world);assert current['catalog_authorized']
    assert all(current[key] is False for key in ('source_authenticated','capture_attested','projection_published','start_ready'))
    saved=history(db);assert decide(db,world,source_command)==source and history(db)==saved
    revoke=decide(db,world,action='revoke',grant=source)
    assert not state(db,world)['source_authorized'] and not state(db,world)['catalog_authorized']
    assert decide(db,world,source_command)==source and not state(db,world)['source_authorized']
    for item in (source,catalogue,revoke):assert service._prove(db,db.get(Decision,item['decision_id'])) is not None
    assert db.scalar(sa.text('SELECT count(*) FROM inventory_transactions'))==0
    assert db.scalar(sa.text('SELECT count(*) FROM sync_runs'))==0


def test_revoking_catalogue_retains_source_and_allows_explicit_new_catalogue_grant(db,world):
    source=decide(db,world);catalogue=decide(db,world,action='catalog_grant',grant=source)
    decide(db,world,action='revoke',grant=catalogue)
    assert state(db,world)['source_authorized'] and not state(db,world)['catalog_authorized']
    second=decide(db,world,action='catalog_grant',grant=source)
    assert state(db,world)['catalog_grant_id']==second['decision_id']!=catalogue['decision_id']


def test_source_revocation_allows_explicit_new_source_and_catalogue_grants(db,world):
    old_source=decide(db,world);old_catalogue=decide(db,world,action='catalog_grant',grant=old_source)
    decide(db,world,action='revoke',grant=old_source)
    source=decide(db,world)
    assert not state(db,world)['catalog_authorized']
    catalogue=decide(db,world,action='catalog_grant',grant=source)
    assert state(db,world)['catalog_grant_id']==catalogue['decision_id']!=old_catalogue['decision_id']


@pytest.mark.parametrize('change',['version','file_hash','file_pending','file_quarantined','file_empty','disabled_source','permission_deny','user_inactive','stale_actor','future_identity'])
def test_wrong_version_evidence_or_current_authority_never_creates_decision(db,world,change):
    from app.models import User
    from app.foundation_models import AuthIdentity
    cmd=command(db,world)
    if change=='version':cmd=cmd.model_copy(update={'expected_subject_sha256':'b'*64})
    elif change=='file_hash':db.get(FileObject,world.file).sha256='b'*64
    elif change=='file_pending':db.get(FileObject,world.file).status='pending'
    elif change=='file_quarantined':db.get(FileObject,world.file).status='quarantined'
    elif change=='file_empty':db.get(FileObject,world.file).size_bytes=0
    elif change=='disabled_source':db.get(SourceSystem,world.source).enabled=False
    elif change=='permission_deny':db.scalar(sa.select(RolePermission)).effect='deny'
    elif change=='user_inactive':db.get(User,world.actor.user_id).is_active=False
    elif change=='stale_actor':db.get(User,world.actor.user_id).authorization_version+=1
    else:db.scalar(sa.select(AuthIdentity).where(AuthIdentity.user_id==world.actor.user_id)).verified_at=service._now(db)+timedelta(days=1)
    db.commit();before=history(db)
    with pytest.raises(service.ControlAuthorityError):decide(db,world,cmd)
    db.commit();assert history(db)==before


@pytest.mark.parametrize('change',['key_payload','request_id','overlap','parent','parent_revoked','parent_validity'])
def test_conflicting_requests_and_parent_authority_are_rejected(db,world,change):
    original=command(db,world,valid_to=service._now(db)+timedelta(days=1))
    source=decide(db,world,original)
    if change=='key_payload':cmd=original.model_copy(update={'reason':'Changed review'})
    elif change=='request_id':cmd=original.model_copy(update={'idempotency_key':'different-'+uuid4().hex})
    elif change=='overlap':cmd=command(db,world)
    elif change=='parent':cmd=command(db,world,action='catalog_grant',grant=source,source_grant_id=uuid4())
    elif change=='parent_revoked':
        decide(db,world,action='revoke',grant=source)
        cmd=command(db,world,action='catalog_grant',grant=source,valid_to=original.valid_to)
    else:cmd=command(db,world,action='catalog_grant',grant=source)
    before=history(db)
    with pytest.raises(service.ControlAuthorityError):decide(db,world,cmd)
    db.commit();assert history(db)==before


def test_validity_is_current_and_expiry_is_not_retroactive_corruption(db,world,monkeypatch):
    now=service._now(db);starts=now+timedelta(hours=1);ends=now+timedelta(hours=2)
    source=decide(db,world,valid_from=starts,valid_to=ends)
    decide(db,world,action='catalog_grant',grant=source,valid_from=starts,valid_to=ends)
    assert not state(db,world)['source_authorized']
    monkeypatch.setattr(service,'_now',lambda db:starts)
    assert state(db,world)['catalog_authorized']
    monkeypatch.setattr(service,'_now',lambda db:ends)
    assert not state(db,world)['source_authorized']
    assert service._prove(db,db.get(Decision,source['decision_id'])) is not None


def test_revocation_after_source_disable_is_still_possible(db,world):
    source=decide(db,world);db.get(SourceSystem,world.source).enabled=False;db.commit()
    assert not state(db,world)['source_authorized']
    decide(db,world,action='revoke',grant=source)
    assert service._revocation(db,db.get(Decision,source['decision_id'])) is not None


def test_evidence_file_changes_disable_current_authority_without_erasing_decision(db,world):
    source=decide(db,world);db.get(FileObject,world.file).status='quarantined';db.commit()
    assert not state(db,world)['source_authorized']
    assert service._prove(db,db.get(Decision,source['decision_id'])) is not None


def test_audit_failure_rolls_back_even_if_caller_commits(db,world,monkeypatch):
    before=history(db)
    def fail(*args,**kwargs):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(service,'append_audit_event',fail)
    with pytest.raises(RuntimeError,match='synthetic audit failure'):decide(db,world)
    db.commit();assert history(db)==before


def test_final_proof_failure_rolls_back_decision_and_audit(db,world,monkeypatch):
    before=history(db)
    monkeypatch.setattr(service,'_prove',lambda *args,**kwargs:service._fail('synthetic_final_failure'))
    with pytest.raises(service.ControlAuthorityError,match='synthetic_final_failure'):decide(db,world)
    db.commit();assert history(db)==before


def test_caller_rollback_removes_successful_decision_and_audit(db,world):
    before=history(db);cmd=command(db,world)
    service.record_inventory_control_authority(db,actor=world.actor,command=cmd)
    db.rollback();assert history(db)==before


def test_immutable_decisions_and_populated_downgrade(db,world):
    decide(db,world);before=history(db)
    for sql in ('UPDATE inventory_control_authority_decisions SET id=id','DELETE FROM inventory_control_authority_decisions'):
        with pytest.raises(sa.exc.IntegrityError,match='append-only'),db.begin_nested():db.execute(sa.text(sql))
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='authority must be retained'):runpy.run_path(str(PATH))['downgrade']()
    assert history(db)==before


def test_pending_caller_edits_are_preserved(db,world):
    cmd=command(db,world)
    file=db.get(FileObject,world.file);file.sha256='c'*64
    with pytest.raises(service.ControlAuthorityError,match='clean_session'):
        service.record_inventory_control_authority(db,actor=world.actor,command=cmd)
    with pytest.raises(service.ControlAuthorityError,match='pending_evidence'):state(db,world)
    assert file in db.dirty and file.sha256=='c'*64


def test_authority_observation_is_select_only_and_preserves_unrelated_outbox(db,world):
    from app.foundation_models import OutboxEvent
    decide(db,world)
    pending=OutboxEvent(event_type='synthetic',aggregate_type='test',aggregate_id=uuid4().hex,
        payload_jsonb={},idempotency_key=uuid4().hex)
    db.add(pending);connection=db.connection();statements=[]
    def capture(_connection,_cursor,sql,*_):statements.append(sql.strip().split()[0])
    sa.event.listen(connection,'before_cursor_execute',capture)
    try:assert state(db,world)['source_authorized']
    finally:sa.event.remove(connection,'before_cursor_execute',capture)
    assert pending in db.new and set(statements)=={'SELECT'}


def test_pending_audit_head_is_not_refreshed_away(db,world):
    decide(db,world)
    head=db.scalar(sa.select(AuditChainHead));head.version+=1;changed=head.version
    with pytest.raises(service.ControlAuthorityError,match='pending_evidence'):state(db,world)
    assert head in db.dirty and head.version==changed
