"""Own immutable return/cancellation facts, separate from fulfillment status."""
from datetime import datetime, timezone

from sqlalchemy import select

from ..stock_operation_models import StockOperationOrder, StockOperationCancellation
from ..inventory_models import InventoryLedgerHead
from ..stock_return_schemas import StockReturnHistoryItemOut, StockReturnHistoryOut
from . import stock_return_facts as facts, inventory_query as inventory
from .stock_return_plan import authorize
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_return_sources import _fail


def return_history(db, *, actor, work_order_id):
    current = authorize(db, actor, "read")
    with db.no_autoflush:
        audit = material_audit_cursor(db)
        cursor = db.scalar(select(InventoryLedgerHead.next_cursor).where(InventoryLedgerHead.stream_key == "inventory"))
        if cursor is None: facts.invalid()
        orders = tuple(db.scalars(select(StockOperationOrder).where(StockOperationOrder.actor_user_id == current.user_id,
            StockOperationOrder.oam_work_order_id == work_order_id).order_by(StockOperationOrder.created_at, StockOperationOrder.id)
            .limit(101).execution_options(populate_existing=True)))
        if len(orders) > 100:
            _fail("stock_return_history_limit", "本人退回历史过多，请按准确原请求回查", 503)
        items = []
        for order in orders:
            original = facts.order_result(db, actor=current, order=order)
            cancellation = db.scalar(select(StockOperationCancellation).where(StockOperationCancellation.operation_id == order.id)
                .execution_options(populate_existing=True))
            items.append(StockReturnHistoryItemOut(original=original, cancellation=facts.cancellation_result(db,
                actor=current, order=order, cancellation=cancellation) if cancellation else None))
        if material_audit_cursor(db) != audit:
            _fail("stock_return_history_changed", "退回历史在读取期间变化，请重新核验")
        inventory._ensure_projection_snapshot_current(db, inventory._ProjectionSnapshot(cursor - 1, None))
        authorize(db, current, "read")
        return StockReturnHistoryOut(person_id=current.person_id, work_order_id=work_order_id,
            authorization_version=current.authorization_version, queried_at=datetime.now(timezone.utc), items=tuple(items))
