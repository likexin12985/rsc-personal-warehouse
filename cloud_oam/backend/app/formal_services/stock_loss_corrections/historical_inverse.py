"""Historical plan proof for an original loss disposition's inverse.

Internal candidate, not read authorization or a posting permit. Reconstructs
the exact pre-inverse state from immutable ledger facts, never current balance
or SN caches. Return compensation, scrap, corrected executions and PostgreSQL
COMMIT authority remain distinct integrations before a public recovery API.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.inventory_models import InventoryMovement, InventoryTransaction, StockAccount
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.work_order_query import _aware
from .chain_projection import InvalidChain
from .correction_models import StockLossDispositionReversal as Inverse, StockLossCorrectionExecution as Execution
from .history_events import load_event_checked_inventory_history
from .historical_holds import read_hold_snapshot
from .historical_original import _bound, verify_historical_original
from .request_contracts import ReversalPreview


def _need(condition):
    if not condition:
        raise InvalidChain('loss_inverse_historical_plan_invalid')


def _balance(db, account_id, cursor):
    amount = Decimal(0); transactions = set()
    for move in db.scalars(select(InventoryMovement).join(InventoryTransaction,
            InventoryTransaction.id == InventoryMovement.transaction_id).where(
                InventoryTransaction.status == 'posted', InventoryTransaction.ledger_cursor <= cursor,
                (InventoryMovement.from_account_id == account_id) | (InventoryMovement.to_account_id == account_id))):
        amount += move.quantity if move.to_account_id == account_id else -move.quantity
        transactions.add(move.transaction_id)
    _need(amount >= 0)
    return amount, len(transactions)


@dataclass(frozen=True)
class HistoricalInverse:
    reversal_id: UUID
    root_disposition_id: UUID
    posting_transaction_id: UUID
    posting_cursor: int
    observed_ledger_cursor: int
    plan_hash: str


def verify_plan(db, *, reversal_id, proved_execution_ids):
    """Prove persisted original/inverse events, request, stock and full plan.

Historical custody remains time-bounded; an expired assignment is valid at
its original time. Current master active status is not historical authority.
"""
    _need(type(reversal_id) is UUID and reversal_id.int != 0)
    with db.no_autoflush:
        start = _bound(db)
        row = db.get(Inverse, reversal_id, populate_existing=True)
        _need(row is not None)
        if row.source_account_id is None:
            from ..stock_scrap.recovery_history import verify_plan as verify_recovery
            return verify_recovery(db, inverse=row, proved_execution_ids=proved_execution_ids)
        root = db.get(StockLossDisposition, row.root_disposition_id, populate_existing=True)
        _need(root is not None)
        execution = db.get(Execution, row.reversed_correction_id, populate_existing=True) if row.reversed_correction_id else root
        _need(execution is not None and execution.id in proved_execution_ids
            and (execution.disposition in {'restore_available', 'convert_used', 'convert_damaged'}
                or (execution is root and execution.disposition == 'return_to_region'))
            and (execution is root or execution.root_disposition_id == root.id))
        order = db.get(StockOperationOrder, root.operation_id, populate_existing=True)
        loaded = load_event_checked_inventory_history(db, root_disposition_id=root.id)
        _need(loaded.observed_ledger_cursor == start[0]
            and any(fact.id == row.id for fact in loaded.history.reversals))
        verify_historical_original(db, root_disposition_id=root.id)
        tx = db.get(InventoryTransaction, row.posting_transaction_id, populate_existing=True)
        original_tx = db.get(InventoryTransaction, execution.posting_transaction_id, populate_existing=True)
        original_move = db.get(InventoryMovement, execution.posting_movement_id, populate_existing=True)
        source = db.get(StockAccount, execution.target_account_id, populate_existing=True)
        target = db.get(StockAccount, root.source_account_id, populate_existing=True)
        _need(all(value is not None for value in (order, tx, original_tx, original_move, source, target)))
        cursor = tx.ledger_cursor - 1
        _need(original_tx.ledger_cursor <= cursor and row.source_account_id == source.id
            and row.target_account_id == target.id and target.availability_bucket == 'frozen'
            and source.id != target.id and row.quantity == root.quantity
            and all(getattr(source, key) == getattr(target, key) for key in
                ('owner_org_id', 'location_id', 'custodian_person_id', 'material_id', 'lot_id')))
        # Select by original time without imposing today's active location or
        # assignment. Immutable account dimensions are independently proved.
        from app.inventory_models import CustodyAssignment
        from sqlalchemy import or_
        at = _aware(row.created_at)
        assignments = tuple(db.scalars(select(CustodyAssignment).where(
            CustodyAssignment.location_id == target.location_id, CustodyAssignment.valid_from <= at,
            or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))
            .execution_options(populate_existing=True)))
        _need(len(assignments) == 1 and assignments[0].id == row.custody_assignment_id
            and assignments[0].custodian_person_id == order.requester_id == target.custodian_person_id)
        custody = assignments[0]
        source_balance, source_version = _balance(db, source.id, cursor)
        target_balance, target_version = _balance(db, target.id, cursor)
        _need(source_balance >= row.quantity)
        holds = read_hold_snapshot(db, source_account_id=target.id, through_cursor=cursor)
        selected = next((line for line in holds.lines if line.line_id == root.line_id), None)
        _need(holds.observed_ledger_cursor == start[0] and selected is not None
            and selected.active_execution_id == execution.id and selected.pending_reversal_id is None
            and selected.frozen_quantity == 0)
        for line in holds.lines:
            if line.root_disposition_id is not None and line.root_disposition_id != root.id:
                verify_historical_original(db, root_disposition_id=line.root_disposition_id)
        serial_ids = tuple(sorted(loaded.history.basis.serial_ids, key=str))
        states = rebuild_serial_states(db, serial_ids, through_cursor=cursor)
        prior_states = rebuild_serial_states(db, serial_ids, through_cursor=original_tx.ledger_cursor - 1)
        serial_basis = []
        for identifier in serial_ids:
            state, prior = states.get(identifier), prior_states.get(identifier)
            _need(state is not None and prior is not None and state.stock_account_id == source.id
                and state.last_movement_id == original_move.id and state.lifecycle_status == 'active'
                and prior.stock_account_id == target.id and prior.lifecycle_status == 'active')
            serial_basis.append(dict(serial_id=str(identifier), original_movement_id=str(original_move.id),
                lifecycle_before=state.lifecycle_status, lifecycle_after=prior.lifecycle_status,
                previous_movement_id=str(prior.last_movement_id), previous_ledger_cursor=prior.ledger_cursor))
        policies, fingerprint = sources._policies(db, {target.material_id}, at)
        selection = ReversalPreview.model_validate(row.command_jsonb['intent'])
        movement = posting.InventoryMovementCommand(source.id, target.id, row.quantity, serial_ids)
        posting._validate_tracking_rules(posting.InventoryPostingCommand(transaction_no='historical-loss-inverse',
            movement_type='reversal', source_document_type='stock_loss_disposition_reversal',
            source_document_id=str(row.id), posting_key='historical-loss-inverse', effective_at=at,
            movements=(movement,)), {source.id: source, target.id: target}, policies)
        recorded = row.plan_jsonb.get('policy_fingerprint')
        _need(type(recorded) is list and len(recorded) == 1 and type(recorded[0]) is list
            and len(recorded[0]) == 7 and recorded[0][:6] == list(fingerprint[0][:6]))
        if recorded[0][6] is not None:
            _need(recorded[0][6] == fingerprint[0][6]
                and _aware(datetime.fromisoformat(recorded[0][6])) > at)
        return_boundary = None
        if execution.disposition == 'return_to_region':
            from . import return_stop
            return_boundary = return_stop.verify(db, root=root, inverse=row)
        expected = dict(schema_version='1.0', stage='stock_preparation_only', intent=selection.model_dump(mode='json'),
            actor_user_id=row.actor_user_id, actor_person_id=str(row.actor_person_id), authorization_version=row.authorization_version,
            root_disposition_id=str(root.id), original_execution_id=str(execution.id),
            original_transaction_id=str(original_tx.id), original_movement_id=str(original_move.id),
            original_ledger_cursor=original_tx.ledger_cursor, source_account_id=str(source.id), target_account_id=str(target.id),
            quantity=format(row.quantity, '.3f'), serial_ids=[str(s) for s in serial_ids], serials=serial_basis,
            source_condition=source.condition_code, target_condition=target.condition_code,
            custody_assignment_id=str(custody.id), ledger_cursor=cursor,
            source_balance_quantity=format(source_balance, '.3f'), source_balance_version=source_version,
            target_balance_quantity=format(target_balance, '.3f'), target_balance_version=target_version,
            frozen_holds_before=holds.plan_basis(), restored_line_id=str(root.line_id),
            restored_frozen_quantity=format(row.quantity, '.3f'), return_boundary=return_boundary,
            policy_fingerprint=recorded)
        # Exact canonical equality also rejects extra fields and JSON boolean
        # substitutions for integer versions/cursors. A self-consistent hash
        # of an invented balance or hold document is insufficient.
        _need(sources._hash(expected) == row.plan_hash == sources._hash(row.plan_jsonb))
        _need(_bound(db) == start)
        return HistoricalInverse(row.id, root.id, tx.id, tx.ledger_cursor, start[0], row.plan_hash)
