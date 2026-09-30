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


from pydantic import ConfigDict
from app.stock_return_shipment_schemas import (
    StockReturnShipmentOptionLineOut, StockReturnShipmentOptionsOut, StockReturnShipmentHistoryOut,
)
from app.loss_return_outbound_schemas import LossReturnOutboundHistoryOut


class LossReturnShipmentOptionLineOut(StockReturnShipmentOptionLineOut):
    model_config = ConfigDict(extra='forbid')
    source_recovery_line_id: None = Field(default=None, exclude=True)
    source_loss_line_id: UUID


class LossReturnShipmentOptionsOut(StockReturnShipmentOptionsOut):
    model_config = ConfigDict(extra='forbid')
    work_order_id: None = Field(default=None, exclude=True)
    origin: LossReturnOrigin
    lines: tuple[LossReturnShipmentOptionLineOut, ...]


class LossReturnShipmentHistoryOut(StockReturnShipmentHistoryOut):
    model_config = ConfigDict(extra='forbid')
    work_order_id: None = Field(default=None, exclude=True)
    origin: LossReturnOrigin
    departures: LossReturnOutboundHistoryOut
    items: tuple[LossReturnShipmentOut, ...]


def option_line_view(line, **fields):
    if line.source_loss_line_id is not None:
        if line.source_recovery_line_id is not None:
            raise ValueError('exclusive line origin required')
        return LossReturnShipmentOptionLineOut(source_loss_line_id=line.source_loss_line_id, **fields)
    return StockReturnShipmentOptionLineOut(source_recovery_line_id=line.source_recovery_line_id, **fields)


def options_view(original, **fields):
    if isinstance(original, LossReturnOrigin):
        return LossReturnShipmentOptionsOut(origin=original, **fields)
    return StockReturnShipmentOptionsOut(work_order_id=original.work_order_id, **fields)


def history_view(original, **fields):
    if isinstance(original, LossReturnOrigin):
        return LossReturnShipmentHistoryOut(origin=original, **fields)
    return StockReturnShipmentHistoryOut(work_order_id=original.work_order_id, **fields)
