"""Exact historical decision lookup under current read scope, never replay.

Original input is reconstructed from the independently verified immutable event
command, predecessor hash and exact attachment rows. No current action grant is
required for old results. Missing results remain unknown; decision closure is
not implemented by the initial-submission seal and must not be inferred here.
"""
from sqlalchemy import select

from app.return_condition_decision_requests import validate_decision
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .historical_original import _bound
from . import return_condition_history as graph, return_condition_history_read as history
from . import return_condition_business_events as business
from . import return_condition_coordinates as coordinates
from . import return_condition_keys as keys
from .return_condition_recovery import _records, _unknown


def lookup(db, *, actor, request):
    request = validate_decision(request)
    with db.no_autoflush:
        tables = graph.tables()
        case = db.execute(select(tables['stock_condition_cases']).where(
            tables['stock_condition_cases'].c.id == request.case_id)).mappings().one_or_none()
        if case is None:
            sources._fail('return_condition_history_not_found', '准确纠正单不存在', 404)
        inbound = case['inbound_line_id']
        current, _, _ = history._scope(db, actor, inbound)
        before = _bound(db)
        records = _records(db, current, request)
        observed = coordinates.capture(db, actor=current, request=request)
        proved = history.read(db, actor=current, inbound_line_id=inbound)
        events, submissions = records
        event = None
        result = dict(request_state='unknown', result_scope='historical_original_outcome',
            result=None, retry_allowed=False, current_stock_verified=False, absence_sealed=False,
            observed_ledger_cursor=proved.observed_ledger_cursor)
        if submissions or len(events) > 1:
            _unknown()
        if events:
            event = events[0]
            if (event['actor_user_id'] != current.user_id or event['actor_person_id'] != current.person_id
                    or event['case_id'] != case['id'] or event['id'] not in proved.graph.event_ids):
                _unknown()
            files = tables['stock_condition_files']
            retained_files = tuple(db.scalars(select(files.c.file_id).where(
                files.c.event_id == event['id']).order_by(files.c.file_id)))
            expected = dict(action=event['kind'], case_id=event['case_id'],
                expected_event_id=event['previous_event_id'],
                expected_event_hash=event['command_jsonb']['previous_request_hash'],
                reason=event['reason'], request_id=event['request_id'],
                evidence_file_ids=tuple(sorted(retained_files, key=str)))
            original = request.model_dump(mode='python', exclude={'idempotency_key'})
            original['evidence_file_ids'] = tuple(sorted(original['evidence_file_ids'], key=str))
            if (original != expected or event['idempotency_key_hash'] !=
                    keys.aliases(request.idempotency_key)['condition_key_hash']):
                sources._fail('return_condition_original_input_conflict',
                    '回查必须使用完整且一致的原纠正动作请求', 409)
            keys.match_original(db, event=event, request=request)
            states = [s for s in proved.graph.projection.cases if s.case_id == case['id']]
            if len(states) != 1:
                _unknown()
            document = request.model_dump(mode='json', exclude={'idempotency_key'})
            document['evidence_file_ids'] = sorted(document['evidence_file_ids'])
            document.update(schema_version='condition_decision_input/1',
                idempotency_key_hash=event['idempotency_key_hash'])
            result.update(request_state='found', result=business.payload(case, event),
                current_case_status=states[0].status, original_input_hash=posting._canonical_hash(document))
        coordinates.verify(observed, event=event)
        if (_records(db, current, request) != records
                or coordinates.capture(db, actor=current, request=request) != observed or _bound(db) != before):
            _unknown()
        latest, _, _ = history._scope(db, current, inbound)
        if latest != current or _bound(db) != before:
            _unknown()
        return result
