"""Atomic new-part consumption and removed-part recovery for one work order."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import select

from ..demand_models import WorkOrderMaterialOperation, WorkOrderReplacement, WorkOrderReplacementPair
from ..foundation_models import OutboxEvent
from ..inventory_models import FormalMaterial, InventoryLot, InventorySerial, SerialCurrentPosition, StockAccount, StockLocation
from . import work_order_material as material
from . import inventory_posting as inventory
from .audit_chain import append_audit_event
from .serial_ledger import SerialLedgerError, rebuild_serial_states
from .work_order_replacement_proof import replacement_child_key as child_key


@dataclass(frozen=True)
class RecoveryLineInput:
    basis_stock_account_id: UUID
    material_id: UUID
    quantity: Decimal
    condition_before: str
    lot_id: UUID | None = None
    target_stock_account_id: UUID | None = None
    serial_ids: tuple[UUID, ...] = ()
    serial_verifications: tuple[material.SerialVerificationInput, ...] = ()


def _fail(code, message, category="conflict"):
    raise material.WorkOrderMaterialPreflightError(code, message, category)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def replacement_request_payload(*, work_order_id, operator_person_id, consume_lines, recover_lines, pairs):
    consumed = material.operation_request_payload(operation_type="consume", work_order_id=work_order_id,
        operator_person_id=operator_person_id, lines=consume_lines,
        replacement_pairs=tuple(sorted(pairs, key=lambda pair: str(pair.installed_serial_id))))
    if not recover_lines:
        _fail("replacement_recovery_required", "以换代修必须同时登记拆回旧坏件", "invalid_request")
    consumed_accounts = {line.stock_account_id for line in consume_lines}
    if {line.basis_stock_account_id for line in recover_lines} != consumed_accounts:
        _fail("replacement_lines_incomplete", "每条投入明细都必须明确关联拆回旧坏件", "invalid_request")
    installed = {identifier for line in consume_lines for identifier in line.serial_ids}
    removed = set()
    recovered = []
    for line in recover_lines:
        if line.basis_stock_account_id not in consumed_accounts:
            _fail("recovery_basis_invalid", "拆回件必须引用本次投料账户的保管位置", "invalid_request")
        if line.condition_before not in {"used", "damaged"}:
            _fail("recover_condition_invalid", "拆回件必须登记为旧件或坏件", "invalid_request")
        checked = material.WorkOrderMaterialLineInput(line.material_id, line.basis_stock_account_id,
            line.quantity, line.serial_ids, line.condition_before, line.serial_verifications)
        material.validate_batch((checked,))
        if removed.intersection(line.serial_ids) or installed.intersection(line.serial_ids):
            _fail("replacement_serial_duplicate", "投入和拆回 SN 必须各自唯一且不能相同", "invalid_request")
        removed.update(line.serial_ids)
        proofs = {proof.serial_id: proof for proof in line.serial_verifications}
        if len(proofs) != len(line.serial_verifications) or set(proofs) != set(line.serial_ids):
            _fail("serial_verification_missing", "每个拆回 SN 必须提供完整三码校验", "invalid_request")
        recovered.append({
            "basis_stock_account_id": str(line.basis_stock_account_id), "material_id": str(line.material_id),
            "lot_id": str(line.lot_id) if line.lot_id else None,
            "target_stock_account_id": str(line.target_stock_account_id) if line.target_stock_account_id else None,
            "quantity": format(line.quantity, ".3f"), "condition_before": line.condition_before,
            "serial_ids": sorted(str(value) for value in line.serial_ids),
            "serial_verifications": [{"serial_id": str(proof.serial_id), "sku_code": proof.sku_code,
                "serial_no": proof.serial_no, "qr_code": proof.qr_code}
                for proof in sorted(line.serial_verifications, key=lambda value: str(value.serial_id))],
        })
    if {pair.installed_serial_id for pair in pairs} != installed or {pair.removed_serial_id for pair in pairs} != removed:
        _fail("replacement_pairs_incomplete", "配对必须完整覆盖本次投入和拆回的全部 SN", "invalid_request")
    installed_bases = {identifier: line.stock_account_id for line in consume_lines for identifier in line.serial_ids}
    removed_bases = {identifier: line.basis_stock_account_id for line in recover_lines for identifier in line.serial_ids}
    if any(installed_bases[pair.installed_serial_id] != removed_bases[pair.removed_serial_id] for pair in pairs):
        _fail("replacement_pair_basis_mismatch", "新旧 SN 配对必须对应明确的原投料明细", "invalid_request")
    return {"work_order_id": str(work_order_id), "operator_person_id": str(operator_person_id),
            "consume_lines": consumed["lines"], "recover_lines": recovered,
            "replacement_pairs": consumed["replacement_pairs"]}


def recovery_target(db, *, operator_person_id, line, require_active):
    """Resolve exact target dimensions without inserting even an empty account."""
    basis = db.get(StockAccount, line.basis_stock_account_id, populate_existing=True)
    location = db.get(StockLocation, basis.location_id, populate_existing=True) if basis else None
    sku = db.get(FormalMaterial, line.material_id, populate_existing=True)
    if (basis is None or basis.custodian_person_id != operator_person_id
            or basis.availability_bucket != "reserved" or location is None
            or location.location_type != "personal" or location.custodian_person_id != operator_person_id
            or (require_active and location.status != "active") or sku is None or (require_active and sku.status != "active")):
        _fail("recovery_basis_invalid", "拆回件保管位置必须来自本人有效个人仓投料账户")
    lot = db.get(InventoryLot, line.lot_id, populate_existing=True) if line.lot_id else None
    if line.lot_id is not None and (lot is None or lot.material_id != line.material_id):
        _fail("recover_lot_invalid", "拆回批次与物料不一致")
    dimensions = dict(owner_org_id=basis.owner_org_id, custodian_person_id=operator_person_id,
        location_id=basis.location_id, material_id=line.material_id, lot_id=line.lot_id,
        condition_code=line.condition_before, availability_bucket="available")
    rows = tuple(db.scalars(select(StockAccount).filter_by(**dimensions).limit(2)))
    if len(rows) > 1:
        _fail("recover_target_missing", "原回收账户不存在或不唯一，请核验原记录")
    if line.target_stock_account_id is not None and (len(rows) != 1 or rows[0].id != line.target_stock_account_id):
        _fail("recover_target_invalid", "指定回收账户与本次物料、成色及保管维度不一致")
    return dimensions, rows[0] if rows else None


def resolve_recovery_lines(db, *, operator_person_id, lines, create):
    """Create only empty dimensions; the paired recovery admits them at commit."""
    resolved = []
    for line in lines:
        dimensions, account = recovery_target(db, operator_person_id=operator_person_id, line=line, require_active=create)
        if account is None and create:
            if db.get_bind().dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            now = datetime.now(timezone.utc)
            db.execute(insert(StockAccount).values(id=uuid4(), **dimensions,
                created_at=now, updated_at=now).on_conflict_do_nothing())
            _dimensions, account = recovery_target(db, operator_person_id=operator_person_id, line=line, require_active=True)
        if account is None:
            _fail("recover_target_missing", "原回收账户不存在或不唯一，请核验原记录")
        resolved.append(material.WorkOrderMaterialLineInput(line.material_id, account.id, line.quantity,
            line.serial_ids, line.condition_before, line.serial_verifications, account.id))
    material.validate_batch(tuple(resolved))
    return tuple(resolved)


def _preflight_removed_serials(db, lines, *, account_contexts=None):
    ids = tuple(identifier for line in lines for identifier in line.serial_ids)
    try:
        states = rebuild_serial_states(db, ids)
    except SerialLedgerError:
        _fail("removed_serial_history_invalid", "拆回 SN 历史流水不一致，请先核验", "service_unavailable")
    for line in lines:
        account = account_contexts[line.stock_account_id] if account_contexts is not None else db.get(StockAccount, line.stock_account_id)
        sku = db.get(FormalMaterial, line.material_id)
        proofs = {proof.serial_id: proof for proof in line.serial_verifications}
        for identifier in line.serial_ids:
            serial = db.get(InventorySerial, identifier, populate_existing=True)
            state = states[identifier]
            position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
            if (serial is None or serial.material_id != line.material_id or serial.lot_id != account.lot_id
                    or sku is None or serial.lifecycle_status != state.lifecycle_status
                    or state.lifecycle_status not in {"active", "consumed"}
                    or (state.owner_org_id is not None and state.owner_org_id != account.owner_org_id)
                    or state.stock_account_id is not None
                    or (state.last_movement_id is None and position is not None)
                    or (state.last_movement_id is not None and (position is None
                        or position.stock_account_id is not None or position.last_movement_id != state.last_movement_id))):
                _fail("removed_serial_unavailable", "拆回 SN 已在库存中，或缺少一致的历史位置证明")
            proof = proofs[identifier]
            if (proof.sku_code != sku.sku_code or proof.serial_no != serial.serial_no or proof.qr_code != serial.qr_code):
                _fail("serial_verification_mismatch", "拆回件二维码、SKU、SN 校验不一致")


def execute_replacement(db, *, actor, work_order_id, consume_lines, recover_lines, pairs, idempotency_key, request_id):
    """The caller commits only after both stock facts, pairs and audit exist."""
    inventory._lock_inventory_ledger_head_for_atomic_batch(db)
    order, current = material.authorize_work_order(db, actor=actor, work_order_id=work_order_id,
        action="operate", lock_rows=True)
    if not idempotency_key or len(idempotency_key) > 200 or idempotency_key.strip() != idempotency_key:
        _fail("idempotency_key_missing", "缺少有效的原请求键", "invalid_request")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", request_id):
        _fail("request_id_invalid", "请求标识无效", "invalid_request")
    command = replacement_request_payload(work_order_id=work_order_id, operator_person_id=current.person_id,
        consume_lines=consume_lines, recover_lines=recover_lines, pairs=pairs)
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    request_hash = _hash(command)
    existing = db.scalar(select(WorkOrderReplacement).where(WorkOrderReplacement.idempotency_key_hash == key_hash))
    if existing is not None:
        if existing.operator_person_id != current.person_id or existing.oam_work_order_id != work_order_id:
            _fail("replacement_not_found", "原替换记录不存在", "not_found")
        if existing.request_hash != request_hash or existing.command_jsonb != command:
            _fail("idempotency_conflict", "原请求键已绑定其他替换内容")
        from .work_order_replacement_read import replacement_result
        replacement_result(db, replacement=existing, actor=current)
        return existing
    if order.status != "active":
        _fail("work_order_inactive", "工单当前不可执行新物料操作")
    from .work_order_query import require_new_work_order_source
    require_new_work_order_source(db, actor=current, work_order_id=work_order_id, now=datetime.now(timezone.utc))
    if db.scalar(select(WorkOrderReplacement.id).where(WorkOrderReplacement.operator_person_id == current.person_id,
            WorkOrderReplacement.request_id == request_id)) is not None:
        _fail("request_id_conflict", "请求标识已绑定原替换，请先回读")
    material.require_work_order_reservations(db, work_order_id=work_order_id, lines=consume_lines)
    recovered_lines = resolve_recovery_lines(db, operator_person_id=current.person_id, lines=recover_lines, create=True)
    # Lock the union before either stock operation; the two ordinary commands
    # recheck their subsets under the same ledger lock and SQL transaction.
    graph_command = inventory.InventoryPostingCommand(transaction_no="replacement-graph", movement_type="consume",
        source_document_type="work_order_material", source_document_id=str(work_order_id),
        posting_key="replacement-graph", effective_at=datetime.now(timezone.utc),
        movements=tuple(inventory.InventoryMovementCommand(line.stock_account_id, None, line.quantity,
                line.serial_ids, "work_order_material_consume") for line in consume_lines)
            + tuple(inventory.InventoryMovementCommand(None, line.stock_account_id, line.quantity,
                line.serial_ids, "work_order_material_recover") for line in recovered_lines))
    inventory._plan_and_lock_terminal_opening_graphs(db, command=graph_command, current_actor_user_id=current.user_id)
    _preflight_removed_serials(db, recovered_lines)
    replacement = WorkOrderReplacement(id=uuid4(), replacement_no="WOR-" + key_hash[:24].upper(),
        oam_work_order_id=work_order_id, operator_person_id=current.person_id,
        consume_operation_id=uuid4(), recover_operation_id=uuid4(), idempotency_key_hash=key_hash,
        request_id=request_id, request_hash=request_hash, command_jsonb=command)
    db.add(replacement); db.flush()
    consumed, _ = material.execute_consume_operation(db, actor=current, work_order_id=work_order_id,
        lines=consume_lines, idempotency_key=child_key(key_hash, "consume"), request_id=request_id,
        _replacement_id=replacement.id)
    db.add_all(WorkOrderReplacementPair(operation_id=consumed.id, replacement_id=replacement.id,
        installed_serial_id=pair.installed_serial_id, removed_serial_id=pair.removed_serial_id) for pair in pairs)
    db.flush()
    material.execute_recover_operation(db, actor=current, work_order_id=work_order_id,
        lines=recovered_lines, idempotency_key=child_key(key_hash, "recover"), request_id=request_id,
        _replacement_id=replacement.id)
    now = datetime.now(timezone.utc)
    append_audit_event(db, stream_key="material_request", actor_user_id=current.user_id,
        action="work_order_material.replace", aggregate_type="work_order_material_replacement",
        aggregate_id=str(replacement.id), before_jsonb={},
        after_jsonb={"work_order_id": str(work_order_id), "consume_operation_id": str(replacement.consume_operation_id),
            "recover_operation_id": str(replacement.recover_operation_id), "request_hash": request_hash, "command": command},
        request_id=request_id, occurred_at=now, created_at=now)
    db.add(OutboxEvent(event_type="work_order_material_replacement_posted", aggregate_type="work_order_material_replacement",
        aggregate_id=str(replacement.id), payload_jsonb={"work_order_id": str(work_order_id),
            "consume_operation_id": str(replacement.consume_operation_id), "recover_operation_id": str(replacement.recover_operation_id)},
        idempotency_key=f"work-order-material-replacement:{replacement.id}", available_at=now))
    db.flush()
    return replacement
