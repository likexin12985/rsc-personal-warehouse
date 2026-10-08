"""Read-only collision and orphan evidence for exact recovery requests.

These are local table handles, never schema mutations or API grants. The
candidate registry columns must already be installed by the migration owner.
"""
import hashlib

from sqlalchemy import or_, select
from app.database import Base
from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent
from app.inventory_models import InventoryTransaction, Receipt, Shipment
from app.stock_loss_correction_models import stock_loss_request_key_bindings, StockLossCorrectionExecution
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_loss_corrections.request_evidence_scope import authentication_state
from app.formal_services.work_order_query import _aware
from .tables import tables
from . import binding_reads

REGISTRY = tables()['stock_loss_request_key_bindings']

NAMES = (
    'stock_operation_orders', 'stock_loss_dispositions', 'stock_loss_regional_reviews',
    'stock_loss_headquarters_reviews', 'stock_loss_request_seals', 'stock_loss_review_request_seals',
    'stock_loss_disposition_request_seals', 'stock_operation_cancellations', 'stock_operation_command_seals',
    'stock_operation_outbounds', 'stock_operation_shipments', 'stock_operation_receipts',
    'stock_operation_return_inbounds', 'stock_operation_return_inbound_seals',
    'stock_loss_disposition_reversals', 'stock_loss_correction_decisions', 'stock_loss_correction_executions',
    'stock_loss_inverse_request_seals', 'stock_loss_correction_approval_seals', 'stock_loss_correction_execution_seals',
    'stock_scrap_recovery_requests', 'stock_scrap_recovery_regional_reviews',
    'stock_scrap_recovery_headquarters_reviews', 'stock_scrap_recovery_executions',
)
ALIASES = ('reversal_key_hash', 'approval_key_hash', 'correction_key_hash', 'recovery_key_hash', 'scrap_key_hash')


def unknown():
    sources._fail('stock_scrap_recovery_request_outcome_unknown',
        '原请求证据不完整或读取期间变化，不能判定未执行或重发', 503)


def keys(request):
    return tuple(posting._storage_hash(prefix + request.idempotency_key) for prefix in (
        'stock-loss:reverse_loss:', 'stock-loss:approve_loss_correction:', 'stock-loss:correct_loss:',
        'stock-scrap-recovery:', 'stock-scrap:'))


def verify(db, *, actor, request, expected, inverse=None, scrap=None, review=None, stage=None, root=None, seal=None):
    if inverse is not None and scrap is not None:
        raise ValueError('one exact stock fact required')
    stock = inverse if inverse is not None else scrap
    bound = inverse if inverse is not None else scrap if type(scrap) is StockLossCorrectionExecution else None
    hashes = keys(request)
    schema = tables()
    observed = {}
    for name in NAMES:
        table = schema[name] if name in schema else Base.metadata.tables[name]
        predicate = [(table.c.actor_user_id == actor.user_id) & (table.c.request_id == request.request_id)]
        for column in ('idempotency_key_hash', *ALIASES):
            if column in table.c:
                predicate.append(table.c[column].in_(hashes))
        ids = set(db.scalars(select(table.c.id).where(or_(*predicate)).limit(4)))
        if ids != expected.get(name, set()):
            unknown()
        observed[name] = ids
    for model in (InventoryTransaction, Shipment, Receipt):
        ids = set(db.scalars(select(model.id).where(model.idempotency_key_hash.in_(hashes)).limit(3)))
        wanted = {stock.posting_transaction_id} if model is InventoryTransaction and stock is not None else set()
        if ids != wanted:
            unknown()
    token = hashlib.sha256(('cloud_oam.loss.correction.key.v1\0' + request.idempotency_key).encode()).hexdigest()
    predicates = [REGISTRY.c.key_token == token,
        (REGISTRY.c.actor_user_id == actor.user_id) & (REGISTRY.c.request_id == request.request_id)]
    predicates.extend(REGISTRY.c[column].in_(hashes) for column in ALIASES)
    if stock is not None:
        predicates.append(REGISTRY.c.fact_id == stock.id)
    bindings = [dict(row) for row in db.execute(select(REGISTRY).where(or_(*predicates)).limit(3)).mappings()]
    if bound is None:
        if bindings:
            unknown()
    else:
        expected_binding = dict(fact_id=bound.id, binding_kind='inverse' if inverse is not None else 'correction',
            root_disposition_id=bound.root_disposition_id,
            actor_user_id=actor.user_id, request_id=request.request_id, request_hash=bound.request_hash,
            key_token=token, inverse_id=bound.id if inverse is not None else None,
            approval_id=None, correction_id=bound.id if scrap is not None else None,
            seal_id=None, approval_seal_id=None, correction_seal_id=None,
            **dict(zip(ALIASES, (*hashes[:3], hashes[3] if inverse is not None else None,
                hashes[4] if scrap is not None else None))))
        if (len(bindings) != 1 or any(bindings[0].get(k) != v for k, v in expected_binding.items())
                or _aware(bindings[0]['created_at']) != _aware(bound.created_at)):
            unknown()
    original = scrap is not None and bound is None
    fact = dict(id=scrap.id, request_hash=scrap.request_hash, created_at=scrap.created_at) if original else review
    new_bindings = binding_reads.verify(db, actor=actor, request=request, hashes=hashes, token=token,
        unknown=unknown, kind='original' if original else stage, fact=fact,
        root=scrap.id if original else root)
    from .seal_reads import candidates
    seals = candidates(db, actor=actor, command=request)
    if seals != ([seal] if seal is not None else []):
        unknown()
    return observed, bindings, new_bindings, seals


def events(db, *, actor, request, expected_audits, expected_states, expected_outbox, expected_notifications):
    """No detached audit, state or delivery intent may masquerade as a miss."""
    reference = posting._request_reference(request.request_id)
    audit = AuditEvent
    audits = list(db.execute(select(audit.aggregate_type, audit.aggregate_id).where(
        audit.actor_user_id == actor.user_id, audit.stream_key.in_(('inventory', 'material_request')),
        or_(audit.request_id.in_((request.request_id, reference)),
            audit.after_jsonb['request_id'].as_string() == request.request_id)).limit(5)))
    state = StateTransitionEvent
    states = list(db.execute(select(state.aggregate_type, state.aggregate_id).where(
        state.actor_id == actor.user_id, ~authentication_state(),
        or_(state.metadata_jsonb['request_id'].as_string() == request.request_id,
            state.metadata_jsonb['request_reference'].as_string() == reference)).limit(5)))
    for actual, wanted in ((audits, expected_audits), (states, expected_states)):
        if sorted(tuple(r) for r in actual) != sorted(wanted):
            unknown()
    for model, kind, identifier, wanted in (
        (OutboxEvent, OutboxEvent.aggregate_type, OutboxEvent.aggregate_id, expected_outbox),
        (NotificationEvent, NotificationEvent.business_type, NotificationEvent.business_id, expected_notifications),
    ):
        body = model.payload_jsonb
        # Existing stock payloads omit actor_user_id; resolve their creator
        # through the immutable audit, while unattributable evidence is unknown.
        attributed = select(audit.id).where(audit.aggregate_type == kind, audit.aggregate_id == identifier,
            audit.actor_user_id == actor.user_id).exists()
        other_actor = select(audit.id).where(audit.aggregate_type == kind, audit.aggregate_id == identifier,
            audit.actor_user_id != actor.user_id).exists()
        actual = list(db.execute(select(kind, identifier).where(body['request_id'].as_string() == request.request_id,
            or_(attributed, body['actor_user_id'].as_string() == actor.user_id,
                body['actor_person_id'].as_string() == str(actor.person_id), ~other_actor)).limit(5)))
        if sorted(tuple(r) for r in actual) != sorted(wanted):
            unknown()
