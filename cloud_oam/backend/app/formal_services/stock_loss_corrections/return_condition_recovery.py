"""Private exact initial-request lookup. No replay or public route.

A found result is the original historical outcome, not current stock. Missing
facts remain unknown, including when a complete source history has no case.
Permanent closure is returned only with exact source, key and audit proof.
"""
from sqlalchemy import or_, select

from app.return_condition_requests import validate_submit
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .historical_original import _bound
from . import return_condition_history as graph, return_condition_history_read as history
from . import return_condition_request_inputs as inputs, return_condition_business_events as business
from . import return_condition_coordinates as coordinates
from . import return_condition_keys as keys
from . import return_condition_seal_reads as seals


def _unknown():
    sources._fail('return_condition_request_outcome_unknown','原纠正请求证据不完整或变化，不能确认结果或重发',503)


def _records(db,actor,request):
    key=posting._storage_hash('stock-condition:'+request.idempotency_key)
    records=[]
    for table in (graph.tables()['stock_condition_events'],inputs.table()):
        records.append(tuple(dict(row) for row in db.execute(select(table).where(or_(
            table.c.idempotency_key_hash==key,
            (table.c.actor_user_id==actor.user_id)&(table.c.request_id==request.request_id)))
            .order_by(*table.primary_key.columns).limit(3)).mappings()))
    return tuple(records)


def lookup(db, *, actor, request):
    request=validate_submit(request)
    with db.no_autoflush:
        current,_,_=history._scope(db,actor,request.inbound_line_id)
        before=_bound(db);records=_records(db,current,request)
        sealed=seals.records(db,actor=current,request=request)
        coordinate_snapshot=coordinates.capture(db,actor=current,request=request)
        proved=history.read(db,actor=current,inbound_line_id=request.inbound_line_id)
        events,registered=records
        event=None;seal=None
        if len(sealed)>1 or (sealed and (events or registered)):
            _unknown()
        result=dict(request_state='unknown',result_scope='historical_original_outcome',result=None,
            retry_allowed=False,current_stock_verified=False,absence_sealed=False,
            observed_ledger_cursor=proved.observed_ledger_cursor)
        if events or registered:
            if len(events)!=1 or len(registered)!=1:
                _unknown()
            event=events[0];original=registered[0]
            if (event['kind']!='submit' or event['id']!=original['event_id']
                    or event['actor_user_id']!=current.user_id or event['actor_person_id']!=current.person_id
                    or event['inbound_line_id']!=request.inbound_line_id
                    or event['id'] not in proved.graph.event_ids):
                _unknown()
            cases=graph.tables()['stock_condition_cases']
            case=db.execute(select(cases).where(cases.c.id==event['case_id'])).mappings().one_or_none()
            if case is None:
                _unknown()
            digest=inputs.match_original(db,request=request,case=case,event=event)
            keys.match_original(db,event=event,request=request)
            if (event['id'],digest) not in proved.graph.original_input_hashes:
                _unknown()
            states=[s for s in proved.graph.projection.cases if s.case_id==case['id']]
            if len(states)!=1:
                _unknown()
            result.update(request_state='found',result=business.payload(case,event),
                          current_case_status=states[0].status,original_input_hash=digest)
        if sealed:
            seal=sealed[0]
            result=seals.verified(db,actor=current,request=request,row=seal,proved=proved)
        coordinates.verify(coordinate_snapshot,event=event,seal=seal)
        if (_records(db,current,request)!=records
                or seals.records(db,actor=current,request=request)!=sealed
                or coordinates.capture(db,actor=current,request=request)!=coordinate_snapshot or _bound(db)!=before):
            _unknown()
        # The longer cross-action scan precedes the final authority check.
        # Revocation during that scan must not release a historical result.
        latest,_,_=history._scope(db,current,request.inbound_line_id)
        if latest!=current or _bound(db)!=before:
            _unknown()
        return result
