"""Exact 0181 contact guard hashes and forward-only readiness registration."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path


RAW = Path(__file__).with_suffix('.json').read_bytes()
CATALOG_SHA256 = '96c12655b376eda976fb1c9b35862046ed6ce7130de971f06b6558592104f21c'
if sha256(RAW).hexdigest() != CATALOG_SHA256:
    raise ValueError('0181 runtime contact catalog changed')
DATA = json.loads(RAW)


def overlay(previous):
    """Accept only the complete 0180 readiness definition, metadata and ACL."""
    ready = DATA['readiness']
    if (previous.DATA['revision'] != DATA['previousRevision']
            or previous.DATA['after'] != ready['before']
            or previous.DATA['afterSha256'] != ready['beforeSha256']):
        raise ValueError('0181 exact readiness predecessor required')
    updated = deepcopy(previous.DATA)
    updated.update(after=deepcopy(ready['after']), afterSha256=ready['afterSha256'],
                   revision=DATA['revision'])
    previous.DATA = updated


def register(namespace):
    """Advance only the two exact prior contact guards; retain all ACL/shape checks."""
    hashes = namespace['MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256']
    changes = []
    if set(DATA['functions']) != {'rsc_guard_material_request_identity_0029()',
                                  'rsc_guard_material_request_revision_0029()'}:
        raise ValueError('0181 exact contact function set required')
    for signature, row in DATA['functions'].items():
        coordinate = (signature[:-2], '')
        if hashes.get(coordinate) != row['beforeSha256'] \
                or sha256(row['before'].encode()).hexdigest() != row['beforeSha256'] \
                or sha256(row['after'].encode()).hexdigest() != row['afterSha256']:
            raise ValueError('0181 exact contact guard predecessor required')
        changes.append((coordinate, row['afterSha256']))
    hashes.update(changes)
