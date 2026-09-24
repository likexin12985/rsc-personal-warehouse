"""An exact original opening request may finish or be permanently fenced.

The inventory ledger lock serializes both new opening facts and this negative
fact. Lookup remains SELECT-only; a missing result never authorizes replay.
"""
import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..stocktake_models import OpeningStartCommandSeal as Seal
from . import opening_stocktake as opening, opening_start_recovery as recovery
from .inventory_posting import _lock_inventory_ledger_head_for_atomic_batch, InventoryPostingError
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot, AuditChainError


def _row(db, actor, request):
    return db.scalar(select(Seal).where(Seal.actor_user_id == actor.user_id,
        Seal.request_id == request).execution_options(populate_existing=True))


def require_unsealed(db, actor, request):
    if _row(db, actor, request) is not None:
        opening._fail('opening_start_request_sealed', 'conflict', '原启动请求已永久终结，请查询原结果')


def _coordinates(region, publication, request):
    opening._require_request_id(request)
    if any(not isinstance(value, UUID) or not value.int for value in (region, publication)):
        opening._fail('opening_recovery_invalid_selection', 'invalid_request', '需要准确的区域与发布批次')


def _actor(db, supplied, region):
    actor = opening._require_current_actor(db, supplied, now=opening._database_now(db))
    opening._authorize_batch_manager(db, actor, region)
    return actor


def seal_payload(row):
    return dict(actor_person_id=str(row.actor_person_id), authorization_version=row.authorization_version,
        region_org_id=str(row.region_org_id), publication_id=str(row.publication_id), trace_request_id=row.request_id)


def _verified_seal(db, actor, region, publication, request, row):
    if (row.actor_user_id != actor.user_id or row.actor_person_id != actor.person_id
            or row.region_org_id != region or row.publication_id != publication or row.request_id != request
            or row.authorization_version < 1 or row.authorization_version > actor.authorization_version
            or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', row.request_id)
            or row.request_reference != opening._request_reference(request)):
        opening._fail('opening_recovery_request_conflict', 'conflict', '原请求终结记录与当前坐标不一致')
    if not row.id.int or opening._as_utc(row.created_at) > opening._database_now(db):
        opening._invalid_start_replay()
    events = tuple(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type == 'opening_start_command_seal',
        AuditEvent.aggregate_id == str(row.id)).limit(2)))
    if len(events) != 1: opening._invalid_start_replay()
    event = events[0]
    if (event.stream_key != 'inventory' or event.actor_user_id != row.actor_user_id
            or event.action != 'opening_start.sealed' or event.before_jsonb != {}
            or event.after_jsonb != seal_payload(row) or event.request_id != 'opening-start-seal:' + str(row.id)
            or opening._as_utc(event.occurred_at) != opening._as_utc(row.created_at)
            or opening._as_utc(event.created_at) != opening._as_utc(row.created_at)):
        opening._invalid_start_replay()
    verify_audit_event_in_read_snapshot(db, stream_key='inventory', event_id=event.id)
    return dict(seal_id=row.id, actor_person_id=row.actor_person_id, authorization_version=row.authorization_version,
        region_org_id=row.region_org_id, publication_id=row.publication_id, trace_request_id=row.request_id,
        sealed_at=opening._as_utc(row.created_at), permanent_nonexecution=True)


def _observe(db, actor, region, publication, request):
    result = recovery._read(db, actor, region, publication, opening._request_reference(request))
    row = _row(db, actor, request)
    seal_audits = tuple(db.scalars(select(AuditEvent.aggregate_id).where(
        AuditEvent.aggregate_type == 'opening_start_command_seal', AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.after_jsonb['trace_request_id'].as_string() == request).limit(2)))
    if seal_audits != ((str(row.id),) if row is not None else ()):
        opening._invalid_start_replay()
    if row is not None and result is not None: opening._invalid_start_replay()
    # Partial or contradictory execution evidence cannot be turned into a seal.
    states = set(db.scalars(select(StateTransitionEvent.aggregate_id).where(
        StateTransitionEvent.actor_id == actor.user_id, StateTransitionEvent.aggregate_type == 'stocktake_task',
        StateTransitionEvent.metadata_jsonb['request_reference'].as_string() == opening._request_reference(request))))
    if states != ({str(result['task_id'])} if result else set()): opening._invalid_start_replay()
    seal = _verified_seal(db, actor, region, publication, request, row) if row is not None else None
    return dict(schema_version='rsc.opening_start_recovery.v2', actor_person_id=str(actor.person_id),
        authorization_version=actor.authorization_version, region_org_id=region, publication_id=publication,
        outcome='found' if result else 'sealed' if seal else 'not_observed', automatic_retry_allowed=False,
        result=result, seal=seal)


def recover_start_command(db, *, actor, region_org_id, publication_id, trace_request_id):
    _coordinates(region_org_id, publication_id, trace_request_id)
    if db.new or db.dirty or db.deleted:
        opening._fail('opening_recovery_requires_clean_session', 'service_unavailable', '结果查询需要独立的只读事务')
    try:
        with db.no_autoflush:
            current = _actor(db, actor, region_org_id)
            first = _observe(db, current, region_org_id, publication_id, trace_request_id)
            db.expire_all()
            current = _actor(db, actor, region_org_id)
            second = _observe(db, current, region_org_id, publication_id, trace_request_id)
            _actor(db, current, region_org_id)
            if first != second:
                opening._fail('opening_recovery_changed', 'service_unavailable', '原请求状态读取期间变化，请重新查询')
            return second
    except opening.OpeningStocktakeError: raise
    except (SQLAlchemyError, AuditChainError, ValueError, KeyError, TypeError, AttributeError):
        opening._fail('opening_recovery_unavailable', 'service_unavailable', '原请求证据暂不可用，请保留记录')


def seal_start_command(db, *, actor, region_org_id, publication_id, trace_request_id):
    _coordinates(region_org_id, publication_id, trace_request_id)
    if db.new or db.dirty or db.deleted:
        opening._fail('opening_seal_requires_clean_session', 'service_unavailable', '终结请求需要独立事务')
    # Do not acquire the region/task/idempotency advisories after this lock:
    # new starts hold them before entering the shared ledger writer graph.
    try:
        _lock_inventory_ledger_head_for_atomic_batch(db)
    except InventoryPostingError:
        opening._fail('opening_seal_unavailable', 'service_unavailable', '原请求终结暂不可用，请保留记录')
    lock_formal_principal_graph(db, (actor.user_id,))
    current = _actor(db, actor, region_org_id)
    original = _observe(db, current, region_org_id, publication_id, trace_request_id)
    if original['outcome'] != 'not_observed': return original
    now = opening._database_now(db)
    row = Seal(id=uuid4(), actor_user_id=current.user_id, actor_person_id=current.person_id,
        authorization_version=current.authorization_version, region_org_id=region_org_id, publication_id=publication_id,
        request_id=trace_request_id, request_reference=opening._request_reference(trace_request_id), created_at=now)
    db.add(row)
    db.flush()
    append_audit_event(db, stream_key='inventory', actor_user_id=current.user_id,
        action='opening_start.sealed', aggregate_type='opening_start_command_seal', aggregate_id=str(row.id),
        before_jsonb={}, after_jsonb=seal_payload(row), request_id='opening-start-seal:' + str(row.id),
        occurred_at=now, created_at=now)
    db.flush()
    return _observe(db, _actor(db, current, region_org_id), region_org_id, publication_id, trace_request_id)
