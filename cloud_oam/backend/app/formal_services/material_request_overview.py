"""National/regional read-only demand and fulfillment overview.

One SQL statement observes all axes and the latest approval attempts together.
No pagination sample, cross-axis sum or inferred stock/receipt fact is used.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, literal, select, union_all
from sqlalchemy.orm import Session

from app.demand_models import ApprovalInstance, ApprovalStep, MaterialRequest
from app.formal_access import FormalPrincipal
from app.formal_services import material_request_query as query
from app.material_request_overview_schemas import DIMENSIONS, STATE_VALUES, MaterialRequestOverviewOut


def material_request_overview(
    db: Session, *, actor: FormalPrincipal, organization_id: UUID | None = None,
    created_from: datetime | None = None, created_before: datetime | None = None,
    now: datetime | None = None,
) -> MaterialRequestOverviewOut:
    for value in (created_from, created_before, now):
        if value is not None and (not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None):
            query._fail('material_request_overview_time_invalid', 'invalid_request', '查询时间必须包含时区')
    if created_from and created_before and created_from >= created_before:
        query._fail('material_request_overview_range_invalid', 'invalid_request', '开始时间必须早于结束时间')
    if organization_id is not None and not isinstance(organization_id, UUID):
        query._fail('material_request_overview_organization_invalid', 'invalid_request', '区域参数无效')
    with db.no_autoflush:
        context = query._load_read_context(db, actor=actor, now=now)
        visible = context.visible_organization_ids
        # Person-only and external approval grants never confer report access.
        if not visible or (organization_id is not None and organization_id not in visible):
            query._fail('material_request_overview_forbidden', 'forbidden', '没有当前区域的需求概览权限')
        selected = visible if organization_id is None else frozenset(
            item for item in visible if context.organizations.descends_from(item, organization_id, require_active_path=True)
        )
        columns = [MaterialRequest.id, MaterialRequest.revision_no]
        for name in STATE_VALUES:
            columns.append(getattr(MaterialRequest, 'status' if name == 'request_status' else name).label(name))
        source = select(*columns).where(MaterialRequest.requester_org_id.in_(selected))
        if created_from is not None:
            source = source.where(MaterialRequest.created_at >= created_from)
        if created_before is not None:
            source = source.where(MaterialRequest.created_at < created_before)
        requests = source.cte('overview_requests')
        # Old submission revisions and superseded attempts are not additional
        # requests and cannot inflate an approval level's count.
        attempts = select(
            requests.c.id.label('request_id'), ApprovalStep.step_no, ApprovalStep.status,
            func.row_number().over(
                partition_by=(requests.c.id, ApprovalStep.step_no),
                order_by=ApprovalStep.attempt_no.desc(),
            ).label('attempt_rank'),
        ).select_from(requests).join(ApprovalInstance, and_(
            ApprovalInstance.request_id == requests.c.id,
            ApprovalInstance.revision_no == requests.c.revision_no,
        )).join(ApprovalStep, ApprovalStep.instance_id == ApprovalInstance.id).cte('overview_attempts')
        aggregates = [select(literal('total').label('dimension'), literal('all').label('state'), func.count().label('count')).select_from(requests)]
        for name in STATE_VALUES:
            aggregates.append(select(literal(name), requests.c[name], func.count()).select_from(requests).group_by(requests.c[name]))
        for level in (1, 2, 3):
            state = func.coalesce(attempts.c.status, 'not_started')
            aggregates.append(select(literal(f'approval_level_{level}'), state, func.count()).select_from(
                requests.outerjoin(attempts, and_(attempts.c.request_id == requests.c.id,
                    attempts.c.step_no == level, attempts.c.attempt_rank == 1))
            ).group_by(state))
        rows = db.execute(union_all(*aggregates)).all()
        counts = {name: dict.fromkeys(values, 0) for name, values in DIMENSIONS.items()}
        total = None
        for dimension, state, count in rows:
            if dimension == 'total' and state == 'all' and total is None:
                total = count
            elif dimension in counts and state in counts[dimension]:
                counts[dimension][state] = count
            else:
                query._fail('material_request_overview_projection_invalid', 'service_unavailable', '需求概览数据未通过校验')
        query._ensure_authorization_current(db, context.principal)
        final_context = query._load_read_context(db, actor=context.principal, now=now)
        if final_context.visible_organization_ids != visible:
            query._fail('material_request_overview_scope_changed', 'precondition_failed', '查询期间权限范围已变化，请重新读取')
        return query._validate_output(MaterialRequestOverviewOut, dict(
            observed_at=now or datetime.now(timezone.utc), created_from=created_from,
            created_before=created_before, organization_id=organization_id,
            matched_requests=total, counts=counts,
        ))
