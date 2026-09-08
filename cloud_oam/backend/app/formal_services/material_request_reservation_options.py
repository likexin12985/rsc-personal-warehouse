"""Bounded, read-only candidates for the allocation-to-reservation boundary.

No allocation, inventory movement, account, audit or notification is generated.
The later reservation command must still revalidate every returned coordinate.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import uuid

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from ..inventory_models import (
    InventorySerial,
    SerialCurrentPosition,
    StockAccount,
    StockAllocation,
    StockAllocationSerial,
    StockReservation,
    StockReservationSerial,
)
from ..material_request_reservation_option_schemas import (
    MaterialRequestReservationOptionOut,
    MaterialRequestReservationOptionPageOut,
    MaterialRequestReservationSerialOptionOut,
)
from . import inventory_query, material_request_query
from .material_request_allocation_options import (
    MaterialRequestAllocationOptionError,
    _decimal,
    _fixed_quantity,
)


MAX_ALLOCATIONS = 100
MAX_RESERVATIONS = 1000
MAX_SERIALS = 1000
_ZERO = Decimal("0")


class MaterialRequestReservationOptionError(MaterialRequestAllocationOptionError):
    """Stable public error with no database or authorization internals."""


def list_reservation_options(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    request_line_id: uuid.UUID,
) -> MaterialRequestReservationOptionPageOut:
    for value in (material_request_id, request_line_id):
        if not isinstance(value, uuid.UUID) or value.int == 0:
            _fail("material_request_reservation_options_id_invalid", "invalid_request", "需求或明细标识无效")
    try:
        with db.no_autoflush:
            actor = _current_actor(db, actor)
            if not set(actor.role_codes).intersection({"admin", "provincial_manager"}):
                _fail("material_request_reservation_options_forbidden", "forbidden", "当前账号没有库存预留候选权限")
            # Both readers enforce live formal grants, including explicit deny.
            view = material_request_query.material_request_detail(db, actor=actor, request_id=material_request_id)
            line = next((row for row in view.lines if row.request_line_id == request_line_id), None)
            if line is None:
                _fail("material_request_line_not_found", "not_found", "需求单明细不存在")
            if line.revision_id != view.current_revision_id or line.revision_no != view.current_revision_no:
                _fail("material_request_line_revision_stale", "conflict", "需求明细版本已变化，请重新读取")
            if view.states.request_status not in {"approved", "partially_approved"} or line.status not in {"approved", "partially_approved"}:
                _fail("material_request_not_approved", "precondition_failed", "需求单及明细尚未完成最终审批")
            if view.states.allocation_status not in {"partially_allocated", "allocated"}:
                _fail("material_request_allocation_required", "precondition_failed", "需求明细尚未完成分配")
            inventory_query._require_inventory_read(db, actor)
            snapshot = inventory_query._projection_snapshot(db)
            allocations = _bounded_scalars(
                db,
                select(StockAllocation).where(
                    StockAllocation.request_id == material_request_id,
                    StockAllocation.request_line_id == request_line_id,
                ).order_by(StockAllocation.id),
                MAX_ALLOCATIONS,
            )
            if not allocations:
                _fail("material_request_allocation_required", "precondition_failed", "需求明细没有可预留的分配事实")
            for fact in allocations:
                if (fact.revision_id != line.revision_id or fact.revision_no != line.revision_no
                    or fact.status != "allocated" or not 0 <= fact.request_version <= view.request_version
                    or fact.source_stock_account_id is None or _decimal(fact.allocated_qty) <= 0):
                    _invalid_fact()
            if sum((_decimal(fact.allocated_qty) for fact in allocations), _ZERO) > line.final_approved_qty - line.cancelled_qty:
                _invalid_fact()

            source_ids = {fact.source_stock_account_id for fact in allocations}
            # At most 100 exact accounts; do not scan a nationwide stock directory.
            values = db.execute(
                inventory_query._account_row_statement(db, actor=actor)
                .where(StockAccount.id.in_(source_ids))
                .order_by(StockAccount.id).limit(MAX_ALLOCATIONS + 1)
                .execution_options(populate_existing=True)
            ).all() if source_ids else []
            rows = {}
            for value in values:
                row = inventory_query._account_row(value)
                if inventory_query._account_allowed(db, actor, row):
                    rows[row.account.id] = row
            # Do not reveal even the allocation identity for an invisible source.
            visible_allocations = [fact for fact in allocations if fact.source_stock_account_id in rows]
            if not rows:
                _fail("material_request_reservation_source_forbidden", "forbidden", "分配来源不在当前库存授权范围内")
            inventory_query._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(rows))
            evidence = inventory_query._validated_opening_evidence(
                db, actor=actor, snapshot=snapshot,
                required_pairs={(row.account.owner_org_id, row.account.location_id) for row in rows.values()},
                discover_authorized_zero_scopes=False,
            )
            if not evidence.complete:
                _fail("inventory_opening_not_established", "precondition_failed", "库存期初建账尚未完成")

            reserved = _reservation_totals(db, visible_allocations)
            policy = inventory_query._effective_policy(db, line.material_id)
            for row in rows.values():
                _validate_source_dimensions(row, material_id=line.material_id, tracking_mode=policy.tracking_mode)
            serials = _serial_options(db, visible_allocations, rows, policy.tracking_mode, reserved)
            candidates = []
            for allocation in visible_allocations:
                row = rows[allocation.source_stock_account_id]
                account = row.account
                if row.material.status != "active" or row.location.status != "active":
                    continue
                if row.balance is None:
                    _fail("material_request_reservation_source_missing_balance", "precondition_failed", "分配来源缺少余额投影")
                inventory_query._ensure_balance_at_snapshot(row.balance, snapshot)
                quantity = _decimal(row.balance.quantity)
                allocated = _decimal(allocation.allocated_qty)
                used = reserved.get(allocation.id, _ZERO)
                remaining = allocated - used
                if remaining < 0 or quantity < 0:
                    _invalid_fact()
                capacity = min(remaining, quantity)
                options = serials.get(allocation.id, ())
                if policy.tracking_mode in {"serial", "lot_and_serial"}:
                    capacity = min(capacity, Decimal(len(options)))
                if capacity <= 0:
                    continue
                _require_reserved_target(db, account)
                source_output = inventory_query._account_output(db, row, snapshot=snapshot, opening_established=True).model_dump()
                source_output.pop("quantity_status")
                source_output.update(
                    quantity=_fixed_quantity(quantity, policy.quantity_scale),
                    quantity_scale=policy.quantity_scale,
                    allocation_id=allocation.id,
                    allocation_no=allocation.allocation_no,
                    allocated_qty=_fixed_quantity(allocated, policy.quantity_scale),
                    reserved_qty=_fixed_quantity(used, policy.quantity_scale),
                    remaining_qty=_fixed_quantity(remaining, policy.quantity_scale),
                    reservable_qty=_fixed_quantity(capacity, policy.quantity_scale),
                    serial_options=options,
                )
                candidates.append(MaterialRequestReservationOptionOut(**source_output))

            reread = material_request_query.material_request_detail(db, actor=actor, request_id=material_request_id)
            if (reread.request_version != view.request_version
                or reread.current_revision_id != view.current_revision_id
                or reread.current_revision_no != view.current_revision_no
                or reread.states != view.states
                or reread.lines != view.lines):
                _fail("material_request_revision_stale", "conflict", "需求单版本已变化，请重新读取")
            inventory_query._ensure_projection_snapshot_current(db, snapshot)
            if _current_actor(db, actor) != actor:
                _fail("material_request_reservation_options_authorization_changed", "forbidden", "读取期间授权范围已变化，请重新读取")
            return MaterialRequestReservationOptionPageOut(
                request_id=material_request_id, request_line_id=request_line_id,
                request_version=view.request_version, current_revision_id=view.current_revision_id,
                current_revision_no=view.current_revision_no, material_id=line.material_id,
                projection_status="ready", opening_balance_status="established",
                projected_at=snapshot.projected_at, ledger_cursor=snapshot.ledger_cursor,
                items=tuple(candidates),
            )
    except MaterialRequestReservationOptionError:
        raise
    except material_request_query.MaterialRequestReadError as exc:
        _fail(exc.code, exc.category, exc.message)
    except inventory_query.InventoryReadError as exc:
        categories = {403: "forbidden", 412: "precondition_failed", 409: "conflict"}
        _fail(exc.code, categories.get(exc.status_code, "service_unavailable"), exc.public_message)
    except DBAPIError:
        _fail("material_request_reservation_options_database_unavailable", "service_unavailable", "库存预留候选暂时不可用")
    except (TypeError, ValueError, InvalidOperation):
        _fail("material_request_reservation_options_projection_invalid", "service_unavailable", "库存预留候选投影无效")
    raise AssertionError("unreachable reservation option boundary")


def _current_actor(db: Session, actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("material_request_reservation_options_forbidden", "forbidden", "预留候选要求正式权限主体")
    try:
        current = load_formal_principal(db, actor.user_id)
    except FormalAccessError:
        _fail("material_request_reservation_options_forbidden", "forbidden", "当前预留查询身份不可用")
    if current.person_id != actor.person_id or current.authorization_version != actor.authorization_version:
        _fail("material_request_reservation_options_authorization_changed", "forbidden", "身份或授权版本已变化，请重新读取")
    return current


def _bounded_scalars(db: Session, statement, limit: int):
    rows = tuple(db.scalars(statement.limit(limit + 1).execution_options(populate_existing=True)).all())
    if len(rows) > limit:
        _fail("material_request_reservation_options_limit_exceeded", "service_unavailable", "预留候选超过安全读取上限，已停止返回不完整结果")
    return rows


def _validate_source_dimensions(row, *, material_id: uuid.UUID, tracking_mode: str) -> None:
    account, lot = row.account, row.lot
    if (account.availability_bucket != "available" or account.material_id != material_id
        or row.material.id != material_id):
        _fail("material_request_reservation_source_invalid", "conflict", "分配来源账户与需求不一致")
    # Use the current policy returned by inventory_query and the exact lot
    # joined by its account reader. These are the posting service's same
    # none/serial versus lot/lot_and_serial account-dimension rules.
    if tracking_mode in {"none", "serial"}:
        valid = account.lot_id is None and lot is None
    else:
        valid = (
            tracking_mode in {"lot", "lot_and_serial"}
            and account.lot_id is not None and lot is not None
            and lot.id == account.lot_id and lot.material_id == material_id
            and isinstance(lot.lot_no, str) and 0 < len(lot.lot_no) <= 160
            and lot.lot_no.strip() == lot.lot_no
            and not any(ord(character) < 32 or ord(character) == 127 for character in lot.lot_no)
        )
    if not valid:
        _fail("material_request_reservation_source_lot_invalid", "conflict", "分配来源批次与当前物料追踪策略不一致")


def _reservation_totals(db: Session, allocations) -> dict[uuid.UUID, Decimal]:
    by_id = {fact.id: fact for fact in allocations}
    if not by_id:
        return {}
    facts = _bounded_scalars(db, select(StockReservation).where(StockReservation.allocation_id.in_(by_id)), MAX_RESERVATIONS)
    totals = {}
    for fact in facts:
        allocation = by_id[fact.allocation_id]
        if (fact.request_id != allocation.request_id or fact.request_line_id != allocation.request_line_id
            or fact.revision_id != allocation.revision_id or fact.revision_no != allocation.revision_no
            or fact.source_stock_account_id != allocation.source_stock_account_id
            or fact.status not in {"reserved", "released", "fulfilled"} or _decimal(fact.reserved_qty) <= 0):
            _invalid_fact()
        # Release and fulfilment consume this allocation's capacity as separate
        # facts; neither is permission to create the same reservation again.
        totals[fact.allocation_id] = totals.get(fact.allocation_id, _ZERO) + _decimal(fact.reserved_qty)
    return totals


def _serial_options(db: Session, allocations, rows, tracking_mode: str, reserved):
    by_id = {fact.id: fact for fact in allocations}
    if not by_id:
        return {}
    bindings = _bounded_scalars(db, select(StockAllocationSerial).where(StockAllocationSerial.allocation_id.in_(by_id)).order_by(StockAllocationSerial.allocation_id, StockAllocationSerial.serial_id), MAX_SERIALS)
    if tracking_mode not in {"serial", "lot_and_serial"}:
        if bindings:
            _fail("material_request_reservation_allocation_serial_invalid", "conflict", "分配 SN 与当前物料策略不一致")
        return {}
    prior = _bounded_scalars(db, select(StockReservationSerial).where(StockReservationSerial.allocation_id.in_(by_id)), MAX_SERIALS)
    used = {(fact.allocation_id, fact.serial_id) for fact in prior}
    bound = {(fact.allocation_id, fact.serial_id) for fact in bindings}
    if len(used) != len(prior) or used - bound:
        _invalid_fact()
    for allocation_id, allocation in by_id.items():
        if (sum(key[0] == allocation_id for key in bound) != _decimal(allocation.allocated_qty)
            or sum(key[0] == allocation_id for key in used) != reserved.get(allocation_id, _ZERO)):
            _invalid_fact()
    serial_ids = {binding.serial_id for binding in bindings}
    serial_rows = db.execute(
        select(InventorySerial, SerialCurrentPosition.stock_account_id)
        .outerjoin(SerialCurrentPosition, SerialCurrentPosition.serial_id == InventorySerial.id)
        .where(InventorySerial.id.in_(serial_ids)).order_by(InventorySerial.id)
        .limit(MAX_SERIALS + 1).execution_options(populate_existing=True)
    ).all() if serial_ids else []
    current = {serial.id: (serial, account_id) for serial, account_id in serial_rows}
    result = {}
    for binding in bindings:
        if (binding.allocation_id, binding.serial_id) in used:
            continue
        allocation = by_id[binding.allocation_id]
        source = rows[allocation.source_stock_account_id].account
        serial, account_id = current.get(binding.serial_id, (None, None))
        if (serial is None or serial.lifecycle_status != "active" or account_id != source.id
            or serial.material_id != source.material_id or serial.lot_id != source.lot_id):
            continue
        result.setdefault(allocation.id, []).append(MaterialRequestReservationSerialOptionOut(
            serial_id=serial.id, serial_no=serial.serial_no, qr_code=serial.qr_code, lot_id=serial.lot_id,
        ))
    return {key: tuple(values) for key, values in result.items()}


def _require_reserved_target(db: Session, source: StockAccount) -> None:
    targets = tuple(db.scalars(select(StockAccount.id).where(
        StockAccount.owner_org_id == source.owner_org_id,
        StockAccount.custodian_person_id == source.custodian_person_id,
        StockAccount.location_id == source.location_id,
        StockAccount.material_id == source.material_id,
        StockAccount.condition_code == source.condition_code,
        StockAccount.availability_bucket == "reserved",
        StockAccount.lot_id == source.lot_id,
    ).limit(2)).all())
    if len(targets) != 1:
        _fail("material_request_reservation_target_missing", "precondition_failed", "当前货源没有唯一匹配的预置占用账户")


def _invalid_fact() -> None:
    _fail("material_request_reservation_options_fact_invalid", "conflict", "分配与预留事实不一致，已停止读取")


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestReservationOptionError(code, category, message)


__all__ = ["MaterialRequestReservationOptionError", "list_reservation_options"]
