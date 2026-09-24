"""Receipt-scoped, read-only inbound state backed by the original facts."""
from datetime import datetime, timezone

from sqlalchemy import select

from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from ..inventory_models import InventoryTransaction
from ..stock_operation_models import StockOperationReceiptLine, StockOperationReturnInbound, StockOperationReturnInboundLine
from . import inventory_query as inventory, stock_return_receipt_facts as receipts
from .stock_return_inbound_facts import inbound_result, invalid
from .stock_return_inbound_plan import authorize_receipt
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_query import _aware
from .work_order_return_sources import _fail


def read_return_inbound_state(db, *, actor, receipt_id):
    with db.no_autoflush:
        snapshot=inventory._projection_snapshot(db);cursor=material_audit_cursor(db)
        current,receipt=authorize_receipt(db,actor=actor,receipt_id=receipt_id,action='read')
        receipts.receipt_result(db,actor=current,fact=receipt)
        rows=tuple(db.scalars(select(StockOperationReturnInbound).where(
            StockOperationReturnInbound.receipt_id==receipt_id).limit(2).execution_options(populate_existing=True)))
        if len(rows)>1:invalid()
        inbound=None
        if rows:
            fact=rows[0]
            proven=inbound_result(db,actor=current,fact=fact)
            tx=db.get(InventoryTransaction,fact.posting_transaction_id,populate_existing=True)
            inbound=dict(inbound_id=proven['inbound_id'],inbound_no=proven['inbound_no'],
                target_location_id=proven['target_location_id'],posting_transaction_id=proven['posting_transaction_id'],
                posted_at=_aware(tx.posted_at))
        else:
            # Absence of the header alone is not evidence of non-execution.
            # A request seal is separate and never becomes a receipt status.
            if db.scalar(select(InventoryTransaction.id).where(
                    InventoryTransaction.posting_key==f'stock-return-receipt-inbound:{receipt_id}').limit(1)):
                invalid()
            if db.scalar(select(StockOperationReturnInboundLine.id).where(
                    StockOperationReturnInboundLine.receipt_line_id.in_(select(StockOperationReceiptLine.id).where(
                        StockOperationReceiptLine.receipt_id==receipt_id))).limit(1)):
                invalid()
            for model,body in ((AuditEvent,AuditEvent.after_jsonb),(OutboxEvent,OutboxEvent.payload_jsonb),
                    (StateTransitionEvent,StateTransitionEvent.metadata_jsonb)):
                if db.scalar(select(model.id).where(model.aggregate_type=='stock_operation_return_inbound',
                        body['receipt_id'].as_string()==str(receipt_id)).limit(1)):
                    invalid()
        final,_=authorize_receipt(db,actor=current,receipt_id=receipt_id,action='read')
        if final!=current or material_audit_cursor(db)!=cursor:
            _fail('stock_return_inbound_state_changed','入账记录或权限在核验期间变化，请刷新验收记录')
        inventory._ensure_projection_snapshot_current(db,snapshot)
        return dict(schema_version='1.0',receipt_id=receipt_id,shipment_id=receipt.shipment_id,
            operator_person_id=current.person_id,authorization_version=current.authorization_version,
            status='posted' if inbound else 'not_posted',inbound=inbound,
            ledger_cursor=snapshot.ledger_cursor,checked_at=datetime.now(timezone.utc))
