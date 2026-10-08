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
    FULFILLMENT_SEED_ROLES,
    assert_0051_authorization_additions,
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
    # Preserve real 0051 stock/count facts, including the unverified observation.
    # No 0076 fulfillment or 0083 recipient seed exists in this predecessor.
    for table in ('stock_accounts', 'stock_balances', 'stocktake_count_lines',
                  'stocktake_count_observations', 'stocktake_scope_count_completions'):
        rows = [row['parameters'] for row in fixture['statements']
                if row['sql'].startswith('INSERT INTO ' + table + ' (')]
        historical[table] = _table([{key: value['value'] if isinstance(value, dict)
                                    and set(value) == {'type', 'value'} else value
                                    for key, value in row.items()} for row in rows])
    historical['inventory_transactions'] = _table([], ('id', 'quantity'))
    _grant(authorization, 'provincial_manager', 'inventory', 'read', effect=effect)
    authorization['permissions']['rows'][0]['created_at'] = '2026-08-30T00:00:00+00:00'
    authorization['role_permissions']['rows'][0]['created_at'] = '2026-08-30T00:00:00+00:00'
    return historical, authorization


def _upgraded_authorization(authorization):
    result = deepcopy(authorization)
    for identifier, action, created_at in (
            (RECIPIENT_FULFILL_PERMISSION_ID, 'fulfill', '2026-09-16T00:00:00+00:00'),
            (RECIPIENT_RECEIVE_PERMISSION_ID, 'receive', '2026-09-23T00:00:00+00:00')):
        result['permissions']['rows'].append(dict(id=identifier, resource='material_request',
            action=action, field_code='', description=action, created_at=created_at))
    for identifier, role in FULFILLMENT_SEED_ROLES.items():
        result['role_permissions']['rows'].append(dict(id=identifier, role_id=role,
            permission_id=(RECIPIENT_RECEIVE_PERMISSION_ID if identifier == RECIPIENT_TECHNICIAN_SEED_ID
                           else RECIPIENT_FULFILL_PERMISSION_ID), effect='allow',
            created_at='2026-09-16T00:00:00+00:00'))
    return result


def test_0083_seed_coordinates_match_frozen_migration():
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'alembic'
        / 'versions' / '20260923_0083_recipient_receipt_permission.py'))
    assert str(migration['TECHNICIAN_SEED_ID']) == RECIPIENT_TECHNICIAN_SEED_ID
    assert str(migration['FULFILL_PERMISSION_ID']) == RECIPIENT_FULFILL_PERMISSION_ID
    assert str(migration['PERMISSION_ID']) == RECIPIENT_RECEIVE_PERMISSION_ID
    assert str(migration['TECHNICIAN_ROLE_ID']) == AUTHORIZATION_ROLES['technician']


@pytest.mark.parametrize('effect', ('allow', 'deny'))
def test_0051_regional_plan_preserves_pre0076_rows_and_observations(effect):
    historical, authorization = _frozen_region_before(effect)
    original = deepcopy((historical, authorization))
    expected, authorization_expected = expected_0051_regional_upgrade(historical, authorization)
    assert expected['users']['rows'][0]['authorization_version'] == 4
    assert expected['stocktake_scope_count_completions'] == historical['stocktake_scope_count_completions']
    assert authorization_expected == authorization
    assert authorization_expected['role_permissions']['rows'][0]['effect'] == effect
    assert expected['stock_balances']['rows'][0]['quantity'] == '0'
    assert expected['stocktake_count_lines']['rows'][0]['counted_qty'] == '0.000'
    assert expected['stocktake_count_observations']['rows'][0]['counted_qty'] == '1'
    assert expected['stocktake_count_observations']['rows'][0]['serial_id'] is None
    assert expected['stocktake_count_observations']['rows'][0]['verification_status'] == 'pending_verification'
    assert expected['inventory_transactions']['rows'] == expected['inventory_movements']['rows'] == []
    for table in historical.keys() - {'users'}:
        assert expected[table] == historical[table]
    assert (historical, authorization) == original
    upgraded = _upgraded_authorization(authorization)
    assert_legacy_upgrade_readback(expected, deepcopy(expected), authorization_expected, upgraded)
    assert_0051_authorization_additions(upgraded)


@pytest.mark.parametrize('identifier', tuple(FULFILLMENT_SEED_ROLES))
def test_0051_regional_plan_rejects_future_0076_grant_id_even_on_old_permission(identifier):
    historical, authorization = _frozen_region_before()
    authorization['role_permissions']['rows'][0]['id'] = identifier
    with pytest.raises(AssertionError, match='0051 contains a future 0076 grant'):
        expected_0051_regional_upgrade(historical, authorization)


@pytest.mark.parametrize('action', ('fulfill', 'receive'))
@pytest.mark.parametrize('collision', ('identity', 'natural_key'))
def test_0051_regional_plan_rejects_future_permission_identity_or_natural_key(action, collision):
    historical, authorization = _frozen_region_before()
    permission = authorization['permissions']['rows'][0]
    if collision == 'identity':
        permission['id'] = (RECIPIENT_FULFILL_PERMISSION_ID if action == 'fulfill'
                            else RECIPIENT_RECEIVE_PERMISSION_ID)
        authorization['role_permissions']['rows'][0]['permission_id'] = permission['id']
    else:
        permission.update(resource='material_request', action=action, field_code='')
    with pytest.raises(AssertionError, match='0051 contains a future 0076/0083 permission'):
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
def test_0051_readback_preserves_every_old_grant_field_after_future_seeds_are_added(field, value):
    historical, authorization = _frozen_region_before('deny')
    expected, authorization_expected = expected_0051_regional_upgrade(historical, authorization)
    actual = _upgraded_authorization(authorization_expected)
    actual['role_permissions']['rows'][0][field] = value
    assert_0051_authorization_additions(actual)
    with pytest.raises(AssertionError, match='historical authorization rows changed'):
        assert_legacy_upgrade_readback(expected, expected, authorization_expected, actual)


def test_0051_future_seed_coordinates_match_the_creation_and_conversion_migrations():
    directory = Path(__file__).resolve().parents[1] / 'alembic' / 'versions'
    creation = runpy.run_path(str(directory / '20260916_0076_material_request_fulfillment_permission.py'))
    conversion = runpy.run_path(str(directory / '20260923_0083_recipient_receipt_permission.py'))
    assert {str(identifier): str(role) for identifier, role in creation['ROLE_PERMISSION_ROWS']} == FULFILLMENT_SEED_ROLES
    assert str(creation['PERMISSION_ID']) == str(conversion['FULFILL_PERMISSION_ID']) == RECIPIENT_FULFILL_PERMISSION_ID
    assert str(conversion['PERMISSION_ID']) == RECIPIENT_RECEIVE_PERMISSION_ID
    assert str(conversion['TECHNICIAN_SEED_ID']) == RECIPIENT_TECHNICIAN_SEED_ID


@pytest.mark.parametrize('case', ('missing_fulfill', 'missing_receive', 'wrong_definition',
                                 'duplicate_definition', 'missing_admin', 'missing_region',
                                 'missing_technician', 'wrong_role', 'technician_still_fulfill',
                                 'region_receive', 'deny_new_seed', 'changed_creation_time'))
def test_0051_upgrade_requires_exact_future_seed_additions(case):
    _, before = _frozen_region_before('deny')
    actual = _upgraded_authorization(before)
    permissions = actual['permissions']['rows']
    grants = actual['role_permissions']['rows']
    by_id = {row['id']: row for row in grants}
    if case in ('missing_fulfill', 'missing_receive'):
        identifier = RECIPIENT_FULFILL_PERMISSION_ID if case == 'missing_fulfill' else RECIPIENT_RECEIVE_PERMISSION_ID
        permissions[:] = [row for row in permissions if row['id'] != identifier]
    elif case == 'wrong_definition':
        permissions[-1]['field_code'] = 'private_field'
    elif case == 'duplicate_definition':
        permissions.append(dict(permissions[-1], id='unexpected-natural-key-duplicate'))
    elif case.startswith('missing_'):
        role = {'missing_admin': 'admin', 'missing_region': 'provincial_manager',
                'missing_technician': 'technician'}[case]
        identifier = next(key for key, value in FULFILLMENT_SEED_ROLES.items() if value == AUTHORIZATION_ROLES[role])
        grants[:] = [row for row in grants if row['id'] != identifier]
    elif case == 'wrong_role':
        by_id[RECIPIENT_TECHNICIAN_SEED_ID]['role_id'] = AUTHORIZATION_ROLES['admin']
    elif case == 'technician_still_fulfill':
        by_id[RECIPIENT_TECHNICIAN_SEED_ID]['permission_id'] = RECIPIENT_FULFILL_PERMISSION_ID
    elif case == 'region_receive':
        identifier = next(key for key, value in FULFILLMENT_SEED_ROLES.items()
                          if value == AUTHORIZATION_ROLES['provincial_manager'])
        by_id[identifier]['permission_id'] = RECIPIENT_RECEIVE_PERMISSION_ID
    elif case == 'deny_new_seed':
        by_id[RECIPIENT_TECHNICIAN_SEED_ID]['effect'] = 'deny'
    else:
        by_id[RECIPIENT_TECHNICIAN_SEED_ID]['created_at'] = '2026-09-23T00:00:00+00:00'
    with pytest.raises(AssertionError, match='0051 upgrade'):
        assert_0051_authorization_additions(actual)
