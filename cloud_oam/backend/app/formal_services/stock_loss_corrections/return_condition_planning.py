"""Shape-only movement plans. Live stock and authority remain adapter-owned."""
from decimal import Decimal
from uuid import UUID

from app.formal_services.stock_loss_planning import (
    Account, ContractError, FreezeLine, MovementOutline, TrackedQuantity,
    plan_freeze, require_quantity,
)
from .return_condition_contracts import Basis


def need(value, code):
    if not value:
        raise ContractError('return_condition_' + code)


def identifier(value):
    need(type(value) is UUID and value.int != 0, 'identifier_invalid')


def integer(value):
    need(type(value) is int and value > 0, 'sequence_invalid')


def serials(value):
    need(type(value) is tuple, 'serial_tuple_required')
    for item in value:
        identifier(item)
    need(len(set(value)) == len(value), 'duplicate_serial')
    return frozenset(value)


def units(value):
    """Integer thousandths without ambient Decimal rounding/traps."""
    need(type(value) is Decimal, 'decimal_required')
    require_quantity(value, positive=False)
    _, digits, exponent = value.as_tuple()
    coefficient = int(''.join(map(str, digits)))
    return coefficient * 10 ** (exponent + 3) if coefficient else 0


def quantity(value):
    return Decimal(f'{value // 1000}.{value % 1000:03d}')


def validate_basis(basis):
    need(type(basis) is Basis, 'basis_required')
    for item in (basis.root_disposition_id, basis.inbound_line_id,
                 basis.original_transaction_id, basis.original_movement_id):
        identifier(item)
    integer(basis.original_ledger_cursor)
    need(type(basis.source) is Account and type(basis.affected) is TrackedQuantity,
         'source_contract_required')
    basis.source.dimensions()
    need(basis.source.condition in {'new', 'used'} and basis.source.bucket == 'available',
         'original_condition_invalid')
    serials(basis.affected.serial_ids)
    basis.affected.validate(basis.source.lot_id)
    units(basis.affected.quantity)


def freeze_outline(basis, *, case_id, requester_person_id, selected, frozen):
    """Only a subset/shape check; this does not establish remaining case budget."""
    validate_basis(basis)
    identifier(case_id)
    identifier(requester_person_id)
    need(type(selected) is TrackedQuantity and type(frozen) is Account, 'claim_contract_required')
    need((selected.tracking_mode, selected.quantity_scale, selected.allow_fraction) ==
         (basis.affected.tracking_mode, basis.affected.quantity_scale, basis.affected.allow_fraction),
         'claim_policy_changed')
    units(selected.quantity)
    serials(selected.serial_ids)
    return plan_freeze(requester_person_id=requester_person_id, lines=(FreezeLine(
        case_id, basis.source, frozen, selected, basis.affected.quantity,
        frozenset(basis.affected.serial_ids)),))[0]


def settlement_outline(basis, *, claim, target, execute):
    freeze_outline(basis, case_id=claim.case_id, requester_person_id=claim.requester_person_id,
                   selected=claim.selected, frozen=claim.frozen)
    need(type(execute) is bool and type(target) is Account, 'settlement_contract_required')
    need(target.dimensions() == basis.source.dimensions() and target.bucket == 'available',
         'settlement_dimensions_changed')
    if execute:
        need(target.condition == 'damaged' and target.id not in {basis.source.id, claim.frozen.id},
             'correction_target_invalid')
    else:
        need(target == basis.source, 'release_not_original_account')
    return MovementOutline(claim.case_id, 'status_change' if execute else 'unfreeze',
        claim.frozen.id, target.id, claim.selected.quantity,
        tuple(sorted(claim.selected.serial_ids, key=str)))
