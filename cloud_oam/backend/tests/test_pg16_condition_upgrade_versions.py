"""Legacy upgrade evidence counts each retained additive policy exactly."""
from pathlib import Path


def test_two_policies_advance_each_affected_user_once_per_migration(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    from run_local_pg16_return_condition_checks import expected_authorization_versions

    versions = {'admin': 4, 'region': 7, 'both': 10, 'tech': 3, 'unassigned': 1}
    assignments = [('admin', 'admin'), ('region', 'provincial_manager'),
                   ('both', 'admin'), ('both', 'provincial_manager'), ('tech', 'technician')]
    assert expected_authorization_versions(versions, assignments, set()) == {
        'admin': 6, 'region': 9, 'both': 12, 'tech': 3, 'unassigned': 1,
    }
    assert versions['admin'] == 4


def test_existing_allow_or_deny_grant_does_not_invalidate_again(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    from run_local_pg16_return_condition_checks import expected_authorization_versions

    # The presence query deliberately includes denies: migrations preserve
    # both effects and do not convert an explicit deny to a default allow.
    retained = {('admin', 'stock_operation', 'review_return_condition_headquarters'),
                ('admin', 'stock_operation', 'cancel_return_condition_approval'),
                ('admin', 'material_request', 'close')}
    assert expected_authorization_versions({'a': 8}, [('a', 'admin')], retained) == {'a': 8}
    retained.remove(('admin', 'material_request', 'close'))
    assert expected_authorization_versions({'a': 8}, [('a', 'admin')], retained) == {'a': 9}
