from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.formal_services import stocktake_posting as service


ZERO = Decimal("0.000")
ONE = Decimal("1.000")
NOW = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _id(value: int) -> uuid.UUID:
    return uuid.UUID(int=value)


def _row(**values):
    return SimpleNamespace(**values)


def _clone(value, **updates):
    fields = dict(vars(value))
    fields.update(updates)
    return _row(**fields)


def _scope(number: int, *, task_id, owner_org_id, location_id):
    return _row(
        id=_id(100 + number),
        task_id=task_id,
        scope_no=number,
        scope_mode="location_all",
        owner_org_id=owner_org_id,
        location_id=location_id,
        material_id=None,
        condition_code=None,
        availability_bucket=None,
    )


def _account(
    number: int,
    *,
    owner_org_id,
    location_id,
    material_id,
    custodian_person_id=None,
    condition="new",
    availability="available",
    lot_id=None,
):
    return _row(
        id=_id(300 + number),
        owner_org_id=owner_org_id,
        location_id=location_id,
        material_id=material_id,
        custodian_person_id=custodian_person_id,
        condition_code=condition,
        availability_bucket=availability,
        lot_id=lot_id,
    )


def _difference(
    number: int,
    *,
    task_id,
    round_id,
    scope_id,
    kind,
    material_id,
    expected=None,
    observed=None,
    observed_line=None,
    serial_id=None,
    book=ZERO,
    counted=ZERO,
    affected=ONE,
    reason="counted_difference",
):
    return _row(
        id=_id(500 + number),
        task_id=task_id,
        round_id=round_id,
        scope_id=scope_id,
        control_snapshot_line_id=None,
        difference_no=number,
        difference_type=kind,
        material_id=material_id,
        expected_account_id=expected,
        observed_account_id=observed,
        observed_line_id=observed_line,
        serial_id=serial_id,
        book_qty=book,
        counted_qty=counted,
        difference_qty=counted - book,
        affected_qty=affected,
        reason_code=reason,
        reason_text="evidence",
        evidence_required=True,
    )


def _completion(number: int, *, task_id, round_id, submission, differences):
    return _row(
        id=_id(700 + number),
        task_id=task_id,
        round_id=round_id,
        round_submission_id=submission.id,
        difference_count=len(differences),
        physical_difference_count=len(differences),
        control_difference_count=0,
        pending_observation_difference_count=sum(
            row.reason_code == "stocktake_pending_verification"
            for row in differences
        ),
        total_affected_qty=sum(
            (row.affected_qty for row in differences), start=ZERO
        ),
        difference_manifest_sha256=f"{number:x}".rjust(64, "0"),
    )


def _review(
    number: int,
    *,
    task_id,
    round_id,
    stage,
    decision,
    at,
):
    return _row(
        id=_id(800 + number),
        task_id=task_id,
        round_id=round_id,
        review_stage=stage,
        decision=decision,
        reviewed_at=at,
        reviewer_user_id=f"user-{number}",
        reviewer_person_id=_id(900 + number),
    )


def _review_items(review, differences, decisions):
    return [
        _row(
            review_id=review.id,
            task_id=review.task_id,
            round_id=review.round_id,
            difference_id=difference.id,
            decision=decisions[difference.id],
        )
        for difference in differences
    ]


def _approval_evidence(world, decisions):
    completion_id = _id(1200)
    terminal_round = max(world.rounds, key=lambda row: row.round_no)
    terminal_hq = next(
        row
        for row in world.reviews
        if row.round_id == terminal_round.id
        and row.review_stage == "headquarters"
    )
    completion_by_round = {row.round_id: row for row in world.completions}
    review_by_id = {row.id: row for row in world.reviews}
    review_items_by_id = {
        (row.review_id, row.difference_id): row for row in world.review_items
    }
    scopes = []
    items = []
    manifest_scopes = []
    for scope in sorted(world.scopes, key=lambda row: str(row.id)):
        source_round = world.effective_round_by_scope[scope.id]
        source_completion = completion_by_round[source_round.id]
        regional = next(
            row
            for row in world.reviews
            if row.round_id == source_round.id and row.review_stage == "region"
        )
        source_differences = [
            row
            for row in sorted(world.differences, key=lambda row: row.difference_no)
            if row.round_id == source_round.id and row.scope_id == scope.id
        ]
        scopes.append(
            service.EffectiveApprovalScopeEvidence(
                completion_id=completion_id,
                scope_id=scope.id,
                source_round_id=source_round.id,
                source_difference_completion_id=source_completion.id,
                regional_review_id=regional.id,
                difference_count=len(source_differences),
            )
        )
        manifest_items = []
        for difference in source_differences:
            regional_decision = review_items_by_id[
                (regional.id, difference.id)
            ].decision
            headquarters_decision = decisions[difference.id]
            items.append(
                service.EffectiveApprovalItemEvidence(
                    completion_id=completion_id,
                    scope_id=scope.id,
                    source_round_id=source_round.id,
                    difference_id=difference.id,
                    regional_review_id=regional.id,
                    regional_decision=regional_decision,
                    headquarters_decision=headquarters_decision,
                )
            )
            manifest_items.append(
                {
                    "difference_id": str(difference.id),
                    "headquarters_decision": headquarters_decision,
                    "regional_decision": regional_decision,
                }
            )
        manifest_scopes.append(
            {
                "difference_count": len(source_differences),
                "items": manifest_items,
                "regional_review_id": str(regional.id),
                "scope_id": str(scope.id),
                "source_difference_completion_id": str(source_completion.id),
                "source_round_id": str(source_round.id),
            }
        )
    difference_count = len(items)
    manifest = service._sha256(
        {
            "completion_id": str(completion_id),
            "difference_count": difference_count,
            "schema": "cloud_oam.stocktake.effective_approval.v1",
            "scope_count": len(world.scopes),
            "scopes": manifest_scopes,
            "task_id": str(world.task.id),
            "terminal_headquarters_review_id": str(terminal_hq.id),
            "terminal_round_id": str(terminal_round.id),
        }
    )
    approval = service.EffectiveApprovalCompletionEvidence(
        id=completion_id,
        task_id=world.task.id,
        terminal_round_id=terminal_round.id,
        terminal_headquarters_review_id=terminal_hq.id,
        scope_count=len(scopes),
        difference_count=difference_count,
        approval_manifest_sha256=manifest,
    )
    return approval, tuple(scopes), tuple(items)


def _initial_world(*, differences=None, scopes=None, accounts=None):
    task_id = _id(1)
    owner = _id(2)
    location_one = _id(3)
    location_two = _id(4)
    material_one = _id(10)
    material_two = _id(11)
    material_three = _id(12)
    if scopes is None:
        scopes = [
            _scope(
                1,
                task_id=task_id,
                owner_org_id=owner,
                location_id=location_one,
            ),
            _scope(
                2,
                task_id=task_id,
                owner_org_id=owner,
                location_id=location_two,
            ),
        ]
    round_row = _row(
        id=_id(20),
        task_id=task_id,
        round_no=1,
        round_type="initial",
        status="submitted",
        submitted_at=NOW,
        count_manifest_sha256="a" * 64,
        recount_case_id=None,
    )
    task = _row(
        id=task_id,
        task_type="full",
        status="approved",
        version=5,
        cutoff_ledger_cursor=3,
        cutoff_at=NOW - timedelta(hours=2),
        submitted_at=NOW,
        posted_at=None,
        closed_at=None,
        current_round_no=1,
    )
    if accounts is None:
        accounts = [
            _account(
                1,
                owner_org_id=owner,
                location_id=location_one,
                material_id=material_one,
            ),
            _account(
                2,
                owner_org_id=owner,
                location_id=location_two,
                material_id=material_one,
            ),
            _account(
                3,
                owner_org_id=owner,
                location_id=location_one,
                material_id=material_two,
            ),
            _account(
                4,
                owner_org_id=owner,
                location_id=location_one,
                material_id=material_two,
                condition="used",
            ),
            _account(
                5,
                owner_org_id=owner,
                location_id=location_one,
                material_id=material_three,
            ),
            _account(
                6,
                owner_org_id=owner,
                location_id=location_two,
                material_id=material_three,
            ),
        ]
    account_by_id = {row.id: row for row in accounts}
    if differences is None:
        differences = [
            _difference(
                1,
                task_id=task_id,
                round_id=round_row.id,
                scope_id=scopes[0].id,
                kind="missing",
                material_id=material_one,
                expected=accounts[0].id,
                book=ONE,
            ),
            _difference(
                2,
                task_id=task_id,
                round_id=round_row.id,
                scope_id=scopes[1].id,
                kind="excess",
                material_id=material_one,
                observed=accounts[1].id,
                counted=ONE,
            ),
            _difference(
                3,
                task_id=task_id,
                round_id=round_row.id,
                scope_id=scopes[0].id,
                kind="wrong_condition",
                material_id=material_two,
                expected=accounts[2].id,
                observed=accounts[3].id,
                book=ONE,
                counted=ONE,
            ),
            _difference(
                4,
                task_id=task_id,
                round_id=round_row.id,
                scope_id=scopes[1].id,
                kind="wrong_location",
                material_id=material_three,
                expected=accounts[4].id,
                observed=accounts[5].id,
                book=ONE,
                counted=ONE,
            ),
        ]
    submission = _row(id=_id(30), task_id=task_id, round_id=round_row.id)
    completion = _completion(
        1,
        task_id=task_id,
        round_id=round_row.id,
        submission=submission,
        differences=differences,
    )
    region = _review(
        1,
        task_id=task_id,
        round_id=round_row.id,
        stage="region",
        decision="approve",
        at=NOW + timedelta(minutes=1),
    )
    hq = _review(
        2,
        task_id=task_id,
        round_id=round_row.id,
        stage="headquarters",
        decision="approve",
        at=NOW + timedelta(minutes=2),
    )
    decisions = {
        row.id: ("no_adjustment" if row.difference_no == 4 else "accept_for_posting")
        for row in differences
    }
    review_items = [
        *_review_items(region, differences, decisions),
        *_review_items(hq, differences, decisions),
    ]
    world = _row(
        task=task,
        scopes=list(scopes),
        rounds=[round_row],
        recount_cases=[],
        recount_assignments=[],
        submissions=[submission],
        completions=[completion],
        differences=list(differences),
        reviews=[region, hq],
        review_items=review_items,
        observations=[],
        accounts=account_by_id,
        observation_account_ids={},
        effective_round_by_scope={row.id: round_row for row in scopes},
    )
    approval, approval_scopes, approval_items = _approval_evidence(world, decisions)
    world.approval = approval
    world.approval_scopes = approval_scopes
    world.approval_items = approval_items
    return world


def _build(world):
    return service._build_nonopening_stocktake_posting_plan(
        task=world.task,
        expected_task_version=world.task.version,
        scopes=world.scopes,
        rounds=world.rounds,
        recount_cases=world.recount_cases,
        recount_assignments=world.recount_assignments,
        submissions=world.submissions,
        completions=world.completions,
        differences=world.differences,
        reviews=world.reviews,
        review_items=world.review_items,
        observations=world.observations,
        accounts=world.accounts,
        observation_account_ids=world.observation_account_ids,
        effective_approval_completion=world.approval,
        effective_approval_scopes=world.approval_scopes,
        effective_approval_items=world.approval_items,
    )


def test_mixed_approved_differences_plan_four_axes_without_balance_write():
    world = _initial_world()
    before = {key: dict(vars(value)) for key, value in world.accounts.items()}

    result = _build(world)

    assert [row.movement_type for row in result.movements] == [
        "stocktake_gain",
        "stocktake_loss",
        "status_change",
    ]
    assert result.no_adjustment_difference_ids == (world.differences[3].id,)
    assert result.effective_difference_count == 4
    assert len(result.effective_scope_rounds) == 2
    assert len(result.plan_manifest_sha256) == 64
    assert {key: dict(vars(value)) for key, value in world.accounts.items()} == before


def test_plan_is_order_independent_and_manifest_is_stable():
    world = _initial_world()
    first = _build(world)
    world.scopes.reverse()
    world.differences.reverse()
    world.reviews.reverse()
    world.review_items.reverse()
    world.approval_scopes = tuple(reversed(world.approval_scopes))
    world.approval_items = tuple(reversed(world.approval_items))

    second = _build(world)

    assert second == first


def test_zero_difference_task_keeps_explicit_scope_approval_plan():
    task_id = _id(1)
    owner = _id(2)
    location = _id(3)
    scope = _scope(
        1,
        task_id=task_id,
        owner_org_id=owner,
        location_id=location,
    )
    world = _initial_world(differences=[], scopes=[scope], accounts=[])

    result = _build(world)

    assert result.movements == ()
    assert result.no_adjustment_difference_ids == ()
    assert result.effective_difference_count == 0
    assert len(result.effective_scope_rounds) == 1


def _partial_recount_world():
    base = _initial_world()
    scope_one, scope_two = base.scopes
    round_one = base.rounds[0]
    first, second = base.differences[:2]
    first = _clone(first, difference_no=1, scope_id=scope_one.id)
    second = _clone(second, difference_no=2, scope_id=scope_two.id)
    submission_one = base.submissions[0]
    completion_one = _completion(
        1,
        task_id=base.task.id,
        round_id=round_one.id,
        submission=submission_one,
        differences=[first, second],
    )
    region_one = _review(
        10,
        task_id=base.task.id,
        round_id=round_one.id,
        stage="region",
        decision="recount",
        at=NOW + timedelta(minutes=1),
    )
    region_one_items = _review_items(
        region_one,
        [first, second],
        {first.id: "accept_for_posting", second.id: "recount"},
    )
    case = _row(
        id=_id(1000),
        task_id=base.task.id,
        source_round_id=round_one.id,
        source_round_submission_id=submission_one.id,
        source_difference_completion_id=completion_one.id,
        trigger_review_id=region_one.id,
        next_round_no=2,
        scope_count=1,
    )
    assignment = _row(
        id=_id(1001),
        recount_case_id=case.id,
        task_id=base.task.id,
        source_round_id=round_one.id,
        scope_id=scope_two.id,
    )
    round_two = _row(
        id=_id(21),
        task_id=base.task.id,
        round_no=2,
        round_type="recount",
        status="submitted",
        submitted_at=NOW + timedelta(minutes=3),
        count_manifest_sha256="b" * 64,
        recount_case_id=case.id,
    )
    submission_two = _row(id=_id(31), task_id=base.task.id, round_id=round_two.id)
    completion_two = _completion(
        2,
        task_id=base.task.id,
        round_id=round_two.id,
        submission=submission_two,
        differences=[],
    )
    region_two = _review(
        11,
        task_id=base.task.id,
        round_id=round_two.id,
        stage="region",
        decision="approve",
        at=NOW + timedelta(minutes=4),
    )
    hq_two = _review(
        12,
        task_id=base.task.id,
        round_id=round_two.id,
        stage="headquarters",
        decision="approve",
        at=NOW + timedelta(minutes=5),
    )
    world = _row(
        task=_clone(base.task, current_round_no=2, submitted_at=round_two.submitted_at),
        scopes=base.scopes,
        rounds=[round_one, round_two],
        recount_cases=[case],
        recount_assignments=[assignment],
        submissions=[submission_one, submission_two],
        completions=[completion_one, completion_two],
        differences=[first, second],
        reviews=[region_one, region_two, hq_two],
        review_items=region_one_items,
        observations=[],
        accounts=base.accounts,
        observation_account_ids={},
        effective_round_by_scope={scope_one.id: round_one, scope_two.id: round_two},
    )
    approval, approval_scopes, approval_items = _approval_evidence(
        world,
        {first.id: "accept_for_posting"},
    )
    world.approval = approval
    world.approval_scopes = approval_scopes
    world.approval_items = approval_items
    return world


def test_partial_recount_uses_old_region_item_but_requires_new_taskwide_hq_seal():
    world = _partial_recount_world()

    result = _build(world)

    selected = {row.scope_id: row.round_no for row in result.effective_scope_rounds}
    assert selected == {world.scopes[0].id: 1, world.scopes[1].id: 2}
    assert [row.difference_id for row in result.movements] == [
        world.differences[0].id
    ]


def test_partial_recount_does_not_treat_old_top_level_recount_as_hq_approval():
    world = _partial_recount_world()
    item = world.approval_items[0]
    world.approval_items = (
        service.EffectiveApprovalItemEvidence(
            completion_id=item.completion_id,
            scope_id=item.scope_id,
            source_round_id=item.source_round_id,
            difference_id=item.difference_id,
            regional_review_id=item.regional_review_id,
            regional_decision=item.regional_decision,
            headquarters_decision="recount",
        ),
    )

    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        _build(world)

    assert caught.value.code == "stocktake_posting_scope_not_approved"


def test_recount_assignment_must_exactly_match_triggered_scope():
    world = _partial_recount_world()
    world.recount_assignments[0] = _clone(
        world.recount_assignments[0], scope_id=world.scopes[0].id
    )

    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        _build(world)

    assert caught.value.code == "stocktake_posting_evidence_invalid"


def test_effective_approval_manifest_tamper_fails_closed():
    world = _initial_world()
    world.approval = service.EffectiveApprovalCompletionEvidence(
        id=world.approval.id,
        task_id=world.approval.task_id,
        terminal_round_id=world.approval.terminal_round_id,
        terminal_headquarters_review_id=(
            world.approval.terminal_headquarters_review_id
        ),
        scope_count=world.approval.scope_count,
        difference_count=world.approval.difference_count,
        approval_manifest_sha256="f" * 64,
    )

    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        _build(world)

    assert caught.value.code == "stocktake_posting_evidence_invalid"


def test_pending_observation_can_never_be_sealed_as_no_adjustment():
    world = _initial_world()
    pending = _clone(
        world.differences[0], reason_code="stocktake_pending_verification"
    )
    world.differences[0] = pending
    world.completions[0] = _completion(
        1,
        task_id=world.task.id,
        round_id=world.rounds[0].id,
        submission=world.submissions[0],
        differences=world.differences,
    )
    decisions = {
        row.id: ("no_adjustment" if row.id == pending.id else "accept_for_posting")
        for row in world.differences
    }
    for item in world.review_items:
        item.decision = decisions[item.difference_id]
    world.approval, world.approval_scopes, world.approval_items = _approval_evidence(
        world, decisions
    )

    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        _build(world)

    assert caught.value.code == "stocktake_posting_pending_observation"


def test_verified_observation_requires_exact_new_account_dimension():
    task_id = _id(1)
    owner = _id(2)
    location = _id(3)
    material = _id(10)
    person = _id(13)
    scope = _scope(
        1,
        task_id=task_id,
        owner_org_id=owner,
        location_id=location,
    )
    observed_line_id = _id(1100)
    account = _account(
        1,
        owner_org_id=owner,
        location_id=location,
        material_id=material,
        custodian_person_id=person,
    )
    difference = _difference(
        1,
        task_id=task_id,
        round_id=_id(20),
        scope_id=scope.id,
        kind="excess",
        material_id=material,
        observed_line=observed_line_id,
        counted=ONE,
    )
    world = _initial_world(
        differences=[difference], scopes=[scope], accounts=[account]
    )
    observation = _row(
        id=observed_line_id,
        task_id=task_id,
        round_id=world.rounds[0].id,
        scope_id=scope.id,
        verification_status="verified",
        material_id=material,
        owner_org_id=owner,
        location_id=location,
        custodian_person_id_snapshot=person,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
        serial_id=None,
    )
    world.observations = [observation]
    world.observation_account_ids = {observation.id: account.id}

    result = _build(world)
    assert result.movements[0].to_account_id == account.id

    world.accounts[account.id] = _clone(account, custodian_person_id=None)
    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        _build(world)
    assert caught.value.code == "stocktake_posting_observation_account_invalid"


def test_wrong_condition_cannot_hide_location_change():
    world = _initial_world()
    target_id = world.differences[2].observed_account_id
    world.accounts[target_id] = _clone(
        world.accounts[target_id], location_id=world.scopes[1].location_id
    )

    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        _build(world)

    assert caught.value.code == "stocktake_posting_evidence_invalid"


def test_same_serial_cannot_be_adjusted_by_two_effective_differences():
    world = _initial_world()
    serial_id = _id(1300)
    first = _clone(
        world.differences[0],
        serial_id=serial_id,
        affected_qty=ONE,
        book_qty=ONE,
        counted_qty=ZERO,
        difference_qty=-ONE,
    )
    second = _clone(
        world.differences[1],
        serial_id=serial_id,
        affected_qty=ONE,
        book_qty=ZERO,
        counted_qty=ONE,
        difference_qty=ONE,
    )
    world.differences = [first, second]
    world.completions[0] = _completion(
        1,
        task_id=world.task.id,
        round_id=world.rounds[0].id,
        submission=world.submissions[0],
        differences=world.differences,
    )
    decisions = {row.id: "accept_for_posting" for row in world.differences}
    world.review_items = [
        *_review_items(world.reviews[0], world.differences, decisions),
        *_review_items(world.reviews[1], world.differences, decisions),
    ]
    world.approval, world.approval_scopes, world.approval_items = _approval_evidence(
        world, decisions
    )

    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        _build(world)

    assert caught.value.code == "stocktake_posting_serial_reused"


def test_stale_task_version_and_posted_task_are_rejected():
    world = _initial_world()
    world.task.version += 1
    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        service._build_nonopening_stocktake_posting_plan(
            task=world.task,
            expected_task_version=5,
            scopes=world.scopes,
            rounds=world.rounds,
            recount_cases=world.recount_cases,
            recount_assignments=world.recount_assignments,
            submissions=world.submissions,
            completions=world.completions,
            differences=world.differences,
            reviews=world.reviews,
            review_items=world.review_items,
            observations=world.observations,
            accounts=world.accounts,
            observation_account_ids=world.observation_account_ids,
            effective_approval_completion=world.approval,
            effective_approval_scopes=world.approval_scopes,
            effective_approval_items=world.approval_items,
        )
    assert caught.value.code == "stocktake_posting_version_conflict"

    world.task.version = 5
    world.task.status = "posted"
    with pytest.raises(service.StocktakeDifferencePostingError) as caught:
        _build(world)
    assert caught.value.code == "stocktake_posting_task_not_postable"
