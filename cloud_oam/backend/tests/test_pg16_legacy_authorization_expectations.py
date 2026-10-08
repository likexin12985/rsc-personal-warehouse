"""Exact pre-upgrade expectations; these checks do not replace hosted PG16."""
from copy import deepcopy
from pathlib import Path
import runpy

import pytest

from pg16_legacy_opening_gate import (
    AUTHORIZATION_POLICIES,
    AUTHORIZATION_ROLES,
    RECIPIENT_TECHNICIAN_SEED_ID,
    RECIPIENT_FULFILL_PERMISSION_ID,
    RECIPIENT_RECEIVE_PERMISSION_ID,
    assert_legacy_upgrade_readback,
    additive_policy_authorization_version_plan,
    expected_additive_policy_facts,
    expected_0051_regional_upgrade,
)
from pg16_legacy_opening_fixture import load_fixture


def _table(rows, columns=()):
    return {'columns': list(rows[0]) if rows else list(columns), 'rows': rows}


def _before():
    versions = {'admin': 7, 'region': 10, 'technician': 2, 'multi': 20,
                'external': 4, 'unassigned': 3}
    users = [dict(id=key, authorization_version=value, name=key,
                  updated_at='2026-09-20T12:00:00+00:00') for key, value in versions.items()]
    assignments = [dict(id=str(index), user_id=user, role_id=AUTHORIZATION_ROLES[role],
                        status='inactive' if user == 'region' else 'active')
                   for index, (user, role) in enumerate((
                       ('admin', 'admin'), ('region', 'provincial_manager'),
                       ('technician', 'technician'), ('multi', 'admin'),
                       ('multi', 'provincial_manager'), ('multi', 'technician'),
                       ('multi', 'admin')))]
    external_id = '10000000-0000-4000-8000-000000000004'
    assignments.append(dict(id='external', user_id='external', role_id=external_id,
                            status='active'))
    historical = {
        'users': _table(users), 'role_assignments': _table(assignments),
        'stocktake_scope_count_completions': _table([
            dict(id='completion', authorization_version=1, request_sha256='preserved')]),
        'inventory_movements': _table([], ('id', 'quantity')),
    }
    authorization = {
        'roles': _table([dict(id=key, code=code, is_external=False, status='active')
                         for code, key in AUTHORIZATION_ROLES.items()] + [
                             dict(id=external_id, code='star_headquarters_approver',
                                  is_external=True, status='active')]),
        'permissions': _table([], ('id', 'resource', 'action', 'field_code', 'description')),
        'role_permissions': _table([], ('id', 'role_id', 'permission_id', 'effect')),
    }
    return historical, authorization


def _grant(authorization, role, resource, action, *, effect, field_code=''):
    # Custom permission/grant identities must be matched by natural key, never
    # assumed to use the deterministic seed UUIDs.
    permissions = authorization['permissions']['rows']
    definition = next((row for row in permissions if
        (row['resource'], row['action'], row['field_code']) == (resource, action, field_code)), None)
    if definition is None:
        definition = dict(id='custom-permission-' + str(len(permissions)), resource=resource,
                          action=action, field_code=field_code, description='reviewed custom')
        permissions.append(definition)
    grants = authorization['role_permissions']['rows']
    grants.append(dict(id='custom-grant-' + str(len(grants)), role_id=AUTHORIZATION_ROLES[role],
                       permission_id=definition['id'], effect=effect))


@pytest.mark.parametrize('index,folder', tuple(enumerate((
    'stock_scrap_0165', 'return_condition_0167', 'request_closure_0169'))))
def test_independent_matrix_matches_reviewed_frozen_policy(index, folder):
    policy = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / 'alembic' / folder / 'permission_policy.py'))
    _, _, defaults = AUTHORIZATION_POLICIES[index]
    assert defaults == policy['DEFAULTS']
    assert {code: str(identifier) for code, identifier in policy['ROLES'].items()} == {
        code: AUTHORIZATION_ROLES[code] for code in policy['ROLES']}


def test_each_policy_advances_each_assigned_user_once_and_preserves_input():
    historical, authorization = _before()
    original = deepcopy((historical, authorization))
    assert additive_policy_authorization_version_plan(historical, authorization) == {
        '20261214_0165': ('admin', 'multi', 'region', 'technician'),
        '20261216_0167': ('admin', 'multi', 'region'),
        '20261218_0169': ('admin', 'multi', 'region'),
    }
    expected = expected_additive_policy_facts(historical, authorization)
    assert {row['id']: row['authorization_version'] for row in expected['users']['rows']} == {
        'admin': 10, 'region': 13, 'technician': 3, 'multi': 23, 'external': 4, 'unassigned': 3}
    assert expected['stocktake_scope_count_completions']['rows'][0]['authorization_version'] == 1
    assert (historical, authorization) == original
    assert_legacy_upgrade_readback(expected, deepcopy(expected), authorization, deepcopy(authorization))


@pytest.mark.parametrize('effect', ('allow', 'deny'))
def test_retained_custom_grants_of_either_effect_suppress_only_their_policy(effect):
    historical, authorization = _before()
    for _, resource, defaults in AUTHORIZATION_POLICIES:
        for role, action in defaults:
            _grant(authorization, role, resource, action, effect=effect)
    original = deepcopy(authorization)
    assert all(not users for users in additive_policy_authorization_version_plan(historical, authorization).values())
    assert expected_additive_policy_facts(historical, authorization) == historical
    authorization['role_permissions']['rows'] = [row for row in authorization['role_permissions']['rows']
        if not (row['role_id'] == AUTHORIZATION_ROLES['admin']
                and row['permission_id'] == authorization['permissions']['rows'][-1]['id'])]
    assert additive_policy_authorization_version_plan(historical, authorization) == {
        '20261214_0165': (), '20261216_0167': (), '20261218_0169': ('admin', 'multi')}
    assert all(row['effect'] == effect for row in original['role_permissions']['rows'])
    assert_legacy_upgrade_readback(historical, historical, original, original)


def test_field_specific_grant_does_not_suppress_full_resource_default():
    historical, authorization = _before()
    for _, resource, defaults in AUTHORIZATION_POLICIES:
        for role, action in defaults:
            _grant(authorization, role, resource, action, effect='deny', field_code='private_field')
    assert additive_policy_authorization_version_plan(historical, authorization)['20261218_0169'] == (
        'admin', 'multi', 'region')


@pytest.mark.parametrize('case', ('under_bump', 'over_bump', 'unrelated_user_field',
                                 'historical_evidence_version', 'new_inventory_row',
                                 'removed_user', 'changed_assignment', 'removed_column'))
def test_exact_upgrade_readback_rejects_any_unplanned_historical_change(case):
    historical, authorization = _before()
    expected = expected_additive_policy_facts(historical, authorization)
    actual = deepcopy(expected)
    if case == 'under_bump':
        actual['users']['rows'][0]['authorization_version'] -= 1
    elif case == 'over_bump':
        actual['users']['rows'][0]['authorization_version'] += 1
    elif case == 'unrelated_user_field':
        actual['users']['rows'][0]['name'] = 'changed'
    elif case == 'historical_evidence_version':
        actual['stocktake_scope_count_completions']['rows'][0]['authorization_version'] += 3
    elif case == 'new_inventory_row':
        actual['inventory_movements']['rows'].append(dict(id='unexpected', quantity=1))
    elif case == 'removed_user':
        actual['users']['rows'].pop()
    elif case == 'changed_assignment':
        actual['role_assignments']['rows'][0]['status'] = 'inactive'
    else:
        actual['users']['columns'].remove('name')
    with pytest.raises(AssertionError, match='unplanned facts'):
        assert_legacy_upgrade_readback(expected, actual, authorization, authorization)


@pytest.mark.parametrize('case', ('deny_to_allow', 'allow_to_deny', 'removed_grant',
                                 'changed_definition', 'changed_role'))
def test_existing_authorization_rows_remain_exact_even_with_correct_user_versions(case):
    historical, authorization = _before()
    _grant(authorization, 'admin', 'material_request', 'close',
           effect='allow' if case == 'allow_to_deny' else 'deny')
    expected = expected_additive_policy_facts(historical, authorization)
    changed = deepcopy(authorization)
    if case in ('deny_to_allow', 'allow_to_deny'):
        changed['role_permissions']['rows'][0]['effect'] = 'deny' if case == 'allow_to_deny' else 'allow'
    elif case == 'removed_grant':
        changed['role_permissions']['rows'].clear()
    elif case == 'changed_definition':
        changed['permissions']['rows'][0]['description'] = 'rewritten'
    else:
        changed['roles']['rows'][0]['status'] = 'inactive'
    with pytest.raises(AssertionError, match='historical authorization rows'):
        assert_legacy_upgrade_readback(expected, expected, authorization, changed)


def _frozen_region_before(effect='allow'):
    historical, authorization = _before()
    fixture = load_fixture()
    user, = [row['parameters'] for row in fixture['statements']
             if row['sql'].startswith('INSERT INTO users (')]
    assignment, = [row['parameters'] for row in fixture['statements']
                   if row['sql'].startswith('INSERT INTO role_assignments (')]
    historical['users'] = _table([dict(id=user['id'], role='provincial_manager',
        authorization_version=1, name=user['name'])])
    historical['role_assignments'] = _table([dict(id=assignment['id']['value'],
        user_id=user['id'], role_id=assignment['role_id']['value'], status='active')])
    authorization['permissions'] = _table([dict(id=RECIPIENT_FULFILL_PERMISSION_ID,
        resource='material_request', action='fulfill', field_code='', description='old fulfill')])
    authorization['role_permissions'] = _table([dict(id=RECIPIENT_TECHNICIAN_SEED_ID,
        role_id=AUTHORIZATION_ROLES['technician'], permission_id=RECIPIENT_FULFILL_PERMISSION_ID,
        effect=effect, created_at='2026-09-01T00:00:00+00:00')])
    return historical, authorization


def test_0083_seed_coordinates_match_frozen_migration():
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'alembic'
        / 'versions' / '20260923_0083_recipient_receipt_permission.py'))
    assert str(migration['TECHNICIAN_SEED_ID']) == RECIPIENT_TECHNICIAN_SEED_ID
    assert str(migration['FULFILL_PERMISSION_ID']) == RECIPIENT_FULFILL_PERMISSION_ID
    assert str(migration['PERMISSION_ID']) == RECIPIENT_RECEIVE_PERMISSION_ID
    assert str(migration['TECHNICIAN_ROLE_ID']) == AUTHORIZATION_ROLES['technician']


@pytest.mark.parametrize('effect', ('allow', 'deny'))
def test_0051_regional_plan_applies_only_exact_0083_seed_change_before_upgrade(effect):
    historical, authorization = _frozen_region_before(effect)
    original = deepcopy((historical, authorization))
    expected, authorization_expected = expected_0051_regional_upgrade(historical, authorization)
    assert expected['users']['rows'][0]['authorization_version'] == 4
    assert expected['stocktake_scope_count_completions'] == historical['stocktake_scope_count_completions']
    precise_authorization = deepcopy(authorization)
    precise_authorization['role_permissions']['rows'][0]['permission_id'] = RECIPIENT_RECEIVE_PERMISSION_ID
    assert authorization_expected == precise_authorization
    assert authorization_expected['role_permissions']['rows'][0]['effect'] == effect
    assert (historical, authorization) == original
    assert_legacy_upgrade_readback(expected, deepcopy(expected), authorization_expected,
                                  deepcopy(authorization_expected))
    with pytest.raises(AssertionError, match='historical authorization rows changed'):
        assert_legacy_upgrade_readback(expected, expected, authorization_expected, authorization)


@pytest.mark.parametrize('case', ('missing_seed', 'wrong_seed_role', 'wrong_seed_permission',
                                 'wrong_seed_effect', 'changed_fulfill', 'existing_receive'))
def test_0051_regional_plan_rejects_unreviewed_0083_predecessor(case):
    historical, authorization = _frozen_region_before()
    seed = authorization['role_permissions']['rows'][0]
    if case == 'missing_seed':
        authorization['role_permissions']['rows'].clear()
    elif case == 'wrong_seed_role':
        seed['role_id'] = AUTHORIZATION_ROLES['admin']
    elif case == 'wrong_seed_permission':
        seed['permission_id'] = RECIPIENT_RECEIVE_PERMISSION_ID
    elif case == 'wrong_seed_effect':
        seed['effect'] = 'unknown'
    elif case == 'changed_fulfill':
        authorization['permissions']['rows'][0]['action'] = 'unreviewed'
    else:
        authorization['permissions']['rows'].append(dict(id=RECIPIENT_RECEIVE_PERMISSION_ID,
            resource='material_request', action='receive', field_code='', description='unexpected'))
    with pytest.raises(AssertionError, match='0083'):
        expected_0051_regional_upgrade(historical, authorization)


@pytest.mark.parametrize('case', ('extra_user', 'technician_identity', 'technician_assignment',
                                 'extra_assignment', 'different_regional_user', 'changed_initial_version'))
def test_0051_regional_plan_refuses_general_or_mixed_historical_populations(case):
    historical, authorization = _frozen_region_before()
    user = historical['users']['rows'][0]
    assignment = historical['role_assignments']['rows'][0]
    if case == 'extra_user':
        historical['users']['rows'].append(dict(id='extra', role='technician', authorization_version=1))
    elif case == 'technician_identity':
        user['role'] = 'technician'
    elif case == 'technician_assignment':
        assignment['role_id'] = AUTHORIZATION_ROLES['technician']
    elif case == 'extra_assignment':
        historical['role_assignments']['rows'].append(dict(id='extra', user_id=user['id'],
            role_id=AUTHORIZATION_ROLES['technician'], status='active'))
    elif case == 'different_regional_user':
        user['id'] = 'different'
        assignment['user_id'] = 'different'
    else:
        user['authorization_version'] = 3
    with pytest.raises(AssertionError, match='0051'):
        expected_0051_regional_upgrade(historical, authorization)


@pytest.mark.parametrize('field,value', (('effect', 'allow'), ('created_at', 'changed'),
                                      ('role_id', AUTHORIZATION_ROLES['admin'])))
def test_0051_0083_seed_readback_rejects_any_change_beyond_permission_id(field, value):
    historical, authorization = _frozen_region_before('deny')
    expected, authorization_expected = expected_0051_regional_upgrade(historical, authorization)
    actual = deepcopy(authorization_expected)
    actual['role_permissions']['rows'][0][field] = value
    with pytest.raises(AssertionError, match='historical authorization rows changed'):
        assert_legacy_upgrade_readback(expected, expected, authorization_expected, actual)
