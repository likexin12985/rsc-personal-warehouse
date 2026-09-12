"""Narrow coordinates shared by posting and immutable serial reconstruction."""
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import aliased

from ..demand_models import WorkOrderReversal, WorkOrderReversalItem, WorkOrderMaterialOperation
from ..inventory_models import InventoryMovement, InventoryMovementSerial, InventoryTransaction


def child_key(parent_hash, original_operation_id):
    return f"work-order-reversal:{parent_hash}:{original_operation_id}"


def child_digest(parent_hash, original_operation_id):
    return hashlib.sha256(child_key(parent_hash, original_operation_id).encode()).hexdigest()


def inverse_movements(db, transaction_id):
    from .inventory_posting import InventoryMovementCommand
    result = []
    for row in db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == transaction_id)
            .order_by(InventoryMovement.line_no)):
        serials = tuple(db.scalars(select(InventoryMovementSerial.serial_id)
            .where(InventoryMovementSerial.movement_id == row.id).order_by(InventoryMovementSerial.serial_id)))
        result.append(InventoryMovementCommand(row.to_account_id, row.from_account_id, row.quantity,
            serials, row.external_boundary_code))
    return tuple(result)


def require_reversal_posting(db, *, actor, command, original_transaction_id):
    """Original and parent already exist; the inverse is still being appended."""
    from .inventory_posting import InventoryPostingError
    from .serial_ledger import rebuild_serial_states
    original = db.get(InventoryTransaction, original_transaction_id)
    if original is None:
        raise InventoryPostingError("original_transaction_not_found", "not_found", "原库存交易不存在")
    bound = db.scalar(select(WorkOrderMaterialOperation.id).where(WorkOrderMaterialOperation.posting_transaction_id == original.id))
    if original.source_document_type != "work_order_material" and command.source_document_type != "work_order_material" and bound is None:
        return {}
    row = db.execute(select(WorkOrderReversal, WorkOrderReversalItem, WorkOrderMaterialOperation)
        .join(WorkOrderReversalItem, WorkOrderReversalItem.reversal_id == WorkOrderReversal.id)
        .join(WorkOrderMaterialOperation, WorkOrderMaterialOperation.id == WorkOrderReversalItem.original_operation_id)
        .where(WorkOrderMaterialOperation.posting_transaction_id == original.id)).one_or_none()
    def invalid():
        raise InventoryPostingError("work_order_reversal_binding_invalid", "precondition_failed",
            "反向流水必须绑定完整原工单冲销命令")
    if row is None:
        invalid()
    parent, item, operation = row
    digest = child_digest(parent.idempotency_key_hash, operation.id)
    if (parent.actor_user_id != actor.user_id or parent.operator_person_id != actor.person_id
            or operation.operator_person_id != actor.person_id or original.actor_user_id != actor.user_id
            or parent.oam_work_order_id != operation.oam_work_order_id
            or command.source_document_type != "work_order_material" or command.source_document_id != str(parent.oam_work_order_id)
            or command.movement_type != "reversal" or command.posting_key != f"work-order-material:reverse:{parent.oam_work_order_id}:{digest}"
            or command.movements != inverse_movements(db, original.id)
            or original.source_document_type != "work_order_material"
            or original.source_document_id != str(parent.oam_work_order_id)
            or original.reversed_transaction_id is not None or original.status != "posted"):
        invalid()
    selected = parent.original_operation_id
    if selected is not None:
        if selected != operation.id or item.ordinal != 1 or operation.replacement_id is not None:
            invalid()
    else:
        from ..demand_models import WorkOrderReplacement
        pair = db.get(WorkOrderReplacement, parent.original_replacement_id)
        if pair is None or item.ordinal not in (1, 2) or (pair.recover_operation_id, pair.consume_operation_id)[item.ordinal - 1] != operation.id:
            invalid()
    ids = tuple(identifier for movement in command.movements for identifier in movement.serial_ids)
    current = rebuild_serial_states(db, ids)
    before = rebuild_serial_states(db, ids, through_cursor=original.ledger_cursor - 1)
    originals = {row.id for row in db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == original.id))}
    if any(current[identifier].last_movement_id not in originals for identifier in ids):
        invalid()
    return before


def serial_reversal_coordinates(db, serial_ids):
    """Read only narrow original/inverse links; whole parent proof lives in PG."""
    if not serial_ids:
        return {}
    inverse_op = aliased(WorkOrderMaterialOperation)
    original_op = aliased(WorkOrderMaterialOperation)
    result = {}
    for inverse_tx_id, original_tx_id, serial_id in db.execute(select(
            inverse_op.posting_transaction_id, original_op.posting_transaction_id, InventoryMovementSerial.serial_id)
            .select_from(WorkOrderReversalItem)
            .join(WorkOrderReversal, WorkOrderReversal.id == WorkOrderReversalItem.reversal_id)
            .join(inverse_op, inverse_op.id == WorkOrderReversalItem.inverse_operation_id)
            .join(original_op, original_op.id == WorkOrderReversalItem.original_operation_id)
            .join(InventoryMovementSerial, InventoryMovementSerial.transaction_id == inverse_op.posting_transaction_id)
            .where(InventoryMovementSerial.serial_id.in_(serial_ids), inverse_op.operation_type == "reverse",
                inverse_op.status == "posted", original_op.status == "posted",
                inverse_op.oam_work_order_id == WorkOrderReversal.oam_work_order_id,
                original_op.oam_work_order_id == WorkOrderReversal.oam_work_order_id,
                inverse_op.operator_person_id == WorkOrderReversal.operator_person_id,
                original_op.operator_person_id == WorkOrderReversal.operator_person_id)):
        result[(inverse_tx_id, serial_id)] = original_tx_id
    return result


def require_reservation_reversal_link(db, transaction):
    """Check narrow immutable links without recursing into original reservations."""
    from .inventory_posting import InventoryPostingError
    original = aliased(WorkOrderMaterialOperation)
    inverse = aliased(WorkOrderMaterialOperation)
    row = db.execute(select(WorkOrderReversal, original, inverse)
        .select_from(WorkOrderReversalItem)
        .join(WorkOrderReversal, WorkOrderReversal.id == WorkOrderReversalItem.reversal_id)
        .join(original, original.id == WorkOrderReversalItem.original_operation_id)
        .join(inverse, inverse.id == WorkOrderReversalItem.inverse_operation_id)
        .where(inverse.posting_transaction_id == transaction.id)).one_or_none()
    if row is not None:
        parent, old, new = row
        if (old.posting_transaction_id == transaction.reversed_transaction_id and new.operation_type == "reverse"
                and old.status == new.status == "posted" and old.operation_type != "reverse"
                and old.oam_work_order_id == new.oam_work_order_id == parent.oam_work_order_id
                and transaction.source_document_id == str(parent.oam_work_order_id)
                and old.operator_person_id == new.operator_person_id == parent.operator_person_id
                and transaction.actor_user_id == parent.actor_user_id):
            # Full line/account/SN equality still matters to read-only reservation
            # arithmetic; matching headers alone cannot justify quantity changes.
            from .inventory_posting import InventoryMovementCommand
            actual = []
            for movement in db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == transaction.id).order_by(InventoryMovement.line_no)):
                ids = tuple(db.scalars(select(InventoryMovementSerial.serial_id).where(InventoryMovementSerial.movement_id == movement.id).order_by(InventoryMovementSerial.serial_id)))
                actual.append(InventoryMovementCommand(movement.from_account_id, movement.to_account_id,
                    movement.quantity, ids, movement.external_boundary_code))
            if tuple(actual) == inverse_movements(db, transaction.reversed_transaction_id):
                return
    raise InventoryPostingError("work_order_reservation_history_invalid", "conflict", "反向占用缺少准确原操作及整批冲销关联")
