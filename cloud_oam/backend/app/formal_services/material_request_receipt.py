"""Append-only receipt acceptance facts; no inventory posting is performed here."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib, hmac, json, uuid
from sqlalchemy import func, select
from ..demand_models import MaterialRequest
from ..inventory_models import Shipment, ShipmentLine, ShipmentSerial, OutboundPosting, Receipt, ReceiptLine, ReceiptSerial, ReceiptException
from .audit_chain import append_audit_event
from . import material_request_outbound as outbound

class ReceiptError(Exception):
    def __init__(self, code, category, message): self.code, self.category, self.message = code, category, message
def _fail(code, category, message): raise ReceiptError(code, category, message)
def _qty(v): return format(Decimal(v), ".3f")

def create_receipt(db, *, actor, request_id, expected_version, receiver_person_id, received_at, lines, idempotency_key, secret, trace_request_id):
    if not isinstance(secret, bytes): secret = secret.encode()
    if len(secret) < 32: _fail("secret_invalid", "service_unavailable", "收货幂等配置不可用")
    try: when = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    except ValueError: _fail("time_invalid", "invalid_request", "收货时间无效")
    if when.tzinfo is None: _fail("time_invalid", "invalid_request", "收货时间必须带时区")
    if len({x.shipment_line_id for x in lines}) != len(lines): _fail("duplicate_line", "invalid_request", "收货明细重复")
    path = f"/api/v1/material-requests/{request_id}/receipts"
    key_hash = hmac.new(secret, f"{actor.user_id}:POST:{path}:{idempotency_key}".encode(), hashlib.sha256).hexdigest()
    payload_hash = hashlib.sha256(json.dumps({"request_id": str(request_id), "version": expected_version, "receiver": str(receiver_person_id), "at": received_at, "lines": [{"id": str(x.shipment_line_id), "a": _qty(x.accepted_qty), "r": _qty(x.rejected_qty), "condition": x.condition, "serials": sorted(str(s) for s in x.serial_ids)} for x in lines]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    request = db.scalar(select(MaterialRequest).where(MaterialRequest.id == request_id).with_for_update())
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    if request.version != expected_version: _fail("version_conflict", "conflict", "需求版本已变化，请重新读取")
    old = db.scalar(select(Receipt).where(Receipt.idempotency_key_hash == key_hash))
    if old is not None:
        if old.request_hash != payload_hash: _fail("key_reused", "conflict", "幂等键已绑定其他收货内容")
        return _result(db, old, replayed=True)
    now = datetime.now(timezone.utc); checked = []; shipment_id = None
    for line in lines:
        shipment_line = db.get(ShipmentLine, line.shipment_line_id)
        if shipment_line is None: _fail("shipment_line_not_found", "not_found", "发运明细不存在")
        shipment = db.get(Shipment, shipment_line.shipment_id)
        if shipment is None: _fail("shipment_not_found", "not_found", "发运单不存在")
        if shipment_id is None: shipment_id = shipment.id
        elif shipment_id != shipment.id: _fail("shipment_mismatch", "invalid_request", "一次收货只能对应一个发运单")
        total = Decimal(line.accepted_qty) + Decimal(line.rejected_qty)
        received = Decimal(db.scalar(select(func.coalesce(func.sum(ReceiptLine.accepted_qty + ReceiptLine.rejected_qty), 0)).where(ReceiptLine.shipment_line_id == shipment_line.id)) or 0)
        if total <= 0 or received + total > shipment_line.shipped_qty: _fail("quantity_exceeded", "precondition_failed", "累计收货超过已发运数量")
        bound = set(db.scalars(select(ShipmentSerial.serial_id).where(ShipmentSerial.shipment_line_id == shipment_line.id)).all())
        given = set(line.serial_ids); used = set(db.scalars(select(ReceiptSerial.serial_id).join(ReceiptLine).where(ReceiptLine.shipment_line_id == shipment_line.id)).all())
        if bound and (len(given) != int(total) or not given <= bound - used): _fail("serial_mismatch", "precondition_failed", "收货 SN 与发运事实不一致")
        if not bound and given: _fail("serial_mismatch", "invalid_request", "非 SN 物料不得提交 SN")
        checked.append((shipment_line, line, total, given))
    receipt = Receipt(id=uuid.uuid4(), receipt_no=f"RCT-{now:%Y%m%d}-{uuid.uuid4().hex[:12].upper()}", shipment_id=shipment_id, status="accepted" if all(Decimal(x.accepted_qty) > 0 and Decimal(x.rejected_qty) == 0 for _, x, _, _ in checked) else "exception", received_at=when, receiver_person_id=receiver_person_id, request_hash=payload_hash, idempotency_key_hash=key_hash, created_at=now)
    db.add(receipt); db.flush(); output = []
    for shipment_line, line, total, serials in checked:
        row = ReceiptLine(id=uuid.uuid4(), receipt_id=receipt.id, shipment_line_id=shipment_line.id, accepted_qty=line.accepted_qty, rejected_qty=line.rejected_qty, condition=line.condition, created_at=now); db.add(row); db.flush(); db.add_all(ReceiptSerial(receipt_line_id=row.id, serial_id=s, accepted=s in serials) for s in serials)
        if Decimal(line.rejected_qty) > 0 or line.condition != "normal":
            db.add(ReceiptException(id=uuid.uuid4(), receipt_id=receipt.id, receipt_line_id=row.id, exception_type=line.condition, detail=f"验收条件={line.condition};拒收数量={_qty(line.rejected_qty)}", evidence_file_id=None, created_at=now))
        output.append({"receipt_line_id": row.id, "shipment_line_id": row.shipment_line_id, "accepted_qty": _qty(row.accepted_qty), "rejected_qty": _qty(row.rejected_qty), "serial_ids": tuple(serials)})
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action="receipt_registered", aggregate_type="receipt", aggregate_id=str(receipt.id), before_jsonb={}, after_jsonb={"request_id": str(request_id), "receipt_no": receipt.receipt_no}, request_id=trace_request_id, occurred_at=now, created_at=now)
    return _result(db, receipt, replayed=False, lines=output)

def _result(db, receipt, replayed, lines=None):
    if lines is None: lines = tuple({"receipt_line_id": x.id, "shipment_line_id": x.shipment_line_id, "accepted_qty": _qty(x.accepted_qty), "rejected_qty": _qty(x.rejected_qty), "serial_ids": tuple(db.scalars(select(ReceiptSerial.serial_id).where(ReceiptSerial.receipt_line_id == x.id)).all())} for x in db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id)).all())
    exceptions = tuple({"exception_id": x.id, "receipt_line_id": x.receipt_line_id, "exception_type": x.exception_type, "detail": x.detail, "evidence_file_id": x.evidence_file_id} for x in db.scalars(select(ReceiptException).where(ReceiptException.receipt_id == receipt.id).order_by(ReceiptException.created_at, ReceiptException.id)).all())
    return {"schema_version": "1.0", "receipt_id": receipt.id, "receipt_no": receipt.receipt_no, "shipment_id": receipt.shipment_id, "status": receipt.status, "lines": tuple(lines), "exceptions": exceptions, "idempotency_replayed": replayed}

def list_receipts(db, *, actor, request_id):
    request = db.get(MaterialRequest, request_id)
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    shipment_ids = select(Shipment.id).join(ShipmentLine, ShipmentLine.shipment_id == Shipment.id).join(OutboundPosting, OutboundPosting.id == ShipmentLine.outbound_posting_id).where(OutboundPosting.request_id == request_id)
    rows = tuple(db.scalars(select(Receipt).where(Receipt.shipment_id.in_(shipment_ids)).order_by(Receipt.created_at, Receipt.id)).all())
    for row in rows:
        source_ids = tuple(db.scalars(select(OutboundPosting.source_stock_account_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == row.shipment_id)).all())
        if source_ids:
            outbound._authorize_account_ids(db, actor, source_ids, action="read", resource="inventory", lock_rows=False)
    return tuple(_result(db, row, replayed=False) for row in rows)
