"""Append a non-posting action with its complete evidence in one transaction.

Caller owns COMMIT/rollback. This private writer never changes the original
case/share/common order or posts stock. Current authority is checked under the
principal and inventory locks; native deferred guards must also accept COMMIT.
An existing request requires lookup, never implicit replay of the decision.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.formal_access import lock_formal_principal_graph
from app.return_condition_decision_requests import validate_decision
from app.return_condition_schema import TRANSITIONS
from app.formal_services import inventory_posting as posting
from . import return_condition_authority as authority
from . import return_condition_business_events as business
from . import return_condition_coordinates as coordinates
from . import return_condition_history_read as history
from . import return_condition_identity as identity
from . import return_condition_keys as keys
from . import return_condition_submission as submission
from .return_condition_event_files import read_event_evidence


def decide(db, *, actor, request):
    request = validate_decision(request)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    admitted = authority.authorize_action(db, actor=actor, case_id=request.case_id,
        expected_event_id=request.expected_event_id, kind=request.action)
    actor = admitted.actor
    if admitted.previous_request_hash != request.expected_event_hash:
        submission.fail('action_changed', '上一事件内容已变化，请重新读取原单')
    key = posting._storage_hash('stock-condition:' + request.idempotency_key)
    submission._unused(db, actor, request, key)
    coordinates.require_unused(db, actor=actor, request=request)
    proved = history.read(db, actor=actor, inbound_line_id=admitted.inbound_line_id)
    states = [s for s in proved.graph.projection.cases if s.case_id == request.case_id]
    if len(states) != 1:
        submission.fail('history_changed', '缺少本次纠正单完整历史')
    tables = submission.tables()
    case = dict(db.execute(select(tables['stock_condition_cases']).where(
        tables['stock_condition_cases'].c.id == request.case_id)).mappings().one())
    previous = dict(db.execute(select(tables['stock_condition_events']).where(
        tables['stock_condition_events'].c.id == request.expected_event_id)).mappings().one())
    targets = [after for kind, before, after in TRANSITIONS
        if kind == request.action and before == states[0].status]
    if (len(targets) != 1 or previous['case_id'] != case['id']
            or previous['to_state'] != states[0].status
            or previous['request_hash'] != request.expected_event_hash):
        submission.fail('action_changed', '纠正状态已变化，请重新读取原单')
    at = datetime.now(timezone.utc)
    files = submission._files(db, actor, request, at)
    selected = tuple(sorted(states[0].claim.selected.serial_ids, key=str))
    event = dict(id=uuid4(), created_at=at, actor_user_id=actor.user_id,
        actor_person_id=actor.person_id, authorization_version=actor.authorization_version,
        request_id=request.request_id, idempotency_key_hash=key, reason=request.reason,
        case_id=case['id'], submit_event_id=case['submit_event_id'],
        inbound_line_id=case['inbound_line_id'], source_account_id=case['source_account_id'],
        frozen_account_id=case['frozen_account_id'], quantity=case['quantity'],
        event_sequence=len(proved.graph.event_ids) + 1, kind=request.action,
        from_state=previous['to_state'], to_state=targets[0],
        previous_event_id=previous['id'], previous_sequence=previous['event_sequence'],
        decision_event_id=None, decision_kind=None, posting_transaction_id=None,
        posting_movement_id=None, movement_type=None, from_account_id=None, to_account_id=None)
    manifest = [dict(file_id=f.file_id, metadata_sha256=f.metadata_sha256) for f in files]
    binding = identity.identity(case, event, serial_ids=selected, evidence=manifest,
        previous_request_hash=request.expected_event_hash)
    event.update(plan_jsonb=binding.plan, plan_hash=binding.plan_hash,
        command_jsonb=binding.command, request_hash=binding.request_hash)
    fresh = authority.authorize_action(db, actor=actor, case_id=request.case_id,
        expected_event_id=request.expected_event_id, kind=request.action)
    if fresh != admitted:
        submission.fail('authority_changed', '权限或保管责任已变化，请重新核验')
    rows = {'stock_condition_events': [event], 'stock_condition_files': [
        dict(event_id=event['id'], created_at=at, **f) for f in manifest]}
    for name, entries in rows.items():
        if entries:
            db.execute(tables[name].insert(), entries)
    db.flush()
    business.record(db, case=case, event=event, recipient=proved.basis.source.custodian_person_id)
    keys.record(db, event=event, request=request)
    submission._persisted(db, rows)
    if read_event_evidence(db, event_id=event['id']) != files:
        submission.fail('evidence_changed', '本次动作附件持久证明发生变化')
    verified = history.read(db, actor=actor, inbound_line_id=admitted.inbound_line_id)
    latest = [s for s in verified.graph.projection.cases if s.case_id == case['id']]
    if len(latest) != 1 or latest[0].status != event['to_state']:
        submission.fail('history_changed', '持久动作未形成完整、唯一的状态转换')
    return business.verify(db, case=case, event=event, recipient=proved.basis.source.custodian_person_id)
