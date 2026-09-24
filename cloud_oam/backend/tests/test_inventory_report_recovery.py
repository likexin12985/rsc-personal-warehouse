"""A lost export response can be reread without replaying its POST."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.foundation_models import FileJob
from app.formal_services import inventory_report_download
from app.formal_services.inventory_report_jobs import report_request_key
from app.formal_services.inventory_query import InventoryReadError


def test_recovery_uses_original_key_and_exact_requester(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[FileJob.__table__])
    owner = "owner-user"
    key = "inventory-report-original-key-12345"
    job = FileJob(
        id=uuid4(), job_type="export", requested_by=owner,
        parameters_jsonb={"report": "inventory_balances", "filters": {}},
        parameters_hash="a" * 64, idempotency_key=report_request_key(owner, key),
        status="queued", export_authorization_version=1,
        export_scope_jsonb={"version": 1, "account_ids": [], "assignment_ids": []},
        export_ledger_cursor=0,
    )
    calls = []

    def checked_status(db, *, actor, job_id):
        calls.append((actor.user_id, job_id))
        return SimpleNamespace(job_id=job_id, status="queued")

    monkeypatch.setattr(inventory_report_download, "get_inventory_report_job_status", checked_status)
    try:
        with Session(engine) as db:
            db.add(job)
            db.commit()
            actor = SimpleNamespace(user_id=owner)
            assert inventory_report_download.recover_inventory_report_job_status(
                db, actor=actor, idempotency_key=key,
            ).job_id == job.id
            assert calls == [(owner, job.id)]
            for other_actor, other_key, expected_code in (
                (SimpleNamespace(user_id="another-user"), key, "inventory_report_job_not_found"),
                (actor, "inventory-report-different-key-12345", "inventory_report_job_not_found"),
                (actor, "", "inventory_report_idempotency_invalid"),
            ):
                with pytest.raises(InventoryReadError) as error:
                    inventory_report_download.recover_inventory_report_job_status(
                        db, actor=other_actor, idempotency_key=other_key,
                    )
                assert error.value.code == expected_code
            assert calls == [(owner, job.id)]
    finally:
        engine.dispose()
