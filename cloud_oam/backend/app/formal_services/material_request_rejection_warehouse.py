"""Only current source warehouses may select returns, accept goods or post stock."""
from decimal import Decimal
from uuid import UUID
from pydantic import ValidationError
from sqlalchemy import select

from app.inventory_models import StockAccount, StockLocation
from app.material_request_rejection_progress_schema import progress
from app.material_request_rejection_warehouse_schemas import (RejectionWarehouseDetailOut, RejectionWarehouseSourceOut,
    RejectionWarehouseReceiptOut, RejectionWarehouseInboxItemOut, RejectionWarehouseInboxOut,
    RejectionWarehousePreviewOut, RejectionWarehousePartOut)
from . import material_request_rejection_receipt as acceptance
from . import material_request_rejection_inbound as inbound
from . import material_request_query as query
from .work_order_evidence_snapshot import material_audit_cursor

receiving = acceptance.receiving


def _write_allowed(db, context):
    request = context[5]
    if (request.status not in ('approved', 'partially_approved')
            or db.scalar(select(acceptance.registration.closures.c.id).where(
                acceptance.registration.closures.c.request_id == request.id)) is not None):
        return False
    try:
        acceptance._authority(db, context)
        return True
    except query.MaterialRequestReadError as exc:
        if exc.category != 'forbidden':
            raise
        return False


def detail(db, *, actor, return_id):
    with db.no_autoflush:
        context = acceptance._context(db, actor, return_id)
        cursor = material_audit_cursor(db)
        source = receiving.rejection_return_receiving_detail(db, actor=context[0], return_id=return_id)
        original = context[1]['evidence_jsonb']['origin']  # verified by source reading above
        source = RejectionWarehouseSourceOut(**source.model_dump(exclude={'status'}),
            **{k: original[k] for k in ('tracking_mode', 'quantity_scale', 'allow_fraction')})
        receipts, confirmed, used = acceptance._history(db, context)
        posted_rows = tuple(db.execute(select(inbound.inbounds).where(inbound.inbounds.c.return_id == return_id)
            .order_by(inbound.inbounds.c.id).limit(1001)).mappings())
        if len(posted_rows) > 1000:
            receiving._fail('history_limit', 'precondition_failed', '仓库入账历史超过完整核验上限')
        posted = {}
        known = {r.receipt_id for r in receipts}
        for row in posted_rows:
            result = inbound.facts.verify(db, context, row)
            if result.receipt_id not in known or result.receipt_id in posted:
                inbound.facts.invalid()
            posted[result.receipt_id] = result
        can_write = _write_allowed(db, context)
        total = lambda field: sum((getattr(r.amounts, field) for r in receipts), Decimal(0))
        accepted, rejected, damaged = (total(k) for k in ('accepted_qty', 'rejected_qty', 'damaged_qty'))
        posted_qty = sum((r.amounts.accepted_qty for r in receipts if r.receipt_id in posted), Decimal(0))
        unconfirmed = Decimal(source.quantity) - confirmed
        result = RejectionWarehouseDetailOut(source=source, accepted_qty=format(accepted, '.3f'),
            rejected_qty=format(rejected, '.3f'), damaged_qty=format(damaged, '.3f'),
            unconfirmed_qty=format(unconfirmed, '.3f'), posted_qty=format(posted_qty, '.3f'),
            pending_inbound_qty=format(accepted - posted_qty, '.3f'),
            unconfirmed_serials=tuple(s for s in source.serials if s.serial_id not in used),
            receive_permitted=can_write and unconfirmed > 0,
            receipts=tuple(RejectionWarehouseReceiptOut(receipt=r, inbound=posted.get(r.receipt_id),
                post_permitted=can_write and r.amounts.accepted_qty > 0 and r.receipt_id not in posted) for r in receipts))
        if acceptance._context(db, context[0], return_id)[-1] != context[-1] or material_audit_cursor(db) != cursor:
            receiving._fail('context_changed', 'precondition_failed', '读取期间仓库责任、权限或退回事实变化')
        return result


def _locations(db, actor):
    # Filter by current custody and organization permissions BEFORE any return id
    # is selected. Each selected object still gets the full unique-role/custody check.
    if not any(g.role_code in ('admin', 'provincial_manager') for g in actor.assignments):
        receiving._fail('forbidden', 'forbidden', '仅当前来源仓区域负责人或总部管理员可查询')
    locations = tuple(db.scalars(select(StockLocation).where(StockLocation.status == 'active',
        StockLocation.location_type.in_(('region', 'headquarters')),
        StockLocation.custodian_person_id == actor.person_id).order_by(StockLocation.id)
        .execution_options(populate_existing=True)))
    return tuple((l.id, l.owner_org_id) for l in locations if all(actor.allows(db, resource, 'read',
        target_scope_type='organization', target_scope_id=str(l.owner_org_id)) for resource in ('stock_operation', 'inventory')))


def inbox(db, *, actor, limit=5, after_id=None):
    if type(limit) is not int or not 1 <= limit <= 20:
        receiving._fail('limit_invalid', 'invalid_request', '仓库退回分页参数无效')
    with db.no_autoflush:
        current = acceptance.posting._require_current_actor(db, actor)
        locations = _locations(db, current)
        cursor = material_audit_cursor(db)
        returns = acceptance.registration.returns
        handed = select(progress.c.return_id).where(progress.c.action == 'handover')
        statement = select(returns.c.id).join(StockAccount, StockAccount.id == returns.c.return_source_account_id).where(
            StockAccount.location_id.in_([row[0] for row in locations]), returns.c.id.in_(handed))
        if after_id is not None:
            statement = statement.where(returns.c.id > after_id)
        rows = tuple(db.scalars(statement.order_by(returns.c.id).limit(limit + 1)))
        items = []
        for identifier in rows[:limit]:
            context = acceptance._context(db, current, identifier)  # auth failures never expose an identifier
            try:
                value = detail(db, actor=current, return_id=identifier)
                items.append(RejectionWarehouseInboxItemOut(return_id=identifier, verification_status='verified',
                    message='交运、逐次验收和独立入账已核验', detail=value))
            except (query.MaterialRequestReadError, ValidationError) as exc:
                if isinstance(exc, query.MaterialRequestReadError) and exc.category not in ('service_unavailable',):
                    raise
                if acceptance._context(db, current, identifier)[-1] != context[-1]:
                    receiving._fail('context_changed', 'precondition_failed', '仓库责任或权限已变化')
                items.append(RejectionWarehouseInboxItemOut(return_id=identifier, verification_status='blocked',
                    message='该退回历史未通过完整核验，请联系管理员；其他已核验记录可继续办理', detail=None))
        latest = acceptance.posting._require_current_actor(db, current)
        if latest != current or _locations(db, latest) != locations or material_audit_cursor(db) != cursor:
            receiving._fail('context_changed', 'precondition_failed', '读取期间仓库范围、权限或退回事实变化')
        return RejectionWarehouseInboxOut(person_id=current.person_id, authorization_version=current.authorization_version,
            items=tuple(items), next_after_id=rows[limit-1] if len(rows) > limit else None)


def preview(db, *, actor, return_id, receipt_id):
    with db.no_autoflush:
        context = acceptance._context(db, actor, return_id)
        cursor = material_audit_cursor(db)
        source = receiving.rejection_return_receiving_detail(db, actor=context[0], return_id=return_id)
        planned = inbound.plans.preview(db, actor=context[0], return_id=return_id, receipt_id=receipt_id)
        plan = planned['plan']
        known = {str(s.serial_id): s for s in source.serials}
        parts = tuple(RejectionWarehousePartOut(condition_code=p['condition_code'], quantity=p['quantity'],
            serials=tuple(known[s] for s in p['serial_ids'])) for p in plan['parts'])
        result = RejectionWarehousePreviewOut(return_id=return_id, receipt_id=receipt_id, request_id=source.request_id,
            request_version=source.request_version, person_id=context[0].person_id, authorization_version=context[0].authorization_version,
            receipt_request_hash=plan['receipt_request_hash'], plan_hash=planned['plan_hash'],
            target_location_id=source.target_location_id, target_location_name=source.target_location_name,
            sku_code=source.sku_code, material_name=source.material_name, parts=parts)
        if acceptance._context(db, context[0], return_id)[-1] != context[-1] or material_audit_cursor(db) != cursor:
            receiving._fail('context_changed', 'precondition_failed', '预览期间仓库责任、权限或退回事实变化')
        return result
