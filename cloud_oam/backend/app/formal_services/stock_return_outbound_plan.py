"""Plan a partial physical return departure from its exact committed lines."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from ..inventory_models import StockAccount, StockBalance, FormalMaterial, InventoryLot, InventorySerial, SerialCurrentPosition
from ..stock_operation_models import StockOperationOrder, StockOperationCancellation, StockOperationOutbound
from ..stock_return_outbound_schemas import StockReturnOutboundPreviewIn, StockReturnOutboundPreviewOut, StockReturnOutboundLineOut
from . import inventory_posting as posting, inventory_query as inventory, work_order_material as material
from . import stock_return_facts as returns
from .stock_return_plan import authorize, destination
from .work_order_query import _aware
from .work_order_return_sources import _hash, _fail, _policies
from .work_order_evidence_snapshot import material_audit_cursor


def intent(operation_id, request):
    value = StockReturnOutboundPreviewIn.model_validate(request.model_dump(include=set(StockReturnOutboundPreviewIn.model_fields)))
    data = value.model_dump(mode="json")
    data["outbound_at"] = value.outbound_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    data["lines"] = [{**line.model_dump(mode="json"), "quantity": format(line.quantity, ".3f"),
        "serial_verifications": [proof.model_dump(mode="json") for proof in sorted(line.serial_verifications, key=lambda row: str(row.serial_id))]}
        for line in sorted(value.lines, key=lambda row: str(row.operation_line_id))]
    return {"operation_type": "outbound_return", "operation_id": str(operation_id), **data}


def original(db, actor, operation_id, work_order_id):
    order = db.get(StockOperationOrder, operation_id, populate_existing=True)
    if order is None or order.oam_work_order_id != work_order_id:
        _fail("stock_return_not_found", "本人原退回单不存在", 404)
    result = returns.order_result(db, actor=actor, order=order)
    cancelled = db.scalar(select(StockOperationCancellation).where(StockOperationCancellation.operation_id == order.id))
    if cancelled:
        returns.cancellation_result(db, actor=actor, order=order, cancellation=cancelled)
        _fail("stock_return_already_cancelled", "原退回单已取消，不能登记实物发出")
    return order, result


def departed(db, actor, order):
    from .stock_return_outbound_facts import outbound_result, lines as posted_lines, serial_ids
    quantities = defaultdict(Decimal); serials = defaultdict(set)
    facts = tuple(db.scalars(select(StockOperationOutbound).where(StockOperationOutbound.operation_id == order.id)
        .order_by(StockOperationOutbound.id).limit(1001).execution_options(populate_existing=True)))
    if len(facts) > 1000: _fail("stock_return_outbound_history_limit", "发出历史超过完整核验范围，请核验原单", 503)
    for fact in facts:
        outbound_result(db, actor=actor, fact=fact)
        for line in posted_lines(db, fact):
            ids = set(serial_ids(db, line))
            if serials[line.operation_line_id] & ids: returns.invalid()
            quantities[line.operation_line_id] += line.quantity
            serials[line.operation_line_id].update(ids)
    return quantities, serials


def transit_dimensions(source, order):
    return {**{name: getattr(source, name) for name in ("owner_org_id", "custodian_person_id", "material_id", "condition_code", "lot_id")},
        "location_id": order.transit_location_id, "availability_bucket": "in_transit"}


def _basis(db, actor, order, request):
    snapshot = inventory._projection_snapshot(db)
    at = datetime.now(timezone.utc)
    if request.outbound_at < _aware(order.created_at) or request.outbound_at > at:
        _fail("stock_return_outbound_time_invalid", "实物发出时间不能早于原退回提交或晚于当前时间")
    route = destination(db, person_id=actor.person_id, source_location_id=order.source_location_id,
        target_location_id=order.target_location_id, transit_location_id=order.transit_location_id, at=at)
    rows = {line.id: line for line in returns.rows(db, order)}
    accounts = {line.reserved_account_id: db.get(StockAccount, line.reserved_account_id, populate_existing=True) for line in rows.values()}
    posting._authorize_account_ids(db, actor, tuple(sorted(accounts, key=str)), resource="inventory", action="read", lock_rows=False)
    posting._require_active_account_masters(db, accounts)
    pairs = {(account.owner_org_id, account.location_id) for account in accounts.values()}
    pairs.update((account.owner_org_id, order.transit_location_id) for account in accounts.values())
    evidence = inventory._validated_opening_evidence(db, actor=actor, snapshot=snapshot, required_pairs=pairs,
        discover_authorized_zero_scopes=False)
    if not evidence.complete:
        _fail("stock_return_outbound_opening_required", "原个人仓和退回在途位置必须先完成正式期初核验")
    if any(_aware(row.established_at) > request.outbound_at for row in evidence.by_scope.values()):
        _fail("stock_return_outbound_time_invalid", "实物发出时间不能早于相关位置的期初建立")
    inventory._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(accounts))
    quantities, prior_serials = departed(db, actor, order)
    policies, policy_fingerprint = _policies(db, {row.material_id for row in rows.values()}, at)
    seen = set(); seen_serials = set(); totals = defaultdict(Decimal); selected_lines = []; moves = []
    for chosen in sorted(request.lines, key=lambda row: str(row.operation_line_id)):
        line = rows.get(chosen.operation_line_id)
        if line is None or line.id in seen: _fail("stock_return_outbound_line_invalid", "必须逐行选择原退回单明细，不能重复或替换来源")
        seen.add(line.id)
        source = accounts[line.reserved_account_id]
        available = db.get(StockBalance, source.id, populate_existing=True)
        held = available.quantity if available else Decimal(0)
        remaining = line.quantity - quantities[line.id]
        if remaining < 0 or held < 0: returns.invalid()
        if chosen.quantity > remaining: _fail("stock_return_outbound_quantity_exceeded", "本次发出超过该退回行尚未发出的数量")
        totals[source.id] += chosen.quantity
        if totals[source.id] > held: _fail("stock_return_outbound_stock_insufficient", "整批发出超过原待退回账户的实际库存")
        ids = tuple(proof.serial_id for proof in chosen.serial_verifications)
        if len(set(ids)) != len(ids) or seen_serials.intersection(ids): _fail("stock_return_outbound_serial_duplicate", "整批发出中同一 SN 只能选择一次")
        seen_serials.update(ids)
        if not set(ids) <= set(returns.serial_ids(db, line)) - prior_serials[line.id]:
            _fail("stock_return_outbound_serial_invalid", "SN 必须属于该退回行且尚未发出")
        physical = material.WorkOrderMaterialLineInput(material_id=source.material_id, stock_account_id=source.id,
            quantity=chosen.quantity, serial_ids=ids, condition_before=source.condition_code,
            serial_verifications=tuple(material.SerialVerificationInput(**proof.model_dump()) for proof in chosen.serial_verifications))
        material.verify_serial_proofs(db, line=physical)
        for identifier in ids:
            sn = db.get(InventorySerial, identifier, populate_existing=True)
            position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
            if position is None or position.stock_account_id != source.id or sn.lifecycle_status != "active":
                _fail("stock_return_outbound_serial_position", "扫码实物已不在原待退回账户")
        sku = db.get(FormalMaterial, source.material_id, populate_existing=True)
        lot = db.get(InventoryLot, source.lot_id, populate_existing=True) if source.lot_id else None
        selected_lines.append(StockReturnOutboundLineOut(operation_line_id=line.id, source_recovery_line_id=line.source_recovery_line_id,
            source_stock_account_id=source.id, material_id=source.material_id, sku_code=sku.sku_code, material_name=sku.name,
            base_unit=sku.base_unit, condition_code=source.condition_code, lot_id=source.lot_id, lot_no=lot.lot_no if lot else None,
            return_quantity=format(line.quantity, ".3f"), departed_quantity=format(quantities[line.id], ".3f"),
            remaining_quantity=format(remaining, ".3f"), held_quantity=format(held, ".3f"), selected_quantity=format(chosen.quantity, ".3f"),
            selected_serials=tuple(dict(serial_id=proof.serial_id, serial_no=proof.serial_no)
                for proof in sorted(chosen.serial_verifications, key=lambda row: str(row.serial_id)))))
        moves.append(posting.InventoryMovementCommand(from_account_id=source.id, to_account_id=None, quantity=chosen.quantity, serial_ids=ids))
    command = posting.InventoryPostingCommand(transaction_no="return-outbound-preview", movement_type="transfer",
        source_document_type="stock_operation_return_outbound", source_document_id=str(order.id), posting_key="preview",
        effective_at=request.outbound_at, movements=tuple(moves))
    posting._validate_tracking_rules(command, accounts, policies)
    posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
    targets = {identifier: SimpleNamespace(**transit_dimensions(account, order)) for identifier, account in accounts.items()}
    posting._require_no_active_hard_freezes(db, targets, effective_at=at)
    inventory._ensure_projection_snapshot_current(db, snapshot)
    return snapshot, route, tuple(selected_lines), policy_fingerprint


def preview_outbound(db, *, actor, work_order_id, operation_id, request):
    request = StockReturnOutboundPreviewIn.model_validate(request.model_dump(include=set(StockReturnOutboundPreviewIn.model_fields)))
    current = authorize(db, actor, "outbound_return")
    if request.operator_person_id != current.person_id: _fail("operator_mismatch", "操作人必须是当前登录人员", 403)
    with db.no_autoflush:
        audit = material_audit_cursor(db)
        order, original_result = original(db, current, operation_id, work_order_id)
        first = _basis(db, current, order, request)
        latest = _basis(db, current, order, request)
        if (first != latest or material_audit_cursor(db) != audit or returns.order_result(db, actor=current, order=order) != original_result):
            _fail("stock_return_outbound_plan_changed", "原退回、库存或接收责任在预检期间变化，请重新核验")
        snapshot, route, lines, policies = latest
        inventory._ensure_projection_snapshot_current(db, snapshot)
        authorize(db, current, "outbound_return")
        value = intent(operation_id, request)
        plan = {"intent": value, "authorization_version": current.authorization_version, "ledger_cursor": snapshot.ledger_cursor,
            "original_request_hash": original_result.request_hash, "destination": route.model_dump(mode="json"),
            "policies": policies, "lines": [row.model_dump(mode="json") for row in lines]}
        return StockReturnOutboundPreviewOut(operation_id=order.id, operation_no=order.operation_no, work_order_id=work_order_id,
            operator_person_id=current.person_id, authorization_version=current.authorization_version, outbound_at=request.outbound_at,
            reason=request.reason, ledger_cursor=snapshot.ledger_cursor, checked_at=datetime.now(timezone.utc), destination=route,
            request_hash=_hash(value), plan_hash=_hash(plan), lines=lines), plan
