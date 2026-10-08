"""Adversarial startup admission checks; no external DB or provider is used."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from app import key_provider_binding_security as security


def namespace():
    coordinate = ('existing_internal_guard', '')
    return {
        'RUNTIME_READ_TABLES': frozenset({'existing_read_table', 'kms_data_key_pins'}),
        'FORMAL_FILE_INTERNAL_FUNCTIONS': {coordinate: ('v', False, 'plpgsql', ())},
        'FORMAL_FILE_INTERNAL_FUNCTION_SHAPES': {coordinate: ('f', 'trigger', False)},
        'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256': {coordinate: 'a' * 64},
        'EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS': {
            'trg_kms_data_key_pins_immutable_0040': (
                'kms_data_key_pins', 'rsc_reject_kms_data_key_pin_mutation_0040', 'A', 27),
        },
    }


class ReadOnlyScalar:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def scalar(self, statement):
        self.calls.append(statement)
        return self.result


def install_snapshot(monkeypatch, value):
    calls = []

    def capture(db, *, table_names, function_names):
        calls.append((db, table_names, function_names))
        return deepcopy(value)

    monkeypatch.setattr(security, 'snapshot', capture)
    return calls


def test_frozen_catalog_bytes_are_admitted_before_any_runtime_registration(tmp_path):
    source = Path(security.__file__)
    path = tmp_path / 'tampered_binding_security.py'
    path.write_bytes(source.read_bytes())
    path.with_suffix('.json').write_bytes(security.RAW + b' ')
    spec = importlib.util.spec_from_file_location('app._tampered_binding_security', path)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ValueError, match='0180 key-provider catalog digest mismatch'):
        spec.loader.exec_module(module)


def test_verify_reads_exact_names_and_requires_correspondence_after_catalog(monkeypatch):
    calls = install_snapshot(monkeypatch, security.DATA)
    db = ReadOnlyScalar()
    security.verify(db)
    assert calls == [(db, ('application_key_version_claims', 'kms_data_key_pins', 'openbao_data_key_pins'),
                      ('rsc_reject_key_provider_binding_mutation_0180',
                       'rsc_guard_application_key_version_claim_0180',
                       'rsc_claim_application_key_version_0180'))]
    assert db.calls == [security._CORRESPONDENCE]


def drift_catalog(case):
    result = deepcopy(security.DATA)
    tables, functions = result['tables'], result['functions']
    claims = tables['application_key_version_claims']
    pin = tables['openbao_data_key_pins']
    fn = functions['rsc_claim_application_key_version_0180()']
    if case == 'missing_new_table':
        del tables['openbao_data_key_pins']
    elif case == 'missing_legacy_table':
        del tables['kms_data_key_pins']
    elif case == 'missing_claim_function':
        del functions['rsc_claim_application_key_version_0180()']
    elif case == 'same_name_overload':
        functions['rsc_claim_application_key_version_0180(text)'] = deepcopy(fn)
    elif case == 'missing_claim_trigger':
        pin['triggers'] = [r for r in pin['triggers'] if r['tgtype'] != 5]
    elif case == 'disabled_legacy_claim_trigger':
        next(r for r in tables['kms_data_key_pins']['triggers'] if r['tgtype'] == 5)['tgenabled'] = 'D'
    elif case == 'deferred_uniqueness':
        next(r for r in claims['indexes'] if r['indisunique'])['indimmediate'] = False
    elif case == 'unvalidated_constraint':
        claims['constraints'][0]['convalidated'] = False
    elif case == 'extra_public_table_grant':
        pin['acl'].append({'grantee': 'PUBLIC', 'privilege': 'SELECT', 'grantable': False})
    elif case == 'api_column_update':
        pin['columns'][0]['acl'].append({'grantee': 'star_oam_api', 'privilege': 'UPDATE', 'grantable': False})
    elif case == 'api_function_execute':
        fn['acl'].append({'grantee': 'star_oam_api', 'privilege': 'EXECUTE', 'grantable': False})
    elif case == 'security_definer':
        fn['prosecdef'] = True
    elif case == 'wrong_owner':
        fn['owner'] = 'star_oam_api'
    elif case == 'extra_trigger':
        pin['triggers'].append(dict(pin['triggers'][0], name='unreviewed_alias'))
    elif case == 'function_body_changed':
        fn['prosrc'] += '-- drift'
    else:
        raise AssertionError(case)
    return result


@pytest.mark.parametrize('case', [
    'missing_new_table', 'missing_legacy_table', 'missing_claim_function',
    'same_name_overload', 'missing_claim_trigger', 'disabled_legacy_claim_trigger',
    'deferred_uniqueness', 'unvalidated_constraint', 'extra_public_table_grant',
    'api_column_update', 'api_function_execute', 'security_definer', 'wrong_owner',
    'extra_trigger', 'function_body_changed',
])
def test_catalog_drift_blocks_before_reading_pin_claim_rows(monkeypatch, case):
    install_snapshot(monkeypatch, drift_catalog(case))
    db = ReadOnlyScalar()
    with pytest.raises(ValueError, match='0180 key-provider full catalog mismatch'):
        security.verify(db)
    assert db.calls == []


@pytest.mark.parametrize('result', [False, None, 0, 1, 'true', [], object()])
def test_missing_unknown_or_truthy_nonboolean_correspondence_is_not_admitted(monkeypatch, result):
    install_snapshot(monkeypatch, security.DATA)
    db = ReadOnlyScalar(result)
    with pytest.raises(ValueError, match='0180 key-provider pin/claim correspondence mismatch'):
        security.verify(db)
    assert db.calls == [security._CORRESPONDENCE]


@pytest.mark.parametrize('case,expected', [
    ('empty', True), ('exact_mixed', True), ('missing_claim', False),
    ('extra_claim', False), ('wrong_provider', False), ('wrong_digest', False),
    ('wrong_creation_time', False), ('duplicate_pin', False), ('duplicate_claim', False),
])
def test_correspondence_relational_query_detects_nonbijective_bindings(case, expected):
    # Exercise the actual set/count query on a local synthetic relational store.
    # Only PostgreSQL's two literal varchar casts are removed for SQLite; this
    # is query logic evidence, not PostgreSQL role/trigger/type evidence.
    sql = str(security._CORRESPONDENCE)
    for provider in ('aliyun_kms', 'openbao_transit_v1'):
        literal = "'" + provider + "'::varchar"
        assert sql.count(literal) == 1
        sql = sql.replace(literal, "'" + provider + "'")
    with sqlite3.connect(':memory:') as db:
        db.execute("ATTACH DATABASE ':memory:' AS public")
        for table in ('kms_data_key_pins', 'openbao_data_key_pins'):
            db.execute(f'CREATE TABLE public.{table} (purpose TEXT, application_key_version INTEGER, ciphertext_sha256 TEXT, created_at TEXT)')
        db.execute('CREATE TABLE public.application_key_version_claims (purpose TEXT, application_key_version INTEGER, provider TEXT, ciphertext_sha256 TEXT, created_at TEXT)')
        if case != 'empty':
            db.execute("INSERT INTO public.kms_data_key_pins VALUES ('authentication_idempotency',1,'a','t1')")
            db.execute("INSERT INTO public.openbao_data_key_pins VALUES ('material_request_contact',2,'b','t2')")
            db.execute("INSERT INTO public.application_key_version_claims VALUES ('authentication_idempotency',1,'aliyun_kms','a','t1'),('material_request_contact',2,'openbao_transit_v1','b','t2')")
        mutations = {
            'missing_claim': 'DELETE FROM public.application_key_version_claims WHERE application_key_version=1',
            'extra_claim': "INSERT INTO public.application_key_version_claims VALUES ('authentication_idempotency',3,'aliyun_kms','c','t3')",
            'wrong_provider': "UPDATE public.application_key_version_claims SET provider='openbao_transit_v1' WHERE application_key_version=1",
            'wrong_digest': "UPDATE public.application_key_version_claims SET ciphertext_sha256='c' WHERE application_key_version=1",
            'wrong_creation_time': "UPDATE public.application_key_version_claims SET created_at='changed' WHERE application_key_version=1",
            'duplicate_pin': 'INSERT INTO public.kms_data_key_pins SELECT * FROM public.kms_data_key_pins',
            'duplicate_claim': 'INSERT INTO public.application_key_version_claims SELECT * FROM public.application_key_version_claims WHERE application_key_version=1',
        }
        if case in mutations:
            db.execute(mutations[case])
        assert bool(db.execute(sql).fetchone()[0]) is expected


def test_register_adds_only_read_bindings_and_preserves_unrelated_contracts():
    current = namespace()
    before = deepcopy(current)
    frozen = deepcopy(security.DATA)
    security.register(current)
    assert current['RUNTIME_READ_TABLES'] == before['RUNTIME_READ_TABLES'] | {'application_key_version_claims', 'openbao_data_key_pins'}
    for registry in ('FORMAL_FILE_INTERNAL_FUNCTIONS', 'FORMAL_FILE_INTERNAL_FUNCTION_SHAPES', 'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256'):
        for key, value in before[registry].items():
            assert current[registry][key] == value
        assert set(current[registry]) - set(before[registry]) == {(name, '') for name in security.FUNCTIONS}
    for name in security.FUNCTIONS:
        assert current['FORMAL_FILE_INTERNAL_FUNCTION_SHAPES'][(name, '')] == ('f', 'trigger', False)
        assert current['FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256'][(name, '')] == sha256(security.DATA['functions'][name + '()']['prosrc'].encode()).hexdigest()
    for key, value in before['EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS'].items():
        assert current['EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS'][key] == value
    assert current['EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS']['trg_kms_data_key_pins_claim_0180'] == (
        'kms_data_key_pins', 'rsc_claim_application_key_version_0180', 'A', 5)
    assert security.DATA == frozen


@pytest.mark.parametrize('case', ['table', 'function', 'legacy_trigger', 'shape_coordinate', 'hash_coordinate'])
def test_register_refuses_existing_coordinates_without_any_partial_updates(case):
    current = namespace()
    coordinate = security.FUNCTIONS[-1], ''
    if case == 'table':
        current['RUNTIME_READ_TABLES'] |= {'openbao_data_key_pins'}
    elif case == 'legacy_trigger':
        current['EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS']['trg_kms_data_key_pins_claim_0180'] = ('old',)
    else:
        registry = {'function': 'FORMAL_FILE_INTERNAL_FUNCTIONS', 'shape_coordinate': 'FORMAL_FILE_INTERNAL_FUNCTION_SHAPES', 'hash_coordinate': 'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256'}[case]
        current[registry][coordinate] = 'existing coordinate must not be replaced'
    before = deepcopy(current)
    with pytest.raises(ValueError):
        security.register(current)
    assert current == before


@pytest.mark.parametrize('registry', ['FORMAL_FILE_INTERNAL_FUNCTION_SHAPES', 'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256'])
def test_register_incomplete_namespace_is_rejected_before_mutation(registry):
    current = namespace()
    del current[registry]
    before = deepcopy(current)
    with pytest.raises((ValueError, KeyError, TypeError)):
        security.register(current)
    assert current == before


@pytest.mark.parametrize('registry', [
    'RUNTIME_READ_TABLES', 'FORMAL_FILE_INTERNAL_FUNCTIONS',
    'FORMAL_FILE_INTERNAL_FUNCTION_SHAPES', 'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256',
    'EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS',
])
def test_register_invalid_namespace_container_is_rejected_before_mutation(registry):
    current = namespace()
    current[registry] = []
    before = deepcopy(current)
    with pytest.raises((ValueError, KeyError, TypeError)):
        security.register(current)
    assert current == before


def test_production_registration_keeps_bindings_read_only_and_functions_internal():
    from app import database_security as integrated

    for table in ('application_key_version_claims', 'openbao_data_key_pins'):
        assert integrated._expected_table_privileges(table) == frozenset({'SELECT'})
        assert table not in integrated.RUNTIME_UPDATE_COLUMNS
    for function in security.FUNCTIONS:
        coordinate = function, ''
        assert coordinate in integrated.FORMAL_FILE_INTERNAL_FUNCTIONS
        assert coordinate not in integrated.RUNTIME_EXECUTE_FUNCTIONS
    assert integrated._key_provider_binding_catalog is security


@pytest.mark.parametrize('case', ['extra_top_key', 'missing_table', 'missing_function', 'function_overload'])
def test_register_requires_exact_catalog_object_identity(monkeypatch, case):
    data = deepcopy(security.DATA)
    if case == 'extra_top_key':
        data['unreviewed'] = {}
    elif case == 'missing_table':
        del data['tables']['kms_data_key_pins']
    elif case == 'missing_function':
        del data['functions'][security.FUNCTIONS[0] + '()']
    else:
        data['functions'][security.FUNCTIONS[0] + '(text)'] = data['functions'][security.FUNCTIONS[0] + '()']
    monkeypatch.setattr(security, 'DATA', data)
    current = namespace()
    before = deepcopy(current)
    with pytest.raises(ValueError, match='0180 exact catalog identity set required'):
        security.register(current)
    assert current == before


@pytest.mark.parametrize('field,value', [
    ('owner', 'star_oam_api'), ('prosecdef', True), ('proconfig', ['search_path=public']),
    ('provolatile', 's'), ('identity_arguments', 'x text'),
    ('acl', [{'grantee': 'PUBLIC', 'privilege': 'EXECUTE', 'grantable': False}]),
])
def test_register_function_contract_drift_is_rejected_before_any_updates(monkeypatch, field, value):
    data = deepcopy(security.DATA)
    data['functions'][security.FUNCTIONS[-1] + '()'][field] = value
    monkeypatch.setattr(security, 'DATA', data)
    current = namespace()
    before = deepcopy(current)
    with pytest.raises(ValueError, match='0180 unexpected binding function contract'):
        security.register(current)
    assert current == before


def test_register_nonstring_source_fails_hash_precomputation_without_partial_updates(monkeypatch):
    data = deepcopy(security.DATA)
    # The final function fails after earlier functions have been validated, so
    # this also detects incremental writes hidden inside the validation loop.
    data['functions'][security.FUNCTIONS[-1] + '()']['prosrc'] = None
    monkeypatch.setattr(security, 'DATA', data)
    current = namespace()
    before = deepcopy(current)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        security.register(current)
    assert current == before
