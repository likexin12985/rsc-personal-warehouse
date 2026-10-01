"""Scoped, read-only exact references; never authorization to replay a write."""
from datetime import datetime, timezone

from sqlalchemy import or_, select

from ..inventory_models import StockLocation
from ..stock_operation_models import StockLossHeadquartersDecision as Decision, StockLossDisposition
from ..stock_loss_execution_source_schemas import ExecutionSourcesOut
from . import stock_loss_review_query as reviews, stock_loss_facts as facts, stock_loss_sources as sources
from .stock_loss_corrections import original_recovery
from .stock_loss_recovery import _cursor
from .stock_return_plan import destination
from .inventory_query import InventoryReadError


def _decisions(db, review_id):
    return tuple(db.scalars(select(Decision).where(Decision.review_id == review_id)
        .order_by(Decision.line_id).limit(101).execution_options(populate_existing=True)))


def _signature(rows):
    return tuple((r.id, r.review_id, r.line_id, r.disposition, r.reason, r.created_at) for r in rows)


def _routes(db, report):
    if not report.headquarters_review or not any(
            r.disposition == 'return_to_region' for r in report.headquarters_review.decisions):
        return 'not_required', (), None
    source = db.get(StockLocation, report.source_location_id, populate_existing=True)
    if source is None or source.parent_id is None:
        return 'unavailable', (), '原个人仓没有可核验的所属区域仓，请先配置库位关系'
    transits = tuple(db.scalars(select(StockLocation.id).where(
        StockLocation.parent_id == source.parent_id, StockLocation.owner_org_id == report.owner_org_id,
        StockLocation.location_type == 'transit', StockLocation.status == 'active')
        .order_by(StockLocation.id).limit(21)))
    if not transits or len(transits) > 20:
        return 'unavailable', (), '当前退回路线缺失或超过完整核验上限，请核验库位配置'
    at = datetime.now(timezone.utc)
    try:
        routes = tuple(destination(db, person_id=report.requester_person_id,
            source_location_id=report.source_location_id, target_location_id=source.parent_id,
            transit_location_id=identifier, at=at) for identifier in transits)
    except InventoryReadError:
        return 'unavailable', (), '当前区域仓、在途位置或接收责任无法完整核验，请先处理配置'
    return 'available', routes, None


def execution_sources(db, *, actor, operation_id):
    with db.no_autoflush:
        current, owners = reviews._scope(db, actor, 'headquarters')
        before = _cursor(db)
        report = reviews.review_report_detail(db, actor=current,
            stage='headquarters', operation_id=operation_id).report
        final = report.headquarters_review
        rows = _decisions(db, final.review_id) if final else ()
        signature = _signature(rows)
        expected = {r.line_id: r for r in final.decisions} if final else {}
        if len(rows) != len(expected) or {r.line_id for r in rows} != set(expected):
            facts.invalid()
        values = []
        for row in rows:
            decision = expected[row.line_id]
            if row.disposition != decision.disposition or row.reason != decision.reason:
                facts.invalid()
            originals = tuple(db.scalars(select(StockLossDisposition).where(or_(
                StockLossDisposition.headquarters_decision_id == row.id,
                StockLossDisposition.line_id == row.line_id)).limit(2)
                .execution_options(populate_existing=True)))
            if len(originals) > 1:
                facts.invalid()
            original = None
            if originals:
                posted = original_recovery.verified(db, row=originals[0])
                if (posted['headquarters_decision_id'] != str(row.id)
                        or posted['line_id'] != str(row.line_id)
                        or posted['operation_id'] != str(report.operation_id)
                        or posted['disposition'] != row.disposition):
                    facts.invalid()
                original = {key: posted[key] for key in ('disposition_id', 'executor_person_id',
                    'posting_transaction_id', 'quantity')}
                original['return_operation_id'] = posted.get('return_operation_id')
            values.append(dict(line_id=row.line_id, headquarters_decision_id=row.id,
                disposition=row.disposition, reason=row.reason, original_posting=original,
                preview_reference=dict(headquarters_decision_id=row.id,
                    expected_headquarters_review_hash=final.request_hash,
                    expected_submission_plan_hash=report.submission_plan_hash)))
        route_status, routes, reason = _routes(db, report)
        latest, latest_owners = reviews._scope(db, current, 'headquarters')
        if (latest != current or latest_owners != owners or _cursor(db) != before
                or (final is not None and _signature(_decisions(db, final.review_id)) != signature)
                or _routes(db, report) != (route_status, routes, reason)):
            sources._fail('stock_loss_execution_sources_changed', '处置来源或权限在读取期间变化，请刷新', 409)
        return ExecutionSourcesOut(person_id=current.person_id,
            authorization_version=current.authorization_version, queried_at=datetime.now(timezone.utc),
            report=report, decisions=tuple(values), return_routes_status=route_status,
            return_routes=routes, return_routes_reason=reason)
