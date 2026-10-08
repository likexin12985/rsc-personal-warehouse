"""Exact historical execute/release readback; missing means unknown, never retry."""
from sqlalchemy import or_, select
from app.return_condition_settlement_requests import validate_settlement
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .historical_original import _bound
from . import return_condition_history as graph, return_condition_history_read as history
from . import return_condition_settlement_inputs as inputs, return_condition_coordinates as coordinates
from . import return_condition_business_events as business, return_condition_keys as keys
from .return_condition_recovery import _records, _unknown


def retained(db, actor, request):
    schema = inputs.tables(); table = schema[inputs.NAME]; scans = schema[inputs.SCANS]
    key = posting._storage_hash('stock-condition:' + request.idempotency_key)
    rows = tuple(dict(r) for r in db.execute(select(table).where(or_(table.c.idempotency_key_hash == key,
        (table.c.actor_user_id == actor.user_id) & (table.c.request_id == request.request_id)))
        .order_by(table.c.event_id).limit(3)).mappings())
    captured = tuple(dict(r) for r in db.execute(select(scans).where(
        scans.c.event_id.in_(tuple(row['event_id'] for row in rows)))
        .order_by(scans.c.event_id, scans.c.serial_id).limit(1001)).mappings())
    if len(captured) > 1000: _unknown()
    return rows, captured


def _lookup(db, *, actor, request):
    request = validate_settlement(request)
    with db.no_autoflush:
        tables = graph.tables(); cases = tables['stock_condition_cases']
        case = db.execute(select(cases).where(cases.c.id == request.case_id)).mappings().one_or_none()
        if case is None: sources._fail('return_condition_history_not_found', '准确纠正单不存在', 404)
        current, _, _ = history._scope(db, actor, case['inbound_line_id'])
        before = _bound(db)
        records = _records(db, current, request)
        saved = retained(db, current, request)
        observed = coordinates.capture(db, actor=current, request=request)
        proved = history.read(db, actor=current, inbound_line_id=case['inbound_line_id'])
        events, submissions = records; rows, _ = saved
        event = None
        result = dict(request_state='unknown', result_scope='historical_original_outcome', result=None,
            retry_allowed=False, current_stock_verified=False, absence_sealed=False,
            observed_ledger_cursor=proved.observed_ledger_cursor)
        if submissions or len(events) > 1 or len(rows) > 1: _unknown()
        if events or rows:
            if len(events) != 1 or len(rows) != 1: _unknown()
            event = events[0]
            if (event['kind'] not in ('execute', 'release') or event['id'] != rows[0]['event_id']
                    or event['case_id'] != case['id'] or event['id'] not in proved.graph.event_ids
                    or event['actor_user_id'] != current.user_id or event['actor_person_id'] != current.person_id):
                _unknown()
            original_hash = inputs.match_original(db, request=request, case=case, event=event)
            keys.match_original(db, event=event, request=request)
            states = [s for s in proved.graph.projection.cases if s.case_id == case['id']]
            if len(states) != 1: _unknown()
            result.update(request_state='found', result=business.payload(case, event),
                current_case_status=states[0].status, original_input_hash=original_hash)
        coordinates.verify(observed, event=event)
        if (_records(db, current, request) != records or retained(db, current, request) != saved
                or coordinates.capture(db, actor=current, request=request) != observed or _bound(db) != before):
            _unknown()
        latest, _, _ = history._scope(db, current, case['inbound_line_id'])
        if latest != current or _bound(db) != before: _unknown()
        return result


def lookup(db, *, actor, request):
    # Shared immutable action seals retain execute/release inputs too. The
    # internal reader remains the exact successful-outcome proof.
    from .return_condition_decision_sealed_recovery import lookup as sealed_lookup
    return sealed_lookup(db, actor=actor, request=validate_settlement(request))
