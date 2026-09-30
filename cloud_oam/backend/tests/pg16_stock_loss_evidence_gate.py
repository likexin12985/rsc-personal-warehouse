"""Real API-role loss-file authority on an owned, empty PostgreSQL 16 cluster.

Permissions and identities are synthetic local fixtures. Object storage is an
in-memory adapter; these checks never upload to OSS or activate production.
"""
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import formal_files
from app.foundation_models import AuthIdentity, FileObject, Permission, Role, RoleAssignment, RolePermission
from test_formal_access import make_organization, make_user, assign
from test_formal_files_service import FakeStorage, SECRET

PURPOSE = 'stock_loss_evidence'


def facts(engine):
    names = ('files','audit_events','audit_chain_heads','inventory_transactions',
             'stock_operation_orders','notification_events','outbox_events','state_transition_events')
    with engine.connect() as db:
        return {name: tuple(sorted((repr(dict(row)) for row in db.execute(
            text('SELECT * FROM '+name)).mappings()))) for name in names}


def upload(db, actor, storage, *, key=None):
    return formal_files.create_file_upload_intent(db, actor=actor,
        command=formal_files.FileUploadIntentInput(purpose=PURPOSE, original_filename='报损证据.jpg',
            size_bytes=128, mime_type='image/jpeg', sha256='a'*64),
        idempotency_key=key or uuid4().hex, idempotency_hmac_secret=SECRET,
        trace_request_id=uuid4().hex, storage=storage, upload_ttl_seconds=600)


def _raw_pending_copy(db, source):
    """A fully shaped candidate; exact authority denial must occur first."""
    identifier = uuid4()
    storage_key = f'formal-files/v1/{PURPOSE}/{identifier.hex[:2]}/{identifier.hex}'
    metadata = {k:v for k,v in source['metadata_jsonb'].items() if k!='completion'}
    metadata.update(file_id=str(identifier), storage_key=storage_key, idempotency_key_hash=uuid4().hex*2)
    db.execute(FileObject.__table__.insert().values(id=identifier, storage_key=storage_key,
        sha256=source['sha256'],size_bytes=source['size_bytes'],mime_type=source['mime_type'],
        original_filename=source['original_filename'],uploaded_by=source['uploaded_by'],
        status='pending',metadata_jsonb=metadata,created_at=datetime.now(timezone.utc)))


def run(engines, migrate):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    with owner.connect() as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert db.scalar(text('SELECT current_user'))=='star_oam_migrator'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        assert not db.scalar(text('SELECT EXISTS(SELECT 1 FROM users)'))
        assert not db.scalar(text("SELECT EXISTS(SELECT 1 FROM permissions WHERE resource='stock_operation' AND action IN ('submit_loss','review_loss_regional','finalize_loss','reverse_loss'))"))
    result = dict(passed=False, productionAcceptance=False, syntheticPermissionOnly=True)
    cases = [('technician','submit_loss'),('provincial_manager','review_loss_regional'),('admin','finalize_loss')]
    identities = []
    with Session(owner) as db:
        hq = make_organization(db, name='Synthetic evidence HQ')
        region = make_organization(db, name='Synthetic evidence region', parent=hq)
        roles = {r.code:r for r in db.scalars(select(Role))}
        for role_code, action in cases:
            user, person = make_user(db, hq if role_code=='admin' else region, name='Synthetic '+role_code)
            scope_type, scope_id = ('national','*') if role_code=='admin' else (
                ('organization',str(region.id)) if role_code=='provincial_manager' else ('person',str(person.id)))
            assignment = assign(db,user,roles[role_code],scope_type=scope_type,scope_id=scope_id)
            permission = Permission(resource='stock_operation',action=action,field_code='',description='Synthetic local loss evidence permission')
            db.add(permission);db.flush()
            binding = RolePermission(role_id=roles[role_code].id,permission_id=permission.id,effect='allow')
            db.add(binding);db.flush()
            identities.append(dict(user=user.id,assignment=assignment.id,grant=binding.id,action=action))
        db.commit()
    storage = FakeStorage()
    for identity in identities:
        key = uuid4().hex
        with Session(api) as db:
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            actor = load_formal_principal(db,identity['user'])
            created = upload(db,actor,storage,key=key)
            identifier = created.file_id
            db.commit()
        with Session(api) as db:
            actor = load_formal_principal(db,identity['user'])
            row = db.get(FileObject,identifier)
            assert row.status=='pending' and not formal_files.is_available_formal_file_for_purpose(row,purpose=PURPOSE)
            storage.materialize(row)
            formal_files.complete_file_upload(db,actor=actor,file_id=identifier,
                trace_request_id=uuid4().hex,storage=storage)
            db.commit()
        with Session(api) as db:
            actor = load_formal_principal(db,identity['user'])
            assert upload(db,actor,storage,key=key).upload is None
            row = db.get(FileObject,identifier)
            assert formal_files.is_available_formal_file_for_purpose(row,purpose=PURPOSE,uploader_user_id=actor.user_id)
            assert not formal_files.is_available_formal_file_for_purpose(row,purpose='stocktake_evidence')
            formal_files.create_file_download_intent(db,actor=actor,file_id=identifier,
                trace_request_id=uuid4().hex,storage=storage,download_ttl_seconds=60)
            db.commit()
        identity['file'] = identifier
    result['apiRoleUploadCompleteReplayDownload'] = len(identities)
    print('PG16 loss evidence: three real role upload/complete/replay/download chains PASS', flush=True)

    engineer = identities[0]
    with api.connect() as db:
        template = dict(db.execute(select(FileObject.__table__).where(FileObject.id==engineer['file'])).mappings().one())
        for signature in ('public.rsc_assert_stock_loss_file_authority_0143(text,bigint)',
                          'public.rsc_guard_stock_loss_file_commit_0143()'):
            assert not db.scalar(text('SELECT has_function_privilege(current_user,:signature,\'EXECUTE\')'),dict(signature=signature))
    # A fully formed SQL insert passes under the valid synthetic permission.
    # Roll it back: it is an adversarial SQL control, not a file-service event.
    with Session(api) as db:
        _raw_pending_copy(db,template)
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        db.rollback()
    result['rawSqlPositiveControl'] = True
    before = facts(owner)
    with Session(api) as db:
        with pytest.raises(formal_files.FormalFileError):
            formal_files.create_file_download_intent(db,actor=load_formal_principal(db,identities[2]['user']),
                file_id=engineer['file'],trace_request_id=uuid4().hex,storage=storage,download_ttl_seconds=60)
    assert facts(owner)==before
    result['unrelatedReviewerCannotDownloadUnboundEvidence'] = True

    for change in ('deny','expired','unverified_identity'):
        with Session(owner) as db:
            binding = db.get(RolePermission,engineer['grant'])
            assignment = db.get(RoleAssignment,engineer['assignment'])
            auth = db.scalar(select(AuthIdentity).where(AuthIdentity.user_id==engineer['user']))
            if change=='deny': binding.effect='deny'
            elif change=='expired': assignment.valid_to=datetime.now(timezone.utc)-timedelta(seconds=1)
            else:
                auth.status='revoked';auth.revoked_at=datetime.now(timezone.utc)
            db.commit()
        before = facts(owner)
        with Session(api) as db:
            with pytest.raises(DBAPIError,match='0143 current loss evidence authority required'):
                _raw_pending_copy(db,template)
            db.rollback()
        assert facts(owner)==before
        with Session(owner) as db:
            if change=='deny': db.get(RolePermission,engineer['grant']).effect='allow'
            elif change=='expired': db.get(RoleAssignment,engineer['assignment']).valid_to=None
            else:
                auth=db.scalar(select(AuthIdentity).where(AuthIdentity.user_id==engineer['user']))
                auth.status='active';auth.revoked_at=None
            db.commit()
    result['rawSqlAuthorityDenials'] = ['deny','expired','unverified_identity']

    # Current authority was valid at INSERT; expiry before COMMIT must roll
    # back both the file intent and its audit event/head, not only the file.
    with Session(owner) as db:
        deadline = db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=6)
        db.get(RoleAssignment,engineer['assignment']).valid_to=deadline
        db.commit()
    before = facts(owner)
    with Session(api) as db:
        upload(db,load_formal_principal(db,engineer['user']),storage)
        wall = db.scalar(text('SELECT clock_timestamp()'))
        assert wall < deadline, 'fixture expired before INSERT; no commit-only proof'
        time.sleep(max(0,(deadline-wall).total_seconds())+0.05)
        assert db.scalar(text('SELECT clock_timestamp()')) > deadline
        with pytest.raises(DBAPIError,match='0143 current loss evidence authority required'):
            db.commit()
        db.rollback()
    assert facts(owner)==before
    with Session(owner) as db:
        db.get(RoleAssignment,engineer['assignment']).valid_to=None
        db.commit()
    result['commitExpiryRollsBackFileAndAudit'] = True
    print('PG16 loss evidence: SQL deny/expiry/identity and commit-time full rollback PASS', flush=True)

    before = facts(owner)
    migrate('populated-loss-evidence-downgrade','downgrade','20261121_0142',
            expected_failure='0143 loss evidence requires retention')
    assert facts(owner)==before
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261206_0157'
        assert db.scalar(text('SELECT count(*) FROM inventory_transactions'))==0
        assert db.scalar(text('SELECT count(*) FROM stock_operation_orders'))==0
    result.update(passed=True, retainedEvidenceBlocksDowngrade=True, inventoryWrites=0,
        storageAdapter='in_memory_only', migrationHead='20261206_0157')
    return result
