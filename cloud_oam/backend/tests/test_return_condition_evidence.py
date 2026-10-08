"""Internal file service against real authority rows; provider is local/fake.

SQLite tests establish service behavior, not the PG16 upload COMMIT fence or
condition-event binding. Public uploads are admitted; unbound downloads stay closed.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.formal_access import load_formal_principal
from app.formal_file_schemas import FileUploadIntentIn
from app.formal_services import formal_files
from app.formal_services.stock_loss_corrections import return_condition_evidence as subject
from app.foundation_models import AuditChainHead, FileObject, RolePermission
from formal_file_integrity import FileUploadIntentInput, FormalFileError
from test_formal_access import db, make_organization, make_user, make_role, make_permission, grant, assign
from test_formal_files_service import FakeStorage, SECRET


@pytest.fixture
def world(db, request):
    role_code, action = getattr(request, 'param', ('provincial_manager', 'submit_return_condition'))
    hq = make_organization(db, name='Condition evidence HQ')
    region = make_organization(db, name='Condition evidence region', parent=hq)
    # HQ affiliation with an explicitly granted region is intentionally valid.
    user, person = make_user(db, hq, name='Condition evidence uploader')
    role = make_role(db, role_code)
    permission = make_permission(db, 'stock_operation', action)
    grant(db, role, permission)
    scope_type, scope_id = ('national', '*') if role_code == 'admin' else (
        ('person', str(person.id)) if role_code == 'technician' else ('organization', str(region.id)))
    assignment = assign(db, user, role, scope_type=scope_type, scope_id=scope_id)
    db.add(AuditChainHead(id=uuid4(), stream_key='inventory', version=0)); db.commit()
    return SimpleNamespace(user=user, person=person, role=role, permission=permission,
        region=region, assignment=assignment, actor=load_formal_principal(db,user.id), storage=FakeStorage())


def create(db, world, key=None):
    return formal_files.create_file_upload_intent(db, actor=world.actor,
        command=FileUploadIntentInput(purpose=subject.PURPOSE, original_filename='成色核对.jpg',
            size_bytes=128, mime_type='image/jpeg', sha256='a'*64),
        idempotency_key=key or uuid4().hex, idempotency_hmac_secret=SECRET,
        trace_request_id=uuid4().hex, storage=world.storage, upload_ttl_seconds=600)


def finish(db, world, row):
    world.storage.materialize(row)
    formal_files.complete_file_upload(db, actor=world.actor, file_id=row.id,
        trace_request_id=uuid4().hex, storage=world.storage)


def proof(world, row, **changes):
    args=dict(uploader_user_id=world.actor.user_id, uploader_person_id=world.actor.person_id,
        authorization_version=world.actor.authorization_version,provider_code=world.storage.provider_code)
    return subject.completed_evidence(row, **(args | changes))


@pytest.mark.parametrize('world', [
    ('provincial_manager','submit_return_condition'),
    ('provincial_manager','supplement_return_condition'),
    ('provincial_manager','review_return_condition_regional'),
    ('admin','review_return_condition_headquarters'),
    ('admin','cancel_return_condition_approval'),
], indirect=True)
def test_dedicated_upload_completion_and_idempotency(db, world):
    key=uuid4().hex
    created=create(db, world, key); row=db.get(FileObject,created.file_id)
    with pytest.raises(FormalFileError): proof(world,row)
    assert create(db,world,key).file_id==row.id
    finish(db,world,row); db.commit()
    result=proof(world,row)
    assert result.file_id==row.id and result.content_sha256==row.sha256
    assert result.metadata_sha256==formal_files._canonical_hash(row.metadata_jsonb)
    assert row.storage_key.startswith('formal-files/v1/return_condition_evidence/')
    assert not formal_files.is_available_formal_file_for_purpose(row,purpose='stock_loss_evidence')
    assert create(db,world,key).upload is None
    assert db.scalar(select(func.count()).select_from(FileObject))==1
    # Even its uploader cannot bypass immutable event binding and current scope.
    with pytest.raises(FormalFileError) as failure:
        formal_files.create_file_download_intent(db,actor=world.actor,file_id=row.id,
            trace_request_id=uuid4().hex,storage=world.storage,download_ttl_seconds=60)
    assert failure.value.code=='file_purpose_forbidden' and not world.storage.download_calls


@pytest.mark.parametrize('world', [
    ('technician','submit_return_condition'),
    ('provincial_manager','submit_loss'),
    ('provincial_manager','review_return_condition_headquarters'),
    ('admin','submit_return_condition'),
    ('admin','finalize_loss'),
], indirect=True)
def test_unrelated_actions_or_roles_cannot_upload(db,world):
    with pytest.raises(FormalFileError): create(db,world)
    assert not world.storage.upload_calls


@pytest.mark.parametrize('change', ['deny','expired','revoked','version','disabled','left','region_inactive'])
def test_current_authority_change_rejects_completion_before_provider(db,world,change):
    row=db.get(FileObject,create(db,world).file_id); world.storage.materialize(row)
    if change=='deny': db.scalar(select(RolePermission)).effect='deny'
    elif change=='expired': world.assignment.valid_to=datetime.now(timezone.utc)-timedelta(seconds=1)
    elif change=='revoked':
        world.assignment.status='revoked'; world.assignment.revoked_by=world.user.id
        world.assignment.revoked_at=datetime.now(timezone.utc)
    elif change=='version': world.user.authorization_version+=1
    elif change=='disabled': world.user.is_active=False
    elif change=='left': world.person.employment_status='left'
    else: world.region.status='inactive'
    db.commit()
    with pytest.raises(FormalFileError):
        formal_files.complete_file_upload(db,actor=world.actor,file_id=row.id,
            trace_request_id=uuid4().hex,storage=world.storage)
    assert row.status=='pending' and not world.storage.head_calls


@pytest.mark.parametrize('changes', [
    {'uploader_user_id':'other'}, {'uploader_person_id':uuid4()},
    {'authorization_version':2}, {'authorization_version':True}, {'provider_code':'different'},
])
def test_completed_object_requires_exact_actor_and_provider(db,world,changes):
    row=db.get(FileObject,create(db,world).file_id); finish(db,world,row)
    with pytest.raises(FormalFileError): proof(world,row,**changes)


@pytest.mark.parametrize('change', ['completion','sha','storage_key','quarantine','purpose'])
def test_completed_proof_refuses_tampered_file(db,world,change):
    row=db.get(FileObject,create(db,world).file_id); finish(db,world,row)
    if change=='completion': row.metadata_jsonb={k:v for k,v in row.metadata_jsonb.items() if k!='completion'}
    elif change=='sha': row.sha256='b'*64
    elif change=='storage_key': row.storage_key=row.storage_key.replace('return_condition','stock_loss')
    elif change=='quarantine': row.status='quarantined'
    else: row.metadata_jsonb=dict(row.metadata_jsonb,purpose='stock_loss_evidence')
    with pytest.raises(FormalFileError): proof(world,row)


def test_historical_completed_proof_survives_later_uploader_revocation(db,world):
    row=db.get(FileObject,create(db,world).file_id); finish(db,world,row); db.commit()
    original=proof(world,row)
    world.user.is_active=False; world.user.authorization_version+=1; db.commit()
    assert proof(world,row)==original
    with pytest.raises(FormalFileError): create(db,world)


def test_storage_mismatch_does_not_make_pending_file_available(db,world):
    row=db.get(FileObject,create(db,world).file_id); world.storage.materialize(row)
    world.storage.objects[row.storage_key]=replace(world.storage.objects[row.storage_key],size_bytes=129)
    with pytest.raises(FormalFileError):
        formal_files.complete_file_upload(db,actor=world.actor,file_id=row.id,
            trace_request_id=uuid4().hex,storage=world.storage)
    assert row.status=='pending' and 'completion' not in row.metadata_jsonb


@pytest.mark.parametrize('change', ['future','before_created','after_event','naive_event'])
def test_completion_time_must_fit_exact_event_boundary(db,world,change):
    row=db.get(FileObject,create(db,world).file_id); finish(db,world,row)
    now=datetime.now(timezone.utc)
    if change=='future':
        row.metadata_jsonb=dict(row.metadata_jsonb,completion=dict(row.metadata_jsonb['completion'],
            verified_at=(now+timedelta(days=1)).isoformat()))
    elif change=='before_created':
        row.metadata_jsonb=dict(row.metadata_jsonb,completion=dict(row.metadata_jsonb['completion'],
            verified_at=(now-timedelta(days=1)).isoformat()))
    changes={'recorded_at':now-timedelta(days=1)} if change=='after_event' else (
        {'recorded_at':now.replace(tzinfo=None)} if change=='naive_event' else {})
    with pytest.raises(FormalFileError) as failure: proof(world,row,**changes)
    assert failure.value.code=='condition_evidence_time_invalid'


def test_public_condition_purpose_is_exact_and_unrelated_purposes_stay_closed():
    fields = dict(original_filename='证据.jpg', size_bytes=128, mime_type='image/jpeg', sha256='a'*64)
    assert FileUploadIntentIn(purpose=subject.PURPOSE, **fields).purpose == subject.PURPOSE
    with pytest.raises(ValidationError):
        FileUploadIntentIn(purpose='condition_evidence', **fields)
