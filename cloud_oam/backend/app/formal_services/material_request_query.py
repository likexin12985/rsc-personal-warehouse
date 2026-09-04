"""Scope-safe, read-only queries for formal V1.0 material requests.

The query surface consumes only the formal demand/approval tables introduced
by migration 0029/0030.  It never reads the quarantined v0.9 transfer model,
never decrypts contact data, never returns authorization snapshots, and never
mutates the SQLAlchemy session.  Approval, allocation, reservation, outbound,
shipment, signature, OAM receipt, personal inbound, notification and
reconciliation remain independent projections.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Final
import uuid

from pydantic import ValidationError
from sqlalchemy import literal, or_, select, union_all
from sqlalchemy.orm import Session

from ..demand_models import (
    ApprovalExternalRegistration,
    ApprovalInstance,
    ApprovalReturnLineFact,
    ApprovalStep,
    ApprovalStepCandidate,
    ApprovalStepLineDecision,
    MaterialRequest,
    MaterialRequestFile,
    MaterialRequestLine,
    MaterialRequestRevision,
    SubstitutionDecision,
    SupplyTask,
)
from ..demand_schemas import MaterialRequestStateAxesOut
from ..formal_access import (
    Entitlement,
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    load_formal_principal,
)
from ..foundation_models import (
    FileObject,
    NotificationEvent,
    Organization,
    OutboxEvent,
    Person,
)
from ..inventory_models import InventoryTransaction
from ..material_request_read_schemas import (
    MaterialRequestAddressSnapshotOut,
    MaterialRequestApprovalCandidatePoolSummaryOut,
    MaterialRequestApprovalInstanceOut,
    MaterialRequestApprovalLineDecisionOut,
    MaterialRequestApprovalStepOut,
    MaterialRequestAttachmentRefOut,
    MaterialRequestContactMaskedOut,
    MaterialRequestDetailOut,
    MaterialRequestExternalEvidenceSummaryOut,
    MaterialRequestLineOut,
    MaterialRequestPageOut,
    MaterialRequestReturnLineFactOut,
    MaterialRequestRevisionSummaryOut,
    MaterialRequestSummaryOut,
    MaterialRequestSupplyTaskOut,
)
from ..models import User


_HTTP_STATUS_BY_CATEGORY: Final[dict[str, int]] = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}
_CANDIDATE_KIND_ORDER: Final[tuple[str, ...]] = (
    "assignee",
    "registrar",
    "verifier",
)
_ACTION_ORDER: Final[tuple[str, ...]] = (
    "update",
    "submit",
    "withdraw",
    "cancel",
    "approve",
    "return",
    "reject",
    "register_external_approval",
    "verify_external_approval",
    "propose_substitution",
    "confirm_substitution",
    "reject_substitution",
    "create_supply_task",
)
_ACTIVE_SUPPLY_STATUSES: Final[frozenset[str]] = frozenset(
    {"open", "reference_registered", "awaiting_supply"}
)


class MaterialRequestReadError(RuntimeError):
    """Stable, database-detail-free formal material-request read failure."""

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
                    "material_request_organization_cycle",
                    "service_unavailable",
                    "需求单组织范围图无效",
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
    actor_organization_id: uuid.UUID
    visible_person_ids: frozenset[uuid.UUID]
    visible_organization_ids: frozenset[uuid.UUID]


@dataclass(frozen=True, slots=True)
class _RequestSnapshot:
    request_id: uuid.UUID
    version: int
    revision_no: int
    status: str


@dataclass(frozen=True, slots=True)
class _AttachmentRow:
    binding: MaterialRequestFile
    file: FileObject


@dataclass(frozen=True, slots=True)
class _RequestGraph:
    request: MaterialRequest
    revisions: tuple[MaterialRequestRevision, ...]
    lines_by_revision: Mapping[uuid.UUID, tuple[MaterialRequestLine, ...]]
    attachments_by_revision: Mapping[uuid.UUID, tuple[_AttachmentRow, ...]]
    instances: tuple[ApprovalInstance, ...]
    steps_by_instance: Mapping[uuid.UUID, tuple[ApprovalStep, ...]]
    candidates_by_step: Mapping[uuid.UUID, tuple[ApprovalStepCandidate, ...]]
    decisions_by_step: Mapping[uuid.UUID, tuple[ApprovalStepLineDecision, ...]]
    registrations_by_step: Mapping[
        uuid.UUID, tuple[ApprovalExternalRegistration, ...]
    ]
    return_facts_by_instance: Mapping[
        uuid.UUID, tuple[ApprovalReturnLineFact, ...]
    ]
    supply_tasks: tuple[SupplyTask, ...]
    lifecycle_substitutions: tuple[SubstitutionDecision, ...]
    lifecycle_supply_tasks: tuple[SupplyTask, ...]
    has_inventory_facts: bool
    has_notification_facts: bool
    has_outbox_facts: bool

    @property
    def current_revision(self) -> MaterialRequestRevision:
        matches = tuple(
            revision
            for revision in self.revisions
            if revision.revision_no == self.request.revision_no
        )
        if len(matches) != 1 or self.revisions[-1].id != matches[0].id:
            _fail(
                "material_request_revision_projection_invalid",
                "service_unavailable",
                "需求单当前版本投影无效",
            )
        return matches[0]

    @property
    def current_lines(self) -> tuple[MaterialRequestLine, ...]:
        return self.lines_by_revision.get(self.current_revision.id, ())


def list_material_requests(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> MaterialRequestPageOut:
    """Return one stable UUID-ordered page inside the caller's current scope.

    ``next_after_id`` is the first row not returned on this page.  A subsequent
    call therefore includes that UUID (``>=``), avoiding both duplication and
    omission while satisfying the schema's cursor-not-in-page invariant.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        _fail(
            "material_request_page_limit_invalid",
            "invalid_request",
            "需求单分页大小无效",
        )
    with db.no_autoflush:
        context = _load_read_context(db, actor=actor, now=now)
        predicate = _visible_request_predicate(context)
        statement = (
            select(MaterialRequest)
            .where(predicate)
            .order_by(MaterialRequest.id)
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        )
        if after_id is not None:
            statement = statement.where(MaterialRequest.id >= after_id)
        rows = tuple(db.scalars(statement).all())
        page_rows = rows[:limit]
        next_after_id = rows[limit].id if len(rows) > limit else None
        if not page_rows:
            _ensure_authorization_current(db, context.principal)
            return MaterialRequestPageOut(items=(), next_after_id=None)
        snapshots = _snapshots(page_rows)
        graphs = _load_request_graphs(db, page_rows)
        items = tuple(_summary(context, graph) for graph in graphs)
        _ensure_requests_current(db, snapshots)
        _ensure_authorization_current(db, context.principal)
        return _validate_output(
            MaterialRequestPageOut,
            {"items": items, "next_after_id": next_after_id},
        )


def material_request_detail(
    db: Session,
    *,
    actor: FormalPrincipal,
    request_id: uuid.UUID,
    now: datetime | None = None,
) -> MaterialRequestDetailOut:
    """Return one complete revision/approval history or a scope-safe 404."""

    if not isinstance(request_id, uuid.UUID):
        _fail(
            "material_request_id_invalid",
            "invalid_request",
            "需求单标识无效",
        )
    with db.no_autoflush:
        context = _load_read_context(db, actor=actor, now=now)
        request = db.scalar(
            select(MaterialRequest)
            .where(
                MaterialRequest.id == request_id,
                _visible_request_predicate(context),
            )
            .execution_options(populate_existing=True)
        )
        # Missing and out-of-scope use the same response to avoid an ID oracle.
        if request is None:
            _ensure_authorization_current(db, context.principal)
            _fail(
                "material_request_not_found",
                "not_found",
                "需求单不存在",
            )
        snapshot = _snapshots((request,))
        graph = _load_request_graphs(db, (request,))[0]
        output = _detail(context, graph)
        _ensure_requests_current(db, snapshot)
        _ensure_authorization_current(db, context.principal)
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
            "需求查询必须使用正式权限主体",
        )
    try:
        current = load_formal_principal(db, actor.user_id, now=now)
    except FormalAccessError:
        _fail(
            "material_request_actor_not_current",
            "forbidden",
            "正式查询权限上下文已失效",
        )
    if (
        current.person_id != actor.person_id
        or current.authorization_version != actor.authorization_version
    ):
        _fail(
            "material_request_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail(
            "material_request_actor_inactive",
            "forbidden",
            "当前账号或人员不可查询需求单",
        )
    person = db.scalar(
        select(Person)
        .where(Person.id == current.person_id)
        .execution_options(populate_existing=True)
    )
    if person is None or person.employment_status != "active":
        _fail(
            "material_request_actor_person_invalid",
            "forbidden",
            "当前人员不可查询需求单",
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
    visible_people: set[uuid.UUID] = set()
    if any(
        grant.role_code == "technician"
        and grant.scope_type == "person"
        and _same_uuid(grant.scope_id, current.person_id)
        for grant in current.assignments
    ) and _permission_allowed(
        current,
        graph,
        resource="material_request",
        action="read",
        field_code="",
        target_scope_type="person",
        target_scope_id=str(current.person_id),
        target_organization_id=person.organization_id,
    ):
        visible_people.add(current.person_id)

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
            resource="material_request",
            action="read",
            field_code="",
            target_scope_type="organization",
            target_scope_id=str(organization.id),
            target_organization_id=organization.id,
        ):
            visible_organizations.add(organization.id)

    if not visible_people and not visible_organizations:
        _fail(
            "material_request_read_forbidden",
            "forbidden",
            "当前账号没有需求单只读权限",
        )
    return _ReadContext(
        principal=current,
        organizations=graph,
        actor_organization_id=person.organization_id,
        visible_person_ids=frozenset(visible_people),
        visible_organization_ids=frozenset(visible_organizations),
    )


def _visible_request_predicate(context: _ReadContext):
    predicates = []
    if context.visible_person_ids:
        predicates.append(
            MaterialRequest.requester_person_id.in_(context.visible_person_ids)
        )
    if context.visible_organization_ids:
        predicates.append(
            MaterialRequest.requester_org_id.in_(context.visible_organization_ids)
        )
    if not predicates:
        # _load_read_context already fails this case; retain a fail-closed
        # predicate for static/type safety.
        return MaterialRequest.id.is_(None)
    return or_(*predicates)


def _load_request_graphs(
    db: Session,
    requests: Sequence[MaterialRequest],
) -> tuple[_RequestGraph, ...]:
    request_ids = tuple(row.id for row in requests)
    revisions = tuple(
        db.scalars(
            select(MaterialRequestRevision)
            .where(MaterialRequestRevision.request_id.in_(request_ids))
            .order_by(
                MaterialRequestRevision.request_id,
                MaterialRequestRevision.revision_no,
                MaterialRequestRevision.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    revision_ids = tuple(row.id for row in revisions)
    lines = tuple(
        db.scalars(
            select(MaterialRequestLine)
            .where(MaterialRequestLine.revision_id.in_(revision_ids))
            .order_by(
                MaterialRequestLine.revision_id,
                MaterialRequestLine.line_no,
                MaterialRequestLine.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if revision_ids else ()
    attachment_pairs = tuple(
        _AttachmentRow(binding, file)
        for binding, file in db.execute(
            select(MaterialRequestFile, FileObject)
            .join(FileObject, FileObject.id == MaterialRequestFile.file_id)
            .where(MaterialRequestFile.revision_id.in_(revision_ids))
            .order_by(
                MaterialRequestFile.revision_id,
                MaterialRequestFile.purpose,
                MaterialRequestFile.request_line_id,
                MaterialRequestFile.created_at,
                MaterialRequestFile.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if revision_ids else ()
    instances = tuple(
        db.scalars(
            select(ApprovalInstance)
            .where(ApprovalInstance.request_id.in_(request_ids))
            .order_by(
                ApprovalInstance.request_id,
                ApprovalInstance.attempt_no,
                ApprovalInstance.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    instance_ids = tuple(row.id for row in instances)
    steps = tuple(
        db.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.instance_id.in_(instance_ids))
            .order_by(
                ApprovalStep.instance_id,
                ApprovalStep.step_no,
                ApprovalStep.attempt_no,
                ApprovalStep.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if instance_ids else ()
    step_ids = tuple(row.id for row in steps)
    candidates = tuple(
        db.scalars(
            select(ApprovalStepCandidate)
            .where(ApprovalStepCandidate.step_id.in_(step_ids))
            .order_by(
                ApprovalStepCandidate.step_id,
                ApprovalStepCandidate.candidate_kind,
                ApprovalStepCandidate.user_id,
                ApprovalStepCandidate.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if step_ids else ()
    decisions = tuple(
        db.scalars(
            select(ApprovalStepLineDecision)
            .where(ApprovalStepLineDecision.step_id.in_(step_ids))
            .order_by(
                ApprovalStepLineDecision.step_id,
                ApprovalStepLineDecision.request_line_id,
                ApprovalStepLineDecision.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if step_ids else ()
    registrations = tuple(
        db.scalars(
            select(ApprovalExternalRegistration)
            .where(ApprovalExternalRegistration.step_id.in_(step_ids))
            .order_by(
                ApprovalExternalRegistration.step_id,
                ApprovalExternalRegistration.registered_at,
                ApprovalExternalRegistration.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if step_ids else ()
    return_facts = tuple(
        db.scalars(
            select(ApprovalReturnLineFact)
            .where(ApprovalReturnLineFact.instance_id.in_(instance_ids))
            .order_by(
                ApprovalReturnLineFact.instance_id,
                ApprovalReturnLineFact.occurred_at,
                ApprovalReturnLineFact.return_action_id,
                ApprovalReturnLineFact.request_line_id,
                ApprovalReturnLineFact.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if instance_ids else ()
    current_revision_ids = {
        revision.id
        for request in requests
        for revision in revisions
        if revision.request_id == request.id
        and revision.revision_no == request.revision_no
    }
    current_line_ids = tuple(
        line.id for line in lines if line.revision_id in current_revision_ids
    )
    all_line_ids = tuple(line.id for line in lines)
    lifecycle_supply_tasks = tuple(
        db.scalars(
            select(SupplyTask)
            .where(SupplyTask.request_line_id.in_(all_line_ids))
            .order_by(SupplyTask.request_line_id, SupplyTask.created_at, SupplyTask.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if all_line_ids else ()
    lifecycle_substitutions = tuple(
        db.scalars(
            select(SubstitutionDecision)
            .where(SubstitutionDecision.request_line_id.in_(all_line_ids))
            .order_by(
                SubstitutionDecision.request_line_id,
                SubstitutionDecision.created_at,
                SubstitutionDecision.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if all_line_ids else ()

    revisions_by_request = _group(revisions, lambda row: row.request_id)
    lines_by_revision = _group(lines, lambda row: row.revision_id)
    attachments_by_revision = _group(
        attachment_pairs,
        lambda row: row.binding.revision_id,
    )
    instances_by_request = _group(instances, lambda row: row.request_id)
    steps_by_instance = _group(steps, lambda row: row.instance_id)
    candidates_by_step = _group(candidates, lambda row: row.step_id)
    decisions_by_step = _group(decisions, lambda row: row.step_id)
    registrations_by_step = _group(registrations, lambda row: row.step_id)
    facts_by_instance = _group(return_facts, lambda row: row.instance_id)
    line_by_id = {line.id: line for line in lines}
    supply_by_request: dict[uuid.UUID, list[SupplyTask]] = defaultdict(list)
    lifecycle_supply_by_request: dict[uuid.UUID, list[SupplyTask]] = defaultdict(list)
    substitution_by_request: dict[uuid.UUID, list[SubstitutionDecision]] = defaultdict(list)
    for task in lifecycle_supply_tasks:
        line = line_by_id.get(task.request_line_id)
        if line is None:
            _fail(
                "material_request_supply_anchor_invalid",
                "service_unavailable",
                "缺货任务未绑定当前需求明细",
            )
        lifecycle_supply_by_request[line.request_id].append(task)
        if line.id in current_line_ids:
            supply_by_request[line.request_id].append(task)
    for decision in lifecycle_substitutions:
        line = line_by_id.get(decision.request_line_id)
        if line is None:
            _fail(
                "material_request_substitution_anchor_invalid",
                "service_unavailable",
                "替代料决定未绑定需求明细",
            )
        substitution_by_request[line.request_id].append(decision)

    coordinates_by_request: dict[uuid.UUID, set[str]] = {}
    for request in requests:
        request_lines = tuple(line for line in lines if line.request_id == request.id)
        request_revisions = tuple(
            revision for revision in revisions if revision.request_id == request.id
        )
        request_supply = lifecycle_supply_by_request.get(request.id, ())
        request_substitutions = substitution_by_request.get(request.id, ())
        coordinates_by_request[request.id] = {
            str(request.id),
            request.request_no,
            *(str(row.id) for row in request_revisions),
            *(str(row.id) for row in request_lines),
            *(str(row.id) for row in request_supply),
            *(str(row.id) for row in request_substitutions),
        }
    all_coordinates = tuple(
        sorted(
            {
                coordinate
                for coordinates in coordinates_by_request.values()
                for coordinate in coordinates
            }
        )
    )
    fact_coordinates: dict[str, set[str]] = {
        "inventory": set(),
        "notification": set(),
        "outbox": set(),
    }
    if all_coordinates:
        rows = db.execute(
            union_all(
                select(
                    literal("inventory").label("fact_kind"),
                    InventoryTransaction.source_document_id.label("coordinate"),
                ).where(
                    InventoryTransaction.source_document_id.in_(all_coordinates)
                ),
                select(
                    literal("notification").label("fact_kind"),
                    NotificationEvent.business_id.label("coordinate"),
                ).where(NotificationEvent.business_id.in_(all_coordinates)),
                select(
                    literal("outbox").label("fact_kind"),
                    OutboxEvent.aggregate_id.label("coordinate"),
                ).where(OutboxEvent.aggregate_id.in_(all_coordinates)),
            )
        ).all()
        for fact_kind, coordinate in rows:
            fact_coordinates[str(fact_kind)].add(str(coordinate))

    return tuple(
        _RequestGraph(
            request=request,
            revisions=revisions_by_request.get(request.id, ()),
            lines_by_revision=lines_by_revision,
            attachments_by_revision=attachments_by_revision,
            instances=instances_by_request.get(request.id, ()),
            steps_by_instance=steps_by_instance,
            candidates_by_step=candidates_by_step,
            decisions_by_step=decisions_by_step,
            registrations_by_step=registrations_by_step,
            return_facts_by_instance=facts_by_instance,
            supply_tasks=tuple(supply_by_request.get(request.id, ())),
            lifecycle_substitutions=tuple(
                substitution_by_request.get(request.id, ())
            ),
            lifecycle_supply_tasks=tuple(
                lifecycle_supply_by_request.get(request.id, ())
            ),
            has_inventory_facts=bool(
                coordinates_by_request[request.id] & fact_coordinates["inventory"]
            ),
            has_notification_facts=bool(
                coordinates_by_request[request.id] & fact_coordinates["notification"]
            ),
            has_outbox_facts=bool(
                coordinates_by_request[request.id] & fact_coordinates["outbox"]
            ),
        )
        for request in requests
    )


def _summary(context: _ReadContext, graph: _RequestGraph) -> MaterialRequestSummaryOut:
    current = graph.current_revision
    common = _common(context, graph)
    return _validate_output(
        MaterialRequestSummaryOut,
        {
            **common,
            "line_count": len(graph.lines_by_revision.get(current.id, ())),
        },
    )


def _detail(context: _ReadContext, graph: _RequestGraph) -> MaterialRequestDetailOut:
    current = graph.current_revision
    lines = graph.lines_by_revision.get(current.id, ())
    revisions = tuple(
        _revision_summary(
            revision,
            line_count=len(graph.lines_by_revision.get(revision.id, ())),
            attachment_count=len(
                graph.attachments_by_revision.get(revision.id, ())
            ),
        )
        for revision in graph.revisions
    )
    approval_history = tuple(
        _approval_instance(context, graph, instance)
        for instance in graph.instances
    )
    return _validate_output(
        MaterialRequestDetailOut,
        {
            **_common(
                context,
                graph,
                approval_history=approval_history,
            ),
            "schema_version": "1.0",
            "lines": tuple(_line(line) for line in lines),
            "revision_history": revisions,
            "approval_history": approval_history,
            "supply_tasks": tuple(
                _supply_task(context, graph, task) for task in graph.supply_tasks
            ),
        },
    )


def _common(
    context: _ReadContext,
    graph: _RequestGraph,
    *,
    approval_history: tuple[MaterialRequestApprovalInstanceOut, ...] | None = None,
) -> dict[str, Any]:
    request = graph.request
    revision = graph.current_revision
    lines = graph.lines_by_revision.get(revision.id, ())
    attachments = graph.attachments_by_revision.get(revision.id, ())
    if not lines:
        _fail(
            "material_request_lines_missing",
            "service_unavailable",
            "需求单当前版本缺少明细",
        )
    if approval_history is None:
        approval_history = tuple(
            _approval_instance(context, graph, instance)
            for instance in graph.instances
        )
    latest = approval_history[-1] if approval_history else None
    return {
        "request_id": request.id,
        "request_no": request.request_no,
        "request_version": request.version,
        "current_revision_id": revision.id,
        "current_revision_no": revision.revision_no,
        "work_order_id": revision.work_order_id,
        "requester_person_id": request.requester_person_id,
        "requester_org_id": request.requester_org_id,
        "purpose": revision.purpose,
        "urgency": revision.urgency,
        "expected_date": revision.expected_date,
        # Only the irreversibly masked projections are touched.  The encrypted
        # contact envelope and full address document are intentionally absent.
        "address_snapshot": _validate_output(
            MaterialRequestAddressSnapshotOut,
            dict(revision.address_masked_jsonb),
        ),
        "contact_masked": _validate_output(
            MaterialRequestContactMaskedOut,
            dict(revision.contact_masked_jsonb),
        ),
        "note": revision.note,
        "attachment_refs": tuple(_attachment(row) for row in attachments),
        "approval_mode": revision.approval_mode,
        "states": _states(request),
        "approval_instance": latest,
        "allowed_actions": _allowed_actions(context, graph),
        "created_at": _aware(request.created_at),
        "updated_at": _aware(request.updated_at),
        "submitted_at": _aware_optional(request.submitted_at),
    }


def _revision_summary(
    revision: MaterialRequestRevision,
    *,
    line_count: int,
    attachment_count: int,
) -> MaterialRequestRevisionSummaryOut:
    return _validate_output(
        MaterialRequestRevisionSummaryOut,
        {
            "revision_id": revision.id,
            "revision_no": revision.revision_no,
            "previous_revision_id": revision.previous_revision_id,
            "status": revision.status,
            "line_count": line_count,
            "attachment_count": attachment_count,
            "sealed_at": _aware_optional(revision.sealed_at),
            "created_at": _aware(revision.created_at),
        },
    )


def _line(line: MaterialRequestLine) -> MaterialRequestLineOut:
    return _validate_output(
        MaterialRequestLineOut,
        {
            "request_line_id": line.id,
            "revision_id": line.revision_id,
            "revision_no": line.revision_no,
            "line_no": line.line_no,
            "material_id": line.material_id,
            "requested_qty": line.requested_qty,
            "required_date": line.required_date,
            "suggested_substitute_material_id": line.suggested_substitute_material_id,
            "note": line.note,
            "final_approved_qty": line.final_approved_qty,
            "cancelled_qty": line.cancelled_qty,
            "status": line.status,
            "version": line.version,
        },
    )


def _attachment(row: _AttachmentRow) -> MaterialRequestAttachmentRefOut:
    display_name = (row.file.original_filename or "").strip()
    if not display_name:
        display_name = f"附件-{str(row.file.id)[:8]}"
    return _validate_output(
        MaterialRequestAttachmentRefOut,
        {
            "revision_id": row.binding.revision_id,
            "revision_no": row.binding.revision_no,
            "request_line_id": row.binding.request_line_id,
            "file_id": row.file.id,
            "display_name": display_name[:255],
            "purpose": row.binding.purpose,
        },
    )


def _approval_instance(
    context: _ReadContext,
    graph: _RequestGraph,
    instance: ApprovalInstance,
) -> MaterialRequestApprovalInstanceOut:
    steps = graph.steps_by_instance.get(instance.id, ())
    may_read_external = _permission_allowed(
        context.principal,
        context.organizations,
        resource="material_request",
        action="read_star_approval",
        field_code="approval_payload",
        target_scope_type="document",
        target_scope_id=str(graph.request.id),
        target_organization_id=graph.request.requester_org_id,
    )
    evidence = None
    if may_read_external:
        evidence = tuple(
            _external_evidence(registration)
            for step in steps
            for registration in graph.registrations_by_step.get(step.id, ())
        )
    facts = tuple(
        _return_fact(instance, fact)
        for fact in graph.return_facts_by_instance.get(instance.id, ())
    )
    return _validate_output(
        MaterialRequestApprovalInstanceOut,
        {
            "instance_id": instance.id,
            "request_revision_id": instance.request_revision_id,
            "revision_no": instance.revision_no,
            "attempt_no": instance.attempt_no,
            "status": instance.status,
            "current_step_no": instance.current_step_no,
            "current_step_id": instance.current_step_id,
            "version": instance.version,
            "steps": tuple(_approval_step(graph, instance, step) for step in steps),
            "external_evidence_summaries": evidence,
            "return_line_facts": facts,
        },
    )


def _approval_step(
    graph: _RequestGraph,
    instance: ApprovalInstance,
    step: ApprovalStep,
) -> MaterialRequestApprovalStepOut:
    candidates = graph.candidates_by_step.get(step.id, ())
    kinds = tuple(
        kind
        for kind in _CANDIDATE_KIND_ORDER
        if any(row.candidate_kind == kind for row in candidates)
    )
    pool = None
    if candidates and kinds:
        pool = _validate_output(
            MaterialRequestApprovalCandidatePoolSummaryOut,
            {
                "candidate_count": len({row.user_id for row in candidates}),
                "candidate_kinds": kinds,
            },
        )
    decisions = tuple(
        _approval_decision(instance, decision)
        for decision in graph.decisions_by_step.get(step.id, ())
    )
    return _validate_output(
        MaterialRequestApprovalStepOut,
        {
            "step_id": step.id,
            "step_no": step.step_no,
            "attempt_no": step.attempt_no,
            "predecessor_step_id": step.predecessor_step_id,
            "supersedes_step_id": step.supersedes_step_id,
            "reopened_from_step_id": step.reopened_from_step_id,
            "source_mode": step.source_mode,
            "status": step.status,
            # Candidate snapshots contain authorization coordinates, not a
            # frozen presentation name.  Returning null plus a count/kind-only
            # pool is safer than deriving a mutable or identifying name.
            "assignee_snapshot": None,
            "candidate_pool_summary": pool,
            "opened_at": _aware_optional(step.opened_at),
            "decided_at": _aware_optional(step.decided_at),
            "version": step.version,
            "line_decisions": decisions,
        },
    )


def _approval_decision(
    instance: ApprovalInstance,
    decision: ApprovalStepLineDecision,
) -> MaterialRequestApprovalLineDecisionOut:
    return _validate_output(
        MaterialRequestApprovalLineDecisionOut,
        {
            "decision_id": decision.id,
            "step_id": decision.step_id,
            "request_revision_id": instance.request_revision_id,
            "revision_no": instance.revision_no,
            "request_line_id": decision.request_line_id,
            "input_qty": decision.input_qty,
            "approved_qty": decision.approved_qty,
            "rejected_qty": decision.rejected_qty,
            "reason": decision.reason,
            "decision_source": decision.decision_source,
            "external_registration_id": decision.external_registration_id,
            "decided_at": _aware(decision.decided_at),
        },
    )


def _external_evidence(
    registration: ApprovalExternalRegistration,
) -> MaterialRequestExternalEvidenceSummaryOut:
    snapshot = registration.external_approver_snapshot_jsonb
    raw_name = snapshot.get("display_name") if isinstance(snapshot, Mapping) else None
    if not isinstance(raw_name, str) or not raw_name.strip():
        _fail(
            "material_request_external_evidence_invalid",
            "service_unavailable",
            "外部审批证据投影无效",
        )
    return _validate_output(
        MaterialRequestExternalEvidenceSummaryOut,
        {
            "registration_id": registration.id,
            "registration_no": registration.registration_no,
            "step_id": registration.step_id,
            "external_action": registration.external_action,
            "status": registration.status,
            "evidence_file_id": registration.evidence_file_id,
            "external_approver_name_masked": _mask_name(raw_name),
            "external_decided_at": _aware(registration.external_decided_at),
            "registered_at": _aware(registration.registered_at),
            "verified_at": _aware_optional(registration.verified_at),
            "version": registration.version,
        },
    )


def _return_fact(
    instance: ApprovalInstance,
    fact: ApprovalReturnLineFact,
) -> MaterialRequestReturnLineFactOut:
    return _validate_output(
        MaterialRequestReturnLineFactOut,
        {
            "return_fact_id": fact.id,
            "return_action_id": fact.return_action_id,
            "instance_id": fact.instance_id,
            "returned_from_step_id": fact.returned_from_step_id,
            "target_kind": fact.target_kind,
            "target_step_id": fact.target_step_id,
            "request_revision_id": fact.request_revision_id,
            "revision_no": instance.revision_no,
            "request_line_id": fact.request_line_id,
            "returned_step_input_qty": fact.returned_step_input_qty,
            "target_step_max_qty": fact.target_step_max_qty,
            "required_review_qty": fact.required_review_qty,
            "reason": fact.reason,
            "occurred_at": _aware(fact.occurred_at),
        },
    )


def _supply_task(
    context: _ReadContext,
    graph: _RequestGraph,
    task: SupplyTask,
) -> MaterialRequestSupplyTaskOut:
    return _validate_output(
        MaterialRequestSupplyTaskOut,
        {
            "id": task.id,
            "task_no": task.task_no,
            "request_line_id": task.request_line_id,
            "substitution_decision_id": task.substitution_decision_id,
            "supply_type": task.supply_type,
            "reference_no": task.reference_no,
            "expected_qty": task.expected_qty,
            "original_equivalent_qty": task.original_equivalent_qty,
            "expected_date": task.expected_date,
            "status": task.status,
            "version": task.version,
            "created_at": _aware(task.created_at),
            "updated_at": _aware(task.updated_at),
            "allowed_actions": _supply_task_allowed_actions(context, graph, task),
        },
    )


def _allowed_actions(
    context: _ReadContext,
    graph: _RequestGraph,
) -> tuple[str, ...]:
    request = graph.request
    revision = graph.current_revision
    principal = context.principal
    actions: set[str] = set()
    requester = (
        request.requester_user_id == principal.user_id
        and request.requester_person_id == principal.person_id
    )
    if requester and request.status in {"draft", "returned"} and _permission_allowed(
        principal,
        context.organizations,
        resource="material_request",
        action="update_draft",
        field_code="",
        target_scope_type="person",
        target_scope_id=str(request.requester_person_id),
        target_organization_id=context.actor_organization_id,
    ):
        actions.add("update")
    if (
        requester
        and request.status in {"draft", "returned"}
        and revision.status == "draft"
        and bool(graph.current_lines)
        and _permission_allowed(
            principal,
            context.organizations,
            resource="material_request",
            action="submit",
            field_code="",
            target_scope_type="person",
            target_scope_id=str(request.requester_person_id),
            target_organization_id=context.actor_organization_id,
        )
    ):
        actions.add("submit")

    if (
        requester
        and request.status in {"submitted", "approval_in_progress"}
        and _withdraw_graph_is_safe(graph)
        and _permission_allowed(
            principal,
            context.organizations,
            resource="material_request",
            action="withdraw",
            field_code="",
            target_scope_type="person",
            target_scope_id=str(request.requester_person_id),
            target_organization_id=request.requester_org_id,
        )
    ):
        actions.add("withdraw")
    if (
        requester
        and request.status
        in {"returned", "partially_approved", "approved", "cancellation_pending"}
        and _direct_cancel_graph_is_safe(graph)
        and _permission_allowed(
            principal,
            context.organizations,
            resource="material_request",
            action="cancel",
            field_code="",
            target_scope_type="person",
            target_scope_id=str(request.requester_person_id),
            target_organization_id=request.requester_org_id,
        )
    ):
        actions.add("cancel")

    if _supply_management_allowed(context, graph) and any(
        _unplanned_supply_quantity(graph, line) > 0
        for line in graph.current_lines
        if _supply_line_is_current(graph, line)
        and not _supply_line_has_active_substitution(graph, line.id)
    ):
        actions.add("create_supply_task")

    # Substitution mutations remain dormant until their guarded service is
    # mounted. Supply planning does not advance any fulfillment state axis.
    if graph.instances:
        instance = graph.instances[-1]
        if (
            instance.status == "active"
            and instance.current_step_id is not None
            and not requester
        ):
            step = next(
                (
                    row
                    for row in graph.steps_by_instance.get(instance.id, ())
                    if row.id == instance.current_step_id
                ),
                None,
            )
            if step is not None and step.step_no == instance.current_step_no:
                if step.status == "open" and step.source_mode == "internal":
                    permission_action = (
                        "approve_region" if step.step_no == 1 else "approve_headquarters"
                    )
                    expected_role = "provincial_manager" if step.step_no == 1 else "admin"
                    if _current_candidate_allows(
                        context,
                        graph,
                        step=step,
                        candidate_kind="assignee",
                        expected_role=expected_role,
                        permission_action=permission_action,
                        permission_field="approval_decision",
                        target_scope_type="organization",
                        target_scope_id=str(request.requester_org_id),
                    ):
                        actions.update(("approve", "return", "reject"))
                elif (
                    step.status == "awaiting_external_evidence"
                    and step.source_mode == "external_registration"
                    and _current_candidate_allows(
                        context,
                        graph,
                        step=step,
                        candidate_kind="registrar",
                        expected_role="admin",
                        permission_action="register_external",
                        permission_field="approval_evidence",
                        target_scope_type="organization",
                        target_scope_id=str(request.requester_org_id),
                    )
                ):
                    actions.add("register_external_approval")
                elif (
                    step.status == "evidence_pending_verification"
                    and step.source_mode == "external_registration"
                    and _verification_separation_holds(context, graph, step)
                    and _current_candidate_allows(
                        context,
                        graph,
                        step=step,
                        candidate_kind="verifier",
                        expected_role="admin",
                        permission_action="verify_external",
                        permission_field="approval_evidence",
                        target_scope_type="organization",
                        target_scope_id=str(request.requester_org_id),
                    )
                ):
                    actions.add("verify_external_approval")
    return tuple(action for action in _ACTION_ORDER if action in actions)


def _supply_management_allowed(context: _ReadContext, graph: _RequestGraph) -> bool:
    """Mirror the supply command's current national-admin authority.

    A national role and a permission granted through an unrelated assignment
    cannot be combined to manufacture supply-management authority. Scoped
    denies still apply to the request's actual organization.
    """

    request = graph.request
    if (
        request.status not in {"approved", "partially_approved"}
        or not graph.instances
        or not _state_axes_are_neutral(request)
    ):
        return False
    revision = graph.current_revision
    latest = graph.instances[-1]
    if (
        revision.status != "sealed"
        or latest.status != "completed"
        or latest.request_revision_id != revision.id
        or latest.revision_no != revision.revision_no
        or latest.current_step_id is not None
        or latest.current_step_no is not None
        or latest.completed_at is None
        or any(row.status == "active" for row in graph.instances)
    ):
        return False
    principal = context.principal
    administrator_ids = {
        grant.assignment_id
        for grant in principal.assignments
        if grant.role_code == "admin"
        and grant.scope_type == "national"
        and grant.scope_id == "*"
    }
    if len(administrator_ids) != 1 or not any(
        entitlement.assignment_id in administrator_ids
        and entitlement.resource == "supply_task"
        and entitlement.action == "manage"
        and entitlement.field_code == ""
        and entitlement.effect == "allow"
        for entitlement in principal.entitlements
    ):
        return False
    return _permission_allowed(
        principal,
        context.organizations,
        resource="supply_task",
        action="manage",
        field_code="",
        target_scope_type="organization",
        target_scope_id=str(request.requester_org_id),
        target_organization_id=request.requester_org_id,
    )


def _supply_line_is_current(graph: _RequestGraph, line: MaterialRequestLine) -> bool:
    return (
        line.request_id == graph.request.id
        and line.revision_id == graph.current_revision.id
        and line.revision_no == graph.request.revision_no
        and line.status in {"approved", "partially_approved"}
        and line.final_approved_qty > line.cancelled_qty
    )


def _unplanned_supply_quantity(
    graph: _RequestGraph,
    line: MaterialRequestLine,
) -> Decimal:
    active_quantity = sum(
        (
            task.original_equivalent_qty
            for task in graph.lifecycle_supply_tasks
            if task.request_line_id == line.id and task.status in _ACTIVE_SUPPLY_STATUSES
        ),
        Decimal("0"),
    )
    return line.final_approved_qty - line.cancelled_qty - active_quantity


def _supply_task_allowed_actions(
    context: _ReadContext,
    graph: _RequestGraph,
    task: SupplyTask,
) -> tuple[str, ...]:
    if (
        task.status not in _ACTIVE_SUPPLY_STATUSES
        or not _supply_management_allowed(context, graph)
    ):
        return ()
    line = next((row for row in graph.current_lines if row.id == task.request_line_id), None)
    if line is None or not _supply_line_is_current(graph, line):
        return ()
    if _supply_line_has_active_substitution(graph, line.id):
        return ("cancel_supply_task",)
    return ("update_supply_task", "cancel_supply_task")


def _supply_line_has_active_substitution(graph: _RequestGraph, line_id: uuid.UUID) -> bool:
    return any(
        row.request_line_id == line_id and row.status in {"proposed", "confirmed"}
        for row in graph.lifecycle_substitutions
    )


def _withdraw_graph_is_safe(graph: _RequestGraph) -> bool:
    if not _state_axes_are_neutral(graph.request) or not graph.instances:
        return False
    latest = graph.instances[-1]
    active = tuple(row for row in graph.instances if row.status == "active")
    if (
        len(active) != 1
        or active[0].id != latest.id
        or latest.request_revision_id != graph.current_revision.id
        or latest.revision_no != graph.current_revision.revision_no
        or latest.current_step_id is None
        or latest.current_step_no is None
    ):
        return False
    steps = graph.steps_by_instance.get(latest.id, ())
    if any(
        registration.status == "pending_verification"
        for step in steps
        for registration in graph.registrations_by_step.get(step.id, ())
    ):
        return False
    current = tuple(
        row
        for row in steps
        if row.id == latest.current_step_id
        and row.step_no == latest.current_step_no
        and row.status
        in {"open", "awaiting_external_evidence", "evidence_pending_verification"}
    )
    return len(current) == 1 and any(
        row.status
        in {"pending", "open", "awaiting_external_evidence", "evidence_pending_verification"}
        for row in steps
    )


def _direct_cancel_graph_is_safe(graph: _RequestGraph) -> bool:
    if (
        not _state_axes_are_neutral(graph.request)
        or not graph.instances
        or any(row.status == "active" for row in graph.instances)
        or any(row.cancelled_qty != 0 for row in graph.current_lines)
        or any(
            row.status in {"proposed", "confirmed"}
            for row in graph.lifecycle_substitutions
        )
        or any(
            row.status in {"open", "reference_registered", "awaiting_supply"}
            for row in graph.lifecycle_supply_tasks
        )
        or graph.has_inventory_facts
        or graph.has_notification_facts
        or graph.has_outbox_facts
    ):
        return False
    latest = graph.instances[-1]
    expected_status = "returned" if graph.request.status == "returned" else "completed"
    if (
        latest.status != expected_status
        or latest.current_step_id is not None
        or latest.current_step_no is not None
        or latest.completed_at is None
    ):
        return False
    return graph.request.status == "returned" or (
        latest.request_revision_id == graph.current_revision.id
        and latest.revision_no == graph.current_revision.revision_no
    )


def _state_axes_are_neutral(request: MaterialRequest) -> bool:
    return all(
        getattr(request, field) == expected
        for field, expected in {
            "allocation_status": "not_allocated",
            "reservation_status": "not_reserved",
            "outbound_status": "not_started",
            "shipment_status": "not_started",
            "logistics_signature_status": "not_signed",
            "oam_receipt_status": "not_occurred",
            "personal_inbound_status": "not_started",
            "notification_status": "not_started",
            "reconciliation_status": "not_started",
        }.items()
    )


def _current_candidate_allows(
    context: _ReadContext,
    graph: _RequestGraph,
    *,
    step: ApprovalStep,
    candidate_kind: str,
    expected_role: str,
    permission_action: str,
    permission_field: str,
    target_scope_type: str,
    target_scope_id: str,
) -> bool:
    principal = context.principal
    matches = tuple(
        row
        for row in graph.candidates_by_step.get(step.id, ())
        if row.user_id == principal.user_id
        and row.person_id == principal.person_id
        and row.candidate_kind == candidate_kind
    )
    if len(matches) != 1:
        return False
    candidate = matches[0]
    grants = tuple(
        grant
        for grant in principal.assignments
        if grant.assignment_id == candidate.role_assignment_id
        and grant.role_code == expected_role
    )
    if len(grants) != 1 or candidate.authorization_version != principal.authorization_version:
        return False
    grant = grants[0]
    if candidate.snapshot_jsonb != _candidate_snapshot(principal, grant):
        return False
    return _permission_allowed(
        principal,
        context.organizations,
        resource="material_request",
        action=permission_action,
        field_code=permission_field,
        target_scope_type=target_scope_type,
        target_scope_id=target_scope_id,
        target_organization_id=graph.request.requester_org_id,
    )


def _verification_separation_holds(
    context: _ReadContext,
    graph: _RequestGraph,
    step: ApprovalStep,
) -> bool:
    pending = tuple(
        row
        for row in graph.registrations_by_step.get(step.id, ())
        if row.status == "pending_verification"
    )
    if len(pending) != 1:
        return False
    registration = pending[0]
    return (
        registration.registered_by_user_id != context.principal.user_id
        and registration.registered_by_person_id != context.principal.person_id
    )


def _candidate_snapshot(
    principal: FormalPrincipal,
    grant: ScopeGrant,
) -> dict[str, Any]:
    document = {
        "user_id": principal.user_id,
        "person_id": str(principal.person_id),
        "role_assignment_id": str(grant.assignment_id),
        "role_code": grant.role_code,
        "scope_type": grant.scope_type,
        "scope_id": grant.scope_id,
        "authorization_version": principal.authorization_version,
        "permission_keys": [list(key) for key in principal.permission_keys()],
    }
    snapshot = {key: value for key, value in document.items() if key != "permission_keys"}
    snapshot["authorization_sha256"] = hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return snapshot


def _permission_allowed(
    principal: FormalPrincipal,
    organizations: _OrganizationGraph,
    *,
    resource: str,
    action: str,
    field_code: str,
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
            or entitlement.field_code not in {"", field_code}
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


def _states(request: MaterialRequest) -> MaterialRequestStateAxesOut:
    return _validate_output(
        MaterialRequestStateAxesOut,
        {
            "request_status": request.status,
            "allocation_status": request.allocation_status,
            "reservation_status": request.reservation_status,
            "outbound_status": request.outbound_status,
            "shipment_status": request.shipment_status,
            "logistics_signature_status": request.logistics_signature_status,
            "oam_receipt_status": request.oam_receipt_status,
            "personal_inbound_status": request.personal_inbound_status,
            "notification_status": request.notification_status,
            "reconciliation_status": request.reconciliation_status,
        },
    )


def _snapshots(requests: Iterable[MaterialRequest]) -> tuple[_RequestSnapshot, ...]:
    return tuple(
        _RequestSnapshot(row.id, row.version, row.revision_no, row.status)
        for row in requests
    )


def _ensure_requests_current(
    db: Session,
    snapshots: Sequence[_RequestSnapshot],
) -> None:
    ids = tuple(row.request_id for row in snapshots)
    current = tuple(
        db.execute(
            select(
                MaterialRequest.id,
                MaterialRequest.version,
                MaterialRequest.revision_no,
                MaterialRequest.status,
            )
            .where(MaterialRequest.id.in_(ids))
            .order_by(MaterialRequest.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    expected = tuple(
        sorted(
            (
                row.request_id,
                row.version,
                row.revision_no,
                row.status,
            )
            for row in snapshots
        )
    )
    actual = tuple((row.id, row.version, row.revision_no, row.status) for row in current)
    if actual != expected:
        _fail(
            "material_request_read_snapshot_changed",
            "conflict",
            "需求单在读取期间发生变化，请重新读取",
        )


def _ensure_authorization_current(db: Session, principal: FormalPrincipal) -> None:
    current = db.execute(
        select(User.person_id, User.account_status, User.authorization_version)
        .where(User.id == principal.user_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if current is None or (
        current.person_id != principal.person_id
        or current.account_status != "active"
        or current.authorization_version != principal.authorization_version
    ):
        _fail(
            "material_request_read_authorization_changed",
            "precondition_failed",
            "需求单读取期间权限已变化，请重新读取",
        )


def _group(rows: Iterable[Any], key) -> dict[Any, tuple[Any, ...]]:
    grouped: dict[Any, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return {group_key: tuple(values) for group_key, values in grouped.items()}


def _validate_output(model, value):
    try:
        return model.model_validate(value)
    except ValidationError:
        _fail(
            "material_request_read_projection_invalid",
            "service_unavailable",
            "需求单只读投影未通过正式合同校验",
        )


def _mask_name(value: str) -> str:
    checked = value.strip()
    if not checked:
        _fail(
            "material_request_external_name_invalid",
            "service_unavailable",
            "外部审批人展示字段无效",
        )
    if len(checked) == 1:
        return "*"
    visible = checked[0]
    return visible + "*" * min(len(checked) - 1, 119)


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        _fail(
            "material_request_time_invalid",
            "service_unavailable",
            "需求单时间字段无效",
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aware_optional(value: datetime | None) -> datetime | None:
    return None if value is None else _aware(value)


def _same_uuid(left: str | uuid.UUID, right: str | uuid.UUID) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestReadError(code, category, message)


__all__ = [
    "MaterialRequestReadError",
    "list_material_requests",
    "material_request_detail",
]
