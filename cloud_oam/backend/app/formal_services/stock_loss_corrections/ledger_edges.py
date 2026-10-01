"""Check every correction-chain edge against persisted inventory facts.

Internal proof component only: bindings must come from persisted business
facts, never a client command. This does not prove approvals, audit/outbox,
current authority, compensation, account semantics or serial lifecycle. The
existing rejection of unproved reversals stays closed until all those proofs
are composed with this component. No ledger write or replay is performed.
"""
from dataclasses import dataclass
import re
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import func, select

from app.inventory_models import InventoryTransaction, InventoryMovement, InventoryMovementSerial
from app.formal_services import inventory_posting as posting
from app.formal_services.work_order_query import _aware
from .chain_projection import InvalidChain, Reversal, project
from .current_holds import History


@dataclass(frozen=True)
class Binding:
    fact_id: UUID
    actor_user_id: str
    actor_person_id: UUID
    authorization_version: int
    idempotency_key_hash: str
    command: posting.InventoryPostingCommand | posting.InventoryReversalCommand


def _need(condition, code):
    if not condition:
        raise InvalidChain(code)


def check_edges(db, history, bindings):
    """Check the WHOLE supplied history, including edges after a UI cutoff.

The caller still needs an authoritative complete business-history loader.
An unrepresented reversal of any supplied transaction is rejected here;
bindings and fact IDs must be bijective, and no cached balance is consulted.
"""
    _need(type(history) is History, 'history_required')
    project(history.basis, history.executions, history.reversals, history.decisions)
    _need(type(bindings) is tuple and all(type(b) is Binding for b in bindings),
        'immutable_ledger_bindings_required')
    indexed = {b.fact_id: b for b in bindings}
    facts = (*history.executions, *history.reversals)
    _need(len(indexed) == len(bindings) == len(facts)
        and set(indexed) == {f.id for f in facts}, 'ledger_binding_mismatch')
    transactions = {f.posting_transaction_id: f for f in facts}
    expected_inverses = {f.posting_transaction_id: f.original_transaction_id for f in history.reversals}
    with db.no_autoflush:
        before_cursor = db.scalar(select(func.max(InventoryTransaction.ledger_cursor)))
        # Fresh reads matter when this session inspected the same fact before
        # another transaction committed; no process-global proof cache exists.
        actual_inverses = dict(db.execute(select(InventoryTransaction.id,
            InventoryTransaction.reversed_transaction_id).where(
                InventoryTransaction.reversed_transaction_id.in_(tuple(transactions)))
            .execution_options(populate_existing=True)).all())
        _need(actual_inverses == expected_inverses, 'unrepresented_or_missing_inverse')
        boundary_by_execution = {}
        # Executions precede inverses here only for deriving the exact original
        # external boundary; ledger ordering is already checked by project().
        for fact in facts:
            binding = indexed[fact.id]
            _need(type(binding.actor_user_id) is str and bool(binding.actor_user_id)
                and type(binding.actor_person_id) is UUID and binding.actor_person_id.int != 0
                and type(binding.authorization_version) is int and binding.authorization_version > 0
                and type(binding.idempotency_key_hash) is str
                and re.fullmatch('[0-9a-f]{64}', binding.idempotency_key_hash) is not None,
                'ledger_actor_binding_invalid')
            actor = SimpleNamespace(user_id=binding.actor_user_id, person_id=binding.actor_person_id,
                authorization_version=binding.authorization_version)
            command = binding.command
            inverse = type(fact) is Reversal
            if inverse:
                _need(type(command) is posting.InventoryReversalCommand
                    and command.original_transaction_id == fact.original_transaction_id,
                    'inverse_command_mismatch')
                _need(command.source_document_type == 'stock_loss_disposition_reversal'
                    and command.source_document_id == str(fact.id), 'inverse_document_mismatch')
                movement_type, reverse_id = 'reversal', fact.original_transaction_id
                request_hash = posting._reversal_request_hash(actor, command)
                boundary = boundary_by_execution[fact.execution_id]
            else:
                _need(type(command) is posting.InventoryPostingCommand
                    and len(command.movements) == 1, 'execution_command_mismatch')
                if fact.predecessor_reversal_id is not None:
                    _need(command.source_document_type == 'stock_loss_correction_execution'
                        and command.source_document_id == str(fact.id), 'correction_document_mismatch')
                elif fact.disposition != 'return_to_region':
                    _need(command.source_document_type == 'stock_loss_disposition'
                        and command.source_document_id == str(fact.id), 'original_document_mismatch')
                else:
                    # The business-history adapter must additionally bind the
                    # exact persisted child order; the ledger alone cannot.
                    _need(command.source_document_type == 'stock_operation_return',
                        'original_return_document_mismatch')
                move = command.movements[0]
                _need(type(move) is posting.InventoryMovementCommand
                    and move.from_account_id == fact.source_account_id
                    and move.to_account_id == fact.target_account_id
                    and move.quantity == fact.quantity
                    and tuple(sorted(fact.serial_ids, key=str)) == move.serial_ids,
                    'execution_command_edge_mismatch')
                movement_type = {'restore_available': 'unfreeze', 'convert_used': 'status_change',
                    'convert_damaged': 'status_change', 'return_to_region': 'reserve', 'scrap': 'scrap'}[fact.disposition]
                _need(command.movement_type == movement_type, 'execution_movement_type_mismatch')
                reverse_id = None
                boundary = move.external_boundary_code
                _need((fact.disposition == 'scrap' and boundary == 'stock_operation_scrap')
                    or (fact.disposition != 'scrap' and boundary is None), 'execution_boundary_mismatch')
                boundary_by_execution[fact.id] = boundary
                request_hash = posting._posting_request_hash(actor, command)
            tx = db.get(InventoryTransaction, fact.posting_transaction_id, populate_existing=True)
            _need(tx is not None, 'ledger_transaction_missing')
            _need(tx.status == 'posted' and tx.ledger_cursor == fact.ledger_cursor
                and tx.actor_user_id == binding.actor_user_id
                and tx.idempotency_key_hash == binding.idempotency_key_hash
                and tx.request_hash == request_hash and tx.movement_type == movement_type
                and tx.reversed_transaction_id == reverse_id
                and _aware(tx.effective_at) == _aware(command.effective_at)
                and all(getattr(tx, key) == getattr(command, key) for key in (
                    'transaction_no', 'source_document_type', 'source_document_id', 'posting_key')),
                'ledger_transaction_binding_mismatch')
            moves = tuple(db.scalars(select(InventoryMovement).where(
                InventoryMovement.transaction_id == tx.id).execution_options(populate_existing=True)))
            _need(len(moves) == 1, 'ledger_movement_count_mismatch')
            movement = moves[0]
            _need(movement.id == fact.posting_movement_id and movement.line_no == 1
                and movement.from_account_id == fact.source_account_id
                and movement.to_account_id == fact.target_account_id
                and movement.quantity == fact.quantity and movement.external_boundary_code == boundary,
                'ledger_movement_binding_mismatch')
            serial_rows = tuple(db.execute(select(InventoryMovementSerial.serial_id,
                InventoryMovementSerial.transaction_id).where(
                    InventoryMovementSerial.movement_id == movement.id)))
            _need(len(serial_rows) == len(fact.serial_ids)
                and {r.serial_id for r in serial_rows} == fact.serial_ids
                and all(r.transaction_id == tx.id for r in serial_rows),
                'ledger_serial_binding_mismatch')
        # Under READ COMMITTED an inverse can commit after the reverse-link
        # query but before the last edge is read. An immutable, monotonic ledger
        # cursor makes this race observable; the caller must reread history.
        after_cursor = db.scalar(select(func.max(InventoryTransaction.ledger_cursor)))
        _need(after_cursor == before_cursor, 'ledger_changed_during_proof')
