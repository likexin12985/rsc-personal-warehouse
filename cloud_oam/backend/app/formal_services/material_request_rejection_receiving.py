"""Read an exact handed-over refusal return as its current source custodian.

Authorization belongs to today's warehouse actor; sender history never loads a
sender login or current grant. This does not accept goods or post stock.
"""
from dataclasses import replace
from sqlalchemy import or_, select

from app.demand_models import MaterialRequest
from app.foundation_models import Organization
from app.inventory_models import CustodyAssignment, StockLocation, StockAccount, FormalMaterial, InventorySerial
from app.material_request_rejection_return_schema import returns
from app.material_request_rejection_receiving_schemas import RejectionReceivingOut, RejectionReceivingSerialOut
from . import material_request_rejection_return as registration
from . import material_request_lifecycle as lifecycle
from . import inventory_posting as posting
from .material_request_rejection_history import verified_rejection_history
from .work_order_evidence_snapshot import material_audit_cursor


def _fail(code, category, message):
    registration._fail('receiving_' + code, category, message)


def _authorized(db, actor, return_id):
    current = posting._require_current_actor(db, actor)
    grants = tuple(g for g in current.assignments if g.role_code in ('admin', 'provincial_manager'))
    if not grants:
        _fail('forbidden', 'forbidden', '只有当前来源仓区域负责人或总部管理员可接收退回')
    parent = db.execute(select(returns).where(returns.c.id == return_id)).mappings().one_or_none()
    source = db.get(StockAccount, parent['return_source_account_id'], populate_existing=True) if parent else None
    location = db.get(StockLocation, source.location_id, populate_existing=True) if source else None
    if (location is None or location.location_type not in ('region', 'headquarters')
            or location.status != 'active' or location.custodian_person_id != current.person_id
            or location.owner_org_id != source.owner_org_id):
        _fail('not_found', 'not_found', '当前接收仓范围内没有此退回')
    organization = db.get(Organization, location.owner_org_id, populate_existing=True)
    if organization is None or organization.status != 'active':
        _fail('not_found', 'not_found', '当前接收仓范围内没有此退回')
    scope = dict(target_scope_type='organization', target_scope_id=str(location.owner_org_id))
    if not all(current.allows(db, resource, 'read', **scope) for resource in ('stock_operation', 'inventory')):
        _fail('forbidden', 'forbidden', '没有当前来源仓退回接收查询权限')
    selected = []
    for grant in grants:
        principal = replace(current, assignments=(grant,), entitlements=tuple(
            e for e in current.entitlements if e.assignment_id == grant.assignment_id))
        if all(principal.allows(db, resource, 'read', **scope) for resource in ('stock_operation', 'inventory')):
            selected.append(grant)
    if len(selected) != 1:
        _fail('grant_ambiguous', 'forbidden', '来源仓当前操作角色不唯一，请核验范围授权')
    now = lifecycle._database_now(db)
    custody = tuple(db.scalars(select(CustodyAssignment).where(
        CustodyAssignment.location_id == location.id, CustodyAssignment.valid_from <= now,
        or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > now),
    ).limit(2).execution_options(populate_existing=True)))
    if len(custody) != 1 or custody[0].custodian_person_id != current.person_id:
        _fail('custody_changed', 'precondition_failed', '来源仓当前保管责任不唯一或已变化')
    request = db.get(MaterialRequest, parent['request_id'], populate_existing=True)
    if request is None:
        _fail('history_invalid', 'service_unavailable', '原需求事实不存在')
    stamp = (current, selected[0].assignment_id, request.version, location.id, location.name,
        location.owner_org_id, location.custodian_person_id, location.status,
        custody[0].id, custody[0].valid_from, custody[0].valid_to,
        source.id, source.material_id, source.lot_id, source.condition_code,
        parent['request_hash'], parent['evidence_sha256'])
    return current, parent, source, location, custody[0], request, stamp


def rejection_return_receiving_detail(db, *, actor, return_id):
    with db.no_autoflush:
        current, parent, source, location, custody, request, stamp = _authorized(db, actor, return_id)
        audit = material_audit_cursor(db)
        checked = verified_rejection_history(db, request=request, parent=parent)
        if len(checked.events) != 2 or checked.events[-1].action != 'handover':
            _fail('handover_required', 'precondition_failed', '此退回尚未完成独立实物发出和承运交接')
        material = db.get(FormalMaterial, source.material_id, populate_existing=True)
        if material is None or source.condition_code not in ('new', 'used', 'damaged'):
            _fail('history_invalid', 'service_unavailable', '原退回物料或成色证据不完整')
        serials = tuple(db.scalars(select(InventorySerial).where(
            InventorySerial.id.in_(checked.registration.serial_ids)).order_by(InventorySerial.id)))
        if (tuple(row.id for row in serials) != checked.registration.serial_ids
                or any(row.material_id != source.material_id or row.lot_id != source.lot_id for row in serials)):
            _fail('history_invalid', 'service_unavailable', '原退回SN或批次不一致')
        handover = checked.events[-1]
        result = RejectionReceivingOut(return_id=parent['id'], return_no=parent['return_no'],
            request_id=request.id, request_version=request.version,
            registration_request_hash=parent['request_hash'], handover_id=handover.event_id,
            handover_request_hash=handover.request_hash, handed_over_at=handover.physical_at,
            carrier=handover.carrier, tracking_no=handover.tracking_no,
            quantity=checked.registration.quantity, material_id=source.material_id,
            sku_code=material.sku_code, material_name=material.name, base_unit=material.base_unit,
            lot_id=source.lot_id, source_condition=source.condition_code,
            original_exception=checked.origin['condition'],
            serials=tuple(RejectionReceivingSerialOut(serial_id=row.id, serial_no=row.serial_no) for row in serials),
            target_location_id=location.id, target_location_name=location.name,
            custody_assignment_id=custody.id, receiver_person_id=current.person_id,
            authorization_version=current.authorization_version)
        if _authorized(db, current, return_id)[-1] != stamp or material_audit_cursor(db) != audit:
            _fail('context_changed', 'precondition_failed', '接收仓责任、权限或需求在读取期间变化')
        return result
