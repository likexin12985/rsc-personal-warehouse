"""Exercise every order-preserving interleaving, using an independent budget oracle.

This is a deterministic event-fold check, not a concurrent PostgreSQL test.
It intentionally includes histories whose *final* quantities fit but whose
earlier prefixes overclaim; a later release must not make those histories valid.
"""
from dataclasses import replace
from decimal import Decimal

import pytest

from app.formal_services.stock_loss_planning import ContractError
from app.formal_services.stock_loss_corrections.return_condition_projection import project
from test_return_condition_projection import action, approved, basis, claim, settle, uid


def weave(left, right):
    if not left:
        yield right
    elif not right:
        yield left
    else:
        for rest in weave(left[1:], right):
            yield (left[0], *rest)
        for rest in weave(left, right[1:]):
            yield (right[0], *rest)


@pytest.mark.parametrize('serial', [False, True])
def test_every_interleaving_of_cancellation_and_second_correction(serial):
    b = basis(serial)
    # Each case individually fits, but their combined claims do not.
    a = claim(b, case=100, amount='2' if serial else '.250',
              sn=b.affected.serial_ids[:2] if serial else ())
    w = action(a, 'withdraw', 2)
    left = (('claim-a', a), ('withdraw', w), ('release', settle(a, w, 3)))
    c = claim(b, case=101, amount='2' if serial else '.250',
              sn=b.affected.serial_ids[1:] if serial else ())
    r, h = approved(c)
    right = (('claim-c', c), ('region', r), ('hq', h), ('execute', settle(c, h, 4, execute=True)))
    valid = invalid = 0
    for order in weave(left, right):
        ids = {name: uid(5000 + i) for i, (name, _) in enumerate(order, 1)}
        events, occupied, overclaimed = [], 0, False
        for i, (name, event) in enumerate(order, 1):
            posting = event.posting
            if posting is not None:
                posting = replace(posting, transaction_id=uid(6000 + i),
                    movement_id=uid(7000 + i), ledger_cursor=i + 1)
            event = replace(event, id=ids[name], sequence=i, posting=posting)
            if name == 'release':
                event = replace(event, decision_id=ids['withdraw'])
            elif name == 'execute':
                event = replace(event, decision_id=ids['hq'])
            events.append(event)
            # Integer portions: each claim uses 2 of 3 available shares.
            occupied += 2 if name.startswith('claim') else -2 if name == 'release' else 0
            overclaimed |= occupied > 3
        if overclaimed:
            invalid += 1
            with pytest.raises(ContractError) as error:
                project(b, tuple(events))
            assert error.value.code == ('return_condition_serial_already_claimed' if serial
                                       else 'return_condition_exception_overclaimed')
        else:
            valid += 1
            result = project(b, tuple(events))
            assert result.held_quantity == 0
            assert result.corrected_quantity == Decimal('2' if serial else '.250')
            assert result.unclaimed_quantity == Decimal('1' if serial else '.125')
    assert valid == 1 and invalid == 34  # C(7,3): no valid overlap before release.
