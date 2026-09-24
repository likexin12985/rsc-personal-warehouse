"""Real API-role export-job admission and transition checks on owned PG16."""

from hashlib import md5, sha256
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4, uuid5

from fastapi import FastAPI
from fastapi.testclient import TestClient
from formal_file_integrity import (
    FILE_METADATA_SCHEMA, FileUploadIntentInput, StoredObjectHead,
    _head_manifest_sha256, _prepare_upload, _storage_key, _upload_request_hash,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import AuditEvent, FileJob, FileObject, Role
from app.formal_services.file_storage import DownloadIntent
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_report_download import (
    create_inventory_report_download_intent, get_inventory_report_job_status,
)
from app.formal_services.inventory_report_jobs import _RESULT_NAMESPACE
from app.formal_services.inventory_report_workbook import MIME_TYPE, render_inventory_workbook
from app.inventory_models import InventoryTransaction, StockBalance
from app.models import User
from app.routers import formal_reports
from app.routers.formal_files import get_formal_file_storage_adapter
from test_formal_access import assign, make_organization, make_user


def assert_report_job_runtime_gate(owner_engine, api_engine) -> dict[str, bool]:
    with Session(owner_engine) as db:
        headquarters = make_organization(db, name="Synthetic report gate HQ")
        region = make_organization(db, name="Synthetic report gate region", parent=headquarters)
        requester, person = make_user(db, region, name="Synthetic report requester")
        assert person is not None
        role = db.scalar(select(Role).where(Role.code == "provincial_manager"))
        assert role is not None
        assignment = assign(
            db, requester, role,
            scope_type="organization", scope_id=str(region.id),
        )
        db.commit()
        requester_id, person_id, assignment_id = requester.id, person.id, assignment.id

    parameters = {"report": "inventory_balances", "filters": {}}
    parameters_hash = sha256(
        json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    scope = {
        "version": 1,
        "account_ids": [],
        "assignment_ids": [str(assignment_id)],
    }

    def new_job() -> FileJob:
        return FileJob(
            id=uuid4(), job_type="export", requested_by=requester_id,
            parameters_jsonb=parameters, parameters_hash=parameters_hash,
            idempotency_key=sha256(uuid4().bytes).hexdigest(),
            status="queued", export_authorization_version=1,
            export_scope_jsonb=scope, export_ledger_cursor=0,
            download_count=0,
        )

    with Session(api_engine) as db:
        job = new_job()
        db.add(job)
        db.commit()
        job_id = job.id
        _expect_sqlstate(
            db, "23514",
            "UPDATE public.file_jobs SET status='succeeded' WHERE id=:id",
            job_id,
        )
        _expect_sqlstate(
            db, "42501",
            "UPDATE public.file_jobs SET parameters_jsonb='{}'::jsonb WHERE id=:id",
            job_id,
        )
        db.execute(text("""
            UPDATE public.file_jobs
            SET status='running', started_at=transaction_timestamp()
            WHERE id=:id
        """), {"id": job_id})
        db.execute(text("""
            UPDATE public.file_jobs
            SET status='failed', completed_at=transaction_timestamp(),
                error_detail='synthetic_report_gate_failure'
            WHERE id=:id
        """), {"id": job_id})
        db.commit()
        assert db.scalar(select(FileJob.status).where(FileJob.id == job_id)) == "failed"

    payload = render_inventory_workbook([], ledger_cursor=0)
    digest = sha256(payload).hexdigest()
    with Session(api_engine) as db:
        successful_job = new_job()
        db.add(successful_job)
        db.commit()
        successful_id = successful_job.id
        db.execute(text("""
            UPDATE public.file_jobs
            SET status='running', started_at=transaction_timestamp()
            WHERE id=:id
        """), {"id": successful_id})
        db.commit()
        file_id = uuid5(_RESULT_NAMESPACE, str(successful_id))
        key = _storage_key("inventory_report_export", file_id)
        filename = "RSC库存余额_PG16合成验证.xlsx"
        prepared = _prepare_upload(
            FileUploadIntentInput(
                purpose="inventory_report_export", original_filename=filename,
                size_bytes=len(payload), mime_type=MIME_TYPE, sha256=digest,
            ), maximum_size_bytes=20 * 1024 * 1024,
        )
        metadata = {
            "authorization_version": 1,
            "file_id": str(file_id),
            "idempotency_key_hash": successful_job.idempotency_key,
            "provider": "aliyun_oss_v2",
            "purpose": "inventory_report_export",
            "request_sha256": _upload_request_hash(prepared),
            "schema": FILE_METADATA_SCHEMA,
            "storage_key": key,
            "uploader_person_id": str(person_id),
            "uploader_user_id": requester_id,
        }
        db_time = db.scalar(select(func.current_timestamp()))
        assert db_time is not None
        report_file = FileObject(
            id=file_id, storage_key=key, sha256=digest, size_bytes=len(payload),
            mime_type=MIME_TYPE, original_filename=filename,
            uploaded_by=requester_id, status="pending", metadata_jsonb=metadata,
            created_at=db_time,
        )
        db.add(report_file)
        try:
            db.flush()
        except DBAPIError as exc:
            raise AssertionError(
                f"report_file_insert_sqlstate_{exc.orig.sqlstate}"
            ) from None
        head = StoredObjectHead(
            storage_key=key, size_bytes=len(payload), mime_type=MIME_TYPE,
            metadata={"sha256": digest, "file-id": str(file_id)},
            etag=md5(payload).hexdigest(),
        )
        report_file.status = "available"
        report_file.metadata_jsonb = {
            **metadata,
            "completion": {
                "etag_sha256": sha256(head.etag.encode()).hexdigest(),
                "head_manifest_sha256": _head_manifest_sha256(head),
                "verified_at": db_time.isoformat(),
            },
        }
        db.flush()
        db.execute(text("""
            UPDATE public.file_jobs
            SET status='succeeded', completed_at=transaction_timestamp(),
                result_file_id=:file_id, result_sha256=:digest, result_size_bytes=:size
            WHERE id=:job_id
        """), {"job_id": successful_id, "file_id": file_id,
               "digest": digest, "size": len(payload)})
        db.commit()

    storage = SimpleNamespace(
        provider_code="aliyun_oss_v2",
        head_object=lambda *, storage_key: head if storage_key == key else None,
        create_download_intent=lambda *, storage_key, ttl_seconds: DownloadIntent(
            storage_key=storage_key,
            url="https://private.example.invalid/download?signature=synthetic",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        ),
    )
    with Session(api_engine) as db:
        actor = load_formal_principal(db, requester_id)
        status = get_inventory_report_job_status(db, actor=actor, job_id=successful_id)
        assert status.status == "succeeded" and status.file_available
        assert status.download_count == 0
        signed = create_inventory_report_download_intent(
            db, actor=actor, job_id=successful_id, storage=storage,
            request_id="pg16-synthetic-report-download-1", ttl_seconds=120,
        )
        db.commit()
        assert signed.file_id == file_id and signed.download_count == 1
        assert signed.url.startswith("https://private.example.invalid/")

    with Session(api_engine) as db:
        assert db.scalar(select(FileJob.download_count).where(FileJob.id == successful_id)) == 1
        audit = db.scalar(select(AuditEvent).where(
            AuditEvent.stream_key == "inventory",
            AuditEvent.action == "inventory_report_download_intent",
            AuditEvent.aggregate_id == str(successful_id),
            AuditEvent.request_id == "pg16-synthetic-report-download-1",
        ))
        assert audit is not None and audit.after_jsonb["download_count"] == 1
        actor = load_formal_principal(db, requester_id)
        try:
            create_inventory_report_download_intent(
                db, actor=actor, job_id=successful_id, storage=storage,
                request_id="pg16-synthetic-report-download-1", ttl_seconds=120,
            )
        except InventoryReadError as exc:
            assert exc.code == "inventory_report_download_request_replayed"
        else:
            raise AssertionError("replayed report download request was accepted")
        db.rollback()
        _expect_sqlstate(
            db, "23514",
            "UPDATE public.file_jobs SET download_count=download_count+2 WHERE id=:id",
            successful_id,
        )

    api = FastAPI()
    api.include_router(formal_reports.router, prefix="/api")
    settings = Settings(
        inventory_report_export_enabled=True,
        file_storage_enabled=True,
        file_storage_provider="aliyun_oss_v2",
        file_storage_region="cn-shanghai",
        file_storage_bucket="rsc-private-report-tests",
        file_idempotency_hmac_secret="f" * 64,
    )

    def pg16_session():
        with Session(api_engine) as db:
            yield db

    with Session(api_engine) as db:
        http_actor = load_formal_principal(db, requester_id)
    api.dependency_overrides[get_db] = pg16_session
    api.dependency_overrides[get_formal_principal] = lambda: http_actor
    api.dependency_overrides[get_settings] = lambda: settings
    api.dependency_overrides[get_formal_file_storage_adapter] = lambda: storage
    url = f"/api/v1/reports/inventory-balances/exports/{successful_id}"
    with TestClient(api) as client:
        status_response = client.get(url)
        assert status_response.status_code == 200
        assert status_response.json()["file_available"] is True
        assert status_response.json()["download_count"] == 1
        assert status_response.headers["cache-control"] == "no-store, max-age=0"
        download_response = client.post(
            f"{url}/download-intents",
            headers={"X-Request-ID": "pg16-synthetic-report-http-download-2"},
        )
        assert download_response.status_code == 200
        assert download_response.json()["download_count"] == 2
        assert download_response.json()["file_id"] == str(file_id)
        assert download_response.headers["cache-control"] == "no-store, max-age=0"
        replay_response = client.post(
            f"{url}/download-intents",
            headers={"X-Request-ID": "pg16-synthetic-report-http-download-2"},
        )
        assert replay_response.status_code == 409
        assert replay_response.json()["detail"]["code"] == "inventory_report_download_request_replayed"
        assert replay_response.headers["cache-control"] == "no-store, max-age=0"
    with Session(api_engine) as db:
        assert db.scalar(select(FileJob.download_count).where(FileJob.id == successful_id)) == 2
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.stream_key == "inventory",
            AuditEvent.action == "inventory_report_download_intent",
            AuditEvent.aggregate_id == str(successful_id),
        )) == 2

    with Session(owner_engine) as db:
        db.get(User, requester_id).is_active = False
        db.commit()
    with TestClient(api) as client:
        revoked_status = client.get(url)
        assert revoked_status.status_code == 403
        assert revoked_status.json()["detail"]["code"] == "inventory_report_actor_stale"
        revoked_download = client.post(
            f"{url}/download-intents",
            headers={"X-Request-ID": "pg16-synthetic-report-http-download-3"},
        )
        assert revoked_download.status_code == 403
        assert revoked_download.json()["detail"]["code"] == "inventory_report_actor_stale"
    with Session(api_engine) as db:
        actor = load_formal_principal(db, requester_id)
        for operation in (
            lambda: get_inventory_report_job_status(db, actor=actor, job_id=successful_id),
            lambda: create_inventory_report_download_intent(
                db, actor=actor, job_id=successful_id, storage=storage,
                request_id="pg16-synthetic-report-download-2", ttl_seconds=120,
            ),
        ):
            try:
                operation()
            except InventoryReadError as exc:
                assert exc.code == "inventory_report_actor_stale"
            else:
                raise AssertionError("revoked report requester was accepted")
            db.rollback()
        with _expected_error("23514"):
            with db.begin_nested():
                db.add(new_job())
                db.flush()
        db.rollback()

    with Session(owner_engine) as db:
        assert db.scalar(select(InventoryTransaction.id).limit(1)) is None
        assert db.scalar(select(StockBalance.stock_account_id).limit(1)) is None
    return {
        "validQueuedClaimFailureTransition": True,
        "validFileResultAndDownloadCount": True,
        "serviceDownloadIntentAudited": True,
        "serviceDownloadReplayRejected": True,
        "serviceDownloadRevocationRejected": True,
        "httpStatusAndDownloadAudited": True,
        "httpDownloadReplayRejected": True,
        "httpDownloadRevocationRejected": True,
        "downloadCountJumpRejected": True,
        "skippedCompletionRejected": True,
        "immutableParametersDenied": True,
        "revokedRequesterRejected": True,
        "stockFactsUntouched": True,
    }


def _expect_sqlstate(db: Session, expected: str, sql: str, job_id) -> None:
    with _expected_error(expected):
        with db.begin_nested():
            db.execute(text(sql), {"id": job_id})


class _expected_error:
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, _traceback):
        if not isinstance(error, DBAPIError):
            raise AssertionError(f"expected PostgreSQL SQLSTATE {self.sqlstate}")
        assert error.orig.sqlstate == self.sqlstate, error.orig.sqlstate
        return True
