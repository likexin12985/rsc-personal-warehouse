"""Pick one immutable reservation slice through the formal inventory ledger.

The caller owns the transaction. Picking moves reserved assets into the exact matching picking account.
It never records physical outbound, shipment or inbound.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
from typing import Mapping
from types import SimpleNamespace
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..demand_models import MaterialRequest, MaterialRequestCommand
from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..inventory_models import (
    InventoryMovement, InventoryMovementSerial, InventoryTransaction,
    StockBalance, StockReservation, StockReservationSerial,
    StockReservationPick, StockReservationPickSerial, StockAccount, OutboundOrder, OutboundLine,
    StockReservationRelease, StockReservationReleaseSerial,
)
from . import inventory_query, material_request_query, material_request_reservation as reserve
from . import material_request_reservation_release as release
from .audit_chain import AuditChainError, append_audit_event, verify_audit_event_in_stream, verify_audit_event_in_read_snapshot
from .inventory_posting import (
    InventoryMovementCommand, InventoryPostingCommand, InventoryPostingError,
    _authorize_account_ids, _lock_inventory_ledger_head_for_atomic_batch,
    _posting_request_hash, _storage_hash, _validate_posting_command,
    post_inventory_transaction,
)

ACTION = "material_request_reservation_picked"
SCHEMA = "rsc.material_request_reservation_pick_command.v1"
ZERO = Decimal("0.000")


class MaterialRequestReservationPickError(reserve.MaterialRequestReservationError):
    pass


@dataclass(frozen=True, slots=True)
class ReservationPickInput:
    reservation_id: uuid.UUID
    picked_qty: Decimal
    reason: str
    source_balance_version: int
    source_ledger_cursor: int
    serial_ids: tuple[uuid.UUID, ...] = ()


def _fail(code, category, message):
    raise MaterialRequestReservationPickError(f"material_request_reservation_pick_{code}", category, message)


def _payload(*, request_id, expected_version, value, actor):
    return {
        "request_id": str(request_id), "expected_request_version": expected_version,
        "reservation_id": str(value.reservation_id), "picked_qty": reserve._quantity_text(value.picked_qty),
        "reason": value.reason, "source_balance_version": value.source_balance_version,
        "source_ledger_cursor": value.source_ledger_cursor, "serial_ids": [str(s) for s in value.serial_ids],
        "actor_user_id": actor.user_id, "actor_person_id": str(actor.person_id),
        "authorization_version": actor.authorization_version,
    }


def _posting(fact, serial_ids):
    return InventoryPostingCommand(
        transaction_no=f"INV-PICK-{fact.id.hex[:16].upper()}", movement_type="pick",
        source_document_type="material_request_reservation_pick", source_document_id=str(fact.id),
        posting_key=f"material-request-reservation-pick:{fact.id}",
        effective_at=reserve._historical_utc(fact.created_at),
        movements=(InventoryMovementCommand(
            from_account_id=fact.source_stock_account_id, to_account_id=fact.target_stock_account_id,
            quantity=fact.picked_qty, serial_ids=tuple(serial_ids), external_boundary_code=None,
        ),),
    )


def picked_quantity(db: Session, reservation_id: uuid.UUID) -> Decimal:
    return Decimal(db.scalar(select(func.coalesce(func.sum(StockReservationPick.picked_qty), 0)).where(
        StockReservationPick.reservation_id == reservation_id,
    )) or ZERO)


def authorize_pick_accounts(db, actor, source_id, target_id):
    ids = tuple(sorted((source_id, target_id), key=str))
    _authorize_account_ids(db, actor, ids, action="read", resource="inventory", lock_rows=False)
    return _authorize_account_ids(db, actor, ids, action="post", lock_rows=False)


def picking_state(db: Session, request: MaterialRequest) -> str:
    from ..demand_models import MaterialRequestLine
    picked = db.scalar(select(func.coalesce(func.sum(StockReservationPick.picked_qty), 0)).where(
        StockReservationPick.request_id == request.id)) or ZERO
    if picked == ZERO:
        return "not_started"
    incomplete = db.scalar(select(MaterialRequestLine.id).where(
        MaterialRequestLine.request_id == request.id,
        MaterialRequestLine.revision_no == request.revision_no,
        MaterialRequestLine.final_approved_qty > MaterialRequestLine.cancelled_qty,
        (select(func.coalesce(func.sum(StockReservationPick.picked_qty), 0)).where(
            StockReservationPick.request_line_id == MaterialRequestLine.id).scalar_subquery())
        < MaterialRequestLine.final_approved_qty - MaterialRequestLine.cancelled_qty).limit(1))
    return "pending_pick" if incomplete is not None else "picked"


def _picking_target(db, source):
    rows = tuple(db.scalars(select(StockAccount).where(
        StockAccount.owner_org_id == source.owner_org_id,
        StockAccount.custodian_person_id == source.custodian_person_id,
        StockAccount.location_id == source.location_id,
        StockAccount.material_id == source.material_id,
        StockAccount.condition_code == source.condition_code,
        StockAccount.lot_id == source.lot_id,
        StockAccount.availability_bucket == "picking",
    ).limit(2).execution_options(populate_existing=True)).all())
    if source.availability_bucket != "reserved" or len(rows) != 1:
        _fail("target_missing", "precondition_failed", "原占用缺少唯一、维度完全一致的预置拣货账户")
    return rows[0]


def _event_identity(fact_id, request_id, operation, before, after):
    if before == after:
        return dict(aggregate_type="stock_reservation_pick", aggregate_id=str(fact_id), from_status=None, to_status="picked")
    return dict(aggregate_type="material_request", aggregate_id=str(request_id), from_status=before, to_status=after)


def _result(fact, request, axes, serial_ids):
    return {
        "schema_version": "1.0", "kind": "reservation_pick",
        "outbound_line_id": str(fact.outbound_line_id), "outbound_id": str(fact.id),
        "outbound_no": f"OB-{reserve._historical_utc(fact.created_at):%Y%m%d}-{fact.id.hex[:12].upper()}",
        "pick_id": str(fact.id), "pick_no": fact.pick_no,
        "reservation_id": str(fact.reservation_id), "allocation_id": str(fact.allocation_id),
        "request_id": str(fact.request_id), "request_no": request.request_no,
        "request_line_id": str(fact.request_line_id), "revision_id": str(fact.revision_id),
        "revision_no": fact.revision_no, "request_version": fact.request_version,
        "picked_qty": reserve._quantity_text(fact.picked_qty), "reason": fact.reason,
        "source_stock_account_id": str(fact.source_stock_account_id),
        "target_stock_account_id": str(fact.target_stock_account_id),
        "source_balance_version": fact.source_balance_version, "source_ledger_cursor": fact.source_ledger_cursor,
        "pick_transaction_id": str(fact.pick_transaction_id),
        "pick_transaction_no": _posting(fact, serial_ids).transaction_no,
        "serial_ids": [str(s) for s in serial_ids], "state_axes": dict(axes),
    }


def _document(fact, serial_ids):
    return {
        "schema": SCHEMA, "operation": "pick", "request_id": str(fact.request_id),
        "target_version": fact.request_version, "pick_id": str(fact.id),
        "reservation_id": str(fact.reservation_id), "payload_sha256": fact.request_hash,
        "pick_transaction_id": str(fact.pick_transaction_id),
        "source_balance_version": fact.source_balance_version, "source_ledger_cursor": fact.source_ledger_cursor,
        "serial_ids": [str(s) for s in serial_ids], "comment_sha256": hashlib.sha256(fact.reason.encode()).hexdigest(),
        "sensitive_fields": "excluded",
    }


def _audit_after(fact, command):
    return {
        "pick_id": str(fact.id), "reservation_id": str(fact.reservation_id),
        "request_id": str(fact.request_id), "request_version": fact.request_version,
        "command_id": str(command.id), "request_hash": command.request_hash, "result_hash": command.result_hash,
        "picked_qty": reserve._quantity_text(fact.picked_qty),
        "pick_transaction_id": str(fact.pick_transaction_id),
    }


def _event_metadata(fact, command):
    return {"pick_id": str(fact.id), "command_id": str(command.id), "request_version": fact.request_version}


def _public_result(result, request, *, replayed):
    return {**result, "current_request_version": request.version, "idempotency_replayed": replayed}


def create_pick(db: Session, *, actor: FormalPrincipal, material_request_id: uuid.UUID,
                   expected_request_version: int, pick: ReservationPickInput,
                   idempotency_key: str, idempotency_hmac_secret: bytes | str,
                   trace_request_id: str) -> dict:
    try:
        return _create(db, actor=actor, request_id=material_request_id, expected_version=expected_request_version,
                       value=pick, key=idempotency_key, secret=idempotency_hmac_secret, trace=trace_request_id)
    except MaterialRequestReservationPickError:
        raise
    except (reserve.MaterialRequestReservationError, InventoryPostingError) as exc:
        raise MaterialRequestReservationPickError(exc.code, exc.category, exc.message) from exc
    except IntegrityError as exc:
        raise MaterialRequestReservationPickError("material_request_reservation_pick_conflict", "conflict", "拣货发生并发冲突，请重新读取") from exc
    except (DBAPIError, AuditChainError) as exc:
        raise MaterialRequestReservationPickError("material_request_reservation_pick_unavailable", "service_unavailable", "拣货未完成，请通过原请求核验结果") from exc


def _create(db, *, actor, request_id, expected_version, value, key, secret, trace):
    reserve._require_actor(actor)
    reserve._uuid(request_id, "request_id")
    reserve._uuid(value.reservation_id, "reservation_id")
    if type(expected_version) is not int or expected_version < 0:
        _fail("version_invalid", "invalid_request", "需求版本无效")
    if (type(value.source_balance_version) is not int or value.source_balance_version < 0
        or type(value.source_ledger_cursor) is not int or value.source_ledger_cursor < 0):
        _fail("coordinate_invalid", "invalid_request", "占用账户版本无效")
    quantity = reserve._quantity_decimal(value.picked_qty)
    if quantity <= ZERO or quantity != value.picked_qty:
        _fail("quantity_invalid", "invalid_request", "拣货数量必须为最多三位小数的正数")
    if (type(value.reason) is not str or not 1 <= len(value.reason) <= 500
        or value.reason != value.reason.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value.reason)):
        _fail("reason_invalid", "invalid_request", "请填写完整拣货原因（最多 500 字）")
    if not isinstance(value.serial_ids, tuple) or len(value.serial_ids) > 1000 or len(set(value.serial_ids)) != len(value.serial_ids):
        _fail("serial_invalid", "invalid_request", "拣货 SN 无效或重复")
    for serial_id in value.serial_ids:
        reserve._uuid(serial_id, "serial_id")
    if not isinstance(key, str) or not 16 <= len(key) <= 200 or not reserve._PRINTABLE.fullmatch(key):
        _fail("key_invalid", "invalid_request", "拣货幂等键无效")
    if not isinstance(trace, str) or not 8 <= len(trace) <= 160 or not reserve._PRINTABLE.fullmatch(trace):
        _fail("trace_invalid", "invalid_request", "请求追踪坐标无效")
    secret = secret.encode() if isinstance(secret, str) else secret
    if not isinstance(secret, bytes) or len(secret) < 32:
        _fail("secret_invalid", "service_unavailable", "拣货幂等配置不可用")
    path = f"/api/v1/material-requests/{request_id}/reservation-picks"
    key_hash = hmac.new(secret, f"{actor.user_id}:POST:{path}:{key}".encode(), hashlib.sha256).hexdigest()
    request_hash = reserve._canonical_hash(_payload(request_id=request_id, expected_version=expected_version, value=value, actor=actor))
    # The existing stocktake and posting paths take the ledger head first. The
    # reservation creation path uses this same ordering before principal/request.
    _lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    context = reserve._request_read_context(db, actor)
    request = db.scalar(select(MaterialRequest).where(
        MaterialRequest.id == request_id, material_request_query._visible_request_predicate(context),
    ).with_for_update().execution_options(populate_existing=True))
    if request is None:
        _fail("not_found", "not_found", "需求单不存在")
    existing = db.scalar(select(StockReservationPick).where(StockReservationPick.idempotency_key_hash == key_hash))
    if existing is not None:
        if existing.request_hash != request_hash or existing.request_id != request_id:
            _fail("key_reused", "conflict", "幂等键已绑定其他拣货内容")
        return _recover(db, actor=actor, fact=existing, request=request)
    if request.version != expected_version:
        _fail("version_conflict", "conflict", "需求版本已变化，请重新读取")
    if request.status not in {"approved", "partially_approved"} or request.outbound_status not in {"not_started", "pending_pick"}:
        _fail("state_invalid", "precondition_failed", "当前需求状态不允许直接拣货占用")
    original = db.scalar(select(StockReservation).where(
        StockReservation.id == value.reservation_id, StockReservation.request_id == request_id,
        StockReservation.revision_no == request.revision_no,
    ).execution_options(populate_existing=True))
    if original is None:
        _fail("reservation_not_found", "not_found", "原占用记录不存在")
    reserve._verified_history(db, fact=original, request=request)
    source = db.get(StockAccount, original.stock_account_id, populate_existing=True)
    target = _picking_target(db, source)
    authorize_pick_accounts(db, actor, original.stock_account_id, target.id)
    remaining = original.reserved_qty - picked_quantity(db, original.id) - release.released_quantity(db, original.id)
    if quantity > remaining:
        _fail("quantity_exceeded", "precondition_failed", "拣货数量超过本笔占用余量")
    bound = set(db.scalars(select(StockReservationSerial.serial_id).where(StockReservationSerial.reservation_id == original.id)).all())
    used = set(db.scalars(select(StockReservationPickSerial.serial_id).where(StockReservationPickSerial.reservation_id == original.id)).all())
    used.update(db.scalars(select(StockReservationReleaseSerial.serial_id).where(
        StockReservationReleaseSerial.reservation_id == original.id)).all())
    if ((bound and (quantity != len(value.serial_ids) or not set(value.serial_ids) <= bound - used))
        or (not bound and value.serial_ids)):
        _fail("serial_mismatch", "precondition_failed", "拣货 SN 必须属于本笔占用且尚未拣货")
    balance = db.scalar(select(StockBalance).where(StockBalance.stock_account_id == original.stock_account_id).execution_options(populate_existing=True))
    if balance is None or (balance.version, balance.ledger_cursor) != (value.source_balance_version, value.source_ledger_cursor):
        _fail("source_stale", "conflict", "占用账户余额版本已变化，请重新读取")
    claimed = (db.scalar(select(func.coalesce(func.sum(StockReservation.reserved_qty), 0)).where(
        StockReservation.stock_account_id == original.stock_account_id)) or ZERO)
    claimed -= (db.scalar(select(func.coalesce(func.sum(StockReservationRelease.released_qty), 0)).where(
        StockReservationRelease.source_stock_account_id == original.stock_account_id)) or ZERO)
    claimed -= (db.scalar(select(func.coalesce(func.sum(StockReservationPick.picked_qty), 0)).where(
        StockReservationPick.source_stock_account_id == original.stock_account_id)) or ZERO)
    if claimed < remaining or balance.quantity < claimed:
        _fail("pool_shortfall", "precondition_failed", "占用账户余额不足以覆盖全部原占用，已停止拣货")
    before_quantity = balance.quantity
    now = datetime.now(timezone.utc)
    fact_id = uuid.uuid4()
    order = OutboundOrder(id=fact_id, outbound_no=f"OB-{now:%Y%m%d}-{fact_id.hex[:12].upper()}",
        source_location_id=source.location_id, status="picked", picked_at=now, outbound_at=None, created_at=now)
    db.add(order)
    db.flush()
    line = OutboundLine(id=uuid.uuid4(), outbound_id=order.id, allocation_id=original.allocation_id,
        planned_qty=quantity, picked_qty=quantity, outbound_qty=ZERO, created_at=now)
    db.add(line)
    db.flush()
    fact = StockReservationPick(outbound_line_id=line.id,
        id=fact_id, pick_no=f"PK-{now:%Y%m%d}-{fact_id.hex[:12].upper()}",
        reservation_id=original.id, allocation_id=original.allocation_id, request_id=request_id,
        request_line_id=original.request_line_id, revision_id=original.revision_id, revision_no=original.revision_no,
        request_version=request.version + 1, picked_qty=quantity, reason=value.reason,
        source_stock_account_id=original.stock_account_id, target_stock_account_id=target.id,
        source_balance_version=value.source_balance_version, source_ledger_cursor=value.source_ledger_cursor,
        idempotency_key_hash=key_hash, request_hash=request_hash, actor_user_id=actor.user_id,
        actor_person_id=actor.person_id, authorization_version=actor.authorization_version, created_at=now,
    )
    posted = post_inventory_transaction(db, actor=actor, command=_posting(fact, value.serial_ids),
        idempotency_key=f"mr-reservation-pick-{key_hash}", request_id=trace)
    fact.pick_transaction_id = posted.transaction_id
    db.expire(balance)
    if (balance.version != value.source_balance_version + 1 or balance.ledger_cursor != posted.ledger_cursor
        or balance.quantity != before_quantity - quantity):
        _fail("projection_invalid", "service_unavailable", "拣货后的余额回读不一致，本次操作未完成")
    db.add(fact)
    db.flush()
    db.add_all([StockReservationPickSerial(pick_id=fact.id, reservation_id=original.id,
        allocation_id=original.allocation_id, serial_id=s, created_at=now) for s in value.serial_ids])
    db.flush()
    before_status = request.outbound_status
    axes = reserve._state_axes(request)
    axes["outbound_status"] = picking_state(db, request)
    result = _result(fact, request, axes, value.serial_ids)
    command = MaterialRequestCommand(
        id=uuid.uuid4(), operation="pick", request_id=request_id, target_version=fact.request_version,
        idempotency_key_hash=key_hash, request_reference=path, request_hash=request_hash,
        result_hash=reserve._canonical_hash(result), request_jsonb=_document(fact, value.serial_ids), result_jsonb=result,
        actor_user_id=actor.user_id, actor_person_id=actor.person_id, actor_role_assignment_id=reserve._actor_assignment_id(actor),
        authorization_version=actor.authorization_version, occurred_at=now, created_at=now,
    )
    db.add(command)
    db.flush()
    request.version = fact.request_version
    request.outbound_status = axes["outbound_status"]
    request.updated_at = now
    db.flush()
    db.add(StateTransitionEvent(
        **_event_identity(fact.id, request_id, "pick", before_status, axes["outbound_status"]),
        reason=ACTION, actor_id=actor.user_id,
        idempotency_key=f"reservation-pick-state-{key_hash}", occurred_at=now,
        metadata_jsonb=_event_metadata(fact, command), created_at=now,
    ))
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action=ACTION,
        aggregate_type="stock_reservation_pick", aggregate_id=str(fact.id),
        before_jsonb={"request_version": fact.request_version - 1, "outbound_status": before_status},
        after_jsonb=_audit_after(fact, command), request_id=trace, occurred_at=now, created_at=now)
    db.flush()
    return _public_result(result, request, replayed=False)


def pick_command_status(db: Session, *, actor: FormalPrincipal, trace_request_id: str):
    try:
        return _pick_command_status(db, actor=actor, trace_request_id=trace_request_id)
    except (reserve.MaterialRequestReservationError, InventoryPostingError) as exc:
        raise MaterialRequestReservationPickError(exc.code, exc.category, exc.message) from exc


def _pick_command_status(db: Session, *, actor: FormalPrincipal, trace_request_id: str):
    reserve._require_actor(actor)
    if not isinstance(trace_request_id, str) or not 8 <= len(trace_request_id) <= 160 or not reserve._PRINTABLE.fullmatch(trace_request_id):
        _fail("trace_invalid", "invalid_request", "请求追踪坐标无效")
    context = reserve._request_read_context(db, actor)
    audits = tuple(db.scalars(select(AuditEvent).where(
        AuditEvent.stream_key == "material_request", AuditEvent.action == ACTION,
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.request_id == trace_request_id,
    ).limit(2).execution_options(populate_existing=True)).all())
    if not audits:
        return None
    if len(audits) != 1 or audits[0].aggregate_type != "stock_reservation_pick":
        _history_invalid()
    try:
        fact_id = uuid.UUID(audits[0].aggregate_id)
    except (ValueError, TypeError):
        _history_invalid()
    fact = db.get(StockReservationPick, fact_id, populate_existing=True)
    if fact is None:
        _history_invalid()
    request = db.scalar(select(MaterialRequest).where(MaterialRequest.id == fact.request_id,
        material_request_query._visible_request_predicate(context)).execution_options(populate_existing=True))
    if request is None:
        _fail("not_found", "not_found", "需求单不存在")
    return _recover(db, actor=actor, fact=fact, request=request)


def _history_invalid():
    _fail("history_invalid", "service_unavailable", "拣货历史证据不完整，保留原请求继续核验")


def _recover(db, *, actor, fact, request):
    if (fact.actor_user_id, fact.actor_person_id, fact.authorization_version) != (actor.user_id, actor.person_id, actor.authorization_version):
        _fail("authorization_changed", "precondition_failed", "原拣货授权已变化")
    authorize_pick_accounts(db, actor, fact.source_stock_account_id, fact.target_stock_account_id)
    return verified_pick_history(db, fact=fact, request=request)


def verified_pick_history(db, *, fact, request, lock_audit=True):
    """Prove immutable pick evidence after the caller authorizes its read.

    Hashes bind the historical actor. Current readers need not be that actor;
    command recovery still requires the original identity and live grants.
    """
    actor = SimpleNamespace(user_id=fact.actor_user_id, person_id=fact.actor_person_id,
        authorization_version=fact.authorization_version)
    def rows(model, *criteria, limit=2):
        return tuple(db.scalars(select(model).where(*criteria).limit(limit).execution_options(populate_existing=True)).all())
    commands = rows(MaterialRequestCommand, MaterialRequestCommand.request_id == fact.request_id,
        MaterialRequestCommand.target_version == fact.request_version, MaterialRequestCommand.operation == "pick")
    audits = rows(AuditEvent, AuditEvent.stream_key == "material_request", AuditEvent.action == ACTION,
        AuditEvent.aggregate_type == "stock_reservation_pick", AuditEvent.aggregate_id == str(fact.id))
    events = rows(StateTransitionEvent, StateTransitionEvent.idempotency_key == f"reservation-pick-state-{fact.idempotency_key_hash}")
    movements = rows(InventoryMovement, InventoryMovement.transaction_id == fact.pick_transaction_id)
    serials = rows(StockReservationPickSerial, StockReservationPickSerial.pick_id == fact.id, limit=1001)
    movement_serials = rows(InventoryMovementSerial, InventoryMovementSerial.transaction_id == fact.pick_transaction_id, limit=1001)
    tx = db.get(InventoryTransaction, fact.pick_transaction_id, populate_existing=True)
    original = db.get(StockReservation, fact.reservation_id, populate_existing=True)
    if any(len(values) != 1 for values in (commands, audits, events, movements)) or tx is None or original is None:
        _history_invalid()
    command, audit, event, movement = commands[0], audits[0], events[0], movements[0]
    result = command.result_jsonb
    if not isinstance(result, dict) or not isinstance(result.get("state_axes"), dict) or not isinstance(result.get("serial_ids"), list):
        _history_invalid()
    try:
        serial_ids = tuple(uuid.UUID(s) for s in result["serial_ids"])
    except (ValueError, TypeError, AttributeError):
        _history_invalid()
    axes = result["state_axes"]
    if (len(serial_ids) > 1000 or len(set(serial_ids)) != len(serial_ids)
        or [str(s) for s in serial_ids] != result["serial_ids"]
        or set(axes) != set(reserve._state_axes(request)) or any(type(s) is not str for s in axes.values())
        or axes.get("outbound_status") not in {"pending_pick", "picked"}):
        _history_invalid()
    value = ReservationPickInput(fact.reservation_id, fact.picked_qty, fact.reason,
        fact.source_balance_version, fact.source_ledger_cursor, serial_ids)
    payload = _payload(request_id=fact.request_id, expected_version=fact.request_version - 1, value=value, actor=actor)
    expected_result = _result(fact, request, axes, serial_ids)
    posting = _validate_posting_command(_posting(fact, serial_ids))
    if (
        request.version < fact.request_version or (request.version == fact.request_version and reserve._state_axes(request) != axes)
        or fact.request_id != original.request_id or fact.request_line_id != original.request_line_id
        or fact.allocation_id != original.allocation_id or fact.revision_id != original.revision_id
        or fact.revision_no != original.revision_no or fact.request_version <= original.request_version
        or fact.source_stock_account_id != original.stock_account_id
        or command.idempotency_key_hash != fact.idempotency_key_hash or command.request_hash != fact.request_hash
        or fact.request_hash != reserve._canonical_hash(payload) or command.result_hash != reserve._canonical_hash(expected_result)
        or command.request_reference != f"/api/v1/material-requests/{request.id}/reservation-picks"
        or command.actor_user_id != fact.actor_user_id or command.actor_person_id != fact.actor_person_id
        or command.authorization_version != fact.authorization_version
        or command.request_jsonb != _document(fact, serial_ids) or result != expected_result
        or audit.actor_user_id != fact.actor_user_id or audit.after_jsonb != _audit_after(fact, command)
        or not isinstance(audit.before_jsonb, dict)
        or audit.before_jsonb.get("outbound_status") not in {"not_started", "pending_pick"}
        or audit.before_jsonb != {"request_version": fact.request_version - 1, "outbound_status": audit.before_jsonb.get("outbound_status")}
        or any(getattr(event, name) != value for name, value in _event_identity(
            fact.id, fact.request_id, "pick", audit.before_jsonb.get("outbound_status"), axes["outbound_status"]).items())
        or event.reason != ACTION or event.actor_id != fact.actor_user_id
        or event.metadata_jsonb != _event_metadata(fact, command)
        or any(reserve._historical_utc(row.occurred_at) != reserve._historical_utc(fact.created_at) for row in (command, audit, event))
        or tx.status != "posted" or tx.movement_type != "pick" or tx.posted_at is None
        or tx.source_document_type != posting.source_document_type or tx.source_document_id != str(fact.id)
        or tx.transaction_no != posting.transaction_no or tx.posting_key != posting.posting_key
        or tx.actor_user_id != fact.actor_user_id or tx.reversed_transaction_id is not None
        or tx.request_hash != _posting_request_hash(actor, posting)
        or tx.idempotency_key_hash != _storage_hash(f"mr-reservation-pick-{fact.idempotency_key_hash}")
        or reserve._historical_utc(tx.effective_at) != posting.effective_at
        or tx.ledger_cursor is None or tx.ledger_cursor <= fact.source_ledger_cursor
        or movement.from_account_id != fact.source_stock_account_id or movement.to_account_id != fact.target_stock_account_id
        or movement.line_no != 1 or movement.quantity != fact.picked_qty or movement.external_boundary_code is not None
        or len(serials) != len(serial_ids) or len(movement_serials) != len(serial_ids)
        or {s.serial_id for s in serials} != set(serial_ids) or {s.serial_id for s in movement_serials} != set(serial_ids)
        or any(s.reservation_id != original.id or s.allocation_id != original.allocation_id for s in serials)
        or any(s.movement_id != movement.id for s in movement_serials)
        or (serial_ids and fact.picked_qty != len(serial_ids))
    ):
        _history_invalid()
    line = db.get(OutboundLine, fact.outbound_line_id, populate_existing=True)
    order = db.get(OutboundOrder, line.outbound_id, populate_existing=True) if line else None
    source = db.get(StockAccount, fact.source_stock_account_id, populate_existing=True)
    target = db.get(StockAccount, fact.target_stock_account_id, populate_existing=True)
    if (line is None or order is None or source is None or target is None
        or line.allocation_id != fact.allocation_id or line.planned_qty != fact.picked_qty
        or line.picked_qty != fact.picked_qty or line.outbound_qty != ZERO
        or order.id != fact.id or order.outbound_no != expected_result["outbound_no"]
        or order.source_location_id != source.location_id or order.status != "picked"
        or order.outbound_at is not None or reserve._historical_utc(order.picked_at) != reserve._historical_utc(fact.created_at)
        or source.availability_bucket != "reserved" or target.availability_bucket != "picking"
        or any(getattr(source, name) != getattr(target, name) for name in (
            "owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id"))):
        _history_invalid()
    reserve._verified_history(db, fact=original, request=request, lock_audit=lock_audit)
    try:
        verifier = verify_audit_event_in_stream if lock_audit else verify_audit_event_in_read_snapshot
        verifier(db, stream_key="material_request", event_id=audit.id)
    except AuditChainError:
        _history_invalid()
    return _public_result(result, request, replayed=True)
