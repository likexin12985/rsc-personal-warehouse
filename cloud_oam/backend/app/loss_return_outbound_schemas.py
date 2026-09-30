"""Separate loss-return output coordinates; ordinary return wire stays exact."""
from pydantic import ConfigDict,Field
from app.stock_return_origin_schemas import LossReturnOrigin
from app.stock_return_outbound_schemas import StockReturnOutboundLineOut,StockReturnOutboundPreviewOut,StockReturnOutboundOut
from app.formal_services import stock_return_origins as origins
from uuid import UUID

class LossReturnOutboundLineOut(StockReturnOutboundLineOut):
    model_config=ConfigDict(extra='forbid')
    source_recovery_line_id: None=Field(default=None,exclude=True)
    source_loss_line_id: UUID

class LossReturnOutboundPreviewOut(StockReturnOutboundPreviewOut):
    model_config=ConfigDict(extra='forbid')
    work_order_id: None=Field(default=None,exclude=True)
    origin: LossReturnOrigin
    lines: tuple[LossReturnOutboundLineOut,...]

class LossReturnOutboundOut(StockReturnOutboundOut):
    model_config=ConfigDict(extra='forbid')
    work_order_id: None=Field(default=None,exclude=True)
    origin: LossReturnOrigin
    lines: tuple[LossReturnOutboundLineOut,...]


def line_view(line,**fields):
    if line.source_loss_line_id is not None:
        return LossReturnOutboundLineOut(source_loss_line_id=line.source_loss_line_id,**fields)
    return StockReturnOutboundLineOut(source_recovery_line_id=line.source_recovery_line_id,**fields)


def plan_origin(origin):
    return {'origin':origin.model_dump(mode='json')} if isinstance(origin,LossReturnOrigin) else {}


def preview_view(origin,**fields):
    if isinstance(origin,LossReturnOrigin):return LossReturnOutboundPreviewOut(origin=origin,**fields)
    return StockReturnOutboundPreviewOut(work_order_id=origin.work_order_id,**fields)


def posted_view(origin,**fields):
    if isinstance(origin,LossReturnOrigin):return LossReturnOutboundOut(origin=origin,**fields)
    return StockReturnOutboundOut(work_order_id=origin.work_order_id,**fields)


def line_origin_matches(origin,line,view):
    return {k:view[k] for k in ('source_recovery_line_id','source_loss_line_id') if k in view}==origins.line_origin(origin,line)


from app.stock_return_outbound_schemas import (
    StockReturnOutboundOptionLineOut, StockReturnOutboundOptionsOut, StockReturnOutboundHistoryOut,
)


class LossReturnOutboundOptionLineOut(StockReturnOutboundOptionLineOut):
    model_config = ConfigDict(extra='forbid')
    source_recovery_line_id: None = Field(default=None, exclude=True)
    source_loss_line_id: UUID


class LossReturnOutboundOptionsOut(StockReturnOutboundOptionsOut):
    model_config = ConfigDict(extra='forbid')
    work_order_id: None = Field(default=None, exclude=True)
    origin: LossReturnOrigin
    lines: tuple[LossReturnOutboundOptionLineOut, ...]


class LossReturnOutboundHistoryOut(StockReturnOutboundHistoryOut):
    model_config = ConfigDict(extra='forbid')
    work_order_id: None = Field(default=None, exclude=True)
    original: LossReturnOrigin
    cancellation: None = None
    items: tuple[LossReturnOutboundOut, ...]


def option_line_view(line, **fields):
    if line.source_loss_line_id is not None:
        if line.source_recovery_line_id is not None:
            raise ValueError('exclusive line origin required')
        return LossReturnOutboundOptionLineOut(source_loss_line_id=line.source_loss_line_id, **fields)
    return StockReturnOutboundOptionLineOut(source_recovery_line_id=line.source_recovery_line_id, **fields)


def options_view(original, **fields):
    if isinstance(original, LossReturnOrigin):
        return LossReturnOutboundOptionsOut(origin=original, **fields)
    return StockReturnOutboundOptionsOut(work_order_id=original.work_order_id, **fields)


def history_view(original, **fields):
    if isinstance(original, LossReturnOrigin):
        return LossReturnOutboundHistoryOut(original=original, **fields)
    return StockReturnOutboundHistoryOut(work_order_id=original.work_order_id, original=original, **fields)
