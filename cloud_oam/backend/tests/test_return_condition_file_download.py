"""Quantity/SN service history, real file route functions; SQLite and fake OSS.

This is not PostgreSQL HTTP or real object-storage verification.
"""
from uuid import UUID, uuid4

import pytest
from fastapi import Response
from sqlalchemy import select, update

from app.config import Settings
from app.formal_access import load_formal_principal
from app.foundation_models import FileObject, Permission, RolePermission
from app.models import User
from app.formal_file_schemas import FileUploadIntentIn
from app.formal_services import formal_files
from app.formal_services.stock_loss_corrections import return_condition_file_download as subject
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.routers import formal_files as api
from formal_file_integrity import FormalFileError
from test_formal_files_service import FakeStorage, SECRET
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready,
    parcel, acceptance, prepared, regional_opening, reader_tables, context, regional_source,
)


@pytest.mark.parametrize('stock,command_name', [('quantity', 'quantity_command'), ('serial', 'serial_command')], indirect=['stock'])
def test_file_download_public_routes_require_exact_history_and_current_read_scope(db, regional_source, request, command_name, monkeypatch):
    c = regional_source; command = request.getfixturevalue(command_name)
    storage = FakeStorage()
    settings = Settings(_env_file=None, file_storage_enabled=True, file_storage_provider='aliyun_oss_v2',
        file_storage_region='cn-shanghai', file_storage_bucket='synthetic-private',
        file_upload_intent_ttl_seconds=600, file_download_intent_ttl_seconds=60,
        file_idempotency_hmac_secret=SECRET)
    # Public upload/completion response contracts must accept this purpose too.
    response = Response()
    uploaded = api.create_formal_file_upload_intent(
        FileUploadIntentIn(purpose='return_condition_evidence', original_filename='核验.jpg',
            size_bytes=128, mime_type='image/jpeg', sha256='a'*64), response,
        c.actor, db, settings, storage, uuid4().hex, uuid4().hex)
    assert uploaded.purpose == 'return_condition_evidence' and uploaded.upload
    row = db.get(FileObject, uploaded.file_id); storage.materialize(row)
    complete = api.complete_formal_file_upload(row.id, Response(), c.actor, db, settings, storage, uuid4().hex)
    assert complete.purpose == uploaded.purpose and response.headers['cache-control'].startswith('no-store')
    # Even its uploader cannot obtain an unbound file.
    with pytest.raises(FormalFileError) as error:
        formal_files.create_file_download_intent(db, actor=c.actor, file_id=row.id,
            trace_request_id=uuid4().hex, storage=storage, download_ttl_seconds=60)
    assert error.value.code == 'file_purpose_forbidden' and not storage.download_calls
    db.rollback()
    # Bind that actual public upload in a real submission, preserving its original intent.
    from app.formal_services.stock_loss_corrections.return_condition_submission_source import inspect_submission_source
    proof = inspect_submission_source(db, actor=c.actor, inbound_line_id=c.line)
    command = type(command).model_validate(dict(command.model_dump(), evidence_file_ids=(row.id,), expected_source_hash=proof.evidence_hash))
    posted = writer.submit(db, actor=c.actor, request=command); db.commit()
    identifier = row.id
    reviewer = load_formal_principal(db, c.reviewer.user_id)
    before = snapshot(db)
    response = Response()
    result = api.create_formal_file_download_intent(identifier, response, reviewer, db, settings, storage, uuid4().hex)
    assert result.file_id == identifier and result.purpose == 'return_condition_evidence'
    assert result.download.method == 'GET' and result.download.url.startswith('https://')
    assert response.headers['cache-control'].startswith('no-store') and response.headers['referrer-policy'] == 'no-referrer'
    after = snapshot(db)
    assert {k for k in before if before[k] != after[k]} == {'audit_events', 'audit_chain_heads'}
    # A former uploader and lost write rights do not invalidate historical proof.
    db.get(User, c.actor.user_id).is_active = False
    grant = db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id == c.regional_role.id,
        Permission.resource == 'stock_operation', Permission.action == 'submit_return_condition'))
    grant.effect = 'deny'; db.commit()
    reviewer = load_formal_principal(db, c.reviewer.user_id)
    formal_files.create_file_download_intent(db, actor=reviewer, file_id=identifier,
        trace_request_id=uuid4().hex, storage=storage, download_ttl_seconds=60)
    db.rollback()
    baseline = snapshot(db); calls = len(storage.download_calls)
    events, files = subject._tables()
    for statement in (
        update(files).where(files.c.file_id == identifier).values(metadata_sha256='b'*64),
        update(events).where(events.c.id == UUID(posted['event_id'])).values(reason='tampered'),
    ):
        db.execute(statement)
        with pytest.raises(FormalFileError):
            formal_files.create_file_download_intent(db, actor=reviewer, file_id=identifier,
                trace_request_id=uuid4().hex, storage=storage, download_ttl_seconds=60)
        db.rollback(); assert snapshot(db) == baseline and len(storage.download_calls) == calls
    # Revocation after full history proof must still prevent URL creation.
    read_grant = db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id == c.regional_role.id,
        Permission.resource == 'stock_operation', Permission.action == 'read'))
    original_read = subject.history.read
    def revoke_after(*args, **kwargs):
        result = original_read(*args, **kwargs)
        db.execute(update(RolePermission).where(RolePermission.id == read_grant.id).values(effect='deny'))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(subject.history, 'read', revoke_after)
        with pytest.raises(FormalFileError) as error:
            formal_files.create_file_download_intent(db, actor=reviewer, file_id=identifier,
                trace_request_id=uuid4().hex, storage=storage, download_ttl_seconds=60)
        assert error.value.http_status_code == 403
    db.rollback(); assert snapshot(db) == baseline and len(storage.download_calls) == calls
