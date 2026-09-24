"""Strict local collection evidence. No credentials, network defaults or publication.

The injected page reader must be the existing shared-session Edge read adapter.
Successful collection proves observed pages and catalogue coverage; independent
source authentication, catalogue authority and publication remain required.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA = 'rsc.inventory_control_coverage.v1'
MAX_BYTES = 64 * 1024 * 1024
IDENTIFIER = re.compile(r'^[A-Za-z0-9._:-]{1,160}$')


class ControlCaptureError(RuntimeError):
    pass


def fail(code):
    raise ControlCaptureError('control_capture_' + code)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        fail('invalid_document')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_document(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: fail('duplicate_key')
            value[key] = item
        return value
    try:
        with open(path, 'rb') as stream: data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES: fail('document_too_large')
        result = json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: fail('invalid_number'))
        canonical(result)
        return result
    except (OSError, ValueError, UnicodeError, RecursionError):
        fail('document_unavailable')


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names): fail('invalid_catalog')


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value): fail('invalid_catalog')


def catalog(value, *, source_instance, company_id, org_code, scope_key):
    value = deepcopy(value)
    _keys(value, ('schema_version', 'binding', 'catalog_revision', 'target_region_code', 'warehouses'))
    expected = dict(source_system='starcharge_oam', source_instance=source_instance,
                    company_id=company_id, org_code=org_code, scope_key=scope_key)
    if value['schema_version'] != SCHEMA or value['binding'] != expected: fail('binding_mismatch')
    for item in (*expected.values(), value['catalog_revision'], value['target_region_code']): _id(item)
    if len(source_instance) > 128: fail('invalid_catalog')
    rows = value['warehouses']; seen = set(); target = False
    if type(rows) is not list or not 1 <= len(rows) <= 10000: fail('invalid_catalog')
    for row in rows:
        _keys(row, ('warehouse_code', 'warehouse_type', 'warehouse_attribute', 'positions'))
        for key in ('warehouse_code', 'warehouse_type', 'warehouse_attribute'): _id(row[key])
        if row['warehouse_code'] in seen: fail('duplicate_warehouse')
        seen.add(row['warehouse_code']); positions = set()
        if type(row['positions']) is not list or not 1 <= len(row['positions']) <= 10000: fail('invalid_catalog')
        for position in row['positions']:
            _keys(position, ('position_code', 'region_code'))
            _id(position['position_code']); _id(position['region_code'])
            if position['position_code'] in positions: fail('duplicate_position')
            positions.add(position['position_code'])
            target |= position['region_code'] == value['target_region_code']
    if not target: fail('target_region_missing')
    if scope_key != 'all' and (not re.fullmatch(r'warehouse:[A-Za-z0-9][A-Za-z0-9._-]{0,127}', scope_key)
            or seen != {scope_key.removeprefix('warehouse:')}): fail('invalid_scope')
    return value


def _pages(read_page, path, query, *, page_size):
    total = None; page = 1
    while True:
        try: response = read_page(path, {**query, 'page':page, 'size':page_size})
        except Exception: fail('source_read_failed')
        if type(response) is not dict or response.get('success') is not True: fail('source_response_invalid')
        model = response.get('model')
        if type(model) is not dict or type(model.get('amount')) is not int or not 0 <= model['amount'] <= 1000000 \
                or type(model.get('result')) is not list or any(type(row) is not dict for row in model['result']):
            fail('source_page_invalid')
        if total is None: total = model['amount']
        if model['amount'] != total: fail('source_total_changed')
        if max(1, (total + page_size - 1) // page_size) > 10000: fail('too_many_pages')
        if len(model['result']) != min(page_size, max(0, total - (page - 1) * page_size)): fail('incomplete_page')
        yield page, total, deepcopy(model['result'])
        if page * page_size >= total: return
        page += 1


def _live_catalog(expected, read_page, page_size):
    binding = expected['binding']; live = {}; seen = set()
    for _, _, rows in _pages(read_page, '/warehouse/list', {}, page_size=page_size):
        for row in rows:
            code = row.get('code')
            if not isinstance(code, str) or not code or code in seen: fail('warehouse_identity_invalid')
            seen.add(code)
            if row.get('companyId') != binding['company_id'] or row.get('orgCode') != binding['org_code']: continue
            if binding['scope_key'] != 'all' and code != binding['scope_key'].removeprefix('warehouse:'): continue
            live[code] = {key:row.get(key) for key in ('code', 'companyId', 'orgCode', 'warehouseType', 'warehouseAttribute')}
    required = {row['warehouse_code']:dict(code=row['warehouse_code'], companyId=binding['company_id'],
        orgCode=binding['org_code'], warehouseType=row['warehouse_type'], warehouseAttribute=row['warehouse_attribute'])
        for row in expected['warehouses']}
    if live != required: fail('live_catalog_changed')
    return live


def collect(expected, *, read_page, normalize_records, bind_scope, page_size=1000, clock=None):
    """Collect every expected warehouse; unknown responses never become zero."""
    if type(page_size) is not int or not 1 <= page_size <= 1000: fail('invalid_page_size')
    if type(expected) is not dict: fail('invalid_catalog')
    expected = deepcopy(expected)
    # Revalidate even callers using mutable dictionaries after the file preflight.
    binding = expected.get('binding', {})
    if type(binding) is not dict: fail('invalid_catalog')
    expected = catalog(expected, **{key:binding.get(key) for key in ('source_instance','company_id','org_code','scope_key')})
    clock = clock or (lambda: datetime.now(timezone.utc))
    def now():
        value = clock()
        if not isinstance(value, datetime) or value.utcoffset() is None: fail('invalid_capture_clock')
        return value.astimezone(timezone.utc)
    live = _live_catalog(expected, read_page, page_size)
    all_records = {}; captures = []
    for row in expected['warehouses']:
        code = row['warehouse_code']; warehouse = live[code]
        positions = {p['position_code'] for p in row['positions']}
        query = dict(warehouseCode=code, warehouseType=row['warehouse_type'], warehouseAttribute=row['warehouse_attribute'],
                     querySource='PC', snDisplayFlag=int(row['warehouse_type']=='supplyWarehouse'))
        started = now(); pages = []
        if captures and started < datetime.fromisoformat(captures[-1]['completed_at']): fail('capture_clock_moved_backwards')
        for page, total, raw in _pages(read_page, '/material_stock/list', query, page_size=page_size):
            for item in raw:
                if item.get('positionCode') not in positions: fail('position_unmapped')
                for field, target in (('companyId',binding['company_id']),('orgCode',binding['org_code']),
                        ('warehouseCode',code),('warehouseType',row['warehouse_type']),('warehouseAttribute',row['warehouse_attribute'])):
                    if item.get(field) not in (None, '', target): fail('inventory_binding_mismatch')
                    # Absent display fields inherit only the exact, independently
                    # checked query binding. A conflicting source value is never replaced.
                    if item.get(field) in (None, ''): item[field] = target
            try: records = normalize_records([bind_scope(item, warehouse) for item in raw])
            except Exception: fail('invalid_inventory_record')
            if len(records) != len(raw): fail('record_count_changed')
            for record in records:
                key = record['business_key']
                if key in all_records: fail('duplicate_record')
                all_records[key] = record
            pages.append(dict(response_status='success', page=page, size=page_size, source_total=total,
                record_keys=[record['business_key'] for record in records], records_sha256=digest(records)))
        completed = now()
        if completed < started: fail('capture_clock_moved_backwards')
        captures.append(dict(query=query, started_at=started.isoformat(), completed_at=completed.isoformat(), pages=pages))
    _live_catalog(expected, read_page, page_size)
    return dict(records=sorted(all_records.values(), key=lambda row:row['business_key']), warehouses=captures)


def load_base(directory, state, expected, *, force_full):
    if force_full: return None
    if type(state) is not dict or type(state.get('scopes',{})) is not dict: fail('invalid_base_state')
    if state.get('version') != 2 or state.get('sourceInstance') != expected['binding']['source_instance']:
        fail('base_binding_mismatch')
    scope = state.get('scopes', {}).get(expected['binding']['scope_key'])
    if scope is None: return None
    if type(scope) is not dict: fail('invalid_base_state')
    if not scope: return None
    pointer = scope.get('controlEvidence')
    if type(pointer) is not dict or set(pointer) != {'file','sha256'}: fail('base_evidence_required_force_full')
    if not isinstance(pointer['file'], str) or not re.fullmatch(r'capture-[A-Za-z0-9._:-]+\.json',pointer['file']): fail('invalid_base_pointer')
    bundle = read_document(Path(directory)/pointer['file'])
    if type(bundle) is not dict: fail('invalid_base_evidence')
    if digest(bundle) != pointer['sha256'] or bundle.get('expected') != expected: fail('base_evidence_changed_force_full')
    try:
        snapshots = bundle['evidence']['snapshots']; last = snapshots[-1]['manifest']
        if type(snapshots) is not list or not snapshots: fail('invalid_base_evidence')
        if len(snapshots) >= 64: fail('base_chain_limit_force_full')
        if bundle['schema_version'] != 'rsc.inventory_control_capture_bundle.v1' or bundle['evidence']['schema_version'] != SCHEMA \
                or last['snapshot_id'] != scope['snapshotId'] or last['entities'][0]['final_sha256'] != scope['entities']['inventory']['sha256']:
            fail('base_evidence_changed_force_full')
        for key, field in (('source_instance','sourceInstance'),('company_id','companyId'),('org_code','orgCode'),('scope_key','scopeKey')):
            if scope[field] != expected['binding'][key]: fail('base_binding_mismatch')
        records = _reconstruct(snapshots)
        inventory = scope['entities']['inventory']
        if set(scope['entities']) != {'inventory'} or type(inventory['records']) is not int \
                or inventory['records'] != len(records) or inventory['sha256'] != digest(records) \
                or inventory['index'] != {row['business_key']:digest(row) for row in records}:
            fail('base_index_changed_force_full')
    except (KeyError, IndexError, TypeError): fail('invalid_base_evidence')
    return bundle['evidence']


def _reconstruct(snapshots):
    current = {}; prior = None
    try:
        for snapshot in snapshots:
            manifest = snapshot['manifest']; entities = manifest['entities']
            if len(entities) != 1 or entities[0]['entity_type'] != 'inventory': fail('invalid_evidence_chain')
            entity = entities[0]; batches = snapshot['batches']
            if prior is None:
                if manifest['sync_mode'] != 'full' or snapshot['base_snapshot_id'] is not None or snapshot['base_final_sha256'] is not None:
                    fail('invalid_evidence_chain')
            elif manifest['sync_mode'] != 'incremental' or snapshot['base_snapshot_id'] != prior['snapshot_id'] \
                    or snapshot['base_final_sha256'] != prior['entities'][0]['final_sha256']:
                fail('invalid_evidence_chain')
            delta = [row for batch in batches for row in batch['records']]
            if len(batches) != entity['batch_count'] or len(delta) != entity['delta_record_count'] \
                    or digest(sorted(delta,key=lambda row:row['business_key'])) != entity['delta_sha256']:
                fail('invalid_evidence_chain')
            seen = set()
            for sequence, batch in enumerate(batches,1):
                if any(batch[key] != manifest[key] for key in ('source_system','snapshot_id','scope_key','sync_mode','company_id','org_code','snapshot_at')) \
                        or batch['entity_type'] != 'inventory' or batch['sequence'] != sequence or batch['total_sequences'] != len(batches):
                    fail('invalid_evidence_chain')
            for row in delta:
                key = row['business_key']
                if key in seen: fail('invalid_evidence_chain')
                seen.add(key)
                if row['operation'] == 'upsert': current[key] = {k:v for k,v in row.items() if k != 'operation'}
                elif row['operation'] == 'delete' and prior is not None and key in current and row['data']=={} and row['source_updated_at'] is None:
                    del current[key]
                else: fail('invalid_evidence_chain')
            records = sorted(current.values(), key=lambda row:row['business_key'])
            if len(records) != entity['final_record_count'] or digest(records) != entity['final_sha256']: fail('invalid_evidence_chain')
            prior = manifest
    except (KeyError,IndexError,TypeError): fail('invalid_evidence_chain')
    return records


def build_bundle(expected, capture, outbox, *, manifest, batches, base):
    entity = outbox['entities'].get('inventory')
    if set(outbox['entities']) != {'inventory'} or entity['finalRecordCount'] != len(capture['records']) \
            or entity['finalSha256'] != digest(capture['records']): fail('outbox_capture_mismatch')
    if (outbox['syncMode'] == 'incremental') != (base is not None): fail('base_mode_mismatch')
    for key, field in (('source_instance','sourceInstance'),('company_id','companyId'),('org_code','orgCode'),('scope_key','scopeKey')):
        if outbox[field] != expected['binding'][key]: fail('outbox_binding_mismatch')
    previous = base['snapshots'][-1] if base else None
    if any(datetime.fromisoformat(row['completed_at']) > datetime.fromisoformat(outbox['snapshotAt']) for row in capture['warehouses']):
        fail('snapshot_time_mismatch')
    snapshot = dict(source_instance=expected['binding']['source_instance'],target_region_code=expected['target_region_code'],
        catalog_revision=expected['catalog_revision'],base_snapshot_id=previous['manifest']['snapshot_id'] if previous else None,
        base_final_sha256=previous['manifest']['entities'][0]['final_sha256'] if previous else None,
        manifest=manifest,batches=batches,warehouses=capture['warehouses'])
    evidence = dict(schema_version=SCHEMA,snapshots=[*(base['snapshots'] if base else []),snapshot])
    if _reconstruct(evidence['snapshots']) != capture['records']: fail('outbox_capture_mismatch')
    bundle = dict(schema_version='rsc.inventory_control_capture_bundle.v1',expected=expected,evidence=evidence,
                  source_authenticated=False,catalog_authenticated=False,capture_attested=False,projection_published=False,start_ready=False)
    if len(canonical(bundle)) > MAX_BYTES: fail('document_too_large')
    return deepcopy(bundle)


def archive(directory, bundle):
    snapshot = bundle['evidence']['snapshots'][-1]['manifest']['snapshot_id']; _id(snapshot)
    directory = Path(directory); directory.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(directory,0o700)
    name = 'capture-' + snapshot + '.json'; path = directory/name; data = canonical(bundle)
    try:
        fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        parent = os.open(directory,os.O_RDONLY)
        try: os.fsync(parent)
        finally: os.close(parent)
    except OSError:
        # An incomplete local archive remains evidence; never overwrite it.
        fail('archive_unconfirmed')
    return dict(file=name,sha256=digest(bundle))


def attestation_payload(bundle, *, key_id):
    """Digest-only claim sent through the existing HMAC channel after completion.

    Normalize typed capture timestamps exactly as the backend coverage schema;
    original manifest/batch dictionaries retain their existing wire values.
    """
    if not isinstance(key_id,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',key_id): fail('key_id_required')
    expected=bundle['expected']; evidence=deepcopy(bundle['evidence'])
    def stamp(value):
        value=datetime.fromisoformat(value.replace('Z','+00:00'))
        if value.utcoffset() is None:fail('invalid_capture_clock')
        return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    for snapshot in evidence['snapshots']:
        for warehouse in snapshot['warehouses']:
            for field in ('started_at','completed_at'):warehouse[field]=stamp(warehouse[field])
    latest=evidence['snapshots'][-1];manifest=latest['manifest']
    catalog={k:v for k,v in expected.items() if k not in ('schema_version','binding')}
    return dict(schema_version='rsc.inventory_control_attestation.v1',collector_contract='rsc.inventory_control_capture.v1',
        key_id=key_id,**expected['binding'],snapshot_id=manifest['snapshot_id'],snapshot_at=stamp(manifest['snapshot_at']),
        sync_mode=manifest['sync_mode'],catalog_revision=expected['catalog_revision'],target_region_code=expected['target_region_code'],
        source_binding_sha256=digest(expected['binding']),catalog_sha256=digest(catalog),capture_chain_sha256=digest(evidence),
        capture_started_at=stamp(min(datetime.fromisoformat(w['started_at'].replace('Z','+00:00')) for w in latest['warehouses']).isoformat()),
        capture_completed_at=stamp(max(datetime.fromisoformat(w['completed_at'].replace('Z','+00:00')) for w in latest['warehouses']).isoformat()))
