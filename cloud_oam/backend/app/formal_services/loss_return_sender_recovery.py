"""Exact loss-sender recovery candidate; no replay and no production route yet."""
from sqlalchemy import or_, select
from app.foundation_models import AuditEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction, Shipment, Receipt
from app.stock_operation_models import (
    StockOperationOrder, StockOperationCancellation, StockOperationOutbound,
    StockOperationShipment, StockOperationReceipt, StockOperationReturnInbound,
    StockOperationCommandSeal, StockOperationReturnInboundSeal, StockLossRequestSeal,
    StockLossRegionalReview, StockLossHeadquartersReview, StockLossReviewRequestSeal,
    StockLossDisposition,
)
from app.stock_return_origin_schemas import LossReturnOrigin
from app.stock_return_outbound_schemas import StockReturnOutboundSubmitIn
from app.stock_return_shipment_schemas import StockReturnShipmentSubmitIn
from app.formal_services import inventory_posting as posting, stock_return_origins as origins
from app.formal_services import stock_return_outbound_plan as outbound_plan, stock_return_shipment_plan as shipment_plan
from app.formal_services import stock_return_outbound_facts as outbound_facts, stock_return_shipment_facts as shipment_facts
from app.formal_services import stock_return_recovery as shared, stock_return_facts as facts
from app.formal_services.stock_return_plan import authorize
from app.formal_services.stock_loss_recovery import _cursor
from app.formal_services.work_order_return_sources import _fail, _hash


def conflict():
    _fail('loss_return_sender_request_conflict', '原发件请求坐标或内容不一致，请保留完整原请求核验')


def _coordinate(operation_type, operation_id, request):
    if operation_type == 'outbound_return':
        request = StockReturnOutboundSubmitIn.model_validate(request.model_dump())
        body = outbound_plan.intent(operation_id, request)
    elif operation_type == 'ship_return':
        request = StockReturnShipmentSubmitIn.model_validate(request.model_dump())
        body = shipment_plan.intent(operation_id, request)
    else:
        _fail('loss_return_sender_request_invalid', '发件请求类型无效', 422)
    key = posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
    posting._require_request_id(request.request_id)
    return request, body, key


def _records(db, actor, request, key, model):
    condition = (model.actor_user_id == actor.user_id) & (model.request_id == request.request_id)
    if hasattr(model, 'idempotency_key_hash'):
        condition = or_(condition, model.idempotency_key_hash == key)
    return tuple(db.scalars(select(model).where(condition).limit(3).execution_options(populate_existing=True)))


def _evidence(db, actor, request, key, fact, result, operation_type, seal):
    shared._require_evidence(db, actor=actor, request_id=request.request_id, result=result, seal=seal)
    expected = (('stock_operation_outbound' if operation_type == 'outbound_return' else
                 'stock_operation_shipment', str(fact.id)),) if fact else ()
    domain = tuple(db.execute(select(AuditEvent.aggregate_type, AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == 'material_request', AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.request_id == request.request_id).limit(3)))
    if domain != expected: facts.invalid()
    # Reject orphan or cross-operation inventory events, even when their
    # source_document_type would be ignored by ordinary-return recovery.
    reference = posting._request_reference(request.request_id)
    audits = tuple(db.scalars(select(AuditEvent.aggregate_id).where(AuditEvent.stream_key == 'inventory',
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.request_id == reference).limit(3)))
    states = tuple(db.scalars(select(StateTransitionEvent.aggregate_id).where(
        StateTransitionEvent.aggregate_type == 'inventory_transaction', StateTransitionEvent.actor_id == actor.user_id,
        StateTransitionEvent.metadata_jsonb['request_reference'].as_string() == reference).limit(3)))
    transactions = tuple(db.scalars(select(InventoryTransaction.id).where(
        InventoryTransaction.idempotency_key_hash == key).limit(3)))
    expected_tx = (str(result.posting_transaction_id),) if result and operation_type == 'outbound_return' else ()
    if audits != expected_tx or states != expected_tx or tuple(str(v) for v in transactions) != expected_tx:
        facts.invalid()
    if db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key == 'inventory',
            AuditEvent.actor_user_id == actor.user_id, AuditEvent.request_id == request.request_id).limit(1)):
        conflict()
    headers = tuple(db.scalars(select(Shipment.id).where(Shipment.idempotency_key_hash == key).limit(3)))
    if headers != ((fact.id,) if fact and operation_type == 'ship_return' else ()):
        conflict()
    if db.scalar(select(Receipt.id).where(Receipt.idempotency_key_hash == key).limit(1)):
        conflict()


def lookup_sender_request(db, *, actor, operation_type, operation_id, request):
    request, body, key = _coordinate(operation_type, operation_id, request)
    with db.no_autoflush:
        current = authorize(db, actor, 'read')
        if request.operator_person_id != current.person_id:
            _fail('operator_mismatch', '操作人必须是当前登录人员', 403)
        before = _cursor(db)
        order = db.get(StockOperationOrder, operation_id, populate_existing=True)
        origin = origins.verify_return_origin(db, actor=current, order=order)
        if not isinstance(origin, LossReturnOrigin):
            _fail('stock_return_not_found', '本人报损派生退回单不存在', 404)
        target = StockOperationOutbound if operation_type == 'outbound_return' else StockOperationShipment
        models = (StockOperationOrder, StockOperationCancellation, StockOperationOutbound,
            StockOperationShipment, StockOperationReceipt, StockOperationReturnInbound,
            StockOperationCommandSeal, StockOperationReturnInboundSeal, StockLossRequestSeal,
            StockLossRegionalReview, StockLossHeadquartersReview, StockLossReviewRequestSeal, StockLossDisposition)
        matched = (); sealed = ()
        for model in models:
            rows = _records(db, current, request, key, model)
            if model is target:
                matched = rows
            elif model is StockOperationCommandSeal:
                sealed = rows
            elif rows:
                conflict()
        if len(matched) > 1 or len(sealed) > 1 or (matched and sealed): conflict()
        seal = sealed[0] if sealed else None
        fact = matched[0] if matched else None
        result = None
        if fact is not None:
            if fact.actor_user_id != current.user_id:
                _fail('stock_return_not_found', '本人原发件记录不存在', 404)
            if (fact.operation_id != operation_id or fact.request_id != request.request_id
                    or fact.command_jsonb != body or fact.plan_hash != request.expected_plan_hash):
                conflict()
            if operation_type == 'outbound_return':
                if fact.idempotency_key_hash != key or fact.request_hash != _hash(body): conflict()
                result = outbound_facts.outbound_result(db, actor=current, fact=fact)
            else:
                header = db.get(Shipment, fact.id, populate_existing=True)
                if header is None or header.idempotency_key_hash != key or header.request_hash != _hash(body): conflict()
                result = shipment_facts.shipment_result(db, actor=current, fact=fact)
        _evidence(db, current, request, key, fact, result, operation_type, seal)
        sealed_result = None
        if seal is not None:
            from app.formal_services.loss_return_sender_seals import verified
            sealed_result = verified(db, actor=current, row=seal, origin=origin,
                operation_type=operation_type, request=request, body=body)
        refreshed = authorize(db, current, 'read')
        if refreshed != current or origins.verify_return_origin(db, actor=refreshed, order=order) != origin or _cursor(db) != before:
            _fail('loss_return_sender_lookup_changed', '发件记录或权限在回查期间变化，请保留原请求重新查询')
        if sealed_result is not None:
            return sealed_result
        if result is None:
            return {'lookup_status': 'not_observed', 'retry_allowed': False}
        return {'lookup_status': 'found', 'retry_allowed': False, 'operation_type': operation_type,
                'result': result.model_dump(mode='json')}
