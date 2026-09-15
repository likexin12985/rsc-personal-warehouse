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


__all__ = ["NotificationItemOut", "NotificationPageOut", "NotificationReadOut"]
