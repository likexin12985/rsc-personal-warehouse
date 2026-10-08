"""Independent conservation and approval-order checks; no DB/HTTP proof."""
from dataclasses import replace
from decimal import Decimal, localcontext, Inexact, Rounded
from itertools import permutations
from uuid import UUID

import pytest

from app.formal_services.stock_loss_planning import Account, ContractError, TrackedQuantity
from app.formal_services.stock_loss_corrections.return_condition_contracts import (
    Action, Basis, Claim, Posting,
)
from app.formal_services.stock_loss_corrections.return_condition_projection import project


def uid(n):
    return UUID(int=n)


SOURCE = Account(uid(1), uid(2), uid(3), uid(4), uid(5), 'new', 'available')
FROZEN = replace(SOURCE, id=uid(6), bucket='frozen')
DAMAGED = replace(SOURCE, id=uid(7), condition='damaged')
SN = tuple(uid(n) for n in (10, 11, 12))


def basis(serial=False, lot=False):
    source = replace(SOURCE, lot_id=uid(8) if lot else None)
    mode = ('lot_and_serial' if lot else 'serial') if serial else ('lot' if lot else 'none')
    selected = TrackedQuantity(Decimal('3' if serial else '.375'), mode,
        0 if serial else 3, not serial, SN if serial else ())
    return Basis(uid(20), uid(21), uid(23), uid(22), 1, source, selected)


def claim(b, *, sequence=1, case=100, amount='.125', sn=(), cursor=None):
    chosen = replace(b.affected, quantity=Decimal(amount), serial_ids=sn)
    frozen = replace(b.source, id=FROZEN.id, bucket='frozen')
    movement = Posting(uid(1000 + sequence), uid(2000 + sequence), cursor or sequence + 1,
        b.source.id, frozen.id, chosen.quantity, sn, 'freeze')
    return Claim(uid(3000 + sequence), uid(case), sequence, b.inbound_line_id,
                 'requester', SOURCE.custodian_person_id, chosen, frozen, movement,
                 '原破损件成色误入，申请实物核验纠正', (uid(4000 + sequence),))


def action(c, kind, sequence, *, actor=None, decision=None, posting=None, target=None):
    default = ('requester', SOURCE.custodian_person_id) if kind in {
        'withdraw', 'supplement', 'execute', 'release'} else (
        ('regional', uid(50)) if kind in {'verify_region', 'reject_region', 'return_evidence'}
        else ('headquarters', uid(51)))
    user, person = actor or default
    return Action(uid(3000 + sequence), c.case_id, sequence, kind, user, person,
        '核实原始异常与本次物资状态', (uid(4000 + sequence),), decision, posting, target)


def approved(c, *, start=2):
    return (action(c, 'verify_region', start), action(c, 'approve_hq', start + 1))


def settle(c, decision, sequence, *, execute=False):
    target = replace(c.frozen, id=DAMAGED.id, bucket='available', condition='damaged') if execute else replace(
        c.frozen, id=SOURCE.id, bucket='available')
    p = Posting(uid(1000 + sequence), uid(2000 + sequence), sequence + 1,
        c.frozen.id, target.id, c.selected.quantity, c.selected.serial_ids,
        'status_change' if execute else 'unfreeze')
    return action(c, 'execute' if execute else 'release', sequence, decision=decision.id,
                  posting=p, target=target)


def refused(b, events, code):
    with pytest.raises(ContractError) as error:
        project(b, events)
    assert error.value.code == code


@pytest.mark.parametrize('serial,lot', [(False, False), (True, False), (False, True), (True, True)])
def test_two_partial_corrections_preserve_dimensions_and_original_history(serial, lot):
    b = basis(serial, lot)
    c = claim(b, amount='1' if serial else '.125', sn=SN[:1] if serial else ())
    r, h = approved(c)
    first = settle(c, h, 4, execute=True)
    d = claim(b, sequence=5, case=101, amount='2' if serial else '.250', sn=SN[1:] if serial else ())
    r2, h2 = approved(d, start=6)
    events = (c, r, h, first, d, r2, h2, settle(d, h2, 8, execute=True))
    snapshot = repr((b, events))
    result = project(b, events)
    assert result.corrected_quantity == b.affected.quantity
    assert result.held_quantity == result.unclaimed_quantity == Decimal(0)
    assert result.corrected_serial_ids == frozenset(b.affected.serial_ids)
    assert [s.status for s in result.cases] == ['executed', 'executed']
    assert repr((b, events)) == snapshot
    for e in (events[3], events[-1]):
        assert e.target.dimensions() == b.source.dimensions()
        assert e.target.condition == 'damaged' and e.posting.to_account_id is not None


@pytest.mark.parametrize('kind', ['reject_region', 'withdraw'])
def test_decision_alone_never_releases_frozen_share(kind):
    b = basis()
    c = claim(b, amount='.375')
    decision = action(c, kind, 2)
    result = project(b, (c, decision))
    assert result.held_quantity == Decimal('.375') and result.unclaimed_quantity == 0
    d = claim(b, sequence=3, case=101)
    refused(b, (c, decision, d), 'return_condition_exception_overclaimed')
    release = settle(c, decision, 3)
    d = claim(b, sequence=4, case=101)
    result = project(b, (c, decision, release, d))
    assert result.held_quantity == Decimal('.125') and result.unclaimed_quantity == Decimal('.250')
    assert result.corrected_quantity == 0


def test_later_release_cannot_hide_an_earlier_overclaim():
    b = basis()
    c = claim(b, amount='.375')
    decision = action(c, 'withdraw', 2)
    d = claim(b, sequence=3, case=101)
    refused(b, (c, decision, d, settle(c, decision, 4)), 'return_condition_exception_overclaimed')


def test_executed_share_is_permanently_consumed_not_reopened_by_available_stock():
    b = basis()
    c = claim(b, amount='.375')
    r, h = approved(c)
    e = settle(c, h, 4, execute=True)
    refused(b, (c, r, h, e, claim(b, sequence=5, case=101)), 'return_condition_exception_overclaimed')
    refused(b, (c, r, h, e, action(c, 'withdraw', 5)), 'return_condition_withdraw_stage_conflict')


def test_overlapping_sn_is_rejected_even_when_total_quantity_would_fit():
    b = basis(True)
    c = claim(b, amount='1', sn=SN[:1])
    d = claim(b, sequence=2, case=101, amount='1', sn=SN[:1])
    refused(b, (c, d), 'return_condition_serial_already_claimed')
    r, h = approved(c)
    e = settle(c, h, 4, execute=True)
    d = claim(b, sequence=5, case=101, amount='1', sn=SN[:1])
    refused(b, (c, r, h, e, d), 'return_condition_serial_already_claimed')


def test_released_sn_can_be_claimed_by_new_case_but_other_sn_never_substituted():
    b = basis(True)
    c = claim(b, amount='1', sn=SN[:1])
    w = action(c, 'withdraw', 2)
    d = claim(b, sequence=4, case=101, amount='1', sn=SN[:1])
    result = project(b, (c, w, settle(c, w, 3), d))
    assert result.held_serial_ids == frozenset(SN[:1])
    assert result.unclaimed_serial_ids == frozenset(SN[1:])
    refused(b, (claim(b, amount='1', sn=(uid(999),)),), 'serial_not_at_source')


@pytest.mark.parametrize('actor', [('requester', uid(99)), ('other-login', SOURCE.custodian_person_id)])
def test_self_review_cannot_be_bypassed_with_another_user_or_person(actor):
    b = basis()
    c = claim(b)
    refused(b, (c, action(c, 'verify_region', 2, actor=actor)), 'return_condition_self_review_forbidden')


def test_headquarters_review_is_independent_and_requires_new_regional_review_after_rework():
    b = basis()
    c = claim(b)
    r, h = approved(c)
    refused(b, (c, r, replace(h, actor_user_id=r.actor_user_id)),
            'return_condition_independent_headquarters_review_required')
    back = action(c, 'return_region', 3)
    refused(b, (c, r, back, action(c, 'approve_hq', 4)), 'return_condition_headquarters_stage_conflict')
    r2, h2 = approved(c, start=4)
    result = project(b, (c, r, back, r2, h2))
    assert result.cases[0].regional_decision_id == r2.id
    assert result.cases[0].terminal_decision_id == h2.id
    assert result.held_quantity == c.selected.quantity  # approval does not move inventory


def test_supplement_requires_applicant_evidence_then_restarts_regional_review():
    b = basis()
    c = claim(b)
    back = action(c, 'return_evidence', 2)
    supplement = action(c, 'supplement', 3)
    refused(b, (c, back, replace(supplement, evidence_file_ids=())),
            'return_condition_physical_evidence_required')
    refused(b, (c, back, action(c, 'verify_region', 3)), 'return_condition_regional_stage_conflict')
    result = project(b, (c, back, supplement))
    assert result.cases[0].status == 'awaiting_regional'
    assert result.cases[0].regional_decision_id is None


def test_approved_case_needs_explicit_cancellation_decision_and_exact_release():
    b = basis()
    c = claim(b)
    r, h = approved(c)
    refused(b, (c, r, h, action(c, 'withdraw', 4)), 'return_condition_withdraw_stage_conflict')
    cancel = action(c, 'cancel_approved', 4)
    refused(b, (c, r, h, cancel, settle(c, h, 5, execute=True)), 'return_condition_decision_binding_invalid')
    result = project(b, (c, r, h, cancel))
    assert result.held_quantity == c.selected.quantity
    result = project(b, (c, r, h, cancel, settle(c, cancel, 5)))
    assert result.held_quantity == result.corrected_quantity == 0
    assert result.unclaimed_quantity == b.affected.quantity
    assert result.cases[0].status == 'released_cancelled'


@pytest.mark.parametrize('field,value', [('quantity', Decimal('.250')), ('serial_ids', (uid(99),)),
    ('from_account_id', SOURCE.id), ('to_account_id', uid(99)), ('movement_type', 'scrap')])
def test_settlement_must_move_exact_case_share(field, value):
    b = basis()
    c = claim(b)
    r, h = approved(c)
    e = settle(c, h, 4, execute=True)
    refused(b, (c, r, h, replace(e, posting=replace(e.posting, **{field: value}))),
            'return_condition_posting_not_exact')


@pytest.mark.parametrize('field,value', [('owner_org_id', uid(90)), ('custodian_person_id', uid(91)),
    ('location_id', uid(92)), ('material_id', uid(93)), ('lot_id', uid(94)), ('bucket', 'reserved')])
def test_correction_cannot_transfer_ownership_custody_or_unrelated_stock(field, value):
    b = basis()
    c = claim(b)
    r, h = approved(c)
    e = settle(c, h, 4, execute=True)
    refused(b, (c, r, h, replace(e, target=replace(e.target, **{field: value}))),
            'return_condition_settlement_dimensions_changed')


def test_replay_ids_cursor_reuse_or_foreign_case_cannot_create_another_posting():
    b = basis()
    c = claim(b)
    r, h = approved(c)
    e = settle(c, h, 4, execute=True)
    refused(b, (c, r, h, e, replace(e, sequence=5)), 'return_condition_event_reused_or_unordered')
    refused(b, (c, r, h, replace(e, case_id=uid(999))), 'return_condition_orphan_action')
    refused(b, (c, r, h, replace(e, posting=replace(e.posting, movement_id=c.posting.movement_id))),
            'return_condition_posting_reused')
    refused(b, (c, r, h, replace(e, posting=replace(e.posting, ledger_cursor=2))),
            'return_condition_posting_order_invalid')
    refused(b, (c, r, h, e, replace(e, id=uid(3999), sequence=5)),
            'return_condition_settlement_stage_conflict')
    refused(b, (replace(c, posting=replace(c.posting, transaction_id=b.original_transaction_id)),),
            'return_condition_posting_reused')


def test_all_reorderings_of_review_and_execution_except_causal_order_are_refused():
    b = basis()
    c = claim(b)
    r, h = approved(c)
    e = settle(c, h, 4, execute=True)
    for order in permutations((r, h, e)):
        reordered = tuple(replace(item, sequence=n) for n, item in enumerate(order, 2))
        if order == (r, h, e):
            assert project(b, (c, *reordered)).corrected_quantity == Decimal('.125')
        else:
            with pytest.raises(ContractError):
                project(b, (c, *reordered))


def test_budget_arithmetic_does_not_depend_on_ambient_decimal_context():
    b = replace(basis(), affected=replace(basis().affected, quantity=Decimal('999999999999999.999')))
    a = claim(b, amount='999999999999999.998')
    c = claim(b, sequence=2, case=101, amount='.001')
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[Inexact] = ctx.traps[Rounded] = True
        result = project(b, (a, c))
        assert result.held_quantity == b.affected.quantity and result.unclaimed_quantity == 0
        refused(b, (a, c, claim(b, sequence=3, case=102, amount='.001')),
                'return_condition_exception_overclaimed')


@pytest.mark.parametrize('amount', [True, .125, '0.125', Decimal('NaN'), Decimal('-1'), Decimal('.0001')])
def test_invalid_quantities_cannot_enter_accounting(amount):
    b = basis()
    c = claim(b)
    with pytest.raises(ContractError):
        project(b, (replace(c, selected=replace(c.selected, quantity=amount)),))


def test_mutable_collection_fake_policy_or_extra_movement_in_review_are_refused():
    b = basis()
    c = claim(b)
    refused(b, [c], 'return_condition_immutable_history_required')
    refused(b, (replace(c, selected=replace(c.selected, serial_ids=[])),),
            'return_condition_serial_tuple_required')
    refused(b, (replace(c, selected=replace(c.selected, quantity_scale=2)),),
            'return_condition_claim_policy_changed')
    refused(b, (c, replace(action(c, 'verify_region', 2), posting=c.posting)),
            'return_condition_unexpected_posting_or_binding')


def test_empty_history_is_unclaimed_but_never_write_authority():
    b = basis()
    result = project(b, ())
    assert not result.cases and result.held_quantity == result.corrected_quantity == 0
    assert result.unclaimed_quantity == b.affected.quantity
    assert not hasattr(result, 'posting_allowed')


def test_claim_and_regional_verification_each_require_their_own_evidence_binding():
    b = basis()
    c = claim(b)
    refused(b, (replace(c, evidence_file_ids=()),), 'return_condition_physical_evidence_required')
    refused(b, (replace(c, evidence_file_ids=(uid(80), uid(80))),), 'return_condition_duplicate_evidence')
    refused(b, (c, replace(action(c, 'verify_region', 2), evidence_file_ids=())),
            'return_condition_physical_evidence_required')


@pytest.mark.parametrize('decision', ['approve_hq', 'reject_hq', 'cancel_approved'])
def test_requester_cannot_self_approve_reject_or_cancel_after_approval(decision):
    b = basis()
    c = claim(b)
    r, h = approved(c)
    history = (c, r, h) if decision == 'cancel_approved' else (c, r)
    attempt = action(c, decision, len(history) + 1,
        actor=('requester', SOURCE.custodian_person_id))
    refused(b, (*history, attempt), 'return_condition_self_review_forbidden')


def test_release_requires_exact_rejection_decision_and_original_account():
    b = basis()
    c = claim(b)
    r = action(c, 'reject_region', 2)
    release = settle(c, r, 3)
    refused(b, (c, r, replace(release, decision_id=uid(999))), 'return_condition_decision_binding_invalid')
    refused(b, (c, r, replace(release, target=replace(SOURCE, id=uid(999)))),
            'return_condition_release_not_original_account')
    refused(b, (c, r, replace(release, actor_user_id='other-user')), 'return_condition_requester_required')


def test_independent_cases_can_share_frozen_account_but_never_borrow_each_others_budget():
    b = basis()
    a = claim(b, amount='.250')
    c = claim(b, sequence=2, case=101)
    r, h = approved(c, start=3)
    execute = settle(c, h, 5, execute=True)
    refused(b, (a, c, r, h, replace(execute, posting=replace(execute.posting, quantity=Decimal('.375')))),
            'return_condition_posting_not_exact')
    result = project(b, (a, c, r, h, execute))
    assert result.held_quantity == Decimal('.250') and result.corrected_quantity == Decimal('.125')
    assert result.unclaimed_quantity == 0
