"""Regression for newer-fact masking versus guards outside a downgrade path."""
import pytest

from test_postgresql16_release_gate import _retention_chain_blocker


@pytest.mark.parametrize('destination, revision', [
    ('20261108_0129', '20261109_0130'),
    ('20261110_0131', '20261111_0132'),
    ('20261111_0132', '20261112_0133'),
    ('20261112_0133', '20261113_0134'),
    ('20261119_0140', '20261120_0141'),
])
def test_older_opening_and_notification_facts_cannot_mask_daily_or_import_guard(destination, revision):
    assert _retention_chain_blocker(destination, blocking_revision=revision, blocker='required guard',
        retained=dict(opening_seals=True, opening_actors=True, zero_openings=True,
                      published_controls=True, source_files=True, control_facts=True,
                      sealed=True)) == 'required guard'


def test_existing_returns_still_mask_daily_guards_and_require_independent_proof():
    assert _retention_chain_blocker('20261108_0129', blocking_revision='20261109_0130',
        blocker='required guard', retained=dict(opening_seals=True, return_inbounds=True)) == (
            '0149 return inbound account admission history requires retention')


def test_newest_existing_guard_wins_regardless_of_dictionary_order():
    assert _retention_chain_blocker('20261021_0111', blocking_revision='20261022_0112',
        blocker='required guard', retained=dict(sealed=True, opening_seals=True,
            loss_dispositions=True, return_inbounds=True)) == (
                '0151 disposition custody proof history requires retention')


def test_old_fence_crossed_by_nonadjacent_downgrade_cannot_replace_newer_required_fence():
    assert _retention_chain_blocker('20261018_0108', blocking_revision='20261022_0112',
        blocker='required guard', retained=dict(sealed=True)) == 'required guard'


@pytest.mark.parametrize('destination, revision', [
    ('head', '20261109_0130'), ('20261109_0130', '20261109_0130'),
    ('20261110_0131', '20261109_0130'), ('20261108_0129', '20261231_9999'),
])
def test_invalid_or_uncrossed_guard_is_not_accepted(destination, revision):
    with pytest.raises(ValueError, match='retention'):
        _retention_chain_blocker(destination, blocking_revision=revision,
                                 blocker='required guard', retained={})
