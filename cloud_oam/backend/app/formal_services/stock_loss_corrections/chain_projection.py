"""Project one complete loss-line correction chain from verified immutable facts.

This is an internal arithmetic/graph component, never an HTTP input or an
authorization proof. A database adapter must independently verify approvals,
exact original/inverse ledger movements, downstream compensation, audit,
notification intents and COMMIT-time authority. No posting is implemented here.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


KINDS = frozenset({'restore_available', 'convert_used', 'convert_damaged',
                   'return_to_region', 'scrap'})


class InvalidChain(ValueError):
    pass


@dataclass(frozen=True)
class Basis:
    operation_id: UUID
    line_id: UUID
    original_decision_id: UUID
    original_disposition: str
    frozen_account_id: UUID
    freeze_cursor: int
    quantity: Decimal
    serial_ids: frozenset[UUID]
    serial_managed: bool


@dataclass(frozen=True)
class Execution:
    id: UUID
    operation_id: UUID
    line_id: UUID
    decision_id: UUID
    predecessor_reversal_id: UUID | None
    disposition: str
    posting_transaction_id: UUID
    posting_movement_id: UUID
    ledger_cursor: int
    source_account_id: UUID
    target_account_id: UUID | None
    quantity: Decimal
    serial_ids: frozenset[UUID]


@dataclass(frozen=True)
class Reversal:
    id: UUID
    operation_id: UUID
    line_id: UUID
    execution_id: UUID
    posting_transaction_id: UUID
    posting_movement_id: UUID
    original_transaction_id: UUID
    ledger_cursor: int
    source_account_id: UUID | None
    target_account_id: UUID
    quantity: Decimal
    serial_ids: frozenset[UUID]


@dataclass(frozen=True)
class CorrectionDecision:
    id: UUID
    operation_id: UUID
    line_id: UUID
    original_decision_id: UUID
    reversal_id: UUID
    disposition: str


@dataclass(frozen=True)
class Projection:
    active_execution_id: UUID | None
    pending_reversal_id: UUID | None
    frozen_quantity: Decimal
    frozen_serial_ids: frozenset[UUID]
    execution_history: tuple[UUID, ...]


def _need(condition, code):
    if not condition:
        raise InvalidChain(code)


def _id(value):
    _need(type(value) is UUID and value.int != 0, 'nonzero_uuid_required')


def _cursor(value):
    _need(type(value) is int and value > 0, 'positive_cursor_required')


def _amount(value):
    _need(type(value) is Decimal and value.is_finite() and value > 0
        and value < Decimal('1000000000000000')
        and value.as_tuple().exponent >= -3, 'exact_positive_quantity_required')


def _serials(value):
    _need(type(value) is frozenset, 'immutable_serial_set_required')
    for identifier in value:
        _id(identifier)


def project(basis, executions, reversals, decisions, *, through_cursor=None):
    """Fold immutable execution/inverse pairs, retaining their original IDs.

An inverse restores the entire original frozen share. A successor may release
that share only once and needs its own decision linked to that exact inverse.
All supplied facts are validated before selecting a historical cursor. Pending
decisions do not move inventory. No closeability is inferred from this result.
"""
    _need(type(basis) is Basis, 'basis_required')
    for identifier in (basis.operation_id, basis.line_id, basis.original_decision_id,
                       basis.frozen_account_id):
        _id(identifier)
    _cursor(basis.freeze_cursor)
    _amount(basis.quantity)
    _serials(basis.serial_ids)
    _need(type(basis.serial_managed) is bool, 'tracking_required')
    _need(basis.original_disposition in KINDS, 'disposition_required')
    _need((basis.serial_managed and basis.quantity == len(basis.serial_ids))
        or (not basis.serial_managed and not basis.serial_ids), 'basis_serial_quantity_mismatch')
    if through_cursor is not None:
        _cursor(through_cursor)
        _need(through_cursor >= basis.freeze_cursor, 'cursor_precedes_freeze')
    for collection in (executions, reversals, decisions):
        _need(type(collection) is tuple, 'immutable_fact_sequence_required')

    execution_by_id, inverse_by_id, decision_by_id = {}, {}, {}
    for collection, cls, index in ((executions, Execution, execution_by_id),
            (reversals, Reversal, inverse_by_id), (decisions, CorrectionDecision, decision_by_id)):
        for fact in collection:
            _need(type(fact) is cls, 'fact_type_mismatch')
            for identifier in (fact.id, fact.operation_id, fact.line_id):
                _id(identifier)
            _need(fact.operation_id == basis.operation_id and fact.line_id == basis.line_id,
                'cross_line_fact')
            _need(fact.id not in index, 'duplicate_fact')
            index[fact.id] = fact

    inverse_for_execution, next_for_inverse = {}, {}
    for approval in decisions:
        _id(approval.original_decision_id)
        _id(approval.reversal_id)
        _need(approval.id != basis.original_decision_id
            and approval.original_decision_id == basis.original_decision_id,
            'correction_requires_distinct_original_bound_decision')
        _need(approval.reversal_id in inverse_by_id and approval.disposition in KINDS,
            'correction_decision_reference_invalid')
        # Approval history does not itself consume an inverse. The authority
        # adapter decides which immutable decision may execute; the fact graph
        # below permits at most one actual successor for each inverse.

    seen_transactions, seen_movements, seen_cursors = set(), set(), set()
    for fact in (*executions, *reversals):
        _id(fact.posting_transaction_id)
        _id(fact.posting_movement_id)
        _cursor(fact.ledger_cursor)
        _amount(fact.quantity)
        _serials(fact.serial_ids)
        _need(fact.ledger_cursor > basis.freeze_cursor, 'posting_precedes_freeze')
        _need(fact.quantity == basis.quantity and fact.serial_ids == basis.serial_ids,
            'original_share_changed')
        _need(fact.posting_transaction_id not in seen_transactions
            and fact.posting_movement_id not in seen_movements
            and fact.ledger_cursor not in seen_cursors, 'posting_reused')
        seen_transactions.add(fact.posting_transaction_id)
        seen_movements.add(fact.posting_movement_id)
        seen_cursors.add(fact.ledger_cursor)

    roots = []
    for execution in executions:
        _id(execution.decision_id)
        _id(execution.source_account_id)
        _need(execution.source_account_id == basis.frozen_account_id,
            'execution_source_not_original_frozen_account')
        _need(execution.disposition in KINDS, 'disposition_required')
        if execution.disposition == 'scrap':
            _need(execution.target_account_id is None, 'scrap_is_external_boundary')
        else:
            _id(execution.target_account_id)
            _need(execution.target_account_id != basis.frozen_account_id, 'execution_has_no_movement')
        if execution.predecessor_reversal_id is None:
            _need(execution.decision_id == basis.original_decision_id
                and execution.disposition == basis.original_disposition,
                'original_decision_mismatch')
            roots.append(execution)
        else:
            _id(execution.predecessor_reversal_id)
            inverse = inverse_by_id.get(execution.predecessor_reversal_id)
            approval = decision_by_id.get(execution.decision_id)
            _need(inverse is not None and approval is not None, 'missing_correction_predecessor')
            _need(approval.reversal_id == inverse.id and approval.disposition == execution.disposition,
                'correction_decision_mismatch')
            _need(execution.ledger_cursor > inverse.ledger_cursor, 'correction_precedes_inverse')
            _need(inverse.id not in next_for_inverse, 'correction_fork')
            next_for_inverse[inverse.id] = execution

    for inverse in reversals:
        _id(inverse.execution_id)
        _id(inverse.original_transaction_id)
        _id(inverse.target_account_id)
        execution = execution_by_id.get(inverse.execution_id)
        _need(execution is not None, 'orphan_inverse')
        _need(inverse.original_transaction_id == execution.posting_transaction_id
            and inverse.source_account_id == execution.target_account_id
            and inverse.target_account_id == execution.source_account_id,
            'inverse_does_not_reverse_exact_execution')
        _need(inverse.ledger_cursor > execution.ledger_cursor, 'inverse_precedes_execution')
        _need(execution.id not in inverse_for_execution, 'duplicate_inverse')
        inverse_for_execution[execution.id] = inverse

    _need(len(roots) == (1 if executions else 0), 'one_original_execution_required')
    active, pending, history, visited = None, None, [], set()
    current = roots[0] if roots else None
    while current is not None:
        _need(current.id not in visited, 'chain_cycle')
        visited.add(current.id)
        if through_cursor is None or current.ledger_cursor <= through_cursor:
            active, pending = current.id, None
            history.append(current.id)
        inverse = inverse_for_execution.get(current.id)
        if inverse is None:
            break
        if through_cursor is None or inverse.ledger_cursor <= through_cursor:
            active, pending = None, inverse.id
        current = next_for_inverse.get(inverse.id)
    _need(len(visited) == len(executions), 'disconnected_correction_chain')
    return Projection(active, pending, basis.quantity if active is None else Decimal(0),
        basis.serial_ids if active is None else frozenset(), tuple(history))
