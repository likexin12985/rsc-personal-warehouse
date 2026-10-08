"""Exact forward runtime contract for 0173, registered at formal activation.

Only read-only catalog data ships with the API. No migration code or mutable
application schema is loaded to determine permitted functions or grants.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .stock_scrap_security_probe import snapshot
from .stock_loss_return_stop_security import verify_uuid

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '0b9f505feaac2c8f838d752fb87deb84915729daec79b9554bb4e93cdee5842a':
    raise ValueError('0173 runtime catalog digest mismatch')
DATA = json.loads(RAW)
TABLES = tuple(name for name, change in DATA['tables'].items() if change['before'] is None)
COLUMN_KEYS = ('name', 'type', 'not_null', 'default_sql', 'attidentity', 'attgenerated')
INDEX_KEYS = ('name', 'definition', 'indisunique', 'indisvalid', 'indisready')
CATALOGS = ('_loss_correction_catalog', '_loss_return_stop_catalog')
CATALOG_SUFFIXES = {'_loss_correction_catalog': ('_0159', '_0161'), '_loss_return_stop_catalog': ('_0163',)}


def table_view(row):
    # Older independent verifiers do not include constraint-backed indexes.
    backing = {c['name'] for c in row['constraints'] if c['type'] in ('p', 'u', 'x')}
    return dict(name=row['name'],
        columns=[{key: c[key] for key in COLUMN_KEYS} for c in row['columns']],
        constraints=deepcopy(row['constraints']),
        indexes=[{key: index[key] for key in INDEX_KEYS}
            for index in row['indexes'] if index['name'] not in backing])


def trigger_view(table, row):
    return dict(table_name=table, name=row['name'], definition=row['definition'],
        tgenabled=row['tgenabled'], tgtype=row['tgtype'],
        tgdeferrable=row['tgdeferrable'], tginitdeferred=row['tginitdeferred'],
        function_name=row['function_signature'].split('(', 1)[0])


def canonical(value):
    result = deepcopy(value)
    if 'signature' in result:
        result['signature'] = signature_key(result['signature'])
    if 'acl' in result:
        result['acl'] = sorted(result['acl'], key=lambda a: (a['grantee'], a['privilege'], a['grantable']))
    return result


def signature_key(value):
    name, arguments = value.split('(', 1)
    return name + '(' + ', '.join(part.strip() for part in arguments[:-1].split(',')) + ')'


def overlay_catalog(catalog, suffixes):
    """Retain the old verifier and patch only its proven predecessor objects."""
    result = deepcopy(catalog.DATA)
    for family in ('newFunctions', 'replacedFunctions'):
        for row in result[family]:
            change = DATA['functions'].get(signature_key(row['signature']))
            if change is None:
                continue
            before = change['before']
            if before is None or canonical({key: row[key] for key in before}) != canonical(before):
                raise ValueError('0173 predecessor full function changed: ' + row['signature'])
            row.update(deepcopy(change['after']))
            for key in ('sha256', 'prosrcSha256'):
                if key in row:
                    row[key] = sha256(row['prosrc'].encode()).hexdigest()
    for row in result['candidateTables']:
        change = DATA['tables'].get(row['name'])
        if change is None:
            continue
        before = table_view(change['before'])
        if {key: row[key] for key in before} != before:
            raise ValueError('0173 predecessor table changed: ' + row['name'])
        row.update(table_view(change['after']))
    old_triggers = {(row['table_name'], row['name']): row for row in result['triggers']}
    for name, change in DATA['tables'].items():
        before = {row['name']: row for row in change['before']['triggers']} if change['before'] else {}
        for row in change['after']['triggers']:
            key = name, row['name']
            prior = before.get(row['name'])
            if key in old_triggers and (prior is None or old_triggers[key] != trigger_view(name, prior)):
                raise ValueError('0173 predecessor trigger changed: ' + row['name'])
            if prior is not None:
                if prior != row:
                    raise ValueError('0173 unexpected old trigger replacement')
                continue
            # Retain the old SQL selector exactly, including old functions
            # newly attached to a forward table.
            function = row['function_signature'].split('(', 1)[0]
            if name in catalog.TABLES or function.endswith(suffixes):
                if key in old_triggers:
                    raise ValueError('0173 duplicate predecessor trigger')
                old_triggers[key] = trigger_view(name, row)
    result['triggers'] = [row for _, row in sorted(old_triggers.items())]
    return result


def register(namespace):
    """Validate the entire forward patch before publishing any allowlist."""
    updates = {}
    digest_names = ('MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256',
        'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256', 'RUNTIME_FUNCTION_BODY_SHA256')
    for name in (*digest_names, 'FORMAL_FILE_INTERNAL_FUNCTIONS', 'FORMAL_FILE_INTERNAL_FUNCTION_SHAPES',
            'FORMAL_FILE_INTERNAL_SET_FUNCTIONS',
            'RUNTIME_EXECUTE_FUNCTIONS', 'RUNTIME_FUNCTION_SHAPES',
            'EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS', 'EXPECTED_AUDIT_TRIGGERS'):
        updates[name] = deepcopy(namespace[name])
    for signature, change in DATA['functions'].items():
        row = change['after']
        coordinate = row['proname'], signature.split('(', 1)[1][:-1]
        if change['before'] is not None:
            matches = [name for name in digest_names if coordinate in updates[name]]
            expected = sha256(change['before']['prosrc'].encode()).hexdigest()
            if len(matches) != 1 or updates[matches[0]][coordinate] != expected:
                raise ValueError('0173 exact predecessor function digest required: ' + signature)
            updates[matches[0]][coordinate] = sha256(row['prosrc'].encode()).hexdigest()
            continue
        if any(coordinate in updates[name] for name in digest_names):
            raise ValueError('0173 duplicate function registration: ' + signature)
        api = any(a['grantee'] == 'star_oam_api' and a['privilege'] == 'EXECUTE' for a in row['acl'])
        definitions, shapes, digests = ('RUNTIME_EXECUTE_FUNCTIONS', 'RUNTIME_FUNCTION_SHAPES',
            'RUNTIME_FUNCTION_BODY_SHA256') if api else ('FORMAL_FILE_INTERNAL_FUNCTIONS',
            'FORMAL_FILE_INTERNAL_FUNCTION_SHAPES', 'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256')
        result = row['definition'].split(' RETURNS ', 1)[1].splitlines()[0].strip()
        if result.startswith('TABLE('):
            if api or coordinate != ('rsc_scrap_recovery_source_0165', 'uuid') or result != (
                    'TABLE(owner_org_id uuid, location_id uuid, requester_id uuid, request_hash text)'):
                raise ValueError('0173 unexpected set-returning helper')
            shape = ('f', 'record', row['proisstrict'])
            updates['FORMAL_FILE_INTERNAL_SET_FUNCTIONS'][coordinate] = ('i', 't', 't', 't', 't')
        else:
            if result not in ('trigger', 'jsonb', 'void', 'boolean', 'uuid[]'):
                raise ValueError('0173 unsupported frozen function shape')
            shape = ('f', result, row['proisstrict'])
        language = row['definition'].split('\n LANGUAGE ', 1)[1].splitlines()[0].strip()
        if language not in ('sql', 'plpgsql'):
            raise ValueError('0173 unexpected frozen function language')
        updates[definitions][coordinate] = (row['provolatile'], row['prosecdef'], language, tuple(row['proconfig'] or ()))
        updates[shapes][coordinate] = shape
        updates[digests][coordinate] = sha256(row['prosrc'].encode()).hexdigest()
    for name, change in DATA['tables'].items():
        old = {row['name']: row for row in change['before']['triggers']} if change['before'] else {}
        for row in change['after']['triggers']:
            if row['name'] in old:
                if old[row['name']] != row:
                    raise ValueError('0173 unexpected changed trigger')
                continue
            coordinate = (name, row['name'])
            if coordinate in updates['EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS']:
                raise ValueError('0173 duplicate trigger registration')
            function = row['function_signature'].split('(', 1)[0]
            constraint = row['definition'].startswith('CREATE CONSTRAINT TRIGGER ')
            value = (name, function, row['tgenabled'], row['tgtype'], constraint,
                row['tgdeferrable'], row['tginitdeferred'])
            updates['EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS'][coordinate] = value
            if name in ('audit_events', 'audit_chain_heads'):
                updates['EXPECTED_AUDIT_TRIGGERS'][coordinate] = (value[0], value[1], *value[3:])
    for privilege, registry in (('SELECT', 'RUNTIME_READ_TABLES'), ('INSERT', 'RUNTIME_INSERT_TABLES')):
        added = {name for name in TABLES if any(a['grantee'] == 'star_oam_api' and a['privilege'] == privilege
            for a in DATA['tables'][name]['after']['acl'])}
        if set(TABLES).intersection(namespace[registry]):
            raise ValueError('0173 table registration already present')
        updates[registry] = namespace[registry] | frozenset(added)
    catalogs = {name: overlay_catalog(namespace[name], CATALOG_SUFFIXES[name]) for name in CATALOGS}
    # The older verifier remains enabled. Patch its overlapping objects only
    # after checking their complete accepted predecessor definitions.
    stock = deepcopy(namespace['_stock_scrap_catalog'].DATA)
    for family in ('tables', 'functions'):
        for name, change in DATA[family].items():
            if name not in stock[family]:
                continue
            if stock[family][name]['after'] != change['before']:
                raise ValueError('0173 full scrap catalog predecessor changed: ' + name)
            stock[family][name]['after'] = deepcopy(change['after'])
    catalogs['_stock_scrap_catalog'] = stock
    condition = deepcopy(namespace['_condition_catalog'].DATA)
    for family in ('tables', 'functions'):
        for name, change in DATA[family].items():
            if name not in condition[family]:
                continue
            if condition[family][name]['after'] != change['before']:
                raise ValueError('0173 exact condition catalog predecessor changed: ' + name)
            condition[family][name]['after'] = deepcopy(change['after'])
    catalogs['_condition_catalog'] = condition
    closure = deepcopy(namespace['_closure_catalog'].DATA)
    for family in ('tables', 'functions'):
        for name, change in DATA[family].items():
            if name not in closure[family]:
                continue
            if closure[family][name]['after'] != change['before']:
                raise ValueError('0173 exact closure catalog predecessor changed: ' + name)
            closure[family][name]['after'] = deepcopy(change['after'])
    catalogs['_closure_catalog'] = closure
    previous = deepcopy(namespace['_remaining_cancel_catalog'].DATA)
    for family in ('tables', 'functions'):
        for name, change in DATA[family].items():
            if name not in previous[family]:
                continue
            if previous[family][name]['after'] != change['before']:
                raise ValueError('0173 exact remaining cancellation predecessor changed: ' + name)
            previous[family][name]['after'] = deepcopy(change['after'])
    catalogs['_remaining_cancel_catalog'] = previous
    predecessor = deepcopy(namespace['_rejection_return_catalog'].DATA)
    for family in ('tables', 'functions'):
        for name, change in DATA[family].items():
            if name not in predecessor[family]:
                continue
            if predecessor[family][name]['after'] != change['before']:
                raise ValueError('0173 exact registration predecessor changed: ' + name)
            predecessor[family][name]['after'] = deepcopy(change['after'])
    catalogs['_rejection_return_catalog'] = predecessor
    namespace.update(updates)
    for name, data in catalogs.items():
        namespace[name].DATA = data


def verify(db):
    """Full changed catalog including native FK guards, ACLs and overloads."""
    from .daily_reconciliation.capture_security import validate_capture_roles
    from .daily_reconciliation.capture_role_contract import ROLES

    verify_uuid(db)
    capture_configured = validate_capture_roles(db, allow_absent=True)
    actual = snapshot(db)
    for family in ('tables', 'functions'):
        for name, change in DATA[family].items():
            expected = deepcopy(change['after'])
            if family == 'tables' and capture_configured:
                # Optional bootstrap roles have a separately verified, complete
                # read-only contract. Add only those exact SELECT grants to a
                # local expectation; never filter actual ACLs or mutate the
                # frozen migration/runtime catalogs.
                expected['acl'].extend(
                    dict(grantee=role, privilege='SELECT', grantable=False)
                    for role, (tables, _) in ROLES.items() if name in tables)
                expected['acl'].sort(key=lambda item:
                    (item['grantee'], item['privilege'], item['grantable']))
            if actual[family].get(name) != expected:
                raise ValueError('0173 exact runtime ' + family + ' catalog mismatch: ' + name)
    names = {change['after']['proname'] for change in DATA['functions'].values()}
    found = {name for name, row in actual['functions'].items() if row['proname'] in names}
    if found != set(DATA['functions']):
        raise ValueError('0173 unexpected runtime function overload')
