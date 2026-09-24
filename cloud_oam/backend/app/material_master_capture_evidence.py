"""OAM spare-master observation contract; hashes are not source authentication.

This module has no database, transport or projection side effects. The spare
endpoint's visible rows do not establish coverage of the full ERP catalogue.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import UUID

SCHEMA = 'rsc.oam_material_master_capture.v1'
FIELD_CONTRACT = 'rsc.oam_spare_master_fields.v1'
ENDPOINT = '/material_type/spare_list'
SCOPE = 'material:spares-visible'
MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 100000
MAX_PAGES = 10000
FRESHNESS = timedelta(minutes=45)
SOURCE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
MATERIAL_CODE = re.compile(r'[A-Z0-9][A-Z0-9._/-]{0,79}')
REQUIRED_FIELDS = {'materialCode', 'materialName', 'unitCode', 'unitName', 'materialStatus'}
OPTIONAL_FIELDS = {'regularModel', 'isSnEnable'}


class MaterialMasterCaptureError(RuntimeError):
    """Payload-free errors may be displayed without exposing source data."""


def fail(code):
    raise MaterialMasterCaptureError('material_master_' + code)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        fail('invalid_document')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        fail('invalid_shape')


def _text(value, limit):
    if type(value) is not str or not value or value != value.strip() or len(value) > limit \
            or any(ord(c) < 32 or ord(c) == 127 for c in value):
        fail('invalid_field')


def source_binding(source_instance):
    if type(source_instance) is not str or not SOURCE_ID.fullmatch(source_instance):
        fail('invalid_source_instance')
    return dict(source_system='starcharge_oam', source_instance=source_instance,
                scope_key=SCOPE, endpoint=ENDPOINT)


def accepted_row(raw):
    """Retain exact typed fields; never prefer aliases, trim codes or infer status."""
    if type(raw) is not dict or not REQUIRED_FIELDS <= raw.keys():
        fail('required_field_missing')
    data = {key: raw[key] for key in sorted(REQUIRED_FIELDS | OPTIONAL_FIELDS) if key in raw}
    _text(data['materialCode'], 80)
    if not MATERIAL_CODE.fullmatch(data['materialCode']):
        fail('invalid_material_code')
    for key, limit in (('materialName', 200), ('unitCode', 32), ('unitName', 32)):
        _text(data[key], limit)
    status = data['materialStatus']
    if type(status) is str:
        _text(status, 64)
    elif type(status) is not int or not -(2**31) <= status < 2**31:
        fail('invalid_material_status')
    if 'regularModel' in data and data['regularModel'] is not None and data['regularModel'] != '':
        _text(data['regularModel'], 300)
    if 'isSnEnable' in data and data['isSnEnable'] is not None:
        value = data['isSnEnable']
        if type(value) is str:
            _text(value, 32)
        elif type(value) is not bool and (type(value) is not int or value not in (0, 1)):
            fail('invalid_serial_flag')
    canonical(data)
    return data


def material_record(data):
    checked = accepted_row(data)
    if checked != data or set(checked) != set(data):
        fail('unapproved_field')
    return dict(external_id=checked['materialCode'], source_updated_at=None,
                source_version='mm-v1:' + digest(checked), data=checked)


def stamp(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        fail('invalid_clock')
    return value.astimezone(timezone.utc).isoformat()


def _time(value):
    try:
        parsed = datetime.fromisoformat(value) if type(value) is str else None
        if stamp(parsed) != value:
            fail('invalid_timestamp')
        return parsed
    except (ValueError, TypeError, OverflowError):
        fail('invalid_timestamp')


@dataclass(frozen=True, slots=True)
class MaterialMasterCaptureReport:
    capture_id: str
    source_instance: str
    observed_count: int
    records_sha256: str
    capture_sha256: str
    oldest_capture_age_seconds: float
    status: str = field(default='observation_consistent', init=False)
    coverage: str = field(default='spare_endpoint_visible_rows', init=False)
    source_authenticated: bool = field(default=False, init=False)
    full_catalog_verified: bool = field(default=False, init=False)
    master_source_evidence_verified: bool = field(default=False, init=False)
    projection_published: bool = field(default=False, init=False)
    start_ready: bool = field(default=False, init=False)


def validate_capture(bundle, *, expected_source_instance, now):
    """Check the independently supplied source binding and both observed scans.

    Matching scans detect observed changes, not all concurrent source mutations.
    An authenticated receiver and authorized publisher must establish trust later.
    """
    if len(canonical(bundle)) > MAX_BYTES:
        fail('document_too_large')
    _keys(bundle, ('schema_version', 'field_contract', 'binding', 'capture_id', 'started_at',
                  'completed_at', 'page_size', 'scans', 'records', 'records_sha256',
                  'observed_count', 'empty_observation'))
    if bundle['schema_version'] != SCHEMA or bundle['field_contract'] != FIELD_CONTRACT:
        fail('unsupported_contract')
    if bundle['binding'] != source_binding(expected_source_instance):
        fail('binding_mismatch')
    try:
        value = bundle['capture_id']
        if type(value) is not str or str(UUID(value)) != value:
            fail('invalid_capture_id')
    except (ValueError, TypeError):
        fail('invalid_capture_id')
    now = _time(stamp(now))
    started, completed = (_time(bundle[key]) for key in ('started_at', 'completed_at'))
    if not started <= completed <= now:
        fail('invalid_capture_interval')
    if now - started > FRESHNESS:
        fail('capture_expired')
    size = bundle['page_size']
    if type(size) is not int or not 1 <= size <= 1000:
        fail('invalid_page_size')
    rows = bundle['records']
    if type(rows) is not list or len(rows) > MAX_RECORDS:
        fail('invalid_records')
    by_code = {}
    for row in rows:
        _keys(row, ('external_id', 'source_updated_at', 'source_version', 'data'))
        if row != material_record(row['data']):
            fail('record_mismatch')
        code = row['external_id']
        if code in by_code:
            fail('duplicate_material')
        by_code[code] = row
    if list(by_code) != sorted(by_code):
        fail('record_order')
    if type(bundle['observed_count']) is not int or bundle['observed_count'] != len(rows) \
            or type(bundle['empty_observation']) is not bool or bundle['empty_observation'] != (not rows) \
            or bundle['records_sha256'] != digest(rows):
        fail('manifest_mismatch')
    scans = bundle['scans']
    if type(scans) is not list or len(scans) != 2:
        fail('two_scans_required')
    previous = started
    for index, scan in enumerate(scans, 1):
        _keys(scan, ('sequence', 'started_at', 'completed_at', 'pages'))
        if type(scan['sequence']) is not int or scan['sequence'] != index:
            fail('scan_sequence')
        scan_start, scan_end = (_time(scan[key]) for key in ('started_at', 'completed_at'))
        if not previous <= scan_start <= scan_end <= completed:
            fail('invalid_capture_interval')
        pages = scan['pages']
        page_count = max(1, (len(rows) + size - 1) // size)
        if type(pages) is not list or len(pages) != page_count or page_count > MAX_PAGES:
            fail('incomplete_pages')
        seen = set()
        page_previous = scan_start
        for number, page in enumerate(pages, 1):
            _keys(page, ('page', 'total', 'started_at', 'completed_at', 'material_codes', 'accepted_rows_sha256'))
            if type(page['page']) is not int or page['page'] != number \
                    or type(page['total']) is not int or page['total'] != len(rows):
                fail('page_sequence_or_total')
            page_start, page_end = (_time(page[key]) for key in ('started_at', 'completed_at'))
            if not page_previous <= page_start <= page_end <= scan_end:
                fail('invalid_capture_interval')
            codes = page['material_codes']
            if type(codes) is not list or len(codes) != min(size, max(0, len(rows) - (number - 1) * size)):
                fail('incomplete_page')
            data = []
            for code in codes:
                if type(code) is not str or code not in by_code or code in seen:
                    fail('page_identity_mismatch')
                seen.add(code)
                data.append(by_code[code]['data'])
            if page['accepted_rows_sha256'] != digest(data):
                fail('page_hash_mismatch')
            page_previous = page_end
        if seen != set(by_code):
            fail('incomplete_pages')
        previous = scan_end
    if started != _time(scans[0]['started_at']) or completed != _time(scans[-1]['completed_at']):
        fail('envelope_time_mismatch')
    return MaterialMasterCaptureReport(bundle['capture_id'], expected_source_instance, len(rows),
        bundle['records_sha256'], digest(bundle), (now - started).total_seconds())
