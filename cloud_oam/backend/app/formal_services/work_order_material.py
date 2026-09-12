"""Formal work-order validation and exact historical posting evidence.

The evidence recorder does not perform a stock movement. Atomic inventory
commands must use the unified posting service in the same caller transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..demand_models import (
    OamWorkOrder, WorkOrderMaterialLine, WorkOrderMaterialOperation,
    WorkOrderMaterialSerial, WorkOrderReplacement,
)
from ..foundation_models import OutboxEvent
from ..inventory_models import (
    FormalMaterial, InventorySerial, InventoryTransaction, SerialCurrentPosition,
    StockAccount, StockLocation, InventoryMovement, InventoryMovementSerial,
)
from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from .inventory_posting import (
    InventoryMovementCommand, InventoryPostingCommand, InventoryPostingError,
    _require_current_actor, post_inventory_transaction,
    _lock_inventory_ledger_head_for_atomic_batch,
)
from .postgresql_lock_graph import lock_material_request_work_order
from .audit_chain import append_audit_event
from .work_order_reservations import require_work_order_reservations
from .work_order_accounts import resolve_work_order_reserved_lines


class WorkOrderMaterialPreflightError(InventoryPostingError):
    def __init__(self, code: str, message: str, category: str = "conflict"):
        super().__init__(code, category, message)


def validate_serial_quantity(quantity: Decimal, serial_ids: tuple[UUID, ...]) -> None:
    """Serial-tracked lines must carry one SN for each physical unit."""
    if serial_ids and quantity != Decimal(len(serial_ids)):
        raise WorkOrderMaterialPreflightError(
            "serial_quantity_mismatch", "SN 数量必须与物料数量一致"
        )


def expected_posting_movement_type(operation_type: str) -> str:
    value = {
        "occupy": "reserve", "release": "release", "consume": "consume",
        "recover": "inbound", "reverse": "reversal",
    }.get(operation_type)
    if value is None:
        raise WorkOrderMaterialPreflightError("operation_type_invalid", "工单物料操作类型不合法")
    return value


@dataclass(frozen=True, slots=True)
class SerialVerificationInput:
    serial_id: UUID
    sku_code: str
    serial_no: str
    qr_code: str


@dataclass(frozen=True, slots=True)
class WorkOrderMaterialLineInput:
    material_id: UUID
    stock_account_id: UUID
    quantity: Decimal
    serial_ids: tuple[UUID, ...] = ()
    condition_before: str = "new"
    serial_verifications: tuple[SerialVerificationInput, ...] = ()
    target_stock_account_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class WorkOrderMaterialPreflight:
    work_order_id: UUID
    operator_person_id: UUID
    lines: tuple[WorkOrderMaterialLineInput, ...]


@dataclass(frozen=True, slots=True)
class WorkOrderReplacementPairInput:
    installed_serial_id: UUID
    removed_serial_id: UUID


def operation_request_payload(*, operation_type: str, work_order_id: UUID,
                           operator_person_id: UUID,
                           lines: tuple[WorkOrderMaterialLineInput, ...],
                           replacement_pairs: tuple[WorkOrderReplacementPairInput, ...] = ()) -> dict:
    """Return the exact command stored with the append-only operation fact."""
    if operation_type not in {"occupy", "release", "consume", "recover", "reverse"}:
        raise WorkOrderMaterialPreflightError("operation_type_invalid", "工单物料操作类型不合法")
    validate_batch(lines)
    seen_installed: set[UUID] = set()
    seen_removed: set[UUID] = set()
    for pair in replacement_pairs:
        if pair.installed_serial_id == pair.removed_serial_id:
            raise WorkOrderMaterialPreflightError("replacement_pair_invalid", "新旧 SN 不能相同")
        if pair.installed_serial_id in seen_installed or pair.removed_serial_id in seen_removed:
            raise WorkOrderMaterialPreflightError("replacement_pair_duplicate", "新旧 SN 不得重复配对")
        seen_installed.add(pair.installed_serial_id)
        seen_removed.add(pair.removed_serial_id)
    payload = {
        "operation_type": operation_type,
        "work_order_id": str(work_order_id),
        "operator_person_id": str(operator_person_id),
        "lines": [
            {"material_id": str(row.material_id), "stock_account_id": str(row.stock_account_id),
             "quantity": format(row.quantity, ".3f"), "condition_before": row.condition_before,
             "serial_ids": sorted(str(value) for value in row.serial_ids),
             "serial_verifications": [
                 {"serial_id": str(v.serial_id), "sku_code": v.sku_code,
                  "serial_no": v.serial_no, "qr_code": v.qr_code}
                 for v in sorted(row.serial_verifications, key=lambda v: str(v.serial_id))],
             "target_stock_account_id": (str(row.target_stock_account_id)
                                          if row.target_stock_account_id else None)}
            for row in lines
        ],
        "replacement_pairs": [
            {"installed_serial_id": str(row.installed_serial_id),
             "removed_serial_id": str(row.removed_serial_id)}
            for row in replacement_pairs
        ],
    }
    return payload


def operation_request_hash(**kwargs) -> str:
    return hashlib.sha256(json.dumps(operation_request_payload(**kwargs), ensure_ascii=False,
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_batch(lines: tuple[WorkOrderMaterialLineInput, ...]) -> None:
    if not lines:
        raise WorkOrderMaterialPreflightError("empty_batch", "物料操作至少需要一条明细")
    seen_accounts: set[UUID] = set()
    seen_serials: set[UUID] = set()
    for line in lines:
        if (not isinstance(line.quantity, Decimal) or not line.quantity.is_finite()
                or line.quantity <= 0 or line.quantity >= Decimal("1000000000000000")
                or line.quantity != line.quantity.quantize(Decimal("0.001"))):
            raise WorkOrderMaterialPreflightError("quantity_invalid", "物料数量必须大于零")
        if line.condition_before not in {"new", "used", "damaged", "scrapped"}:
            raise WorkOrderMaterialPreflightError("condition_invalid", "物料状态不合法")
        validate_serial_quantity(line.quantity, line.serial_ids)
        if line.stock_account_id in seen_accounts:
            raise WorkOrderMaterialPreflightError("duplicate_stock_account", "同一来源库存账户请合并数量后提交")
        seen_accounts.add(line.stock_account_id)
        for serial_id in line.serial_ids:
            if serial_id in seen_serials:
                raise WorkOrderMaterialPreflightError("duplicate_serial", "同一序列号不得重复提交")
            seen_serials.add(serial_id)


def preflight_work_order_material_batch(
    db: Session,
    *,
    work_order_id: UUID,
    operator_person_id: UUID,
    lines: tuple[WorkOrderMaterialLineInput, ...],
) -> WorkOrderMaterialPreflight:
    """Verify exact work-order/operator/account coordinates without writes."""
    validate_batch(lines)
    order = db.scalar(select(OamWorkOrder).where(OamWorkOrder.id == work_order_id))
    if order is None:
        raise WorkOrderMaterialPreflightError("work_order_not_found", "工单不存在")
    if order.status != "active":
        raise WorkOrderMaterialPreflightError("work_order_inactive", "工单当前不可执行物料操作")
    if order.engineer_person_id != operator_person_id:
        raise WorkOrderMaterialPreflightError("operator_mismatch", "操作人不是工单工程师")
    for line in lines:
        material = db.get(FormalMaterial, line.material_id)
        if material is None or material.status != "active":
            raise WorkOrderMaterialPreflightError("material_invalid", "物料不存在或已停用")
        account = db.get(StockAccount, line.stock_account_id)
        if account is None or account.material_id != line.material_id:
            raise WorkOrderMaterialPreflightError("stock_account_material_mismatch", "库存账户与物料不匹配")
        if account.custodian_person_id != operator_person_id:
            raise WorkOrderMaterialPreflightError("stock_account_custodian_mismatch", "库存账户不属于当前操作人")
        if account.availability_bucket != "available":
            raise WorkOrderMaterialPreflightError("stock_account_unavailable", "库存账户当前不可用于工单操作")
        if account.condition_code != line.condition_before:
            raise WorkOrderMaterialPreflightError("condition_mismatch", "物料状态与库存账户不一致")
        for serial_id in line.serial_ids:
            serial = db.get(InventorySerial, serial_id)
            if serial is None or serial.material_id != line.material_id:
                raise WorkOrderMaterialPreflightError("serial_material_mismatch", "SN 与物料不匹配")
            if serial.lifecycle_status != "active":
                raise WorkOrderMaterialPreflightError("serial_not_active", "SN 当前不可操作")
            position = db.get(SerialCurrentPosition, serial_id)
            if position is None or position.stock_account_id != line.stock_account_id:
                raise WorkOrderMaterialPreflightError("serial_account_mismatch", "SN 不在指定库存账户")
    return WorkOrderMaterialPreflight(work_order_id, operator_person_id, lines)


def authorize_work_order(db, *, actor, work_order_id, action, lock_rows=False):
    if lock_rows:
        lock_formal_principal_graph(db, (actor.user_id,))
    current = _require_current_actor(db, actor)
    if lock_rows:
        lock_material_request_work_order(db, work_order_id)
    order = db.get(OamWorkOrder, work_order_id, populate_existing=True)
    if order is None:
        raise WorkOrderMaterialPreflightError("work_order_not_found", "工单不存在", "not_found")
    scope_type, scope_id = (("person", str(order.engineer_person_id))
                            if order.engineer_person_id else ("organization", str(order.organization_id)))
    if not current.allows(db, "work_order_material", action,
                          target_scope_type=scope_type, target_scope_id=scope_id):
        raise WorkOrderMaterialPreflightError("work_order_forbidden", "没有该工单的数据范围权限", "forbidden")
    if action == "operate" and order.engineer_person_id != current.person_id:
        raise WorkOrderMaterialPreflightError("operator_mismatch", "操作人不是工单工程师", "forbidden")
    return order, current


def _verify_posted_lines(db, *, transaction, operation_type, operator_person_id, lines):
    movements = tuple(db.scalars(select(InventoryMovement).where(
        InventoryMovement.transaction_id == transaction.id).order_by(InventoryMovement.line_no)).all())
    if len(movements) != len(lines):
        raise WorkOrderMaterialPreflightError("posting_lines_mismatch", "库存流水明细与工单操作不一致")
    remaining = {line.stock_account_id: line for line in lines}
    for movement in movements:
        account_id = movement.to_account_id if operation_type == "recover" else movement.from_account_id
        line = remaining.pop(account_id, None)
        if line is None or movement.quantity != line.quantity:
            raise WorkOrderMaterialPreflightError("posting_lines_mismatch", "库存账户或数量与原流水不一致")
        if operation_type == "recover" and line.condition_before not in {"used", "damaged"}:
            raise WorkOrderMaterialPreflightError("recover_condition_invalid", "拆回件必须登记为旧件或坏件", "invalid_request")
        account = db.get(StockAccount, account_id, populate_existing=True)
        location = db.get(StockLocation, account.location_id, populate_existing=True) if account else None
        if (account is None or account.material_id != line.material_id
                or account.custodian_person_id != operator_person_id
                or account.availability_bucket != {"occupy": "available", "consume": "reserved", "release": "reserved", "recover": "available"}.get(operation_type, account.availability_bucket)
                or account.condition_code != line.condition_before
                or location is None or location.location_type != "personal"
                or location.custodian_person_id != operator_person_id):
            raise WorkOrderMaterialPreflightError("posting_account_mismatch", "工单物料必须绑定本人个人仓库存账户")
        serial_ids = set(db.scalars(select(InventoryMovementSerial.serial_id).where(
            InventoryMovementSerial.movement_id == movement.id)).all())
        if serial_ids != set(line.serial_ids):
            raise WorkOrderMaterialPreflightError("posting_serials_mismatch", "SN 与原库存流水不一致")
        verify_serial_proofs(db, line=line)


def verify_serial_proofs(db, *, line):
    """Compare supplied physical codes with the exact formal serial master.

    Shared by read-only preview and immutable posting proof; current-position
    checks remain in the respective snapshot/posting service.
    """
    proofs = {v.serial_id: v for v in line.serial_verifications}
    if set(proofs) != set(line.serial_ids) or len(proofs) != len(line.serial_verifications):
        raise WorkOrderMaterialPreflightError("serial_verification_missing", "每个受控 SN 必须提供完整三码校验")
    material = db.get(FormalMaterial, line.material_id, populate_existing=True)
    for serial_id in line.serial_ids:
        serial = db.get(InventorySerial, serial_id, populate_existing=True)
        proof = proofs[serial_id]
        if (serial is None or material is None or serial.material_id != material.id
                or proof.sku_code != material.sku_code or proof.serial_no != serial.serial_no
                or proof.qr_code != serial.qr_code):
            raise WorkOrderMaterialPreflightError("serial_verification_mismatch", "二维码、SKU、SN 校验不一致")


def record_posted_operation(
    db: Session,
    *,
    actor: FormalPrincipal,
    operation_type: str,
    work_order_id: UUID,
    operator_person_id: UUID,
    lines: tuple[WorkOrderMaterialLineInput, ...],
    posting_transaction_id: UUID,
    idempotency_key: str,
    replacement_pairs: tuple[WorkOrderReplacementPairInput, ...] = (),
    replacement_id: UUID | None = None,
) -> WorkOrderMaterialOperation:
    """Append a work-order fact only after the inventory transaction exists.

    This is a historical evidence recorder, not an inventory command. A future
    atomic command must call it before committing the unified stock transaction.
    """
    _lock_inventory_ledger_head_for_atomic_batch(db)
    order, current = authorize_work_order(db, actor=actor, work_order_id=work_order_id,
                                        action="operate", lock_rows=True)
    if current.person_id != operator_person_id:
        raise WorkOrderMaterialPreflightError("operator_mismatch", "操作人必须是当前人员", "forbidden")
    validate_batch(lines)
    if not idempotency_key.strip():
        raise WorkOrderMaterialPreflightError("idempotency_key_missing", "缺少幂等键")
    transaction = db.get(InventoryTransaction, posting_transaction_id)
    if transaction is None or transaction.status != "posted":
        raise WorkOrderMaterialPreflightError("posting_transaction_missing", "库存事务尚未成功过账")
    if (transaction.source_document_type != "work_order_material"
            or transaction.source_document_id != str(work_order_id)
            or transaction.actor_user_id != current.user_id):
        raise WorkOrderMaterialPreflightError("posting_origin_mismatch", "库存事务未绑定该工单及操作人")
    # Historical evidence, not the SN's later mutable position, proves a posted operation.
    _verify_posted_lines(db, transaction=transaction, operation_type=operation_type,
                         operator_person_id=operator_person_id, lines=lines)
    expected_movement_type = expected_posting_movement_type(operation_type)
    if transaction.movement_type != expected_movement_type:
        raise WorkOrderMaterialPreflightError(
            "posting_type_mismatch", "库存事务类型与工单操作类型不匹配"
        )
    request_hash = operation_request_hash(
        operation_type=operation_type, work_order_id=work_order_id,
        operator_person_id=operator_person_id, lines=lines,
        replacement_pairs=replacement_pairs,
    )
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    replacement_operation_id = None
    if replacement_id is not None:
        from .work_order_replacements import child_key
        replacement = db.get(WorkOrderReplacement, replacement_id)
        if (replacement is None or operation_type not in {"consume", "recover"}
                or replacement.oam_work_order_id != work_order_id
                or replacement.operator_person_id != operator_person_id
                or child_key(replacement.idempotency_key_hash, operation_type) != idempotency_key):
            raise WorkOrderMaterialPreflightError("replacement_binding_invalid", "替换操作未绑定原命令")
        replacement_operation_id = (replacement.consume_operation_id if operation_type == "consume"
                                    else replacement.recover_operation_id)
    existing = db.scalar(select(WorkOrderMaterialOperation).where(
        WorkOrderMaterialOperation.idempotency_key_hash == key_hash
    ))
    if existing is not None:
        if (existing.request_hash != request_hash or existing.posting_transaction_id != posting_transaction_id
                or existing.replacement_id != replacement_id):
            raise WorkOrderMaterialPreflightError("idempotency_conflict", "幂等键已绑定其他工单操作")
        return existing
    bound = db.scalar(select(WorkOrderMaterialOperation.id).where(
        WorkOrderMaterialOperation.posting_transaction_id == posting_transaction_id))
    if bound is not None:
        raise WorkOrderMaterialPreflightError("posting_already_bound", "库存事务已绑定工单操作，请使用原幂等键回读")
    if order.status != "active":
        raise WorkOrderMaterialPreflightError("work_order_inactive", "工单当前不可执行新物料操作")
    if replacement_pairs:
        # Pairing needs both a consume fact and a return fact; arbitrary UUIDs
        # cannot establish that evidence. The atomic replacement command is pending.
        raise WorkOrderMaterialPreflightError("replacement_evidence_required", "配对需由新件消耗和拆回件入账的关联命令建立")
    if operation_type in {"consume", "release"}:
        require_work_order_reservations(db, work_order_id=work_order_id, lines=lines,
                                        before_cursor=transaction.ledger_cursor)
    operation = WorkOrderMaterialOperation(
        **({"id": replacement_operation_id} if replacement_id is not None else {}),
        replacement_id=replacement_id,
        operation_no=f"WOM-{UUID(int=work_order_id.int).hex[:12].upper()}-{key_hash[:12].upper()}",
        oam_work_order_id=work_order_id, operator_person_id=operator_person_id,
        operation_type=operation_type, status="posted",
        posting_transaction_id=posting_transaction_id,
        idempotency_key_hash=key_hash, request_hash=request_hash,
    )
    db.add(operation)
    db.flush()
    for index, value in enumerate(lines, 1):
        line = WorkOrderMaterialLine(
            operation_id=operation.id, line_no=index, material_id=value.material_id,
            stock_account_id=value.stock_account_id, quantity=value.quantity,
            condition_before=value.condition_before, condition_after=None,
        )
        db.add(line)
        db.flush()
        for serial_id in value.serial_ids:
            db.add(WorkOrderMaterialSerial(
                operation_line_id=line.id, serial_id=serial_id,
                sku_verified=True, qr_verified=True,
            ))
    occurred_at = datetime.now(timezone.utc)
    append_audit_event(
        db, stream_key="material_request", actor_user_id=current.user_id,
        action=f"work_order_material.{operation_type}", aggregate_type="work_order_material_operation",
        aggregate_id=str(operation.id), before_jsonb={},
        after_jsonb={"work_order_id": str(work_order_id), "operation_type": operation_type,
                     "posting_transaction_id": str(posting_transaction_id), "line_count": len(lines),
                     "request_hash": request_hash, "command": operation_request_payload(
                         operation_type=operation_type, work_order_id=work_order_id,
                         operator_person_id=operator_person_id, lines=lines, replacement_pairs=replacement_pairs)},
        request_id=f"work-order-material:{key_hash}", occurred_at=occurred_at, created_at=occurred_at,
    )
    db.add(OutboxEvent(
        event_type="work_order_material_operation_posted", aggregate_type="work_order_material_operation",
        aggregate_id=str(operation.id), payload_jsonb={"work_order_id": str(work_order_id),
            "operation_type": operation_type, "operation_no": operation.operation_no,
            "posting_transaction_id": str(posting_transaction_id)}, status="pending", attempts=0,
        idempotency_key=f"work-order-material-operation:{operation.id}", available_at=occurred_at,
    ))
    return operation


def _command_effective_at(db: Session, *, order: OamWorkOrder, actor: FormalPrincipal, posting_key: str) -> datetime:
    """Reuse only the original server timestamp; the ledger rechecks the request.

    The caller already owns the work-order lock. The unified posting service
    still hashes the current actor and movements and checks scopes on replay.
    Reusing an entire stored command here would hide changed caller input.
    """
    effective_at = db.scalar(select(InventoryTransaction.effective_at).where(
        InventoryTransaction.posting_key == posting_key))
    if effective_at is not None:
        # SQLite drops tzinfo on read; production timestamptz retains it.
        return effective_at if effective_at.tzinfo else effective_at.replace(tzinfo=timezone.utc)
    if order.status != "active":
        raise WorkOrderMaterialPreflightError("work_order_inactive", "工单当前不可执行新物料操作", "precondition_failed")
    from .work_order_query import require_new_work_order_source
    now = datetime.now(timezone.utc)
    require_new_work_order_source(db, actor=actor, work_order_id=order.id, now=now)
    return now


def execute_consume_operation(
    db: Session,
    *,
    actor: FormalPrincipal,
    work_order_id: UUID,
    lines: tuple[WorkOrderMaterialLineInput, ...],
    idempotency_key: str,
    request_id: str,
    _replacement_id: UUID | None = None,
) -> tuple[WorkOrderMaterialOperation, object]:
    """Atomically post a work-order consume movement and its fact.

    No commit occurs here. The router commits both the inventory transaction
    and operation fact together, or rolls both back on any failure.
    """
    _lock_inventory_ledger_head_for_atomic_batch(db)
    order, current = authorize_work_order(db, actor=actor, work_order_id=work_order_id,
                                          action="operate", lock_rows=True)
    # Do not reject an idempotent replay merely because the immutable OAM
    # projection has since closed. ``record_posted_operation`` rejects a new
    # fact after checking the existing key, while the unified posting service
    # similarly returns the original transaction on replay.
    validate_batch(lines)
    if not idempotency_key.strip():
        raise WorkOrderMaterialPreflightError("idempotency_key_missing", "缺少幂等键", "precondition_failed")
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    posting_key = f"work-order-material:consume:{work_order_id}:{key_hash}"
    original_cursor = db.scalar(select(InventoryTransaction.ledger_cursor).where(
        InventoryTransaction.posting_key == posting_key))
    require_work_order_reservations(db, work_order_id=work_order_id, lines=lines,
                                    before_cursor=original_cursor)
    command = InventoryPostingCommand(
        transaction_no=f"INV-WO-CONSUME-{key_hash[:20].upper()}",
        movement_type="consume", source_document_type="work_order_material",
        source_document_id=str(work_order_id),
        posting_key=posting_key,
        effective_at=_command_effective_at(db, order=order, actor=current, posting_key=posting_key),
        movements=tuple(InventoryMovementCommand(
            from_account_id=line.stock_account_id, to_account_id=None,
            quantity=line.quantity, serial_ids=line.serial_ids,
            external_boundary_code="work_order_material_consume",
        ) for line in lines),
    )
    posted = post_inventory_transaction(
        db, actor=current, command=command,
        idempotency_key=f"work-order-material:consume:{idempotency_key}",
        request_id=request_id,
        permission_action="operate", permission_resource="work_order_material",
    )
    operation = record_posted_operation(
        db, actor=current, operation_type="consume", work_order_id=work_order_id,
        operator_person_id=current.person_id, lines=lines,
        posting_transaction_id=posted.transaction_id,
        idempotency_key=idempotency_key,
        **({"replacement_id": _replacement_id} if _replacement_id is not None else {}),
    )
    return operation, posted


def execute_occupy_operation(
    db: Session, *, actor: FormalPrincipal, work_order_id: UUID,
    lines: tuple[WorkOrderMaterialLineInput, ...], idempotency_key: str,
    request_id: str,
) -> tuple[WorkOrderMaterialOperation, object]:
    """Move exact personal-available stock into reserved stock."""
    _lock_inventory_ledger_head_for_atomic_batch(db)
    order, current = authorize_work_order(db, actor=actor, work_order_id=work_order_id,
                                          action="operate", lock_rows=True)
    validate_batch(lines)
    if not idempotency_key.strip():
        raise WorkOrderMaterialPreflightError("idempotency_key_missing", "缺少幂等键", "precondition_failed")
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    posting_key = f"work-order-material:occupy:{work_order_id}:{key_hash}"
    effective_at = _command_effective_at(db, order=order, actor=current, posting_key=posting_key)
    replay = db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.posting_key == posting_key)) is not None
    lines = resolve_work_order_reserved_lines(db, operator_person_id=current.person_id, lines=lines, create=not replay)
    command = InventoryPostingCommand(
        transaction_no=f"INV-WO-OCCUPY-{key_hash[:20].upper()}", movement_type="reserve",
        source_document_type="work_order_material", source_document_id=str(work_order_id),
        posting_key=posting_key,
        effective_at=effective_at,
        movements=tuple(InventoryMovementCommand(
            from_account_id=line.stock_account_id, to_account_id=line.target_stock_account_id,
            quantity=line.quantity, serial_ids=line.serial_ids,
        ) for line in lines),
    )
    posted = post_inventory_transaction(db, actor=current, command=command,
        idempotency_key=f"work-order-material:occupy:{idempotency_key}", request_id=request_id,
        permission_action="operate", permission_resource="work_order_material")
    operation = record_posted_operation(
        db, actor=current, operation_type="occupy", work_order_id=work_order_id,
        operator_person_id=current.person_id, lines=lines,
        posting_transaction_id=posted.transaction_id, idempotency_key=idempotency_key)
    return operation, posted


def execute_release_operation(
    db: Session, *, actor: FormalPrincipal, work_order_id: UUID,
    lines: tuple[WorkOrderMaterialLineInput, ...], idempotency_key: str,
    request_id: str,
) -> tuple[WorkOrderMaterialOperation, object]:
    """Release occupied personal stock back to the exact available account."""
    _lock_inventory_ledger_head_for_atomic_batch(db)
    order, current = authorize_work_order(db, actor=actor, work_order_id=work_order_id,
                                          action="operate", lock_rows=True)
    validate_batch(lines)
    if not idempotency_key.strip():
        raise WorkOrderMaterialPreflightError("idempotency_key_missing", "缺少幂等键", "precondition_failed")
    for line in lines:
        if line.target_stock_account_id is None:
            raise WorkOrderMaterialPreflightError("release_target_missing", "释放必须指定可用目标账户", "invalid_request")
        target = db.get(StockAccount, line.target_stock_account_id, populate_existing=True)
        location = db.get(StockLocation, target.location_id, populate_existing=True) if target else None
        if (target is None or target.material_id != line.material_id
                or target.custodian_person_id != current.person_id
                or target.availability_bucket != "available"
                or location is None or location.location_type != "personal"
                or location.custodian_person_id != current.person_id):
            raise WorkOrderMaterialPreflightError("release_target_invalid", "释放目标不是当前人员的可用个人仓账户", "conflict")
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    posting_key = f"work-order-material:release:{work_order_id}:{key_hash}"
    original_cursor = db.scalar(select(InventoryTransaction.ledger_cursor).where(
        InventoryTransaction.posting_key == posting_key))
    require_work_order_reservations(db, work_order_id=work_order_id, lines=lines,
                                    before_cursor=original_cursor)
    command = InventoryPostingCommand(
        transaction_no=f"INV-WO-RELEASE-{key_hash[:20].upper()}",
        movement_type="release", source_document_type="work_order_material",
        source_document_id=str(work_order_id),
        posting_key=posting_key,
        effective_at=_command_effective_at(db, order=order, actor=current, posting_key=posting_key),
        movements=tuple(InventoryMovementCommand(
            from_account_id=line.stock_account_id, to_account_id=line.target_stock_account_id,
            quantity=line.quantity, serial_ids=line.serial_ids,
        ) for line in lines),
    )
    posted = post_inventory_transaction(
        db, actor=current, command=command,
        idempotency_key=f"work-order-material:release:{idempotency_key}", request_id=request_id,
        permission_action="operate", permission_resource="work_order_material",
    )
    operation = record_posted_operation(
        db, actor=current, operation_type="release", work_order_id=work_order_id,
        operator_person_id=current.person_id, lines=lines,
        posting_transaction_id=posted.transaction_id, idempotency_key=idempotency_key,
    )
    return operation, posted


def execute_recover_operation(
    db: Session, *, actor: FormalPrincipal, work_order_id: UUID,
    lines: tuple[WorkOrderMaterialLineInput, ...], idempotency_key: str,
    request_id: str,
    _replacement_id: UUID | None = None,
) -> tuple[WorkOrderMaterialOperation, object]:
    """Receive returned material into the operator's available personal account."""
    _lock_inventory_ledger_head_for_atomic_batch(db)
    order, current = authorize_work_order(db, actor=actor, work_order_id=work_order_id,
                                          action="operate", lock_rows=True)
    validate_batch(lines)
    if not idempotency_key.strip():
        raise WorkOrderMaterialPreflightError("idempotency_key_missing", "缺少幂等键", "precondition_failed")
    for line in lines:
        if line.target_stock_account_id is None:
            raise WorkOrderMaterialPreflightError("recover_target_missing", "回收必须指定可用目标账户", "invalid_request")
        if line.condition_before not in {"used", "damaged"}:
            raise WorkOrderMaterialPreflightError("recover_condition_invalid", "拆回件必须登记为旧件或坏件", "invalid_request")
        target = db.get(StockAccount, line.target_stock_account_id, populate_existing=True)
        location = db.get(StockLocation, target.location_id, populate_existing=True) if target else None
        if (target is None or target.material_id != line.material_id
                or target.custodian_person_id != current.person_id
                or target.availability_bucket != "available"
                or target.condition_code != line.condition_before
                or location is None or location.location_type != "personal"
                or location.status != "active"
                or location.custodian_person_id != current.person_id):
            raise WorkOrderMaterialPreflightError("recover_target_invalid", "回收目标不是当前人员的可用个人仓账户", "conflict")
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    posting_key = f"work-order-material:recover:{work_order_id}:{key_hash}"
    command = InventoryPostingCommand(
        transaction_no=f"INV-WO-RECOVER-{key_hash[:20].upper()}", movement_type="inbound",
        source_document_type="work_order_material", source_document_id=str(work_order_id),
        posting_key=posting_key,
        effective_at=_command_effective_at(db, order=order, actor=current, posting_key=posting_key),
        movements=tuple(InventoryMovementCommand(
            from_account_id=None, to_account_id=line.target_stock_account_id,
            quantity=line.quantity, serial_ids=line.serial_ids,
            external_boundary_code="work_order_material_recover",
        ) for line in lines),
    )
    posted = post_inventory_transaction(
        db, actor=current, command=command,
        idempotency_key=f"work-order-material:recover:{idempotency_key}", request_id=request_id,
        permission_action="operate", permission_resource="work_order_material",
    )
    # Historical recover facts bind their stock account to the inventory
    # movement's destination. Keep the transport target in the command input,
    # then record the canonical destination account in the immutable fact.
    recorded_lines = tuple(replace(line, stock_account_id=line.target_stock_account_id,
                                    target_stock_account_id=None) for line in lines)
    operation = record_posted_operation(
        db, actor=current, operation_type="recover", work_order_id=work_order_id,
        operator_person_id=current.person_id, lines=recorded_lines,
        posting_transaction_id=posted.transaction_id, idempotency_key=idempotency_key,
        **({"replacement_id": _replacement_id} if _replacement_id is not None else {}),
    )
    return operation, posted
