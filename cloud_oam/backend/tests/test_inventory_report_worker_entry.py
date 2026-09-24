"""A report worker cannot enter storage or database work without deployment config."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

from app import inventory_report_worker
from app.inventory_report_worker import WorkerResult


def test_worker_entry_fails_closed_before_database_or_oss(monkeypatch, capsys):
    monkeypatch.setattr(
        inventory_report_worker,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            database_url="postgresql+psycopg://redacted@localhost/test",
            inventory_report_export_enabled=False,
            file_storage_configuration_ready=lambda: False,
        ),
    )
    monkeypatch.setattr(
        inventory_report_worker,
        "process_one_inventory_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("database touched")),
    )
    assert inventory_report_worker.main([]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "code": "inventory_report_worker_not_configured",
    }


def test_worker_module_reports_missing_database_configuration_without_traceback():
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.update({
        "PYTHONPATH": str(root / "backend"),
        "OAM_ENVIRONMENT": "production",
        "OAM_DATABASE_URL": "",
    })
    process = subprocess.run(
        [sys.executable, "-m", "app.inventory_report_worker"],
        cwd=root, env=environment, capture_output=True, text=True, timeout=15,
    )
    assert process.returncode == 2 and not process.stderr
    assert json.loads(process.stdout) == {
        "ok": False,
        "code": "inventory_report_worker_not_configured",
    }


def test_polling_worker_drains_terminal_jobs_then_waits_when_idle(monkeypatch, capsys):
    job_id = uuid4()
    statuses = iter((
        WorkerResult(job_id=job_id, status="succeeded"),
        WorkerResult(job_id=job_id, status="failed"),
        WorkerResult(job_id=None, status="idle"),
    ))
    monkeypatch.setattr(
        inventory_report_worker,
        "process_one_inventory_report",
        lambda *args, **kwargs: next(statuses),
    )

    class Stop:
        requested = False

        def is_set(self):
            return self.requested

        def wait(self, seconds):
            assert seconds == 5
            self.requested = True

    stop = Stop()
    inventory_report_worker._poll_inventory_reports(
        lambda: None, storage=object(), poll_seconds=5, stop_event=stop,
    )
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["status"] for row in output] == ["succeeded", "failed"]
    assert stop.requested


def test_polling_arguments_reject_out_of_bounds_without_configuration(capsys):
    assert inventory_report_worker.main(["--poll-seconds", "1"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "inventory_report_worker_poll_invalid"


def test_worker_checks_database_boundary_before_storage_or_queue(monkeypatch, capsys):
    from app import database_security
    from app.formal_services import file_storage

    monkeypatch.setattr(inventory_report_worker, "get_settings", lambda: SimpleNamespace(
        environment="production",
        database_url="postgresql+psycopg://redacted@localhost/test",
        database_expected_runtime_role="star_oam_api",
        database_expected_migration_role="star_oam_migrator",
        inventory_report_export_enabled=True,
        file_storage_configuration_ready=lambda: True,
    ))
    monkeypatch.setattr(
        database_security, "validate_production_database_security",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boundary rejected")),
    )
    monkeypatch.setattr(
        file_storage, "AliyunOssV2StorageAdapter",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("storage touched")),
    )
    assert inventory_report_worker.main([]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "inventory_report_worker_failed"
