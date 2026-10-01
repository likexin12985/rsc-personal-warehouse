"""Exact read-only recovery of independent approval and correction execution.

Candidate only. Missing requests never permit replay. Historical approval,
posting, current inventory and channel delivery are deliberately separate.
"""
from sqlalchemy import or_, select

from app import stock_operation_models as stock_models
from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .correction_models import StockLossDispositionReversal as Inverse
from .correction_models import StockLossCorrectionDecision as Decision, StockLossCorrectionExecution as Execution
from .request_contracts import CorrectionApprove, CorrectionExecute, validate, original_request, require_original_row
from .historical_original import _bound
from .history_chain import verify_chain
from . import business_events
from . import inverse_recovery
from . import sealed_inverse
from . import correction_seal_facts as seals
from .request_evidence_scope import authentication_state


MODELS = {CorrectionApprove: Decision, CorrectionExecute: Execution}


def _conflict():
    sources._fail('loss_correction_request_conflict', '准确纠正请求已绑定不同内容，请保留完整原请求核验', 409)


def _unknown():
    sources._fail('loss_correction_request_outcome_unknown', '纠正请求证据不完整或读取期间变化，不能判定未执行或重发', 503)


def _coordinates(db, *, actor, request, keys):
    matches = []
    for model in (Inverse, Decision, Execution):
        matches.extend(db.scalars(select(model).where(or_(model.idempotency_key_hash.in_(keys),
            (model.actor_user_id == actor.user_id) & (model.request_id == request.request_id)))
            .limit(3).execution_options(populate_existing=True)))
    for name in ('StockOperationOrder', 'StockLossDisposition', 'StockLossRegionalReview',
            'StockLossHeadquartersReview', 'StockLossRequestSeal', 'StockLossReviewRequestSeal',
            'StockLossDispositionRequestSeal', 'StockOperationCancellation', 'StockOperationCommandSeal',
            'StockOperationOutbound', 'StockOperationShipment', 'StockOperationReceipt',
            'StockOperationReturnInbound', 'StockOperationReturnInboundSeal'):
        model = getattr(stock_models, name)
        condition = (model.actor_user_id == actor.user_id) & (model.request_id == request.request_id)
        if hasattr(model, 'idempotency_key_hash'):
            condition = or_(condition, model.idempotency_key_hash.in_(keys))
        if db.scalar(select(model.id).where(condition).limit(1)) is not None:
            _conflict()
    if len(matches) > 1 or (matches and type(matches[0]) is not MODELS[type(request)]):
        _conflict()
    sealed_inverse.require_unsealed(db, actor=actor, request=request)
    return matches[0] if matches else None


def _evidence(db, *, actor, request, keys, row):
    posted = type(row) is Execution
    expected_tx = [str(row.posting_transaction_id)] if posted else []
    txs = [str(v) for v in db.scalars(select(InventoryTransaction.id).where(
        InventoryTransaction.idempotency_key_hash.in_(keys)).limit(3))]
    if txs != expected_tx: _unknown()
    reference = posting._request_reference(request.request_id)
    expected_audits, expected_states, expected_domain = [], [], []
    if row is not None:
        aggregate, kind, _, _ = business_events.KINDS[type(row)]
        expected_audits.append(('inventory', aggregate, str(row.id), kind, request.request_id))
        expected_states.append((aggregate, str(row.id)))
        expected_domain.append((aggregate, str(row.id)))
    if posted:
        expected_audits.append(('inventory', 'inventory_transaction', str(row.posting_transaction_id),
            'inventory.transaction.posted', reference))
        expected_states.append(('inventory_transaction', str(row.posting_transaction_id)))
    audits = [tuple(v) for v in db.execute(select(AuditEvent.stream_key, AuditEvent.aggregate_type,
        AuditEvent.aggregate_id, AuditEvent.action, AuditEvent.request_id).where(
            AuditEvent.actor_user_id == actor.user_id,
            AuditEvent.stream_key.in_(('inventory', 'material_request')),
            AuditEvent.request_id.in_((request.request_id, reference))).limit(4))]
    states = [tuple(v) for v in db.execute(select(StateTransitionEvent.aggregate_type,
        StateTransitionEvent.aggregate_id).where(StateTransitionEvent.actor_id == actor.user_id,
            ~authentication_state(),
            or_(StateTransitionEvent.metadata_jsonb['request_id'].as_string() == request.request_id,
                StateTransitionEvent.metadata_jsonb['request_reference'].as_string() == reference)).limit(4))]
    if sorted(audits) != sorted(expected_audits) or sorted(states) != sorted(expected_states):
        _unknown()
    for model, body, kind, identifier in (
        (OutboxEvent, OutboxEvent.payload_jsonb, OutboxEvent.aggregate_type, OutboxEvent.aggregate_id),
        (NotificationEvent, NotificationEvent.payload_jsonb, NotificationEvent.business_type, NotificationEvent.business_id),
    ):
        events = [tuple(v) for v in db.execute(select(kind, identifier).where(
            body['actor_user_id'].as_string() == actor.user_id,
            body['request_id'].as_string() == request.request_id).limit(3))]
        if sorted(events) != sorted(expected_domain): _unknown()


def lookup(db, *, actor, request):
    request = validate(request)
    if type(request) not in MODELS:
        raise ValueError('complete original correction approval or execution request required')
    with db.no_autoflush:
        start = _bound(db)
        root = db.get(stock_models.StockLossDisposition, request.root_disposition_id, populate_existing=True)
        order = db.get(stock_models.StockOperationOrder, root.operation_id, populate_existing=True) if root else None
        if root is None or order is None:
            sources._fail('loss_correction_not_found', '准确原处置不存在', 404)
        # Recovery requires current read rights, independent of past write rights.
        current = inverse_recovery._authorize(db, actor, order)
        inverse = db.get(Inverse, request.reversal_id, populate_existing=True)
        if (inverse is None or inverse.root_disposition_id != root.id
                or request.expected_root_request_hash != root.request_hash
                or request.expected_submission_plan_hash != order.plan_hash
                or request.expected_reversal_hash != inverse.request_hash):
            _conflict()
        if type(request) is CorrectionExecute:
            decision = db.get(Decision, request.correction_decision_id, populate_existing=True)
            if (decision is None or decision.root_disposition_id != root.id
                    or decision.reversal_id != inverse.id
                    or decision.request_hash != request.expected_correction_decision_hash):
                _conflict()
        binding = original_request(actor=current, request=request)
        keys = sealed_inverse._keys(request)
        row = _coordinates(db, actor=current, request=request, keys=keys)
        seal = seals.find(db, actor=current, request=request)
        if row is not None and seal is not None:
            _unknown()
        if row is not None:
            if row.actor_user_id != current.user_id or row.actor_person_id != current.person_id:
                sources._fail('loss_correction_not_found', '本人原纠正请求不存在', 404)
            require_original_row(row=row, actor=current, request=request)
        # One complete proof already validates every inverse, execution and
        # approval under the same ledger/audit boundary. Select exact facts
        # from that proof instead of rebuilding the whole chain twice.
        proof = verify_chain(db, root_disposition_id=root.id)
        if not any(value.reversal_id == inverse.id for value in proof.inverse_proofs):
            _unknown()
        if row is not None:
            if type(row) is Execution:
                if not any(value.correction_execution_id == row.id for value in proof.correction_proofs):
                    _unknown()
            elif row.id not in proof.proved_decision_ids:
                _unknown()
        _evidence(db, actor=current, request=request, keys=keys, row=row)
        scope = ('historical_approval' if type(row) is Decision else 'historical_correction_posting') if row else 'unconfirmed_request'
        result = dict(request_state='found' if row is not None else 'not_found', retry_allowed=False,
            request_id=request.request_id, request_hash=binding.request_hash, result_scope=scope,
            result=business_events.payload(row, root=root, order=order) if row is not None else None)
        if seal is not None:
            result = seals.verify(db, actor=current, request=request, row=seal, root=root)
        later = _coordinates(db, actor=current, request=request, keys=keys)
        later_seal = seals.find(db, actor=current, request=request)
        if (later_seal.id if later_seal is not None else None) != (seal.id if seal is not None else None):
            _unknown()
        if later_seal is not None:
            if seals.verify(db, actor=current, request=request, row=later_seal, root=root) != result:
                _unknown()
        if (later.id if later is not None else None) != (row.id if row is not None else None): _unknown()
        if later is not None: require_original_row(row=later, actor=current, request=request)
        _evidence(db, actor=current, request=request, keys=keys, row=later)
        inverse_recovery._authorize(db, current, order)
        if _bound(db) != start: _unknown()
        return result
