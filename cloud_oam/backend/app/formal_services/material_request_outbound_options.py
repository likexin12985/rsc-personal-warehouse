"""Fresh, original-pick-bound physical outbound options without writes."""
from decimal import Decimal, InvalidOperation
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from ..demand_models import MaterialRequest
from ..inventory_models import (
    InventorySerial, SerialCurrentPosition, StockAccount,
    StockReservationPick, StockReservationPickSerial, OutboundPosting, OutboundPostingSerial,
)
from ..material_request_outbound_schemas import OutboundOptionsOut
from . import inventory_query, material_request_query, material_request_reservation as reserve
from . import material_request_picking as picking, material_request_outbound as outbound
from .inventory_posting import InventoryPostingError
from .material_request_reservation_options import MaterialRequestReservationOptionError, _current_actor

MAX_PICKS = 100
MAX_POSTINGS = MAX_SERIALS = 1000
ZERO = Decimal("0.000")


class OutboundOptionsError(reserve.MaterialRequestReservationError):
    pass


def _fail(code, category, message):
    raise OutboundOptionsError(f"material_request_outbound_options_{code}", category, message)


def _bounded(db, statement, limit):
    rows = tuple(db.scalars(statement.limit(limit + 1).execution_options(populate_existing=True)).all())
    if len(rows) > limit:
        _fail("limit_exceeded", "precondition_failed", "出库记录超过单次核验上限，已停止返回不完整清单")
    return rows


def list_outbound_options(db, *, actor, material_request_id: uuid.UUID, request_line_id: uuid.UUID):
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
        _fail("evidence_invalid", "service_unavailable", "出库证据暂时无法核验，请刷新后重试")


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
        _fail("approval_required", "precondition_failed", "需求及明细须完成最终审批")
    if view.states.outbound_status not in {"pending_pick", "picked", "outbound"}:
        _fail("picking_required", "precondition_failed", "需求尚无已确认的实物拣货")
    inventory_query._require_inventory_read(db, actor)
    snapshot = inventory_query._projection_snapshot(db)
    if snapshot.projected_at is None:
        _fail("projection_not_ready", "precondition_failed", "库存尚未建立可核验快照")
    facts = _bounded(db, select(StockReservationPick).where(
        StockReservationPick.request_id == request_id, StockReservationPick.request_line_id == line_id,
    ).order_by(StockReservationPick.request_version, StockReservationPick.id), MAX_PICKS)
    source_ids = {f.target_stock_account_id for f in facts}
    raw_rows = db.execute(inventory_query._account_row_statement(db, actor=actor)
        .where(StockAccount.id.in_(source_ids)).execution_options(populate_existing=True)).all() if source_ids else []
    rows = {}
    for raw in raw_rows:
        row = inventory_query._account_row(raw)
        if inventory_query._account_allowed(db, actor, row):
            rows[row.account.id] = row
    visible = [fact for fact in facts if fact.target_stock_account_id in rows]
    if facts and not visible:
        _fail("source_forbidden", "forbidden", "原拣货账户不在当前库存授权范围内")
    if rows:
        inventory_query._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(rows))
        evidence = inventory_query._validated_opening_evidence(db, actor=actor, snapshot=snapshot,
            required_pairs={(row.account.owner_org_id, row.account.location_id) for row in rows.values()},
            discover_authorized_zero_scopes=False)
        if not evidence.complete:
            _fail("opening_invalid", "precondition_failed", "原拣货账户期初证据尚未建立")
    request = db.get(MaterialRequest, request_id, populate_existing=True)
    ids = {f.id for f in visible}
    postings = _bounded(db, select(OutboundPosting).where(OutboundPosting.pick_id.in_(ids))
        .order_by(OutboundPosting.request_version), MAX_POSTINGS) if ids else ()
    bindings = _bounded(db, select(StockReservationPickSerial).where(
        StockReservationPickSerial.pick_id.in_(ids)), MAX_SERIALS) if ids else ()
    consumed_bindings = _bounded(db, select(OutboundPostingSerial).where(
        OutboundPostingSerial.pick_id.in_(ids)), MAX_SERIALS) if ids else ()
    used, last_at = {}, {}
    for fact in postings:
        outbound.verified_outbound_history(db, fact=fact, request=request)
        used[fact.pick_id] = used.get(fact.pick_id, ZERO) + fact.outbound_qty
        last_at[fact.pick_id] = reserve._historical_utc(fact.created_at).isoformat()
    consumed_keys = {(s.pick_id, s.serial_id) for s in consumed_bindings}
    bound_keys = {(s.pick_id, s.serial_id) for s in bindings}
    if not consumed_keys <= bound_keys:
        _fail("serial_graph_invalid", "conflict", "出库 SN 与原拣货绑定不一致")
    serial_ids = {s.serial_id for s in bindings}
    serial_rows = db.execute(select(InventorySerial, SerialCurrentPosition.stock_account_id)
        .outerjoin(SerialCurrentPosition, SerialCurrentPosition.serial_id == InventorySerial.id)
        .where(InventorySerial.id.in_(serial_ids)).execution_options(populate_existing=True)).all() if serial_ids else []
    serials = {s.id: (s, account_id) for s, account_id in serial_rows}
    picked_totals = dict(db.execute(select(StockReservationPick.target_stock_account_id, func.sum(StockReservationPick.picked_qty))
        .where(StockReservationPick.target_stock_account_id.in_(rows))
        .group_by(StockReservationPick.target_stock_account_id)).all()) if rows else {}
    outbound_totals = dict(db.execute(select(OutboundPosting.source_stock_account_id, func.sum(OutboundPosting.outbound_qty))
        .where(OutboundPosting.source_stock_account_id.in_(rows))
        .group_by(OutboundPosting.source_stock_account_id)).all()) if rows else {}
    policy = inventory_query._effective_policy(db, line.material_id)
    candidates = []
    for fact in visible:
        if fact.revision_id != line.revision_id or fact.revision_no != line.revision_no:
            _fail("pick_revision_invalid", "conflict", "原拣货与当前需求明细版本不一致")
        original = picking.verified_pick_history(db, fact=fact, request=request)
        row = rows[fact.target_stock_account_id]
        account, balance = row.account, row.balance
        if account.availability_bucket != "picking" or account.material_id != line.material_id:
            _fail("source_invalid", "conflict", "原拣货账户维度不一致")
        if ((policy.tracking_mode in {"none", "serial"} and account.lot_id is not None)
            or (policy.tracking_mode in {"lot", "lot_and_serial"} and
                (row.lot is None or row.lot.material_id != line.material_id or row.lot.id != account.lot_id))):
            _fail("lot_invalid", "conflict", "原拣货批次与当前物料策略不一致")
        if balance is None:
            _fail("balance_missing", "precondition_failed", "原拣货账户缺少余额投影")
        inventory_query._ensure_balance_at_snapshot(balance, snapshot)
        consumed = used.get(fact.id, ZERO)
        remaining = fact.picked_qty - consumed
        pool_claim = picked_totals.get(account.id, ZERO) - outbound_totals.get(account.id, ZERO)
        original_ids = {s.serial_id for s in bindings if s.pick_id == fact.id}
        consumed_ids = {serial_id for pick_id, serial_id in consumed_keys if pick_id == fact.id}
        if remaining < 0 or pool_claim < remaining:
            _fail("quantity_graph_invalid", "conflict", "拣货与出库数量证据不一致")
        if policy.tracking_mode in {"serial", "lot_and_serial"}:
            if len(original_ids) != fact.picked_qty or len(consumed_ids) != consumed:
                _fail("serial_graph_invalid", "conflict", "出库 SN 数量与原拣货不一致")
        elif original_ids:
            _fail("policy_changed", "conflict", "原拣货 SN 与当前物料策略不一致")
        if remaining == 0:
            continue
        if row.material.status != "active" or row.location.status != "active" or balance.quantity < pool_claim:
            _fail("source_blocked", "precondition_failed", "原拣货账户不可用或余额不足")
        options = []
        for serial_id in sorted(original_ids - consumed_ids, key=str):
            serial, current_id = serials.get(serial_id, (None, None))
            if (serial is None or serial.lifecycle_status != "active" or current_id != account.id
                or serial.material_id != account.material_id or serial.lot_id != account.lot_id):
                _fail("serial_blocked", "precondition_failed", "原拣货 SN 当前位置或物料维度不符")
            options.append(dict(serial_id=serial.id, serial_no=serial.serial_no))
        target = outbound.transit_target(db, account)
        outbound.authorize_outbound_accounts(db, actor, account.id, target.id)
        candidates.append(dict(
            pick_id=fact.id, pick_no=fact.pick_no, outbound_line_id=fact.outbound_line_id,
            outbound_id=original["outbound_id"], outbound_no=original["outbound_no"],
            reservation_id=fact.reservation_id, allocation_id=fact.allocation_id,
            source_stock_account_id=account.id, target_stock_account_id=target.id,
            location_name=row.location.name, sku_code=row.material.sku_code, material_name=row.material.name,
            condition_code=account.condition_code, lot_no=row.lot.lot_no if row.lot else None,
            picked_qty=format(fact.picked_qty, ".3f"), outbound_qty=format(consumed, ".3f"),
            outboundable_qty=format(remaining, ".3f"), last_outbound_at=last_at.get(fact.id),
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
        outbound.authorize_outbound_accounts(db, actor, item["source_stock_account_id"], item["target_stock_account_id"])
    inventory_query._ensure_projection_snapshot_current(db, snapshot)
    return OutboundOptionsOut(request_id=request_id, request_line_id=line_id,
        request_version=view.request_version, revision_id=line.revision_id, revision_no=line.revision_no,
        state_axes=view.states, items=candidates)
