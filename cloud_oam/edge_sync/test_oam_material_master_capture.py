"""Synthetic source pages; no shared credentials, browser or OAM calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('material_capture_under_test',
    Path(__file__).with_name('oam_material_master_capture.py'))
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
from app import material_master_capture_evidence as evidence

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)
SOURCE = 'synthetic-edge'


def material(code='SKU-1', **changes):
    return dict(materialCode=code, materialName='Synthetic spare', unitCode='EA', unitName='piece',
                materialStatus=0, **changes)


class Source:
    def __init__(self, rows=None):
        self.rows = [material('SKU-2'), material()] if rows is None else rows
        self.calls = []

    def __call__(self, path, query):
        assert path == evidence.ENDPOINT
        assert set(query) == {'page', 'size'}
        self.calls.append((path, deepcopy(query)))
        offset = (query['page'] - 1) * query['size']
        return dict(success=True, model=dict(amount=len(self.rows), result=deepcopy(self.rows[offset:offset + query['size']])))


def collect(source=None, **kwargs):
    return capture.collect(source_instance=SOURCE, read_page=source or Source(), page_size=1,
                           clock=lambda: NOW, **kwargs)


def inspect(bundle):
    return evidence.validate_capture(bundle, expected_source_instance=SOURCE, now=NOW)


def test_complete_capture_retains_exact_fields_versions_and_unknown_time():
    raw = material(isSnEnable=False, regularModel=None, privateCredential='never-serialize-this', updateTime='undocumented')
    source = Source([raw, material('SKU-2')])
    bundle = collect(source)
    report = inspect(bundle)
    assert len(source.calls) == 4
    assert report.observed_count == 2
    assert bundle['records'][0]['data']['materialStatus'] == 0
    assert type(bundle['records'][0]['data']['materialStatus']) is int
    assert bundle['records'][0]['data']['isSnEnable'] is False
    assert bundle['records'][0]['data']['regularModel'] is None
    assert bundle['records'][0]['source_updated_at'] is None
    assert bundle['records'][0]['source_version'] == 'mm-v1:' + evidence.digest(bundle['records'][0]['data'])
    assert 'privateCredential' not in evidence.canonical(bundle).decode()
    assert 'never-serialize-this' not in evidence.canonical(bundle).decode()
    assert 'updateTime' not in bundle['records'][0]['data']
    assert report.coverage == 'spare_endpoint_visible_rows'
    assert not any((report.source_authenticated, report.full_catalog_verified,
                    report.master_source_evidence_verified, report.projection_published, report.start_ready))


def test_explicit_empty_requires_two_successful_empty_pages_and_is_not_catalog_deletion():
    source = Source([])
    bundle = collect(source)
    assert len(source.calls) == 2
    assert bundle['empty_observation'] is True and bundle['records'] == []
    assert inspect(bundle).observed_count == 0
    assert not inspect(bundle).full_catalog_verified


@pytest.mark.parametrize('field', sorted(evidence.REQUIRED_FIELDS))
def test_missing_required_fields_stop_without_alias_fallback(field):
    row = material()
    del row[field]
    row.update(platformMaterialCode='SKU-1', name='Synthetic alias', status='active', unit='piece')
    source = Source([row])
    with pytest.raises(evidence.MaterialMasterCaptureError, match='required_field_missing'):
        collect(source)
    assert len(source.calls) == 1


@pytest.mark.parametrize('field,value', [
    ('materialCode', 'sku-1'), ('materialCode', ' SKU-1'), ('materialCode', 'SKU-1 '),
    ('materialCode', 'SKU\n1'), ('materialCode', True), ('materialCode', ''),
    ('materialName', ''), ('materialName', 'x' * 201), ('materialName', None),
    ('unitName', ''), ('unitCode', 'EA '), ('unitCode', 1),
    ('materialStatus', None), ('materialStatus', True), ('materialStatus', 0.0),
    ('materialStatus', []), ('materialStatus', ' inactive '), ('materialStatus', 2**31),
    ('regularModel', {}), ('regularModel', True), ('regularModel', 'x' * 301),
    ('isSnEnable', 2), ('isSnEnable', 1.0), ('isSnEnable', []),
])
def test_ambiguous_types_or_noncanonical_values_are_not_coerced(field, value):
    row = material()
    row[field] = value
    with pytest.raises(evidence.MaterialMasterCaptureError):
        collect(Source([row]))


@pytest.mark.parametrize('value', [0, '0', 1, 'active', 'inactive'])
def test_status_is_raw_and_no_value_is_treated_as_active(value):
    row = material()
    row['materialStatus'] = value
    bundle = collect(Source([row]))
    assert type(bundle['records'][0]['data']['materialStatus']) is type(value)
    assert bundle['records'][0]['data']['materialStatus'] == value
    assert not inspect(bundle).master_source_evidence_verified


@pytest.mark.parametrize('kind', ['response_missing', 'success_string', 'model_missing', 'total_bool',
    'total_float', 'total_negative', 'total_too_large', 'result_missing', 'result_object', 'row_object',
    'short_page', 'overfull_page', 'changed_total', 'duplicate', 'timeout'])
def test_source_errors_are_not_empty_or_retried_and_never_echo_payload(kind):
    source = Source()
    def broken(path, query):
        value = source(path, query)
        if kind == 'timeout': raise RuntimeError('private-source-value')
        if kind == 'response_missing': return None
        if kind == 'success_string': value['success'] = 'true'
        if kind == 'model_missing': value.pop('model')
        elif kind == 'total_bool': value['model']['amount'] = True
        elif kind == 'total_float': value['model']['amount'] = 2.0
        elif kind == 'total_negative': value['model']['amount'] = -1
        elif kind == 'total_too_large': value['model']['amount'] = evidence.MAX_RECORDS + 1
        elif kind == 'result_missing': value['model'].pop('result')
        elif kind == 'result_object': value['model']['result'] = {}
        elif kind == 'row_object': value['model']['result'] = [None]
        elif kind == 'short_page': value['model']['result'] = []
        elif kind == 'overfull_page': value['model']['result'] *= 2
        elif kind == 'changed_total' and query['page'] == 2: value['model']['amount'] = 3
        elif kind == 'duplicate' and query['page'] == 2: value['model']['result'][0]['materialCode'] = 'SKU-2'
        return value
    with pytest.raises(evidence.MaterialMasterCaptureError) as caught:
        collect(broken)
    assert 'private-source-value' not in str(caught.value)
    assert len(source.calls) <= 2


@pytest.mark.parametrize('kind', ['name', 'unit', 'status_type', 'missing_optional', 'remove', 'add'])
def test_same_total_changes_and_membership_changes_between_scans_fail(kind):
    source = Source([material(isSnEnable=0), material('SKU-2')])
    def changed(path, query):
        if len(source.calls) == 2:
            if kind == 'name': source.rows[0]['materialName'] = 'Changed'
            if kind == 'unit': source.rows[0]['unitName'] = 'box'
            if kind == 'status_type': source.rows[0]['materialStatus'] = '0'
            if kind == 'missing_optional': source.rows[0].pop('isSnEnable')
            if kind == 'remove': source.rows.pop()
            if kind == 'add': source.rows.append(material('SKU-3'))
        return source(path, query)
    with pytest.raises(evidence.MaterialMasterCaptureError, match='source_changed_between_scans'):
        collect(changed)


def test_reordered_pages_between_scans_are_valid_when_exact_record_set_matches():
    source = Source()
    def reordered(path, query):
        if len(source.calls) == 2: source.rows.reverse()
        return source(path, query)
    bundle = collect(reordered)
    assert bundle['scans'][0]['pages'][0]['material_codes'] != bundle['scans'][1]['pages'][0]['material_codes']
    assert inspect(bundle).observed_count == 2


@pytest.mark.parametrize('kind', ['backwards', 'naive', 'expired'])
def test_capture_clock_and_duration_are_enforced(kind):
    moments = [NOW] * 40
    if kind == 'backwards': moments[2] = NOW - timedelta(seconds=1)
    if kind == 'naive': moments[2] = NOW.replace(tzinfo=None)
    if kind == 'expired': moments[2:] = [NOW + timedelta(minutes=46)] * 38
    iterator = iter(moments)
    with pytest.raises(evidence.MaterialMasterCaptureError):
        capture.collect(source_instance=SOURCE, read_page=Source(), clock=lambda: next(iterator))


@pytest.mark.parametrize('kind', ['binding', 'schema', 'extra', 'source_time', 'source_version', 'record_hash',
    'record_order', 'record_duplicate', 'manifest_count_bool', 'zero_bool', 'missing_scan', 'page_total_bool',
    'page_duplicate', 'page_hash', 'missing_page', 'scan_sequence', 'future', 'envelope_time', 'raw_extra'])
def test_offline_verifier_reconstructs_evidence_instead_of_trusting_report(kind):
    bundle = collect()
    if kind == 'binding': bundle['binding']['source_instance'] = 'other'
    if kind == 'schema': bundle['field_contract'] = 'unreviewed'
    if kind == 'extra': bundle['source_authenticated'] = True
    if kind == 'source_time': bundle['records'][0]['source_updated_at'] = evidence.stamp(NOW)
    if kind == 'source_version': bundle['records'][0]['source_version'] = 'forged'
    if kind == 'record_hash': bundle['records'][0]['data']['materialName'] = 'Changed'
    if kind == 'record_order': bundle['records'].reverse()
    if kind == 'record_duplicate': bundle['records'][1] = deepcopy(bundle['records'][0])
    if kind == 'manifest_count_bool': bundle['observed_count'] = True
    if kind == 'zero_bool': bundle['empty_observation'] = 0
    if kind == 'missing_scan': bundle['scans'].pop()
    if kind == 'page_total_bool': bundle['scans'][0]['pages'][0]['total'] = True
    if kind == 'page_duplicate': bundle['scans'][0]['pages'][1]['material_codes'] = bundle['scans'][0]['pages'][0]['material_codes']
    if kind == 'page_hash': bundle['scans'][1]['pages'][0]['accepted_rows_sha256'] = '0' * 64
    if kind == 'missing_page': bundle['scans'][1]['pages'].pop()
    if kind == 'scan_sequence': bundle['scans'][0]['sequence'] = True
    if kind == 'future': bundle['completed_at'] = evidence.stamp(NOW + timedelta(seconds=1))
    if kind == 'envelope_time': bundle['started_at'] = evidence.stamp(NOW - timedelta(seconds=1))
    if kind == 'raw_extra': bundle['records'][0]['data']['unapproved'] = 'private'
    with pytest.raises(evidence.MaterialMasterCaptureError): inspect(bundle)


def test_document_limits_and_duplicate_json_keys(tmp_path, monkeypatch):
    path = tmp_path / 'duplicate.json'
    path.write_text('{"private":1,"private":2}')
    with pytest.raises(evidence.MaterialMasterCaptureError, match='duplicate_json_key'): capture.read_capture(path)
    path.write_text('{"value":NaN}')
    with pytest.raises(evidence.MaterialMasterCaptureError, match='invalid_number'): capture.read_capture(path)
    monkeypatch.setattr(capture, 'MAX_BYTES', 8)
    with pytest.raises(evidence.MaterialMasterCaptureError, match='document_too_large'): capture.read_capture(path)
    with pytest.raises(evidence.MaterialMasterCaptureError, match='document_too_large'): collect()


def test_private_archive_reopens_for_independent_inspection_and_never_overwrites(tmp_path):
    tmp_path.chmod(0o700)
    bundle = collect()
    pointer = capture.archive(bundle, directory=tmp_path, expected_source_instance=SOURCE, now=NOW)
    target = tmp_path / pointer['file']
    assert target.stat().st_mode & 0o777 == 0o600
    assert evidence.digest(capture.read_capture(target)) == pointer['sha256']
    assert inspect(capture.read_capture(target)) == inspect(bundle)
    before = target.read_bytes()
    with pytest.raises(evidence.MaterialMasterCaptureError, match='archive_unconfirmed'):
        capture.archive(bundle, directory=tmp_path, expected_source_instance=SOURCE, now=NOW)
    assert target.read_bytes() == before


@pytest.mark.parametrize('kind', ['public', 'symlink', 'file', 'missing'])
def test_archive_rejects_unsafe_paths_before_writing(tmp_path, kind):
    target = tmp_path / 'capture'
    if kind == 'public': target.mkdir(mode=0o755)
    if kind == 'symlink': target.symlink_to(tmp_path, target_is_directory=True)
    if kind == 'file': target.write_text('unchanged')
    with pytest.raises(evidence.MaterialMasterCaptureError, match='unsafe_archive_directory'):
        capture.archive(collect(), directory=target, expected_source_instance=SOURCE, now=NOW)
    if kind == 'file': assert target.read_text() == 'unchanged'


def test_archive_sync_failure_keeps_unknown_file_and_blocks_replay(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    bundle = collect()
    with monkeypatch.context() as patch:
        patch.setattr(capture.os, 'fsync', lambda _: (_ for _ in ()).throw(OSError('private')))
        with pytest.raises(evidence.MaterialMasterCaptureError, match='archive_unconfirmed'):
            capture.archive(bundle, directory=tmp_path, expected_source_instance=SOURCE, now=NOW)
    assert len(list(tmp_path.iterdir())) == 1
    with pytest.raises(evidence.MaterialMasterCaptureError, match='archive_unconfirmed'):
        capture.archive(bundle, directory=tmp_path, expected_source_instance=SOURCE, now=NOW)


def test_cli_capture_health_archive_and_offline_readback(tmp_path, monkeypatch, capsys):
    tmp_path.chmod(0o700)
    source, calls = Source(), []
    monkeypatch.setattr(capture, '_local_transport', lambda: source)
    monkeypatch.setattr(capture, '_edge_preflight', lambda: calls.append('original-edge'))
    monkeypatch.setattr(capture, '_health', lambda: calls.append('health'))
    assert capture.main(['--capture', '--source-instance', SOURCE, '--archive-dir', str(tmp_path), '--page-size', '1']) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == ['original-edge', 'health'] and len(source.calls) == 4
    assert result['report']['observed_count'] == 2
    assert 'Synthetic spare' not in json.dumps(result)
    monkeypatch.setattr(capture, '_local_transport', lambda: pytest.fail('inspection must not contact source'))
    monkeypatch.setattr(capture, '_edge_preflight', lambda: pytest.fail('inspection must not inspect Edge'))
    monkeypatch.setattr(capture, '_health', lambda: pytest.fail('inspection must not check login'))
    assert capture.main(['--inspect-file', str(tmp_path / result['archive']['file']), '--source-instance', SOURCE]) == 0
    reviewed = json.loads(capsys.readouterr().out)
    assert reviewed['report']['capture_sha256'] == result['report']['capture_sha256']
    assert reviewed['archive'] is None


def test_cli_configuration_fails_before_any_source_contact(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(capture, '_local_transport', lambda: pytest.fail('invalid configuration'))
    assert capture.main(['--capture', '--source-instance', SOURCE]) == 1
    assert 'archive_directory_required' in capsys.readouterr().err
    assert capture.main(['--capture', '--source-instance', SOURCE, '--archive-dir', str(tmp_path / 'missing')]) == 1
    assert 'unsafe_archive_directory' in capsys.readouterr().err
    assert capture.main(['--inspect-file', 'missing', '--source-instance', SOURCE, '--archive-dir', str(tmp_path)]) == 1
    assert 'inspection_arguments_conflict' in capsys.readouterr().err


def test_help_works_without_business_client_or_database():
    result = subprocess.run([sys.executable, str(Path(capture.__file__)), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert '--capture' in result.stdout and '--inspect-file' in result.stdout


@pytest.mark.parametrize('payload,code,expected', [
    ({'ok': True, 'components': {'oam': {'status': 'ok'}}}, 0, None),
    ({'ok': True}, 0, 'source_health_unconfirmed'),
    ({'ok': True, 'components': None}, 0, 'source_health_unconfirmed'),
    ({'ok': True, 'components': {'oam': {'status': 'transport_error'}}}, 0, 'source_health_unconfirmed'),
    ({'ok': False, 'components': {'oam': {'status': 'auth_required'}}}, 1, 'source_auth_required'),
    ({'ok': False, 'components': {'oam': {'status': 'page_missing'}}}, 1, 'source_health_unconfirmed'),
    ({'ok': True, 'components': {'oam': {'status': 'ok'}}}, 1, 'source_health_unconfirmed'),
])
def test_only_exact_live_oam_health_can_prove_authentication_failure(monkeypatch, payload, code, expected):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[-2:] == ['--require', 'oam'] and kwargs['timeout'] == 60
        return SimpleNamespace(returncode=code, stdout=json.dumps(payload), stderr='private-source-value')
    monkeypatch.setattr(capture.subprocess, 'run', run)
    if expected:
        with pytest.raises(evidence.MaterialMasterCaptureError, match=expected): capture._health()
    else:
        capture._health()
    assert len(calls) == 1


def test_local_transport_is_existing_shared_client_exact_read_endpoint_and_no_retries(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    (work / 'inventory_query_portal').mkdir(parents=True)
    for name in ('oam_shared_session.py', 'global_business_session_health.py', 'inventory_query_portal/oam_read_client.py'):
        (work / name).write_text('# synthetic import boundary')
    module = ModuleType('inventory_query_portal.oam_read_client')
    calls = []
    module.post_json = lambda *a, **k: calls.append((a, k)) or {'synthetic': True}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(capture, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    read_page = capture._local_transport()
    assert read_page(evidence.ENDPOINT, {'page': 1, 'size': 1000}) == {'synthetic': True}
    assert calls == [((evidence.ENDPOINT, {'page': 1, 'size': 1000}), {'referer': '/warehouse/stock/list', 'retries': 0})]
    with pytest.raises(evidence.MaterialMasterCaptureError, match='source_query_not_allowed'):
        read_page('/material_type/erp_list', {'page': 1, 'size': 1000})
    with pytest.raises(evidence.MaterialMasterCaptureError, match='source_query_not_allowed'):
        read_page(evidence.ENDPOINT, {'page': 1, 'size': 1000, 'materialCode': 'SKU-1'})
    assert len(calls) == 1


def test_missing_unmanaged_client_does_not_start_a_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, 'ROOT', tmp_path)
    with pytest.raises(evidence.MaterialMasterCaptureError, match='local_read_client_unavailable'):
        capture._local_transport()


@pytest.mark.parametrize('kind', ['valid', 'other_browser', 'other_profile', 'other_port', 'unconfirmed', 'failed', 'timeout'])
def test_original_edge_ownership_preflight_requires_installed_exact_route(monkeypatch, kind):
    value = dict(ok=True, browser='original Mac Edge', cdp='http://127.0.0.1:9224', compatibility='validated',
        profile=str(Path.home() / 'Library' / 'Application Support' / 'Microsoft Edge'))
    if kind == 'other_browser': value['browser'] = 'other browser'
    if kind == 'other_profile': value['profile'] = '/synthetic/other-profile'
    if kind == 'other_port': value['cdp'] = 'http://127.0.0.1:9226'
    if kind == 'unconfirmed': value['compatibility'] = 'unknown'
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert Path(command[1]).name == 'ensure_local_edge.py'
        assert command[-1] == '--compatibility'
        assert kwargs['timeout'] == 60
        if kind == 'timeout': raise subprocess.TimeoutExpired(command, 60)
        return SimpleNamespace(returncode=1 if kind == 'failed' else 0, stdout=json.dumps(value), stderr='private')
    monkeypatch.setattr(capture.subprocess, 'run', run)
    if kind == 'valid': capture._edge_preflight()
    else:
        with pytest.raises(evidence.MaterialMasterCaptureError, match='original_edge_unconfirmed'):
            capture._edge_preflight()
    assert len(calls) == 1


def test_wrong_edge_route_stops_before_health_source_or_archive(tmp_path, monkeypatch, capsys):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(capture, '_local_transport', lambda: lambda *a: pytest.fail('source read'))
    monkeypatch.setattr(capture, '_edge_preflight', lambda: evidence.fail('original_edge_unconfirmed'))
    monkeypatch.setattr(capture, '_health', lambda: pytest.fail('health probe'))
    assert capture.main(['--capture', '--source-instance', SOURCE, '--archive-dir', str(tmp_path)]) == 1
    assert 'original_edge_unconfirmed' in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
