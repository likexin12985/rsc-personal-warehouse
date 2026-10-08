"""Exact decision closure reads; historical source/audit proof, never replay."""
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import or_, select
from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction
from app.stock_operation_models import StockOperationReturnInbound
from app.return_condition_decision_seal_schema import NAME
from app.return_condition_complete_schema import build_schema
from app.return_condition_key_schema import ALIASES
from app.return_condition_schema import TRANSITIONS
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_return_facts import audit
from app.formal_services.work_order_query import _aware
from . import return_condition_history as graph, return_condition_keys as keys
from . import return_condition_coordinates as coordinates
from .return_condition_decision_seal_admission import canonical
from .return_condition_seal_reads import payload
from .request_evidence_scope import authentication_state


@lru_cache(maxsize=1)
def table():
    # Shared ten-table model, isolated from live Base metadata.
    return build_schema()[0].tables[NAME]


def records(db, *, actor, request):
    t = table(); aliases = keys.aliases(request.idempotency_key)
    values = tuple(aliases[name] for name in ALIASES)
    terms = [t.c.key_token == aliases['key_token'],
        (t.c.actor_user_id == actor.user_id) & (t.c.request_id == request.request_id)]
    terms.extend(t.c[name].in_(values) for name in ('idempotency_key_hash', *ALIASES))
    return tuple(dict(row) for row in db.execute(select(t).where(or_(*terms))
        .order_by(t.c.id).limit(2)).mappings())


def verify_coordinates(observed, row):
    # The caller already proved the exact closure and its audit. Accept only
    # that row while preserving every other action's collision for rejection.
    coordinates.verify(observed, decision_seal=row)


def verified(db, *, actor, request, row, proved):
    document = canonical(request)
    if row['actor_user_id'] != actor.user_id or row['actor_person_id'] != actor.person_id:
        coordinates.unknown()
    if row['command_jsonb'] != document or any(row[k] != v for k, v in keys.aliases(request.idempotency_key).items()):
        sources._fail('return_condition_original_input_conflict', '回查必须使用完整且一致的原纠正动作请求', 409)
    tables = graph.tables(); cases = tables['stock_condition_cases']; events = tables['stock_condition_events']
    case = db.execute(select(cases).where(cases.c.id == request.case_id)).mappings().one_or_none()
    previous = db.execute(select(events).where(events.c.id == request.expected_event_id,
        events.c.case_id == request.case_id)).mappings().one_or_none()
    if case is None or previous is None or previous['id'] not in proved.graph.event_ids:
        coordinates.unknown()
    submitted = db.execute(select(events).where(events.c.id == case['submit_event_id'],
        events.c.case_id == case['id'], events.c.kind == 'submit')).mappings().one_or_none()
    if submitted is None or submitted['id'] not in proved.graph.event_ids:
        coordinates.unknown()
    if request.action in ('supplement', 'withdraw', 'execute', 'release'):
        if actor.user_id != submitted['actor_user_id'] or actor.person_id != submitted['actor_person_id']:
            coordinates.unknown()
    else:
        if actor.user_id == submitted['actor_user_id'] or actor.person_id == submitted['actor_person_id']:
            coordinates.unknown()
        if request.action in ('return_region','reject_hq','approve_hq','cancel_approved'):
            regional = db.execute(select(events).where(events.c.case_id == case['id'],
                events.c.kind == 'verify_region', events.c.event_sequence <= previous['event_sequence'])
                .order_by(events.c.event_sequence.desc()).limit(1)).mappings().one_or_none()
            if regional is None or actor.user_id == regional['actor_user_id'] or actor.person_id == regional['actor_person_id']:
                coordinates.unknown()
    if not any(kind == request.action and state == previous['to_state'] for kind,state,_ in TRANSITIONS):
        coordinates.unknown()
    basis = proved.basis
    header = db.get(StockOperationReturnInbound, case['inbound_id'], populate_existing=True)
    if header is None or case['inbound_line_id'] != basis.inbound_line_id:
        coordinates.unknown()
    expected = dict(kind=request.action, case_id=request.case_id, expected_event_id=request.expected_event_id,
        claimed_event_hash=request.expected_event_hash, historical_state=previous['to_state'],
        historical_sequence=previous['event_sequence'], request_id=request.request_id, reason=request.reason,
        idempotency_key_hash=document['idempotency_key_hash'], request_hash=posting._canonical_hash(document),
        inbound_id=header.id, inbound_line_id=basis.inbound_line_id, root_disposition_id=basis.root_disposition_id,
        source_account_id=basis.source.id, original_transaction_id=basis.original_transaction_id,
        original_movement_id=basis.original_movement_id, original_ledger_cursor=basis.original_ledger_cursor)
    at = _aware(row['created_at'])
    if (any(row[k] != v for k,v in expected.items()) or row['authorization_version'] < 1
            or not max(_aware(header.created_at),_aware(submitted['created_at']),_aware(previous['created_at'])) <= at <= datetime.now(timezone.utc)):
        coordinates.unknown()
    # history_hash binds the observation at closure through immutable audit;
    # comparing it to today's advancing history would strand valid old seals.
    identifier = str(row['id']); audit_request = 'condition-decision-seal:' + identifier
    audits = tuple(db.scalars(select(AuditEvent).where(or_(AuditEvent.aggregate_id == identifier,
        AuditEvent.request_id == audit_request)).where(or_(
            AuditEvent.stream_key.in_(('inventory','material_request')),
            AuditEvent.aggregate_type == 'stock_condition_decision_seal',
            AuditEvent.action == 'seal_condition_decision_request',
            AuditEvent.request_id.like('condition-decision-seal:%'))).limit(2).execution_options(populate_existing=True)))
    if len(audits) != 1 or _aware(audits[0].created_at) != at or _aware(audits[0].occurred_at) != at:
        coordinates.unknown()
    body = payload(row)
    audit(db, actor=actor, stream='inventory', aggregate_type='stock_condition_decision_seal',
        identifier=row['id'], action='seal_condition_decision_request', request_id=audit_request, before={}, after=body)
    for model,column in ((StateTransitionEvent,StateTransitionEvent.aggregate_id),
            (OutboxEvent,OutboxEvent.aggregate_id),(NotificationEvent,NotificationEvent.business_id),
            (InventoryTransaction,InventoryTransaction.source_document_id)):
        statement = select(model.id).where(column == identifier)
        if model is StateTransitionEvent:
            statement = statement.where(or_(~authentication_state(),
                StateTransitionEvent.aggregate_type == 'stock_condition_decision_seal'))
        if db.scalar(statement.limit(1)) is not None:
            coordinates.unknown()
    return dict(request_state='sealed', result_scope='closed_original_request', result=None,
        seal=body, original_input_hash=row['request_hash'], absence_sealed=True,
        retry_allowed=False, current_stock_verified=False, original_preflight_verified=False,
        stock_effect='none', observed_ledger_cursor=proved.observed_ledger_cursor)
