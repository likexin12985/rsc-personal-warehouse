"""Bounded, current release candidates anchored to original reservations."""
from decimal import Decimal
import uuid

from sqlalchemy import select

from ..demand_models import MaterialRequest
from ..inventory_models import (
    InventorySerial, SerialCurrentPosition, StockAccount, StockReservation,
    StockReservationSerial, StockReservationReleaseSerial,
)
from ..material_request_reservation_release_schemas import ReservationReleaseOptionsOut
from . import inventory_query, material_request_query, material_request_reservation as reserve
from . import material_request_reservation_release as release
from .inventory_posting import InventoryPostingError


def list_release_options(db, *, actor, material_request_id: uuid.UUID, request_line_id: uuid.UUID):
    try:
        with db.no_autoflush:
            return _list(db, actor=actor, request_id=material_request_id, line_id=request_line_id)
    except (reserve.MaterialRequestReservationError, InventoryPostingError) as exc:
        raise release.MaterialRequestReservationReleaseError(exc.code, exc.category, exc.message) from exc
    except inventory_query.InventoryReadError as exc:
        category = {403: "forbidden", 404: "not_found", 409: "conflict", 412: "precondition_failed"}.get(exc.status_code, "service_unavailable")
        raise release.MaterialRequestReservationReleaseError(exc.code, category, exc.public_message) from exc
    except material_request_query.MaterialRequestReadError as exc:
        raise release.MaterialRequestReservationReleaseError(exc.code, exc.category, exc.message) from exc


def _list(db, *, actor, request_id, line_id):
    reserve._uuid(request_id, "request_id")
    reserve._uuid(line_id, "request_line_id")
    reserve._request_read_context(db, actor)
    view = material_request_query.material_request_detail(db, actor=actor, request_id=request_id)
    line = next((row for row in view.lines if row.request_line_id == line_id), None)
    if line is None:
        release._fail("line_not_found", "not_found", "需求明细不存在")
    if line.revision_id != view.current_revision_id or line.revision_no != view.current_revision_no:
        release._fail("revision_stale", "conflict", "需求明细版本已变化")
    if view.states.request_status not in {"approved", "partially_approved"} or view.states.outbound_status != "not_started":
        release._fail("state_invalid", "precondition_failed", "当前需求状态不允许直接释放占用")
    inventory_query._require_inventory_read(db, actor)
    facts = tuple(db.scalars(select(StockReservation).where(
        StockReservation.request_id == request_id, StockReservation.request_line_id == line_id,
        StockReservation.revision_id == line.revision_id,
    ).order_by(StockReservation.request_version, StockReservation.id).limit(101)).all())
    if len(facts) > 100:
        release._fail("too_many", "precondition_failed", "当前明细占用记录过多，请按业务批次核验")
    request = db.get(MaterialRequest, request_id, populate_existing=True)
    snapshot = inventory_query._projection_snapshot(db)
    ids = {account_id for f in facts for account_id in (f.stock_account_id, f.source_stock_account_id)}
    raw_rows = db.execute(inventory_query._account_row_statement(db, actor=actor).where(StockAccount.id.in_(ids))).all() if ids else []
    rows = {}
    for raw in raw_rows:
        row = inventory_query._account_row(raw)
        if inventory_query._account_allowed(db, actor, row): rows[row.account.id] = row
    visible = [f for f in facts if f.stock_account_id in rows and f.source_stock_account_id in rows]
    if facts and not visible:
        release._fail("source_forbidden", "forbidden", "原占用账户不在当前库存授权范围内")
    if rows:
        inventory_query._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(rows))
        evidence = inventory_query._validated_opening_evidence(db, actor=actor, snapshot=snapshot,
            required_pairs={(row.account.owner_org_id, row.account.location_id) for row in rows.values()},
            discover_authorized_zero_scopes=False)
        if not evidence.complete:
            release._fail("opening_invalid", "precondition_failed", "原占用账户期初证据尚未建立")
    candidates = []
    total_serials = 0
    for fact in visible:
        release.authorize_release_accounts(db, actor, fact.stock_account_id, fact.source_stock_account_id)
        reserve._verified_history(db, fact=fact, request=request)
        used = release.released_quantity(db, fact.id)
        remaining = fact.reserved_qty - used
        if remaining < 0: release._history_invalid()
        if remaining == 0: continue
        row = rows[fact.stock_account_id]
        target = rows[fact.source_stock_account_id]
        if row.balance is None or target.balance is None:
            release._fail("balance_missing", "precondition_failed", "原占用账户缺少余额投影")
        inventory_query._ensure_balance_at_snapshot(row.balance, snapshot)
        inventory_query._ensure_balance_at_snapshot(target.balance, snapshot)
        if (row.account.availability_bucket != "reserved" or target.account.availability_bucket != "available"
            or row.material.status != "active" or row.location.status != "active"):
            release._fail("source_invalid", "precondition_failed", "原占用账户或物料状态已变化")
        capacity = min(remaining, row.balance.quantity)
        if capacity <= 0: continue
        policy = inventory_query._effective_policy(db, row.account.material_id)
        released_serials = select(StockReservationReleaseSerial.serial_id).where(StockReservationReleaseSerial.reservation_id == fact.id)
        serials = db.execute(select(InventorySerial.id, InventorySerial.serial_no)
            .join(StockReservationSerial, StockReservationSerial.serial_id == InventorySerial.id)
            .join(SerialCurrentPosition, SerialCurrentPosition.serial_id == InventorySerial.id)
            .where(StockReservationSerial.reservation_id == fact.id,
                InventorySerial.id.not_in(released_serials), InventorySerial.lifecycle_status == "active",
                SerialCurrentPosition.stock_account_id == fact.stock_account_id)
            .order_by(InventorySerial.id).limit(1001)).all()
        total_serials += len(serials)
        if total_serials > 1000:
            release._fail("too_many_serials", "precondition_failed", "当前释放候选 SN 超出单次核验范围")
        if policy.tracking_mode in {"serial", "lot_and_serial"}:
            capacity = min(capacity, Decimal(len(serials)))
        elif serials:
            release._fail("policy_changed", "conflict", "原占用 SN 与当前物料策略不一致")
        if capacity <= 0: continue
        candidates.append(dict(
            reservation_id=fact.id, reservation_no=fact.reservation_no, allocation_id=fact.allocation_id,
            reserved_qty=format(fact.reserved_qty, ".3f"), released_qty=format(used, ".3f"), releasable_qty=format(capacity, ".3f"),
            material_name=row.material.name, sku_code=row.material.sku_code, location_name=row.location.name,
            tracking_mode=policy.tracking_mode, quantity_scale=policy.quantity_scale, allow_fraction=policy.allow_fraction,
            source_stock_account_id=fact.stock_account_id, target_stock_account_id=fact.source_stock_account_id,
            source_balance_version=row.balance.version, source_ledger_cursor=row.balance.ledger_cursor,
            serials=[dict(serial_id=s.id, serial_no=s.serial_no) for s in serials],
        ))
    return ReservationReleaseOptionsOut(request_id=request_id, request_line_id=line_id,
        request_version=view.request_version, revision_id=line.revision_id, revision_no=line.revision_no,
        state_axes=reserve._state_axes(request), items=candidates)
