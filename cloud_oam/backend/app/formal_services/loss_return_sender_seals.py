"""Permanent actor/request tombstones for loss sender commands; candidate only."""
from datetime import datetime, timezone
import re
from uuid import uuid4

from app.formal_access import lock_formal_principal_graph
from app.foundation_models import AuditEvent
from app.stock_operation_models import StockOperationCommandSeal
from app.formal_services import inventory_posting as posting, stock_return_facts as facts
from app.formal_services import stock_return_origins as origins
from app.formal_services.audit_chain import append_audit_event, lock_audit_chain_head, AuditChainError
from app.formal_services.work_order_query import _aware
from app.formal_services.work_order_return_sources import _hash, _fail


def payload(row):
    return dict(operator_person_id=str(row.operator_person_id), operation_id=str(row.operation_id),
        operation_type=row.operation_type, authorization_version=row.authorization_version,
        request_id=row.request_id, request_hash=row.request_hash,
        source_loss_disposition_id=str(row.source_loss_disposition_id))


def verified(db, *, actor, row, origin, operation_type, request, body):
    if (row.actor_user_id != actor.user_id or row.operator_person_id != actor.person_id
            or row.operation_type != operation_type or row.operation_id != origin.operation_id
            or row.oam_work_order_id is not None or row.shipment_id is not None
            or row.source_loss_disposition_id != origin.disposition_id
            or row.request_id != request.request_id or row.request_hash != _hash(body)):
        _fail('loss_return_sender_seal_conflict', '原发件请求已按其他坐标或内容封存，请保留原记录核验')
    try:
        if (row.authorization_version < 1
                or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', row.request_id)
                or not re.fullmatch(r'[a-f0-9]{64}', row.request_hash)
                or row.request_reference != posting._request_reference(row.request_id)
                or not origin.submitted_at <= _aware(row.created_at) <= datetime.now(timezone.utc)):
            facts.invalid()
        facts.audit(db, actor=actor, stream='material_request', aggregate_type='stock_operation_command_seal',
            identifier=row.id, action='stock_return.command_sealed', request_id='stock-return-seal:'+str(row.id),
            before={}, after=payload(row))
        event = facts.single(db, AuditEvent, stream_key='material_request',
            aggregate_type='stock_operation_command_seal', aggregate_id=str(row.id))
        if _aware(event.created_at) != _aware(row.created_at) or _aware(event.occurred_at) != _aware(row.created_at):
            facts.invalid()
    except (ValueError, TypeError, AttributeError, AuditChainError):
        facts.invalid()
    # Existing tombstones close the actor/request namespace for every key.
    # They do not store a key/plan digest: do not claim either was sealed.
    return dict(lookup_status='sealed', retry_allowed=False, seal=dict(
        seal_id=str(row.id), seal_scope='actor_request_id', operation_type=row.operation_type,
        operation_id=str(row.operation_id), operator_person_id=str(row.operator_person_id),
        source_loss_disposition_id=str(row.source_loss_disposition_id),
        request_id=row.request_id, request_hash=row.request_hash,
        sealed_at=_aware(row.created_at).isoformat()))


def seal_sender_request(db, *, actor, operation_type, operation_id, request):
    from app.formal_services.loss_return_sender_recovery import _coordinate, lookup_sender_request
    request, body, _key = _coordinate(operation_type, operation_id, request)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    current, _order, origin = origins.authorize_return_fulfillment(db, actor=actor,
        operation_id=operation_id, action=operation_type)
    if origin.origin_kind != 'loss_report':
        _fail('stock_return_not_found', '本人报损派生退回单不存在', 404)
    lock_audit_chain_head(db, stream_key='material_request')
    previous = lookup_sender_request(db, actor=current, operation_type=operation_type,
        operation_id=operation_id, request=request)
    if previous['lookup_status'] != 'not_observed':
        return previous
    now = datetime.now(timezone.utc)
    row = StockOperationCommandSeal(id=uuid4(), actor_user_id=current.user_id,
        operator_person_id=current.person_id, oam_work_order_id=None, shipment_id=None,
        operation_id=operation_id, source_loss_disposition_id=origin.disposition_id,
        operation_type=operation_type, authorization_version=current.authorization_version,
        request_id=request.request_id, request_reference=posting._request_reference(request.request_id),
        request_hash=_hash(body), created_at=now)
    db.add(row)
    append_audit_event(db, stream_key='material_request', actor_user_id=current.user_id,
        action='stock_return.command_sealed', aggregate_type='stock_operation_command_seal', aggregate_id=str(row.id),
        before_jsonb={}, after_jsonb=payload(row), request_id='stock-return-seal:'+str(row.id),
        occurred_at=now, created_at=now)
    db.flush()
    checked, _, proof = origins.authorize_return_fulfillment(db, actor=current,
        operation_id=operation_id, action=operation_type)
    if checked != current or proof != origin:
        _fail('loss_return_sender_seal_changed', '封存期间原单或当前权限发生变化，请回查原请求')
    return lookup_sender_request(db, actor=current, operation_type=operation_type,
        operation_id=operation_id, request=request)
