from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

from app import oam_receipt_projector as projector
from app.formal_services.oam_receipt_projection import OamReceiptProjectionError


def test_worker_idle_does_not_open_publish_transaction(monkeypatch):
    events: list[object] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback(self):
            events.append("rollback")

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    monkeypatch.setattr(projector, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        projector,
        "next_unpublished_oam_receipt_snapshot_id",
        lambda _db: events.append("queue-query") or None,
    )
    monkeypatch.setattr(projector, "_emit", events.append)

    assert projector._process_one(None) == (False, 0)
    assert events == ["queue-query"]


def test_worker_publishes_one_snapshot_and_emits_receipt_result(monkeypatch):
    events: list[object] = []
    monkeypatch.setattr(projector, "_production_boundary_error", lambda: None)
    result = SimpleNamespace(
        snapshot_id="snapshot-1",
        sync_run_id=uuid.uuid4(),
        projected_records=2,
        duplicate_records=1,
        duplicate=False,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    monkeypatch.setattr(projector, "engine", SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    monkeypatch.setattr(projector, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        projector,
        "_select_snapshot_and_coordinates",
        lambda _snapshot_id: ("snapshot-1", None),
    )
    monkeypatch.setattr(projector, "_receipt_source", lambda _db: "source")
    monkeypatch.setattr(
        projector,
        "publish_completed_oam_receipt_snapshot",
        lambda _db, *, snapshot_id, source: events.append(("publish", snapshot_id, source)) or result,
    )
    monkeypatch.setattr(projector, "_emit", events.append)

    assert projector._process_one("snapshot-1") == (True, 0)
    assert ("publish", "snapshot-1", "source") in events
    assert "commit" in events
    payload = events[-1]
    assert payload["ok"] is True
    assert payload["projectedRecords"] == 2
    assert payload["duplicateRecords"] == 1


def test_worker_quarantines_deterministic_projection_error(monkeypatch):
    events: list[object] = []
    monkeypatch.setattr(projector, "_production_boundary_error", lambda: None)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    failed_id = uuid.uuid4()
    monkeypatch.setattr(projector, "engine", SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    monkeypatch.setattr(projector, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        projector,
        "_select_snapshot_and_coordinates",
        lambda _snapshot_id: ("snapshot-1", None),
    )
    monkeypatch.setattr(projector, "_receipt_source", lambda _db: "source")

    def fail_publish(*_args, **_kwargs):
        raise OamReceiptProjectionError("oam_receipt_manifest_invalid", "invalid")

    monkeypatch.setattr(projector, "publish_completed_oam_receipt_snapshot", fail_publish)
    monkeypatch.setattr(
        projector,
        "record_failed_oam_receipt_snapshot",
        lambda _db, *, snapshot_id, failure_code: events.append(
            ("quarantine", snapshot_id, failure_code)
        ) or failed_id,
    )
    monkeypatch.setattr(projector, "_emit", events.append)

    assert projector._process_one("snapshot-1") == (True, 3)
    assert ("quarantine", "snapshot-1", "oam_receipt_manifest_invalid") in events
    assert events[-1]["failedRunId"] == str(failed_id)


def test_worker_production_boundary_fails_closed_before_queue(monkeypatch):
    events: list[object] = []
    monkeypatch.setattr(
        projector,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            database_expected_runtime_role=projector.PROJECTOR_ROLE,
            database_expected_migration_role="star_oam_migrator",
        ),
    )
    monkeypatch.setattr(
        projector,
        "verify_oam_projection_database_boundary",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        projector,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("queue must not be read")),
    )
    monkeypatch.setattr(projector, "_emit", events.append)

    assert projector._process_one(None) == (False, 2)
    assert events == [
        {
            "ok": False,
            "code": "oam_receipt_projection_database_boundary_invalid",
            "message": "生产OAM收货投影器数据库权限边界未完成专用证明",
        }
    ]


def test_invalid_poll_interval_is_rejected(monkeypatch):
    events: list[object] = []
    monkeypatch.setattr(projector, "_emit", events.append)
    assert projector.main(["--once", "--poll-seconds", "1"]) == 2
    assert events[0]["code"] == "oam_receipt_projection_poll_invalid"


def test_postgresql_worker_locks_scope_before_repeatable_read_publish(monkeypatch):
    events: list[object] = []

    class FakeConnection:
        dialect = SimpleNamespace(name="postgresql")

        def __enter__(self):
            events.append("connection-enter")
            return self

        def __exit__(self, *_args):
            events.append("connection-exit")

        def commit(self):
            events.append("connection-commit")

        def rollback(self):
            events.append("connection-rollback")

        def in_transaction(self):
            return False

        def execution_options(self, **options):
            events.append(("connection-isolation", options))
            return self

        def invalidate(self):
            events.append("connection-invalidate")

    connection = FakeConnection()

    class FakeEngine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return connection

    class ProcessingSession:
        def __enter__(self):
            events.append("processing-enter")
            return self

        def __exit__(self, *_args):
            events.append("processing-exit")

        def get_bind(self):
            return connection

        def connection(self, *, execution_options):
            events.append(("worker-isolation", execution_options))
            return connection

        def commit(self):
            events.append("processing-commit")

        def rollback(self):
            events.append("processing-rollback")

    result = SimpleNamespace(
        snapshot_id="snapshot-1",
        sync_run_id=uuid.uuid4(),
        projected_records=1,
        duplicate_records=0,
        duplicate=False,
    )
    monkeypatch.setattr(projector, "_production_boundary_error", lambda: None)
    monkeypatch.setattr(projector, "engine", FakeEngine())
    monkeypatch.setattr(projector, "Session", lambda **_kwargs: ProcessingSession())
    monkeypatch.setattr(projector, "_receipt_source", lambda _db: "source")
    monkeypatch.setattr(
        projector,
        "_select_snapshot_and_coordinates",
        lambda _snapshot_id: ("snapshot-1", ("edge-1", "oam-receipts:company-1")),
    )
    monkeypatch.setattr(
        projector,
        "acquire_external_sync_scope_session_lock",
        lambda _connection, **_kwargs: events.append("scope-lock-acquire"),
    )
    monkeypatch.setattr(
        projector,
        "release_external_sync_scope_session_lock",
        lambda _connection, **_kwargs: events.append("scope-lock-release") or True,
    )
    monkeypatch.setattr(
        projector,
        "publish_completed_oam_receipt_snapshot",
        lambda _db, **_kwargs: events.append("publish") or result,
    )
    monkeypatch.setattr(projector, "_emit", lambda payload: events.append(("emit", payload)))

    assert projector._process_one("snapshot-1") == (True, 0)
    assert events.index("scope-lock-acquire") < events.index(
        ("connection-isolation", {"isolation_level": "REPEATABLE READ"})
    )
    assert events.index(
        ("connection-isolation", {"isolation_level": "REPEATABLE READ"})
    ) < events.index("publish")
    assert events.index("publish") < events.index("scope-lock-release")
