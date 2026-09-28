"""Exact return provenance and the separate current physical executor boundary.

Historical HQ derivation is not the engineer's authority to depart or ship.
No stock, shipment, receipt or notification is created by these readers.
"""
from datetime import datetime, timezone
from sqlalchemy import or_, select
from app.inventory_models import CustodyAssignment, StockLocation
from app.stock_operation_models import StockOperationOrder, StockLossDisposition
from app.stock_return_origin_schemas import WorkOrderReturnOrigin, LossReturnOrigin
from app.formal_services import stock_return_facts as returns
from app.formal_services import stock_loss_disposition_facts as dispositions
from app.formal_services.stock_return_plan import authorize
from app.formal_services.work_order_query import _aware
from app.formal_services.work_order_return_sources import _fail


def _not_found():
    _fail('stock_return_not_found', '本人原退回单不存在', 404)


def verify_return_origin(db, *, actor, order):
    if order is None or order.operation_type != 'return' or order.requester_id != actor.person_id:
        _not_found()
    if order.loss_headquarters_decision_id is None:
        if order.oam_work_order_id is None:
            returns.invalid()
        verified = returns.order_result(db, actor=actor, order=order)
        return WorkOrderReturnOrigin(operation_id=verified.operation_id, work_order_id=verified.work_order_id,
            requester_id=verified.requester_id, submitted_by_user_id=order.actor_user_id,
            request_hash=verified.request_hash, plan_hash=verified.plan_hash,
            posting_transaction_id=verified.posting_transaction_id, submitted_at=verified.submitted_at)
    if order.oam_work_order_id is not None:
        returns.invalid()
    found = tuple(db.scalars(select(StockLossDisposition).where(
        StockLossDisposition.return_operation_id == order.id).limit(2).execution_options(populate_existing=True)))
    if len(found) != 1:
        returns.invalid()
    row = found[0]
    # This binds the whole immutable root/child/line/approval/ledger/event graph.
    # Current HQ grants intentionally play no role in a later physical return.
    verified = dispositions.verified(db, row=row)
    if (row.disposition != 'return_to_region' or row.headquarters_decision_id != order.loss_headquarters_decision_id
            or verified['return_operation_id'] != str(order.id)):
        returns.invalid()
    return LossReturnOrigin(operation_id=order.id, requester_id=order.requester_id,
        submitted_by_user_id=order.actor_user_id, request_hash=order.request_hash, plan_hash=order.plan_hash,
        posting_transaction_id=order.posting_transaction_id, submitted_at=_aware(order.created_at),
        loss_operation_id=row.operation_id, loss_line_id=row.line_id,
        headquarters_decision_id=row.headquarters_decision_id, disposition_id=row.id)


def event_origin(origin):
    if isinstance(origin, WorkOrderReturnOrigin):
        # Preserve existing work-order event bytes and their audit proof.
        return {'work_order_id': str(origin.work_order_id)}
    if isinstance(origin, LossReturnOrigin):
        return dict(origin_kind=origin.origin_kind, loss_operation_id=str(origin.loss_operation_id),
            loss_line_id=str(origin.loss_line_id), headquarters_decision_id=str(origin.headquarters_decision_id),
            loss_disposition_id=str(origin.disposition_id))
    raise TypeError('proved return origin required')


def line_origin(origin, line):
    if line.operation_id != origin.operation_id:
        returns.invalid()
    if isinstance(origin, WorkOrderReturnOrigin):
        if line.source_recovery_line_id is None or line.source_loss_line_id is not None:
            returns.invalid()
        return {'source_recovery_line_id': str(line.source_recovery_line_id)}
    if isinstance(origin, LossReturnOrigin):
        if line.source_recovery_line_id is not None or line.source_loss_line_id != origin.loss_line_id:
            returns.invalid()
        return {'source_loss_line_id': str(origin.loss_line_id)}
    raise TypeError('proved return origin required')


def authorize_return_fulfillment(db, *, actor, operation_id, action):
    if action not in ('outbound_return', 'ship_return'):
        raise ValueError('physical return action required')
    current = authorize(db, actor, action)
    with db.no_autoflush:
        order = db.get(StockOperationOrder, operation_id, populate_existing=True)
        origin = verify_return_origin(db, actor=current, order=order)
        at = datetime.now(timezone.utc)
        location = db.get(StockLocation, order.source_location_id, populate_existing=True)
        if (location is None or location.status != 'active' or location.location_type != 'personal'
                or location.custodian_person_id != current.person_id):
            _fail('stock_return_source_custody_changed', '原个人仓保管责任已变化，请核验原退回单', 412)
        active = tuple(db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id == location.id,
            CustodyAssignment.valid_from <= at,
            or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))
            .limit(2).execution_options(populate_existing=True)))
        if len(active) != 1 or active[0].custodian_person_id != current.person_id:
            _fail('stock_return_source_custody_changed', '原个人仓必须有唯一且一致的当前保管责任', 412)
        return current, order, origin
