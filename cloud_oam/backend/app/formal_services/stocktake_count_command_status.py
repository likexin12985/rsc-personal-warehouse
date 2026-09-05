"""Historical acknowledgements for non-opening initial/recount scope counts.

No flush, commit, rollback, replay or business mutation. PostgreSQL owner locks
are intentional: this is NOT a SQL READ ONLY transaction. An absent trace is
unknown, never proof of nonexecution or permission to replace a command.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import AuditEvent, DocumentAttachment, FileObject, StateTransitionEvent
from ..stocktake_models import (
    FormalStocktakeTask, StocktakeRound, StocktakeScopeCountCompletion,
    StocktakeRecountCase, StocktakeRecountScopeAssignment,
)
from ..stocktake_count_command_status_schemas import (
    StocktakeCountCommandStatusOut, StocktakeCountHistoricalCommandOut,
)
from . import stocktake_count as count
from . import stocktake_difference as difference
from . import stocktake_query as query
from . import stocktake_recount_count as recount
from . import stocktake_review as review
from .audit_chain import (
    _lock_audit_chain_head_with_proof, _verify_audit_event_with_prelocked_proof,
)
from .postgresql_lock_graph import lock_nonopening_stocktake_review_graph
from . import postgresql_lock_graph as owner_locks
from .stocktake_count_history import _make_history_context


_TRACE = re.compile(r"[A-Za-z0-9._:-]{8,160}", re.ASCII)
_ACTIONS = {
    "initial_count": ("stocktake.scope_count.submitted", "stocktake.initial_round.submitted"),
    "recount_count": ("stocktake.recount_scope_count.submitted", "stocktake.recount_round.submitted"),
}


class StocktakeCountCommandStatusError(RuntimeError):
    def __init__(self, code, category, message):
        super().__init__(message)
        self.code, self.category, self.message = code, category, message
        self.http_status_code = {
            "invalid_request": 400, "forbidden": 403, "not_found": 404,
            "conflict": 409, "precondition_failed": 412, "service_unavailable": 503,
        }[category]

    def as_detail(self):
        return {"code": self.code, "category": self.category, "message": self.message}


def stocktake_count_command_status(
    db: Session, *, actor: FormalPrincipal, operation: str,
    task_id: uuid.UUID, round_id: uuid.UUID, scope_id: uuid.UUID,
    actor_person_id: uuid.UUID, actor_authorization_version: int, trace_request_id: str,
) -> StocktakeCountCommandStatusOut:
    for coordinate in (task_id, round_id, scope_id, actor_person_id):
        if not isinstance(coordinate, uuid.UUID) or coordinate.int == 0:
            _invalid_input()
    if (
        not isinstance(operation, str) or operation not in _ACTIONS
        or type(actor_authorization_version) is not int or actor_authorization_version < 1
        or not isinstance(trace_request_id, str) or _TRACE.fullmatch(trace_request_id) is None
    ):
        _invalid_input()
    try:
        with db.no_autoflush:
            context = _context(db, actor, actor_person_id, actor_authorization_version)
            task = _visible_task(db, context, task_id)
            # Preserve the not-found/operation contract before the SQL owner
            # capability (which intentionally exposes only invariant failure).
            target_round = db.scalar(select(StocktakeRound).where(
                StocktakeRound.task_id == task_id, StocktakeRound.id == round_id,
            ).execution_options(populate_existing=True))
            if target_round is None:
                _not_found()
            _validate_operation_round(operation, target_round)
            # 0062 owns the complete principal/reference/evidence/file union.
            # Nothing below may discover a new PG owner while proving history.
            owner_locks.lock_nonopening_stocktake_count_history_graph(db, task_id, round_id, actor.user_id)
            # Same first owner as every count/posting writer; no audit-first
            # recovery path and no POST idempotency advisory lock is needed.
            cursor = count._lock_current_ledger_cursor(db)
            task = _visible_task(db, context, task_id, lock=True)
            users = _principal_users(db, task_id, actor.user_id)
            lock_formal_principal_graph(db, users)
            target_round = db.scalar(select(StocktakeRound).where(
                StocktakeRound.task_id == task_id, StocktakeRound.id == round_id,
            ).with_for_update().execution_options(populate_existing=True))
            if target_round is None:
                _not_found()
            _validate_operation_round(operation, target_round)
            lock_nonopening_stocktake_review_graph(db, task_id, round_id)
            if users != _principal_users(db, task_id, actor.user_id):
                _changed()
            context = _context(db, actor, actor_person_id, actor_authorization_version)
            _visible_task(db, context, task_id)
            graph = query._load_task_graphs(db, (task,))[0]
            query._validate_graph(graph)
            snapshots = query._task_snapshots((task,))
            scope = next((row for row in graph.scopes if row.id == scope_id), None)
            if scope is None or scope_id not in query._visible_scope_ids(context, graph):
                _not_found()
            _current_scope_dimensions(db, graph, scope)
            if task.cutoff_ledger_cursor is None or cursor < task.cutoff_ledger_cursor:
                _invalid_evidence()
            plans = _frozen_scope_plans(db, graph)
            count._validate_snapshot_manifest(task, plans, graph.snapshots, graph.accounts)
            attachment_rows, files = _lock_files(db, graph)
            history = _make_history_context(
                db, graph=graph, target_round=target_round, scope_id=scope_id,
                operation=operation, plans=plans, files=tuple(files.values()), now=_database_now(db),
            )
            recount_graph = None
            if operation == "recount_count":
                recount_graph = recount._load_and_validate_recount_assignment_graph(
                    db, task=task, round_row=target_round, scopes=graph.scopes, history=history,
                )
            grant = _authorize(db, context.principal, graph, scope, recount_graph)
            completions = tuple(row for row in graph.scope_completions if row.round_id == round_id)
            file_counts = _validate_counts(db, graph, target_round, completions, files, attachment_rows, cursor)
            submission = _validate_submission(db, graph, target_round, completions, recount_graph, history=history)
            ancestors = []
            source_graph = recount_graph
            while source_graph is not None:
                source = source_graph.source_evidence
                prior_graph = source.ancestor_graph
                prior_completions = tuple(row for row in graph.scope_completions if row.round_id == source.round_row.id)
                prior_files = _validate_counts(db, graph, source.round_row, prior_completions, files, attachment_rows, cursor)
                prior_submission = _validate_submission(
                    db, graph, source.round_row, prior_completions, prior_graph, history=history,
                )
                ancestors.append((source.round_row, prior_completions, prior_submission, prior_graph, prior_files))
                source_graph = prior_graph
            # Last new owner. Everything below is SELECT-only and reuses the
            # prelocked chain proof; no helper may acquire new reference locks.
            _head, proof = _lock_audit_chain_head_with_proof(db, stream_key=count.INVENTORY_STREAM_KEY)
            history.verify_release_audits(db, task, proof)
            if recount_graph is not None:
                recount._verify_recount_source_audits(db, graph=recount_graph, proof=proof)
            for prior_round, prior_completions, prior_submission, prior_graph, prior_files in ancestors:
                _validate_audits(
                    db, graph, prior_round, prior_completions, prior_submission, prior_graph,
                    prior_files, proof, "recount_count" if prior_graph is not None else "initial_count",
                )
            events = _validate_audits(
                db, graph, target_round, completions, submission,
                recount_graph, file_counts, proof, operation,
            )
            command = _resolve_trace(
                db, context.principal, graph, target_round, scope, completions,
                submission, events, grant, trace_request_id, operation,
            )
            fresh = _context(db, actor, actor_person_id, actor_authorization_version)
            _visible_task(db, fresh, task_id)
            if fresh != context or scope_id not in query._visible_scope_ids(fresh, graph):
                _authorization_changed()
            _current_scope_dimensions(db, graph, scope)
            # Re-evaluate time-sensitive count permission without late owner
            # locks. The exact selected grant was already checked and pinned.
            if not count._grant_allows_count(
                db, fresh.principal, grant,
                target_scope_type="person" if grant.role_code == "technician" else
                    "national" if grant.role_code == "admin" else "organization",
                target_scope_id=str(scope.custodian_person_id_snapshot) if grant.role_code == "technician" else
                    "*" if grant.role_code == "admin" else str(scope.owner_org_id),
            ):
                _forbidden()
            if _attachment_rows(db, graph) != attachment_rows:
                _changed()
            query._ensure_tasks_current(db, snapshots)
            return StocktakeCountCommandStatusOut(
                operation=operation, task_id=task_id, round_id=round_id, scope_id=scope_id,
                actor_person_id=actor_person_id, actor_authorization_version=actor_authorization_version,
                trace_request_id=trace_request_id,
                lookup_status="confirmed" if command is not None else "not_observed", command=command,
            )
    except StocktakeCountCommandStatusError:
        raise
    except (query.StocktakeReadError, count.StocktakeCountError, recount.StocktakeRecountCountError) as exc:
        if exc.category == "forbidden":
            _forbidden()
        if exc.category == "precondition_failed":
            _authorization_changed()
        if exc.category == "conflict":
            _changed()
        _invalid_evidence()
    except Exception:
        # Never expose stored count payloads, SQL, or a broken proof as absence.
        _invalid_evidence()


def _database_now(db):
    return count._database_now(db)


def _validate_operation_round(operation, round_row):
    if (operation == "initial_count") != (
        round_row.round_type == "initial" and round_row.round_no == 1
    ) or (operation == "recount_count" and round_row.round_type != "recount"):
        _invalid_input()


def _context(db, actor, person_id, version):
    if not isinstance(actor, FormalPrincipal) or actor.person_id != person_id or actor.authorization_version != version:
        _authorization_changed()
    return query._load_read_context(db, actor=actor, now=_database_now(db))


def _visible_task(db, context, task_id, *, lock=False):
    statement = select(FormalStocktakeTask).where(
        FormalStocktakeTask.id == task_id, FormalStocktakeTask.task_type.in_(count._NON_OPENING_TYPES),
        query._visible_task_predicate(context),
    )
    if lock:
        statement = statement.with_for_update()
    task = db.scalar(statement.execution_options(populate_existing=True))
    if task is None:
        _not_found()
    return task


def _principal_users(db, task_id, user_id):
    values = set(review._task_principal_user_ids(db, task_id, supplied_user_ids=(user_id,)))
    for model, column in (
        (StocktakeScopeCountCompletion, StocktakeScopeCountCompletion.completed_by_user_id),
        (StocktakeRecountCase, StocktakeRecountCase.opened_by_user_id),
        (StocktakeRecountScopeAssignment, StocktakeRecountScopeAssignment.assignee_user_id),
    ):
        values.update(db.scalars(select(column).where(model.task_id == task_id)).all())
    return tuple(sorted(values))


def _authorize(db, actor, graph, scope, recount_graph):
    if recount_graph is None:
        grant = count._authorize_exact_scope_actor(db, actor, graph.task, scope)
        count._lock_current_assignment(db, actor, grant, _database_now(db), lock_rows=False)
        return grant
    assignment = next((row for row in recount_graph.assignments if row.scope_id == scope.id), None)
    if assignment is None:
        _forbidden()
    _live, grant = recount._authorize_exact_recount_actor(
        db, actor=actor, task=graph.task, scope=scope, assignment_snapshot=assignment,
        now=_database_now(db), lock_rows=False,
    )
    return grant


def _current_scope_dimensions(db, graph, scope):
    # These helpers use ordinary SELECT on PG; the target location/organization
    # owners are already held. Do not revalidate unrelated current assignees.
    service = count.task_service
    location = service._require_location(db, scope.location_id, graph.task.region_org_id)
    service._require_asset_owner(db, scope.owner_org_id, graph.task.region_org_id)
    if service._require_location_custody(db, location, now=_database_now(db)) != scope.custodian_person_id_snapshot:
        _authorization_changed()


def _frozen_scope_plans(db, graph):
    """Reprove frozen scope facts, not today's unrelated assignee lifecycle."""
    service = count.task_service
    rows = tuple(db.scalars(select(StateTransitionEvent).where(
        StateTransitionEvent.aggregate_type == "stocktake_task",
        StateTransitionEvent.aggregate_id == str(graph.task.id),
        StateTransitionEvent.from_status.is_(None), StateTransitionEvent.to_status == "draft",
    ).execution_options(populate_existing=True)).all())
    if len(rows) != 1 or not isinstance(rows[0].metadata_jsonb, dict):
        _invalid_evidence()
    metadata = rows[0].metadata_jsonb
    documents = metadata.get("scope_plan")
    if metadata.get("schema") != service._CREATE_COMMAND_SCHEMA or not isinstance(documents, list) or len(documents) != len(graph.scopes):
        _invalid_evidence()
    plans = []
    initial_round = next((row for row in graph.rounds if row.round_no == 1), None)
    # Non-opening start captures the ledger cutoff before waiting for the
    # audit owner, then timestamps the atomic freeze/round at its later start.
    # Fixed test clocks may coincide; production clocks normally do not.
    if (initial_round is None
        or count._as_utc(graph.task.cutoff_at) > count._as_utc(graph.task.frozen_at)
        or count._as_utc(initial_round.started_at) != count._as_utc(graph.task.frozen_at)):
        _invalid_evidence()
    for scope, document in zip(graph.scopes, documents, strict=True):
        if not isinstance(document, dict) or document.get("freeze_mode") not in {"hard", "cutoff_replay"}:
            _invalid_evidence()
        plan = service._ScopePlan(
            scope_id=scope.id, scope_no=scope.scope_no, scope_mode=scope.scope_mode,
            owner_org_id=scope.owner_org_id, location_id=scope.location_id,
            assignee_user_id=scope.assignee_user_id, custodian_person_id=scope.custodian_person_id_snapshot,
            material_id=scope.material_id, condition_code=scope.condition_code,
            availability_bucket=scope.availability_bucket, freeze_mode=document["freeze_mode"],
            scope_key=scope.scope_key, scope_sha256=scope.scope_sha256,
        )
        if (service._scope_plan_document(plan) != document or service._scope_hash(plan) != scope.scope_sha256
            or service._scope_key(plan) != scope.scope_key):
            _invalid_evidence()
        freeze = next((row for row in graph.freezes if row.stocktake_scope_id == scope.id), None)
        if (freeze is None or freeze.scope_key != scope.scope_key or freeze.freeze_mode != plan.freeze_mode
            or count._as_utc(freeze.valid_from) != count._as_utc(graph.task.frozen_at)):
            _invalid_evidence()
        plans.append(plan)
    service._require_non_overlapping_plans(plans)
    if service._scope_manifest_sha256(graph.task.task_type, graph.task.region_org_id, plans) != graph.task.scope_manifest_sha256:
        _invalid_evidence()
    return tuple(plans)


def _attachment_rows(db, graph):
    ids = tuple(str(row.id) for row in graph.scope_completions)
    if not ids:
        return ()
    return tuple(db.execute(select(
        DocumentAttachment.id, DocumentAttachment.document_id, DocumentAttachment.file_id,
        DocumentAttachment.status, DocumentAttachment.uploaded_by,
    ).where(
        DocumentAttachment.document_type == "stocktake_scope_count_completion",
        DocumentAttachment.document_id.in_(ids),
        DocumentAttachment.attachment_type == "stocktake_evidence",
    ).order_by(DocumentAttachment.id)).all())


def _lock_files(db, graph):
    attachments = _attachment_rows(db, graph)
    ids = tuple(sorted({row.file_id for row in attachments}, key=str))
    files = tuple(db.scalars(select(FileObject).where(FileObject.id.in_(ids))
        .order_by(FileObject.id).with_for_update().execution_options(populate_existing=True)).all()) if ids else ()
    if len(files) != len(ids):
        _invalid_evidence()
    return attachments, {row.id: row for row in files}


def _validate_counts(db, graph, round_row, completions, files, attachments, cursor):
    scope_ids = {row.id for row in graph.scopes}
    completed_ids = {row.scope_id for row in completions}
    if len(completed_ids) != len(completions) or not completed_ids <= scope_ids:
        _invalid_evidence()
    lines = tuple(row for row in graph.count_lines if row.round_id == round_row.id)
    observations = tuple(row for row in graph.observations if row.round_id == round_row.id)
    if any(row.scope_id not in completed_ids for row in (*lines, *observations)):
        _invalid_evidence()
    file_counts = {}
    for completion in completions:
        scope = next(row for row in graph.scopes if row.id == completion.scope_id)
        if (round_row.round_type == "initial" and completion.completed_by_user_id != scope.assignee_user_id):
            _invalid_evidence()
        scope_lines = tuple(row for row in lines if row.scope_id == completion.scope_id)
        scope_observations = tuple(row for row in observations if row.scope_id == completion.scope_id)
        bound = tuple(row for row in attachments if row.document_id == str(completion.id))
        scope_files = tuple(files[row.file_id] for row in bound)
        if len({row.file_id for row in bound}) != len(bound) or any(
            row.status != "active" or row.uploaded_by != completion.completed_by_user_id for row in bound
        ) or any(not count.formal_file_service.is_available_formal_file_for_purpose(
            row, purpose="stocktake_evidence", uploader_user_id=completion.completed_by_user_id,
        ) for row in scope_files):
            _invalid_evidence()
        difference._validate_historical_authorization(db, completion)
        expected = count._scope_evidence_manifest(
            db, completion_id=completion.id, task_id=graph.task.id, round_id=round_row.id,
            scope_id=completion.scope_id, lines=scope_lines, observations=scope_observations,
            files=scope_files, authorization_sha256=completion.authorization_sha256,
            count_ledger_cursor=completion.count_ledger_cursor,
        )
        serial_count = sum(row.count_line_id in {line.id for line in scope_lines} for row in graph.count_serials)
        serial_count += sum(row.serial_id is not None for row in scope_observations)
        snapshot_ids = {row.stock_account_id for row in graph.snapshots if row.scope_id == completion.scope_id}
        if (
            {row.stock_account_id for row in scope_lines} != snapshot_ids or len(scope_lines) != len(snapshot_ids)
            or completion.count_line_count != len(scope_lines) or completion.observation_line_count != len(scope_observations)
            or completion.serial_count != serial_count
            or completion.total_counted_qty != count._quantity_sum(tuple(row.counted_qty for row in (*scope_lines, *scope_observations)))
            or completion.zero_confirmed is not (not scope_lines and not scope_observations)
            or completion.evidence_manifest_sha256 != expected
            or type(completion.count_ledger_cursor) is not int
            or not graph.task.cutoff_ledger_cursor <= completion.count_ledger_cursor <= cursor
            or count._as_utc(completion.created_at) != count._as_utc(completion.completed_at)
            or count._as_utc(completion.completed_at) < count._as_utc(round_row.started_at)
        ):
            _invalid_evidence()
        file_counts[completion.id] = len(scope_files)
    return file_counts


def _validate_submission(db, graph, round_row, completions, recount_graph, *, history=None):
    submissions = tuple(row for row in graph.submissions if row.round_id == round_row.id)
    expected_scopes = recount_graph.selected_scope_ids if recount_graph is not None else {row.id for row in graph.scopes}
    if not {row.scope_id for row in completions} <= expected_scopes:
        _invalid_evidence()
    if round_row.status == "counting":
        if submissions or len(completions) >= len(expected_scopes) or graph.task.current_round_no != round_row.round_no:
            _invalid_evidence()
        return None
    if round_row.status != "submitted" or len(submissions) != 1:
        _invalid_evidence()
    submission = submissions[0]
    if recount_graph is not None:
        recount._validate_sealed_recount_submission(db, graph.task, round_row, recount_graph)
    else:
        difference._validate_completion_and_submission_manifests(
            db, task=graph.task, round_row=round_row, scopes=graph.scopes,
            snapshots=graph.snapshots, accounts=graph.accounts,
            count_lines=tuple(row for row in graph.count_lines if row.round_id == round_row.id),
            count_serials=tuple(row for row in graph.count_serials if row.round_id == round_row.id),
            observations=tuple(row for row in graph.observations if row.round_id == round_row.id),
            completions=completions, submission=submission,
            history=history,
        )
    sealing = next((row for row in completions if row.id == submission.sealing_completion_id), None)
    if sealing is None or (
        submission.submitted_by_user_id != sealing.completed_by_user_id
        or submission.submitted_by_person_id != sealing.completed_by_person_id
        or submission.submitted_role_assignment_id != sealing.completed_role_assignment_id
        or submission.authorization_version != sealing.authorization_version
        or round_row.submitted_by_user_id != sealing.completed_by_user_id
        or any(count._as_utc(value) != count._as_utc(sealing.completed_at) for value in
               (submission.submitted_at, submission.created_at, round_row.submitted_at))
    ):
        _invalid_evidence()
    return submission


def _validate_audits(db, graph, round_row, completions, submission, recount_graph, file_counts, proof, operation):
    action, round_action = _ACTIONS[operation]
    candidates = tuple(db.scalars(select(AuditEvent).where(
        AuditEvent.action == action, AuditEvent.aggregate_type == "stocktake_scope",
        AuditEvent.aggregate_id.in_(tuple(str(row.scope_id) for row in completions)),
    ).execution_options(populate_existing=True)).all())
    events = {}
    for completion in completions:
        matching = tuple(row for row in candidates if row.aggregate_id == str(completion.scope_id)
                         and isinstance(row.after_jsonb, dict) and row.after_jsonb.get("round_id") == str(round_row.id))
        if len(matching) != 1:
            _invalid_evidence()
        event = matching[0]
        sealed = submission is not None and submission.sealing_completion_id == completion.id
        expected = {
            "count_ledger_cursor": completion.count_ledger_cursor, "count_line_count": completion.count_line_count,
            "evidence_file_count": file_counts[completion.id], "observation_line_count": completion.observation_line_count,
            "round_id": str(round_row.id), "round_submitted": sealed, "serial_count": completion.serial_count,
            "task_id": str(graph.task.id), "zero_confirmed": completion.zero_confirmed,
        }
        if recount_graph is not None:
            assignment = next(row for row in recount_graph.assignments if row.scope_id == completion.scope_id)
            if (completion.completed_by_user_id != assignment.assignee_user_id
                or completion.completed_by_person_id != assignment.assignee_person_id
                or completion.completed_role_assignment_id != assignment.assignee_role_assignment_id):
                _invalid_evidence()
            expected.update(assignment_id=str(assignment.id), recount_case_id=str(recount_graph.case.id))
        _verify_event(db, event, completion, proof)
        if event.before_jsonb is not None or count._canonical_bytes(event.after_jsonb) != count._canonical_bytes(expected):
            _invalid_evidence()
        companions = tuple(db.scalars(select(AuditEvent).where(
            AuditEvent.actor_user_id == event.actor_user_id, AuditEvent.request_id == event.request_id,
        ).order_by(AuditEvent.stream_version, AuditEvent.id).execution_options(populate_existing=True)).all())
        if len(companions) != (2 if sealed else 1) or event.id not in {row.id for row in companions}:
            _invalid_evidence()
        if sealed:
            other = next(row for row in companions if row.id != event.id)
            _verify_event(db, other, completion, proof)
            transition = db.scalar(select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task", StateTransitionEvent.aggregate_id == str(graph.task.id),
                StateTransitionEvent.idempotency_key == (recount if recount_graph else count)._event_key("task", round_row.id),
            ).execution_options(populate_existing=True))
            if transition is None or not isinstance(transition.metadata_jsonb, dict):
                _invalid_evidence()
            version = transition.metadata_jsonb.get("task_version")
            after = {"difference_status": "not_evaluated", "status": "submitted", "task_id": str(graph.task.id),
                     "task_status": "submitted", "task_version": version}
            if recount_graph is not None:
                after.update(recount_case_id=str(recount_graph.case.id), selected_scope_count=len(recount_graph.selected_scope_ids))
            if (type(version) is not int or not 1 <= version <= graph.task.version
                or other.action != round_action or other.aggregate_type != "stocktake_round" or other.aggregate_id != str(round_row.id)
                or other.before_jsonb != {"status": "counting"}
                or count._canonical_bytes(other.after_jsonb) != count._canonical_bytes(after)
                or other.stream_version != event.stream_version + 1 or other.previous_hash != event.event_hash):
                _invalid_evidence()
        events[completion.id] = event
    return events


def _verify_event(db, event, completion, proof):
    _verify_audit_event_with_prelocked_proof(db, proof=proof, stream_key=count.INVENTORY_STREAM_KEY, event_id=event.id)
    if (event.stream_key != count.INVENTORY_STREAM_KEY or event.actor_user_id != completion.completed_by_user_id
        or count._as_utc(event.occurred_at) != count._as_utc(completion.completed_at)):
        _invalid_evidence()


def _resolve_trace(db, actor, graph, round_row, scope, completions, submission, events, grant, trace, operation):
    reference = (count if operation == "initial_count" else recount)._request_reference(trace)
    references = (count._request_reference(trace), recount._request_reference(trace))
    audits = tuple(db.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.request_id.in_(references),
    ).order_by(AuditEvent.stream_version, AuditEvent.id).execution_options(populate_existing=True)).all())
    if not audits:
        return None
    completion = next((row for row in completions if row.scope_id == scope.id), None)
    if completion is None or any(row.request_id != reference for row in audits):
        _invalid_evidence()
    event = events[completion.id]
    if event.id not in {row.id for row in audits} or event.request_id != reference:
        _invalid_evidence()
    if (completion.completed_by_user_id != actor.user_id or completion.completed_by_person_id != actor.person_id):
        _forbidden()
    if completion.authorization_version != actor.authorization_version or completion.completed_role_assignment_id != grant.assignment_id:
        _authorization_changed()
    return StocktakeCountHistoricalCommandOut(
        completion_id=completion.id, round_no=round_row.round_no, completed_at=count._as_utc(completion.completed_at),
        scope_completed=True, caused_round_submission=submission is not None and submission.sealing_completion_id == completion.id,
    )


def _error(suffix, category, message):
    raise StocktakeCountCommandStatusError("stocktake_count_command_status_" + suffix, category, message) from None


def _invalid_input():
    _error("input_invalid", "invalid_request", "盘点历史命令查询坐标无效")


def _invalid_evidence():
    _error("evidence_invalid", "service_unavailable", "盘点历史命令证据不完整或存在歧义，请保持阻塞并重新查询")


def _not_found():
    _error("not_found", "not_found", "日常盘点命令范围不存在")


def _forbidden():
    _error("forbidden", "forbidden", "当前正式权限不允许查询此盘点命令")


def _authorization_changed():
    _error("authorization_changed", "precondition_failed", "原盘点命令的人员或授权版本已变化，请保持恢复阻塞")


def _changed():
    _error("read_changed", "conflict", "盘点证据读取期间发生变化，请保持阻塞并重新查询")
