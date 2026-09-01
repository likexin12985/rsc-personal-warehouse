"""Append-only recount causality for reviewed non-opening stocktakes.

One recount case links an immutable submitted source round, its complete
difference seal and the terminal regional/headquarters review to exactly one
contiguous successor round.  Only scopes that contain a terminal review item
requiring recount are assigned.  Existing source rounds, differences and
reviews are never overwritten or marked superseded.

The service creates no stock account, ledger transaction, movement, balance,
posting, notification or outbox row.  The caller owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Final, Mapping, Sequence
import uuid

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    lock_formal_principal_graph,
    load_formal_principal,
)
from ..foundation_models import (
    AuditEvent,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..models import User
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    StocktakePosting,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
)
from . import stocktake_review as review_service
from . import stocktake_task as task_service
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)
from .postgresql_lock_graph import lock_nonopening_stocktake_review_graph


INVENTORY_STREAM_KEY: Final[str] = "inventory"
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class StocktakeRecountError(RuntimeError):
    """Stable database-detail-free error for recount-open commands."""

    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported error category: {category}")
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "category": self.category, "message": self.message}


@dataclass(frozen=True, slots=True)
class StocktakeRecountScopeAssignmentInput:
    scope_id: uuid.UUID
    assignee_user_id: str


@dataclass(frozen=True, slots=True)
class OpenStocktakeRecountCommand:
    task_id: uuid.UUID
    source_round_id: uuid.UUID
    expected_task_version: int
    assignments: tuple[StocktakeRecountScopeAssignmentInput, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class StocktakeRecountResult:
    recount_case_id: uuid.UUID
    task_id: uuid.UUID
    source_round_id: uuid.UUID
    next_round_id: uuid.UUID
    next_round_no: int
    scope_count: int
    assignment_count: int
    resulting_task_status: str
    task_version: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedAssignment:
    scope: FormalStocktakeScope
    actor: FormalPrincipal
    assignment: RoleAssignment
    grant: ScopeGrant


def open_stocktake_recount(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: OpenStocktakeRecountCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeRecountResult:
    """Open the next contiguous recount round without inventory side effects."""

    try:
        return _open_recount(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeRecountError:
        raise
    except review_service.StocktakeReviewError as exc:
        _fail(
            "stocktake_recount_source_evidence_invalid",
            "service_unavailable",
            "复盘来源的初盘、差异或复核证据无法重证",
            cause=exc,
        )
    except AuditChainError as exc:
        _fail(
            "stocktake_recount_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，复盘轮次未打开",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "stocktake_recount_concurrent_conflict",
            "conflict",
            "复盘轮次发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "stocktake_recount_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了复盘轮次，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable stocktake recount boundary")


def _open_recount(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: OpenStocktakeRecountCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeRecountResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    raw_key = _require_idempotency_key(idempotency_key)
    trace_id = _require_trace_request_id(trace_request_id)
    path = (
        f"/api/v1/stocktakes/{checked.task_id}/rounds/"
        f"{checked.source_round_id}/recount"
    )
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    request_hash = _request_hmac(secret, supplied, checked)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-recount-idempotency", key_hash),
            _lock_coordinate("stocktake-recount-task", str(checked.task_id)),
            _lock_coordinate("stocktake-recount-round", str(checked.source_round_id)),
        ),
    )

    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type not in review_service.NON_OPENING_TYPES:
        _fail("stocktake_recount_task_not_found", "not_found", "非期初盘点任务不存在")
    source_round = db.scalar(
        select(StocktakeRound)
        .where(
            StocktakeRound.id == checked.source_round_id,
            StocktakeRound.task_id == task.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source_round is None:
        _fail("stocktake_recount_source_round_not_found", "not_found", "复盘来源轮次不存在")

    assignment_user_ids = tuple(row.assignee_user_id for row in checked.assignments)
    principal_ids = review_service._task_principal_user_ids(
        db,
        task.id,
        supplied_user_ids=(supplied.user_id, *assignment_user_ids),
    )
    lock_formal_principal_graph(db, principal_ids)
    lock_nonopening_stocktake_review_graph(db, task.id, source_round.id)
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    opener_assignment, opener_grant = _authorize_opener(db, current, task, now)
    evidence = review_service._load_and_validate_sealed_difference_evidence(
        db, task=task, round_row=source_round, now=now
    )
    reviews = review_service._load_reviews(db, task.id, source_round.id)
    trigger_review, trigger_items = _validate_terminal_review_graph(
        db, evidence=evidence, reviews=reviews
    )
    needed_scope_ids = _required_recount_scope_ids(
        evidence.differences, trigger_review, trigger_items
    )
    supplied_by_scope = {row.scope_id: row for row in checked.assignments}
    if set(supplied_by_scope) != needed_scope_ids:
        _fail(
            "stocktake_recount_scope_assignments_incomplete",
            "invalid_request",
            "复盘分配必须且只能覆盖触发复盘决定涉及的全部范围",
        )
    scope_by_id = {row.id: row for row in evidence.scopes}
    prepared = tuple(
        _prepare_scope_assignment(
            db,
            scope=scope_by_id[scope_id],
            assignee_user_id=supplied_by_scope[scope_id].assignee_user_id,
            now=now,
        )
        for scope_id in sorted(needed_scope_ids, key=str)
    )
    scope_manifest = _scope_manifest(task.id, source_round.id, prepared)

    existing_cases = tuple(
        db.scalars(
            select(StocktakeRecountCase)
            .where(
                or_(
                    StocktakeRecountCase.idempotency_key_hash == key_hash,
                    StocktakeRecountCase.source_round_id == source_round.id,
                )
            )
            .order_by(StocktakeRecountCase.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if existing_cases:
        if len(existing_cases) != 1:
            _evidence_invalid("复盘因果事实不唯一")
        case = existing_cases[0]
        _validate_replay(
            db,
            task=task,
            source_round=source_round,
            evidence=evidence,
            trigger_review=trigger_review,
            case=case,
            prepared=prepared,
            actor=current,
            command=checked,
            key_hash=key_hash,
            request_hash=request_hash,
            scope_manifest=scope_manifest,
        )
        _head, proof = _lock_audit_chain_head_with_proof(
            db, stream_key=INVENTORY_STREAM_KEY
        )
        review_service._verify_evidence_audit(
            db, evidence.source_audit_event, proof
        )
        for row in reviews:
            review_service._verify_review_audit(db, row, proof)
        _verify_recount_audit(db, case, proof)
        return replace(_result(db, task, case), replayed=True)

    _validate_new_recount_state(
        db,
        task=task,
        source_round=source_round,
        trigger_review=trigger_review,
        command=checked,
        now=now,
    )
    _head, proof = _lock_audit_chain_head_with_proof(
        db, stream_key=INVENTORY_STREAM_KEY
    )
    review_service._verify_evidence_audit(db, evidence.source_audit_event, proof)
    for row in reviews:
        review_service._verify_review_audit(db, row, proof)
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    opener_assignment, opener_grant = _authorize_opener(
        db, current, task, now, lock_rows=False
    )
    prepared = tuple(
        _prepare_scope_assignment(
            db,
            scope=row.scope,
            assignee_user_id=row.actor.user_id,
            now=now,
            lock_rows=False,
        )
        for row in prepared
    )
    _validate_new_recount_state(
        db,
        task=task,
        source_round=source_round,
        trigger_review=trigger_review,
        command=checked,
        now=now,
    )
    return _write_recount(
        db,
        task=task,
        source_round=source_round,
        evidence=evidence,
        trigger_review=trigger_review,
        actor=current,
        opener_assignment=opener_assignment,
        opener_grant=opener_grant,
        prepared=prepared,
        command=checked,
        key_hash=key_hash,
        request_hash=request_hash,
        scope_manifest=scope_manifest,
        trace_request_id=trace_id,
        now=now,
    )


def _write_recount(
    db: Session,
    *,
    task: FormalStocktakeTask,
    source_round: StocktakeRound,
    evidence: review_service.SealedNonOpeningDifferenceEvidence,
    trigger_review: StocktakeReview,
    actor: FormalPrincipal,
    opener_assignment: RoleAssignment,
    opener_grant: ScopeGrant,
    prepared: Sequence[_PreparedAssignment],
    command: OpenStocktakeRecountCommand,
    key_hash: str,
    request_hash: str,
    scope_manifest: str,
    trace_request_id: str,
    now: datetime,
) -> StocktakeRecountResult:
    case_id = uuid.uuid4()
    next_round_id = uuid.uuid4()
    next_round_no = source_round.round_no + 1
    assignment_documents = [
        _assignment_document(case_id, row, now) for row in prepared
    ]
    assignment_manifest = _sha256(
        {
            "assignments": assignment_documents,
            "recount_case_id": str(case_id),
            "schema": "cloud_oam.stocktake.nonopening_recount_assignments.v1",
        }
    )
    recount_manifest = _sha256(
        {
            "assignment_manifest_sha256": assignment_manifest,
            "difference_completion_id": str(evidence.completion.id),
            "difference_manifest_sha256": evidence.completion.difference_manifest_sha256,
            "next_round_no": next_round_no,
            "recount_case_id": str(case_id),
            "schema": "cloud_oam.stocktake.nonopening_recount.v1",
            "scope_manifest_sha256": scope_manifest,
            "source_round_id": str(source_round.id),
            "trigger_review_id": str(trigger_review.id),
        }
    )
    opener_authorization = _authorization_sha256(
        actor=actor,
        assignment=opener_assignment,
        grant=opener_grant,
        occurred_at=now,
        schema="cloud_oam.stocktake.nonopening_recount_opener_authorization.v1",
    )
    case = StocktakeRecountCase(
        id=case_id,
        task_id=task.id,
        source_round_id=source_round.id,
        source_round_submission_id=evidence.submission.id,
        source_difference_completion_id=evidence.completion.id,
        trigger_review_id=trigger_review.id,
        next_round_no=next_round_no,
        scope_count=len(prepared),
        scope_manifest_sha256=scope_manifest,
        assignment_manifest_sha256=assignment_manifest,
        recount_manifest_sha256=recount_manifest,
        request_sha256=request_hash,
        idempotency_key_hash=key_hash,
        reason=command.reason,
        opened_by_user_id=actor.user_id,
        opened_by_person_id=actor.person_id,
        opened_role_assignment_id=opener_assignment.id,
        authorization_version=actor.authorization_version,
        role_code=opener_grant.role_code,
        scope_type=opener_grant.scope_type,
        scope_id_snapshot=opener_grant.scope_id,
        authorization_sha256=opener_authorization,
        opened_at=now,
        created_at=now,
    )
    db.add(case)
    db.flush()
    for row, document in zip(prepared, assignment_documents, strict=True):
        assignment_authorization = _authorization_sha256(
            actor=row.actor,
            assignment=row.assignment,
            grant=row.grant,
            occurred_at=now,
            schema="cloud_oam.stocktake.nonopening_recount_scope_authorization.v1",
        )
        db.add(
            StocktakeRecountScopeAssignment(
                id=uuid.uuid4(),
                recount_case_id=case.id,
                task_id=task.id,
                source_round_id=source_round.id,
                scope_id=row.scope.id,
                assignee_user_id=row.actor.user_id,
                assignee_person_id=row.actor.person_id,
                assignee_role_assignment_id=row.assignment.id,
                authorization_version=row.actor.authorization_version,
                role_code=row.grant.role_code,
                scope_type=row.grant.scope_type,
                scope_id_snapshot=row.grant.scope_id,
                authorization_sha256=assignment_authorization,
                assignment_sha256=_sha256(document),
                assigned_at=now,
                created_at=now,
            )
        )
    db.flush()

    previous_status = task.status
    task.status = "counting"
    task.current_round_no = next_round_no
    task.version += 1
    task.updated_at = now
    metadata = {
        "assignment_manifest_sha256": assignment_manifest,
        "next_round_id": str(next_round_id),
        "next_round_no": next_round_no,
        "recount_case_id": str(case.id),
        "recount_manifest_sha256": recount_manifest,
        "schema": "cloud_oam.stocktake.nonopening_recount_event.v1",
        "scope_count": len(prepared),
        "scope_manifest_sha256": scope_manifest,
        "source_round_id": str(source_round.id),
        "trigger_review_id": str(trigger_review.id),
    }
    db.add(
        StateTransitionEvent(
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            from_status=previous_status,
            to_status="counting",
            reason="nonopening_recount_opened",
            actor_id=actor.user_id,
            idempotency_key=_event_key("state", case.id),
            occurred_at=now,
            metadata_jsonb=metadata,
            created_at=now,
        )
    )
    db.flush()
    next_round = StocktakeRound(
        id=next_round_id,
        task_id=task.id,
        round_no=next_round_no,
        round_type="recount",
        status="counting",
        submitted_by_user_id=None,
        started_at=now,
        submitted_at=None,
        count_manifest_sha256=None,
        idempotency_key_hash=_round_idempotency_hash(case.id),
        recount_case_id=case.id,
        created_at=now,
        updated_at=now,
    )
    db.add(next_round)
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action="stocktake.nonopening.recount_opened",
        aggregate_type="stocktake_recount_case",
        aggregate_id=str(case.id),
        before_jsonb=None,
        after_jsonb={
            **metadata,
            "authorization_version": actor.authorization_version,
            "opened_by_person_id": str(actor.person_id),
            "opened_role_assignment_id": str(opener_assignment.id),
            "opened_role_code": opener_grant.role_code,
        },
        request_id=_request_reference(trace_request_id),
        occurred_at=now,
    )
    db.flush()
    return _result(db, task, case)


def _validate_terminal_review_graph(
    db: Session,
    *,
    evidence: review_service.SealedNonOpeningDifferenceEvidence,
    reviews: Sequence[StocktakeReview],
) -> tuple[StocktakeReview, tuple[StocktakeReviewItem, ...]]:
    region = next(
        (row for row in reviews if row.review_stage == review_service.REGION_STAGE),
        None,
    )
    headquarters = next(
        (
            row
            for row in reviews
            if row.review_stage == review_service.HEADQUARTERS_STAGE
        ),
        None,
    )
    if region is None:
        _evidence_invalid("复盘来源缺少区域复核事实")
    if region.decision == "approve":
        trigger = headquarters
        if trigger is None or trigger.decision == "approve":
            _fail(
                "stocktake_recount_trigger_missing",
                "precondition_failed",
                "当前复核链没有要求复盘或驳回的终态决定",
            )
        if (
            trigger.reviewer_user_id == region.reviewer_user_id
            or trigger.reviewer_person_id == region.reviewer_person_id
            or _as_utc(trigger.reviewed_at) <= _as_utc(region.reviewed_at)
        ):
            _evidence_invalid("区域与总部复核职责分离或时间顺序无效")
    else:
        if headquarters is not None:
            _evidence_invalid("区域非通过后不得存在总部复核事实")
        trigger = region
    if trigger.decision not in {"recount", "reject"}:
        _fail(
            "stocktake_recount_trigger_missing",
            "precondition_failed",
            "终态复核没有要求复盘或驳回",
        )

    for review in reviews:
        items = tuple(
            db.scalars(
                select(StocktakeReviewItem)
                .where(StocktakeReviewItem.review_id == review.id)
                .order_by(StocktakeReviewItem.difference_id)
            ).all()
        )
        command = review_service.SubmitStocktakeReviewCommand(
            task_id=evidence.task.id,
            round_id=evidence.round_row.id,
            expected_task_version=0,
            decision=review.decision,
            comment=review.comment,
            items=tuple(
                review_service.StocktakeReviewItemInput(
                    difference_id=row.difference_id,
                    decision=row.decision,
                    comment=row.comment,
                )
                for row in items
            ),
        )
        decisions = review_service._validate_item_decisions(
            command,
            evidence.differences,
            stage=review.review_stage,
            existing_reviews=reviews,
        )
        manifest = review_service._review_manifest(
            evidence=evidence,
            stage=review.review_stage,
            command=command,
            decisions=decisions,
        )
        _validate_historical_reviewer(db, evidence.task, review)
        if (
            len(items) != len(evidence.differences)
            or review.decision_manifest_sha256 != manifest
            or any(
                row.task_id != evidence.task.id
                or row.round_id != evidence.round_row.id
                or _as_utc(row.created_at) != _as_utc(review.reviewed_at)
                for row in items
            )
        ):
            _evidence_invalid("逐项复核事实或复核摘要无法重证")
        if review.id == trigger.id:
            trigger_items = items
    return trigger, trigger_items


def _validate_historical_reviewer(
    db: Session,
    task: FormalStocktakeTask,
    review: StocktakeReview,
) -> None:
    assignment = db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.id == review.reviewer_role_assignment_id)
        .execution_options(populate_existing=True)
    )
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, review.reviewer_user_id)
    occurred = _as_utc(review.reviewed_at)
    if review.review_stage == review_service.REGION_STAGE:
        expected_role, expected_type, expected_id = (
            "provincial_manager",
            "organization",
            str(task.region_org_id),
        )
    else:
        expected_role, expected_type, expected_id = "admin", "national", "*"
    if (
        assignment is None
        or role is None
        or user is None
        or assignment.user_id != review.reviewer_user_id
        or user.person_id != review.reviewer_person_id
        or user.authorization_version < review.authorization_version
        or assignment.scope_type != expected_type
        or (
            assignment.scope_id != expected_id
            if expected_type == "national"
            else not _same_uuid(assignment.scope_id, expected_id)
        )
        or role.code != expected_role
        or role.is_external
        or _as_utc(assignment.valid_from) > occurred
        or (assignment.valid_to is not None and occurred >= _as_utc(assignment.valid_to))
        or (assignment.revoked_at is not None and occurred >= _as_utc(assignment.revoked_at))
    ):
        _evidence_invalid("历史盘点复核人员授权无法重证")


def _required_recount_scope_ids(
    differences: Sequence[object],
    trigger_review: StocktakeReview,
    trigger_items: Sequence[StocktakeReviewItem],
) -> set[uuid.UUID]:
    difference_by_id = {row.id: row for row in differences}
    if trigger_review.decision == "reject":
        selected = tuple(trigger_items)
    else:
        selected = tuple(
            row
            for row in trigger_items
            if row.decision in review_service.RECOUNT_ITEM_DECISIONS
        )
    scope_ids: set[uuid.UUID] = set()
    for item in selected:
        difference = difference_by_id.get(item.difference_id)
        if difference is None or difference.scope_id is None:
            _evidence_invalid("非期初复盘差异缺少精确范围")
        scope_ids.add(difference.scope_id)
    if not scope_ids:
        _fail(
            "stocktake_recount_scope_missing",
            "precondition_failed",
            "终态复核没有可分配的复盘范围",
        )
    return scope_ids


def _authorize_opener(
    db: Session,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
    now: datetime,
    *,
    lock_rows: bool = True,
) -> tuple[RoleAssignment, ScopeGrant]:
    candidates = tuple(
        row
        for row in actor.assignments
        if (
            row.role_code == "admin"
            and row.scope_type == "national"
            and row.scope_id == "*"
        )
        or (
            row.role_code == "provincial_manager"
            and row.scope_type == "organization"
            and _same_uuid(row.scope_id, task.region_org_id)
        )
    )
    candidates = tuple(
        row
        for row in candidates
        if task_service._grant_allows(
            db,
            actor,
            row,
            "stocktake",
            "manage",
            target_scope_type="organization",
            target_scope_id=str(task.region_org_id),
        )
    )
    if len(candidates) != 1:
        _fail(
            "stocktake_recount_forbidden",
            "forbidden",
            "当前人员没有唯一有效的盘点复盘管理授权",
        )
    grant = candidates[0]
    statement = select(RoleAssignment).where(RoleAssignment.id == grant.assignment_id)
    if lock_rows:
        statement = statement.with_for_update()
    assignment = db.scalar(statement.execution_options(populate_existing=True))
    _validate_current_assignment(assignment, grant, actor, now)
    return assignment, grant


def _prepare_scope_assignment(
    db: Session,
    *,
    scope: FormalStocktakeScope,
    assignee_user_id: str,
    now: datetime,
    lock_rows: bool = True,
) -> _PreparedAssignment:
    try:
        principal = load_formal_principal(db, assignee_user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "stocktake_recount_assignee_not_current",
            "precondition_failed",
            "复盘执行人员没有当前有效的正式身份",
            cause=exc,
        )
    if principal.access_mode != "active":
        _fail(
            "stocktake_recount_assignee_not_current",
            "precondition_failed",
            "复盘执行人员当前不可执行盘点",
        )
    candidates: list[ScopeGrant] = []
    for grant in principal.assignments:
        if (
            grant.role_code == "admin"
            and grant.scope_type == "national"
            and grant.scope_id == "*"
        ):
            target_type, target_id = "national", "*"
        elif (
            grant.role_code == "provincial_manager"
            and grant.scope_type == "organization"
            and _same_uuid(grant.scope_id, scope.owner_org_id)
        ):
            target_type, target_id = "organization", str(scope.owner_org_id)
        elif (
            grant.role_code == "technician"
            and grant.scope_type == "person"
            and scope.custodian_person_id_snapshot is not None
            and principal.person_id == scope.custodian_person_id_snapshot
            and _same_uuid(grant.scope_id, principal.person_id)
        ):
            target_type, target_id = "person", str(principal.person_id)
        else:
            continue
        if task_service._grant_allows(
            db,
            principal,
            grant,
            "stocktake",
            "count",
            target_scope_type=target_type,
            target_scope_id=target_id,
        ):
            candidates.append(grant)
    if len(candidates) != 1:
        _fail(
            "stocktake_recount_assignee_forbidden",
            "precondition_failed",
            "每个复盘范围必须绑定唯一且有实盘权限的当前执行人员",
        )
    grant = candidates[0]
    statement = select(RoleAssignment).where(RoleAssignment.id == grant.assignment_id)
    if lock_rows:
        statement = statement.with_for_update()
    assignment = db.scalar(statement.execution_options(populate_existing=True))
    _validate_current_assignment(assignment, grant, principal, now)
    return _PreparedAssignment(
        scope=scope,
        actor=principal,
        assignment=assignment,
        grant=grant,
    )


def _validate_current_assignment(
    assignment: RoleAssignment | None,
    grant: ScopeGrant,
    actor: FormalPrincipal,
    now: datetime,
) -> None:
    if assignment is None:
        _fail("stocktake_recount_assignment_changed", "precondition_failed", "角色授权不存在")
    assert assignment is not None
    if (
        assignment.user_id != actor.user_id
        or assignment.status != "active"
        or assignment.revoked_at is not None
        or _as_utc(assignment.valid_from) > now
        or (assignment.valid_to is not None and now >= _as_utc(assignment.valid_to))
        or assignment.scope_type != grant.scope_type
        or assignment.scope_id != grant.scope_id
    ):
        _fail(
            "stocktake_recount_assignment_changed",
            "precondition_failed",
            "角色授权已失效或发生变化",
        )


def _validate_new_recount_state(
    db: Session,
    *,
    task: FormalStocktakeTask,
    source_round: StocktakeRound,
    trigger_review: StocktakeReview,
    command: OpenStocktakeRecountCommand,
    now: datetime,
) -> None:
    if task.version != command.expected_task_version:
        _fail("stocktake_recount_version_conflict", "conflict", "盘点任务版本已变化，请重新读取")
    if (
        task.status != "recount_required"
        or source_round.status != "submitted"
        or task.current_round_no != source_round.round_no
        or source_round.submitted_at is None
        or trigger_review.decision not in {"recount", "reject"}
        or now <= _as_utc(trigger_review.reviewed_at)
        or db.scalar(
            select(func.count())
            .select_from(StocktakePosting)
            .where(
                StocktakePosting.task_id == task.id,
                StocktakePosting.round_id == source_round.id,
            )
        )
        or db.scalar(
            select(func.count())
            .select_from(StocktakeRound)
            .where(
                StocktakeRound.task_id == task.id,
                StocktakeRound.round_no == source_round.round_no + 1,
            )
        )
    ):
        _fail(
            "stocktake_recount_state_invalid",
            "precondition_failed",
            "盘点任务当前状态或复核因果不允许打开下一复盘轮次",
        )


def _validate_replay(
    db: Session,
    *,
    task: FormalStocktakeTask,
    source_round: StocktakeRound,
    evidence: review_service.SealedNonOpeningDifferenceEvidence,
    trigger_review: StocktakeReview,
    case: StocktakeRecountCase,
    prepared: Sequence[_PreparedAssignment],
    actor: FormalPrincipal,
    command: OpenStocktakeRecountCommand,
    key_hash: str,
    request_hash: str,
    scope_manifest: str,
) -> None:
    rows = tuple(
        db.scalars(
            select(StocktakeRecountScopeAssignment)
            .where(StocktakeRecountScopeAssignment.recount_case_id == case.id)
            .order_by(StocktakeRecountScopeAssignment.scope_id)
        ).all()
    )
    next_rounds = tuple(
        db.scalars(
            select(StocktakeRound).where(StocktakeRound.recount_case_id == case.id)
        ).all()
    )
    expected_by_scope = {row.scope.id: row for row in prepared}
    stored_by_scope = {row.scope_id: row for row in rows}
    if set(expected_by_scope) != set(stored_by_scope):
        _idempotency_conflict()
    assignment_documents = [
        _assignment_document(case.id, expected_by_scope[row.scope_id], row.assigned_at)
        for row in rows
    ]
    expected_assignment_manifest = _sha256(
        {
            "assignments": assignment_documents,
            "recount_case_id": str(case.id),
            "schema": "cloud_oam.stocktake.nonopening_recount_assignments.v1",
        }
    )
    expected_recount_manifest = _sha256(
        {
            "assignment_manifest_sha256": expected_assignment_manifest,
            "difference_completion_id": str(evidence.completion.id),
            "difference_manifest_sha256": evidence.completion.difference_manifest_sha256,
            "next_round_no": source_round.round_no + 1,
            "recount_case_id": str(case.id),
            "schema": "cloud_oam.stocktake.nonopening_recount.v1",
            "scope_manifest_sha256": scope_manifest,
            "source_round_id": str(source_round.id),
            "trigger_review_id": str(trigger_review.id),
        }
    )
    opener_grant = ScopeGrant(
        assignment_id=case.opened_role_assignment_id,
        role_code=case.role_code,
        scope_type=case.scope_type,
        scope_id=case.scope_id_snapshot,
        valid_from=_as_utc(case.opened_at),
        valid_to=None,
    )
    opener_assignment = db.get(RoleAssignment, case.opened_role_assignment_id)
    valid_rows = True
    for stored, document in zip(rows, assignment_documents, strict=True):
        prepared_row = expected_by_scope[stored.scope_id]
        expected_authorization = _authorization_sha256(
            actor=prepared_row.actor,
            assignment=prepared_row.assignment,
            grant=prepared_row.grant,
            occurred_at=stored.assigned_at,
            schema="cloud_oam.stocktake.nonopening_recount_scope_authorization.v1",
        )
        valid_rows = valid_rows and (
            stored.assignee_user_id == prepared_row.actor.user_id
            and stored.assignee_person_id == prepared_row.actor.person_id
            and stored.assignee_role_assignment_id == prepared_row.assignment.id
            and stored.authorization_version == prepared_row.actor.authorization_version
            and stored.role_code == prepared_row.grant.role_code
            and stored.scope_type == prepared_row.grant.scope_type
            and stored.scope_id_snapshot == prepared_row.grant.scope_id
            and stored.authorization_sha256 == expected_authorization
            and stored.assignment_sha256 == _sha256(document)
            and _as_utc(stored.created_at) == _as_utc(stored.assigned_at)
        )
    expected_opener_authorization = (
        _authorization_sha256(
            actor=actor,
            assignment=opener_assignment,
            grant=opener_grant,
            occurred_at=case.opened_at,
            schema="cloud_oam.stocktake.nonopening_recount_opener_authorization.v1",
        )
        if opener_assignment is not None
        else ""
    )
    state_rows = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.idempotency_key == _event_key("state", case.id),
            )
        ).all()
    )
    if (
        case.idempotency_key_hash != key_hash
        or case.request_sha256 != request_hash
        or case.task_id != task.id
        or case.source_round_id != source_round.id
        or case.source_round_submission_id != evidence.submission.id
        or case.source_difference_completion_id != evidence.completion.id
        or case.trigger_review_id != trigger_review.id
        or case.next_round_no != source_round.round_no + 1
        or case.scope_count != len(prepared)
        or case.scope_manifest_sha256 != scope_manifest
        or case.assignment_manifest_sha256 != expected_assignment_manifest
        or case.recount_manifest_sha256 != expected_recount_manifest
        or case.reason != command.reason
        or case.opened_by_user_id != actor.user_id
        or case.opened_by_person_id != actor.person_id
        or case.authorization_version != actor.authorization_version
        or case.authorization_sha256 != expected_opener_authorization
        or _as_utc(case.created_at) != _as_utc(case.opened_at)
        or not valid_rows
        or len(next_rounds) != 1
        or next_rounds[0].task_id != task.id
        or next_rounds[0].round_no != case.next_round_no
        or next_rounds[0].round_type != "recount"
        or next_rounds[0].status != "counting"
        or next_rounds[0].idempotency_key_hash != _round_idempotency_hash(case.id)
        or task.current_round_no != case.next_round_no
        or task.status != "counting"
        or len(state_rows) != 1
        or state_rows[0].from_status != "recount_required"
        or state_rows[0].to_status != "counting"
    ):
        _idempotency_conflict()


def _assignment_document(
    case_id: uuid.UUID,
    prepared: _PreparedAssignment,
    assigned_at: datetime,
) -> dict[str, object]:
    authorization = _authorization_sha256(
        actor=prepared.actor,
        assignment=prepared.assignment,
        grant=prepared.grant,
        occurred_at=assigned_at,
        schema="cloud_oam.stocktake.nonopening_recount_scope_authorization.v1",
    )
    return {
        "assignee_person_id": str(prepared.actor.person_id),
        "assignee_role_assignment_id": str(prepared.assignment.id),
        "assignee_user_id": prepared.actor.user_id,
        "assigned_at": _timestamp(assigned_at),
        "authorization_sha256": authorization,
        "authorization_version": prepared.actor.authorization_version,
        "recount_case_id": str(case_id),
        "role_code": prepared.grant.role_code,
        "schema": "cloud_oam.stocktake.nonopening_recount_assignment.v1",
        "scope_id": str(prepared.scope.id),
        "scope_id_snapshot": prepared.grant.scope_id,
        "scope_type": prepared.grant.scope_type,
    }


def _scope_manifest(
    task_id: uuid.UUID,
    source_round_id: uuid.UUID,
    prepared: Sequence[_PreparedAssignment],
) -> str:
    return _sha256(
        {
            "schema": "cloud_oam.stocktake.nonopening_recount_scopes.v1",
            "scopes": [
                {
                    "scope_id": str(row.scope.id),
                    "scope_no": row.scope.scope_no,
                    "scope_sha256": row.scope.scope_sha256,
                }
                for row in sorted(prepared, key=lambda value: value.scope.scope_no)
            ],
            "source_round_id": str(source_round_id),
            "task_id": str(task_id),
        }
    )


def _authorization_sha256(
    *,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    occurred_at: datetime,
    schema: str,
) -> str:
    return _sha256(
        {
            "assignment_id": str(assignment.id),
            "authorization_version": actor.authorization_version,
            "occurred_at": _timestamp(occurred_at),
            "person_id": str(actor.person_id),
            "role_code": grant.role_code,
            "schema": schema,
            "scope_id": grant.scope_id,
            "scope_type": grant.scope_type,
            "user_id": actor.user_id,
        }
    )


def _verify_recount_audit(
    db: Session, case: StocktakeRecountCase, proof: object
) -> None:
    events = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action == "stocktake.nonopening.recount_opened",
                AuditEvent.aggregate_type == "stocktake_recount_case",
                AuditEvent.aggregate_id == str(case.id),
            )
        ).all()
    )
    if len(events) != 1:
        _evidence_invalid("复盘开启审计事实缺失或不唯一")
    try:
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=events[0].id,
        )
    except AuditChainError as exc:
        _fail(
            "stocktake_recount_audit_invalid",
            "service_unavailable",
            "复盘开启审计链无法重证",
            cause=exc,
        )
    after = verified.after_jsonb
    if (
        not isinstance(after, dict)
        or after.get("recount_case_id") != str(case.id)
        or after.get("recount_manifest_sha256") != case.recount_manifest_sha256
    ):
        _evidence_invalid("复盘开启审计摘要与因果事实不一致")


def _result(
    db: Session, task: FormalStocktakeTask, case: StocktakeRecountCase
) -> StocktakeRecountResult:
    next_round = db.scalar(
        select(StocktakeRound).where(StocktakeRound.recount_case_id == case.id)
    )
    if next_round is None:
        _evidence_invalid("复盘因果事实缺少唯一后继轮次")
    assignment_count = db.scalar(
        select(func.count())
        .select_from(StocktakeRecountScopeAssignment)
        .where(StocktakeRecountScopeAssignment.recount_case_id == case.id)
    )
    return StocktakeRecountResult(
        recount_case_id=case.id,
        task_id=task.id,
        source_round_id=case.source_round_id,
        next_round_id=next_round.id,
        next_round_no=case.next_round_no,
        scope_count=case.scope_count,
        assignment_count=assignment_count or 0,
        resulting_task_status=task.status,
        task_version=task.version,
    )


def _validate_command(command: OpenStocktakeRecountCommand) -> OpenStocktakeRecountCommand:
    if not isinstance(command, OpenStocktakeRecountCommand):
        _fail("stocktake_recount_command_required", "invalid_request", "复盘命令类型无效")
    task_id = _require_uuid("task_id", command.task_id)
    source_round_id = _require_uuid("source_round_id", command.source_round_id)
    if not isinstance(command.expected_task_version, int) or command.expected_task_version < 0:
        _fail("stocktake_recount_version_invalid", "invalid_request", "任务版本无效")
    if not isinstance(command.assignments, tuple) or not command.assignments:
        _fail("stocktake_recount_assignments_invalid", "invalid_request", "复盘分配不能为空且必须使用不可变元组")
    assignments: list[StocktakeRecountScopeAssignmentInput] = []
    seen: set[uuid.UUID] = set()
    for row in command.assignments:
        if not isinstance(row, StocktakeRecountScopeAssignmentInput):
            _fail("stocktake_recount_assignment_invalid", "invalid_request", "复盘分配明细类型无效")
        scope_id = _require_uuid("scope_id", row.scope_id)
        if scope_id in seen:
            _fail("stocktake_recount_assignment_duplicate", "invalid_request", "同一范围不得重复分配")
        seen.add(scope_id)
        assignments.append(
            StocktakeRecountScopeAssignmentInput(
                scope_id=scope_id,
                assignee_user_id=_require_user_id(row.assignee_user_id),
            )
        )
    reason = _require_text("reason", command.reason, 10000, allow_empty=False)
    return OpenStocktakeRecountCommand(
        task_id=task_id,
        source_round_id=source_round_id,
        expected_task_version=command.expected_task_version,
        assignments=tuple(assignments),
        reason=reason,
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal) or not actor.user_id:
        _fail("stocktake_recount_actor_invalid", "forbidden", "正式操作人上下文无效")
    return actor


def _require_current_actor(
    db: Session, supplied: FormalPrincipal, now: datetime
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail("stocktake_recount_actor_not_current", "forbidden", "正式操作人授权已失效", cause=exc)
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.access_mode != "active"
    ):
        _fail("stocktake_recount_actor_principal_stale", "forbidden", "正式操作人权限版本已变化")
    return current


def _request_hmac(
    secret: bytes,
    actor: FormalPrincipal,
    command: OpenStocktakeRecountCommand,
) -> str:
    return _hmac_hex(
        secret,
        {
            "actor_authorization_version": actor.authorization_version,
            "actor_person_id": str(actor.person_id),
            "actor_user_id": actor.user_id,
            "assignments": [
                {"assignee_user_id": row.assignee_user_id, "scope_id": str(row.scope_id)}
                for row in command.assignments
            ],
            "expected_task_version": command.expected_task_version,
            "reason": command.reason,
            "schema": "cloud_oam.stocktake.nonopening_recount_request.v1",
            "source_round_id": str(command.source_round_id),
            "task_id": str(command.task_id),
        },
    )


def _idempotency_hmac(
    secret: bytes, actor_user_id: str, path: str, raw_key: str
) -> str:
    return _hmac_hex(
        secret,
        {
            "actor_user_id": actor_user_id,
            "idempotency_key": raw_key,
            "path": path,
            "schema": "cloud_oam.stocktake.nonopening_recount_idempotency.v1",
        },
    )


def _round_idempotency_hash(case_id: uuid.UUID) -> str:
    return _sha256(
        {
            "recount_case_id": str(case_id),
            "schema": "cloud_oam.stocktake.nonopening_recount_round.v1",
        }
    )


def _hmac_hex(secret: bytes, document: Mapping[str, object]) -> str:
    return hmac.new(secret, _canonical_bytes(document), hashlib.sha256).hexdigest()


def _sha256(document: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(document)).hexdigest()


def _canonical_bytes(document: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("stocktake_recount_manifest_invalid", "invalid_request", "复盘清单无法规范化", cause=exc)
    raise AssertionError("unreachable canonical document")


def _event_key(kind: str, case_id: uuid.UUID) -> str:
    return f"stocktake-nonopening-recount:{kind}:{case_id}"


def _request_reference(raw: str) -> str:
    return f"stocktake-recount:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _require_uuid(field: str, value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        _fail("stocktake_recount_uuid_invalid", "invalid_request", f"{field} 无效", cause=exc)
    raise AssertionError("unreachable uuid")


def _require_user_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 36:
        _fail("stocktake_recount_user_id_invalid", "invalid_request", "复盘执行账号无效")
    return value.strip()


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or not (8 <= len(value) <= 200) or not _PRINTABLE.fullmatch(value):
        _fail("stocktake_recount_idempotency_key_invalid", "invalid_request", "Idempotency-Key 无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    secret = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(secret, bytes) or len(secret) < 32:
        _fail("stocktake_recount_hmac_secret_invalid", "service_unavailable", "幂等摘要密钥未安全配置")
    return secret


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        _fail("stocktake_recount_trace_id_invalid", "invalid_request", "请求追踪号无效")
    return value.strip()


def _require_text(
    field: str, value: object, limit: int, *, allow_empty: bool
) -> str:
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        _fail("stocktake_recount_text_invalid", "invalid_request", f"{field} 无效")
    return value


def _lock_coordinate(namespace: str, value: str) -> int:
    raw = hashlib.sha256(f"{namespace}\0{value}".encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


def _take_advisory_locks(db: Session, coordinates: Sequence[int]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": coordinate})


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if value is None:
        _fail("stocktake_recount_clock_unavailable", "service_unavailable", "数据库时间不可用")
    return _as_utc(value)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_uuid(left: object, right: object) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _idempotency_conflict() -> None:
    _fail(
        "stocktake_recount_idempotency_conflict",
        "conflict",
        "幂等键或来源轮次已绑定不同复盘请求",
    )


def _evidence_invalid(message: str) -> None:
    _fail("stocktake_recount_evidence_invalid", "service_unavailable", message)


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = StocktakeRecountError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "OpenStocktakeRecountCommand",
    "StocktakeRecountError",
    "StocktakeRecountResult",
    "StocktakeRecountScopeAssignmentInput",
    "open_stocktake_recount",
]
