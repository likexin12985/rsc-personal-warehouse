from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.foundation_models import NotificationDelivery, NotificationRecipient
from app.notification_dispatcher import (
    NotificationProviderError,
    ProviderResult,
    dispatch_notification_batch,
    notification_request_hash,
)

from test_material_request_approval_service import approval_db
from test_notification_delivery import _queued_delivery


NOW = datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc)


def _session_factory(db):
    db.commit()
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)


class _Provider:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    def send(self, *, channel, recipient_key, payload):
        self.calls.append((channel, recipient_key, payload))
        if self.error:
            raise self.error
        return self.result


def test_dispatch_commits_claim_before_provider_and_records_success(approval_db):
    delivery = _queued_delivery(approval_db)
    recipient = approval_db.get(NotificationRecipient, delivery.recipient_id)
    assert recipient is not None
    recipient_key = recipient.recipient_key
    provider = _Provider(ProviderResult.accepted(response_code="200", provider_message_id="wx-1"))
    factory = _session_factory(approval_db)

    result = dispatch_notification_batch(
        factory,
        worker_id="worker-a",
        adapters={"feishu": provider},
        limit=10,
        now=NOW,
    )

    assert result.claimed == 1
    assert result.sent == 1
    assert result.failed == 0
    assert result.unknown == 0
    assert provider.calls == [("feishu", recipient_key, {"tracking_no": "TEST-002", "quantity": "1.000"})]
    with factory() as check:
        stored = check.get(NotificationDelivery, delivery.id)
        assert stored is not None
        assert stored.status == "sent"
        assert stored.provider_message_id == "wx-1"


def test_missing_adapter_fails_closed_without_provider_call(approval_db):
    delivery = _queued_delivery(approval_db)
    factory = _session_factory(approval_db)

    result = dispatch_notification_batch(
        factory,
        worker_id="worker-a",
        adapters={},
        now=NOW,
    )

    assert result.claimed == 1
    assert result.sent == 0
    assert result.failed == 1
    assert result.unknown == 0
    with factory() as check:
        stored = check.get(NotificationDelivery, delivery.id)
        assert stored is not None
        assert stored.status == "failed"
        assert "not configured" in stored.last_error


def test_timeout_is_unknown_and_not_retryable(approval_db):
    delivery = _queued_delivery(approval_db)
    provider = _Provider(error=NotificationProviderError("timeout", uncertain=True))
    factory = _session_factory(approval_db)

    result = dispatch_notification_batch(
        factory,
        worker_id="worker-a",
        adapters={"feishu": provider},
        now=NOW,
    )

    assert result.unknown == 1
    with factory() as check:
        stored = check.get(NotificationDelivery, delivery.id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.last_error == "timeout"


def test_invalid_provider_result_clears_lease_as_unknown(approval_db):
    delivery = _queued_delivery(approval_db)
    provider = _Provider(object())
    factory = _session_factory(approval_db)

    result = dispatch_notification_batch(
        factory,
        worker_id="worker-a",
        adapters={"feishu": provider},
        now=NOW,
    )

    assert result.unknown == 1
    with factory() as check:
        stored = check.get(NotificationDelivery, delivery.id)
        assert stored is not None
        assert stored.locked_by is None
        assert stored.last_error == "provider adapter returned an invalid result"


def test_request_hash_is_stable_and_sha256():
    class Claim:
        channel = "sms"
        recipient_key = "13800000000"
        payload = {"b": 2, "a": 1}
        attempt_no = 1

    value = notification_request_hash(Claim())
    assert len(value) == 64
    assert value == notification_request_hash(Claim())
    int(value, 16)


def test_cli_rejects_unconfigured_provider_before_database(monkeypatch, capsys):
    from app import notification_dispatcher as worker

    monkeypatch.setattr(worker, "_configured_adapters", lambda: {})
    assert worker.main(["--once"]) == 2
    assert "notification_provider_not_configured" in capsys.readouterr().out
