"""Account arithmetic retaining each loss report line's frozen allocation.

The adapter must load verified immutable loss lines, same-document dispositions
and their exact posting/SN links under the ledger lock. These DTOs are not HTTP
inputs. Passing this check does not prove authorization, posting or DB guards.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from .stock_loss_planning import ContractError, require_id, require_quantity


@dataclass(frozen=True, slots=True)
class Hold:
    line_id: UUID
    operation_id: UUID
    account_id: UUID
    quantity: Decimal
    serial_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class Release:
    id: UUID
    line_id: UUID
    operation_id: UUID
    posting_transaction_id: UUID
    posting_movement_id: UUID
    quantity: Decimal
    serial_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class Remainder:
    line_id: UUID
    account_id: UUID
    quantity: Decimal
    serial_ids: frozenset[UUID]


def _serial_quantity(quantity, serial_ids):
    if not isinstance(serial_ids, frozenset):
        raise ContractError('hold_serial_set_required')
    for serial in serial_ids:
        require_id(serial)
    if serial_ids and quantity != Decimal(len(serial_ids)):
        raise ContractError('hold_serial_quantity_mismatch')


def remaining_holds(holds: tuple[Hold, ...], releases: tuple[Release, ...]):
    """Retain each original line identity while subtracting exact releases."""
    originals, released_quantity, released_serials = {}, {}, {}
    for hold in holds:
        for identifier in (hold.line_id,hold.operation_id,hold.account_id):
            require_id(identifier)
        require_quantity(hold.quantity)
        _serial_quantity(hold.quantity,hold.serial_ids)
        if hold.line_id in originals:
            raise ContractError('hold_line_duplicate')
        originals[hold.line_id]=hold
        released_quantity[hold.line_id]=Decimal(0)
        released_serials[hold.line_id]=set()
    seen_releases, seen_movements = set(), set()
    for release in releases:
        for identifier in (release.id,release.line_id,release.operation_id,
                           release.posting_transaction_id,release.posting_movement_id):
            require_id(identifier)
        require_quantity(release.quantity)
        _serial_quantity(release.quantity,release.serial_ids)
        if release.id in seen_releases:
            raise ContractError('hold_release_duplicate')
        seen_releases.add(release.id)
        # One posting transaction may contain several legitimate releases, but
        # its same movement cannot be credited to two report lines.
        if release.posting_movement_id in seen_movements:
            raise ContractError('hold_posting_movement_reused')
        seen_movements.add(release.posting_movement_id)
        hold=originals.get(release.line_id)
        if hold is None or hold.operation_id != release.operation_id:
            raise ContractError('hold_release_original_mismatch')
        if bool(hold.serial_ids) != bool(release.serial_ids):
            raise ContractError('hold_release_tracking_mismatch')
        if not release.serial_ids <= hold.serial_ids:
            raise ContractError('hold_release_serial_mismatch')
        if release.serial_ids & released_serials[hold.line_id]:
            raise ContractError('hold_serial_released_twice')
        released_serials[hold.line_id].update(release.serial_ids)
        released_quantity[hold.line_id]+=release.quantity
        if released_quantity[hold.line_id] > hold.quantity:
            raise ContractError('hold_release_exceeds_original')
    return tuple(Remainder(hold.line_id,hold.account_id,hold.quantity-released_quantity[hold.line_id],
                          hold.serial_ids-released_serials[hold.line_id])
                 for hold in sorted(originals.values(),key=lambda row:str(row.line_id)))


def assert_holds_preserved(remainders: tuple[Remainder, ...], *,
                           balances: dict[UUID,Decimal], serial_positions: dict[UUID,UUID|None]):
    """Other documents may spend only the unencumbered account remainder.

    Quantity checks alone cannot protect an exact SN: an unrelated serial with
    the same SKU is not a replacement for a report's held serial.
    """
    quantities, serial_owners, lines = {}, {}, set()
    for hold in remainders:
        require_id(hold.line_id);require_id(hold.account_id)
        require_quantity(hold.quantity,positive=False)
        _serial_quantity(hold.quantity,hold.serial_ids)
        if hold.line_id in lines:
            raise ContractError('hold_line_duplicate')
        lines.add(hold.line_id)
        quantities[hold.account_id]=quantities.get(hold.account_id,Decimal(0))+hold.quantity
        require_quantity(quantities[hold.account_id],positive=False)
        for serial_id in hold.serial_ids:
            if serial_id in serial_owners:
                raise ContractError('serial_held_by_multiple_reports')
            serial_owners[serial_id]=hold.account_id
    for account_id, quantity in quantities.items():
        if account_id not in balances:
            raise ContractError('held_account_snapshot_missing')
        require_quantity(balances[account_id],positive=False)
        if balances[account_id] < quantity:
            raise ContractError('other_operation_consumed_report_hold')
    if any(serial_positions.get(serial) != account for serial,account in serial_owners.items()):
        raise ContractError('other_operation_moved_report_serial')
    return quantities
