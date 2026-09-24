"""Synthetic transport, real PG16 opening/report application/worker/download."""

from datetime import datetime, timedelta, timezone
from hashlib import md5, sha256 as sha256_digest
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from formal_file_integrity import StoredObjectHead
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.formal_services.file_storage import DownloadIntent
from app.formal_services.inventory_report_snapshot import capture_inventory_report_snapshot
from app.foundation_models import AuditEvent, FileJob, FileObject
from app.inventory_models import InventoryTransaction, StockBalance
from app.inventory_report_worker import process_one_inventory_report
from app.models import User
from app.routers import formal_reports
from app.routers.formal_files import get_formal_file_storage_adapter


class _SyntheticPrivateStorage:
    provider_code = "aliyun_oss_v2"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[StoredObjectHead, bytes]] = {}

    def put_report_object(
        self, *, storage_key: str, file_id: str, sha256: str, payload: bytes,
    ) -> StoredObjectHead:
        assert storage_key not in self.objects
        assert sha256 == sha256_digest(payload).hexdigest()
        head = StoredObjectHead(
            storage_key=storage_key,
            size_bytes=len(payload),
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            metadata={"sha256": sha256, "file-id": str(file_id)},
            etag=md5(payload).hexdigest(),
        )
        self.objects[storage_key] = (head, payload)
        return head

    def head_object(self, *, storage_key: str) -> StoredObjectHead:
        return self.objects[storage_key][0]

    def create_download_intent(self, *, storage_key: str, ttl_seconds: int) -> DownloadIntent:
        assert storage_key in self.objects
        return DownloadIntent(
            storage_key=storage_key,
            url="https://private.example.invalid/download?signature=synthetic",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )


def assert_report_full_flow(owner_engine, api_engine) -> dict[str, bool]:
    with Session(api_engine) as db:
        requester = db.scalar(select(User).where(User.name == "Synthetic opening counter"))
        assert requester is not None
        requester_id = requester.id
        actor = load_formal_principal(db, requester_id)
        snapshot = capture_inventory_report_snapshot(db, actor=actor)
        assert snapshot.account_ids and snapshot.lines
        assert any(line.quantity == "1.000" for line in snapshot.lines)

    def stock_facts() -> tuple[object, object]:
        with Session(owner_engine) as db:
            return (
                tuple(db.scalars(select(InventoryTransaction.id).order_by(InventoryTransaction.id))),
                tuple(db.execute(select(
                    StockBalance.stock_account_id, StockBalance.quantity,
                    StockBalance.ledger_cursor,
                ).order_by(StockBalance.stock_account_id))),
            )

    before_stock = stock_facts()
    storage = _SyntheticPrivateStorage()
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

    api.dependency_overrides[get_db] = pg16_session
    api.dependency_overrides[get_formal_principal] = lambda: actor
    api.dependency_overrides[get_settings] = lambda: settings
    api.dependency_overrides[get_formal_file_storage_adapter] = lambda: storage
    endpoint = "/api/v1/reports/inventory-balances/exports"
    idempotency_key = "pg16-opening-report-" + uuid4().hex
    with TestClient(api) as client:
        capability = client.get(f"{endpoint}/capabilities")
        assert capability.status_code == 200 and capability.json() == {"available": True}
        created = client.post(endpoint, headers={
            "Idempotency-Key": idempotency_key,
            "X-Request-ID": "pg16-opening-report-request-" + uuid4().hex,
        })
        assert created.status_code == 202, created.json()
        job_id = UUID(created.json()["job_id"])
        assert created.json()["status"] == "queued"
        assert created.json()["replayed"] is False
        replay = client.post(endpoint, headers={
            "Idempotency-Key": idempotency_key,
            "X-Request-ID": "pg16-opening-report-replay-" + uuid4().hex,
        })
        assert replay.status_code == 202, replay.json()
        assert replay.json()["job_id"] == str(job_id) and replay.json()["replayed"] is True
        recovered = client.get(f"{endpoint}/recovery", headers={"Idempotency-Key": idempotency_key})
        assert recovered.status_code == 200 and recovered.json()["job_id"] == str(job_id)
        assert recovered.json()["status"] == "queued"
        wrong_key = client.get(f"{endpoint}/recovery", headers={
            "Idempotency-Key": idempotency_key + "-other",
        })
        assert wrong_key.status_code == 404

    with Session(api_engine) as db:
        assert db.scalar(select(func.count()).select_from(FileJob).where(
            FileJob.requested_by == requester_id,
        )) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "inventory_report_export_requested",
            AuditEvent.aggregate_id == str(job_id),
        )) == 1

    processed = process_one_inventory_report(
        lambda: Session(api_engine), storage=storage, job_id=job_id,
    )
    assert processed.job_id == job_id and processed.status == "succeeded"
    with Session(api_engine) as db:
        job = db.get(FileJob, job_id)
        assert job is not None and job.result_file_id is not None
        result_file = db.get(FileObject, job.result_file_id)
        assert result_file is not None and result_file.status == "available"
        head, payload = storage.objects[result_file.storage_key]
        assert head.size_bytes == job.result_size_bytes == len(payload)
        assert sha256_digest(payload).hexdigest() == job.result_sha256 == result_file.sha256
        assert payload.startswith(b"PK")
        result_file_id = result_file.id

    with TestClient(api) as client:
        recovered = client.get(f"{endpoint}/recovery", headers={"Idempotency-Key": idempotency_key})
        assert recovered.status_code == 200 and recovered.json()["job_id"] == str(job_id)
        assert recovered.json()["file_available"] is True
        status = client.get(f"{endpoint}/{job_id}")
        assert status.status_code == 200, status.json()
        assert status.json()["status"] == "succeeded" and status.json()["file_available"] is True
        downloaded = client.post(f"{endpoint}/{job_id}/download-intents", headers={
            "X-Request-ID": "pg16-opening-report-download-" + uuid4().hex,
        })
        assert downloaded.status_code == 200, downloaded.json()
        assert downloaded.json()["download_count"] == 1
        assert downloaded.json()["file_id"] == str(result_file_id)
        assert downloaded.headers["cache-control"] == "no-store, max-age=0"

    with Session(api_engine) as db:
        assert db.scalar(select(FileJob.download_count).where(FileJob.id == job_id)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "inventory_report_download_intent",
            AuditEvent.aggregate_id == str(job_id),
        )) == 1
    assert stock_facts() == before_stock
    return {
        "formalOpeningRevalidated": True,
        "requestHttpAndIdempotentReplay": True,
        "originalKeyRecoveryWithoutWrite": True,
        "workerCreatedBoundPrivateWorkbook": True,
        "statusAndDownloadHttp": True,
        "stockFactsUntouchedByReport": True,
        "syntheticStorageOnly": True,
    }
