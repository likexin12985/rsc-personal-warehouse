"""Bounded notification provider dispatcher.

The expansion worker creates queued delivery facts.  This process owns the
next boundary: claim one delivery lease, call an explicitly configured
provider adapter, and record exactly one provider result.  Provider adapters
are injected at process start; this module never guesses credentials or makes
an external request when an adapter is absent.

An adapter timeout is recorded as an unknown outcome.  Unknown outcomes clear
the lease but are not retryable, because the provider may already have
accepted the message.  A later operator/provider reconciliation must resolve
that delivery explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol


class NotificationProviderError(RuntimeError):
    """A provider adapter rejected or could not determine a send result."""

    def __init__(self, message: str, *, response_code: str | None = None, uncertain: bool = False):
        super().__init__(message)
        self.response_code = response_code
        self.uncertain = uncertain


@dataclass(frozen=True)
class ProviderResult:
    """Sanitized evidence returned by a provider adapter."""

    response_code: str | None
    response_json: dict[str, Any] | None
    provider_message_id: str | None
    error: str | None = None
    uncertain: bool = False

    @classmethod
    def accepted(
        cls,
        *,
        response_code: str,
        provider_message_id: str,
        response_json: dict[str, Any] | None = None,
    ) -> "ProviderResult":
        return cls(
            response_code=response_code,
            response_json=response_json,
            provider_message_id=provider_message_id,
        )

    @classmethod
    def rejected(
        cls,
        *,
        error: str,
        response_code: str | None = None,
        response_json: dict[str, Any] | None = None,
    ) -> "ProviderResult":
        return cls(
            response_code=response_code,
            response_json=response_json,
            provider_message_id=None,
            error=error,
        )

    @classmethod
    def unknown(cls, *, error: str, response_json: dict[str, Any] | None = None) -> "ProviderResult":
        return cls(
            response_code=None,
            response_json={"outcome": "unknown", **(response_json or {})},
            provider_message_id=None,
            error=error,
            uncertain=True,
        )


class NotificationProvider(Protocol):
    """Minimal channel adapter contract.

    Implementations must not return access tokens or recipient secrets in
    ``response_json``.  They may raise :class:`NotificationProviderError` for
    a rejected request or an uncertain transport result.
    """

    def send(
        self,
        *,
        channel: str,
        recipient_key: str,
        payload: Mapping[str, Any],
    ) -> ProviderResult: ...


Adapter = Callable[..., ProviderResult]


def notification_request_hash(claim: Any) -> str:
    """Hash only the exact outbound request coordinates and payload."""

    material = {
        "channel": claim.channel,
        "recipient_key": claim.recipient_key,
        "payload": claim.payload,
        "attempt_no": claim.attempt_no,
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _adapter_result(adapter: NotificationProvider | Adapter, claim: Any) -> ProviderResult:
    try:
        sender = getattr(adapter, "send", adapter)
        result = sender(
            channel=claim.channel,
            recipient_key=claim.recipient_key,
            payload=claim.payload,
        )
    except NotificationProviderError as exc:
        if exc.uncertain:
            return ProviderResult.unknown(error=str(exc))
        return ProviderResult.rejected(error=str(exc), response_code=exc.response_code)
    except (TimeoutError, ConnectionError) as exc:
        return ProviderResult.unknown(error=f"provider transport outcome unknown: {type(exc).__name__}")
    except Exception:
        # Do not expose provider credentials, URLs, or response bodies in the
        # durable attempt record.
        return ProviderResult.unknown(error="provider adapter failed with unknown outcome")

    if not isinstance(result, ProviderResult):
        return ProviderResult.unknown(error="provider adapter returned an invalid result")
    if result.provider_message_id is not None and (
        not isinstance(result.provider_message_id, str)
        or not result.provider_message_id.strip()
    ):
        return ProviderResult.unknown(error="provider adapter returned an invalid message id")
    if result.error is not None and (
        not isinstance(result.error, str) or not result.error.strip()
    ):
        return ProviderResult.unknown(error="provider adapter returned an invalid error")
    if result.provider_message_id is not None and result.error is not None:
        return ProviderResult.unknown(error="provider adapter returned conflicting outcome")
    if result.provider_message_id is not None and (
        not isinstance(result.response_code, str) or not result.response_code.strip()
    ):
        return ProviderResult.unknown(error="provider adapter omitted response code")
    if result.error is None and result.provider_message_id is None:
        return ProviderResult.unknown(error="provider adapter omitted delivery outcome")
    if result.response_json is not None and not isinstance(result.response_json, dict):
        return ProviderResult.unknown(error="provider adapter returned invalid response evidence")
    return result


@dataclass(frozen=True)
class DispatchBatchResult:
    claimed: int
    sent: int
    failed: int
    unknown: int
    errors: tuple[str, ...] = ()


def dispatch_notification_batch(
    session_factory: Callable[[], Any],
    *,
    worker_id: str,
    adapters: Mapping[str, NotificationProvider],
    limit: int = 50,
    now: datetime | None = None,
) -> DispatchBatchResult:
    """Claim and dispatch one bounded batch with short DB transactions.

    Claims are committed before any provider call.  Each result is recorded in
    a fresh transaction, so a slow provider cannot hold row locks and a
    provider result cannot be rolled back with an unrelated later delivery.
    """

    from .formal_services.notification_delivery import (
        NotificationDeliveryError,
        claim_notification_deliveries,
        record_notification_delivery_result,
    )

    claim_session = session_factory()
    try:
        claims = claim_notification_deliveries(
            claim_session, worker_id=worker_id, limit=limit, now=now
        )
        claim_session.commit()
    except Exception:
        claim_session.rollback()
        raise
    finally:
        claim_session.close()

    sent = failed = unknown = 0
    errors: list[str] = []
    for claim in claims:
        adapter = adapters.get(claim.channel)
        if adapter is None:
            result = ProviderResult.rejected(error=f"provider adapter is not configured for {claim.channel}")
        else:
            result = _adapter_result(adapter, claim)
        if result.uncertain:
            unknown += 1
        elif result.provider_message_id is not None and result.error is None:
            sent += 1
        else:
            failed += 1

        result_session = session_factory()
        try:
            record_notification_delivery_result(
                result_session,
                delivery_id=claim.delivery_id,
                worker_id=worker_id,
                request_hash=notification_request_hash(claim),
                response_code=result.response_code,
                response_json=result.response_json,
                provider_message_id=result.provider_message_id,
                error=result.error or ("provider outcome unknown" if result.uncertain else None),
                now=now,
            )
            result_session.commit()
        except NotificationDeliveryError as exc:
            result_session.rollback()
            errors.append(str(exc))
        except Exception:
            result_session.rollback()
            errors.append("notification delivery result could not be recorded")
        finally:
            result_session.close()

    return DispatchBatchResult(
        claimed=len(claims), sent=sent, failed=failed, unknown=unknown, errors=tuple(errors)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dispatch queued notification deliveries")
    parser.add_argument("--once", action="store_true", help="dispatch one bounded batch and exit")
    parser.add_argument("--limit", type=int, default=50, help="maximum deliveries per batch")
    parser.add_argument("--poll-seconds", type=int, default=30, help="bounded idle polling interval")
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("OAM_NOTIFICATION_WORKER_ID", "notification-dispatcher"),
        help="stable worker identity used by the delivery lease",
    )
    return parser


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), flush=True)


def _configured_adapters() -> Mapping[str, NotificationProvider]:
    """Return adapters registered by the deployment package.

    The base application intentionally ships no provider credentials.  A
    deployment may replace this function or monkeypatch the registry with
    audited channel adapters; an empty mapping fails closed per delivery.
    """

    module_name = os.environ.get("OAM_NOTIFICATION_ADAPTER_MODULE", "").strip()
    if not module_name:
        return {}
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise NotificationProviderError("notification provider adapter module could not be loaded") from exc
    factory = getattr(module, "get_notification_adapters", None)
    adapters = factory() if callable(factory) else getattr(module, "NOTIFICATION_ADAPTERS", None)
    if not isinstance(adapters, Mapping):
        raise NotificationProviderError("notification provider adapter registry is invalid")
    allowed = {"wechat", "sms", "feishu"}
    if any(channel not in allowed for channel in adapters):
        raise NotificationProviderError("notification provider channel is invalid")
    return dict(adapters)


def _run_once(*, worker_id: str, limit: int, adapters: Mapping[str, NotificationProvider]) -> tuple[int, dict[str, Any]]:
    from .database import SessionLocal

    try:
        result = dispatch_notification_batch(
            SessionLocal,
            worker_id=worker_id,
            adapters=adapters,
            limit=limit,
            now=datetime.now(timezone.utc),
        )
    except Exception:
        return 2, {"ok": False, "code": "notification_dispatch_failed", "message": "通知投递 worker 发生未分类故障"}
    payload = {
        "ok": not result.errors,
        "claimed": result.claimed,
        "sent": result.sent,
        "failed": result.failed,
        "unknown": result.unknown,
        "errors": list(result.errors),
    }
    return (0 if not result.errors else 2), payload


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.limit <= 100:
        _emit({"ok": False, "code": "notification_dispatch_limit_invalid", "message": "limit必须在1到100之间"})
        return 2
    if not 5 <= args.poll_seconds <= 300:
        _emit({"ok": False, "code": "notification_dispatch_poll_invalid", "message": "poll-seconds必须在5到300秒之间"})
        return 2
    if not isinstance(args.worker_id, str) or not args.worker_id.strip() or len(args.worker_id.strip()) > 160:
        _emit({"ok": False, "code": "notification_dispatch_worker_invalid", "message": "worker-id无效"})
        return 2

    try:
        adapters = _configured_adapters()
    except NotificationProviderError as exc:
        _emit({"ok": False, "code": "notification_provider_configuration_invalid", "message": str(exc)})
        return 2
    if not adapters:
        _emit({"ok": False, "code": "notification_provider_not_configured", "message": "未配置经过审核的通知供应商适配器"})
        return 2

    if args.once:
        exit_code, payload = _run_once(worker_id=args.worker_id.strip(), limit=args.limit, adapters=adapters)
        _emit(payload)
        return exit_code

    stopping = False

    def stop(_signal_number: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        exit_code, payload = _run_once(worker_id=args.worker_id.strip(), limit=args.limit, adapters=adapters)
        if exit_code != 0:
            _emit(payload)
            return exit_code
        if payload["claimed"]:
            _emit(payload)
        else:
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
