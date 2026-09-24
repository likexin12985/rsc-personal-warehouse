"""Signed receipt to reviewed catalogue, immutable history and recovery."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import runpy
from uuid import UUID,uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import material_projection as service
from app import material_source_authority as source
from app.foundation_models import ExternalObject,ExternalObjectVersion,FileObject,SourceSystem,Permission,RolePermission,Role
from app.inventory_models import FormalMaterial,MaterialInventoryPolicy
from app.material_projection_models import MaterialProjectionPublication as Publication,MaterialProjectionLine as Line
from app.models import AuthSession,User
from app.formal_services.material_catalog import list_active_materials
from test_material_source_authority import db as source_db,ingress_db,transport_world,world,login,command as grant_command,apply as grant_apply,capture_receipt,revoke,authority,facts,Binding

PATH=Path(__file__).parents[1]/'alembic/versions/20261029_0119_material_publications.py'


@pytest.fixture
def db(source_db):
    with Operations.context(MigrationContext.configure(source_db.connection())):
        Line.__table__.drop(source_db.connection());Publication.__table__.drop(source_db.connection())
        runpy.run_path(str(PATH))['upgrade']()
    source_db.commit();return source_db


def command(db,world,receipt_id,**changes):
    receipt=db.get(service.Receipt,receipt_id)
    decisions=[dict(sku_code=r['external_id'],raw_sha256=service.digest(r['data']),base_unit='piece',status='active',
                    tracking_mode='none',quantity_scale=0,allow_fraction=False) for r in receipt.payload_jsonb['capture']['records']]
    values=dict(receipt_id=receipt_id,capture_sha256=receipt.capture_sha256,decisions=decisions,
        evidence_file_id=world.file,evidence_sha256='a'*64,reason='Synthetic explicit field and policy review',
        idempotency_key=uuid4().hex,request_id=uuid4().hex)
    values.update(changes);return service.MaterialPublicationCommand.model_validate(values)


def prepare(db,world,login,rows=None):
    grant=grant_apply(db,login,grant_command(db,world))
    return grant,command(db,world,capture_receipt(db,rows))


def apply(db,login,cmd,review=None):
    args=dict(**login,command=cmd)
    review=review or service.preview_material_publication(db,**args)
    result=service.execute_material_publication(db,**args,review_sha256=review['review_sha256'])
    db.commit();return result


def test_receipt_review_publication_catalogue_and_exact_recovery(db,world,login):
    permission=Permission(resource='inventory',action='read',field_code='',description='Synthetic catalogue reader')
    db.add(permission);db.flush()
    db.add(RolePermission(role_id=db.scalar(sa.select(Role.id).where(Role.code=='admin')),permission_id=permission.id,effect='allow'))
    db.commit()
    _,cmd=prepare(db,world,login);before=facts(db)
    review=service.preview_material_publication(db,**login,command=cmd)
    assert facts(db)==before and 'storage_key' not in str(review)
    args=dict(**login,command=cmd,review_sha256=review['review_sha256'])
    assert service.read_material_publication(db,**args)==dict(recorded=False,retry_allowed=False,projection_published=False,start_ready=False)
    result=apply(db,login,cmd,review);after=facts(db)
    assert {k for k in after if after[k]!=before[k]}=={'external_objects','external_object_versions','materials',
        'material_inventory_policies','material_projection_publications','material_projection_lines','audit_events','audit_chain_heads'}
    assert result['projection_published'] and not result['full_catalog_verified'] and not result['start_ready']
    assert apply(db,login,cmd,review)==result and service.read_material_publication(db,**args)==result and facts(db)==after
    page=list_active_materials(db,actor=world.actor,limit=100)
    item=page.items[0]
    assert page.schema_version=='2.0' and item.sku_code=='SKU-1' and item.source_updated_at is None
    assert db.scalar(sa.select(MaterialInventoryPolicy)).tracking_mode=='none'
    pub=db.get(Publication,UUID(result['publication_id']))
    assert login['access_token'] not in str(pub.payload_jsonb)


def test_three_versions_a_b_a_preserve_material_identity_and_original_payload(db,world,login):
    grant,first=prepare(db,world,login);review=service.preview_material_publication(db,**login,command=first)
    initial=apply(db,login,first,review);first_pub=db.get(Publication,UUID(initial['publication_id']))
    line=db.scalar(sa.select(Line));old_payload=deepcopy(db.get(ExternalObjectVersion,line.version_id).payload_jsonb)
    material_id=line.material_id;policy_id=line.policy_id;original_id=line.version_id
    for name in ('Changed name','Synthetic'):
        receipt=capture_receipt(db,[dict(materialCode='SKU-1',materialName=name,unitCode='EA',unitName='piece',materialStatus=0)])
        apply(db,login,command(db,world,receipt))
    assert db.scalar(sa.select(sa.func.count()).select_from(FormalMaterial))==1
    versions=list(db.scalars(sa.select(ExternalObjectVersion).order_by(ExternalObjectVersion.valid_from)))
    assert len(versions)==3 and [v.is_current for v in versions]==[False,False,True]
    assert versions[0].valid_to==versions[1].valid_from and versions[1].valid_to==versions[2].valid_from
    assert versions[0].id==original_id and versions[0].payload_jsonb==old_payload
    assert db.scalar(sa.select(FormalMaterial)).id==material_id and db.scalar(sa.select(MaterialInventoryPolicy)).id==policy_id
    assert len({v.source_version for v in versions})==3 and all(v.source_updated_at is None for v in versions)
    assert service.read_material_publication(db,**login,command=first,review_sha256=review['review_sha256'])==initial
    assert service._prove(db,first_pub)==first_pub
    revoke(db,world,login,grant)
    assert service.read_material_publication(db,**login,command=first,review_sha256=review['review_sha256'])==initial


@pytest.mark.parametrize('change',['status','sn_flag','raw_type','missing','extra','duplicate'])
def test_semantics_pin_exact_raw_typed_rows_and_complete_capture(db,world,login,change):
    _,cmd=prepare(db,world,login);values=cmd.model_dump();rows=values['decisions']
    if change in ('status','sn_flag','raw_type'):
        raw=deepcopy(db.get(service.Receipt,cmd.receipt_id).payload_jsonb['capture']['records'][0]['data'])
        if change=='status':raw['materialStatus']=1
        elif change=='sn_flag':raw['isSnEnable']=False
        else:raw['materialStatus']='0'
        rows[0]['raw_sha256']=service.digest(raw)
    elif change=='missing':values['decisions']=[]
    elif change=='extra':values['decisions']=list(rows)+[dict(rows[0],sku_code='SKU-2')]
    else:values['decisions']=list(rows)+[rows[0]]
    before=facts(db)
    with pytest.raises((ValueError,service.MaterialProjectionError)):
        apply(db,login,service.MaterialPublicationCommand.model_validate(values))
    assert facts(db)==before


@pytest.mark.parametrize('change',['file','source','binding','grant','session','authorization'])
def test_current_evidence_and_authorization_rechecked_before_writing(db,world,login,change):
    grant,cmd=prepare(db,world,login);review=service.preview_material_publication(db,**login,command=cmd)
    if change=='file':db.get(FileObject,world.file).status='quarantined'
    elif change=='source':db.get(SourceSystem,world.source).enabled=False
    elif change=='binding':db.get(Binding,world.binding).revoked_at=authority._now(db)
    elif change=='grant':revoke(db,world,login,grant)
    elif change=='session':db.scalar(sa.select(AuthSession)).revoked_at=authority._now(db)
    else:db.get(User,world.actor.user_id).authorization_version+=1
    db.commit();before=facts(db)
    with pytest.raises((service.MaterialProjectionError,source.MaterialSourceAuthorityError,
                        service.configuration.ControlConfigurationError,authority.ControlAuthorityError)):
        apply(db,login,cmd,review)
    assert facts(db)==before


@pytest.mark.parametrize('change',['unit','serial','fraction'])
def test_current_unit_and_tracking_policy_require_separate_migration(db,world,login,change):
    _,cmd=prepare(db,world,login);apply(db,login,cmd)
    newer=command(db,world,capture_receipt(db));values=newer.model_dump()
    if change=='unit':values['decisions'][0]['base_unit']='EA'
    elif change=='serial':values['decisions'][0]['tracking_mode']='serial'
    else:values['decisions'][0].update(quantity_scale=3,allow_fraction=True)
    before=facts(db)
    with pytest.raises(service.MaterialProjectionError,match='unit_or_policy_migration_required'):
        apply(db,login,service.MaterialPublicationCommand.model_validate(values))
    assert facts(db)==before


def test_older_capture_cannot_replace_newer_version_and_absence_does_not_delete(db,world,login):
    _,old=prepare(db,world,login)
    newer=command(db,world,capture_receipt(db))
    apply(db,login,newer);before=facts(db)
    with pytest.raises(service.MaterialProjectionError,match='stale_or_overlapping_capture'):apply(db,login,old)
    assert facts(db)==before
    different=capture_receipt(db,[dict(materialCode='SKU-2',materialName='Second',unitCode='EA',unitName='piece',materialStatus=0)])
    apply(db,login,command(db,world,different))
    assert set(db.scalars(sa.select(FormalMaterial.sku_code)))=={'SKU-1','SKU-2'}
    assert set(db.scalars(sa.select(FormalMaterial.status)))=={'active'}


def test_any_existing_unmanaged_sku_blocks_entire_batch(db,world,login):
    rows=[dict(materialCode=code,materialName=code,unitCode='EA',unitName='piece',materialStatus=0) for code in ('SKU-1','SKU-2')]
    _,cmd=prepare(db,world,login,rows)
    now=authority._now(db)
    obj=ExternalObject(source_system_id=world.source,entity_type='material',external_id='SKU-2',created_at=now,updated_at=now)
    db.add(obj);db.commit();before=facts(db)
    with pytest.raises(service.MaterialProjectionError,match='unmanaged_identity_conflict'):apply(db,login,cmd)
    assert facts(db)==before


@pytest.mark.parametrize('fault',['after_audit','expiry','rollback','line'])
def test_failure_after_writes_rolls_back_all_materials_versions_policies_and_audit(db,world,login,monkeypatch,fault):
    _,cmd=prepare(db,world,login);review=service.preview_material_publication(db,**login,command=cmd);before=facts(db)
    original=service.append_audit_event
    if fault in ('after_audit','expiry'):
        def changed(*args,**kwargs):
            event=original(*args,**kwargs)
            if fault=='after_audit':raise RuntimeError('synthetic post-audit failure')
            now=authority._now(db)+timedelta(minutes=46)
            monkeypatch.setattr(authority,'_now',lambda db:now)
            return event
        monkeypatch.setattr(service,'append_audit_event',changed)
    elif fault=='line':
        monkeypatch.setattr(service,'_proof_line',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('synthetic line failure')))
    if fault=='rollback':
        service.execute_material_publication(db,**login,command=cmd,review_sha256=review['review_sha256']);db.rollback()
    else:
        with pytest.raises(RuntimeError):service.execute_material_publication(db,**login,command=cmd,review_sha256=review['review_sha256'])
    assert facts(db)==before


@pytest.mark.parametrize('change',['name','version','policy','current_pointer'])
def test_recovery_rejects_current_graph_drift(db,world,login,change):
    _,cmd=prepare(db,world,login);review=service.preview_material_publication(db,**login,command=cmd);apply(db,login,cmd,review)
    if change=='name':db.scalar(sa.select(FormalMaterial)).name='Tampered'
    elif change=='version':db.scalar(sa.select(ExternalObjectVersion)).payload_sha256='b'*64
    elif change=='policy':db.scalar(sa.select(MaterialInventoryPolicy)).tracking_mode='serial'
    else:db.scalar(sa.select(ExternalObject)).current_version_id=uuid4()
    db.commit();before=facts(db)
    with pytest.raises(service.MaterialProjectionError):service.read_material_publication(db,**login,command=cmd,review_sha256=review['review_sha256'])
    assert facts(db)==before


@pytest.mark.parametrize('lost_ack',[False,True])
def test_owner_cli_publish_and_recover_exact_commit_result(db,world,login,tmp_path,monkeypatch,capsys,lost_ack):
    import importlib.util,json,os
    path=PATH.parents[3]/'scripts/configure_inventory_control.py'
    spec=importlib.util.spec_from_file_location('material_publication_cli_test',path)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    _,cmd=prepare(db,world,login);db.commit()
    engine=db.get_bind();monkeypatch.setattr(sa,'create_engine',lambda *a,**kw:engine);monkeypatch.setattr(engine,'dispose',lambda:None)
    monkeypatch.setattr(cli,'_connection_config',lambda:('synthetic-dsn','synthetic-db'))
    monkeypatch.setattr(cli,'_database_preflight',lambda db,target:None)
    path=tmp_path/'publication.json'
    document=dict(command=authority._json(cmd.model_dump()),expected_authorization_version=login['expected_authorization_version'])
    def run(mode,expected=0):
        reader,writer=os.pipe();os.write(writer,login['access_token'].encode());os.close(writer)
        try:code=cli.main(['--material-publication-file',str(path),'--mode',mode,'--access-token-fd',str(reader)])
        finally:os.close(reader)
        out=capsys.readouterr();assert code==expected and login['access_token'] not in out.out+out.err
        if expected:
            assert not out.out;return json.loads(out.err)
        assert not out.err;return json.loads(out.out)
    path.write_text(json.dumps(document));review=run('preview')
    document['review_sha256']=review['review_sha256'];path.write_text(json.dumps(document))
    if lost_ack:
        original=sa.orm.Session.commit;attempts=[]
        def lose(session):attempts.append(1);original(session);raise RuntimeError('synthetic lost commit acknowledgement')
        with monkeypatch.context() as patch:
            patch.setattr(sa.orm.Session,'commit',lose);unknown=run('apply',3)
        assert attempts==[1] and unknown['next_action']=='read_exact_status'
    else:assert run('apply')['recorded']
    recovered=run('status')
    assert recovered['recorded'] and recovered['projection_published']
    assert db.scalar(sa.select(sa.func.count()).select_from(Publication))==1


def test_inactive_status_and_serial_policy_require_explicit_review(db,world,login):
    rows=[dict(materialCode='SKU-SN',materialName='Serial item',unitCode='EA',unitName='piece',materialStatus='unverified-inactive',isSnEnable='unverified-sn')]
    _,cmd=prepare(db,world,login,rows);values=cmd.model_dump()
    values['decisions'][0].update(status='inactive',tracking_mode='serial')
    apply(db,login,service.MaterialPublicationCommand.model_validate(values))
    assert db.scalar(sa.select(FormalMaterial.status))=='inactive'
    assert db.scalar(sa.select(MaterialInventoryPolicy.tracking_mode))=='serial'
    assert db.scalar(sa.select(ExternalObjectVersion)).payload_jsonb['raw']==rows[0]


@pytest.mark.parametrize('field,value',[('quantity_scale',True),('quantity_scale','1'),('allow_fraction',0),
    ('base_unit',' '),('base_unit','EA\n'),('quantity_scale',4)])
def test_semantic_decisions_do_not_coerce_ambiguous_policy_values(db,world,login,field,value):
    _,cmd=prepare(db,world,login);values=cmd.model_dump();values['decisions'][0][field]=value
    with pytest.raises(ValueError):service.MaterialPublicationCommand.model_validate(values)
