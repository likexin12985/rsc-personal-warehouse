"""Strict public contracts for the formal V1 file-intent API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


FilePurpose = Literal[
    "request_attachment",
    "external_approval_evidence",
    "stocktake_evidence",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FileUploadIntentIn(_StrictModel):
    purpose: FilePurpose
    original_filename: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=1, le=120 * 1024 * 1024)
    mime_type: str = Field(min_length=1, max_length=160)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PresignedUploadOut(_StrictModel):
    method: Literal["PUT"] = "PUT"
    url: str = Field(min_length=1, max_length=8192)
    expires_at: datetime
    headers: dict[str, str]


class FileUploadIntentOut(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    file_id: UUID
    purpose: FilePurpose
    status: Literal["pending", "available"]
    upload: PresignedUploadOut | None
    idempotency_replayed: bool


class FileCompleteOut(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    file_id: UUID
    purpose: FilePurpose
    status: Literal["available"] = "available"
    verified_at: datetime
    already_available: bool


class PresignedDownloadOut(_StrictModel):
    method: Literal["GET"] = "GET"
    url: str = Field(min_length=1, max_length=8192)
    expires_at: datetime


class FileDownloadIntentOut(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    file_id: UUID
    purpose: FilePurpose
    status: Literal["available"] = "available"
    download: PresignedDownloadOut
