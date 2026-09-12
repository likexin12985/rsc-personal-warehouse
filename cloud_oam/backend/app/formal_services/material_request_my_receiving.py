"""Read packages bound to the current recipient, without granting source access.

This projection cannot post receipts/inventory or serve as command recovery.
Historical source evidence is checked internally, then projected to an explicit
recipient field allowlist. Source account IDs, balances and other recipients
never enter this response.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select

from ..demand_models import MaterialRequest, MaterialRequestLine
from ..inventory_models import (
    CustodyAssignment, FormalMaterial, OutboundPosting, Receipt, ReceiptLine,
    Shipment, ShipmentLine, StockAccount, StockLocation,
)
from ..material_request_my_receiving_schemas import MyReceivingOut, ReceivingLineOut, ReceivingPackageOut
from . import material_request_outbound as outbound
from . import material_request_query as query


def _fail(code, category, message):
    raise query.MaterialRequestReadError(f"my_receiving_{code}", category, message)


def _quantity(value):
    value = Decimal(value)
    if not value.is_finite() or value < 0 or value.as_tuple().exponent < -3:
        _fail("history_invalid", "service_unavailable", "包裹数量证据不完整")
    return value


def _location(db, *, context, shipment, now):
    rows = tuple(db.scalars(select(StockLocation).where(
        StockLocation.location_type == "personal",
        StockLocation.custodian_person_id == context.principal.person_id,
        StockLocation.status == "active",
    )).all())
    if len(rows) != 1 or rows[0].id != shipment.target_location_id:
        _fail("location_invalid", "precondition_failed", "包裹目的地与本人有效个人仓不一致")
    location = rows[0]
    if not context.organizations.descends_from(context.actor_organization_id, location.owner_org_id, require_active_path=True):
        _fail("location_invalid", "precondition_failed", "个人仓所属组织与当前人员不一致")
    if db.scalar(select(StockLocation.id).where(StockLocation.parent_id == location.id).limit(1)) is not None:
        _fail("location_invalid", "precondition_failed", "个人仓位置不是独立叶子库位")
    custody = tuple(db.scalars(select(CustodyAssignment).where(
        CustodyAssignment.location_id == location.id,
        CustodyAssignment.valid_from <= now,
        or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > now),
    )).all())
    if len(custody) != 1 or custody[0].custodian_person_id != context.principal.person_id:
        _fail("custody_invalid", "precondition_failed", "个人仓保管责任未确认")
    return location


def list_my_receiving(db, *, actor, request_id, limit=20, after_id=None):
    # A read must not flush unrelated pending changes from a caller's session.
    with db.no_autoflush:
        return _list(db, actor=actor, request_id=request_id, limit=limit, after_id=after_id)


def _list(db, *, actor, request_id, limit, after_id):
    if type(limit) is not int or not 1 <= limit <= 20:
        _fail("limit_invalid", "invalid_request", "包裹分页参数无效")
    context = query._load_read_context(db, actor=actor, now=None)
    current = context.principal
    if not current.allows(db, "inventory", "read", target_scope_type="person", target_scope_id=str(current.person_id)):
        _fail("forbidden", "forbidden", "没有本人库存读取权限")
    request = db.scalar(select(MaterialRequest).where(
        MaterialRequest.id == request_id, query._visible_request_predicate(context),
    ).execution_options(populate_existing=True))
    if request is None:
        _fail("not_found", "not_found", "需求单不存在")
    version = request.version
    linked = select(ShipmentLine.shipment_id).join(
        OutboundPosting, OutboundPosting.id == ShipmentLine.outbound_posting_id,
    ).where(OutboundPosting.request_id == request.id)
    statement = select(Shipment).where(
        Shipment.id.in_(linked), Shipment.target_person_id == current.person_id,
    )
    if after_id is not None:
        statement = statement.where(Shipment.id > after_id)
    shipments = tuple(db.scalars(statement.order_by(Shipment.id).limit(limit + 1)).all())
    packages = []
    now = datetime.now(timezone.utc)
    for shipment in shipments[:limit]:
        location = _location(db, context=context, shipment=shipment, now=now)
        rows = tuple(db.scalars(select(ShipmentLine).where(
            ShipmentLine.shipment_id == shipment.id,
        ).order_by(ShipmentLine.id).limit(101)).all())
        if not 1 <= len(rows) <= 100:
            _fail("history_invalid", "service_unavailable", "包裹明细不完整")
        lines = []
        for row in rows:
            fact = db.get(OutboundPosting, row.outbound_posting_id)
            if fact is None or fact.request_id != request.id or fact.outbound_line_id != row.outbound_line_id:
                _fail("history_invalid", "service_unavailable", "包裹与需求出库事实不一致")
            source = db.get(StockAccount, fact.source_stock_account_id)
            request_line = db.get(MaterialRequestLine, fact.request_line_id)
            if source is None or request_line is None or request_line.request_id != request.id or source.location_id != shipment.source_location_id or source.material_id != request_line.material_id:
                _fail("history_invalid", "service_unavailable", "包裹物料来源不完整")
            try:
                outbound.verified_outbound_history(db, fact=fact, request=request, lock_audit=False)
            except outbound.MaterialRequestOutboundError:
                _fail("history_invalid", "service_unavailable", "包裹出库证据未通过校验")
            material = db.get(FormalMaterial, request_line.material_id)
            if material is None:
                _fail("history_invalid", "service_unavailable", "包裹物料不存在")
            shipped = _quantity(row.shipped_qty)
            total_shipped = db.scalar(select(func.sum(ShipmentLine.shipped_qty)).where(ShipmentLine.outbound_posting_id == fact.id))
            if shipped == 0 or _quantity(total_shipped) > fact.outbound_qty:
                _fail("history_invalid", "service_unavailable", "发运数量与出库事实不一致")
            accepted = rejected = Decimal("0.000")
            has_exception = False
            for receipt_line in db.scalars(select(ReceiptLine).where(ReceiptLine.shipment_line_id == row.id)).all():
                receipt = db.get(Receipt, receipt_line.receipt_id)
                if receipt is None or receipt.shipment_id != shipment.id or receipt.receiver_person_id != current.person_id or receipt.status == "draft":
                    _fail("history_invalid", "service_unavailable", "收货记录与本人包裹不一致")
                accepted += _quantity(receipt_line.accepted_qty)
                rejected += _quantity(receipt_line.rejected_qty)
                has_exception = has_exception or receipt_line.condition != "normal" or receipt_line.rejected_qty > 0
            if accepted + rejected > shipped:
                _fail("history_invalid", "service_unavailable", "累计验收超过发运数量")
            lines.append(ReceivingLineOut(
                shipment_line_id=row.id, request_line_id=request_line.id, sku_code=material.sku_code,
                material_name=material.name, base_unit=material.base_unit,
                shipped_qty=format(shipped, ".3f"), accepted_qty=format(accepted, ".3f"),
                rejected_qty=format(rejected, ".3f"), unconfirmed_qty=format(shipped - accepted - rejected, ".3f"),
                has_exception=has_exception,
            ))
        packages.append(ReceivingPackageOut(
            shipment_id=shipment.id, shipment_no=shipment.shipment_no, shipment_status=shipment.status,
            carrier=shipment.carrier, tracking_no=shipment.tracking_no, shipped_at=shipment.shipped_at,
            target_location_id=location.id, target_location_name=location.name, lines=tuple(lines),
        ))
    latest = query._load_read_context(db, actor=actor, now=None)
    if latest.principal != current or db.scalar(select(MaterialRequest.version).where(MaterialRequest.id == request.id)) != version:
        _fail("context_changed", "precondition_failed", "查询期间权限或需求已变化，请刷新")
    return MyReceivingOut(
        request_id=request.id, request_no=request.request_no, request_version=version, person_id=current.person_id,
        packages=tuple(packages), next_after_id=shipments[limit - 1].id if len(shipments) > limit else None,
    )
