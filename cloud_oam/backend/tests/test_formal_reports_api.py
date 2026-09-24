"""HTTP contract for the opt-in private inventory report routes."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services.inventory_report_download import (
    InventoryReportDownload,
    InventoryReportJobStatus,
)
from app.main import app as production_app
from app.routers import formal_reports
from app.routers.formal_files import get_formal_file_storage_adapter


def _client(*, enabled: bool, database: Mock, storage: object) -> TestClient:
    api = FastAPI()
    api.include_router(formal_reports.router, prefix="/api")
    settings = Settings(
        inventory_report_export_enabled=enabled,
        file_storage_enabled=True,
        file_storage_provider="aliyun_oss_v2",
        file_storage_region="cn-shanghai",
        file_storage_bucket="rsc-private-report-tests",
        file_idempotency_hmac_secret="f" * 64,
    )
    api.dependency_overrides[get_settings] = lambda: settings
    api.dependency_overrides[get_db] = lambda: database
    api.dependency_overrides[get_formal_principal] = lambda: SimpleNamespace(user_id="actor")
    api.dependency_overrides[get_formal_file_storage_adapter] = lambda: storage
    return TestClient(api)


def test_report_routes_are_closed_by_default(monkeypatch):
    create = Mock(side_effect=AssertionError("disabled route reached service"))
    monkeypatch.setattr(formal_reports.inventory_report_jobs, "create_inventory_report_job", create)
    database = Mock()
    with _client(enabled=False, database=database, storage=SimpleNamespace(provider_code="aliyun_oss_v2")) as client:
        capability = client.get("/api/v1/reports/inventory-balances/exports/capabilities")
        response = client.post(
            "/api/v1/reports/inventory-balances/exports",
            headers={"Idempotency-Key": "one", "X-Request-ID": "one"},
        )
        recovery = client.get(
            "/api/v1/reports/inventory-balances/exports/recovery",
            headers={"Idempotency-Key": "one"},
        )
    assert response.status_code == 503
    assert capability.status_code == 200 and capability.json() == {"available": False}
    assert capability.headers["cache-control"] == "no-store, max-age=0"
    assert response.json()["detail"]["code"] == "inventory_report_export_disabled"
    assert recovery.status_code == 503
    assert recovery.json()["detail"]["code"] == "inventory_report_export_disabled"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    create.assert_not_called()
    database.commit.assert_not_called()


def test_main_app_report_framework_failures_are_not_cacheable():
    with TestClient(production_app) as client:
        response = client.get("/api/v1/reports/inventory-balances/exports/not-a-uuid")
    assert response.status_code in {401, 422}
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_report_routes_expose_only_private_job_and_short_download(monkeypatch):
    job_id, file_id = uuid4(), uuid4()
    now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        formal_reports.inventory_report_jobs,
        "create_inventory_report_job",
        lambda *args, **kwargs: (SimpleNamespace(id=job_id, status="queued"), False),
    )
    monkeypatch.setattr(
        formal_reports.inventory_report_download,
        "get_inventory_report_job_status",
        lambda *args, **kwargs: InventoryReportJobStatus(
            job_id=job_id, status="succeeded", created_at=now, completed_at=now,
            download_count=0, file_available=True,
        ),
    )
    recover = Mock(return_value=InventoryReportJobStatus(
        job_id=job_id, status="succeeded", created_at=now, completed_at=now,
        download_count=0, file_available=True,
    ))
    monkeypatch.setattr(
        formal_reports.inventory_report_download,
        "recover_inventory_report_job_status", recover,
    )
    monkeypatch.setattr(
        formal_reports.inventory_report_download,
        "create_inventory_report_download_intent",
        lambda *args, **kwargs: InventoryReportDownload(
            job_id=job_id, file_id=file_id,
            url="https://private.example.invalid/report", expires_at=now,
            download_count=1,
        ),
    )
    database = Mock()
    storage = SimpleNamespace(provider_code="aliyun_oss_v2")
    with _client(enabled=True, database=database, storage=storage) as client:
        capability = client.get("/api/v1/reports/inventory-balances/exports/capabilities")
        created = client.post(
            "/api/v1/reports/inventory-balances/exports",
            headers={"Idempotency-Key": "one", "X-Request-ID": "create-one"},
        )
        status = client.get(f"/api/v1/reports/inventory-balances/exports/{job_id}")
        recovered = client.get(
            "/api/v1/reports/inventory-balances/exports/recovery",
            headers={"Idempotency-Key": "one"},
        )
        download = client.post(
            f"/api/v1/reports/inventory-balances/exports/{job_id}/download-intents",
            headers={"X-Request-ID": "download-one"},
        )
    assert created.status_code == 202
    assert capability.status_code == 200 and capability.json() == {"available": True}
    assert created.json() == {"job_id": str(job_id), "status": "queued", "replayed": False}
    assert status.status_code == 200 and status.json()["file_available"] is True
    assert recovered.status_code == 200 and recovered.json()["job_id"] == str(job_id)
    assert recovered.json()["file_available"] is True
    assert recover.call_args.kwargs["idempotency_key"] == "one"
    assert download.status_code == 200 and download.json()["file_id"] == str(file_id)
    for response in (created, status, recovered, download):
        assert response.headers["cache-control"] == "no-store, max-age=0"
        assert response.headers["referrer-policy"] == "no-referrer"
    assert database.commit.call_count == 2
