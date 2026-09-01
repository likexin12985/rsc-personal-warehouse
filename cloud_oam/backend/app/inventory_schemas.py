"""Response contracts for the formal V1.0 inventory read surface.

All quantities are serialized as fixed-scale decimal strings.  JavaScript
clients must not turn inventory facts into IEEE-754 numbers or recompute a
summary from a paginated account list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


ProjectionStatus = Literal["not_initialized", "ready"]
OpeningBalanceStatus = Literal["not_established", "established"]
InventoryQuantityStatus = Literal[
    "opening_not_established",
    "material_filter_required",
]


class InventoryScopeOut(BaseModel):
    scope_type: Literal["national", "organization", "person"]
    scope_id: str


class InventoryProjectionOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    projection_status: ProjectionStatus
    opening_balance_status: OpeningBalanceStatus
    projected_at: datetime | None
    ledger_cursor: int


class InventorySummaryOut(InventoryProjectionOut):
    scopes: list[InventoryScopeOut]
    # Quantities from different SKUs/base units are not additive.  Until a
    # material-scoped summary is requested *and* the formal opening stocktake
    # workflow has established the scope, every quantity remains null.
    quantity_status: InventoryQuantityStatus
    physical_in_stock_qty: str | None
    available_qty: str | None
    reserved_qty: str | None
    committed_qty: str | None
    frozen_qty: str | None
    physical_in_transit_qty: str | None
    # Expected supply belongs to the later approved-request/allocation domain.
    # Null is deliberate: returning zero before that domain exists would turn
    # "not measured" into a false business fact.
    expected_supply_qty: None = None
    expected_supply_status: Literal["not_available"] = "not_available"


class InventoryAccountOut(BaseModel):
    stock_account_id: UUID
    owner_org_id: UUID
    owner_org_code: str
    owner_org_name: str
    location_owner_org_id: UUID
    location_owner_org_code: str
    location_owner_org_name: str
    location_id: UUID
    location_code: str
    location_name: str
    location_type: str
    location_parent_id: UUID | None
    custodian_person_id: UUID | None
    custodian_person_name: str | None
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    tracking_mode: str
    condition_code: str
    availability_bucket: str
    lot_id: UUID | None
    lot_no: str | None
    quantity_status: Literal["opening_not_established", "available"]
    quantity: str | None
    balance_version: int
    ledger_cursor: int


class InventoryAccountPageOut(InventoryProjectionOut):
    items: list[InventoryAccountOut]
    next_after_id: UUID | None


class PersonalWarehouseOut(InventoryProjectionOut):
    person_id: UUID
    location_id: UUID | None
    location_code: str | None
    location_name: str | None
    location_status: str | None
    custody_effective_from: datetime | None
    items: list[InventoryAccountOut]


class InventoryMovementOut(BaseModel):
    movement_id: UUID
    line_no: int
    from_account_id: UUID | None
    to_account_id: UUID | None
    external_boundary_code: str | None
    quantity: str
    serial_ids: list[UUID]
    serial_numbers: list[str]


class InventoryTransactionOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    transaction_id: UUID
    transaction_no: str
    ledger_cursor: int
    movement_type: str
    status: Literal["posted"]
    source_document_type: str
    source_document_id: str
    effective_at: datetime
    posted_at: datetime
    operator_person_id: UUID
    operator_person_name: str
    idempotency_fingerprint: str
    reversed_transaction_id: UUID | None
    reversed_by_transaction_id: UUID | None
    movements: list[InventoryMovementOut]
