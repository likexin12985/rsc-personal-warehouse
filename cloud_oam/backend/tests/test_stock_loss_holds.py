"""Cross-report arithmetic probes; database lineage/authority not substituted."""
from dataclasses import replace
from decimal import Decimal as D
from uuid import UUID
import pytest
from app.formal_services.stock_loss_planning import ContractError
from app.formal_services.stock_loss_holds import Hold, Release, assert_holds_preserved, remaining_holds


def uid(value):return UUID(int=value)
A=Hold(uid(1),uid(11),uid(21),D('2'),frozenset({uid(31),uid(32)}))
B=Hold(uid(2),uid(12),uid(21),D('1'),frozenset({uid(33)}))


def release(**kwargs):
    return replace(Release(uid(41),A.line_id,A.operation_id,uid(51),uid(61),D('2'),A.serial_ids),**kwargs)


def reject(code,action):
    with pytest.raises(ContractError) as error:action()
    assert error.value.code==code


def test_same_account_keeps_each_report_hold_and_allows_only_unencumbered_stock():
    remaining=remaining_holds((A,B),())
    assert [(h.line_id,h.quantity) for h in remaining]==[(A.line_id,D('2')),(B.line_id,D('1'))]
    totals=assert_holds_preserved(remaining,balances={A.account_id:D('4')},
        serial_positions={serial:A.account_id for serial in A.serial_ids|B.serial_ids})
    assert totals=={A.account_id:D('3')}
    reject('other_operation_consumed_report_hold',lambda:assert_holds_preserved(remaining,
        balances={A.account_id:D('2')},serial_positions={serial:A.account_id for serial in A.serial_ids|B.serial_ids}))


def test_releasing_a_cannot_release_b_even_when_account_total_looks_valid():
    remaining=remaining_holds((A,B),(release(),))
    assert remaining[0].quantity==0 and remaining[1].quantity==1
    assert_holds_preserved(remaining,balances={A.account_id:D('1')},serial_positions={uid(33):A.account_id})
    reject('other_operation_moved_report_serial',lambda:assert_holds_preserved(remaining,
        balances={A.account_id:D('1')},serial_positions={uid(31):A.account_id,uid(33):uid(22)}))


def test_partial_releases_are_exact_quantities_and_serials():
    remaining=remaining_holds((A,B),(release(quantity=D('1'),serial_ids=frozenset({uid(31)})),))
    assert remaining[0].quantity==1 and remaining[0].serial_ids==frozenset({uid(32)})
    assert_holds_preserved(remaining,balances={A.account_id:D('2')},
        serial_positions={uid(32):A.account_id,uid(33):A.account_id})


@pytest.mark.parametrize('bad',[
    release(line_id=B.line_id),release(operation_id=B.operation_id),release(line_id=uid(99)),
])
def test_a_release_cannot_be_relabelled_as_another_report(bad):
    reject('hold_release_original_mismatch',lambda:remaining_holds((A,B),(bad,)))


def test_repeated_release_and_repeated_serial_release_are_rejected():
    reject('hold_release_duplicate',lambda:remaining_holds((A,),(release(),release())))
    part=release(quantity=D('1'),serial_ids=frozenset({uid(31)}))
    reject('hold_serial_released_twice',lambda:remaining_holds((A,),(
        part,replace(part,id=uid(42),posting_movement_id=uid(62)))))
    reject('hold_release_serial_mismatch',lambda:remaining_holds((A,),(
        release(quantity=D('1'),serial_ids=B.serial_ids),)))


def test_same_serial_cannot_be_held_by_two_active_report_lines():
    b=replace(B,serial_ids=frozenset({uid(31)}))
    reject('serial_held_by_multiple_reports',lambda:assert_holds_preserved(remaining_holds((A,b),()),
        balances={A.account_id:D('3')},serial_positions={serial:A.account_id for serial in A.serial_ids}))


def test_missing_account_or_sn_snapshot_is_unknown_not_zero_or_released():
    remaining=remaining_holds((A,),())
    reject('held_account_snapshot_missing',lambda:assert_holds_preserved(remaining,balances={},serial_positions={}))
    reject('other_operation_moved_report_serial',lambda:assert_holds_preserved(remaining,
        balances={A.account_id:D('2')},serial_positions={}))


def test_nonserial_fractional_freeze_cannot_be_overreleased():
    a=replace(A,quantity=D('1.125'),serial_ids=frozenset())
    part=release(quantity=D('.125'),serial_ids=frozenset())
    remaining=remaining_holds((a,),(part,))
    assert remaining[0].quantity==D('1.000') and not remaining[0].serial_ids
    reject('hold_release_exceeds_original',lambda:remaining_holds((a,),(
        release(quantity=D('1.126'),serial_ids=frozenset()),)))


def test_serial_hold_cannot_use_quantity_only_release():
    reject('hold_release_tracking_mismatch',lambda:remaining_holds((A,),(
        release(serial_ids=frozenset()),)))


def test_one_posted_movement_cannot_release_two_reports():
    first = replace(A, quantity=D('1'), serial_ids=frozenset())
    second = replace(B, serial_ids=frozenset())
    movement = uid(61)
    first_release = Release(uid(41), first.line_id, first.operation_id, uid(51),
                            movement, D('1'), frozenset())
    second_release = Release(uid(42), second.line_id, second.operation_id, uid(51),
                             movement, D('1'), frozenset())
    reject('hold_posting_movement_reused', lambda: remaining_holds(
        (first, second), (first_release, second_release)))


def test_one_transaction_can_release_two_lines_with_distinct_movements():
    first = replace(A, quantity=D('1'), serial_ids=frozenset())
    second = replace(B, serial_ids=frozenset())
    releases = (
        Release(uid(41), first.line_id, first.operation_id, uid(51), uid(61), D('1'), frozenset()),
        Release(uid(42), second.line_id, second.operation_id, uid(51), uid(62), D('1'), frozenset()),
    )
    assert all(row.quantity == 0 for row in remaining_holds((first, second), releases))
