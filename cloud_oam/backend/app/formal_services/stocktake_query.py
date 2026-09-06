"""Scope-safe, SELECT-only queries for formal non-opening stocktakes.

This service reads only the formal stocktake and immutable inventory evidence
tables.  It never flushes, commits or rolls back, never decrypts identity data,
and never calls an external client.  Full/sample/ad-hoc/personal/termination
tasks are intentionally separate from opening-establishment queries.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Final
import uuid

from pydantic import ValidationError
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from ..formal_access import (
    Entitlement,
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    load_formal_principal,
)
from ..foundation_models import AuditEvent, Organization, Person, StateTransitionEvent
from ..inventory_models import InventoryLedgerHead, StockAccount, StockLocation
from ..models import User
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeCloseCompletion,
    StocktakeCloseReconciliationAccount,
    StocktakeCloseReconciliationCompletion,
    StocktakeCloseReconciliationSerial,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeEffectiveApprovalCompletion,
    StocktakeEffectiveApprovalItem,
    StocktakeEffectiveApprovalScope,
    StocktakeObservationDisposition,
    StocktakePosting,
    StocktakePostingCompletion,
    StocktakePostingCompletionItem,
    StocktakePostingItem,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from ..stocktake_read_schemas import (
    StocktakeCountLineOut,
    StocktakeCloseCompletionOut,
    StocktakeCloseControlOut,
    StocktakeDifferenceCompletionOut,
    StocktakeDifferenceOut,
    StocktakeFreezeFactOut,
    StocktakeLatestCloseReconciliationOut,
    StocktakeObservationDispositionOut,
    StocktakeObservationOut,
    StocktakePostingFactOut,
    StocktakeRecountAssignmentOut,
    StocktakeRecountCauseOut,
    StocktakeReviewFactOut,
    StocktakeReviewItemOut,
    StocktakeRoundOut,
    StocktakeRoundSubmissionOut,
    StocktakeScopeCountCompletionOut,
    StocktakeScopeOut,
    StocktakeSnapshotAccountOut,
    StocktakeStateAxesOut,
    StocktakeTaskDetailOut,
    StocktakeTaskPageOut,
    StocktakeTaskSummaryOut,
)
from .stocktake_task_policy import TASK_TYPES, may_show_book_quantity


_HTTP_STATUS_BY_CATEGORY: Final[dict[str, int]] = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}
_TASK_ACTION_ORDER: Final[tuple[str, ...]] = (
    "start",
    "submit_initial_count",
    "generate_initial_differences",
    "review_region",
    "review_headquarters",
    "open_recount",
    "submit_recount_count",
    "generate_recount_differences",
    "post",
    "reconcile",
    "close",
)
_ZERO: Final[Decimal] = Decimal("0.000")


class StocktakeReadError(RuntimeError):
    """Stable formal read failure without database or authorization details."""

    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported error category: {category}")
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message

    @property
    def status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    @property
    def http_status_code(self) -> int:
        return self.status_code

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class _OrganizationGraph:
    parent_by_id: Mapping[uuid.UUID, uuid.UUID | None]
    status_by_id: Mapping[uuid.UUID, str]

    def descends_from(
        self,
        organization_id: uuid.UUID,
        ancestor_id: uuid.UUID,
        *,
        require_active_path: bool,
    ) -> bool:
        current: uuid.UUID | None = organization_id
        seen: set[uuid.UUID] = set()
        while current is not None:
            if current in seen:
                _fail(
                    "stocktake_read_organization_cycle",
                    "service_unavailable",
                    "盘点组织范围图无效",
                )
            seen.add(current)
            if current not in self.parent_by_id:
                return False
            if require_active_path and self.status_by_id.get(current) != "active":
                return False
            if current == ancestor_id:
                return True
            current = self.parent_by_id[current]
        return False


@dataclass(frozen=True, slots=True)
class _ReadContext:
    principal: FormalPrincipal
    organizations: _OrganizationGraph
    visible_organization_ids: frozenset[uuid.UUID]
    technician_self_visible: bool


@dataclass(frozen=True, slots=True)
class _TaskSnapshot:
    task_id: uuid.UUID
    version: int
    status: str
    current_round_no: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class _TaskGraph:
    task: FormalStocktakeTask
    scopes: tuple[FormalStocktakeScope, ...]
    locations: Mapping[uuid.UUID, StockLocation]
    people: Mapping[uuid.UUID, Person]
    freezes: tuple[InventoryFreeze, ...]
    snapshots: tuple[StocktakeSnapshotLine, ...]
    accounts: Mapping[uuid.UUID, StockAccount]
    rounds: tuple[StocktakeRound, ...]
    scope_completions: tuple[StocktakeScopeCountCompletion, ...]
    submissions: tuple[StocktakeRoundSubmission, ...]
    count_lines: tuple[StocktakeCountLine, ...]
    count_serials: tuple[StocktakeCountSerial, ...]
    observations: tuple[StocktakeCountObservation, ...]
    dispositions: tuple[StocktakeObservationDisposition, ...]
    difference_completions: tuple[StocktakeDifferenceSetCompletion, ...]
    differences: tuple[StocktakeDifference, ...]
    reviews: tuple[StocktakeReview, ...]
    review_items: tuple[StocktakeReviewItem, ...]
    review_audit_events: tuple[AuditEvent, ...]
    state_transition_events: tuple[StateTransitionEvent, ...]
    effective_approval_completions: tuple[
        StocktakeEffectiveApprovalCompletion, ...
    ]
    effective_approval_scopes: tuple[StocktakeEffectiveApprovalScope, ...]
    effective_approval_items: tuple[StocktakeEffectiveApprovalItem, ...]
    recount_cases: tuple[StocktakeRecountCase, ...]
    recount_assignments: tuple[StocktakeRecountScopeAssignment, ...]
    postings: tuple[StocktakePosting, ...]
    posting_items: tuple[StocktakePostingItem, ...]
    posting_completions: tuple[StocktakePostingCompletion, ...]
    posting_completion_items: tuple[StocktakePostingCompletionItem, ...]
    close_reconciliations: tuple[StocktakeCloseReconciliationCompletion, ...]
    close_reconciliation_accounts: tuple[StocktakeCloseReconciliationAccount, ...]
    close_reconciliation_serials: tuple[StocktakeCloseReconciliationSerial, ...]
    close_completions: tuple[StocktakeCloseCompletion, ...]
    current_ledger_cursor: int


def list_stocktake_tasks(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> StocktakeTaskPageOut:
    """Return one deterministic UUID page inside the caller's current scope."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        _fail("stocktake_read_page_limit_invalid", "invalid_request", "盘点分页大小无效")
    if after_id is not None and not isinstance(after_id, uuid.UUID):
        _fail("stocktake_read_cursor_invalid", "invalid_request", "盘点分页游标无效")
    with db.no_autoflush:
        context = _load_read_context(db, actor=actor, now=now)
        statement = (
            select(FormalStocktakeTask)
            .where(
                FormalStocktakeTask.task_type.in_(tuple(sorted(TASK_TYPES))),
                _visible_task_predicate(context),
            )
            .order_by(FormalStocktakeTask.id)
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        )
        if after_id is not None:
            statement = statement.where(FormalStocktakeTask.id >= after_id)
        rows = tuple(db.scalars(statement).all())
        page_rows = rows[:limit]
        next_after_id = rows[limit].id if len(rows) > limit else None
        if not page_rows:
            _ensure_authorization_current(db, context.principal, now=now)
            return StocktakeTaskPageOut(items=(), next_after_id=None)
        snapshots = _task_snapshots(page_rows)
        graphs = _load_task_graphs(db, page_rows)
        items = tuple(
            _task_summary(context, graph, effective_now=_effective_now(now))
            for graph in graphs
        )
        _ensure_tasks_current(db, snapshots)
        _ensure_authorization_current(db, context.principal, now=now)
        return _validate_output(
            StocktakeTaskPageOut,
            {"items": items, "next_after_id": next_after_id},
        )


def stocktake_task_detail(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    now: datetime | None = None,
) -> StocktakeTaskDetailOut:
    """Return a scope-cropped task graph or the same 404 for missing/forbidden."""

    if not isinstance(task_id, uuid.UUID):
        _fail("stocktake_read_task_id_invalid", "invalid_request", "盘点任务标识无效")
    with db.no_autoflush:
        context = _load_read_context(db, actor=actor, now=now)
        task = db.scalar(
            select(FormalStocktakeTask)
            .where(
                FormalStocktakeTask.id == task_id,
                FormalStocktakeTask.task_type.in_(tuple(sorted(TASK_TYPES))),
                _visible_task_predicate(context),
            )
            .execution_options(populate_existing=True)
        )
        if task is None:
            _ensure_authorization_current(db, context.principal, now=now)
            _fail("stocktake_read_not_found", "not_found", "盘点任务不存在")
        snapshots = _task_snapshots((task,))
        graph = _load_task_graphs(db, (task,))[0]
        output = _task_detail(context, graph, effective_now=_effective_now(now))
        _ensure_tasks_current(db, snapshots)
        _ensure_authorization_current(db, context.principal, now=now)
        return output


def _load_read_context(
    db: Session,
    *,
    actor: FormalPrincipal,
    now: datetime | None,
) -> _ReadContext:
    if not isinstance(actor, FormalPrincipal):
        _fail(
            "formal_principal_required",
            "forbidden",
            "盘点查询必须使用正式权限主体",
        )
    try:
        current = load_formal_principal(db, actor.user_id, now=now)
    except FormalAccessError:
        _fail(
            "stocktake_read_actor_not_current",
            "forbidden",
            "正式盘点查询权限上下文已失效",
        )
    if (
        current.person_id != actor.person_id
        or current.authorization_version != actor.authorization_version
    ):
        _fail(
            "stocktake_read_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail(
            "stocktake_read_actor_inactive",
            "forbidden",
            "当前账号或人员不可查询盘点任务",
        )
    person = db.scalar(
        select(Person)
        .where(Person.id == current.person_id)
        .execution_options(populate_existing=True)
    )
    if person is None or person.employment_status != "active":
        _fail(
            "stocktake_read_actor_person_invalid",
            "forbidden",
            "当前人员不可查询盘点任务",
        )
    organizations = tuple(
        db.scalars(
            select(Organization)
            .order_by(Organization.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    graph = _OrganizationGraph(
        parent_by_id={row.id: row.parent_id for row in organizations},
        status_by_id={row.id: row.status for row in organizations},
    )
    manager_ancestors = {
        uuid.UUID(grant.scope_id)
        for grant in current.assignments
        if grant.role_code == "provincial_manager"
        and grant.scope_type == "organization"
        and _is_uuid(grant.scope_id)
    }
    has_admin = any(
        grant.role_code == "admin"
        and grant.scope_type == "national"
        and grant.scope_id == "*"
        for grant in current.assignments
    )
    visible_organizations: set[uuid.UUID] = set()
    for organization in organizations:
        role_scope_covers = has_admin or any(
            graph.descends_from(
                organization.id,
                ancestor,
                require_active_path=True,
            )
            for ancestor in manager_ancestors
        )
        if role_scope_covers and _permission_allowed(
            current,
            graph,
            resource="stocktake",
            action="read",
            target_scope_type="organization",
            target_scope_id=str(organization.id),
            target_organization_id=organization.id,
        ):
            visible_organizations.add(organization.id)
    technician_self_visible = any(
        grant.role_code == "technician"
        and grant.scope_type == "person"
        and _same_uuid(grant.scope_id, current.person_id)
        and _grant_permission_allowed(
            current,
            graph,
            grant,
            resource="stocktake",
            action="read",
            target_scope_type="person",
            target_scope_id=str(current.person_id),
            target_organization_id=person.organization_id,
        )
        for grant in current.assignments
    )
    if not visible_organizations and not technician_self_visible:
        _fail(
            "stocktake_read_forbidden",
            "forbidden",
            "当前账号没有盘点只读权限",
        )
    return _ReadContext(
        principal=current,
        organizations=graph,
        visible_organization_ids=frozenset(visible_organizations),
        technician_self_visible=technician_self_visible,
    )


def _visible_task_predicate(context: _ReadContext):
    predicates = []
    if context.visible_organization_ids:
        predicates.append(
            FormalStocktakeTask.region_org_id.in_(context.visible_organization_ids)
        )
    if context.technician_self_visible:
        initial_assignment = exists(
            select(FormalStocktakeScope.id).where(
                FormalStocktakeScope.task_id == FormalStocktakeTask.id,
                FormalStocktakeScope.assignee_user_id == context.principal.user_id,
                FormalStocktakeTask.current_round_no.in_((0, 1)),
            )
        )
        recount_assignment = exists(
            select(StocktakeRecountScopeAssignment.id)
            .join(
                StocktakeRound,
                StocktakeRound.recount_case_id
                == StocktakeRecountScopeAssignment.recount_case_id,
            )
            .where(
                StocktakeRecountScopeAssignment.task_id == FormalStocktakeTask.id,
                StocktakeRecountScopeAssignment.assignee_user_id
                == context.principal.user_id,
                StocktakeRecountScopeAssignment.assignee_person_id
                == context.principal.person_id,
                StocktakeRound.task_id == FormalStocktakeTask.id,
                StocktakeRound.round_no == FormalStocktakeTask.current_round_no,
            )
        )
        predicates.append(or_(initial_assignment, recount_assignment))
    if not predicates:
        return FormalStocktakeTask.id.is_(None)
    return or_(*predicates)


def _load_task_graphs(
    db: Session,
    tasks: Sequence[FormalStocktakeTask],
) -> tuple[_TaskGraph, ...]:
    task_ids = tuple(row.id for row in tasks)
    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id.in_(task_ids))
            .order_by(
                FormalStocktakeScope.task_id,
                FormalStocktakeScope.scope_no,
                FormalStocktakeScope.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    locations = tuple(
        db.scalars(
            select(StockLocation)
            .join(FormalStocktakeScope, FormalStocktakeScope.location_id == StockLocation.id)
            .where(FormalStocktakeScope.task_id.in_(task_ids))
            .order_by(StockLocation.id)
            .distinct()
            .execution_options(populate_existing=True)
        ).all()
    )
    people = tuple(
        db.scalars(
            select(Person)
            .join(
                FormalStocktakeScope,
                FormalStocktakeScope.custodian_person_id_snapshot == Person.id,
            )
            .where(FormalStocktakeScope.task_id.in_(task_ids))
            .order_by(Person.id)
            .distinct()
            .execution_options(populate_existing=True)
        ).all()
    )
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id.in_(task_ids))
            .order_by(InventoryFreeze.task_id, InventoryFreeze.stocktake_scope_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    snapshot_lines = tuple(
        db.scalars(
            select(StocktakeSnapshotLine)
            .where(StocktakeSnapshotLine.task_id.in_(task_ids))
            .order_by(
                StocktakeSnapshotLine.task_id,
                StocktakeSnapshotLine.scope_id,
                StocktakeSnapshotLine.stock_account_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    accounts = tuple(
        db.scalars(
            select(StockAccount)
            .join(
                StocktakeSnapshotLine,
                StocktakeSnapshotLine.stock_account_id == StockAccount.id,
            )
            .where(StocktakeSnapshotLine.task_id.in_(task_ids))
            .order_by(StockAccount.id)
            .distinct()
            .execution_options(populate_existing=True)
        ).all()
    )
    rounds = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(StocktakeRound.task_id.in_(task_ids))
            .order_by(StocktakeRound.task_id, StocktakeRound.round_no, StocktakeRound.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    scope_completions = tuple(
        db.scalars(
            select(StocktakeScopeCountCompletion)
            .where(StocktakeScopeCountCompletion.task_id.in_(task_ids))
            .order_by(
                StocktakeScopeCountCompletion.task_id,
                StocktakeScopeCountCompletion.round_id,
                StocktakeScopeCountCompletion.scope_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    submissions = tuple(
        db.scalars(
            select(StocktakeRoundSubmission)
            .where(StocktakeRoundSubmission.task_id.in_(task_ids))
            .order_by(StocktakeRoundSubmission.task_id, StocktakeRoundSubmission.round_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(StocktakeCountLine.task_id.in_(task_ids))
            .order_by(
                StocktakeCountLine.task_id,
                StocktakeCountLine.round_id,
                StocktakeCountLine.scope_id,
                StocktakeCountLine.stock_account_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    count_serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .join(
                StocktakeCountLine,
                and_(
                    StocktakeCountLine.id == StocktakeCountSerial.count_line_id,
                    StocktakeCountLine.round_id == StocktakeCountSerial.round_id,
                ),
            )
            .where(StocktakeCountLine.task_id.in_(task_ids))
            .order_by(
                StocktakeCountSerial.round_id,
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(StocktakeCountObservation.task_id.in_(task_ids))
            .order_by(
                StocktakeCountObservation.task_id,
                StocktakeCountObservation.round_id,
                StocktakeCountObservation.scope_id,
                StocktakeCountObservation.observation_no,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    dispositions = tuple(
        db.scalars(
            select(StocktakeObservationDisposition)
            .where(StocktakeObservationDisposition.task_id.in_(task_ids))
            .order_by(
                StocktakeObservationDisposition.task_id,
                StocktakeObservationDisposition.round_id,
                StocktakeObservationDisposition.observation_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    difference_completions = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion)
            .where(StocktakeDifferenceSetCompletion.task_id.in_(task_ids))
            .order_by(
                StocktakeDifferenceSetCompletion.task_id,
                StocktakeDifferenceSetCompletion.round_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    differences = tuple(
        db.scalars(
            select(StocktakeDifference)
            .where(StocktakeDifference.task_id.in_(task_ids))
            .order_by(
                StocktakeDifference.task_id,
                StocktakeDifference.round_id,
                StocktakeDifference.difference_no,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(StocktakeReview.task_id.in_(task_ids))
            .order_by(
                StocktakeReview.task_id,
                StocktakeReview.round_id,
                StocktakeReview.review_stage,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    review_ids = tuple(row.id for row in reviews)
    review_audit_events = tuple(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.stream_key == "inventory",
                AuditEvent.aggregate_type == "stocktake_review",
                AuditEvent.aggregate_id.in_(tuple(str(value) for value in review_ids)),
            )
            .order_by(AuditEvent.aggregate_id, AuditEvent.occurred_at, AuditEvent.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    state_transition_events = tuple(
        db.scalars(
            select(StateTransitionEvent)
            .where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id.in_(
                    tuple(str(value) for value in task_ids)
                ),
            )
            .order_by(
                StateTransitionEvent.aggregate_id,
                StateTransitionEvent.occurred_at,
                StateTransitionEvent.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    review_items = tuple(
        db.scalars(
            select(StocktakeReviewItem)
            .join(StocktakeReview, StocktakeReview.id == StocktakeReviewItem.review_id)
            .where(StocktakeReview.task_id.in_(task_ids))
            .order_by(StocktakeReviewItem.review_id, StocktakeReviewItem.difference_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    effective_approval_completions = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalCompletion)
            .where(StocktakeEffectiveApprovalCompletion.task_id.in_(task_ids))
            .order_by(
                StocktakeEffectiveApprovalCompletion.task_id,
                StocktakeEffectiveApprovalCompletion.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    effective_approval_scopes = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalScope)
            .where(StocktakeEffectiveApprovalScope.task_id.in_(task_ids))
            .order_by(
                StocktakeEffectiveApprovalScope.task_id,
                StocktakeEffectiveApprovalScope.scope_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    effective_approval_items = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalItem)
            .where(StocktakeEffectiveApprovalItem.task_id.in_(task_ids))
            .order_by(
                StocktakeEffectiveApprovalItem.task_id,
                StocktakeEffectiveApprovalItem.scope_id,
                StocktakeEffectiveApprovalItem.difference_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    recount_cases = tuple(
        db.scalars(
            select(StocktakeRecountCase)
            .where(StocktakeRecountCase.task_id.in_(task_ids))
            .order_by(StocktakeRecountCase.task_id, StocktakeRecountCase.next_round_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    recount_assignments = tuple(
        db.scalars(
            select(StocktakeRecountScopeAssignment)
            .where(StocktakeRecountScopeAssignment.task_id.in_(task_ids))
            .order_by(
                StocktakeRecountScopeAssignment.task_id,
                StocktakeRecountScopeAssignment.recount_case_id,
                StocktakeRecountScopeAssignment.scope_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    postings = tuple(
        db.scalars(
            select(StocktakePosting)
            .where(StocktakePosting.task_id.in_(task_ids))
            .order_by(
                StocktakePosting.task_id,
                StocktakePosting.round_id,
                StocktakePosting.posted_at,
                StocktakePosting.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    posting_items = tuple(
        db.scalars(
            select(StocktakePostingItem)
            .join(StocktakePosting, StocktakePosting.id == StocktakePostingItem.posting_id)
            .where(StocktakePosting.task_id.in_(task_ids))
            .order_by(StocktakePostingItem.posting_id, StocktakePostingItem.inventory_movement_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    posting_completions = tuple(
        db.scalars(
            select(StocktakePostingCompletion)
            .where(StocktakePostingCompletion.task_id.in_(task_ids))
            .order_by(
                StocktakePostingCompletion.task_id,
                StocktakePostingCompletion.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    posting_completion_items = tuple(
        db.scalars(
            select(StocktakePostingCompletionItem)
            .where(StocktakePostingCompletionItem.task_id.in_(task_ids))
            .order_by(
                StocktakePostingCompletionItem.task_id,
                StocktakePostingCompletionItem.scope_id,
                StocktakePostingCompletionItem.difference_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    close_reconciliations = tuple(
        db.scalars(
            select(StocktakeCloseReconciliationCompletion)
            .where(StocktakeCloseReconciliationCompletion.task_id.in_(task_ids))
            .order_by(
                StocktakeCloseReconciliationCompletion.task_id,
                StocktakeCloseReconciliationCompletion.reconciliation_no,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    close_reconciliation_accounts = tuple(
        db.scalars(
            select(StocktakeCloseReconciliationAccount)
            .where(StocktakeCloseReconciliationAccount.task_id.in_(task_ids))
            .order_by(
                StocktakeCloseReconciliationAccount.task_id,
                StocktakeCloseReconciliationAccount.completion_id,
                StocktakeCloseReconciliationAccount.stock_account_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    close_reconciliation_serials = tuple(
        db.scalars(
            select(StocktakeCloseReconciliationSerial)
            .where(StocktakeCloseReconciliationSerial.task_id.in_(task_ids))
            .order_by(
                StocktakeCloseReconciliationSerial.task_id,
                StocktakeCloseReconciliationSerial.completion_id,
                StocktakeCloseReconciliationSerial.serial_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    close_completions = tuple(
        db.scalars(
            select(StocktakeCloseCompletion)
            .where(StocktakeCloseCompletion.task_id.in_(task_ids))
            .order_by(StocktakeCloseCompletion.task_id, StocktakeCloseCompletion.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    ledger_heads = tuple(
        db.scalars(
            select(InventoryLedgerHead)
            .where(InventoryLedgerHead.stream_key == "inventory")
            .order_by(InventoryLedgerHead.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if (
        len(ledger_heads) != 1
        or type(ledger_heads[0].next_cursor) is not int
        or ledger_heads[0].next_cursor <= 0
    ):
        _projection_invalid()
    current_ledger_cursor = ledger_heads[0].next_cursor - 1

    by_task = {
        "scopes": _group(scopes, lambda row: row.task_id),
        "freezes": _group(freezes, lambda row: row.task_id),
        "snapshots": _group(snapshot_lines, lambda row: row.task_id),
        "rounds": _group(rounds, lambda row: row.task_id),
        "scope_completions": _group(scope_completions, lambda row: row.task_id),
        "submissions": _group(submissions, lambda row: row.task_id),
        "count_lines": _group(count_lines, lambda row: row.task_id),
        "observations": _group(observations, lambda row: row.task_id),
        "dispositions": _group(dispositions, lambda row: row.task_id),
        "difference_completions": _group(difference_completions, lambda row: row.task_id),
        "differences": _group(differences, lambda row: row.task_id),
        "reviews": _group(reviews, lambda row: row.task_id),
        "state_transition_events": _group(
            state_transition_events, lambda row: row.aggregate_id
        ),
        "effective_approval_completions": _group(
            effective_approval_completions, lambda row: row.task_id
        ),
        "effective_approval_scopes": _group(
            effective_approval_scopes, lambda row: row.task_id
        ),
        "effective_approval_items": _group(
            effective_approval_items, lambda row: row.task_id
        ),
        "recount_cases": _group(recount_cases, lambda row: row.task_id),
        "recount_assignments": _group(recount_assignments, lambda row: row.task_id),
        "postings": _group(postings, lambda row: row.task_id),
        "posting_completions": _group(
            posting_completions, lambda row: row.task_id
        ),
        "posting_completion_items": _group(
            posting_completion_items, lambda row: row.task_id
        ),
        "close_reconciliations": _group(
            close_reconciliations, lambda row: row.task_id
        ),
        "close_reconciliation_accounts": _group(
            close_reconciliation_accounts, lambda row: row.task_id
        ),
        "close_reconciliation_serials": _group(
            close_reconciliation_serials, lambda row: row.task_id
        ),
        "close_completions": _group(close_completions, lambda row: row.task_id),
    }
    count_line_task = {row.id: row.task_id for row in count_lines}
    count_serials_by_task: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(list)
    for row in count_serials:
        task_id = count_line_task.get(row.count_line_id)
        if task_id is None:
            _projection_invalid()
        count_serials_by_task[task_id].append(row)
    review_task = {row.id: row.task_id for row in reviews}
    review_audit_by_task: dict[uuid.UUID, list[AuditEvent]] = defaultdict(list)
    for row in review_audit_events:
        try:
            review_id = uuid.UUID(row.aggregate_id)
        except (TypeError, ValueError):
            _projection_invalid()
        task_id = review_task.get(review_id)
        if task_id is None:
            _projection_invalid()
        review_audit_by_task[task_id].append(row)
    review_items_by_task: dict[uuid.UUID, list[StocktakeReviewItem]] = defaultdict(list)
    for row in review_items:
        task_id = review_task.get(row.review_id)
        if task_id is None:
            _projection_invalid()
        review_items_by_task[task_id].append(row)
    posting_task = {row.id: row.task_id for row in postings}
    posting_items_by_task: dict[uuid.UUID, list[StocktakePostingItem]] = defaultdict(list)
    for row in posting_items:
        task_id = posting_task.get(row.posting_id)
        if task_id is None:
            _projection_invalid()
        posting_items_by_task[task_id].append(row)

    location_map = {row.id: row for row in locations}
    person_map = {row.id: row for row in people}
    account_map = {row.id: row for row in accounts}
    graphs: list[_TaskGraph] = []
    for task in tasks:
        graph = _TaskGraph(
            task=task,
            scopes=by_task["scopes"].get(task.id, ()),
            locations=location_map,
            people=person_map,
            freezes=by_task["freezes"].get(task.id, ()),
            snapshots=by_task["snapshots"].get(task.id, ()),
            accounts=account_map,
            rounds=by_task["rounds"].get(task.id, ()),
            scope_completions=by_task["scope_completions"].get(task.id, ()),
            submissions=by_task["submissions"].get(task.id, ()),
            count_lines=by_task["count_lines"].get(task.id, ()),
            count_serials=tuple(count_serials_by_task.get(task.id, ())),
            observations=by_task["observations"].get(task.id, ()),
            dispositions=by_task["dispositions"].get(task.id, ()),
            difference_completions=by_task["difference_completions"].get(task.id, ()),
            differences=by_task["differences"].get(task.id, ()),
            reviews=by_task["reviews"].get(task.id, ()),
            review_items=tuple(review_items_by_task.get(task.id, ())),
            review_audit_events=tuple(review_audit_by_task.get(task.id, ())),
            state_transition_events=tuple(
                by_task["state_transition_events"].get(str(task.id), ())
            ),
            effective_approval_completions=by_task[
                "effective_approval_completions"
            ].get(task.id, ()),
            effective_approval_scopes=by_task["effective_approval_scopes"].get(
                task.id, ()
            ),
            effective_approval_items=by_task["effective_approval_items"].get(
                task.id, ()
            ),
            recount_cases=by_task["recount_cases"].get(task.id, ()),
            recount_assignments=by_task["recount_assignments"].get(task.id, ()),
            postings=by_task["postings"].get(task.id, ()),
            posting_items=tuple(posting_items_by_task.get(task.id, ())),
            posting_completions=by_task["posting_completions"].get(task.id, ()),
            posting_completion_items=by_task["posting_completion_items"].get(
                task.id, ()
            ),
            close_reconciliations=by_task["close_reconciliations"].get(task.id, ()),
            close_reconciliation_accounts=by_task[
                "close_reconciliation_accounts"
            ].get(task.id, ()),
            close_reconciliation_serials=by_task[
                "close_reconciliation_serials"
            ].get(task.id, ()),
            close_completions=by_task["close_completions"].get(task.id, ()),
            current_ledger_cursor=current_ledger_cursor,
        )
        _validate_graph(graph)
        graphs.append(graph)
    return tuple(graphs)


def _validate_graph(graph: _TaskGraph) -> None:
    task = graph.task
    if task.task_type not in TASK_TYPES or not graph.scopes:
        _projection_invalid()
    scope_ids = {row.id for row in graph.scopes}
    if len(scope_ids) != len(graph.scopes):
        _projection_invalid()
    if any(
        row.task_id != task.id or row.location_id not in graph.locations
        for row in graph.scopes
    ):
        _projection_invalid()
    round_by_id = {row.id: row for row in graph.rounds}
    if len(round_by_id) != len(graph.rounds):
        _projection_invalid()
    if tuple(row.round_no for row in graph.rounds) != tuple(
        range(1, len(graph.rounds) + 1)
    ):
        _projection_invalid()
    if task.current_round_no != len(graph.rounds):
        _projection_invalid()
    if task.current_round_no == 0 and any(
        (
            graph.freezes,
            graph.snapshots,
            graph.scope_completions,
            graph.submissions,
            graph.count_lines,
            graph.observations,
            graph.difference_completions,
            graph.differences,
            graph.reviews,
            graph.effective_approval_completions,
            graph.effective_approval_scopes,
            graph.effective_approval_items,
            graph.recount_cases,
            graph.recount_assignments,
            graph.postings,
            graph.posting_completions,
            graph.posting_completion_items,
            graph.close_reconciliations,
            graph.close_reconciliation_accounts,
            graph.close_reconciliation_serials,
            graph.close_completions,
        )
    ):
        _projection_invalid()
    for row in (*graph.snapshots, *graph.count_lines, *graph.observations):
        if row.scope_id not in scope_ids:
            _projection_invalid()
    for row in graph.snapshots:
        account = graph.accounts.get(row.stock_account_id)
        scope = next((value for value in graph.scopes if value.id == row.scope_id), None)
        if account is None or scope is None or (
            account.owner_org_id != scope.owner_org_id
            or account.location_id != scope.location_id
        ):
            _projection_invalid()
    for row in graph.count_lines:
        if row.round_id not in round_by_id or row.stock_account_id not in graph.accounts:
            _projection_invalid()
    observation_ids = {row.id for row in graph.observations}
    if any(row.observation_id not in observation_ids for row in graph.dispositions):
        _projection_invalid()
    for row in graph.differences:
        if row.round_id not in round_by_id or row.scope_id not in scope_ids:
            # Non-opening stocktakes never have OAM control-unassigned rows.
            _projection_invalid()
    difference_ids = {row.id for row in graph.differences}
    review_ids = {row.id for row in graph.reviews}
    # Non-opening review history is recoverable only when the command's
    # optimistic-concurrency coordinates were persisted as one continuous
    # pair.  NULL legacy rows are deliberately fail-closed; guessing from the
    # current task version would turn a current read into a false historical
    # fact.
    if any(
        type(row.expected_task_version) is not int
        or row.expected_task_version < 0
        or type(row.resulting_task_version) is not int
        or row.resulting_task_version != row.expected_task_version + 1
        for row in graph.reviews
    ):
        _projection_invalid()
    _validate_review_history_evidence(graph)
    if any(
        row.review_id not in review_ids or row.difference_id not in difference_ids
        for row in graph.review_items
    ):
        _projection_invalid()
    _validate_effective_approval_projection(
        graph,
        scope_ids=scope_ids,
        round_by_id=round_by_id,
        difference_ids=difference_ids,
        review_ids=review_ids,
    )
    _validate_posting_completion_projection(
        graph,
        scope_ids=scope_ids,
        round_by_id=round_by_id,
        difference_ids=difference_ids,
    )
    _validate_close_reconciliation_projection(graph, scope_ids=scope_ids)
    if any(row.round_id not in round_by_id for row in graph.postings):
        _projection_invalid()
    if any(
        row.scope_id not in scope_ids for row in graph.recount_assignments
    ):
        _projection_invalid()
    _unique_by(graph.freezes, lambda row: row.stocktake_scope_id)
    _unique_by(graph.submissions, lambda row: row.round_id)
    _unique_by(graph.difference_completions, lambda row: row.round_id)
    _unique_by(graph.reviews, lambda row: (row.round_id, row.review_stage))
    _unique_by(graph.recount_cases, lambda row: row.next_round_no)
    _unique_by(graph.dispositions, lambda row: row.observation_id)


def _validate_review_history_evidence(graph: _TaskGraph) -> None:
    """Reprove persisted non-opening review facts before exposing history.

    The review row is only one projection of a command.  A query must not
    return it as historical truth when the corresponding state transition or
    immutable audit payload is missing or carries a different version pair.
    This is deliberately a payload check only; callers that need the complete
    audit-chain proof use the write/replay services' prelocked verifier.
    """

    if not graph.reviews:
        return
    differences_by_round: dict[uuid.UUID, tuple[StocktakeDifference, ...]] = {}
    for round_row in graph.rounds:
        differences_by_round[round_row.id] = tuple(
            row for row in graph.differences if row.round_id == round_row.id
        )
    completion_by_round = {
        row.round_id: row for row in graph.difference_completions
    }
    review_audits = tuple(graph.review_audit_events)
    state_events = tuple(graph.state_transition_events)
    for review in graph.reviews:
        if review.review_stage not in {"region", "headquarters"}:
            _projection_invalid()
        differences = differences_by_round.get(review.round_id)
        completion = completion_by_round.get(review.round_id)
        if differences is None or completion is None:
            _projection_invalid()
        assert differences is not None and completion is not None
        expected_status = (
            "recount_required"
            if review.decision != "approve"
            else ("hq_review" if review.review_stage == "region" else "approved")
        )
        previous_status = (
            "submitted" if review.review_stage == "region" else "hq_review"
        )
        expected_metadata = {
            "decision": review.decision,
            "decision_manifest_sha256": review.decision_manifest_sha256,
            "difference_completion_id": str(completion.id),
            "difference_manifest_sha256": completion.difference_manifest_sha256,
            "expected_task_version": review.expected_task_version,
            "item_count": len(differences),
            "pending_verification_count": sum(
                row.reason_code == "stocktake_pending_verification"
                for row in differences
            ),
            "review_id": str(review.id),
            "round_id": str(review.round_id),
            "resulting_task_version": review.resulting_task_version,
            "schema": "cloud_oam.stocktake.nonopening_review_event.v2",
            "stage": review.review_stage,
        }
        state_rows = tuple(
            row
            for row in state_events
            if row.idempotency_key
            == f"stocktake-nonopening-review:state:{review.id}"
        )
        if len(state_rows) != 1:
            _projection_invalid()
        state = state_rows[0]
        state_metadata = state.metadata_jsonb
        if (
            state.aggregate_type != "stocktake_task"
            or state.aggregate_id != str(graph.task.id)
            or state.from_status != previous_status
            or state.to_status != expected_status
            or state.reason
            != f"nonopening_{review.review_stage}_review_{review.decision}"
            or state.actor_id != review.reviewer_user_id
            or _aware(state.occurred_at) != _aware(review.reviewed_at)
            or not isinstance(state_metadata, dict)
            or any(state_metadata.get(key) != value for key, value in expected_metadata.items())
        ):
            _projection_invalid()

        audit_rows = tuple(
            row
            for row in review_audits
            if row.aggregate_id == str(review.id)
            and row.action
            == f"stocktake.nonopening.{review.review_stage}_reviewed"
        )
        if len(audit_rows) != 1:
            _projection_invalid()
        audit = audit_rows[0]
        after = audit.after_jsonb
        if (
            audit.stream_key != "inventory"
            or audit.aggregate_type != "stocktake_review"
            or audit.actor_user_id != review.reviewer_user_id
            or audit.before_jsonb is not None
            or _aware(audit.occurred_at) != _aware(review.reviewed_at)
            or not isinstance(after, dict)
            or any(after.get(key) != value for key, value in expected_metadata.items())
            or after.get("authorization_version") != review.authorization_version
            or after.get("reviewer_person_id") != str(review.reviewer_person_id)
            or after.get("reviewer_role_assignment_id")
            != str(review.reviewer_role_assignment_id)
            or after.get("reviewer_user_id") != review.reviewer_user_id
        ):
            _projection_invalid()


def _validate_effective_approval_projection(
    graph: _TaskGraph,
    *,
    scope_ids: set[uuid.UUID],
    round_by_id: Mapping[uuid.UUID, StocktakeRound],
    difference_ids: set[uuid.UUID],
    review_ids: set[uuid.UUID],
) -> None:
    _unique_by(graph.effective_approval_completions, lambda row: row.task_id)
    completion_by_id = {
        row.id: row for row in graph.effective_approval_completions
    }
    if len(completion_by_id) != len(graph.effective_approval_completions):
        _projection_invalid()
    if any(
        row.task_id != graph.task.id
        or row.terminal_round_id not in round_by_id
        or row.terminal_headquarters_review_id not in review_ids
        for row in graph.effective_approval_completions
    ):
        _projection_invalid()
    if any(
        row.completion_id not in completion_by_id
        or row.task_id != graph.task.id
        or row.scope_id not in scope_ids
        or row.source_round_id not in round_by_id
        or row.source_difference_completion_id
        not in {value.id for value in graph.difference_completions}
        or row.regional_review_id not in review_ids
        for row in graph.effective_approval_scopes
    ):
        _projection_invalid()
    scope_bindings = {
        (row.completion_id, row.scope_id): row
        for row in graph.effective_approval_scopes
    }
    if len(scope_bindings) != len(graph.effective_approval_scopes):
        _projection_invalid()
    difference_by_id = {row.id: row for row in graph.differences}
    review_by_id = {row.id: row for row in graph.reviews}
    review_item_by_coordinate = {
        (row.review_id, row.difference_id): row for row in graph.review_items
    }
    if len(review_item_by_coordinate) != len(graph.review_items):
        _projection_invalid()
    for row in graph.effective_approval_items:
        scope = scope_bindings.get((row.completion_id, row.scope_id))
        difference = difference_by_id.get(row.difference_id)
        regional_review = review_by_id.get(row.regional_review_id)
        regional_item = review_item_by_coordinate.get(
            (row.regional_review_id, row.difference_id)
        )
        if (
            row.task_id != graph.task.id
            or row.difference_id not in difference_ids
            or scope is None
            or difference is None
            or difference.scope_id != row.scope_id
            or difference.round_id != row.source_round_id
            or scope.source_round_id != row.source_round_id
            or scope.regional_review_id != row.regional_review_id
            or regional_review is None
            or regional_review.review_stage != "region"
            or regional_review.round_id != row.source_round_id
            or regional_review.decision != "approve"
            or regional_item is None
            or regional_item.decision != row.regional_decision
            or row.regional_decision not in {"accept_for_posting", "no_adjustment"}
            or row.headquarters_decision != row.regional_decision
        ):
            _projection_invalid()
    _unique_by(
        graph.effective_approval_items,
        lambda row: (row.completion_id, row.difference_id),
    )
    for completion in graph.effective_approval_completions:
        selected_scopes = tuple(
            row
            for row in graph.effective_approval_scopes
            if row.completion_id == completion.id
        )
        selected_items = tuple(
            row
            for row in graph.effective_approval_items
            if row.completion_id == completion.id
        )
        terminal_review = review_by_id.get(
            completion.terminal_headquarters_review_id
        )
        if (
            frozenset(row.scope_id for row in selected_scopes)
            != frozenset(scope_ids)
            or len(selected_scopes) != completion.scope_count
            or sum(row.difference_count for row in selected_scopes)
            != completion.difference_count
            or len(selected_items) != completion.difference_count
            or sum(
                row.headquarters_decision == "accept_for_posting"
                for row in selected_items
            )
            != completion.accepted_difference_count
            or sum(
                row.headquarters_decision == "no_adjustment"
                for row in selected_items
            )
            != completion.no_adjustment_count
            or terminal_review is None
            or terminal_review.round_id != completion.terminal_round_id
            or terminal_review.review_stage != "headquarters"
            or terminal_review.decision != "approve"
        ):
            _projection_invalid()
        for selected_scope in selected_scopes:
            completion_row = next(
                (
                    row
                    for row in graph.difference_completions
                    if row.id == selected_scope.source_difference_completion_id
                ),
                None,
            )
            regional_review = review_by_id.get(selected_scope.regional_review_id)
            selected_difference_ids = {
                row.id
                for row in graph.differences
                if row.scope_id == selected_scope.scope_id
                and row.round_id == selected_scope.source_round_id
            }
            selected_item_ids = {
                row.difference_id
                for row in selected_items
                if row.scope_id == selected_scope.scope_id
            }
            if (
                completion_row is None
                or completion_row.task_id != graph.task.id
                or completion_row.round_id != selected_scope.source_round_id
                or regional_review is None
                or regional_review.round_id != selected_scope.source_round_id
                or selected_scope.difference_count != len(selected_difference_ids)
                or selected_item_ids != selected_difference_ids
            ):
                _projection_invalid()


def _validate_posting_completion_projection(
    graph: _TaskGraph,
    *,
    scope_ids: set[uuid.UUID],
    round_by_id: Mapping[uuid.UUID, StocktakeRound],
    difference_ids: set[uuid.UUID],
) -> None:
    _unique_by(graph.posting_completions, lambda row: row.task_id)
    completion_by_id = {row.id: row for row in graph.posting_completions}
    approval_by_id = {
        row.id: row for row in graph.effective_approval_completions
    }
    posting_by_id = {row.id: row for row in graph.postings}
    difference_by_id = {row.id: row for row in graph.differences}
    posting_item_by_difference = {
        row.difference_id: row
        for row in graph.posting_items
        if row.difference_id is not None
    }
    if len(posting_item_by_difference) != sum(
        row.difference_id is not None for row in graph.posting_items
    ):
        _projection_invalid()
    for row in graph.posting_completion_items:
        completion = completion_by_id.get(row.completion_id)
        difference = difference_by_id.get(row.difference_id)
        if (
            completion is None
            or difference is None
            or row.task_id != graph.task.id
            or row.scope_id not in scope_ids
            or row.source_round_id not in round_by_id
            or row.difference_id not in difference_ids
            or difference.scope_id != row.scope_id
            or difference.round_id != row.source_round_id
        ):
            _projection_invalid()
        posting = posting_by_id.get(row.posting_id) if row.posting_id else None
        posting_item = posting_item_by_difference.get(row.difference_id)
        if row.decision == "accept_for_posting":
            if (
                posting is None
                or posting.effective_approval_completion_id
                != completion.effective_approval_completion_id
                or posting.round_id != row.source_round_id
                or posting_item is None
                or posting_item.posting_id != row.posting_id
                or posting_item.inventory_movement_id != row.inventory_movement_id
                or row.inventory_transaction_id != posting.inventory_transaction_id
                or row.posting_kind != posting.posting_kind
            ):
                _projection_invalid()
        elif row.decision == "no_adjustment":
            if any(
                value is not None
                for value in (
                    row.posting_id,
                    row.inventory_transaction_id,
                    row.inventory_movement_id,
                    row.posting_kind,
                )
            ):
                _projection_invalid()
        else:
            _projection_invalid()
    _unique_by(
        graph.posting_completion_items,
        lambda row: (row.completion_id, row.difference_id),
    )
    for completion in graph.posting_completions:
        approval = approval_by_id.get(completion.effective_approval_completion_id)
        items = tuple(
            row
            for row in graph.posting_completion_items
            if row.completion_id == completion.id
        )
        if (
            completion.task_id != graph.task.id
            or completion.terminal_round_id not in round_by_id
            or approval is None
            or approval.task_id != graph.task.id
            or approval.terminal_round_id != completion.terminal_round_id
            or completion.expected_task_version
            != approval.approved_task_version
            or completion.approval_manifest_sha256
            != approval.approval_manifest_sha256
            or completion.scope_count != approval.scope_count
            or completion.difference_count != approval.difference_count
            or len(items) != completion.difference_count
            or sum(row.decision == "accept_for_posting" for row in items)
            != completion.accepted_difference_count
            or sum(row.decision == "no_adjustment" for row in items)
            != completion.no_adjustment_count
        ):
            _projection_invalid()
    if graph.task.status == "approved":
        if len(graph.effective_approval_completions) != 1 or graph.posting_completions:
            _projection_invalid()
    elif graph.task.status in {"posted", "closed"}:
        if len(graph.posting_completions) != 1:
            _projection_invalid()
        completion = graph.posting_completions[0]
        if completion.posted_task_version > graph.task.version:
            _projection_invalid()
    elif graph.effective_approval_completions or graph.posting_completions:
        _projection_invalid()


def _validate_close_reconciliation_projection(
    graph: _TaskGraph,
    *,
    scope_ids: set[uuid.UUID],
) -> None:
    """Validate the append-only non-opening reconcile/close state axis."""

    reconciliations = graph.close_reconciliations
    accounts_by_completion = _group(
        graph.close_reconciliation_accounts, lambda row: row.completion_id
    )
    serials_by_completion = _group(
        graph.close_reconciliation_serials, lambda row: row.completion_id
    )
    completion_ids = {row.id for row in reconciliations}
    if len(completion_ids) != len(reconciliations):
        _projection_invalid()
    if any(
        row.task_id != graph.task.id or row.completion_id not in completion_ids
        for row in (
            *graph.close_reconciliation_accounts,
            *graph.close_reconciliation_serials,
        )
    ):
        _projection_invalid()
    posting = graph.posting_completions[0] if len(graph.posting_completions) == 1 else None
    expected_version = posting.posted_task_version if posting is not None else None
    previous: StocktakeCloseReconciliationCompletion | None = None
    for expected_no, row in enumerate(reconciliations, start=1):
        account_rows = accounts_by_completion.get(row.id, ())
        serial_rows = serials_by_completion.get(row.id, ())
        if (
            posting is None
            or expected_version is None
            or row.task_id != graph.task.id
            or row.posting_completion_id != posting.id
            or row.posting_manifest_sha256 != posting.posting_manifest_sha256
            or row.reconciliation_no != expected_no
            or row.previous_reconciliation_id
            != (previous.id if previous is not None else None)
            or row.expected_task_version != expected_version
            or row.reconciled_task_version != expected_version + 1
            or row.scope_count != len(scope_ids)
            or row.account_count != len(account_rows)
            or row.scoped_account_count
            != sum(value.scope_id is not None for value in account_rows)
            or row.serial_count != len(serial_rows)
            or row.reconciliation_ledger_cursor > graph.current_ledger_cursor
            or (
                previous is not None
                and row.reconciliation_ledger_cursor
                < previous.reconciliation_ledger_cursor
            )
            or len({value.stock_account_id for value in account_rows})
            != len(account_rows)
            or len({value.serial_id for value in serial_rows}) != len(serial_rows)
            or any(
                value.scope_id is not None and value.scope_id not in scope_ids
                for value in account_rows
            )
            or any(
                value.evidence_scope_id is not None
                and value.evidence_scope_id not in scope_ids
                for value in serial_rows
            )
        ):
            _projection_invalid()
        previous = row
        expected_version = row.reconciled_task_version

    if graph.task.status not in {"posted", "closed"}:
        if reconciliations or graph.close_completions:
            _projection_invalid()
        return
    if posting is None:
        _projection_invalid()
    if graph.task.status == "posted":
        if graph.close_completions:
            _projection_invalid()
        expected_task_version = (
            reconciliations[-1].reconciled_task_version
            if reconciliations
            else posting.posted_task_version
        )
        if graph.task.version != expected_task_version or graph.task.closed_at is not None:
            _projection_invalid()
        return

    if len(graph.close_completions) != 1 or not reconciliations:
        _projection_invalid()
    close = graph.close_completions[0]
    latest = reconciliations[-1]
    if (
        graph.task.closed_at is None
        or close.task_id != graph.task.id
        or close.posting_completion_id != posting.id
        or close.reconciliation_completion_id != latest.id
        or close.reconciliation_no != latest.reconciliation_no
        or close.reconciliation_ledger_cursor != latest.reconciliation_ledger_cursor
        or close.posting_manifest_sha256 != posting.posting_manifest_sha256
        or close.reconciliation_manifest_sha256
        != latest.reconciliation_manifest_sha256
        or close.expected_task_version != latest.reconciled_task_version
        or close.closed_task_version != close.expected_task_version + 1
        or graph.task.version != close.closed_task_version
        or _aware(graph.task.closed_at) != _aware(close.closed_at)
    ):
        _projection_invalid()


def _task_summary(
    context: _ReadContext,
    graph: _TaskGraph,
    *,
    effective_now: datetime,
) -> StocktakeTaskSummaryOut:
    visible_scope_ids = _visible_scope_ids(context, graph)
    if not visible_scope_ids:
        _projection_invalid()
    current_round = _current_round(graph)
    completions = tuple(
        row
        for row in graph.scope_completions
        if current_round is not None
        and row.round_id == current_round.id
        and row.scope_id in visible_scope_ids
    )
    actions = _task_actions(context, graph, visible_scope_ids, effective_now)
    return _validate_output(
        StocktakeTaskSummaryOut,
        {
            "task_id": graph.task.id,
            "task_no": graph.task.task_no,
            "task_type": graph.task.task_type,
            "region_org_id": graph.task.region_org_id,
            "status": graph.task.status,
            "version": graph.task.version,
            "blind_count": graph.task.blind_count,
            "current_round_no": graph.task.current_round_no,
            "current_round_status": current_round.status if current_round else None,
            "cutoff_ledger_cursor": graph.task.cutoff_ledger_cursor,
            "cutoff_at": _aware_or_none(graph.task.cutoff_at),
            "visible_scope_count": len(visible_scope_ids),
            "current_round_visible_completed_scope_count": len(completions),
            "freeze_status": _freeze_status(graph, visible_scope_ids),
            "state_axes": _state_axes(context, graph, visible_scope_ids),
            "deadline": _aware_or_none(graph.task.deadline),
            "allowed_actions": actions,
        },
    )


def _close_control_output(graph: _TaskGraph) -> StocktakeCloseControlOut:
    latest = graph.close_reconciliations[-1] if graph.close_reconciliations else None
    close = graph.close_completions[0] if graph.close_completions else None
    latest_output = (
        _validate_output(
            StocktakeLatestCloseReconciliationOut,
            {
                "completion_id": latest.id,
                "reconciliation_no": latest.reconciliation_no,
                "reconciliation_ledger_cursor": latest.reconciliation_ledger_cursor,
                "reconciled_task_version": latest.reconciled_task_version,
                "reconciled_at": _aware(latest.reconciled_at),
            },
        )
        if latest is not None
        else None
    )
    close_output = (
        _validate_output(
            StocktakeCloseCompletionOut,
            {
                "completion_id": close.id,
                "reconciliation_completion_id": close.reconciliation_completion_id,
                "closed_task_version": close.closed_task_version,
                "closed_at": _aware(close.closed_at),
            },
        )
        if close is not None
        else None
    )
    return _validate_output(
        StocktakeCloseControlOut,
        {
            "latest_reconciliation": latest_output,
            "close_completion": close_output,
        },
    )


def _task_detail(
    context: _ReadContext,
    graph: _TaskGraph,
    *,
    effective_now: datetime,
) -> StocktakeTaskDetailOut:
    visible_scope_ids = _visible_scope_ids(context, graph)
    if not visible_scope_ids:
        _projection_invalid()
    task_actions = _task_actions(context, graph, visible_scope_ids, effective_now)
    book_visible = _book_evidence_visible(graph)
    scope_outputs = tuple(
        _scope_output(
            context,
            graph,
            scope,
            visible_scope_ids,
            book_visible=book_visible,
            effective_now=effective_now,
        )
        for scope in graph.scopes
        if scope.id in visible_scope_ids
    )
    round_outputs = tuple(
        _round_output(
            context,
            graph,
            round_row,
            visible_scope_ids,
            book_visible=book_visible,
            effective_now=effective_now,
        )
        for round_row in graph.rounds
    )
    return _validate_output(
        StocktakeTaskDetailOut,
        {
            "task_id": graph.task.id,
            "task_no": graph.task.task_no,
            "task_type": graph.task.task_type,
            "region_org_id": graph.task.region_org_id,
            "status": graph.task.status,
            "version": graph.task.version,
            "blind_count": graph.task.blind_count,
            "current_round_no": graph.task.current_round_no,
            "cutoff_ledger_cursor": graph.task.cutoff_ledger_cursor,
            "cutoff_at": _aware_or_none(graph.task.cutoff_at),
            "issued_at": _aware_or_none(graph.task.issued_at),
            "frozen_at": _aware_or_none(graph.task.frozen_at),
            "submitted_at": _aware_or_none(graph.task.submitted_at),
            "posted_at": _aware_or_none(graph.task.posted_at),
            "closed_at": _aware_or_none(graph.task.closed_at),
            "cancelled_at": _aware_or_none(graph.task.cancelled_at),
            "deadline": _aware_or_none(graph.task.deadline),
            "note": graph.task.note,
            "state_axes": _state_axes(context, graph, visible_scope_ids),
            "close_control": _close_control_output(graph),
            "scopes": scope_outputs,
            "rounds": round_outputs,
            "allowed_actions": task_actions,
        },
    )


def _scope_output(
    context: _ReadContext,
    graph: _TaskGraph,
    scope: FormalStocktakeScope,
    visible_scope_ids: frozenset[uuid.UUID],
    *,
    book_visible: bool,
    effective_now: datetime,
) -> StocktakeScopeOut:
    freeze = next(
        (row for row in graph.freezes if row.stocktake_scope_id == scope.id),
        None,
    )
    if graph.task.current_round_no == 0:
        snapshot_visibility = "not_started"
        snapshot_outputs = ()
    elif book_visible:
        snapshot_visibility = "visible"
        snapshot_outputs = tuple(
            _snapshot_output(graph, row)
            for row in graph.snapshots
            if row.scope_id == scope.id
        )
    else:
        snapshot_visibility = "hidden"
        snapshot_outputs = ()
    scope_actions: tuple[str, ...] = ()
    if _can_submit_initial_count(
        context,
        graph,
        scope,
        effective_now=effective_now,
    ):
        scope_actions = ("submit_initial_count",)
    elif _can_submit_recount_count(
        context,
        graph,
        scope,
        effective_now=effective_now,
    ):
        scope_actions = ("submit_recount_count",)
    return _validate_output(
        StocktakeScopeOut,
        {
            "scope_id": scope.id,
            "scope_no": scope.scope_no,
            "scope_mode": scope.scope_mode,
            "owner_org_id": scope.owner_org_id,
            "location_id": scope.location_id,
            "custodian_person_id_snapshot": scope.custodian_person_id_snapshot,
            "material_id": scope.material_id,
            "condition_code": scope.condition_code,
            "availability_bucket": scope.availability_bucket,
            "assigned_to_me": scope.id
            in _current_actor_assignment_scope_ids(context, graph),
            "freeze": _freeze_output(freeze) if freeze is not None else None,
            "snapshot_visibility": snapshot_visibility,
            "snapshot_accounts": snapshot_outputs,
            "allowed_actions": scope_actions,
        },
    )


def _snapshot_output(
    graph: _TaskGraph,
    row: StocktakeSnapshotLine,
) -> StocktakeSnapshotAccountOut:
    account = graph.accounts.get(row.stock_account_id)
    if account is None:
        _projection_invalid()
    expected_serial_ids = _expected_serial_ids(row)
    return _validate_output(
        StocktakeSnapshotAccountOut,
        {
            "stock_account_id": account.id,
            "material_id": account.material_id,
            "condition_code": account.condition_code,
            "availability_bucket": account.availability_bucket,
            "lot_id": account.lot_id,
            "book_qty": row.book_qty,
            "expected_serial_ids": expected_serial_ids,
        },
    )


def _round_output(
    context: _ReadContext,
    graph: _TaskGraph,
    round_row: StocktakeRound,
    visible_scope_ids: frozenset[uuid.UUID],
    *,
    book_visible: bool,
    effective_now: datetime,
) -> StocktakeRoundOut:
    covers_all = visible_scope_ids == frozenset(row.id for row in graph.scopes)
    completions = tuple(
        row
        for row in graph.scope_completions
        if row.round_id == round_row.id and row.scope_id in visible_scope_ids
    )
    count_lines = tuple(
        row
        for row in graph.count_lines
        if row.round_id == round_row.id and row.scope_id in visible_scope_ids
    )
    observations = tuple(
        row
        for row in graph.observations
        if row.round_id == round_row.id and row.scope_id in visible_scope_ids
    )
    submission = next(
        (row for row in graph.submissions if row.round_id == round_row.id),
        None,
    )
    differences_visible = book_visible
    visible_differences = tuple(
        row
        for row in graph.differences
        if differences_visible
        and row.round_id == round_row.id
        and row.scope_id in visible_scope_ids
    )
    difference_completion = next(
        (
            row
            for row in graph.difference_completions
            if row.round_id == round_row.id
        ),
        None,
    )
    region_review = next(
        (
            row
            for row in graph.reviews
            if row.round_id == round_row.id and row.review_stage == "region"
        ),
        None,
    )
    headquarters_review = next(
        (
            row
            for row in graph.reviews
            if row.round_id == round_row.id
            and row.review_stage == "headquarters"
        ),
        None,
    )
    recount_case = next(
        (
            row
            for row in graph.recount_cases
            if round_row.recount_case_id is not None
            and row.id == round_row.recount_case_id
        ),
        None,
    )
    postings = tuple(
        row for row in graph.postings if row.round_id == round_row.id
    )
    visible_postings, _visible_posting_items = _visible_posting_rows(
        graph,
        postings,
        visible_scope_ids,
        covers_all=covers_all,
    )
    if visible_postings and any(
        row.verification_status == "pending_verification"
        for row in graph.observations
        if row.round_id == round_row.id and row.scope_id in visible_scope_ids
    ):
        _fail(
            "stocktake_read_pending_observation_posted",
            "service_unavailable",
            "待核验实盘不得显示为已过账",
        )
    round_action_set: set[str] = set()
    if _can_generate_initial_differences(context, graph, round_row):
        round_action_set.add("generate_initial_differences")
    if _can_review_round(context, graph, round_row, stage="region"):
        round_action_set.add("review_region")
    if _can_review_round(context, graph, round_row, stage="headquarters"):
        round_action_set.add("review_headquarters")
    if _can_open_recount(context, graph, round_row):
        round_action_set.add("open_recount")
    if _can_generate_recount_differences(context, graph, round_row):
        round_action_set.add("generate_recount_differences")
    round_actions = tuple(
        value for value in _TASK_ACTION_ORDER if value in round_action_set
    )
    return _validate_output(
        StocktakeRoundOut,
        {
            "round_id": round_row.id,
            "round_no": round_row.round_no,
            "round_type": round_row.round_type,
            "status": round_row.status,
            "started_at": _aware(round_row.started_at),
            "submitted_at": _aware_or_none(round_row.submitted_at),
            "submission": _submission_output(completions, submission, covers_all)
            if submission is not None
            else None,
            "visible_scope_completions": tuple(
                _scope_completion_output(row) for row in completions
            ),
            "visible_count_lines": tuple(
                _count_line_output(
                    context,
                    graph,
                    row,
                    book_visible=book_visible,
                )
                for row in count_lines
            ),
            "visible_observations": tuple(
                _observation_output(context, graph, row) for row in observations
            ),
            "differences_visible": differences_visible,
            "difference_completion": (
                _difference_completion_output(
                    graph,
                    difference_completion,
                    visible_differences,
                    covers_all,
                )
                if differences_visible and difference_completion is not None
                else None
            ),
            "visible_differences": tuple(
                _difference_output(graph, row) for row in visible_differences
            ),
            "region_review": (
                _review_output(graph, region_review, visible_differences, covers_all)
                if differences_visible and region_review is not None
                else None
            ),
            "headquarters_review": (
                _review_output(
                    graph,
                    headquarters_review,
                    visible_differences,
                    covers_all,
                )
                if differences_visible and headquarters_review is not None
                else None
            ),
            "recount_cause": (
                _recount_output(
                    context,
                    graph,
                    recount_case,
                    visible_scope_ids,
                    details_visible=book_visible,
                    covers_all=covers_all,
                )
                if recount_case is not None
                else None
            ),
            "posting": _posting_output(
                graph,
                postings,
                visible_scope_ids,
                covers_all=covers_all,
            ),
            "allowed_actions": round_actions,
        },
    )


def _freeze_output(row: InventoryFreeze) -> StocktakeFreezeFactOut:
    return _validate_output(
        StocktakeFreezeFactOut,
        {
            "freeze_id": row.id,
            "freeze_mode": row.freeze_mode,
            "status": row.status,
            "valid_from": _aware(row.valid_from),
            "valid_to": _aware_or_none(row.valid_to),
            "version": row.version,
        },
    )


def _scope_completion_output(
    row: StocktakeScopeCountCompletion,
) -> StocktakeScopeCountCompletionOut:
    return _validate_output(
        StocktakeScopeCountCompletionOut,
        {
            "completion_id": row.id,
            "scope_id": row.scope_id,
            "count_ledger_cursor": row.count_ledger_cursor,
            "count_line_count": row.count_line_count,
            "observation_line_count": row.observation_line_count,
            "serial_count": row.serial_count,
            "total_counted_qty": row.total_counted_qty,
            "zero_confirmed": row.zero_confirmed,
            "completed_by_person_id": row.completed_by_person_id,
            "completed_at": _aware(row.completed_at),
        },
    )


def _submission_output(
    completions: Sequence[StocktakeScopeCountCompletion],
    submission: StocktakeRoundSubmission,
    covers_all: bool,
) -> StocktakeRoundSubmissionOut:
    visible_total = _sum_quantity(row.total_counted_qty for row in completions)
    if covers_all and (
        submission.scope_count != len(completions)
        or submission.zero_scope_count != sum(row.zero_confirmed for row in completions)
        or submission.count_line_count != sum(row.count_line_count for row in completions)
        or submission.observation_line_count
        != sum(row.observation_line_count for row in completions)
        or submission.serial_count != sum(row.serial_count for row in completions)
        or Decimal(submission.total_counted_qty) != visible_total
    ):
        _projection_invalid()
    return _validate_output(
        StocktakeRoundSubmissionOut,
        {
            "submission_id": submission.id,
            "submitted_at": _aware(submission.submitted_at),
            "visible_scope_count": len(completions),
            "visible_zero_scope_count": sum(row.zero_confirmed for row in completions),
            "visible_count_line_count": sum(row.count_line_count for row in completions),
            "visible_observation_line_count": sum(
                row.observation_line_count for row in completions
            ),
            "visible_serial_count": sum(row.serial_count for row in completions),
            "visible_total_counted_qty": visible_total,
            "covers_all_task_scopes": covers_all,
        },
    )


def _count_line_output(
    context: _ReadContext,
    graph: _TaskGraph,
    row: StocktakeCountLine,
    *,
    book_visible: bool,
) -> StocktakeCountLineOut:
    account = graph.accounts.get(row.stock_account_id)
    snapshot = next(
        (
            value
            for value in graph.snapshots
            if value.scope_id == row.scope_id
            and value.stock_account_id == row.stock_account_id
        ),
        None,
    )
    if account is None or snapshot is None:
        _projection_invalid()
    serial_ids = tuple(
        value.serial_id
        for value in graph.count_serials
        if value.count_line_id == row.id and value.round_id == row.round_id
    )
    return _validate_output(
        StocktakeCountLineOut,
        {
            "count_line_id": row.id,
            "scope_id": row.scope_id,
            "stock_account_id": row.stock_account_id,
            "material_id": account.material_id,
            "counted_qty": row.counted_qty,
            "count_method": row.count_method,
            "reason_code": row.reason_code,
            "remark": row.remark,
            "counted_by_me": row.counted_by_user_id == context.principal.user_id,
            "counted_at": _aware(row.counted_at),
            "counted_serial_ids": serial_ids,
            "book_qty": snapshot.book_qty if book_visible else None,
            "expected_serial_ids": _expected_serial_ids(snapshot)
            if book_visible
            else None,
        },
    )


def _observation_output(
    context: _ReadContext,
    graph: _TaskGraph,
    row: StocktakeCountObservation,
) -> StocktakeObservationOut:
    disposition = next(
        (value for value in graph.dispositions if value.observation_id == row.id),
        None,
    )
    return _validate_output(
        StocktakeObservationOut,
        {
            "observation_id": row.id,
            "scope_id": row.scope_id,
            "observation_no": row.observation_no,
            "owner_org_id": row.owner_org_id,
            "location_id": row.location_id,
            "custodian_person_id_snapshot": row.custodian_person_id_snapshot,
            "material_id": row.material_id,
            "material_identifier_raw": row.material_identifier_raw,
            "material_identifier_type": row.material_identifier_type,
            "condition_code": row.condition_code,
            "availability_bucket": row.availability_bucket,
            "lot_id": row.lot_id,
            "lot_no_raw": row.lot_no_raw,
            "serial_id": row.serial_id,
            "serial_no_raw": row.serial_no_raw,
            "serial_identifier_type": row.serial_identifier_type,
            "counted_qty": row.counted_qty,
            "verification_status": row.verification_status,
            "requires_verification": row.verification_status
            == "pending_verification",
            "count_method": row.count_method,
            "reason_code": row.reason_code,
            "remark": row.remark,
            "counted_by_me": row.counted_by_user_id == context.principal.user_id,
            "counted_at": _aware(row.counted_at),
            "disposition": _disposition_output(disposition)
            if disposition is not None
            else None,
        },
    )


def _disposition_output(
    row: StocktakeObservationDisposition,
) -> StocktakeObservationDispositionOut:
    return _validate_output(
        StocktakeObservationDispositionOut,
        {
            "disposition_id": row.id,
            "disposition": row.disposition,
            "resolved_material_id": row.resolved_material_id,
            "resolved_lot_id": row.resolved_lot_id,
            "resolved_serial_id": row.resolved_serial_id,
            "reason_code": row.reason_code,
            "comment": row.comment,
            "decided_at": _aware(row.decided_at),
        },
    )


def _difference_output(
    graph: _TaskGraph,
    row: StocktakeDifference,
) -> StocktakeDifferenceOut:
    observation = next(
        (
            value
            for value in graph.observations
            if row.observed_line_id is not None and value.id == row.observed_line_id
        ),
        None,
    )
    blocked = bool(
        row.reason_code == "stocktake_pending_verification"
        or (
            observation is not None
            and observation.verification_status == "pending_verification"
        )
    )
    return _validate_output(
        StocktakeDifferenceOut,
        {
            "difference_id": row.id,
            "scope_id": row.scope_id,
            "difference_no": row.difference_no,
            "difference_type": row.difference_type,
            "material_id": row.material_id,
            "expected_account_id": row.expected_account_id,
            "observed_account_id": row.observed_account_id,
            "observed_line_id": row.observed_line_id,
            "serial_id": row.serial_id,
            "book_qty": row.book_qty,
            "counted_qty": row.counted_qty,
            "difference_qty": row.difference_qty,
            "affected_qty": row.affected_qty,
            "reason_code": row.reason_code,
            "reason_text": row.reason_text,
            "evidence_required": row.evidence_required,
            "posting_blocked_by_pending_verification": blocked,
        },
    )


def _difference_completion_output(
    graph: _TaskGraph,
    completion: StocktakeDifferenceSetCompletion,
    visible_differences: Sequence[StocktakeDifference],
    covers_all: bool,
) -> StocktakeDifferenceCompletionOut:
    pending_count = sum(
        1
        for row in visible_differences
        if _difference_is_pending(graph, row)
    )
    total = _sum_quantity(row.affected_qty for row in visible_differences)
    if covers_all and (
        completion.difference_count != len(visible_differences)
        or completion.physical_difference_count != len(visible_differences)
        or completion.control_difference_count != 0
        or completion.pending_observation_difference_count != pending_count
        or Decimal(completion.total_affected_qty) != total
    ):
        _projection_invalid()
    return _validate_output(
        StocktakeDifferenceCompletionOut,
        {
            "completion_id": completion.id,
            "completed_at": _aware(completion.completed_at),
            "visible_difference_count": len(visible_differences),
            "visible_pending_verification_count": pending_count,
            "visible_total_affected_qty": total,
            "covers_all_task_scopes": covers_all,
        },
    )


def _difference_is_pending(graph: _TaskGraph, row: StocktakeDifference) -> bool:
    if row.reason_code == "stocktake_pending_verification":
        return True
    return any(
        observation.id == row.observed_line_id
        and observation.verification_status == "pending_verification"
        for observation in graph.observations
    )


def _review_output(
    graph: _TaskGraph,
    review: StocktakeReview,
    visible_differences: Sequence[StocktakeDifference],
    covers_all: bool,
) -> StocktakeReviewFactOut:
    visible_ids = {row.id for row in visible_differences}
    items = tuple(
        row
        for row in graph.review_items
        if row.review_id == review.id and row.difference_id in visible_ids
    )
    return _validate_output(
        StocktakeReviewFactOut,
        {
            "review_id": review.id,
            "review_stage": review.review_stage,
            "decision": review.decision,
            "expected_task_version": review.expected_task_version,
            "resulting_task_version": review.resulting_task_version,
            "comment": review.comment if covers_all else None,
            "comment_visible": covers_all,
            "reviewer_person_id": review.reviewer_person_id,
            "reviewed_at": _aware(review.reviewed_at),
            "visible_items": tuple(
                _validate_output(
                    StocktakeReviewItemOut,
                    {
                        "difference_id": row.difference_id,
                        "decision": row.decision,
                        "comment": row.comment,
                    },
                )
                for row in items
            ),
            "covers_all_task_scopes": covers_all,
        },
    )


def _recount_output(
    context: _ReadContext,
    graph: _TaskGraph,
    recount_case: StocktakeRecountCase,
    visible_scope_ids: frozenset[uuid.UUID],
    *,
    details_visible: bool,
    covers_all: bool,
) -> StocktakeRecountCauseOut:
    assignments = tuple(
        row
        for row in graph.recount_assignments
        if row.recount_case_id == recount_case.id and row.scope_id in visible_scope_ids
    )
    if covers_all and recount_case.scope_count != len(assignments):
        _projection_invalid()
    reason_visible = details_visible and covers_all
    return _validate_output(
        StocktakeRecountCauseOut,
        {
            "recount_case_id": recount_case.id,
            "source_round_id": recount_case.source_round_id,
            "source_difference_completion_id": recount_case.source_difference_completion_id,
            "trigger_review_id": recount_case.trigger_review_id,
            "next_round_no": recount_case.next_round_no,
            "visible_scope_count": len(assignments),
            "covers_all_task_scopes": covers_all,
            "reason": recount_case.reason if reason_visible else None,
            "reason_visible": reason_visible,
            "opened_by_person_id": recount_case.opened_by_person_id,
            "opened_at": _aware(recount_case.opened_at),
            "assignments": tuple(
                _validate_output(
                    StocktakeRecountAssignmentOut,
                    {
                        "assignment_id": row.id,
                        "scope_id": row.scope_id,
                        "assignee_person_id": row.assignee_person_id,
                        "assigned_to_me": row.assignee_user_id
                        == context.principal.user_id,
                        "assigned_at": _aware(row.assigned_at),
                    },
                )
                for row in assignments
            ),
        },
    )


def _posting_output(
    graph: _TaskGraph,
    postings: Sequence[StocktakePosting],
    visible_scope_ids: frozenset[uuid.UUID],
    *,
    covers_all: bool,
) -> StocktakePostingFactOut:
    visible_postings, items = _visible_posting_rows(
        graph,
        postings,
        visible_scope_ids,
        covers_all=covers_all,
    )
    if not visible_postings:
        return _validate_output(
            StocktakePostingFactOut,
            {
                "status": "not_posted",
                "posting_ids": (),
                "posting_fact_count": 0,
                "visible_total_quantity": _ZERO,
                "covers_all_task_scopes": covers_all,
                "inventory_transaction_count": 0,
                "first_posted_at": None,
                "last_posted_at": None,
            },
        )
    posting_ids = frozenset(row.id for row in visible_postings)
    total = _sum_quantity(row.quantity for row in items)
    if covers_all and _sum_quantity(
        row.total_quantity for row in visible_postings
    ) != total:
        _projection_invalid()
    posted_times = tuple(sorted(_aware(row.posted_at) for row in visible_postings))
    return _validate_output(
        StocktakePostingFactOut,
        {
            "status": "recorded",
            "posting_ids": tuple(sorted(posting_ids, key=str)),
            "posting_fact_count": len(visible_postings),
            "visible_total_quantity": total,
            "covers_all_task_scopes": covers_all,
            "inventory_transaction_count": sum(
                row.inventory_transaction_id is not None for row in visible_postings
            ),
            "first_posted_at": posted_times[0],
            "last_posted_at": posted_times[-1],
        },
    )


def _visible_posting_rows(
    graph: _TaskGraph,
    postings: Sequence[StocktakePosting],
    visible_scope_ids: frozenset[uuid.UUID],
    *,
    covers_all: bool,
) -> tuple[tuple[StocktakePosting, ...], tuple[StocktakePostingItem, ...]]:
    """Crop posting facts before exposing identifiers or transaction counts."""

    posting_ids = frozenset(row.id for row in postings)
    difference_scope = {row.id: row.scope_id for row in graph.differences}
    count_scope = {row.id: row.scope_id for row in graph.count_lines}
    all_items = tuple(
        row for row in graph.posting_items if row.posting_id in posting_ids
    )
    items = tuple(
        row
        for row in all_items
        if (
            row.difference_id is not None
            and difference_scope.get(row.difference_id) in visible_scope_ids
        )
        or (
            row.count_line_id is not None
            and count_scope.get(row.count_line_id) in visible_scope_ids
        )
    )
    if covers_all:
        return tuple(postings), items
    visible_posting_ids = frozenset(row.posting_id for row in items)
    return (
        tuple(row for row in postings if row.id in visible_posting_ids),
        items,
    )


def _state_axes(
    context: _ReadContext,
    graph: _TaskGraph,
    visible_scope_ids: frozenset[uuid.UUID],
) -> StocktakeStateAxesOut:
    del context
    current = _current_round(graph)
    if current is None:
        count_status = "not_started"
    elif current.status == "counting":
        count_status = "counting"
    else:
        count_status = "submitted"
    difference_completion = next(
        (
            row
            for row in graph.difference_completions
            if current is not None and row.round_id == current.id
        ),
        None,
    )
    if current is None or current.status == "counting":
        difference_status = (
            "hidden_for_blind_counter"
            if current is not None and graph.task.blind_count
            else "not_ready"
        )
    elif graph.task.blind_count and not _book_evidence_visible(graph):
        difference_status = "hidden_for_blind_counter"
    elif difference_completion is None:
        difference_status = "not_evaluated"
    else:
        difference_status = "evaluated"
    region = next(
        (
            row
            for row in graph.reviews
            if current is not None
            and row.round_id == current.id
            and row.review_stage == "region"
        ),
        None,
    )
    headquarters = next(
        (
            row
            for row in graph.reviews
            if current is not None
            and row.round_id == current.id
            and row.review_stage == "headquarters"
        ),
        None,
    )
    region_status = (
        region.decision
        if region is not None
        else "pending"
        if difference_completion is not None
        else "not_ready"
    )
    headquarters_status = (
        headquarters.decision
        if headquarters is not None
        else "pending"
        if region is not None and region.decision == "approve"
        else "not_ready"
    )
    if current is not None and current.round_type == "recount":
        recount_status = "counting" if current.status == "counting" else "submitted"
    elif graph.task.status == "recount_required" or any(
        row.decision == "recount" for row in graph.reviews
    ):
        recount_status = "required"
    else:
        recount_status = "not_required"
    current_postings = tuple(
        row
        for row in graph.postings
        if current is not None and row.round_id == current.id
    )
    visible_postings, _posting_items = _visible_posting_rows(
        graph,
        current_postings,
        visible_scope_ids,
        covers_all=visible_scope_ids == frozenset(row.id for row in graph.scopes),
    )
    posting_status = (
        "recorded"
        if graph.task.status in {"posted", "closed"}
        and len(graph.posting_completions) == 1
        else "not_posted"
    )
    latest_reconciliation = (
        graph.close_reconciliations[-1] if graph.close_reconciliations else None
    )
    if latest_reconciliation is None:
        reconciliation_status = "not_reconciled"
    elif graph.task.status == "posted" and (
        latest_reconciliation.reconciled_task_version != graph.task.version
        or latest_reconciliation.reconciliation_ledger_cursor
        != graph.current_ledger_cursor
    ):
        reconciliation_status = "stale"
    else:
        reconciliation_status = "recorded"
    closure_status = "closed" if graph.close_completions else "open"
    return _validate_output(
        StocktakeStateAxesOut,
        {
            "count_status": count_status,
            "difference_status": difference_status,
            "region_review_status": region_status,
            "headquarters_review_status": headquarters_status,
            "recount_status": recount_status,
            "posting_status": posting_status,
            "reconciliation_status": reconciliation_status,
            "closure_status": closure_status,
        },
    )


def _task_actions(
    context: _ReadContext,
    graph: _TaskGraph,
    visible_scope_ids: frozenset[uuid.UUID],
    effective_now: datetime,
) -> tuple[str, ...]:
    actions: set[str] = set()
    # Managed starts validate every assignee's live authorization in the write
    # service.  The read path conservatively advertises start only for the
    # self-contained personal task whose sole assignee is this live principal.
    if (
        graph.task.task_type == "personal"
        and graph.task.created_by_user_id == context.principal.user_id
        and graph.task.status == "draft"
        and graph.task.current_round_no == 0
        and graph.task.cutoff_at is None
        and graph.task.cutoff_ledger_cursor is None
        and (graph.task.deadline is None or _aware(graph.task.deadline) > effective_now)
        and len(graph.scopes) == 1
        and graph.scopes[0].assignee_user_id == context.principal.user_id
        and _can_count_scope_permission(context, graph, graph.scopes[0])
    ):
        actions.add("start")
    if any(
        scope.id in visible_scope_ids
        and _can_submit_initial_count(
            context,
            graph,
            scope,
            effective_now=effective_now,
        )
        for scope in graph.scopes
    ):
        actions.add("submit_initial_count")
    current = _current_round(graph)
    if current is not None and _can_generate_initial_differences(
        context, graph, current
    ):
        actions.add("generate_initial_differences")
    if current is not None and _can_review_round(
        context, graph, current, stage="region"
    ):
        actions.add("review_region")
    if current is not None and _can_review_round(
        context, graph, current, stage="headquarters"
    ):
        actions.add("review_headquarters")
    if current is not None and _can_open_recount(context, graph, current):
        actions.add("open_recount")
    if any(
        scope.id in visible_scope_ids
        and _can_submit_recount_count(
            context,
            graph,
            scope,
            effective_now=effective_now,
        )
        for scope in graph.scopes
    ):
        actions.add("submit_recount_count")
    if current is not None and _can_generate_recount_differences(
        context, graph, current
    ):
        actions.add("generate_recount_differences")
    if _can_post_differences(context, graph, visible_scope_ids):
        actions.add("post")
    if _can_reconcile_for_close(context, graph, visible_scope_ids):
        actions.add("reconcile")
    if _can_close_reconciled(context, graph, visible_scope_ids):
        actions.add("close")
    return tuple(value for value in _TASK_ACTION_ORDER if value in actions)


def _can_reconcile_for_close(
    context: _ReadContext,
    graph: _TaskGraph,
    visible_scope_ids: frozenset[uuid.UUID],
) -> bool:
    return bool(
        graph.task.status == "posted"
        and graph.task.posted_at is not None
        and graph.task.closed_at is None
        and len(graph.posting_completions) == 1
        and not graph.close_completions
        and visible_scope_ids == frozenset(row.id for row in graph.scopes)
        and _can_exact_hq_terminal_action(context, action="reconcile")
    )


def _can_close_reconciled(
    context: _ReadContext,
    graph: _TaskGraph,
    visible_scope_ids: frozenset[uuid.UUID],
) -> bool:
    latest = graph.close_reconciliations[-1] if graph.close_reconciliations else None
    return bool(
        graph.task.status == "posted"
        and graph.task.posted_at is not None
        and graph.task.closed_at is None
        and len(graph.posting_completions) == 1
        and not graph.close_completions
        and latest is not None
        and latest.reconciled_task_version == graph.task.version
        and latest.reconciliation_ledger_cursor == graph.current_ledger_cursor
        and visible_scope_ids == frozenset(row.id for row in graph.scopes)
        and _can_exact_hq_terminal_action(context, action="close")
    )


def _can_exact_hq_terminal_action(
    context: _ReadContext,
    *,
    action: str,
) -> bool:
    candidates = tuple(
        grant
        for grant in context.principal.assignments
        if grant.role_code == "admin"
        and grant.scope_type == "national"
        and grant.scope_id == "*"
        and _grant_permission_allowed(
            context.principal,
            context.organizations,
            grant,
            resource="stocktake",
            action=action,
            target_scope_type="national",
            target_scope_id="*",
            target_organization_id=None,
        )
    )
    return len(candidates) == 1


def _can_post_differences(
    context: _ReadContext,
    graph: _TaskGraph,
    visible_scope_ids: frozenset[uuid.UUID],
) -> bool:
    """Conservatively advertise the independent HQ posting command."""

    current = _current_round(graph)
    if (
        graph.task.status != "approved"
        or graph.task.submitted_at is None
        or graph.task.cutoff_at is None
        or graph.task.cutoff_ledger_cursor is None
        or graph.task.posted_at is not None
        or graph.task.closed_at is not None
        or current is None
        or current.status != "submitted"
        or visible_scope_ids != frozenset(row.id for row in graph.scopes)
        or len(graph.effective_approval_completions) != 1
        or graph.posting_completions
        or graph.postings
        or any(_difference_is_pending(graph, row) for row in graph.differences)
        or len(graph.freezes) != len(graph.scopes)
        or frozenset(row.stocktake_scope_id for row in graph.freezes)
        != frozenset(row.id for row in graph.scopes)
        or any(
            row.task_id != graph.task.id
            or row.status != "active"
            or row.valid_to is not None
            for row in graph.freezes
        )
    ):
        return False
    approval = graph.effective_approval_completions[0]
    if (
        approval.task_id != graph.task.id
        or approval.terminal_round_id != current.id
        or approval.approved_task_version != graph.task.version
    ):
        return False
    candidates = tuple(
        grant
        for grant in context.principal.assignments
        if grant.role_code == "admin"
        and grant.scope_type == "national"
        and grant.scope_id == "*"
        and _grant_permission_allowed(
            context.principal,
            context.organizations,
            grant,
            resource="stocktake",
            action="post_difference",
            target_scope_type="national",
            target_scope_id="*",
            target_organization_id=None,
        )
    )
    return len(candidates) == 1


def _can_submit_initial_count(
    context: _ReadContext,
    graph: _TaskGraph,
    scope: FormalStocktakeScope,
    *,
    effective_now: datetime,
) -> bool:
    current = _current_round(graph)
    if (
        current is None
        or graph.task.status != "counting"
        or graph.task.current_round_no != 1
        or current.round_no != 1
        or current.round_type != "initial"
        or current.status != "counting"
        or current.submitted_at is not None
        or scope.assignee_user_id != context.principal.user_id
        or (graph.task.deadline is not None and _aware(graph.task.deadline) <= effective_now)
        or any(
            row.round_id == current.id and row.scope_id == scope.id
            for row in graph.scope_completions
        )
        or any(
            row.round_id == current.id and row.scope_id == scope.id
            for row in (*graph.count_lines, *graph.observations)
        )
    ):
        return False
    freeze = next(
        (row for row in graph.freezes if row.stocktake_scope_id == scope.id),
        None,
    )
    if freeze is None or freeze.status != "active" or freeze.valid_to is not None:
        return False
    return _can_count_scope_permission(context, graph, scope)


def _can_count_scope_permission(
    context: _ReadContext,
    graph: _TaskGraph,
    scope: FormalStocktakeScope,
) -> bool:
    location = graph.locations.get(scope.location_id)
    if location is None:
        return False
    if location.location_type == "personal":
        if scope.custodian_person_id_snapshot is None:
            return False
        person = graph.people.get(scope.custodian_person_id_snapshot)
        if person is None:
            return False
        target_type = "person"
        target_id = str(scope.custodian_person_id_snapshot)
        target_org = person.organization_id
    else:
        target_type = "organization"
        target_id = str(scope.owner_org_id)
        target_org = scope.owner_org_id
    return any(
        _grant_permission_allowed(
            context.principal,
            context.organizations,
            grant,
            resource="stocktake",
            action="count",
            target_scope_type=target_type,
            target_scope_id=target_id,
            target_organization_id=target_org,
        )
        for grant in context.principal.assignments
    )


def _can_generate_initial_differences(
    context: _ReadContext,
    graph: _TaskGraph,
    round_row: StocktakeRound,
) -> bool:
    current_completions = tuple(
        row for row in graph.scope_completions if row.round_id == round_row.id
    )
    if (
        graph.task.status != "submitted"
        or graph.task.current_round_no != 1
        or round_row.round_no != 1
        or round_row.round_type != "initial"
        or round_row.status != "submitted"
        or round_row.submitted_at is None
        or graph.task.cutoff_ledger_cursor is None
        or len(current_completions) != len(graph.scopes)
        or any(
            type(row.count_ledger_cursor) is not int
            or row.count_ledger_cursor < graph.task.cutoff_ledger_cursor
            for row in current_completions
        )
        or len([row for row in graph.submissions if row.round_id == round_row.id]) != 1
        or any(row.round_id == round_row.id for row in graph.difference_completions)
        or any(row.round_id == round_row.id for row in graph.differences)
        or any(row.round_id == round_row.id for row in graph.reviews)
        or any(row.round_id == round_row.id for row in graph.postings)
    ):
        return False
    return _can_manage_exact_region(context, graph.task.region_org_id)


def _can_submit_recount_count(
    context: _ReadContext,
    graph: _TaskGraph,
    scope: FormalStocktakeScope,
    *,
    effective_now: datetime,
) -> bool:
    current = _current_round(graph)
    if (
        current is None
        or graph.task.status != "counting"
        or graph.task.current_round_no <= 1
        or current.round_no != graph.task.current_round_no
        or current.round_type != "recount"
        or current.status != "counting"
        or current.recount_case_id is None
        or current.submitted_at is not None
        or (graph.task.deadline is not None and _aware(graph.task.deadline) <= effective_now)
        or any(
            row.round_id == current.id and row.scope_id == scope.id
            for row in graph.scope_completions
        )
        or any(
            row.round_id == current.id and row.scope_id == scope.id
            for row in (*graph.count_lines, *graph.observations)
        )
    ):
        return False
    assignments = tuple(
        row
        for row in graph.recount_assignments
        if row.recount_case_id == current.recount_case_id and row.scope_id == scope.id
    )
    if len(assignments) != 1:
        return False
    assignment = assignments[0]
    if (
        assignment.assignee_user_id != context.principal.user_id
        or assignment.assignee_person_id != context.principal.person_id
        or context.principal.authorization_version < assignment.authorization_version
    ):
        return False
    freeze = next(
        (row for row in graph.freezes if row.stocktake_scope_id == scope.id),
        None,
    )
    if (
        freeze is None
        or freeze.status != "active"
        or freeze.valid_to is not None
        or _aware(freeze.valid_from) > effective_now
    ):
        return False
    grants = tuple(
        row
        for row in context.principal.assignments
        if row.assignment_id == assignment.assignee_role_assignment_id
        and row.role_code == assignment.role_code
        and row.scope_type == assignment.scope_type
        and row.scope_id == assignment.scope_id_snapshot
    )
    if len(grants) != 1:
        return False
    grant = grants[0]
    if grant.role_code == "technician":
        if (
            scope.custodian_person_id_snapshot is None
            or not _same_uuid(
                grant.scope_id,
                scope.custodian_person_id_snapshot,
            )
        ):
            return False
        target_scope_type = "person"
        target_scope_id = str(scope.custodian_person_id_snapshot)
        person = graph.people.get(scope.custodian_person_id_snapshot)
        target_organization_id = person.organization_id if person is not None else None
    elif grant.role_code == "provincial_manager":
        if not _same_uuid(grant.scope_id, scope.owner_org_id):
            return False
        target_scope_type = "organization"
        target_scope_id = str(scope.owner_org_id)
        target_organization_id = scope.owner_org_id
    elif grant.role_code == "admin" and grant.scope_type == "national" and grant.scope_id == "*":
        target_scope_type = "organization"
        target_scope_id = str(scope.owner_org_id)
        target_organization_id = scope.owner_org_id
    else:
        return False
    if graph.task.task_type == "personal" and grant.role_code != "technician":
        return False
    return _grant_permission_allowed(
        context.principal,
        context.organizations,
        grant,
        resource="stocktake",
        action="count",
        target_scope_type=target_scope_type,
        target_scope_id=target_scope_id,
        target_organization_id=target_organization_id,
    )


def _can_generate_recount_differences(
    context: _ReadContext,
    graph: _TaskGraph,
    round_row: StocktakeRound,
) -> bool:
    if round_row.recount_case_id is None:
        return False
    selected_scope_ids = frozenset(
        row.scope_id
        for row in graph.recount_assignments
        if row.recount_case_id == round_row.recount_case_id
    )
    completions = tuple(
        row for row in graph.scope_completions if row.round_id == round_row.id
    )
    if (
        graph.task.status != "submitted"
        or graph.task.current_round_no != round_row.round_no
        or round_row.round_no <= 1
        or round_row.round_type != "recount"
        or round_row.status != "submitted"
        or round_row.submitted_at is None
        or graph.task.submitted_at is None
        or graph.task.cutoff_ledger_cursor is None
        or not selected_scope_ids
        or frozenset(row.scope_id for row in completions) != selected_scope_ids
        or len(completions) != len(selected_scope_ids)
        or any(
            type(row.count_ledger_cursor) is not int
            or row.count_ledger_cursor < graph.task.cutoff_ledger_cursor
            for row in completions
        )
        or len([row for row in graph.submissions if row.round_id == round_row.id]) != 1
        or any(row.round_id == round_row.id for row in graph.difference_completions)
        or any(row.round_id == round_row.id for row in graph.differences)
        or any(row.round_id == round_row.id for row in graph.reviews)
        or any(row.round_id == round_row.id for row in graph.postings)
    ):
        return False
    return _can_manage_exact_region(context, graph.task.region_org_id)


def _can_review_round(
    context: _ReadContext,
    graph: _TaskGraph,
    round_row: StocktakeRound,
    *,
    stage: str,
) -> bool:
    if (
        graph.task.current_round_no != round_row.round_no
        or round_row.status != "submitted"
        or round_row.submitted_at is None
        or len(
            [row for row in graph.difference_completions if row.round_id == round_row.id]
        )
        != 1
        or any(row.round_id == round_row.id for row in graph.postings)
    ):
        return False
    reviews = tuple(row for row in graph.reviews if row.round_id == round_row.id)
    if stage == "region":
        if graph.task.status != "region_review" or reviews:
            return False
        role_code, scope_type, scope_id = (
            "provincial_manager",
            "organization",
            str(graph.task.region_org_id),
        )
        action = "review_region"
        target_scope_type = "organization"
        target_scope_id = str(graph.task.region_org_id)
        target_organization_id = graph.task.region_org_id
    elif stage == "headquarters":
        region = next(
            (row for row in reviews if row.review_stage == "region"),
            None,
        )
        if (
            graph.task.status != "hq_review"
            or len(reviews) != 1
            or region is None
            or region.decision != "approve"
            or region.reviewer_user_id == context.principal.user_id
            or region.reviewer_person_id == context.principal.person_id
        ):
            return False
        role_code, scope_type, scope_id = "admin", "national", "*"
        action = "review_headquarters"
        target_scope_type = "national"
        target_scope_id = "*"
        target_organization_id = None
    else:
        return False
    candidates = tuple(
        grant
        for grant in context.principal.assignments
        if grant.role_code == role_code
        and grant.scope_type == scope_type
        and (
            grant.scope_id == scope_id
            if scope_type == "national"
            else _same_uuid(grant.scope_id, scope_id)
        )
        and _grant_permission_allowed(
            context.principal,
            context.organizations,
            grant,
            resource="stocktake",
            action=action,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
            target_organization_id=target_organization_id,
        )
    )
    return len(candidates) == 1


def _can_open_recount(
    context: _ReadContext,
    graph: _TaskGraph,
    round_row: StocktakeRound,
) -> bool:
    reviews = tuple(row for row in graph.reviews if row.round_id == round_row.id)
    headquarters = next(
        (row for row in reviews if row.review_stage == "headquarters"), None
    )
    region = next((row for row in reviews if row.review_stage == "region"), None)
    terminal = headquarters or region
    if (
        graph.task.status != "recount_required"
        or graph.task.current_round_no != round_row.round_no
        or round_row.status != "submitted"
        or terminal is None
        or terminal.decision not in {"recount", "reject"}
        or any(row.source_round_id == round_row.id for row in graph.recount_cases)
        or any(row.round_id == round_row.id for row in graph.postings)
    ):
        return False
    candidates = tuple(
        grant
        for grant in context.principal.assignments
        if (
            grant.role_code == "admin"
            and grant.scope_type == "national"
            and grant.scope_id == "*"
            or grant.role_code == "provincial_manager"
            and grant.scope_type == "organization"
            and _same_uuid(grant.scope_id, graph.task.region_org_id)
        )
        and _grant_permission_allowed(
            context.principal,
            context.organizations,
            grant,
            resource="stocktake",
            action="manage",
            target_scope_type="organization",
            target_scope_id=str(graph.task.region_org_id),
            target_organization_id=graph.task.region_org_id,
        )
    )
    return len(candidates) == 1


def _can_manage_exact_region(
    context: _ReadContext,
    region_org_id: uuid.UUID,
) -> bool:
    return any(
        (
            grant.role_code == "admin"
            and grant.scope_type == "national"
            and grant.scope_id == "*"
            or grant.role_code == "provincial_manager"
            and grant.scope_type == "organization"
            and _same_uuid(grant.scope_id, region_org_id)
        )
        and _grant_permission_allowed(
            context.principal,
            context.organizations,
            grant,
            resource="stocktake",
            action="manage",
            target_scope_type="organization",
            target_scope_id=str(region_org_id),
            target_organization_id=region_org_id,
        )
        for grant in context.principal.assignments
    )


def _visible_scope_ids(
    context: _ReadContext,
    graph: _TaskGraph,
) -> frozenset[uuid.UUID]:
    if graph.task.region_org_id in context.visible_organization_ids:
        return frozenset(row.id for row in graph.scopes)
    if not context.technician_self_visible:
        return frozenset()
    return _current_actor_assignment_scope_ids(context, graph)


def _current_actor_assignment_scope_ids(
    context: _ReadContext,
    graph: _TaskGraph,
) -> frozenset[uuid.UUID]:
    if graph.task.current_round_no in {0, 1}:
        return frozenset(
            row.id
            for row in graph.scopes
            if row.assignee_user_id == context.principal.user_id
        )
    current = _current_round(graph)
    if current is None or current.recount_case_id is None:
        return frozenset()
    return frozenset(
        row.scope_id
        for row in graph.recount_assignments
        if row.recount_case_id == current.recount_case_id
        and row.assignee_user_id == context.principal.user_id
        and row.assignee_person_id == context.principal.person_id
    )


def _book_evidence_visible(graph: _TaskGraph) -> bool:
    current = _current_round(graph)
    if current is None:
        return False
    completion_exists = any(
        row.round_id == current.id for row in graph.difference_completions
    )
    try:
        return may_show_book_quantity(
            blind_count=graph.task.blind_count,
            round_status=current.status,
            difference_set_sealed=completion_exists,
        )
    except Exception:
        _projection_invalid()


def _current_round(graph: _TaskGraph) -> StocktakeRound | None:
    if graph.task.current_round_no == 0:
        return None
    matches = tuple(
        row for row in graph.rounds if row.round_no == graph.task.current_round_no
    )
    if len(matches) != 1:
        _projection_invalid()
    return matches[0]


def _freeze_status(
    graph: _TaskGraph,
    visible_scope_ids: frozenset[uuid.UUID],
) -> str:
    rows = tuple(
        row for row in graph.freezes if row.stocktake_scope_id in visible_scope_ids
    )
    if not rows:
        return "not_started"
    statuses = {row.status for row in rows}
    if len(statuses) == 1:
        return next(iter(statuses))
    return "mixed"


def _expected_serial_ids(row: StocktakeSnapshotLine) -> tuple[uuid.UUID, ...]:
    document = row.serial_snapshot_jsonb
    if not isinstance(document, list) or row.serial_count != len(document):
        _projection_invalid()
    values: list[uuid.UUID] = []
    for item in document:
        if not isinstance(item, dict) or not isinstance(item.get("serial_id"), str):
            _projection_invalid()
        try:
            values.append(uuid.UUID(item["serial_id"]))
        except (TypeError, ValueError):
            _projection_invalid()
    if len(set(values)) != len(values):
        _projection_invalid()
    return tuple(sorted(values, key=str))


def _permission_allowed(
    principal: FormalPrincipal,
    organizations: _OrganizationGraph,
    *,
    resource: str,
    action: str,
    target_scope_type: str,
    target_scope_id: str,
    target_organization_id: uuid.UUID | None,
) -> bool:
    grants = {grant.assignment_id: grant for grant in principal.assignments}
    matching: list[Entitlement] = []
    for entitlement in principal.entitlements:
        if (
            entitlement.resource != resource
            or entitlement.action != action
            or entitlement.field_code != ""
        ):
            continue
        grant = grants.get(entitlement.assignment_id)
        if grant is not None and _scope_covers(
            organizations,
            grant,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
            target_organization_id=target_organization_id,
        ):
            matching.append(entitlement)
    if any(row.effect == "deny" for row in matching):
        return False
    return any(row.effect == "allow" for row in matching)


def _grant_permission_allowed(
    principal: FormalPrincipal,
    organizations: _OrganizationGraph,
    grant: ScopeGrant,
    *,
    resource: str,
    action: str,
    target_scope_type: str,
    target_scope_id: str,
    target_organization_id: uuid.UUID | None,
) -> bool:
    matching = [
        row
        for row in principal.entitlements
        if row.assignment_id == grant.assignment_id
        and row.resource == resource
        and row.action == action
        and row.field_code == ""
        and _scope_covers(
            organizations,
            grant,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
            target_organization_id=target_organization_id,
        )
    ]
    if any(row.effect == "deny" for row in matching):
        return False
    return any(row.effect == "allow" for row in matching)


def _scope_covers(
    organizations: _OrganizationGraph,
    grant: ScopeGrant,
    *,
    target_scope_type: str,
    target_scope_id: str,
    target_organization_id: uuid.UUID | None,
) -> bool:
    if grant.scope_type == "national" and grant.scope_id == "*":
        return True
    if grant.scope_type == target_scope_type:
        if grant.scope_type in {"organization", "person"}:
            if _same_uuid(grant.scope_id, target_scope_id):
                return True
        elif grant.scope_id == target_scope_id:
            return True
    if grant.scope_type != "organization" or target_organization_id is None:
        return False
    try:
        ancestor = uuid.UUID(grant.scope_id)
    except (TypeError, ValueError):
        return False
    return organizations.descends_from(
        target_organization_id,
        ancestor,
        require_active_path=True,
    )


def _task_snapshots(
    tasks: Iterable[FormalStocktakeTask],
) -> tuple[_TaskSnapshot, ...]:
    return tuple(
        _TaskSnapshot(
            row.id,
            row.version,
            row.status,
            row.current_round_no,
            _aware(row.updated_at),
        )
        for row in tasks
    )


def _ensure_tasks_current(
    db: Session,
    snapshots: Sequence[_TaskSnapshot],
) -> None:
    ids = tuple(row.task_id for row in snapshots)
    current = tuple(
        db.execute(
            select(
                FormalStocktakeTask.id,
                FormalStocktakeTask.version,
                FormalStocktakeTask.status,
                FormalStocktakeTask.current_round_no,
                FormalStocktakeTask.updated_at,
            )
            .where(FormalStocktakeTask.id.in_(ids))
            .order_by(FormalStocktakeTask.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    expected = tuple(
        sorted(
            (
                row.task_id,
                row.version,
                row.status,
                row.current_round_no,
                row.updated_at,
            )
            for row in snapshots
        )
    )
    actual = tuple(
        (
            row.id,
            row.version,
            row.status,
            row.current_round_no,
            _aware(row.updated_at),
        )
        for row in current
    )
    if actual != expected:
        _fail(
            "stocktake_read_snapshot_changed",
            "conflict",
            "盘点任务在读取期间发生变化，请重新读取",
        )


def _ensure_authorization_current(
    db: Session,
    principal: FormalPrincipal,
    *,
    now: datetime | None,
) -> None:
    try:
        current = load_formal_principal(db, principal.user_id, now=now)
    except FormalAccessError:
        _fail(
            "stocktake_read_authorization_changed",
            "precondition_failed",
            "盘点读取期间权限已变化，请重新读取",
        )
    if current != principal:
        _fail(
            "stocktake_read_authorization_changed",
            "precondition_failed",
            "盘点读取期间权限已变化，请重新读取",
        )


def _group(rows: Iterable[Any], key) -> dict[Any, tuple[Any, ...]]:
    grouped: dict[Any, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return {group_key: tuple(values) for group_key, values in grouped.items()}


def _unique_by(rows: Iterable[Any], key) -> None:
    values = [key(row) for row in rows]
    if len(values) != len(set(values)):
        _projection_invalid()


def _sum_quantity(values: Iterable[Decimal]) -> Decimal:
    return sum((Decimal(value) for value in values), start=_ZERO)


def _validate_output(model, value):
    try:
        return model.model_validate(value)
    except ValidationError:
        _projection_invalid()


def _projection_invalid() -> None:
    _fail(
        "stocktake_read_projection_invalid",
        "service_unavailable",
        "盘点只读投影未通过正式合同校验",
    )


def _effective_now(value: datetime | None) -> datetime:
    return _aware(value or datetime.now(timezone.utc))


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        _projection_invalid()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aware_or_none(value: datetime | None) -> datetime | None:
    return None if value is None else _aware(value)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def _same_uuid(left: str | uuid.UUID, right: str | uuid.UUID) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _fail(code: str, category: str, message: str):
    raise StocktakeReadError(code, category, message)


__all__ = [
    "StocktakeReadError",
    "list_stocktake_tasks",
    "stocktake_task_detail",
]
