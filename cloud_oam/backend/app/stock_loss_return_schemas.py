"""Exact HQ-approved loss return; inventory dimensions remain server-owned."""
from uuid import UUID

from app.stock_loss_schemas import StockLossDispositionPreviewIn


class StockLossReturnPreviewIn(StockLossDispositionPreviewIn):
    target_location_id: UUID
    transit_location_id: UUID


from app.stock_loss_schemas import StockLossDispositionExecuteIn


class StockLossReturnExecuteIn(StockLossReturnPreviewIn, StockLossDispositionExecuteIn):
    pass
