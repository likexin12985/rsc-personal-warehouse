"""Read exact regional return parcels for their currently bound receiver.

No sender login, source-warehouse grant, acceptance, or stock mutation occurs.
An unreadable parcel is isolated in a list; detail requires its complete proof.
"""
from datetime import datetime, timezone

from sqlalchemy import or_, select

from ..foundation_models import Organization
from ..inventory_models import CustodyAssignment, Shipment, StockLocation
from ..stock_operation_models import StockOperationOrder, StockOperationShipment
from ..stock_return_receiving_schemas import (
    StockReturnReceivingBlockedOut, StockReturnReceivingDetailOut, StockReturnReceivingLineOut,
    StockReturnReceivingOut, StockReturnReceivingPackageOut,
)
from . import inventory_posting as posting, inventory_query as inventory
from . import stock_return_shipment_facts as facts
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_return_sources import _fail


def _authorize(db, actor):
    current = posting._require_current_actor(db, actor)
    if (not {"admin", "provincial_manager"}.intersection(current.role_codes)
            or not current.allows(db, "stock_operation", "read",
                target_scope_type="person", target_scope_id=str(current.person_id))):
        _fail("stock_return_receiving_forbidden", "没有区域仓退回接收查询权限", 403)
    return current


def _locations(db, actor):
    now = datetime.now(timezone.utc)
    rows = tuple(db.scalars(select(StockLocation).where(
        StockLocation.location_type == "region", StockLocation.status == "active",
        StockLocation.custodian_person_id == actor.person_id,
    ).order_by(StockLocation.id).limit(1001).execution_options(populate_existing=True)))
    if len(rows) > 1000:
        _fail("stock_return_receiving_scope_limit", "接收仓范围超过完整核验上限", 503)
    result = {}
    for location in rows:
        organization = db.get(Organization, location.owner_org_id, populate_existing=True)
        if organization is None or organization.status != "active":
            continue
        if all(actor.allows(db, resource, "read", target_scope_type="organization",
                target_scope_id=str(location.owner_org_id)) for resource in ("stock_operation", "inventory")):
            assignments = tuple(db.execute(select(CustodyAssignment.id, CustodyAssignment.custodian_person_id,
                CustodyAssignment.valid_from, CustodyAssignment.valid_to).where(
                    CustodyAssignment.location_id == location.id, CustodyAssignment.valid_from <= now,
                    or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > now),
                ).order_by(CustodyAssignment.id).limit(2)))
            result[location.id] = (location.name, location.owner_org_id, location.custodian_person_id, assignments)
    return result


def _parcels(db, actor, locations, *, limit, after_id=None, shipment_id=None):
    statement = select(StockOperationShipment).join(Shipment, Shipment.id == StockOperationShipment.id).where(
        Shipment.target_person_id == actor.person_id, Shipment.target_location_id.in_(locations))
    if shipment_id is not None:
        statement = statement.where(StockOperationShipment.id == shipment_id)
    if after_id is not None:
        statement = statement.where(StockOperationShipment.id > after_id)
    return tuple(db.scalars(statement.order_by(StockOperationShipment.id).limit(limit)
        .execution_options(populate_existing=True)))


def _package(db, actor, fact, locations):
    header = db.get(Shipment, fact.id, populate_existing=True)
    if header is None or header.target_person_id != actor.person_id or header.target_location_id not in locations:
        _fail("stock_return_receiving_not_found", "本人当前接收范围内没有此退回包裹", 404)
    now = datetime.now(timezone.utc)
    assignments = tuple(db.scalars(select(CustodyAssignment).where(
        CustodyAssignment.location_id == header.target_location_id, CustodyAssignment.valid_from <= now,
        or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > now),
    ).limit(2).execution_options(populate_existing=True)))
    if (len(assignments) != 1 or assignments[0].id != fact.target_custody_assignment_id
            or assignments[0].custodian_person_id != actor.person_id
            or tuple((row.id, row.custodian_person_id, row.valid_from, row.valid_to) for row in assignments)
                != locations[header.target_location_id][3]):
        _fail("stock_return_receiving_custody_changed", "本包裹原接收责任与当前区域仓责任不一致，请先核验交接", 409)
    checked = facts.verified_shipment_history(db, fact=fact)
    if checked.shipped_at > now or checked.recorded_at > now:
        facts.invalid()
    return project_package(db, fact, checked, target_location_name=locations[header.target_location_id][0])


def project_package(db, fact, checked, *, target_location_name):
    """Project proven parcel facts; authorization stays with the caller."""
    header = db.get(Shipment, fact.id, populate_existing=True)
    order = db.get(StockOperationOrder, checked.operation_id, populate_existing=True)
    # Exact association IDs are deliberately projected from typed parcel rows;
    # the sender's source accounts, balances, QR payloads and request keys are not.
    rows = facts.lines(db, fact)
    if len(rows) != len(checked.lines):
        facts.invalid()
    lines = tuple(StockReturnReceivingLineOut(shipment_line_id=row.id, outbound_no=view.outbound_no,
        material_id=view.material_id, sku_code=view.sku_code, material_name=view.material_name,
        base_unit=view.base_unit, condition_code=view.condition_code, lot_id=view.lot_id, lot_no=view.lot_no,
        shipped_quantity=view.selected_quantity, serials=view.selected_serials)
        for row, view in zip(rows, checked.lines))
    return StockReturnReceivingPackageOut(shipment_id=header.id, shipment_no=checked.shipment_no,
        operation_id=order.id, operation_no=order.operation_no, work_order_id=order.oam_work_order_id,
        sender_person_id=checked.operator_person_id, receiver_person_id=header.target_person_id,
        target_location_id=header.target_location_id, target_location_name=target_location_name,
        custody_assignment_id=fact.target_custody_assignment_id, carrier=checked.carrier, tracking_no=checked.tracking_no,
        shipped_at=checked.shipped_at, recorded_at=checked.recorded_at, lines=lines)


def _query(db, actor, *, limit, after_id=None, shipment_id=None):
    current = _authorize(db, actor)
    audit = material_audit_cursor(db)
    snapshot = inventory._projection_snapshot(db)
    locations = _locations(db, current)
    rows = _parcels(db, current, locations, limit=limit + 1, after_id=after_id, shipment_id=shipment_id)
    if shipment_id is not None and not rows:
        _fail("stock_return_receiving_not_found", "本人当前接收范围内没有此退回包裹", 404)
    items = []
    for fact in rows[:limit]:
        try:
            items.append(_package(db, current, fact, locations))
        except (inventory.InventoryReadError, posting.InventoryPostingError):
            if shipment_id is not None:
                raise
            items.append(StockReturnReceivingBlockedOut(shipment_id=fact.id))
    latest = _authorize(db, current)
    if (latest != current or _locations(db, latest) != locations or material_audit_cursor(db) != audit
            or tuple(row.id for row in _parcels(db, latest, locations, limit=limit + 1,
                after_id=after_id, shipment_id=shipment_id)) != tuple(row.id for row in rows)):
        _fail("stock_return_receiving_changed", "权限、包裹或接收仓在读取期间变化，请刷新", 409)
    inventory._ensure_projection_snapshot_current(db, snapshot)
    basis = dict(person_id=current.person_id, authorization_version=current.authorization_version,
        ledger_cursor=snapshot.ledger_cursor, queried_at=datetime.now(timezone.utc))
    if shipment_id is not None:
        return StockReturnReceivingDetailOut(**basis, package=items[0])
    return StockReturnReceivingOut(**basis, items=tuple(items),
        next_after_id=rows[limit - 1].id if len(rows) > limit else None)


def list_my_return_receiving(db, *, actor, limit=10, after_id=None):
    if type(limit) is not int or not 1 <= limit <= 20:
        _fail("stock_return_receiving_limit_invalid", "退回包裹分页数量必须为 1–20", 400)
    with db.no_autoflush:
        return _query(db, actor, limit=limit, after_id=after_id)


def my_return_receiving_detail(db, *, actor, shipment_id):
    with db.no_autoflush:
        return _query(db, actor, limit=1, shipment_id=shipment_id)
