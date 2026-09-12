"""Read-only own-work-order selection; never resolve prototype IDs by guessing."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from ..demand_models import OamWorkOrder
from ..foundation_models import ExternalObject, ExternalObjectVersion, SourceSystem
from ..work_order_query_schemas import MyWorkOrderOut, MyWorkOrdersOut
from . import oam_work_order_projection as projection
from .work_order_material import WorkOrderMaterialPreflightError
from .inventory_posting import _require_current_actor

_STATUSES = {"pending", "active", "completed", "closed", "cancelled", "inactive"}


def _fail(code, message, category="service_unavailable"):
    raise WorkOrderMaterialPreflightError(code, message, category)


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _own_actor(db, actor):
    current = _require_current_actor(db, actor)
    if not current.allows(db, "work_order_material", "read", target_scope_type="person", target_scope_id=str(current.person_id)):
        _fail("work_order_forbidden", "没有本人工单读取权限", "forbidden")
    return current


def _own_statement(current):
    return (
        select(OamWorkOrder, ExternalObject, ExternalObjectVersion, SourceSystem)
        .outerjoin(ExternalObject, ExternalObject.id == OamWorkOrder.external_object_id)
        .outerjoin(ExternalObjectVersion, ExternalObjectVersion.id == ExternalObject.current_version_id)
        .outerjoin(SourceSystem, SourceSystem.id == ExternalObject.source_system_id)
        .where(OamWorkOrder.engineer_person_id == current.person_id)
        .execution_options(populate_existing=True)
    )


def _order_output(row, *, current, now, can_operate):
    order, external, version, source = row
    if (external is None or version is None or source is None
            or source.code != projection.SOURCE_SYSTEM_CODE or source.mode != projection.SOURCE_SYSTEM_MODE
            or external.entity_type != "work_order" or external.deleted_at is not None
            or version.external_object_id != external.id):
        _fail("work_order_source_invalid", "本人工单缺少完整 OAM 正式来源，已停止读取")
    try:
        projection._validate_current_projection_evidence(external=external, current_version=version, work_order=order)
    except projection.OamWorkOrderProjectionError:
        _fail("work_order_projection_invalid", "本人工单与当前 OAM 来源版本不一致，请先核验同步")
    synced_at = _aware(order.updated_at)
    if synced_at > now:
        _fail("work_order_projection_invalid", "本人工单同步时间无效，请先核验同步")
    fresh = now - synced_at <= timedelta(minutes=45)
    return MyWorkOrderOut(
        work_order_id=order.id, work_order_no=order.work_order_no, status=order.status,
        engineer_person_id=current.person_id, organization_id=order.organization_id,
        source_external_id=external.external_id, source_version=version.source_version,
        source_updated_at=_aware(order.source_updated_at), synced_at=synced_at,
        freshness="fresh" if fresh else "stale",
        can_operate=bool(can_operate and order.status == "active" and fresh and source.enabled),
    )


def get_my_work_order(db, *, actor, work_order_id):
    current = _own_actor(db, actor)
    with db.no_autoflush:
        row = db.execute(_own_statement(current).where(OamWorkOrder.id == work_order_id)).one_or_none()
    if row is None:
        _fail("work_order_not_found", "本人工单不存在", "not_found")
    item = _order_output(row, current=current, now=datetime.now(timezone.utc), can_operate=current.allows(
        db, "work_order_material", "operate", target_scope_type="person", target_scope_id=str(current.person_id)))
    _require_current_actor(db, current)
    return item


def require_new_work_order_source(db, *, actor, work_order_id, now):
    """Validate new commands under the actor and exact OAM work-order locks.

    Operate permission is enforced by the command; no unrelated read grant is
    required. Historical replay deliberately skips this current-source check.
    """
    current = _require_current_actor(db, actor)
    with db.no_autoflush:
        row = db.execute(_own_statement(current).where(OamWorkOrder.id == work_order_id)).one_or_none()
    if row is None:
        _fail("work_order_not_found", "本人有效工单不存在", "not_found")
    item = _order_output(row, current=current, now=now, can_operate=True)
    if not item.can_operate:
        _fail("work_order_source_not_current", "工单来源已过期或停用，请同步并重新预检后再提交", "precondition_failed")
    _require_current_actor(db, current)
    return item


def list_my_work_orders(db, *, actor, limit=50, after_id: UUID | None = None, search="", status="active"):
    current = _own_actor(db, actor)
    if (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100
            or (after_id is not None and not isinstance(after_id, UUID))
            or not isinstance(search, str) or len(search) > 100
            or (status is not None and status not in _STATUSES)):
        _fail("work_order_query_invalid", "工单查询条件无效", "invalid_request")
    statement = _own_statement(current)
    if status is not None:
        statement = statement.where(OamWorkOrder.status == status)
    if search.strip():
        statement = statement.where(OamWorkOrder.work_order_no.contains(search.strip(), autoescape=True))
    if after_id is not None:
        statement = statement.where(OamWorkOrder.id > after_id)
    with db.no_autoflush:
        rows = tuple(db.execute(statement.order_by(OamWorkOrder.id).limit(limit + 1)))
    now = datetime.now(timezone.utc)
    permission = current.allows(db, "work_order_material", "operate", target_scope_type="person", target_scope_id=str(current.person_id))
    items = [_order_output(row, current=current, now=now, can_operate=permission) for row in rows[:limit]]
    _require_current_actor(db, current)
    return MyWorkOrdersOut(person_id=current.person_id, authorization_version=current.authorization_version, queried_at=now, items=tuple(items),
        next_after_id=rows[limit - 1][0].id if len(rows) > limit else None)
