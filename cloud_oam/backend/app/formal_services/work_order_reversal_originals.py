"""Own verified original commands for explicit whole-reversal selection.

An unreversed record is a selectable original, not permission to compensate it.
The full preview and writer separately check current stock and lifecycle state.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from ..demand_models import WorkOrderMaterialOperation, WorkOrderMaterialLine, WorkOrderReplacement, WorkOrderReversalItem, WorkOrderReversal
from ..inventory_models import InventoryTransaction, FormalMaterial
from ..work_order_reversal_schemas import WorkOrderReversalOriginalOut, WorkOrderReversalOriginalsOut
from . import inventory_query as inventory
from .inventory_posting import _require_current_actor
from .work_order_material_options import material_options
from .work_order_operation_read import verify_operation_history
from .work_order_replacement_read import replacement_result
from .work_order_reversal_read import reversal_result, _invalid, _utc
from .work_order_evidence_snapshot import material_audit_cursor


def list_originals(db, *, actor, work_order_id):
    current = _require_current_actor(db, actor)
    with db.no_autoflush:
        options = material_options(db, actor=current, work_order_id=work_order_id)
        snapshot = inventory._ProjectionSnapshot(options.ledger_cursor, options.projected_at)
        audit_cursor = material_audit_cursor(db)
        rows = tuple(db.scalars(select(WorkOrderMaterialOperation).where(
            WorkOrderMaterialOperation.oam_work_order_id == work_order_id,
            WorkOrderMaterialOperation.operator_person_id == current.person_id
        ).order_by(WorkOrderMaterialOperation.id).limit(1001).execution_options(populate_existing=True)))
        if len(rows) > 1000:
            raise inventory.InventoryReadError(code="work_order_reversal_history_too_large", status_code=503,
                message="该工单原记录超出当前完整读取范围，请保留记录核验")
        commands = {}; results = []
        for row in rows:
            tx = db.get(InventoryTransaction, row.posting_transaction_id, populate_existing=True)
            if tx is None: _invalid()
            # The same person may have historical identities; only this actor's
            # proven commands are candidates for their own compensation.
            if tx.actor_user_id != current.user_id or row.operation_type == "reverse": continue
            key = ("replace", row.replacement_id) if row.replacement_id else ("operation", row.id)
            if key in commands: continue
            if row.replacement_id:
                parent = db.get(WorkOrderReplacement, row.replacement_id, populate_existing=True)
                if parent is None: _invalid()
                replacement_result(db, actor=current, replacement=parent)
                originals = tuple(db.get(WorkOrderMaterialOperation, identifier, populate_existing=True)
                    for identifier in (parent.recover_operation_id, parent.consume_operation_id))
                operation_id, replacement_id, number, kind = None, parent.id, parent.replacement_no, "replace"
            else:
                verify_operation_history(db, actor=current, operation=row)
                originals = (row,)
                operation_id, replacement_id, number, kind = row.id, None, row.operation_no, row.operation_type
            if any(original is None or original.operator_person_id != current.person_id or original.oam_work_order_id != work_order_id for original in originals): _invalid()
            transactions = tuple(db.get(InventoryTransaction, original.posting_transaction_id) for original in originals)
            compensated = tuple(db.scalars(select(WorkOrderReversalItem).where(
                WorkOrderReversalItem.original_operation_id.in_([original.id for original in originals]))))
            reversal_id = None
            if compensated:
                if len(compensated) != len(originals) or len({item.reversal_id for item in compensated}) != 1: _invalid()
                reversal = db.get(WorkOrderReversal, compensated[0].reversal_id, populate_existing=True)
                if reversal is None: _invalid()
                proof = reversal_result(db, actor=current, parent=reversal)
                if proof.original_operation_id != operation_id or proof.original_replacement_id != replacement_id: _invalid()
                reversal_id = reversal.id
            elif db.scalar(select(InventoryTransaction.id).where(
                InventoryTransaction.reversed_transaction_id.in_([tx.id for tx in transactions])).limit(1)):
                _invalid()
            lines = tuple(db.execute(select(WorkOrderMaterialLine.id, FormalMaterial.name).join(
                FormalMaterial, FormalMaterial.id == WorkOrderMaterialLine.material_id).where(
                    WorkOrderMaterialLine.operation_id.in_([original.id for original in originals]))))
            if not lines or len(lines) > 200: _invalid()
            results.append(WorkOrderReversalOriginalOut(original_operation_id=operation_id, original_replacement_id=replacement_id,
                original_no=number, original_type=kind, posted_at=max(_utc(tx.posted_at) for tx in transactions),
                operation_count=len(originals), line_count=len(lines), material_names=tuple(sorted({name for _, name in lines})), reversal_id=reversal_id))
            commands[key] = True
        inventory._ensure_projection_snapshot_current(db, snapshot)
        if options != material_options(db, actor=current, work_order_id=work_order_id) or audit_cursor != material_audit_cursor(db):
            raise inventory.InventoryReadError(code="work_order_reversal_history_changed", status_code=409,
                message="工单原记录在读取期间变化，请重新读取")
        _require_current_actor(db, current)
        return WorkOrderReversalOriginalsOut(person_id=current.person_id, authorization_version=current.authorization_version,
            work_order=options.work_order, ledger_cursor=options.ledger_cursor, queried_at=datetime.now(timezone.utc),
            items=tuple(sorted(results, key=lambda row: (row.posted_at, row.original_no), reverse=True)))
