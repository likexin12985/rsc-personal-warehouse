"""Persisted, cursor-bound loss shares for one shared frozen account.

This is an internal inventory-history component. Complete business history,
current write authority and the atomic posting service remain separate. No
balance/SN projection is trusted or updated and no client supplies histories.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.inventory_models import InventoryMovement, InventoryTransaction, StockAccount
from app.stock_operation_models import StockOperationOrder, StockOperationLine, StockOperationSerial, StockLossDisposition
from app.formal_services import stock_loss_facts as submission
from app.formal_services.serial_ledger import rebuild_serial_states
from .chain_projection import InvalidChain, project
from .history_inventory import load_inventory_history


@dataclass(frozen=True)
class LineShare:
    line_id: UUID
    operation_id: UUID
    freeze_cursor: int
    original_quantity: Decimal
    original_serial_ids: frozenset[UUID]
    # Visible only after the root's immutable transaction at this cutoff.
    root_disposition_id: UUID | None
    active_execution_id: UUID | None
    pending_reversal_id: UUID | None
    frozen_quantity: Decimal
    frozen_serial_ids: frozenset[UUID]


@dataclass(frozen=True)
class HoldSnapshot:
    source_account_id: UUID
    through_cursor: int
    observed_ledger_cursor: int
    balance_quantity: Decimal
    balance_version: int
    lines: tuple[LineShare, ...]

    @property
    def held_quantity(self):
        return sum((line.frozen_quantity for line in self.lines), Decimal(0))

    def legacy_basis(self):
        """Retain old pre-correction plan bytes, never silently relabel a chain."""
        if any(line.pending_reversal_id is not None or (line.active_execution_id is not None
                and line.active_execution_id != line.root_disposition_id) for line in self.lines):
            raise InvalidChain('legacy_plan_cannot_represent_correction')
        return [dict(line_id=str(line.line_id),operation_id=str(line.operation_id),
            quantity=format(line.original_quantity,'.3f'),serial_ids=sorted(map(str,line.original_serial_ids)),
            disposition_id=str(line.active_execution_id) if line.active_execution_id else None)
            for line in self.lines]

    def plan_basis(self):
        # observed_ledger_cursor is a read-race bound, not part of historical
        # plan identity. Future unrelated transactions cannot alter this basis.
        return dict(schema_version='2.0',source_account_id=str(self.source_account_id),
            ledger_cursor=self.through_cursor,balance_quantity=format(self.balance_quantity,'.3f'),
            balance_version=self.balance_version,lines=[dict(line_id=str(line.line_id),
                operation_id=str(line.operation_id),freeze_cursor=line.freeze_cursor,
                original_quantity=format(line.original_quantity,'.3f'),
                original_serial_ids=sorted(map(str,line.original_serial_ids)),
                root_disposition_id=str(line.root_disposition_id) if line.root_disposition_id else None,
                active_execution_id=str(line.active_execution_id) if line.active_execution_id else None,
                pending_reversal_id=str(line.pending_reversal_id) if line.pending_reversal_id else None,
                frozen_quantity=format(line.frozen_quantity,'.3f'),
                frozen_serial_ids=sorted(map(str,line.frozen_serial_ids))) for line in self.lines])


def _need(condition, code):
    if not condition:
        raise InvalidChain(code)


def _cursor(db):
    return db.scalar(select(func.max(InventoryTransaction.ledger_cursor))) or 0


def _lines(db, source_id, cutoff):
    return tuple(db.scalars(select(StockOperationLine).join(StockOperationOrder,
        StockOperationOrder.id==StockOperationLine.operation_id).join(InventoryTransaction,
        InventoryTransaction.id==StockOperationOrder.posting_transaction_id).where(
            StockOperationLine.operation_type=='loss_report',StockOperationLine.reserved_account_id==source_id,
            InventoryTransaction.ledger_cursor<=cutoff).order_by(StockOperationLine.id)
        .execution_options(populate_existing=True)))


def read_hold_snapshot(db, *, source_account_id, through_cursor=None):
    _need(type(source_account_id) is UUID and source_account_id.int!=0,'frozen_account_id_required')
    _need(through_cursor is None or (type(through_cursor) is int and through_cursor>=0),'invalid_hold_cursor')
    with db.no_autoflush:
        before=_cursor(db);cutoff=before if through_cursor is None else through_cursor
        _need(cutoff<=before,'hold_cursor_ahead_of_ledger')
        source=db.get(StockAccount,source_account_id,populate_existing=True)
        _need(source is not None and source.availability_bucket=='frozen','frozen_account_required')
        originals=_lines(db,source.id,cutoff);line_ids=tuple(line.id for line in originals)
        shares=[];held_serials=set()
        for line in originals:
            order=db.get(StockOperationOrder,line.operation_id,populate_existing=True)
            _need(order is not None and order.operation_type=='loss_report','original_loss_order_required')
            submission.submission_evidence(db,order=order)
            freeze=db.get(InventoryTransaction,order.posting_transaction_id,populate_existing=True)
            serials=frozenset(db.scalars(select(StockOperationSerial.serial_id).where(StockOperationSerial.line_id==line.id)))
            roots=tuple(db.scalars(select(StockLossDisposition).where(StockLossDisposition.line_id==line.id)
                .execution_options(populate_existing=True)))
            _need(len(roots)<=1,'multiple_disposition_roots')
            visible_root=active=pending=None;quantity=line.quantity;remaining_serials=serials
            if roots:
                root=roots[0];loaded=load_inventory_history(db,root_disposition_id=root.id)
                _need(loaded.observed_ledger_cursor==before,'hold_history_changed_during_read')
                history=loaded.history
                _need(history.basis.frozen_account_id==source.id,'hold_root_account_mismatch')
                projected=project(history.basis,history.executions,history.reversals,history.decisions,
                    through_cursor=cutoff)
                visible_root=root.id if root.id in projected.execution_history else None
                active=projected.active_execution_id;pending=projected.pending_reversal_id
                quantity=projected.frozen_quantity;remaining_serials=projected.frozen_serial_ids
            _need(not held_serials.intersection(remaining_serials),'serial_held_by_multiple_losses')
            held_serials.update(remaining_serials)
            shares.append(LineShare(line.id,order.id,freeze.ledger_cursor,line.quantity,serials,
                visible_root,active,pending,quantity,remaining_serials))
        # Reconstruct the same account version as the posting projection, from
        # every immutable stock transaction, including non-loss frozen stock.
        balance=Decimal(0);transactions=set()
        for movement in db.scalars(select(InventoryMovement).join(InventoryTransaction,
                InventoryTransaction.id==InventoryMovement.transaction_id).where(
                    InventoryTransaction.status=='posted',InventoryTransaction.ledger_cursor<=cutoff,
                    (InventoryMovement.from_account_id==source.id)|(InventoryMovement.to_account_id==source.id))):
            balance+=movement.quantity if movement.to_account_id==source.id else -movement.quantity
            transactions.add(movement.transaction_id)
        result=HoldSnapshot(source.id,cutoff,before,balance,len(transactions),tuple(shares))
        _need(balance>=0 and result.held_quantity<=balance,'other_operation_consumed_loss_hold')
        states=rebuild_serial_states(db,held_serials,through_cursor=cutoff)
        _need(all(serial in states and states[serial].stock_account_id==source.id
            and states[serial].lifecycle_status=='active' for serial in held_serials),'other_operation_moved_loss_serial')
        _need(_cursor(db)==before and tuple(line.id for line in _lines(db,source.id,cutoff))==line_ids,
            'hold_history_changed_during_read')
        return result
