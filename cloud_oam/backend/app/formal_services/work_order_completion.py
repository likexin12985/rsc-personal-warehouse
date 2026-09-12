"""Read exact material obligations before finishing a work order.

Stock, identity admission and return custody are independent. A moved/consumed
returned part is not a return-document receipt. Until the formal return domain
produces an immutable source-line release, every recovery remains pending return.
Unpaired serial consumption needs an explicit recovery assessment; absence of a
replacement record cannot establish that no removed part was required.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from ..demand_models import WorkOrderMaterialLine, WorkOrderMaterialOperation, WorkOrderMaterialSerial
from ..demand_models import WorkOrderReplacement, WorkOrderRemovedSerialRegistration as Registration
from ..foundation_models import AuditChainHead, SourceSystem
from ..inventory_models import FormalMaterial, InventoryLot, InventorySerial, InventoryTransaction, InventoryMovement, StockAccount
from ..work_order_completion_schemas import WorkOrderCompletionCheckOut, WorkOrderCompletionIssueOut
from ..work_order_query_schemas import WorkOrderSerialOptionOut
from . import inventory_query as inventory
from .inventory_posting import _require_current_actor
from .work_order_material_options import material_options
from .work_order_operation_read import verify_operation_history
from .work_order_replacement_read import replacement_result
from .work_order_removed_registration import verified_registration
from .work_order_reservations import read_work_order_reservations

MAX_HISTORY = 1000


def _invalid():
    raise inventory.InventoryReadError(code="work_order_completion_evidence_invalid", status_code=503,
        message="工单物料结束检查的原始证据不完整，请先核验流水与责任记录")


def _changed():
    raise inventory.InventoryReadError(code="work_order_completion_changed", status_code=409,
        message="工单、库存或拆回登记在检查期间发生变化，请重新检查")


def _audit_cursor(db):
    return tuple(db.execute(select(AuditChainHead.version, AuditChainHead.last_event_id, AuditChainHead.last_hash)
        .where(AuditChainHead.stream_key == "material_request")))


def _history(db, model, work_order_id):
    rows = tuple(db.scalars(select(model).where(model.oam_work_order_id == work_order_id)
        .order_by(model.id).limit(MAX_HISTORY + 1).execution_options(populate_existing=True)))
    if len(rows) > MAX_HISTORY:
        raise inventory.InventoryReadError(code="work_order_completion_history_too_large", status_code=503,
            message="该工单历史超出当前完整检查范围，不能用部分结果判定完成")
    return rows


def _issue(db, *, kind, reference_id, account_id, material_id, condition, quantity, serial_ids,
           lot_id=None, operation=None):
    sku = db.get(FormalMaterial, material_id, populate_existing=True)
    lot = db.get(InventoryLot, lot_id, populate_existing=True) if lot_id else None
    if sku is None or (lot_id and (lot is None or lot.material_id != sku.id)):
        _invalid()
    serials = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(serial_ids))
        .order_by(InventorySerial.id).execution_options(populate_existing=True)))
    if ({row.id for row in serials} != set(serial_ids) or any(row.material_id != sku.id or row.lot_id != lot_id for row in serials)
            or (serials and Decimal(quantity) != len(serials))):
        _invalid()
    return WorkOrderCompletionIssueOut(kind=kind, reference_id=reference_id,
        operation_id=operation.id if operation else None, operation_no=operation.operation_no if operation else None,
        stock_account_id=account_id, material_id=sku.id, sku_code=sku.sku_code,
        material_name=sku.name, base_unit=sku.base_unit, condition_code=condition, lot_id=lot_id,
        lot_no=lot.lot_no if lot else None, quantity=format(Decimal(quantity), ".3f"),
        serials=tuple(WorkOrderSerialOptionOut(serial_id=row.id, serial_no=row.serial_no) for row in serials))


def _issues(db, *, actor, options, operations, registrations):
    issues = []; blockers = set(); recovered_serials = set(); verified_parents = set()
    order_id = options.work_order.work_order_id
    # Inspect all original reserve destinations, including accounts that no
    # longer have positive current balance. Never silently hide an obligation.
    destinations = set(db.scalars(select(InventoryMovement.to_account_id).join(InventoryTransaction,
        InventoryTransaction.id == InventoryMovement.transaction_id).where(
            InventoryTransaction.source_document_type == "work_order_material",
            InventoryTransaction.source_document_id == str(order_id), InventoryTransaction.movement_type == "reserve")))
    remaining = read_work_order_reservations(db, work_order_id=order_id, account_ids=destinations,
        before_cursor=options.ledger_cursor + 1)
    available = {item.stock_account_id: item for item in options.items if item.availability_bucket == "reserved"}
    for identifier, reservation in sorted(remaining.items(), key=lambda row: str(row[0])):
        if reservation.quantity == 0: continue
        account = db.get(StockAccount, identifier, populate_existing=True)
        if account is None: _invalid()
        if account.custodian_person_id != actor.person_id or account.location_id != options.location_id:
            blockers.add("history_scope_unresolved"); continue
        item = available.get(identifier)
        if (item is None or Decimal(item.selectable_quantity) != reservation.quantity
                or {row.serial_id for row in item.serials} != set(reservation.serial_ids)):
            _invalid()
        issues.append(_issue(db, kind="unreleased_reservation", reference_id=identifier, account_id=identifier,
            material_id=account.material_id, condition=account.condition_code, lot_id=account.lot_id,
            quantity=reservation.quantity, serial_ids=reservation.serial_ids))
    for operation in operations:
        tx = db.get(InventoryTransaction, operation.posting_transaction_id, populate_existing=True)
        if tx is None: _invalid()
        if operation.operator_person_id != actor.person_id or tx.actor_user_id != actor.user_id:
            blockers.add("history_scope_unresolved"); continue
        if operation.operation_type == "reverse" or db.scalar(select(InventoryTransaction.id)
                .where(InventoryTransaction.reversed_transaction_id == tx.id).limit(1)):
            blockers.add("reversal_review_required"); continue
        if operation.replacement_id:
            if operation.replacement_id not in verified_parents:
                parent = db.get(WorkOrderReplacement, operation.replacement_id, populate_existing=True)
                if parent is None: _invalid()
                replacement_result(db, replacement=parent, actor=actor)
                verified_parents.add(parent.id)
        else:
            verify_operation_history(db, actor=actor, operation=operation)
        if operation.operation_type not in {"consume", "recover"}: continue
        for line in db.scalars(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == operation.id)
                .order_by(WorkOrderMaterialLine.line_no)):
            serials = set(db.scalars(select(WorkOrderMaterialSerial.serial_id).where(WorkOrderMaterialSerial.operation_line_id == line.id)))
            if operation.operation_type == "recover":
                recovered_serials.update(serials)
                kind = "pending_return"
            elif serials and operation.replacement_id is None:
                kind = "unpaired_serial_consumption"
            else: continue
            account = db.get(StockAccount, line.stock_account_id, populate_existing=True)
            if account is None: _invalid()
            issues.append(_issue(db, kind=kind, reference_id=line.id, account_id=line.stock_account_id,
                material_id=line.material_id, condition=line.condition_before, quantity=line.quantity,
                lot_id=account.lot_id, serial_ids=serials, operation=operation))
    for row in registrations:
        if row.operator_person_id != actor.person_id or row.actor_user_id != actor.user_id:
            blockers.add("history_scope_unresolved"); continue
        verified_registration(db, actor=actor, row=row)
        if row.serial_id not in recovered_serials:
            issues.append(_issue(db, kind="pending_recovery", reference_id=row.id, account_id=row.basis_stock_account_id,
                material_id=row.material_id, condition=row.command_jsonb["condition_before"], lot_id=row.lot_id,
                quantity=Decimal(1), serial_ids={row.serial_id}))
    if len(issues) > MAX_HISTORY:
        _invalid()
    return tuple(sorted(issues, key=lambda row: (row.kind, str(row.reference_id)))), blockers


def completion_check(db, *, actor, work_order_id):
    current = _require_current_actor(db, actor)
    with db.no_autoflush:
        options = material_options(db, actor=current, work_order_id=work_order_id)
        audit_cursor = _audit_cursor(db)
        snapshot = inventory._ProjectionSnapshot(options.ledger_cursor, options.projected_at)
        source_enabled = db.scalar(select(SourceSystem.enabled).where(SourceSystem.code == "starcharge_oam"))
        operations = _history(db, WorkOrderMaterialOperation, work_order_id)
        registrations = _history(db, Registration, work_order_id)
        try:
            transaction_ids = tuple(db.scalars(select(InventoryTransaction.id).where(
                InventoryTransaction.source_document_type == "work_order_material",
                InventoryTransaction.source_document_id == str(work_order_id)).limit(MAX_HISTORY + 1)))
            if set(transaction_ids) != {row.posting_transaction_id for row in operations}:
                _invalid()
            issues, blockers = _issues(db, actor=current, options=options, operations=operations, registrations=registrations)
        except Exception as exc:
            inventory._ensure_projection_snapshot_current(db, snapshot)
            if _audit_cursor(db) != audit_cursor: _changed()
            if isinstance(exc, (KeyError, ValueError, TypeError, AttributeError)): _invalid()
            raise
        inventory._ensure_projection_snapshot_current(db, snapshot)
        if (material_options(db, actor=current, work_order_id=work_order_id) != options
                or _audit_cursor(db) != audit_cursor
                or db.scalar(select(SourceSystem.enabled).where(SourceSystem.code == "starcharge_oam")) != source_enabled):
            _changed()
        _require_current_actor(db, current)
        if options.opening_balance_status != "established": blockers.add("opening_not_established")
        if options.work_order.freshness != "fresh": blockers.add("source_stale")
        if not source_enabled: blockers.add("source_disabled")
        blockers.update(row.kind for row in issues)
        return WorkOrderCompletionCheckOut(person_id=current.person_id, authorization_version=current.authorization_version,
            work_order=options.work_order, checked_at=datetime.now(timezone.utc), ledger_cursor=options.ledger_cursor,
            material_check_status="blocked" if blockers else "clear", blockers=tuple(sorted(blockers)),
            issue_count=len(issues), issues=issues)
