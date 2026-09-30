"""Current scoped readers inspect immutable loss facts; no approval is written."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from ..foundation_models import Organization, Person
from ..inventory_models import StockLocation
from ..models import User
from ..stock_operation_models import (
    StockOperationOrder as Order, StockOperationLine as Line,
    StockLossRegionalReview as Regional, StockLossHeadquartersReview as Headquarters,
)
from ..stock_loss_review_query_schemas import (
    LossReviewLineOut, LossReviewFactOut, LossReviewHeadquartersFactOut,
    LossReviewDetailOut, LossReviewBlockedOut, LossReviewQueueOut, LossReviewQueryOut,
)
from . import inventory_posting as posting, stock_loss_sources as sources, stock_loss_facts as facts
from . import stock_loss_regional_reviews as regional, stock_loss_headquarters_reviews as headquarters
from .stock_loss_review_recovery import _authorize
from .stock_loss_recovery import _cursor
from .inventory_query import InventoryReadError
from .inventory_posting import InventoryPostingError


def _scope(db, actor, stage):
    if stage not in ('regional', 'headquarters'):
        sources._fail('stock_loss_review_stage_invalid', '请选择区域核实或总部终审', 400)
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    if user is None or not user.is_active:
        sources._fail('stock_loss_review_read_forbidden', '当前账号不能查看报损审批', 403)
    role, scope = ('provincial_manager', 'organization') if stage == 'regional' else ('admin', 'national')
    grants = tuple(g for g in current.assignments if g.role_code == role and g.scope_type == scope
        and (stage != 'headquarters' or g.scope_id == '*')
        and any(e.assignment_id == g.assignment_id and e.role_code == role and e.scope_type == scope
            and e.scope_id == g.scope_id and e.resource == 'stock_operation' and e.action == 'read'
            and e.field_code == '' and e.effect == 'allow' for e in current.entitlements))
    if not grants:
        sources._fail('stock_loss_review_read_forbidden', '没有当前报损审批查看权限', 403)
    statement = select(Organization).where(Organization.status == 'active')
    if stage == 'regional':
        try: identifiers = tuple(UUID(g.scope_id) for g in grants)
        except (TypeError, ValueError, AttributeError):
            sources._fail('stock_loss_review_read_forbidden', '当前审批范围无效', 403)
        statement = statement.where(Organization.id.in_(identifiers))
    organizations = tuple(db.scalars(statement.order_by(Organization.id).limit(1001)
        .execution_options(populate_existing=True)))
    if len(organizations) > 1000:
        sources._fail('stock_loss_review_scope_limit', '审批组织范围超过完整核验上限', 503)
    owners = {org.id:org.name for org in organizations if current.allows(db, 'stock_operation', 'read',
        target_scope_type='organization', target_scope_id=str(org.id))}
    return current, owners


def _rows(db, actor, owners, *, limit, after_id=None, operation_id=None):
    statement = select(Order).join(StockLocation, StockLocation.id == Order.source_location_id).where(
        Order.operation_type == 'loss_report', StockLocation.owner_org_id.in_(owners),
        Order.actor_user_id != actor.user_id, Order.requester_id != actor.person_id)
    if after_id is not None: statement = statement.where(Order.id > after_id)
    if operation_id is not None: statement = statement.where(Order.id == operation_id)
    return tuple(db.scalars(statement.order_by(Order.id).limit(limit).execution_options(populate_existing=True)))


def _project(db, actor, order, stage, owners):
    _, owner = _authorize(db, actor, order, stage)
    original = facts.submission_evidence(db, order=order)
    location = db.get(StockLocation, order.source_location_id, populate_existing=True)
    requester = db.get(Person, order.requester_id, populate_existing=True)
    if requester is None or location is None or owner not in owners: facts.invalid()
    region_rows = tuple(db.scalars(select(Regional).where(Regional.operation_id == order.id).limit(2)
        .execution_options(populate_existing=True)))
    headquarters_rows = tuple(db.scalars(select(Headquarters).where(Headquarters.operation_id == order.id).limit(2)
        .execution_options(populate_existing=True)))
    if len(region_rows)>1 or len(headquarters_rows)>1 or headquarters_rows and not region_rows: facts.invalid()
    region = regional.verified(db, row=region_rows[0], order=order) if region_rows else None
    final = headquarters.verified(db, row=headquarters_rows[0], order=order) if headquarters_rows else None
    lines = tuple(db.scalars(select(Line).where(Line.operation_id == order.id).order_by(Line.line_no)
        .execution_options(populate_existing=True)))
    if len(lines) != len(original.lines): facts.invalid()
    projected = tuple(LossReviewLineOut(line_id=line.id, material_id=view.source.material_id,
        sku_code=view.source.sku_code, material_name=view.source.material_name, base_unit=view.source.base_unit,
        condition_code=view.source.condition_code, lot_id=view.source.lot_id, lot_no=view.source.lot_no,
        quantity=view.selected_quantity, serials=view.selected_serials)
        for line, view in zip(lines, original.lines, strict=True))
    def proof(value, schema):
        return schema.model_validate({key:getattr(value,key) for key in schema.model_fields}) if value else None
    return LossReviewDetailOut(operation_id=order.id, operation_no=order.operation_no,
        owner_org_id=owner, owner_org_name=owners[owner], requester_person_id=order.requester_id,
        requester_name=requester.name, source_location_id=location.id, source_location_name=location.name,
        submitted_at=original.submitted_at, reason=original.reason, submission_plan_hash=original.plan_hash,
        approval_stage='approved' if final else 'awaiting_headquarters' if region else 'awaiting_regional',
        lines=projected, evidence=original.evidence, regional_review=proof(region, LossReviewFactOut),
        headquarters_review=proof(final, LossReviewHeadquartersFactOut))


def _query(db, actor, stage, *, view='pending', limit=10, after_id=None, operation_id=None):
    if type(limit) is not int or not 1 <= limit <= 20 or view not in ('pending', 'all'):
        sources._fail('stock_loss_review_query_invalid', '审批分页或查询范围无效', 400)
    current, owners = _scope(db, actor, stage)
    before = _cursor(db)
    rows = _rows(db, current, owners, limit=limit+1, after_id=after_id, operation_id=operation_id)
    if operation_id is not None and not rows:
        sources._fail('stock_loss_not_found', '当前审批范围内没有此报损单', 404)
    items = []
    for row in rows[:limit]:
        try: item = _project(db, current, row, stage, owners)
        except (InventoryReadError, InventoryPostingError):
            if operation_id is not None: raise
            item = LossReviewBlockedOut(operation_id=row.id)
        expected = 'awaiting_regional' if stage == 'regional' else 'awaiting_headquarters'
        if operation_id is not None or view == 'all' or item.availability == 'blocked' or item.approval_stage == expected:
            items.append(item)
    latest, latest_owners = _scope(db, current, stage)
    if latest != current or latest_owners != owners or _cursor(db) != before or tuple(r.id for r in rows) != tuple(
        r.id for r in _rows(db, latest, latest_owners, limit=limit+1, after_id=after_id, operation_id=operation_id)):
        sources._fail('stock_loss_review_query_changed', '权限或审批记录在读取期间变化，请刷新', 409)
    basis = dict(person_id=current.person_id, authorization_version=current.authorization_version,
        stage=stage, queried_at=datetime.now(timezone.utc))
    if operation_id is not None: return LossReviewQueryOut(**basis, report=items[0])
    return LossReviewQueueOut(**basis, view=view, items=tuple(items),
        next_after_id=rows[limit-1].id if len(rows)>limit else None)


def list_review_reports(db, *, actor, stage, view='pending', limit=10, after_id=None):
    with db.no_autoflush: return _query(db, actor, stage, view=view, limit=limit, after_id=after_id)


def review_report_detail(db, *, actor, stage, operation_id):
    with db.no_autoflush: return _query(db, actor, stage, operation_id=operation_id, limit=1)
