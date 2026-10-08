"""Exact original outcomes for all four found-stock stages, without replay.

Current read authority is required; current write permission, current approval
stage and current custody are not historical read requirements. No locks, DML,
notification resend, seal or public endpoint are introduced here.
"""
from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError
from app.models import User
from app.stock_loss_correction_models import StockLossDispositionReversal
from app.stock_scrap_recovery_schemas import (
    ScrapRecoveryExecute, ScrapRecoveryRequestLookup, validated_recovery_request,
)
from app.formal_services import stock_loss_sources as sources
from app.formal_services.stock_loss_recovery import authorize_lookup
from app.formal_services.stock_loss_review_recovery import _authorize as authorize_review
from app.formal_services.stock_loss_corrections import business_events
from app.formal_services.stock_loss_corrections.historical_original import _bound
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from . import recovery_authority as authority, recovery_facts as facts, recovery_events, recovery_history
from . import lookup_coordinates as coordinates, seal_reads
from .tables import tables


def conflict():
    sources._fail('stock_scrap_recovery_request_conflict', '完整原请求、原操作者或请求键不一致', 409)


def _authorize(db, actor, request, source, stage):
    if stage == 'apply':
        current = authorize_lookup(db, actor)
        if current.person_id != source['order'].requester_id:
            sources._fail('stock_scrap_recovery_read_forbidden', '只能回查本人原申请', 403)
    else:
        current, _ = authorize_review(db, actor, source['order'], 'regional' if stage == 'regional' else 'headquarters')
    user = db.get(User, current.user_id, populate_existing=True)
    if user is None or not user.is_active or current.person_id != request.operator_person_id:
        sources._fail('stock_scrap_recovery_read_forbidden', '当前账号与原操作者不一致或已停用', 403)
    return current


def _observe(db, actor, command, source, stage):
    name = facts.NAMES.get(stage, 'stock_scrap_recovery_executions')
    table = tables()[name]
    key = coordinates.keys(command)[3]
    rows = [dict(r) for r in db.execute(select(table).where(or_(table.c.idempotency_key_hash == key,
        (table.c.actor_user_id == actor.user_id) & (table.c.request_id == command.request_id))).limit(3)).mappings()]
    if len(rows) > 1:
        coordinates.unknown()
    row = rows[0] if rows else None
    canonical = facts.intent(command)
    if row is not None and (row['actor_user_id'] != actor.user_id or row['actor_person_id'] != actor.person_id
            or row['request_id'] != command.request_id or row['idempotency_key_hash'] != key
            or row['command_jsonb'] != canonical or row['request_hash'] != sources._hash(canonical)):
        conflict()
    try:
        seal = seal_reads.find(db,actor=actor,command=command,kind=stage,document=canonical,conflict=conflict)
        if seal is not None and row is not None:
            coordinates.unknown()
        verify_chain(db, root_disposition_id=source['root'].id)
        application = row if stage == 'apply' else None
        if stage != 'apply':
            application_table = tables()[facts.NAMES['apply']]
            application = db.execute(select(application_table).where(
                application_table.c.id == command.recovery_request_id)).mappings().one_or_none()
            if (application is None or application['scrap_line_id'] != source['line']['id']
                    or application['request_hash'] != command.expected_request_hash):
                conflict()
        if application is not None:
            facts.verify_history(db, application=application, source=source)
        if stage == 'headquarters':
            regional = tables()[facts.NAMES['regional']]
            prior = db.execute(select(regional).where(regional.c.id == command.regional_review_id)).mappings().one_or_none()
            if (prior is None or prior['recovery_request_id'] != command.recovery_request_id
                    or prior['request_hash'] != command.expected_regional_hash or prior['decision'] != 'verified'):
                conflict()
        if stage == 'execute':
            from .recovery_plan import approval
            _, _, final = approval(db, command, source)
        inverse, body = None, None
        expected = {name: {row['id']}} if row else {}
        audits, states, outbox, notifications = [], [], [], []
        if row and stage != 'execute':
            body = recovery_events.verify(db, row=row, stage=stage, recipient=application['actor_person_id'])
            aggregate = recovery_events.coordinates(row, stage)[0]
            audits = states = outbox = notifications = [(aggregate, str(row['id']))]
        elif row:
            inverse = db.get(StockLossDispositionReversal, row['reversal_id'], populate_existing=True)
            if inverse is None:
                coordinates.unknown()
            recovery_history.verify_execution(db, inverse=inverse, source=source)
            expected['stock_loss_disposition_reversals'] = {inverse.id}
            body = business_events.payload(inverse, root=source['root'], order=source['order'])
            parent = ('stock_loss_disposition_reversal', str(inverse.id))
            child = ('stock_scrap_recovery_execution', str(row['id']))
            audits = states = [parent, child, ('inventory_transaction', str(inverse.posting_transaction_id))]
            outbox, notifications = [parent, child], [parent]
        sealed = None
        if seal is not None:
            fields = dict(root_disposition_id=source['root'].id,scrap_line_id=source['line']['id'],
                loss_operation_id=source['order'].id,loss_line_id=source['line']['loss_line_id'])
            at = source['line']['created_at']
            if stage!='apply':
                fields['recovery_request_id']=application['id']; at=application['created_at']
            if stage=='headquarters':
                fields.update(regional_review_id=prior['id'],regional_decision='verified');at=prior['created_at']
            elif stage=='execute':
                fields.update(headquarters_review_id=final['id'],headquarters_decision='approve');at=final['created_at']
            sealed = seal_reads.verified(db,actor=actor,command=command,row=seal,source_fields=fields,source_time=at)
            audits=[(seal_reads.AGGREGATE,str(seal['id']))]
        snapshot = coordinates.verify(db, actor=actor, request=command, expected=expected, inverse=inverse,
            review=row if stage != 'execute' else None, stage=stage, root=source['root'].id,seal=seal)
        coordinates.events(db, actor=actor, request=command, expected_audits=audits, expected_states=states,
            expected_outbox=outbox, expected_notifications=notifications)
        result = sealed or dict(request_state='found' if row else 'not_found', retry_allowed=False,
            request_id=command.request_id, request_hash=sources._hash(canonical),
            result_scope='historical_original_outcome' if row else 'unconfirmed_request', result=body)
        return result, snapshot, row
    except (InvalidChain, DBAPIError, KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
        coordinates.unknown()


def lookup(db, *, actor, request):
    request = validated_recovery_request(request)
    if type(request) is not ScrapRecoveryRequestLookup:
        raise ValueError('complete original recovery request lookup required')
    command = request.original
    stage = next((key for key, model in facts.CONTRACTS.items() if type(command) is model), None)
    if type(command) is ScrapRecoveryExecute:
        stage = 'execute'
    if stage is None:
        raise ValueError('exact recovery command required')
    with db.no_autoflush:
        start = _bound(db)
        source = authority.load_source(db, command.source)
        current = _authorize(db, actor, request, source, stage)
        first = _observe(db, current, command, source, stage)
        source = authority.load_source(db, command.source)
        current = _authorize(db, current, request, source, stage)
        second = _observe(db, current, command, source, stage)
        _authorize(db, current, request, source, stage)
        if first != second or _bound(db) != start:
            coordinates.unknown()
        return first[0]
