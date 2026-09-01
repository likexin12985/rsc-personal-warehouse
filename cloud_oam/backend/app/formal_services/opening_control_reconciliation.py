"""Independent reconciliation of OAM opening-control differences.

This service never calls OAM and never writes an inventory account, balance,
movement, transaction, opening establishment, or stocktake difference.  It
binds the already sealed OAM control snapshot to the already posted local
opening ledger, records a regional explanation, and records a separate NIO
headquarters approval.  The caller owns the surrounding transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Final, Mapping, Sequence
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    load_formal_principal,
)
from ..foundation_models import (
    AuditEvent,
    FileObject,
    OpeningControlReconciliationCommandConsumption,
    OpeningControlReconciliationItem,
    OpeningControlReconciliationRun,
    Organization,
    OutboxEvent,
    Person,
    ReconciliationCommand,
    ReconciliationItem,
    ReconciliationRun,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..models import User
from ..reconciliation_schemas import (
    OpeningControlReconciliationDetailOut,
    OpeningControlReconciliationItemOut,
    OpeningControlReconciliationPageOut,
    OpeningControlReconciliationSummaryOut,
)
from ..stocktake_models import (
    FormalStocktakeTask,
    InventoryOpeningEstablishment,
    StocktakeControlSnapshotLine,
    StocktakeDifference,
    StocktakePosting,
    StocktakeRound,
)
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _require_prelocked_audit_stream_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)


INVENTORY_STREAM_KEY: Final[str] = "inventory"
_ZERO: Final[Decimal] = Decimal("0.000")
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000000000000")
_QUANTITY_QUANTUM: Final[Decimal] = Decimal("0.001")
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_PG_LOCK_SOURCE_FUNCTION: Final[str] = (
    "public.rsc_lock_opening_reconciliation_source_0026"
)
_PG_LOCK_RUN_FUNCTION: Final[str] = (
    "public.rsc_lock_opening_reconciliation_run_0026"
)
_PG_LOCK_FILES_FUNCTION: Final[str] = (
    "public.rsc_lock_opening_reconciliation_files_0026"
)
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class OpeningControlReconciliationError(RuntimeError):
    """Stable, database-detail-free reconciliation failure."""

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
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class StartOpeningControlReconciliationCommand:
    task_id: uuid.UUID
    expected_task_version: int


@dataclass(frozen=True, slots=True)
class OpeningControlExplanationInput:
    reconciliation_item_id: uuid.UUID
    expected_version: int
    explanation: str
    evidence_reference: str
    evidence_file_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ExplainOpeningControlReconciliationCommand:
    reconciliation_run_id: uuid.UUID
    expected_version: int
    items: tuple[OpeningControlExplanationInput, ...]


@dataclass(frozen=True, slots=True)
class ApproveOpeningControlReconciliationCommand:
    reconciliation_run_id: uuid.UUID
    expected_version: int
    comment: str


@dataclass(frozen=True, slots=True)
class OpeningControlReconciliationStartResult:
    reconciliation_run_id: uuid.UUID
    task_id: uuid.UUID
    status: str
    version: int
    item_count: int
    created_at: datetime
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class OpeningControlReconciliationExplainResult:
    reconciliation_run_id: uuid.UUID
    task_id: uuid.UUID
    status: str
    version: int
    explained_item_count: int
    explained_at: datetime
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class OpeningControlReconciliationApproveResult:
    reconciliation_run_id: uuid.UUID
    task_id: uuid.UUID
    status: str
    version: int
    resolved_item_count: int
    approved_at: datetime
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class OpeningControlReconciliationStatus:
    status: str
    reconciliation_run_id: uuid.UUID | None
    pending_control_difference_count: int


@dataclass(frozen=True, slots=True)
class OpeningControlReconciliationProof:
    status: str
    reconciliation_run_id: uuid.UUID | None
    task_id: uuid.UUID
    pending_control_difference_count: int
    approved_at: datetime | None


@dataclass(frozen=True, slots=True)
class _ActorAuthorization:
    assignment: RoleAssignment
    grant: ScopeGrant
    person: Person
    organization: Organization


@dataclass(frozen=True, slots=True)
class _ItemGraph:
    item: ReconciliationItem
    binding: OpeningControlReconciliationItem
    difference: StocktakeDifference
    control_line: StocktakeControlSnapshotLine


@dataclass(frozen=True, slots=True)
class _RunGraph:
    run: ReconciliationRun
    binding: OpeningControlReconciliationRun
    task: FormalStocktakeTask
    round_row: StocktakeRound
    posting: StocktakePosting
    establishments: tuple[InventoryOpeningEstablishment, ...]
    items: tuple[_ItemGraph, ...]
    commands: tuple[ReconciliationCommand, ...]
    consumptions: tuple[OpeningControlReconciliationCommandConsumption, ...]


_PRELOCKED_RECONCILIATION_GRAPH_SEAL: Final[object] = object()
_PRELOCKED_RECONCILIATION_BATCH_SEAL: Final[object] = object()
_PRELOCKED_RECONCILIATION_PRINCIPAL_SEAL: Final[object] = object()


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningReconciliationPrincipalGraph:
    """One task's opening + reconciliation principal lock coverage."""

    session: Session
    transaction: object
    task_id: uuid.UUID
    historical_reconciliation_user_ids: tuple[str, ...]
    allowed_reconciliation_user_ids: tuple[str, ...]
    opening_principal_graph: object
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningControlReconciliationGraph:
    """Internal transaction-bound coordinates for one task reconciliation.

    The proof covers only the reconciliation source/projection graph.  Its
    caller must already hold and later purely re-prove the corresponding
    opening task/evidence/reference/serial/balance graph before using this
    proof, and must take the inventory audit head only after this proof has
    been issued.
    """

    session: Session
    transaction: object
    task_id: uuid.UUID
    round_id: uuid.UUID
    run_id: uuid.UUID | None
    pending_control_difference_count: int
    item_ids: tuple[uuid.UUID, ...]
    evidence_file_ids: tuple[uuid.UUID, ...]
    historical_reconciliation_user_ids: tuple[str, ...]
    allowed_reconciliation_user_ids: tuple[str, ...]
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningControlReconciliationBatch:
    """Transaction-bound, audit-free reconciliation graph batch."""

    session: Session
    transaction: object
    task_ids: tuple[uuid.UUID, ...]
    graphs: tuple[_PrelockedOpeningControlReconciliationGraph, ...]
    principal_graph: object
    seal: object


def start_opening_control_reconciliation(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: StartOpeningControlReconciliationCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningControlReconciliationStartResult:
    try:
        return _start_opening_control_reconciliation(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningControlReconciliationError:
        raise
    except AuditChainError as exc:
        _fail(
            "opening_reconciliation_audit_unavailable",
            "service_unavailable",
            "对账审计链不可用，未创建对账运行",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "opening_reconciliation_concurrent_conflict",
            "conflict",
            "对账运行发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "opening_reconciliation_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了对账运行，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable reconciliation start boundary")


def explain_opening_control_reconciliation(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: ExplainOpeningControlReconciliationCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningControlReconciliationExplainResult:
    try:
        return _explain_opening_control_reconciliation(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningControlReconciliationError:
        raise
    except AuditChainError as exc:
        _fail(
            "opening_reconciliation_audit_unavailable",
            "service_unavailable",
            "对账审计链不可用，解释未保存",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "opening_reconciliation_concurrent_conflict",
            "conflict",
            "对账解释发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "opening_reconciliation_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了对账解释，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable reconciliation explanation boundary")


def approve_opening_control_reconciliation(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: ApproveOpeningControlReconciliationCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningControlReconciliationApproveResult:
    try:
        return _approve_opening_control_reconciliation(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningControlReconciliationError:
        raise
    except AuditChainError as exc:
        _fail(
            "opening_reconciliation_audit_unavailable",
            "service_unavailable",
            "对账审计链不可用，批准未保存",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "opening_reconciliation_concurrent_conflict",
            "conflict",
            "对账批准发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "opening_reconciliation_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了对账批准，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable reconciliation approval boundary")


def _start_opening_control_reconciliation(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: StartOpeningControlReconciliationCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningControlReconciliationStartResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_start_command(command)
    key = _require_idempotency_key(idempotency_key)
    request_reference = _request_reference("create_opening", request_id)
    key_hash = _storage_hash("create_opening", key)
    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-reconciliation-key", key_hash),
            _advisory_coordinate("opening-reconciliation-task", str(checked.task_id)),
        ),
    )
    opening_root = _lock_opening_terminal_root_for_reconciliation(
        db,
        checked.task_id,
    )
    task = getattr(opening_root, "task", None)
    if not isinstance(task, FormalStocktakeTask) or task.id != checked.task_id:
        _evidence_invalid("期初过账根锁返回了错误任务")
    principal_graph = _lock_opening_principal_graph_for_reconciliation(
        db,
        task.id,
        supplied_user_ids=(supplied.user_id,),
    )
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    authorization = _authorize_actor(
        db,
        current,
        now=now,
        action="create_opening",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    )
    request_document = _start_request_document(current, checked)
    request_hash = _hash_document(request_document)
    existing_command = _load_command_by_key(db, key_hash)
    if existing_command is None:
        if task.task_type != "opening" or task.status != "posted":
            _fail(
                "opening_reconciliation_task_state_invalid",
                "precondition_failed",
                "仅已独立过账且尚未关闭的期初任务可创建对账运行",
            )
        if task.version != checked.expected_task_version:
            _fail(
                "opening_reconciliation_task_version_conflict",
                "conflict",
                "期初任务版本已变化，请重新读取",
            )
        if db.scalar(
            select(OpeningControlReconciliationRun.run_id).where(
                OpeningControlReconciliationRun.task_id == task.id
            )
        ) is not None:
            _fail(
                "opening_reconciliation_already_exists",
                "conflict",
                "该期初任务已由其他幂等请求创建对账运行",
            )

    opening_proof = _lock_opening_terminal_graph_for_reconciliation(
        db,
        opening_root,
        principal_graph,
    )
    reconciliation_proof = (
        _lock_opening_control_reconciliation_graph_for_task(
            db,
            task_id=task.id,
            principal_graph=principal_graph,
        )
    )
    round_row, posting, establishments, differences, controls = (
        _load_posted_source_graph(db, task, lock=False)
    )
    if existing_command is None and not differences:
        _fail(
            "opening_reconciliation_not_required",
            "precondition_failed",
            "该期初任务没有待核实 OAM 控制差异",
        )
    manifest = _item_manifest_sha256(
        task_id=task.id,
        round_id=round_row.id,
        differences=differences,
        controls=controls,
    )
    ledger_cursors = {row.established_ledger_cursor for row in establishments}
    if len(ledger_cursors) != 1:
        _evidence_invalid("期初成立账本游标不唯一")
    ledger_cursor = next(iter(ledger_cursors))
    if task.control_source_system_id is None or task.control_snapshot_at is None:
        _evidence_invalid("期初控制来源锚点缺失")

    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    _validate_prelocked_opening_terminal_for_reconciliation(
        db,
        opening_proof,
        audit_proof=audit_proof,
    )
    if existing_command is not None:
        if reconciliation_proof.run_id != existing_command.run_id:
            _idempotency_conflict()
        existing_graph = (
            _validate_run_graph_from_prelocked_reconciliation_graph(
                db,
                proof=reconciliation_proof,
                audit_proof=audit_proof,
            )
        )
        return _replay_start(
            db,
            command_row=existing_command,
            request_hash=request_hash,
            expected_task_id=checked.task_id,
            prevalidated_graph=existing_graph,
        )
    if reconciliation_proof.run_id is not None:
        _fail(
            "opening_reconciliation_already_exists",
            "conflict",
            "该期初任务已由其他幂等请求创建对账运行",
        )
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    authorization = _authorize_actor(
        db,
        current,
        now=now,
        action="create_opening",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    )
    if task.posted_at is None:
        _evidence_invalid("期初任务缺少独立过账时间")
    if now < _as_utc(task.posted_at):
        _fail(
            "opening_reconciliation_create_clock_not_monotonic",
            "service_unavailable",
            "数据库时间早于期初独立过账时间，禁止形成对账创建顺序",
        )
    run_id = uuid.uuid4()
    result = OpeningControlReconciliationStartResult(
        reconciliation_run_id=run_id,
        task_id=task.id,
        status="differences",
        version=0,
        item_count=len(differences),
        created_at=now,
    )
    result_document = _start_result_document(result)
    command_row = _command_row(
        operation="create_opening",
        run_id=run_id,
        target_version=0,
        idempotency_key_hash=key_hash,
        request_reference=request_reference,
        request_document=request_document,
        result_document=result_document,
        actor=current,
        authorization=authorization,
        now=now,
    )
    run = ReconciliationRun(
        id=run_id,
        run_key=f"opening-control:{task.id}:{round_row.id}",
        source_system_id=task.control_source_system_id,
        scope=f"opening:{task.region_org_id}:{task.id}",
        external_snapshot_at=task.control_snapshot_at,
        local_ledger_cursor=str(ledger_cursor),
        status="differences",
        summary_jsonb={
            "schema": "cloud_oam.opening_control_reconciliation.summary.v1",
            "task_id": str(task.id),
            "round_id": str(round_row.id),
            "item_count": len(differences),
            "item_manifest_sha256": manifest,
        },
        started_at=now,
        completed_at=now,
        created_at=now,
        updated_at=now,
    )
    binding = OpeningControlReconciliationRun(
        run_id=run_id,
        create_command_id=command_row.id,
        task_id=task.id,
        round_id=round_row.id,
        region_org_id=task.region_org_id,
        posting_id=posting.id,
        control_sync_run_id=task.control_sync_run_id,
        version=0,
        item_count=len(differences),
        item_manifest_sha256=manifest,
        created_by_user_id=current.user_id,
        created_by_person_id=current.person_id,
        created_role_assignment_id=authorization.assignment.id,
        created_authorization_version=current.authorization_version,
        approved_by_user_id=None,
        approved_by_person_id=None,
        approved_role_assignment_id=None,
        approved_authorization_version=None,
        approved_at=None,
        approval_comment="",
        created_at=now,
        updated_at=now,
    )
    # The formal extension and its create-command coordinate are the seal for
    # the generic projection.  Their foreign keys are deferred so the binding
    # can be written first, the projection can prove that binding on INSERT,
    # and the append-only command can close the graph last in this transaction.
    db.add(binding)
    db.flush()
    db.add(run)
    db.flush()

    controls_by_id = {row.id: row for row in controls}
    for difference in differences:
        control = controls_by_id.get(difference.control_snapshot_line_id)
        if control is None:
            _evidence_invalid("待核实差异没有唯一控制快照行")
        item_id = uuid.uuid4()
        item = ReconciliationItem(
            id=item_id,
            run_id=run.id,
            business_key=control.external_business_key,
            external_qty=difference.book_qty,
            local_qty=difference.counted_qty,
            difference=difference.book_qty - difference.counted_qty,
            status="difference",
            explanation="",
            evidence_file_id=None,
            created_at=now,
            updated_at=now,
        )
        item_binding = OpeningControlReconciliationItem(
            item_id=item_id,
            run_id=run.id,
            task_id=task.id,
            round_id=round_row.id,
            difference_id=difference.id,
            control_snapshot_line_id=control.id,
            version=0,
            evidence_reference="",
            evidence_file_sha256=None,
            evidence_file_size_bytes=None,
            evidence_file_mime_type=None,
            explained_by_user_id=None,
            explained_by_person_id=None,
            explained_role_assignment_id=None,
            explanation_authorization_version=None,
            explained_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(item_binding)
        db.flush()
        db.add(item)
        db.flush()

    db.add(command_row)
    db.flush()
    _append_effects(
        db,
        operation="create_opening",
        run=run,
        actor=current,
        authorization=authorization,
        command_row=command_row,
        before={"status": None, "version": None},
        result=result_document,
        transitions=(("reconciliation_run", run.id, None, "differences"),),
        now=now,
    )
    _seal_command_consumption(db, command_row)
    _validate_run_graph(
        db,
        run.id,
        lock=False,
        audit_proof=audit_proof,
    )
    _validate_reconciliation_actor_coverage_from_prelocked_graph(
        db,
        proof=reconciliation_proof,
    )
    return result


def _explain_opening_control_reconciliation(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: ExplainOpeningControlReconciliationCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningControlReconciliationExplainResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_explain_command(command)
    key = _require_idempotency_key(idempotency_key)
    request_reference = _request_reference("explain_opening", request_id)
    key_hash = _storage_hash("explain_opening", key)
    task_id = _task_id_for_run(db, checked.reconciliation_run_id)
    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-reconciliation-key", key_hash),
            _advisory_coordinate(
                "opening-reconciliation-task", str(task_id)
            ),
        ),
    )
    opening_root = _lock_opening_terminal_root_for_reconciliation(db, task_id)
    task = getattr(opening_root, "task", None)
    if not isinstance(task, FormalStocktakeTask) or task.id != task_id:
        _evidence_invalid("对账运行预读任务坐标与期初根锁不一致")
    # Global row/owner order is ledger -> task -> principal -> complete
    # opening graph -> reconciliation source/run/items/files -> audit head.
    principal_graph = _lock_opening_principal_graph_for_reconciliation(
        db,
        task.id,
        supplied_user_ids=(supplied.user_id,),
    )
    opening_proof = _lock_opening_terminal_graph_for_reconciliation(
        db,
        opening_root,
        principal_graph,
    )
    reconciliation_proof = (
        _lock_opening_control_reconciliation_graph_for_task(
            db,
            task_id=task_id,
            expected_run_id=checked.reconciliation_run_id,
            extra_evidence_file_ids=tuple(
                row.evidence_file_id
                for row in checked.items
                if row.evidence_file_id is not None
            ),
            principal_graph=principal_graph,
        )
    )
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    _validate_prelocked_opening_terminal_for_reconciliation(
        db,
        opening_proof,
        audit_proof=audit_proof,
    )
    graph = _validate_run_graph_from_prelocked_reconciliation_graph(
        db,
        proof=reconciliation_proof,
        audit_proof=audit_proof,
    )
    if graph.task.id != task_id or graph.binding.task_id != task_id:
        _evidence_invalid("对账运行预读任务坐标与锁后正式绑定不一致")
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    authorization = _authorize_actor(
        db,
        current,
        now=now,
        action="explain_opening",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(graph.task.region_org_id),
    )
    request_document = _explain_request_document(current, checked)
    request_hash = _hash_document(request_document)
    existing_command = _load_command_by_key(db, key_hash)
    if existing_command is not None:
        return _replay_explain(
            db,
            command_row=existing_command,
            request_hash=request_hash,
            expected_run_id=checked.reconciliation_run_id,
            prevalidated_graph=graph,
        )

    if graph.task.status != "posted" or graph.run.status != "differences":
        _fail(
            "opening_reconciliation_explain_state_invalid",
            "precondition_failed",
            "仅已过账且尚未批准的期初对账运行可提交解释",
        )
    if graph.binding.version != checked.expected_version:
        _fail(
            "opening_reconciliation_version_conflict",
            "conflict",
            "对账运行版本已变化，请重新读取",
        )
    supplied_by_id = {row.reconciliation_item_id: row for row in checked.items}
    if len(supplied_by_id) != len(checked.items) or set(supplied_by_id) != {
        row.item.id for row in graph.items
    }:
        _fail(
            "opening_reconciliation_item_set_mismatch",
            "precondition_failed",
            "解释请求必须一次完整覆盖当前对账运行全部差异项",
        )
    evidence_files: dict[uuid.UUID, FileObject] = {}
    for item_graph in graph.items:
        supplied_item = supplied_by_id[item_graph.item.id]
        if item_graph.binding.version != supplied_item.expected_version:
            _fail(
                "opening_reconciliation_item_version_conflict",
                "conflict",
                "对账差异项版本已变化，请重新读取",
            )
        if (
            supplied_item.evidence_file_id is not None
            and supplied_item.evidence_file_id not in evidence_files
        ):
            evidence_file = _validate_optional_file(
                db,
                supplied_item.evidence_file_id,
            )
            if evidence_file is None:
                _evidence_invalid("对账附件锁定结果为空")
            evidence_files[supplied_item.evidence_file_id] = evidence_file

    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    authorization = _authorize_actor(
        db,
        current,
        now=now,
        action="explain_opening",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(graph.task.region_org_id),
    )
    before = {
        "status": graph.run.status,
        "version": graph.binding.version,
        "item_versions": {
            str(row.item.id): row.binding.version for row in graph.items
        },
    }
    result = OpeningControlReconciliationExplainResult(
        reconciliation_run_id=graph.run.id,
        task_id=graph.task.id,
        status="differences",
        version=graph.binding.version + 1,
        explained_item_count=len(graph.items),
        explained_at=now,
    )
    result_document = _explain_result_document(result)
    command_row = _command_row(
        operation="explain_opening",
        run_id=graph.run.id,
        target_version=result.version,
        idempotency_key_hash=key_hash,
        request_reference=request_reference,
        request_document=request_document,
        result_document=result_document,
        actor=current,
        authorization=authorization,
        now=now,
    )
    # The append-only command is the authorization/idempotency cause of the
    # mutable projection update.  Persist it first so both PostgreSQL and the
    # immediate SQLite guard can reject an orphan projection transition.
    db.add(command_row)
    db.flush()

    transitions: list[tuple[str, uuid.UUID, str | None, str]] = []
    for item_graph in graph.items:
        supplied_item = supplied_by_id[item_graph.item.id]
        previous_status = item_graph.item.status
        item_graph.item.status = "explained"
        item_graph.item.explanation = supplied_item.explanation
        item_graph.item.evidence_file_id = supplied_item.evidence_file_id
        item_graph.item.updated_at = now
        db.flush((item_graph.item,))

        item_graph.binding.version += 1
        item_graph.binding.evidence_reference = supplied_item.evidence_reference
        evidence_file = (
            evidence_files.get(supplied_item.evidence_file_id)
            if supplied_item.evidence_file_id is not None
            else None
        )
        item_graph.binding.evidence_file_sha256 = (
            evidence_file.sha256 if evidence_file is not None else None
        )
        item_graph.binding.evidence_file_size_bytes = (
            evidence_file.size_bytes if evidence_file is not None else None
        )
        item_graph.binding.evidence_file_mime_type = (
            evidence_file.mime_type if evidence_file is not None else None
        )
        item_graph.binding.explained_by_user_id = current.user_id
        item_graph.binding.explained_by_person_id = current.person_id
        item_graph.binding.explained_role_assignment_id = authorization.assignment.id
        item_graph.binding.explanation_authorization_version = (
            current.authorization_version
        )
        item_graph.binding.explained_at = now
        item_graph.binding.updated_at = now
        db.flush((item_graph.binding,))
        if previous_status == "difference":
            transitions.append(
                (
                    "reconciliation_item",
                    item_graph.item.id,
                    "difference",
                    "explained",
                )
            )
    graph.binding.version += 1
    graph.binding.updated_at = now
    db.flush((graph.binding,))
    if graph.binding.version != result.version:
        _evidence_invalid("对账解释结果版本与投影推进不一致")
    _append_effects(
        db,
        operation="explain_opening",
        run=graph.run,
        actor=current,
        authorization=authorization,
        command_row=command_row,
        before=before,
        result=result_document,
        transitions=tuple(transitions),
        now=now,
    )
    _seal_command_consumption(db, command_row)
    _validate_run_graph_from_prelocked_reconciliation_graph(
        db,
        proof=reconciliation_proof,
        audit_proof=audit_proof,
    )
    return result


def _approve_opening_control_reconciliation(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: ApproveOpeningControlReconciliationCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningControlReconciliationApproveResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_approve_command(command)
    key = _require_idempotency_key(idempotency_key)
    request_reference = _request_reference("approve_opening", request_id)
    key_hash = _storage_hash("approve_opening", key)
    task_id = _task_id_for_run(db, checked.reconciliation_run_id)
    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-reconciliation-key", key_hash),
            _advisory_coordinate(
                "opening-reconciliation-task", str(task_id)
            ),
        ),
    )
    opening_root = _lock_opening_terminal_root_for_reconciliation(db, task_id)
    task = getattr(opening_root, "task", None)
    if not isinstance(task, FormalStocktakeTask) or task.id != task_id:
        _evidence_invalid("对账运行预读任务坐标与期初根锁不一致")
    principal_graph = _lock_opening_principal_graph_for_reconciliation(
        db,
        task.id,
        supplied_user_ids=(supplied.user_id,),
    )
    opening_proof = _lock_opening_terminal_graph_for_reconciliation(
        db,
        opening_root,
        principal_graph,
    )
    reconciliation_proof = (
        _lock_opening_control_reconciliation_graph_for_task(
            db,
            task_id=task_id,
            expected_run_id=checked.reconciliation_run_id,
            principal_graph=principal_graph,
        )
    )
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    _validate_prelocked_opening_terminal_for_reconciliation(
        db,
        opening_proof,
        audit_proof=audit_proof,
    )
    graph = _validate_run_graph_from_prelocked_reconciliation_graph(
        db,
        proof=reconciliation_proof,
        audit_proof=audit_proof,
    )
    if graph.task.id != task_id or graph.binding.task_id != task_id:
        _evidence_invalid("对账运行预读任务坐标与锁后正式绑定不一致")
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    authorization = _authorize_actor(
        db,
        current,
        now=now,
        action="approve_opening",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    )
    request_document = _approve_request_document(current, checked)
    request_hash = _hash_document(request_document)
    existing_command = _load_command_by_key(db, key_hash)
    if existing_command is not None:
        return _replay_approve(
            db,
            command_row=existing_command,
            request_hash=request_hash,
            expected_run_id=checked.reconciliation_run_id,
            prevalidated_graph=graph,
        )

    if graph.task.status != "posted" or graph.run.status != "differences":
        _fail(
            "opening_reconciliation_approve_state_invalid",
            "precondition_failed",
            "仅已过账且尚未批准的期初对账运行可批准",
        )
    if graph.binding.version != checked.expected_version:
        _fail(
            "opening_reconciliation_version_conflict",
            "conflict",
            "对账运行版本已变化，请重新读取",
        )
    if any(row.item.status != "explained" for row in graph.items):
        _fail(
            "opening_reconciliation_explanation_incomplete",
            "precondition_failed",
            "全部待核差异必须先由区域负责人提交原因和证据",
        )
    if any(row.binding.explained_by_user_id == current.user_id for row in graph.items):
        _fail(
            "opening_reconciliation_self_approval_forbidden",
            "forbidden",
            "对账解释提交人不得批准自己的解释",
        )

    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    authorization = _authorize_actor(
        db,
        current,
        now=now,
        action="approve_opening",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    )
    if any(row.binding.explained_by_user_id == current.user_id for row in graph.items):
        _fail(
            "opening_reconciliation_self_approval_forbidden",
            "forbidden",
            "对账解释提交人不得批准自己的解释",
        )
    before = {
        "status": graph.run.status,
        "version": graph.binding.version,
        "item_versions": {
            str(row.item.id): row.binding.version for row in graph.items
        },
    }
    result = OpeningControlReconciliationApproveResult(
        reconciliation_run_id=graph.run.id,
        task_id=graph.task.id,
        status="approved",
        version=graph.binding.version + 1,
        resolved_item_count=len(graph.items),
        approved_at=now,
    )
    result_document = _approve_result_document(result)
    command_row = _command_row(
        operation="approve_opening",
        run_id=graph.run.id,
        target_version=result.version,
        idempotency_key_hash=key_hash,
        request_reference=request_reference,
        request_document=request_document,
        result_document=result_document,
        actor=current,
        authorization=authorization,
        now=now,
    )
    db.add(command_row)
    db.flush()

    transitions: list[tuple[str, uuid.UUID, str | None, str]] = []
    for item_graph in graph.items:
        item_graph.item.status = "resolved"
        item_graph.item.updated_at = now
        db.flush((item_graph.item,))
        item_graph.binding.version += 1
        item_graph.binding.updated_at = now
        db.flush((item_graph.binding,))
        transitions.append(
            (
                "reconciliation_item",
                item_graph.item.id,
                "explained",
                "resolved",
            )
        )
    graph.binding.version += 1
    graph.binding.approved_by_user_id = current.user_id
    graph.binding.approved_by_person_id = current.person_id
    graph.binding.approved_role_assignment_id = authorization.assignment.id
    graph.binding.approved_authorization_version = current.authorization_version
    graph.binding.approved_at = now
    graph.binding.approval_comment = checked.comment
    graph.binding.updated_at = now
    if graph.binding.version != result.version:
        _evidence_invalid("对账批准结果版本与投影推进不一致")
    # Persist the item resolutions and approval extension before the parent
    # run enters its terminal status. The terminal database trigger then sees
    # the complete graph in both supported dialects.
    db.flush((graph.binding,))
    graph.run.status = "approved"
    graph.run.updated_at = now
    transitions.append(
        ("reconciliation_run", graph.run.id, "differences", "approved")
    )
    db.flush()
    _append_effects(
        db,
        operation="approve_opening",
        run=graph.run,
        actor=current,
        authorization=authorization,
        command_row=command_row,
        before=before,
        result=result_document,
        transitions=tuple(transitions),
        now=now,
    )
    _seal_command_consumption(db, command_row)
    _validate_run_graph_from_prelocked_reconciliation_graph(
        db,
        proof=reconciliation_proof,
        audit_proof=audit_proof,
    )
    return result


def _lock_and_validate_reconciliation_read_batch(
    db: Session,
    *,
    task_ids: Sequence[uuid.UUID],
    supplied_user_ids: Sequence[str] = (),
) -> tuple[
    dict[uuid.UUID, OpeningControlReconciliationStatus],
    dict[uuid.UUID, _RunGraph | None],
]:
    """Canonical strong-read boundary for terminal reconciliation tasks.

    The complete order is ledger -> UUID-sorted task rows -> one complete
    opening+reconciliation principal union -> opening evidence/reference/
    serial/balance -> reconciliation source/run/items/commands/files -> one
    inventory audit head -> plain opening and reconciliation proofs.
    """

    checked_task_ids = tuple(
        sorted(
            {_require_uuid("task_id", task_id) for task_id in task_ids},
            key=str,
        )
    )
    if not checked_task_ids:
        return {}, {}

    from . import opening_stocktake_finalize as finalize_service
    from .inventory_posting import (
        InventoryPostingError,
        _lock_opening_task_principal_graph,
    )

    try:
        root = finalize_service._lock_opening_terminal_task_batch_root(
            db,
            task_ids=checked_task_ids,
        )
        reconciliation_user_ids = (
            _opening_control_reconciliation_historical_user_ids(
                db,
                task_ids=checked_task_ids,
            )
        )
        principal_graph = (
            _lock_opening_task_principal_graph(
                db,
                task_ids=checked_task_ids,
                supplied_user_ids=tuple(
                    sorted(
                        set(supplied_user_ids).union(
                            reconciliation_user_ids
                        )
                    )
                ),
            )
        )
        opening_graph = (
            finalize_service._lock_opening_terminal_task_batch_graph(
                db,
                root=root,
                principal_graph=principal_graph,
            )
        )
    except (
        finalize_service.OpeningStocktakeFinalizeError,
        InventoryPostingError,
    ) as exc:
        _fail(
            "opening_reconciliation_source_evidence_invalid",
            "precondition_failed",
            "期初终态批量证据无法预锁，禁止读取强一致对账视图",
            cause=exc,
        )
    reconciliation_graph = _lock_opening_control_reconciliation_batch_graph(
        db,
        task_ids=checked_task_ids,
        principal_graph=principal_graph,
    )
    try:
        _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except AuditChainError as exc:
        _fail(
            "opening_reconciliation_audit_unavailable",
            "service_unavailable",
            "库存审计链不可用，无法读取强一致对账视图",
            cause=exc,
        )
    try:
        finalize_service._validate_opening_terminal_batch_from_prelocked_graph(
            db,
            proof=opening_graph,
            audit_proof=audit_proof,
            require_closed_task_ids=tuple(
                task.id for task in root.tasks if task.status == "closed"
            ),
        )
    except (
        finalize_service.OpeningStocktakeFinalizeError,
        InventoryPostingError,
    ) as exc:
        _fail(
            "opening_reconciliation_source_evidence_invalid",
            "precondition_failed",
            "期初终态批量证据无法纯重证，禁止读取强一致对账视图",
            cause=exc,
        )
    return _validate_opening_control_reconciliation_batch_from_prelocked_graph(
        db,
        proof=reconciliation_graph,
        audit_proof=audit_proof,
    )


def list_opening_control_reconciliations(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None = None,
) -> OpeningControlReconciliationPageOut:
    _require_read_actor(actor)
    if isinstance(limit, bool) or not 1 <= limit <= 20:
        _fail(
            "opening_reconciliation_page_limit_invalid",
            "invalid_request",
            "对账分页大小无效",
        )
    nationwide = _has_exact_action(
        db,
        actor,
        action="read",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    )
    region_org_ids: tuple[uuid.UUID, ...] = ()
    if not nationwide:
        region_org_ids = _readable_region_org_ids(actor)
        if not region_org_ids:
            return OpeningControlReconciliationPageOut(
                items=[],
                next_after_id=None,
            )
    statement = select(
        OpeningControlReconciliationRun.run_id,
        OpeningControlReconciliationRun.task_id,
    ).order_by(OpeningControlReconciliationRun.run_id)
    if not nationwide:
        statement = statement.where(
            OpeningControlReconciliationRun.region_org_id.in_(region_org_ids)
        )
    if after_id is not None:
        statement = statement.where(
            OpeningControlReconciliationRun.run_id > after_id
        )
    candidate_rows = tuple(db.execute(statement.limit(limit + 1)).all())
    page_rows = candidate_rows[:limit]
    page_ids = tuple(run_id for run_id, _task_id in page_rows)
    _statuses, graphs_by_task = _lock_and_validate_reconciliation_read_batch(
        db,
        task_ids=tuple(task_id for _run_id, task_id in page_rows),
        supplied_user_ids=(actor.user_id,),
    )
    visible: list[OpeningControlReconciliationSummaryOut] = []
    for run_id, task_id in page_rows:
        graph = graphs_by_task.get(task_id)
        if graph is None or graph.run.id != run_id:
            _evidence_invalid("对账列表候选坐标与预锁图不一致")
        if not _can_read_graph(db, actor, graph):
            _evidence_invalid("对账列表数据范围与授权过滤结果不一致")
        visible.append(_summary_out(db, actor, graph))
    has_more = len(candidate_rows) > limit
    return OpeningControlReconciliationPageOut(
        items=visible,
        next_after_id=(page_ids[-1] if has_more and page_ids else None),
    )


def opening_control_reconciliation_detail(
    db: Session,
    *,
    actor: FormalPrincipal,
    reconciliation_run_id: uuid.UUID,
) -> OpeningControlReconciliationDetailOut:
    _require_read_actor(actor)
    run_id = _require_uuid("reconciliation_run_id", reconciliation_run_id)
    task_id = _task_id_for_run(db, run_id)
    _statuses, graphs_by_task = _lock_and_validate_reconciliation_read_batch(
        db,
        task_ids=(task_id,),
        supplied_user_ids=(actor.user_id,),
    )
    graph = graphs_by_task.get(task_id)
    if graph is None or graph.run.id != run_id:
        _evidence_invalid("对账详情候选坐标与预锁图不一致")
    if not _can_read_graph(db, actor, graph):
        _fail(
            "opening_reconciliation_not_found",
            "not_found",
            "对账运行不存在或不在当前数据范围",
        )
    summary = _summary_out(db, actor, graph)
    return OpeningControlReconciliationDetailOut(
        **summary.model_dump(),
        source_system_id=graph.run.source_system_id,
        round_id=graph.round_row.id,
        posting_id=graph.posting.id,
        difference_manifest_sha256=graph.binding.item_manifest_sha256,
        approval_comment=graph.binding.approval_comment,
        items=[_item_out(row) for row in graph.items],
    )


def opening_control_reconciliation_statuses(
    db: Session,
    *,
    task_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, OpeningControlReconciliationStatus]:
    checked_ids = tuple(sorted({_require_uuid("task_id", row) for row in task_ids}, key=str))
    if not checked_ids:
        return {}
    statuses, _graphs = _lock_and_validate_reconciliation_read_batch(
        db,
        task_ids=checked_ids,
    )
    return statuses


def opening_control_reconciliation_is_approved(
    db: Session,
    *,
    task_id: uuid.UUID,
) -> bool:
    status = opening_control_reconciliation_statuses(db, task_ids=(task_id,))[
        task_id
    ]
    return status.status in {"not_required", "approved"}


def _checked_persisted_reconciliation_user_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 36
        or "\x00" in value
    ):
        _evidence_invalid("对账历史人员标识无效")
    return value


def _opening_control_reconciliation_historical_user_ids_by_task(
    db: Session,
    *,
    task_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, tuple[str, ...]]:
    """Plainly discover every persisted reconciliation authorization actor.

    The caller uses this only after the corresponding task rows are held and
    before taking the one complete principal graph.  It intentionally takes
    no advisory, owner-helper, row, or audit lock.
    """

    checked_task_ids = tuple(
        sorted(
            {_require_uuid("task_id", task_id) for task_id in task_ids},
            key=str,
        )
    )
    result: dict[uuid.UUID, set[str]] = {
        task_id: set() for task_id in checked_task_ids
    }
    if not checked_task_ids:
        return {}

    bindings = tuple(
        db.execute(
            select(
                OpeningControlReconciliationRun.task_id,
                OpeningControlReconciliationRun.run_id,
                OpeningControlReconciliationRun.created_by_user_id,
                OpeningControlReconciliationRun.approved_by_user_id,
            )
            .where(
                OpeningControlReconciliationRun.task_id.in_(
                    checked_task_ids
                )
            )
            .order_by(
                OpeningControlReconciliationRun.task_id,
                OpeningControlReconciliationRun.run_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    run_task_ids: dict[uuid.UUID, uuid.UUID] = {}
    for task_id, run_id, created_by, approved_by in bindings:
        prior_task_id = run_task_ids.setdefault(run_id, task_id)
        if prior_task_id != task_id:
            _evidence_invalid("对账运行绑定了多个期初任务")
        result[task_id].add(
            _checked_persisted_reconciliation_user_id(created_by)
        )
        if approved_by is not None:
            result[task_id].add(
                _checked_persisted_reconciliation_user_id(approved_by)
            )

    for task_id, explained_by in db.execute(
        select(
            OpeningControlReconciliationItem.task_id,
            OpeningControlReconciliationItem.explained_by_user_id,
        )
        .where(
            OpeningControlReconciliationItem.task_id.in_(checked_task_ids),
            OpeningControlReconciliationItem.explained_by_user_id.is_not(
                None
            ),
        )
        .order_by(
            OpeningControlReconciliationItem.task_id,
            OpeningControlReconciliationItem.item_id,
        )
        .execution_options(populate_existing=True)
    ).all():
        result[task_id].add(
            _checked_persisted_reconciliation_user_id(explained_by)
        )

    if run_task_ids:
        for run_id, actor_user_id in db.execute(
            select(
                ReconciliationCommand.run_id,
                ReconciliationCommand.actor_user_id,
            )
            .where(ReconciliationCommand.run_id.in_(tuple(run_task_ids)))
            .order_by(
                ReconciliationCommand.run_id,
                ReconciliationCommand.id,
            )
            .execution_options(populate_existing=True)
        ).all():
            result[run_task_ids[run_id]].add(
                _checked_persisted_reconciliation_user_id(actor_user_id)
            )

    return {
        task_id: tuple(sorted(user_ids))
        for task_id, user_ids in result.items()
    }


def _opening_control_reconciliation_historical_user_ids(
    db: Session,
    *,
    task_ids: Sequence[uuid.UUID],
) -> tuple[str, ...]:
    """Return the sorted task-union for a caller's one principal lock."""

    by_task = _opening_control_reconciliation_historical_user_ids_by_task(
        db,
        task_ids=task_ids,
    )
    return tuple(
        sorted(
            {
                user_id
                for user_ids in by_task.values()
                for user_id in user_ids
            }
        )
    )


def _lock_opening_control_reconciliation_graph_for_task(
    db: Session,
    *,
    task_id: uuid.UUID,
    expected_run_id: uuid.UUID | None = None,
    extra_evidence_file_ids: Sequence[uuid.UUID] = (),
    principal_graph: object | None = None,
    _preplanned_historical_reconciliation_user_ids: tuple[str, ...]
    | None = None,
) -> _PrelockedOpeningControlReconciliationGraph:
    """Lock only one task's reconciliation graph in canonical owner order.

    Strong precondition: the caller already owns the mutable task row and the
    complete opening task/evidence/reference/serial/balance graph.  This helper
    then locks reconciliation source -> mutable run/binding/items -> immutable
    commands/seals -> files.  It deliberately does not touch the audit head.
    The caller must take that single final shared lock before invoking the pure
    validator below.
    """

    checked_task_id = _require_uuid("task_id", task_id)
    checked_expected_run_id = (
        _require_uuid("reconciliation_run_id", expected_run_id)
        if expected_run_id is not None
        else None
    )
    checked_extra_file_ids = tuple(
        sorted(
            {
                _require_uuid("evidence_file_id", file_id)
                for file_id in extra_evidence_file_ids
            },
            key=str,
        )
    )
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_reconciliation_prelocked_graph_transaction_required",
            "precondition_failed",
            "对账预锁图必须绑定活动事务",
        )

    historical_reconciliation_user_ids = (
        _opening_control_reconciliation_historical_user_ids(
            db,
            task_ids=(checked_task_id,),
        )
        if _preplanned_historical_reconciliation_user_ids is None
        else tuple(
            sorted(
                {
                    _checked_persisted_reconciliation_user_id(user_id)
                    for user_id in (
                        _preplanned_historical_reconciliation_user_ids
                    )
                }
            )
        )
    )
    allowed_reconciliation_user_ids = historical_reconciliation_user_ids
    if principal_graph is not None:
        if isinstance(
            principal_graph,
            _PrelockedOpeningReconciliationPrincipalGraph,
        ):
            checked_principal_graph = (
                _require_prelocked_reconciliation_principal_graph(
                    db,
                    task_id=checked_task_id,
                    proof=principal_graph,
                )
            )
            if (
                checked_principal_graph.historical_reconciliation_user_ids
                != historical_reconciliation_user_ids
            ):
                _evidence_invalid(
                    "对账历史人员集合在人员图锁定与对账图规划之间发生变化"
                )
            allowed_reconciliation_user_ids = (
                checked_principal_graph.allowed_reconciliation_user_ids
            )
        else:
            from .inventory_posting import (
                InventoryPostingError,
                _require_opening_task_principal_graph_proof,
            )

            try:
                checked_opening_principal_graph = (
                    _require_opening_task_principal_graph_proof(
                        db,
                        principal_graph,
                        required_task_ids=(checked_task_id,),
                    )
                )
            except InventoryPostingError as exc:
                _fail(
                    "opening_reconciliation_prelocked_principal_graph_invalid",
                    "precondition_failed",
                    "对账人员预锁图证明无效",
                    cause=exc,
                )
            if not set(historical_reconciliation_user_ids).issubset(
                checked_opening_principal_graph.user_ids
            ):
                _fail(
                    "opening_reconciliation_prelocked_principal_graph_incomplete",
                    "precondition_failed",
                    "对账人员预锁图未覆盖全部历史授权人员",
                )

    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked_task_id)
        .execution_options(populate_existing=True)
    )
    if task is None:
        _fail(
            "opening_reconciliation_task_not_found",
            "not_found",
            "期初任务不存在",
        )
    if task.task_type != "opening":
        _evidence_invalid("对账图预锁包含非期初盘点任务")
    round_rows = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(
                StocktakeRound.task_id == task.id,
                StocktakeRound.round_no == task.current_round_no,
            )
            .order_by(StocktakeRound.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(round_rows) != 1:
        _evidence_invalid("期初任务当前轮次不唯一")
    round_id = round_rows[0].id
    binding_rows = tuple(
        db.scalars(
            select(OpeningControlReconciliationRun)
            .where(OpeningControlReconciliationRun.task_id == task.id)
            .order_by(OpeningControlReconciliationRun.run_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(binding_rows) > 1:
        _evidence_invalid("期初任务绑定了多个对账运行")
    binding = binding_rows[0] if binding_rows else None
    run_id = binding.run_id if binding is not None else None
    if checked_expected_run_id is not None and run_id != checked_expected_run_id:
        _fail(
            "opening_reconciliation_not_found",
            "not_found",
            "对账运行不存在",
        )

    item_ids: tuple[uuid.UUID, ...] = ()
    evidence_file_ids: tuple[uuid.UUID, ...] = checked_extra_file_ids
    if run_id is not None:
        _take_advisory_locks(
            db,
            (
                _advisory_coordinate(
                    "opening-reconciliation-run", str(run_id)
                ),
            ),
        )
    # The source exists before the first reconciliation run.  Lock it for both
    # create and existing-run paths after the opening graph and before any
    # reconciliation projection row.  The 0026 run helper below only re-enters
    # this exact task-local source subset.
    _lock_readonly_source(db, task.id)
    if run_id is not None:
        run = db.scalar(
            select(ReconciliationRun)
            .where(ReconciliationRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        locked_binding = db.scalar(
            select(OpeningControlReconciliationRun)
            .where(OpeningControlReconciliationRun.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            run is None
            or locked_binding is None
            or locked_binding.task_id != task.id
            or locked_binding.round_id != round_id
        ):
            _evidence_invalid("对账运行坐标在锁定期间发生变化")
        _lock_readonly_run_graph(db, run_id)

        generic_item_ids = tuple(
            db.scalars(
                select(ReconciliationItem.id)
                .where(ReconciliationItem.run_id == run_id)
                .order_by(ReconciliationItem.id)
                .with_for_update()
            ).all()
        )
        extension_item_ids = tuple(
            db.scalars(
                select(OpeningControlReconciliationItem.item_id)
                .where(OpeningControlReconciliationItem.run_id == run_id)
                .order_by(OpeningControlReconciliationItem.item_id)
                .with_for_update()
            ).all()
        )
        if generic_item_ids != extension_item_ids:
            _evidence_invalid("对账通用明细与正式扩展明细集合不一致")
        item_ids = generic_item_ids
        current_file_ids = tuple(
            db.scalars(
                select(ReconciliationItem.evidence_file_id)
                .where(
                    ReconciliationItem.run_id == run_id,
                    ReconciliationItem.evidence_file_id.is_not(None),
                )
                .order_by(ReconciliationItem.evidence_file_id)
            ).all()
        )
        evidence_file_ids = tuple(
            sorted(
                set(current_file_ids).union(checked_extra_file_ids),
                key=str,
            )
        )
        _lock_readonly_files(db, run_id, checked_extra_file_ids)
        locked_file_ids = tuple(
            db.scalars(
                select(FileObject.id)
                .where(FileObject.id.in_(evidence_file_ids))
                .order_by(FileObject.id)
                .execution_options(populate_existing=True)
            ).all()
            if evidence_file_ids
            else ()
        )
        if locked_file_ids != evidence_file_ids:
            _evidence_invalid("对账附件证据集合在锁定期间发生变化")

    pending_count = int(
        db.scalar(
            select(func.count()).select_from(StocktakeDifference).where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == round_id,
                StocktakeDifference.difference_type == "control_unassigned",
            )
        )
        or 0
    )
    return _PrelockedOpeningControlReconciliationGraph(
        session=db,
        transaction=transaction,
        task_id=task.id,
        round_id=round_id,
        run_id=run_id,
        pending_control_difference_count=pending_count,
        item_ids=item_ids,
        evidence_file_ids=evidence_file_ids,
        historical_reconciliation_user_ids=(
            historical_reconciliation_user_ids
        ),
        allowed_reconciliation_user_ids=allowed_reconciliation_user_ids,
        seal=_PRELOCKED_RECONCILIATION_GRAPH_SEAL,
    )


def _lock_opening_control_reconciliation_batch_graph(
    db: Session,
    *,
    task_ids: Sequence[uuid.UUID],
    principal_graph: object,
) -> _PrelockedOpeningControlReconciliationBatch:
    """Lock a task-sorted reconciliation batch and stop before audit.

    Strong preconditions: the caller already owns the ledger/task roots, the
    complete principal union (including
    :func:`_opening_control_reconciliation_historical_user_ids`), and every
    opening evidence/reference/serial/balance graph for ``task_ids``.  Tasks
    are expanded in UUID order as reconciliation source -> run/binding/items
    -> commands/seals -> files.  No opening or audit helper is called here.
    """

    checked_task_ids = tuple(
        sorted(
            {_require_uuid("task_id", task_id) for task_id in task_ids},
            key=str,
        )
    )
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_reconciliation_prelocked_batch_transaction_required",
            "precondition_failed",
            "对账批量预锁图必须绑定活动事务",
        )
    from .inventory_posting import (
        InventoryPostingError,
        _require_opening_task_principal_graph_proof,
    )

    try:
        checked_principal_graph = (
            _require_opening_task_principal_graph_proof(
                db,
                principal_graph,
                required_task_ids=checked_task_ids,
            )
        )
    except InventoryPostingError as exc:
        _fail(
            "opening_reconciliation_prelocked_principal_graph_invalid",
            "precondition_failed",
            "对账批量人员预锁图证明无效",
            cause=exc,
        )
    historical_by_task = (
        _opening_control_reconciliation_historical_user_ids_by_task(
            db,
            task_ids=checked_task_ids,
        )
    )
    historical_user_ids = {
        user_id
        for user_ids in historical_by_task.values()
        for user_id in user_ids
    }
    if not historical_user_ids.issubset(checked_principal_graph.user_ids):
        _fail(
            "opening_reconciliation_prelocked_principal_graph_incomplete",
            "precondition_failed",
            "对账批量人员预锁图未覆盖全部历史授权人员",
        )
    graphs = tuple(
        _lock_opening_control_reconciliation_graph_for_task(
            db,
            task_id=task_id,
            _preplanned_historical_reconciliation_user_ids=(
                historical_by_task[task_id]
            ),
        )
        for task_id in checked_task_ids
    )
    return _PrelockedOpeningControlReconciliationBatch(
        session=db,
        transaction=transaction,
        task_ids=checked_task_ids,
        graphs=graphs,
        principal_graph=checked_principal_graph,
        seal=_PRELOCKED_RECONCILIATION_BATCH_SEAL,
    )


def _require_prelocked_reconciliation_graph_proof(
    db: Session,
    *,
    task_id: uuid.UUID,
    proof: object,
) -> _PrelockedOpeningControlReconciliationGraph:
    checked_task_id = _require_uuid("task_id", task_id)
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedOpeningControlReconciliationGraph)
        or proof.seal is not _PRELOCKED_RECONCILIATION_GRAPH_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or proof.task_id != checked_task_id
    ):
        _fail(
            "opening_reconciliation_prelocked_graph_proof_invalid",
            "precondition_failed",
            "对账预锁图证明与当前事务或任务不一致",
        )
    return proof


def _require_prelocked_reconciliation_principal_graph(
    db: Session,
    *,
    task_id: uuid.UUID,
    proof: object,
) -> _PrelockedOpeningReconciliationPrincipalGraph:
    checked_task_id = _require_uuid("task_id", task_id)
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedOpeningReconciliationPrincipalGraph)
        or proof.seal is not _PRELOCKED_RECONCILIATION_PRINCIPAL_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or proof.task_id != checked_task_id
        or not set(proof.historical_reconciliation_user_ids).issubset(
            proof.allowed_reconciliation_user_ids
        )
    ):
        _fail(
            "opening_reconciliation_prelocked_principal_graph_invalid",
            "precondition_failed",
            "对账人员预锁图证明与当前事务或任务不一致",
        )
    return proof


def _validate_reconciliation_actor_coverage_from_prelocked_graph(
    db: Session,
    *,
    proof: _PrelockedOpeningControlReconciliationGraph,
    _current_reconciliation_user_ids: tuple[str, ...] | None = None,
) -> None:
    current = set(
        _opening_control_reconciliation_historical_user_ids(
            db,
            task_ids=(proof.task_id,),
        )
        if _current_reconciliation_user_ids is None
        else _current_reconciliation_user_ids
    )
    historical = set(proof.historical_reconciliation_user_ids)
    allowed = set(proof.allowed_reconciliation_user_ids)
    if not historical.issubset(current) or not current.issubset(allowed):
        _evidence_invalid(
            "对账历史人员集合在预锁与纯重证之间发生扩展、缩减或替换"
        )


def _require_prelocked_reconciliation_batch_proof(
    db: Session,
    *,
    proof: object,
) -> _PrelockedOpeningControlReconciliationBatch:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedOpeningControlReconciliationBatch)
        or proof.seal is not _PRELOCKED_RECONCILIATION_BATCH_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or proof.task_ids
        != tuple(sorted(set(proof.task_ids), key=str))
        or tuple(graph.task_id for graph in proof.graphs) != proof.task_ids
        or proof.principal_graph is None
    ):
        _fail(
            "opening_reconciliation_prelocked_batch_proof_invalid",
            "precondition_failed",
            "对账批量预锁图证明与当前事务或任务集合不一致",
        )
    return proof


def _require_reconciliation_audit_proof(
    db: Session,
    *,
    audit_proof: object,
) -> None:
    """Fail closed unless the inventory audit head belongs to this transaction."""

    try:
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except AuditChainError as exc:
        _evidence_invalid(
            "对账审计证明不属于当前事务或库存审计流",
            cause=exc,
        )


def _validate_run_graph_from_prelocked_reconciliation_graph(
    db: Session,
    *,
    proof: object,
    audit_proof: object,
    _current_reconciliation_user_ids: tuple[str, ...] | None = None,
) -> _RunGraph:
    """Purely re-read one already locked reconciliation graph.

    Strong preconditions: the caller has retained the exact opening graph,
    this reconciliation graph and the inventory audit head in the current
    transaction.  This function performs no owner-helper call and emits no
    ``FOR UPDATE`` clause.
    """

    _require_reconciliation_audit_proof(db, audit_proof=audit_proof)
    if not isinstance(proof, _PrelockedOpeningControlReconciliationGraph):
        _fail(
            "opening_reconciliation_prelocked_graph_proof_invalid",
            "precondition_failed",
            "对账预锁图证明无效",
        )
    checked = _require_prelocked_reconciliation_graph_proof(
        db,
        task_id=proof.task_id,
        proof=proof,
    )
    _validate_reconciliation_actor_coverage_from_prelocked_graph(
        db,
        proof=checked,
        _current_reconciliation_user_ids=(
            _current_reconciliation_user_ids
        ),
    )
    if checked.run_id is None:
        _fail(
            "opening_reconciliation_pending",
            "precondition_failed",
            "期初任务仍有待核实 OAM 控制差异",
        )
    graph = _validate_run_graph(
        db,
        checked.run_id,
        lock=False,
        audit_proof=audit_proof,
    )
    current_file_ids = tuple(
        sorted(
            {
                row.item.evidence_file_id
                for row in graph.items
                if row.item.evidence_file_id is not None
            },
            key=str,
        )
    )
    if (
        graph.task.id != checked.task_id
        or graph.round_row.id != checked.round_id
        or tuple(row.item.id for row in graph.items) != checked.item_ids
        or not set(current_file_ids).issubset(checked.evidence_file_ids)
    ):
        _evidence_invalid("对账图在预锁与纯重证之间发生扩展或漂移")
    return graph


def _validate_opening_control_reconciliation_batch_from_prelocked_graph(
    db: Session,
    *,
    proof: object,
    audit_proof: object,
) -> tuple[
    dict[uuid.UUID, OpeningControlReconciliationStatus],
    dict[uuid.UUID, _RunGraph | None],
]:
    """Purely re-read statuses and run graphs after the final audit lock.

    No advisory, owner helper, opening replay, audit helper, or ``FOR UPDATE``
    is issued.  The task/round/difference/binding coordinates and every
    reconciliation actor, item and file set must remain exactly covered by the
    transaction-bound batch proof.
    """

    _require_reconciliation_audit_proof(db, audit_proof=audit_proof)
    checked = _require_prelocked_reconciliation_batch_proof(db, proof=proof)
    from .inventory_posting import (
        InventoryPostingError,
        _validate_prelocked_opening_task_principal_graph,
    )

    try:
        _validate_prelocked_opening_task_principal_graph(
            db,
            proof=checked.principal_graph,
        )
    except InventoryPostingError as exc:
        _fail(
            "opening_reconciliation_prelocked_principal_graph_changed",
            "precondition_failed",
            "对账批量人员预锁图在纯重证前发生变化",
            cause=exc,
        )
    current_actor_ids_by_task = (
        _opening_control_reconciliation_historical_user_ids_by_task(
            db,
            task_ids=checked.task_ids,
        )
    )
    result: dict[uuid.UUID, OpeningControlReconciliationStatus] = {}
    run_graphs: dict[uuid.UUID, _RunGraph | None] = {}
    closed_task_ids: set[uuid.UUID] = set()
    for graph_proof in checked.graphs:
        current_task = db.scalar(
            select(FormalStocktakeTask)
            .where(FormalStocktakeTask.id == graph_proof.task_id)
            .execution_options(populate_existing=True)
        )
        if current_task is None:
            _fail(
                "opening_reconciliation_task_not_found",
                "not_found",
                "期初任务不存在",
            )
        if current_task.task_type != "opening":
            _evidence_invalid("对账状态批量重证包含非期初盘点任务")
        if current_task.status == "closed":
            closed_task_ids.add(current_task.id)
        current_round_ids = tuple(
            db.scalars(
                select(StocktakeRound.id)
                .where(
                    StocktakeRound.task_id == current_task.id,
                    StocktakeRound.round_no
                    == current_task.current_round_no,
                )
                .order_by(StocktakeRound.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        current_binding_run_ids = tuple(
            db.scalars(
                select(OpeningControlReconciliationRun.run_id)
                .where(
                    OpeningControlReconciliationRun.task_id
                    == current_task.id
                )
                .order_by(OpeningControlReconciliationRun.run_id)
                .execution_options(populate_existing=True)
            ).all()
        )
        current_pending_count = int(
            db.scalar(
                select(func.count())
                .select_from(StocktakeDifference)
                .where(
                    StocktakeDifference.task_id == current_task.id,
                    StocktakeDifference.round_id == graph_proof.round_id,
                    StocktakeDifference.difference_type
                    == "control_unassigned",
                )
            )
            or 0
        )
        if (
            current_round_ids != (graph_proof.round_id,)
            or current_binding_run_ids
            != (() if graph_proof.run_id is None else (graph_proof.run_id,))
            or current_pending_count
            != graph_proof.pending_control_difference_count
        ):
            _evidence_invalid(
                "对账批量状态坐标在预锁与纯重证之间发生扩展或漂移"
            )

        pending_count = graph_proof.pending_control_difference_count
        if pending_count == 0:
            if graph_proof.run_id is not None:
                _validate_run_graph_from_prelocked_reconciliation_graph(
                    db,
                    proof=graph_proof,
                    audit_proof=audit_proof,
                    _current_reconciliation_user_ids=(
                        current_actor_ids_by_task[current_task.id]
                    ),
                )
                _evidence_invalid("无待核控制差异的任务存在异常对账运行")
            _validate_reconciliation_actor_coverage_from_prelocked_graph(
                db,
                proof=graph_proof,
                _current_reconciliation_user_ids=(
                    current_actor_ids_by_task[current_task.id]
                ),
            )
            result[current_task.id] = OpeningControlReconciliationStatus(
                status="not_required",
                reconciliation_run_id=None,
                pending_control_difference_count=0,
            )
            run_graphs[current_task.id] = None
            continue
        if graph_proof.run_id is None:
            _validate_reconciliation_actor_coverage_from_prelocked_graph(
                db,
                proof=graph_proof,
                _current_reconciliation_user_ids=(
                    current_actor_ids_by_task[current_task.id]
                ),
            )
            if graph_proof.item_ids or graph_proof.evidence_file_ids:
                _evidence_invalid("无对账运行的任务存在异常对账投影坐标")
            result[current_task.id] = OpeningControlReconciliationStatus(
                status="pending",
                reconciliation_run_id=None,
                pending_control_difference_count=pending_count,
            )
            run_graphs[current_task.id] = None
            continue
        graph = _validate_run_graph_from_prelocked_reconciliation_graph(
            db,
            proof=graph_proof,
            audit_proof=audit_proof,
            _current_reconciliation_user_ids=(
                current_actor_ids_by_task[current_task.id]
            ),
        )
        if len(graph.items) != pending_count:
            _evidence_invalid("对账运行未精确覆盖全部待核控制差异")
        approved = graph.run.status == "approved"
        result[current_task.id] = OpeningControlReconciliationStatus(
            status="approved" if approved else "pending",
            reconciliation_run_id=graph.run.id,
            pending_control_difference_count=0 if approved else pending_count,
        )
        run_graphs[current_task.id] = graph
    if set(result) != set(checked.task_ids):
        _evidence_invalid("对账批量状态结果没有精确覆盖全部预锁任务")
    if any(
        result[task_id].status not in {"not_required", "approved"}
        for task_id in closed_task_ids
    ):
        _evidence_invalid(
            "已关闭期初任务存在未完成或缺失的独立控制账对账"
        )
    return result, run_graphs


def _opening_control_reconciliation_statuses_from_prelocked_batch(
    db: Session,
    *,
    proof: object,
    audit_proof: object,
) -> dict[uuid.UUID, OpeningControlReconciliationStatus]:
    """Return only statuses from the sealed post-audit batch proof."""

    statuses, _run_graphs = (
        _validate_opening_control_reconciliation_batch_from_prelocked_graph(
            db,
            proof=proof,
            audit_proof=audit_proof,
        )
    )
    return statuses


def _require_approved_opening_control_reconciliation_from_prelocked_graph(
    db: Session,
    *,
    task_id: uuid.UUID,
    proof: object,
    audit_proof: object,
) -> OpeningControlReconciliationProof:
    """Internal close proof after opening/reconciliation/audit locks are held."""

    _require_reconciliation_audit_proof(db, audit_proof=audit_proof)
    checked = _require_prelocked_reconciliation_graph_proof(
        db,
        task_id=task_id,
        proof=proof,
    )
    if checked.pending_control_difference_count == 0:
        if checked.run_id is not None:
            _validate_run_graph_from_prelocked_reconciliation_graph(
                db,
                proof=checked,
                audit_proof=audit_proof,
            )
            _evidence_invalid("无待核控制差异的任务存在异常对账运行")
        return OpeningControlReconciliationProof(
            status="not_required",
            reconciliation_run_id=None,
            task_id=checked.task_id,
            pending_control_difference_count=0,
            approved_at=None,
        )
    if checked.run_id is None:
        _fail(
            "opening_reconciliation_pending",
            "precondition_failed",
            "期初任务仍有待核实 OAM 控制差异",
        )
    graph = _validate_run_graph_from_prelocked_reconciliation_graph(
        db,
        proof=checked,
        audit_proof=audit_proof,
    )
    if (
        graph.run.status != "approved"
        or graph.binding.approved_at is None
        or len(graph.items) != checked.pending_control_difference_count
    ):
        _fail(
            "opening_reconciliation_pending",
            "precondition_failed",
            "期初任务仍有待核实 OAM 控制差异",
        )
    return OpeningControlReconciliationProof(
        status="approved",
        reconciliation_run_id=graph.run.id,
        task_id=checked.task_id,
        pending_control_difference_count=0,
        approved_at=_as_utc(graph.binding.approved_at),
    )


def require_approved_opening_control_reconciliation(
    db: Session,
    *,
    task_id: uuid.UUID,
) -> OpeningControlReconciliationProof:
    checked_task_id = _require_uuid("task_id", task_id)
    statuses, graphs = _lock_and_validate_reconciliation_read_batch(
        db,
        task_ids=(checked_task_id,),
    )
    status = statuses[checked_task_id]
    if status.status == "not_required":
        return OpeningControlReconciliationProof(
            status="not_required",
            reconciliation_run_id=None,
            task_id=checked_task_id,
            pending_control_difference_count=0,
            approved_at=None,
        )
    if status.status != "approved" or status.reconciliation_run_id is None:
        _fail(
            "opening_reconciliation_pending",
            "precondition_failed",
            "期初任务仍有待核实 OAM 控制差异",
        )
    graph = graphs.get(checked_task_id)
    if graph is None or graph.run.id != status.reconciliation_run_id:
        _evidence_invalid("已批准对账状态与预锁运行图不一致")
    if graph.binding.approved_at is None:
        _evidence_invalid("已批准对账运行缺少批准时间")
    return OpeningControlReconciliationProof(
        status="approved",
        reconciliation_run_id=graph.run.id,
        task_id=checked_task_id,
        pending_control_difference_count=0,
        approved_at=_as_utc(graph.binding.approved_at),
    )


def _validate_run_graph(
    db: Session,
    run_id: uuid.UUID,
    *,
    lock: bool,
    extra_evidence_file_ids: tuple[uuid.UUID, ...] = (),
    audit_proof: object,
) -> _RunGraph:
    if lock:
        _fail(
            "opening_reconciliation_internal_lock_contract_invalid",
            "precondition_failed",
            "对账图验证器只允许普通重读；写路径必须使用预锁图契约",
        )
    if extra_evidence_file_ids:
        _fail(
            "opening_reconciliation_internal_lock_contract_invalid",
            "precondition_failed",
            "对账候选附件只能由预锁图规划器处理",
        )
    _require_reconciliation_audit_proof(db, audit_proof=audit_proof)
    checked_run_id = _require_uuid("reconciliation_run_id", run_id)
    run_statement = select(ReconciliationRun).where(ReconciliationRun.id == checked_run_id)
    binding_statement = select(OpeningControlReconciliationRun).where(
        OpeningControlReconciliationRun.run_id == checked_run_id
    )
    run = db.scalar(run_statement.execution_options(populate_existing=True))
    binding = db.scalar(binding_statement.execution_options(populate_existing=True))
    if run is None or binding is None:
        _fail(
            "opening_reconciliation_not_found",
            "not_found",
            "对账运行不存在",
        )
    task = db.get(FormalStocktakeTask, binding.task_id)
    if task is None:
        _evidence_invalid("对账运行绑定的期初任务不存在")
    round_row, posting, establishments, differences, controls = (
        _load_posted_source_graph(db, task, lock=False)
    )
    if round_row.id != binding.round_id or posting.id != binding.posting_id:
        _evidence_invalid("对账运行与期初轮次或过账事实不一致")
    if (
        binding.region_org_id != task.region_org_id
        or binding.control_sync_run_id != task.control_sync_run_id
        or run.source_system_id != task.control_source_system_id
        or _as_utc(run.external_snapshot_at) != _as_utc(task.control_snapshot_at)
    ):
        _evidence_invalid("对账运行与期初控制来源锚点不一致")
    ledger_cursors = {row.established_ledger_cursor for row in establishments}
    if len(ledger_cursors) != 1 or run.local_ledger_cursor != str(
        next(iter(ledger_cursors))
    ):
        _evidence_invalid("对账运行与期初本地账本游标不一致")
    expected_manifest = _item_manifest_sha256(
        task_id=task.id,
        round_id=round_row.id,
        differences=differences,
        controls=controls,
    )
    if (
        not differences
        or binding.item_count != len(differences)
        or binding.item_manifest_sha256 != expected_manifest
        or run.summary_jsonb
        != {
            "schema": "cloud_oam.opening_control_reconciliation.summary.v1",
            "task_id": str(task.id),
            "round_id": str(round_row.id),
            "item_count": len(differences),
            "item_manifest_sha256": expected_manifest,
        }
        or run.status not in {"differences", "approved"}
        or binding.version < 0
    ):
        _evidence_invalid("对账运行摘要或状态与待核差异集合不一致")

    item_statement = (
        select(ReconciliationItem, OpeningControlReconciliationItem)
        .join(
            OpeningControlReconciliationItem,
            OpeningControlReconciliationItem.item_id == ReconciliationItem.id,
        )
        .where(ReconciliationItem.run_id == run.id)
        .order_by(ReconciliationItem.id)
    )
    pairs = tuple(db.execute(item_statement.execution_options(populate_existing=True)).all())
    generic_id_statement = (
        select(ReconciliationItem.id)
        .where(ReconciliationItem.run_id == run.id)
        .order_by(ReconciliationItem.id)
    )
    extension_id_statement = (
        select(OpeningControlReconciliationItem.item_id)
        .where(OpeningControlReconciliationItem.run_id == run.id)
        .order_by(OpeningControlReconciliationItem.item_id)
    )
    generic_item_ids = tuple(db.scalars(generic_id_statement).all())
    extension_item_ids = tuple(db.scalars(extension_id_statement).all())
    joined_item_ids = tuple(item.id for item, _binding in pairs)
    if (
        generic_item_ids != joined_item_ids
        or extension_item_ids != joined_item_ids
    ):
        _evidence_invalid("对账通用明细与正式扩展明细集合不一致")

    current_evidence_ids = {
        item.evidence_file_id
        for item, _binding in pairs
        if item.evidence_file_id is not None
    }
    evidence_ids_to_lock = tuple(sorted(current_evidence_ids, key=str))
    if evidence_ids_to_lock:
        locked_evidence_ids = set(
            db.scalars(
                select(FileObject.id)
                .where(FileObject.id.in_(evidence_ids_to_lock))
                .order_by(FileObject.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if not current_evidence_ids.issubset(locked_evidence_ids):
            _evidence_invalid("对账投影引用的附件证据缺失")
    controls_by_id = {row.id: row for row in controls}
    differences_by_id = {row.id: row for row in differences}
    item_graphs: list[_ItemGraph] = []
    seen_differences: set[uuid.UUID] = set()
    for item, item_binding in pairs:
        difference = differences_by_id.get(item_binding.difference_id)
        control = controls_by_id.get(item_binding.control_snapshot_line_id)
        if (
            difference is None
            or control is None
            or item_binding.run_id != run.id
            or item_binding.task_id != task.id
            or item_binding.round_id != round_row.id
            or difference.control_snapshot_line_id != control.id
            or difference.book_qty != control.control_qty
            or difference.material_id != control.material_id
            or item.business_key != control.external_business_key
            or item.external_qty != difference.book_qty
            or item.local_qty != difference.counted_qty
            or item.difference != difference.book_qty - difference.counted_qty
            or item.difference == _ZERO
            or item_binding.version < 0
            or difference.id in seen_differences
        ):
            _evidence_invalid("对账差异项与期初控制差异不一致")
        seen_differences.add(difference.id)
        _validate_item_state(db, run=run, binding=binding, item=item, item_binding=item_binding)
        item_graphs.append(
            _ItemGraph(
                item=item,
                binding=item_binding,
                difference=difference,
                control_line=control,
            )
        )
    if seen_differences != set(differences_by_id) or len(item_graphs) != binding.item_count:
        _evidence_invalid("对账运行未精确覆盖全部待核控制差异")

    command_statement = (
        select(ReconciliationCommand)
        .where(ReconciliationCommand.run_id == run.id)
        .order_by(ReconciliationCommand.target_version)
    )
    consumption_statement = (
        select(OpeningControlReconciliationCommandConsumption)
        .where(OpeningControlReconciliationCommandConsumption.run_id == run.id)
        .order_by(OpeningControlReconciliationCommandConsumption.target_version)
    )
    commands = tuple(db.scalars(command_statement).all())
    consumptions = tuple(db.scalars(consumption_statement).all())
    _validate_command_chain(
        run,
        binding,
        task,
        tuple(item_graphs),
        commands,
        consumptions,
    )
    _validate_command_effects(
        db,
        run=run,
        binding=binding,
        items=tuple(item_graphs),
        commands=commands,
        audit_proof=audit_proof,
    )
    return _RunGraph(
        run=run,
        binding=binding,
        task=task,
        round_row=round_row,
        posting=posting,
        establishments=establishments,
        items=tuple(item_graphs),
        commands=commands,
        consumptions=consumptions,
    )


def _validate_item_state(
    db: Session,
    *,
    run: ReconciliationRun,
    binding: OpeningControlReconciliationRun,
    item: ReconciliationItem,
    item_binding: OpeningControlReconciliationItem,
) -> None:
    explanation_fields = (
        item_binding.explained_by_user_id,
        item_binding.explained_by_person_id,
        item_binding.explained_role_assignment_id,
        item_binding.explanation_authorization_version,
        item_binding.explained_at,
    )
    evidence_snapshot = (
        item_binding.evidence_file_sha256,
        item_binding.evidence_file_size_bytes,
        item_binding.evidence_file_mime_type,
    )
    if item.status == "difference":
        if (
            run.status != "differences"
            or item.explanation != ""
            or item.evidence_file_id is not None
            or item_binding.evidence_reference != ""
            or any(value is not None for value in evidence_snapshot)
            or any(value is not None for value in explanation_fields)
            or item_binding.version != 0
        ):
            _evidence_invalid("未解释对账项携带了伪造的处置事实")
        return
    if item.status not in {"explained", "resolved"}:
        _evidence_invalid("正式期初对账项状态无效")
    if (
        any(value is None for value in explanation_fields)
        or not item.explanation.strip()
        or not item_binding.evidence_reference.strip()
        or item.explanation != item.explanation.strip()
        or item_binding.evidence_reference != item_binding.evidence_reference.strip()
        or item_binding.version < 1
        or _as_utc(item_binding.explained_at) < _as_utc(binding.created_at)
    ):
        _evidence_invalid("对账项缺少完整原因、证据或区域解释人快照")
    evidence_file = _validate_optional_file(
        db,
        item.evidence_file_id,
        evidence=True,
    )
    if item.evidence_file_id is None:
        if any(value is not None for value in evidence_snapshot):
            _evidence_invalid("无附件对账项携带了伪造的文件快照")
    elif (
        evidence_file is None
        or item_binding.evidence_file_sha256 != evidence_file.sha256
        or item_binding.evidence_file_size_bytes != evidence_file.size_bytes
        or item_binding.evidence_file_mime_type != evidence_file.mime_type
    ):
        _evidence_invalid("对账附件当前元数据与解释时快照不一致")
    if run.status == "differences" and item.status != "explained":
        _evidence_invalid("未批准运行含有已解决对账项")
    if run.status == "approved" and item.status != "resolved":
        _evidence_invalid("已批准运行仍含未解决对账项")
    if (
        run.status == "approved"
        and binding.approved_by_user_id == item_binding.explained_by_user_id
    ):
        _evidence_invalid("对账解释人与批准人相同")


def _validate_command_chain(
    run: ReconciliationRun,
    binding: OpeningControlReconciliationRun,
    task: FormalStocktakeTask,
    items: tuple[_ItemGraph, ...],
    commands: tuple[ReconciliationCommand, ...],
    consumptions: tuple[OpeningControlReconciliationCommandConsumption, ...],
) -> None:
    create_rows = [row for row in commands if row.operation == "create_opening"]
    explain_rows = [row for row in commands if row.operation == "explain_opening"]
    approve_rows = [row for row in commands if row.operation == "approve_opening"]
    if len(create_rows) != 1 or len(approve_rows) != (1 if run.status == "approved" else 0):
        _evidence_invalid("对账运行缺少唯一创建或批准命令")
    if any(
        row.operation not in {"create_opening", "explain_opening", "approve_opening"}
        or row.run_id != run.id
        or _hash_document(row.request_jsonb) != row.request_hash
        or _hash_document(row.result_jsonb) != row.result_hash
        or _as_utc(row.created_at) != _as_utc(row.occurred_at)
        or _SHA256.fullmatch(row.idempotency_key_hash or "") is None
        for row in commands
    ):
        _evidence_invalid("对账幂等命令证据损坏")
    expected_target_versions = tuple(range(binding.version + 1))
    if (
        tuple(row.target_version for row in commands) != expected_target_versions
        or len(consumptions) != len(commands)
        or tuple(row.target_version for row in consumptions)
        != expected_target_versions
    ):
        _evidence_invalid("对账命令目标版本或消费封印不连续")
    consumptions_by_command = {row.command_id: row for row in consumptions}
    if len(consumptions_by_command) != len(consumptions):
        _evidence_invalid("对账命令消费封印重复")
    for row in commands:
        consumption = consumptions_by_command.get(row.id)
        if (
            consumption is None
            or consumption.run_id != run.id
            or consumption.operation != row.operation
            or consumption.target_version != row.target_version
            or _as_utc(consumption.consumed_at) != _as_utc(row.occurred_at)
            or _command_result_version(row) != row.target_version
        ):
            _evidence_invalid("对账命令与提交消费封印不一致")
        expected_operation = (
            "create_opening"
            if row.target_version == 0
            else (
                "approve_opening"
                if run.status == "approved" and row.target_version == binding.version
                else "explain_opening"
            )
        )
        if row.operation != expected_operation:
            _evidence_invalid("对账命令操作顺序与目标版本不一致")
        _validate_command_documents(
            run=run,
            binding=binding,
            task=task,
            items=items,
            command=row,
        )
    create = create_rows[0]
    if (
        create.id != binding.create_command_id
        or create.actor_user_id != binding.created_by_user_id
        or create.actor_person_id != binding.created_by_person_id
        or create.actor_role_assignment_id != binding.created_role_assignment_id
        or create.authorization_version != binding.created_authorization_version
        or _as_utc(create.occurred_at) != _as_utc(binding.created_at)
    ):
        _evidence_invalid("对账创建命令与创建人快照不一致")
    if any(row.item.status in {"explained", "resolved"} for row in items) and not explain_rows:
        _evidence_invalid("对账解释投影缺少追加式命令证据")
    if explain_rows:
        _validate_latest_explanation_projection(
            items=items,
            command=max(explain_rows, key=lambda row: row.target_version),
        )
    if run.status == "approved":
        approval = approve_rows[0]
        if (
            approval.actor_user_id != binding.approved_by_user_id
            or approval.actor_person_id != binding.approved_by_person_id
            or approval.actor_role_assignment_id != binding.approved_role_assignment_id
            or approval.authorization_version != binding.approved_authorization_version
            or _as_utc(approval.occurred_at) != _as_utc(binding.approved_at)
            or not binding.approval_comment.strip()
        ):
            _evidence_invalid("对账批准命令与总部批准快照不一致")
    elif any(
        value is not None
        for value in (
            binding.approved_by_user_id,
            binding.approved_by_person_id,
            binding.approved_role_assignment_id,
            binding.approved_authorization_version,
            binding.approved_at,
        )
    ) or binding.approval_comment != "":
        _evidence_invalid("未批准运行携带了批准事实")
    expected_version = len(explain_rows) + len(approve_rows)
    if binding.version != expected_version:
        _evidence_invalid("对账运行版本与追加式命令数量不一致")
    expected_item_version = len(explain_rows) + len(approve_rows)
    if any(row.binding.version != expected_item_version for row in items):
        _evidence_invalid("对账项版本与批量解释及批准命令数量不一致")
    _validate_graph_chronology(
        run=run,
        binding=binding,
        task=task,
        items=items,
        commands=commands,
    )


def _validate_graph_chronology(
    *,
    run: ReconciliationRun,
    binding: OpeningControlReconciliationRun,
    task: FormalStocktakeTask,
    items: tuple[_ItemGraph, ...],
    commands: tuple[ReconciliationCommand, ...],
) -> None:
    created_at = _as_utc(binding.created_at)
    if task.posted_at is None or created_at < _as_utc(task.posted_at):
        _evidence_invalid("对账创建时间早于期初独立过账时间")
    command_times = tuple(_as_utc(row.occurred_at) for row in commands)
    if (
        not command_times
        or command_times[0] != created_at
        or any(current < previous for previous, current in zip(command_times, command_times[1:]))
    ):
        _evidence_invalid("对账命令时间轴不连续")
    latest_at = command_times[-1]
    run_terminal_at = latest_at if run.status == "approved" else created_at
    if (
        _as_utc(run.started_at) != created_at
        or _as_utc(run.completed_at) != created_at
        or _as_utc(run.created_at) != created_at
        or _as_utc(run.updated_at) != run_terminal_at
        or _as_utc(binding.updated_at) != latest_at
    ):
        _evidence_invalid("对账运行时间投影与命令链不一致")
    if any(
        _as_utc(row.item.created_at) != created_at
        or _as_utc(row.binding.created_at) != created_at
        or _as_utc(row.item.updated_at) != latest_at
        or _as_utc(row.binding.updated_at) != latest_at
        for row in items
    ):
        _evidence_invalid("对账明细时间投影与命令链不一致")


def _validate_command_documents(
    *,
    run: ReconciliationRun,
    binding: OpeningControlReconciliationRun,
    task: FormalStocktakeTask,
    items: tuple[_ItemGraph, ...],
    command: ReconciliationCommand,
) -> None:
    """Bind immutable command JSON to the exact persisted projection.

    Hash self-consistency alone is insufficient: a direct database writer
    could otherwise seal a different but internally hash-consistent replay
    result.  Every read therefore rebuilds the result from the formal graph
    and validates the operation-specific request envelope as independent
    evidence.
    """

    request = command.request_jsonb
    if not isinstance(request, dict):
        _evidence_invalid("对账命令请求文档无效")
    expected_actor = {
        "user_id": command.actor_user_id,
        "person_id": str(command.actor_person_id),
        "authorization_version": command.authorization_version,
    }
    expected_result = _expected_command_result_document(
        run=run,
        binding=binding,
        items=items,
        command=command,
    )
    if command.result_jsonb != expected_result:
        _evidence_invalid("对账命令结果与已封存投影不一致")

    if command.operation == "create_opening":
        expected_task_version = task.version - (1 if task.status == "closed" else 0)
        if (
            set(request)
            != {"schema", "actor", "task_id", "expected_task_version"}
            or request.get("schema")
            != "cloud_oam.opening_control_reconciliation.create.v1"
            or request.get("actor") != expected_actor
            or request.get("task_id") != str(binding.task_id)
            or not _is_nonnegative_int(expected_task_version)
            or request.get("expected_task_version") != expected_task_version
        ):
            _evidence_invalid("对账创建命令请求语义无效")
        return

    if command.operation == "approve_opening":
        if (
            set(request)
            != {"schema", "actor", "reconciliation_run_id", "expected_version", "comment"}
            or request.get("schema")
            != "cloud_oam.opening_control_reconciliation.approve.v1"
            or request.get("actor") != expected_actor
            or request.get("reconciliation_run_id") != str(run.id)
            or request.get("expected_version") != command.target_version - 1
            or request.get("comment") != binding.approval_comment
            or not _is_trimmed_nonempty_text(request.get("comment"))
        ):
            _evidence_invalid("对账批准命令请求语义无效")
        return

    if set(request) != {
        "schema",
        "actor",
        "reconciliation_run_id",
        "expected_version",
        "items",
    }:
        _evidence_invalid("对账解释命令请求结构无效")
    request_items = request.get("items")
    if (
        request.get("schema")
        != "cloud_oam.opening_control_reconciliation.explain.v1"
        or request.get("actor") != expected_actor
        or request.get("reconciliation_run_id") != str(run.id)
        or request.get("expected_version") != command.target_version - 1
        or not isinstance(request_items, list)
        or len(request_items) != len(items)
    ):
        _evidence_invalid("对账解释命令请求语义无效")
    expected_item_ids = {str(row.item.id) for row in items}
    seen_item_ids: set[str] = set()
    for document in request_items:
        if not isinstance(document, dict) or set(document) != {
            "reconciliation_item_id",
            "expected_version",
            "explanation",
            "evidence_reference",
            "evidence_file_id",
        }:
            _evidence_invalid("对账解释命令明细结构无效")
        item_id = document.get("reconciliation_item_id")
        evidence_file_id = document.get("evidence_file_id")
        if (
            not isinstance(item_id, str)
            or item_id not in expected_item_ids
            or item_id in seen_item_ids
            or document.get("expected_version") != command.target_version - 1
            or not _is_trimmed_nonempty_text(document.get("explanation"))
            or not _is_trimmed_nonempty_text(document.get("evidence_reference"))
            or not _is_optional_canonical_uuid(evidence_file_id)
        ):
            _evidence_invalid("对账解释命令明细语义无效")
        seen_item_ids.add(item_id)
    if seen_item_ids != expected_item_ids:
        _evidence_invalid("对账解释命令未精确覆盖全部差异项")


def _expected_command_result_document(
    *,
    run: ReconciliationRun,
    binding: OpeningControlReconciliationRun,
    items: tuple[_ItemGraph, ...],
    command: ReconciliationCommand,
) -> dict[str, object]:
    common = {
        "reconciliation_run_id": str(run.id),
        "task_id": str(binding.task_id),
        "version": command.target_version,
    }
    occurred_at = _timestamp_text(command.occurred_at)
    if command.operation == "create_opening":
        return {
            "schema": "cloud_oam.opening_control_reconciliation.create_result.v1",
            **common,
            "status": "differences",
            "item_count": len(items),
            "created_at": occurred_at,
        }
    if command.operation == "explain_opening":
        return {
            "schema": "cloud_oam.opening_control_reconciliation.explain_result.v1",
            **common,
            "status": "differences",
            "explained_item_count": len(items),
            "explained_at": occurred_at,
        }
    if command.operation == "approve_opening":
        return {
            "schema": "cloud_oam.opening_control_reconciliation.approve_result.v1",
            **common,
            "status": "approved",
            "resolved_item_count": len(items),
            "approved_at": occurred_at,
        }
    _evidence_invalid("对账命令操作无效")
    raise AssertionError("unreachable reconciliation command operation")


def _validate_latest_explanation_projection(
    *,
    items: tuple[_ItemGraph, ...],
    command: ReconciliationCommand,
) -> None:
    """Bind the last explanation command to the surviving mutable projection."""

    request_items = command.request_jsonb.get("items")
    if not isinstance(request_items, list):
        _evidence_invalid("最新对账解释命令明细无效")
    documents = {
        row.get("reconciliation_item_id"): row
        for row in request_items
        if isinstance(row, dict)
    }
    if len(documents) != len(items):
        _evidence_invalid("最新对账解释命令明细重复或缺失")
    for item_graph in items:
        document = documents.get(str(item_graph.item.id))
        expected_file_id = (
            str(item_graph.item.evidence_file_id)
            if item_graph.item.evidence_file_id is not None
            else None
        )
        if (
            document is None
            or document.get("explanation") != item_graph.item.explanation
            or document.get("evidence_reference")
            != item_graph.binding.evidence_reference
            or document.get("evidence_file_id") != expected_file_id
            or command.actor_user_id != item_graph.binding.explained_by_user_id
            or command.actor_person_id != item_graph.binding.explained_by_person_id
            or command.actor_role_assignment_id
            != item_graph.binding.explained_role_assignment_id
            or command.authorization_version
            != item_graph.binding.explanation_authorization_version
            or _as_utc(command.occurred_at)
            != _as_utc(item_graph.binding.explained_at)
        ):
            _evidence_invalid("最新对账解释命令与当前投影不一致")


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_trimmed_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip() and "\x00" not in value


def _is_optional_canonical_uuid(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.int != 0 and str(parsed) == value


def _validate_command_effects(
    db: Session,
    *,
    run: ReconciliationRun,
    binding: OpeningControlReconciliationRun,
    items: tuple[_ItemGraph, ...],
    commands: tuple[ReconciliationCommand, ...],
    audit_proof: object,
) -> None:
    """Re-prove every accepted command's independent persisted effects."""

    _require_reconciliation_audit_proof(db, audit_proof=audit_proof)
    expected_state_keys: set[str] = set()
    expected_outbox_keys: set[str] = set()
    expected_audit_requests: set[str] = set()
    event_types = {
        f"reconciliation.opening.{operation.removesuffix('_opening')}"
        for operation in ("create_opening", "explain_opening", "approve_opening")
    }
    explain_seen = False
    for command in commands:
        event_type = (
            f"reconciliation.opening.{command.operation.removesuffix('_opening')}"
        )
        transitions: tuple[tuple[str, uuid.UUID, str | None, str], ...]
        if command.operation == "create_opening":
            transitions = (("reconciliation_run", run.id, None, "differences"),)
        elif command.operation == "explain_opening":
            transitions = (
                tuple(
                    (
                        "reconciliation_item",
                        item_graph.item.id,
                        "difference",
                        "explained",
                    )
                    for item_graph in items
                )
                if not explain_seen
                else ()
            )
            explain_seen = True
        else:
            transitions = tuple(
                (
                    "reconciliation_item",
                    item_graph.item.id,
                    "explained",
                    "resolved",
                )
                for item_graph in items
            ) + (("reconciliation_run", run.id, "differences", "approved"),)

        occurred_at = _as_utc(command.occurred_at)
        for index, (aggregate_type, aggregate_id, from_status, to_status) in enumerate(
            transitions,
            start=1,
        ):
            state_key = _event_key(
                command.operation,
                command.idempotency_key_hash,
                f"state-{index}",
            )
            expected_state_keys.add(state_key)
            state_rows = tuple(
                db.scalars(
                    select(StateTransitionEvent).where(
                        StateTransitionEvent.idempotency_key == state_key
                    )
                ).all()
            )
            if len(state_rows) != 1:
                _evidence_invalid("对账命令缺少唯一状态迁移证据")
            state = state_rows[0]
            if (
                state.aggregate_type != aggregate_type
                or state.aggregate_id != str(aggregate_id)
                or state.from_status != from_status
                or state.to_status != to_status
                or state.reason != event_type
                or state.actor_id != command.actor_user_id
                or _as_utc(state.occurred_at) != occurred_at
                or _as_utc(state.created_at) != occurred_at
                or state.metadata_jsonb
                != {
                    "reconciliation_run_id": str(run.id),
                    "result_hash": command.result_hash,
                }
            ):
                _evidence_invalid("对账状态迁移证据与命令不一致")

        outbox_key = _event_key(
            command.operation,
            command.idempotency_key_hash,
            "outbox",
        )
        expected_outbox_keys.add(outbox_key)
        outbox_rows = tuple(
            db.scalars(
                select(OutboxEvent).where(OutboxEvent.idempotency_key == outbox_key)
            ).all()
        )
        if len(outbox_rows) != 1:
            _evidence_invalid("对账命令缺少唯一 Outbox 证据")
        outbox = outbox_rows[0]
        if (
            outbox.event_type != event_type
            or outbox.aggregate_type != "reconciliation_run"
            or outbox.aggregate_id != str(run.id)
            or outbox.payload_jsonb
            != {
                "reconciliation_run_id": str(run.id),
                "result": command.result_jsonb,
                "result_hash": command.result_hash,
            }
            or _as_utc(outbox.available_at) != occurred_at
            or _as_utc(outbox.created_at) != occurred_at
        ):
            _evidence_invalid("对账 Outbox 证据与命令不一致")

        expected_audit_requests.add(command.request_reference)
        audit_rows = tuple(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                    AuditEvent.action == event_type,
                    AuditEvent.aggregate_type == "reconciliation_run",
                    AuditEvent.aggregate_id == str(run.id),
                    AuditEvent.request_id == command.request_reference,
                )
            ).all()
        )
        if len(audit_rows) != 1:
            _evidence_invalid("对账命令缺少唯一链式审计证据")
        audit = audit_rows[0]
        result_version = _command_result_version(command)
        before = (
            {"status": None, "version": None}
            if command.operation == "create_opening"
            else {
                "status": "differences",
                "version": result_version - 1,
                "item_versions": {
                    str(item_graph.item.id): result_version - 1
                    for item_graph in items
                },
            }
        )
        if command.operation == "explain_opening":
            role_code, scope_type, scope_id = (
                "provincial_manager",
                "organization",
                str(binding.region_org_id),
            )
        else:
            role_code, scope_type, scope_id = "admin", "national", "*"
        after = {
            "result": command.result_jsonb,
            "result_hash": command.result_hash,
            "actor_person_id": str(command.actor_person_id),
            "actor_role_assignment_id": str(command.actor_role_assignment_id),
            "actor_role_code": role_code,
            "actor_scope_type": scope_type,
            "actor_scope_id": scope_id,
            "authorization_version": command.authorization_version,
        }
        try:
            verified_audit = _verify_audit_event_with_prelocked_proof(
                db,
                proof=audit_proof,
                stream_key=INVENTORY_STREAM_KEY,
                event_id=audit.id,
            )
        except AuditChainError as exc:
            _evidence_invalid("对账审计事件不在完整库存审计链中", cause=exc)
        if (
            verified_audit.id != audit.id
            or audit.actor_user_id != command.actor_user_id
            or audit.before_jsonb != before
            or audit.after_jsonb != after
            or _as_utc(audit.occurred_at) != occurred_at
        ):
            _evidence_invalid("对账链式审计证据与命令不一致")

    actual_state_keys = {
        row.idempotency_key
        for row in db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.reason.in_(tuple(event_types))
            )
        ).all()
        if isinstance(row.metadata_jsonb, dict)
        and row.metadata_jsonb.get("reconciliation_run_id") == str(run.id)
    }
    actual_outbox_keys = set(
        db.scalars(
            select(OutboxEvent.idempotency_key).where(
                OutboxEvent.aggregate_type == "reconciliation_run",
                OutboxEvent.aggregate_id == str(run.id),
                OutboxEvent.event_type.in_(tuple(event_types)),
            )
        ).all()
    )
    actual_audit_requests = set(
        db.scalars(
            select(AuditEvent.request_id).where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.aggregate_type == "reconciliation_run",
                AuditEvent.aggregate_id == str(run.id),
                AuditEvent.action.in_(tuple(event_types)),
            )
        ).all()
    )
    if (
        actual_state_keys != expected_state_keys
        or actual_outbox_keys != expected_outbox_keys
        or actual_audit_requests != expected_audit_requests
    ):
        _evidence_invalid("对账命令副作用集合不完整或含有未绑定事实")


def _command_result_version(command: ReconciliationCommand) -> int:
    value = command.result_jsonb.get("version") if isinstance(command.result_jsonb, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _evidence_invalid("对账命令结果版本无效")
    return value


def _load_posted_source_graph(
    db: Session,
    task: FormalStocktakeTask,
    *,
    lock: bool,
) -> tuple[
    StocktakeRound,
    StocktakePosting,
    tuple[InventoryOpeningEstablishment, ...],
    tuple[StocktakeDifference, ...],
    tuple[StocktakeControlSnapshotLine, ...],
]:
    if lock:
        _fail(
            "opening_reconciliation_internal_lock_contract_invalid",
            "precondition_failed",
            "对账来源验证器只允许普通重读；来源锁必须由预锁图处理",
        )
    if task.task_type != "opening" or task.status not in {"posted", "closed"}:
        _evidence_invalid("对账来源不是已过账或已关闭的期初任务")
    round_statement = select(StocktakeRound).where(
        StocktakeRound.task_id == task.id,
        StocktakeRound.round_no == task.current_round_no,
    )
    rounds = tuple(db.scalars(round_statement.execution_options(populate_existing=True)).all())
    if len(rounds) != 1:
        _evidence_invalid("期初任务的当前轮次不唯一")
    round_row = rounds[0]
    posting_statement = select(StocktakePosting).where(
        StocktakePosting.task_id == task.id,
        StocktakePosting.round_id == round_row.id,
        StocktakePosting.posting_kind == "opening",
    )
    establishment_statement = (
        select(InventoryOpeningEstablishment)
        .where(
            InventoryOpeningEstablishment.task_id == task.id,
            InventoryOpeningEstablishment.round_id == round_row.id,
        )
        .order_by(InventoryOpeningEstablishment.scope_id)
    )
    difference_statement = (
        select(StocktakeDifference)
        .where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == round_row.id,
            StocktakeDifference.difference_type == "control_unassigned",
        )
        .order_by(StocktakeDifference.difference_no)
    )
    control_statement = (
        select(StocktakeControlSnapshotLine)
        .where(StocktakeControlSnapshotLine.task_id == task.id)
        .order_by(StocktakeControlSnapshotLine.line_no)
    )
    postings = tuple(db.scalars(posting_statement.execution_options(populate_existing=True)).all())
    establishments = tuple(
        db.scalars(establishment_statement.execution_options(populate_existing=True)).all()
    )
    differences = tuple(
        db.scalars(difference_statement.execution_options(populate_existing=True)).all()
    )
    controls = tuple(db.scalars(control_statement.execution_options(populate_existing=True)).all())
    if len(postings) != 1 or not establishments:
        _evidence_invalid("期初任务的轮次、过账或成立事实不唯一")
    posting = postings[0]
    if (
        posting.round_id != round_row.id
        or any(
            row.round_id != round_row.id
            or row.posting_id != posting.id
            or not row.has_pending_control_difference
            for row in establishments
        )
        or any(row.round_id != round_row.id for row in differences)
    ):
        _evidence_invalid("期初待核差异与最终轮次或成立事实不一致")
    return round_row, posting, establishments, differences, controls


def _item_manifest_sha256(
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    differences: Sequence[StocktakeDifference],
    controls: Sequence[StocktakeControlSnapshotLine],
) -> str:
    controls_by_id = {row.id: row for row in controls}
    rows: list[dict[str, object]] = []
    for difference in differences:
        control = controls_by_id.get(difference.control_snapshot_line_id)
        if control is None or difference.difference_type != "control_unassigned":
            _evidence_invalid("待核差异未绑定唯一 OAM 控制快照行")
        rows.append(
            {
                "difference_id": str(difference.id),
                "difference_no": difference.difference_no,
                "control_snapshot_line_id": str(control.id),
                "business_key": control.external_business_key,
                "material_id": (
                    str(difference.material_id)
                    if difference.material_id is not None
                    else None
                ),
                "external_qty": _quantity_text(difference.book_qty),
                "local_qty": _quantity_text(difference.counted_qty),
                "difference": _signed_quantity_text(
                    difference.book_qty - difference.counted_qty
                ),
            }
        )
    return _hash_document(
        {
            "schema": "cloud_oam.opening_control_reconciliation.items.v1",
            "task_id": str(task_id),
            "round_id": str(round_id),
            "items": rows,
        }
    )


def _summary_out(
    db: Session,
    actor: FormalPrincipal,
    graph: _RunGraph,
) -> OpeningControlReconciliationSummaryOut:
    explained = sum(row.item.status == "explained" for row in graph.items)
    resolved = sum(row.item.status == "resolved" for row in graph.items)
    return OpeningControlReconciliationSummaryOut(
        reconciliation_run_id=graph.run.id,
        task_id=graph.task.id,
        task_no=graph.task.task_no,
        region_org_id=graph.task.region_org_id,
        status=graph.run.status,
        version=graph.binding.version,
        item_count=graph.binding.item_count,
        explained_item_count=explained,
        resolved_item_count=resolved,
        external_snapshot_at=_as_utc(graph.run.external_snapshot_at),
        local_ledger_cursor=graph.run.local_ledger_cursor,
        created_at=_as_utc(graph.binding.created_at),
        approved_at=(
            _as_utc(graph.binding.approved_at)
            if graph.binding.approved_at is not None
            else None
        ),
        allowed_actions=_allowed_actions(db, actor, graph),
    )


def _item_out(graph: _ItemGraph) -> OpeningControlReconciliationItemOut:
    return OpeningControlReconciliationItemOut(
        reconciliation_item_id=graph.item.id,
        stocktake_difference_id=graph.difference.id,
        control_snapshot_line_id=graph.control_line.id,
        business_key=graph.item.business_key,
        material_id=graph.difference.material_id,
        external_qty=_quantity_text(graph.item.external_qty),
        local_qty=_quantity_text(graph.item.local_qty),
        difference=_signed_quantity_text(graph.item.difference),
        status=graph.item.status,
        version=graph.binding.version,
        explanation=graph.item.explanation,
        evidence_reference=graph.binding.evidence_reference,
        evidence_file_id=graph.item.evidence_file_id,
        explained_at=(
            _as_utc(graph.binding.explained_at)
            if graph.binding.explained_at is not None
            else None
        ),
    )


def _allowed_actions(
    db: Session,
    actor: FormalPrincipal,
    graph: _RunGraph,
) -> list[str]:
    actions: list[str] = []
    if graph.run.status != "differences" or graph.task.status != "posted":
        return actions
    if _has_exact_action(
        db,
        actor,
        action="explain_opening",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(graph.task.region_org_id),
    ):
        actions.append("explain")
    if (
        all(row.item.status == "explained" for row in graph.items)
        and all(row.binding.explained_by_user_id != actor.user_id for row in graph.items)
        and _has_exact_action(
            db,
            actor,
            action="approve_opening",
            role_code="admin",
            scope_type="national",
            scope_id="*",
        )
    ):
        actions.append("approve")
    return actions


def _require_read_actor(actor: FormalPrincipal) -> None:
    _validate_supplied_actor(actor)
    if not any(
        row.resource == "reconciliation"
        and row.action == "read"
        and row.effect == "allow"
        for row in actor.entitlements
    ) or any(
        row.resource == "reconciliation"
        and row.action == "read"
        and row.effect == "deny"
        for row in actor.entitlements
    ):
        _fail(
            "opening_reconciliation_read_forbidden",
            "forbidden",
            "当前账号无权读取正式对账运行",
        )


def _can_read_graph(db: Session, actor: FormalPrincipal, graph: _RunGraph) -> bool:
    return _has_exact_action(
        db,
        actor,
        action="read",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    ) or _has_exact_action(
        db,
        actor,
        action="read",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(graph.task.region_org_id),
    )


def _readable_region_org_ids(
    actor: FormalPrincipal,
) -> tuple[uuid.UUID, ...]:
    denied = any(
        row.resource == "reconciliation"
        and row.action == "read"
        and row.effect == "deny"
        for row in actor.entitlements
    )
    if denied:
        return ()
    allowed_assignment_ids = {
        row.assignment_id
        for row in actor.entitlements
        if row.resource == "reconciliation"
        and row.action == "read"
        and row.field_code == ""
        and row.effect == "allow"
        and row.role_code == "provincial_manager"
        and row.scope_type == "organization"
    }
    region_ids: set[uuid.UUID] = set()
    for grant in actor.assignments:
        if (
            grant.assignment_id not in allowed_assignment_ids
            or grant.role_code != "provincial_manager"
            or grant.scope_type != "organization"
        ):
            continue
        try:
            region_id = uuid.UUID(grant.scope_id)
        except (AttributeError, TypeError, ValueError):
            continue
        if region_id.int != 0:
            region_ids.add(region_id)
    return tuple(sorted(region_ids, key=str))


def _has_exact_action(
    db: Session,
    actor: FormalPrincipal,
    *,
    action: str,
    role_code: str,
    scope_type: str,
    scope_id: str,
) -> bool:
    grant_ids = {
        row.assignment_id
        for row in actor.assignments
        if row.role_code == role_code
        and row.scope_type == scope_type
        and row.scope_id == scope_id
    }
    if not grant_ids:
        return False
    if any(
        row.resource == "reconciliation"
        and row.action == action
        and row.field_code == ""
        and row.effect == "deny"
        for row in actor.entitlements
    ):
        return False
    return any(
        row.assignment_id in grant_ids
        and row.role_code == role_code
        and row.scope_type == scope_type
        and row.scope_id == scope_id
        and row.resource == "reconciliation"
        and row.action == action
        and row.field_code == ""
        and row.effect == "allow"
        for row in actor.entitlements
    )


def _authorize_actor(
    db: Session,
    actor: FormalPrincipal,
    *,
    now: datetime,
    action: str,
    role_code: str,
    scope_type: str,
    scope_id: str,
) -> _ActorAuthorization:
    grants = tuple(
        row
        for row in actor.assignments
        if row.role_code == role_code
        and row.scope_type == scope_type
        and row.scope_id == scope_id
    )
    if len(grants) != 1 or not _has_exact_action(
        db,
        actor,
        action=action,
        role_code=role_code,
        scope_type=scope_type,
        scope_id=scope_id,
    ):
        _fail(
            "opening_reconciliation_forbidden",
            "forbidden",
            "当前账号没有该对账动作的精确角色与数据范围",
        )
    grant = grants[0]
    # Every write boundary locked the complete principal graph before entering
    # this validator.  Reauthorization after the final audit-head lock must be
    # a plain exact reread; another FOR UPDATE here would violate audit-final.
    assignment = db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.id == grant.assignment_id)
        .execution_options(populate_existing=True)
    )
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, actor.user_id)
    person = db.get(Person, actor.person_id)
    organization = (
        db.get(Organization, person.organization_id)
        if person is not None
        else None
    )
    if (
        assignment is None
        or assignment.user_id != actor.user_id
        or assignment.scope_type != scope_type
        or assignment.scope_id != scope_id
        or assignment.status not in {"scheduled", "active"}
        or _as_utc(assignment.valid_from) > now
        or (
            assignment.valid_to is not None
            and now >= _as_utc(assignment.valid_to)
        )
        or (
            assignment.revoked_at is not None
            and now >= _as_utc(assignment.revoked_at)
        )
        or role is None
        or role.code != role_code
        or role.status != "active"
        or role.is_external
        or user is None
        or user.person_id != actor.person_id
        or person is None
        or person.employment_status != "active"
        or organization is None
        or organization.status != "active"
        or (
            role_code == "admin"
            and organization.org_type != "headquarters"
        )
        or (
            role_code == "provincial_manager"
            and organization.org_type
            not in {"headquarters", "region_company", "department"}
        )
    ):
        _fail(
            "opening_reconciliation_assignment_not_current",
            "forbidden",
            "对账角色授权已失效",
        )
    return _ActorAuthorization(
        assignment=assignment,
        grant=grant,
        person=person,
        organization=organization,
    )


def _append_effects(
    db: Session,
    *,
    operation: str,
    run: ReconciliationRun,
    actor: FormalPrincipal,
    authorization: _ActorAuthorization,
    command_row: ReconciliationCommand,
    before: dict[str, object],
    result: dict[str, object],
    transitions: tuple[tuple[str, uuid.UUID, str | None, str], ...],
    now: datetime,
) -> None:
    event_type = f"reconciliation.opening.{operation.removesuffix('_opening')}"
    for index, (aggregate_type, aggregate_id, from_status, to_status) in enumerate(
        transitions, start=1
    ):
        db.add(
            StateTransitionEvent(
                aggregate_type=aggregate_type,
                aggregate_id=str(aggregate_id),
                from_status=from_status,
                to_status=to_status,
                reason=event_type,
                actor_id=actor.user_id,
                idempotency_key=_event_key(
                    operation,
                    command_row.idempotency_key_hash,
                    f"state-{index}",
                ),
                occurred_at=now,
                metadata_jsonb={
                    "reconciliation_run_id": str(run.id),
                    "result_hash": command_row.result_hash,
                },
                created_at=now,
            )
        )
    db.add(
        OutboxEvent(
            event_type=event_type,
            aggregate_type="reconciliation_run",
            aggregate_id=str(run.id),
            payload_jsonb={
                "reconciliation_run_id": str(run.id),
                "result": result,
                "result_hash": command_row.result_hash,
            },
            status="pending",
            attempts=0,
            idempotency_key=_event_key(
                operation,
                command_row.idempotency_key_hash,
                "outbox",
            ),
            available_at=now,
            locked_at=None,
            locked_by=None,
            published_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action=event_type,
        aggregate_type="reconciliation_run",
        aggregate_id=str(run.id),
        before_jsonb=before,
        after_jsonb={
            "result": result,
            "result_hash": command_row.result_hash,
            "actor_person_id": str(actor.person_id),
            "actor_role_assignment_id": str(authorization.assignment.id),
            "actor_role_code": authorization.grant.role_code,
            "actor_scope_type": authorization.grant.scope_type,
            "actor_scope_id": authorization.grant.scope_id,
            "authorization_version": actor.authorization_version,
        },
        request_id=command_row.request_reference,
        occurred_at=now,
    )


def _command_row(
    *,
    operation: str,
    run_id: uuid.UUID,
    target_version: int,
    idempotency_key_hash: str,
    request_reference: str,
    request_document: dict[str, object],
    result_document: dict[str, object],
    actor: FormalPrincipal,
    authorization: _ActorAuthorization,
    now: datetime,
) -> ReconciliationCommand:
    return ReconciliationCommand(
        id=uuid.uuid4(),
        operation=operation,
        run_id=run_id,
        target_version=target_version,
        idempotency_key_hash=idempotency_key_hash,
        request_reference=request_reference,
        request_hash=_hash_document(request_document),
        result_hash=_hash_document(result_document),
        request_jsonb=request_document,
        result_jsonb=result_document,
        actor_user_id=actor.user_id,
        actor_person_id=actor.person_id,
        actor_role_assignment_id=authorization.assignment.id,
        authorization_version=actor.authorization_version,
        occurred_at=now,
        created_at=now,
    )


def _seal_command_consumption(
    db: Session,
    command_row: ReconciliationCommand,
) -> OpeningControlReconciliationCommandConsumption:
    consumption = OpeningControlReconciliationCommandConsumption(
        command_id=command_row.id,
        run_id=command_row.run_id,
        operation=command_row.operation,
        target_version=command_row.target_version,
        consumed_at=command_row.occurred_at,
    )
    db.add(consumption)
    db.flush()
    return consumption


def _replay_start(
    db: Session,
    *,
    command_row: ReconciliationCommand,
    request_hash: str,
    expected_task_id: uuid.UUID,
    prevalidated_graph: _RunGraph,
) -> OpeningControlReconciliationStartResult:
    if command_row.operation != "create_opening" or command_row.request_hash != request_hash:
        _idempotency_conflict()
    graph = prevalidated_graph
    if graph.task.id != expected_task_id:
        _idempotency_conflict()
    value = _expected_command_result_document(
        run=graph.run,
        binding=graph.binding,
        items=graph.items,
        command=command_row,
    )
    try:
        result = OpeningControlReconciliationStartResult(
            reconciliation_run_id=uuid.UUID(str(value["reconciliation_run_id"])),
            task_id=uuid.UUID(str(value["task_id"])),
            status=str(value["status"]),
            version=int(value["version"]),
            item_count=int(value["item_count"]),
            created_at=_parse_datetime(value["created_at"]),
            replayed=True,
        )
    except (KeyError, TypeError, ValueError):
        _evidence_invalid("对账创建幂等结果损坏")
    if _hash_document(value) != command_row.result_hash or result.status != "differences":
        _evidence_invalid("对账创建幂等结果哈希或状态无效")
    return result


def _replay_explain(
    db: Session,
    *,
    command_row: ReconciliationCommand,
    request_hash: str,
    expected_run_id: uuid.UUID,
    prevalidated_graph: _RunGraph,
) -> OpeningControlReconciliationExplainResult:
    if command_row.operation != "explain_opening" or command_row.request_hash != request_hash:
        _idempotency_conflict()
    graph = prevalidated_graph
    if command_row.run_id != expected_run_id:
        _idempotency_conflict()
    value = _expected_command_result_document(
        run=graph.run,
        binding=graph.binding,
        items=graph.items,
        command=command_row,
    )
    try:
        result = OpeningControlReconciliationExplainResult(
            reconciliation_run_id=uuid.UUID(str(value["reconciliation_run_id"])),
            task_id=uuid.UUID(str(value["task_id"])),
            status=str(value["status"]),
            version=int(value["version"]),
            explained_item_count=int(value["explained_item_count"]),
            explained_at=_parse_datetime(value["explained_at"]),
            replayed=True,
        )
    except (KeyError, TypeError, ValueError):
        _evidence_invalid("对账解释幂等结果损坏")
    if _hash_document(value) != command_row.result_hash or result.status != "differences":
        _evidence_invalid("对账解释幂等结果哈希或状态无效")
    return result


def _replay_approve(
    db: Session,
    *,
    command_row: ReconciliationCommand,
    request_hash: str,
    expected_run_id: uuid.UUID,
    prevalidated_graph: _RunGraph,
) -> OpeningControlReconciliationApproveResult:
    if command_row.operation != "approve_opening" or command_row.request_hash != request_hash:
        _idempotency_conflict()
    graph = prevalidated_graph
    if command_row.run_id != expected_run_id or graph.run.status != "approved":
        _idempotency_conflict()
    value = _expected_command_result_document(
        run=graph.run,
        binding=graph.binding,
        items=graph.items,
        command=command_row,
    )
    try:
        result = OpeningControlReconciliationApproveResult(
            reconciliation_run_id=uuid.UUID(str(value["reconciliation_run_id"])),
            task_id=uuid.UUID(str(value["task_id"])),
            status=str(value["status"]),
            version=int(value["version"]),
            resolved_item_count=int(value["resolved_item_count"]),
            approved_at=_parse_datetime(value["approved_at"]),
            replayed=True,
        )
    except (KeyError, TypeError, ValueError):
        _evidence_invalid("对账批准幂等结果损坏")
    if _hash_document(value) != command_row.result_hash or result.status != "approved":
        _evidence_invalid("对账批准幂等结果哈希或状态无效")
    return result


def _start_request_document(
    actor: FormalPrincipal,
    command: StartOpeningControlReconciliationCommand,
) -> dict[str, object]:
    return {
        "schema": "cloud_oam.opening_control_reconciliation.create.v1",
        "actor": _actor_document(actor),
        "task_id": str(command.task_id),
        "expected_task_version": command.expected_task_version,
    }


def _explain_request_document(
    actor: FormalPrincipal,
    command: ExplainOpeningControlReconciliationCommand,
) -> dict[str, object]:
    return {
        "schema": "cloud_oam.opening_control_reconciliation.explain.v1",
        "actor": _actor_document(actor),
        "reconciliation_run_id": str(command.reconciliation_run_id),
        "expected_version": command.expected_version,
        "items": [
            {
                "reconciliation_item_id": str(row.reconciliation_item_id),
                "expected_version": row.expected_version,
                "explanation": row.explanation,
                "evidence_reference": row.evidence_reference,
                "evidence_file_id": (
                    str(row.evidence_file_id)
                    if row.evidence_file_id is not None
                    else None
                ),
            }
            for row in sorted(command.items, key=lambda value: str(value.reconciliation_item_id))
        ],
    }


def _approve_request_document(
    actor: FormalPrincipal,
    command: ApproveOpeningControlReconciliationCommand,
) -> dict[str, object]:
    return {
        "schema": "cloud_oam.opening_control_reconciliation.approve.v1",
        "actor": _actor_document(actor),
        "reconciliation_run_id": str(command.reconciliation_run_id),
        "expected_version": command.expected_version,
        "comment": command.comment,
    }


def _actor_document(actor: FormalPrincipal) -> dict[str, object]:
    return {
        "user_id": actor.user_id,
        "person_id": str(actor.person_id),
        "authorization_version": actor.authorization_version,
    }


def _start_result_document(
    result: OpeningControlReconciliationStartResult,
) -> dict[str, object]:
    return {
        "schema": "cloud_oam.opening_control_reconciliation.create_result.v1",
        "reconciliation_run_id": str(result.reconciliation_run_id),
        "task_id": str(result.task_id),
        "status": result.status,
        "version": result.version,
        "item_count": result.item_count,
        "created_at": _timestamp_text(result.created_at),
    }


def _explain_result_document(
    result: OpeningControlReconciliationExplainResult,
) -> dict[str, object]:
    return {
        "schema": "cloud_oam.opening_control_reconciliation.explain_result.v1",
        "reconciliation_run_id": str(result.reconciliation_run_id),
        "task_id": str(result.task_id),
        "status": result.status,
        "version": result.version,
        "explained_item_count": result.explained_item_count,
        "explained_at": _timestamp_text(result.explained_at),
    }


def _approve_result_document(
    result: OpeningControlReconciliationApproveResult,
) -> dict[str, object]:
    return {
        "schema": "cloud_oam.opening_control_reconciliation.approve_result.v1",
        "reconciliation_run_id": str(result.reconciliation_run_id),
        "task_id": str(result.task_id),
        "status": result.status,
        "version": result.version,
        "resolved_item_count": result.resolved_item_count,
        "approved_at": _timestamp_text(result.approved_at),
    }


def _validate_start_command(
    command: StartOpeningControlReconciliationCommand,
) -> StartOpeningControlReconciliationCommand:
    if not isinstance(command, StartOpeningControlReconciliationCommand):
        _fail(
            "opening_reconciliation_command_required",
            "invalid_request",
            "对账创建命令类型无效",
        )
    return StartOpeningControlReconciliationCommand(
        task_id=_require_uuid("task_id", command.task_id),
        expected_task_version=_require_version(command.expected_task_version),
    )


def _validate_explain_command(
    command: ExplainOpeningControlReconciliationCommand,
) -> ExplainOpeningControlReconciliationCommand:
    if not isinstance(command, ExplainOpeningControlReconciliationCommand):
        _fail(
            "opening_reconciliation_command_required",
            "invalid_request",
            "对账解释命令类型无效",
        )
    if not command.items:
        _fail(
            "opening_reconciliation_items_required",
            "invalid_request",
            "对账解释必须包含差异项",
        )
    checked_items: list[OpeningControlExplanationInput] = []
    for row in command.items:
        if not isinstance(row, OpeningControlExplanationInput):
            _fail(
                "opening_reconciliation_item_invalid",
                "invalid_request",
                "对账解释项类型无效",
            )
        checked_items.append(
            OpeningControlExplanationInput(
                reconciliation_item_id=_require_uuid(
                    "reconciliation_item_id", row.reconciliation_item_id
                ),
                expected_version=_require_version(row.expected_version),
                explanation=_require_trimmed_text(
                    "explanation", row.explanation, minimum=4, maximum=4000
                ),
                evidence_reference=_require_trimmed_text(
                    "evidence_reference",
                    row.evidence_reference,
                    minimum=4,
                    maximum=1000,
                ),
                evidence_file_id=(
                    _require_uuid("evidence_file_id", row.evidence_file_id)
                    if row.evidence_file_id is not None
                    else None
                ),
            )
        )
    return ExplainOpeningControlReconciliationCommand(
        reconciliation_run_id=_require_uuid(
            "reconciliation_run_id", command.reconciliation_run_id
        ),
        expected_version=_require_version(command.expected_version),
        items=tuple(checked_items),
    )


def _validate_approve_command(
    command: ApproveOpeningControlReconciliationCommand,
) -> ApproveOpeningControlReconciliationCommand:
    if not isinstance(command, ApproveOpeningControlReconciliationCommand):
        _fail(
            "opening_reconciliation_command_required",
            "invalid_request",
            "对账批准命令类型无效",
        )
    return ApproveOpeningControlReconciliationCommand(
        reconciliation_run_id=_require_uuid(
            "reconciliation_run_id", command.reconciliation_run_id
        ),
        expected_version=_require_version(command.expected_version),
        comment=_require_trimmed_text(
            "comment", command.comment, minimum=4, maximum=4000
        ),
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if (
        not isinstance(actor, FormalPrincipal)
        or not isinstance(actor.user_id, str)
        or not actor.user_id.strip()
        or not isinstance(actor.person_id, uuid.UUID)
        or actor.person_id.int == 0
        or not isinstance(actor.authorization_version, int)
        or isinstance(actor.authorization_version, bool)
        or actor.authorization_version <= 0
        or actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail(
            "opening_reconciliation_actor_invalid",
            "forbidden",
            "当前正式身份不可执行对账操作",
        )
    return actor


def _require_current_actor(
    db: Session,
    supplied: FormalPrincipal,
    now: datetime,
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "opening_reconciliation_actor_not_current",
            "forbidden",
            "当前正式身份已失效",
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail(
            "opening_reconciliation_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取",
        )
    return current


def _lock_opening_terminal_root_for_reconciliation(
    db: Session,
    task_id: uuid.UUID,
) -> object:
    """Take ledger -> task roots through the opening owner boundary."""

    from .opening_stocktake_finalize import (
        OpeningStocktakeFinalizeError,
        _lock_opening_terminal_task_root,
    )
    from .inventory_posting import InventoryPostingError

    try:
        return _lock_opening_terminal_task_root(db, task_id=task_id)
    except (OpeningStocktakeFinalizeError, InventoryPostingError) as exc:
        _fail(
            "opening_reconciliation_source_evidence_invalid",
            "precondition_failed",
            "期初过账根证据无法锁定，禁止创建或推进对账",
            cause=exc,
        )


def _lock_opening_principal_graph_for_reconciliation(
    db: Session,
    task_id: uuid.UUID,
    *,
    supplied_user_ids: Sequence[str],
) -> object:
    """Lock one sealed opening+reconciliation historical principal union."""

    from .inventory_posting import (
        InventoryPostingError,
        _lock_opening_task_principal_graph,
    )

    checked_task_id = _require_uuid("task_id", task_id)
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_reconciliation_prelocked_principal_transaction_required",
            "precondition_failed",
            "对账人员预锁图必须绑定活动事务",
        )
    historical_reconciliation_user_ids = (
        _opening_control_reconciliation_historical_user_ids(
            db,
            task_ids=(checked_task_id,),
        )
    )
    allowed_reconciliation_user_ids = tuple(
        sorted(
            set(historical_reconciliation_user_ids).union(
                _checked_persisted_reconciliation_user_id(user_id)
                for user_id in supplied_user_ids
            )
        )
    )
    try:
        opening_principal_graph = _lock_opening_task_principal_graph(
            db,
            task_ids=(checked_task_id,),
            supplied_user_ids=allowed_reconciliation_user_ids,
        )
    except InventoryPostingError as exc:
        _fail(
            "opening_reconciliation_source_evidence_invalid",
            "precondition_failed",
            "期初过账历史人员图无法锁定，禁止创建或推进对账",
            cause=exc,
        )
    return _PrelockedOpeningReconciliationPrincipalGraph(
        session=db,
        transaction=transaction,
        task_id=checked_task_id,
        historical_reconciliation_user_ids=(
            historical_reconciliation_user_ids
        ),
        allowed_reconciliation_user_ids=allowed_reconciliation_user_ids,
        opening_principal_graph=opening_principal_graph,
        seal=_PRELOCKED_RECONCILIATION_PRINCIPAL_SEAL,
    )


def _lock_opening_terminal_graph_for_reconciliation(
    db: Session,
    root: object,
    principal_graph: object,
) -> object:
    """Expand an already locked opening root without touching audit/recon."""

    from .opening_stocktake_finalize import (
        OpeningStocktakeFinalizeError,
        _lock_opening_terminal_task_graph_for_task,
    )
    from .inventory_posting import InventoryPostingError

    task = getattr(root, "task", None)
    if not isinstance(task, FormalStocktakeTask):
        _evidence_invalid("期初过账根锁缺少任务坐标")
    checked_principal_graph = (
        _require_prelocked_reconciliation_principal_graph(
            db,
            task_id=task.id,
            proof=principal_graph,
        )
    )
    try:
        return _lock_opening_terminal_task_graph_for_task(
            db,
            root=root,
            principal_graph=(
                checked_principal_graph.opening_principal_graph
            ),
        )
    except (OpeningStocktakeFinalizeError, InventoryPostingError) as exc:
        _fail(
            "opening_reconciliation_source_evidence_invalid",
            "precondition_failed",
            "期初过账引用图无法锁定，禁止创建或推进对账",
            cause=exc,
        )


def _validate_prelocked_opening_terminal_for_reconciliation(
    db: Session,
    proof: object,
    *,
    audit_proof: object,
) -> None:
    """Purely prove the opening graph after the final audit-head lock."""

    from .opening_stocktake_finalize import (
        OpeningStocktakeFinalizeError,
        _validate_opening_terminal_from_prelocked_graph,
    )
    from .inventory_posting import InventoryPostingError

    try:
        _validate_opening_terminal_from_prelocked_graph(
            db,
            proof=proof,
            audit_proof=audit_proof,
            require_closed=False,
        )
    except (OpeningStocktakeFinalizeError, InventoryPostingError) as exc:
        _fail(
            "opening_reconciliation_source_evidence_invalid",
            "precondition_failed",
            "期初过账证据无法从预锁图重证，禁止创建或推进对账",
            cause=exc,
        )


def _task_id_for_run(db: Session, run_id: uuid.UUID) -> uuid.UUID:
    """Resolve the immutable task coordinate before taking business locks."""

    task_id = db.scalar(
        select(OpeningControlReconciliationRun.task_id).where(
            OpeningControlReconciliationRun.run_id == run_id
        )
    )
    if task_id is None:
        _fail(
            "opening_reconciliation_not_found",
            "not_found",
            "对账运行不存在",
        )
    return task_id


def _load_command_by_key(
    db: Session, idempotency_key_hash: str
) -> ReconciliationCommand | None:
    # Every caller already holds the transaction-scoped advisory lock for
    # this idempotency-key hash, while the append-only table has a global
    # unique constraint.  A command row lock would both require an unsafe
    # UPDATE grant and invert the established run -> command lock order.
    statement = select(ReconciliationCommand).where(
        ReconciliationCommand.idempotency_key_hash == idempotency_key_hash
    )
    return db.scalar(statement.execution_options(populate_existing=True))


def _validate_optional_file(
    db: Session,
    file_id: uuid.UUID | None,
    *,
    evidence: bool = False,
) -> FileObject | None:
    if file_id is None:
        return None
    statement = select(FileObject).where(FileObject.id == file_id)
    # Mutation callers pinned files through the deterministic owner helper;
    # validators and reads must never introduce a late direct row lock.
    file = db.scalar(statement.execution_options(populate_existing=True))
    if (
        file is None
        or file.status != "available"
        or not isinstance(file.sha256, str)
        or _SHA256.fullmatch(file.sha256) is None
        or not isinstance(file.size_bytes, int)
        or isinstance(file.size_bytes, bool)
        or file.size_bytes < 0
        or not isinstance(file.mime_type, str)
        or not file.mime_type.strip()
        or file.mime_type != file.mime_type.strip()
    ):
        if evidence:
            _evidence_invalid("对账附件证据不可用或哈希无效")
        _fail(
            "opening_reconciliation_evidence_file_unavailable",
            "precondition_failed",
            "对账附件不存在、未通过隔离校验或哈希无效",
        )
    return file


def _require_uuid(field: str, value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        checked = value
    elif isinstance(value, str):
        try:
            checked = uuid.UUID(value)
        except ValueError:
            _fail(f"{field}_invalid", "invalid_request", f"{field} 必须为 UUID")
    else:
        _fail(f"{field}_invalid", "invalid_request", f"{field} 必须为 UUID")
    if checked.int == 0:
        _fail(f"{field}_invalid", "invalid_request", f"{field} 不能为零 UUID")
    return checked


def _require_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(
            "opening_reconciliation_expected_version_invalid",
            "invalid_request",
            "expected_version 必须为非负整数",
        )
    return value


def _require_trimmed_text(
    field: str,
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not minimum <= len(value) <= maximum
        or "\x00" in value
    ):
        _fail(
            f"opening_reconciliation_{field}_invalid",
            "invalid_request",
            f"{field} 必须为 {minimum}-{maximum} 位非空文本且不能有首尾空白",
        )
    return value


def _require_idempotency_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 128
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "opening_reconciliation_idempotency_key_invalid",
            "invalid_request",
            "幂等键必须为 16 至 128 位可打印 ASCII 字符",
        )
    return value


def _hash_document(value: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _evidence_invalid("对账证据不是规范 JSON 文档", cause=exc)
    return hashlib.sha256(payload).hexdigest()


def _storage_hash(operation: str, raw: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_control_reconciliation.{operation}.idempotency.v1\0{raw}".encode()
    ).hexdigest()


def _request_reference(operation: str, raw: object) -> str:
    if (
        not isinstance(raw, str)
        or not 8 <= len(raw) <= 160
        or _PRINTABLE.fullmatch(raw) is None
    ):
        _fail(
            "opening_reconciliation_request_id_invalid",
            "invalid_request",
            "请求标识必须为 8 至 160 位可打印 ASCII 字符",
        )
    digest = hashlib.sha256(
        f"cloud_oam.opening_control_reconciliation.{operation}.request.v1\0{raw}".encode()
    ).hexdigest()
    return f"opening-reconciliation-request-{digest}"


def _event_key(operation: str, anchor: str, suffix: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_control_reconciliation.event.v1\0{operation}\0{anchor}\0{suffix}".encode()
    ).hexdigest()
    return f"opening-reconciliation-{suffix}-{digest}"


def _advisory_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.opening_control_reconciliation.lock.v1\0{namespace}\0{value}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _take_advisory_locks(db: Session, coordinates: tuple[int, ...]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": coordinate},
        )


def _lock_readonly_source(db: Session, task_id: uuid.UUID) -> None:
    """Lock immutable opening source evidence without granting table UPDATE."""

    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text(f"SELECT {_PG_LOCK_SOURCE_FUNCTION}(CAST(:task_id AS uuid))"),
        {"task_id": str(task_id)},
    )


def _lock_readonly_run_graph(db: Session, run_id: uuid.UUID) -> None:
    """Lock append-only command/source evidence through the owner boundary."""

    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text(f"SELECT {_PG_LOCK_RUN_FUNCTION}(CAST(:run_id AS uuid))"),
        {"run_id": str(run_id)},
    )


def _lock_readonly_files(
    db: Session,
    run_id: uuid.UUID,
    file_ids: tuple[uuid.UUID, ...],
) -> None:
    """Lock current plus candidate evidence files in one owner-side UUID order."""

    if db.get_bind().dialect.name != "postgresql":
        return
    ordered_ids = tuple(sorted(set(file_ids), key=str))
    db.execute(
        text(
            f"SELECT {_PG_LOCK_FILES_FUNCTION}("
            "CAST(:run_id AS uuid), CAST(:file_ids AS uuid[]))"
        ),
        {
            "run_id": str(run_id),
            "file_ids": [str(file_id) for file_id in ordered_ids],
        },
    )


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            _fail(
                "opening_reconciliation_server_time_unavailable",
                "service_unavailable",
                "数据库服务端时间不可用",
            )
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _quantity(value: object, *, signed: bool) -> Decimal:
    try:
        checked = value if isinstance(value, Decimal) else Decimal(value)  # type: ignore[arg-type]
        quantized = checked.quantize(_QUANTITY_QUANTUM)
    except (InvalidOperation, TypeError, ValueError):
        _evidence_invalid("对账数量精度无效")
    if (
        not checked.is_finite()
        or quantized != checked
        or abs(checked) >= _MAX_QUANTITY
        or (not signed and checked < _ZERO)
    ):
        _evidence_invalid("对账数量超出 Numeric(18,3) 安全范围")
    return quantized


def _quantity_text(value: object) -> str:
    return format(_quantity(value, signed=False), ".3f")


def _signed_quantity_text(value: object) -> str:
    return format(_quantity(value, signed=True), ".3f")


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        _evidence_invalid("对账证据时间无效")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    # Fixed-width UTC text is part of the immutable command/seal contract.
    # Semantically equivalent timestamp spellings must not produce distinct
    # replay documents or bypass a database-side exact-text proof.
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be aware")
    return parsed.astimezone(timezone.utc)


def _idempotency_conflict() -> None:
    _fail(
        "opening_reconciliation_idempotency_conflict",
        "conflict",
        "幂等键已绑定其他对账请求",
    )


def _evidence_invalid(message: str, *, cause: Exception | None = None) -> None:
    _fail(
        "opening_reconciliation_evidence_invalid",
        "service_unavailable",
        message,
        cause=cause,
    )


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = OpeningControlReconciliationError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "ApproveOpeningControlReconciliationCommand",
    "ExplainOpeningControlReconciliationCommand",
    "OpeningControlExplanationInput",
    "OpeningControlReconciliationApproveResult",
    "OpeningControlReconciliationError",
    "OpeningControlReconciliationExplainResult",
    "OpeningControlReconciliationProof",
    "OpeningControlReconciliationStartResult",
    "OpeningControlReconciliationStatus",
    "StartOpeningControlReconciliationCommand",
    "approve_opening_control_reconciliation",
    "explain_opening_control_reconciliation",
    "list_opening_control_reconciliations",
    "opening_control_reconciliation_detail",
    "opening_control_reconciliation_is_approved",
    "opening_control_reconciliation_statuses",
    "require_approved_opening_control_reconciliation",
    "start_opening_control_reconciliation",
]
