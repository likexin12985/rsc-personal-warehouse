"""Historical proofs reject mismatched predecessors without mutation."""
from copy import deepcopy
import json

import pytest
import forward_catalog_expectations as subject


def fixture_chain(tmp_path, monkeypatch, before):
    monkeypatch.setattr(subject, '__file__', str(tmp_path/'backend/tests/helper.py'))
    monkeypatch.setattr(subject, '_successor_paths', lambda _: (('synthetic_0002', ''),))
    folder = tmp_path/'backend/alembic/forward_0002'
    folder.mkdir(parents=True)
    (folder/'catalog.json').write_text(json.dumps({'functions': {
        'guard()': {'before': before, 'after': {'body': 'new', 'acl': ['owner']}}}}))
    return {'functions': {'guard()': {'before': None, 'after': {'body': 'old', 'acl': ['owner']}}}}


def test_exact_full_predecessor_is_required_and_original_is_preserved(tmp_path, monkeypatch):
    original = fixture_chain(tmp_path, monkeypatch, {'body': 'old', 'acl': ['owner']})
    saved = deepcopy(original)
    advanced = subject.current_catalog('synthetic_0001', original)
    assert advanced['functions']['guard()']['after'] == {'body': 'new', 'acl': ['owner']}
    assert advanced['functions']['guard()']['before'] is None
    assert original == saved


@pytest.mark.parametrize('before', [
    {'body': 'other', 'acl': ['owner']}, {'body': 'old', 'acl': ['public']}])
def test_body_or_privilege_drift_cannot_be_skipped(tmp_path, monkeypatch, before):
    original = fixture_chain(tmp_path, monkeypatch, before)
    saved = deepcopy(original)
    with pytest.raises(AssertionError, match='full catalog predecessor discontinuity'):
        subject.current_catalog('synthetic_0001', original)
    assert original == saved
