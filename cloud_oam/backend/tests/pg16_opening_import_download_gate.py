"""Real API-role grants; only HTTP identity and OSS signing are synthetic."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import AuditEvent, FileJob
from app.formal_services import formal_files
from app.formal_services.file_storage import DownloadIntent
from app.formal_services.opening_count_import_download import create_opening_count_error_download
from app.formal_services.opening_count_import_jobs import OpeningCountImportJobError
from app.routers import formal_opening_imports as http


def exercise_import_error_download(api, *, actor, other_actor_id, job_id, artifact):
    action = "opening_count_import_error_download_granted"
    cases = []

    class PrivateSigner:
        provider_code = "aliyun_oss_v2"

        def __init__(self):
            self.calls = []
            self.mode = "valid"

        def create_download_intent(self, **arguments):
            assert arguments == {"storage_key": artifact.storage_key, "ttl_seconds": 60}
            self.calls.append(arguments)
            return DownloadIntent(
                artifact.storage_key if self.mode != "wrong-key" else "unrelated-private-file",
                "https://synthetic.example.invalid/error?signature=synthetic" if self.mode != "http" else
                "http://synthetic.example.invalid/error?signature=synthetic",
                datetime.now(timezone.utc) + timedelta(seconds=600 if self.mode == "long-ttl" else 60))

    signer = PrivateSigner()
    arguments = dict(actor=actor, job_id=job_id, request_id="pg16-error-download",
                     storage=signer, ttl_seconds=60)

    def audit_rows():
        with Session(api) as db:
            return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action,
                AuditEvent.aggregate_id == str(job_id))))

    with Session(api) as db:
        other = load_formal_principal(db, other_actor_id)
        original = db.get(FileJob, job_id)
        original_facts = (original.status, original.error_file_id, original.import_completion_id)
    for reader, expected in ((other, "opening_import_job_not_found"),
                             (replace(actor, authorization_version=actor.authorization_version + 1),
                              "opening_import_actor_stale")):
        with Session(api) as db:
            try:
                create_opening_count_error_download(db, **{**arguments, "actor": reader})
            except OpeningCountImportJobError as error:
                assert error.code == expected
            else:
                raise AssertionError("noncurrent or unrelated requester received private URL")
        cases.append("error-download-" + expected)
    assert not signer.calls and not audit_rows()

    # Change the real authoritative user row, not only the supplied principal.
    # The deliberate revocation is local to this transaction and rolled back.
    with Session(api) as db:
        db.execute(text("UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id"),
                   {"id": actor.user_id})
        try:
            create_opening_count_error_download(db, **arguments)
        except OpeningCountImportJobError as error:
            assert error.code == "opening_import_actor_stale"
        else:
            raise AssertionError("live authorization revocation did not block signing")
        db.rollback()
    assert not signer.calls and not audit_rows()
    cases.append("error-download-live-authority-revocation-no-sign")

    with Session(api) as db:
        try:
            formal_files.create_file_download_intent(db, actor=actor, file_id=artifact.file_id,
                trace_request_id="pg16-error-generic", storage=signer, download_ttl_seconds=60)
        except formal_files.FormalFileError as error:
            assert error.code == "file_purpose_forbidden"
        else:
            raise AssertionError("generic file route bypassed import scope binding")
    assert not signer.calls
    cases.append("error-download-generic-file-bypass-refused")

    for mode in ("wrong-key", "http", "long-ttl"):
        signer.mode = mode
        with Session(api) as db:
            try:
                create_opening_count_error_download(db, **arguments)
            except formal_files.FormalFileError as error:
                assert error.code == "file_storage_response_invalid"
            else:
                raise AssertionError("invalid signed download response accepted")
        assert not audit_rows()
        cases.append("error-download-" + mode + "-refused")
    signer.mode = "valid"
    with Session(api) as db:
        result = create_opening_count_error_download(db, **arguments)
        assert result.file_id == artifact.file_id and result.sha256 == artifact.sha256
        db.rollback()
    assert not audit_rows()
    cases.append("error-download-grant-audit-rolls-back")

    app = FastAPI()
    app.include_router(http.router, prefix="/api")
    app.dependency_overrides[get_formal_principal] = lambda: actor
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        opening_count_import_enabled=True, database_url="postgresql+psycopg://synthetic",
        file_storage_configuration_ready=lambda: True, file_download_intent_ttl_seconds=60)
    app.dependency_overrides[http.get_formal_file_storage_adapter] = lambda: signer
    app.dependency_overrides[http.get_import_session_factory] = lambda: lambda: Session(api)
    endpoint = f"/api/v1/stocktakes/opening/imports/opening-count/jobs/{job_id}/error-download-intents"
    with TestClient(app) as client:
        response = client.post(endpoint, json={}, headers={"X-Request-ID": "pg16-error-http"})
        assert response.status_code == 200, response.text
        assert response.json()["file_id"] == str(artifact.file_id)
        assert response.json()["download"]["url"].startswith("https://")
        assert "storage_key" not in response.text
        assert "no-store" in response.headers["cache-control"]
        assert response.headers["referrer-policy"] == "no-referrer"
        audits = audit_rows()
        assert len(audits) == 1 and "signature" not in str(audits[0].after_jsonb)
        cases.append("http-error-download-committed-private-grant")
        signed_count = len(signer.calls)
        app.dependency_overrides[get_formal_principal] = lambda: other
        forbidden = client.post(endpoint, json={}, headers={"X-Request-ID": "pg16-error-other"})
        assert forbidden.status_code == 404 and len(signer.calls) == signed_count
        app.dependency_overrides[get_formal_principal] = lambda: actor
        injected = client.post(endpoint, json={"file_id": str(uuid4())},
                               headers={"X-Request-ID": "pg16-error-injected"})
        assert injected.status_code == 422 and len(signer.calls) == signed_count
        cases.append("http-error-download-unrelated-and-injected-file-refused")

        class CommitFailure(Session):
            def commit(self):
                raise RuntimeError("synthetic failure before grant audit commit")

        app.dependency_overrides[http.get_import_session_factory] = lambda: lambda: CommitFailure(api)
        failed = client.post(endpoint, json={}, headers={"X-Request-ID": "pg16-error-commit-fail"})
        assert failed.status_code == 503 and "signature" not in failed.text
        assert len(audit_rows()) == 1
        cases.append("http-error-download-commit-failure-never-releases-url")
    with Session(api) as db:
        current = db.get(FileJob, job_id)
        assert (current.status, current.error_file_id, current.import_completion_id) == original_facts
    cases.append("error-download-preserves-failed-job-and-count-facts")
    return cases
