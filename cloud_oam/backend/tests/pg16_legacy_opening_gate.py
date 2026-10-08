"""Readback for a 0051 historical fixture and atomic failed migration."""
from copy import deepcopy

from sqlalchemy import text
from pg16_legacy_opening_fixture import load_fixture


# Independent expectations reviewed against the three frozen additive policies.
# Do not derive a version delta from the upgraded database or current ORM.
AUTHORIZATION_POLICIES = (
    ('20261214_0165', 'stock_operation', (
        ('technician', 'submit_loss'),
        ('provincial_manager', 'review_loss_regional'),
        ('admin', 'finalize_loss'),
        ('admin', 'dispose_loss'),
        ('admin', 'reverse_loss'),
        ('admin', 'approve_loss_correction'),
        ('admin', 'correct_loss'),
        ('technician', 'apply_scrap_recovery'),
        ('provincial_manager', 'review_scrap_recovery_regional'),
        ('admin', 'review_scrap_recovery_headquarters'),
        ('admin', 'execute_scrap_recovery'),
    )),
    ('20261216_0167', 'stock_operation', (
        ('provincial_manager', 'submit_return_condition'),
        ('provincial_manager', 'supplement_return_condition'),
        ('provincial_manager', 'withdraw_return_condition'),
        ('provincial_manager', 'execute_return_condition'),
        ('provincial_manager', 'release_return_condition'),
        ('provincial_manager', 'review_return_condition_regional'),
        ('admin', 'review_return_condition_headquarters'),
        ('admin', 'cancel_return_condition_approval'),
    )),
    ('20261218_0169', 'material_request', (
        ('admin', 'close'), ('provincial_manager', 'close'),
    )),
)
AUTHORIZATION_ROLES = {
    'admin': '10000000-0000-4000-8000-000000000001',
    'provincial_manager': '10000000-0000-4000-8000-000000000002',
    'technician': '10000000-0000-4000-8000-000000000003',
}
AUTHORIZATION_TABLES = ('roles', 'permissions', 'role_permissions')
RECIPIENT_TECHNICIAN_SEED_ID = '21000000-0000-4000-8000-000000000105'
RECIPIENT_FULFILL_PERMISSION_ID = '20000000-0000-4000-8000-000000000059'
RECIPIENT_RECEIVE_PERMISSION_ID = '20000000-0000-4000-8000-000000000062'
FULFILLMENT_SEED_ROLES = {
    '21000000-0000-4000-8000-000000000103': AUTHORIZATION_ROLES['admin'],
    '21000000-0000-4000-8000-000000000104': AUTHORIZATION_ROLES['provincial_manager'],
    RECIPIENT_TECHNICIAN_SEED_ID: AUTHORIZATION_ROLES['technician'],
}


def snapshot(engine, *, tables=None, columns=None):
    """Exact old columns/rows, including empty tables, before migration."""
    result = {}
    with engine.connect() as db:
        names = tables if tables is not None else db.scalars(text("""SELECT tablename
            FROM pg_tables WHERE schemaname='public' ORDER BY tablename""")).all()
        for table in names:
            selected = columns[table]['columns'] if columns is not None else db.scalars(text("""SELECT column_name
                FROM information_schema.columns WHERE table_schema='public' AND table_name=:table
                ORDER BY ordinal_position"""), {'table': table}).all()
            quote = engine.dialect.identifier_preparer.quote
            sql = "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM (SELECT "
            sql += ','.join(quote(c) for c in selected) + ' FROM public.' + quote(table) + ') t'
            result[table] = dict(columns=list(selected), rows=db.scalar(text(sql)))
    return result


def legacy_catalog(engine):
    """Compare named SQL objects and privileges, excluding transient OIDs."""
    with engine.connect() as db:
        functions = db.execute(text("""SELECT p.proname,pg_get_function_identity_arguments(p.oid),
            p.prokind,p.prosecdef,p.provolatile,p.proisstrict,p.proparallel,p.proleakproof,
            pg_get_userbyid(p.proowner),p.proconfig,p.proacl::text,pg_get_function_result(p.oid),
            encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' ORDER BY 1,2""")).all()
        triggers = db.execute(text("""SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid)
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2""")).all()
        constraints = db.execute(text("""SELECT c.relname,t.conname,pg_get_constraintdef(t.oid),t.convalidated
            FROM pg_constraint t JOIN pg_class c ON c.oid=t.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' ORDER BY 1,2""")).all()
        indexes = db.execute(text("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2")).all()
        tables = db.execute(text("""SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,
            pg_get_userbyid(c.relowner),c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' ORDER BY 1""")).all()
        columns = db.execute(text("""SELECT table_name,column_name,ordinal_position,
            data_type,udt_schema,udt_name,domain_schema,domain_name,is_nullable,column_default,
            character_maximum_length,numeric_precision,numeric_scale,datetime_precision,
            collation_schema,collation_name,is_identity,identity_generation,is_generated,generation_expression
            FROM information_schema.columns WHERE table_schema='public'
            ORDER BY table_name,ordinal_position""")).all()
    return dict(functions=functions,triggers=triggers,constraints=constraints,indexes=indexes,tables=tables,columns=columns)


def assert_backfill(engine, evidence):
    with engine.connect() as db:
        row = db.execute(text("""SELECT request_jsonb,request_resolution_jsonb,request_sha256
            FROM stocktake_scope_count_completions WHERE id=:id"""), {'id': evidence['completion_id']}).one()
        assert row[0] == evidence['expected_request_jsonb']
        assert row[1] == evidence['expected_request_resolution_jsonb']
        assert row[2] == evidence['request_sha256'] == row[1]['request_sha256']
        item, = row[1]['items']
        assert item['target_type'] == 'observation' and item['target_id'] == str(evidence['observation_id'])
        assert item['request_ordinal'] == 1 and item['serial_alias_keys'] == [evidence['serial_alias_key']]
        assert db.scalar(text('SELECT count(*) FROM inventory_transactions')) == 0
        assert db.scalar(text('SELECT count(*) FROM inventory_movements')) == 0


def historical_facts(engine, *, columns=None):
    return snapshot(engine,tables=load_fixture()['tables'],columns=columns)


def authorization_facts(engine, *, columns=None):
    """Capture exact pre-upgrade role/definition/grant rows independently."""
    return snapshot(engine, tables=AUTHORIZATION_TABLES, columns=columns)


def _rows_by_id(facts, table):
    rows = facts[table]['rows']
    result = {row['id']: row for row in rows}
    assert len(result) == len(rows), 'duplicate historical identities: ' + table
    return result


def additive_policy_authorization_version_plan(historical_before, authorization_before):
    """Only 0165/0167/0169 invalidations, not a general 0051-to-head plan.

    The policies retain either effect on an existing full-resource grant and
    invalidate all assigned users, including inactive/expired assignments.
    Several new grants or assigned roles must never multiply the same bump.
    Only pre-upgrade facts enter this function.
    """
    roles = _rows_by_id(authorization_before, 'roles')
    for code, identifier in AUTHORIZATION_ROLES.items():
        role = roles.get(identifier)
        assert (role is not None and role['code'] == code
                and role['is_external'] is False
                and role['status'] in ('active', 'inactive')), 'unreviewed role identity'
    permissions = _rows_by_id(authorization_before, 'permissions')
    natural = {(row['resource'], row['action'], row['field_code']): row['id']
               for row in permissions.values()}
    assert len(natural) == len(permissions), 'ambiguous historical permissions'
    grants = _rows_by_id(authorization_before, 'role_permissions')
    pairs = set()
    for grant in grants.values():
        assert grant['effect'] in ('allow', 'deny'), 'invalid historical grant effect'
        assert grant['role_id'] in roles and grant['permission_id'] in permissions
        pairs.add((grant['role_id'], grant['permission_id']))
    assert len(pairs) == len(grants), 'ambiguous historical role grants'
    users = _rows_by_id(historical_before, 'users')
    assignments = _rows_by_id(historical_before, 'role_assignments')
    assert all(row['user_id'] in users and row['role_id'] in roles
               for row in assignments.values()), 'unbound historical assignment'
    planned = {}
    for revision, resource, defaults in AUTHORIZATION_POLICIES:
        changed_roles = {
            AUTHORIZATION_ROLES[code] for code, action in defaults
            if (AUTHORIZATION_ROLES[code], natural.get((resource, action, ''))) not in pairs
        }
        planned[revision] = tuple(sorted({
            row['user_id'] for row in assignments.values()
            if row['role_id'] in changed_roles
        }))
    return planned


def expected_additive_policy_facts(historical_before, authorization_before):
    """Apply only the three additive policies, not earlier role transitions."""
    planned = additive_policy_authorization_version_plan(historical_before, authorization_before)
    expected = deepcopy(historical_before)
    for user in expected['users']['rows']:
        delta = sum(user['id'] in affected for affected in planned.values())
        if delta:
            version = user['authorization_version']
            assert type(version) is int and 1 <= version < 2**63 - delta, 'version cannot advance'
            user['authorization_version'] = version + delta
    return expected


def expected_0051_regional_upgrade(historical_before, authorization_before):
    """Plan only rows that exist in the frozen 0051 single-region fixture.

    0083 invalidates technician users. This fixture has exactly one regional
    user and one regional assignment, so no earlier technician invalidation
    applies. 0076 creates the fulfillment seeds later; 0083 changes one of
    those new rows. Neither migration may change the pre-0051 authorization
    snapshot. Refuse other fixture populations and future seed identities.
    """
    fixture = load_fixture()
    frozen_users = [row['parameters'] for row in fixture['statements']
                    if row['sql'].startswith('INSERT INTO users (')]
    frozen_assignments = [row['parameters'] for row in fixture['statements']
                          if row['sql'].startswith('INSERT INTO role_assignments (')]
    assert len(frozen_users) == len(frozen_assignments) == 1, 'unreviewed frozen 0051 fixture'
    user, assignment = frozen_users[0], frozen_assignments[0]
    users = _rows_by_id(historical_before, 'users')
    assignments = _rows_by_id(historical_before, 'role_assignments')
    assert set(users) == {user['id']}, '0051 expectation requires the frozen regional user'
    assert (users[user['id']]['role'], users[user['id']]['authorization_version']) == (
        'provincial_manager', user['authorization_version']), '0051 regional identity changed'
    assert set(assignments) == {assignment['id']['value']}, '0051 regional assignment changed'
    actual_assignment = assignments[assignment['id']['value']]
    assert (actual_assignment['user_id'], actual_assignment['role_id']) == (
        user['id'], AUTHORIZATION_ROLES['provincial_manager']), '0051 regional assignment changed'

    # This snapshot precedes 0076, not merely 0083. Never inject a future seed
    # to satisfy the later migration's guard or silently accept its collision.
    grants = _rows_by_id(authorization_before, 'role_permissions')
    assert not set(FULFILLMENT_SEED_ROLES).intersection(grants), '0051 contains a future 0076 grant'
    permissions = _rows_by_id(authorization_before, 'permissions')
    assert not {RECIPIENT_FULFILL_PERMISSION_ID, RECIPIENT_RECEIVE_PERMISSION_ID}.intersection(
        permissions), '0051 contains a future 0076/0083 permission identity'
    assert not any((row['resource'], row['action'], row['field_code']) in (
        ('material_request', 'fulfill', ''), ('material_request', 'receive', ''))
        for row in permissions.values()), '0051 contains a future 0076/0083 permission definition'
    expected_historical = expected_additive_policy_facts(historical_before, authorization_before)
    return expected_historical, deepcopy(authorization_before)


def assert_0051_authorization_additions(authorization_after):
    """Check newly created 0076/0083 rows only after the real upgrade."""
    permissions = _rows_by_id(authorization_after, 'permissions')
    for identifier, action in ((RECIPIENT_FULFILL_PERMISSION_ID, 'fulfill'),
                               (RECIPIENT_RECEIVE_PERMISSION_ID, 'receive')):
        row = permissions.get(identifier)
        key = ('material_request', action, '')
        assert row is not None and (row['resource'], row['action'], row['field_code']) == key, (
            '0051 upgrade missing or changed 0076/0083 permission')
        assert sum((item['resource'], item['action'], item['field_code']) == key
                   for item in permissions.values()) == 1, '0051 upgrade ambiguous 0076/0083 permission'
    grants = _rows_by_id(authorization_after, 'role_permissions')
    for identifier, role in FULFILLMENT_SEED_ROLES.items():
        seed = grants.get(identifier)
        permission = (RECIPIENT_RECEIVE_PERMISSION_ID if identifier == RECIPIENT_TECHNICIAN_SEED_ID
                      else RECIPIENT_FULFILL_PERMISSION_ID)
        assert seed is not None and (seed['role_id'], seed['permission_id'], seed['effect']) == (
            role, permission, 'allow'), '0051 upgrade missing or changed 0076/0083 grant'
        assert seed['created_at'] == permissions[RECIPIENT_FULFILL_PERMISSION_ID]['created_at'], (
            '0051 upgrade changed 0076 seed creation time')


def assert_legacy_upgrade_readback(expected, actual, authorization_expected, authorization_after):
    """Exact old facts and preplanned authorization rows; additions checked by their gates."""
    assert actual == expected, 'historical upgrade changed unplanned facts'
    assert set(authorization_expected) == set(authorization_after) == set(AUTHORIZATION_TABLES)
    for table in AUTHORIZATION_TABLES:
        assert authorization_after[table]['columns'] == authorization_expected[table]['columns']
        old = _rows_by_id(authorization_expected, table)
        new = _rows_by_id(authorization_after, table)
        assert set(old) <= set(new), 'historical authorization rows removed: ' + table
        assert all(new[key] == value for key, value in old.items()), (
            'historical authorization rows changed: ' + table)
