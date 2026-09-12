"""Read-only replacement preview and exact physical removed-part resolution."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from ..inventory_models import FormalMaterial, InventoryLot, InventorySerial, StockAccount
from ..work_order_material_schemas import WorkOrderReplacementPreviewOut, WorkOrderRemovedScanOut
from . import inventory_posting as posting
from . import inventory_query as inventory
from . import work_order_material as material
from . import work_order_replacements as replacements
from .work_order_material_options import material_options
from .work_order_preview import preview_batch, _policies, _fail
from .work_order_query import get_my_work_order


def _recover_check(db, *, current, line, policies, checked_at):
    dimensions, _account = replacements.recovery_target(db, operator_person_id=current.person_id,
        line=line, require_active=True)
    # The basis UUID is only the local lookup key for the shared validator.
    # Its mapped dimensions describe the removed part; no target ID is invented
    # or returned and no account/StockBalance is inserted by this read.
    contexts = {line.basis_stock_account_id: SimpleNamespace(**dimensions)}
    recovered = material.WorkOrderMaterialLineInput(line.material_id, line.basis_stock_account_id,
        line.quantity, line.serial_ids, line.condition_before, line.serial_verifications)
    posting._require_no_active_hard_freezes(db, contexts, effective_at=checked_at)
    posting._require_no_active_hard_freezes(db,
        {line.basis_stock_account_id: db.get(StockAccount, line.basis_stock_account_id)}, effective_at=checked_at)
    command = posting.InventoryPostingCommand(transaction_no="replacement-preview", movement_type="inbound",
        source_document_type="work_order_material", source_document_id="replacement-preview",
        posting_key="replacement-preview", effective_at=checked_at,
        movements=(posting.InventoryMovementCommand(None, line.basis_stock_account_id, line.quantity, line.serial_ids),))
    posting._validate_tracking_rules(command, contexts, policies)
    replacements._preflight_removed_serials(db, (recovered,), account_contexts=contexts)
    return tuple(sorted((key, str(value)) for key, value in dimensions.items()))


def preview_replacement(db, *, actor, work_order_id, consume_lines, recover_lines, pairs):
    current = posting._require_current_actor(db, actor)
    if len(consume_lines) > 100 or len(recover_lines) > 100:
        _fail("replacement_preview_invalid", "本次替换的投料与回收明细均不能超过 100 条")
    command = replacements.replacement_request_payload(work_order_id=work_order_id, operator_person_id=current.person_id,
        consume_lines=consume_lines, recover_lines=recover_lines, pairs=pairs)
    with db.no_autoflush:
        first = preview_batch(db, actor=current, work_order_id=work_order_id, operation_type="consume", lines=consume_lines)
        snapshot = inventory._projection_snapshot(db)
        if snapshot.ledger_cursor != first.ledger_cursor:
            _fail("replacement_preview_changed", "库存在预检期间发生变化，请重新核验整批替换")
        material_ids = {line.material_id for line in recover_lines}
        policies, fingerprint = _policies(db, material_ids, first.checked_at)
        dimensions = tuple(_recover_check(db, current=current, line=line, policies=policies,
            checked_at=first.checked_at) for line in recover_lines)
        if len(set(dimensions)) != len(dimensions):
            _fail("duplicate_stock_account", "同一回收目标的拆回件请合并数量后提交")
        inventory._ensure_projection_snapshot_current(db, snapshot)
        latest = preview_batch(db, actor=current, work_order_id=work_order_id, operation_type="consume", lines=consume_lines)
        if latest.ledger_cursor != first.ledger_cursor or latest.source_version != first.source_version:
            _fail("replacement_preview_changed", "工单或库存在预检期间发生变化，请重新核验整批替换")
        checked_at = datetime.now(timezone.utc)
        if _policies(db, material_ids, checked_at)[1] != fingerprint:
            _fail("work_order_policy_changed", "拆回件库存策略已变化，请重新预检")
        if dimensions != tuple(_recover_check(db, current=current, line=line, policies=policies,
                checked_at=checked_at) for line in recover_lines):
            _fail("replacement_preview_changed", "回收维度在预检期间发生变化，请重新核验")
        inventory._ensure_projection_snapshot_current(db, snapshot)
        posting._require_current_actor(db, current)
    return WorkOrderReplacementPreviewOut(work_order_id=work_order_id, operator_person_id=current.person_id,
        authorization_version=current.authorization_version, source_version=first.source_version,
        ledger_cursor=first.ledger_cursor, checked_at=checked_at, consume_line_count=len(consume_lines),
        recover_line_count=len(recover_lines), pair_count=len(pairs), request_hash=replacements._hash(command))


def lookup_removed_part(db, *, actor, work_order_id, scan):
    current = posting._require_current_actor(db, actor)
    if scan.operator_person_id != current.person_id:
        _fail("operator_mismatch", "操作人必须是当前登录人员")
    with db.no_autoflush:
        options = material_options(db, actor=current, work_order_id=work_order_id)
        basis = next((row for row in options.items if row.stock_account_id == scan.basis_stock_account_id), None)
        if (not options.work_order.can_operate or options.opening_balance_status != "established"
                or basis is None or "replace" not in basis.allowed_actions):
            _fail("replacement_basis_invalid", "请先选择本人工单的剩余占用物料，再核验拆回件")
        snapshot = inventory._ProjectionSnapshot(options.ledger_cursor, options.projected_at)
        checked_at = datetime.now(timezone.utc)
        sku = db.scalar(select(FormalMaterial).where(FormalMaterial.sku_code == scan.sku_code).execution_options(populate_existing=True))
        if sku is None or sku.status != "active":
            _fail("removed_material_not_found", "拆回物料未匹配有效 SKU，请核验物料编码")
        policies, fingerprint = _policies(db, {sku.id}, checked_at)
        policy = policies[sku.id]
        serial = None
        if policy.tracking_mode in {"serial", "lot_and_serial"}:
            if not scan.serial_no or not scan.qr_code:
                _fail("serial_verification_missing", "拆回受控物料须扫描 SKU、SN 和二维码")
            serial = db.scalar(select(InventorySerial).where(InventorySerial.material_id == sku.id,
                InventorySerial.serial_no == scan.serial_no, InventorySerial.qr_code == scan.qr_code)
                .execution_options(populate_existing=True))
            if serial is None:
                _fail("removed_serial_not_found", "拆回件三码未匹配已登记 SN；请核验后走受控登记，不能自动入账")
        elif scan.serial_no is not None or scan.qr_code is not None:
            _fail("serial_not_allowed", "该 SKU 不按 SN 管理，请核验库存策略")
        lot = None
        if policy.tracking_mode in {"lot", "lot_and_serial"}:
            if serial is not None:
                lot = db.get(InventoryLot, serial.lot_id, populate_existing=True) if serial.lot_id else None
                if scan.lot_no is not None and (lot is None or lot.lot_no != scan.lot_no):
                    _fail("recover_lot_invalid", "扫码 SN 与拆回批次不一致")
            elif scan.lot_no:
                lot = db.scalar(select(InventoryLot).where(InventoryLot.material_id == sku.id, InventoryLot.lot_no == scan.lot_no))
            if lot is None or lot.material_id != sku.id:
                _fail("recover_lot_invalid", "请提供拆回物料的准确已登记批次")
        elif scan.lot_no is not None:
            _fail("lot_not_allowed", "该物料策略不允许绑定批次")
        line = replacements.RecoveryLineInput(scan.basis_stock_account_id, sku.id, Decimal(1), scan.condition_before,
            lot_id=lot.id if lot else None, serial_ids=(serial.id,) if serial else (),
            serial_verifications=(material.SerialVerificationInput(serial.id, scan.sku_code, scan.serial_no, scan.qr_code),) if serial else ())
        metadata = _catalog_fingerprint(db, sku.id, line.lot_id)
        dimensions = _recover_check(db, current=current, line=line, policies=policies, checked_at=checked_at)
        output = WorkOrderRemovedScanOut(work_order_id=work_order_id, operator_person_id=current.person_id,
            authorization_version=current.authorization_version, source_version=options.work_order.source_version,
            ledger_cursor=options.ledger_cursor, checked_at=checked_at, basis_stock_account_id=scan.basis_stock_account_id,
            material_id=sku.id, sku_code=sku.sku_code, material_name=sku.name, base_unit=sku.base_unit,
            condition_before=scan.condition_before, tracking_mode=policy.tracking_mode,
            quantity_scale=policy.quantity_scale, allow_fraction=policy.allow_fraction,
            lot_id=lot.id if lot else None, lot_no=lot.lot_no if lot else None,
            serial_id=serial.id if serial else None, serial_no=serial.serial_no if serial else None)
        if get_my_work_order(db, actor=current, work_order_id=work_order_id) != options.work_order:
            _fail("work_order_projection_changed", "工单来源在核验期间发生变化，请重新扫描")
        if _policies(db, {sku.id}, datetime.now(timezone.utc))[1] != fingerprint:
            _fail("work_order_policy_changed", "拆回物料策略已变化，请重新扫描")
        if _recover_check(db, current=current, line=line, policies=policies, checked_at=datetime.now(timezone.utc)) != dimensions:
            _fail("replacement_preview_changed", "回收维度已变化，请重新核验")
        if _catalog_fingerprint(db, sku.id, line.lot_id) != metadata:
            _fail("removed_material_changed", "拆回物料或批次资料在核验期间变化，请重新扫描")
        inventory._ensure_projection_snapshot_current(db, snapshot)
        posting._require_current_actor(db, current)
        return output


def _catalog_fingerprint(db, material_id, lot_id):
    sku = db.get(FormalMaterial, material_id, populate_existing=True)
    lot = db.get(InventoryLot, lot_id, populate_existing=True) if lot_id else None
    return (sku.sku_code, sku.name, sku.base_unit, sku.status, sku.updated_at,
        (lot.material_id, lot.lot_no, lot.updated_at) if lot else None)
