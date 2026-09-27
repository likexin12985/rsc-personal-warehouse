"""Loss planning invariants; no database or network.

The test oracle is the baseline's conservation, precise custody, no self-review
and independent return/inbound facts. This is not a service/permission/PG gate.
"""
from dataclasses import replace
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from app.formal_services.stock_loss_planning import (
    Account, ContractError, Disposition, DispositionKind, FreezeLine, HeldLine,
    ReviewAuthority, TrackedQuantity, derive_progress, next_review_stage,
    plan_dispositions, plan_freeze,
)


def uid(value):
    return UUID(int=value)


OWNER, PERSON, OTHER, LOCATION, MATERIAL = (uid(i) for i in range(1, 6))
SOURCE = Account(uid(10), OWNER, PERSON, LOCATION, MATERIAL, 'new', 'available')
FROZEN = replace(SOURCE, id=uid(11), bucket='frozen')
LINE = uid(20)
SERIALS = (uid(30), uid(31))


def tracked(*, quantity='2.000', serials=SERIALS, mode='serial', scale=0, fraction=False):
    return TrackedQuantity(Decimal(quantity), mode, scale, fraction, serials)


def freeze_line(**kwargs):
    data = dict(line_id=LINE, source=SOURCE, frozen=FROZEN, selected=tracked(),
                available_quantity=Decimal('2.000'), source_serial_ids=frozenset(SERIALS))
    return FreezeLine(**(data | kwargs))


def held_line(**kwargs):
    data = dict(line_id=LINE, original=SOURCE, frozen=FROZEN, selected=tracked(),
                remaining_quantity=Decimal('2.000'), remaining_serial_ids=frozenset(SERIALS))
    return HeldLine(**(data | kwargs))


def decision(kind):
    target = None if kind == DispositionKind.SCRAP else replace(SOURCE, id=uid(12),
        bucket='return_pending' if kind == DispositionKind.RETURN else 'available',
        condition={DispositionKind.USED:'used', DispositionKind.DAMAGED:'damaged'}.get(kind,'new'))
    return Disposition(LINE, kind, '明确核实后的处置原因', target,
        return_operation_id=uid(41) if kind == DispositionKind.RETURN else None,
        scrap_operation_id=uid(42) if kind == DispositionKind.SCRAP else None)


def dispose(*, line=None, selected=None, stage='approved'):
    return plan_dispositions(approval_fact_id=uid(40), approval_stage=stage,
        disposition_fact_ids={LINE:uid(43)}, held_lines=(line or held_line(),),
        decisions=(selected or decision(DispositionKind.RESTORE),))


def assert_refused(code, action):
    with pytest.raises(ContractError) as error:
        action()
    assert error.value.code == code


def test_freeze_preserves_every_dimension_quantity_and_serial():
    original=freeze_line()
    move, = plan_freeze(requester_person_id=PERSON, lines=(original,))
    assert (move.from_account_id,move.to_account_id,move.quantity,move.serial_ids)==(
        SOURCE.id,FROZEN.id,Decimal('2.000'),SERIALS)
    assert move.movement_type=='freeze' and move.external_boundary_code is None
    assert original==freeze_line()  # A preview neither mutates nor asserts posting.


@pytest.mark.parametrize('field,value', [('owner_org_id',uid(70)),('custodian_person_id',OTHER),
    ('location_id',uid(71)),('material_id',uid(72)),('lot_id',uid(73)),('condition','used')])
def test_freeze_never_changes_ownership_custody_or_stock_dimensions(field,value):
    line=freeze_line(frozen=replace(FROZEN,**{field:value}))
    assert_refused('freeze_account_dimensions',lambda:plan_freeze(requester_person_id=PERSON,lines=(line,)))


@pytest.mark.parametrize('bucket',['reserved','outbound','transit','return_pending','frozen'])
def test_report_cannot_consume_another_business_pending_stock(bucket):
    line=freeze_line(source=replace(SOURCE,bucket=bucket))
    assert_refused('freeze_account_buckets',lambda:plan_freeze(requester_person_id=PERSON,lines=(line,)))


def test_one_insufficient_line_rejects_whole_plan():
    second=freeze_line(line_id=uid(21),source=replace(SOURCE,id=uid(14),material_id=uid(6)),
        frozen=replace(FROZEN,id=uid(15),material_id=uid(6)),
        selected=tracked(serials=(uid(32),uid(33))),source_serial_ids=frozenset((uid(32),uid(33))),
        available_quantity=Decimal('1.000'))
    assert_refused('source_insufficient',lambda:plan_freeze(requester_person_id=PERSON,lines=(freeze_line(),second)))


def test_other_custodian_and_missing_serial_are_refused():
    assert_refused('source_not_requester',lambda:plan_freeze(requester_person_id=OTHER,lines=(freeze_line(),)))
    assert_refused('serial_not_at_source',lambda:plan_freeze(requester_person_id=PERSON,
        lines=(freeze_line(source_serial_ids=frozenset((SERIALS[0],))),)))


@pytest.mark.parametrize('quantity,code',[('0','quantity_out_of_range'),('-1','quantity_out_of_range'),
    ('NaN','quantity_decimal_required'),('Infinity','quantity_decimal_required'),
    ('1000000000000000','quantity_out_of_range'),('0.0001','quantity_out_of_range')])
def test_invalid_quantity_never_reaches_a_plan(quantity,code):
    line=freeze_line(selected=tracked(quantity=quantity,serials=(),mode='none',scale=3,fraction=True))
    assert_refused(code,lambda:plan_freeze(requester_person_id=PERSON,lines=(line,)))


def test_fractional_untracked_material_and_integral_serial_policy():
    chosen=tracked(quantity='1.125',serials=(),mode='none',scale=3,fraction=True)
    move,=plan_freeze(requester_person_id=PERSON,lines=(freeze_line(selected=chosen,source_serial_ids=frozenset()),))
    assert move.quantity==Decimal('1.125') and not move.serial_ids
    assert_refused('quantity_fraction_forbidden',lambda:plan_freeze(requester_person_id=PERSON,
        lines=(freeze_line(selected=replace(chosen,allow_fraction=False)),)))
    assert_refused('serial_quantity_mismatch',lambda:plan_freeze(requester_person_id=PERSON,
        lines=(freeze_line(selected=tracked(quantity='1')),)))


@pytest.mark.parametrize('quantity,scale', [
    ('1.001', 0), ('1.210', 1), ('10.011', 2),
    ('999999999999999.001', 2),
])
def test_low_decimal_context_cannot_round_away_forbidden_fraction(quantity, scale):
    chosen = tracked(quantity=quantity, serials=(), mode='none', scale=scale, fraction=True)
    with localcontext() as context:
        context.prec = 2
        assert_refused('quantity_policy_scale', lambda: chosen.validate(None))


@pytest.mark.parametrize('quantity,scale', [
    ('2.000', 0), ('1.200', 1), ('0.010', 2), ('999999999999999.000', 0),
])
def test_low_decimal_context_preserves_allowed_padded_quantity(quantity, scale):
    chosen = tracked(quantity=quantity, serials=(), mode='none', scale=scale, fraction=True)
    with localcontext() as context:
        context.prec = 2
        assert chosen.validate(None) == ()
    assert chosen.quantity.as_tuple() == Decimal(quantity).as_tuple()


@pytest.mark.parametrize('kind',list(DispositionKind))
def test_all_five_outcomes_only_scrap_removes_managed_assets(kind):
    result,=dispose(selected=decision(kind))
    movement=result.movement
    assert movement.quantity==Decimal('2.000') and movement.serial_ids==SERIALS
    assert movement.from_account_id==FROZEN.id
    if kind==DispositionKind.SCRAP:
        assert movement.to_account_id is None and movement.movement_type=='scrap'
        assert movement.external_boundary_code=='stock_operation_scrap'
        assert result.lifecycle_after=='scrapped' and result.source_document_id==uid(42)
    else:
        assert movement.to_account_id==uid(12) and movement.external_boundary_code is None
        assert result.lifecycle_after=='active'
    assert result.return_fulfillment_required==(kind==DispositionKind.RETURN)


@pytest.mark.parametrize('stage',['awaiting_regional','awaiting_headquarters','needs_evidence','rejected'])
def test_no_disposition_before_headquarters_approval(stage):
    assert_refused('headquarters_approval_required',lambda:dispose(stage=stage))


def test_cannot_borrow_a_different_report_hold_even_with_sufficient_pooled_stock():
    assert_refused('line_hold_not_exact',lambda:dispose(line=held_line(remaining_quantity=Decimal('1.000'))))
    assert_refused('line_hold_not_exact',lambda:dispose(line=held_line(remaining_serial_ids=frozenset((uid(32),uid(33))))))


@pytest.mark.parametrize('field,value',[('owner_org_id',uid(70)),('custodian_person_id',OTHER),
    ('location_id',uid(71)),('material_id',uid(72)),('lot_id',uid(73))])
def test_disposition_cannot_transfer_custody_by_changing_target_dimensions(field,value):
    chosen=decision(DispositionKind.RESTORE)
    chosen=replace(chosen,target=replace(chosen.target,**{field:value}))
    assert_refused('disposition_account_dimensions',lambda:dispose(selected=chosen))


def test_scrap_cannot_be_a_scrapped_balance_or_omit_its_separate_document():
    assert_refused('scrap_target_invalid',lambda:dispose(selected=replace(decision(DispositionKind.SCRAP),target=SOURCE)))
    assert_refused('identifier_invalid',lambda:dispose(selected=replace(decision(DispositionKind.SCRAP),scrap_operation_id=None)))


def test_return_must_bind_a_distinct_real_return_document():
    assert_refused('identifier_invalid',lambda:dispose(selected=replace(decision(DispositionKind.RETURN),return_operation_id=None)))
    assert_refused('disposition_target_semantics',lambda:dispose(selected=replace(decision(DispositionKind.RETURN),target=SOURCE)))


def reviewer(role,person=OTHER):
    return ReviewAuthority('review-user',person,role,
        frozenset({'review_loss_regional','finalize_loss'}),frozenset({OWNER}),role=='admin')


def review(stage,action,actor):
    return next_review_stage(current_stage=stage,decision=action,actor=actor,
        requester_user_id='request-user',requester_person_id=PERSON,owner_org_id=OWNER)


def test_two_levels_have_separate_decisions_and_return_for_evidence():
    assert review('awaiting_regional','verified',reviewer('provincial_manager'))=='awaiting_headquarters'
    assert review('awaiting_regional','needs_evidence',reviewer('provincial_manager'))=='needs_evidence'
    assert review('awaiting_headquarters','request_regional_review',reviewer('admin'))=='needs_regional_review'
    assert review('awaiting_headquarters','approve',reviewer('admin'))=='approved'
    assert_refused('regional_review_forbidden',lambda:review('awaiting_regional','verified',reviewer('admin')))
    assert_refused('headquarters_review_forbidden',lambda:review('awaiting_headquarters','approve',reviewer('provincial_manager')))


@pytest.mark.parametrize('stage,action,role',[('awaiting_regional','verified','provincial_manager'),
    ('awaiting_headquarters','approve','admin')])
def test_same_person_cannot_self_approve_using_another_account(stage,action,role):
    assert_refused('self_approval_forbidden',lambda:review(stage,action,reviewer(role,PERSON)))


def test_effective_deny_and_cross_region_are_not_overridden_by_role_name():
    assert_refused('regional_review_forbidden',lambda:review('awaiting_regional','verified',
        replace(reviewer('provincial_manager'),allowed_actions=frozenset())))
    assert_refused('regional_review_forbidden',lambda:review('awaiting_regional','verified',
        replace(reviewer('provincial_manager'),scope_org_ids=frozenset({uid(99)}))))


def test_posting_does_not_close_a_return_until_physical_receiving_and_inbound_complete():
    args=dict(approval_stage='approved',expected_lines=frozenset({LINE}),posted_lines=frozenset({LINE}),
              return_lines=frozenset({LINE}),completed_return_lines=frozenset())
    result=derive_progress(**args)
    assert result.disposition_stage=='complete' and result.return_fulfillment_stage=='pending' and not result.closed
    done=derive_progress(**(args|{'completed_return_lines':frozenset({LINE})}))
    assert done.closed
    assert not derive_progress(**(args|{'completed_return_lines':frozenset({LINE}),'reversal_pending':True})).closed
    assert_refused('return_completion_without_posting',lambda:derive_progress(**(
        args|{'posted_lines':frozenset(),'completed_return_lines':frozenset({LINE})})))
