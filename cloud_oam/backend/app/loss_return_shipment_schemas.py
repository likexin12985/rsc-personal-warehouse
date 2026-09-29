"""Verified shipment-origin adapters; ordinary wire contracts stay exact."""
from uuid import UUID
from pydantic import Field
from app.stock_return_origin_schemas import LossReturnOrigin, WorkOrderReturnOrigin
from app.stock_return_shipment_schemas import (
    StockReturnShipmentLineOut, StockReturnShipmentPreviewOut, StockReturnShipmentOut,
)


class LossReturnShipmentLineOut(StockReturnShipmentLineOut):
    source_recovery_line_id: None = Field(default=None, exclude=True)
    source_loss_line_id: UUID


class LossReturnShipmentPreviewOut(StockReturnShipmentPreviewOut):
    work_order_id: None = Field(default=None, exclude=True)
    origin: LossReturnOrigin
    lines: tuple[LossReturnShipmentLineOut, ...]


class LossReturnShipmentOut(StockReturnShipmentOut):
    work_order_id: None = Field(default=None, exclude=True)
    origin: LossReturnOrigin
    lines: tuple[LossReturnShipmentLineOut, ...]


def line_view(line, **fields):
    if line.source_loss_line_id is not None:
        if line.source_recovery_line_id is not None:
            raise ValueError('exclusive line origin required')
        return LossReturnShipmentLineOut(source_loss_line_id=line.source_loss_line_id, **fields)
    return StockReturnShipmentLineOut(source_recovery_line_id=line.source_recovery_line_id, **fields)


def line_wire(origin, value):
    schema = LossReturnShipmentLineOut if isinstance(origin, LossReturnOrigin) else StockReturnShipmentLineOut
    return schema.model_validate(value).model_dump(mode='json')


def preview_view(origin, **fields):
    if isinstance(origin, LossReturnOrigin):
        return LossReturnShipmentPreviewOut(origin=origin, **fields)
    if isinstance(origin, WorkOrderReturnOrigin):
        return StockReturnShipmentPreviewOut(work_order_id=origin.work_order_id, **fields)
    raise TypeError('verified return origin required')


def posted_view(origin, **fields):
    if isinstance(origin, LossReturnOrigin):
        return LossReturnShipmentOut(origin=origin, **fields)
    if isinstance(origin, WorkOrderReturnOrigin):
        return StockReturnShipmentOut(work_order_id=origin.work_order_id, **fields)
    raise TypeError('verified return origin required')
