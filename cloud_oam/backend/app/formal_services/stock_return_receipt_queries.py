"""Current receiving progress, separate from each receipt's original preview."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from ..stock_return_receipt_schemas import StockReturnReceiptHistoryOut, StockReturnReceiptProgressOut
from . import stock_return_receipt_plan as planning, stock_return_receipt_facts as facts
from . import inventory_query as inventory
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_return_sources import _fail


def receipt_history(db, *, actor, shipment_id):
    with db.no_autoflush:
        snapshot = inventory._projection_snapshot(db); audit = material_audit_cursor(db)
        current, detail = planning.authorize(db, actor, shipment_id, action='read')
        receipts = facts.verified_receipts(db, shipment_id=shipment_id)
        accepted = defaultdict(Decimal); rejected = defaultdict(Decimal); damaged = defaultdict(Decimal); confirmed = defaultdict(set)
        for receipt in receipts:
            for line in receipt.lines:
                identifier = line.shipment_line_id
                accepted[identifier] += Decimal(line.accepted_qty)
                rejected[identifier] += Decimal(line.rejected_qty)
                damaged[identifier] += Decimal(line.damaged_qty)
                confirmed[identifier].update(sn.serial_id for sn in (*line.accepted_serials, *line.rejected_serials))
        lines = []
        for original in detail.package.lines:
            identifier = original.shipment_line_id
            remaining = Decimal(original.shipped_quantity) - accepted[identifier] - rejected[identifier]
            if remaining < 0 or damaged[identifier] > accepted[identifier]: facts.invalid()
            lines.append(StockReturnReceiptProgressOut(shipment_line_id=identifier, shipped_qty=original.shipped_quantity,
                accepted_qty=format(accepted[identifier], '.3f'), rejected_qty=format(rejected[identifier], '.3f'),
                damaged_qty=format(damaged[identifier], '.3f'), unconfirmed_qty=format(remaining, '.3f'),
                unconfirmed_serials=tuple(sn for sn in original.serials if sn.serial_id not in confirmed[identifier])))
        final_actor, final_detail = planning.authorize(db, current, shipment_id, action='read')
        if current != final_actor or detail.package != final_detail.package or material_audit_cursor(db) != audit:
            _fail('stock_return_receipt_history_changed', '验收记录、权限或接收责任在读取期间变化，请刷新原包裹')
        inventory._ensure_projection_snapshot_current(db, snapshot)
        return StockReturnReceiptHistoryOut(person_id=current.person_id, authorization_version=current.authorization_version,
            ledger_cursor=snapshot.ledger_cursor, queried_at=datetime.now(timezone.utc), package=detail.package,
            receipts=receipts, lines=tuple(lines))
