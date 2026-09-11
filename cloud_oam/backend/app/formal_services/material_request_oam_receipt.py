"""Read-only OAM receipt evidence bound to an immutable local shipment."""
from datetime import datetime, timezone
import re
import uuid

from sqlalchemy import select

from ..demand_models import MaterialRequest
from ..foundation_models import ExternalObject
from ..inventory_models import OamReceiptEvidence, OutboundPosting, Shipment, ShipmentLine
from . import material_request_outbound as outbound
from . import material_request_query


class OamReceiptEvidenceError(Exception):
    def __init__(self, code, category, message):
        self.code, self.category, self.message = code, category, message


def _fail(code, category, message):
    raise OamReceiptEvidenceError(code, category, message)


def _aware(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def record_oam_receipt_evidence(
    db, *, external_object_id, shipment_id, status, source_time, source_version, payload_sha256
):
    """Insert one validated sync fact; this is an internal projector boundary, not an HTTP write."""
    try:
        external_id = uuid.UUID(str(external_object_id))
        shipment_uuid = uuid.UUID(str(shipment_id))
    except (ValueError, TypeError):
        _fail("coordinate_invalid", "invalid_request", "OAM收货证据坐标无效")
    if status not in {"synced", "exception"} or not isinstance(source_version, str) or not source_version.strip():
        _fail("payload_invalid", "invalid_request", "OAM收货证据状态或版本无效")
    if not isinstance(payload_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
        _fail("payload_invalid", "invalid_request", "OAM收货证据指纹无效")
    if not isinstance(source_time, datetime):
        _fail("payload_invalid", "invalid_request", "OAM收货证据时间无效")
    when = _aware(source_time)
    external = db.get(ExternalObject, external_id)
    if external is None or external.entity_type != "oam_receipt":
        _fail("external_object_not_found", "conflict", "OAM外部对象不存在")
    shipment = db.get(Shipment, shipment_uuid)
    if shipment is None:
        _fail("shipment_not_found", "not_found", "发运单不存在")
    existing = db.scalar(select(OamReceiptEvidence).where(OamReceiptEvidence.external_object_id == external_id))
    if existing is not None:
        if (existing.shipment_id != shipment_uuid or existing.status != status
                or _aware(existing.source_time) != when or existing.source_version != source_version.strip()
                or existing.payload_sha256 != payload_sha256):
            _fail("external_object_reused", "conflict", "OAM外部收货对象已绑定其他证据")
        return existing, True
    row = OamReceiptEvidence(
        id=uuid.uuid4(), external_object_id=external_id, shipment_id=shipment_uuid,
        status=status, source_time=when, source_version=source_version.strip(),
        payload_sha256=payload_sha256, created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row, False


def _result(row):
    return {
        "schema_version": "1.0", "evidence_id": row.id,
        "external_object_id": row.external_object_id, "shipment_id": row.shipment_id,
        "status": row.status, "source_time": _aware(row.source_time),
        "source_version": row.source_version, "payload_sha256": row.payload_sha256,
    }


def list_oam_receipt_evidence(db, *, actor, request_id):
    context = material_request_query._load_read_context(db, actor=actor, now=None)
    request = db.scalar(select(MaterialRequest).where(
        MaterialRequest.id == request_id,
        material_request_query._visible_request_predicate(context),
    ))
    if request is None:
        _fail("not_found", "not_found", "需求单不存在")
    shipment_ids = select(Shipment.id).join(ShipmentLine, ShipmentLine.shipment_id == Shipment.id).join(
        OutboundPosting, OutboundPosting.id == ShipmentLine.outbound_posting_id
    ).where(OutboundPosting.request_id == request_id)
    shipments = tuple(db.scalars(select(Shipment).where(Shipment.id.in_(shipment_ids))).all())
    for shipment in shipments:
        source_ids = tuple(db.scalars(select(OutboundPosting.source_stock_account_id).join(
            ShipmentLine, ShipmentLine.outbound_posting_id == OutboundPosting.id
        ).where(ShipmentLine.shipment_id == shipment.id)).all())
        if source_ids:
            for source_id in source_ids:
                outbound._authorize_account_ids(db, actor, (source_id,), action="read", resource="inventory", lock_rows=False)
    rows = tuple(db.scalars(select(OamReceiptEvidence).where(
        OamReceiptEvidence.shipment_id.in_(shipment_ids)
    ).order_by(OamReceiptEvidence.source_time, OamReceiptEvidence.id)).all())
    return tuple(_result(row) for row in rows)
