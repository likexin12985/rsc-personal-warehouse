"""Append-only carrier tracking facts; never imply receipt or inventory inbound."""
from datetime import datetime, timezone
import hashlib, hmac, json, uuid
from sqlalchemy import select
from ..foundation_models import OutboxEvent
from ..demand_models import MaterialRequest
from ..inventory_models import LogisticsEvent, Shipment, ShipmentLine, OutboundPosting
from .audit_chain import append_audit_event
from . import material_request_outbound as outbound

class LogisticsEventError(Exception):
    def __init__(self, code, category, message): self.code, self.category, self.message = code, category, message

def _fail(code, category, message): raise LogisticsEventError(code, category, message)

def _ensure_after_shipping(shipped_at, event_at):
    if shipped_at is not None and event_at < shipped_at:
        _fail("time_invalid", "precondition_failed", "物流事件时间不能早于交运时间")

def create_event(db, *, actor, request_id, shipment_id, event_type, event_at, source, evidence_file_id, external_ref, idempotency_key, secret, trace_request_id):
    if not isinstance(secret, bytes): secret = secret.encode()
    if len(secret) < 32: _fail("secret_invalid", "service_unavailable", "物流事件幂等配置不可用")
    try: when = datetime.fromisoformat(event_at.replace("Z", "+00:00"))
    except ValueError: _fail("time_invalid", "invalid_request", "物流事件时间无效")
    if when.tzinfo is None: _fail("time_invalid", "invalid_request", "物流事件时间必须带时区")
    request = db.get(MaterialRequest, request_id)
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    shipment = db.get(Shipment, shipment_id)
    if shipment is None: _fail("shipment_not_found", "not_found", "发运单不存在")
    _ensure_after_shipping(shipment.shipped_at, when)
    belongs = db.scalar(select(OutboundPosting.request_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == shipment_id))
    if belongs != request_id: _fail("shipment_request_mismatch", "conflict", "发运单不属于当前需求")
    source_ids = tuple(db.scalars(select(OutboundPosting.source_stock_account_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == shipment_id)).all())
    if not source_ids: _fail("source_missing", "conflict", "发运缺少来源库存账户")
    outbound._authorize_account_ids(db, actor, source_ids, action="read", resource="inventory", lock_rows=False)
    path = f"/api/v1/material-requests/{request_id}/shipments/{shipment_id}/logistics-events"
    key_hash = hmac.new(secret, f"{actor.user_id}:POST:{path}:{idempotency_key}".encode(), hashlib.sha256).hexdigest()
    payload_hash = hashlib.sha256(json.dumps({"request_id": str(request_id), "shipment_id": str(shipment_id), "event_type": event_type, "event_at": event_at, "source": source, "evidence_file_id": str(evidence_file_id) if evidence_file_id else None, "external_ref": external_ref}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = db.scalar(select(LogisticsEvent).where(LogisticsEvent.idempotency_key_hash == key_hash))
    if existing is not None:
        if existing.external_ref != external_ref or existing.event_type != event_type or existing.event_at != when: _fail("key_reused", "conflict", "幂等键已绑定其他物流事件")
        return _result(existing, True)
    now = datetime.now(timezone.utc)
    row = LogisticsEvent(id=uuid.uuid4(), shipment_id=shipment_id, event_type=event_type, event_at=when, source=source.strip(), evidence_file_id=evidence_file_id, external_ref=external_ref.strip() if external_ref else None, idempotency_key_hash=key_hash, actor_user_id=actor.user_id, created_at=now)
    db.add(row); db.flush()
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action="logistics_event_registered", aggregate_type="logistics_event", aggregate_id=str(row.id), before_jsonb={}, after_jsonb={"request_id": str(request_id), "shipment_id": str(shipment_id), "event_type": event_type}, request_id=trace_request_id, occurred_at=now, created_at=now)
    db.add(OutboxEvent(event_type="logistics_event_registered", aggregate_type="logistics_event", aggregate_id=str(row.id), payload_jsonb={"request_id": str(request_id), "shipment_id": str(shipment_id), "event_type": event_type, "event_at": when.isoformat()}, status="pending", attempts=0, idempotency_key=f"logistics-event:{row.id}", available_at=now))
    return _result(row, False)

def _result(row, replayed):
    return {"schema_version": "1.0", "event_id": row.id, "shipment_id": row.shipment_id, "event_type": row.event_type, "event_at": row.event_at.isoformat(), "source": row.source, "evidence_file_id": row.evidence_file_id, "external_ref": row.external_ref, "idempotency_replayed": replayed}

def list_events(db, *, actor, request_id, shipment_id):
    request = db.get(MaterialRequest, request_id)
    if request is None: _fail("not_found", "not_found", "需求单不存在")
    shipment = db.get(Shipment, shipment_id)
    if shipment is None: _fail("shipment_not_found", "not_found", "发运单不存在")
    belongs = db.scalar(select(OutboundPosting.request_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == shipment_id))
    if belongs != request_id: _fail("shipment_request_mismatch", "conflict", "发运单不属于当前需求")
    source_ids = tuple(db.scalars(select(OutboundPosting.source_stock_account_id).join(ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == shipment_id)).all())
    if not source_ids: _fail("source_missing", "conflict", "发运缺少来源库存账户")
    outbound._authorize_account_ids(db, actor, source_ids, action="read", resource="inventory", lock_rows=False)
    rows = tuple(db.scalars(select(LogisticsEvent).where(LogisticsEvent.shipment_id == shipment_id).order_by(LogisticsEvent.event_at, LogisticsEvent.created_at, LogisticsEvent.id)).all())
    return tuple(_result(row, False) for row in rows)
