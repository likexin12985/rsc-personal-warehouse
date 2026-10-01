"""Receipt damage is a subset, never an additional incoming quantity."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.formal_services.stock_return_inbound_quality import (
    accepted_parts, build_quality_inbound_command, ReturnInboundContractError,
)
from test_stock_return_inbound_contract import ident, line


@pytest.mark.parametrize('condition', ['new', 'used', 'damaged'])
@pytest.mark.parametrize('damaged', ['0', '.125', '.375'])
def test_quantity_partitions_conserve_acceptance(condition, damaged):
    parts = accepted_parts(source_condition=condition, accepted_quantity=Decimal('.375'),
        damaged_quantity=Decimal(damaged), tracked=False)
    assert sum(part.quantity for part in parts) == Decimal('.375')
    assert all(part.quantity > 0 and part.serial_ids == () for part in parts)
    expected = (Decimal('.375') if condition == 'damaged' else Decimal(damaged))
    assert sum(part.quantity for part in parts if part.condition_code == 'damaged') == expected
    assert len({part.condition_code for part in parts}) == len(parts)


@pytest.mark.parametrize('condition', ['new', 'used', 'damaged'])
@pytest.mark.parametrize('count', [0, 1, 3])
def test_sn_partition_matches_exact_damage_without_duplication(condition, count):
    ids = (ident(81), ident(79), ident(80))
    damaged = ids[:count]
    parts = accepted_parts(source_condition=condition, accepted_quantity=Decimal(3),
        damaged_quantity=Decimal(count), accepted_serial_ids=ids,
        damaged_serial_ids=damaged, tracked=True)
    assert sum(part.quantity for part in parts) == 3
    flattened = tuple(sn for part in parts for sn in part.serial_ids)
    assert len(flattened) == len(set(flattened)) == 3
    assert set(flattened) == set(ids)
    for part in parts:
        assert len(part.serial_ids) == part.quantity
        if part.condition_code == 'damaged':
            assert set(part.serial_ids) == set(ids if condition == 'damaged' else damaged)


@pytest.mark.parametrize('patch', [
    {'accepted_quantity': 1.0}, {'accepted_quantity': True},
    {'accepted_quantity': Decimal('NaN')}, {'accepted_quantity': Decimal('Infinity')},
    {'accepted_quantity': Decimal('0')}, {'accepted_quantity': Decimal('-1')},
    {'accepted_quantity': Decimal('1.0001')}, {'accepted_quantity': Decimal('1000000000000000')},
    {'damaged_quantity': Decimal('-1')}, {'damaged_quantity': Decimal('2')},
    {'damaged_quantity': Decimal('NaN')}, {'source_condition': 'scrapped'}, {'tracked': 1},
    {'accepted_serial_ids': (ident(1),)}, {'damaged_serial_ids': (ident(1),)},
    {'accepted_serial_ids': (UUID(int=0),)}, {'accepted_serial_ids': [ident(1)]},
])
def test_invalid_quantities_and_tracking_do_not_form_parts(patch):
    args = dict(source_condition='used', accepted_quantity=Decimal(1),
        damaged_quantity=Decimal(0), tracked=False)
    with pytest.raises(ReturnInboundContractError):
        accepted_parts(**(args | patch))


@pytest.mark.parametrize('patch', [
    {'accepted_serial_ids': (ident(1), ident(1))},
    {'damaged_serial_ids': (ident(3),)}, {'damaged_serial_ids': ()},
    {'accepted_quantity': Decimal('1.5')}, {'damaged_quantity': Decimal('.5')},
])
def test_serial_counts_and_damage_membership_must_match(patch):
    args = dict(source_condition='used', accepted_quantity=Decimal(2), damaged_quantity=Decimal(1),
        accepted_serial_ids=(ident(1), ident(2)), damaged_serial_ids=(ident(1),), tracked=True)
    with pytest.raises(ReturnInboundContractError):
        accepted_parts(**(args | patch))


def build(*lines):
    return build_quality_inbound_command(receipt_id=ident(9),
        effective_at=datetime(2026, 9, 13, tzinfo=timezone.utc), lines=tuple(lines))


def pair():
    normal = line(serial_ids=(ident(71),))
    damaged = replace(normal, condition_code='damaged', target_account_id=ident(202), serial_ids=(ident(72),))
    return normal, damaged


def test_one_receipt_line_creates_two_movements_in_one_transaction():
    normal, damaged = pair()
    command = build(normal, damaged)
    assert len(command.movements) == 2
    assert sum(m.quantity for m in command.movements) == 2
    assert {m.from_account_id for m in command.movements} == {normal.source_account_id}
    assert {m.to_account_id for m in command.movements} == {normal.target_account_id, damaged.target_account_id}
    assert command.posting_key == f'stock-return-receipt-inbound:{ident(9)}'


@pytest.mark.parametrize('patch', [
    {'condition_code': 'used'}, {'source_account_id': ident(888)}, {'material_id': ident(888)},
    {'lot_id': ident(888)}, {'target_account_id': ident(201)}, {'serial_ids': (ident(71),)},
    {'accepted_quantity': True}, {'accepted_quantity': 1.0},
])
def test_duplicate_or_rebound_partition_is_rejected(patch):
    normal, damaged = pair()
    with pytest.raises(ReturnInboundContractError):
        build(normal, replace(damaged, **patch))


def test_canonical_receipt_alias_cannot_evade_duplicate_detection():
    normal, _ = pair()
    with pytest.raises(ReturnInboundContractError):
        build(normal, replace(normal, receipt_line_id=normal.receipt_line_id.hex))


def test_separate_receipts_may_share_an_exact_target_but_not_a_serial():
    a = line()
    b = replace(a, receipt_line_id=ident(2), source_account_id=ident(102))
    assert len(build(a, b).movements) == 2
    with pytest.raises(ReturnInboundContractError):
        build(replace(a, serial_ids=(ident(71),)), replace(b, serial_ids=(ident(71),)))
