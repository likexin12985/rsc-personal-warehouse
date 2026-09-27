"""Dedicated evidence contracts and current service authority; storage is local."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from app.formal_access import load_formal_principal
from app.formal_file_schemas import FileUploadIntentIn
from app.formal_services import formal_files
from app.foundation_models import AuditChainHead, FileObject, RolePermission
from formal_file_integrity import FileUploadIntentInput, FormalFileError, _prepare_upload
from test_formal_access import db, make_organization, make_user, make_role, make_permission, grant, assign
from test_formal_files_service import FakeStorage, SECRET

PURPOSE = 'stock_loss_evidence'


@pytest.fixture
def evidence_world(db, request):
    role_code, action = getattr(request, 'param', ('technician', 'submit_loss'))
    hq = make_organization(db, name='Loss evidence HQ')
    region = make_organization(db, name='Loss evidence region', parent=hq)
    actor_org = hq if role_code == 'admin' else region
    user, person = make_user(db, actor_org, name='Loss evidence uploader')
    role = make_role(db, role_code)
    permission = make_permission(db, 'stock_operation', action)
    grant(db, role, permission)
    scope_type, scope_id = ('national', '*') if role_code == 'admin' else (
        ('organization', str(region.id)) if role_code == 'provincial_manager' else ('person', str(person.id)))
    assignment = assign(db, user, role, scope_type=scope_type, scope_id=scope_id)
    db.add(AuditChainHead(id=uuid4(), stream_key='inventory', version=0))
    db.commit()
    return SimpleNamespace(user=user, person=person, role=role, permission=permission,
        assignment=assignment, actor=load_formal_principal(db, user.id), storage=FakeStorage())


def create(db, world, key=None):
    return formal_files.create_file_upload_intent(db, actor=world.actor,
        command=FileUploadIntentInput(purpose=PURPOSE, original_filename='报损照片.jpg',
            size_bytes=128, mime_type='image/jpeg', sha256='a'*64),
        idempotency_key=key or uuid4().hex, idempotency_hmac_secret=SECRET,
        trace_request_id=uuid4().hex, storage=world.storage, upload_ttl_seconds=600)


@pytest.mark.parametrize('evidence_world', [
    ('technician','submit_loss'), ('provincial_manager','submit_loss'),
    ('provincial_manager','review_loss_regional'), ('admin','finalize_loss'),
    ('admin','reverse_loss'),
], indirect=True)
def test_dedicated_upload_complete_replay_and_private_download(db, evidence_world):
    world = evidence_world
    key = uuid4().hex
    created = create(db, world, key)
    assert create(db, world, key).file_id == created.file_id
    row = db.get(FileObject, created.file_id)
    assert not formal_files.is_available_formal_file_for_purpose(row, purpose=PURPOSE)
    world.storage.materialize(row)
    formal_files.complete_file_upload(db, actor=world.actor, file_id=row.id,
        trace_request_id=uuid4().hex, storage=world.storage)
    db.commit()
    assert formal_files.is_available_formal_file_for_purpose(row, purpose=PURPOSE, uploader_user_id=world.user.id)
    assert not formal_files.is_available_formal_file_for_purpose(row, purpose='stocktake_evidence')
    assert create(db, world, key).upload is None
    result = formal_files.create_file_download_intent(db, actor=world.actor, file_id=row.id,
        trace_request_id=uuid4().hex, storage=world.storage, download_ttl_seconds=60)
    assert result.purpose == PURPOSE
    assert db.scalar(select(func.count()).select_from(FileObject)) == 1
    assert row.storage_key.startswith('formal-files/v1/stock_loss_evidence/')


@pytest.mark.parametrize('change', ['deny', 'expired', 'revoked', 'version', 'inactive', 'no_action'])
def test_current_authority_changes_refuse_without_storage_call(db, evidence_world, change):
    world = evidence_world
    if change == 'deny':
        db.scalar(select(RolePermission)).effect = 'deny'
    elif change == 'expired':
        world.assignment.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif change == 'revoked':
        world.assignment.revoked_at = datetime.now(timezone.utc)
        world.assignment.revoked_by = world.user.id
        world.assignment.status = 'revoked'
    elif change == 'version':
        world.user.authorization_version += 1
    elif change == 'inactive':
        world.person.employment_status = 'left'
    else:
        world.permission.action = 'read'
    db.commit()
    with pytest.raises(FormalFileError):
        create(db, world)
    assert not world.storage.upload_calls
    assert db.scalar(select(func.count()).select_from(FileObject)) == 0


def test_completion_rechecks_permission_before_storage_read(db, evidence_world):
    world = evidence_world
    row = db.get(FileObject, create(db, world).file_id)
    world.storage.materialize(row)
    db.scalar(select(RolePermission)).effect = 'deny'
    db.commit()
    with pytest.raises(FormalFileError):
        formal_files.complete_file_upload(db, actor=world.actor, file_id=row.id,
            trace_request_id=uuid4().hex, storage=world.storage)
    assert row.status == 'pending' and not world.storage.head_calls


@pytest.mark.parametrize('role,action', [('technician','finalize_loss'), ('provincial_manager','finalize_loss'), ('admin','review_loss_regional')])
def test_action_cannot_be_borrowed_from_an_ineligible_role(db, role, action):
    # Independent current identities, not role strings accepted from a client.
    hq = make_organization(db, name='HQ')
    region = make_organization(db, name='Region', parent=hq)
    user, person = make_user(db, hq if role=='admin' else region, name='Wrong loss action')
    stored_role = make_role(db, role)
    grant(db, stored_role, make_permission(db, 'stock_operation', action))
    scope_type, scope_id = ('national','*') if role=='admin' else (
        ('organization',str(region.id)) if role=='provincial_manager' else ('person',str(person.id)))
    assign(db, user, stored_role, scope_type=scope_type, scope_id=scope_id)
    db.commit()
    world = SimpleNamespace(actor=load_formal_principal(db,user.id),storage=FakeStorage())
    with pytest.raises(FormalFileError, match='不能上传报损审核证据'):
        create(db, world)
    assert not world.storage.upload_calls


def test_public_and_backup_contract_use_dedicated_non_xlsx_purpose():
    values = dict(purpose=PURPOSE, original_filename='照片.png', size_bytes=128,
                  mime_type='image/png', sha256='a'*64)
    assert FileUploadIntentIn(**values).purpose == PURPOSE
    assert _prepare_upload(FileUploadIntentInput(**values), maximum_size_bytes=1024).purpose == PURPOSE
    with pytest.raises(FormalFileError):
        _prepare_upload(FileUploadIntentInput(**(values | dict(original_filename='数据.xlsx',
            mime_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))), maximum_size_bytes=1024)
