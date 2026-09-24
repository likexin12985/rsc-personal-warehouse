"""Actual file service, source authority and forward migration boundaries."""
from datetime import timedelta
from io import StringIO
import hashlib
from pathlib import Path
import runpy
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import inventory_control_authority as authority
from app.formal_access import FormalAccessError, load_formal_principal
from app.formal_file_schemas import FileUploadIntentIn
from app.formal_services import formal_files
from app.foundation_models import FileObject, Organization, Person, Role, RolePermission
from source_configuration_file_fixtures import source_evidence
from test_inventory_control_authority import db, world, prepared, command, decide
from test_formal_access import make_organization, make_user, assign

PATH = Path(__file__).parents[1]/'alembic/versions/20261030_0120_source_configuration_files.py'


def install(db):
    migration = runpy.run_path(str(PATH))
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['files'](False)['_create_sqlite_triggers']()
        migration['upgrade']()
    db.commit()
    return migration


def test_real_upload_completion_decision_and_private_download(db, world):
    install(db)
    file, storage = source_evidence(db, world.actor.user_id, complete=False)
    assert not formal_files.is_available_formal_file_for_purpose(file, purpose='source_configuration_evidence')
    with pytest.raises(authority.ControlAuthorityError, match='evidence_file_unavailable'):
        authority._file(db, file.id, file.sha256)
    storage.materialize(file)
    formal_files.complete_file_upload(db, actor=world.actor, file_id=file.id,
        trace_request_id=uuid4().hex, storage=storage)
    assert authority._file(db, file.id, file.sha256)['file_id'] == str(file.id)
    current = world.file
    world.file = file.id
    result = decide(db, world)
    assert result['decision_id']
    downloaded = formal_files.create_file_download_intent(db, actor=world.actor, file_id=file.id,
        trace_request_id=uuid4().hex, storage=storage, download_ttl_seconds=60)
    assert downloaded.purpose == 'source_configuration_evidence'
    other, _ = make_user(db, make_organization(db, name='Other synthetic HQ'), name='Unrelated operator')
    assign(db, other, db.scalar(select(Role).where(Role.code == 'admin')), scope_type='national', scope_id='*')
    db.commit()
    with pytest.raises(formal_files.FormalFileError):
        formal_files.create_file_download_intent(db, actor=load_formal_principal(db, other.id), file_id=file.id,
            trace_request_id=uuid4().hex, storage=storage, download_ttl_seconds=60)
    assert len(storage.download_calls) == 1
    world.file = current


@pytest.mark.parametrize('change', ['region', 'permission'])
def test_upload_requires_current_headquarters_source_authority(db, world, change):
    if change == 'region':
        person = db.get(Person, world.actor.person_id)
        db.get(Organization, person.organization_id).org_type = 'region_company'
    else:
        for row in db.scalars(select(RolePermission)):
            row.effect = 'deny'
    db.commit()
    before = db.scalar(text('SELECT count(*) FROM files'))
    with pytest.raises((formal_files.FormalFileError, FormalAccessError)):
        source_evidence(db, world.actor.user_id)
    assert db.scalar(text('SELECT count(*) FROM files')) == before


@pytest.mark.parametrize('change', ['legacy', 'provider', 'completion', 'future'])
def test_source_review_rejects_legacy_or_damaged_completion(db, world, change):
    file = db.get(FileObject, world.file)
    if change == 'legacy':
        file.metadata_jsonb = {}
    elif change == 'provider':
        file.metadata_jsonb = {**file.metadata_jsonb, 'provider': 'unverified-provider'}
    elif change == 'completion':
        file.metadata_jsonb = {k:v for k,v in file.metadata_jsonb.items() if k != 'completion'}
    else:
        file.metadata_jsonb = {**file.metadata_jsonb, 'completion': {**file.metadata_jsonb['completion'],
            'verified_at': (authority._now(db)+timedelta(days=1)).isoformat()}}
    db.commit()
    with pytest.raises(authority.ControlAuthorityError, match='evidence_file_unavailable'):
        decide(db, world)


def test_file_guard_rejects_direct_available_insert_and_retains_evidence(db, world):
    migration = install(db)
    before = db.scalar(text('SELECT count(*) FROM files'))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(FileObject(storage_key='invalid/'+uuid4().hex, sha256='a'*64, size_bytes=100,
                          mime_type='application/pdf', uploaded_by=world.actor.user_id, status='available'))
        db.flush()
    assert db.scalar(text('SELECT count(*) FROM files')) == before
    with Operations.context(MigrationContext.configure(db.connection())), pytest.raises(RuntimeError, match='0120 downgrade blocked'):
        migration['downgrade']()
    assert db.scalar(text('SELECT count(*) FROM files')) == before


def test_forward_migration_blocks_existing_invalid_review_without_mutation(db, world):
    decide(db, world)
    db.get(FileObject, world.file).metadata_jsonb = {}
    db.commit()
    migration = runpy.run_path(str(PATH))
    before = tuple(db.execute(text('SELECT id,payload_sha256 FROM inventory_control_authority_decisions')))
    with Operations.context(MigrationContext.configure(db.connection())), pytest.raises(RuntimeError, match='0120 upgrade blocked'):
        migration['upgrade']()
    assert tuple(db.execute(text('SELECT id,payload_sha256 FROM inventory_control_authority_decisions'))) == before


def test_api_schema_admits_explicit_source_evidence_purpose():
    value = FileUploadIntentIn(purpose='source_configuration_evidence', original_filename='review.pdf',
        size_bytes=100, mime_type='application/pdf', sha256='a'*64)
    assert value.purpose == 'source_configuration_evidence'


def test_postgresql_forward_sql_function_acl_and_manifest_agree():
    from app import database_security as security, oam_sync_scope_security as scope
    parser = pytest.importorskip('pglast.parser')
    m = runpy.run_path(str(PATH))
    coordinate = (m['FUNCTION'], '')
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate] == m['FUNCTION_HASH']
    assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS
    assert coordinate not in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    new = runpy.run_path(str(PATH.parent/'20261112_0133_daily_review_evidence.py'))
    assert new['HASHES']['public.rsc_guard_formal_file_object_0036()'][0] == m['FILE_HASHES'][1]
    report_file = runpy.run_path(str(PATH.parent/'20261116_0137_report_export_file_purpose.py'))
    assert report_file['OLD_FILE_HASH'] == new['HASHES']['public.rsc_guard_formal_file_object_0036()'][1]
    opening_source = runpy.run_path(str(PATH.parent/'20261119_0140_opening_count_source_purpose.py'))
    assert opening_source['OLD_FILE_HASH'] == report_file['NEW_FILE_HASH']
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[('rsc_guard_formal_file_object_0036','')] == opening_source['NEW_FILE_HASH']
    assert m['NEW_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0120['rsc_oam_runtime_binding_ready_0044()'][6]
    for name,table in m['TRIGGERS'].items():
        assert len(name) <= 63
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table,m['FUNCTION'],'A',7,False,False,False)
    for body in (m['BODY'], m['source_changes']()[1]):
        parser.parse_plpgsql_json('CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $body$'+body+'$body$')
    for direction in ('upgrade','downgrade'):
        output = StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
            m[direction]()
        sql = output.getvalue()
        parser.parse_sql(sql)
        assert sql.index('LOCK TABLE public.alembic_version') < sql.index('source evidence')
        if direction == 'upgrade':
            assert sql.count('ENABLE ALWAYS TRIGGER') == 4
            assert 'TO star_oam_api' not in sql
        else:
            assert sql.index('review facts must be retained') < sql.index('DROP TRIGGER')
