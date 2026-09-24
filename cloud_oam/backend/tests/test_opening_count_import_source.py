"""A private source must be owned, current and completed before OSS is read."""

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from formal_file_integrity import (
    FILE_METADATA_SCHEMA, FileUploadIntentInput, _prepare_upload,
    _storage_key, _upload_request_hash,
)
from app.database import Base
from app.formal_access import Entitlement, FormalPrincipal
from app.formal_services import opening_count_import_prevalidation as prevalidation
from app.formal_services import opening_count_import_source as source_service
from app.foundation_models import FileObject


MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BODY = b"verified private xlsx bytes"


def _actor(*, user_id="uploader", version=1, allowed=True):
    assignment_id = uuid4()
    return FormalPrincipal(
        user_id=user_id, person_id=uuid4(), account_status="active",
        employment_status="active", authorization_version=version,
        access_mode="active", assignments=(),
        entitlements=(Entitlement(
            assignment_id=assignment_id, role_code="technician",
            scope_type="person", scope_id="unused", resource="stocktake",
            action="count", field_code="", effect="allow" if allowed else "deny",
        ),),
    )


def _completed_file(actor):
    file_id = uuid4()
    digest = sha256(BODY).hexdigest()
    key = _storage_key("opening_count_import", file_id)
    prepared = _prepare_upload(FileUploadIntentInput(
        purpose="opening_count_import", original_filename="期初盘点.xlsx",
        size_bytes=len(BODY), mime_type=MIME, sha256=digest,
    ), maximum_size_bytes=120 * 1024 * 1024)
    return FileObject(
        id=file_id, storage_key=key, sha256=digest, size_bytes=len(BODY),
        mime_type=MIME, original_filename="期初盘点.xlsx",
        uploaded_by=actor.user_id, status="available",
        metadata_jsonb={
            "authorization_version": actor.authorization_version,
            "file_id": str(file_id),
            "idempotency_key_hash": "a" * 64,
            "provider": "aliyun_oss_v2",
            "purpose": "opening_count_import",
            "request_sha256": _upload_request_hash(prepared),
            "schema": FILE_METADATA_SCHEMA,
            "storage_key": key,
            "uploader_person_id": str(actor.person_id),
            "uploader_user_id": actor.user_id,
            "completion": {
                "etag_sha256": "b" * 64,
                "head_manifest_sha256": "c" * 64,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )


class _Storage:
    provider_code = "aliyun_oss_v2"

    def __init__(self):
        self.calls = []

    def read_opening_count_source(self, **binding):
        self.calls.append(binding)
        return BODY


@pytest.fixture
def world(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[FileObject.__table__])
    actor = _actor()
    row = _completed_file(actor)
    monkeypatch.setattr(source_service, "lock_formal_principal_graph", lambda *_: None)
    monkeypatch.setattr(source_service, "load_formal_principal", lambda *_: actor)
    with Session(engine) as db:
        db.add(row)
        db.commit()
        yield db, actor, row, _Storage()
    engine.dispose()


def test_completed_owned_source_passes_exact_identity_to_private_reader(world):
    db, actor, row, storage = world
    result = source_service.read_authorized_opening_count_source(
        db, actor=actor, file_id=row.id, storage=storage,
    )
    assert result.file_id == row.id
    assert result.source_sha256 == sha256(BODY).hexdigest()
    assert result.data == BODY
    assert storage.calls == [{
        "storage_key": row.storage_key,
        "file_id": str(row.id),
        "sha256": row.sha256,
        "size_bytes": len(BODY),
    }]


@pytest.mark.parametrize("change,code", [
    ("other_uploader", "opening_import_source_unavailable"),
    ("pending", "opening_import_source_unavailable"),
    ("other_purpose", "opening_import_source_unavailable"),
    ("stale_version", "opening_import_source_binding_changed"),
    ("wrong_provider", "opening_import_source_binding_changed"),
])
def test_source_rejects_untrusted_binding_before_object_read(world, change, code):
    db, actor, row, storage = world
    if change == "other_uploader":
        row.uploaded_by = "another-user"
    elif change == "pending":
        row.status = "pending"
    elif change == "other_purpose":
        row.metadata_jsonb = {**row.metadata_jsonb, "purpose": "stocktake_evidence"}
    elif change == "stale_version":
        row.metadata_jsonb = {**row.metadata_jsonb, "authorization_version": 2}
    else:
        row.metadata_jsonb = {**row.metadata_jsonb, "provider": "other-provider"}
    db.flush()
    with pytest.raises(source_service.OpeningCountImportSourceError) as caught:
        source_service.read_authorized_opening_count_source(
            db, actor=actor, file_id=row.id, storage=storage,
        )
    assert caught.value.code == code
    assert storage.calls == []


def test_stale_principal_and_denied_permission_stop_before_object_read(world, monkeypatch):
    db, actor, row, storage = world
    monkeypatch.setattr(source_service, "load_formal_principal", lambda *_: replace(
        actor, authorization_version=2,
    ))
    with pytest.raises(source_service.OpeningCountImportSourceError) as caught:
        source_service.read_authorized_opening_count_source(
            db, actor=actor, file_id=row.id, storage=storage,
        )
    assert caught.value.code == "opening_import_source_actor_stale"
    monkeypatch.setattr(source_service, "load_formal_principal", lambda *_: replace(
        actor, entitlements=(),
    ))
    with pytest.raises(source_service.OpeningCountImportSourceError) as caught:
        source_service.read_authorized_opening_count_source(
            db, actor=actor, file_id=row.id, storage=storage,
        )
    assert caught.value.code == "opening_import_source_forbidden"
    assert storage.calls == []


def test_source_locks_release_before_business_prevalidation(world, monkeypatch):
    db, actor, row, storage = world
    events = []

    class _Transaction:
        def __init__(self, phase):
            self.phase = phase

        def __enter__(self):
            events.append(("begin", self.phase))
            return db

        def __exit__(self, *_):
            events.append(("end", self.phase))

    phases = iter(("source", "count"))

    def read_source(*args, **kwargs):
        events.append(("read", "source"))
        return source_service.AuthorizedOpeningCountSource(
            file_id=row.id, source_sha256=row.sha256,
            size_bytes=row.size_bytes, data=BODY,
        )

    def check_business(*args, **kwargs):
        events.append(("check", "count"))
        assert kwargs["data"] == BODY
        assert kwargs["expected_source_sha256"] == row.sha256
        return "preview"

    monkeypatch.setattr(prevalidation, "read_authorized_opening_count_source", read_source)
    monkeypatch.setattr(prevalidation, "prevalidate_opening_count_import_bytes", check_business)
    result = prevalidation.prevalidate_authorized_opening_count_import(
        lambda: _Transaction(next(phases)), actor=actor, storage=storage,
        file_id=row.id, task_id=uuid4(), round_id=uuid4(), scope_id=uuid4(),
        idempotency_key="idempotency", request_id="request",
    )
    assert result == "preview"
    assert events == [
        ("begin", "source"), ("read", "source"), ("end", "source"),
        ("begin", "count"), ("check", "count"), ("end", "count"),
    ]
