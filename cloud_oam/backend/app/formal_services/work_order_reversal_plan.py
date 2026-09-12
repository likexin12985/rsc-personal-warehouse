"""Prove the whole original and propose exact inverses without posting them.

The plan is not an admission token. A dedicated writer must reconstruct it under
the ledger/reference locks and apply one atomic parent command; generic reversal
remains closed. No stock, lifecycle, reservation or custody changes happen here.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from sqlalchemy import select

from ..demand_models import (WorkOrderMaterialOperation, WorkOrderMaterialLine, WorkOrderReplacement, WorkOrderReplacementPair,
    WorkOrderRemovedSerialRegistration)
from ..inventory_models import (FormalMaterial, InventoryLot, InventoryMovement, InventoryMovementSerial,
    InventorySerial, InventoryTransaction, SerialCurrentPosition, StockAccount)
from ..work_order_reversal_schemas import (WorkOrderReversalPreviewIn, WorkOrderReversalPreviewOut,
    WorkOrderReversalChildOut, WorkOrderReversalMovementOut, WorkOrderReversalSerialOut, WorkOrderReversalPairOut)
from . import inventory_posting as posting, inventory_query as inventory, work_order_material as material
from .serial_ledger import rebuild_serial_states, SerialLedgerError
from .work_order_material_options import material_options
from .work_order_operation_read import verify_operation_history
from .work_order_replacement_read import replacement_result
from .work_order_removed_registration import verified_registration
from .work_order_reservations import require_work_order_reservations
from .work_order_preview import _policies
from .work_order_evidence_snapshot import material_audit_cursor as _audit_cursor


def _fail(code, message, category="precondition_failed"):
    raise material.WorkOrderMaterialPreflightError(code, message, category)


def _invalid():
    _fail("work_order_reversal_evidence_invalid", "原工单操作、SN 或反向明细证据不完整，请先核验", "service_unavailable")


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _originals(db, actor, work_order_id, request):
    pairs = ()
    if request.original_replacement_id is not None:
        parent = db.get(WorkOrderReplacement, request.original_replacement_id, populate_existing=True)
        if parent is None or parent.oam_work_order_id != work_order_id or parent.operator_person_id != actor.person_id:
            _fail("work_order_reversal_original_not_found", "未找到本人该工单的原记录", "not_found")
        replacement_result(db, replacement=parent, actor=actor)
        # Reverse physical recovery first, then restore the installed material.
        identifiers = (parent.recover_operation_id, parent.consume_operation_id)
        pairs = tuple(WorkOrderReversalPairOut(installed_serial_id=a, removed_serial_id=b) for a, b in db.execute(
            select(WorkOrderReplacementPair.installed_serial_id, WorkOrderReplacementPair.removed_serial_id)
            .where(WorkOrderReplacementPair.replacement_id == parent.id)
            .order_by(WorkOrderReplacementPair.installed_serial_id)))
    else:
        identifiers = (request.original_operation_id,)
    operations = []
    for identifier in identifiers:
        operation = db.get(WorkOrderMaterialOperation, identifier, populate_existing=True)
        if operation is None or operation.oam_work_order_id != work_order_id or operation.operator_person_id != actor.person_id:
            _fail("work_order_reversal_original_not_found", "未找到本人该工单的原记录", "not_found")
        if request.original_operation_id is not None and operation.replacement_id is not None:
            _fail("work_order_reversal_parent_required", "成对更换须选择整个原更换记录，不能单独冲销一个子操作")
        if operation.operation_type == "reverse":
            _fail("reversal_of_reversal_forbidden", "冲销记录不能再次作为原操作")
        if request.original_operation_id is not None:
            verify_operation_history(db, actor=actor, operation=operation)
        tx = db.get(InventoryTransaction, operation.posting_transaction_id, populate_existing=True)
        if tx is None or tx.actor_user_id != actor.user_id:
            _invalid()
        if db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.reversed_transaction_id == tx.id).limit(1)):
            _fail("original_transaction_already_reversed", "原操作已有反向流水，请核验原冲销记录")
        operations.append((operation, tx))
    return tuple(operations), pairs


def _children(db, *, actor, options, operations, checked_at):
    proposals = []
    quantities = {item.stock_account_id: Decimal(item.quantity) for item in options.items}
    for operation, tx in operations:
        movements = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == tx.id)
            .order_by(InventoryMovement.line_no)))
        if not 1 <= len(movements) <= 100:
            _invalid()
        rows = []
        for movement in movements:
            from_id, to_id = movement.to_account_id, movement.from_account_id
            accounts = {identifier: db.get(StockAccount, identifier, populate_existing=True)
                for identifier in (from_id, to_id) if identifier is not None}
            if not accounts or any(account is None or account.custodian_person_id != actor.person_id
                    or account.location_id != options.location_id for account in accounts.values()):
                _fail("work_order_reversal_custody_changed", "原库存账户不属于本人当前个人仓，需核验保管交接")
            posting._require_active_account_masters(db, accounts)
            posting._require_no_active_hard_freezes(db, accounts, effective_at=checked_at)
            account = accounts[from_id or to_id]
            sku = db.get(FormalMaterial, account.material_id, populate_existing=True)
            lot = db.get(InventoryLot, account.lot_id, populate_existing=True) if account.lot_id else None
            if sku is None or (account.lot_id and (lot is None or lot.material_id != sku.id)):
                _invalid()
            serial_ids = tuple(db.scalars(select(InventoryMovementSerial.serial_id)
                .where(InventoryMovementSerial.movement_id == movement.id).order_by(InventoryMovementSerial.serial_id)))
            policies, _ = _policies(db, {account.material_id}, checked_at)
            posting._validate_tracking_rules(posting.InventoryPostingCommand(transaction_no="reversal-plan", movement_type="reversal",
                source_document_type="work_order_material", source_document_id=str(operation.oam_work_order_id),
                posting_key="reversal-plan", effective_at=checked_at, movements=(posting.InventoryMovementCommand(
                    from_id, to_id, movement.quantity, serial_ids, movement.external_boundary_code),)), accounts, policies)
            if from_id is not None:
                if quantities.get(from_id, Decimal(0)) < movement.quantity:
                    _fail("work_order_reversal_stock_insufficient", "原操作反向来源库存不足，不能用其他账户或虚构入库补足")
                quantities[from_id] -= movement.quantity
            if to_id is not None:
                quantities[to_id] = quantities.get(to_id, Decimal(0)) + movement.quantity
            if operation.operation_type == "occupy":
                require_work_order_reservations(db, work_order_id=operation.oam_work_order_id,
                    lines=(material.WorkOrderMaterialLineInput(account.material_id, from_id, movement.quantity, serial_ids),))
            try:
                current = rebuild_serial_states(db, serial_ids, through_cursor=options.ledger_cursor)
                before = rebuild_serial_states(db, serial_ids, through_cursor=tx.ledger_cursor - 1)
            except SerialLedgerError:
                _invalid()
            serials = []
            for identifier in serial_ids:
                serial = db.get(InventorySerial, identifier, populate_existing=True)
                position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
                state, prior = current[identifier], before[identifier]
                if (serial is None or serial.lifecycle_status != state.lifecycle_status or position is None
                        or position.last_movement_id != state.last_movement_id or position.stock_account_id != state.stock_account_id):
                    _invalid()
                if state.last_movement_id != movement.id or state.stock_account_id != from_id:
                    _fail("work_order_reversal_serial_has_later_activity", "原 SN 已发生后续业务，不能直接撤回较早的操作")
                if prior.stock_account_id != to_id or prior.lifecycle_status not in {"active", "consumed"}:
                    _invalid()
                registration = db.scalar(select(WorkOrderRemovedSerialRegistration).where(
                    WorkOrderRemovedSerialRegistration.serial_id == identifier))
                if registration is not None:
                    # A fresh identity remains registered after its first inbound
                    # is compensated; it must never become a fictitious return.
                    if registration.operator_person_id != actor.person_id or registration.actor_user_id != actor.user_id:
                        _fail("work_order_reversal_registration_scope_unresolved", "原 SN 身份登记属于其他经手人，请核验交接证明")
                    verified_registration(db, actor=actor, row=registration)
                serials.append(WorkOrderReversalSerialOut(serial_id=identifier, serial_no=serial.serial_no,
                    lifecycle_before=state.lifecycle_status, lifecycle_after=prior.lifecycle_status,
                    previous_movement_id=prior.last_movement_id, previous_ledger_cursor=prior.ledger_cursor,
                    registration_id=registration.id if registration else None))
            delta = (-movement.quantity if operation.operation_type == "occupy" else
                movement.quantity if operation.operation_type in {"consume", "release"} else Decimal(0))
            rows.append(WorkOrderReversalMovementOut(original_movement_id=movement.id, line_no=movement.line_no,
                material_id=account.material_id, sku_code=sku.sku_code, material_name=sku.name, base_unit=sku.base_unit,
                condition_code=account.condition_code, lot_id=account.lot_id, lot_no=lot.lot_no if lot else None,
                from_account_id=from_id, to_account_id=to_id,
                from_bucket=accounts[from_id].availability_bucket if from_id else None,
                to_bucket=accounts[to_id].availability_bucket if to_id else None,
                quantity=format(movement.quantity, ".3f"), reservation_delta=format(delta, ".3f"), serials=tuple(serials)))
        proposals.append(WorkOrderReversalChildOut(original_operation_id=operation.id,
            original_operation_no=operation.operation_no, original_operation_type=operation.operation_type,
            original_transaction_id=tx.id, original_ledger_cursor=tx.ledger_cursor, movements=tuple(rows)))
    return tuple(proposals)


def _preview_with_document(db, *, actor, work_order_id, request: WorkOrderReversalPreviewIn):
    current = posting._require_current_actor(db, actor)
    if request.operator_person_id != current.person_id:
        _fail("operator_mismatch", "操作人必须是当前登录人员", "forbidden")
    material.authorize_work_order(db, actor=current, work_order_id=work_order_id, action="operate")
    with db.no_autoflush:
        options = material_options(db, actor=current, work_order_id=work_order_id)
        if not options.work_order.can_operate or options.opening_balance_status != "established":
            _fail("work_order_reversal_not_operable", "本人有效工单、新鲜来源和已建立个人仓均须通过核验")
        snapshot = inventory._ProjectionSnapshot(options.ledger_cursor, options.projected_at)
        audit_cursor = _audit_cursor(db)
        operations, pairs = _originals(db, current, work_order_id, request)
        checked_at = datetime.now(timezone.utc)
        material_ids = set(db.scalars(select(WorkOrderMaterialLine.material_id).where(
            WorkOrderMaterialLine.operation_id.in_([operation.id for operation, _tx in operations]))))
        policy_fingerprint = _policies(db, material_ids, checked_at)[1]
        children = _children(db, actor=current, options=options, operations=operations, checked_at=checked_at)
        inventory._ensure_projection_snapshot_current(db, snapshot)
        latest_operations, latest_pairs = _originals(db, current, work_order_id, request)
        latest_at = datetime.now(timezone.utc)
        if (material_options(db, actor=current, work_order_id=work_order_id) != options
                or _audit_cursor(db) != audit_cursor or latest_pairs != pairs
                or _children(db, actor=current, options=options, operations=latest_operations, checked_at=latest_at) != children
                or _policies(db, material_ids, latest_at)[1] != policy_fingerprint):
            _fail("work_order_reversal_plan_changed", "工单、库存或原始证据在预检期间变化，请重新核验", "conflict")
        inventory._ensure_projection_snapshot_current(db, snapshot)
        posting._require_current_actor(db, current)
    intent = {"work_order_id": str(work_order_id), **request.model_dump(mode="json")}
    request_hash = _hash(intent)
    document = {"intent": intent, "authorization_version": current.authorization_version,
        "work_order": options.work_order.model_dump(mode="json"), "ledger_cursor": options.ledger_cursor,
        "policies": policy_fingerprint, "children": [row.model_dump(mode="json") for row in children],
        "replacement_pairs": [row.model_dump(mode="json") for row in pairs]}
    # Freeze JSON-compatible values so persisted proof hashes round-trip exactly.
    document = json.loads(json.dumps(document, ensure_ascii=False))
    result = WorkOrderReversalPreviewOut(operator_person_id=current.person_id,
        authorization_version=current.authorization_version, work_order=options.work_order,
        ledger_cursor=options.ledger_cursor, checked_at=checked_at,
        original_operation_id=request.original_operation_id, original_replacement_id=request.original_replacement_id,
        reason=request.reason, request_hash=request_hash, plan_hash=_hash(document), children=children, replacement_pairs=pairs)
    return result, document


def preview_reversal(db, *, actor, work_order_id, request: WorkOrderReversalPreviewIn):
    return _preview_with_document(db, actor=actor, work_order_id=work_order_id, request=request)[0]
