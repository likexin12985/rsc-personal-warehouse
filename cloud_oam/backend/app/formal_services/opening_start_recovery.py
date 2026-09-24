"""Read-only recovery of one caller's exact published-start request.

Absence is only 'not_observed'; it never authorizes replay or a new write key.
Both the immutable start graph and current caller/scope are revalidated. No
lock, commit, session mutation, or source freshness requirement is introduced.
"""
from dataclasses import asdict,replace
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from ..foundation_models import AuditEvent
from ..stocktake_models import FormalStocktakeTask,FormalStocktakeScope,StocktakeRound,InventoryFreeze
from . import opening_stocktake as opening
from .opening_publication_admission import selection


def _read(db,actor,region,publication,request_reference):
    document,lines=selection(db,actor=actor,region=region,publication=publication)
    audits=tuple(db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id==actor.user_id,
        AuditEvent.action=='stocktake.opening.started',AuditEvent.aggregate_type=='stocktake_task',
        AuditEvent.request_id==request_reference).limit(2)))
    if not audits:return None
    if len(audits)!=1:opening._invalid_start_replay()
    task=db.get(FormalStocktakeTask,UUID(audits[0].aggregate_id),populate_existing=True)
    if task is None or task.created_by_user_id!=actor.user_id or task.region_org_id!=region \
        or str(task.control_sync_run_id)!=document['sync_run_id'] or str(task.control_source_system_id)!=document['source_system_id']:
        opening._fail('opening_recovery_request_conflict','conflict','原请求与选定的区域或发布批次不一致')
    rounds=tuple(db.scalars(select(StocktakeRound).where(StocktakeRound.task_id==task.id,StocktakeRound.round_no==1)))
    if len(rounds)!=1:opening._invalid_start_replay()
    scopes=tuple(db.scalars(select(FormalStocktakeScope).where(FormalStocktakeScope.task_id==task.id).order_by(FormalStocktakeScope.scope_no)))
    freezes={row.stocktake_scope_id:row for row in db.scalars(select(InventoryFreeze).where(InventoryFreeze.task_id==task.id))}
    if any(row.id not in freezes for row in scopes):opening._invalid_start_replay()
    command=opening.StartOpeningStocktakeCommand(task_no=task.task_no,region_org_id=region,
        control_source_system_id=task.control_source_system_id,control_sync_run_id=task.control_sync_run_id,
        control_sync_scope_key=document['sync_scope_key'],control_lines=lines,
        scopes=tuple(opening.OpeningStocktakeScopeInput(row.owner_org_id,row.location_id,row.assignee_user_id,freezes[row.id].freeze_mode) for row in scopes),
        blind_count=task.blind_count,deadline=opening._as_optional_utc(task.deadline),note=task.note)
    result=opening._load_replay(db,actor=actor,command=opening._validate_command(command),idempotency_key_hash=rounds[0].idempotency_key_hash,
        now=opening._database_now(db),locked_replay=(task,rounds[0]),read_only=True)
    return asdict(replace(result,replayed=True))


def recover_start(db,*,actor,region_org_id,publication_id,trace_request_id):
    checked=opening._require_request_id(trace_request_id)
    if not isinstance(region_org_id,UUID) or not region_org_id.int or not isinstance(publication_id,UUID) or not publication_id.int:
        opening._fail('opening_recovery_invalid_selection','invalid_request','需要准确的区域与发布批次')
    if db.new or db.dirty or db.deleted:
        opening._fail('opening_recovery_requires_clean_session','service_unavailable','结果查询需要独立的只读事务')
    try:
        with db.no_autoflush:
            now=opening._database_now(db)
            current=opening._require_current_actor(db,actor,now=now)
            opening._authorize_batch_manager(db,current,region_org_id)
            first=_read(db,current,region_org_id,publication_id,opening._request_reference(checked))
            db.expire_all()
            current=opening._require_current_actor(db,actor,now=opening._database_now(db))
            opening._authorize_batch_manager(db,current,region_org_id)
            second=_read(db,current,region_org_id,publication_id,opening._request_reference(checked))
            opening._require_current_actor(db,current,now=opening._database_now(db))
            if first!=second:
                opening._fail('opening_recovery_changed','service_unavailable','启动结果在读取期间发生变化，请重新查询')
            return dict(schema_version='rsc.opening_start_recovery.v1',actor_person_id=str(current.person_id),
                authorization_version=current.authorization_version,region_org_id=region_org_id,publication_id=publication_id,
                outcome='found' if second else 'not_observed',automatic_retry_allowed=False,result=second)
    except opening.OpeningStocktakeError:raise
    except (SQLAlchemyError,ValueError,KeyError,TypeError,AttributeError):
        opening._fail('opening_recovery_unavailable','service_unavailable','启动结果证据暂不可用，请重新查询')
