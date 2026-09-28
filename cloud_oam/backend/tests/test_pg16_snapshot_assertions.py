"""A populated gate must retain history and prove the exact added identities."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from pg16_snapshot_assertions import assert_exact_appends


def test_existing_history_and_new_identity_survive_reordering_and_uuid_forms():
    old = {'id': '00000000-0000-0000-0000-000000000001', 'payload': {'quantity': '1.000'}}
    new = {'id': '00000000-0000-0000-0000-000000000002', 'payload': {'quantity': '2.000'}}
    assert_exact_appends([old], [new, deepcopy(old)], [UUID(new['id'])])
    assert_exact_appends([], [SimpleNamespace(_mapping=new)], [new['id']])
    assert_exact_appends([old], [deepcopy(old)], [])


@pytest.mark.parametrize('damage', ['changed', 'removed', 'unexpected', 'missing', 'substituted',
                                  'duplicate_old', 'duplicate_new', 'duplicate_expected', 'reused'])
def test_equal_counts_never_hide_changed_history_or_wrong_new_records(damage):
    old = [{'id': 'prior', 'payload': {'hash': 'original'}}]
    after = deepcopy(old) + [{'id': 'created', 'payload': {'hash': 'new'}}]
    expected = ['created']
    if damage == 'changed': after[0]['payload']['hash'] = 'tampered'
    elif damage == 'removed': after.pop(0)
    elif damage == 'unexpected': after.append({'id': 'unrelated'})
    elif damage == 'missing': after.pop()
    elif damage == 'substituted': after[-1]['id'] = 'wrong'
    elif damage == 'duplicate_old': old.append(deepcopy(old[0]))
    elif damage == 'duplicate_new': after.append(deepcopy(after[-1]))
    elif damage == 'duplicate_expected': expected.append('created')
    elif damage == 'reused': expected = ['prior', 'created']
    with pytest.raises(AssertionError):
        assert_exact_appends(old, after, expected)
