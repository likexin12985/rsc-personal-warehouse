"""Transaction-bound evidence context for historical count acknowledgements.

This is not a permission or a write-mode switch. Only the count-status reader
creates it after its complete owner graph is locked. Current caller/target
authorization stays with that reader; writers continue using their live guards.
"""
from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..foundation_models import AuditEvent, StateTransitionEvent


_CONTEXT_SEAL = object()


@dataclass(frozen=True, slots=True)
class CountHistoryContext:
    _seal: object
    _db: Session
    _transaction: object
    task_id: uuid.UUID
    operation: str
    target_round_id: uuid.UUID
    target_scope_id: uuid.UUID
    round_ids: frozenset[uuid.UUID]
    scope_ids: frozenset[uuid.UUID]
    plans: tuple
    files: tuple
    creation_audit: object
    release_audits: tuple
    release_approval: object | None

    def require(self, db, task, round_row, scopes=()):
        if (
            self._seal is not _CONTEXT_SEAL or db is not self._db
            or db.get_transaction() is not self._transaction
            or self._transaction is None or not self._transaction.is_active
            or task.id != self.task_id or round_row.task_id != self.task_id
            or round_row.id not in self.round_ids
            or any(row.task_id != self.task_id or row.id not in self.scope_ids for row in scopes)
        ):
            _invalid()

    def files_for(self, db, task, round_row, file_ids):
        self.require(db, task, round_row)
        by_id = {row.id: row for row in self.files}
        if not set(file_ids) <= by_id.keys():
            _invalid()
        return tuple(by_id[value] for value in file_ids)

    def verify_release_audits(self, db, task, proof):
        if db is not self._db or db.get_transaction() is not self._transaction or task.id != self.task_id:
            _invalid()
        from . import stocktake_posting as posting
        from .audit_chain import _verify_audit_event_with_prelocked_proof

        for event in (self.creation_audit, *self.release_audits):
            verified = _verify_audit_event_with_prelocked_proof(
                db, proof=proof, stream_key=posting.INVENTORY_STREAM_KEY, event_id=event.id,
            )
            if verified.after_jsonb != event.after_jsonb:
                _invalid()
        if self.release_approval is not None:
            posting._verify_source_audits(db, task=task, approval=self.release_approval, proof=proof)


def _make_history_context(db, *, graph, target_round, scope_id, operation, plans, files, now):
    """Called only after 0062 owners and a fresh complete graph/file reread."""
    from . import stocktake_count as count

    if (
        db.get_transaction() is None
        or operation not in {"initial_count", "recount_count"}
        or target_round.task_id != graph.task.id
        or scope_id not in {row.id for row in graph.scopes}
        or len(graph.freezes) != len(graph.scopes)
        or {row.stocktake_scope_id for row in graph.freezes} != {row.id for row in graph.scopes}
    ):
        _invalid()
    terminal = graph.task.status in {"posted", "closed"}
    creation_audit = _validate_creation_source(db, graph)
    audits, approval = _validate_release_source(db, graph, now) if terminal else ((), None)
    for freeze in graph.freezes:
        if freeze.task_id != graph.task.id or count._as_utc(freeze.valid_from) > now:
            _invalid()
        if not terminal and (
            freeze.status != "active" or freeze.valid_to is not None
            or freeze.released_by_user_id is not None or freeze.release_reason != ""
        ):
            _invalid()
    if not terminal and (graph.posting_completions or graph.task.posted_at is not None):
        _invalid()
    return CountHistoryContext(
        _seal=_CONTEXT_SEAL, _db=db, _transaction=db.get_transaction(),
        task_id=graph.task.id, operation=operation, target_round_id=target_round.id,
        target_scope_id=scope_id,
        round_ids=frozenset(row.id for row in graph.rounds if row.round_no <= target_round.round_no),
        scope_ids=frozenset(row.id for row in graph.scopes), plans=tuple(plans),
        files=tuple(files), creation_audit=creation_audit,
        release_audits=audits, release_approval=approval,
    )


def _validate_creation_source(db, graph):
    from . import stocktake_count as count

    task = graph.task
    rows = tuple(db.scalars(select(AuditEvent).where(
        AuditEvent.stream_key == count.INVENTORY_STREAM_KEY,
        AuditEvent.action == "stocktake.task.created",
        AuditEvent.aggregate_type == "stocktake_task", AuditEvent.aggregate_id == str(task.id),
    ).execution_options(populate_existing=True)).all())
    expected = {
        "region_org_id": str(task.region_org_id), "scope_count": len(graph.scopes),
        "scope_manifest_sha256": task.scope_manifest_sha256, "status": "draft",
        "task_no": task.task_no, "task_type": task.task_type, "version": 0,
    }
    if (
        len(rows) != 1 or rows[0].actor_user_id != task.created_by_user_id
        or count._as_utc(rows[0].occurred_at) != count._as_utc(task.created_at)
        or rows[0].before_jsonb is not None or rows[0].after_jsonb != expected
    ):
        _invalid()
    return rows[0]


def _validate_release_source(db, graph, now):
    """Prove only the historical freeze interval, not an inventory replay ack.

Reuses the pure effective-scope validators on the real terminal task, never a
fake approved/posted task or an old current time. Posting audit is checked last
against the reader's pinned chain, before either confirmed or not_observed.
"""
    from . import stocktake_count as count
    from . import stocktake_posting as posting

    if len(graph.posting_completions) != 1 or len(graph.effective_approval_completions) != 1:
        _invalid()
    task = graph.task
    completion = graph.posting_completions[0]
    approval = graph.effective_approval_completions[0]
    _validate_effective_release_approval(graph, approval)
    if (
        task.posted_at is None
        or completion.task_id != task.id
        or completion.effective_approval_completion_id != approval.id
        or completion.terminal_round_id != approval.terminal_round_id
        or completion.expected_task_version != approval.approved_task_version
        or completion.posted_task_version != completion.expected_task_version + 1
        or completion.posted_task_version > task.version
        or completion.approval_manifest_sha256 != approval.approval_manifest_sha256
        or completion.scope_count != len(graph.scopes)
        or completion.role_code != "admin" or completion.scope_type != "national"
        or completion.scope_id_snapshot != "*"
        or count._as_utc(completion.posted_at) != count._as_utc(task.posted_at)
        or count._as_utc(completion.created_at) != count._as_utc(completion.posted_at)
        or count._as_utc(completion.posted_at) < count._as_utc(approval.completed_at)
        or count._as_utc(completion.posted_at) > now
        or completion.authorization_sha256 != posting._posting_authorization_document_sha256(
            user_id=completion.posted_by_user_id, person_id=completion.posted_by_person_id,
            assignment_id=completion.posted_role_assignment_id,
            authorization_version=completion.authorization_version, posted_at=completion.posted_at,
        )
        or any(posting._SHA256.fullmatch(value or "") is None for value in (
            completion.request_sha256, completion.idempotency_key_hash, completion.posting_manifest_sha256,
        ))
    ):
        _invalid()
    released_at = count._as_utc(completion.posted_at)
    if any(
        row.status != "released" or row.valid_to is None
        or count._as_utc(row.valid_to) != released_at
        or count._as_utc(row.valid_from) > released_at
        or row.released_by_user_id != completion.posted_by_user_id
        or row.release_reason != posting.FREEZE_RELEASE_REASON
        for row in graph.freezes
    ) or any(count._as_utc(row.completed_at) > released_at for row in graph.scope_completions):
        _invalid()
    states = tuple(db.scalars(select(StateTransitionEvent).where(
        StateTransitionEvent.aggregate_type == "stocktake_task",
        StateTransitionEvent.aggregate_id == str(task.id),
        StateTransitionEvent.to_status == "posted",
    ).execution_options(populate_existing=True)).all())
    audits = tuple(db.scalars(select(AuditEvent).where(
        AuditEvent.stream_key == posting.INVENTORY_STREAM_KEY,
        AuditEvent.action == "stocktake.nonopening.difference_posted",
        AuditEvent.aggregate_type == "stocktake_posting_completion",
        AuditEvent.aggregate_id == str(completion.id),
    ).execution_options(populate_existing=True)).all())
    metadata = posting._posting_event_metadata(completion)
    if (
        len(states) != 1 or len(audits) != 1
        or states[0].from_status != "approved"
        or states[0].reason != "nonopening_stocktake_difference_posted"
        or states[0].idempotency_key != posting._event_key("state", completion.id)
        or states[0].actor_id != completion.posted_by_user_id
        or count._as_utc(states[0].occurred_at) != released_at
        or states[0].metadata_jsonb != metadata
        or audits[0].actor_user_id != completion.posted_by_user_id
        or count._as_utc(audits[0].occurred_at) != released_at
        or audits[0].after_jsonb != metadata
        or audits[0].before_jsonb != {"status": "approved", "task_version": completion.expected_task_version}
    ):
        _invalid()
    return audits, approval


def _validate_effective_release_approval(graph, approval):
    from . import stocktake_posting as posting

    task = graph.task
    scopes = posting._validate_scopes(task, graph.scopes)
    rounds = posting._validate_rounds(task, graph.rounds)
    submissions, completions, differences = posting._validate_round_evidence(
        task, rounds, graph.submissions, graph.difference_completions, graph.differences,
        scope_ids={row.id for row in scopes},
    )
    reviews, items = posting._validate_review_rows(task, rounds, differences, graph.reviews, graph.review_items)
    effective = posting._select_effective_rounds(
        task=task, scopes=scopes, rounds=rounds, recount_cases=graph.recount_cases,
        recount_assignments=graph.recount_assignments, submissions_by_round=submissions,
        completions_by_round=completions, differences_by_round=differences,
        reviews_by_round=reviews, items_by_review=items,
    )
    posting._validate_effective_approval(
        task=task, rounds=rounds, scopes=scopes, completion_by_round=completions,
        differences_by_round=differences, reviews_by_round=reviews, items_by_review=items,
        effective_round_by_scope=effective,
        approval=posting.EffectiveApprovalCompletionEvidence(
            id=approval.id, task_id=approval.task_id, terminal_round_id=approval.terminal_round_id,
            terminal_headquarters_review_id=approval.terminal_headquarters_review_id,
            scope_count=approval.scope_count, difference_count=approval.difference_count,
            approval_manifest_sha256=approval.approval_manifest_sha256,
        ),
        approval_scopes=tuple(posting.EffectiveApprovalScopeEvidence(
            completion_id=row.completion_id, scope_id=row.scope_id, source_round_id=row.source_round_id,
            source_difference_completion_id=row.source_difference_completion_id,
            regional_review_id=row.regional_review_id, difference_count=row.difference_count,
        ) for row in graph.effective_approval_scopes),
        approval_items=tuple(posting.EffectiveApprovalItemEvidence(
            completion_id=row.completion_id, scope_id=row.scope_id, source_round_id=row.source_round_id,
            difference_id=row.difference_id, regional_review_id=row.regional_review_id,
            regional_decision=row.regional_decision, headquarters_decision=row.headquarters_decision,
        ) for row in graph.effective_approval_items),
    )


def _invalid():
    raise ValueError("Historical count context or immutable freeze source is invalid")
