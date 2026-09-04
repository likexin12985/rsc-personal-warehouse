"""Historical count confirmation under the existing opening-read lock graph.

This GET does not mutate business state, flush, commit, or replay a POST. It is
NOT a PostgreSQL READ ONLY transaction: the canonical ledger/task/reference/
audit owner locks are deliberately retained. ``not_observed`` never proves
that a write did not execute and never authorizes a replacement write.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal
from ..foundation_models import AuditEvent
from ..opening_count_command_status_schemas import (
    OpeningCountCommandStatusOut,
    OpeningCountHistoricalCommandOut,
)
from ..stocktake_models import (
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
)
from . import opening_stocktake_count as count
from . import opening_stocktake_query as query
from . import opening_stocktake_recount as recount


_TRACE = re.compile(r"[A-Za-z0-9._:-]{8,160}", re.ASCII)
_SCOPE_ACTION = "stocktake.opening.scope_count_completed"
_ROUND_ACTION = "stocktake.opening.round_submitted"


class OpeningCountCommandStatusError(RuntimeError):
    def __init__(self, code: str, category: str, message: str):
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.http_status_code = {
            "invalid_request": 400, "forbidden": 403, "not_found": 404,
            "conflict": 409, "precondition_failed": 412,
            "service_unavailable": 503,
        }[category]

    def as_detail(self):
        return {"code": self.code, "category": self.category, "message": self.message}


@dataclass(frozen=True)
class _CountTarget:
    graph: object
    round_row: StocktakeRound
    scope: object
    freezes: tuple
    assignments: dict
    completion: StocktakeScopeCountCompletion | None
    count_plan: object | None
    recount_plan: object | None
    grant_id: uuid.UUID


def opening_count_command_status(
    db: Session, *, actor: FormalPrincipal, task_id: uuid.UUID,
    round_id: uuid.UUID, scope_id: uuid.UUID, actor_person_id: uuid.UUID,
    actor_authorization_version: int, trace_request_id: str,
) -> OpeningCountCommandStatusOut:
    """Bind the authenticated account to a persisted non-sensitive sentinel."""
    for coordinate in (task_id, round_id, scope_id, actor_person_id):
        if not isinstance(coordinate, uuid.UUID) or coordinate.int == 0:
            _invalid_input()
    if (
        type(actor_authorization_version) is not int
        or actor_authorization_version < 1
        or not isinstance(trace_request_id, str)
        or _TRACE.fullmatch(trace_request_id) is None
    ):
        _invalid_input()
    try:
        with db.no_autoflush:
            current = _current_actor(
                db, actor, actor_person_id, actor_authorization_version,
            )
            read_scope = query._require_stocktake_read(db, current)
            task = db.scalar(select(FormalStocktakeTask).where(
                FormalStocktakeTask.id == task_id,
                FormalStocktakeTask.task_type == "opening",
                query._visible_task_predicate(current, read_scope),
            ).execution_options(populate_existing=True))
            if task is None:
                _not_found()
            snapshot = query._snapshot(query._ledger_snapshot(db), (task,))
            # Identical owner order to the formal detail reader. Never call a
            # standalone replay after this transaction's final audit lock.
            root = query._lock_opening_read_batch_root(db, snapshot=snapshot)
            proof = query._lock_opening_read_batch_graph(db, root=root, actor=current)
            current = _current_actor(
                db, current, actor_person_id, actor_authorization_version,
            )
            read_scope = query._require_stocktake_read(db, current)
            graphs = query._load_task_graphs(
                db, actor=current, read_scope=read_scope, tasks=root.tasks,
            )
            graph = graphs[0]
            target = _plan_target(
                db, actor=current, graph=graph, proof=proof,
                round_id=round_id, scope_id=scope_id,
            )
            detail_plan = query._plan_detail_evidence(db, graph, proof=proof)
            _head, audit_proof = query._lock_audit_chain_head_with_proof(
                db, stream_key=count.INVENTORY_STREAM_KEY,
            )
            query._validate_opening_read_batch_graph(db, proof=proof, audit_proof=audit_proof)
            query._reprove_detail_evidence(
                db, graph, proof=proof, detail_plan=detail_plan, audit_proof=audit_proof,
            )
            if target.recount_plan is not None:
                recount._validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
                    db, plan=target.recount_plan, audit_proof=audit_proof,
                )
            if target.count_plan is not None:
                count._validate_opening_count_replay_evidence_from_prelocked_task_graph(
                    db, plan=target.count_plan, audit_proof=audit_proof,
                )
            query._load_reconciliation_statuses(
                db, graphs=graphs, proof=proof, audit_proof=audit_proof,
            )
            command = _resolve_trace(
                db, actor=current, target=target, trace=trace_request_id,
            )
            # Time may have advanced while owners were waiting. Do not turn a
            # historical authorization into current permission to recover.
            current = _current_actor(
                db, current, actor_person_id, actor_authorization_version,
            )
            query._require_stocktake_read(db, current)
            grant, _assignment = _authorize_target(db, current, target)
            if grant.assignment_id != target.grant_id:
                _authorization_changed()
            query._ensure_observation_facts_current(db, graphs)
            query._ensure_snapshot_current(db, snapshot)
            return OpeningCountCommandStatusOut(
                task_id=task_id, round_id=round_id, scope_id=scope_id,
                actor_person_id=current.person_id,
                actor_authorization_version=current.authorization_version,
                trace_request_id=trace_request_id,
                lookup_status="confirmed" if command is not None else "not_observed",
                command=command,
            )
    except OpeningCountCommandStatusError:
        raise
    except query.OpeningStocktakeReadError as exc:
        if exc.status_code == 403:
            _forbidden()
        if exc.status_code == 409:
            raise OpeningCountCommandStatusError(
                "opening_count_command_status_read_changed", "conflict",
                "盘点证据读取期间发生变化，请保持阻塞并重新查询",
            ) from None
        _invalid_evidence()
    except count.OpeningStocktakeCountError as exc:
        if exc.category == "forbidden":
            _forbidden()
        if exc.code == "opening_count_actor_principal_stale":
            _authorization_changed()
        _invalid_evidence()
    except FormalAccessError:
        _forbidden()
    except (SQLAlchemyError, ValidationError, AttributeError, TypeError, ValueError,
            OverflowError, StopIteration, recount.OpeningStocktakeRecountError,
            query.finalize_service.OpeningStocktakeFinalizeError,
            query.reconciliation_service.OpeningControlReconciliationError):
        _invalid_evidence()
    except Exception:
        # All other broken internal proof shapes are unavailable evidence,
        # never an absent command or an exception carrying stored payloads.
        _invalid_evidence()


def _current_actor(db, supplied, person_id, authorization_version):
    if (
        not isinstance(supplied, FormalPrincipal)
        or supplied.person_id != person_id
        or supplied.authorization_version != authorization_version
    ):
        _authorization_changed()
    current = count._require_current_actor(db, supplied, _database_now(db))
    return current


def _database_now(db):
    return count._database_now(db)


def _plan_target(db, *, actor, graph, proof, round_id, scope_id):
    checked = query._require_opening_read_batch_graph(db, proof)
    if graph.task.id not in {task_id for task_id, _plans in checked.round_plans}:
        _invalid_evidence()
    target_round = db.scalar(select(StocktakeRound).where(
        StocktakeRound.task_id == graph.task.id, StocktakeRound.id == round_id,
    ).execution_options(populate_existing=True))
    scope = next((row for row in graph.visible_scopes if row.id == scope_id), None)
    if target_round is None or scope is None:
        _not_found()
    if round_id not in {
        rid for tid, plans in checked.round_plans if tid == graph.task.id
        for rid, _plan in plans
    }:
        _invalid_evidence()
    freezes = tuple(db.scalars(select(InventoryFreeze).where(
        InventoryFreeze.task_id == graph.task.id,
    ).order_by(InventoryFreeze.stocktake_scope_id).execution_options(populate_existing=True)).all())
    # These are historical anchor checks, not a POST state gate. On PG all
    # relevant owners were already pinned by the shared 0027 reference graph.
    count._validate_start_anchors(
        db, graph.task, target_round, graph.scopes, freezes,
        _database_now(db), allow_downstream=True,
    )
    location = next(row for row in graph.locations if row.id == scope.location_id)
    count._validate_current_scope_dimensions(
        db, graph.task, scope, location, _database_now(db),
    )
    recount_plan = None
    assignments = {}
    if target_round.round_type == "recount":
        recount_plan = recount._plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
            db, task=graph.task, round_row=target_round, scopes=graph.scopes,
            freezes=freezes,
            disposition_resolutions=query._round_disposition_resolutions(checked, round_id),
        )
        assignments = recount._opening_recount_assignments_from_plan(db, plan=recount_plan)
    completions = tuple(db.scalars(select(StocktakeScopeCountCompletion).where(
        StocktakeScopeCountCompletion.task_id == graph.task.id,
        StocktakeScopeCountCompletion.round_id == round_id,
    ).order_by(StocktakeScopeCountCompletion.scope_id).execution_options(populate_existing=True)).all())
    completion = next((row for row in completions if row.scope_id == scope_id), None)
    selected = completion or (completions[0] if completions else None)
    count_plan = (
        count._plan_opening_count_replay_evidence(
            db, graph.task, target_round, graph.scopes, selected,
            round_assignments=assignments,
        ) if selected is not None else None
    )
    if selected is None:
        if target_round.status != "counting" or graph.task.current_round_no != target_round.round_no:
            _invalid_evidence()
        for model in (StocktakeCountLine, StocktakeCountObservation):
            if db.scalar(select(model.id).where(model.round_id == round_id).limit(1)) is not None:
                _invalid_evidence()
    target = _CountTarget(graph, target_round, scope, freezes, assignments,
                          completion, count_plan, recount_plan, uuid.UUID(int=0))
    grant, _assignment = _authorize_target(db, actor, target)
    return _CountTarget(graph, target_round, scope, freezes, assignments,
                        completion, count_plan, recount_plan, grant.assignment_id)


def _authorize_target(db, actor, target):
    graph = target.graph
    location = next(row for row in graph.locations if row.id == target.scope.location_id)
    return count._authorize_round_scope_actor(
        db, actor, task=graph.task, round_row=target.round_row,
        scopes=graph.scopes, freezes=target.freezes, scope=target.scope,
        location=location, task_region_org_id=graph.task.region_org_id,
        round_assignments=target.assignments, allow_downstream=True,
    )


def _resolve_trace(db, *, actor, target, trace):
    reference = count._request_reference(trace)
    # Do not constrain this query to the requested scope. Reuse of one actor /
    # trace across multiple scope commands is ambiguous, not a matching hit.
    audits = tuple(db.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.request_id == reference,
    ).order_by(AuditEvent.stream_version, AuditEvent.id).execution_options(populate_existing=True)).all())
    if not audits:
        return None
    primary = tuple(row for row in audits if row.action == _SCOPE_ACTION)
    if len(primary) != 1:
        _invalid_evidence()
    event = primary[0]
    completion = target.completion
    plan = target.count_plan
    if completion is None or plan is None:
        _invalid_evidence()
    if (
        event.aggregate_type != "stocktake_scope"
        or event.aggregate_id != str(target.scope.id)
        or event.stream_key != count.INVENTORY_STREAM_KEY
        or event.id not in plan.audit_event_ids
        or completion.completed_by_user_id != actor.user_id
        or completion.completed_by_person_id != actor.person_id
    ):
        _invalid_evidence()
    if (
        completion.authorization_version != actor.authorization_version
        or completion.completed_role_assignment_id != target.grant_id
    ):
        _authorization_changed()
    context = count._round_event_context(target.graph.task, target.round_row)
    document = event.after_jsonb
    if (
        not isinstance(document, dict)
        or set(document) != {*context, "has_pending_verification", "round_sealed", "zero_confirmed"}
        or any(document[key] != value for key, value in context.items())
        or type(document["round_sealed"]) is not bool
        or type(document["has_pending_verification"]) is not bool
        or document["zero_confirmed"] is not completion.zero_confirmed
        or count._as_utc(event.occurred_at) != count._as_utc(completion.completed_at)
        or count._as_utc(event.created_at) != count._as_utc(completion.completed_at)
    ):
        _invalid_evidence()
    sealed = document["round_sealed"]
    companions = tuple(row for row in audits if row.id != event.id)
    if sealed:
        if len(companions) != 1:
            _invalid_evidence()
        other = companions[0]
        submissions = tuple(db.scalars(select(StocktakeRoundSubmission).where(
            StocktakeRoundSubmission.task_id == target.graph.task.id,
            StocktakeRoundSubmission.round_id == target.round_row.id,
        )).all())
        if (
            len(submissions) != 1
            or submissions[0].sealing_completion_id != completion.id
            or other.action != _ROUND_ACTION
            or other.aggregate_type != "stocktake_round"
            or other.aggregate_id != str(target.round_row.id)
            or other.stream_key != count.INVENTORY_STREAM_KEY
            or other.id not in plan.audit_event_ids
            or other.after_jsonb != context
            or count._as_utc(other.occurred_at) != count._as_utc(completion.completed_at)
            or count._as_utc(other.created_at) != count._as_utc(completion.completed_at)
            or other.stream_version != event.stream_version + 1
            or other.previous_hash != event.event_hash
        ):
            _invalid_evidence()
    elif companions:
        _invalid_evidence()
    return OpeningCountHistoricalCommandOut(
        completion_id=completion.id,
        round_no=target.round_row.round_no,
        completed_at=count._as_utc(completion.completed_at),
        scope_completed=True, caused_round_submission=sealed,
    )


def _invalid_input():
    raise OpeningCountCommandStatusError(
        "opening_count_command_status_input_invalid", "invalid_request",
        "盘点历史命令查询坐标无效",
    )


def _invalid_evidence():
    raise OpeningCountCommandStatusError(
        "opening_count_command_status_evidence_invalid", "service_unavailable",
        "盘点历史命令证据不完整或存在歧义，请保持阻塞并重新查询",
    ) from None


def _not_found():
    raise OpeningCountCommandStatusError(
        "opening_count_command_status_not_found", "not_found",
        "期初盘点命令范围不存在",
    )


def _forbidden():
    raise OpeningCountCommandStatusError(
        "opening_count_command_status_forbidden", "forbidden",
        "当前正式权限不允许查询此盘点命令",
    ) from None


def _authorization_changed():
    raise OpeningCountCommandStatusError(
        "opening_count_command_status_authorization_changed", "precondition_failed",
        "原盘点命令的人员或授权版本已变化，请保持恢复阻塞",
    ) from None
