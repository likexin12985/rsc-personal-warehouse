"""Fresh original-bound picking options; no facts or projections are written."""
from decimal import Decimal, InvalidOperation
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from ..demand_models import MaterialRequest
from ..inventory_models import (
    InventorySerial, SerialCurrentPosition, StockAccount, StockReservation,
    StockReservationRelease, StockReservationReleaseSerial, StockReservationSerial, StockReservationPick, StockReservationPickSerial,
)
from ..material_request_picking_schemas import PickOptionsOut
from . import inventory_query, material_request_query, material_request_reservation as reserve
from . import material_request_reservation_release as release
from . import material_request_picking as pick
from .inventory_posting import InventoryPostingError
from .material_request_reservation_options import MaterialRequestReservationOptionError, _current_actor

MAX_RESERVATIONS = 100
MAX_RELEASES = 1000
MAX_SERIALS = 1000
ZERO = Decimal("0.000")


class PickingOptionsError(reserve.MaterialRequestReservationError):
    pass


def _fail(code, category, message):
    raise PickingOptionsError(f"material_request_picking_options_{code}", category, message)


def _bounded(db, statement, limit):
    rows = tuple(db.scalars(statement.limit(limit + 1).execution_options(populate_existing=True)).all())
    if len(rows) > limit:
        _fail("limit_exceeded", "precondition_failed", "拣货记录超过单次核验上限，已停止返回不完整清单")
    return rows


def list_picking_options(db, *, actor, material_request_id: uuid.UUID, request_line_id: uuid.UUID):
    try:
        with db.no_autoflush:
            return _list(db, actor=actor, request_id=material_request_id, line_id=request_line_id)
    except (reserve.MaterialRequestReservationError, material_request_query.MaterialRequestReadError):
        raise
    except MaterialRequestReservationOptionError as exc:
        _fail("authorization_invalid", exc.category, exc.message)
    except InventoryPostingError as exc:
        _fail("account_forbidden", exc.category, exc.message)
    except inventory_query.InventoryReadError as exc:
        category = {403: "forbidden", 404: "not_found", 409: "conflict", 412: "precondition_failed"}.get(exc.status_code, "service_unavailable")
        _fail(exc.code, category, exc.public_message)
    except (DBAPIError, TypeError, ValueError, InvalidOperation):
        _fail("evidence_invalid", "service_unavailable", "拣货证据暂时无法核验，请刷新后重试")


def _list(db, *, actor, request_id, line_id):
    reserve._uuid(request_id, "request_id")
    reserve._uuid(line_id, "request_line_id")
    actor = _current_actor(db, actor)
    reserve._request_read_context(db, actor)
    view = material_request_query.material_request_detail(db, actor=actor, request_id=request_id)
    line = next((row for row in view.lines if row.request_line_id == line_id), None)
    if line is None:
        _fail("line_not_found", "not_found", "需求明细不存在")
    if line.revision_id != view.current_revision_id or line.revision_no != view.current_revision_no:
        _fail("revision_stale", "conflict", "需求明细版本已变化，请刷新详情")
    if view.states.request_status not in {"approved", "partially_approved"} or line.status not in {"approved", "partially_approved"}:
        _fail("approval_required", "precondition_failed", "需求及明细须完成最终审批后才能准备履约")
    if view.states.outbound_status not in {"not_started", "pending_pick"}:
        _fail("outbound_started", "precondition_failed", "需求已进入出库流程，请按对应履约单据核验")
    inventory_query._require_inventory_read(db, actor)
    snapshot = inventory_query._projection_snapshot(db)
    if snapshot.projected_at is None:
        _fail("projection_not_ready", "precondition_failed", "库存尚未建立可核验快照")
    facts = _bounded(db, select(StockReservation).where(
        StockReservation.request_id == request_id, StockReservation.request_line_id == line_id,
    ).order_by(StockReservation.request_version, StockReservation.id), MAX_RESERVATIONS)
    source_ids = {f.stock_account_id for f in facts}
    raw_rows = db.execute(inventory_query._account_row_statement(db, actor=actor)
        .where(StockAccount.id.in_(source_ids)).execution_options(populate_existing=True)).all() if source_ids else []
    rows = {}
    for raw in raw_rows:
        row = inventory_query._account_row(raw)
        if inventory_query._account_allowed(db, actor, row):
            rows[row.account.id] = row
    visible = [fact for fact in facts if fact.stock_account_id in rows]
    if facts and not visible:
        _fail("source_forbidden", "forbidden", "原占用账户不在当前库存授权范围内")
    if rows:
        inventory_query._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(rows))
        evidence = inventory_query._validated_opening_evidence(db, actor=actor, snapshot=snapshot,
            required_pairs={(row.account.owner_org_id, row.account.location_id) for row in rows.values()},
            discover_authorized_zero_scopes=False)
        if not evidence.complete:
            _fail("opening_invalid", "precondition_failed", "原占用账户期初证据尚未建立")
    request = db.get(MaterialRequest, request_id, populate_existing=True)
    ids = {f.id for f in visible}
    releases = _bounded(db, select(StockReservationRelease).where(
        StockReservationRelease.reservation_id.in_(ids)).order_by(StockReservationRelease.request_version), MAX_RELEASES) if ids else ()
    bindings = _bounded(db, select(StockReservationSerial).where(
        StockReservationSerial.reservation_id.in_(ids)), MAX_SERIALS) if ids else ()
    picks = _bounded(db, select(StockReservationPick).where(
        StockReservationPick.reservation_id.in_(ids)).order_by(StockReservationPick.request_version), MAX_RELEASES) if ids else ()
    picked_bindings = _bounded(db, select(StockReservationPickSerial).where(
        StockReservationPickSerial.reservation_id.in_(ids)), MAX_SERIALS) if ids else ()
    picked = {}
    for fact in picks:
        pick.verified_pick_history(db, fact=fact, request=request)
        picked[fact.reservation_id] = picked.get(fact.reservation_id, ZERO) + fact.picked_qty
    picked_keys = {(s.reservation_id, s.serial_id) for s in picked_bindings}
    released_bindings = _bounded(db, select(StockReservationReleaseSerial).where(
        StockReservationReleaseSerial.reservation_id.in_(ids)), MAX_SERIALS) if ids else ()
    used = {}
    for fact in releases:
        release.verified_release_history(db, fact=fact, request=request)
        used[fact.reservation_id] = used.get(fact.reservation_id, ZERO) + fact.released_qty
    released_keys = {(s.reservation_id, s.serial_id) for s in released_bindings}
    bound_keys = {(s.reservation_id, s.serial_id) for s in bindings}
    if not (released_keys | picked_keys) <= bound_keys or released_keys & picked_keys:
        _fail("serial_graph_invalid", "conflict", "释放 SN 与原占用绑定不一致")
    serial_ids = {s.serial_id for s in bindings}
    serial_rows = db.execute(select(InventorySerial, SerialCurrentPosition.stock_account_id)
        .outerjoin(SerialCurrentPosition, SerialCurrentPosition.serial_id == InventorySerial.id)
        .where(InventorySerial.id.in_(serial_ids)).execution_options(populate_existing=True)).all() if serial_ids else []
    serials = {s.id: (s, account_id) for s, account_id in serial_rows}
    # All claims on each shared pool are checked, including other requests.
    # No identity or quantity belonging to an invisible request is returned.
    reserved_totals = dict(db.execute(select(StockReservation.stock_account_id, func.sum(StockReservation.reserved_qty))
        .where(StockReservation.stock_account_id.in_(rows)).group_by(StockReservation.stock_account_id)).all()) if rows else {}
    release_totals = dict(db.execute(select(StockReservationRelease.source_stock_account_id, func.sum(StockReservationRelease.released_qty))
        .where(StockReservationRelease.source_stock_account_id.in_(rows)).group_by(StockReservationRelease.source_stock_account_id)).all()) if rows else {}
    pick_totals = dict(db.execute(select(StockReservationPick.source_stock_account_id, func.sum(StockReservationPick.picked_qty))
        .where(StockReservationPick.source_stock_account_id.in_(rows)).group_by(StockReservationPick.source_stock_account_id)).all()) if rows else {}
    policy = inventory_query._effective_policy(db, line.material_id)
    candidates = []
    for fact in visible:
        if fact.revision_id != line.revision_id or fact.revision_no != line.revision_no:
            _fail("reservation_revision_invalid", "conflict", "原占用与当前需求明细版本不一致")
        reserve._verified_history(db, fact=fact, request=request)
        row = rows[fact.stock_account_id]
        account, balance = row.account, row.balance
        if account.availability_bucket != "reserved" or account.material_id != line.material_id:
            _fail("source_invalid", "conflict", "原占用账户维度不一致")
        if ((policy.tracking_mode in {"none", "serial"} and account.lot_id is not None)
            or (policy.tracking_mode in {"lot", "lot_and_serial"} and
                (row.lot is None or row.lot.material_id != line.material_id or row.lot.id != account.lot_id))):
            _fail("lot_invalid", "conflict", "原占用批次与当前物料策略不一致")
        if balance is None:
            _fail("balance_missing", "precondition_failed", "原占用账户缺少余额投影")
        inventory_query._ensure_balance_at_snapshot(balance, snapshot)
        released = used.get(fact.id, ZERO)
        consumed = picked.get(fact.id, ZERO)
        remaining = fact.reserved_qty - released - consumed
        pool_claim = reserved_totals.get(account.id, ZERO) - release_totals.get(account.id, ZERO) - pick_totals.get(account.id, ZERO)
        if remaining < 0 or pool_claim < remaining:
            _fail("quantity_graph_invalid", "conflict", "占用与释放数量证据不一致")
        options = []
        blockers = []
        if remaining > 0:
            if row.material.status != "active" or row.location.status != "active":
                blockers.append("source_inactive")
            if balance.quantity < pool_claim:
                blockers.append("pool_shortfall")
            original_ids = {s.serial_id for s in bindings if s.reservation_id == fact.id}
            own_released = {serial_id for reservation_id, serial_id in released_keys if reservation_id == fact.id}
            own_picked = {serial_id for reservation_id, serial_id in picked_keys if reservation_id == fact.id}
            remaining_ids = original_ids - own_released - own_picked
            if policy.tracking_mode in {"serial", "lot_and_serial"}:
                for serial_id in sorted(remaining_ids, key=str):
                    serial, current_id = serials.get(serial_id, (None, None))
                    if (serial is not None and serial.lifecycle_status == "active" and current_id == account.id
                        and serial.material_id == account.material_id and serial.lot_id == account.lot_id):
                        options.append(dict(serial_id=serial.id, serial_no=serial.serial_no))
                if len(original_ids) != fact.reserved_qty or len(own_released) != released or len(own_picked) != consumed or len(options) != remaining:
                    blockers.append("serial_mismatch")
            elif original_ids:
                _fail("policy_changed", "conflict", "原占用 SN 与当前物料策略不一致")
        if remaining == 0:
            continue
        if blockers:
            _fail("source_blocked", "precondition_failed", "原占用余额或 SN 核验失败，请先处理库存异常")
        target = pick._picking_target(db, account)
        pick.authorize_pick_accounts(db, actor, account.id, target.id)
        candidates.append(dict(
            reservation_id=fact.id, reservation_no=fact.reservation_no, allocation_id=fact.allocation_id,
            source_stock_account_id=account.id, target_stock_account_id=target.id,
            location_name=row.location.name, sku_code=row.material.sku_code, material_name=row.material.name,
            condition_code=account.condition_code, lot_no=row.lot.lot_no if row.lot else None,
            reserved_qty=format(fact.reserved_qty, ".3f"), released_qty=format(released, ".3f"),
            picked_qty=format(consumed, ".3f"), pickable_qty=format(remaining, ".3f"),
            tracking_mode=policy.tracking_mode, quantity_scale=policy.quantity_scale, allow_fraction=policy.allow_fraction,
            source_balance_version=balance.version, source_ledger_cursor=balance.ledger_cursor, serials=options,
        ))
    reread = material_request_query.material_request_detail(db, actor=actor, request_id=request_id)
    if (reread.request_version != view.request_version or reread.current_revision_id != view.current_revision_id
        or reread.current_revision_no != view.current_revision_no or reread.states != view.states or reread.lines != view.lines):
        _fail("request_changed", "conflict", "读取期间需求版本已变化，请刷新详情")
    if _current_actor(db, actor) != actor:
        _fail("authorization_changed", "forbidden", "读取期间授权范围已变化")
    reserve._request_read_context(db, actor)
    inventory_query._require_inventory_read(db, actor)
    if any(not inventory_query._account_allowed(db, actor, row) for row in rows.values()):
        _fail("authorization_changed", "forbidden", "读取期间库存授权范围已变化")
    for item in candidates:
        pick.authorize_pick_accounts(db, actor, item["source_stock_account_id"], item["target_stock_account_id"])
    inventory_query._ensure_projection_snapshot_current(db, snapshot)
    return PickOptionsOut(request_id=request_id, request_line_id=line_id,
        request_version=view.request_version, revision_id=line.revision_id, revision_no=line.revision_no,
        state_axes=view.states,
        items=candidates)
