"""Read an exact permanent closure against original source and immutable audit.

The caller proves current read scope and repeats the surrounding evidence scan.
A seal closes a request; it never certifies a lost scan or changes inventory.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction
from app.stock_operation_models import StockOperationReturnInbound, StockOperationReturnInboundLine
from app.return_condition_key_schema import ALIASES
from app.return_condition_seal_schema import NAME
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_return_facts import audit
from app.formal_services.work_order_query import _aware
from .request_evidence_scope import authentication_state
from . import return_condition_history as graph, return_condition_keys as keys
from . import return_condition_request_inputs as inputs, return_condition_coordinates as coordinates


def table():
    return graph.tables()[NAME]


def records(db, *, actor, request):
    t = table()
    aliases = keys.aliases(request.idempotency_key)
    values = tuple(aliases[name] for name in ALIASES)
    terms = [t.c.key_token == aliases['key_token'],
        (t.c.actor_user_id == actor.user_id) & (t.c.request_id == request.request_id)]
    terms.extend(t.c[name].in_(values) for name in ('idempotency_key_hash', *ALIASES))
    return tuple(dict(row) for row in db.execute(select(t).where(or_(*terms))
        .order_by(t.c.id).limit(2)).mappings())


def payload(row):
    body = {key: str(value) if isinstance(value, UUID) else value
        for key, value in row.items() if key not in ('key_token', 'idempotency_key_hash', *ALIASES)}
    body.update(created_at=_aware(row['created_at']).astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        request_state='sealed', retry_allowed=False, stock_effect='none')
    return body


def verified(db, *, actor, request, row, proved):
    document = inputs.canonical(request)
    if row['actor_user_id'] != actor.user_id or row['actor_person_id'] != actor.person_id:
        coordinates.unknown()
    if row['command_jsonb'] != document or any(row[k] != v for k, v in keys.aliases(request.idempotency_key).items()):
        sources._fail('return_condition_original_input_conflict', '回查必须使用完整且一致的原纠正请求', 409)
    basis = proved.basis
    line = db.get(StockOperationReturnInboundLine, request.inbound_line_id, populate_existing=True)
    header = db.get(StockOperationReturnInbound, line.inbound_id, populate_existing=True) if line else None
    tx = db.get(InventoryTransaction, basis.original_transaction_id, populate_existing=True)
    if header is None or tx is None:
        coordinates.unknown()
    expected = dict(kind='submit', request_id=request.request_id, reason=request.reason,
        idempotency_key_hash=document['idempotency_key_hash'], request_hash=posting._canonical_hash(document),
        inbound_id=header.id, inbound_line_id=basis.inbound_line_id, root_disposition_id=basis.root_disposition_id,
        source_account_id=basis.source.id, original_transaction_id=basis.original_transaction_id,
        original_movement_id=basis.original_movement_id, original_ledger_cursor=basis.original_ledger_cursor)
    at = _aware(row['created_at'])
    if (any(row[k] != v for k, v in expected.items()) or row['authorization_version'] < 1
            or basis.source.custodian_person_id != actor.person_id
            or not max(_aware(header.created_at), _aware(tx.posted_at)) <= at <= datetime.now(timezone.utc)):
        coordinates.unknown()
    identifier = str(row['id']); audit_request = 'condition-seal:' + identifier
    events = tuple(db.scalars(select(AuditEvent).where(or_(AuditEvent.aggregate_id == identifier,
        AuditEvent.request_id == audit_request)).where(or_(
            AuditEvent.stream_key.in_(('inventory', 'material_request')),
            AuditEvent.aggregate_type == 'stock_condition_request_seal',
            AuditEvent.action == 'seal_condition_request', AuditEvent.request_id.like('condition-seal:%')))
        .limit(2).execution_options(populate_existing=True)))
    if len(events) != 1 or _aware(events[0].created_at) != at or _aware(events[0].occurred_at) != at:
        coordinates.unknown()
    body = payload(row)
    audit(db, actor=actor, stream='inventory', aggregate_type='stock_condition_request_seal',
        identifier=row['id'], action='seal_condition_request', request_id=audit_request, before={}, after=body)
    for model, column in ((StateTransitionEvent, StateTransitionEvent.aggregate_id),
            (OutboxEvent, OutboxEvent.aggregate_id), (NotificationEvent, NotificationEvent.business_id),
            (InventoryTransaction, InventoryTransaction.source_document_id)):
        statement = select(model.id).where(column == identifier)
        if model is StateTransitionEvent:
            statement = statement.where(or_(~authentication_state(),
                StateTransitionEvent.aggregate_type == 'stock_condition_request_seal'))
        if db.scalar(statement.limit(1)) is not None:
            coordinates.unknown()
    return dict(request_state='sealed', result_scope='closed_original_request', result=None,
        seal=body, original_input_hash=row['request_hash'], absence_sealed=True,
        retry_allowed=False, current_stock_verified=False, stock_effect='none',
        observed_ledger_cursor=proved.observed_ledger_cursor)
