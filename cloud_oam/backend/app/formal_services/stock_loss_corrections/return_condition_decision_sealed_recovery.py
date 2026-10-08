"""Eight-table candidate lookup; requires the decision-seal migration installed.

No fallback on a missing relation: deployment mismatch fails closed. The old
seven-table reader stays intact until full native integration is accepted.
"""
from sqlalchemy import select
from .return_condition_decision_seal_admission import validate_request
from . import return_condition_settlement_recovery as settlements
from app.return_condition_settlement_requests import ConditionSettlement
from app.formal_services import stock_loss_sources as sources
from .historical_original import _bound
from . import return_condition_decision_recovery as decisions
from . import return_condition_decision_seal_reads as seals
from . import return_condition_history as graph, return_condition_history_read as history
from . import return_condition_coordinates as coordinates


def lookup(db, *, actor, request):
    request = validate_request(request)
    with db.no_autoflush:
        cases = graph.tables()['stock_condition_cases']
        case = db.execute(select(cases).where(cases.c.id == request.case_id)).mappings().one_or_none()
        if case is None:
            sources._fail('return_condition_history_not_found', '准确纠正单不存在', 404)
        inbound = case['inbound_line_id']
        current,_,_ = history._scope(db,actor,inbound)
        before = _bound(db)
        rows = seals.records(db,actor=current,request=request)
        observed = coordinates.capture(db,actor=current,request=request)
        if len(rows)>1:
            coordinates.unknown()
        if rows:
            proved = history.read(db,actor=current,inbound_line_id=inbound)
            result = seals.verified(db,actor=current,request=request,row=rows[0],proved=proved)
            seals.verify_coordinates(observed,rows[0])
        else:
            reader = settlements._lookup if type(request) is ConditionSettlement else decisions.lookup
            result = reader(db,actor=current,request=request)
        if (seals.records(db,actor=current,request=request) != rows
                or coordinates.capture(db,actor=current,request=request) != observed or _bound(db) != before):
            coordinates.unknown()
        latest,_,_ = history._scope(db,current,inbound)
        if latest != current or _bound(db) != before:
            coordinates.unknown()
        return result
