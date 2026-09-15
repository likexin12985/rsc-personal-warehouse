"""Bounded worker entrypoint for durable notification delivery expansion.

The expander only turns pending notification facts into queued delivery facts.
It never calls WeChat, SMS, or another provider. Provider workers must claim
those queued rows and record an exact result through ``notification_delivery``.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from typing import Any

# Keep CLI parsing and argument validation independent from the production
# database configuration. The real session factory is imported only when a
# valid run is about to touch the queue.
SessionLocal = None
NotificationExpansionError = RuntimeError
expand_pending_notification_events = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expand durable notification facts into queued deliveries"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process one bounded batch and exit",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum pending events per batch",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="bounded idle polling interval for the long-running worker",
    )
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        flush=True,
    )


def _run_once(*, limit: int) -> tuple[int, dict[str, Any]]:
    global NotificationExpansionError, expand_pending_notification_events
    if expand_pending_notification_events is None:
        from .formal_services.notification_expansion import (
            NotificationExpansionError as expansion_error,
            expand_pending_notification_events as expand_pending,
        )

        NotificationExpansionError = expansion_error
        expand_pending_notification_events = expand_pending
    session_factory = SessionLocal
    if session_factory is None:
        from .database import SessionLocal as session_factory

    with session_factory() as db:
        try:
            results = expand_pending_notification_events(db, limit=limit)
            db.commit()
        except NotificationExpansionError as exc:
            db.rollback()
            return 2, {
                "ok": False,
                "code": "notification_expansion_invalid",
                "message": str(exc),
            }
        except Exception:
            db.rollback()
            return 2, {
                "ok": False,
                "code": "notification_expansion_failed",
                "message": "通知投递扩展发生未分类故障",
            }
    return 0, {
        "ok": True,
        "processed": len(results),
        "createdDeliveries": sum(item.created_delivery_count for item in results),
        "eventIds": [str(item.event_id) for item in results],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.limit <= 500:
        _emit({
            "ok": False,
            "code": "notification_expansion_limit_invalid",
            "message": "limit必须在1到500之间",
        })
        return 2
    if not 5 <= args.poll_seconds <= 300:
        _emit({
            "ok": False,
            "code": "notification_expansion_poll_invalid",
            "message": "poll-seconds必须在5到300秒之间",
        })
        return 2

    if args.once:
        exit_code, payload = _run_once(limit=args.limit)
        _emit(payload)
        return exit_code

    stopping = False

    def stop(_signal_number, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        exit_code, payload = _run_once(limit=args.limit)
        if exit_code != 0:
            _emit(payload)
            return exit_code
        if payload["processed"]:
            _emit(payload)
        else:
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
