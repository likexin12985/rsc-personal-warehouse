"""Create a personal-inbound order as a separate, pending fact."""
from datetime import datetime, timezone
from decimal import Decimal
import uuid
from sqlalchemy import select
from ..foundation_models import OutboxEvent
from ..demand_models import MaterialRequest
from ..inventory_models import InboundOrder, InboundPosting, InventoryTransaction, Receipt, ReceiptLine, Shipment, ShipmentLine, OutboundPosting, StockAccount
from .inventory_posting import InventoryMovementCommand, InventoryPostingCommand, InventoryPostingError, post_inventory_transaction
from .audit_chain import append_audit_event
from .material_request_fulfillment_command import record_fulfillment_command, verify_fulfillment_command
from . import material_request_outbound as outbound
from . import material_request_query

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
    belongs = set(db.scalars(select(OutboundPosting.request_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == shipment.id)).all())
    if belongs != {request_id}: _fail("receipt_request_mismatch", "conflict", "收货单不属于当前需求")
    source_ids = tuple(db.scalars(select(OutboundPosting.source_stock_account_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == shipment.id)).all())
    if not source_ids: _fail("source_missing", "conflict", "发运缺少来源库存账户")
    outbound._authorize_account_ids(db, actor, source_ids, action="read", resource="inventory", lock_rows=False)
    if receipt.status not in {"accepted", "exception"}: _fail("receipt_not_final", "precondition_failed", "收货尚未完成验收")
    if db.scalar(select(ReceiptLine.id).where(ReceiptLine.receipt_id == receipt.id, ReceiptLine.accepted_qty > 0).limit(1)) is None:
        _fail("receipt_empty", "precondition_failed", "收货没有可入账的合格数量")
    existing = db.scalar(select(InboundOrder).where(InboundOrder.receipt_id == receipt.id))
    if existing is not None: return {**_result(existing), **_posting_projection(db, existing)}
    row = _new_inbound_order(db, receipt_id=receipt.id, target_location_id=target_location_id, target_person_id=target_person_id)
    _record_order_created(db, row=row, actor=actor, request_id=request_id, trace_request_id=trace_request_id)
    return _result(row)


def _new_inbound_order(db, *, receipt_id, target_location_id, target_person_id):
    now = datetime.now(timezone.utc)
    row = InboundOrder(id=uuid.uuid4(), inbound_no=f"INB-{now:%Y%m%d}-{uuid.uuid4().hex[:12].upper()}", receipt_id=receipt_id, target_location_id=target_location_id, target_person_id=target_person_id, status="pending", posting_transaction_id=None, created_at=now)
    db.add(row); db.flush()
    return row


def _record_order_created(db, *, row, actor, request_id, trace_request_id):
    now = _aware_time(row.created_at)
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action="personal_inbound_order_created", aggregate_type="inbound_order", aggregate_id=str(row.id), before_jsonb={}, after_jsonb={"request_id": str(request_id), "receipt_id": str(row.receipt_id), "status": row.status}, request_id=trace_request_id, occurred_at=now, created_at=now)
    db.add(OutboxEvent(event_type="personal_inbound_order_created", aggregate_type="inbound_order", aggregate_id=str(row.id), payload_jsonb={"request_id": str(request_id), "receipt_id": str(row.receipt_id), "inbound_no": row.inbound_no}, status="pending", attempts=0, idempotency_key=f"inbound-order:{row.id}", available_at=now))

def _aware_time(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def _result(row):
    return {"schema_version":"1.0", "inbound_order_id":row.id, "inbound_no":row.inbound_no, "receipt_id":row.receipt_id, "target_location_id":row.target_location_id, "target_person_id":row.target_person_id, "status":row.status}

def resolve_personal_target_account(db, *, receipt_id, target_location_id, target_person_id, shipment_line_id, create=False):
    """Resolve the receipt dimension; new accounts must commit with its posting."""
    line = db.get(ShipmentLine, shipment_line_id)
    if line is None: _fail("shipment_line_not_found", "not_found", "发运明细不存在")
    posting = db.get(OutboundPosting, line.outbound_posting_id)
    source = db.get(StockAccount, posting.target_stock_account_id) if posting else None
    if source is None: _fail("target_source_missing", "conflict", "发运目标账户不存在")
    dimensions = dict(owner_org_id=source.owner_org_id,
        custodian_person_id=target_person_id, location_id=target_location_id,
        material_id=source.material_id, condition_code=source.condition_code,
        lot_id=source.lot_id, availability_bucket="available")
    query = select(StockAccount).filter_by(**dimensions).limit(2)
    rows = tuple(db.scalars(query).all())
    if not rows and create:
        receipt = db.get(Receipt, receipt_id)
        shipment = db.get(Shipment, receipt.shipment_id) if receipt else None
        if shipment is None or line.shipment_id != shipment.id:
            _fail("target_source_invalid", "conflict", "目标账户必须来自当前验收的原发运明细")
        _validate_inbound_target(shipment, target_location_id, target_person_id)
        if source.availability_bucket != "in_transit":
            _fail("target_source_invalid", "conflict", "入账来源不是原在途库存")
        # Keep timestamps identical for the immutable admission proof. Two
        # independent receipts may discover the same absent dimension at once;
        # INSERT-only conflict handling reuses the winner without UPDATE rights.
        now = datetime.now(timezone.utc)
        if db.get_bind().dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        db.execute(insert(StockAccount).values(id=uuid.uuid4(), **dimensions,
            created_at=now, updated_at=now).on_conflict_do_nothing())
        rows = tuple(db.scalars(query).all())
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
    serial_ids = tuple(db.scalars(select(ReceiptSerial.serial_id).where(ReceiptSerial.receipt_line_id == line.id, ReceiptSerial.accepted.is_(True)).order_by(ReceiptSerial.serial_id)).all())
    return InventoryPostingCommand(
        transaction_no=f"INV-IN-{inbound_order.id.hex[:16].upper()}", movement_type="transfer",
        source_document_type="personal_inbound", source_document_id=str(inbound_order.id),
        posting_key=f"personal-inbound:{inbound_order.id}:{line.id}", effective_at=_aware_time(inbound_order.created_at),
        movements=(InventoryMovementCommand(from_account_id=posting.target_stock_account_id, to_account_id=target_account.id, quantity=line.accepted_qty, serial_ids=serial_ids, external_boundary_code=None),),
    )

def _order_posting_command(db, order, *, create_missing=False):
    lines = tuple(db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == order.receipt_id).order_by(ReceiptLine.id)).all())
    if not lines: _fail("receipt_empty", "precondition_failed", "收货没有可入账明细")
    movements = []
    for line in lines:
        # Rejected-only receipt lines remain receipt/exception facts and must
        # never become personal-warehouse inventory.  A receipt may contain
        # both accepted and rejected lines; only the accepted quantity is an
        # inventory movement.
        if Decimal(line.accepted_qty) <= 0:
            continue
        target = resolve_personal_target_account(db, receipt_id=order.receipt_id, target_location_id=order.target_location_id, target_person_id=order.target_person_id, shipment_line_id=line.shipment_line_id, create=create_missing)
        command = build_inbound_posting_command(db, inbound_order=order, receipt_line_id=line.id, target_account=target)
        movements.extend(command.movements)
    if not movements:
        _fail("receipt_empty", "precondition_failed", "收货没有可入账的合格数量")
    return InventoryPostingCommand(transaction_no=f"INV-IN-{order.id.hex[:16].upper()}", movement_type="transfer", source_document_type="personal_inbound", source_document_id=str(order.id), posting_key=f"personal-inbound:{order.id}", effective_at=_aware_time(order.created_at), movements=tuple(movements))


def post_inbound_order(db, *, actor, inbound_order_id, material_request_id, idempotency_key, request_id):
    return _post_inbound_order(db, actor=actor, inbound_order_id=inbound_order_id,
        material_request_id=material_request_id, idempotency_key=idempotency_key, request_id=request_id)


def _post_inbound_order(db, *, actor, inbound_order_id, material_request_id, idempotency_key,
        request_id, authority=None, new_order=False):
    from .inventory_posting import _lock_inventory_ledger_head_for_atomic_batch
    # Match reservation/release and stocktake: ledger before principal/request.
    _lock_inventory_ledger_head_for_atomic_batch(db)
    # The parent lock serializes every order for this request. Orders themselves
    # are immutable and the API deliberately has no UPDATE/row-lock privilege.
    request = db.scalar(select(MaterialRequest).where(MaterialRequest.id == material_request_id).with_for_update())
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    order = db.scalar(select(InboundOrder).where(InboundOrder.id == inbound_order_id))
    if order is None: _fail("inbound_not_found", "not_found", "个人仓入账单不存在")
    receipt = db.get(Receipt, order.receipt_id)
    if receipt is None or receipt.status not in {"accepted", "exception"}: _fail("receipt_not_final", "precondition_failed", "收货尚未完成验收")
    bound_requests = set(db.scalars(select(OutboundPosting.request_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == receipt.shipment_id)).all())
    if bound_requests != {material_request_id}: _fail("request_mismatch", "conflict", "入账单不属于当前需求")
    shipment = db.get(Shipment, receipt.shipment_id)
    if shipment is None: _fail("shipment_not_found", "conflict", "收货关联发运不存在")
    _validate_inbound_target(shipment, order.target_location_id, order.target_person_id)
    existing = db.scalar(select(InboundPosting).where(InboundPosting.inbound_order_id == order.id))
    command = _order_posting_command(db, order, create_missing=existing is None)
    if authority is None:
        if new_order:
            _fail("inbound_authority_missing", "forbidden", "本人入账缺少原验收授权")
        result = post_inventory_transaction(db, actor=actor, command=command, idempotency_key=idempotency_key, request_id=request_id)
    else:
        from .inventory_posting import _post_personal_receipt_inventory_transaction
        result = _post_personal_receipt_inventory_transaction(db, actor=actor, command=command,
            idempotency_key=idempotency_key, request_id=request_id, authority=authority)
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
    transaction = db.get(InventoryTransaction, result.transaction_id)
    reference = f"/api/v1/material-requests/{request.id}/inbound-orders/{order.id}/post"
    if authority is not None:
        reference = f"/api/v1/material-requests/{request.id}/my-inbounds"
    if existing is None:
        if new_order:
            _record_order_created(db, row=order, actor=actor, request_id=material_request_id, trace_request_id=request_id)
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
        db.flush()
        record_fulfillment_command(db, request=request, actor=actor, operation="personal_inbound",
            fact=transaction, request_reference=reference, permission_action="receive" if authority is not None else "fulfill")
    else:
        from ..demand_models import MaterialRequestCommand
        original = db.scalar(select(MaterialRequestCommand).where(
            MaterialRequestCommand.idempotency_key_hash == transaction.idempotency_key_hash))
        if original is None:
            _fail("inbound_command_missing", "service_unavailable", "原入账缺少连续版本命令，请保留原请求核验")
        verify_fulfillment_command(db, request=request, actor=actor, operation="personal_inbound",
            fact=transaction, request_reference=reference, expected_version=original.target_version)
    return {"inbound_order_id": order.id, "inventory_transaction_id": result.transaction_id, "replayed": result.replayed}

def list_inbound_orders(db, *, actor, request_id):
    with db.no_autoflush:
        return _list_inbound_orders(db, actor=actor, request_id=request_id)

def _list_inbound_orders(db, *, actor, request_id):
    context = material_request_query._load_read_context(db, actor=actor, now=None)
    request = db.scalar(select(MaterialRequest).where(
        MaterialRequest.id == request_id,
        material_request_query._visible_request_predicate(context),
    ))
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    shipment_ids = select(Shipment.id).join(ShipmentLine, ShipmentLine.shipment_id == Shipment.id).join(OutboundPosting, OutboundPosting.id == ShipmentLine.outbound_posting_id).where(OutboundPosting.request_id == request_id)
    receipt_ids = select(Receipt.id).where(Receipt.shipment_id.in_(shipment_ids))
    rows = tuple(db.scalars(select(InboundOrder).where(InboundOrder.receipt_id.in_(receipt_ids)).order_by(InboundOrder.created_at, InboundOrder.id)).all())
    for row in rows:
        source_ids = tuple(db.scalars(select(OutboundPosting.source_stock_account_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).join(Receipt, Receipt.shipment_id == ShipmentLine.shipment_id).where(Receipt.id == row.receipt_id)).all())
        if source_ids:
            outbound._authorize_account_ids(db, actor, source_ids, action="read", resource="inventory", lock_rows=False)
    return tuple({**_result(row), **_posting_projection(db, row)} for row in rows)

def _posting_projection(db, row):
    posting = db.scalar(select(InboundPosting).where(InboundPosting.inbound_order_id == row.id))
    if posting is None:
        if row.status == "posted" or row.posting_transaction_id is not None:
            _fail("inbound_history_invalid", "service_unavailable", "入账投影缺少不可变库存事实")
        return {"status": row.status, "posting_transaction_id": None}
    transaction = db.get(InventoryTransaction, posting.inventory_transaction_id)
    if (transaction is None or transaction.status != "posted" or transaction.movement_type != "transfer"
        or transaction.source_document_type != "personal_inbound" or transaction.source_document_id != str(row.id)
        or transaction.posting_key != f"personal-inbound:{row.id}"):
        _fail("inbound_history_invalid", "service_unavailable", "入账绑定与原库存事务不一致")
    return {"status": "posted", "posting_transaction_id": transaction.id}
