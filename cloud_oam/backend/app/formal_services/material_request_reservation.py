"""Append immutable inventory-reservation facts for approved allocations.

Reservation is the first post-allocation movement in the V1.0 fulfilment
chain.  A reservation moves quantity from an ``available`` stock account to a
pre-provisioned ``reserved`` account through the formal inventory posting
primitive.  It then appends the request command, aggregate projection, state
event and audit evidence in the same caller-owned transaction.

The service intentionally has no release, pick, outbound, shipment, receipt or
inbound operation.  Those operations have separate facts and state axes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import re
from typing import Any, Mapping
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..demand_models import MaterialRequest, MaterialRequestCommand, MaterialRequestLine
from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..inventory_models import (
    InventoryMovement,
    InventoryMovementSerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    StockAccount,
    StockAllocation,
    StockAllocationSerial,
    StockBalance,
    StockReservation,
    StockReservationSerial,
)
from ..models import User
from . import inventory_query, material_request_query
from .audit_chain import AuditChainError, append_audit_event, verify_audit_event_in_stream
from .inventory_posting import (
    InventoryMovementCommand,
    InventoryPostingCommand,
    InventoryPostingError,
    post_inventory_transaction,
    _posting_document,
    _storage_hash,
    _lock_inventory_ledger_head_for_atomic_batch,
)


_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}
_RESERVATION_STATUS = "reserved"
_COMMAND_SCHEMA = "rsc.material_request_reservation_command.v1"
_RESULT_SCHEMA_VERSION = "1.0"
_AUDIT_ACTION = "material_request_reservation_created"
_AUDIT_STREAM = "material_request"
_AUDIT_AGGREGATE = "stock_reservation"
_RESERVATION_ROLES = frozenset({"admin", "provincial_manager"})
_DECIMAL_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d{1,3})?$", re.ASCII)
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_QUANTITY = Decimal("1000000000000000")
_ZERO = Decimal("0.000")


class MaterialRequestReservationError(RuntimeError):
    """Stable public error for the reservation command boundary."""

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
class ReservationCreateInput:
    request_line_id: uuid.UUID
    allocation_id: uuid.UUID
    reserved_qty: Decimal
    source_balance_version: int
    source_ledger_cursor: int
    serial_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class ReservationCommandResult:
    request_id: uuid.UUID
    reservation_id: uuid.UUID
    reservation_no: str
    request_version: int
    revision_id: uuid.UUID
    revision_no: int
    request_line_id: uuid.UUID
    allocation_id: uuid.UUID
    source_stock_account_id: uuid.UUID
    stock_account_id: uuid.UUID
    reserve_transaction_id: uuid.UUID
    reserve_transaction_no: str
    reserved_qty: Decimal
    reservation_status: str
    request_status: str
    state_axes: Mapping[str, str]
    source_balance_version: int
    source_ledger_cursor: int
    serial_ids: tuple[uuid.UUID, ...]
    replayed: bool = False
    current_request_version: int | None = None


def create_reservation(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_request_version: int,
    reservation: ReservationCreateInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> ReservationCommandResult:
    """Reserve one quantity slice and append all linked immutable facts.

    The caller owns commit/rollback.  Any exception after the inventory post
    must roll back the complete transaction, including the inventory movement.
    """

    try:
        return _create_reservation_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            expected_request_version=expected_request_version,
            reservation=reservation,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except MaterialRequestReservationError:
        raise
    except InventoryPostingError as exc:
        raise MaterialRequestReservationError(
            exc.code, exc.category, exc.message
        ) from exc
    except inventory_query.InventoryReadError as exc:
        category = {
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            412: "precondition_failed",
        }.get(exc.status_code, "service_unavailable")
        raise MaterialRequestReservationError(
            exc.code, category, exc.public_message
        ) from exc
    except AuditChainError as exc:
        raise MaterialRequestReservationError(
            "material_request_reservation_audit_unavailable",
            "service_unavailable",
            "预约审计链不可用，本次操作未完成",
        ) from exc
    except IntegrityError as exc:
        raise MaterialRequestReservationError(
            "material_request_reservation_concurrent_conflict",
            "conflict",
            "预约发生并发冲突，请重新读取后再操作",
        ) from exc
    except DBAPIError as exc:
        raise MaterialRequestReservationError(
            "material_request_reservation_database_unavailable",
            "service_unavailable",
            "预约数据库暂时不可用，本次操作未完成",
        ) from exc


def reservation_command_status(
    db: Session,
    *,
    actor: FormalPrincipal,
    trace_request_id: str,
) -> ReservationCommandResult | None:
    """Read the durable result of a possibly interrupted reservation write."""

    _require_actor(actor)
    if not isinstance(trace_request_id, str) or not 8 <= len(trace_request_id) <= 160 or not _PRINTABLE.fullmatch(trace_request_id):
        _fail(
            "material_request_reservation_trace_invalid",
            "invalid_request",
            "请求追踪坐标无效",
        )
    with db.no_autoflush:
        audits = tuple(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.stream_key == _AUDIT_STREAM,
                    AuditEvent.action == _AUDIT_ACTION,
                    AuditEvent.actor_user_id == actor.user_id,
                    AuditEvent.request_id == trace_request_id,
                )
                .order_by(AuditEvent.id)
                .limit(2)
                .execution_options(populate_existing=True)
            ).all()
        )
    if not audits:
        return None
    if len(audits) != 1:
        _fail(
            "material_request_reservation_history_invalid",
            "service_unavailable",
            "预约历史证据不完整",
        )
    audit = audits[0]
    if (
        audit.aggregate_type != _AUDIT_AGGREGATE
        or audit.action != _AUDIT_ACTION
        or not isinstance(audit.after_jsonb, Mapping)
    ):
        _fail(
            "material_request_reservation_history_invalid",
            "service_unavailable",
            "预约历史证据不匹配",
        )
    try:
        reservation_id = uuid.UUID(str(audit.after_jsonb["reservation_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MaterialRequestReservationError(
            "material_request_reservation_history_invalid",
            "service_unavailable",
            "预约历史证据无效",
        ) from exc
    if audit.aggregate_id != str(reservation_id):
        _fail(
            "material_request_reservation_history_invalid",
            "service_unavailable",
            "预约审计对象不匹配",
        )
    read_context = _request_read_context(db, actor)
    with db.no_autoflush:
        fact = db.scalar(
            select(StockReservation)
            .where(StockReservation.id == reservation_id)
            .execution_options(populate_existing=True)
        )
        request = (
            db.scalar(
                select(MaterialRequest)
                .where(
                    MaterialRequest.id == fact.request_id,
                    material_request_query._visible_request_predicate(read_context),
                )
                .execution_options(populate_existing=True)
            )
            if fact is not None
            else None
        )
    if fact is not None and request is None:
        _fail("material_request_not_found", "not_found", "需求单不存在")
    if fact is None:
        _fail(
            "material_request_reservation_history_invalid",
            "service_unavailable",
            "预约事实或命令证据缺失",
        )
    if (
        fact.actor_user_id != actor.user_id
        or fact.actor_person_id != actor.person_id
        or fact.authorization_version != actor.authorization_version
    ):
        _fail(
            "material_request_reservation_authorization_changed",
            "precondition_failed",
            "原预约授权版本已变化",
        )
    command, transaction = _verified_history(db, fact=fact, request=request)
    return _result_from_existing(
        fact,
        request=request,
        command=command,
        replayed=True,
        reserve_transaction_no=transaction.transaction_no,
    )


def _create_reservation_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_request_version: int,
    reservation: ReservationCreateInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> ReservationCommandResult:
    request_id = _uuid(material_request_id, "material_request_id")
    _require_actor(actor)
    if (
        not isinstance(expected_request_version, int)
        or isinstance(expected_request_version, bool)
        or expected_request_version < 0
    ):
        _fail(
            "material_request_reservation_request_version_invalid",
            "invalid_request",
            "需求版本无效",
        )
    if not isinstance(trace_request_id, str) or not 8 <= len(trace_request_id) <= 160 or not _PRINTABLE.fullmatch(trace_request_id):
        _fail(
            "material_request_reservation_trace_invalid",
            "invalid_request",
            "请求追踪坐标无效",
        )
    if (
        not isinstance(idempotency_key, str)
        or not 16 <= len(idempotency_key) <= 200
        or _PRINTABLE.fullmatch(idempotency_key) is None
    ):
        _fail(
            "material_request_reservation_idempotency_invalid",
            "invalid_request",
            "幂等键无效",
        )
    secret = (
        idempotency_hmac_secret.encode()
        if isinstance(idempotency_hmac_secret, str)
        else idempotency_hmac_secret
    )
    if not isinstance(secret, bytes) or len(secret) < 32:
        _fail(
            "material_request_reservation_secret_invalid",
            "service_unavailable",
            "预约幂等密钥配置不可用",
        )
    _validate_input(reservation)
    reservation = replace(
        reservation, reserved_qty=_quantity_decimal(reservation.reserved_qty)
    )
    path = f"/api/v1/material-requests/{request_id}/reservations"
    key_hash = hmac.new(
        secret,
        f"{actor.user_id}:POST:{path}:{idempotency_key}".encode(),
        hashlib.sha256,
    ).hexdigest()
    request_hash = _canonical_hash(
        {
            "request_id": str(request_id),
            "expected_request_version": expected_request_version,
            "request_line_id": str(reservation.request_line_id),
            "allocation_id": str(reservation.allocation_id),
            "reserved_qty": _quantity_text(reservation.reserved_qty),
            "source_balance_version": reservation.source_balance_version,
            "source_ledger_cursor": reservation.source_ledger_cursor,
            "serial_ids": [str(value) for value in reservation.serial_ids],
            "actor_user_id": actor.user_id,
            "actor_person_id": str(actor.person_id),
            "authorization_version": actor.authorization_version,
        }
    )

    # Keep the ledger -> principal -> request order shared with release and
    # stocktake. No immutable fact or SELECT-only account is row-locked here.
    _lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    user = db.scalar(select(User).where(User.id == actor.user_id).with_for_update())
    if (
        user is None
        or user.person_id != actor.person_id
        or user.authorization_version != actor.authorization_version
    ):
        _fail(
            "material_request_reservation_actor_changed",
            "forbidden",
            "预约期间身份或授权版本已变化",
        )
    read_context = _request_read_context(db, actor)
    request = db.scalar(
        select(MaterialRequest).where(
            MaterialRequest.id == request_id,
            material_request_query._visible_request_predicate(read_context),
        ).with_for_update()
    )
    if request is None:
        _fail("material_request_not_found", "not_found", "需求单不存在")

    existing = db.scalar(
        select(StockReservation)
        .where(StockReservation.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if existing.request_hash != request_hash or existing.request_id != request_id:
            _fail(
                "material_request_reservation_idempotency_reused",
                "conflict",
                "幂等键已绑定其他预约内容",
            )
        command, transaction = _verified_history(db, fact=existing, request=request)
        return _result_from_existing(
            existing,
            request=request,
            command=command,
            replayed=True,
            reserve_transaction_no=transaction.transaction_no,
        )

    if request.version != expected_request_version:
        _fail("material_request_version_conflict", "conflict", "需求版本已变化，请重新读取")
    if request.status not in {"approved", "partially_approved"}:
        _fail(
            "material_request_not_approved",
            "precondition_failed",
            "需求单尚未完成最终审批",
        )
    if request.allocation_status not in {"partially_allocated", "allocated"}:
        _fail(
            "material_request_allocation_required",
            "precondition_failed",
            "需求明细尚未完成分配，不能预约库存",
        )

    line = db.scalar(
        select(MaterialRequestLine)
        .where(
            MaterialRequestLine.id == reservation.request_line_id,
            MaterialRequestLine.request_id == request_id,
        )
        .with_for_update()
    )
    if line is None:
        _fail("material_request_line_not_found", "not_found", "需求单明细不存在")
    if (
        line.revision_no != request.revision_no
        or line.revision_id != _current_revision_id(db, request_id, request.revision_no)
    ):
        _fail(
            "material_request_line_revision_stale",
            "conflict",
            "需求单明细版本已变化，请重新读取",
        )
    if line.status not in {"approved", "partially_approved"}:
        _fail(
            "material_request_line_not_approved",
            "precondition_failed",
            "需求单明细尚未完成最终审批",
        )

    allocation = db.scalar(
        select(StockAllocation)
        .where(
            StockAllocation.id == reservation.allocation_id,
            StockAllocation.request_id == request_id,
            StockAllocation.request_line_id == line.id,
            StockAllocation.revision_id == line.revision_id,
            StockAllocation.status == "allocated",
        )
        .execution_options(populate_existing=True)
    )
    if allocation is None:
        _fail(
            "material_request_allocation_not_found",
            "not_found",
            "分配事实不存在或已过期",
        )
    if allocation.source_stock_account_id is None:
        _fail(
            "material_request_allocation_source_invalid",
            "conflict",
            "分配货源坐标无效",
        )

    prior_reserved = db.scalar(
        select(func.coalesce(func.sum(StockReservation.reserved_qty), 0)).where(
            StockReservation.allocation_id == allocation.id,
        )
    ) or _ZERO
    if prior_reserved + reservation.reserved_qty > allocation.allocated_qty:
        _fail(
            "material_request_reservation_quantity_exceeded",
            "precondition_failed",
            "预约数量超过分配余量",
        )

    source = db.scalar(
        select(StockAccount)
        .where(StockAccount.id == allocation.source_stock_account_id)
        .execution_options(populate_existing=True)
    )
    if source is None or source.availability_bucket != "available" or source.material_id != line.material_id:
        _fail(
            "material_request_reservation_source_invalid",
            "conflict",
            "分配货源已变化或与需求物料不匹配",
        )
    source_balance = db.scalar(
        select(StockBalance)
        .where(StockBalance.stock_account_id == source.id)
        .execution_options(populate_existing=True)
    )
    if source_balance is None:
        _fail(
            "material_request_reservation_source_missing_balance",
            "precondition_failed",
            "分配货源缺少余额投影",
        )
    if (
        source_balance.version != reservation.source_balance_version
        or source_balance.ledger_cursor != reservation.source_ledger_cursor
    ):
        _fail(
            "material_request_reservation_source_stale",
            "conflict",
            "货源余额版本已变化，请重新读取",
        )
    if source_balance.quantity < reservation.reserved_qty:
        _fail(
            "material_request_reservation_source_insufficient",
            "precondition_failed",
            "货源可用量不足",
        )
    source_quantity_before = source_balance.quantity

    target = _reserved_target_account(db, source)
    policy = inventory_query._effective_policy(db, line.material_id)
    _validate_serial_binding(
        db,
        allocation=allocation,
        source=source,
        tracking_mode=policy.tracking_mode,
        reservation=reservation,
    )

    now = datetime.now(timezone.utc)
    reservation_id = uuid.uuid4()
    reservation_no = f"RS-{now.strftime('%Y%m%d')}-{reservation_id.hex[:12].upper()}"
    transaction_no = f"INV-RES-{reservation_id.hex[:16].upper()}"
    posting_key = f"material-request-reservation:{reservation_id}"
    posting_result = post_inventory_transaction(
        db,
        actor=actor,
        command=InventoryPostingCommand(
            transaction_no=transaction_no,
            movement_type="reserve",
            source_document_type="material_request_reservation",
            source_document_id=str(reservation_id),
            posting_key=posting_key,
            effective_at=now,
            movements=(
                InventoryMovementCommand(
                    from_account_id=source.id,
                    to_account_id=target.id,
                    quantity=reservation.reserved_qty,
                    serial_ids=tuple(reservation.serial_ids),
                    external_boundary_code=None,
                ),
            ),
        ),
        idempotency_key=f"mr-reservation-{key_hash}",
        request_id=trace_request_id,
    )
    # The posting primitive owns the final account/balance locks.  Re-read the
    # source projection after it returns and prove that this command consumed
    # exactly the submitted coordinate.  A concurrent write therefore fails
    # closed even if it raced between the preflight read and posting.
    db.expire(source_balance)
    source_after = db.scalar(
        select(StockBalance)
        .where(StockBalance.stock_account_id == source.id)
        .execution_options(populate_existing=True)
    )
    if (
        source_after is None
        or source_after.version != reservation.source_balance_version + 1
        or source_after.ledger_cursor != posting_result.ledger_cursor
        or source_after.quantity != source_quantity_before - reservation.reserved_qty
    ):
        _fail(
            "material_request_reservation_source_projection_invalid",
            "service_unavailable",
            "预约后的货源余额投影无法精确回读，本次操作未完成",
        )

    resulting_request_version = request.version + 1
    db.add(
        StockReservation(
            id=reservation_id,
            reservation_no=reservation_no,
            request_id=request_id,
            request_line_id=line.id,
            revision_id=line.revision_id,
            revision_no=line.revision_no,
            allocation_id=allocation.id,
            source_stock_account_id=source.id,
            stock_account_id=target.id,
            reserved_qty=reservation.reserved_qty,
            released_qty=_ZERO,
            reserve_transaction_id=posting_result.transaction_id,
            release_transaction_id=None,
            status=_RESERVATION_STATUS,
            request_version=resulting_request_version,
            idempotency_key_hash=key_hash,
            request_hash=request_hash,
            actor_user_id=actor.user_id,
            actor_person_id=actor.person_id,
            authorization_version=actor.authorization_version,
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()
    for serial_id in reservation.serial_ids:
        db.add(
            StockReservationSerial(
                reservation_id=reservation_id,
                allocation_id=allocation.id,
                serial_id=serial_id,
                created_at=now,
            )
        )
    db.flush()

    from .material_request_reservation_release import reservation_state
    previous_status = request.reservation_status
    aggregate_status = reservation_state(db, request)
    # 0069's request guard is a BEFORE UPDATE trigger.  Insert the immutable
    # command first so the subsequent aggregate update can prove its exact
    # target version and reservation fact without a trigger bypass.
    state_axes = _state_axes(request)
    state_axes["reservation_status"] = aggregate_status
    result_document = {
        "kind": "reservation",
        "schema_version": _RESULT_SCHEMA_VERSION,
        "request_id": str(request_id),
        "request_no": request.request_no,
        "request_version": resulting_request_version,
        "revision_id": str(line.revision_id),
        "revision_no": line.revision_no,
        "request_line_id": str(line.id),
        "allocation_id": str(allocation.id),
        "source_stock_account_id": str(source.id),
        "stock_account_id": str(target.id),
        "reserved_qty": _quantity_text(reservation.reserved_qty),
        "source_balance_version": reservation.source_balance_version,
        "source_ledger_cursor": reservation.source_ledger_cursor,
        "serial_ids": [str(value) for value in reservation.serial_ids],
        "reservation_id": str(reservation_id),
        "reservation_no": reservation_no,
        "reserve_transaction_id": str(posting_result.transaction_id),
        "reserve_transaction_no": posting_result.transaction_no,
        "reservation_status": _RESERVATION_STATUS,
        "request_status": request.status,
        "state_axes": state_axes,
    }
    command = MaterialRequestCommand(
        id=uuid.uuid4(),
        operation="reserve",
        request_id=request_id,
        target_version=resulting_request_version,
        idempotency_key_hash=key_hash,
        request_reference=path,
        request_hash=request_hash,
        result_hash=_canonical_hash(result_document),
        request_jsonb={
            "schema": _COMMAND_SCHEMA,
            "operation": "reserve",
            "request_id": str(request_id),
            "revision_id": str(line.revision_id),
            "revision_no": line.revision_no,
            "request_line_id": str(line.id),
            "allocation_id": str(allocation.id),
            "target_version": resulting_request_version,
            "reserved_qty": _quantity_text(reservation.reserved_qty),
            "source_stock_account_id": str(source.id),
            "stock_account_id": str(target.id),
            "reserve_transaction_id": str(posting_result.transaction_id),
            "source_balance_version": reservation.source_balance_version,
            "source_ledger_cursor": reservation.source_ledger_cursor,
            "serial_ids": [str(value) for value in reservation.serial_ids],
            "payload_sha256": request_hash,
            "comment_sha256": hashlib.sha256(b"").hexdigest(),
            "sensitive_fields": "excluded",
        },
        result_jsonb=result_document,
        actor_user_id=actor.user_id,
        actor_person_id=actor.person_id,
        actor_role_assignment_id=_actor_assignment_id(actor),
        authorization_version=actor.authorization_version,
        occurred_at=now,
        created_at=now,
    )
    db.add(command)
    db.flush()
    request.reservation_status = aggregate_status
    request.version = resulting_request_version
    request.updated_at = now
    db.flush()
    db.add(
        StateTransitionEvent(
            aggregate_type="material_request",
            aggregate_id=str(request_id),
            from_status=previous_status,
            to_status=aggregate_status,
            reason=_AUDIT_ACTION,
            actor_id=actor.user_id,
            idempotency_key=f"reservation-state-{key_hash}",
            occurred_at=now,
            metadata_jsonb={
                "reservation_id": str(reservation_id),
                "reserved_qty": _quantity_text(reservation.reserved_qty),
                "command_id": str(command.id),
                "idempotency_key_hash": key_hash,
                "request_version": resulting_request_version,
            },
            created_at=now,
        )
    )
    append_audit_event(
        db,
        stream_key=_AUDIT_STREAM,
        actor_user_id=actor.user_id,
        action=_AUDIT_ACTION,
        aggregate_type=_AUDIT_AGGREGATE,
        aggregate_id=str(reservation_id),
        before_jsonb={
            "request_version": resulting_request_version - 1,
            "reservation_status": previous_status,
        },
        after_jsonb={
            "command_id": str(command.id),
            "request_hash": command.request_hash,
            "result_hash": command.result_hash,
            "reservation_id": str(reservation_id),
            "reservation_no": reservation_no,
            "request_id": str(request_id),
            "request_line_id": str(line.id),
            "allocation_id": str(allocation.id),
            "source_stock_account_id": str(source.id),
            "stock_account_id": str(target.id),
            "reserved_qty": _quantity_text(reservation.reserved_qty),
            "reserve_transaction_id": str(posting_result.transaction_id),
            "request_version": resulting_request_version,
            "reservation_status": aggregate_status,
        },
        request_id=trace_request_id,
        occurred_at=now,
        created_at=now,
    )
    db.flush()
    return ReservationCommandResult(
        request_id=request_id,
        reservation_id=reservation_id,
        reservation_no=reservation_no,
        request_version=resulting_request_version,
        current_request_version=resulting_request_version,
        revision_id=line.revision_id,
        revision_no=line.revision_no,
        request_line_id=line.id,
        allocation_id=allocation.id,
        source_stock_account_id=source.id,
        stock_account_id=target.id,
        reserve_transaction_id=posting_result.transaction_id,
        reserve_transaction_no=posting_result.transaction_no,
        reserved_qty=reservation.reserved_qty,
        reservation_status=_RESERVATION_STATUS,
        request_status=request.status,
        state_axes=state_axes,
        source_balance_version=reservation.source_balance_version,
        source_ledger_cursor=reservation.source_ledger_cursor,
        serial_ids=reservation.serial_ids,
    )


def _reserved_target_account(db: Session, source: StockAccount) -> StockAccount:
    rows = tuple(
        db.scalars(
            select(StockAccount)
            .where(
                StockAccount.owner_org_id == source.owner_org_id,
                StockAccount.custodian_person_id == source.custodian_person_id,
                StockAccount.location_id == source.location_id,
                StockAccount.material_id == source.material_id,
                StockAccount.condition_code == source.condition_code,
                StockAccount.availability_bucket == "reserved",
                StockAccount.lot_id == source.lot_id,
            )
            .order_by(StockAccount.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) != 1:
        _fail(
            "material_request_reservation_target_missing",
            "precondition_failed",
            "当前货源没有唯一匹配的预置占用账户",
        )
    if rows[0].id == source.id:
        _fail(
            "material_request_reservation_target_invalid",
            "service_unavailable",
            "占用账户不能与可用货源账户相同",
        )
    return rows[0]


def _validate_serial_binding(
    db: Session,
    *,
    allocation: StockAllocation,
    source: StockAccount,
    tracking_mode: str,
    reservation: ReservationCreateInput,
) -> None:
    serial_mode = tracking_mode in {"serial", "lot_and_serial"}
    if not serial_mode and reservation.serial_ids:
        _fail(
            "material_request_reservation_serials_unexpected",
            "invalid_request",
            "非 SN 物料不能提交 SN",
        )
    allocation_serials = {
        row.serial_id
        for row in db.scalars(
            select(StockAllocationSerial).where(
                StockAllocationSerial.allocation_id == allocation.id
            )
        ).all()
    }
    prior_serials = {
        row.serial_id
        for row in db.scalars(
            select(StockReservationSerial).where(
                StockReservationSerial.allocation_id == allocation.id
            )
        ).all()
    }
    if serial_mode:
        if reservation.reserved_qty != reservation.reserved_qty.to_integral_value() or len(reservation.serial_ids) != int(reservation.reserved_qty):
            _fail(
                "material_request_reservation_serials_required",
                "invalid_request",
                "SN 物料必须逐件绑定整数数量",
            )
        submitted = set(reservation.serial_ids)
        if submitted - allocation_serials or submitted & prior_serials or len(allocation_serials) < len(submitted):
            _fail(
                "material_request_reservation_serial_invalid",
                "conflict",
                "预约 SN 不属于当前分配或已被预约",
            )
    elif allocation_serials:
        _fail(
            "material_request_reservation_allocation_serial_invalid",
            "conflict",
            "分配事实包含 SN，但当前预约物料策略不允许继续预约",
        )


def _history_invalid() -> None:
    _fail(
        "material_request_reservation_history_invalid",
        "service_unavailable",
        "预约命令、库存流水或审计证据无法精确核对，保持结果待核验",
    )


def _historical_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        _history_invalid()
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _verified_history(
    db: Session, *, fact: StockReservation, request: MaterialRequest
) -> tuple[MaterialRequestCommand, InventoryTransaction]:
    """Prove the original command using immutable facts, never live candidates.

    Both recovery and idempotent replay use this path. Mutable balances and SN
    locations may have advanced; they cannot prove what this command posted.
    Every evidence collection is bounded and duplicate evidence fails closed.
    """
    with db.no_autoflush:
        commands = tuple(db.scalars(
            select(MaterialRequestCommand).where(
                MaterialRequestCommand.request_id == fact.request_id,
                MaterialRequestCommand.operation == "reserve",
                MaterialRequestCommand.target_version == fact.request_version,
            ).limit(2).execution_options(populate_existing=True)
        ).all())
        audits = tuple(db.scalars(
            select(AuditEvent).where(
                AuditEvent.stream_key == _AUDIT_STREAM,
                AuditEvent.action == _AUDIT_ACTION,
                AuditEvent.aggregate_type == _AUDIT_AGGREGATE,
                AuditEvent.aggregate_id == str(fact.id),
            ).limit(2).execution_options(populate_existing=True)
        ).all())
        transaction = db.scalar(
            select(InventoryTransaction).where(
                InventoryTransaction.id == fact.reserve_transaction_id
            ).execution_options(populate_existing=True)
        )
        movements = tuple(db.scalars(
            select(InventoryMovement).where(
                InventoryMovement.transaction_id == fact.reserve_transaction_id
            ).limit(2).execution_options(populate_existing=True)
        ).all())
        serials = tuple(db.scalars(
            select(StockReservationSerial).where(
                StockReservationSerial.reservation_id == fact.id
            ).limit(1001).execution_options(populate_existing=True)
        ).all())
        movement_serials = tuple(db.scalars(
            select(InventoryMovementSerial).where(
                InventoryMovementSerial.transaction_id == fact.reserve_transaction_id
            ).limit(1001).execution_options(populate_existing=True)
        ).all())
        events = tuple(db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.idempotency_key == f"reservation-state-{fact.idempotency_key_hash}"
            ).limit(2).execution_options(populate_existing=True)
        ).all())
    if (
        len(commands) != 1 or len(audits) != 1 or transaction is None
        or len(movements) != 1 or len(events) != 1
        or len(serials) > 1000 or len(movement_serials) > 1000
    ):
        _history_invalid()
    command, audit, movement, event = commands[0], audits[0], movements[0], events[0]
    result, document = command.result_jsonb, command.request_jsonb
    if not isinstance(result, dict) or not isinstance(document, dict):
        _history_invalid()
    balance_version = result.get("source_balance_version")
    ledger_cursor = result.get("source_ledger_cursor")
    raw_serial_ids = result.get("serial_ids")
    axes = result.get("state_axes")
    if (
        type(balance_version) is not int or balance_version < 0
        or type(ledger_cursor) is not int or ledger_cursor < 0
        or not isinstance(raw_serial_ids, list) or len(raw_serial_ids) > 1000
        or not isinstance(axes, dict) or set(axes) != set(_state_axes(request))
        or any(type(value) is not str for value in axes.values())
        or axes.get("request_status") not in {"approved", "partially_approved"}
        or axes.get("reservation_status") not in {"pending", "reserved", "partially_released"}
    ):
        _history_invalid()
    try:
        serial_ids = tuple(uuid.UUID(value) for value in raw_serial_ids)
    except (ValueError, TypeError, AttributeError):
        _history_invalid()
    if (
        len(set(serial_ids)) != len(serial_ids) or any(value.int == 0 for value in serial_ids)
        or [str(value) for value in serial_ids] != raw_serial_ids
    ):
        _history_invalid()
    quantity = _quantity_text(fact.reserved_qty)
    # SQLite's timestamp adapter loses UTC offsets; production timestamptz
    # preserves them. Both represent the original UTC instant.
    effective_at = _historical_utc(fact.created_at)
    inventory_hash = _canonical_hash({
        "operation": "post",
        "actor": {
            "user_id": fact.actor_user_id, "person_id": str(fact.actor_person_id),
            "authorization_version": fact.authorization_version,
        },
        "command": _posting_document(InventoryPostingCommand(
            transaction_no=transaction.transaction_no, movement_type="reserve",
            source_document_type="material_request_reservation", source_document_id=str(fact.id),
            posting_key=f"material-request-reservation:{fact.id}", effective_at=effective_at,
            movements=(InventoryMovementCommand(
                from_account_id=fact.source_stock_account_id, to_account_id=fact.stock_account_id,
                quantity=fact.reserved_qty, serial_ids=tuple(sorted(serial_ids, key=str)),
                external_boundary_code=None,
            ),),
        )),
    })
    expected_result = {
        "kind": "reservation", "schema_version": _RESULT_SCHEMA_VERSION,
        "request_id": str(fact.request_id), "request_no": request.request_no,
        "request_version": fact.request_version,
        "revision_id": str(fact.revision_id), "revision_no": fact.revision_no,
        "request_line_id": str(fact.request_line_id), "allocation_id": str(fact.allocation_id),
        "source_stock_account_id": str(fact.source_stock_account_id),
        "stock_account_id": str(fact.stock_account_id), "reserved_qty": quantity,
        "source_balance_version": balance_version, "source_ledger_cursor": ledger_cursor,
        "serial_ids": raw_serial_ids,
        "reservation_id": str(fact.id), "reservation_no": fact.reservation_no,
        "reserve_transaction_id": str(fact.reserve_transaction_id),
        "reserve_transaction_no": transaction.transaction_no,
        "reservation_status": _RESERVATION_STATUS,
        "request_status": axes["request_status"], "state_axes": axes,
    }
    payload_hash = _canonical_hash({
        "request_id": str(fact.request_id), "expected_request_version": fact.request_version - 1,
        "request_line_id": str(fact.request_line_id), "allocation_id": str(fact.allocation_id),
        "reserved_qty": quantity, "source_balance_version": balance_version,
        "source_ledger_cursor": ledger_cursor, "serial_ids": raw_serial_ids,
        "actor_user_id": fact.actor_user_id, "actor_person_id": str(fact.actor_person_id),
        "authorization_version": fact.authorization_version,
    })
    expected_document = {
        "schema": _COMMAND_SCHEMA, "operation": "reserve", "request_id": str(fact.request_id),
        "revision_id": str(fact.revision_id), "revision_no": fact.revision_no,
        "request_line_id": str(fact.request_line_id), "allocation_id": str(fact.allocation_id),
        "target_version": fact.request_version, "reserved_qty": quantity,
        "source_stock_account_id": str(fact.source_stock_account_id),
        "stock_account_id": str(fact.stock_account_id),
        "reserve_transaction_id": str(fact.reserve_transaction_id),
        "source_balance_version": balance_version, "source_ledger_cursor": ledger_cursor,
        "serial_ids": raw_serial_ids, "payload_sha256": payload_hash,
        "comment_sha256": hashlib.sha256(b"").hexdigest(), "sensitive_fields": "excluded",
    }
    expected_after = {
        "command_id": str(command.id), "request_hash": command.request_hash,
        "result_hash": command.result_hash,
        "reservation_id": str(fact.id), "reservation_no": fact.reservation_no,
        "request_id": str(fact.request_id), "request_line_id": str(fact.request_line_id),
        "allocation_id": str(fact.allocation_id),
        "source_stock_account_id": str(fact.source_stock_account_id),
        "stock_account_id": str(fact.stock_account_id), "reserved_qty": quantity,
        "reserve_transaction_id": str(fact.reserve_transaction_id),
        "request_version": fact.request_version, "reservation_status": axes["reservation_status"],
    }
    if (
        fact.status != _RESERVATION_STATUS or fact.released_qty != _ZERO
        or fact.release_transaction_id is not None or request.id != fact.request_id
        or request.version < fact.request_version
        or (request.version == fact.request_version and _state_axes(request) != axes)
        or command.idempotency_key_hash != fact.idempotency_key_hash
        or command.request_hash != fact.request_hash or fact.request_hash != payload_hash
        or command.request_reference != f"/api/v1/material-requests/{fact.request_id}/reservations"
        or command.actor_user_id != fact.actor_user_id or command.actor_person_id != fact.actor_person_id
        or command.authorization_version != fact.authorization_version
        or _historical_utc(command.occurred_at) != effective_at
        or _historical_utc(audit.occurred_at) != effective_at
        or result != expected_result or document != expected_document
        or command.result_hash != _canonical_hash(expected_result)
        or audit.actor_user_id != fact.actor_user_id or audit.after_jsonb != expected_after
        or audit.before_jsonb != {"request_version": fact.request_version - 1, "reservation_status": event.from_status}
        or event.aggregate_type != "material_request" or event.aggregate_id != str(fact.request_id)
        or event.reason != _AUDIT_ACTION or event.actor_id != fact.actor_user_id
        or event.to_status != axes["reservation_status"]
        or event.metadata_jsonb != {
            "reservation_id": str(fact.id), "reserved_qty": quantity,
            "command_id": str(command.id), "idempotency_key_hash": fact.idempotency_key_hash,
            "request_version": fact.request_version,
        }
        or transaction.status != "posted" or transaction.movement_type != "reserve"
        or transaction.source_document_type != "material_request_reservation"
        or transaction.source_document_id != str(fact.id)
        or transaction.posting_key != f"material-request-reservation:{fact.id}"
        or transaction.actor_user_id != fact.actor_user_id or transaction.posted_at is None
        or _historical_utc(transaction.effective_at) != effective_at
        or transaction.request_hash != inventory_hash
        or transaction.idempotency_key_hash != _storage_hash(f"mr-reservation-{fact.idempotency_key_hash}")
        or transaction.reversed_transaction_id is not None
        or transaction.ledger_cursor is None or transaction.ledger_cursor <= ledger_cursor
        or movement.line_no != 1 or movement.from_account_id != fact.source_stock_account_id
        or movement.to_account_id != fact.stock_account_id or movement.quantity != fact.reserved_qty
        or movement.external_boundary_code is not None
        or any(row.allocation_id != fact.allocation_id for row in serials)
        or {row.serial_id for row in serials} != set(serial_ids)
        or len(serials) != len(serial_ids) or len(movement_serials) != len(serial_ids)
        or any(row.movement_id != movement.id for row in movement_serials)
        or {row.serial_id for row in movement_serials} != set(serial_ids)
        or (serial_ids and fact.reserved_qty != Decimal(len(serial_ids)))
    ):
        _history_invalid()
    try:
        verify_audit_event_in_stream(db, stream_key=_AUDIT_STREAM, event_id=audit.id)
    except AuditChainError:
        _history_invalid()
    return command, transaction


def _result_from_existing(
    fact: StockReservation,
    *,
    request: MaterialRequest,
    command: MaterialRequestCommand,
    replayed: bool,
    reserve_transaction_no: str = "",
) -> ReservationCommandResult:
    return ReservationCommandResult(
        request_id=fact.request_id,
        reservation_id=fact.id,
        reservation_no=fact.reservation_no,
        request_version=fact.request_version,
        current_request_version=request.version,
        revision_id=fact.revision_id,
        revision_no=fact.revision_no,
        request_line_id=fact.request_line_id,
        allocation_id=fact.allocation_id,
        source_stock_account_id=fact.source_stock_account_id,
        stock_account_id=fact.stock_account_id,
        reserve_transaction_id=fact.reserve_transaction_id,
        reserve_transaction_no=reserve_transaction_no,
        reserved_qty=fact.reserved_qty,
        reservation_status=fact.status,
        request_status=command.result_jsonb["request_status"],
        state_axes=dict(command.result_jsonb["state_axes"]),
        source_balance_version=command.result_jsonb["source_balance_version"],
        source_ledger_cursor=command.result_jsonb["source_ledger_cursor"],
        serial_ids=tuple(uuid.UUID(value) for value in command.result_jsonb["serial_ids"]),
        replayed=replayed,
    )


def _state_axes(request: MaterialRequest) -> dict[str, str]:
    return {
        "request_status": request.status,
        **{
            key: getattr(request, key)
            for key in (
                "allocation_status",
                "reservation_status",
                "outbound_status",
                "shipment_status",
                "logistics_signature_status",
                "oam_receipt_status",
                "personal_inbound_status",
                "notification_status",
                "reconciliation_status",
            )
        },
    }


def _validate_input(value: ReservationCreateInput) -> None:
    if not isinstance(value, ReservationCreateInput):
        _fail("material_request_reservation_input_invalid", "invalid_request", "预约内容无效")
    _uuid(value.request_line_id, "request_line_id")
    _uuid(value.allocation_id, "allocation_id")
    _quantity_decimal(value.reserved_qty)
    if (
        isinstance(value.source_balance_version, bool)
        or not isinstance(value.source_balance_version, int)
        or value.source_balance_version < 0
        or isinstance(value.source_ledger_cursor, bool)
        or not isinstance(value.source_ledger_cursor, int)
        or value.source_ledger_cursor < 0
    ):
        _fail("material_request_reservation_input_invalid", "invalid_request", "预约坐标无效")
    if not isinstance(value.serial_ids, tuple) or len(set(value.serial_ids)) != len(value.serial_ids):
        _fail("material_request_reservation_serials_duplicate", "invalid_request", "预约 SN 不能重复")
    for serial_id in value.serial_ids:
        _uuid(serial_id, "serial_id")


def _request_read_context(db: Session, actor: FormalPrincipal):
    try:
        context = material_request_query._load_read_context(db, actor=actor, now=None)
    except material_request_query.MaterialRequestReadError as exc:
        raise MaterialRequestReservationError(exc.code, exc.category, exc.message) from exc
    _require_actor(context.principal)
    if context.principal != actor:
        _fail(
            "material_request_reservation_authorization_changed",
            "precondition_failed",
            "需求或库存预约授权范围已变化，请重新读取",
        )
    return context


def _require_actor(actor: FormalPrincipal) -> None:
    if (
        not isinstance(actor, FormalPrincipal)
        and not all(hasattr(actor, name) for name in ("user_id", "person_id", "authorization_version", "role_codes"))
    ):
        _fail("material_request_reservation_forbidden", "forbidden", "当前账号没有库存预约权限")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
        or not set(actor.role_codes).intersection(_RESERVATION_ROLES)
    ):
        _fail("material_request_reservation_forbidden", "forbidden", "当前账号没有库存预约权限")


def _actor_assignment_id(actor: FormalPrincipal) -> uuid.UUID:
    candidates = tuple(
        sorted(
            (
                grant.assignment_id
                for grant in getattr(actor, "assignments", ())
                if grant.role_code in _RESERVATION_ROLES
            ),
            key=str,
        )
    )
    if not candidates:
        _fail(
            "material_request_reservation_authorization_missing",
            "forbidden",
            "当前账号缺少可记录预约命令的授权坐标",
        )
    return candidates[0]


def _current_revision_id(db: Session, request_id: uuid.UUID, revision_no: int) -> uuid.UUID | None:
    from ..demand_models import MaterialRequestRevision

    return db.scalar(
        select(MaterialRequestRevision.id).where(
            MaterialRequestRevision.request_id == request_id,
            MaterialRequestRevision.revision_no == revision_no,
        )
    )


def _quantity_decimal(value: object) -> Decimal:
    try:
        quantity = Decimal(value)  # type: ignore[arg-type]
    except Exception:
        _fail("material_request_reservation_input_invalid", "invalid_request", "预约数量格式无效")
    if not quantity.is_finite() or quantity <= 0 or quantity >= _MAX_QUANTITY:
        _fail("material_request_reservation_input_invalid", "invalid_request", "预约数量格式无效")
    quantized = quantity.quantize(Decimal("0.001"))
    if quantized != quantity:
        _fail("material_request_reservation_input_invalid", "invalid_request", "预约数量最多三位小数")
    return quantized


def _quantity_text(value: Decimal) -> str:
    return f"{_quantity_decimal(value):.3f}"


def _uuid(value: object, name: str) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(f"material_request_reservation_{name}_invalid", "invalid_request", f"{name}无效")
    return value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestReservationError(code, category, message)


__all__ = [
    "MaterialRequestReservationError",
    "ReservationCommandResult",
    "ReservationCreateInput",
    "create_reservation",
    "reservation_command_status",
]
