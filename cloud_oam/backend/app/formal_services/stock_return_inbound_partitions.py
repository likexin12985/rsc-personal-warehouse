"""Read exact receipt partitions under an effective material policy."""
from sqlalchemy import select

from ..inventory_models import MaterialInventoryPolicy
from ..stock_operation_models import StockOperationReceiptSerial
from .stock_return_inbound_quality import accepted_parts, ReturnInboundContractError
from .work_order_return_sources import _fail


def receipt_parts(db, *, origin, source, at):
    policies = tuple(db.scalars(select(MaterialInventoryPolicy).where(
        MaterialInventoryPolicy.material_id == source.material_id,
        MaterialInventoryPolicy.effective_from <= at,
        (MaterialInventoryPolicy.effective_to.is_(None)) | (MaterialInventoryPolicy.effective_to > at),
    ).limit(2).execution_options(populate_existing=True)))
    if len(policies) != 1 or policies[0].tracking_mode not in ('none', 'lot', 'serial', 'lot_and_serial'):
        _fail('stock_return_inbound_policy_invalid', '退回入库的物料追踪规则不唯一', 412)
    rows = tuple(db.scalars(select(StockOperationReceiptSerial).where(
        StockOperationReceiptSerial.line_id == origin.id,
        StockOperationReceiptSerial.result == 'accepted',
    ).order_by(StockOperationReceiptSerial.serial_id).limit(1001)
        .execution_options(populate_existing=True)))
    if len(rows) > 1000:
        _fail('stock_return_inbound_evidence_invalid', '接受SN数量超过验收边界', 503)
    try:
        return accepted_parts(source_condition=source.condition_code,
            accepted_quantity=origin.accepted_qty, damaged_quantity=origin.damaged_qty,
            accepted_serial_ids=tuple(row.serial_id for row in rows),
            damaged_serial_ids=tuple(row.serial_id for row in rows if row.damaged),
            tracked=policies[0].tracking_mode in ('serial', 'lot_and_serial'))
    except ReturnInboundContractError:
        _fail('stock_return_inbound_evidence_invalid', '验收破损份额与数量或SN证据不一致', 503)
