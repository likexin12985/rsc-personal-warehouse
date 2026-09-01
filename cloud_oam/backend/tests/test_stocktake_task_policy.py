import pytest

from app.formal_services.stocktake_task_policy import (
    StocktakeTaskPolicyError,
    difference_posting_movement,
    may_show_book_quantity,
    require_atomic_start_path,
    require_stocktake_task_type,
    require_stocktake_transition,
)


def test_all_formal_nonopening_types_are_explicit_and_opening_is_separate():
    for value in ("full", "sample", "ad_hoc", "personal", "termination"):
        assert require_stocktake_task_type(value) == value
    with pytest.raises(StocktakeTaskPolicyError):
        require_stocktake_task_type("opening")


def test_state_machine_keeps_review_recount_posting_and_close_distinct():
    for source, target in (
        ("submitted", "region_review"),
        ("region_review", "hq_review"),
        ("hq_review", "approved"),
        ("approved", "posted"),
        ("posted", "closed"),
        ("recount_required", "counting"),
    ):
        require_stocktake_transition(source, target)
    with pytest.raises(StocktakeTaskPolicyError):
        require_stocktake_transition("submitted", "posted")
    with pytest.raises(StocktakeTaskPolicyError):
        require_stocktake_transition("approved", "closed")


def test_atomic_start_preserves_all_three_intermediate_events():
    require_atomic_start_path("draft", ("issued", "frozen", "counting"))
    with pytest.raises(StocktakeTaskPolicyError):
        require_atomic_start_path("draft", ("counting",))


def test_blind_count_visibility_waits_for_both_round_and_difference_seals():
    assert may_show_book_quantity(
        blind_count=False,
        round_status="counting",
        difference_set_sealed=False,
    )
    assert not may_show_book_quantity(
        blind_count=True,
        round_status="submitted",
        difference_set_sealed=False,
    )
    assert may_show_book_quantity(
        blind_count=True,
        round_status="submitted",
        difference_set_sealed=True,
    )


def test_difference_maps_to_immutable_movement_and_never_balance_overwrite():
    assert difference_posting_movement("1.250") == "stocktake_gain"
    assert difference_posting_movement("-1.250") == "stocktake_loss"
    assert difference_posting_movement("0.000") is None
    assert difference_posting_movement("-0.000") is None
    for invalid in (
        "1",
        "1.2",
        "1.0000",
        "NaN",
        "--1.000",
        "00.000",
        "+1.000",
        " 1.000",
        "1e3",
    ):
        with pytest.raises(StocktakeTaskPolicyError):
            difference_posting_movement(invalid)
