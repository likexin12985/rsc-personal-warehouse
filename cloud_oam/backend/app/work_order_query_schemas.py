"""Own OAM work-order choices for the formal engineer workflow."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from .inventory_schemas import InventoryAccountOut, PersonalWarehouseOut

WorkOrderStatus = Literal["pending", "active", "completed", "closed", "cancelled", "inactive"]


class MyWorkOrderOut(BaseModel):
    work_order_id: UUID
    work_order_no: str
    status: WorkOrderStatus
    engineer_person_id: UUID
    organization_id: UUID
    source_system: Literal["starcharge_oam"] = "starcharge_oam"
    source_external_id: str
    source_version: str
    source_updated_at: datetime
    synced_at: datetime
    freshness: Literal["fresh", "stale"]
    can_operate: bool


class MyWorkOrdersOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    person_id: UUID
    authorization_version: int
    queried_at: datetime
    items: tuple[MyWorkOrderOut, ...]
    next_after_id: UUID | None


class WorkOrderSerialOptionOut(BaseModel):
    serial_id: UUID
    serial_no: str


class WorkOrderMaterialOptionOut(InventoryAccountOut):
    # The reserved account may pool multiple work orders. This is the amount
    # belonging to the selected order, never the pooled account quantity.
    selectable_quantity: str
    release_target_stock_account_id: UUID | None
    serials: tuple[WorkOrderSerialOptionOut, ...]
    allowed_actions: tuple[Literal["occupy", "consume", "release", "replace"], ...]


class WorkOrderMaterialOptionsOut(PersonalWarehouseOut):
    work_order: MyWorkOrderOut
    authorization_version: int
    items: tuple[WorkOrderMaterialOptionOut, ...]
