"""Read original loss facts; absence never grants permission to replay a write.

The original request id, stock idempotency key and content/plan digests must
agree. A cursor fence discards mixed READ COMMITTED observations. Permanent
seals have their own immutable schema and database exclusion proof.
"""
from sqlalchemy import or_, select

from ..foundation_models import AuditChainHead, AuditEvent, StateTransitionEvent
from ..inventory_models import InventoryLedgerHead, InventoryTransaction, Receipt, Shipment
from ..stock_operation_models import (
    StockOperationOrder as Order, StockOperationCancellation, StockOperationOutbound,
    StockOperationShipment, StockOperationReceipt, StockOperationReturnInbound,
    StockOperationCommandSeal, StockOperationReturnInboundSeal,
)
from ..stock_loss_schemas import StockLossRequestLookupIn, StockLossRequestFoundOut, StockLossRequestMissingOut
from . import inventory_posting as posting, stock_loss_facts as facts, stock_loss_sources as sources, stock_loss_seals as seals


def authorize_lookup(db, actor):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    if not current.allows(db, 'stock_operation', 'read',
            target_scope_type='person', target_scope_id=str(current.person_id)):
        sources._fail('stock_loss_read_forbidden', '没有本人报损记录的当前查看权限', 403)
    return current


def _conflict():
    sources._fail('stock_loss_request_conflict', '原请求坐标或内容不一致，请保留原请求核验')


def _cursor(db):
    ledger = db.scalar(select(InventoryLedgerHead.next_cursor).where(InventoryLedgerHead.stream_key=='inventory'))
    audits = tuple(db.execute(select(AuditChainHead.stream_key, AuditChainHead.version,
        AuditChainHead.last_event_id, AuditChainHead.last_hash)
        .where(AuditChainHead.stream_key.in_(('inventory','material_request'))).order_by(AuditChainHead.stream_key)))
    if ledger is None or ledger < 1 or tuple(row[0] for row in audits)!=('inventory','material_request'):
        facts.invalid()
    return ledger, audits


def _original(db, actor, request, key):
    rows = tuple(db.scalars(select(Order).where(or_(Order.idempotency_key_hash==key,
        (Order.actor_user_id==actor.user_id)&(Order.request_id==request.request_id)))
        .limit(3).execution_options(populate_existing=True)))
    if len(rows)>1: _conflict()
    order = rows[0] if rows else None
    if order is not None:
        if order.actor_user_id!=actor.user_id or order.requester_id!=actor.person_id:
            sources._fail('stock_loss_not_found', '本人原报损单不存在', 404)
        if (order.operation_type!='loss_report' or order.request_id!=request.request_id
                or order.idempotency_key_hash!=key or order.request_hash!=request.request_hash
                or order.plan_hash!=request.expected_plan_hash):
            _conflict()
    for model in (StockOperationCancellation, StockOperationOutbound, StockOperationShipment,
            StockOperationReceipt, StockOperationReturnInbound, StockOperationCommandSeal, StockOperationReturnInboundSeal):
        if db.scalar(select(model.id).where(model.actor_user_id==actor.user_id,
                model.request_id==request.request_id).limit(1)) is not None:
            _conflict()
    for model in (Receipt, Shipment):
        if db.scalar(select(model.id).where(model.idempotency_key_hash==key).limit(1)) is not None:
            _conflict()
    return order


def _require_request_evidence(db, actor, request, key, order, seal=None):
    # An orphaned event or posting is an unknown outcome, never a clean miss.
    if db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key=='material_request',
            AuditEvent.actor_user_id==actor.user_id, or_(AuditEvent.request_id==request.request_id,
                (AuditEvent.aggregate_type.in_(('stock_operation_command_seal','stock_operation_return_inbound_seal')))
                & (AuditEvent.after_jsonb['request_id'].as_string()==request.request_id))).limit(1)) is not None:
        _conflict()
    domain = tuple(db.execute(select(AuditEvent.aggregate_type, AuditEvent.aggregate_id, AuditEvent.action)
        .where(AuditEvent.stream_key=='inventory', AuditEvent.actor_user_id==actor.user_id,
            AuditEvent.request_id==request.request_id).limit(3)))
    expected_domain = ((facts.AGGREGATE, str(order.id), facts.KIND),) if order else ()
    if domain != expected_domain: facts.invalid()
    reference = posting._request_reference(request.request_id)
    audits = tuple(db.scalars(select(AuditEvent.aggregate_id).where(AuditEvent.stream_key=='inventory',
        AuditEvent.actor_user_id==actor.user_id, AuditEvent.request_id==reference,
        AuditEvent.aggregate_type=='inventory_transaction').limit(3)))
    states = tuple(db.scalars(select(StateTransitionEvent.aggregate_id).where(StateTransitionEvent.actor_id==actor.user_id,
        StateTransitionEvent.aggregate_type=='inventory_transaction',
        StateTransitionEvent.metadata_jsonb['request_reference'].as_string()==reference).limit(3)))
    keyed = tuple(db.scalars(select(InventoryTransaction.id).where(InventoryTransaction.idempotency_key_hash==key).limit(2)))
    expected = (str(order.posting_transaction_id),) if order else ()
    if audits!=expected or states!=expected or tuple(str(value) for value in keyed)!=expected:
        facts.invalid()
    sealed=tuple(db.scalars(select(AuditEvent.aggregate_id).where(AuditEvent.stream_key=='inventory',
        AuditEvent.actor_user_id==actor.user_id,AuditEvent.aggregate_type==seals.AGGREGATE,
        AuditEvent.after_jsonb['request_id'].as_string()==request.request_id).limit(3)))
    if sealed!=((str(seal.id),) if seal else ()):facts.invalid()


def lookup_loss_request(db, *, actor, request):
    request = StockLossRequestLookupIn.model_validate(request.model_dump())
    with db.no_autoflush:
        current = authorize_lookup(db, actor)
        if request.operator_person_id!=current.person_id:
            sources._fail('operator_mismatch', '操作人必须是当前登录人员', 403)
        key = posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
        before = _cursor(db)
        order = _original(db, current, request, key)
        seal=seals.find(db,actor=current,request=request,key=key)
        if order is not None and seal is not None:facts.invalid()
        _require_request_evidence(db, current, request, key, order, seal)
        result = (seals.verified(db,actor=current,row=seal) if seal else
            StockLossRequestFoundOut(submission=facts.order_result(db, actor=current, order=order))
            if order else StockLossRequestMissingOut())
        if _cursor(db)!=before:
            sources._fail('stock_loss_lookup_changed', '原报损记录在回查期间变化，请保留原请求并重新查询')
        authorize_lookup(db, current)
        return result
