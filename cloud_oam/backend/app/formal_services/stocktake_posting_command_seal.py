"""Seal-first guard for a durable non-opening stocktake post command.

The seal is an append-only tombstone.  It deliberately does not touch the
task, inventory ledger, freezes, or any posting state; a later POST must treat
the exact request coordinate as permanently non-executable.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..stocktake_models import FormalStocktakeTask, StocktakePostingCommandOutcome
from ..stocktake_posting_command_status_schemas import StocktakePostingSealedCommandOut
from . import inventory_posting as inventory_service
from . import stocktake_posting as posting
from .postgresql_lock_graph import lock_nonopening_stocktake_posting_graph


_TRACE = re.compile(r"[A-Za-z0-9._:-]{8,160}", re.ASCII)
_NON_OPENING = posting.NON_OPENING_TYPES


class StocktakePostingSealError(RuntimeError):
    def __init__(self, code: str, category: str, message: str) -> None:
        self.code, self.category, self.message = code, category, message
        self.http_status_code = {
            "invalid_request": 400,
            "forbidden": 403,
            "not_found": 404,
            "conflict": 409,
            "precondition_failed": 412,
            "service_unavailable": 503,
        }[category]
        super().__init__(message)

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "category": self.category, "message": self.message}


def seal_nonopening_stocktake_post_command(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    expected_task_version: int,
    actor_person_id: uuid.UUID,
    actor_authorization_version: int,
    trace_request_id: str,
) -> StocktakePostingSealedCommandOut:
    """Insert one ``sealed_not_executed`` outcome under the writer lock graph."""
    _validate_input(
        task_id, expected_task_version, actor_person_id,
        actor_authorization_version, trace_request_id,
    )
    if not isinstance(actor, FormalPrincipal):
        _error("principal_required", "forbidden", "必须使用正式权限主体")
    if (
        actor.person_id != actor_person_id
        or actor.authorization_version != actor_authorization_version
        or actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _error("authorization_changed", "precondition_failed", "当前人员或授权版本已变化，请重新查询")

    if not actor.allows(
        db, "stocktake", "post_difference",
        target_scope_type="national", target_scope_id="*",
    ):
        _error("forbidden", "forbidden", "当前正式权限不允许封存盘点过账命令")

    request_reference = posting._request_reference(trace_request_id)
    request_sha256 = _request_sha256(task_id, expected_task_version, trace_request_id)
    # Keep exactly the same lock order as the real posting writer.  The ledger
    # lock is a proof boundary only here; this function performs no inventory
    # write and never advances task/version.
    inventory_service._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_nonopening_stocktake_posting_graph(db, task_id)
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type not in _NON_OPENING:
        _error("task_not_found", "not_found", "非期初盘点任务不存在")
    lock_formal_principal_graph(db, (actor.user_id,))
    try:
        current = inventory_service._require_current_stocktake_difference_finalizer(db, actor)
        posting._current_admin_assignment(db, current)
    except Exception as exc:
        _error("authorization_changed", "precondition_failed", "当前总部管理员授权已变化，请重新查询", cause=exc)

    if task.status != "approved" or task.version != expected_task_version:
        _error("task_not_postable", "precondition_failed", "盘点任务不是当前版本的已批准待过账状态")

    outcomes = tuple(
        db.scalars(
            select(StocktakePostingCommandOutcome)
            .where(StocktakePostingCommandOutcome.task_id == task_id)
            .order_by(StocktakePostingCommandOutcome.created_at, StocktakePostingCommandOutcome.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    for row in outcomes:
        if row.request_reference == request_reference:
            if (
                row.disposition != "sealed_not_executed"
                or row.expected_task_version != expected_task_version
                or row.request_sha256 != request_sha256
                or row.sealed_by_person_id != actor_person_id
                or row.authorization_version != actor_authorization_version
            ):
                _error("seal_conflict", "conflict", "封存坐标已绑定不同请求或权限上下文")
            return _output(row, trace_request_id)
        # A task may retain more than one independently sealed request
        # coordinate.  Only the exact request reference is idempotent.

    row = StocktakePostingCommandOutcome(
        id=uuid.uuid4(), task_id=task_id, request_reference=request_reference,
        disposition="sealed_not_executed", completion_id=None,
        expected_task_version=expected_task_version, request_sha256=request_sha256,
        sealed_by_user_id=actor.user_id, sealed_by_person_id=actor.person_id,
        sealed_role_assignment_id=posting._current_admin_assignment(db, actor).id,
        authorization_version=actor.authorization_version,
        sealed_at=posting._database_now(db),
    )
    row.created_at = row.sealed_at
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        _error("seal_conflict", "conflict", "封存命令发生并发冲突，请重新查询", cause=exc)
    return _output(row, trace_request_id)


def _validate_input(task_id, version, person_id, auth_version, trace):
    if not isinstance(task_id, uuid.UUID) or task_id.int == 0:
        _error("input_invalid", "invalid_request", "盘点任务标识无效")
    if type(version) is not int or version < 0:
        _error("input_invalid", "invalid_request", "盘点任务预期版本无效")
    if not isinstance(person_id, uuid.UUID) or person_id.int == 0 or type(auth_version) is not int or auth_version < 1:
        _error("input_invalid", "invalid_request", "人员授权坐标无效")
    if not isinstance(trace, str) or _TRACE.fullmatch(trace) is None:
        _error("input_invalid", "invalid_request", "请求标识无效")


def _request_sha256(task_id, version, trace):
    payload = {
        "expected_task_version": version,
        "schema": "cloud_oam.stocktake.nonopening_posting_seal.v1",
        "task_id": str(task_id),
        "trace_request_id": trace,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _output(row, trace_request_id):
    return StocktakePostingSealedCommandOut(
        seal_id=row.id, task_id=row.task_id,
        expected_task_version=row.expected_task_version,
        actor_person_id=row.sealed_by_person_id,
        actor_authorization_version=row.authorization_version,
        trace_request_id=trace_request_id,
        sealed_at=row.sealed_at,
    )


def _error(code, category, message, *, cause=None):
    raise StocktakePostingSealError("stocktake_posting_seal_" + code, category, message) from cause


__all__ = ["StocktakePostingSealError", "seal_nonopening_stocktake_post_command"]
