"""Synthetic storage, real upload intent/completion and audit service calls."""
from uuid import uuid4

from app.formal_access import load_formal_principal
from app.formal_services import formal_files
from app.foundation_models import FileObject
from test_formal_files_service import FakeStorage, SECRET


def source_evidence(db, user_id, *, complete=True):
    actor = load_formal_principal(db, user_id)
    storage = FakeStorage()
    intent = formal_files.create_file_upload_intent(
        db, actor=actor, command=formal_files.FileUploadIntentInput(
            purpose='source_configuration_evidence', original_filename='synthetic-source-review.pdf',
            sha256='a'*64, size_bytes=100, mime_type='application/pdf'),
        idempotency_key=uuid4().hex, idempotency_hmac_secret=SECRET,
        trace_request_id=uuid4().hex, storage=storage, upload_ttl_seconds=60)
    row = db.get(FileObject, intent.file_id)
    if complete:
        storage.materialize(row)
        formal_files.complete_file_upload(db, actor=actor, file_id=row.id,
            trace_request_id=uuid4().hex, storage=storage)
    return row, storage
