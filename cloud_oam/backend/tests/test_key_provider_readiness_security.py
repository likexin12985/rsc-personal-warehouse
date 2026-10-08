"""Prove 0180 advances only the pinned readiness revision, preserving 0179."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import key_provider_readiness_security as candidate


ROOT = Path(__file__).resolve().parents[1]


def _previous():
    data = json.loads((ROOT / 'alembic/return_receipt_routing_0179/functions.json').read_text())
    return SimpleNamespace(DATA=deepcopy(data['readiness']))


def test_readiness_runtime_copy_matches_frozen_migration_and_exact_predecessor():
    raw = (ROOT / 'alembic/key_provider_bindings_0180/functions.json').read_bytes()
    assert candidate.RAW == raw
    assert sha256(raw).hexdigest() == '39f4a03998a528a5dd9a0e5314e783b9bd9f3ccb14a364fe8014f97a048d4109'
    previous = _previous().DATA
    assert candidate.DATA['readiness']['before'] == previous['after']
    assert candidate.DATA['readiness']['beforeSha256'] == previous['afterSha256']
    ready = candidate.DATA['readiness']
    function = candidate.DATA['functions']['rsc_oam_runtime_binding_ready_0044()']
    assert set(candidate.DATA['functions']) == {'rsc_oam_runtime_binding_ready_0044()'}
    for side in ('before', 'after'):
        assert function[side] == ready[side]['prosrc']
        assert function[side + 'Sha256'] == ready[side + 'Sha256'] == sha256(function[side].encode()).hexdigest()


def test_readiness_revision_is_the_only_changed_function_catalog_content():
    ready = candidate.DATA['readiness']
    before, after = deepcopy(ready['before']), deepcopy(ready['after'])
    for field in ('prosrc', 'definition'):
        assert before[field].count('20261228_0179') == 1
        assert after[field] == before[field].replace('20261228_0179', '20261229_0180')
        del before[field], after[field]
    assert after == before


def test_overlay_preserves_prior_history_and_does_not_mutate_catalog_inputs():
    previous = _previous()
    original = previous.DATA
    old = deepcopy(original)
    frozen = deepcopy(candidate.DATA)
    candidate.overlay(previous)
    assert original == old
    assert candidate.DATA == frozen
    assert previous.DATA['revision'] == '20261229_0180'
    assert previous.DATA['before'] == old['before']
    assert previous.DATA['beforeSha256'] == old['beforeSha256']
    assert previous.DATA['after'] == candidate.DATA['readiness']['after']
    previous.DATA['after']['acl'].append({'grantee': 'synthetic-drift'})
    assert candidate.DATA == frozen


@pytest.mark.parametrize('field', ['revision', 'afterSha256', 'prosrc', 'definition', 'acl', 'owner', 'proconfig', 'prosecdef'])
def test_overlay_rejects_any_predecessor_drift_without_partial_mutation(field):
    previous = _previous()
    if field in ('revision', 'afterSha256'):
        previous.DATA[field] = 'unexpected'
    else:
        previous.DATA['after'][field] = 'unexpected'
    before = deepcopy(previous.DATA)
    with pytest.raises(ValueError, match='0180 exact readiness predecessor required'):
        candidate.overlay(previous)
    assert previous.DATA == before


def test_overlay_does_not_accept_a_second_application():
    previous = _previous()
    candidate.overlay(previous)
    before = deepcopy(previous.DATA)
    with pytest.raises(ValueError, match='0180 exact readiness predecessor required'):
        candidate.overlay(previous)
    assert previous.DATA == before
