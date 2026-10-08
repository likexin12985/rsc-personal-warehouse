"""0179 narrow typed receipt route; exact runtime catalog, no DDL."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '13c8d63deb452a759537b320e6f242f24e891adf035caf83e5a2998d1ed0fe02':
    raise ValueError('0179 runtime function catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    ready = DATA['readiness']
    if previous.DATA['after'] != ready['before'] or previous.DATA['afterSha256'] != ready['beforeSha256']:
        raise ValueError('0179 exact readiness predecessor required')
    updated = deepcopy(previous.DATA)
    updated.update(after=deepcopy(ready['after']), afterSha256=ready['afterSha256'], revision=DATA['revision'])
    previous.DATA = updated


def register(namespace):
    signature = 'rsc_guard_request_open_0169()'
    row = DATA['functions'][signature]
    coordinate = ('rsc_guard_request_open_0169', '')
    names = ('MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256',
             'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256', 'RUNTIME_FUNCTION_BODY_SHA256')
    matches = [name for name in names if coordinate in namespace[name]]
    if len(matches) != 1 or namespace[matches[0]][coordinate] != row['beforeSha256']:
        raise ValueError('0179 exact barrier predecessor required')
    catalogs = {}
    for name in ('_closure_catalog', '_remaining_cancel_catalog'):
        data = deepcopy(namespace[name].DATA)
        if data['functions'][signature]['after'] != DATA['barrier']['before']:
            raise ValueError('0179 exact full barrier catalog required')
        data['functions'][signature]['after'] = deepcopy(DATA['barrier']['after'])
        catalogs[name] = data
    namespace[matches[0]][coordinate] = row['afterSha256']
    for name, data in catalogs.items():
        namespace[name].DATA = data
