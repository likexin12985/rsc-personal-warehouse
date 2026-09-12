"""Read-only batch preview. A successful preview never reserves stock."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import or_, select

from ..inventory_models import FormalMaterial, MaterialInventoryPolicy, StockAccount
from ..work_order_material_schemas import WorkOrderMaterialPreviewOut
from . import inventory_query as inventory
from . import inventory_posting as posting
from . import work_order_material as material
from .work_order_material_options import material_options
from .work_order_operation_read import client_request_hash
from .work_order_query import get_my_work_order


def _fail(code, message):
    raise material.WorkOrderMaterialPreflightError(code, message, "precondition_failed")


def _policies(db, material_ids, checked_at):
    rows = tuple(db.scalars(select(MaterialInventoryPolicy).where(
        MaterialInventoryPolicy.material_id.in_(material_ids),
        MaterialInventoryPolicy.effective_from <= checked_at,
        or_(MaterialInventoryPolicy.effective_to.is_(None), MaterialInventoryPolicy.effective_to > checked_at),
    ).execution_options(populate_existing=True)))
    by_material = {row.material_id: row for row in rows}
    if len(rows) != len(material_ids) or set(by_material) != material_ids:
        _fail("inventory_policy_not_unique", "物料没有唯一有效库存策略，请核验后再操作")
    if any(row.tracking_mode not in posting.TRACKING_MODES for row in rows):
        _fail("inventory_policy_not_unique", "物料库存策略无效")
    fingerprint = tuple(sorted((str(row.id), row.tracking_mode, row.quantity_scale, row.allow_fraction,
        row.effective_from.isoformat(), row.effective_to.isoformat() if row.effective_to else None) for row in rows))
    return by_material, fingerprint


def preview_batch(db, *, actor, work_order_id, operation_type, lines):
    if operation_type not in {"occupy", "consume", "release"} or len(lines) > 100:
        _fail("work_order_preview_invalid", "工单物料预检类型或明细数量无效")
    current = posting._require_current_actor(db, actor)
    material.validate_batch(lines)
    with db.no_autoflush:
        options = material_options(db, actor=current, work_order_id=work_order_id)
        if not options.work_order.can_operate:
            _fail("work_order_not_operable", "工单来源过期、已停用或权限不足，请刷新后核验")
        if options.opening_balance_status != "established":
            _fail("inventory_opening_required", "个人仓尚未完成期初建账")
        snapshot = inventory._ProjectionSnapshot(options.ledger_cursor, options.projected_at)
        checked_at = datetime.now(timezone.utc)
        candidates = {row.stock_account_id: row for row in options.items}
        accounts = {}
        for line in lines:
            candidate = candidates.get(line.stock_account_id)
            if (candidate is None or operation_type not in candidate.allowed_actions
                    or line.material_id != candidate.material_id or line.condition_before != candidate.condition_code):
                _fail("work_order_material_choice_invalid", "物料已不属于本人工单当前可操作范围，请刷新整批物料")
            if line.quantity > Decimal(candidate.selectable_quantity):
                _fail("work_order_material_quantity_insufficient", "整批预检未通过：物料数量超过本人可用库存或本工单剩余占用")
            if not set(line.serial_ids) <= {row.serial_id for row in candidate.serials}:
                _fail("work_order_serial_choice_invalid", "SN 已不属于本人可用库存或本工单剩余占用")
            if operation_type == "release":
                if line.target_stock_account_id != candidate.release_target_stock_account_id:
                    _fail("release_target_invalid", "释放目标与原个人仓库存维度不一致")
            elif line.target_stock_account_id is not None:
                _fail("work_order_target_invalid", "该操作不接受调用方指定库存目标")
            account = db.get(StockAccount, line.stock_account_id, populate_existing=True)
            item = db.get(FormalMaterial, line.material_id, populate_existing=True)
            if item is None or item.status != "active":
                _fail("material_invalid", "物料不存在或已停用")
            accounts[line.stock_account_id] = account
            material.verify_serial_proofs(db, line=line)
        policies, policy_fingerprint = _policies(db, {line.material_id for line in lines}, checked_at)
        _unfrozen(db, accounts, operation_type, checked_at)
        # Reuse the posting validator without calling any posting or account
        # creation function. Both directions inherit the same source dimensions.
        command = posting.InventoryPostingCommand(
            transaction_no="preview", movement_type=material.expected_posting_movement_type(operation_type),
            source_document_type="work_order_material", source_document_id=str(work_order_id),
            posting_key="preview", effective_at=checked_at,
            movements=tuple(posting.InventoryMovementCommand(from_account_id=line.stock_account_id,
                to_account_id=None, quantity=line.quantity, serial_ids=line.serial_ids) for line in lines))
        posting._validate_tracking_rules(command, accounts, policies)
        inventory._ensure_projection_snapshot_current(db, snapshot)
        if get_my_work_order(db, actor=current, work_order_id=work_order_id) != options.work_order:
            _fail("work_order_projection_changed", "工单来源在预检期间发生变化，请刷新后重新预检")
        if _policies(db, set(policies), datetime.now(timezone.utc))[1] != policy_fingerprint:
            _fail("work_order_policy_changed", "库存策略在预检期间发生变化，请重新预检")
        _unfrozen(db, accounts, operation_type, datetime.now(timezone.utc))
        inventory._ensure_projection_snapshot_current(db, snapshot)
        posting._require_current_actor(db, current)
    return WorkOrderMaterialPreviewOut(work_order_id=work_order_id, operator_person_id=current.person_id,
        authorization_version=current.authorization_version, operation_type=operation_type,
        source_version=options.work_order.source_version, ledger_cursor=options.ledger_cursor,
        checked_at=checked_at, line_count=len(lines), request_hash=client_request_hash(
            operation_type=operation_type, work_order_id=work_order_id, operator_person_id=current.person_id, lines=lines))


def _unfrozen(db, accounts, operation_type, checked_at):
    posting._require_no_active_hard_freezes(db, accounts, effective_at=checked_at)
    if operation_type in {"occupy", "release"}:
        # A first occupy may not yet have a reserved account. Check that exact
        # destination's dimensions without creating it, not only its source.
        targets = {key: SimpleNamespace(**{name: getattr(account, name) for name in (
            "owner_org_id", "location_id", "material_id", "condition_code")},
            availability_bucket="reserved" if operation_type == "occupy" else "available")
            for key, account in accounts.items()}
        posting._require_no_active_hard_freezes(db, targets, effective_at=checked_at)
