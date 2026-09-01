from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sys
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from pydantic import ValidationError

# Register every FK target before creating the isolated SQLite schema.
from app import demand_models, inventory_models, stocktake_models  # noqa: F401
from app.database import Base
from app.config import Settings, get_settings
from app.dependencies import get_formal_principal
from app.formal_access import Entitlement, FormalPrincipal, ScopeGrant
from app.formal_services import formal_files
from app.formal_services.file_storage import (
    AliyunOssV2StorageAdapter,
    DownloadIntent,
    FileStorageError,
    StoredObjectHead,
    UploadIntent,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    DocumentAttachment,
    FileObject,
    Organization,
    Person,
)
from app.models import User
from app.main import app
from app.routers.formal_files import get_formal_file_storage_adapter
from app.database import get_db


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
SECRET = "formal-file-idempotency-secret-at-least-32-chars"


class FakeStorage:
    provider_code = "aliyun_oss_v2"

    def __init__(self) -> None:
        self.upload_calls: list[dict[str, object]] = []
        self.head_calls: list[str] = []
        self.download_calls: list[dict[str, object]] = []
        self.objects: dict[str, StoredObjectHead] = {}
        self.fail_upload = False
        self.fail_head = False
        self.fail_download = False
        self.bad_upload_headers: dict[str, str] | None = None
        self.bad_download_key: str | None = None

    def create_upload_intent(self, **kwargs) -> UploadIntent:
        if self.fail_upload:
            raise FileStorageError("secret sdk detail")
        self.upload_calls.append(dict(kwargs))
        headers = self.bad_upload_headers or {
            "Content-Type": str(kwargs["mime_type"]),
            "x-oss-meta-sha256": str(kwargs["sha256"]),
            "x-oss-meta-file-id": str(kwargs["file_id"]),
            "x-oss-forbid-overwrite": "true",
        }
        return UploadIntent(
            storage_key=str(kwargs["storage_key"]),
            url="https://private-bucket.oss-cn-shanghai.aliyuncs.com/signed-put?x=1",
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=int(kwargs["ttl_seconds"])),
            headers=headers,
        )

    def head_object(self, *, storage_key: str) -> StoredObjectHead:
        if self.fail_head:
            raise FileStorageError("secret sdk detail")
        self.head_calls.append(storage_key)
        if storage_key not in self.objects:
            raise FileStorageError("object not found secret")
        return self.objects[storage_key]

    def create_download_intent(self, **kwargs) -> DownloadIntent:
        if self.fail_download:
            raise FileStorageError("secret sdk detail")
        self.download_calls.append(dict(kwargs))
        return DownloadIntent(
            storage_key=self.bad_download_key or str(kwargs["storage_key"]),
            url="https://private-bucket.oss-cn-shanghai.aliyuncs.com/signed-get?x=1",
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=int(kwargs["ttl_seconds"])),
        )

    def materialize(self, row: FileObject, *, etag: str = '"etag-1"') -> None:
        self.objects[row.storage_key] = StoredObjectHead(
            storage_key=row.storage_key,
            size_bytes=row.size_bytes,
            mime_type=row.mime_type,
            metadata={"sha256": row.sha256, "file-id": str(row.id)},
            etag=etag,
        )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, autoflush=False, expire_on_commit=False)
    org = Organization(
        id=uuid.uuid4(),
        code="R-FILE",
        name="文件测试区域",
        parent_id=None,
        org_type="region_company",
        province_code="310000",
        status="active",
    )
    db.add(org)
    db.flush()

    principals: dict[str, FormalPrincipal] = {}
    users: dict[str, User] = {}
    for index, label in enumerate(("owner", "other"), start=1):
        person = Person(
            id=uuid.uuid4(),
            organization_id=org.id,
            employee_no=f"FILE-{index}",
            name=label,
            mobile_encrypted=None,
            mobile_hash=None,
            employment_status="active",
            source_updated_at=NOW,
        )
        user = User(
            id=str(uuid.uuid4()),
            person_id=person.id,
            account_status="active",
            authorization_version=1,
            mobile=f"1380000000{index}",
            name=label,
            password_hash="disabled",
            role="technician",
            province=None,
            is_active=True,
            require_password_change=False,
        )
        assignment_id = uuid.uuid4()
        grant = ScopeGrant(
            assignment_id=assignment_id,
            role_code="technician",
            scope_type="person",
            scope_id=str(person.id),
            valid_from=NOW - timedelta(days=1),
            valid_to=None,
        )
        entitlements = tuple(
            Entitlement(
                assignment_id=assignment_id,
                role_code="technician",
                scope_type="person",
                scope_id=str(person.id),
                resource=resource,
                action=action,
                field_code=field_code,
                effect="allow",
            )
            for resource, action, field_code in (
                ("material_request", "create", ""),
                ("material_request", "register_external", "approval_evidence"),
                ("material_request", "read", ""),
                ("stocktake", "count", ""),
                ("stocktake", "read", ""),
            )
        )
        principal = FormalPrincipal(
            user_id=user.id,
            person_id=person.id,
            account_status="active",
            employment_status="active",
            authorization_version=1,
            access_mode="active",
            assignments=(grant,),
            entitlements=entitlements,
        )
        db.add_all((person, user))
        db.flush()
        principals[user.id] = principal
        users[label] = user
    db.add_all(
        (
            AuditChainHead(
                id=uuid.uuid4(),
                stream_key="material_request",
                last_event_id=None,
                last_hash=None,
                version=0,
            ),
            AuditChainHead(
                id=uuid.uuid4(),
                stream_key="inventory",
                last_event_id=None,
                last_hash=None,
                version=0,
            ),
        )
    )
    db.commit()
    monkeypatch.setattr(
        formal_files,
        "lock_formal_principal_graph",
        lambda _db, _ids: None,
    )
    monkeypatch.setattr(
        formal_files,
        "load_formal_principal",
        lambda _db, user_id: principals[user_id],
    )
    storage = FakeStorage()
    yield SimpleNamespace(
        db=db,
        storage=storage,
        owner=principals[users["owner"].id],
        other=principals[users["other"].id],
        principals=principals,
    )
    db.close()
    engine.dispose()


def _command(**changes) -> formal_files.FileUploadIntentInput:
    values = {
        "purpose": "request_attachment",
        "original_filename": "现场照片.jpg",
        "size_bytes": 128,
        "mime_type": "image/jpeg",
        "sha256": "a" * 64,
    }
    values.update(changes)
    return formal_files.FileUploadIntentInput(**values)


def _create(world, *, key: str = "upload-1", **changes):
    return formal_files.create_file_upload_intent(
        world.db,
        actor=world.owner,
        command=_command(**changes),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
        storage=world.storage,
        upload_ttl_seconds=600,
        maximum_size_bytes=1024 * 1024,
    )


def test_create_is_deterministic_hmac_idempotent_and_never_stores_raw_key(world):
    first = _create(world, key="same-key")
    second = _create(world, key="same-key")

    assert first.file_id == second.file_id
    assert first.replayed is False
    assert second.replayed is True
    assert first.upload is not None and second.upload is not None
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 1
    row = world.db.get(FileObject, first.file_id)
    assert row is not None and row.status == "pending"
    serialized = json.dumps(row.metadata_jsonb, sort_keys=True)
    assert "same-key" not in serialized
    assert row.storage_key.endswith(row.id.hex)
    assert row.metadata_jsonb["idempotency_key_hash"] != "same-key"
    assert len(row.metadata_jsonb["idempotency_key_hash"]) == 64
    assert world.db.scalar(select(func.count()).select_from(AuditEvent)) == 2
    assert world.db.in_transaction()


def test_same_idempotency_key_with_changed_request_conflicts(world):
    _create(world, key="conflict")
    with pytest.raises(formal_files.FormalFileError) as caught:
        _create(world, key="conflict", size_bytes=129)
    assert caught.value.code == "file_upload_idempotency_conflict"
    assert caught.value.http_status_code == 409


def test_replay_rejects_tampered_persisted_request_evidence(world):
    created = _create(world, key="tampered-request-evidence")
    row = world.db.get(FileObject, created.file_id)
    row.original_filename = "different.jpg"
    world.db.commit()

    with pytest.raises(formal_files.FormalFileError) as caught:
        _create(world, key="tampered-request-evidence")

    assert caught.value.code == "file_metadata_invalid"
    assert len(world.storage.upload_calls) == 1


@pytest.mark.parametrize(
    ("changes", "code"),
    (
        ({"size_bytes": True}, "file_size_invalid"),
        ({"size_bytes": 0}, "file_size_invalid"),
        ({"original_filename": "../proof.jpg"}, "file_name_invalid"),
        ({"original_filename": "proof.png"}, "file_extension_mismatch"),
        ({"mime_type": "text/html"}, "file_mime_type_invalid"),
        ({"sha256": "A" * 64}, "file_sha256_invalid"),
    ),
)
def test_create_rejects_noncanonical_metadata(world, changes, code):
    with pytest.raises(formal_files.FormalFileError) as caught:
        _create(world, key=f"invalid-{code}", **changes)
    assert caught.value.code == code
    assert not world.storage.upload_calls


def test_purpose_permission_is_rechecked_inside_service(world):
    denied = replace(world.owner, entitlements=())
    world.principals[denied.user_id] = denied
    with pytest.raises(formal_files.FormalFileError) as caught:
        formal_files.create_file_upload_intent(
            world.db,
            actor=denied,
            command=_command(),
            idempotency_key="denied",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-denied",
            storage=world.storage,
            upload_ttl_seconds=600,
        )
    assert caught.value.code == "file_purpose_forbidden"
    assert not world.storage.upload_calls


def test_upload_intent_requires_all_four_exact_bound_headers(world):
    world.storage.bad_upload_headers = {
        "Content-Type": "image/jpeg",
        "x-oss-meta-sha256": "a" * 64,
        "x-oss-meta-file-id": str(uuid.uuid4()),
        # overwrite protection deliberately absent
    }
    with pytest.raises(formal_files.FormalFileError) as caught:
        _create(world, key="bad-signature")
    assert caught.value.code == "file_storage_response_invalid"
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 0


def test_complete_heads_exact_object_before_making_available_and_reverifies(world):
    created = _create(world, key="complete")
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)

    first = formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-complete-1",
        storage=world.storage,
    )
    assert first.already_available is False
    assert row.status == "available"
    assert formal_files.is_available_formal_file_for_purpose(
        row,
        purpose="request_attachment",
        uploader_user_id=world.owner.user_id,
    )
    assert not formal_files.is_available_formal_file_for_purpose(
        row,
        purpose="external_approval_evidence",
        uploader_user_id=world.owner.user_id,
    )
    assert len(world.storage.head_calls) == 1
    world.db.commit()

    second = formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-complete-2",
        storage=world.storage,
    )
    assert second.already_available is True
    assert len(world.storage.head_calls) == 2


@pytest.mark.parametrize(
    "tamper",
    ("key", "size", "mime", "sha", "file_id", "duplicate_meta"),
)
def test_complete_rejects_every_head_mismatch_without_availability(world, tamper):
    created = _create(world, key=f"head-{tamper}")
    row = world.db.get(FileObject, created.file_id)
    head = StoredObjectHead(
        storage_key=(row.storage_key + "-other" if tamper == "key" else row.storage_key),
        size_bytes=(row.size_bytes + 1 if tamper == "size" else row.size_bytes),
        mime_type=("image/png" if tamper == "mime" else row.mime_type),
        metadata={
            "sha256": ("b" * 64 if tamper == "sha" else row.sha256),
            "file-id": (str(uuid.uuid4()) if tamper == "file_id" else str(row.id)),
            **(
                {"x-oss-meta-file-id": str(row.id)}
                if tamper == "duplicate_meta"
                else {}
            ),
        },
        etag='"etag"',
    )
    world.storage.objects[row.storage_key] = head
    with pytest.raises(formal_files.FormalFileError) as caught:
        formal_files.complete_file_upload(
            world.db,
            actor=world.owner,
            file_id=row.id,
            trace_request_id=f"trace-head-{tamper}",
            storage=world.storage,
        )
    assert caught.value.code == "file_object_verification_failed"
    assert row.status == "pending"


def test_repeat_complete_detects_etag_change(world):
    created = _create(world, key="etag")
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row, etag='"etag-1"')
    formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-etag-1",
        storage=world.storage,
    )
    world.db.commit()
    world.storage.materialize(row, etag='"etag-2"')
    with pytest.raises(formal_files.FormalFileError) as caught:
        formal_files.complete_file_upload(
            world.db,
            actor=world.owner,
            file_id=row.id,
            trace_request_id="trace-etag-2",
            storage=world.storage,
        )
    assert caught.value.code == "file_object_changed_after_completion"


@pytest.mark.parametrize("change", ("person", "authorization"))
def test_complete_requires_original_uploader_identity_continuity(world, change):
    created = _create(world, key=f"identity-{change}")
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)
    original = world.owner
    changed = FormalPrincipal(
        user_id=original.user_id,
        person_id=(uuid.uuid4() if change == "person" else original.person_id),
        account_status=original.account_status,
        employment_status=original.employment_status,
        authorization_version=(2 if change == "authorization" else 1),
        access_mode=original.access_mode,
        assignments=original.assignments,
        entitlements=original.entitlements,
    )
    world.principals[original.user_id] = changed
    with pytest.raises(formal_files.FormalFileError) as caught:
        formal_files.complete_file_upload(
            world.db,
            actor=changed,
            file_id=row.id,
            trace_request_id=f"trace-identity-{change}",
            storage=world.storage,
        )
    assert caught.value.code == "file_uploader_identity_changed"
    assert row.status == "pending"


def test_unbound_available_file_download_is_uploader_only(world):
    created = _create(world, key="download")
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)
    formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-download-complete",
        storage=world.storage,
    )
    world.db.commit()

    output = formal_files.create_file_download_intent(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-download",
        storage=world.storage,
        download_ttl_seconds=300,
    )
    assert output.download.storage_key == row.storage_key
    with pytest.raises(formal_files.FormalFileError) as caught:
        formal_files.create_file_download_intent(
            world.db,
            actor=world.other,
            file_id=row.id,
            trace_request_id="trace-download-other",
            storage=world.storage,
            download_ttl_seconds=300,
        )
    assert caught.value.code == "file_download_forbidden"


def test_unbound_download_rechecks_uploader_identity_continuity(world):
    created = _create(world, key="download-identity-change")
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)
    formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-download-identity-complete",
        storage=world.storage,
    )
    world.db.commit()

    changed = replace(world.owner, authorization_version=2)
    world.principals[changed.user_id] = changed
    with pytest.raises(formal_files.FormalFileError) as caught:
        formal_files.create_file_download_intent(
            world.db,
            actor=changed,
            file_id=row.id,
            trace_request_id="trace-download-identity-changed",
            storage=world.storage,
            download_ttl_seconds=300,
        )
    assert caught.value.code == "file_uploader_identity_changed"
    assert not world.storage.download_calls


def test_download_rejects_storage_key_mismatch(world):
    created = _create(world, key="bad-download")
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)
    formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-bad-download-complete",
        storage=world.storage,
    )
    world.db.commit()
    world.storage.bad_download_key = "formal-files/v1/wrong"
    with pytest.raises(formal_files.FormalFileError) as caught:
        formal_files.create_file_download_intent(
            world.db,
            actor=world.owner,
            file_id=row.id,
            trace_request_id="trace-bad-download",
            storage=world.storage,
            download_ttl_seconds=300,
        )
    assert caught.value.code == "file_storage_response_invalid"


def test_stocktake_evidence_file_cannot_be_reused_across_bindings(world):
    created = _create(
        world,
        key="multi-stocktake",
        purpose="stocktake_evidence",
    )
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)
    formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-multi-complete",
        storage=world.storage,
    )
    world.db.commit()
    for index in range(2):
        world.db.add(
            DocumentAttachment(
                id=uuid.uuid4(),
                document_type="stocktake_scope_count_completion",
                document_id=str(uuid.uuid4()),
                file_id=row.id,
                attachment_type="stocktake_evidence",
                status="active",
                uploaded_by=world.owner.user_id,
                created_at=NOW + timedelta(seconds=index),
            )
        )
    with pytest.raises(IntegrityError, match="document_attachments.file_id"):
        world.db.commit()
    world.db.rollback()
    assert not world.storage.download_calls


def test_bound_request_attachment_requires_formal_request_scope_read(world, monkeypatch):
    created = _create(world, key="request-bound")
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)
    formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-request-bound-complete",
        storage=world.storage,
    )
    world.db.commit()
    request_id = uuid.uuid4()
    world.db.add(
        demand_models.MaterialRequestFile(
            id=uuid.uuid4(),
            request_id=request_id,
            revision_id=uuid.uuid4(),
            revision_no=1,
            request_line_id=None,
            file_id=row.id,
            purpose="request_attachment",
            created_by_user_id=world.owner.user_id,
            created_at=NOW,
        )
    )
    world.db.commit()
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(
        formal_files.material_request_query,
        "material_request_detail",
        lambda _db, *, actor, request_id: seen.append(request_id),
    )
    formal_files.create_file_download_intent(
        world.db,
        actor=world.other,
        file_id=row.id,
        trace_request_id="trace-request-bound-download",
        storage=world.storage,
        download_ttl_seconds=300,
    )
    assert seen == [request_id]

    def denied(*_args, **_kwargs):
        raise formal_files.material_request_query.MaterialRequestReadError(
            "material_request_not_found",
            "not_found",
            "需求单不存在",
        )

    monkeypatch.setattr(
        formal_files.material_request_query,
        "material_request_detail",
        denied,
    )
    with pytest.raises(formal_files.FormalFileError) as caught:
        formal_files.create_file_download_intent(
            world.db,
            actor=world.other,
            file_id=row.id,
            trace_request_id="trace-request-bound-denied",
            storage=world.storage,
            download_ttl_seconds=300,
        )
    assert caught.value.code == "file_download_forbidden"


def test_external_evidence_binding_uses_parent_request_scope(world, monkeypatch):
    created = _create(
        world,
        key="external-bound",
        purpose="external_approval_evidence",
        original_filename="external.pdf",
        mime_type="application/pdf",
    )
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)
    formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-external-bound-complete",
        storage=world.storage,
    )
    world.db.commit()
    request_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    step_id = uuid.uuid4()
    world.db.add(
        demand_models.ApprovalInstance(
            id=instance_id,
            request_id=request_id,
            request_revision_id=uuid.uuid4(),
            revision_no=1,
            route_version_id=uuid.uuid4(),
            attempt_no=1,
            status="completed",
            current_step_no=None,
            current_step_id=None,
            version=1,
            completed_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    world.db.flush()
    world.db.add(
        demand_models.ApprovalStep(
            id=step_id,
            instance_id=instance_id,
            step_no=1,
            attempt_no=1,
            predecessor_step_id=None,
            supersedes_step_id=None,
            reopened_from_step_id=None,
            source_mode="external_registration",
            status="approved",
            assignee_user_id=None,
            assignee_snapshot_jsonb={},
            decision_manifest_sha256="c" * 64,
            opened_at=NOW - timedelta(minutes=1),
            decided_at=NOW,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    world.db.flush()
    world.db.add(
        demand_models.ApprovalExternalRegistration(
            id=uuid.uuid4(),
            step_id=step_id,
            registration_no=f"REG-{uuid.uuid4().hex}",
            external_action="approve",
            status="pending_verification",
            evidence_file_id=row.id,
            external_approver_snapshot_jsonb={"name_masked": "星***"},
            external_decided_at=NOW,
            decision_manifest_sha256="d" * 64,
            registered_by_user_id=world.owner.user_id,
            registered_by_person_id=world.owner.person_id,
            registered_role_assignment_id=uuid.uuid4(),
            authorization_version=1,
            registered_at=NOW,
            verified_by_user_id=None,
            verified_by_person_id=None,
            verified_role_assignment_id=None,
            verified_authorization_version=None,
            verification_comment="",
            verified_at=None,
            version=0,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    world.db.commit()
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(
        formal_files.material_request_query,
        "material_request_detail",
        lambda _db, *, actor, request_id: seen.append(request_id),
    )
    formal_files.create_file_download_intent(
        world.db,
        actor=world.other,
        file_id=row.id,
        trace_request_id="trace-external-bound-download",
        storage=world.storage,
        download_ttl_seconds=300,
    )
    assert seen == [request_id]


def test_single_stocktake_binding_invokes_formal_scope_read(world, monkeypatch):
    created = _create(
        world,
        key="single-stocktake",
        purpose="stocktake_evidence",
    )
    row = world.db.get(FileObject, created.file_id)
    world.storage.materialize(row)
    formal_files.complete_file_upload(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-single-complete",
        storage=world.storage,
    )
    world.db.commit()
    completion_id = uuid.uuid4()
    task_id = uuid.uuid4()
    world.db.add(
        DocumentAttachment(
            id=uuid.uuid4(),
            document_type="stocktake_scope_count_completion",
            document_id=str(completion_id),
            file_id=row.id,
            attachment_type="stocktake_evidence",
            status="active",
            uploaded_by=world.owner.user_id,
            created_at=NOW,
        )
    )
    world.db.commit()
    monkeypatch.setattr(
        formal_files,
        "_stocktake_task_ids_for_bindings",
        lambda _db, bindings: (task_id,),
    )
    seen: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        formal_files.stocktake_query,
        "stocktake_task_detail",
        lambda _db, *, actor, task_id: seen.append((task_id, actor.user_id)),
    )
    formal_files.create_file_download_intent(
        world.db,
        actor=world.owner,
        file_id=row.id,
        trace_request_id="trace-single-download",
        storage=world.storage,
        download_ttl_seconds=300,
    )
    assert seen == [(task_id, world.owner.user_id)]


def test_storage_failure_is_stable_and_has_no_file_fact(world):
    world.storage.fail_upload = True
    with pytest.raises(formal_files.FormalFileError) as caught:
        _create(world, key="storage-error")
    assert caught.value.code == "file_storage_unavailable"
    assert "secret" not in caught.value.message
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 0


@pytest.fixture
def api_client(world):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite://",
        database_schema_mode="alembic",
        file_storage_enabled=True,
        file_storage_provider="aliyun_oss_v2",
        file_storage_region="cn-shanghai",
        file_storage_bucket="rsc-private-files",
        file_upload_intent_ttl_seconds=600,
        file_download_intent_ttl_seconds=300,
        file_idempotency_hmac_secret=SECRET,
        max_upload_bytes=1024 * 1024,
    )

    def override_db():
        yield world.db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_formal_principal] = lambda: world.owner
    app.dependency_overrides[get_formal_file_storage_adapter] = (
        lambda: world.storage
    )
    with TestClient(app) as client:
        yield client, world, settings
    app.dependency_overrides.clear()


def _api_upload(client: TestClient, *, key: str = "api-upload", body=None):
    return client.post(
        "/api/v1/files/upload-intents",
        headers={"Idempotency-Key": key, "X-Request-ID": f"trace-{key}"},
        json=body
        or {
            "purpose": "request_attachment",
            "original_filename": "proof.pdf",
            "size_bytes": 256,
            "mime_type": "application/pdf",
            "sha256": "b" * 64,
        },
    )


def test_api_end_to_end_uses_fake_only_and_sets_no_store_headers(api_client):
    client, world, _ = api_client
    created = _api_upload(client)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store, max-age=0"
    assert created.headers["referrer-policy"] == "no-referrer"
    assert created.json()["upload"]["headers"]["x-oss-forbid-overwrite"] == "true"
    file_id = uuid.UUID(created.json()["file_id"])
    row = world.db.get(FileObject, file_id)
    world.storage.materialize(row)

    completed = client.post(
        f"/api/v1/files/{file_id}/complete",
        headers={"X-Request-ID": "trace-api-complete"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "available"
    downloaded = client.get(
        f"/api/v1/files/{file_id}/download-intent",
        headers={"X-Request-ID": "trace-api-download"},
    )
    assert downloaded.status_code == 200
    assert downloaded.json()["download"]["method"] == "GET"
    assert len(world.storage.upload_calls) == 1
    assert len(world.storage.head_calls) == 1
    assert len(world.storage.download_calls) == 1
    assert world.db.scalar(select(func.count()).select_from(AuditEvent)) == 3


def test_api_strict_schema_rejects_boolean_size_without_storage_call(api_client):
    client, world, _ = api_client
    response = _api_upload(
        client,
        key="bool-size",
        body={
            "purpose": "request_attachment",
            "original_filename": "proof.pdf",
            "size_bytes": True,
            "mime_type": "application/pdf",
            "sha256": "b" * 64,
        },
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert not world.storage.upload_calls
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 0


def test_api_permission_is_denied_by_service_not_only_http_dependency(api_client):
    client, world, _ = api_client
    denied = replace(world.owner, entitlements=())
    world.principals[denied.user_id] = denied
    app.dependency_overrides[get_formal_principal] = lambda: denied
    response = _api_upload(client, key="api-denied")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "file_purpose_forbidden"
    assert not world.storage.upload_calls


def test_api_disabled_runtime_fails_before_storage_or_database(api_client):
    client, world, settings = api_client
    disabled = settings.model_copy(
        update={
            "file_storage_enabled": False,
            "file_storage_provider": "disabled",
            "file_idempotency_hmac_secret": "",
        }
    )
    app.dependency_overrides[get_settings] = lambda: disabled
    response = _api_upload(client, key="api-disabled")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "file_storage_disabled"
    assert not world.storage.upload_calls
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 0


def test_api_adapter_error_is_sanitized_and_transaction_rolls_back(api_client):
    client, world, _ = api_client
    world.storage.fail_upload = True
    response = _api_upload(client, key="api-storage-error")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "file_storage_unavailable",
        "category": "service_unavailable",
        "message": "文件存储暂不可用",
    }
    assert "secret" not in response.text
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 0
    assert world.db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_api_unknown_adapter_error_is_sanitized_and_rolls_back(api_client, monkeypatch):
    client, world, _ = api_client

    def uncertain(**_kwargs):
        raise RuntimeError("uncertain provider detail must not leak")

    monkeypatch.setattr(world.storage, "create_upload_intent", uncertain)
    response = _api_upload(client, key="api-uncertain")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "file_operation_unavailable"
    assert "uncertain" not in response.text
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 0


def test_api_audit_failure_rolls_back_pending_file(api_client):
    client, world, _ = api_client
    head = world.db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "material_request")
    )
    world.db.delete(head)
    world.db.commit()
    response = _api_upload(client, key="api-audit-fail")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "file_audit_unavailable"
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 0
    assert world.db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_api_missing_required_headers_is_stable_and_no_side_effect(api_client):
    client, world, _ = api_client
    response = client.post(
        "/api/v1/files/upload-intents",
        json={
            "purpose": "request_attachment",
            "original_filename": "proof.pdf",
            "size_bytes": 256,
            "mime_type": "application/pdf",
            "sha256": "b" * 64,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "file_idempotency_key_invalid"
    assert not world.storage.upload_calls
    assert world.db.scalar(select(func.count()).select_from(FileObject)) == 0


def _production_settings(**overrides) -> Settings:
    values = {
        "environment": "production",
        "database_url": "postgresql+psycopg://star_oam_api:test@db/test",
        "database_schema_mode": "alembic",
        "legacy_prototype_writes_enabled": False,
        "password_login_enabled": False,
        "admin_mobile": "",
        "admin_name": "",
        "admin_initial_password": None,
        "edge_sync_enabled": False,
        "edge_sync_secret": "",
        "edge_sync_allowed_sources": "",
        "edge_sync_legacy_batches_enabled": False,
        "edge_sync_legacy_personnel_projection_enabled": False,
        "jwt_secret": "production-jwt-secret-at-least-thirty-two-characters",
        "identity_hash_secret": "production-identity-secret-at-least-thirty-two",
        "auth_idempotency_hmac_secret": "production-auth-replay-secret-at-least-thirty-two",
        "auth_idempotency_encryption_provider": "aliyun_kms",
        "auth_idempotency_kms_key_id": "kms-production-auth",
        "auth_login_rate_limit_hmac_secret": "production-login-limit-secret-at-least-thirty-two",
        "sms_login_enabled": True,
        "sms_provider": "aliyun_pnvs",
        "sms_access_key_id": "test-access-id",
        "sms_access_key_secret": "test-access-secret",
        "sms_sign_name": "test-sign",
        "sms_template_code": "SMS_TEST",
        "sms_scheme_name": "test-scheme",
        "wechat_login_enabled": False,
        "material_request_writes_enabled": False,
        "stocktake_writes_enabled": False,
        "file_storage_enabled": True,
        "file_storage_provider": "aliyun_oss_v2",
        "file_storage_region": "cn-shanghai",
        "file_storage_bucket": "rsc-private-files",
        "file_upload_intent_ttl_seconds": 600,
        "file_download_intent_ttl_seconds": 300,
        "file_idempotency_hmac_secret": "production-file-hmac-secret-at-least-thirty-two",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_file_configuration_is_complete_distinct_and_fail_closed():
    settings = _production_settings()
    settings.validate_api_startup()
    assert settings.file_storage_configuration_ready() is True

    with pytest.raises(ValueError, match="private bucket coordinates"):
        _production_settings(file_storage_region="").validate_api_startup()
    with pytest.raises(ValueError, match="dedicated idempotency HMAC"):
        _production_settings(file_idempotency_hmac_secret="").validate_api_startup()
    with pytest.raises(ValueError, match="pairwise distinct"):
        _production_settings(
            file_idempotency_hmac_secret=(
                "production-jwt-secret-at-least-thirty-two-characters"
            )
        ).validate_api_startup()
    with pytest.raises(ValidationError):
        _production_settings(file_upload_intent_ttl_seconds=3600)

    fields = Settings.model_fields
    assert fields["file_storage_enabled"].default is False
    assert fields["file_storage_provider"].default == "disabled"
    assert fields["file_idempotency_hmac_secret"].default == ""
    assert not any(
        "file" in name and "access_key" in name for name in fields
    )


def test_formal_file_routes_and_deployment_coordinates_are_present():
    paths = {route.path for route in app.routes}
    assert {
        "/api/v1/files/upload-intents",
        "/api/v1/files/{file_id}/complete",
        "/api/v1/files/{file_id}/download-intent",
    }.issubset(paths)
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    requirements = (root / "backend/requirements.txt").read_text(encoding="utf-8")
    for name in (
        "OAM_FILE_STORAGE_ENABLED",
        "OAM_FILE_STORAGE_PROVIDER",
        "OAM_FILE_STORAGE_REGION",
        "OAM_FILE_STORAGE_BUCKET",
        "OAM_FILE_UPLOAD_INTENT_TTL_SECONDS",
        "OAM_FILE_DOWNLOAD_INTENT_TTL_SECONDS",
        "OAM_FILE_IDEMPOTENCY_HMAC_SECRET",
    ):
        assert name in env_example
        assert name in compose
    assert "OAM_FILE_STORAGE_ENABLED=false" in env_example
    assert "OAM_FILE_STORAGE_PROVIDER=disabled" in env_example
    assert "OSS_ACCESS_KEY_ID" not in env_example
    assert "OSS_ACCESS_KEY_SECRET" not in env_example
    assert "alibabacloud-oss-v2==1.3.2" in requirements


def test_production_adapter_uses_env_credentials_v4_and_overwrite_bound_headers(
    monkeypatch,
):
    calls: dict[str, object] = {}

    class EnvProvider:
        def __init__(self):
            calls["credential_provider"] = "environment"

    class Client:
        def __init__(self, config):
            calls["config"] = config

        def presign(self, request, options):
            calls.setdefault("presign", []).append((request, options))
            if request.kind == "put":
                return SimpleNamespace(
                    url="https://bucket.oss-cn-shanghai.aliyuncs.com/key?put=1",
                    signed_headers={
                        "Content-Type": request.kwargs["content_type"],
                        "x-oss-meta-sha256": request.kwargs["metadata"]["sha256"],
                        "x-oss-meta-file-id": request.kwargs["metadata"]["file-id"],
                        "x-oss-forbid-overwrite": "true",
                    },
                )
            return SimpleNamespace(
                url="https://bucket.oss-cn-shanghai.aliyuncs.com/key?get=1",
                signed_headers={},
            )

        def head_object(self, request):
            calls["head_request"] = request
            return SimpleNamespace(
                content_length=12,
                content_type="application/pdf",
                metadata={"sha256": "a" * 64, "file-id": str(uuid.UUID(int=1))},
                etag='"etag"',
            )

    class Request:
        def __init__(self, kind, **kwargs):
            self.kind = kind
            self.kwargs = kwargs

    config = SimpleNamespace(
        credentials_provider=None,
        region=None,
        signature_version=None,
    )
    fake_sdk = SimpleNamespace(
        credentials=SimpleNamespace(
            EnvironmentVariableCredentialsProvider=EnvProvider,
        ),
        config=SimpleNamespace(load_default=lambda: config),
        Client=Client,
        PutObjectRequest=lambda **kwargs: Request("put", **kwargs),
        GetObjectRequest=lambda **kwargs: Request("get", **kwargs),
        HeadObjectRequest=lambda **kwargs: Request("head", **kwargs),
        PresignOptions=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_oss_v2", fake_sdk)
    adapter = AliyunOssV2StorageAdapter(
        region="cn-shanghai",
        bucket="rsc-private-files",
    )
    file_id = str(uuid.UUID(int=1))
    upload = adapter.create_upload_intent(
        storage_key="formal-files/v1/request_attachment/00/00000000000000000000000000000001",
        file_id=file_id,
        sha256="a" * 64,
        size_bytes=12,
        mime_type="application/pdf",
        ttl_seconds=600,
    )
    put_request = calls["presign"][0][0]
    assert calls["credential_provider"] == "environment"
    assert config.region == "cn-shanghai"
    assert config.signature_version == "v4"
    assert put_request.kwargs["forbid_overwrite"] is True
    assert put_request.kwargs["metadata"] == {
        "sha256": "a" * 64,
        "file-id": file_id,
    }
    assert upload.headers == {
        "Content-Type": "application/pdf",
        "x-oss-meta-sha256": "a" * 64,
        "x-oss-meta-file-id": file_id,
        "x-oss-forbid-overwrite": "true",
    }
    head = adapter.head_object(storage_key=upload.storage_key)
    assert head.size_bytes == 12
    download = adapter.create_download_intent(
        storage_key=upload.storage_key,
        ttl_seconds=300,
    )
    assert download.url.endswith("?get=1")
