#!/usr/bin/env python3
"""Explicit local spare-master capture. No upload, scheduler or database writes."""
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'cloud_oam' / 'backend'
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.material_master_capture_evidence import (  # noqa: E402
    ENDPOINT, FIELD_CONTRACT, MAX_BYTES, MAX_PAGES, MAX_RECORDS, SCHEMA,
    MaterialMasterCaptureError, accepted_row, canonical, digest, fail,
    material_record, source_binding, stamp, validate_capture,
)


def collect(*, source_instance, read_page, page_size=1000, clock=None):
    binding = source_binding(source_instance)
    if type(page_size) is not int or not 1 <= page_size <= 1000:
        fail('invalid_page_size')
    clock = clock or (lambda: datetime.now(timezone.utc))
    previous = None
    def now():
        nonlocal previous
        value = stamp(clock())
        if previous is not None and value < previous:
            fail('capture_clock_moved_backwards')
        previous = value
        return value
    scans = []
    records = None
    for sequence in (1, 2):
        started = now()
        pages, current, total, byte_count = [], {}, None, 0
        page = 1
        while True:
            page_started = now()
            try:
                response = read_page(ENDPOINT, {'page': page, 'size': page_size})
            except Exception:
                fail('source_read_failed')
            page_completed = now()
            if type(response) is not dict or response.get('success') is not True:
                fail('source_response_invalid')
            model = response.get('model')
            if type(model) is not dict or type(model.get('amount')) is not int \
                    or not 0 <= model['amount'] <= MAX_RECORDS or type(model.get('result')) is not list:
                fail('source_page_invalid')
            if total is None:
                total = model['amount']
            if model['amount'] != total:
                fail('source_total_changed')
            if max(1, (total + page_size - 1) // page_size) > MAX_PAGES:
                fail('too_many_pages')
            raw = model['result']
            if len(raw) != min(page_size, max(0, total - (page - 1) * page_size)):
                fail('incomplete_page')
            accepted = []
            for row in raw:
                data = accepted_row(row)
                record = material_record(data)
                code = record['external_id']
                if code in current:
                    fail('duplicate_material')
                current[code] = record
                byte_count += len(canonical(record))
                if byte_count > MAX_BYTES:
                    fail('document_too_large')
                accepted.append(data)
            pages.append(dict(page=page, total=total, started_at=page_started, completed_at=page_completed,
                material_codes=[row['materialCode'] for row in accepted], accepted_rows_sha256=digest(accepted)))
            if page * page_size >= total:
                break
            page += 1
        ordered = sorted(current.values(), key=lambda row: row['external_id'])
        if records is not None and canonical(ordered) != canonical(records):
            fail('source_changed_between_scans')
        records = ordered
        scans.append(dict(sequence=sequence, started_at=started, completed_at=now(), pages=pages))
    bundle = dict(schema_version=SCHEMA, field_contract=FIELD_CONTRACT, binding=binding,
        capture_id=str(uuid4()), started_at=scans[0]['started_at'], completed_at=scans[-1]['completed_at'],
        page_size=page_size, scans=scans, records=records, records_sha256=digest(records),
        observed_count=len(records), empty_observation=not records)
    validate_capture(bundle, expected_source_instance=source_instance, now=datetime.fromisoformat(now()))
    return bundle


def _private_directory(path):
    """Do not chmod somebody else's directory or follow symlink components."""
    path = Path(os.path.abspath(path))
    try:
        for parent in (*reversed(path.parents), path):
            if stat.S_ISLNK(parent.lstat().st_mode):
                fail('unsafe_archive_directory')
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            fail('unsafe_archive_directory')
    except OSError:
        fail('unsafe_archive_directory')
    return path


def archive(bundle, *, directory, expected_source_instance, now):
    report = validate_capture(bundle, expected_source_instance=expected_source_instance, now=now)
    directory = _private_directory(directory)
    name = 'material-master-' + report.capture_id + '.json'
    data = canonical(bundle)
    parent = None
    try:
        parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(parent)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            fail('unsafe_archive_directory')
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    except OSError:
        # Keep any incomplete file. An uncertain archive must never be overwritten.
        fail('archive_unconfirmed')
    finally:
        if parent is not None:
            os.close(parent)
    return dict(file=name, sha256=report.capture_sha256)


def read_capture(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail('duplicate_json_key')
            result[key] = value
        return result
    try:
        with open(path, 'rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            fail('document_too_large')
        result = json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: fail('invalid_number'))
        canonical(result)
        return result
    except (OSError, ValueError, UnicodeError, RecursionError):
        fail('document_unavailable')


def _local_transport():
    work = ROOT / 'work'
    required = (work / 'oam_shared_session.py', work / 'global_business_session_health.py',
                work / 'inventory_query_portal' / 'oam_read_client.py')
    if not all(path.is_file() for path in required):
        fail('local_read_client_unavailable')
    if str(work) not in sys.path:
        sys.path.insert(0, str(work))
    try:
        from inventory_query_portal.oam_read_client import post_json
    except Exception:
        fail('local_read_client_unavailable')
    def read_page(path, payload):
        if path != ENDPOINT or set(payload) != {'page', 'size'}:
            fail('source_query_not_allowed')
        return post_json(path, payload, referer='/warehouse/stock/list', retries=0)
    return read_page


def _health():
    try:
        result = subprocess.run([sys.executable, str(ROOT / 'work' / 'global_business_session_health.py'),
            '--require', 'oam'], cwd=ROOT, capture_output=True, text=True, check=False, timeout=60)
        payload = json.loads(result.stdout)
    except Exception:
        fail('source_health_unknown')
    components = payload.get('components') if type(payload) is dict else None
    oam = components.get('oam') if type(components) is dict else None
    if result.returncode != 0 or type(payload) is not dict or payload.get('ok') is not True \
            or type(oam) is not dict or oam.get('status') != 'ok':
        # Only the shared exact-component checker can prove an authentication failure.
        if type(oam) is dict and oam.get('status') == 'auth_required':
            fail('source_auth_required')
        fail('source_health_unconfirmed')


def _edge_preflight():
    helper = Path.home() / 'Library' / 'Application Support' / 'CodexLocalEdge' / 'ensure_local_edge.py'
    try:
        result = subprocess.run([sys.executable, str(helper), '--compatibility'],
            capture_output=True, text=True, check=False, timeout=60)
        value = json.loads(result.stdout)
    except Exception:
        fail('original_edge_unconfirmed')
    expected_profile = str(Path.home() / 'Library' / 'Application Support' / 'Microsoft Edge')
    if result.returncode != 0 or type(value) is not dict or value.get('ok') is not True \
            or value.get('browser') != 'original Mac Edge' or value.get('profile') != expected_profile \
            or value.get('cdp') != 'http://127.0.0.1:9224' or value.get('compatibility') != 'validated':
        fail('original_edge_unconfirmed')


def _transport_config(api_base, key_id):
    secret = os.getenv('RSC_EDGE_SYNC_SECRET', '')
    if len(secret) < 32 or any(word in secret.lower() for word in ('replace-with','replace-me','change-me','changeme')):
        fail('transport_secret_required')
    if type(key_id) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',key_id): fail('transport_key_required')
    try:
        url = urlsplit(api_base)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment \
                or any(c.isspace() for c in api_base): fail('transport_url_invalid')
        url.port
    except ValueError: fail('transport_url_invalid')
    return api_base.rstrip('/'), secret


def _shared_request(*args, **kwargs):
    adapter = Path.home() / 'Library' / 'Application Support' / 'CodexLocalEdge'
    if str(adapter) not in sys.path: sys.path.insert(0, str(adapter))
    from edge_http import request
    return request(*args, **kwargs)


def _historical_time(bundle):
    try:
        value=datetime.fromisoformat(bundle['completed_at'])
        if value.utcoffset() is None or value>datetime.now(timezone.utc): fail('invalid_capture_interval')
        return value
    except (KeyError,TypeError,ValueError,OverflowError): fail('invalid_document')


def transmit(bundle, *, source_instance, api_base, key_id, operation):
    """One signed request; lost or malformed acknowledgements never trigger replay."""
    if operation not in ('receive','status'): fail('invalid_transport_operation')
    now = datetime.now(timezone.utc) if operation == 'receive' else _historical_time(bundle)
    report = validate_capture(bundle, expected_source_instance=source_instance, now=now)
    api_base, secret = _transport_config(api_base,key_id)
    payload = dict(schema_version='rsc.oam_material_capture_request.v1', operation=operation, key_id=key_id,
        **({'capture':bundle} if operation == 'receive' else {'capture_id':report.capture_id}))
    raw = canonical(payload)
    stamp_value = str(int(time.time()))
    batch = 'material-' + report.capture_id + '-' + operation
    signature = hmac.new(secret.encode(), b'\n'.join((stamp_value.encode(),source_instance.encode(),batch.encode(),raw)),hashlib.sha256).hexdigest()
    url = api_base + '/integrations/oam/edge/material-master/captures' + ('/status' if operation == 'status' else '')
    try:
        code, body, _ = _shared_request('POST',url,headers={'Content-Type':'application/json',
            'X-RSC-Edge-Source':source_instance,'X-RSC-Edge-Timestamp':stamp_value,
            'X-RSC-Edge-Batch':batch,'X-RSC-Edge-Signature':signature},body=raw,timeout=60)
        if code != 200: fail('upload_result_unknown')
        if len(body)>16384: fail('acknowledgement_mismatch')
        def unique(pairs):
            value={}
            for key,item in pairs:
                if key in value: fail('acknowledgement_mismatch')
                value[key]=item
            return value
        result = json.loads(body,object_pairs_hook=unique,parse_constant=lambda _:fail('acknowledgement_mismatch'))
    except Exception:
        fail('upload_result_unknown')
    if type(result) is not dict or result.get('ok') is not True or result.get('mode') != 'staging_only' \
            or result.get('source_instance') != source_instance or result.get('capture_id') != report.capture_id:
        fail('acknowledgement_mismatch')
    if operation == 'status' and result.get('status') == 'not_found':
        if result.get('retry_allowed') is not False or set(result)!={'ok','mode','status','source_instance','capture_id','retry_allowed'}:
            fail('acknowledgement_mismatch')
        return result
    expected_keys={'ok','mode','duplicate','receipt_id','capture_id','source_instance','key_id','capture_sha256',
        'records_sha256','observed_count','received_at','channel_attested','within_freshness_target','full_catalog_verified',
        'source_authorized','master_source_evidence_verified','projection_published','start_ready'}
    if operation=='status':expected_keys.add('status')
    if set(result)!=expected_keys:fail('acknowledgement_mismatch')
    try:
        if str(UUID(result['receipt_id'])) != result['receipt_id']: fail('acknowledgement_mismatch')
        received = datetime.fromisoformat(result['received_at'])
        if received.utcoffset() is None or received < datetime.fromisoformat(bundle['completed_at']) \
                or received>datetime.now(timezone.utc)+timedelta(minutes=5): fail('acknowledgement_mismatch')
    except (KeyError, TypeError, ValueError, AttributeError): fail('acknowledgement_mismatch')
    if result.get('capture_sha256') != report.capture_sha256 or result.get('records_sha256') != report.records_sha256 \
            or type(result.get('observed_count')) is not int or result['observed_count'] != report.observed_count \
            or result.get('channel_attested') is not True or type(result.get('within_freshness_target')) is not bool \
            or type(result.get('duplicate')) is not bool \
            or type(result.get('key_id')) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',result['key_id']) \
            or any(result.get(key) is not False for key in ('full_catalog_verified','source_authorized',
                'master_source_evidence_verified','projection_published','start_ready')) \
            or (operation == 'receive' and result.get('key_id') != key_id) \
            or (operation == 'status' and result.get('status') != 'received'):
        fail('acknowledgement_mismatch')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='OAM 备件主数据采集、证据检查与显式认证暂存；不发布正式物料')
    parser.add_argument('--source-instance', required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--capture', action='store_true', help='显式发起两轮只读采集')
    action.add_argument('--inspect-file', type=Path, help='离线重验已保存的采集文件')
    action.add_argument('--status-file', type=Path, help='签名查询原文件的接收状态；不重新上传')
    parser.add_argument('--archive-dir', type=Path, help='已有的本人所有、权限 0700 的私有目录')
    parser.add_argument('--page-size', type=int, default=1000)
    parser.add_argument('--upload', action='store_true', help='仅配合新采集，将已归档文件上传隔离接收器')
    parser.add_argument('--api-base', default=os.getenv('RSC_EDGE_API_BASE',''))
    parser.add_argument('--key-id', default=os.getenv('RSC_EDGE_MATERIAL_CAPTURE_KEY_ID',''))
    args = parser.parse_args(argv)
    try:
        source_binding(args.source_instance)
        receipt = None
        if args.upload or args.status_file:
            if args.upload and not args.capture: fail('upload_requires_fresh_capture')
            _transport_config(args.api_base,args.key_id)
        if args.capture:
            if args.archive_dir is None:
                fail('archive_directory_required')
            _private_directory(args.archive_dir)
            if not 1 <= args.page_size <= 1000:
                fail('invalid_page_size')
            read_page = _local_transport()
            _edge_preflight()
            _health()
            bundle = collect(source_instance=args.source_instance, read_page=read_page, page_size=args.page_size)
            pointer = archive(bundle, directory=args.archive_dir, expected_source_instance=args.source_instance,
                              now=datetime.now(timezone.utc))
            if args.upload:
                receipt = transmit(bundle,source_instance=args.source_instance,api_base=args.api_base,key_id=args.key_id,operation='receive')
        else:
            if args.archive_dir is not None or args.page_size != 1000:
                fail('inspection_arguments_conflict')
            bundle, pointer = read_capture(args.inspect_file or args.status_file), None
            if args.status_file:
                validate_capture(bundle,expected_source_instance=args.source_instance,now=_historical_time(bundle))
                _edge_preflight()
                receipt = transmit(bundle,source_instance=args.source_instance,api_base=args.api_base,key_id=args.key_id,operation='status')
        # A status lookup can re-read an expired received capture; report its
        # historical consistency and let the server state current freshness.
        report_time = _historical_time(bundle) if args.status_file else datetime.now(timezone.utc)
        report = validate_capture(bundle, expected_source_instance=args.source_instance, now=report_time)
        summary=asdict(report)
        if args.status_file:
            summary['oldest_capture_age_seconds']=(datetime.now(timezone.utc)-datetime.fromisoformat(bundle['started_at'])).total_seconds()
            summary['historical_only']=True
        print(json.dumps(dict(report=summary, archive=pointer, receipt=receipt), ensure_ascii=False))
        return 0
    except MaterialMasterCaptureError as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
