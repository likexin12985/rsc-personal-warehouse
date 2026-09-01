"""Strict contracts for the formal opening-control reconciliation boundary.

OAM control quantities remain read-only comparison facts.  These contracts
never accept an inventory account, movement, balance, actor, status, or source
snapshot from the client; the service resolves every one of those facts from
the sealed opening-stocktake graph.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


_ZERO_UUID = UUID(int=0)


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _required_uuid(value: UUID, field_name: str) -> UUID:
    if value == _ZERO_UUID:
        raise ValueError(f"{field_name} cannot be the zero UUID")
    return value


def _trimmed(value: str, field_name: str) -> str:
    if value != value.strip():
        raise ValueError(f"{field_name} cannot have surrounding whitespace")
    return value


class OpeningControlReconciliationStartIn(_StrictRequestModel):
    expected_task_version: int = Field(ge=0, strict=True)


class OpeningControlReconciliationExplanationItemIn(_StrictRequestModel):
    reconciliation_item_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    explanation: StrictStr = Field(min_length=4, max_length=4000)
    evidence_reference: StrictStr = Field(min_length=4, max_length=1000)
    evidence_file_id: UUID | None = None

    @field_validator("reconciliation_item_id", "evidence_file_id")
    @classmethod
    def validate_ids(cls, value: UUID | None, info) -> UUID | None:
        return None if value is None else _required_uuid(value, info.field_name)

    @field_validator("explanation", "evidence_reference")
    @classmethod
    def validate_trimmed_text(cls, value: str, info) -> str:
        return _trimmed(value, info.field_name)


class OpeningControlReconciliationExplainIn(_StrictRequestModel):
    expected_version: int = Field(ge=0, strict=True)
    items: tuple[OpeningControlReconciliationExplanationItemIn, ...] = Field(
        min_length=1
    )


class OpeningControlReconciliationApproveIn(_StrictRequestModel):
    expected_version: int = Field(ge=0, strict=True)
    comment: StrictStr = Field(min_length=4, max_length=4000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        return _trimmed(value, "comment")


class OpeningControlReconciliationStartOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    reconciliation_run_id: UUID
    task_id: UUID
    status: Literal["differences"]
    version: int = Field(ge=0)
    item_count: int = Field(ge=1)
    created_at: datetime
    replayed: bool


class OpeningControlReconciliationExplainOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    reconciliation_run_id: UUID
    task_id: UUID
    status: Literal["differences"]
    version: int = Field(ge=1)
    explained_item_count: int = Field(ge=1)
    explained_at: datetime
    replayed: bool


class OpeningControlReconciliationApproveOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    reconciliation_run_id: UUID
    task_id: UUID
    status: Literal["approved"]
    version: int = Field(ge=1)
    resolved_item_count: int = Field(ge=1)
    approved_at: datetime
    replayed: bool


ReconciliationAllowedAction = Literal["explain", "approve"]


class OpeningControlReconciliationItemOut(BaseModel):
    reconciliation_item_id: UUID
    stocktake_difference_id: UUID
    control_snapshot_line_id: UUID
    business_key: str
    material_id: UUID | None
    external_qty: str
    local_qty: str
    difference: str
    status: Literal["difference", "explained", "resolved"]
    version: int = Field(ge=0)
    explanation: str
    evidence_reference: str
    evidence_file_id: UUID | None
    explained_at: datetime | None


class OpeningControlReconciliationSummaryOut(BaseModel):
    reconciliation_run_id: UUID
    task_id: UUID
    task_no: str
    region_org_id: UUID
    status: Literal["differences", "approved"]
    version: int = Field(ge=0)
    item_count: int = Field(ge=1)
    explained_item_count: int = Field(ge=0)
    resolved_item_count: int = Field(ge=0)
    external_snapshot_at: datetime
    local_ledger_cursor: str
    created_at: datetime
    approved_at: datetime | None
    allowed_actions: list[ReconciliationAllowedAction]


class OpeningControlReconciliationPageOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[OpeningControlReconciliationSummaryOut]
    next_after_id: UUID | None


class OpeningControlReconciliationDetailOut(
    OpeningControlReconciliationSummaryOut
):
    schema_version: Literal["1.0"] = "1.0"
    source_system_id: UUID
    round_id: UUID
    posting_id: UUID
    difference_manifest_sha256: str = Field(min_length=64, max_length=64)
    approval_comment: str
    items: list[OpeningControlReconciliationItemOut]


__all__ = [
    "OpeningControlReconciliationApproveIn",
    "OpeningControlReconciliationApproveOut",
    "OpeningControlReconciliationDetailOut",
    "OpeningControlReconciliationExplainIn",
    "OpeningControlReconciliationExplainOut",
    "OpeningControlReconciliationExplanationItemIn",
    "OpeningControlReconciliationItemOut",
    "OpeningControlReconciliationPageOut",
    "OpeningControlReconciliationStartIn",
    "OpeningControlReconciliationStartOut",
    "OpeningControlReconciliationSummaryOut",
    "ReconciliationAllowedAction",
]
