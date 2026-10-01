"""Adapt proven correction histories to the existing per-report hold arithmetic.

Database lineage, current authority and inventory posting are separate gates.
The adapter deliberately credits only the active execution of each chain.
"""
from dataclasses import dataclass

from app.formal_services.stock_loss_holds import Hold, Release, remaining_holds

from .chain_projection import Basis, Execution, Reversal, CorrectionDecision, InvalidChain, project


@dataclass(frozen=True)
class History:
    basis: Basis
    executions: tuple[Execution, ...]
    reversals: tuple[Reversal, ...]
    decisions: tuple[CorrectionDecision, ...]


def remaining(histories: tuple[History, ...], *, through_cursor=None):
    if type(histories) is not tuple or any(type(h) is not History for h in histories):
        raise InvalidChain('immutable_histories_required')
    holds, releases, seen_movements = [], [], set()
    for history in histories:
        basis = history.basis
        state = project(basis, history.executions, history.reversals, history.decisions,
            through_cursor=through_cursor)
        for fact in (*history.executions, *history.reversals):
            if fact.posting_movement_id in seen_movements:
                raise InvalidChain('movement_shared_by_different_histories')
            seen_movements.add(fact.posting_movement_id)
        holds.append(Hold(basis.line_id, basis.operation_id, basis.frozen_account_id,
            basis.quantity, basis.serial_ids))
        if state.active_execution_id is not None:
            active = next(e for e in history.executions if e.id == state.active_execution_id)
            releases.append(Release(active.id, basis.line_id, basis.operation_id,
                active.posting_transaction_id, active.posting_movement_id,
                active.quantity, active.serial_ids))
    return remaining_holds(tuple(holds), tuple(releases))
