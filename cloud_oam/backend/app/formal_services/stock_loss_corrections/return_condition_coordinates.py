"""Cross-action evidence for condition submission and historical lookup.

SELECT only, after the caller has established current source scope. A clear
scan is not proof of absence and never permits replay. Permanent registration,
reverse database fences and absence seals are separate, still-required work.
The caller supplies an event only after proving its complete retained history.
"""
from hashlib import sha256

from sqlalchemy import and_, or_, select

from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
from app.return_condition_key_schema import PREFIXES, ALIASES, NAME as KEY_NAME
from app.return_condition_seal_schema import NAME as SEAL_NAME
from app.return_condition_decision_seal_schema import NAME as DECISION_SEAL
from app.return_condition_settlement_input_schema import NAME as SETTLEMENT_INPUT
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .request_evidence_scope import authentication_state
from .return_condition_history import tables


# Include the unprefixed legacy return/loss key and the older disposition and
# review domains as well as the five aliases retained by the 0159/0165 registry.
HASH_COLUMNS = ('idempotency_key_hash', *ALIASES)
LEGACY_TABLES = (
    'stock_operation_orders', 'stock_loss_dispositions', 'stock_loss_regional_reviews',
    'stock_loss_headquarters_reviews', 'stock_loss_request_seals', 'stock_loss_review_request_seals',
    'stock_loss_disposition_request_seals', 'stock_operation_cancellations', 'stock_operation_command_seals',
    'stock_operation_outbounds', 'stock_operation_shipments', 'stock_operation_receipts',
    'stock_operation_return_inbounds', 'stock_operation_return_inbound_seals',
    'stock_loss_disposition_reversals', 'stock_loss_correction_decisions', 'stock_loss_correction_executions',
    'stock_loss_inverse_request_seals', 'stock_loss_correction_approval_seals', 'stock_loss_correction_execution_seals',
    'stock_scrap_recovery_requests', 'stock_scrap_recovery_regional_reviews',
    'stock_scrap_recovery_headquarters_reviews', 'stock_scrap_recovery_executions',
    'stock_loss_request_key_bindings', 'stock_scrap_request_key_bindings', 'stock_scrap_request_seals',
    'stock_condition_events', 'stock_condition_submission_requests',
    'inventory_transactions', 'shipments', 'receipts',
    KEY_NAME,
)
TABLES = (*LEGACY_TABLES, SEAL_NAME, DECISION_SEAL, SETTLEMENT_INPUT)


def unknown():
    sources._fail('return_condition_request_outcome_unknown',
        '原请求存在冲突、残留证据或读取期间变化，不能确认结果或重发', 503)


def hashes(request):
    return tuple(posting._storage_hash(prefix + request.idempotency_key) for prefix in PREFIXES)


def capture(db, *, actor, request):
    """Bounded evidence snapshot; contains identifiers, never raw client keys."""
    keys = hashes(request)
    token = sha256(('cloud_oam.loss.correction.key.v1\0' + request.idempotency_key).encode()).hexdigest()
    schema = tables()
    observed = {}
    for name in TABLES:
        table = schema[name]
        terms = []
        if 'request_id' in table.c and 'actor_user_id' in table.c:
            terms.append(and_(table.c.actor_user_id == actor.user_id, table.c.request_id == request.request_id))
        terms.extend(table.c[column].in_(keys) for column in HASH_COLUMNS if column in table.c)
        if 'key_token' in table.c:
            terms.append(table.c.key_token == token)
        if not terms or len(table.primary_key.columns) != 1:
            raise ValueError('exact request coordinate schema required: ' + name)
        primary = tuple(table.primary_key.columns)[0]
        # At most one row in each table can belong to this exact initial
        # command. Two is already a conflict, irrespective of omitted rows.
        observed[name] = tuple(db.scalars(select(primary).where(or_(*terms)).order_by(primary).limit(2)))
    reference = posting._request_reference(request.request_id)
    audit = AuditEvent
    predicate = and_(audit.actor_user_id == actor.user_id,
        audit.stream_key.in_(('inventory', 'material_request')),
        or_(audit.request_id.in_((request.request_id, reference)),
            audit.after_jsonb['request_id'].as_string() == request.request_id,
            audit.after_jsonb['request_reference'].as_string() == reference))
    observed['audits'] = tuple(tuple(row) for row in db.execute(select(
        audit.id, audit.aggregate_type, audit.aggregate_id).where(predicate).order_by(audit.id).limit(3)))
    state = StateTransitionEvent
    predicate = and_(state.actor_id == actor.user_id, ~authentication_state(),
        or_(state.metadata_jsonb['request_id'].as_string() == request.request_id,
            state.metadata_jsonb['request_reference'].as_string() == reference))
    observed['states'] = tuple(tuple(row) for row in db.execute(select(
        state.id, state.aggregate_type, state.aggregate_id).where(predicate).order_by(state.id).limit(3)))
    for name, model, kind, identifier in (
        ('outbox', OutboxEvent, OutboxEvent.aggregate_type, OutboxEvent.aggregate_id),
        ('notifications', NotificationEvent, NotificationEvent.business_type, NotificationEvent.business_id),
    ):
        body = model.payload_jsonb
        attributed = select(audit.id).where(audit.aggregate_type == kind, audit.aggregate_id == identifier,
            audit.actor_user_id == actor.user_id).exists()
        other = select(audit.id).where(audit.aggregate_type == kind, audit.aggregate_id == identifier,
            audit.actor_user_id != actor.user_id).exists()
        # A payload naming another actor is not authoritative on its own.
        # Exclude it only when immutable evidence attributes that aggregate to
        # another actor and it does not also name this actor/person.
        predicate = and_(or_(body['request_id'].as_string() == request.request_id,
                body['request_reference'].as_string() == reference),
            or_(attributed, body['actor_user_id'].as_string() == actor.user_id,
                body['actor_person_id'].as_string() == str(actor.person_id), ~other))
        observed[name] = tuple(tuple(row) for row in db.execute(select(model.id, kind, identifier)
            .where(predicate).order_by(model.id).limit(2)))
    return observed


def verify(observed, *, event=None, seal=None, decision_seal=None):
    """Accept only an independently proved event and its intentional rows.

    Inventory uses an event-derived posting key, not a raw-client-key alias.
    Its exact posting and effects are independently proved by the history reader.
    The request-reference audit/state still must resolve to that same posting.
    """
    expected = {}
    if sum(value is not None for value in (event, seal, decision_seal)) > 1:
        unknown()
    if event is not None:
        if event['kind'] == 'submit' and event['posting_transaction_id'] is not None:
            expected = dict(stock_operation_orders=(event['case_id'],),
                stock_condition_events=(event['id'],), stock_condition_submission_requests=(event['id'],))
        elif event['kind'] in ('execute','release') and event['posting_transaction_id'] is not None:
            expected = {'stock_condition_events':(event['id'],),SETTLEMENT_INPUT:(event['id'],)}
        elif event['kind'] in ('supplement', 'withdraw', 'verify_region', 'return_evidence',
                'reject_region', 'return_region', 'reject_hq', 'approve_hq', 'cancel_approved'):
            if event['posting_transaction_id'] is not None:
                unknown()
            expected = dict(stock_condition_events=(event['id'],))
        else:
            unknown()
        expected[KEY_NAME]=(event['id'],)
    if seal is not None:
        expected[SEAL_NAME] = (seal['id'],)
    if decision_seal is not None:
        expected[DECISION_SEAL] = (decision_seal['id'],)
    for name in TABLES:
        if observed[name] != expected.get(name, ()):
            unknown()
    business = [('stock_condition_event', str(event['id']))] if event is not None else []
    inventory = [('inventory_transaction', str(event['posting_transaction_id']))] if (
        event is not None and event['posting_transaction_id'] is not None) else []
    seal_audit = [('stock_condition_request_seal', str(seal['id']))] if seal is not None else []
    if decision_seal is not None:
        seal_audit = [('stock_condition_decision_seal', str(decision_seal['id']))]
    for name, wanted in (('audits', business + inventory + seal_audit), ('states', business + inventory),
                         ('outbox', business), ('notifications', business)):
        if sorted(row[1:] for row in observed[name]) != sorted(wanted):
            unknown()


def require_unused(db, *, actor, request):
    # Caller holds the shared ledger lock. This checks existing evidence only;
    # it is not a durable binding and cannot fence a future late legacy write.
    with db.no_autoflush:
        observed = capture(db, actor=actor, request=request)
        if observed[SEAL_NAME] or observed[DECISION_SEAL]:
            # A coordinate match alone may be a collision or corrupt remnant.
            # Reuse the complete scoped original-command/history/audit proof;
            # only a verified permanent closure receives a definite conflict.
            from app.return_condition_requests import ConditionSubmit
            from . import return_condition_recovery as initial
            from . import return_condition_decision_sealed_recovery as actions
            reader = initial if type(request) is ConditionSubmit else actions
            outcome = reader.lookup(db, actor=actor, request=request)
            if (outcome['request_state'] == 'sealed' and outcome['absence_sealed'] is True
                    and outcome['retry_allowed'] is False and outcome['stock_effect'] == 'none'):
                sources._fail('return_condition_request_sealed',
                    '原请求已永久封存，禁止再次提交，请只读回查封存结果', 409)
            unknown()
        verify(observed)
