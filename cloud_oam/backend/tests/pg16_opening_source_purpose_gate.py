"""Actual PG16 API-role admission checks for private opening-count sources."""

from hashlib import sha256
from dataclasses import replace
from uuid import uuid4

from formal_file_integrity import (
    FILE_METADATA_SCHEMA, FileUploadIntentInput, _PreparedUpload,
    _prepare_upload, _storage_key, _upload_request_hash,
)
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import FileObject, Role
from app.formal_access import load_formal_principal
from app.formal_services.opening_count_import_source import (
    OpeningCountImportSourceError, read_authorized_opening_count_source,
)
from test_formal_access import assign, make_organization, make_user


MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PURPOSE = 'opening_count_import'


def _file(*, user_id, person_id, size_bytes=1024, prepared=None):
    file_id = uuid4()
    key = _storage_key(PURPOSE, file_id)
    digest = sha256(str(file_id).encode()).hexdigest()
    prepared = prepared or _prepare_upload(FileUploadIntentInput(
        purpose=PURPOSE, original_filename='期初盘点.xlsx', size_bytes=size_bytes,
        mime_type=MIME, sha256=digest,
    ), maximum_size_bytes=120 * 1024 * 1024)
    metadata = {
        'authorization_version': 1,
        'file_id': str(file_id),
        'idempotency_key_hash': sha256(('upload:' + str(file_id)).encode()).hexdigest(),
        'provider': 'aliyun_oss_v2',
        'purpose': PURPOSE,
        'request_sha256': _upload_request_hash(prepared),
        'schema': FILE_METADATA_SCHEMA,
        'storage_key': key,
        'uploader_person_id': str(person_id),
        'uploader_user_id': user_id,
    }
    return FileObject(
        id=file_id, storage_key=key, sha256=digest, size_bytes=size_bytes,
        mime_type=MIME, original_filename='期初盘点.xlsx', uploaded_by=user_id,
        status='pending', metadata_jsonb=metadata,
    )


def _expect_rejected(engine, row, *, sqlstates=('23514',)):
    with Session(engine) as db:
        db.add(row)
        try:
            db.flush()
        except DBAPIError as exc:
            assert exc.orig.sqlstate in sqlstates, exc.orig.sqlstate
            db.rollback()
        else:
            raise AssertionError('API role admitted an invalid opening-count source')


def assert_opening_source_runtime_gate(owner_engine, api_engine) -> dict[str, bool]:
    with Session(owner_engine) as db:
        region = make_organization(db, name='Synthetic import-source region')
        counter, person = make_user(db, region, name='Synthetic import-source counter')
        no_role, no_role_person = make_user(db, region, name='Synthetic import-source no-role')
        role = db.scalar(select(Role).where(Role.code == 'technician'))
        assert role is not None and person is not None and no_role_person is not None
        assign(db, counter, role, scope_type='person', scope_id=str(person.id))
        db.commit()
        counter_id, person_id = counter.id, person.id
        no_role_id, no_role_person_id = no_role.id, no_role_person.id

    with Session(api_engine) as db:
        valid = _file(user_id=counter_id, person_id=person_id)
        db.add(valid)
        db.commit()
        assert db.get(FileObject, valid.id) is not None

    class PrivateSource:
        provider_code = 'aliyun_oss_v2'
        calls = []

        def read_opening_count_source(self, **binding):
            self.calls.append(binding)
            return b'synthetic private source'

    private_source = PrivateSource()
    with Session(api_engine) as db:
        row = db.get(FileObject, valid.id)
        verified_at = db.scalar(select(func.current_timestamp()))
        row.metadata_jsonb = {
            **row.metadata_jsonb,
            'completion': {
                'etag_sha256': 'a' * 64,
                'head_manifest_sha256': 'b' * 64,
                'verified_at': verified_at.isoformat(),
            },
        }
        row.status = 'available'
        db.commit()
    with Session(api_engine) as db:
        actor = load_formal_principal(db, counter_id)
        source = read_authorized_opening_count_source(
            db, actor=actor, file_id=valid.id, storage=private_source,
        )
        assert source.file_id == valid.id
        assert source.source_sha256 == valid.sha256
        assert len(private_source.calls) == 1
        assert private_source.calls[0] == {
            'storage_key': valid.storage_key,
            'file_id': str(valid.id),
            'sha256': valid.sha256,
            'size_bytes': valid.size_bytes,
        }
    with Session(api_engine) as db:
        unauthorized = replace(actor, user_id=no_role_id, person_id=no_role_person_id)
        try:
            read_authorized_opening_count_source(
                db, actor=unauthorized, file_id=valid.id, storage=private_source,
            )
        except OpeningCountImportSourceError as exc:
            assert exc.http_status_code == 403
        else:
            raise AssertionError('unassigned user read private opening source')
        assert len(private_source.calls) == 1

    _expect_rejected(api_engine, _file(user_id=no_role_id, person_id=no_role_person_id))
    oversized = 8 * 1024 * 1024 + 1
    forged_prepared = _PreparedUpload(
        purpose=PURPOSE, original_filename='期初盘点.xlsx', size_bytes=oversized,
        mime_type=MIME, sha256='a' * 64,
    )
    # The pure validator refuses this size; construct a plausible hash to
    # verify that the database guard independently refuses it as well.
    oversized_row = _file(
        user_id=counter_id, person_id=person_id,
        size_bytes=oversized, prepared=forged_prepared,
    )
    oversized_row.sha256 = forged_prepared.sha256
    # The existing generic file trigger may reject its invalid shape first
    # (P0001); the new 0140 guard independently carries the 8 MiB predicate.
    _expect_rejected(api_engine, oversized_row, sqlstates=('23514', 'P0001'))
    return {
        'authorizedSourceAccepted': True,
        'unassignedUploaderRejected': True,
        'oversizedSourceRejected': True,
        'authorizedPrivateReadAccepted': True,
        'unauthorizedPrivateReadRejected': True,
    }
