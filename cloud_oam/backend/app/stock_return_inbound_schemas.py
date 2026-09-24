"""Schemas for the independent return inbound posting boundary."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReturnInboundModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StockReturnInboundSubmitIn(ReturnInboundModel):
    operator_person_id: UUID
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")


class StockReturnInboundPreviewOut(ReturnInboundModel):
    schema_version: Literal["1.0"] = "1.0"
    planning_status: Literal["inbound_preview_only"] = "inbound_preview_only"
    receipt_id: UUID
    shipment_id: UUID
    operator_person_id: UUID
    authorization_version: int
    target_location_id: UUID
    target_custody_assignment_id: UUID
    receipt_plan_hash: str
    plan_hash: str
    reason: str
    checked_at: datetime
    ledger_cursor: int
    lines: tuple[dict, ...]


class StockReturnInboundOut(ReturnInboundModel):
    schema_version: Literal["1.0"] = "1.0"
    inbound_id: UUID
    inbound_no: str
    receipt_id: UUID
    shipment_id: UUID
    target_location_id: UUID
    target_custody_assignment_id: UUID
    status: Literal["posted"]
    posting_transaction_id: UUID
    request_id: str
    request_hash: str
    plan_hash: str
    replayed: bool = False


class StockReturnInboundSealIn(ReturnInboundModel):
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class StockReturnInboundSeal(ReturnInboundModel):
    seal_id: UUID
    receipt_id: UUID
    shipment_id: UUID
    request_id: str
    request_hash: str
    sealed_at: datetime


class StockReturnInboundSealOut(ReturnInboundModel):
    schema_version: Literal["1.0"] = "1.0"
    lookup_status: Literal["sealed"] = "sealed"
    seal: StockReturnInboundSeal


class StockReturnInboundSummary(ReturnInboundModel):
    inbound_id: UUID
    inbound_no: str
    target_location_id: UUID
    posting_transaction_id: UUID
    posted_at: datetime


class StockReturnInboundStateOut(ReturnInboundModel):
    schema_version: Literal['1.0'] = '1.0'
    receipt_id: UUID
    shipment_id: UUID
    operator_person_id: UUID
    authorization_version: int = Field(ge=1)
    status: Literal['not_posted','posted']
    inbound: StockReturnInboundSummary | None
    ledger_cursor: int = Field(ge=0)
    checked_at: datetime

    @model_validator(mode='after')
    def consistent_state(self):
        if (self.status=='posted') != (self.inbound is not None):
            raise ValueError('posted inbound requires its proven summary')
        return self
