"""Isolated process entrypoint for formal OAM receipt evidence projection.

The worker consumes only a completed cloud mirror.  It never authenticates to
or writes OAM, and it never creates local receipt or personal-warehouse facts.
The production boundary remains fail-closed until the receipt-specific RLS/ACL
graph is installed and verified.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from typing import Any

from sqlalchemy.orm import Session

from .config import get_settings
from .database import SessionLocal, engine
from .external_sync_scope_lock import (
    acquire_external_sync_scope_session_lock,
    release_external_sync_scope_session_lock,
)
from .formal_services.oam_receipt_projection import (
    OamReceiptProjectionError,
    next_unpublished_oam_receipt_snapshot_id,
    oam_receipt_snapshot_lock_coordinates,
    publish_completed_oam_receipt_snapshot,
    record_failed_oam_receipt_snapshot,
)
from .oam_projection_security import PROJECTOR_ROLE
from .oam_receipt_projection_security import (
    verify_oam_receipt_projection_database_boundary,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish completed OAM receipt snapshots into immutable evidence"
    )
    parser.add_argument(
        "--snapshot-id",
        default="",
        help="exact internal staging snapshot row ID; required for conflict retry",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process at most one pending snapshot and exit",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="bounded idle polling interval for the long-running worker",
    )
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), flush=True)


def _begin_worker_transaction(db: Session) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})


def _run_selected_snapshot(
    db: Session,
    *,
    selected: str,
) -> tuple[int, dict[str, Any]]:
    _begin_worker_transaction(db)
    try:
        result = publish_completed_oam_receipt_snapshot(
            db,
            snapshot_id=selected,
            source=_receipt_source(db),
        )
        db.commit()
    except OamReceiptProjectionError as exc:
        db.rollback()
        failed_run_id = None
        try:
            _begin_worker_transaction(db)
            failed_run_id = record_failed_oam_receipt_snapshot(
                db,
                snapshot_id=selected,
                failure_code=exc.code,
            )
            if failed_run_id is not None:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
        return (
            3 if failed_run_id is not None else 2,
            {
                "ok": False,
                "code": exc.code,
                "message": exc.message,
                "snapshotId": selected,
                "failedRunId": str(failed_run_id) if failed_run_id else None,
            },
        )
    except Exception:
        db.rollback()
        return (
            2,
            {
                "ok": False,
                "code": "oam_receipt_projection_unexpected_failure",
                "message": "OAM收货证据投影发生未分类故障",
                "snapshotId": selected,
                "failedRunId": None,
            },
        )
    return (
        0,
        {
            "ok": True,
            "snapshotId": result.snapshot_id,
            "syncRunId": str(result.sync_run_id),
            "projectedRecords": result.projected_records,
            "duplicateRecords": result.duplicate_records,
            "duplicate": result.duplicate,
        },
    )


def _receipt_source(db: Session):
    """Load the exact source row; the service validates its read-only state."""

    from sqlalchemy import select

    from .foundation_models import SourceSystem

    source = db.scalar(select(SourceSystem).where(SourceSystem.code == "starcharge_oam"))
    if source is None:
        raise OamReceiptProjectionError(
            "oam_receipt_source_invalid", "OAM来源系统不存在"
        )
    return source


def _select_snapshot_and_coordinates(
    snapshot_id: str | None,
) -> tuple[str | None, tuple[str, str] | None]:
    with SessionLocal() as db:
        selected = snapshot_id or next_unpublished_oam_receipt_snapshot_id(db)
        if selected is None:
            return None, None
        coordinates = oam_receipt_snapshot_lock_coordinates(db, snapshot_id=selected)
        db.rollback()
        return selected, coordinates


def _production_boundary_error() -> dict[str, Any] | None:
    settings = get_settings()
    if settings.environment != "production":
        return None
    if settings.database_expected_runtime_role != PROJECTOR_ROLE:
        return {
            "ok": False,
            "code": "oam_receipt_projection_role_invalid",
            "message": "生产OAM收货投影器必须使用独立数据库角色",
        }
    try:
        verify_oam_receipt_projection_database_boundary(
            engine,
            expected_role=settings.database_expected_runtime_role,
            expected_migration_role=settings.database_expected_migration_role,
        )
    except Exception:
        return {
            "ok": False,
            "code": "oam_receipt_projection_database_boundary_invalid",
            "message": "生产OAM收货投影器数据库权限边界未完成专用证明",
        }


def _process_one(snapshot_id: str | None) -> tuple[bool, int]:
    boundary_error = _production_boundary_error()
    if boundary_error is not None:
        _emit(boundary_error)
        return snapshot_id is not None, 2
    try:
        selected, coordinates = _select_snapshot_and_coordinates(snapshot_id)
    except Exception:
        _emit(
            {
                "ok": False,
                "code": "oam_receipt_projection_queue_failed",
                "message": "OAM收货投影待处理队列读取失败",
                "snapshotId": snapshot_id,
            }
        )
        return snapshot_id is not None, 2
    if selected is None:
        return False, 0
    if engine.dialect.name != "postgresql":
        with SessionLocal() as db:
            exit_code, payload = _run_selected_snapshot(db, selected=selected)
        _emit(payload)
        return True, exit_code
    if coordinates is None:
        _emit(
            {
                "ok": False,
                "code": "oam_receipt_snapshot_not_found",
                "message": "OAM收货暂存快照不存在",
                "snapshotId": selected,
                "failedRunId": None,
            }
        )
        return True, 2

    source_instance, scope_key = coordinates
    output: tuple[int, dict[str, Any]] | None = None
    lock_acquired = False
    lock_cleaned = False
    try:
        with engine.connect() as connection:
            try:
                acquire_external_sync_scope_session_lock(
                    connection,
                    source_instance=source_instance,
                    scope_key=scope_key,
                )
                lock_acquired = True
                connection.commit()
                connection.execution_options(isolation_level="REPEATABLE READ")
                with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
                    output = _run_selected_snapshot(db, selected=selected)
            finally:
                if connection.in_transaction():
                    connection.rollback()
                if lock_acquired:
                    try:
                        lock_cleaned = release_external_sync_scope_session_lock(
                            connection,
                            source_instance=source_instance,
                            scope_key=scope_key,
                        )
                        connection.commit()
                    except Exception:
                        lock_cleaned = False
                    if not lock_cleaned:
                        connection.invalidate()
    except Exception:
        _emit(
            {
                "ok": False,
                "code": "oam_receipt_projection_scope_lock_failed",
                "message": "OAM收货投影范围锁获取或释放失败",
                "snapshotId": selected,
            }
        )
        return True, 2
    if not lock_acquired or not lock_cleaned or output is None:
        _emit(
            {
                "ok": False,
                "code": "oam_receipt_projection_scope_lock_failed",
                "message": "OAM收货投影范围锁获取或释放失败",
                "snapshotId": selected,
            }
        )
        return True, 2
    exit_code, payload = output
    _emit(payload)
    return True, exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 5 <= args.poll_seconds <= 300:
        _emit(
            {
                "ok": False,
                "code": "oam_receipt_projection_poll_invalid",
                "message": "poll-seconds必须在5到300秒之间",
            }
        )
        return 2
    boundary_error = _production_boundary_error()
    if boundary_error is not None:
        _emit(boundary_error)
        return 2
    if args.snapshot_id:
        _, exit_code = _process_one(args.snapshot_id)
        return exit_code
    if args.once:
        processed, exit_code = _process_one(None)
        if not processed:
            _emit({"ok": True, "status": "idle", "processed": False})
        return exit_code
    stopping = False

    def stop(_signal_number, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        processed, exit_code = _process_one(None)
        if exit_code == 2:
            return exit_code
        if exit_code == 3:
            continue
        if not processed:
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
