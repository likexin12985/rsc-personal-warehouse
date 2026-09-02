from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.database import Base
from app.foundation_models import (
    ExternalObject,
    SourceSystem,
    SyncConflict,
    SyncRun,
)
from app.models import ExternalSyncSnapshot
from app.routers import integrations


NOW = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _snapshot(
    *,
    source_instance: str,
    scope_key: str,
    snapshot_id: str,
    snapshot_at: datetime,
    completed_at: datetime | None,
    status: str = "complete",
    sync_mode: str = "incremental",
) -> ExternalSyncSnapshot:
    return ExternalSyncSnapshot(
        source_system="starcharge_oam",
        source_instance=source_instance,
        snapshot_id=snapshot_id,
        scope_key=scope_key,
        sync_mode=sync_mode,
        company_id="company-nio",
        org_code="org-nio",
        snapshot_at=snapshot_at,
        status=status,
        manifest_json="{}",
        manifest_sha256="a" * 64,
        received_at=snapshot_at + timedelta(seconds=10),
        completed_at=completed_at,
    )


def _source(db: Session) -> SourceSystem:
    source = SourceSystem(
        code="starcharge_oam",
        name="StarCharge OAM",
        mode="read_only",
        enabled=True,
        configuration_jsonb={},
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
    )
    db.add(source)
    db.flush()
    return source


def _run(
    db: Session,
    source: SourceSystem,
    *,
    suffix: str,
    status: str,
    started_at: datetime,
    completed_at: datetime | None,
) -> SyncRun:
    run = SyncRun(
        source_system_id=source.id,
        run_key=f"oam-work-order:{suffix}",
        scope_key=f"oam-work-order-scope:{suffix}",
        mode="incremental",
        watermark_from=None,
        watermark_to=None,
        status=status,
        manifest_sha256="b" * 64,
        started_at=started_at,
        completed_at=completed_at,
        failure_code=("projection_failed" if status == "failed" else None),
        failure_detail=None,
        created_at=started_at,
        updated_at=completed_at or started_at,
    )
    db.add(run)
    db.flush()
    return run


def test_status_route_remains_admin_only():
    route = next(
        route
        for route in integrations.management_router.routes
        if route.path.endswith("/status")
    )
    role_dependency = next(
        dependency.call
        for dependency in route.dependant.dependencies
        if dependency.name == "_"
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/integrations/oam/edge/status",
            "headers": [],
        }
    )
    admin = SimpleNamespace(role="admin")
    assert role_dependency(request, admin) is admin
    with pytest.raises(HTTPException) as error:
        role_dependency(request, SimpleNamespace(role="engineer"))
    assert error.value.status_code == 403


def test_database_now_is_aware_utc(db: Session):
    before = datetime.now(timezone.utc) - timedelta(seconds=2)
    database_now = integrations._database_utc_now(db)
    after = datetime.now(timezone.utc) + timedelta(seconds=2)

    assert database_now.tzinfo == timezone.utc
    assert before <= database_now <= after


def test_status_reports_latest_complete_per_source_and_scope(
    db: Session,
    monkeypatch,
):
    monkeypatch.setattr(integrations, "_database_utc_now", lambda _: NOW)
    db.add_all(
        [
            _snapshot(
                source_instance="edge-a",
                scope_key="all",
                snapshot_id="edge-a-all-old",
                snapshot_at=NOW - timedelta(minutes=20),
                completed_at=NOW - timedelta(minutes=19),
                sync_mode="full",
            ),
            _snapshot(
                source_instance="edge-a",
                scope_key="all",
                snapshot_id="edge-a-all-latest-complete",
                snapshot_at=NOW - timedelta(minutes=10),
                completed_at=NOW - timedelta(minutes=9),
            ),
            _snapshot(
                source_instance="edge-a",
                scope_key="all",
                snapshot_id="edge-a-all-newer-receiving",
                snapshot_at=NOW - timedelta(minutes=1),
                completed_at=None,
                status="receiving",
            ),
            _snapshot(
                source_instance="edge-a",
                scope_key="work-orders:recent-30d",
                snapshot_id="edge-a-work-orders-stale",
                snapshot_at=NOW - timedelta(minutes=46),
                completed_at=NOW - timedelta(minutes=45, seconds=30),
            ),
            _snapshot(
                source_instance="edge-b",
                scope_key="all",
                snapshot_id="edge-b-all-complete",
                snapshot_at=NOW - timedelta(minutes=5),
                completed_at=NOW - timedelta(minutes=4),
            ),
        ]
    )
    db.flush()

    result = integrations.edge_sync_status(None, db)
    scopes = {
        (item["source_instance"], item["scope_key"]): item
        for item in result["scopes"]
    }

    assert set(scopes) == {
        ("edge-a", "all"),
        ("edge-a", "work-orders:recent-30d"),
        ("edge-b", "all"),
    }
    assert scopes[("edge-a", "all")]["snapshot_id"] == (
        "edge-a-all-latest-complete"
    )
    assert scopes[("edge-a", "all")]["sync_mode"] == "incremental"
    assert scopes[("edge-a", "all")]["snapshot_at"].tzinfo == timezone.utc
    assert scopes[("edge-a", "all")]["completed_at"].tzinfo == timezone.utc
    assert scopes[("edge-a", "all")]["age_seconds"] == 600
    assert scopes[("edge-a", "all")]["completed_age_seconds"] == 540
    assert scopes[("edge-a", "all")]["fresh"] is True
    assert scopes[("edge-a", "all")]["freshness_status"] == "fresh"
    assert scopes[("edge-a", "work-orders:recent-30d")]["age_seconds"] == 2760
    assert scopes[("edge-a", "work-orders:recent-30d")]["fresh"] is False
    assert scopes[("edge-a", "work-orders:recent-30d")]["freshness_status"] == (
        "stale"
    )
    assert scopes[("edge-b", "all")]["fresh"] is True
    assert result["freshness_threshold_seconds"] == 45 * 60
    assert result["database_now"] == NOW
    assert result["last_completed_snapshot"]["snapshot_id"] == (
        "edge-b-all-complete"
    )
    assert result["work_order_projection"]["healthy"] is False
    assert result["healthy"] is False


def test_failed_conflict_or_open_conflict_blocks_projection_health(
    db: Session,
    monkeypatch,
):
    monkeypatch.setattr(integrations, "_database_utc_now", lambda _: NOW)
    db.add(
        _snapshot(
            source_instance="edge-a",
            scope_key="work-orders:recent-30d",
            snapshot_id="edge-a-work-orders-current",
            snapshot_at=NOW - timedelta(minutes=4),
            completed_at=NOW - timedelta(minutes=3, seconds=30),
        )
    )
    source = _source(db)
    failed = _run(
        db,
        source,
        suffix="failed",
        status="failed",
        started_at=NOW - timedelta(minutes=20),
        completed_at=NOW - timedelta(minutes=19),
    )
    conflict = _run(
        db,
        source,
        suffix="conflict",
        status="conflict",
        started_at=NOW - timedelta(minutes=15),
        completed_at=NOW - timedelta(minutes=14),
    )
    completed = _run(
        db,
        source,
        suffix="completed",
        status="completed",
        started_at=NOW - timedelta(minutes=3),
        completed_at=NOW - timedelta(minutes=2),
    )
    external = ExternalObject(
        source_system_id=source.id,
        entity_type="work_order",
        external_id="work-order-conflict",
        current_version_id=None,
        deleted_at=None,
        created_at=NOW - timedelta(minutes=15),
        updated_at=NOW - timedelta(minutes=15),
    )
    db.add(external)
    db.flush()
    open_conflict = SyncConflict(
        run_id=conflict.id,
        inbox_event_id=None,
        external_object_id=external.id,
        dedup_key=f"status-test:{uuid.uuid4().hex}",
        conflict_type="mapping_conflict",
        external_value_jsonb={},
        local_value_jsonb={},
        status="open",
        resolution_jsonb=None,
        resolved_by=None,
        resolved_at=None,
        created_at=NOW - timedelta(minutes=14),
        updated_at=NOW - timedelta(minutes=14),
    )
    db.add(open_conflict)
    db.flush()

    result = integrations.edge_sync_status(None, db)
    projection = result["work_order_projection"]
    assert projection["latest_run"]["run_id"] == str(completed.id)
    assert projection["latest_run"]["status"] == "completed"
    assert projection["latest_run"]["age_seconds"] == 120
    assert projection["freshness_status"] == "fresh"
    assert projection["failed_run_count"] == 1
    assert projection["conflict_run_count"] == 1
    assert projection["open_conflict_count"] == 1
    assert projection["healthy"] is False
    assert result["healthy"] is False

    failed.status = "completed"
    conflict.status = "completed"
    open_conflict.status = "resolved"
    open_conflict.resolution_jsonb = {"reason": "explicit-test-resolution"}
    open_conflict.resolved_at = NOW - timedelta(minutes=1)
    db.flush()

    recovered = integrations.edge_sync_status(None, db)
    assert recovered["work_order_projection"]["failed_run_count"] == 0
    assert recovered["work_order_projection"]["conflict_run_count"] == 0
    assert recovered["work_order_projection"]["open_conflict_count"] == 0
    assert recovered["work_order_projection"]["healthy"] is True
    assert recovered["healthy"] is True


def test_empty_scope_set_fails_closed(db: Session, monkeypatch):
    monkeypatch.setattr(integrations, "_database_utc_now", lambda _: NOW)

    result = integrations.edge_sync_status(None, db)

    assert result["scopes"] == []
    assert result["healthy"] is False
