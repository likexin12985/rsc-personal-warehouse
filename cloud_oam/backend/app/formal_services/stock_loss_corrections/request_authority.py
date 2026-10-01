"""Current authority and exact persisted reference checks, without stock writes.

This is one internal preparation stage, not a complete preview or execution
permit. The write service must lock/reload the graph and the migration must
enforce authority at COMMIT; downstream stock and history proofs are separate.
"""
from dataclasses import dataclass

from app.models import User
from app.inventory_models import StockLocation
from app.stock_operation_models import StockLossDisposition, StockOperationOrder, StockOperationLine
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .correction_models import StockLossDispositionReversal, StockLossCorrectionDecision, StockLossCorrectionExecution
from .request_contracts import ACTIONS, ReversalPreview, CorrectionApprove, CorrectionPreview, validate


def _fail():
    sources._fail('stock_loss_correction_forbidden', '没有当前总部报损冲销或纠正权限', 403)


def authorize(db, *, actor, order, action):
    if action not in frozenset(ACTIONS.values()):
        _fail()
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    location = db.get(StockLocation, order.source_location_id, populate_existing=True)
    if user is None or not user.is_active or location is None or order.operation_type != 'loss_report':
        _fail()
    assignment_ids = {g.assignment_id for g in current.assignments
        if g.role_code == 'admin' and g.scope_type == 'national' and g.scope_id == '*'}
    if (not assignment_ids or not current.allows(db, 'stock_operation', action,
            target_scope_type='organization', target_scope_id=str(location.owner_org_id))
            or not any(g.assignment_id in assignment_ids and g.role_code == 'admin'
                and g.scope_type == 'national' and g.scope_id == '*' and g.resource == 'stock_operation'
                and g.action == action and g.field_code == '' and g.effect == 'allow'
                for g in current.entitlements)):
        _fail()
    # The baseline prohibits the loss applicant from approving their own loss.
    # This is deliberately separate from physical execution authority.
    if action == 'approve_loss_correction' and (
            current.user_id == order.actor_user_id or current.person_id == order.requester_id):
        sources._fail('stock_loss_self_review_forbidden', '申请人不能审批自己的报损纠正', 403)
    return current


@dataclass(frozen=True)
class References:
    actor: object
    root: StockLossDisposition
    order: StockOperationOrder
    execution: object | None = None
    reversal: object | None = None
    decision: object | None = None


def _changed():
    sources._fail('stock_loss_correction_reference_changed', '原处置、反向或纠正批准与本次请求不一致')


def _fact(db, model, identifier, digest):
    row = db.get(model, identifier, populate_existing=True)
    if row is None or row.request_hash != digest or sources._hash(row.command_jsonb) != digest:
        _changed()
    return row


def references(db, *, actor, request):
    """Read exact references and recheck access; does not judge stock availability."""
    request = validate(request)
    with db.no_autoflush:
        root = db.get(StockLossDisposition, request.root_disposition_id, populate_existing=True)
        order = db.get(StockOperationOrder, root.operation_id, populate_existing=True) if root else None
        if root is None or order is None:
            sources._fail('stock_loss_correction_not_found', '准确原处置不存在', 404)
        current = authorize(db, actor=actor, order=order, action=ACTIONS[type(request)])
        root = _fact(db, StockLossDisposition, root.id, request.expected_root_request_hash)
        line = db.get(StockOperationLine, root.line_id, populate_existing=True)
        if (line is None or line.operation_id != order.id or line.operation_type != 'loss_report'
                or order.plan_hash != request.expected_submission_plan_hash):
            _changed()
        execution = reversal = decision = None
        if isinstance(request, ReversalPreview):
            execution = _fact(db, StockLossCorrectionExecution, request.reversed_correction_id,
                request.expected_execution_request_hash) if request.reversed_correction_id else root
            if (execution.request_hash != request.expected_execution_request_hash
                    or (execution is not root and execution.root_disposition_id != root.id)):
                _changed()
        else:
            reversal = _fact(db, StockLossDispositionReversal, request.reversal_id, request.expected_reversal_hash)
            if reversal.root_disposition_id != root.id:
                _changed()
            if isinstance(request, CorrectionPreview):
                decision = _fact(db, StockLossCorrectionDecision, request.correction_decision_id,
                    request.expected_correction_decision_hash)
                if (decision.root_disposition_id != root.id or decision.reversal_id != reversal.id
                        or decision.expected_reversal_hash != reversal.request_hash):
                    _changed()
        # An immutable historical approver need not still have today's roles.
        # Only the caller must retain the exact current permission.
        current = authorize(db, actor=current, order=order, action=ACTIONS[type(request)])
        return References(current, root, order, execution, reversal, decision)
