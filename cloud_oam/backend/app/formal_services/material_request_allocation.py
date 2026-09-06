"""Create immutable source-allocation facts for approved material requests.

Allocation is deliberately a separate state axis.  This service records the
selected source and projection coordinates, but it does not move stock,
reserve a balance, create an outbound order, or claim shipment/receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from typing import Any, Mapping
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..demand_models import MaterialRequest, MaterialRequestLine
from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..inventory_models import (
    InventorySerial,
    SerialCurrentPosition,
    StockAccount,
    StockAllocation,
    StockAllocationSerial,
    StockBalance,
)
from ..models import User
from .audit_chain import AuditChainError, append_audit_event
from . import inventory_query


_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}
_ACTIVE_ALLOCATION_STATUS = "allocated"


class MaterialRequestAllocationError(RuntimeError):
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
class AllocationCreateInput:
    request_line_id: uuid.UUID
    source_stock_account_id: uuid.UUID
    allocated_qty: Decimal
    source_balance_version: int
    source_ledger_cursor: int
    serial_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class AllocationCommandResult:
    request_id: uuid.UUID
    allocation_id: uuid.UUID
    allocation_no: str
    request_version: int
    revision_id: uuid.UUID
    revision_no: int
    request_line_id: uuid.UUID
    source_stock_account_id: uuid.UUID
    allocated_qty: Decimal
    allocation_status: str
    request_status: str
    state_axes: Mapping[str, str]
    replayed: bool = False


def create_allocation(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_request_version: int,
    allocation: AllocationCreateInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> AllocationCommandResult:
    try:
        return _create_allocation_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            expected_request_version=expected_request_version,
            allocation=allocation,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except MaterialRequestAllocationError:
        raise
    except inventory_query.InventoryReadError as exc:
        category = {
            403: "forbidden",
            409: "conflict",
            412: "precondition_failed",
        }.get(exc.status_code, "service_unavailable")
        raise MaterialRequestAllocationError(
            exc.code, category, exc.public_message
        ) from exc
    except AuditChainError as exc:
        raise MaterialRequestAllocationError(
            "material_request_allocation_audit_unavailable", "service_unavailable", "分配审计链不可用，本次操作未完成"
        ) from exc
    except IntegrityError as exc:
        raise MaterialRequestAllocationError(
            "material_request_allocation_concurrent_conflict", "conflict", "分配发生并发冲突，请重新读取后再操作"
        ) from exc
    except DBAPIError as exc:
        raise MaterialRequestAllocationError(
            "material_request_allocation_database_unavailable", "service_unavailable", "分配数据库暂时不可用，本次操作未完成"
        ) from exc


def allocation_command_status(
    db: Session,
    *,
    actor: FormalPrincipal,
    trace_request_id: str,
) -> AllocationCommandResult | None:
    """Resolve one allocation write from its non-sensitive request trace.

    The audit event is the durable bridge because the allocation fact stores
    no raw request coordinate.  Any incomplete or contradictory evidence is a
    service error, never an ``not_observed`` result that could release a retry.
    """
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
        or not actor.role_codes
        or not set(actor.role_codes).intersection({"admin", "provincial_manager"})
    ):
        _fail("material_request_allocation_forbidden", "forbidden", "当前账号没有货源分配权限")
    if not isinstance(trace_request_id, str) or not trace_request_id.strip():
        _fail("material_request_allocation_trace_invalid", "invalid_request", "请求追踪坐标无效")
    # This endpoint is a recovery read.  Suppress autoflush so a dirty ORM
    # object in the request session can never turn the lookup into a write.
    with db.no_autoflush:
        audits = tuple(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.stream_key == "material_request",
                    AuditEvent.action == "material_request_allocation_created",
                    AuditEvent.actor_user_id == actor.user_id,
                    AuditEvent.request_id == trace_request_id,
                )
                .order_by(AuditEvent.id)
                .execution_options(populate_existing=True)
            ).all()
        )
    if not audits:
        return None
    if len(audits) != 1:
        _fail("material_request_allocation_history_invalid", "service_unavailable", "分配历史证据不完整")
    audit = audits[0]
    if (
        audit.action != "material_request_allocation_created"
        or audit.aggregate_type != "stock_allocation"
        or not isinstance(audit.after_jsonb, Mapping)
    ):
        _fail("material_request_allocation_history_invalid", "service_unavailable", "分配历史证据不匹配")
    try:
        allocation_id = uuid.UUID(str(audit.after_jsonb["allocation_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MaterialRequestAllocationError(
            "material_request_allocation_history_invalid", "service_unavailable", "分配历史证据无效"
        ) from exc
    if audit.aggregate_id != str(allocation_id):
        _fail("material_request_allocation_history_invalid", "service_unavailable", "分配审计对象不匹配")
    with db.no_autoflush:
        fact = db.scalar(
            select(StockAllocation)
            .where(StockAllocation.id == allocation_id)
            .execution_options(populate_existing=True)
        )
        request = (
            db.scalar(
                select(MaterialRequest)
                .where(MaterialRequest.id == fact.request_id)
                .execution_options(populate_existing=True)
            )
            if fact
            else None
        )
    if fact is None or request is None:
        _fail("material_request_allocation_history_invalid", "service_unavailable", "分配事实缺失")
    expected = {
        "allocation_no": fact.allocation_no,
        "request_id": str(fact.request_id),
        "request_line_id": str(fact.request_line_id),
        "source_stock_account_id": str(fact.source_stock_account_id),
        "allocated_qty": str(fact.allocated_qty),
        "source_balance_version": fact.source_balance_version,
        "source_ledger_cursor": fact.source_ledger_cursor,
    }
    if any(audit.after_jsonb.get(key) != value for key, value in expected.items()):
        _fail("material_request_allocation_history_invalid", "service_unavailable", "分配审计与事实不一致")
    if (
        fact.actor_user_id != actor.user_id
        or fact.actor_person_id != actor.person_id
        or fact.authorization_version != actor.authorization_version
    ):
        _fail("material_request_allocation_authorization_changed", "precondition_failed", "原分配授权版本已变化")
    return _result_from_existing(fact, request=request, replayed=True)


def _create_allocation_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_request_version: int,
    allocation: AllocationCreateInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> AllocationCommandResult:
    request_id = _uuid(material_request_id, "material_request_id")
    if not isinstance(expected_request_version, int) or isinstance(expected_request_version, bool) or expected_request_version < 0:
        _fail("material_request_allocation_request_version_invalid", "invalid_request", "需求版本无效")
    if not isinstance(trace_request_id, str) or not trace_request_id.strip():
        _fail("material_request_allocation_trace_invalid", "invalid_request", "请求追踪坐标无效")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
        or not actor.role_codes
        or not set(actor.role_codes).intersection({"admin", "provincial_manager"})
    ):
        _fail("material_request_allocation_forbidden", "forbidden", "当前账号没有货源分配权限")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        _fail("material_request_allocation_idempotency_invalid", "invalid_request", "幂等键无效")
    secret = idempotency_hmac_secret.encode() if isinstance(idempotency_hmac_secret, str) else idempotency_hmac_secret
    if not isinstance(secret, bytes) or len(secret) < 32:
        _fail("material_request_allocation_secret_invalid", "service_unavailable", "分配幂等密钥配置不可用")
    _validate_input(allocation)
    path = f"/api/v1/material-requests/{request_id}/allocations"
    key_hash = hmac.new(secret, f"{actor.user_id}:POST:{path}:{idempotency_key}".encode(), hashlib.sha256).hexdigest()
    request_hash = _canonical_hash({
        "request_id": str(request_id), "expected_request_version": expected_request_version,
        "request_line_id": str(allocation.request_line_id), "source_stock_account_id": str(allocation.source_stock_account_id),
        "allocated_qty": str(allocation.allocated_qty), "source_balance_version": allocation.source_balance_version,
        "source_ledger_cursor": allocation.source_ledger_cursor, "serial_ids": [str(value) for value in allocation.serial_ids],
        "actor_user_id": actor.user_id, "actor_person_id": str(actor.person_id),
        "authorization_version": actor.authorization_version,
    })
    lock_formal_principal_graph(db, (actor.user_id,))
    user = db.scalar(select(User).where(User.id == actor.user_id).with_for_update())
    if user is None or user.person_id != actor.person_id or user.authorization_version != actor.authorization_version:
        _fail("material_request_allocation_actor_changed", "forbidden", "分配期间身份或授权版本已变化")
    request = db.scalar(select(MaterialRequest).where(MaterialRequest.id == request_id).with_for_update())
    if request is None:
        _fail("material_request_not_found", "not_found", "需求单不存在")
    existing = db.scalar(
        select(StockAllocation)
        .where(StockAllocation.idempotency_key_hash == key_hash)
        .with_for_update()
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            _fail("material_request_allocation_idempotency_reused", "conflict", "幂等键已绑定其他分配内容")
        if existing.request_id != request_id:
            _fail("material_request_allocation_idempotency_reused", "conflict", "幂等键已绑定其他需求单")
        return _result_from_existing(existing, request=request, replayed=True)
    if request.version != expected_request_version:
        _fail("material_request_version_conflict", "conflict", "需求版本已变化，请重新读取")
    if request.status not in {"approved", "partially_approved"}:
        _fail("material_request_not_approved", "precondition_failed", "需求单尚未完成最终审批")
    line = db.scalar(select(MaterialRequestLine).where(MaterialRequestLine.id == allocation.request_line_id, MaterialRequestLine.request_id == request_id).with_for_update())
    if line is None:
        _fail("material_request_line_not_found", "not_found", "需求单明细不存在")
    if line.revision_no != request.revision_no or line.revision_id != _current_revision_id(db, request_id, request.revision_no):
        _fail("material_request_line_revision_stale", "conflict", "需求单明细版本已变化，请重新读取")
    if line.status not in {"approved", "partially_approved"}:
        _fail("material_request_line_not_approved", "precondition_failed", "需求单明细尚未完成最终审批")
    remaining = line.final_approved_qty - line.cancelled_qty
    prior_line_allocated = db.scalar(select(func.coalesce(func.sum(StockAllocation.allocated_qty), 0)).where(StockAllocation.request_line_id == line.id, StockAllocation.status == _ACTIVE_ALLOCATION_STATUS)) or Decimal("0")
    if prior_line_allocated + allocation.allocated_qty > remaining:
        _fail("material_request_allocation_quantity_exceeded", "precondition_failed", "分配数量超过明细可分配余量")

    inventory_query._require_inventory_read(db, actor)
    snapshot = inventory_query._projection_snapshot(db)
    rows = inventory_query._authorized_account_rows(db, actor=actor)
    inventory_query._validate_current_projection_integrity(db, snapshot=snapshot, account_ids={row.account.id for row in rows})
    evidence = inventory_query._validated_opening_evidence(db, actor=actor, snapshot=snapshot, required_pairs={(row.account.owner_org_id, row.account.location_id) for row in rows}, discover_authorized_zero_scopes=True)
    if not evidence.complete:
        _fail("inventory_opening_not_established", "precondition_failed", "库存期初建账尚未完成")
    source_row = next((row for row in rows if row.account.id == allocation.source_stock_account_id), None)
    if source_row is None:
        _fail("material_request_allocation_source_forbidden", "forbidden", "货源不在当前账号授权范围内")
    account = db.scalar(select(StockAccount).where(StockAccount.id == allocation.source_stock_account_id).with_for_update())
    balance = db.scalar(select(StockBalance).where(StockBalance.stock_account_id == allocation.source_stock_account_id).with_for_update())
    if account is None or balance is None or account.availability_bucket != "available" or account.material_id != line.material_id:
        _fail("material_request_allocation_source_invalid", "conflict", "货源已变化或与需求物料不匹配")
    if balance.version != allocation.source_balance_version or balance.ledger_cursor != allocation.source_ledger_cursor:
        _fail("material_request_allocation_source_stale", "conflict", "货源余额版本已变化，请重新读取")
    if balance.quantity < allocation.allocated_qty:
        _fail("material_request_allocation_source_insufficient", "precondition_failed", "货源可用量不足")
    policy = inventory_query._effective_policy(db, line.material_id)
    _validate_serial_binding(db, account, policy.tracking_mode, allocation)

    now = datetime.now(timezone.utc)
    allocation_id = uuid.uuid4()
    allocation_no = f"AL-{now.strftime('%Y%m%d')}-{str(allocation_id).split('-', 1)[0].upper()}"
    fact = StockAllocation(
        id=allocation_id, allocation_no=allocation_no, request_id=request_id, request_line_id=line.id,
        revision_id=line.revision_id, revision_no=line.revision_no, request_version=request.version,
        source_stock_account_id=account.id, allocated_qty=allocation.allocated_qty,
        source_balance_version=balance.version, source_ledger_cursor=balance.ledger_cursor,
        status=_ACTIVE_ALLOCATION_STATUS, idempotency_key_hash=key_hash, request_hash=request_hash,
        actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version, created_at=now, updated_at=now,
    )
    db.add(fact)
    db.flush()
    for serial_id in allocation.serial_ids:
        db.add(StockAllocationSerial(allocation_id=allocation_id, serial_id=serial_id, created_at=now))
    db.flush()
    approved_total = db.scalar(select(func.coalesce(func.sum(MaterialRequestLine.final_approved_qty - MaterialRequestLine.cancelled_qty), 0)).where(MaterialRequestLine.request_id == request_id, MaterialRequestLine.revision_no == request.revision_no, MaterialRequestLine.status.in_(("approved", "partially_approved")))) or Decimal("0")
    allocated_total = db.scalar(select(func.coalesce(func.sum(StockAllocation.allocated_qty), 0)).where(StockAllocation.request_id == request_id, StockAllocation.status == _ACTIVE_ALLOCATION_STATUS)) or Decimal("0")
    previous_status = request.allocation_status
    request.allocation_status = "allocated" if allocated_total >= approved_total else "partially_allocated"
    request.version += 1
    request.updated_at = now
    db.add(StateTransitionEvent(
        aggregate_type="material_request", aggregate_id=str(request_id), from_status=previous_status,
        to_status=request.allocation_status, reason="material_request_allocation_created", actor_id=actor.user_id,
        idempotency_key=f"allocation-state-{key_hash}", occurred_at=now,
        metadata_jsonb={"allocation_id": str(allocation_id), "allocated_qty": str(allocation.allocated_qty)},
    ))
    append_audit_event(
        db, stream_key="material_request", actor_user_id=actor.user_id, action="material_request_allocation_created",
        aggregate_type="stock_allocation", aggregate_id=str(allocation_id),
        before_jsonb=None, after_jsonb={"allocation_no": allocation_no, "request_id": str(request_id), "request_line_id": str(line.id), "source_stock_account_id": str(account.id), "allocated_qty": str(allocation.allocated_qty), "source_balance_version": balance.version, "source_ledger_cursor": balance.ledger_cursor},
        request_id=trace_request_id, occurred_at=now,
    )
    db.flush()
    return AllocationCommandResult(
        request_id=request_id, allocation_id=allocation_id, allocation_no=allocation_no, request_version=request.version,
        revision_id=line.revision_id, revision_no=line.revision_no, request_line_id=line.id,
        source_stock_account_id=account.id, allocated_qty=allocation.allocated_qty,
        allocation_status=fact.status, request_status=request.status, state_axes=_state_axes(request),
    )


def _validate_input(value: AllocationCreateInput) -> None:
    if not isinstance(value, AllocationCreateInput) or value.allocated_qty <= 0 or value.source_balance_version < 0 or value.source_ledger_cursor < 0:
        _fail("material_request_allocation_input_invalid", "invalid_request", "分配内容无效")
    if len(set(value.serial_ids)) != len(value.serial_ids):
        _fail("material_request_allocation_serials_duplicate", "invalid_request", "分配 SN 不能重复")


def _validate_serial_binding(db: Session, account: StockAccount, tracking_mode: str, allocation: AllocationCreateInput) -> None:
    serial_mode = tracking_mode in {"serial", "lot_and_serial"}
    if not serial_mode and allocation.serial_ids:
        _fail("material_request_allocation_serials_unexpected", "invalid_request", "非 SN 物料不能提交 SN")
    if serial_mode:
        if allocation.allocated_qty != allocation.allocated_qty.to_integral_value() or len(allocation.serial_ids) != int(allocation.allocated_qty):
            _fail("material_request_allocation_serials_required", "invalid_request", "SN 物料必须逐件绑定整数数量")
        positions = dict(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id).where(SerialCurrentPosition.serial_id.in_(allocation.serial_ids))).all())
        serials = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(allocation.serial_ids))).all())
        if len(serials) != len(allocation.serial_ids) or any(row.material_id != account.material_id or row.lifecycle_status != "active" or positions.get(row.id) != account.id for row in serials):
            _fail("material_request_allocation_serial_invalid", "conflict", "SN 不属于当前可用货源")


def _current_revision_id(db: Session, request_id: uuid.UUID, revision_no: int) -> uuid.UUID | None:
    from ..demand_models import MaterialRequestRevision
    return db.scalar(select(MaterialRequestRevision.id).where(MaterialRequestRevision.request_id == request_id, MaterialRequestRevision.revision_no == revision_no))


def _result_from_existing(
    fact: StockAllocation, *, request: MaterialRequest, replayed: bool
) -> AllocationCommandResult:
    return AllocationCommandResult(
        request_id=fact.request_id, allocation_id=fact.id, allocation_no=fact.allocation_no,
        request_version=request.version, revision_id=fact.revision_id, revision_no=fact.revision_no,
        request_line_id=fact.request_line_id, source_stock_account_id=fact.source_stock_account_id,
        allocated_qty=fact.allocated_qty, allocation_status=fact.status,
        request_status=request.status, state_axes=_state_axes(request), replayed=replayed,
    )


def _state_axes(request: MaterialRequest) -> dict[str, str]:
    axes = {"request_status": request.status}
    axes.update({name: getattr(request, name) for name in ("allocation_status", "reservation_status", "outbound_status", "shipment_status", "logistics_signature_status", "oam_receipt_status", "personal_inbound_status", "notification_status", "reconciliation_status")})
    return axes


def _uuid(value: object, name: str) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(f"material_request_allocation_{name}_invalid", "invalid_request", f"{name}无效")
    return value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestAllocationError(code, category, message)


__all__ = [
    "AllocationCommandResult",
    "AllocationCreateInput",
    "MaterialRequestAllocationError",
    "allocation_command_status",
    "create_allocation",
]
