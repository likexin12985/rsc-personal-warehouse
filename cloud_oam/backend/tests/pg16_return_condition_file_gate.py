"""Real API-role file lifecycle on owned PG16; fake object store only.

Runs within the outer authority gate transaction. Candidate SQL and all files,
audit and grants roll back. SET CONSTRAINTS exercises the deferred upload
authority guard; this is not full correction COMMIT or real OSS acceptance.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import runpy
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.foundation_models import FileObject, Permission, Person, RoleAssignment, RolePermission
from app.models import User
from app.formal_services import formal_files
from app.formal_services.stock_loss_corrections import return_condition_evidence as subject
from formal_file_integrity import FileUploadIntentInput, FormalFileError
from migration_script_cache import cache_migration_compilation
from test_formal_files_service import FakeStorage, SECRET


def run(db, connection, *, actor, submit_link_id, directory):
    folder = Path(__file__).resolve().parents[1] / 'alembic'
    with cache_migration_compilation(folder / 'versions'):
        candidate = runpy.run_path(str(folder / 'return_condition_candidate/files.py'))
    body = connection.scalar(text("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:signature)"),
        dict(signature=candidate['SIGNATURE']))
    assert body == candidate['EXPECTED_BODY']
    assert hashlib.sha256(body.encode()).hexdigest() == candidate['EXPECTED_SHA256']
    statements = candidate['statements']()
    compiled = '\n\n'.join(s.rstrip(';')+';' for s in statements)+'\n'
    (Path(directory)/'condition-file-candidate.sql').write_text(compiled)
    for statement in statements: db.execute(text(statement))
    assert connection.scalar(text("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:signature)"),
        dict(signature=candidate['SIGNATURE'])) == candidate['BODY']
    for function in ('rsc_condition_file_authority(text,bigint,uuid)', 'rsc_condition_file_commit()'):
        for role in ('star_oam_api','star_oam_projector','star_oam_edge','edge_inbox','star_oam_backup'):
            assert not db.scalar(text('SELECT has_function_privilege(:role,:function,\'EXECUTE\')'),
                dict(role=role,function='public.'+function))
    assert db.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname='condition_file_authority_commit' "
        "AND tgenabled='A' AND tgdeferrable AND tginitdeferred"))==1
    storage = FakeStorage()

    def api(function):
        db.flush(); db.execute(text('SET LOCAL ROLE star_oam_api'))
        try: return function()
        finally: db.execute(text('SET LOCAL ROLE star_oam_migrator'))

    def create(key):
        return formal_files.create_file_upload_intent(db,actor=actor,
            command=FileUploadIntentInput(purpose=subject.PURPOSE,original_filename='成色核对.jpg',
                size_bytes=128,mime_type='image/jpeg',sha256='a'*64),
            idempotency_key=key,idempotency_hmac_secret=SECRET,trace_request_id=uuid4().hex,
            storage=storage,upload_ttl_seconds=600)

    key=uuid4().hex
    initial=api(lambda:create(key)); row=db.get(FileObject,initial.file_id)
    assert row.status=='pending'
    assert api(lambda:create(key)).file_id==row.id
    storage.materialize(row)
    api(lambda:formal_files.complete_file_upload(db,actor=actor,file_id=row.id,
        trace_request_id=uuid4().hex,storage=storage))
    proof=subject.completed_evidence(row,uploader_user_id=actor.user_id,uploader_person_id=actor.person_id,
        authorization_version=actor.authorization_version,provider_code=storage.provider_code)
    assert row.status=='available' and api(lambda:create(key)).upload is None
    db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    db.execute(text('SET CONSTRAINTS ALL DEFERRED'))
    refused=[]
    for change in ('deny','action','expired','version','disabled','person'):
        nested=db.begin_nested()
        try:
            # Enqueue the deferred check before authority changes; this catches
            # a permission/version/identity change after a successful INSERT.
            pending=api(lambda:create(uuid4().hex))
            user=db.get(User,actor.user_id)
            link=db.get(RolePermission,submit_link_id)
            if change=='deny': link.effect='deny'
            elif change=='action':
                # submit_loss already exists in real old application history;
                # do not fail this authority test on permission uniqueness.
                unrelated='condition_unrelated_fixture'
                assert db.scalar(select(Permission.id).where(Permission.resource=='stock_operation',
                    Permission.action==unrelated,Permission.field_code=='')) is None
                db.get(Permission,link.permission_id).action=unrelated
            elif change=='expired':
                assignments=db.scalars(select(RoleAssignment).where(RoleAssignment.user_id==actor.user_id,
                    RoleAssignment.role_id==link.role_id)).all()
                for assignment in assignments:
                    assignment.valid_to=datetime.now(timezone.utc)-timedelta(seconds=1)
            elif change=='version': user.authorization_version+=1
            elif change=='disabled': user.is_active=False
            else:
                # An existing exact person is used only as a negative identity
                # mutation; the earlier uploader coordinate must still match.
                original_person=db.get(Person,actor.person_id)
                other=Person(id=uuid4(),organization_id=original_person.organization_id,
                    employee_no='COND-'+uuid4().hex[:16],name='Synthetic changed file uploader',employment_status='active')
                db.add(other); db.flush(); user.person_id=other.id
            db.flush()
            try:
                db.execute(text('SET CONSTRAINTS condition_file_authority_commit IMMEDIATE'))
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514'
                assert 'condition current file authority required' in str(error.orig)
                refused.append(change)
            else: raise AssertionError('deferred upload authority accepted '+change)
        finally:
            nested.rollback(); db.expire_all()
        assert api(lambda:create(key)).file_id==row.id
    # A completed object's immutable metadata remains valid after revocation;
    # it is historical proof, not a new upload or a new condition action.
    nested=db.begin_nested()
    try:
        db.get(RolePermission,submit_link_id).effect='deny'; db.flush()
        assert subject.completed_evidence(db.get(FileObject,proof.file_id,populate_existing=True),
            uploader_user_id=actor.user_id,uploader_person_id=actor.person_id,
            authorization_version=actor.authorization_version,provider_code=storage.provider_code)==proof
        calls=len(storage.upload_calls)
        try: api(lambda:create(uuid4().hex))
        except FormalFileError as error: assert error.code=='file_purpose_forbidden'
        else: raise AssertionError('revoked uploader accepted')
        assert len(storage.upload_calls)==calls
    finally: nested.rollback(); db.expire_all()
    return dict(passed=True,realApiRole=True,statements=len(statements),
        executedDdlSha256=hashlib.sha256(compiled.encode()).hexdigest(),
        originalFileGuardSha256=candidate['EXPECTED_SHA256'],candidateFileGuardSha256=candidate['BODY_SHA256'],
        completedEvidenceMetadataSha256=proof.metadata_sha256,deferredAuthorityRefused=refused,
        historicalProofAfterRevocation=True,fakeObjectStorage=True,actualCommitTested=False,
        correctionBindingTested=False,physicalEvidenceVerified=False,productionAcceptance=False)
