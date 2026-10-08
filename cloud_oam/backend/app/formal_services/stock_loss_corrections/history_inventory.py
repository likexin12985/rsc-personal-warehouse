"""Load persisted correction facts and prove their inventory edges.

This internal stage is not a business-history response or a write authority.
The original submission/HQ approval are checked with existing proofs, but the
original disposition audit, new correction audits, downstream compensation,
current permissions and serial lifecycle still need the full composed proof.
Nothing here accepts client-supplied Binding/History objects or posts stock.
"""
from dataclasses import dataclass
import re
from uuid import UUID

from sqlalchemy import func, select

from app.inventory_models import InventoryTransaction, StockAccount
from app.stock_operation_models import StockLossDisposition, StockLossHeadquartersDecision
from app.stock_operation_models import StockLossHeadquartersReview, StockOperationLine, StockOperationOrder, StockOperationSerial
from app.formal_services import inventory_posting as posting
from app.formal_services import stock_loss_facts as submission
from app.formal_services import stock_loss_headquarters_reviews as headquarters
from app.formal_services import stock_loss_disposition_facts as original_commands
from app.formal_services import stock_loss_return_facts as return_commands
from app.formal_services import stock_loss_sources as sources
from app.formal_services.work_order_query import _aware
from .correction_models import StockLossDispositionReversal as InverseRow
from .correction_models import StockLossCorrectionDecision as DecisionRow, StockLossCorrectionExecution as ExecutionRow
from .chain_projection import Basis, Execution, Reversal, CorrectionDecision, InvalidChain, project
from .current_holds import History
from .ledger_edges import Binding, check_edges
from .request_facts import verify_request_fact


@dataclass(frozen=True)
class InventoryHistory:
    root_disposition_id: UUID
    history: History
    # This is the observation bound for later composition, not authorization.
    observed_ledger_cursor: int


def _need(condition, code):
    if not condition:
        raise InvalidChain(code)


def _row(db, model, identifier, code):
    value = db.get(model, identifier, populate_existing=True)
    _need(value is not None, code)
    return value


def _request(row):
    _need(type(row.command_jsonb) is dict and sources._hash(row.command_jsonb) == row.request_hash
        and re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', row.request_id) is not None,
        'persisted_request_hash_mismatch')


def _plan(row, serials):
    _need(type(row.plan_jsonb) is dict and sources._hash(row.plan_jsonb) == row.plan_hash,
        'persisted_plan_hash_mismatch')
    _need(row.plan_jsonb.get('serial_ids') == [str(s) for s in serials],
        'persisted_plan_serials_mismatch')


def inverse_command(row):
    return posting.InventoryReversalCommand(original_transaction_id=row.original_transaction_id,
        transaction_no='INV-LOSS-REV-' + row.idempotency_key_hash[:20].upper(),
        source_document_type='stock_loss_disposition_reversal',source_document_id=str(row.id),
        posting_key=f'stock-loss:reverse_loss:{row.id}:{row.idempotency_key_hash}',
        effective_at=_aware(row.created_at))


def correction_command(row, serials):
    if row.disposition == 'scrap':
        from ..stock_scrap.request_facts import posting_command as scrap_command
        command = scrap_command(row)
        _need(command.movements[0].serial_ids == serials, 'scrap_serial_command_mismatch')
        return command
    movement = {'restore_available':'unfreeze','convert_used':'status_change',
        'convert_damaged':'status_change','return_to_region':'reserve','scrap':'scrap'}[row.disposition]
    return posting.InventoryPostingCommand(transaction_no='INV-LOSS-CORR-' + row.idempotency_key_hash[:20].upper(),
        movement_type=movement,source_document_type='stock_loss_correction_execution',source_document_id=str(row.id),
        posting_key=f'stock-loss:correct_loss:{row.id}:{row.idempotency_key_hash}',effective_at=_aware(row.created_at),
        movements=(posting.InventoryMovementCommand(from_account_id=row.source_account_id,
            to_account_id=row.target_account_id,quantity=row.quantity,serial_ids=serials,
            external_boundary_code='stock_operation_scrap' if row.disposition=='scrap' else None),))


def _candidate_rows(db, root_id):
    return tuple(tuple(db.scalars(select(model).where(model.root_disposition_id == root_id)
        .order_by(model.id).execution_options(populate_existing=True)))
        for model in (InverseRow, DecisionRow, ExecutionRow))


def load_inventory_history(db, *, root_disposition_id):
    """Read all persisted successors; callers cannot omit an inconvenient edge."""
    _need(type(root_disposition_id) is UUID and root_disposition_id.int != 0, 'root_id_required')
    with db.no_autoflush:
        before_cursor = db.scalar(select(func.max(InventoryTransaction.ledger_cursor)))
        root = _row(db,StockLossDisposition,root_disposition_id,'root_disposition_missing')
        line = _row(db,StockOperationLine,root.line_id,'original_line_missing')
        order = _row(db,StockOperationOrder,root.operation_id,'original_order_missing')
        decision = _row(db,StockLossHeadquartersDecision,root.headquarters_decision_id,'original_decision_missing')
        review = _row(db,StockLossHeadquartersReview,decision.review_id,'original_review_missing')
        freeze = _row(db,InventoryTransaction,order.posting_transaction_id,'original_freeze_missing')
        account = _row(db,StockAccount,line.reserved_account_id,'original_frozen_account_missing')
        _need(order.operation_type == line.operation_type == 'loss_report'
            and line.operation_id == root.operation_id == review.operation_id
            and decision.line_id == root.line_id and decision.disposition == root.disposition
            and line.quantity == root.quantity and root.source_account_id == account.id
            and account.availability_bucket == 'frozen' and account.material_id == line.material_id,
            'original_lineage_mismatch')
        # These checks use the original actors/timestamps. They do not require
        # departed historical reviewers to regain current write permission.
        submission.submission_evidence(db,order=order)
        headquarters.verified(db,row=review,order=order)
        serials = tuple(sorted(db.scalars(select(StockOperationSerial.serial_id).where(
            StockOperationSerial.line_id == line.id)),key=str))
        policies,_ = sources._policies(db,{account.material_id},_aware(freeze.effective_at))
        basis = Basis(order.id,line.id,decision.id,root.disposition,account.id,
            freeze.ledger_cursor,line.quantity,frozenset(serials),
            policies[account.material_id].tracking_mode in {'serial','lot_and_serial'})
        inverses,decisions,corrections = _candidate_rows(db,root.id)
        observed_ids = tuple(tuple(r.id for r in rows) for rows in (inverses,decisions,corrections))
        for row in (root,*inverses,*decisions,*corrections): _request(row)
        for row in (root,*inverses,*corrections): _plan(row,serials)
        root_tx = _row(db,InventoryTransaction,root.posting_transaction_id,'root_transaction_missing')
        executions = [Execution(root.id,order.id,line.id,decision.id,None,root.disposition,
            root.posting_transaction_id,root.posting_movement_id,root_tx.ledger_cursor,
            root.source_account_id,root.target_account_id,root.quantity,frozenset(serials))]
        root_command = (return_commands if root.disposition=='return_to_region' else original_commands).posting_command(root)
        bindings = [Binding(root.id,root.actor_user_id,root.executor_person_id,
            root.authorization_version,root.idempotency_key_hash,root_command)]
        for row in corrections:
            tx = _row(db,InventoryTransaction,row.posting_transaction_id,'correction_transaction_missing')
            executions.append(Execution(row.id,order.id,line.id,row.correction_decision_id,row.reversal_id,
                row.disposition,row.posting_transaction_id,row.posting_movement_id,tx.ledger_cursor,
                row.source_account_id,row.target_account_id,row.quantity,frozenset(serials)))
            bindings.append(Binding(row.id,row.actor_user_id,row.actor_person_id,row.authorization_version,
                row.idempotency_key_hash,correction_command(row,serials)))
        execution_by_id = {row.id:row for row in (root,*corrections)}
        reversal_facts=[]
        for row in inverses:
            target_id = row.reversed_correction_id or root.id
            target = execution_by_id.get(target_id)
            _need(target is not None and row.original_transaction_id==target.posting_transaction_id
                and row.original_movement_id==target.posting_movement_id,'inverse_original_binding_mismatch')
            tx = _row(db,InventoryTransaction,row.posting_transaction_id,'inverse_transaction_missing')
            reversal_facts.append(Reversal(row.id,order.id,line.id,target_id,row.posting_transaction_id,
                row.posting_movement_id,row.original_transaction_id,tx.ledger_cursor,
                row.source_account_id,row.target_account_id,row.quantity,frozenset(serials)))
            bindings.append(Binding(row.id,row.actor_user_id,row.actor_person_id,row.authorization_version,
                row.idempotency_key_hash,inverse_command(row)))
        inverse_by_id={row.id:row for row in inverses}
        decision_facts=[]
        for row in decisions:
            inverse=inverse_by_id.get(row.reversal_id)
            _need(inverse is not None and row.expected_reversal_hash==inverse.request_hash,
                'correction_approval_inverse_hash_mismatch')
            decision_facts.append(CorrectionDecision(row.id,order.id,line.id,decision.id,row.reversal_id,row.disposition))
        history = History(basis,tuple(executions),tuple(reversal_facts),tuple(decision_facts))
        project(basis,history.executions,history.reversals,history.decisions)
        # A correctly rehashed arbitrary JSON object is not a business command.
        # Prove canonical intent and exact persisted parent/result bindings.
        decision_by_id={row.id:row for row in decisions}
        for row in inverses:
            verify_request_fact(row=row,root=root,order=order,
                reversed_execution=execution_by_id.get(row.reversed_correction_id))
        for row in decisions:
            verify_request_fact(row=row,root=root,order=order,reversal=inverse_by_id.get(row.reversal_id))
        for row in corrections:
            verify_request_fact(row=row,root=root,order=order,reversal=inverse_by_id.get(row.reversal_id),
                decision=decision_by_id.get(row.correction_decision_id))
        check_edges(db,history,tuple(bindings))
        # Decisions alone do not advance the inventory cursor. Reread IDs as
        # well so an approval committed during loading cannot be silently lost.
        current_ids=tuple(tuple(r.id for r in rows) for rows in _candidate_rows(db,root.id))
        after_cursor=db.scalar(select(func.max(InventoryTransaction.ledger_cursor)))
        _need(current_ids==observed_ids and after_cursor==before_cursor,'business_history_changed_during_read')
        return InventoryHistory(root.id,history,after_cursor)
