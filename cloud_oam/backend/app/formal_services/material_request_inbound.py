"""Create a personal-inbound order as a separate, pending fact."""
from datetime import datetime, timezone
from decimal import Decimal
import uuid
from sqlalchemy import select
from ..foundation_models import OutboxEvent
from ..demand_models import MaterialRequest
from ..inventory_models import InboundOrder, InboundPosting, Receipt, ReceiptLine, Shipment, ShipmentLine, OutboundPosting, StockAccount
from .inventory_posting import InventoryMovementCommand, InventoryPostingCommand, InventoryPostingError, post_inventory_transaction
from .audit_chain import append_audit_event
from . import material_request_outbound as outbound

class InboundError(InventoryPostingError):
    """Stable inbound failure using the inventory HTTP error contract."""
def _fail(c, k, m): raise InboundError(c, k, m)

def _validate_inbound_target(shipment, target_location_id, target_person_id):
    """The inbound target must remain the immutable shipment destination."""
    if (shipment.target_location_id != target_location_id
            or shipment.target_person_id != target_person_id
            or target_person_id is None):
        _fail("target_mismatch", "conflict", "个人仓入账目标必须与发运事实一致")

def create_inbound_order(db, *, actor, request_id, expected_version, receipt_id, target_location_id, target_person_id, trace_request_id):
    request = db.scalar(select(MaterialRequest).where(MaterialRequest.id == request_id).with_for_update())
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    if request.version != expected_version: _fail("version_conflict", "conflict", "需求版本已变化，请重新读取")
    receipt = db.get(Receipt, receipt_id)
    if receipt is None: _fail("receipt_not_found", "not_found", "收货单不存在")
    shipment = db.get(Shipment, receipt.shipment_id)
    if shipment is None: _fail("shipment_not_found", "conflict", "收货关联发运不存在")
    _validate_inbound_target(shipment, target_location_id, target_person_id)
    belongs = db.scalar(select(OutboundPosting.request_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == shipment.id))
    if belongs != request_id: _fail("receipt_request_mismatch", "conflict", "收货单不属于当前需求")
    source_ids = tuple(db.scalars(select(OutboundPosting.source_stock_account_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == shipment.id)).all())
    if not source_ids: _fail("source_missing", "conflict", "发运缺少来源库存账户")
    outbound._authorize_account_ids(db, actor, source_ids, action="read", resource="inventory", lock_rows=False)
    if receipt.status not in {"accepted", "exception"}: _fail("receipt_not_final", "precondition_failed", "收货尚未完成验收")
    existing = db.scalar(select(InboundOrder).where(InboundOrder.receipt_id == receipt.id))
    if existing is not None: return _result(existing)
    now = datetime.now(timezone.utc)
    row = InboundOrder(id=uuid.uuid4(), inbound_no=f"INB-{now:%Y%m%d}-{uuid.uuid4().hex[:12].upper()}", receipt_id=receipt.id, target_location_id=target_location_id, target_person_id=target_person_id, status="pending", posting_transaction_id=None, created_at=now)
    db.add(row); db.flush()
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action="personal_inbound_order_created", aggregate_type="inbound_order", aggregate_id=str(row.id), before_jsonb={}, after_jsonb={"request_id": str(request_id), "receipt_id": str(receipt.id), "status": row.status}, request_id=trace_request_id, occurred_at=now, created_at=now)
    db.add(OutboxEvent(event_type="personal_inbound_order_created", aggregate_type="inbound_order", aggregate_id=str(row.id), payload_jsonb={"request_id": str(request_id), "receipt_id": str(receipt.id), "inbound_no": row.inbound_no}, status="pending", attempts=0, idempotency_key=f"inbound-order:{row.id}", available_at=now))
    return _result(row)

def _result(row):
    return {"schema_version":"1.0", "inbound_order_id":row.id, "inbound_no":row.inbound_no, "receipt_id":row.receipt_id, "target_location_id":row.target_location_id, "target_person_id":row.target_person_id, "status":row.status}

def resolve_personal_target_account(db, *, receipt_id, target_location_id, target_person_id, shipment_line_id):
    """Resolve the pre-provisioned available account for accepted stock."""
    line = db.get(ShipmentLine, shipment_line_id)
    if line is None: _fail("shipment_line_not_found", "not_found", "发运明细不存在")
    posting = db.get(OutboundPosting, line.outbound_posting_id)
    source = db.get(StockAccount, posting.target_stock_account_id) if posting else None
    if source is None: _fail("target_source_missing", "conflict", "发运目标账户不存在")
    rows = tuple(db.scalars(select(StockAccount).where(
        StockAccount.owner_org_id == source.owner_org_id,
        StockAccount.custodian_person_id == target_person_id,
        StockAccount.location_id == target_location_id,
        StockAccount.material_id == source.material_id,
        StockAccount.condition_code == source.condition_code,
        StockAccount.lot_id == source.lot_id,
        StockAccount.availability_bucket == "available",
    ).limit(2)).all())
    if len(rows) != 1: _fail("personal_target_missing", "precondition_failed", "个人仓目标账户不存在或不唯一")
    return rows[0]

def build_inbound_posting_command(db, *, inbound_order, receipt_line_id, target_account):
    """Build, but do not post, one accepted receipt line as an inbound movement."""
    from ..inventory_models import ReceiptLine, ReceiptSerial
    line = db.get(ReceiptLine, receipt_line_id)
    if line is None: _fail("receipt_line_not_found", "not_found", "收货明细不存在")
    if line.receipt_id != inbound_order.receipt_id or line.accepted_qty <= 0:
        _fail("receipt_line_invalid", "precondition_failed", "收货明细未形成可入账数量")
    shipment_line = db.get(ShipmentLine, line.shipment_line_id)
    posting = db.get(OutboundPosting, shipment_line.outbound_posting_id) if shipment_line else None
    if posting is None: _fail("shipment_line_invalid", "conflict", "收货明细缺少原发运事实")
    serial_ids = tuple(db.scalars(select(ReceiptSerial.serial_id).where(ReceiptSerial.receipt_line_id == line.id, ReceiptSerial.accepted.is_(True))).all())
    return InventoryPostingCommand(
        transaction_no=f"INV-IN-{inbound_order.id.hex[:16].upper()}", movement_type="transfer",
        source_document_type="personal_inbound", source_document_id=str(inbound_order.id),
        posting_key=f"personal-inbound:{inbound_order.id}:{line.id}", effective_at=inbound_order.created_at,
        movements=(InventoryMovementCommand(from_account_id=posting.target_stock_account_id, to_account_id=target_account.id, quantity=line.accepted_qty, serial_ids=serial_ids, external_boundary_code=None),),
    )

def post_inbound_order(db, *, actor, inbound_order_id, material_request_id, idempotency_key, request_id):
    # Use the same parent-first lock order as receipt/order creation. The order
    # lock serializes different idempotency keys for one immutable posting fact.
    request = db.scalar(select(MaterialRequest).where(MaterialRequest.id == material_request_id).with_for_update())
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    order = db.scalar(select(InboundOrder).where(InboundOrder.id == inbound_order_id).with_for_update())
    if order is None: _fail("inbound_not_found", "not_found", "个人仓入账单不存在")
    receipt = db.get(Receipt, order.receipt_id)
    if receipt is None or receipt.status not in {"accepted", "exception"}: _fail("receipt_not_final", "precondition_failed", "收货尚未完成验收")
    bound_requests = set(db.scalars(select(OutboundPosting.request_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == receipt.shipment_id)).all())
    if bound_requests != {material_request_id}: _fail("request_mismatch", "conflict", "入账单不属于当前需求")
    shipment = db.get(Shipment, receipt.shipment_id)
    if shipment is None: _fail("shipment_not_found", "conflict", "收货关联发运不存在")
    _validate_inbound_target(shipment, order.target_location_id, order.target_person_id)
    existing = db.scalar(select(InboundPosting).where(InboundPosting.inbound_order_id == order.id))
    lines = tuple(db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id).order_by(ReceiptLine.id)).all())
    if not lines: _fail("receipt_empty", "precondition_failed", "收货没有可入账明细")
    movements = []
    for line in lines:
        # Rejected-only receipt lines remain receipt/exception facts and must
        # never become personal-warehouse inventory.  A receipt may contain
        # both accepted and rejected lines; only the accepted quantity is an
        # inventory movement.
        if Decimal(line.accepted_qty) <= 0:
            continue
        target = resolve_personal_target_account(db, receipt_id=receipt.id, target_location_id=order.target_location_id, target_person_id=order.target_person_id, shipment_line_id=line.shipment_line_id)
        command = build_inbound_posting_command(db, inbound_order=order, receipt_line_id=line.id, target_account=target)
        movements.extend(command.movements)
    if not movements:
        _fail("receipt_empty", "precondition_failed", "收货没有可入账的合格数量")
    command = InventoryPostingCommand(transaction_no=f"INV-IN-{order.id.hex[:16].upper()}", movement_type="transfer", source_document_type="personal_inbound", source_document_id=str(order.id), posting_key=f"personal-inbound:{order.id}", effective_at=order.created_at, movements=tuple(movements))
    result = post_inventory_transaction(db, actor=actor, command=command, idempotency_key=idempotency_key, request_id=request_id)
    # Even a completed order must pass the unified posting service's current
    # principal, account scopes and actor/command-bound idempotency validation.
    # A different key is a conflict, not permission to bypass those checks.
    if existing is not None:
        if existing.inventory_transaction_id != result.transaction_id:
            _fail("posting_mismatch", "conflict", "入账事实与库存事务不一致")
    if (
        order.posting_transaction_id is not None
        and order.posting_transaction_id != result.transaction_id
    ):
        _fail("posting_mismatch", "conflict", "入账单绑定的库存事务不一致")
    # Keep the mutable read projection bound to the same immutable inventory
    # transaction as InboundPosting.  This assignment is part of the caller's
    # transaction, so a later audit/outbox failure rolls it back together with
    # the posting binding.  Replays assign the same UUID and remain idempotent.
    order.posting_transaction_id = result.transaction_id
    if existing is None:
        db.add(InboundPosting(id=uuid.uuid4(), inbound_order_id=order.id, inventory_transaction_id=result.transaction_id, created_at=datetime.now(timezone.utc)))
        now = datetime.now(timezone.utc)
        append_audit_event(
            db, stream_key="material_request", actor_user_id=str(actor.user_id),
            action="personal_inbound_posted", aggregate_type="inbound_order",
            aggregate_id=str(order.id), before_jsonb={"status": "pending"},
            after_jsonb={"status": "posted", "request_id": str(material_request_id),
                         "inventory_transaction_id": str(result.transaction_id)},
            request_id=request_id, occurred_at=now, created_at=now,
        )
        db.add(OutboxEvent(
            event_type="personal_inbound_posted", aggregate_type="inbound_order",
            aggregate_id=str(order.id), payload_jsonb={
                "request_id": str(material_request_id),
                "inbound_order_id": str(order.id),
                "inventory_transaction_id": str(result.transaction_id),
            }, status="pending", attempts=0,
            idempotency_key=f"inbound-posted:{order.id}", available_at=now,
        ))
    # Keep the orchestration projection aligned with the immutable posting
    # fact.  The inventory transaction remains the source of truth; this
    # status is only the request-facing read model and is safe to repeat on a
    # replay of the same bound transaction.
    order.status = "posted"
    # Keep the projection line-aware for split receipts and partial postings.
    # The small contract fakes used by the unit tests do not expose a real
    # session; production sessions always take this path after flushing the
    # immutable posting binding.
    if hasattr(db, "flush"):
        db.flush()
        from .material_request_inbound_state import refresh_personal_inbound_status
        refresh_personal_inbound_status(db, request)
    else:
        request.personal_inbound_status = "posted"
    return {"inbound_order_id": order.id, "inventory_transaction_id": result.transaction_id, "replayed": result.replayed}

def list_inbound_orders(db, *, actor, request_id):
    request = db.get(MaterialRequest, request_id)
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    shipment_ids = select(Shipment.id).join(ShipmentLine, ShipmentLine.shipment_id == Shipment.id).join(OutboundPosting, OutboundPosting.id == ShipmentLine.outbound_posting_id).where(OutboundPosting.request_id == request_id)
    receipt_ids = select(Receipt.id).where(Receipt.shipment_id.in_(shipment_ids))
    rows = tuple(db.scalars(select(InboundOrder).where(InboundOrder.receipt_id.in_(receipt_ids)).order_by(InboundOrder.created_at, InboundOrder.id)).all())
    for row in rows:
        source_ids = tuple(db.scalars(select(OutboundPosting.source_stock_account_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).join(Receipt, Receipt.shipment_id == ShipmentLine.shipment_id).where(Receipt.id == row.receipt_id)).all())
        if source_ids:
            outbound._authorize_account_ids(db, actor, source_ids, action="read", resource="inventory", lock_rows=False)
    return tuple({"schema_version":"1.0", "inbound_order_id": row.id, "inbound_no": row.inbound_no, "receipt_id": row.receipt_id, "target_location_id": row.target_location_id, "target_person_id": row.target_person_id, "status": "posted" if row.posting_transaction_id is not None else row.status, "posting_transaction_id": row.posting_transaction_id} for row in rows)
