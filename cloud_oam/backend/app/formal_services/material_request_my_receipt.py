"""Recipient-only acceptance and exact-command recovery; never posts inventory."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import uuid

from sqlalchemy import func, or_, select

from ..demand_models import MaterialRequest, MaterialRequestLine
from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent, FileObject, OutboxEvent
from ..inventory_models import (
    InventorySerial, MaterialInventoryPolicy, OutboundPosting, OutboundPostingSerial, Receipt, ReceiptException, ReceiptLine,
    ReceiptSerial, Shipment, ShipmentLine, ShipmentSerial, StockAccount,
)
from ..material_request_my_receipt_schemas import MyReceiptIn, MyReceiptLineOut, MyReceiptOut
from . import material_request_my_receiving as receiving
from . import material_request_query as query
from .audit_chain import append_audit_event
from .material_request_fulfillment_command import record_fulfillment_command, verify_fulfillment_command


def _fail(code, category, message):
    raise query.MaterialRequestReadError(f"my_receipt_{code}", category, message)


def _key(actor, request_id, key, secret):
    secret = secret.encode() if isinstance(secret, str) else secret
    if not isinstance(secret, bytes) or len(secret) < 32:
        _fail("secret_invalid", "service_unavailable", "验收幂等配置不可用")
    if not isinstance(key, str) or not 16 <= len(key) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        _fail("key_invalid", "invalid_request", "验收幂等键无效")
    path = f"/api/v1/material-requests/{request_id}/my-receipts"
    return hmac.new(secret, f"{actor.user_id}:POST:{path}:{key}".encode(), hashlib.sha256).hexdigest()


def request_hash(request_id, person_id, payload):
    value = payload.model_dump(mode="json")
    value["lines"].sort(key=lambda row: row["shipment_line_id"])
    for line in value["lines"]:
        line["accepted_serial_ids"].sort()
        line["rejected_serial_ids"].sort()
    value.update(request_id=str(request_id), person_id=str(person_id))
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _context(db, actor, request_id, *, write=False):
    if write:
        lock_formal_principal_graph(db, (actor.user_id,))
    context = query._load_read_context(db, actor=actor, now=None)
    current = context.principal
    permissions = [("inventory", "read")]
    if write:
        permissions.append(("material_request", "receive"))
    for resource, action in permissions:
        if not current.allows(db, resource, action, target_scope_type="person", target_scope_id=str(current.person_id)):
            _fail("forbidden", "forbidden", "没有本人验收所需权限")
    statement = select(MaterialRequest).where(MaterialRequest.id == request_id, query._visible_request_predicate(context))
    if write:
        statement = statement.with_for_update()
    request = db.scalar(statement.execution_options(populate_existing=True))
    if request is None:
        _fail("not_found", "not_found", "需求单不存在")
    return context, request


def _package(db, context, request, shipment_id):
    shipment = db.get(Shipment, shipment_id, populate_existing=True)
    if shipment is None or shipment.target_person_id != context.principal.person_id:
        _fail("shipment_not_found", "not_found", "本人包裹不存在")
    receiving._location(db, context=context, shipment=shipment, now=datetime.now(timezone.utc))
    rows = tuple(db.scalars(select(ShipmentLine).where(ShipmentLine.shipment_id == shipment.id).order_by(ShipmentLine.id).limit(101)).all())
    if not 1 <= len(rows) <= 100:
        _fail("history_invalid", "service_unavailable", "包裹明细不完整")
    for row in rows:
        fact = db.get(OutboundPosting, row.outbound_posting_id)
        line = db.get(MaterialRequestLine, fact.request_line_id) if fact else None
        source = db.get(StockAccount, fact.source_stock_account_id) if fact else None
        if fact is None or line is None or source is None or fact.request_id != request.id or line.request_id != request.id or row.outbound_line_id != fact.outbound_line_id or source.location_id != shipment.source_location_id or source.material_id != line.material_id:
            _fail("history_invalid", "service_unavailable", "包裹与需求出库事实不一致")
        try:
            receiving.outbound.verified_outbound_history(db, fact=fact, request=request)
        except Exception as exc:
            if hasattr(exc, "category") and hasattr(exc, "code"):
                _fail("history_invalid", "service_unavailable", "原出库证据不完整")
            raise
        total = db.scalar(select(func.sum(ShipmentLine.shipped_qty)).where(ShipmentLine.outbound_posting_id == fact.id))
        if receiving._quantity(row.shipped_qty) <= 0 or receiving._quantity(total) > fact.outbound_qty:
            _fail("history_invalid", "service_unavailable", "累计发运超过出库事实")
        original_serials = set(db.scalars(select(OutboundPostingSerial.serial_id).where(OutboundPostingSerial.posting_id == fact.id)).all())
        bound_serials = set(db.scalars(select(ShipmentSerial.serial_id).where(ShipmentSerial.shipment_line_id == row.id)).all())
        if original_serials and (row.shipped_qty != row.shipped_qty.to_integral_value() or len(bound_serials) != int(row.shipped_qty) or not bound_serials <= original_serials):
            _fail("history_invalid", "service_unavailable", "包裹 SN 与原出库事实不一致")
        if not original_serials and bound_serials:
            _fail("history_invalid", "service_unavailable", "包裹包含原出库之外的 SN")
    return shipment, {row.id: row for row in rows}


def _evidence(db, actor, line):
    if line.exception_evidence_file_id is None:
        return
    file = db.get(FileObject, line.exception_evidence_file_id, populate_existing=True)
    if file is None or file.status != "available" or file.uploaded_by != actor.user_id:
        _fail("evidence_unavailable", "precondition_failed", "异常证据必须是本人已完成上传的文件")


def _serials(db, row, line, when):
    bound = set(db.scalars(select(ShipmentSerial.serial_id).where(ShipmentSerial.shipment_line_id == row.id)).all())
    used = set(db.scalars(select(ReceiptSerial.serial_id).join(ReceiptLine).where(ReceiptLine.shipment_line_id == row.id)).all())
    accepted, rejected = set(line.accepted_serial_ids), set(line.rejected_serial_ids)
    fact = db.get(OutboundPosting, row.outbound_posting_id)
    request_line = db.get(MaterialRequestLine, fact.request_line_id)
    policies = tuple(db.scalars(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id == request_line.material_id, MaterialInventoryPolicy.effective_from <= when, or_(MaterialInventoryPolicy.effective_to.is_(None), MaterialInventoryPolicy.effective_to > when))).all())
    if len(policies) != 1:
        _fail("policy_invalid", "precondition_failed", "验收时点的物料数量规则不唯一")
    policy = policies[0]
    for qty in (line.accepted_qty, line.rejected_qty):
        if qty != qty.quantize(Decimal(1).scaleb(-policy.quantity_scale)) or (not policy.allow_fraction and qty != qty.to_integral_value()):
            _fail("quantity_invalid", "precondition_failed", "验收数量不符合物料精度规则")
    if bool(bound) != (policy.tracking_mode in {"serial", "lot_and_serial"}):
        _fail("policy_invalid", "precondition_failed", "物料 SN 规则与发运证据不一致")
    if not bound:
        if accepted or rejected:
            _fail("serial_invalid", "precondition_failed", "非 SN 发运不能登记 SN")
        return
    for qty, serial_ids in ((line.accepted_qty, accepted), (line.rejected_qty, rejected)):
        if qty != qty.to_integral_value() or len(serial_ids) != int(qty):
            _fail("serial_invalid", "precondition_failed", "合格和拒收数量必须各自对应逐件 SN")
    if not (accepted | rejected) <= bound - used:
        _fail("serial_invalid", "precondition_failed", "SN 不属于本包裹或已经验收")
    for serial_id in accepted | rejected:
        serial = db.get(InventorySerial, serial_id)
        if serial is None or serial.material_id != request_line.material_id:
            _fail("serial_invalid", "precondition_failed", "SN 与包裹物料不一致")


def create_my_receipt(db, *, actor, request_id, payload, idempotency_key, secret, trace_request_id):
    payload = MyReceiptIn.model_validate(payload)
    if not isinstance(trace_request_id, str) or not 8 <= len(trace_request_id) <= 160 or any(ord(c) < 33 or ord(c) > 126 for c in trace_request_id):
        _fail("trace_invalid", "invalid_request", "验收请求追踪坐标无效")
    key = _key(actor, request_id, idempotency_key, secret)
    context, request = _context(db, actor, request_id, write=True)
    digest = request_hash(request_id, context.principal.person_id, payload)
    old = db.scalar(select(Receipt).where(Receipt.idempotency_key_hash == key))
    if old is not None:
        if old.request_hash != digest:
            _fail("key_reused", "conflict", "原幂等键已绑定其他验收内容")
        return _result(db, context, request, old, replayed=True)
    if request.version != payload.expected_request_version:
        _fail("version_conflict", "conflict", "需求版本已变化，请刷新后重新核对")
    if request.status not in {"approved", "partially_approved"}:
        _fail("state_invalid", "precondition_failed", "需求当前状态不能新增验收")
    shipment, rows = _package(db, context, request, payload.shipment_id)
    when = datetime.fromisoformat(payload.received_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    shipped_at = shipment.shipped_at
    if shipped_at is None or shipment.status == "pending_handover":
        _fail("shipment_not_handed_over", "precondition_failed", "包裹尚未交运")
    if shipped_at.tzinfo is None:
        shipped_at = shipped_at.replace(tzinfo=timezone.utc)
    if when < shipped_at or when > datetime.now(timezone.utc):
        _fail("time_invalid", "precondition_failed", "验收时间应在交运之后且不晚于当前时间")
    for line in payload.lines:
        row = rows.get(line.shipment_line_id)
        if row is None:
            _fail("line_invalid", "precondition_failed", "验收明细不属于本人当前包裹")
        received = db.scalar(select(func.coalesce(func.sum(ReceiptLine.accepted_qty + ReceiptLine.rejected_qty), 0)).where(ReceiptLine.shipment_line_id == row.id))
        if receiving._quantity(received) + line.accepted_qty + line.rejected_qty > row.shipped_qty:
            _fail("quantity_exceeded", "precondition_failed", "累计验收不能超过本包裹发运数量")
        _serials(db, row, line, when)
        _evidence(db, context.principal, line)
    now = datetime.now(timezone.utc)
    receipt = Receipt(id=uuid.uuid4(), receipt_no=f"RCT-{now:%Y%m%d}-{uuid.uuid4().hex[:12].upper()}", shipment_id=shipment.id, receiver_person_id=context.principal.person_id, status="accepted" if all(x.condition == "normal" for x in payload.lines) else "exception", received_at=when, request_hash=digest, idempotency_key_hash=key, created_at=now)
    db.add(receipt)
    db.flush()
    for line in payload.lines:
        row = ReceiptLine(id=uuid.uuid4(), receipt_id=receipt.id, shipment_line_id=line.shipment_line_id, accepted_qty=line.accepted_qty, rejected_qty=line.rejected_qty, condition=line.condition, created_at=now)
        db.add(row)
        db.flush()
        db.add_all(ReceiptSerial(receipt_line_id=row.id, serial_id=s, accepted=accepted) for accepted, ids in ((True, line.accepted_serial_ids), (False, line.rejected_serial_ids)) for s in ids)
        if line.condition != "normal" or line.exception_evidence_file_id is not None:
            db.add(ReceiptException(id=uuid.uuid4(), receipt_id=receipt.id, receipt_line_id=row.id, exception_type=line.condition, detail=f"本人验收条件={line.condition};拒收数量={line.rejected_qty:.3f}", evidence_file_id=line.exception_evidence_file_id, created_at=now))
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action="my_receipt_registered", aggregate_type="receipt", aggregate_id=str(receipt.id), before_jsonb={}, after_jsonb={"request_id": str(request.id), "receipt_no": receipt.receipt_no, "request_hash": digest, "command": payload.model_dump(mode="json")}, request_id=trace_request_id, occurred_at=now, created_at=now)
    db.add(OutboxEvent(event_type="receipt_registered", aggregate_type="receipt", aggregate_id=str(receipt.id), payload_jsonb={"request_id": str(request.id), "receipt_no": receipt.receipt_no, "shipment_id": str(shipment.id)}, status="pending", attempts=0, idempotency_key=f"receipt:{receipt.id}", available_at=now))
    db.flush()
    record_fulfillment_command(db, request=request, actor=context.principal, operation="receipt", fact=receipt,
        request_reference=f"/api/v1/material-requests/{request.id}/my-receipts", permission_action="receive")
    return _result(db, context, request, receipt, replayed=False)


def _result(db, context, request, receipt, *, replayed):
    shipment, rows = _package(db, context, request, receipt.shipment_id)
    if receipt.receiver_person_id != context.principal.person_id or receipt.status not in {"accepted", "exception"}:
        _fail("history_invalid", "service_unavailable", "验收结果未绑定当前人员")
    audit = tuple(db.scalars(select(AuditEvent).where(AuditEvent.action == "my_receipt_registered", AuditEvent.aggregate_type == "receipt", AuditEvent.aggregate_id == str(receipt.id))).all())
    if len(audit) != 1 or audit[0].actor_user_id != context.principal.user_id or audit[0].after_jsonb.get("request_hash") != receipt.request_hash or audit[0].after_jsonb.get("request_id") != str(request.id):
        _fail("history_invalid", "service_unavailable", "原验收请求证据不完整")
    try:
        original = MyReceiptIn.model_validate(audit[0].after_jsonb["command"])
    except (ValueError, KeyError, TypeError):
        _fail("history_invalid", "service_unavailable", "原验收请求证据不完整")
    if request_hash(request.id, context.principal.person_id, original) != receipt.request_hash or original.shipment_id != shipment.id:
        _fail("history_invalid", "service_unavailable", "验收请求指纹不一致")
    verify_fulfillment_command(db, request=request, actor=context.principal, operation="receipt", fact=receipt,
        request_reference=f"/api/v1/material-requests/{request.id}/my-receipts", expected_version=original.expected_request_version + 1)
    original_at = datetime.fromisoformat(original.received_at.replace("Z", "+00:00"))
    stored_at = receipt.received_at.replace(tzinfo=timezone.utc) if receipt.received_at.tzinfo is None else receipt.received_at
    if original_at != stored_at:
        _fail("history_invalid", "service_unavailable", "验收时间与原请求不一致")
    lines = tuple(db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id).order_by(ReceiptLine.shipment_line_id)).all())
    if not lines or len(lines) > 100:
        _fail("history_invalid", "service_unavailable", "验收明细不完整")
    output = []
    for line in lines:
        if line.shipment_line_id not in rows:
            _fail("history_invalid", "service_unavailable", "验收明细与本人包裹不一致")
        serials = tuple(db.scalars(select(ReceiptSerial).where(ReceiptSerial.receipt_line_id == line.id).order_by(ReceiptSerial.serial_id)).all())
        exceptions = tuple(db.scalars(select(ReceiptException).where(ReceiptException.receipt_line_id == line.id)).all())
        if len(exceptions) > 1 or (line.condition != "normal" and not exceptions):
            _fail("history_invalid", "service_unavailable", "验收异常证据不完整")
        output.append(MyReceiptLineOut(receipt_line_id=line.id, shipment_line_id=line.shipment_line_id, accepted_qty=line.accepted_qty, rejected_qty=line.rejected_qty, condition=line.condition, accepted_serial_ids=tuple(s.serial_id for s in serials if s.accepted), rejected_serial_ids=tuple(s.serial_id for s in serials if not s.accepted), exception_evidence_file_id=exceptions[0].evidence_file_id if exceptions else None))
    normalized = lambda line: {**line.model_dump(mode="json", exclude={"receipt_line_id"}), "accepted_serial_ids": sorted(str(s) for s in line.accepted_serial_ids), "rejected_serial_ids": sorted(str(s) for s in line.rejected_serial_ids)}
    if [normalized(x) for x in output] != [normalized(x) for x in sorted(original.lines, key=lambda x: str(x.shipment_line_id))]:
        _fail("history_invalid", "service_unavailable", "验收明细与原请求不一致")
    latest = query._load_read_context(db, actor=context.principal, now=None)
    if latest.principal != context.principal:
        _fail("context_changed", "precondition_failed", "验收期间权限已变化")
    return MyReceiptOut(request_id=request.id, person_id=context.principal.person_id, receipt_id=receipt.id, receipt_no=receipt.receipt_no, shipment_id=shipment.id, received_at=receipt.received_at.replace(tzinfo=timezone.utc) if receipt.received_at.tzinfo is None else receipt.received_at, status=receipt.status, request_hash=receipt.request_hash, lines=tuple(output), idempotency_replayed=replayed)


def my_receipt_command_status(db, *, actor, request_id, idempotency_key, secret):
    with db.no_autoflush:
        context, request = _context(db, actor, request_id)
        key = _key(actor, request_id, idempotency_key, secret)
        row = db.scalar(select(Receipt).where(Receipt.idempotency_key_hash == key))
        return None if row is None else _result(db, context, request, row, replayed=True)
