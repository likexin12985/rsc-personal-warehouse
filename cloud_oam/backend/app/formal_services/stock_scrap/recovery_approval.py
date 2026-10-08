"""Private atomic application and two-stage review for found scrapped stock.

Caller owns commit/rollback. This creates approval/evidence/events only and
never restores stock. Public activation requires request seals, full native
COMMIT guards and a forward migration; no route or grants are installed here.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import or_, select
from app.formal_access import lock_formal_principal_graph
from app.stock_scrap_recovery_schemas import validated_recovery_request
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_loss_plan import _evidence
from app.formal_services.work_order_query import _aware
from . import recovery_authority as authority, recovery_facts as facts, recovery_events as events
from .tables import tables


def _unused(db, actor, request, key):
    # One namespace across application/region/HQ/execution: a key cannot be
    # rebound to a different stage. Durable absence seals remain a separate gate.
    for name in (*facts.NAMES.values(), 'stock_scrap_recovery_executions'):
        table = tables()[name]
        if db.scalar(select(table.c.id).where(or_(table.c.idempotency_key_hash == key,
                (table.c.actor_user_id == actor.user_id) & (table.c.request_id == request.request_id)))) is not None:
            authority.fail('request_requires_lookup', '原请求已有事实，请只读回查，禁止重复提交')


def _application(db, request, source):
    table = tables()[facts.NAMES['apply']]
    row = db.execute(select(table).where(table.c.id == request.recovery_request_id)).mappings().one_or_none()
    if row is None or row['scrap_line_id'] != source['line']['id'] or row['request_hash'] != request.expected_request_hash:
        authority.fail('request_changed', '找回申请与本次复核绑定不一致')
    return dict(row)


def submit(db, *, actor, request):
    request = validated_recovery_request(request)
    stage = next((key for key, model in facts.CONTRACTS.items() if type(request) is model), None)
    if stage is None:
        raise ValueError('an exact recovery application or review is required')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    source = authority.load_source(db, request.source)
    application = _application(db, request, source) if stage != 'apply' else None
    # Scope check before traversing private business evidence.
    current = authority.authorize(db, actor=actor, source=source, stage=stage, applicant=application)
    authority.require_current_scrap(db, source)
    key = posting._storage_hash('stock-scrap-recovery:' + request.idempotency_key)
    _unused(db, current, request, key)
    regional, evidence = None, None
    if stage == 'apply':
        table = tables()[facts.NAMES['apply']]
        prior = facts.rows(db, 'apply', table.c.scrap_line_id == source['line']['id'])
        prior_times = [_aware(source['line']['created_at'])]
        for old in prior:
            history = facts.verify_history(db, application=old, source=source)
            if history[0] != 'needs_evidence':
                authority.fail('application_pending', '准确报废已有待处理或已批准找回申请')
            prior_times.append(history[-1])
        evidence = _evidence(db, current, request.evidence_file_ids)
        at_after = max(prior_times)
    else:
        status, regional, final, at_after = facts.verify_history(db, application=application, source=source)
        expected = 'awaiting_regional' if stage == 'regional' else 'awaiting_headquarters'
        if status != expected:
            authority.fail('stage_changed', '找回审批阶段已变化，请重新查看准确记录')
        if stage == 'headquarters' and (regional['id'] != request.regional_review_id or regional['request_hash'] != request.expected_regional_hash):
            authority.fail('regional_changed', '总部复核必须绑定当前准确的区域核实')
        current = authority.authorize(db, actor=current, source=source, stage=stage, applicant=application,
            regional=regional if stage == 'headquarters' else None)
    at = datetime.now(timezone.utc)
    if at <= _aware(at_after):
        authority.fail('clock_invalid', '当前时间未晚于前序事实，拒绝倒写审批')
    value = facts.intent(request)
    row = dict(id=uuid4(), created_at=at, actor_user_id=current.user_id, actor_person_id=current.person_id,
        authorization_version=current.authorization_version, request_id=request.request_id,
        idempotency_key_hash=key, request_hash=sources._hash(value), reason=request.reason, command_jsonb=value,
        scrap_line_id=request.source.scrap_line_id)
    if stage == 'apply':
        row['expected_scrap_request_hash'] = request.source.expected_scrap_request_hash
    else:
        row.update(recovery_request_id=application['id'], decision=request.decision)
        if stage == 'regional':
            row['expected_request_hash'] = request.expected_request_hash
        else:
            row.update(regional_review_id=regional['id'], regional_decision='verified', expected_regional_hash=regional['request_hash'])
    db.execute(tables()[facts.NAMES[stage]].insert(), row)
    if evidence is not None:
        db.execute(tables()['stock_scrap_recovery_files'].insert(), [dict(recovery_request_id=row['id'],
            file_id=UUID(r['file_id']), metadata_sha256=r['metadata_sha256'], created_at=at) for r in evidence])
    recipient = application['actor_person_id'] if application else current.person_id
    events.record(db, row=row, stage=stage, recipient=recipient)
    table = tables()[facts.NAMES[stage]]
    persisted = db.execute(select(table).where(table.c.id == row['id'])).mappings().one()
    if any((_aware(persisted[k]) != _aware(v) if k == 'created_at' else persisted[k] != v) for k, v in row.items()):
        authority.fail('persisted_changed', '持久找回事实与本次准确请求不一致')
    facts.verify_history(db, application=application or row, source=source)
    authority.authorize(db, actor=current, source=authority.load_source(db, request.source), stage=stage,
        applicant=application, regional=regional if stage == 'headquarters' else None)
    authority.require_current_scrap(db, source)
    return events.verify(db, row=row, stage=stage, recipient=recipient)
