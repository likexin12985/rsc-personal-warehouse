"""A newly registered removed identity first belongs to its explicit work order."""
import hashlib
from sqlalchemy import select
from ..demand_models import WorkOrderRemovedSerialRegistration as Registration, WorkOrderReplacement, WorkOrderReplacementPair
from ..inventory_models import StockAccount
from .work_order_replacement_proof import replacement_child_key


def require_registration_context(db, *, serial_id, work_order_id, basis_stock_account_id, operator_person_id):
    reg=db.scalar(select(Registration).where(Registration.serial_id==serial_id))
    if reg is not None and (reg.oam_work_order_id!=work_order_id or reg.basis_stock_account_id!=basis_stock_account_id
            or reg.operator_person_id!=operator_person_id):
        from .inventory_posting import InventoryPostingError
        raise InventoryPostingError("removed_registration_scope_mismatch","precondition_failed","新登记的拆回 SN 只能用于其原工单、投入依据及本人个人仓")


def require_registration_posting(db, *, serial_id, state, command, movement, account):
    if state.last_movement_id is not None:return
    reg=db.scalar(select(Registration).where(Registration.serial_id==serial_id))
    if reg is None:return
    basis=db.get(StockAccount,reg.basis_stock_account_id)
    correct=(command.movement_type=="inbound" and command.source_document_type=="work_order_material"
        and command.source_document_id==str(reg.oam_work_order_id) and movement.from_account_id is None
        and movement.external_boundary_code=="work_order_material_recover" and account.condition_code in {"used","damaged"}
        and account.availability_bucket=="available" and account.custodian_person_id==reg.operator_person_id
        and basis is not None and account.owner_org_id==basis.owner_org_id and account.location_id==basis.location_id)
    if correct:
        parents=db.scalars(select(WorkOrderReplacement).join(WorkOrderReplacementPair,WorkOrderReplacementPair.replacement_id==WorkOrderReplacement.id)
            .where(WorkOrderReplacement.oam_work_order_id==reg.oam_work_order_id,WorkOrderReplacement.operator_person_id==reg.operator_person_id,
                WorkOrderReplacementPair.removed_serial_id==reg.serial_id,
                WorkOrderReplacementPair.operation_id==WorkOrderReplacement.consume_operation_id))
        for parent in parents:
            digest=hashlib.sha256(replacement_child_key(parent.idempotency_key_hash,"recover").encode()).hexdigest()
            if command.posting_key==f"work-order-material:recover:{reg.oam_work_order_id}:{digest}" and any(
                    line.get("basis_stock_account_id")==str(reg.basis_stock_account_id) and str(reg.serial_id) in line.get("serial_ids",[])
                    for line in parent.command_jsonb.get("recover_lines",[])):
                return
    from .inventory_posting import InventoryPostingError
    raise InventoryPostingError("removed_registration_origin_invalid","precondition_failed","新登记拆回 SN 的首次入库必须有原工单完整成对回收证明")
