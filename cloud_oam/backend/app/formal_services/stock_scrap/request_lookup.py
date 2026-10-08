"""Read exact original/corrected scrap outcomes after subsequent recoveries.

Historical approval is checked independently of current write authority and
stock. A clean absence never allows replay. Durable seals and HTTP activation
remain separate gates; no new binding is manufactured by this reader.
"""
from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError
from app.stock_operation_models import (
    StockOperationOrder, StockOperationLine, StockLossDisposition,
    StockLossHeadquartersDecision, StockLossHeadquartersReview,
)
from app.stock_loss_correction_models import (
    StockLossCorrectionDecision, StockLossCorrectionExecution, StockLossDispositionReversal,
)
from app.stock_scrap_schemas import ScrapPreview, ScrapRequestLookup, validated_scrap_request
from app.formal_services import stock_loss_sources as sources, stock_loss_facts, stock_loss_headquarters_reviews
from app.formal_services.stock_loss_disposition_recovery import _authorize
from app.formal_services.stock_loss_corrections.historical_original import _bound
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from . import lookup_coordinates as coordinates, historical_facts, events, seal_reads
from .tables import tables


def conflict():
    sources._fail('stock_scrap_request_conflict', '完整原报废请求、来源批准或原操作者不一致', 409)


def canonical(command):
    intent = ScrapPreview.model_validate(command.model_dump(include=set(ScrapPreview.model_fields))).model_dump(mode='json')
    intent['evidence_file_ids'].sort()
    return dict(schema_version=1, action='scrap', intent=intent,
        request_id=command.request_id, expected_plan_hash=command.expected_plan_hash)


def references(db, actor, request):
    source = request.original.source
    if source.kind == 'original':
        decision = db.get(StockLossHeadquartersDecision, source.headquarters_decision_id, populate_existing=True)
        review = db.get(StockLossHeadquartersReview, decision.review_id, populate_existing=True) if decision else None
        line = db.get(StockOperationLine, decision.line_id, populate_existing=True) if decision else None
        order = db.get(StockOperationOrder, line.operation_id, populate_existing=True) if line else None
        roots = tuple(db.scalars(select(StockLossDisposition).where(
            StockLossDisposition.headquarters_decision_id == source.headquarters_decision_id)
            .limit(2).execution_options(populate_existing=True)))
        if len(roots) > 1:
            coordinates.unknown()
        root = roots[0] if roots else None
        inverse = None
    else:
        root = db.get(StockLossDisposition, source.root_disposition_id, populate_existing=True)
        order = db.get(StockOperationOrder, root.operation_id, populate_existing=True) if root else None
        decision = db.get(StockLossCorrectionDecision, source.correction_decision_id, populate_existing=True)
        inverse = db.get(StockLossDispositionReversal, source.reversal_id, populate_existing=True)
        review = line = None
    if order is None or decision is None:
        sources._fail('stock_scrap_source_not_found', '准确原报废批准来源不存在', 404)
    current = _authorize(db, actor, order)
    if current.person_id != request.operator_person_id:
        sources._fail('stock_scrap_read_forbidden', '只能以准确原操作者回查报废请求', 403)
    if decision.disposition != 'scrap' or source.expected_submission_plan_hash != order.plan_hash:
        conflict()
    if source.kind == 'original':
        if review is None or review.operation_id != order.id or source.expected_headquarters_review_hash != review.request_hash:
            conflict()
    elif (root is None or inverse is None or source.expected_root_request_hash != root.request_hash
            or inverse.root_disposition_id != root.id or source.expected_reversal_hash != inverse.request_hash
            or decision.root_disposition_id != root.id or decision.reversal_id != inverse.id
            or source.expected_correction_decision_hash != decision.request_hash):
        conflict()
    return current, root, order, review


def observe(db, actor, request, refs):
    _, root, order, review = refs
    command = request.original
    corrected = command.source.kind == 'correction'
    name = 'stock_loss_correction_executions' if corrected else 'stock_loss_dispositions'
    table = tables()[name]
    key = coordinates.keys(command)[4]
    rows = [dict(r) for r in db.execute(select(table).where(or_(table.c.idempotency_key_hash == key,
        (table.c.actor_user_id == actor.user_id) & (table.c.request_id == command.request_id))).limit(3)).mappings()]
    if len(rows) > 1:
        coordinates.unknown()
    row = rows[0] if rows else None
    document = canonical(command)
    if row and (row['actor_user_id'] != actor.user_id
            or row['actor_person_id' if corrected else 'executor_person_id'] != actor.person_id
            or row['request_id'] != command.request_id or row['idempotency_key_hash'] != key
            or row['command_jsonb'] != document or row['request_hash'] != sources._hash(document)):
        conflict()
    try:
        seal = seal_reads.find(db,actor=actor,command=command,kind=command.source.kind,document=document,conflict=conflict)
        if seal is not None and row is not None:
            coordinates.unknown()
        stock_loss_facts.submission_evidence(db, order=order)
        if review is not None:
            stock_loss_headquarters_reviews.verified(db, row=review, order=order)
        if root is not None:
            verify_chain(db, root_disposition_id=root.id)
        fact, body = None, None
        expected = {}
        audits, states, outbox, notifications = [], [], [], []
        if row:
            fact = db.get(StockLossCorrectionExecution if corrected else StockLossDisposition,
                row['id'], populate_existing=True)
            if root is None or (fact.root_disposition_id if corrected else fact.id) != root.id:
                coordinates.unknown()
            records = historical_facts.records(db, fact)
            body = events.verify(db, records=records)
            child_id = records['stock_operation_orders'][0]['id']
            expected = {name: {fact.id}, 'stock_operation_orders': {child_id}}
            parent = ('stock_loss_correction_execution' if corrected else 'stock_loss_disposition', str(fact.id))
            child = ('stock_operation_scrap', str(child_id))
            audits = states = [parent, child, ('inventory_transaction', str(fact.posting_transaction_id))]
            outbox, notifications = [parent, child], [parent]
        sealed = None
        if seal is not None:
            if corrected:
                decision = db.get(StockLossCorrectionDecision,command.source.correction_decision_id,populate_existing=True)
                source_fields = dict(root_disposition_id=root.id,loss_line_id=root.line_id,
                    reversal_id=command.source.reversal_id,correction_decision_id=decision.id)
                at = decision.created_at
            else:
                decision = db.get(StockLossHeadquartersDecision,command.source.headquarters_decision_id,populate_existing=True)
                source_fields = dict(original_decision_id=decision.id,loss_line_id=decision.line_id)
                at = review.created_at
            source_fields['loss_operation_id'] = order.id
            sealed = seal_reads.verified(db,actor=actor,command=command,row=seal,source_fields=source_fields,source_time=at)
            audits = [(seal_reads.AGGREGATE,str(seal['id']))]
        snapshot = coordinates.verify(db, actor=actor, request=command, expected=expected, scrap=fact,seal=seal)
        coordinates.events(db, actor=actor, request=command, expected_audits=audits, expected_states=states,
            expected_outbox=outbox, expected_notifications=notifications)
        if sealed is not None:
            return sealed,snapshot,seal
        return dict(request_state='found' if row else 'not_found', retry_allowed=False,
            request_id=command.request_id, request_hash=sources._hash(document),
            result_scope='historical_original_outcome' if row else 'unconfirmed_request', result=body), snapshot, row
    except (InvalidChain, DBAPIError, KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
        coordinates.unknown()


def lookup(db, *, actor, request):
    request = validated_scrap_request(request)
    if type(request) is not ScrapRequestLookup:
        raise ValueError('complete original scrap request lookup required')
    with db.no_autoflush:
        start = _bound(db)
        refs = references(db, actor, request)
        first = observe(db, refs[0], request, refs)
        refs = references(db, refs[0], request)
        second = observe(db, refs[0], request, refs)
        references(db, refs[0], request)
        if first != second or _bound(db) != start:
            coordinates.unknown()
        return first[0]
