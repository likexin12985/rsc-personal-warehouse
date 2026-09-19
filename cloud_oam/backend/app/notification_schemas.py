"""Private message-center contracts for the formal notification surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationItemOut(_StrictOutput):
    delivery_id: UUID
    event_id: UUID
    event_type: str = Field(min_length=1, max_length=100)
    business_type: str = Field(min_length=1, max_length=80)
    business_id: str = Field(min_length=1, max_length=64)
    channel: Literal["wechat", "sms", "feishu"]
    status: Literal[
        "queued",
        "sending",
        "sent",
        "delivered",
        "read",
        "failed",
        "cancelled",
    ]
    payload: dict[str, Any]
    occurred_at: AwareDatetime
    created_at: AwareDatetime
    sent_at: AwareDatetime | None
    delivered_at: AwareDatetime | None
    read_at: AwareDatetime | None


class NotificationPageOut(_StrictOutput):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[NotificationItemOut, ...]
    next_after_id: UUID | None
    unread_count: int = Field(ge=0)


class NotificationReadOut(_StrictOutput):
    schema_version: Literal["1.0"] = "1.0"
    item: NotificationItemOut


class NotificationDeliveryRecordOut(_StrictOutput):
    """Redacted operator view of one provider delivery fact.

    ``recipient_key`` is intentionally absent: it may be a phone number,
    WeChat openid, or Feishu chat identifier.  Provider response bodies are
    also excluded; adapters persist only the safe response code/error fields
    exposed below.
    """

    delivery_id: UUID
    event_id: UUID
    event_type: str = Field(min_length=1, max_length=100)
    business_type: str = Field(min_length=1, max_length=80)
    business_id: str = Field(min_length=1, max_length=64)
    recipient_user_id: str | None = Field(default=None, min_length=1, max_length=36)
    channel: Literal["wechat", "sms", "feishu"]
    status: Literal[
        "queued",
        "sending",
        "sent",
        "delivered",
        "read",
        "failed",
        "cancelled",
    ]
    attempts: int = Field(ge=0)
    provider_message_id: str | None = Field(default=None, max_length=250)
    last_error: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    sent_at: AwareDatetime | None
    delivered_at: AwareDatetime | None
    read_at: AwareDatetime | None
    latest_attempt_no: int | None = Field(default=None, ge=1)
    latest_response_code: str | None = Field(default=None, max_length=80)
    latest_error: str | None = None
    latest_attempted_at: AwareDatetime | None
    retryable: bool


class NotificationDeliveryPageOut(_StrictOutput):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[NotificationDeliveryRecordOut, ...]
    next_after_id: UUID | None


class NotificationDeliveryRetryIn(_StrictOutput):
    expected_attempt_no: int = Field(ge=1, le=100)
    reason: str = Field(min_length=1, max_length=500)


class NotificationDeliveryRetryOut(_StrictOutput):
    schema_version: Literal["1.0"] = "1.0"
    delivery_id: UUID
    retry_attempt_no: int = Field(ge=1)
    status: Literal["queued"]
    queued_at: AwareDatetime
    replayed: bool


__all__ = [
    "NotificationItemOut",
    "NotificationPageOut",
    "NotificationReadOut",
    "NotificationDeliveryRecordOut",
    "NotificationDeliveryPageOut",
    "NotificationDeliveryRetryIn",
    "NotificationDeliveryRetryOut",
]
