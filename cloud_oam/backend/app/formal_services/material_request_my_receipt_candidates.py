"""SELECT-only preparation for an exact recipient package.

The writer still rechecks identity, custody, policy, version, quantity and SN in
its transaction. A scan match or candidate response is not an acceptance fact.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select

from ..demand_models import MaterialRequestLine
from ..inventory_models import (
    FormalMaterial, InventoryLot, InventorySerial, MaterialInventoryPolicy, OutboundPosting,
    Receipt, ReceiptLine, ReceiptSerial, ShipmentLine, ShipmentSerial, StockAccount,
)
from ..material_request_my_receipt_candidate_schemas import (
    CandidateLineOut, CandidateSerialOut, MyReceiptCandidateOut,
)
from . import material_request_my_receipt as receipt_service
from . import material_request_my_receiving as receiving
from . import material_request_query as query


def _fail(message):
    raise query.MaterialRequestReadError("my_receipt_candidates_invalid", "service_unavailable", message)


def _line(db, *, row, shipment, now):
    fact = db.get(OutboundPosting, row.outbound_posting_id)
    source = db.get(StockAccount, fact.source_stock_account_id)
    request_line = db.get(MaterialRequestLine, fact.request_line_id)
    material = db.get(FormalMaterial, request_line.material_id)
    if material is None:
        _fail("包裹物料不存在")
    policies = tuple(db.scalars(select(MaterialInventoryPolicy).where(
        MaterialInventoryPolicy.material_id == material.id,
        MaterialInventoryPolicy.effective_from <= now,
        or_(MaterialInventoryPolicy.effective_to.is_(None), MaterialInventoryPolicy.effective_to > now),
    )).all())
    if len(policies) != 1:
        _fail("当前物料验收规则不唯一")
    policy = policies[0]
    lot = db.get(InventoryLot, source.lot_id) if source.lot_id else None
    if (source.lot_id is not None and (lot is None or lot.material_id != material.id)) or (policy.tracking_mode in {"lot", "lot_and_serial"} and lot is None):
        _fail("包裹物料的批次证据不完整")
    bound = tuple(db.scalars(select(ShipmentSerial.serial_id).where(
        ShipmentSerial.shipment_line_id == row.id,
    ).order_by(ShipmentSerial.serial_id).limit(1001)).all())
    tracked = policy.tracking_mode in {"serial", "lot_and_serial"}
    if len(bound) > 1000 or bool(bound) != tracked:
        _fail("物料 SN 规则与包裹证据不一致")
    if bound and db.scalar(select(ShipmentSerial.serial_id).join(ShipmentLine).where(
        ShipmentLine.outbound_posting_id == fact.id,
        ShipmentLine.id != row.id, ShipmentSerial.serial_id.in_(bound),
    ).limit(1)) is not None:
        _fail("同一出库 SN 重复绑定多个包裹")
    serials = tuple(db.scalars(select(InventorySerial).where(
        InventorySerial.id.in_(bound),
    ).order_by(InventorySerial.id)).all()) if bound else ()
    if len(serials) != len(bound) or any(s.material_id != material.id or s.lot_id != source.lot_id for s in serials):
        _fail("包裹 SN 物料或批次不一致")
    accepted = rejected = Decimal("0.000")
    used = set()
    has_exception = False
    for line in db.scalars(select(ReceiptLine).where(ReceiptLine.shipment_line_id == row.id)).all():
        receipt = db.get(Receipt, line.receipt_id)
        if receipt is None or receipt.shipment_id != shipment.id or receipt.receiver_person_id != shipment.target_person_id or receipt.status not in {"accepted", "exception"}:
            _fail("已有验收记录与本人包裹不一致")
        good, bad = receiving._quantity(line.accepted_qty), receiving._quantity(line.rejected_qty)
        links = tuple(db.scalars(select(ReceiptSerial).where(ReceiptSerial.receipt_line_id == line.id)).all())
        ids = {s.serial_id for s in links}
        if len(ids) != len(links) or ids & used or not ids <= set(bound):
            _fail("已有验收 SN 重复或不属于本包裹")
        if tracked and (sum(s.accepted for s in links) != good or sum(not s.accepted for s in links) != bad):
            _fail("已有合格或拒收数量与 SN 不一致")
        used.update(ids)
        accepted += good
        rejected += bad
        has_exception = has_exception or line.condition != "normal" or bad > 0
    shipped = receiving._quantity(row.shipped_qty)
    if accepted + rejected > shipped:
        _fail("累计验收超过发运数量")
    remaining = shipped - accepted - rejected
    for qty in (shipped, accepted, rejected, remaining):
        if qty != qty.quantize(Decimal(1).scaleb(-policy.quantity_scale)) or (not policy.allow_fraction and qty != qty.to_integral_value()):
            _fail("包裹数量与当前验收精度规则不一致")
    available = tuple(s for s in serials if s.id not in used)
    if tracked and Decimal(len(available)) != remaining:
        _fail("剩余 SN 与未验收数量不一致")
    return CandidateLineOut(
        shipment_line_id=row.id, request_line_id=request_line.id,
        sku_code=material.sku_code, material_name=material.name, base_unit=material.base_unit,
        shipped_qty=format(shipped, ".3f"), accepted_qty=format(accepted, ".3f"),
        rejected_qty=format(rejected, ".3f"), unconfirmed_qty=format(remaining, ".3f"),
        has_exception=has_exception, lot_no=lot.lot_no if lot else None, tracking_mode=policy.tracking_mode,
        quantity_scale=policy.quantity_scale, allow_fraction=policy.allow_fraction,
        remaining_serials=tuple(CandidateSerialOut(serial_id=s.id, serial_no=s.serial_no, qr_code=s.qr_code) for s in available),
    )


def my_receipt_candidates(db, *, actor, request_id, shipment_id):
    with db.no_autoflush:
        context, request = receipt_service._context(db, actor, request_id)
        version = request.version
        shipment, rows = receipt_service._package(db, context, request, shipment_id)
        now = datetime.now(timezone.utc)
        shipped_at = shipment.shipped_at
        if shipped_at is None:
            _fail("包裹交运时间缺失")
        if shipped_at.tzinfo is None:
            shipped_at = shipped_at.replace(tzinfo=timezone.utc)
        location = receiving._location(db, context=context, shipment=shipment, now=now)
        lines = tuple(_line(db, row=row, shipment=shipment, now=now) for row in rows.values())
        serials = [s.serial_id for line in lines for s in line.remaining_serials]
        if len(serials) != len(set(serials)):
            _fail("包裹不同明细重复使用同一个 SN")
        blocked = None
        if not context.principal.allows(db, "material_request", "receive", target_scope_type="person", target_scope_id=str(context.principal.person_id)):
            blocked = "permission_required"
        elif request.status not in {"approved", "partially_approved"}:
            blocked = "request_not_approved"
        elif shipment.status == "pending_handover" or shipped_at > now:
            blocked = "pending_handover"
        elif all(Decimal(line.unconfirmed_qty) == 0 for line in lines):
            blocked = "complete"
        latest, latest_request = receipt_service._context(db, actor, request_id)
        if latest.principal != context.principal or latest_request.version != version:
            raise query.MaterialRequestReadError("my_receipt_candidates_context_changed", "precondition_failed", "查询期间权限或需求已变化，请刷新")
        receiving._location(db, context=latest, shipment=shipment, now=datetime.now(timezone.utc))
        return MyReceiptCandidateOut(
            request_id=request.id, request_no=request.request_no, request_version=version,
            person_id=context.principal.person_id, shipment_id=shipment.id,
            shipment_no=shipment.shipment_no, shipped_at=shipped_at,
            target_location_name=location.name, checked_at=now,
            can_receive=blocked is None, blocked_reason=blocked, lines=lines,
        )
