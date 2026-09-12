"""Narrow serial recovery coordinates from immutable replacement commands."""
import hashlib
from uuid import UUID

from sqlalchemy import select

from ..demand_models import WorkOrderMaterialOperation, WorkOrderReplacement, WorkOrderReplacementPair


def replacement_child_key(parent_hash, operation):
    return f"work-order-replacement:{parent_hash}:{operation}"


def has_serial_recovery_command(db, *, command, serial_id, operator_person_id):
    if command.movement_type != "inbound" or command.source_document_type != "work_order_material":
        return False
    try:
        work_order_id = UUID(command.source_document_id)
    except (TypeError, ValueError):
        return False
    replacements = db.scalars(select(WorkOrderReplacement).join(WorkOrderReplacementPair,
        WorkOrderReplacementPair.replacement_id == WorkOrderReplacement.id).where(
            WorkOrderReplacementPair.removed_serial_id == serial_id,
            WorkOrderReplacementPair.operation_id == WorkOrderReplacement.consume_operation_id,
            WorkOrderReplacement.oam_work_order_id == work_order_id,
            WorkOrderReplacement.operator_person_id == operator_person_id))
    for replacement in replacements:
        key = replacement_child_key(replacement.idempotency_key_hash, "recover")
        digest = hashlib.sha256(key.encode()).hexdigest()
        if command.posting_key == f"work-order-material:recover:{work_order_id}:{digest}":
            return True
    return False


def serial_recovery_coordinates(db, serial_ids):
    if not serial_ids:
        return set()
    return set(db.execute(select(WorkOrderMaterialOperation.posting_transaction_id,
        WorkOrderReplacementPair.removed_serial_id).select_from(WorkOrderReplacementPair)
        .join(WorkOrderReplacement, WorkOrderReplacement.id == WorkOrderReplacementPair.replacement_id)
        .join(WorkOrderMaterialOperation, WorkOrderMaterialOperation.id == WorkOrderReplacement.recover_operation_id)
        .where(WorkOrderReplacementPair.removed_serial_id.in_(serial_ids),
            WorkOrderReplacementPair.operation_id == WorkOrderReplacement.consume_operation_id,
            WorkOrderMaterialOperation.replacement_id == WorkOrderReplacement.id,
            WorkOrderMaterialOperation.operation_type == "recover", WorkOrderMaterialOperation.status == "posted",
            WorkOrderMaterialOperation.oam_work_order_id == WorkOrderReplacement.oam_work_order_id,
            WorkOrderMaterialOperation.operator_person_id == WorkOrderReplacement.operator_person_id)).all())
